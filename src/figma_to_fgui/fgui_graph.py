"""Extract the small editable FairyGUI graph subset from public UIR facts."""

from __future__ import annotations

import math
from collections.abc import Mapping

from figma_to_fgui.fgui_plan_models import GraphPlan
from figma_to_fgui.uir_models import UIRNode


def _visible_paints(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        paint
        for paint in value
        if isinstance(paint, Mapping)
        and paint.get("visible") is not False
        and paint.get("opacity") != 0
        and not (
            isinstance(paint.get("color"), Mapping)
            and paint["color"].get("a") == 0
        )
    )


def _channel(value: object, default: float) -> float | None:
    resolved = default if value is None else value
    return float(resolved) if isinstance(resolved, (int, float)) and math.isfinite(resolved) else None


def _solid_color(paints: object, *, alpha: bool) -> str | None:
    visible = _visible_paints(paints)
    if len(visible) != 1 or visible[0].get("type") != "SOLID":
        return None
    paint = visible[0]
    color = paint.get("color")
    if not isinstance(color, Mapping):
        return None
    channels: list[float] = []
    for key, default in (("r", 0), ("g", 0), ("b", 0), ("a", 1)):
        channel = _channel(color.get(key), default)
        if channel is None:
            return None
        channels.append(channel)
    opacity = _channel(paint.get("opacity"), 1)
    if opacity is None:
        return None
    red, green, blue, color_alpha = channels
    rgb = tuple(max(0, min(255, round(channel * 255))) for channel in (red, green, blue))
    if not alpha:
        if color_alpha != 1 or opacity != 1:
            return None
        return "#" + "".join(f"{channel:02x}" for channel in rgb)
    resolved_alpha = max(0, min(255, round(color_alpha * opacity * 255)))
    return f"#{resolved_alpha:02x}" + "".join(f"{channel:02x}" for channel in rgb)


def _corner_radii(node: UIRNode) -> tuple[float, float, float, float] | None:
    visual = node.visual
    general = visual.get("cornerRadius", 0)
    if not isinstance(general, (int, float)) or not math.isfinite(general) or general < 0:
        return None
    values: list[float] = []
    for key in ("topLeftRadius", "topRightRadius", "bottomRightRadius", "bottomLeftRadius"):
        value = visual.get(key, general)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return None
        values.append(float(value))
    # Figma accepts deliberately oversized radii (999 is the common "pill"
    # value) and clamps them to the rendered box. FairyGUI stores a literal
    # radius, so forwarding 999 changes the shape instead of reproducing it.
    limit = min(
        node.geometry.resolved_bounds.width,
        node.geometry.resolved_bounds.height,
    ) / 2
    return tuple(min(value, limit) for value in values)  # type: ignore[return-value]


def graph_plan_for_node(node: UIRNode) -> GraphPlan | None:
    """Return an editable graph only when the visual result is fully represented."""
    source_type = node.source.type.upper()
    leaf_shape = source_type in {"RECTANGLE", "ELLIPSE"} and not node.children
    root_container_shape = (
        source_type in {"FRAME", "COMPONENT"}
        and bool(
            _visible_paints(node.visual.get("fills"))
            or _visible_paints(node.visual.get("strokes"))
        )
    )
    if not leaf_shape and not root_container_shape:
        return None
    if _visible_paints(node.visual.get("effects")):
        return None
    blend = node.visual.get("blendMode", node.visual.get("blend_mode"))
    if isinstance(blend, str) and blend.upper() not in {"NORMAL", "PASS_THROUGH"}:
        return None
    fills = _visible_paints(node.visual.get("fills"))
    strokes = _visible_paints(node.visual.get("strokes"))
    if len(fills) > 1 or len(strokes) > 1:
        return None
    fill_color = None if not fills else _solid_color(fills, alpha=True)
    line_color = None if not strokes else _solid_color(strokes, alpha=False)
    if (fills and fill_color is None) or (strokes and line_color is None):
        return None
    raw_line_size = node.visual.get("strokeWeight", 0)
    if not isinstance(raw_line_size, (int, float)) or not math.isfinite(raw_line_size) or raw_line_size < 0:
        return None
    corner_radius = None
    corner_radii = None
    if source_type != "ELLIPSE":
        corners = _corner_radii(node)
        if corners is None:
            return None
        if len(set(corners)) == 1:
            corner_radius = corners[0]
        else:
            corner_radii = corners
    return GraphPlan(
        shape="ellipse" if source_type == "ELLIPSE" else "rect",
        fillColor=fill_color,
        lineColor=line_color,
        lineSize=float(raw_line_size) if line_color is not None else 0,
        cornerRadius=corner_radius if corner_radius and corner_radius > 0 else None,
        cornerRadii=corner_radii,
    )
