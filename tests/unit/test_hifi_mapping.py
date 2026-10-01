from __future__ import annotations

from pathlib import Path

import pytest

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_mapping import HifiMappingError, apply_mapping_decision, build_mapping
from figma_to_fgui.hifi_project_inspector import (
    inspect_component,
    inspect_hifi_targets,
    target_from_option,
)
from figma_to_fgui.hifi_replacement_models import HifiMappingDecision
from figma_to_fgui.models import Bounds
from figma_to_fgui.uploaded_project import index_uploaded_project

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement"


def _inputs():
    root = FIXTURE / "old_project"
    project = index_uploaded_project(root, "old.zip")
    tree = inspect_hifi_targets(root, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    component = next(item for item in directory.components if item.resource_id == "sketch01")
    inventory = inspect_component(root, target_from_option(project, package, directory, component))
    manifest = SelectionManifest.model_validate_json(
        (FIXTURE / "hifi-selection.json").read_text("utf-8")
    )
    return inventory, manifest


def test_mapping_carries_each_canvas_size_for_full_page_preview() -> None:
    inventory, manifest = _inputs()
    source = manifest.top_level_nodes[0]
    taller = source.model_copy(update={"bounds": Bounds(x=0, y=0, width=900, height=1800)})
    draft = build_mapping(inventory, manifest.model_copy(update={"top_level_nodes": (taller,)}))
    assert draft.old_canvas_size == (inventory.width, inventory.height)
    assert draft.source_canvas_size == (900, 1800)


def test_visible_psd_layer_cannot_be_owned_by_runtime_hidden_old_object() -> None:
    inventory, manifest = _inputs()
    board = next(item for item in inventory.objects if item.object_id == "board_bg")
    hidden_inventory = inventory.model_copy(update={
        "objects": (board.model_copy(update={"default_visible": False}),),
    })
    source_board = manifest.top_level_nodes[0].children[0].model_copy(update={
        "id": "psd-layer:board",
        "type": "IMAGE",
        "properties": {"psdKind": "pixel", "psdDocumentIndex": 1},
    })
    source = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:synthetic",
            name="PSD",
            type="FRAME",
            bounds=manifest.top_level_nodes[0].bounds,
            children=(source_board,),
        ),),
    )

    draft = build_mapping(hidden_inventory, source)

    old_item = next(item for item in draft.items if item.old_object_id == "board_bg")
    assert old_item.action == "keep_old"
    assert old_item.figma_node_id is None
    assert any(item.figma_node_id == "psd-layer:board" and item.status == "hifi_added"
               for item in draft.items)


def test_unmatched_backdrop_stack_is_composited_into_existing_root_background() -> None:
    inventory, manifest = _inputs()
    board = next(item for item in inventory.objects if item.object_id == "board_bg")
    title = next(item for item in inventory.objects if item.object_id == "title_bar")
    focused_inventory = inventory.model_copy(update={"objects": (board, title)})
    base = SelectionNode(
        id="psd-layer:base",
        name="BoardBg",
        type="IMAGE",
        bounds=Bounds(x=0, y=0, width=750, height=420),
        properties={"psdKind": "pixel", "psdDocumentIndex": 1},
    )
    shade = SelectionNode(
        id="psd-layer:shade",
        name="Bottom shade",
        type="IMAGE",
        bounds=Bounds(x=0, y=250, width=750, height=170),
        properties={"psdKind": "pixel", "psdDocumentIndex": 2},
    )
    grade = SelectionNode(
        id="psd-layer:grade",
        name="Edge grade",
        type="IMAGE",
        bounds=Bounds(x=0, y=0, width=750, height=420),
        opacity=0.8,
        properties={"psdKind": "pixel", "psdDocumentIndex": 3},
    )
    label = SelectionNode(
        id="psd-layer:title",
        name="TitleBar",
        type="TEXT",
        text=title.effective_text,
        bounds=Bounds(x=46, y=28, width=360, height=48),
        properties={"psdKind": "type", "psdDocumentIndex": 4},
    )
    source = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:synthetic",
            name="PSD",
            type="FRAME",
            bounds=manifest.top_level_nodes[0].bounds,
            children=(base, shade, grade, label),
        ),),
    )

    draft = build_mapping(
        focused_inventory,
        source,
        owned_visual_validator=lambda *_: True,
    )

    background = next(item for item in draft.items if item.old_object_id == "board_bg")
    assert background.figma_node_id == "psd-layer:base"
    assert background.composite_group_id == "psd-root:synthetic"
    assert background.composite_source_ids == ("psd-layer:grade", "psd-layer:shade")
    assert not any(item.status == "hifi_added" for item in draft.items)


