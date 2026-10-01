from __future__ import annotations

from pathlib import Path

from figma_to_fgui.hifi_mapping import build_mapping
from figma_to_fgui.hifi_project_inspector import (
    inspect_component,
    inspect_hifi_targets,
    target_from_option,
)
from figma_to_fgui.psd_hifi_adapter import psd_lossless_blockers, psd_source_manifest
from figma_to_fgui.psd_intake import PsdInspection, PsdLayer
from figma_to_fgui.psd_source_store import PsdRasterResource, PsdSource
from figma_to_fgui.uploaded_project import index_uploaded_project

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement"


def test_overflow_pixels_cannot_be_proved_by_an_in_canvas_screenshot() -> None:
    from dataclasses import replace
    inspection = PsdInspection(
        source_name="overflow.psd", byte_size=1, sha256="a" * 64,
        width=100, height=200, depth=8, color_mode="RGB", layer_count=1,
        kind_counts={"pixel": 1}, text_layer_count=0, smart_object_count=0,
        adjustment_layer_count=0, effect_layer_count=0, blocking_issues=(), warnings=(),
    )
    source = PsdSource(1, "a" * 64, inspection, (_layer(0, "Overflow", "pixel", (-5, 0, 100, 200)),))
    assert psd_lossless_blockers(source) == ("outside_canvas_content_requires_equivalence_check",)
    inside = replace(source, layers=(replace(source.layers[0], bounds=(0, 0, 100, 200)),))
    assert psd_lossless_blockers(inside) == ()
    hidden = replace(source, layers=(replace(source.layers[0], effective_visible=False),))
    assert psd_lossless_blockers(hidden) == ()


def _layer(
    index: int,
    name: str,
    kind: str,
    bounds: tuple[int, int, int, int],
    *,
    parent_id: str | None = None,
    text: str | None = None,
) -> PsdLayer:
    source_hash = "a" * 64
    return PsdLayer(
        id=f"psd-layer:{source_hash}:{index + 1}",
        native_id=index + 1,
        parent_id=parent_id,
        document_index=index,
        sibling_index=index,
        name=name,
        path=(name,),
        kind=kind,
        bounds=bounds,
        visible=True,
        effective_visible=True,
        opacity=255,
        blend_mode="normal",
        clipping=False,
        text=text,
        has_pixel_mask=False,
        has_vector_mask=False,
        has_effects=False,
    )


def test_psd_hifi_ir_reuses_existing_mapping_with_hierarchy_and_text() -> None:
    layers = (
        _layer(0, "TitleBar", "type", (48, 30, 404, 76), text="Village Notes"),
        _layer(1, "CharacterSilhouette", "shape", (54, 101, 264, 347)),
        _layer(2, "Btn_Next", "group", (568, 337, 688, 381)),
    )
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=750,
            height=420,
            depth=8,
            color_mode="RGB",
            layer_count=len(layers),
            kind_counts={"group": 1, "shape": 1, "type": 1},
            text_layer_count=1,
            smart_object_count=0,
            adjustment_layer_count=0,
            effect_layer_count=0,
            blocking_issues=(),
            warnings=(),
        ),
        layers=layers,
    )
    manifest = psd_source_manifest(source)

    root = FIXTURE / "old_project"
    project = index_uploaded_project(root, "old.zip")
    tree = inspect_hifi_targets(root, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    component = next(item for item in directory.components if item.resource_id == "sketch01")
    inventory = inspect_component(root, target_from_option(project, package, directory, component))
    mapping = build_mapping(inventory, manifest)

    assert manifest.top_level_nodes[0].type == "FRAME"
    assert manifest.top_level_nodes[0].children[0].text == "Village Notes"
    title = next(item for item in mapping.items if item.old_name == "TitleBar")
    assert title.figma_node_id == layers[0].id
    assert title.status == "matched"
    assert mapping.model_dump_json() == build_mapping(inventory, manifest).model_dump_json()


def test_psd_hifi_ir_attaches_generated_raster_to_visual_layer() -> None:
    from dataclasses import replace
    layer = _layer(0, "Card", "shape", (10, 20, 110, 80))
    layer = replace(layer, opacity=128)
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=750,
            height=420,
            depth=8,
            color_mode="RGB",
            layer_count=1,
            kind_counts={"shape": 1},
            text_layer_count=0,
            smart_object_count=0,
            adjustment_layer_count=0,
            effect_layer_count=0,
            blocking_issues=("shape_styles_require_equivalence_check",),
            warnings=(),
        ),
        layers=(layer,),
    )
    raster = PsdRasterResource(
        layer_id=layer.id,
        key="psd-card",
        mime_type="image/png",
        size=456,
        bounds=(7, 17, 113, 83),
    )

    manifest = psd_source_manifest(source, raster_resources={layer.id: raster})

    assert manifest.resources[0].key == "psd-card"
    assert manifest.resources[0].size == 456
    assert manifest.top_level_nodes[0].children[0].resource_keys == ("psd-card",)
    assert manifest.top_level_nodes[0].children[0].opacity == 1.0
    assert psd_source_manifest(source).top_level_nodes[0].children[0].opacity == 128 / 255
    bounds = manifest.top_level_nodes[0].children[0].bounds
    assert (bounds.x, bounds.y, bounds.width, bounds.height) == (7, 17, 106, 66)


