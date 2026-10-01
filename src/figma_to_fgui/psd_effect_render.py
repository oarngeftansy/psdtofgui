"""Bounded PSD rendering with explicit leaf ownership; never a merged-page crop.

RGBA8 output still requires Photoshop/Editor equivalence verification.
Unsupported effects fail instead of silently disappearing.
"""

from __future__ import annotations

import math
from typing import Any

import aggdraw
from PIL import Image, ImageChops, ImageFilter
from psd_tools.constants import Tag

from figma_to_fgui.psd_intake import PsdAnalysis, PsdLayer, PsdLayerEffect
from figma_to_fgui.psd_stroke_render import render_stroke_only, stroke_viewport


def composite_hard_shadow(body: Image.Image, effect: PsdLayerEffect) -> Image.Image:
    values = (effect.opacity, effect.angle, effect.distance, *(effect.color_rgba or ()))
    if (
        effect.opacity is None or effect.angle is None or effect.distance is None
    ):
        raise ValueError("psd_shadow_unsupported")
    if (
        effect.kind != "DropShadow"
        or effect.blend_mode != "normal"
        or effect.size != 0
        or effect.choke not in (None, 0)
        or effect.spread not in (None, 0)
        or effect.color_rgba is None
        or len(effect.color_rgba) != 4
        or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
        or not 0 <= effect.opacity <= 100
        or not 0 <= effect.distance <= 256
        or not all(0 <= v <= 1 for v in effect.color_rgba)
    ):
        raise ValueError("psd_shadow_unsupported")
    angle = math.radians(effect.angle)
    dx, dy = -math.cos(angle) * effect.distance, math.sin(angle) * effect.distance
    alpha = body.getchannel("A").transform(
        body.size,
        Image.Transform.AFFINE,
        (1, 0, -dx, 0, 1, -dy),
        resample=Image.Resampling.BILINEAR,
        fillcolor=0,
    )
    alpha = alpha.point(
        [round(v * effect.opacity / 100 * effect.color_rgba[3]) for v in range(256)]
    )
    result = Image.new(
        "RGBA", body.size, tuple(round(v * 255) for v in effect.color_rgba[:3]) + (0,)
    )
    result.putalpha(alpha)
    result.alpha_composite(body)
    return result


def composite_visual_effects(
    body: Image.Image,
    effects: tuple[PsdLayerEffect, ...],
) -> Image.Image:
    """Add effect pixels that psd-tools clips to the logical layer bbox."""
    original_alpha = body.getchannel("A")
    result = Image.new("RGBA", body.size)
    for effect in effects:
        if not effect.enabled or effect.kind != "DropShadow":
            continue
        values = (
            effect.opacity,
            effect.angle,
            effect.distance,
            effect.size,
            *(effect.color_rgba or ()),
        )
        if (
            effect.blend_mode != "normal"
            or effect.opacity is None
            or effect.angle is None
            or effect.distance is None
            or effect.size is None
            or effect.choke not in (None, 0)
            or effect.spread not in (None, 0)
            or effect.color_rgba is None
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)
            or not 0 <= effect.opacity <= 100
            or not 0 <= effect.distance <= 256
            or not 0 <= effect.size <= 256
            or not all(0 <= value <= 1 for value in effect.color_rgba)
        ):
            raise ValueError("psd_shadow_unsupported")
        angle = math.radians(effect.angle)
        dx = -math.cos(angle) * effect.distance
        dy = math.sin(angle) * effect.distance
        alpha = original_alpha
        if effect.size > 0:
            alpha = alpha.filter(ImageFilter.GaussianBlur(radius=effect.size))
        alpha = alpha.transform(
            body.size,
            Image.Transform.AFFINE,
            (1, 0, -dx, 0, 1, -dy),
            resample=Image.Resampling.BILINEAR,
            fillcolor=0,
        )
        alpha = alpha.point(
            [round(value * effect.opacity / 100 * effect.color_rgba[3]) for value in range(256)]
        )
        shadow = Image.new(
            "RGBA",
            body.size,
            tuple(round(value * 255) for value in effect.color_rgba[:3]) + (0,),
        )
        shadow.putalpha(alpha)
        result.alpha_composite(shadow)

    for effect in effects:
        if (
            not effect.enabled
            or effect.kind != "Stroke"
            or effect.position != "outside"
        ):
            continue
        if (
            effect.blend_mode != "normal"
            or effect.opacity is None
            or effect.size is None
            or effect.color_rgba is None
            or not math.isfinite(effect.size)
            or not 0 <= effect.size <= 256
            or not 0 <= effect.opacity <= 100
            or not all(0 <= value <= 1 for value in effect.color_rgba)
        ):
            raise ValueError("psd_stroke_unsupported")
        radius = math.ceil(effect.size)
        if radius <= 0:
            continue
        expanded = original_alpha.filter(ImageFilter.MaxFilter(radius * 2 + 1))
        ring = ImageChops.subtract(expanded, original_alpha).point(
            [round(value * effect.opacity / 100 * effect.color_rgba[3]) for value in range(256)]
        )
        stroke = Image.new(
            "RGBA",
            body.size,
            tuple(round(value * 255) for value in effect.color_rgba[:3]) + (0,),
        )
        stroke.putalpha(ring)
        result.alpha_composite(stroke)

    result.alpha_composite(body)
    return result


