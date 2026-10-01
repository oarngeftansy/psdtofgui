from types import SimpleNamespace as NS

import pytest
from PIL import Image
from psd_tools.constants import Tag


def shadow(**changes):
    args = {
        "kind": "DropShadow", "enabled": True, "blend_mode": "normal", "opacity": 50.0,
        "color_rgba": (0.,0.,0.,1.), "size": 0., "angle": 0., "distance": 3.,
        "spread": None, "choke": 0., "position": None,
    }
    args.update(changes)
    return NS(**args)


def test_hard_shadow_extends_alpha_without_wrapping_or_filling_transparency():
    from figma_to_fgui.psd_effect_render import composite_hard_shadow

    body = Image.new("RGBA", (12, 10), (0, 0, 0, 0))
    body.paste((255, 200, 100, 255), (5, 3, 8, 7))
    result = composite_hard_shadow(body, shadow())
    assert result.getpixel((3, 4)) == (0, 0, 0, 128)
    assert result.getpixel((6, 4)) == (255, 200, 100, 255)
    assert result.getpixel((11, 4))[3] == 0
    assert result.getpixel((3, 0))[3] == 0


@pytest.mark.parametrize("change", [{"choke": 4.0}, {"blend_mode": "multiply"}])
def test_shadow_rejects_features_it_cannot_render(change):
    from figma_to_fgui.psd_effect_render import composite_hard_shadow

    with pytest.raises(ValueError, match="unsupported"):
        composite_hard_shadow(Image.new("RGBA", (10, 10)), shadow(**change))


def test_visual_effect_compositor_expands_outside_stroke_and_blurred_shadow() -> None:
    from figma_to_fgui.psd_effect_render import composite_visual_effects

    body = Image.new("RGBA", (40, 30))
    body.paste((80, 160, 40, 255), (10, 8, 30, 20))
    outside = NS(
        kind="Stroke", enabled=True, blend_mode="normal", opacity=100.0,
        color_rgba=(1.0, 0.0, 0.0, 1.0), size=4.0, angle=None,
        distance=None, spread=None, choke=None, position="outside",
    )
    blurred = shadow(size=2.0, angle=180.0, distance=5.0)

    rendered = composite_visual_effects(body, (blurred, outside))

    assert rendered.getpixel((7, 14)) == (255, 0, 0, 255)
    assert rendered.getpixel((10, 14)) == (80, 160, 40, 255)
    assert rendered.getpixel((35, 14))[3] > 0


def test_degenerate_shape_uses_document_space_vector_mask_instead_of_empty_layer_bbox() -> None:
    from figma_to_fgui.psd_effect_render import render_leaf

    def knot(y: float, x: float) -> NS:
        point = (y, x)
        return NS(anchor=point, preceding=point, leaving=point)

    path = [
        knot(0.20, 0.10),
        knot(0.20, 0.30),
        knot(0.40, 0.30),
        knot(0.40, 0.10),
    ]
    blocks = {
        Tag.SOLID_COLOR_SHEET_SETTING: {
            b"Clr ": {b"Rd  ": 250.0, b"Grn ": 240.0, b"Bl  ": 230.0}
        },
        Tag.BLEND_FILL_OPACITY: 255,
    }
    layer = NS(
        bbox=(0, -19, 0, -19),
        vector_mask=NS(inverted=False, paths=[path]),
        stroke=None,
        opacity=255,
        _psd=NS(width=100, height=100),
        tagged_blocks=NS(get_data=lambda key, default=None: blocks.get(key, default)),
        parent=None,
    )
    metadata = NS(bounds=(10, 20, 30, 40), effects=())

    image, viewport = render_leaf(layer, metadata)

    assert viewport == (10, 20, 30, 40)
    assert image.getbbox() == (0, 0, 20, 20)
    assert image.getpixel((10, 10)) == (250, 240, 230, 255)


def test_owned_visual_requires_an_exact_partition_and_keeps_text_separate():
    from figma_to_fgui.psd_effect_render import validate_owned_partition

    leaves = [
        NS(id="bg", kind="shape", effective_visible=True, bounds=(0, 0, 10, 10),
           has_effects=False, clipping=False, opacity=255, blend_mode="normal"),
        NS(id="label", kind="type", effective_visible=True, bounds=(0, 0, 10, 10),
           has_effects=False, clipping=False, opacity=255, blend_mode="normal"),
    ]
    validate_owned_partition(leaves, frozenset({"bg"}), frozenset({"label"}))
    for owned, retained in [
        ({"bg"}, set()),
        ({"bg", "label"}, set()),
        ({"bg", "elsewhere"}, {"label"}),
        ({"bg"}, {"bg", "label"}),
    ]:
        with pytest.raises(ValueError, match="ownership"):
            validate_owned_partition(leaves, frozenset(owned), frozenset(retained))


