from __future__ import annotations

import json
import re
import shutil
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, UnidentifiedImageError
from psd_tools import PSDImage

from figma_to_fgui.psd_intake import (
    PsdAnalysis,
    PsdInspection,
    PsdLayer,
    PsdLayerEffect,
    PsdTextRun,
    PsdTextStyle,
    analyze_psd,
)


class PsdSourceStoreError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _calibrate_opaque_owned_pixels(
    image: Image.Image,
    bounds: tuple[int, int, int, int],
    composite_path: Path,
) -> Image.Image:
    """Match PSD's final 16-bit rounding without changing real layer colors."""
    with Image.open(composite_path) as source:
        target = source.convert("RGBA").crop(bounds)
    owned = image.convert("RGBA")
    if target.size != owned.size:
        return owned
    difference = ImageChops.difference(owned.convert("RGB"), target.convert("RGB"))
    channels = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    near = maximum.point(lambda value: 255 if value <= 1 else 0)
    opaque = owned.getchannel("A").point(lambda value: 255 if value == 255 else 0)
    mask = ImageChops.multiply(near, opaque)
    return Image.composite(target, owned, mask)


def _calibrate_opaque_owned_pixels_to_target(
    image: Image.Image,
    bounds: tuple[int, int, int, int],
    composite_path: Path,
    protected_mask: Image.Image,
) -> Image.Image:
    """Copy exact PSD colors into opaque owned pixels outside retained artwork.

    A fully opaque resource pixel is the final screen pixel in FairyGUI, so no
    blend equation is required.  The retained mask prevents text (or another
    separately mapped sibling) from being baked into the PNG that replaces the
    old image/loader object.
    """
    owned = image.convert("RGBA")
    protected = protected_mask.convert("L")
    if protected.size != owned.size:
        return owned
    with Image.open(composite_path) as source:
        target = source.convert("RGBA").crop(bounds)
    if target.size != owned.size:
        return owned
    opaque = owned.getchannel("A").point(lambda value: 255 if value == 255 else 0)
    writable = ImageChops.subtract(opaque, protected)
    return Image.composite(target, owned, writable)


def _retained_visual_mask(
    document: Any,
    source: PsdAnalysis,
    retained_ids: frozenset[str],
    bounds: tuple[int, int, int, int],
) -> Image.Image:
    """Rasterize only retained glyph/object coverage into resource coordinates."""
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    result = Image.new("L", (width, height), 0)
    if not retained_ids:
        return result
    metadata = {layer.id: layer for layer in source.layers}
    actual = list(document.descendants())
    for layer_id in retained_ids:
        layer = metadata.get(layer_id)
        if layer is None or not 0 <= layer.document_index < len(actual):
            continue
        rendered = actual[layer.document_index].topil()
        if rendered is None:
            continue
        alpha = rendered.convert("RGBA").getchannel("A")
        layer_bounds = tuple(getattr(actual[layer.document_index], "bbox", layer.bounds))
        if len(layer_bounds) != 4:
            continue
        layer_mask = Image.new("L", result.size, 0)
        layer_mask.paste(alpha, (layer_bounds[0] - bounds[0], layer_bounds[1] - bounds[1]))
        expansion = 0
        for effect in layer.effects:
            if not effect.enabled:
                continue
            if effect.kind == "Stroke" and effect.size is not None:
                expansion = max(expansion, round(effect.size))
            elif effect.kind == "DropShadow":
                expansion = max(
                    expansion,
                    round((effect.size or 0) + (effect.distance or 0)),
                )
        if expansion:
            layer_mask = layer_mask.filter(ImageFilter.MaxFilter(expansion * 2 + 1))
        result = ImageChops.lighter(result, layer_mask)
    return result


