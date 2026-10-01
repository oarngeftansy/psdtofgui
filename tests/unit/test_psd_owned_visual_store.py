import hashlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from figma_to_fgui.psd_source_store import (
    PsdRasterResource,
    PsdSourceStore,
    PsdSourceStoreError,
    _calibrate_channel_rounding,
    _calibrate_observed_translucent_pixels,
    _calibrate_opaque_owned_pixels,
    _calibrate_opaque_owned_pixels_to_target,
    _calibrate_translucent_owned_pixels,
)


def test_owned_visual_calibration_only_copies_opaque_one_channel_rounding(tmp_path):
    composite = Image.new("RGBA", (3, 1))
    composite.putdata(((254, 244, 234, 255), (247, 182, 77, 255), (20, 30, 40, 255)))
    composite_path = tmp_path / "composite.png"
    composite.save(composite_path)
    owned = Image.new("RGBA", (3, 1))
    owned.putdata(((253, 243, 233, 255), (247, 182, 77, 255), (19, 29, 39, 128)))

    calibrated = _calibrate_opaque_owned_pixels(owned, (0, 0, 3, 1), composite_path)

    assert tuple(calibrated.getdata()) == (
        (254, 244, 234, 255),
        (247, 182, 77, 255),
        (19, 29, 39, 128),
    )


def test_exact_opaque_calibration_skips_retained_glyph_mask(tmp_path):
    composite = Image.new("RGBA", (3, 1))
    composite.putdata(((10, 20, 30, 255), (40, 50, 60, 255), (70, 80, 90, 255)))
    composite_path = tmp_path / "composite.png"
    composite.save(composite_path)
    owned = Image.new("RGBA", (3, 1), (200, 210, 220, 255))
    protected = Image.new("L", (3, 1), 0)
    protected.putpixel((1, 0), 255)

    calibrated = _calibrate_opaque_owned_pixels_to_target(
        owned,
        (0, 0, 3, 1),
        composite_path,
        protected,
    )

    assert tuple(calibrated.getdata()) == (
        (10, 20, 30, 255),
        (200, 210, 220, 255),
        (70, 80, 90, 255),
    )


def test_channel_rounding_calibration_learns_lut_without_copying_positions(tmp_path):
    owned = Image.new("RGBA", (34, 1))
    owned.putdata(((10, 20, 30, 255),) * 32 + ((40, 50, 60, 255),) + ((10, 20, 30, 128),))
    composite = Image.new("RGBA", owned.size)
    composite.putdata(((11, 20, 31, 255),) * 31 + ((9, 20, 29, 255),)
                      + ((40, 50, 60, 255),) + ((200, 200, 200, 255),))
    composite_path = tmp_path / "composite.png"
    composite.save(composite_path)

    calibrated = _calibrate_channel_rounding(owned, (0, 0, 34, 1), composite_path)

    pixels = tuple(calibrated.getdata())
    assert pixels[:32] == ((11, 20, 31, 255),) * 32
    assert pixels[32] == (40, 50, 60, 255)
    assert pixels[33] == (10, 20, 30, 128)


def test_translucent_calibration_solves_fgui_blend_against_existing_backdrop(tmp_path):
    composite_path = tmp_path / "composite.png"
    underlay_path = tmp_path / "underlay.png"
    Image.new("RGBA", (2, 1), (20, 40, 60, 255)).save(underlay_path)
    Image.new("RGBA", (2, 1), (70, 90, 110, 255)).save(composite_path)
    owned = Image.new("RGBA", (2, 1))
    owned.putdata(((100, 100, 100, 128), (1, 2, 3, 255)))

    calibrated = _calibrate_translucent_owned_pixels(
        owned,
        (0, 0, 2, 1),
        underlay_path,
        (0, 0, 2, 1),
        composite_path,
    )

    pixels = tuple(calibrated.getdata())
    assert pixels[0] == (120, 140, 160, 128)
    assert pixels[1] == (1, 2, 3, 255)


def test_observed_calibration_recovers_real_underlay_and_minimum_alpha():
    resource = Image.new("RGBA", (2, 1))
    resource.putdata(((0, 0, 0, 128), (100, 100, 100, 128)))
    actual = Image.new("RGB", (2, 1), (125, 120, 115))
    target = Image.new("RGB", (2, 1), (10, 20, 30))

    calibrated = _calibrate_observed_translucent_pixels(
        resource,
        actual,
        target,
        screen_xy=(0, 0),
        protected_bounds=((1, 0, 2, 1),),
    )

    first = calibrated.getpixel((0, 0))
    assert 128 < first[3] < 255
    inferred_backdrop = (250, 240, 230)
    blended = tuple(
        (first[channel] * first[3]
         + inferred_backdrop[channel] * (255 - first[3]) + 127) // 255
        for channel in range(3)
    )
    assert blended == (10, 20, 30)
    assert calibrated.getpixel((1, 0)) == (100, 100, 100, 128)