def test_owned_partition_ignores_only_nonpainting_empty_groups():
    from figma_to_fgui.psd_effect_render import validate_owned_partition

    empty = NS(id="empty", kind="group", effective_visible=True, bounds=(0, 0, 10, 10),
               has_effects=False,
               has_pixel_mask=False, has_vector_mask=False, clipping=False,
               opacity=255, blend_mode="pass_through")
    body = NS(id="body", kind="shape", effective_visible=True, bounds=(0, 0, 10, 10),
              has_effects=False, clipping=False, opacity=255, blend_mode="normal")
    validate_owned_partition([empty, body], frozenset({"body"}), frozenset())
    empty.has_pixel_mask = True
    validate_owned_partition([empty, body], frozenset({"body"}), frozenset())
    empty.has_effects = True
    with pytest.raises(ValueError, match="ownership"):
        validate_owned_partition([empty, body], frozenset({"body"}), frozenset())


def test_leaf_inherits_opaque_normal_color_overlay_without_losing_alpha():
    from figma_to_fgui.psd_effect_render import render_leaf

    effect = NS(
        enabled=True,
        opacity=100.0,
        blend_mode=b"norm",
        color={b"Rd  ": 250.0, b"Grn ": 180.0, b"Bl  ": 70.0},
    )

    class Effects(list):
        enabled = True

        def find(self, name):
            return iter(self if name == "ColorOverlay" else ())

    parent = NS(
        parent=None,
        opacity=255,
        clipping=False,
        blend_mode=NS(value=b"pass"),
        has_mask=lambda: False,
        has_vector_mask=lambda: False,
        effects=Effects([effect]),
    )
    leaf = NS(parent=parent, composite=lambda **kw: Image.new("RGBA", (3, 3), (255, 0, 0, 128)))
    metadata = NS(bounds=(0, 0, 3, 3), effects=())
    image, _ = render_leaf(leaf, metadata)
    assert image.getpixel((1, 1)) == (250, 180, 70, 128)
    parent.opacity = 128
    with pytest.raises(ValueError, match="context"):
        render_leaf(leaf, metadata)


def test_owned_visual_crops_transparent_declared_bounds(monkeypatch):
    from figma_to_fgui import psd_effect_render

    group = NS(
        id="group", parent_id=None, kind="group", effective_visible=True,
        bounds=(0, 0, 100, 100), document_index=0, sibling_index=0,
        has_effects=False, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="pass_through", effects=(),
    )
    leaf = NS(
        id="leaf", parent_id="group", kind="shape", effective_visible=True,
        bounds=(10, 20, 90, 90), document_index=1, sibling_index=0,
        has_effects=False, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="normal", effects=(),
    )
    source = NS(layers=(group, leaf))
    document = NS(descendants=lambda: (NS(), NS()))
    painted = Image.new("RGBA", (80, 70))
    painted.paste((20, 2, 0, 120), (5, 35, 75, 63))
    monkeypatch.setattr(
        psd_effect_render,
        "layer_viewport",
        lambda _actual, metadata: metadata.bounds,
    )
    monkeypatch.setattr(
        psd_effect_render,
        "render_leaf",
        lambda *_args, **_kwargs: (painted.copy(), (10, 20, 90, 90)),
    )

    image, bounds = psd_effect_render.render_owned_visual(
        document,
        source,
        "group",
        frozenset({"leaf"}),
        frozenset(),
    )

    assert image.size == (70, 28)
    assert bounds == (15, 55, 85, 83)