def test_psd_multi_leaf_skin_without_image_owner_stays_incomplete(tmp_path) -> None:
    from test_hifi_nested import nested_case

    _, inventory, _, _ = nested_case(tmp_path)
    selected = tuple(o.model_copy(update={"raster_conversion_allowed": True})
                     if o.object_id == "a:bg" else o
                     for o in inventory.objects if o.object_id in {"a:bg", "a:title"})
    inventory = inventory.model_copy(update={"objects": selected})
    visual = (
        SelectionNode(id="body", name="Background", type="RECTANGLE",
                      bounds=Bounds(x=10, y=20, width=120, height=44),
                      properties={"psdKind": "shape", "psdDocumentIndex": 1}),
        SelectionNode(id="stroke", name="Stroke", type="VECTOR",
                      bounds=Bounds(x=10, y=20, width=120, height=44),
                      properties={"psdKind": "shape", "psdDocumentIndex": 2}),
    )
    title = SelectionNode(id="label", name="title", type="TEXT", text="New",
                          bounds=Bounds(x=20, y=30, width=90, height=24),
                          properties={"psdKind": "type"})
    group = SelectionNode(id="button", name="Button", type="GROUP",
                          bounds=Bounds(x=10, y=20, width=120, height=44),
                          properties={"psdKind": "group"}, children=(*visual, title))
    source = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(
        SelectionNode(id="psd-root:synthetic", name="PSD", type="FRAME",
                      bounds=Bounds(x=0, y=0, width=750, height=420), children=(group,)),
    ))
    validated = []

    def proof(group_id, owned, retained):
        validated.append((group_id, owned, retained))
        return True

    draft = build_mapping(inventory, source, owned_visual_validator=proof)
    body = next(i for i in draft.items if i.old_object_id == "a:bg")
    assert validated == []
    assert body.owned_source_ids == ()
    assert body.owned_group_id is None
    assert body.retained_source_ids == ()
    assert body.action == "accept"
    mapped_title = next(i for i in draft.items if i.old_object_id == "a:title")
    assert mapped_title.action == "accept"
    assert any(i.figma_node_id == "stroke" and i.status == "hifi_added"
               for i in draft.items)
    from figma_to_fgui.hifi_mapping import require_psd_coverage

    with pytest.raises(HifiMappingError, match="coverage"):
        require_psd_coverage(draft, source)
    rejected = build_mapping(inventory, source, owned_visual_validator=lambda *_: False)
    assert any(i.figma_node_id == "stroke" and i.status == "hifi_added"
               for i in rejected.items)


def test_owned_psd_leaves_replace_overlapping_legacy_visual_siblings(tmp_path) -> None:
    from test_hifi_nested import nested_case

    _, inventory, _, _ = nested_case(tmp_path)
    body = next(o for o in inventory.objects if o.object_id == "a:bg")
    title = next(o for o in inventory.objects if o.object_id == "a:title")
    icon = body.model_copy(update={
        "object_id": "a:icon",
        "local_object_id": "icon",
        "name": "icon",
        "object_type": "loader",
        "x": 42,
        "y": 28,
        "width": 24,
        "height": 24,
        "child_index": 1,
        "raster_conversion_allowed": True,
    })
    inventory = inventory.model_copy(update={
        "objects": (
            body.model_copy(update={"raster_conversion_allowed": True}),
            icon,
            title.model_copy(update={"child_index": 2, "runtime_text_override": True}),
        ),
    })
    visual = (
        SelectionNode(id="body", name="Background", type="RECTANGLE",
                      bounds=Bounds(x=10, y=20, width=120, height=44),
                      properties={"psdKind": "shape"}),
        SelectionNode(id="stroke", name="Stroke", type="VECTOR",
                      bounds=Bounds(x=10, y=20, width=120, height=44),
                      properties={"psdKind": "shape"}),
    )
    label = SelectionNode(id="label", name="title", type="TEXT", text="New",
                          bounds=Bounds(x=20, y=30, width=90, height=24),
                          properties={"psdKind": "type"})
    group = SelectionNode(id="button", name="Button", type="GROUP",
                          bounds=Bounds(x=10, y=20, width=120, height=44),
                          properties={"psdKind": "group"}, children=(*visual, label))
    source = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(
        SelectionNode(id="psd-root:synthetic", name="PSD", type="FRAME",
                      bounds=Bounds(x=0, y=0, width=750, height=420), children=(group,)),
    ))
    validated: list[tuple[str, frozenset[str], frozenset[str]]] = []

    def proof(group_id, owned, retained):
        validated.append((group_id, owned, retained))
        return True

    draft = build_mapping(inventory, source, owned_visual_validator=proof)
    mapped_body = next(i for i in draft.items if i.old_object_id == "a:bg")
    mapped_icon = next(i for i in draft.items if i.old_object_id == "a:icon")

    assert mapped_body.action == "accept"
    assert mapped_body.owned_source_ids == ()
    assert mapped_icon.owned_source_ids == ()
    assert mapped_icon.retained_source_ids == ()
    assert mapped_icon.figma_node_id == "stroke"
    assert mapped_icon.action == "accept"
    assert mapped_icon.visual_echo is False
    assert validated == []

    rebuilt = build_mapping(
        inventory,
        source,
        owned_visual_validator=proof,
        proven_source_owners={
            item.old_object_id: item.figma_node_id
            for item in draft.items
            if item.old_object_id and item.figma_node_id and item.owned_source_ids
        },
    )
    rebuilt_icon = next(item for item in rebuilt.items if item.old_object_id == "a:icon")
    rebuilt_title = next(item for item in rebuilt.items if item.old_object_id == "a:title")
    assert rebuilt_icon.owned_source_ids == ()
    assert rebuilt_icon.retained_source_ids == ()
    assert rebuilt_icon.visual_echo is False
    assert rebuilt_title.preserve_runtime_text is True