def _effects(metadata: PsdLayer) -> tuple[PsdLayerEffect, ...]:
    return tuple(e for e in metadata.effects if e.enabled)


def _render_degenerate_vector_shape(
    layer: Any,
    viewport: tuple[int, int, int, int],
) -> Image.Image | None:
    """Rasterize a valid document-space path whose PSD layer bbox is corrupt."""
    layer_bounds = tuple(getattr(layer, "bbox", (0, 0, 0, 0)))
    if len(layer_bounds) != 4 or (
        layer_bounds[2] > layer_bounds[0] and layer_bounds[3] > layer_bounds[1]
    ):
        return None
    mask = getattr(layer, "vector_mask", None)
    document = getattr(layer, "_psd", None)
    paths = getattr(mask, "paths", ()) if mask is not None else ()
    if (
        mask is None
        or getattr(mask, "inverted", False)
        or document is None
        or not paths
        or getattr(layer, "stroke", None) is not None
    ):
        return None
    try:
        fill = layer.tagged_blocks.get_data(Tag.SOLID_COLOR_SHEET_SETTING)
        color = fill[b"Clr "]
        channels = tuple(round(float(color[key])) for key in (b"Rd  ", b"Grn ", b"Bl  "))
        fill_opacity = float(layer.tagged_blocks.get_data(Tag.BLEND_FILL_OPACITY, 255))
        layer_opacity = float(getattr(layer, "opacity", 255))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if (
        not all(0 <= value <= 255 for value in channels)
        or not math.isfinite(fill_opacity)
        or not math.isfinite(layer_opacity)
        or not 0 <= fill_opacity <= 255
        or not 0 <= layer_opacity <= 255
    ):
        return None
    width, height = viewport[2] - viewport[0], viewport[3] - viewport[1]
    scale = 4
    image = Image.new("RGBA", (width * scale, height * scale))
    drawing = aggdraw.Draw(image)
    alpha = round(fill_opacity * layer_opacity / 255)
    brush = aggdraw.Brush((*channels, alpha))

    def point(value: Any) -> tuple[float, float]:
        # psd-tools exposes knot points as (vertical, horizontal).
        y, x = value
        return (
            (float(x) * document.width - viewport[0]) * scale,
            (float(y) * document.height - viewport[1]) * scale,
        )

    try:
        for source_path in paths:
            knots = tuple(source_path)
            if len(knots) < 2:
                return None
            path = aggdraw.Path()
            path.moveto(*point(knots[0].anchor))
            for index, current in enumerate(knots):
                following = knots[(index + 1) % len(knots)]
                path.curveto(
                    *point(current.leaving),
                    *point(following.preceding),
                    *point(following.anchor),
                )
            path.close()
            drawing.path(path, brush)
        drawing.flush()
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    return image.resize((width, height), Image.Resampling.LANCZOS)


def layer_viewport(layer: Any, metadata: PsdLayer) -> tuple[int, int, int, int]:
    bounds = metadata.bounds
    if not getattr(getattr(layer, "stroke", None), "fill_enabled", True):
        try:
            bounds = stroke_viewport(layer)
        except (AttributeError, KeyError, IndexError, TypeError) as error:
            raise ValueError("psd_stroke_only_raster_unsupported") from error
    padding = 0
    for effect in _effects(metadata):
        values = (effect.size or 0, effect.distance or 0)
        if not all(math.isfinite(v) and 0 <= v <= 256 for v in values):
            raise ValueError("psd_effect_unsupported")
        padding = max(padding, math.ceil(sum(values)) + 2)
    return bounds[0] - padding, bounds[1] - padding, bounds[2] + padding, bounds[3] + padding


