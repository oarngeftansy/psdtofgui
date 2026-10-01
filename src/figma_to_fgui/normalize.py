import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self, cast

from figma_to_fgui.fgui_asset_payloads import (
    MAX_ASSET_PAYLOAD_BYTES,
    _inspect_raster_in_isolated_process,
    read_bounded_stable_asset_file,
)
from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.models import (
    Bounds,
    Diagnostic,
    NormalizedNode,
    NormalizedResourceReference,
)
from figma_to_fgui.svg_security import safe_svg_dimensions

MAX_NORMALIZED_TREE_DEPTH = 256


def _guard_raw_tree_depth(roots: tuple[dict[str, Any], ...]) -> None:
    pending = [(root, 1) for root in roots]
    while pending:
        node, depth = pending.pop()
        if depth > MAX_NORMALIZED_TREE_DEPTH:
            raise ValueError("source tree depth exceeds the supported limit")
        children = node.get("children", ())
        if isinstance(children, (list, tuple)):
            pending.extend(
                (child, depth + 1)
                for child in children
                if isinstance(child, dict)
            )


@dataclass(frozen=True)
class SelectionAsset:
    asset: str
    mime_type: str
    source_path: Path
    size: int
    sha256: str
    artifact_fingerprint: str


class SelectionDocument(dict[str, object]):
    """A mapping-shaped document with private conversion-only asset context."""

    def __init__(
        self,
        raw: dict[str, object],
        selection_assets: tuple[SelectionAsset, ...],
        warning_codes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(raw)
        self._selection_assets = selection_assets
        self._warning_codes = warning_codes

    @property
    def _conversion_assets(self) -> tuple[SelectionAsset, ...]:
        return self._selection_assets

    @property
    def _selection_warning_codes(self) -> tuple[str, ...]:
        return self._warning_codes

    def copy(self) -> Self:
        return type(self)(dict(self), self._selection_assets, self._warning_codes)


@dataclass(frozen=True)
class SelectionConversionDocument:
    raw: SelectionDocument
    assets: tuple[SelectionAsset, ...]


def _node(raw: dict[str, Any], source_order: int) -> NormalizedNode:
    box = raw["absoluteBoundingBox"]
    raw_children = raw.get("children", [])
    children = tuple(_node(child, index) for index, child in enumerate(raw_children))
    properties = {
        name: value.get("value", "")
        for name, value in raw.get("componentProperties", {}).items()
    }
    for source_key, target_key in {
        "layoutMode": "layout_mode",
        "itemSpacing": "item_spacing",
        "paddingTop": "padding_top",
        "paddingRight": "padding_right",
        "paddingBottom": "padding_bottom",
        "paddingLeft": "padding_left",
        "primaryAxisAlignItems": "primary_axis_align_items",
        "counterAxisAlignItems": "counter_axis_align_items",
        "primaryAxisSizingMode": "primary_axis_sizing_mode",
        "counterAxisSizingMode": "counter_axis_sizing_mode",
        "counterAxisSpacing": "counter_axis_spacing",
        "layoutWrap": "layout_wrap",
        "minWidth": "min_width",
        "maxWidth": "max_width",
        "minHeight": "min_height",
        "maxHeight": "max_height",
        "clipsContent": "clips_content",
        "cornerRadius": "corner_radius",
        "topLeftRadius": "top_left_radius",
        "topRightRadius": "top_right_radius",
        "bottomLeftRadius": "bottom_left_radius",
        "bottomRightRadius": "bottom_right_radius",
        "layoutAlign": "layout_align",
        "layoutGrow": "layout_grow",
        "constraints": "constraints",
    }.items():
        if source_key in raw and target_key not in properties:
            properties[target_key] = raw[source_key]
    reactions = raw.get("reactions")
    if isinstance(reactions, (list, tuple)) and reactions:
        properties["interactions"] = {
            "present": True,
            "reaction_count": len(reactions),
        }
    if isinstance(raw_children, (list, tuple)) and any(
        isinstance(child, dict) and child.get("isMask") is True
        for child in raw_children
    ):
        properties["explicit_mask"] = True
    raw_style = dict(raw.get("style", {}))
    for key in (
        "fills",
        "strokes",
        "effects",
        "relativeTransform",
        "absoluteTransform",
        "blendMode",
    ):
        if key in raw and key not in raw_style:
            raw_style[key] = raw[key]
    character_overrides = raw.get("characterStyleOverrides")
    override_table = raw.get("styleOverrideTable")
    has_text_overrides = (
        isinstance(character_overrides, (list, tuple))
        and any(value not in (0, "0", None) for value in character_overrides)
    ) or (isinstance(override_table, dict) and bool(override_table))
    if has_text_overrides and "runs" not in raw_style:
        raw_style["runs"] = (
            {
                "content": raw.get("characters", ""),
                "style": {},
                "unsupportedFeatures": ("rest_text_style_overrides",),
            },
        )
    raw_references = raw_style.pop("resourceRefs", ())
    resource_refs: list[NormalizedResourceReference] = []
    if isinstance(raw_references, (list, tuple)):
        for raw_reference in raw_references:
            if not isinstance(raw_reference, dict):
                continue
            reference = dict(raw_reference)
            if "exportFormat" not in reference:
                mime_type = reference.get("mimeType")
                reference["exportFormat"] = {
                    "image/jpeg": "jpg",
                    "image/png": "png",
                    "image/svg+xml": "svg",
                    "image/webp": "webp",
                }.get(mime_type if isinstance(mime_type, str) else "", "png")
            try:
                resource_refs.append(NormalizedResourceReference.model_validate(reference))
            except ValueError:
                continue
    return NormalizedNode(
        id=str(raw["id"]),
        name=str(raw.get("name", "")),
        type=str(raw["type"]),
        bounds=Bounds(x=box["x"], y=box["y"], width=box["width"], height=box["height"]),
        children=children,
        text=raw.get("characters"),
        rotation=float(raw.get("rotation", 0)),
        opacity=float(raw.get("opacity", 1)),
        visible=bool(raw.get("visible", True)),
        source_order=int(raw.get("sourceOrder", source_order)),
        properties=properties,
        raw_style=raw_style,
        resource_refs=tuple(resource_refs),
    )


def normalize_document(
    raw: dict[str, object],
) -> tuple[tuple[NormalizedNode, ...], tuple[Diagnostic, ...]]:
    roots = raw.get("roots")
    if isinstance(roots, list) and all(isinstance(root, dict) for root in roots):
        _guard_raw_tree_depth(tuple(roots))
        normalized = tuple(_node(root, index) for index, root in enumerate(roots))
        warning_codes = (
            raw._selection_warning_codes
            if isinstance(raw, SelectionDocument)
            else ()
        )
        retained_codes = tuple(
            dict.fromkeys(
                code
                for code in warning_codes
                if code
                in {
                    "nine_slice_invalid",
                    "nine_slice_out_of_bounds",
                    "node_hidden",
                    "node_locked",
                    "unsupported_prototype",
                    "unsupported_video",
                    "visual_rasterized",
                }
            )
        )
        if retained_codes:
            normalized = tuple(
                root.model_copy(
                    update={
                        "properties": {
                            **root.properties,
                            "selection_warning_codes": retained_codes,
                        }
                    }
                )
                for root in normalized
            )
        return normalized, ()
    _guard_raw_tree_depth((raw,))
    return (_node(raw, 0),), ()


def selection_conversion_document(
    manifest: SelectionManifest, resources_root: Path, artifact_fingerprint: str = ""
) -> SelectionConversionDocument:
    resources = {resource.key: resource for resource in manifest.resources}
    references: dict[str, dict[str, object]] = {}
    raster_dimensions: dict[str, tuple[int, int]] = {}
    assets: list[SelectionAsset] = []
    try:
        root = resources_root.resolve(strict=True)
    except OSError as error:
        raise ValueError("selection resources are unavailable") from error

    for index, (key, resource) in enumerate(resources.items()):
        try:
            source = (root / key).resolve(strict=True)
        except OSError as error:
            raise ValueError("selection resource is unavailable") from error
        if not source.is_file() or root not in source.parents or source.stat().st_size != resource.size:
            raise ValueError("selection resource is unavailable")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
        asset = "asset_" + hashlib.sha256(
            f"{artifact_fingerprint}|{index}|{resource.mime_type}|{resource.size}|{digest.hexdigest()}".encode()
        ).hexdigest()[:24]
        references[key] = {
            "asset": asset,
            "mimeType": resource.mime_type,
            "sha256": digest.hexdigest(),
        }
        content = read_bounded_stable_asset_file(source, max_bytes=MAX_ASSET_PAYLOAD_BYTES)
        if resource.mime_type == "image/svg+xml":
            try:
                svg_dimensions = safe_svg_dimensions(content)
            except ValueError:
                svg_dimensions = None
            if svg_dimensions is not None:
                raster_dimensions[key] = svg_dimensions
        else:
            raster_details = _inspect_raster_in_isolated_process(content)
            if raster_details is not None:
                raster_dimensions[key] = (raster_details[1], raster_details[2])
        assets.append(
            SelectionAsset(
                asset=asset,
                mime_type=resource.mime_type,
                source_path=source,
                size=resource.size,
                sha256=digest.hexdigest(),
                artifact_fingerprint=artifact_fingerprint,
            )
        )

    def node_id(selection: SelectionNode, position: tuple[int, ...]) -> str:
        payload = {
            "position": position,
            "name": selection.name,
            "type": selection.type,
            "bounds": selection.bounds.model_dump(mode="json"),
            "rotation": selection.rotation,
            "text": selection.text,
            "properties": selection.properties,
            "style": selection.style,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"node_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20]}"

    def node(selection: SelectionNode, position: tuple[int, ...]) -> dict[str, object]:
        width = _positive_integer_dimension(selection.bounds.width)
        height = _positive_integer_dimension(selection.bounds.height)
        nine_slice = _nine_slice(selection, width, height)
        node_references: list[dict[str, object]] = []
        export_strategy = selection.properties.get("export_strategy")
        if export_strategy == "vector_asset":
            if selection.rotation != 0:
                raise ValueError("vector PNG rotation must be baked")
            if any(
                references[key]["mimeType"] != "image/png"
                for key in selection.resource_keys
            ):
                raise ValueError("vector asset must use PNG")
        if (
            export_strategy in {"image_asset", "composite_png"}
            and selection.rotation != 0
            and any(
                references[key]["mimeType"] == "image/png"
                for key in selection.resource_keys
            )
        ):
            raise ValueError("exported PNG rotation must be baked")
        for key in selection.resource_keys:
            reference = dict(references[key])
            reference["exportFormat"] = {
                "image/png": "png",
                "image/svg+xml": "svg",
                "image/webp": "webp",
            }[str(reference["mimeType"])]
            resource_width, resource_height = raster_dimensions.get(key, (width, height))
            if resource_width is not None and resource_height is not None:
                reference.update({"width": resource_width, "height": resource_height})
            if nine_slice is not None:
                reference["nineSlice"] = nine_slice
            node_references.append(reference)
        style = dict(selection.style)
        raw_mask = style.get("mask")
        if isinstance(raw_mask, dict):
            local_refs = {
                selection.id: node_id(selection, position),
                **{
                    child.id: node_id(child, position + (index,))
                    for index, child in enumerate(selection.children)
                },
            }
            translated_mask = dict(raw_mask)
            mask_ref = translated_mask.get("maskNodeRef")
            if isinstance(mask_ref, str) and mask_ref in local_refs:
                translated_mask["maskNodeRef"] = local_refs[mask_ref]
            content_refs = translated_mask.get("contentNodeRefs")
            if isinstance(content_refs, (list, tuple)):
                translated_mask["contentNodeRefs"] = tuple(
                    local_refs.get(ref, ref) if isinstance(ref, str) else ref
                    for ref in content_refs
                )
            safe_root_ref = translated_mask.get("safeRasterRootRef")
            if isinstance(safe_root_ref, str) and safe_root_ref in local_refs:
                translated_mask["safeRasterRootRef"] = local_refs[safe_root_ref]
            style["mask"] = translated_mask
        if node_references:
            style["resourceRefs"] = tuple(node_references)
        raw: dict[str, object] = {
            "id": node_id(selection, position),
            "name": selection.name,
            "type": selection.type,
            "absoluteBoundingBox": selection.bounds.model_dump(mode="json"),
            "children": [node(child, position + (index,)) for index, child in enumerate(selection.children)],
            "rotation": selection.rotation,
            "opacity": selection.opacity,
            "visible": selection.visible,
            "sourceOrder": selection.source_order,
            "componentProperties": {
                name: {"value": value} for name, value in selection.properties.items()
            },
            "style": style,
        }
        if selection.text is not None:
            raw["characters"] = selection.text
        return raw

    return SelectionConversionDocument(
        raw=SelectionDocument(
            {"roots": [node(selection, (index,)) for index, selection in enumerate(manifest.top_level_nodes)]},
            tuple(assets),
            tuple(warning.code for warning in manifest.warnings),
        ),
        assets=tuple(assets),
    )


def _positive_integer_dimension(value: float) -> int | None:
    if not math.isfinite(value) or value <= 0 or not value.is_integer():
        return None
    return int(value)


def _nine_slice(
    selection: SelectionNode,
    width: int | None,
    height: int | None,
) -> dict[str, int] | None:
    raw = selection.properties.get("nine_slice_insets")
    if not isinstance(raw, dict) or width is None or height is None:
        return None
    values = tuple(raw.get(key) for key in ("left", "top", "right", "bottom"))
    if not all(type(value) is int and value >= 0 for value in values):
        return None
    left, top, right, bottom = cast(tuple[int, int, int, int], values)
    if left + right >= width or top + bottom >= height:
        return None
    return {
        "x": left,
        "y": top,
        "width": width - left - right,
        "height": height - top - bottom,
    }


def selection_document(manifest: SelectionManifest, resources_root: Path) -> dict[str, object]:
    return selection_conversion_document(manifest, resources_root).raw
