"""Transparent rasterization of a deliberately bounded stroke-only PSD subset.

No mutation of the PSD or psd-tools globals. Output is an RGBA8 render, not a
claim that 16-bit source pixels or Photoshop's antialiasing are lossless.
"""

from __future__ import annotations

import math
from typing import Any

import aggdraw
from PIL import Image, ImageChops


def _smooth(path: Any) -> bool:
    for index, knot in enumerate(path):
        anchor = knot.anchor
        previous = knot.preceding if knot.preceding != anchor else path[index - 1].anchor
        following = knot.leaving if knot.leaving != anchor else path[(index + 1) % len(path)].anchor
        incoming = (anchor[0] - previous[0], anchor[1] - previous[1])
        outgoing = (following[0] - anchor[0], following[1] - anchor[1])
        length = math.hypot(*incoming) * math.hypot(*outgoing)
        if (
            length == 0
            or abs(incoming[0] * outgoing[1] - incoming[1] * outgoing[0]) > 1e-5 * length
        ):
            return False
        if incoming[0] * outgoing[0] + incoming[1] * outgoing[1] <= 0:
            return False
    return True


def render_stroke_only(
    layer: Any, *, viewport: tuple[int, int, int, int] | None = None
) -> Image.Image:
    """Render a single closed, solid, normal-blend vector stroke; reject omissions."""
    try:
        stroke, mask = layer.stroke, layer.vector_mask
        paths = mask.paths
        if (
            layer.kind != "shape"
            or stroke is None
            or not stroke.enabled
            or stroke.fill_enabled
            or layer.opacity != 255
            or layer.clipping
            or layer.has_effects()
            or layer.has_mask()
            or layer.blend_mode.value != b"norm"
            or stroke.blend_mode != b"normal"
            or mask.inverted
            or mask.disabled
            or mask.initial_fill_rule != 0
            or len(paths) != 1
            or not paths[0].is_closed()
            or len(paths[0]) < 2
            or paths[0].operation != 1
            or stroke.line_dash_set
            or stroke.line_alignment not in {"inner", "center", "outer"}
            or (stroke.line_join_type != "round" and not _smooth(paths[0]))
        ):
            raise ValueError("psd_stroke_only_raster_unsupported")
        width = float(stroke.line_width)
        opacity = float(stroke.opacity) / 100
        fill = stroke.content[b"Clr "]
        channels = tuple(float(fill[k]) for k in (b"Rd  ", b"Grn ", b"Bl  "))
        if (
            not math.isfinite(width)
            or not 0 < width <= 256
            or not 0 <= opacity <= 1
            or not all(math.isfinite(v) and 0 <= v <= 255 for v in channels)
        ):
            raise ValueError("psd_stroke_only_raster_unsupported")
        viewport = viewport or stroke_viewport(layer)
        left, top, right, bottom = viewport
        if right <= left or bottom <= top or (right - left) * (bottom - top) > 16_000_000:
            raise ValueError("psd_stroke_only_raster_unsupported")
        # Rasterize at native resolution: aggdraw integrates fractional Bezier
        # coverage. Supersampling adds a second filter and blurs thin borders.
        path = aggdraw.Path()

        def point(pair: tuple[float, float]) -> tuple[float, float]:
            y, x = pair
            return x * layer._psd.width - left, y * layer._psd.height - top

        knots = paths[0]
        path.moveto(*point(knots[0].anchor))
        for first, second in zip(knots, (*knots[1:], knots[0])):
            path.curveto(*point(first.leaving), *point(second.preceding), *point(second.anchor))
        path.close()
        alpha = Image.new("L", (right - left, bottom - top), 0)
        draw = aggdraw.Draw(alpha)
        draw.path(path, aggdraw.Pen(255, width if stroke.line_alignment == "center" else 2 * width))
        draw.flush()
        if stroke.line_alignment != "center":
            interior = Image.new("L", alpha.size, 0)
            draw = aggdraw.Draw(interior)
            draw.path(path, None, aggdraw.Brush(255))
            draw.flush()
            alpha = ImageChops.multiply(
                alpha, interior if stroke.line_alignment == "inner" else ImageChops.invert(interior)
            )
        alpha = alpha.point([round(v * opacity) for v in range(256)])
        result = Image.new("RGBA", alpha.size, tuple(round(v) for v in channels) + (0,))
        result.putalpha(alpha)
        return result
    except (AttributeError, KeyError, TypeError, OverflowError) as error:
        raise ValueError("psd_stroke_only_raster_unsupported") from error


def stroke_viewport(layer: Any) -> tuple[int, int, int, int]:
    """Include control points and the stroke radius, even beyond the document."""
    points = [
        p
        for knot in layer.vector_mask.paths[0]
        for p in (knot.anchor, knot.preceding, knot.leaving)
    ]
    padding = layer.stroke.line_width * (0.5 if layer.stroke.line_alignment == "center" else 1) + 1
    xs = [p[1] * layer._psd.width for p in points]
    ys = [p[0] * layer._psd.height for p in points]
    return (
        math.floor(min(xs) - padding),
        math.floor(min(ys) - padding),
        math.ceil(max(xs) + padding),
        math.ceil(max(ys) + padding),
    )
