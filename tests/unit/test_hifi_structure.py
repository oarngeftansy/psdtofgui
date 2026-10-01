import pytest
from test_hifi_nested import nested_case


def test_nonrendering_groups_resolve_but_children_remain_independent(tmp_path):
    from figma_to_fgui.hifi_mapping import HifiMappingError, build_mapping, require_psd_coverage
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root, inventory, source, _ = nested_case(tmp_path)
    frame = source.top_level_nodes[0]
    source = source.model_copy(
        update={
            "top_level_nodes": (
                frame.model_copy(
                    update={
                        "children": tuple(
                            n.model_copy(update={"name": "Unrelated source shape"})
                            for n in frame.children
                        )
                    }
                ),
            )
        }
    )
    path = root / inventory.target.component_relative_path
    text = path.read_text().replace(
        "</displayList>",
        '<group id="layout" name="Layout" xy="0,0" size="1,1" advanced="true" layout="hz"/></displayList>',
    )
    path.write_text(text)
    inventory = inspect_component_tree(root, inventory.target)
    draft = build_mapping(inventory, source)
    group = next(i for i in draft.items if i.old_object_id == "layout")
    assert group.action == "preserve_structure"
    assert group.figma_node_id is None and not group.candidates
    assert all(i.action == "keep_old" for i in draft.items if i.status == "fgui_only")
    assert all(i.action is None for i in draft.items if i.status == "hifi_added")
    # Ambiguous old/new pairings remain review decisions instead of being
    # silently waived as exceptions.
    with pytest.raises(HifiMappingError, match="hifi_mapping_coverage_incomplete"):
        require_psd_coverage(draft, source)


def test_group_with_unknown_custom_attributes_is_not_auto_closed(tmp_path):
    from figma_to_fgui.hifi_mapping import build_mapping
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root, inventory, source, _ = nested_case(tmp_path)
    path = root / inventory.target.component_relative_path
    path.write_text(
        path.read_text().replace(
            "</displayList>",
            '<group id="custom" name="Custom" xy="0,0" size="1,1" customRuntime="x"/></displayList>',
        )
    )
    draft = build_mapping(inspect_component_tree(root, inventory.target), source)
    assert next(i for i in draft.items if i.old_object_id == "custom").action == "keep_old"


def test_graph_without_shape_or_paint_is_structural_but_painted_graph_is_not(tmp_path):
    from figma_to_fgui.hifi_mapping import build_mapping
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root, inventory, source, _ = nested_case(tmp_path)
    path = root / inventory.target.component_relative_path
    path.write_text(path.read_text().replace(
        "</displayList>",
        '<graph id="sizer" name="sizer" xy="0,0" size="120,44" touchable="false"/>'
        '<graph id="painted" name="painted" xy="0,0" size="120,44" '
        'type="rect" fillColor="#ff000000"/></displayList>',
    ))
    inspected = inspect_component_tree(root, inventory.target)
    draft = build_mapping(inspected, source)
    assert next(i for i in draft.items if i.old_object_id == "sizer").action == "preserve_structure"
    assert next(i for i in draft.items if i.old_object_id == "painted").action == "keep_old"


def test_editor_standard_attributes_are_preserved_without_false_parse_gap(tmp_path):
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root, inventory, _source, _mapping = nested_case(tmp_path)
    path = root / inventory.target.component_relative_path
    path.write_text(
        path.read_text().replace(
            "</displayList>",
            '<image id="standard" name="standard" xy="0,0" size="10,10" '
            'pkg="library" aspect="true" autoSize="true" '
            'strokeColor="#ffffff" strokeSize="1"/></displayList>',
        )
    )
    inspected = inspect_component_tree(root, inventory.target)
    standard = next(obj for obj in inspected.objects if obj.object_id == "standard")
    assert standard.unknown_attributes == ()
    assert inspected.parse_complete
    path.write_text(path.read_text().replace('strokeSize="1"', 'strokeSize="1" customRuntime="x"'))
    assert not inspect_component_tree(root, inventory.target).parse_complete