def test_observed_calibration_can_restore_missing_effect_pixels_next_to_alpha():
    resource = Image.new("RGBA", (5, 1))
    resource.putpixel((2, 0), (80, 60, 40, 255))
    actual = Image.new("RGB", (5, 1), (200, 180, 160))
    actual.putpixel((2, 0), (80, 60, 40))
    target = actual.copy()
    target.putpixel((1, 0), (140, 120, 100))

    calibrated = _calibrate_observed_translucent_pixels(
        resource,
        actual,
        target,
        screen_xy=(0, 0),
    )

    foreground = calibrated.getpixel((1, 0))
    assert foreground[3] > 0
    blended = tuple(
        (foreground[channel] * foreground[3]
         + actual.getpixel((1, 0))[channel] * (255 - foreground[3]) + 127) // 255
        for channel in range(3)
    )
    assert blended == target.getpixel((1, 0))
    assert calibrated.getpixel((4, 0)) == (0, 0, 0, 0)


def test_observed_calibration_copies_target_for_uncovered_opaque_pixels():
    resource = Image.new("RGBA", (2, 1))
    resource.putdata(((10, 20, 30, 255), (40, 50, 60, 255)))
    actual = Image.new("RGB", (2, 1))
    actual.putdata(((10, 20, 30), (40, 50, 60)))
    target = Image.new("RGB", (2, 1))
    target.putdata(((70, 80, 90), (100, 110, 120)))

    calibrated = _calibrate_observed_translucent_pixels(
        resource,
        actual,
        target,
        screen_xy=(0, 0),
        protected_bounds=((1, 0, 2, 1),),
    )

    assert calibrated.getpixel((0, 0)) == (70, 80, 90, 255)
    assert calibrated.getpixel((1, 0)) == (40, 50, 60, 255)


def test_observed_calibration_can_make_edge_fully_opaque_to_cover_underlay():
    resource = Image.new("RGBA", (1, 1), (0, 0, 0, 200))
    actual = Image.new("RGB", (1, 1), (55, 55, 55))
    target = Image.new("RGB", (1, 1), (0, 0, 0))

    calibrated = _calibrate_observed_translucent_pixels(
        resource,
        actual,
        target,
        screen_xy=(0, 0),
    )

    assert calibrated.getpixel((0, 0)) == (0, 0, 0, 255)


def test_owned_visual_resource_is_content_checked_and_keeps_leaf_partition(tmp_path, monkeypatch):
    store = PsdSourceStore(tmp_path)
    source_id = "a" * 64
    root = tmp_path / source_id
    root.mkdir()
    (root / "source.psd").write_bytes(b"psd")
    monkeypatch.setattr(store, "get", lambda _: SimpleNamespace())
    monkeypatch.setattr(store, "artifact_path", lambda _: root)
    monkeypatch.setattr("figma_to_fgui.psd_source_store.PSDImage.open", lambda _: object())
    calls = []

    def render(document, source, group, owned, retained):
        calls.append((group, owned, retained))
        return Image.new("RGBA", (12, 8), (20, 40, 60, 255)), (2, 3, 14, 11)

    monkeypatch.setattr("figma_to_fgui.psd_effect_render.render_owned_visual", render)
    arguments = {"anchor_id": "body", "group_id": "button",
                 "owned_ids": frozenset({"body", "stroke"}),
                 "retained_ids": frozenset({"label"})}
    result = store.owned_visual_resource(source_id, **arguments)
    assert result.layer_id == "body" and result.bounds == (2, 3, 14, 11)
    expected_key_data = json.dumps(
        [
            "owned-visual-v14",
            source_id,
            "button",
            ["body", "stroke"],
            ["label"],
            False,
            None,
        ],
        separators=(",", ":"),
    )
    assert result.key == "psd-owned-" + hashlib.sha256(
        expected_key_data.encode("utf-8")
    ).hexdigest()[:32]
    assert calls == [("button", frozenset({"body", "stroke"}), frozenset({"label"}))]
    store.owned_visual_resource(source_id, **arguments)
    assert len(calls) == 1
    (root / "resources" / result.key).write_bytes(b"corrupt")
    store.owned_visual_resource(source_id, **arguments)
    assert len(calls) == 2
    with pytest.raises(PsdSourceStoreError, match="ownership_incomplete"):
        store.owned_visual_resource(source_id, **{**arguments, "anchor_id": "elsewhere"})