def _calibrate_translucent_owned_pixels(
    image: Image.Image,
    bounds: tuple[int, int, int, int],
    underlay_path: Path,
    underlay_bounds: tuple[int, int, int, int],
    composite_path: Path,
) -> Image.Image:
    """Precompensate PSD edge pixels for FairyGUI's straight-alpha blend.

    Photoshop's merged preview and FairyGUI do not round translucent edge
    pixels identically.  The existing full-canvas background object is the
    known underlay, so solve the source RGB that makes FairyGUI's normal
    ``source-over`` equation produce the PSD composite.  Opaque pixels are
    deliberately excluded: text and other retained objects may be painted
    above them and must never be baked into this image.
    """
    owned = image.convert("RGBA")
    with Image.open(underlay_path) as source:
        underlay = source.convert("RGBA")
    with Image.open(composite_path) as source:
        target = source.convert("RGBA")
    pixels = owned.load()
    background = underlay.load()
    expected = target.load()
    left, top, _, _ = bounds
    underlay_left, underlay_top, _, _ = underlay_bounds
    for local_y in range(owned.height):
        document_y = top + local_y
        background_y = document_y - underlay_top
        if not 0 <= background_y < underlay.height or not 0 <= document_y < target.height:
            continue
        for local_x in range(owned.width):
            *_, alpha = pixels[local_x, local_y]
            if not 16 <= alpha < 255:
                continue
            document_x = left + local_x
            background_x = document_x - underlay_left
            if not 0 <= background_x < underlay.width or not 0 <= document_x < target.width:
                continue
            backdrop = background[background_x, background_y]
            if backdrop[3] != 255:
                continue
            desired = expected[document_x, document_y]
            corrected = tuple(
                max(
                    0,
                    min(
                        255,
                        round(
                            (desired[channel] * 255 - backdrop[channel] * (255 - alpha))
                            / alpha
                        ),
                    ),
                )
                for channel in range(3)
            )
            pixels[local_x, local_y] = (*corrected, alpha)
    return owned


def _calibrate_observed_translucent_pixels(
    image: Image.Image,
    actual_frame: Image.Image,
    target_frame: Image.Image,
    *,
    screen_xy: tuple[int, int],
    protected_bounds: tuple[tuple[int, int, int, int], ...] = (),
) -> Image.Image:
    """Correct one resource from an observed FairyGUI frame.

    The observed frame contains the real lower-layer result, including legacy
    objects and controller state. Recover that backdrop from the current
    source-over equation, then choose the smallest alpha capable of producing
    the target pixel. Protected upper-object rectangles are never sampled or
    baked into the resource.
    """
    owned = image.convert("RGBA")
    actual = actual_frame.convert("RGB")
    target = target_frame.convert("RGB")
    pixels = owned.load()
    observed = actual.load()
    expected = target.load()
    origin_x, origin_y = screen_xy
    # Missing outside strokes/shadows occupy transparent pixels immediately
    # beside the source alpha. Limit recovery to that narrow effect band so a
    # resource can never absorb unrelated UI elsewhere in its rectangular
    # bounds.
    effect_band = owned.getchannel("A").filter(ImageFilter.MaxFilter(9)).load()

    def blend(foreground: int, backdrop: int, alpha: int) -> int:
        return (foreground * alpha + backdrop * (255 - alpha) + 127) // 255

    def closest_solution(target_value: int, backdrop: int, alpha: int) -> int | None:
        ideal = (target_value * 255 - backdrop * (255 - alpha)) / alpha
        center = round(ideal)
        candidates = range(max(0, center - 2), min(255, center + 2) + 1)
        return next(
            (
                value
                for value in sorted(candidates, key=lambda item: abs(item - ideal))
                if blend(value, backdrop, alpha) == target_value
            ),
            None,
        )

    for local_y in range(owned.height):
        screen_y = origin_y + local_y
        if not 0 <= screen_y < min(actual.height, target.height):
            continue
        for local_x in range(owned.width):
            screen_x = origin_x + local_x
            if not 0 <= screen_x < min(actual.width, target.width):
                continue
            if any(
                left <= screen_x < right and top <= screen_y < bottom
                for left, top, right, bottom in protected_bounds
            ):
                continue
            red, green, blue, alpha = pixels[local_x, local_y]
            desired = expected[screen_x, screen_y]
            if alpha == 255:
                # An opaque resource pixel is the final Editor pixel whenever
                # no retained upper object covers it (those regions are
                # protected above), so copy the target colour exactly.
                pixels[local_x, local_y] = (*desired, 255)
                continue
            if effect_band[local_x, local_y] == 0:
                continue
            current = observed[screen_x, screen_y]
            if current == desired:
                continue
            backdrop_values: list[int] = list(current) if alpha == 0 else []
            if alpha:
                for foreground, current_value in zip(
                    (red, green, blue), current, strict=True
                ):
                    denominator = 255 - alpha
                    ideal = (current_value * 255 - foreground * alpha) / denominator
                    center = round(ideal)
                    candidates = range(max(0, center - 2), min(255, center + 2) + 1)
                    matches = [
                        value for value in candidates
                        if blend(foreground, value, alpha) == current_value
                    ]
                    if not matches:
                        break
                    backdrop_values.append(min(matches, key=lambda value: abs(value - ideal)))
            if len(backdrop_values) != 3:
                continue
            for corrected_alpha in range(max(1, alpha), 256):
                corrected = tuple(
                    closest_solution(value, backdrop, corrected_alpha)
                    for value, backdrop in zip(desired, backdrop_values, strict=True)
                )
                if all(value is not None for value in corrected):
                    pixels[local_x, local_y] = (
                        int(corrected[0]),
                        int(corrected[1]),
                        int(corrected[2]),
                        corrected_alpha,
                    )
                    break
    return owned