def _inherit_overlays(image: Image.Image, layer: Any) -> Image.Image:
    ancestor = getattr(layer, "parent", None)
    while ancestor is not None:
        if (
            getattr(ancestor, "opacity", 255) != 255
            or getattr(ancestor, "clipping", False)
            or getattr(ancestor, "has_mask", lambda: False)()
            or getattr(ancestor, "has_vector_mask", lambda: False)()
            or getattr(getattr(ancestor, "blend_mode", None), "value", b"norm")
            not in {b"norm", b"pass"}
        ):
            raise ValueError("psd_owned_context_unsupported")
        effects = getattr(ancestor, "effects", None)
        if effects is not None and effects.enabled:
            enabled = [e for e in effects if e.enabled]
            overlays = list(effects.find("ColorOverlay"))
            if len(enabled) != len(overlays) or len(overlays) > 1:
                raise ValueError("psd_owned_context_unsupported")
            for effect in overlays:
                if effect.opacity != 100 or effect.blend_mode not in {b"norm", b"normal", b"Nrml"}:
                    raise ValueError("psd_owned_context_unsupported")
                channels = tuple(float(effect.color[k]) for k in (b"Rd  ", b"Grn ", b"Bl  "))
                if not all(math.isfinite(v) and 0 <= v <= 255 for v in channels):
                    raise ValueError("psd_owned_context_unsupported")
                tinted = Image.new("RGBA", image.size, tuple(round(v) for v in channels) + (0,))
                tinted.putalpha(image.getchannel("A"))
                image = tinted
        ancestor = getattr(ancestor, "parent", None)
    return image