def test_component_skin_prefers_existing_loader_over_converting_background_graph(tmp_path) -> None:
    from test_hifi_nested import nested_case

    _, inventory, _, _ = nested_case(tmp_path)
    component = next(o for o in inventory.objects if o.object_id == "a")
    graph = next(o for o in inventory.objects if o.object_id == "a:bg")
    title = next(o for o in inventory.objects if o.object_id == "a:title")
    loader = graph.model_copy(update={
        "object_id": "a:skin",
        "local_object_id": "skin",
        "name": "skin",
        "object_type": "loader",
        "child_index": 1,
        "raster_conversion_allowed": True,
    })
    focused = inventory.model_copy(update={"objects": (
        component,
        graph.model_copy(update={"raster_conversion_allowed": True}),
        loader,
        title.model_copy(update={"child_index": 2}),
    )})
    body = SelectionNode(
        id="body", name="Body", type="RECTANGLE",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        properties={"psdKind": "shape", "psdDocumentIndex": 1},
    )
    stroke = SelectionNode(
        id="stroke", name="Stroke", type="VECTOR",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        properties={"psdKind": "shape", "psdDocumentIndex": 2},
    )
    label = SelectionNode(
        id="label", name="title", type="TEXT", text="Default",
        bounds=Bounds(x=20, y=30, width=90, height=24),
        properties={"psdKind": "type", "psdDocumentIndex": 3},
    )
    group = SelectionNode(
        id="button", name="Delegate", type="GROUP",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        properties={"psdKind": "group"}, children=(body, stroke, label),
    )
    source = SelectionManifest(
        version=1, display_name="PSD", top_level_nodes=(SelectionNode(
            id="psd-root:test", name="PSD", type="FRAME",
            bounds=Bounds(x=0, y=0, width=750, height=420), children=(group,),
        ),),
    )

    draft = build_mapping(focused, source, owned_visual_validator=lambda *_: True)

    graph_item = next(i for i in draft.items if i.old_object_id == "a:bg")
    loader_item = next(i for i in draft.items if i.old_object_id == "a:skin")
    assert graph_item.action == "keep_old"
    assert not graph_item.graph_conversion_proven
    assert loader_item.action == "accept"
    assert loader_item.owned_source_ids == ("body", "stroke")


def test_owned_group_partitions_visuals_across_existing_image_and_loader(tmp_path) -> None:
    """One owned PSD group must not be repeated over an old visual sibling."""
    from test_hifi_nested import nested_case

    _, inventory, _, _ = nested_case(tmp_path)
    component = next(o for o in inventory.objects if o.object_id == "a")
    original = next(o for o in inventory.objects if o.object_id == "a:bg")
    title = next(o for o in inventory.objects if o.object_id == "a:title")
    background = original.model_copy(update={
        "object_type": "image",
        "resource_id": "legacy-background",
        "raster_conversion_allowed": True,
    })
    icon = original.model_copy(update={
        "object_id": "a:icon",
        "local_object_id": "icon",
        "name": "icon",
        "object_type": "loader",
        "resource_id": None,
        "x": 20,
        "y": 28,
        "width": 24,
        "height": 24,
        "child_index": 1,
        "raster_conversion_allowed": True,
    })
    focused = inventory.model_copy(update={"objects": (
        component,
        background,
        icon,
        title.model_copy(update={"child_index": 2}),
    )})
    body = SelectionNode(
        id="body", name="Background", type="RECTANGLE",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        properties={"psdKind": "shape", "psdDocumentIndex": 1},
    )
    icon_leaf = SelectionNode(
        id="icon", name="Icon", type="IMAGE",
        bounds=Bounds(x=20, y=28, width=24, height=24),
        properties={"psdKind": "smartobject", "psdDocumentIndex": 2},
    )
    ornament = SelectionNode(
        id="ornament", name="Info", type="VECTOR",
        bounds=Bounds(x=100, y=28, width=16, height=16),
        properties={"psdKind": "shape", "psdDocumentIndex": 3},
    )
    label = SelectionNode(
        id="label", name="title", type="TEXT", text="Default",
        bounds=Bounds(x=48, y=30, width=44, height=20),
        properties={"psdKind": "type", "psdDocumentIndex": 4},
    )
    group = SelectionNode(
        id="header", name="Header", type="GROUP",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        properties={"psdKind": "group"},
        children=(body, icon_leaf, ornament, label),
    )
    source = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:test", name="PSD", type="FRAME",
            bounds=Bounds(x=0, y=0, width=750, height=420),
            children=(group,),
        ),),
    )

    draft = build_mapping(focused, source, owned_visual_validator=lambda *_: True)

    mapped_background = next(i for i in draft.items if i.old_object_id == "a:bg")
    mapped_icon = next(i for i in draft.items if i.old_object_id == "a:icon")
    assert mapped_background.action == "accept"
    assert mapped_background.owned_source_ids == ("body",)
    assert mapped_icon.action == "accept"
    assert set(mapped_icon.owned_source_ids) == {"icon", "ornament"}
    assert set(mapped_background.owned_source_ids).isdisjoint(mapped_icon.owned_source_ids)