def test_psd_hifi_ir_preserves_all_group_children_and_ignores_legacy_group_raster() -> None:
    group = _layer(0, "ChallengeButton", "group", (665, 1976, 1053, 2090))
    shape = _layer(
        1,
        "GreenFill",
        "shape",
        (666, 1976, 1052, 2090),
        parent_id=group.id,
    )
    label = _layer(
        2,
        "Challenge",
        "type",
        (748, 2013, 971, 2062),
        parent_id=group.id,
        text="Challenge",
    )
    adjustment = _layer(
        3,
        "GreenAdjustment",
        "huesaturation",
        (665, 1976, 1053, 2090),
        parent_id=group.id,
    )
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=1080,
            height=2340,
            depth=8,
            color_mode="RGB",
            layer_count=4,
            kind_counts={"group": 1, "shape": 1, "type": 1, "huesaturation": 1},
            text_layer_count=1,
            smart_object_count=0,
            adjustment_layer_count=0,
            effect_layer_count=0,
            blocking_issues=(),
            warnings=(),
        ),
        layers=(group, shape, label, adjustment),
    )
    raster = PsdRasterResource(
        layer_id=group.id,
        key="psd-challenge-group",
        mime_type="image/png",
        size=789,
    )

    manifest = psd_source_manifest(source, raster_resources={group.id: raster})

    group_node = manifest.top_level_nodes[0].children[0]
    assert group_node.type == "GROUP"
    assert group_node.resource_keys == ()
    assert group_node.properties["psdCompositeGroup"] is False
    assert [child.id for child in group_node.children] == [shape.id, label.id, adjustment.id]
    assert group_node.children[1].text == "Challenge"


def test_psd_hifi_ir_preserves_nested_group_layers() -> None:
    controls = _layer(0, "BottomControls", "group", (28, 1976, 1053, 2090))
    back_button = _layer(
        1,
        "BackButton",
        "smartobject",
        (28, 1976, 142, 2090),
        parent_id=controls.id,
    )
    challenge = _layer(
        2,
        "ChallengeButton",
        "group",
        (665, 1976, 1053, 2090),
        parent_id=controls.id,
    )
    fill = _layer(
        3,
        "GreenFill",
        "shape",
        (665, 1976, 1053, 2090),
        parent_id=challenge.id,
    )
    label = _layer(
        4,
        "Challenge",
        "type",
        (748, 2013, 971, 2062),
        parent_id=challenge.id,
        text="Challenge",
    )
    adjustment = _layer(
        5,
        "GreenAdjustment",
        "huesaturation",
        (665, 1976, 1053, 2090),
        parent_id=challenge.id,
    )
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=1080,
            height=2340,
            depth=8,
            color_mode="RGB",
            layer_count=6,
            kind_counts={
                "group": 2,
                "smartobject": 1,
                "shape": 1,
                "type": 1,
                "huesaturation": 1,
            },
            text_layer_count=1,
            smart_object_count=1,
            adjustment_layer_count=1,
            effect_layer_count=0,
            blocking_issues=(),
            warnings=(),
        ),
        layers=(controls, back_button, challenge, fill, label, adjustment),
    )
    raster = PsdRasterResource(
        layer_id=challenge.id,
        key="psd-challenge-group",
        mime_type="image/png",
        size=789,
    )

    manifest = psd_source_manifest(source, raster_resources={challenge.id: raster})

    controls_node = manifest.top_level_nodes[0].children[0]
    assert controls_node.properties["psdCompositeGroup"] is False
    assert [child.id for child in controls_node.children] == [
        back_button.id,
        challenge.id,
    ]
    challenge_node = controls_node.children[1]
    assert challenge_node.properties["psdCompositeGroup"] is False
    assert challenge_node.resource_keys == ()
    assert [child.id for child in challenge_node.children] == [fill.id, label.id, adjustment.id]


def test_psd_hifi_ir_keeps_granular_layers_when_nested_effect_group_has_no_bounds() -> None:
    group = _layer(0, "LeftRail", "group", (0, -19, 133, 1876))
    nested = PsdLayer(
        **{
            **_layer(1, "EffectGroup", "group", (0, 0, 0, 0), parent_id=group.id).__dict__,
            "has_effects": True,
        }
    )
    shape = _layer(2, "RailFill", "shape", (27, 1434, 131, 1876), parent_id=group.id)
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=1080,
            height=2340,
            depth=8,
            color_mode="RGB",
            layer_count=3,
            kind_counts={"group": 2, "shape": 1},
            text_layer_count=0,
            smart_object_count=0,
            adjustment_layer_count=0,
            effect_layer_count=1,
            blocking_issues=(),
            warnings=(),
        ),
        layers=(group, nested, shape),
    )

    manifest = psd_source_manifest(source)

    group_node = manifest.top_level_nodes[0].children[0]
    assert group_node.properties["psdCompositeGroup"] is False
    assert [child.id for child in group_node.children] == [nested.id, shape.id]


