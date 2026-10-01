from __future__ import annotations

from dataclasses import asdict
from typing import Any

from figma_to_fgui.figma_selection import (
    SelectionManifest,
    SelectionNode,
    SelectionResource,
    SelectionWarning,
)
from figma_to_fgui.models import Bounds
from figma_to_fgui.psd_intake import PsdLayer
from figma_to_fgui.psd_source_store import PsdRasterResource, PsdSource

_PSD_NODE_TYPES = {
    "group": "GROUP",
    "type": "TEXT",
    "shape": "VECTOR",
    "pixel": "IMAGE",
    "smartobject": "IMAGE",
}

def _node_type(layer: PsdLayer) -> str:
    return _PSD_NODE_TYPES.get(layer.kind.casefold(), layer.kind.upper())


def psd_composite_group_ids(source: PsdSource) -> frozenset[str]:
    # Group compositing cannot substitute for explicit leaf ownership.
    return frozenset()


def psd_lossless_blockers(source: PsdSource) -> tuple[str, ...]:
    blockers = source.inspection.blocking_issues
    width, height = source.inspection.width, source.inspection.height
    if any(
        layer.effective_visible and layer.kind.casefold() != "group"
        and layer.bounds[2] > layer.bounds[0] and layer.bounds[3] > layer.bounds[1]
        and (layer.bounds[0] < 0 or layer.bounds[1] < 0
             or layer.bounds[2] > width or layer.bounds[3] > height)
        for layer in source.layers
    ):
        blockers += ("outside_canvas_content_requires_equivalence_check",)
    return tuple(dict.fromkeys(blockers))


def psd_source_manifest(
    source: PsdSource,
    *,
    raster_resources: dict[str, PsdRasterResource] | None = None,
    native_graphs: dict[str, dict[str, Any]] | None = None,
    viewport_bounds: tuple[int, int, int, int] | None = None,
) -> SelectionManifest:
    raster_resources = {key: value for key, value in (raster_resources or {}).items()
                        if any(layer.id == key and layer.kind.casefold() != "group" for layer in source.layers)}
    by_parent: dict[str | None, list[PsdLayer]] = {}
    for layer in source.layers:
        if not layer.effective_visible:
            continue
        by_parent.setdefault(layer.parent_id, []).append(layer)
    for children in by_parent.values():
        children.sort(key=lambda layer: (layer.sibling_index, layer.document_index, layer.id))
    composite_groups = psd_composite_group_ids(source)

    def convert(layer: PsdLayer, *, composite_text: bool = False) -> SelectionNode:
        left, top, right, bottom = layer.bounds
        native = native_graphs.get(layer.id) if native_graphs else None
        if native is not None and layer.id not in raster_resources:
            left, top, right, bottom = native["bounds"]
        raster = raster_resources.get(layer.id)
        if raster is not None and raster.bounds is not None:
            left, top, right, bottom = raster.bounds
        child_layers = tuple(by_parent.get(layer.id, ()))
        return SelectionNode(
            id=layer.id,
            name=layer.name,
            type=_node_type(layer),
            bounds=Bounds(
                x=left,
                y=top,
                width=max(0, right - left),
                height=max(0, bottom - top),
            ),
            children=tuple(
                convert(
                    child,
                    composite_text=layer.id in composite_groups
                    and child.kind.casefold() == "type",
                )
                for child in child_layers
            ),
            visible=layer.effective_visible,
            # Isolated PNG pixels already include this layer's opacity.
            opacity=1.0 if raster is not None else layer.opacity / 255,
            source_order=layer.sibling_index,
            text=layer.text,
            properties={
                **({"fguiGraph": native["graph"]} if native is not None else {}),
                "psdKind": layer.kind,
                "blendMode": layer.blend_mode,
                "clipping": layer.clipping,
                "hasPixelMask": layer.has_pixel_mask,
                "hasVectorMask": layer.has_vector_mask,
                "hasEffects": layer.has_effects,
                "psdDocumentIndex": layer.document_index,
                "psdEffects": tuple(asdict(effect) for effect in layer.effects),
                "psdCompositeGroup": layer.id in composite_groups,
                "psdCompositeText": composite_text,
            },
            style=(
                {"psdTextStyle": asdict(layer.text_style)}
                if layer.text_style is not None
                else {}
            ),
            resource_keys=(raster.key,) if raster is not None else (),
        )

    root = SelectionNode(
        id=f"psd-root:{source.source_id}",
        name=source.inspection.source_name,
        type="FRAME",
        bounds=Bounds(
            x=0,
            y=0,
            width=source.inspection.width,
            height=source.inspection.height,
        ),
        children=tuple(convert(layer) for layer in by_parent.get(None, ())),
        properties={"psdViewportBounds": viewport_bounds} if viewport_bounds else {},
    )
    warnings = tuple(
        SelectionWarning(
            code=code,
            message=f"PSD lossless gate: {code}",
        )
        for code in (*psd_lossless_blockers(source), *source.inspection.warnings)
    )
    return SelectionManifest(
        version=1,
        display_name=source.inspection.source_name,
        top_level_nodes=(root,),
        resources=tuple(
            SelectionResource(
                key=resource.key,
                mime_type="image/png",
                size=resource.size,
            )
            for resource in sorted(raster_resources.values(), key=lambda item: item.key)
        ),
        warnings=warnings,
    )