def test_visible_psd_accessory_reuses_controller_hidden_component_loader(tmp_path) -> None:
    """A visible PSD subgroup must activate and skin the old controlled accessory."""
    from test_hifi_nested import nested_case

    _, inventory, _, _ = nested_case(tmp_path)
    root_component = next(o for o in inventory.objects if o.object_id == "a")
    original = next(o for o in inventory.objects if o.object_id == "a:bg")
    background = original.model_copy(update={
        "object_type": "image",
        "resource_id": "legacy-background",
        "raster_conversion_allowed": True,
    })
    accessory = root_component.model_copy(update={
        "object_id": "a:info",
        "local_object_id": "info",
        "name": "btn_info",
        "parent_id": "a",
        "child_index": 2,
        "x": 100,
        "y": 20,
        "width": 24,
        "height": 24,
        "default_visible": False,
        "position_runtime_bound": True,
        "structural_only": True,
        "controller_refs": ("hasTips",),
        "dynamic_properties": ("visible",),
        "instance_path": ("a",),
    })
    accessory_icon = original.model_copy(update={
        "object_id": "a:info:icon",
        "local_object_id": "icon",
        "name": "icon",
        "object_type": "loader",
        "resource_id": None,
        "parent_id": "a:info",
        "child_index": 0,
        "x": 100,
        "y": 20,
        "width": 24,
        "height": 24,
        "default_visible": False,
        "position_runtime_bound": True,
        "structural_only": False,
        "instance_path": ("a", "a:info"),
    })
    focused = inventory.model_copy(update={"objects": (
        root_component,
        background,
        accessory,
        accessory_icon,
    )})
    body = SelectionNode(
        id="body", name="Background", type="RECTANGLE",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        properties={"psdKind": "shape", "psdDocumentIndex": 1},
    )
    ring = SelectionNode(
        id="info-ring", name="Ring", type="VECTOR",
        bounds=Bounds(x=100, y=20, width=24, height=24),
        properties={"psdKind": "shape", "psdDocumentIndex": 2},
    )
    mark = SelectionNode(
        id="info-mark", name="Mark", type="VECTOR",
        bounds=Bounds(x=109, y=24, width=6, height=16),
        properties={"psdKind": "shape", "psdDocumentIndex": 3},
    )
    info_group = SelectionNode(
        id="info-group", name="Info", type="GROUP",
        bounds=Bounds(x=100, y=20, width=24, height=24),
        properties={"psdKind": "group"},
        children=(ring, mark),
    )
    outer = SelectionNode(
        id="header", name="Header", type="GROUP",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        properties={"psdKind": "group"},
        children=(body, info_group),
    )
    source = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:test", name="PSD", type="FRAME",
            bounds=Bounds(x=0, y=0, width=750, height=420),
            children=(outer,),
        ),),
    )

    draft = build_mapping(focused, source, owned_visual_validator=lambda *_: True)

    mapped_component = next(i for i in draft.items if i.old_object_id == "a:info")
    mapped_icon = next(i for i in draft.items if i.old_object_id == "a:info:icon")
    assert mapped_component.action == "accept"
    assert mapped_component.figma_node_id == "info-group"
    assert mapped_icon.action == "accept"
    assert mapped_icon.owned_group_id == "info-group"
    assert set(mapped_icon.owned_source_ids) == {"info-ring", "info-mark"}
    assert not any(
        item.status == "hifi_added" and item.figma_node_id in {"info-ring", "info-mark"}
        for item in draft.items
    )


def test_second_pass_keeps_original_psd_geometry_for_existing_nodes() -> None:
    from figma_to_fgui.hifi_replacement_workflow import HifiReplacementWorkflow

    original_child = SelectionNode(
        id="shape",
        name="shape",
        type="IMAGE",
        bounds=Bounds(x=10, y=20, width=100, height=80),
    )
    original = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="root",
            name="root",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=200, height=200),
            children=(original_child,),
        ),),
    )
    generated = SelectionNode(
        id="state:new",
        name="state",
        type="IMAGE",
        bounds=Bounds(x=1, y=2, width=3, height=4),
    )
    cropped = original.model_copy(update={
        "top_level_nodes": (original.top_level_nodes[0].model_copy(update={
            "children": (
                original_child.model_copy(update={
                    "bounds": Bounds(x=15, y=27, width=70, height=56),
                }),
                generated,
            ),
        }),),
    })

    restored = HifiReplacementWorkflow._restore_source_geometry(cropped, original)

    assert restored.top_level_nodes[0].children[0].bounds == original_child.bounds
    assert restored.top_level_nodes[0].children[1].bounds == generated.bounds


def test_native_graph_layers_are_not_rasterized_for_graph_replacements() -> None:
    from figma_to_fgui.hifi_replacement_workflow import HifiReplacementWorkflow

    assert HifiReplacementWorkflow._exclude_native_graph_rasters(
        ("flat-rounded-rect", "painted-image"),
        {"flat-rounded-rect": {"graph": {"shape": "rect"}}},
    ) == ("painted-image",)


def test_viewport_pixel_check_rejects_letterbox_pixels_outside_component(tmp_path: Path) -> None:
    from PIL import Image

    from figma_to_fgui.hifi_replacement_workflow import HifiReplacementWorkflow

    raster = tmp_path / "letterbox.png"
    image = Image.new("RGBA", (10, 10))
    image.paste((0, 0, 0, 255), (0, 0, 10, 2))
    image.save(raster)

    assert not HifiReplacementWorkflow._raster_intersects_viewport(
        raster,
        (0, 0, 10, 10),
        (0, 2, 10, 8),
    )
    assert HifiReplacementWorkflow._raster_intersects_viewport(
        raster,
        (0, 0, 10, 10),
        (0, 1, 10, 8),
    )


