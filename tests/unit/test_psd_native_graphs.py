from types import SimpleNamespace

from PIL import Image, ImageDraw
from psd_tools.constants import Tag


def shape(**changes):
    orig = SimpleNamespace(invalidated=False, origin_type=1, bbox=(0, 0, 100, 50))
    blocks = {Tag.SOLID_COLOR_SHEET_SETTING: {b"Clr ": {b"Rd  ": 17, b"Grn ": 34, b"Bl  ": 51}}}
    layer = SimpleNamespace(
        kind="shape",
        clipping=False,
        opacity=255,
        parent=None,
        bbox=(0, 0, 100, 50),
        stroke=None,
        origination=[orig],
        blend_mode=SimpleNamespace(value=b"norm"),
        vector_mask=SimpleNamespace(inverted=False, paths=[object()]),
        has_effects=lambda: False,
        has_mask=lambda: False,
        tagged_blocks=SimpleNamespace(get_data=lambda key, default=None: blocks.get(key, default)),
    )
    for key, value in changes.items():
        setattr(layer, key, value)
    return layer


def test_simple_shape_exports_native_graph_without_bitmap_or_extra_nodes():
    from figma_to_fgui.psd_native_graphs import native_graph_for_layer

    assert native_graph_for_layer(shape()) == {
        "shape": "rect",
        "fillColor": "#ff112233",
        "lineSize": 0,
    }


def test_shape_fill_opacity_is_kept_in_native_graph_alpha() -> None:
    from figma_to_fgui.psd_native_graphs import native_graph_for_layer

    layer = shape()
    original_get_data = layer.tagged_blocks.get_data
    layer.tagged_blocks = SimpleNamespace(
        get_data=lambda key, default=None: (
            128 if key == Tag.BLEND_FILL_OPACITY else original_get_data(key, default)
        )
    )

    assert native_graph_for_layer(layer) == {
        "shape": "rect",
        "fillColor": "#80112233",
        "lineSize": 0,
    }


def test_shape_effects_and_parent_effects_never_disappear_in_native_conversion():
    from figma_to_fgui.psd_native_graphs import native_graph_for_layer

    assert native_graph_for_layer(shape(has_effects=lambda: True)) is None
    assert native_graph_for_layer(shape(parent=shape(has_effects=lambda: True))) is None
    assert native_graph_for_layer(shape(clipping=True)) is None
    assert native_graph_for_layer(shape(origination=[])) is None


def test_moved_or_composite_shape_is_not_misrepresented_as_simple_rectangle():
    from figma_to_fgui.psd_native_graphs import native_graph_for_layer

    assert native_graph_for_layer(shape(bbox=(2, 0, 102, 50))) is None
    assert (
        native_graph_for_layer(
            shape(vector_mask=SimpleNamespace(inverted=False, paths=[object(), object()]))
        )
        is None
    )


def test_invalidated_vertical_pill_is_recovered_as_native_rounded_graph() -> None:
    from figma_to_fgui.psd_native_graphs import native_graph_for_layer

    image = Image.new("RGBA", (20, 60))
    ImageDraw.Draw(image).rounded_rectangle((0, 0, 19, 59), radius=10, fill=(18, 5, 3, 102))
    invalidated = SimpleNamespace(invalidated=True, origin_type=0, bbox=(0, 0, 20, 60))
    layer = shape(
        bbox=(0, 0, 20, 60),
        origination=[invalidated],
        composite=lambda **_kwargs: image.copy(),
    )
    original_get_data = layer.tagged_blocks.get_data
    layer.tagged_blocks = SimpleNamespace(
        get_data=lambda key, default=None: (
            102 if key == Tag.BLEND_FILL_OPACITY else original_get_data(key, default)
        )
    )

    assert native_graph_for_layer(layer) == {
        "shape": "rect",
        "fillColor": "#66112233",
        "lineSize": 0,
        "cornerRadii": (10.0, 10.0, 10.0, 10.0),
    }


def test_zero_layer_bbox_recovers_document_space_vector_mask_bounds() -> None:
    from figma_to_fgui.psd_intake import effective_layer_bounds

    layer = SimpleNamespace(
        left=0,
        top=-19,
        right=0,
        bottom=-19,
        kind="shape",
        vector_mask=SimpleNamespace(
            inverted=False,
            bbox=(0.0637863, 0.6255936, 0.08251, 0.6342353),
        ),
    )

    assert effective_layer_bounds(layer, 1080, 2340) == (68, 1463, 90, 1485)