def test_psd_hifi_ir_keeps_simple_text_banner_granular() -> None:
    group = _layer(0, "RewardBanner", "group", (22, 944, 397, 1016))
    shape = _layer(1, "BannerFill", "shape", (22, 950, 397, 1011), parent_id=group.id)
    label = _layer(
        2,
        "Noble Reception",
        "type",
        (67, 967, 352, 1003),
        parent_id=group.id,
        text="Noble Reception",
    )
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=1080,
            height=2340,
            depth=8,
            color_mode="RGB",
            layer_count=3,
            kind_counts={"group": 1, "shape": 1, "type": 1},
            text_layer_count=1,
            smart_object_count=0,
            adjustment_layer_count=0,
            effect_layer_count=0,
            blocking_issues=(),
            warnings=(),
        ),
        layers=(group, shape, label),
    )

    manifest = psd_source_manifest(source)

    group_node = manifest.top_level_nodes[0].children[0]
    assert group_node.properties["psdCompositeGroup"] is False
    assert [child.id for child in group_node.children] == [shape.id, label.id]


def test_psd_hifi_ir_does_not_bake_tall_cross_cutting_group() -> None:
    group = _layer(0, "LeftRail", "group", (0, -19, 133, 1876))
    adjustment = _layer(
        1,
        "RailAdjustment",
        "huesaturation",
        (0, -19, 133, 1876),
        parent_id=group.id,
    )
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=1080,
            height=2340,
            depth=8,
            color_mode="RGB",
            layer_count=2,
            kind_counts={"group": 1, "huesaturation": 1},
            text_layer_count=0,
            smart_object_count=0,
            adjustment_layer_count=1,
            effect_layer_count=0,
            blocking_issues=(),
            warnings=(),
        ),
        layers=(group, adjustment),
    )

    manifest = psd_source_manifest(source)

    group_node = manifest.top_level_nodes[0].children[0]
    assert group_node.properties["psdCompositeGroup"] is False
    assert [child.id for child in group_node.children] == [adjustment.id]


def test_psd_hifi_ir_does_not_bake_nested_control_inside_tall_group() -> None:
    rail = _layer(0, "LeftRail", "group", (0, -19, 133, 1876))
    record = _layer(1, "Record", "group", (25, 1599, 133, 1700), parent_id=rail.id)
    icon = _layer(2, "RecordIcon", "pixel", (45, 1600, 120, 1660), parent_id=record.id)
    label = _layer(
        3,
        "Record",
        "type",
        (25, 1660, 133, 1700),
        parent_id=record.id,
        text="Record",
    )
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=1080,
            height=2340,
            depth=8,
            color_mode="RGB",
            layer_count=4,
            kind_counts={"group": 2, "pixel": 1, "type": 1},
            text_layer_count=1,
            smart_object_count=0,
            adjustment_layer_count=0,
            effect_layer_count=0,
            blocking_issues=(),
            warnings=(),
        ),
        layers=(rail, record, icon, label),
    )

    manifest = psd_source_manifest(source)

    rail_node = manifest.top_level_nodes[0].children[0]
    record_node = rail_node.children[0]
    assert record_node.properties["psdCompositeGroup"] is False
    assert [child.id for child in record_node.children] == [icon.id, label.id]


def test_psd_hifi_ir_keeps_dense_text_strip_granular() -> None:
    group = _layer(0, "DailyReward", "group", (37, 1039, 380, 1073))
    icon = _layer(1, "Coin", "smartobject", (274, 1039, 306, 1071), parent_id=group.id)
    label = _layer(
        2,
        "Available Today",
        "type",
        (37, 1042, 272, 1073),
        parent_id=group.id,
        text="Available Today",
    )
    source = PsdSource(
        version=1,
        source_id="a" * 64,
        inspection=PsdInspection(
            source_name="screen.psd",
            byte_size=123,
            sha256="a" * 64,
            width=1080,
            height=2340,
            depth=8,
            color_mode="RGB",
            layer_count=3,
            kind_counts={"group": 1, "smartobject": 1, "type": 1},
            text_layer_count=1,
            smart_object_count=1,
            adjustment_layer_count=0,
            effect_layer_count=0,
            blocking_issues=(),
            warnings=(),
        ),
        layers=(group, icon, label),
    )

    manifest = psd_source_manifest(source)

    group_node = manifest.top_level_nodes[0].children[0]
    assert group_node.properties["psdCompositeGroup"] is False
    assert [child.id for child in group_node.children] == [icon.id, label.id]