def test_full_viewport_rasters_are_probed_for_transparent_letterboxing() -> None:
    from types import SimpleNamespace

    from figma_to_fgui.hifi_replacement_workflow import HifiReplacementWorkflow

    layers = (
        SimpleNamespace(id="cover", kind="pixel", effective_visible=True,
                        bounds=(0, 0, 1080, 2340)),
        SimpleNamespace(id="partial", kind="pixel", effective_visible=True,
                        bounds=(0, 300, 1080, 1800)),
        SimpleNamespace(id="text", kind="type", effective_visible=True,
                        bounds=(0, 0, 1080, 2340)),
        SimpleNamespace(id="hidden", kind="pixel", effective_visible=False,
                        bounds=(0, 0, 1080, 2340)),
    )

    assert HifiReplacementWorkflow._viewport_probe_layer_ids(
        SimpleNamespace(layers=layers),
        (0, 210, 1080, 1920),
    ) == ("cover",)


def test_proven_visual_owner_stays_on_its_anchor_after_resource_bounds_change(tmp_path) -> None:
    from test_hifi_nested import nested_case

    _, inventory, _, _ = nested_case(tmp_path)
    old = next(o for o in inventory.objects if o.object_id == "a:bg")
    inventory = inventory.model_copy(update={"objects": (old.model_copy(update={
        "raster_conversion_allowed": True,
    }),)})
    nodes = (
        SelectionNode(id="anchor", name="Background", type="IMAGE",
                      bounds=Bounds(x=10, y=20, width=116, height=44)),
        SelectionNode(id="sibling", name="Background", type="IMAGE",
                      bounds=Bounds(x=10, y=20, width=120, height=44)),
    )
    manifest = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(
        SelectionNode(id="psd-root:test", name="PSD", type="FRAME",
                      bounds=Bounds(x=0, y=0, width=750, height=420), children=nodes),
    ))
    draft = build_mapping(inventory, manifest, proven_source_owners={old.object_id: "anchor"})
    assert next(i for i in draft.items if i.old_object_id == old.object_id).figma_node_id == "anchor"


def test_generated_state_is_tied_to_existing_object_only(tmp_path) -> None:
    from test_hifi_nested import nested_case

    _, inventory, _, _ = nested_case(tmp_path)
    old = next(o for o in inventory.objects if o.object_id == "a:title")
    inventory = inventory.model_copy(update={"objects": (old,)})
    state = SelectionNode(id="derived-state:test", name="new state", type="TEXT",
                          bounds=Bounds(x=20, y=30, width=90, height=24),
                          properties={"generatedStateOwner": old.object_id,
                                      "generatedStateRole": "text"})
    manifest = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(
        SelectionNode(id="psd-root:test", name="PSD", type="FRAME",
                      bounds=Bounds(x=0, y=0, width=750, height=420), children=(state,)),
    ))
    draft = build_mapping(inventory, manifest)
    assert draft.unresolved_count == 0
    assert len(draft.items) == 1
    assert draft.items[0].generated_state and draft.items[0].action == "accept"


def test_full_bleed_visual_proof_selects_only_the_unique_matching_pixel_layer() -> None:
    inventory, _ = _inputs()
    old = next(o for o in inventory.objects if o.object_type == "image")
    old = old.model_copy(update={"x": 0, "y": -20, "width": 750, "height": 460,
                                 "name": "legacy backdrop"})
    inventory = inventory.model_copy(update={"objects": (old,), "width": 750, "height": 420})
    candidates = tuple(SelectionNode(
        id=f"backdrop-{index}", name=f"layer {index}", type="IMAGE",
        bounds=Bounds(x=0, y=-20, width=750, height=460),
        properties={"psdKind": "pixel", "blendMode": "normal",
                    "hasEffects": False, "hasPixelMask": False,
                    "hasVectorMask": False, "clipping": False},
    ) for index in range(3))
    manifest = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(
        SelectionNode(id="psd-root:synthetic", name="PSD", type="FRAME",
                      bounds=Bounds(x=0, y=0, width=750, height=420), children=candidates),
    ))
    draft = build_mapping(
        inventory, manifest,
        full_bleed_visual_validator=lambda _old, node: .998 if node.id == "backdrop-2" else 0,
    )
    match = next(i for i in draft.items if i.old_object_id == old.object_id)
    assert (match.figma_node_id, match.action, match.score) == ("backdrop-2", "accept", .998)


def test_unique_renderable_psd_shape_proves_graph_conversion() -> None:
    inventory, _ = _inputs()
    old = next(o for o in inventory.objects if o.object_type == "graph")
    old = old.model_copy(update={"x": 20, "y": 50, "width": 100, "height": 400,
                                 "raster_conversion_allowed": False})
    inventory = inventory.model_copy(update={"objects": (old,), "width": 750, "height": 500})
    shape = SelectionNode(id="shape", name="new rail", type="VECTOR",
                          bounds=Bounds(x=10, y=45, width=100, height=400),
                          properties={"psdKind": "shape", "blendMode": "normal",
                                      "hasVectorMask": True})
    other = shape.model_copy(update={"id": "other", "bounds": Bounds(
        x=500, y=45, width=100, height=400)})
    manifest = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(
        SelectionNode(id="psd-root:test", name="PSD", type="FRAME",
                      bounds=Bounds(x=0, y=0, width=750, height=500),
                      children=(shape, other)),
    ))
    proven = build_mapping(inventory, manifest, graph_raster_validator=lambda n: n.id == "shape")
    match = next(i for i in proven.items if i.old_object_id == old.object_id)
    assert match.action == "accept" and match.graph_conversion_proven
    assert match.figma_node_id == "shape"
    unrenderable = build_mapping(inventory, manifest, graph_raster_validator=lambda _: False)
    assert not any(i.graph_conversion_proven for i in unrenderable.items)
    competing = shape.model_copy(update={"id": "competing", "bounds": Bounds(
        x=15, y=47, width=100, height=400)})
    ambiguous = manifest.model_copy(update={"top_level_nodes": (
        manifest.top_level_nodes[0].model_copy(update={"children": (shape, competing)}),)})
    result = build_mapping(inventory, ambiguous, graph_raster_validator=lambda _: True)
    assert not any(i.graph_conversion_proven for i in result.items)
    competing_old = old.model_copy(update={"object_id": "image-over-graph",
                                            "object_type": "image", "name": "other"})
    shared_pixels = inventory.model_copy(update={"objects": (old, competing_old)})
    result = build_mapping(shared_pixels, manifest, graph_raster_validator=lambda _: True)
    assert not any(i.graph_conversion_proven for i in result.items)