def test_owned_visual_can_render_a_root_level_backdrop_partition(monkeypatch):
    from figma_to_fgui import psd_effect_render

    def layer(layer_id, kind, index):
        return NS(
            id=layer_id,
            parent_id=None,
            kind=kind,
            effective_visible=True,
            bounds=(0, 0, 4, 4),
            document_index=index,
            sibling_index=index,
            has_effects=False,
            has_pixel_mask=False,
            has_vector_mask=False,
            clipping=False,
            opacity=255,
            blend_mode="normal",
            effects=(),
        )

    base = layer("base", "pixel", 0)
    shade = layer("shade", "pixel", 1)
    grade = layer("grade", "pixel", 2)
    label = layer("label", "type", 3)
    source = NS(layers=(base, shade, grade, label))
    document = NS(descendants=lambda: (NS(), NS(), NS(), NS()))
    colors = {
        "shade": (255, 0, 0, 128),
        "grade": (0, 0, 255, 128),
    }
    monkeypatch.setattr(
        psd_effect_render,
        "layer_viewport",
        lambda _actual, metadata: metadata.bounds,
    )
    monkeypatch.setattr(
        psd_effect_render,
        "render_leaf",
        lambda _actual, metadata, **_kwargs: (
            Image.new("RGBA", (4, 4), colors[metadata.id]),
            (0, 0, 4, 4),
        ),
    )

    image, bounds = psd_effect_render.render_owned_visual(
        document,
        source,
        "psd-root:synthetic",
        frozenset({"shade", "grade"}),
        frozenset({"base", "label"}),
    )

    assert bounds == (0, 0, 4, 4)
    assert image.getpixel((2, 2)) == (85, 0, 170, 192)


def test_owned_visual_uses_final_alpha_bbox_instead_of_group_padding(monkeypatch):
    from figma_to_fgui import psd_effect_render

    group = NS(
        id="group", parent_id=None, kind="group", effective_visible=True,
        bounds=(10, 20, 90, 90), document_index=0, sibling_index=0,
        has_effects=False, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="pass_through", effects=(),
    )
    leaf = NS(
        id="leaf", parent_id="group", kind="shape", effective_visible=True,
        bounds=(10, 20, 90, 90), document_index=1, sibling_index=0,
        has_effects=False, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="normal", effects=(),
    )
    source = NS(layers=(group, leaf))
    document = NS(descendants=lambda: (NS(), NS()))
    painted = Image.new("RGBA", (80, 70))
    painted.paste((255, 200, 100, 255), (5, 7, 75, 63))
    monkeypatch.setattr(
        psd_effect_render,
        "layer_viewport",
        lambda _actual, metadata: metadata.bounds,
    )
    monkeypatch.setattr(
        psd_effect_render,
        "render_leaf",
        lambda *_args, **_kwargs: (painted.copy(), (10, 20, 90, 90)),
    )

    image, bounds = psd_effect_render.render_owned_visual(
        document,
        source,
        "group",
        frozenset({"leaf"}),
        frozenset(),
    )

    assert image.size == (70, 56)
    assert bounds == (15, 27, 85, 83)


def test_owned_visual_native_and_fallback_share_effect_expanded_viewport(monkeypatch):
    from figma_to_fgui import psd_effect_render

    effect = NS(
        kind="ColorOverlay", enabled=True, blend_mode="normal", opacity=100,
        color_rgba=(1.0, 1.0, 1.0, 1.0), size=None, distance=None,
    )
    group = NS(
        id="group", parent_id=None, kind="group", effective_visible=True,
        bounds=(0, 0, 100, 100), document_index=0, sibling_index=0,
        has_effects=True, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="pass_through", effects=(effect,),
    )
    leaf = NS(
        id="leaf", parent_id="group", kind="shape", effective_visible=True,
        bounds=(10, 20, 90, 90), document_index=1, sibling_index=0,
        has_effects=False, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="normal", effects=(),
    )
    source = NS(layers=(group, leaf))
    viewports = []

    class Document:
        def descendants(self):
            return (NS(), NS())

        def composite(self, *, viewport, **_kwargs):
            viewports.append(("fallback", viewport))
            image = Image.new("RGBA", (viewport[2] - viewport[0], viewport[3] - viewport[1]))
            image.putpixel((5, 7), (255, 255, 255, 255))
            return image

    monkeypatch.setattr(
        psd_effect_render,
        "layer_viewport",
        lambda _actual, metadata: metadata.bounds,
    )

    def unsupported_leaf(*_args, viewport, **_kwargs):
        viewports.append(("native", viewport))
        raise ValueError("force_fallback")

    monkeypatch.setattr(psd_effect_render, "render_leaf", unsupported_leaf)

    image, bounds = psd_effect_render.render_owned_visual(
        Document(), source, "group", frozenset({"leaf"}), frozenset()
    )

    assert viewports == [
        ("native", (0, 0, 100, 100)),
        ("fallback", (0, 0, 100, 100)),
    ]
    assert image.size == (1, 1)
    assert bounds == (5, 7, 6, 8)