def test_automatic_structure_cannot_be_used_to_waive_an_image(tmp_path):
    from figma_to_fgui.hifi_patch import HifiPatchError, build_hifi_change_bundle

    root, inventory, source, mapping = nested_case(tmp_path)
    mapping = mapping.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"action": "preserve_structure"})
                if i.old_object_id == "a:bg"
                else i
                for i in mapping.items
            )
        }
    )
    with pytest.raises(HifiPatchError, match="structural_resolution_invalid"):
        build_hifi_change_bundle(root, inventory, source, mapping)


def test_empty_psd_group_with_mask_is_auto_structural(tmp_path):
    from figma_to_fgui.figma_selection import SelectionNode
    from figma_to_fgui.hifi_mapping import build_mapping, require_psd_coverage
    from figma_to_fgui.models import Bounds

    _root, inventory, source, _ = nested_case(tmp_path)
    empty = SelectionNode(
        id="empty",
        name="Empty",
        type="GROUP",
        bounds=Bounds(x=0, y=0, width=0, height=0),
        properties={
            "psdKind": "group",
            "hasEffects": False,
            "hasPixelMask": True,
            "hasVectorMask": False,
            "clipping": False,
            "blendMode": "pass_through",
        },
    )
    frame = source.top_level_nodes[0].model_copy(update={"children": (empty,)})
    source = source.model_copy(update={"top_level_nodes": (frame,)})
    draft = build_mapping(inventory, source)
    item = next(i for i in draft.items if i.figma_node_id == "empty")
    assert item.status == "structural"
    assert item.action == "preserve_structure"
    require_psd_coverage(draft, source)


def test_preserved_group_geometry_cannot_change_in_candidate(tmp_path):
    import shutil

    from figma_to_fgui.hifi_mapping import build_mapping
    from figma_to_fgui.hifi_nested import inspect_component_tree
    from figma_to_fgui.hifi_patch import HifiPatchError, validate_hifi_candidate

    root, inventory, source, _ = nested_case(tmp_path)
    path = root / inventory.target.component_relative_path
    path.write_text(
        path.read_text().replace(
            "</displayList>", '<group id="layout" name="Layout" xy="0,0" size="1,1"/></displayList>'
        )
    )
    inventory = inspect_component_tree(root, inventory.target)
    draft = build_mapping(inventory, source)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    changed = candidate / inventory.target.component_relative_path
    changed.write_text(changed.read_text().replace('size="1,1"', 'size="2,2"'))
    with pytest.raises(HifiPatchError, match="structural_resolution_invalid"):
        validate_hifi_candidate(root, candidate, inventory, draft, session_id="0" * 32)


def test_psd_old_graph_retires_when_existing_mapped_sibling_replaces_same_pixels(tmp_path):
    from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
    from figma_to_fgui.hifi_mapping import build_mapping
    from figma_to_fgui.hifi_nested import inspect_component_tree
    from figma_to_fgui.models import Bounds

    root, old_inventory, _source, _mapping = nested_case(tmp_path)
    child = root / "assets/MyVillage/Common/Button_Common.xml"
    child.write_text(
        '<component size="120,44" extention="Button"><displayList>'
        '<graph id="bg" name="LegacyBacking" xy="0,0" size="120,44" '
        'type="rect" fillColor="#ffffcc00"/>'
        '<loader id="skin" name="Skin" xy="0,0" size="120,44" '
        'url="ui://myvillage01skin0001" fill="scaleFree"/>'
        '<text id="title" name="title" xy="10,10" size="90,24" text="Default"/>'
        '</displayList><Button/></component>',
        encoding="utf-8",
    )
    inventory = inspect_component_tree(root, old_inventory.target)
    skin = next(obj for obj in inventory.objects if obj.object_id == "a:skin")
    source = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:retirement",
            name="PSD",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=750, height=420),
            children=(SelectionNode(
                id="skin-leaf",
                name="Skin",
                type="IMAGE",
                bounds=Bounds(x=skin.x, y=skin.y, width=skin.width, height=skin.height),
            ),),
        ),),
    )

    draft = build_mapping(inventory, source)
    graph = next(item for item in draft.items if item.old_object_id == "a:bg")
    mapped_skin = next(item for item in draft.items if item.old_object_id == "a:skin")

    assert mapped_skin.action == "accept"
    assert graph.action == "keep_old"
    assert graph.visual_disposition == "retire"