def _calibrate_channel_rounding(
    image: Image.Image,
    bounds: tuple[int, int, int, int],
    composite_path: Path,
) -> Image.Image:
    """Learn PSD merged-preview channel rounding without copying spatial UI."""
    with Image.open(composite_path) as source:
        target = source.convert("RGBA").crop(bounds)
    owned = image.convert("RGBA")
    if target.size != owned.size:
        return owned
    observations = [[Counter() for _ in range(256)] for _ in range(3)]
    source_pixels = owned.load()
    target_pixels = target.load()
    for y in range(owned.height):
        for x in range(owned.width):
            current = source_pixels[x, y]
            expected = target_pixels[x, y]
            if current[3] != 255 or max(
                abs(current[channel] - expected[channel]) for channel in range(3)
            ) > 1:
                continue
            for channel in range(3):
                observations[channel][current[channel]][expected[channel]] += 1
    tables = [list(range(256)) for _ in range(3)]
    for channel in range(3):
        for value, counts in enumerate(observations[channel]):
            if sum(counts.values()) >= 16:
                tables[channel][value] = counts.most_common(1)[0][0]
    red, green, blue, alpha = owned.split()
    calibrated = Image.merge(
        "RGBA",
        (red.point(tables[0]), green.point(tables[1]), blue.point(tables[2]), alpha),
    )
    opaque = alpha.point(lambda value: 255 if value == 255 else 0)
    return Image.composite(calibrated, owned, opaque)


@dataclass(frozen=True)
class PsdSource:
    version: int
    source_id: str
    inspection: PsdInspection
    layers: tuple[PsdLayer, ...]


@dataclass(frozen=True)
class PsdRasterResource:
    layer_id: str
    key: str
    mime_type: str
    size: int
    bounds: tuple[int, int, int, int] | None = None