def test_psd_incompatible_type_cannot_be_auto_accepted_or_offered() -> None:
    inventory, manifest = _inputs()
    old = next(item for item in inventory.objects if item.object_type == "image")
    leaf = SelectionNode(id="wrong-text", name=old.name, type="TEXT", text="Wrong",
                         bounds=Bounds(x=old.x, y=old.y, width=old.width, height=old.height))
    root = manifest.top_level_nodes[0].model_copy(update={"id": "psd-root:test", "children": (leaf,)})
    draft = build_mapping(inventory.model_copy(update={"objects": (old,)}),
                          manifest.model_copy(update={"top_level_nodes": (root,)}))
    item = next(item for item in draft.items if item.old_object_id == old.object_id)
    assert item.action == "keep_old"
    assert not item.candidates


def test_zero_area_psd_layer_cannot_claim_old_image() -> None:
    inventory, manifest = _inputs()
    old = next(item for item in inventory.objects if item.object_type == "image")
    empty = SelectionNode(
        id="empty-image", name=old.name, type="IMAGE",
        bounds=Bounds(x=old.x, y=old.y, width=0, height=0),
    )
    root = manifest.top_level_nodes[0].model_copy(update={"id": "psd-root:test", "children": (empty,)})
    draft = build_mapping(
        inventory.model_copy(update={"objects": (old,)}),
        manifest.model_copy(update={"top_level_nodes": (root,)}),
    )
    item = next(item for item in draft.items if item.old_object_id == old.object_id)
    assert item.figma_node_id is None
    assert not item.candidates


def test_wholly_new_psd_group_remains_unresolved_for_existing_object_matching() -> None:
    inventory, manifest = _inputs()
    leaves = tuple(SelectionNode(id=f"leaf-{i}", name=f"Leaf {i}", type="IMAGE",
                                bounds=Bounds(x=i, y=0, width=10, height=10)) for i in range(20))
    group = SelectionNode(id="group", name="Section", type="GROUP",
                          bounds=Bounds(x=0, y=0, width=30, height=10), children=leaves)
    root = manifest.top_level_nodes[0].model_copy(update={"id": "psd-root:test", "children": (group,)})
    draft = build_mapping(inventory.model_copy(update={"objects": ()}),
                          manifest.model_copy(update={"top_level_nodes": (root,)}))
    added = [item for item in draft.items if item.status == "hifi_added"]
    assert {item.figma_node_id for item in added} == {leaf.id for leaf in leaves}
    assert draft.unresolved_count == len(leaves)
    assert all(item.action is None for item in added)


def test_waived_psd_leaf_does_not_count_as_existing_object_coverage() -> None:
    from figma_to_fgui.hifi_mapping import require_psd_coverage
    inventory, manifest = _inputs()
    manifest = manifest.model_copy(update={"top_level_nodes": (
        manifest.top_level_nodes[0].model_copy(update={"id": "psd-root:test"}),)})
    draft = build_mapping(inventory, manifest)
    waived = draft.model_copy(update={"items": tuple(
        item.model_copy(update={"action": "exception"})
        if item.status == "hifi_added" else item
        for item in draft.items), "unresolved_count": 0})
    with pytest.raises(HifiMappingError, match="hifi_mapping_coverage_incomplete"):
        require_psd_coverage(waived, manifest)


def test_psd_mapping_geometry_uses_same_unscaled_canvas_as_patch() -> None:
    from figma_to_fgui.hifi_mapping import _figma_bounds, _selection_box

    inventory, manifest = _inputs()
    root = manifest.top_level_nodes[0].model_copy(update={
        "id": "psd-root:test", "bounds": Bounds(x=0, y=0, width=1080, height=2340),
    })
    manifest = manifest.model_copy(update={"top_level_nodes": (root,)})
    node = SelectionNode(id="bottom", name="bottom", type="IMAGE",
                         bounds=Bounds(x=-6, y=1976, width=388, height=114))
    assert _selection_box(manifest, node, inventory) == (-6, 1976, 388, 114)
    assert _figma_bounds(manifest, node, inventory) == pytest.approx(
        (0, 1976 / 2340, 388 / 1080, 114 / 2340)
    )


def test_psd_mapping_score_uses_declared_viewport_without_moving_source_highlight() -> None:
    from figma_to_fgui.hifi_mapping import _figma_bounds, _selection_box

    inventory, manifest = _inputs()
    root = manifest.top_level_nodes[0].model_copy(update={
        "id": "psd-root:test",
        "bounds": Bounds(x=0, y=0, width=1080, height=2340),
        "properties": {"psdViewportBounds": (0, 210, 1080, 1920)},
    })
    manifest = manifest.model_copy(update={"top_level_nodes": (root,)})
    node = SelectionNode(id="button", name="button", type="IMAGE",
                         bounds=Bounds(x=20, y=1976, width=388, height=114))
    assert _selection_box(manifest, node, inventory) == (20, 1766, 388, 114)
    assert _figma_bounds(manifest, node, inventory)[1] == pytest.approx(1976 / 2340)