def render_leaf(
    layer: Any,
    metadata: PsdLayer,
    *,
    viewport: tuple[int, int, int, int] | None = None,
    apply_ancestors: bool = True,
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    effects = _effects(metadata)
    if any(
        e.kind not in {"ColorOverlay", "GradientOverlay", "PatternOverlay", "Stroke", "DropShadow"}
        for e in effects
    ):
        raise ValueError("psd_effect_unsupported")
    viewport = viewport or layer_viewport(layer, metadata)
    width, height = viewport[2] - viewport[0], viewport[3] - viewport[1]
    if width <= 0 or height <= 0 or width * height > 16_000_000:
        raise ValueError("psd_raster_bounds_unsupported")
    recovered = _render_degenerate_vector_shape(layer, viewport)
    if recovered is not None:
        image = recovered
    elif not getattr(getattr(layer, "stroke", None), "fill_enabled", True):
        image = render_stroke_only(layer, viewport=viewport)
    else:
        image = layer.composite(viewport=viewport, force=True, alpha=0.0)
    if image is None:
        raise ValueError("psd_layer_raster_unavailable")
    image = image.convert("RGBA")
    image = composite_visual_effects(image, effects)
    if apply_ancestors:
        image = _inherit_overlays(image, layer)
    return image, viewport


def validate_owned_partition(
    leaves: list[PsdLayer], owned: frozenset[str], retained: frozenset[str]
) -> None:
    # A group with no visible descendants has no pixel source. A mask on that
    # empty group can only remove pixels, so it cannot contribute artwork.
    # Effects and non-normal compositing remain blocked until rendered proof.
    visible = {l.id: l for l in leaves if l.effective_visible
        and l.bounds[2] > l.bounds[0] and l.bounds[3] > l.bounds[1]
        and not (
            l.kind == "group" and not l.has_effects and not l.clipping
            and l.opacity == 255 and l.blend_mode in {"normal", "pass_through"}
        )}
    if (
        not owned
        or owned & retained
        or owned | retained != set(visible)
        or any(
            visible[key].kind not in {"shape", "pixel", "smartobject"}
            for key in owned
        )
        or any(
            visible[key].kind not in {"type", "shape", "pixel", "smartobject"}
            for key in retained
        )
    ):
        raise ValueError("psd_visual_ownership_incomplete")


def render_owned_visual(
    document: Any,
    source: PsdAnalysis,
    group_id: str,
    owned: frozenset[str],
    retained: frozenset[str],
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Render declared visual leaves; retained/delegated leaves are never baked in.

    A retained visual leaf can be owned by another existing FGUI sibling.  Each
    resource still declares the complete group partition, while rendering only
    its own portion. This verifies PSD ownership only; it cannot approve an
    FGUI correspondence.
    """
    metadata = {l.id: l for l in source.layers}
    children: dict[str, list[PsdLayer]] = {}
    for item in source.layers:
        if item.effective_visible:
            children.setdefault(item.parent_id or "", []).append(item)
    group = metadata.get(group_id)
    root_partition = group is None and group_id.startswith("psd-root:")
    if not root_partition and (group is None or group.kind != "group"):
        raise ValueError("psd_visual_ownership_incomplete")
    descendants: list[PsdLayer] = []

    def collect(item: PsdLayer) -> None:
        descendants.append(item)
        for child in children.get(item.id or "", ()):
            collect(child)

    if root_partition:
        for root_child in children.get("", ()):
            collect(root_child)
    else:
        assert group is not None
        collect(group)
    leaves = [l for l in descendants if not children.get(l.id or "")]
    validate_owned_partition(leaves, owned, retained)
    ancestor = None if group is None else metadata.get(group.parent_id or "")
    while ancestor is not None:
        if (
            ancestor.has_effects
            or ancestor.has_pixel_mask
            or ancestor.has_vector_mask
            or ancestor.opacity != 255
            or ancestor.clipping
            or ancestor.blend_mode not in {"normal", "pass_through"}
        ):
            raise ValueError("psd_owned_context_unsupported")
        ancestor = metadata.get(ancestor.parent_id or "")
    actual = list(document.descendants())
    allowed_ids = set(owned)
    for leaf in leaves:
        if leaf.id not in owned:
            continue
        parent = metadata.get(leaf.parent_id or "")
        while parent is not None:
            allowed_ids.add(parent.id)
            parent = metadata.get(parent.parent_id or "")
    effect_context = [
        metadata[layer_id]
        for layer_id in allowed_ids
        if metadata[layer_id].has_effects
    ]
    boxes = [
        layer_viewport(actual[layer.document_index], layer)
        for layer in (*[leaf for leaf in leaves if leaf.id in owned], *effect_context)
    ]
    viewport = (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
    size = viewport[2] - viewport[0], viewport[3] - viewport[1]
    if size[0] * size[1] > 16_000_000:
        raise ValueError("psd_raster_bounds_unsupported")

    def draw(item: PsdLayer) -> Image.Image:
        if item.id in retained:
            return Image.new("RGBA", size)
        if item.blend_mode not in {"normal", "pass_through"} or item.clipping:
            raise ValueError("psd_owned_context_unsupported")
        if item.kind != "group":
            return render_leaf(
                actual[item.document_index], item, viewport=viewport, apply_ancestors=False
            )[0]
        if not children.get(item.id) and not item.has_effects:
            return Image.new("RGBA", size)
        if item.has_pixel_mask or item.has_vector_mask or item.opacity != 255:
            raise ValueError("psd_owned_context_unsupported")
        effects = _effects(item)
        if (
            any(
                effect.kind != "ColorOverlay"
                or effect.blend_mode != "normal"
                or effect.opacity != 100
                or effect.color_rgba is None
                for effect in effects
            )
            or len(effects) > 1
        ):
            raise ValueError("psd_owned_context_unsupported")
        if effects:
            branch: list[str] = []

            def ids(node: PsdLayer) -> None:
                branch.append(node.id)
                for child in children.get(node.id, ()):
                    ids(child)

            ids(item)
            if retained.intersection(branch):
                raise ValueError("psd_owned_context_unsupported")
        image = Image.new("RGBA", size)
        for child in sorted(children.get(item.id, ()), key=lambda layer: layer.sibling_index):
            image.alpha_composite(draw(child))
        if effects:
            color = effects[0].color_rgba or (0.0, 0.0, 0.0, 1.0)
            tinted = Image.new(
                "RGBA",
                size,
                tuple(round(value * 255) for value in color[:3]) + (0,),
            )
            tinted.putalpha(image.getchannel("A"))
            image = tinted
        return image

    def draw_partition() -> Image.Image:
        if group is not None:
            return draw(group)
        image = Image.new("RGBA", size)
        for child in sorted(children.get("", ()), key=lambda layer: layer.sibling_index):
            image.alpha_composite(draw(child))
        return image

    def crop_to_painted_bounds(
        image: Image.Image,
        image_viewport: tuple[int, int, int, int],
    ) -> tuple[Image.Image, tuple[int, int, int, int]]:
        painted = image.getbbox()
        if painted is None:
            raise ValueError("psd_visual_ownership_incomplete")
        cropped_viewport = (
            image_viewport[0] + painted[0],
            image_viewport[1] + painted[1],
            image_viewport[0] + painted[2],
            image_viewport[1] + painted[3],
        )
        return image.crop(painted), cropped_viewport

    try:
        return crop_to_painted_bounds(draw_partition(), viewport)
    except ValueError:
        # Complex masks/blends that the deterministic recursive renderer
        # cannot express still use psd-tools' filtered compositor. Keeping
        # this as a fallback avoids regressing groups where the native engine
        # is required, while simple UI skins retain correct group-effect
        # boundaries and text exclusion above.
        fallback_viewport = viewport
        allowed_actual = {
            id(actual[metadata[layer_id].document_index]) for layer_id in allowed_ids
        }
        image = document.composite(
            viewport=fallback_viewport,
            force=True,
            alpha=0.0,
            layer_filter=lambda layer: id(layer) in allowed_actual,
        )
        expected_size = (
            fallback_viewport[2] - fallback_viewport[0],
            fallback_viewport[3] - fallback_viewport[1],
        )
        if image is None or image.size != expected_size:
            raise ValueError("psd_visual_ownership_incomplete")
        image = image.convert("RGBA")
        # psd-tools drops shape layers whose ordinary layer rectangle is empty,
        # even when their document-space vector mask is valid. Restore those
        # owned pixels into the same resource after the filtered fallback and
        # replay supported ancestor effects (notably a group's outside stroke).
        for leaf in leaves:
            if leaf.id not in owned or not leaf.has_vector_mask:
                continue
            actual_leaf = actual[leaf.document_index]
            actual_bounds = tuple(getattr(actual_leaf, "bbox", (0, 0, 0, 0)))
            if len(actual_bounds) != 4 or (
                actual_bounds[2] > actual_bounds[0]
                and actual_bounds[3] > actual_bounds[1]
            ):
                continue
            recovered, _ = render_leaf(
                actual_leaf,
                leaf,
                viewport=fallback_viewport,
                apply_ancestors=False,
            )
            parent = metadata.get(leaf.parent_id or "")
            while parent is not None and parent.id in allowed_ids:
                if (
                    parent.has_pixel_mask
                    or parent.has_vector_mask
                    or parent.opacity != 255
                    or parent.clipping
                    or parent.blend_mode not in {"normal", "pass_through"}
                ):
                    raise ValueError("psd_owned_context_unsupported")
                parent_effects = _effects(parent)
                overlays = tuple(effect for effect in parent_effects if effect.kind == "ColorOverlay")
                pixel_effects = tuple(
                    effect for effect in parent_effects if effect.kind in {"Stroke", "DropShadow"}
                )
                if len(overlays) > 1 or len(overlays) + len(pixel_effects) != len(parent_effects):
                    raise ValueError("psd_owned_context_unsupported")
                if overlays:
                    overlay = overlays[0]
                    if (
                        overlay.blend_mode != "normal"
                        or overlay.opacity != 100
                        or overlay.color_rgba is None
                    ):
                        raise ValueError("psd_owned_context_unsupported")
                    tinted = Image.new(
                        "RGBA",
                        recovered.size,
                        tuple(round(value * 255) for value in overlay.color_rgba[:3]) + (0,),
                    )
                    tinted.putalpha(recovered.getchannel("A"))
                    recovered = tinted
                if pixel_effects:
                    recovered = composite_visual_effects(recovered, pixel_effects)
                parent = metadata.get(parent.parent_id or "")
            image.alpha_composite(recovered)
        for leaf in leaves:
            if leaf.id not in owned or not any(
                effect.enabled
                and (
                    effect.kind == "DropShadow"
                    or (effect.kind == "Stroke" and effect.position == "outside")
                )
                for effect in leaf.effects
            ):
                continue
            try:
                overflow, _ = render_leaf(
                    actual[leaf.document_index],
                    leaf,
                    viewport=fallback_viewport,
                )
            except ValueError:
                continue
            overflow = overflow.copy()
            left = max(0, leaf.bounds[0] - fallback_viewport[0])
            top = max(0, leaf.bounds[1] - fallback_viewport[1])
            right = min(expected_size[0], leaf.bounds[2] - fallback_viewport[0])
            bottom = min(expected_size[1], leaf.bounds[3] - fallback_viewport[1])
            if left < right and top < bottom:
                overflow.paste((0, 0, 0, 0), (left, top, right, bottom))
            image.alpha_composite(overflow)
        return crop_to_painted_bounds(image, fallback_viewport)