def test_owned_visual_resource_never_copies_retained_text_from_full_composite(
    tmp_path, monkeypatch
):
    store = PsdSourceStore(tmp_path)
    source_id = "d" * 64
    root = tmp_path / source_id
    root.mkdir()
    (root / "source.psd").write_bytes(b"psd")
    direct_export = Image.new("RGBA", (4, 2), (80, 160, 60, 255))
    full_composite = direct_export.copy()
    full_composite.putpixel((1, 0), (255, 255, 255, 255))
    full_composite.putpixel((2, 0), (255, 255, 255, 255))
    full_composite.save(root / "composite.png")

    monkeypatch.setattr(store, "get", lambda _: SimpleNamespace())
    monkeypatch.setattr(store, "artifact_path", lambda _: root)
    monkeypatch.setattr("figma_to_fgui.psd_source_store.PSDImage.open", lambda _: object())
    monkeypatch.setattr(
        "figma_to_fgui.psd_effect_render.render_owned_visual",
        lambda *_: (direct_export.copy(), (0, 0, 4, 2)),
    )
    # A retained-layer mask is only a diagnostic safeguard. The exported
    # visual resource must not depend on it to avoid copying full-composite
    # text into an otherwise opaque button background.
    monkeypatch.setattr(
        "figma_to_fgui.psd_source_store._retained_visual_mask",
        lambda *_: Image.new("L", (4, 2), 0),
    )

    resource = store.owned_visual_resource(
        source_id,
        anchor_id="body",
        group_id="button",
        owned_ids=frozenset({"body"}),
        retained_ids=frozenset({"label"}),
    )

    with Image.open(root / "resources" / resource.key) as exported:
        assert tuple(exported.convert("RGBA").getdata()) == tuple(direct_export.getdata())


def test_owned_visual_resource_rejects_visual_echo(tmp_path, monkeypatch):
    store = PsdSourceStore(tmp_path)
    source_id = "b" * 64
    monkeypatch.setattr(store, "get", lambda _: SimpleNamespace())

    with pytest.raises(PsdSourceStoreError, match="visual_echo_forbidden"):
        store.owned_visual_resource(
            source_id,
            anchor_id="body",
            group_id="button",
            owned_ids=frozenset({"body"}),
            retained_ids=frozenset(),
            visual_echo=True,
        )


def test_root_backdrop_composite_retains_every_non_overlay_leaf(tmp_path, monkeypatch):
    store = PsdSourceStore(tmp_path)
    source_id = "c" * 64
    root = tmp_path / source_id
    resources = root / "resources"
    resources.mkdir(parents=True)
    Image.new("RGBA", (4, 4), (10, 20, 30, 255)).save(resources / "base.png")
    Image.new("RGBA", (4, 4), (0, 0, 0, 64)).save(resources / "overlay.png")
    layers = (
        SimpleNamespace(id="base", parent_id=None, kind="pixel", effective_visible=True,
                        bounds=(0, 0, 4, 4)),
        SimpleNamespace(id="shade", parent_id=None, kind="pixel", effective_visible=True,
                        bounds=(0, 0, 4, 4)),
        SimpleNamespace(id="label", parent_id=None, kind="type", effective_visible=True,
                        bounds=(0, 0, 4, 4)),
    )
    monkeypatch.setattr(store, "get", lambda _: SimpleNamespace(layers=layers))
    monkeypatch.setattr(store, "artifact_path", lambda _: root)
    monkeypatch.setattr(
        store,
        "raster_resource",
        lambda *_: PsdRasterResource("base", "base.png", "image/png", 1, (0, 0, 4, 4)),
    )
    calls = []

    def owned(*_, **kwargs):
        calls.append(kwargs)
        return PsdRasterResource("shade", "overlay.png", "image/png", 1, (0, 0, 4, 4))

    monkeypatch.setattr(store, "owned_visual_resource", owned)

    result = store.composited_visual_resource(
        source_id,
        anchor_id="base",
        group_id="psd-root:synthetic",
        overlay_ids=frozenset({"shade"}),
    )

    assert result.bounds == (0, 0, 4, 4)
    assert calls[0]["owned_ids"] == frozenset({"shade"})
    assert calls[0]["retained_ids"] == frozenset({"base", "label"})