def test_psd_viewport_translation_is_not_tied_to_homepage_dimensions() -> None:
    from figma_to_fgui.hifi_mapping import _selection_box

    inventory, manifest = _inputs()
    root = manifest.top_level_nodes[0].model_copy(update={
        "id": "psd-root:other-project",
        "bounds": Bounds(x=0, y=0, width=900, height=700),
        "properties": {"psdViewportBounds": (75, 140, 750, 420)},
    })
    manifest = manifest.model_copy(update={"top_level_nodes": (root,)})
    node = SelectionNode(id="other-button", name="other-button", type="IMAGE",
                         bounds=Bounds(x=95, y=175, width=120, height=44))
    assert _selection_box(manifest, node, inventory) == (20, 35, 120, 44)


def test_psd_old_only_visuals_are_preserved() -> None:
    inventory, manifest = _inputs()
    root = manifest.top_level_nodes[0].model_copy(update={"id": "psd-root:test"})
    draft = build_mapping(inventory, manifest.model_copy(update={"top_level_nodes": (root,)}))
    reset = next(item for item in draft.items if item.old_object_id == "btn_reset")
    assert reset.action == "keep_old"


def test_psd_only_visuals_remain_matcher_failures() -> None:
    inventory, manifest = _inputs()
    root = manifest.top_level_nodes[0].model_copy(update={"id": "psd-root:test"})

    draft = build_mapping(inventory, manifest.model_copy(update={"top_level_nodes": (root,)}))

    added = next(item for item in draft.items if item.figma_node_id == "progress-bubble")
    assert added.status == "hifi_added"
    assert added.action is None



def test_mapping_classifies_matched_added_missing_and_uncertain() -> None:
    inventory, manifest = _inputs()
    draft = build_mapping(inventory, manifest)
    by_old = {item.old_object_id: item for item in draft.items if item.old_object_id}
    by_figma = {item.figma_node_id: item for item in draft.items if item.figma_node_id}
    assert by_old["silhouette_01"].status == "matched"
    assert by_old["silhouette_01"].old_object_type is not None
    assert by_figma["progress-bubble"].status == "hifi_added"
    assert by_figma["progress-bubble"].action is None
    assert by_old["btn_reset"].status == "fgui_only"
    assert by_old["old_badge"].status == "uncertain"
    assert draft.model_dump_json() == build_mapping(inventory, manifest).model_dump_json()


def test_decision_advances_revision_and_rejects_stale_or_duplicate_target() -> None:
    inventory, manifest = _inputs()
    draft = build_mapping(inventory, manifest)
    uncertain = next(item for item in draft.items if item.status == "uncertain")
    candidate = uncertain.candidates[0]
    updated = apply_mapping_decision(
        draft,
        HifiMappingDecision(
            version=1,
            mapping_revision=draft.mapping_revision,
            item_id=uncertain.item_id,
            action="retarget",
            figma_node_id=candidate,
        ),
        manifest,
    )
    assert updated.mapping_revision == draft.mapping_revision + 1
    with pytest.raises(HifiMappingError, match="stale_mapping"):
        apply_mapping_decision(
            updated,
            HifiMappingDecision(
                version=1,
                mapping_revision=draft.mapping_revision,
                item_id=uncertain.item_id,
                action="keep_old",
            ),
            manifest,
        )


def test_mapping_normalizes_absolute_figma_canvas_coordinates() -> None:
    inventory, manifest = _inputs()

    def move(node):
        return node.model_copy(
            update={
                "bounds": Bounds(
                    x=node.bounds.x + 1000,
                    y=node.bounds.y + 500,
                    width=node.bounds.width,
                    height=node.bounds.height,
                ),
                "children": tuple(move(child) for child in node.children),
            }
        )

    shifted = manifest.model_copy(update={"top_level_nodes": tuple(move(node) for node in manifest.top_level_nodes)})
    original = build_mapping(inventory, manifest)
    moved = build_mapping(inventory, shifted)
    original_silhouette = next(item for item in original.items if item.old_object_id == "silhouette_01")
    moved_silhouette = next(item for item in moved.items if item.old_object_id == "silhouette_01")
    assert moved_silhouette.status == original_silhouette.status
    assert moved_silhouette.figma_bounds == original_silhouette.figma_bounds


def test_mapping_blocks_unsupported_added_container_until_user_marks_exception() -> None:
    inventory, manifest = _inputs()
    selection_root = manifest.top_level_nodes[0]
    unsupported = SelectionNode(
        id="hifi-dialog",
        name="InteractiveDialog",
        type="FRAME",
        bounds=Bounds(x=100, y=100, width=200, height=120),
        children=(
            SelectionNode(
                id="hifi-dialog-button",
                name="DialogButton",
                type="INSTANCE",
                bounds=Bounds(x=120, y=180, width=80, height=30),
            ),
        ),
    )
    manifest = manifest.model_copy(
        update={
            "top_level_nodes": (
                selection_root.model_copy(
                    update={"children": (*selection_root.children, unsupported)}
                ),
            )
        }
    )

    mapping = build_mapping(inventory, manifest)

    blocked = next(item for item in mapping.items if item.figma_node_id == "hifi-dialog")
    assert blocked.status == "blocked"
    assert blocked.action is None
    assert mapping.unresolved_count >= 1
    with pytest.raises(HifiMappingError, match="mapping_action_not_allowed"):
        apply_mapping_decision(
            mapping,
            HifiMappingDecision(
                version=1,
                mapping_revision=mapping.mapping_revision,
                item_id=blocked.item_id,
                action="add_visual",
            ),
            manifest,
        )