class PsdSourceStore:
    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir / "hifi-sources" / "psd"
        self._root.mkdir(parents=True, exist_ok=True)

    def admit(self, upload_path: Path, *, source_name: str) -> PsdSource:
        analysis = analyze_psd(upload_path, source_name=source_name)
        source_id = analysis.inspection.sha256
        destination = self._root / source_id
        if destination.is_dir():
            upload_path.unlink(missing_ok=True)
            refreshed = destination / f".hifi-ir-{uuid.uuid4().hex[:8]}.json"
            refreshed.write_text(
                json.dumps(
                    self._payload(source_id, analysis),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            refreshed.replace(destination / "hifi-ir.json")
            return self.get(source_id)

        staging = self._root / f".admit-{source_id}-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            upload_path.replace(staging / "source.psd")
            payload = self._payload(source_id, analysis)
            (staging / "hifi-ir.json").write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            try:
                staging.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
                shutil.rmtree(staging)
            return self.get(source_id)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def get(self, source_id: str) -> PsdSource:
        if len(source_id) != 64 or any(character not in "0123456789abcdef" for character in source_id):
            raise PsdSourceStoreError("psd_source_not_found")
        source_root = self._root / source_id
        source_path = source_root / "source.psd"
        manifest_path = source_root / "hifi-ir.json"
        if not source_path.is_file() or not manifest_path.is_file():
            raise PsdSourceStoreError("psd_source_not_found")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            source = self._parse(payload, expected_source_id=source_id)
            if source_path.stat().st_size != source.inspection.byte_size:
                raise PsdSourceStoreError("psd_source_corrupt")
            digest = sha256()
            with source_path.open("rb") as stored_psd:
                while chunk := stored_psd.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != source_id:
                raise PsdSourceStoreError("psd_source_corrupt")
            return source
        except PsdSourceStoreError:
            raise
        except (OSError, TypeError, ValueError, KeyError) as error:
            raise PsdSourceStoreError("psd_source_corrupt") from error

    def artifact_path(self, source_id: str) -> Path:
        self.get(source_id)
        return self._root / source_id

    def raster_resource(self, source_id: str, layer_id: str) -> PsdRasterResource:
        return self.raster_resources(source_id, (layer_id,))[layer_id]

    def owned_visual_resource(
        self,
        source_id: str,
        *,
        anchor_id: str,
        group_id: str,
        owned_ids: frozenset[str],
        retained_ids: frozenset[str],
        visual_echo: bool = False,
        underlay: PsdRasterResource | None = None,
    ) -> PsdRasterResource:
        """Render an explicit PSD leaf partition into one existing visual object."""
        from figma_to_fgui.psd_effect_render import render_owned_visual

        if visual_echo:
            raise PsdSourceStoreError("psd_visual_echo_forbidden")
        source = self.get(source_id)
        if anchor_id not in owned_ids or not owned_ids or owned_ids & retained_ids:
            raise PsdSourceStoreError("psd_visual_ownership_incomplete")
        key_data = json.dumps(
            [
                "owned-visual-v14",
                source_id,
                group_id,
                sorted(owned_ids),
                sorted(retained_ids),
                visual_echo,
                (underlay.key, underlay.bounds) if underlay is not None else None,
            ],
            separators=(",", ":"),
        )
        key = "psd-owned-" + sha256(key_data.encode("utf-8")).hexdigest()[:32]
        root = self.artifact_path(source_id)
        destination = root / "resources" / key
        metadata_path = destination.with_suffix(".json")
        if destination.is_file() and metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text("utf-8"))
                bounds = tuple(metadata["bounds"])
                if (metadata["key_data"] == key_data and len(bounds) == 4
                    and metadata["sha256"] == sha256(destination.read_bytes()).hexdigest()):
                    with Image.open(destination) as image:
                        image.verify()
                    return PsdRasterResource(anchor_id, key, "image/png", destination.stat().st_size, bounds)
            except (OSError, ValueError, KeyError, TypeError, UnidentifiedImageError):
                pass
        try:
            document = PSDImage.open(root / "source.psd")
            image, bounds = render_owned_visual(document, source, group_id, owned_ids, retained_ids)  # type: ignore[assignment, arg-type]
            if image.width < 1 or image.height < 1:
                raise ValueError("psd_visual_ownership_incomplete")
            # ``render_owned_visual`` is the authoritative PSD-layer export.
            # Never copy pixels back from the merged preview here: that image
            # also contains retained text and neighbouring artwork owned by
            # other FGUI objects. Spatial calibration would therefore break
            # the ownership partition and create doubled text or dirty alpha
            # edges in the replacement resource.
            destination.parent.mkdir(exist_ok=True)
            temporary = destination.with_name(f".owned-{uuid.uuid4().hex}")
            try:
                image.save(temporary, format="PNG")
                content_hash = sha256(temporary.read_bytes()).hexdigest()
                temporary.replace(destination)
                metadata_path.write_text(json.dumps({
                    "key_data": key_data, "bounds": bounds, "sha256": content_hash,
                }), encoding="utf-8")
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError, UnidentifiedImageError) as error:
            raise PsdSourceStoreError("psd_visual_ownership_unsupported") from error
        return PsdRasterResource(anchor_id, key, "image/png", destination.stat().st_size, bounds)

    def composited_visual_resource(
        self,
        source_id: str,
        *,
        anchor_id: str,
        group_id: str,
        overlay_ids: frozenset[str],
    ) -> PsdRasterResource:
        """Bake a visible PSD overlay into its existing full-canvas image owner."""
        if not overlay_ids:
            raise PsdSourceStoreError("psd_visual_ownership_incomplete")
        retained_ids: frozenset[str] = frozenset()
        if group_id.startswith("psd-root:"):
            retained_ids = self.root_retained_layer_ids(source_id, overlay_ids)
        key_data = json.dumps(
            ["composited-visual-v4", source_id, anchor_id, group_id, sorted(overlay_ids)],
            separators=(",", ":"),
        )
        key = "psd-composite-" + sha256(key_data.encode("utf-8")).hexdigest()[:32]
        root = self.artifact_path(source_id)
        destination = root / "resources" / key
        metadata_path = destination.with_suffix(".json")
        if destination.is_file() and metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text("utf-8"))
                bounds = tuple(metadata["bounds"])
                if (
                    metadata["key_data"] == key_data
                    and len(bounds) == 4
                    and metadata["sha256"] == sha256(destination.read_bytes()).hexdigest()
                ):
                    with Image.open(destination) as image:
                        image.verify()
                    return PsdRasterResource(
                        anchor_id, key, "image/png", destination.stat().st_size, bounds
                    )
            except (OSError, ValueError, KeyError, TypeError, UnidentifiedImageError):
                pass
        try:
            base = self.raster_resource(source_id, anchor_id)
            overlay = self.owned_visual_resource(
                source_id,
                anchor_id=min(overlay_ids),
                group_id=group_id,
                owned_ids=overlay_ids,
                retained_ids=retained_ids,
            )
            if base.bounds is None or overlay.bounds is None:
                raise ValueError("psd_visual_ownership_incomplete")
            bounds = (
                min(base.bounds[0], overlay.bounds[0]),
                min(base.bounds[1], overlay.bounds[1]),
                max(base.bounds[2], overlay.bounds[2]),
                max(base.bounds[3], overlay.bounds[3]),
            )
            image = Image.new("RGBA", (bounds[2] - bounds[0], bounds[3] - bounds[1]))
            with Image.open(root / "resources" / base.key) as base_image:
                image.alpha_composite(
                    base_image.convert("RGBA"),
                    (base.bounds[0] - bounds[0], base.bounds[1] - bounds[1]),
                )
            with Image.open(root / "resources" / overlay.key) as overlay_image:
                image.alpha_composite(
                    overlay_image.convert("RGBA"),
                    (overlay.bounds[0] - bounds[0], overlay.bounds[1] - bounds[1]),
                )
            composite = root / "composite.png"
            if composite.is_file():
                # Spatially copy only opaque pixels already within one channel
                # value of the merged PSD.  Those are compositor rounding
                # differences, while foreground UI pixels differ by much more
                # and are never baked into the root background.
                image = _calibrate_opaque_owned_pixels(image, bounds, composite)
            destination.parent.mkdir(exist_ok=True)
            temporary = destination.with_name(f".composite-{uuid.uuid4().hex}")
            try:
                image.save(temporary, format="PNG")
                content_hash = sha256(temporary.read_bytes()).hexdigest()
                temporary.replace(destination)
                metadata_path.write_text(
                    json.dumps({
                        "key_data": key_data,
                        "bounds": bounds,
                        "sha256": content_hash,
                    }),
                    encoding="utf-8",
                )
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError, TypeError, UnidentifiedImageError) as error:
            raise PsdSourceStoreError("psd_visual_ownership_unsupported") from error
        return PsdRasterResource(
            anchor_id, key, "image/png", destination.stat().st_size, bounds
        )

    def root_retained_layer_ids(
        self,
        source_id: str,
        owned_ids: frozenset[str],
    ) -> frozenset[str]:
        """Return the exact visible root-leaf complement for a root partition."""
        source = self.get(source_id)
        parent_ids = {
            layer.parent_id
            for layer in source.layers
            if layer.effective_visible and layer.parent_id is not None
        }
        return frozenset(
            layer.id
            for layer in source.layers
            if layer.id not in parent_ids
            and layer.id not in owned_ids
            and layer.effective_visible
            and layer.bounds[2] > layer.bounds[0]
            and layer.bounds[3] > layer.bounds[1]
            and layer.kind in {"type", "shape", "pixel", "smartobject"}
        )

    def raster_resources(
        self, source_id: str, layer_ids: tuple[str, ...]
    ) -> dict[str, PsdRasterResource]:
        source = self.get(source_id)
        by_id = {layer.id: layer for layer in source.layers}
        source_root = self._root / source_id
        resources = source_root / "resources"
        resources.mkdir(exist_ok=True)
        result: dict[str, PsdRasterResource] = {}
        pending: list[tuple[PsdLayer, str, Path, int, int]] = []
        for layer_id in dict.fromkeys(layer_ids):
            layer = by_id.get(layer_id)
            if layer is None:
                raise PsdSourceStoreError("psd_layer_not_found")
            if layer.kind.casefold() == "group":
                raise PsdSourceStoreError("psd_group_requires_leaf_mapping")
            if layer.kind.casefold() not in {"pixel", "shape", "smartobject"}:
                raise PsdSourceStoreError("psd_layer_raster_unsupported")
            width = layer.bounds[2] - layer.bounds[0]
            height = layer.bounds[3] - layer.bounds[1]
            if width <= 0 or height <= 0:
                raise PsdSourceStoreError("psd_layer_raster_unavailable")
            # v5 invalidates resources produced before render_leaf painted
            # enabled outside strokes/drop shadows into the expanded viewport.
            key = "psd-" + sha256(("isolated-leaf-v5\0" + layer.id).encode("utf-8")).hexdigest()[:32]
            destination = resources / key
            metadata_path = resources / (key + ".json")
            if destination.is_file() and metadata_path.is_file():
                try:
                    metadata = json.loads(metadata_path.read_text("utf-8"))
                    bounds = tuple(metadata["bounds"])
                    if (len(bounds) != 4 or not all(isinstance(v, int) for v in bounds)
                        or metadata["layer_id"] != layer.id or metadata["source_bounds"] != list(layer.bounds)
                        or metadata["sha256"] != sha256(destination.read_bytes()).hexdigest()):
                        raise PsdSourceStoreError("psd_layer_raster_corrupt")
                    with Image.open(destination) as image:
                        if (
                            image.format != "PNG"
                            or image.mode != "RGBA"
                            or image.size != (bounds[2]-bounds[0], bounds[3]-bounds[1])
                        ):
                            raise PsdSourceStoreError("psd_layer_raster_corrupt")
                        image.verify()
                    result[layer.id] = PsdRasterResource(
                        layer_id=layer.id,
                        key=key,
                        mime_type="image/png",
                        size=destination.stat().st_size,
                        bounds=bounds,
                    )
                    continue
                except (OSError, UnidentifiedImageError, ValueError, KeyError, TypeError) as error:
                    raise PsdSourceStoreError("psd_layer_raster_corrupt") from error
            pending.append((layer, key, destination, width, height))

        if pending:
            try:
                document = PSDImage.open(source_root / "source.psd")
                document_layers = list(document.descendants())
            except (OSError, TypeError, ValueError) as error:
                raise PsdSourceStoreError("psd_layer_raster_unavailable") from error
            for layer, key, destination, width, height in pending:
                temporary = resources / f".tmp-{uuid.uuid4().hex[:8]}"
                try:
                    if layer.document_index >= len(document_layers):
                        raise PsdSourceStoreError("psd_layer_raster_unavailable")
                    source_layer = document_layers[layer.document_index]
                    from figma_to_fgui.psd_effect_render import render_leaf
                    try:
                        image, bounds = render_leaf(source_layer, layer)  # type: ignore[assignment]
                    except ValueError as error:
                        code = ("psd_stroke_only_raster_unsupported"
                                if str(error) == "psd_stroke_only_raster_unsupported"
                                else "psd_layer_raster_unsupported")
                        raise PsdSourceStoreError(code) from error
                    if image is None or image.size != (bounds[2]-bounds[0], bounds[3]-bounds[1]):
                        raise PsdSourceStoreError("psd_layer_raster_unavailable")
                    image.convert("RGBA").save(temporary, format="PNG")
                    temporary.replace(destination)
                    metadata = {"layer_id": layer.id, "source_bounds":layer.bounds, "bounds":bounds,
                                "sha256":sha256(destination.read_bytes()).hexdigest()}
                    metadata_temporary = temporary.with_suffix(".json")
                    metadata_temporary.write_text(json.dumps(metadata), encoding="utf-8")
                    metadata_temporary.replace(resources / (key + ".json"))
                    result[layer.id] = PsdRasterResource(
                        layer_id=layer.id,
                        key=key,
                        mime_type="image/png",
                        size=destination.stat().st_size,
                        bounds=bounds,
                    )
                except PsdSourceStoreError:
                    raise
                except (ImportError, OSError, UnidentifiedImageError, ValueError) as error:
                    raise PsdSourceStoreError("psd_layer_raster_unavailable") from error
                finally:
                    temporary.unlink(missing_ok=True)
                    temporary.with_suffix(".json").unlink(missing_ok=True)
        return result

    def composite_path(self, source_id: str) -> Path:
        source = self.get(source_id)
        source_root = self._root / source_id
        composite = source_root / "composite.png"
        if composite.is_file():
            try:
                with Image.open(composite) as image:
                    if image.format != "PNG" or image.size != (
                        source.inspection.width,
                        source.inspection.height,
                    ):
                        raise PsdSourceStoreError("psd_composite_corrupt")
                    image.verify()
                return composite
            except (OSError, UnidentifiedImageError) as error:
                raise PsdSourceStoreError("psd_composite_corrupt") from error

        temporary = source_root / f".composite-{uuid.uuid4().hex}.png"
        try:
            document = PSDImage.open(source_root / "source.psd")
            composite_image: Image.Image = document.topil(apply_icc=True)  # type: ignore[assignment]
            if composite_image is None:
                raise PsdSourceStoreError("psd_composite_unavailable")
            if composite_image.size != (source.inspection.width, source.inspection.height):
                raise PsdSourceStoreError("psd_composite_corrupt")
            composite_image.save(temporary, format="PNG")
            temporary.replace(composite)
            return composite
        except PsdSourceStoreError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as error:
            raise PsdSourceStoreError("psd_composite_unavailable") from error
        finally:
            temporary.unlink(missing_ok=True)

    def effective_viewport_bounds(
        self, source_id: str, width: int, height: int
    ) -> tuple[int, int, int, int]:
        source = self.get(source_id)
        if (
            width <= 0
            or height <= 0
            or width > source.inspection.width
            or height > source.inspection.height
        ):
            raise PsdSourceStoreError("psd_viewport_dimensions_invalid")
        centered_left = (source.inspection.width - width) // 2
        centered_top = (source.inspection.height - height) // 2
        dimension_tokens = {str(width), str(height)}

        candidates: list[tuple[tuple[int, int, int, int], PsdLayer]] = []
        for layer in source.layers:
            layer_width = layer.bounds[2] - layer.bounds[0]
            layer_height = layer.bounds[3] - layer.bounds[1]
            if layer_width != width or layer_height != height:
                continue
            name_tokens = set(re.findall(r"\d+", layer.name))
            dimension_name = dimension_tokens.issubset(name_tokens)
            distance = abs(layer.bounds[0] - centered_left) + abs(
                layer.bounds[1] - centered_top
            )
            candidates.append(
                (
                    (
                        0 if dimension_name else 1,
                        0 if layer.parent_id is None else 1,
                        distance,
                        layer.document_index,
                    ),
                    layer,
                )
            )

        if candidates:
            marker = min(candidates, key=lambda item: item[0])[1]
            left = max(0, min(marker.bounds[0], source.inspection.width - width))
            top = max(0, min(marker.bounds[1], source.inspection.height - height))
        else:
            left = centered_left
            top = centered_top
        return left, top, width, height

    def composite_viewport_path(self, source_id: str, width: int, height: int) -> Path:
        left, top, width, height = self.effective_viewport_bounds(
            source_id, width, height
        )
        return self.composite_region_path(source_id, left, top, width, height)

    def composite_region_path(
        self, source_id: str, left: int, top: int, width: int, height: int
    ) -> Path:
        source = self.get(source_id)
        if (
            left < 0
            or top < 0
            or width <= 0
            or height <= 0
            or left + width > source.inspection.width
            or top + height > source.inspection.height
        ):
            raise PsdSourceStoreError("psd_viewport_dimensions_invalid")
        source_root = self._root / source_id
        viewport = source_root / f"viewport-{left}-{top}-{width}x{height}.png"
        if viewport.is_file():
            try:
                with Image.open(viewport) as image:
                    if image.format != "PNG" or image.size != (width, height):
                        raise PsdSourceStoreError("psd_viewport_corrupt")
                    image.verify()
                return viewport
            except (OSError, UnidentifiedImageError) as error:
                raise PsdSourceStoreError("psd_viewport_corrupt") from error

        temporary = source_root / f".viewport-{uuid.uuid4().hex}.png"
        try:
            with Image.open(self.composite_path(source_id)) as composite:
                composite.crop((left, top, left + width, top + height)).save(
                    temporary, format="PNG"
                )
            temporary.replace(viewport)
            return viewport
        except PsdSourceStoreError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as error:
            raise PsdSourceStoreError("psd_viewport_unavailable") from error
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _payload(source_id: str, analysis: PsdAnalysis) -> dict[str, Any]:
        return {
            "version": 1,
            "source_id": source_id,
            "inspection": asdict(analysis.inspection),
            "layers": [asdict(layer) for layer in analysis.layers],
        }

    @staticmethod
    def payload(source: PsdSource) -> dict[str, Any]:
        return {
            "version": source.version,
            "source_id": source.source_id,
            "inspection": asdict(source.inspection),
            "layers": [asdict(layer) for layer in source.layers],
        }

    @staticmethod
    def _parse(payload: Any, *, expected_source_id: str) -> PsdSource:
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise PsdSourceStoreError("psd_source_corrupt")
        if payload.get("source_id") != expected_source_id:
            raise PsdSourceStoreError("psd_source_corrupt")
        inspection_data = payload.get("inspection")
        layers_data = payload.get("layers")
        if not isinstance(inspection_data, dict) or not isinstance(layers_data, list):
            raise PsdSourceStoreError("psd_source_corrupt")
        inspection = PsdInspection(
            **{
                **inspection_data,
                "blocking_issues": tuple(inspection_data["blocking_issues"]),
                "warnings": tuple(inspection_data["warnings"]),
            }
        )
        if inspection.sha256 != expected_source_id:
            raise PsdSourceStoreError("psd_source_corrupt")
        layers = tuple(
            PsdSourceStore._parse_layer(item)
            for item in layers_data
            if isinstance(item, dict)
        )
        if len(layers) != len(layers_data) or len(layers) != inspection.layer_count:
            raise PsdSourceStoreError("psd_source_corrupt")
        if len({layer.id for layer in layers}) != len(layers):
            raise PsdSourceStoreError("psd_source_corrupt")
        return PsdSource(version=1, source_id=expected_source_id, inspection=inspection, layers=layers)

    @staticmethod
    def _parse_layer(item: dict[str, Any]) -> PsdLayer:
        text_style_data = item.get("text_style")
        text_style: PsdTextStyle | None = None
        if isinstance(text_style_data, dict):
            run_items = text_style_data.get("runs")
            transform = text_style_data.get("transform")
            if not isinstance(run_items, list) or not isinstance(transform, list):
                raise PsdSourceStoreError("psd_source_corrupt")
            runs = tuple(
                PsdTextRun(
                    **{
                        **run,
                        "fill_rgba": (
                            tuple(run["fill_rgba"])
                            if run.get("fill_rgba") is not None
                            else None
                        ),
                    }
                )
                for run in run_items
                if isinstance(run, dict)
            )
            if len(runs) != len(run_items) or len(transform) != 6:
                raise PsdSourceStoreError("psd_source_corrupt")
            text_style = PsdTextStyle(
                runs=runs,
                transform=(
                    float(transform[0]),
                    float(transform[1]),
                    float(transform[2]),
                    float(transform[3]),
                    float(transform[4]),
                    float(transform[5]),
                ),
                paragraph_justification=text_style_data.get("paragraph_justification"),
                anti_alias=text_style_data.get("anti_alias"),
            )
        effect_items = item.get("effects", [])
        if not isinstance(effect_items, list) or not all(
            isinstance(effect, dict) for effect in effect_items
        ):
            raise PsdSourceStoreError("psd_source_corrupt")
        effects = tuple(
            PsdLayerEffect(
                **{
                    **effect,
                    "color_rgba": (
                        tuple(effect["color_rgba"])
                        if effect.get("color_rgba") is not None
                        else None
                    ),
                }
            )
            for effect in effect_items
        )
        return PsdLayer(
            **{
                **item,
                "path": tuple(item["path"]),
                "bounds": tuple(item["bounds"]),
                "text_style": text_style,
                "effects": effects,
            }
        )