def test_owned_visual_fallback_restores_effect_pixels_outside_logical_bounds():
    from figma_to_fgui import psd_effect_render

    outside = NS(
        kind="Stroke", enabled=True, blend_mode="normal", opacity=100.0,
        color_rgba=(1.0, 0.0, 0.0, 1.0), size=2.0, angle=None,
        distance=None, spread=None, choke=None, position="outside",
    )
    group = NS(
        id="group", parent_id=None, kind="group", effective_visible=True,
        bounds=(8, 8, 22, 22), document_index=0, sibling_index=0,
        has_effects=False, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="pass_through", effects=(),
    )
    effect_leaf = NS(
        id="effect", parent_id="group", kind="shape", effective_visible=True,
        bounds=(10, 10, 20, 20), document_index=1, sibling_index=0,
        has_effects=True, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="normal", effects=(outside,),
    )
    unsupported_leaf = NS(
        id="unsupported", parent_id="group", kind="shape", effective_visible=True,
        bounds=(12, 12, 18, 18), document_index=2, sibling_index=1,
        has_effects=False, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="multiply", effects=(),
    )
    source = NS(layers=(group, effect_leaf, unsupported_leaf))

    actual_group = NS(
        parent=None, opacity=255, clipping=False, blend_mode=NS(value=b"pass"),
        has_mask=lambda: False, has_vector_mask=lambda: False, effects=None,
    )

    def leaf_composite(*, viewport, **_kwargs):
        image = Image.new("RGBA", (viewport[2] - viewport[0], viewport[3] - viewport[1]))
        image.paste(
            (80, 160, 40, 255),
            (10 - viewport[0], 10 - viewport[1], 20 - viewport[0], 20 - viewport[1]),
        )
        return image

    actual_effect = NS(
        parent=actual_group, stroke=NS(fill_enabled=True), composite=leaf_composite,
    )
    actual_unsupported = NS(parent=actual_group, stroke=NS(fill_enabled=True))

    class Document:
        def descendants(self):
            return (actual_group, actual_effect, actual_unsupported)

        def composite(self, *, viewport, **_kwargs):
            return leaf_composite(viewport=viewport)

    image, bounds = psd_effect_render.render_owned_visual(
        Document(),
        source,
        "group",
        frozenset({"effect", "unsupported"}),
        frozenset(),
    )

    assert bounds == (8, 8, 22, 22)
    assert image.size == (14, 14)
    assert image.getpixel((0, 7)) == (255, 0, 0, 255)


def test_owned_visual_fallback_restores_degenerate_vector_leaf_with_group_stroke(monkeypatch):
    from figma_to_fgui import psd_effect_render

    outside = NS(
        kind="Stroke", enabled=True, blend_mode="normal", opacity=100.0,
        color_rgba=(0.0, 0.0, 0.0, 1.0), size=1.0, angle=None,
        distance=None, spread=None, choke=None, position="outside",
    )
    group = NS(
        id="group", parent_id=None, kind="group", effective_visible=True,
        bounds=(0, 0, 20, 20), document_index=0, sibling_index=0,
        has_effects=True, has_pixel_mask=False, has_vector_mask=False,
        clipping=False, opacity=255, blend_mode="pass_through", effects=(outside,),
    )
    leaf = NS(
        id="star", parent_id="group", kind="shape", effective_visible=True,
        bounds=(5, 5, 15, 15), document_index=1, sibling_index=0,
        has_effects=False, has_pixel_mask=False, has_vector_mask=True,
        clipping=False, opacity=255, blend_mode="normal", effects=(),
    )
    actual_group = NS(bbox=(0, 0, 20, 20))
    actual_leaf = NS(bbox=(0, -19, 0, -19))

    class Document:
        def descendants(self):
            return (actual_group, actual_leaf)

        def composite(self, *, viewport, **_kwargs):
            return Image.new("RGBA", (viewport[2] - viewport[0], viewport[3] - viewport[1]))

    def recovered(_actual, metadata, *, viewport, apply_ancestors=True):
        assert metadata is leaf and apply_ancestors is False
        image = Image.new("RGBA", (viewport[2] - viewport[0], viewport[3] - viewport[1]))
        image.paste((255, 255, 255, 255), (8, 8, 12, 12))
        return image, viewport

    monkeypatch.setattr(psd_effect_render, "render_leaf", recovered)

    image, bounds = psd_effect_render.render_owned_visual(
        Document(), source=NS(layers=(group, leaf)), group_id="group",
        owned=frozenset({"star"}), retained=frozenset(),
    )

    assert bounds == (4, 4, 10, 10)
    assert image.getpixel((0, 3)) == (0, 0, 0, 255)
    assert image.getpixel((3, 3)) == (255, 255, 255, 255)