def test_mapping_requires_psd_image_leaf_to_retarget_an_existing_object() -> None:
    inventory, manifest = _inputs()
    selection_root = manifest.top_level_nodes[0]
    psd_image = SelectionNode(
        id="psd-layer:image-leaf",
        name="Rendered smart object",
        type="IMAGE",
        bounds=Bounds(x=40, y=60, width=320, height=180),
        properties={"psdKind": "smartobject"},
    )
    manifest = manifest.model_copy(
        update={
            "top_level_nodes": (
                selection_root.model_copy(
                    update={
                        "id": "psd-root:test",
                        "children": (*selection_root.children, psd_image),
                    }
                ),
            )
        }
    )

    mapping = build_mapping(inventory, manifest)

    added = next(item for item in mapping.items if item.figma_node_id == psd_image.id)
    assert added.status == "hifi_added"
    assert added.action is None


def test_mapping_does_not_add_zero_area_psd_image() -> None:
    inventory, manifest = _inputs()
    selection_root = manifest.top_level_nodes[0]
    empty_image = SelectionNode(
        id="psd-layer:empty-image",
        name="Empty pixel layer",
        type="IMAGE",
        bounds=Bounds(x=0, y=0, width=0, height=0),
        properties={"psdKind": "pixel"},
    )
    manifest = manifest.model_copy(
        update={
            "top_level_nodes": (
                selection_root.model_copy(
                    update={"children": (*selection_root.children, empty_image)}
                ),
            )
        }
    )

    mapping = build_mapping(inventory, manifest)

    empty = next(item for item in mapping.items if item.figma_node_id == empty_image.id)
    assert empty.status == "blocked"


def test_component_instance_prefers_same_bounds_psd_group_as_its_visual_section() -> None:
    inventory, _ = _inputs()
    button = next(item for item in inventory.objects if item.object_id == "btn_next")
    focused_inventory = inventory.model_copy(update={"objects": (button,)})
    manifest = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(
            SelectionNode(
                id="psd-root:" + "d" * 64,
                name="PSD",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=inventory.width, height=inventory.height),
                children=(
                    SelectionNode(
                        id="psd-button-group",
                        name="Primary action",
                        type="GROUP",
                        bounds=Bounds(
                            x=button.x,
                            y=button.y,
                            width=button.width,
                            height=button.height,
                        ),
                    ),
                ),
            ),
        ),
    )

    mapping = build_mapping(focused_inventory, manifest)

    item = next(item for item in mapping.items if item.old_object_id == button.object_id)
    assert item.figma_node_id == "psd-button-group"
    assert item.status == "suggested"


def test_tentative_matches_are_unique_and_not_listed_again_as_new() -> None:
    inventory, _ = _inputs()
    button = next(item for item in inventory.objects if item.object_id == "btn_next")
    duplicate = button.model_copy(update={"object_id": "other_button", "name": "other_button"})
    focused_inventory = inventory.model_copy(update={"objects": (button, duplicate)})
    node = SelectionNode(id="psd-button", name="Primary action", type="GROUP", bounds=Bounds(x=button.x, y=button.y, width=button.width, height=button.height))
    manifest = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(SelectionNode(id="root", name="PSD", type="FRAME", bounds=Bounds(x=0, y=0, width=inventory.width, height=inventory.height), children=(node,)),))

    mapping = build_mapping(focused_inventory, manifest)

    assert sum(item.figma_node_id == node.id for item in mapping.items) == 1
    assert not any(item.status == "hifi_added" for item in mapping.items)


def test_unmatched_group_is_one_review_unit_instead_of_each_descendant() -> None:
    inventory, _ = _inputs()
    group = SelectionNode(id="new-group", name="New section", type="GROUP", bounds=Bounds(x=0, y=0, width=100, height=100), children=(SelectionNode(id="new-leaf", name="Artwork", type="IMAGE", bounds=Bounds(x=0, y=0, width=100, height=100)),))
    manifest = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(SelectionNode(id="root", name="PSD", type="FRAME", bounds=Bounds(x=0, y=0, width=inventory.width, height=inventory.height), children=(group,)),))
    empty_inventory = inventory.model_copy(update={"objects": ()})

    mapping = build_mapping(empty_inventory, manifest)

    assert [item.figma_node_id for item in mapping.items] == ["new-group"]


def test_retargeting_an_old_object_resolves_the_same_psd_branch_as_new() -> None:
    inventory, manifest = _inputs()
    mapping = build_mapping(inventory, manifest)
    old = next(item for item in mapping.items if item.old_object_id == "btn_reset")
    new = next(item for item in mapping.items if item.figma_node_id == "progress-bubble")

    updated = apply_mapping_decision(mapping, HifiMappingDecision(version=1, mapping_revision=mapping.mapping_revision, item_id=old.item_id, action="retarget", figma_node_id=new.figma_node_id), manifest)

    assert next(item for item in updated.items if item.item_id == old.item_id).action == "retarget"
    assert next(item for item in updated.items if item.item_id == new.item_id).action == "exception"
    assert updated.unresolved_count == mapping.unresolved_count - 1
