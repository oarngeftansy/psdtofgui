from types import SimpleNamespace as NS

import pytest


class ClosedPath(list):
    operation = 1

    def is_closed(self):
        return True


def layer(alignment="inner"):
    points = [(0.2, 0.2), (0.2, 0.8), (0.8, 0.8), (0.8, 0.2)]
    knots = ClosedPath(NS(anchor=p, preceding=p, leaving=p) for p in points)
    return NS(
        kind="shape",
        opacity=255,
        clipping=False,
        blend_mode=NS(value=b"norm"),
        _psd=NS(width=50, height=50, depth=8),
        bbox=(10, 10, 40, 40),
        vector_mask=NS(paths=[knots], inverted=False, disabled=False, initial_fill_rule=0),
        stroke=NS(
            enabled=True,
            fill_enabled=False,
            line_width=4.0,
            line_alignment=alignment,
            line_join_type="round",
            line_cap_type="round",
            line_dash_set=[],
            blend_mode=b"normal",
            opacity=100.0,
            content={b"Clr ": {b"Rd  ": 200.0, b"Grn ": 100.0, b"Bl  ": 50.0}},
        ),
        has_effects=lambda: False,
        has_mask=lambda: False,
    )


@pytest.mark.parametrize("alignment,border_x", [("inner", 11), ("center", 10), ("outer", 8)])
def test_stroke_keeps_transparent_hole_and_obeys_alignment(alignment, border_x):
    from figma_to_fgui.psd_stroke_render import render_stroke_only

    result = render_stroke_only(layer(alignment), viewport=(0, 0, 50, 50))
    assert result.mode == "RGBA" and result.size == (50, 50)
    assert result.getpixel((25, 25))[3] == 0
    assert result.getpixel((border_x, 25)) == (200, 100, 50, 255)
    assert result.getpixel((1, 25))[3] == 0


def test_unimplemented_dash_and_effects_are_not_silently_discarded():
    from figma_to_fgui.psd_stroke_render import render_stroke_only

    shape = layer()
    shape.stroke.line_dash_set = [2.0, 3.0]
    with pytest.raises(ValueError, match="unsupported"):
        render_stroke_only(shape)
    shape.stroke.line_dash_set = []
    shape.has_effects = lambda: True
    with pytest.raises(ValueError, match="unsupported"):
        render_stroke_only(shape)
