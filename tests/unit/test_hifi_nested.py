import shutil
from pathlib import Path

import pytest
from PIL import Image

from figma_to_fgui.hifi_project_inspector import inspect_hifi_targets, target_from_option
from figma_to_fgui.uploaded_project import index_uploaded_project

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement/old_project"


def test_largest_opaque_rectangle_excludes_transparent_skin_border() -> None:
    from figma_to_fgui.hifi_nested import _largest_opaque_rectangle

    image = Image.new("RGBA", (9, 7), (0, 0, 0, 0))
    for y in range(1, 6):
        for x in range(2, 7):
            image.putpixel((x, y), (240, 220, 180, 255))

    assert _largest_opaque_rectangle(image) == (2, 1, 5, 5)


@pytest.mark.parametrize("fill", ["scale", "scaleFree"])
def test_psd_retired_graph_is_not_geometry_fitted_under_new_skin(
    tmp_path, fill: str
) -> None:
    from lxml import etree

    from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
    from figma_to_fgui.hifi_mapping import build_mapping
    from figma_to_fgui.hifi_nested import (
        _fit_retained_graphs_under_owned_siblings,
        inspect_component_tree,
    )
    from figma_to_fgui.models import Bounds

    root, old_inventory, _, _ = nested_case(tmp_path)
    child_path = "assets/MyVillage/Common/Button_Common.xml"
    child = root / child_path
    child.write_text(
        '<component size="120,44" extention="Button"><displayList>'
        '<graph id="bg" name="Background" xy="0,0" size="120,44" '
        'type="rect" fillColor="#ffffcc00"/>'
        '<loader id="skin" name="icon" xy="0,0" size="100,40" '
        f'url="ui://myvillage01skin0001" align="center" vAlign="middle" fill="{fill}"/>'
        '<text id="title" name="title" xy="10,10" size="90,24" text="Default"/>'
        '</displayList><Button downEffect="scale" downEffectValue=".99"/></component>',
        encoding="utf-8",
    )
    package = root / "assets/MyVillage/package.xml"
    package.write_text(
        package.read_text(encoding="utf-8").replace(
            "</resources>",
            '<image id="skin0001" name="Skin.png" path="/Img/HIFI/" atlas="0"/>'
            "</resources>",
        ),
        encoding="utf-8",
    )
    image_path = root / "assets/MyVillage/Img/HIFI/Skin.png"
    image_path.parent.mkdir(parents=True)
    image = Image.new("RGBA", (120, 44), (0, 0, 0, 0))
    for y in range(8, 36):
        for x in range(12, 108):
            image.putpixel((x, y), (255, 247, 237, 255))
    image.save(image_path)

    inventory = inspect_component_tree(root, old_inventory.target)
    # A relation on the component instance marks every nested child as
    # runtime-position-bound in the flattened inventory.  That must not block
    # local geometry changes inside the component definition: the instance
    # still moves as one unit and keeps the same child object identity.
    inventory = inventory.model_copy(update={
        "objects": tuple(
            item.model_copy(update={"position_runtime_bound": True})
            if item.object_id == "a:bg"
            else item
            for item in inventory.objects
        ),
    })
    source = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:skin",
            name="PSD",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=750, height=420),
            children=(SelectionNode(
                id="skin-leaf",
                name="icon",
                type="IMAGE",
                bounds=Bounds(x=10, y=20, width=120, height=44),
            ),),
        ),),
    )
    draft = build_mapping(inventory, source)
    mapping = draft.model_copy(update={
        "items": tuple(
            item.model_copy(update={
                "action": "accept",
                "figma_node_id": "skin-leaf",
                "owned_source_ids": ("skin-leaf",),
            })
            if item.old_object_id == "a:skin"
            else item.model_copy(update={
                "action": "keep_old" if item.old_object_id else "exception",
            })
            for item in draft.items
        ),
        "unresolved_count": 0,
    })

    changed = _fit_retained_graphs_under_owned_siblings(root, inventory, mapping)

    # PSD reskins no longer mutate old geometry to tuck a legacy paint layer
    # underneath the new skin. The mapping marks that graph for visual
    # retirement and the normal patch pass turns off its default pixels.
    graph_mapping = next(i for i in mapping.items if i.old_object_id == "a:bg")
    assert graph_mapping.visual_disposition == "retire"
    assert changed == set()
    document = etree.parse(str(child))
    assert document.xpath("./displayList/*/@id") == ["bg", "skin", "title"]
    graph = document.xpath("./displayList/graph[@id='bg']")[0]
    assert graph.get("xy") == "0,0"
    assert graph.get("size") == "120,44"
    assert graph.get("alpha") is None
    assert graph.get("visible") is None
    assert graph.get("fillColor") == "#ffffcc00"
    assert document.xpath("string(./Button/@downEffect)") == "scale"


def test_changed_auto_layout_group_uses_mapped_member_gap(tmp_path) -> None:
    from lxml import etree

    from figma_to_fgui.hifi_nested import _sync_changed_group_bounds, inspect_component_tree

    root, old_inventory, _source, mapping = nested_case(tmp_path)
    child_path = "assets/MyVillage/Common/Button_Common.xml"
    child = root / child_path
    child.write_text(
        '<component size="120,44"><displayList>'
        '<graph id="bg" name="Background" xy="0,0" size="40,44" group="row" '
        'type="rect" fillColor="#ff112233"/>'
        '<text id="title" name="title" xy="39,10" size="81,24" group="row" text="Default"/>'
        '<group id="row" name="row" xy="0,0" size="120,44" advanced="true" '
        'layout="hz" colGap="5" excludeInvisibles="true"/>'
        '</displayList></component>',
        encoding="utf-8",
    )
    inventory = inspect_component_tree(root, old_inventory.target)
    mapping = mapping.model_copy(update={
        "items": tuple(
            item.model_copy(update={"action": "accept"})
            if item.old_object_id in {"a:bg", "a:title"}
            else item
            for item in mapping.items
        ),
    })

    _sync_changed_group_bounds(root, inventory, mapping)

    document = etree.parse(str(child))
    group = document.xpath("./displayList/group[@id='row']")[0]
    assert group.get("colGap") == "-1"
    assert group.get("xy") == "0,0"
    assert group.get("size") == "120,44"


def inputs(root=FIXTURE):
    project = index_uploaded_project(root, "old.zip")
    tree = inspect_hifi_targets(root, project)
    package = tree.packages[0]
    directory = next(d for d in package.directories if d.path == "Panel")
    component = next(c for c in directory.components if c.resource_id == "sketch01")
    return target_from_option(project, package, directory, component)


def test_nested_instances_have_distinct_identity_and_local_ownership():
    from figma_to_fgui.hifi_nested import inspect_component_tree

    inventory = inspect_component_tree(FIXTURE, inputs())
    labels = [o for o in inventory.objects if o.local_object_id == "shared_label"]
    assert len(labels) >= 2
    assert len({o.object_id for o in inventory.objects}) == len(inventory.objects)
    assert len({o.instance_path for o in labels}) == len(labels)
    for label in labels:
        parent = next(o for o in inventory.objects if o.object_id == label.instance_path[-1])
        assert label.x == parent.x + 12
        assert label.y == parent.y + 10
        assert label.component_relative_path.endswith("Common/Button_Common.xml")
    assert inventory.expanded_instances


def test_nested_unknown_fields_make_parse_coverage_incomplete(tmp_path):
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    child = root / "assets/MyVillage/Common/Button_Common.xml"
    child.write_text(
        child.read_text().replace('text="Common"', 'text="Common" unknownRuntimeBinding="x"')
    )
    inventory = inspect_component_tree(root, inputs(root))
    assert not inventory.parse_complete
    assert any(
        o.local_object_id == "shared_label" and o.unknown_attributes for o in inventory.objects
    )


def test_nested_cycle_is_reported_without_losing_sibling_objects(tmp_path):
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    child = root / "assets/MyVillage/Common/Button_Common.xml"
    child.write_text(
        child.read_text().replace(
            "</displayList>",
            '<component id="cycle" name="cycle" src="sharedbtn1" xy="0,0"/></displayList>',
        )
    )
    inventory = inspect_component_tree(root, inputs(root))
    assert not inventory.parse_complete
    assert any("cycle" in issue for issue in inventory.scope_issues)
    assert len(inventory.objects) < 100


def nested_case(tmp_path):
    from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
    from figma_to_fgui.hifi_mapping import build_mapping
    from figma_to_fgui.hifi_nested import inspect_component_tree
    from figma_to_fgui.models import Bounds

    root = tmp_path / "project"
    shutil.copytree(FIXTURE, root)
    target = inputs(root)
    (root / target.component_relative_path).write_text(
        '<component size="750,420"><displayList>'
        '<component id="a" name="Delegate" src="sharedbtn1" xy="10,20"/>'
        '<component id="b" name="Reward" src="sharedbtn1" xy="200,100"/>'
        "</displayList></component>"
    )
    child = root / "assets/MyVillage/Common/Button_Common.xml"
    child.write_text(
        '<component size="120,44" extention="Button">'
        '<controller name="enabled" pages="0,on,1,off" selected="0"/>'
        '<displayList><graph id="bg" name="Background" xy="0,0" size="120,44" '
        'type="rect" fillColor="#ff112233"><gearDisplay controller="enabled" pages="0"/></graph>'
        '<text id="title" name="title" xy="10,10" size="90,24" text="Default"/></displayList>'
        '<Button downEffect="scale" downEffectValue=".99"/></component>'
    )
    inventory = inspect_component_tree(root, target)
    nodes = tuple(
        SelectionNode(
            id=f"shape-{x}",
            name="Background",
            type="RECTANGLE",
            bounds=Bounds(x=x, y=y, width=120, height=44),
            properties={"fguiGraph": {"shape": "rect", "fillColor": "#ff445566", "lineSize": 0}},
        )
        for x, y in ((10, 20), (200, 100))
    )
    source = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(
            SelectionNode(
                id="psd-root:test",
                name="PSD",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=750, height=420),
                children=nodes,
            ),
        ),
    )
    draft = build_mapping(inventory, source)
    chosen = {"a:bg": nodes[0].id, "b:bg": nodes[1].id}
    items = tuple(
        item.model_copy(update={"action": "retarget", "figma_node_id": chosen[item.old_object_id]})
        if item.old_object_id in chosen
        else item.model_copy(update={"action": "keep_old" if item.old_object_id else "exception"})
        for item in draft.items
    )
    return root, inventory, source, draft.model_copy(update={"items": items, "unresolved_count": 0})


def test_default_visibility_propagates_through_hidden_controller_instance(tmp_path):
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root, inventory, _, _ = nested_case(tmp_path)
    source = root / inventory.target.component_relative_path
    source.write_text(source.read_text().replace(
        '<component size="750,420"><displayList>',
        '<component size="750,420"><controller name="hasTab" pages="0,false,1,true" '
        'selected="0"/><displayList>').replace(
        '<component id="a" name="Delegate" src="sharedbtn1" xy="10,20"/>',
        '<component id="a" name="Delegate" src="sharedbtn1" xy="10,20">'
        '<gearDisplay controller="hasTab" pages="1"/></component>'))
    expanded = inspect_component_tree(root, inventory.target)
    assert not next(o for o in expanded.objects if o.object_id == "a").default_visible
    assert not next(o for o in expanded.objects if o.object_id == "a:bg").default_visible
    assert next(o for o in expanded.objects if o.object_id == "b:bg").default_visible


def test_nested_runtime_visibility_uses_first_page_unless_instance_overrides_controller(tmp_path):
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root, inventory, _, _ = nested_case(tmp_path)
    child = root / "assets/MyVillage/Common/Button_Common.xml"
    child.write_text(
        child.read_text().replace(
            'controller name="enabled" pages="0,on,1,off" selected="0"',
            'controller name="enabled" exported="true" pages="0,on,1,off" selected="1"',
        ).replace(
            '<gearDisplay controller="enabled" pages="0"/>',
            '<gearDisplay controller="enabled" pages="1"/>',
        )
    )

    expanded = inspect_component_tree(root, inventory.target)
    assert not next(o for o in expanded.objects if o.object_id == "a:bg").default_visible
    assert not next(o for o in expanded.objects if o.object_id == "b:bg").default_visible

    panel = root / inventory.target.component_relative_path
    panel.write_text(
        panel.read_text().replace(
            '<component id="a" name="Delegate" src="sharedbtn1" xy="10,20"/>',
            '<component id="a" name="Delegate" src="sharedbtn1" xy="10,20" '
            'controller="enabled,1"/>',
        )
    )
    overridden = inspect_component_tree(root, inventory.target)
    assert next(o for o in overridden.objects if o.object_id == "a:bg").default_visible
    assert not next(o for o in overridden.objects if o.object_id == "b:bg").default_visible


def test_nested_icon_gear_updates_runtime_first_page_not_editor_selected_page():
    from lxml import etree

    from figma_to_fgui.hifi_nested import _update_runtime_gear_icon

    component = etree.fromstring(
        b'<component><controller name="type" pages="0,runtime,1,editor" selected="1"/>'
        b'<displayList><loader id="icon" url="ui://pkgnew">'
        b'<gearIcon controller="type" pages="0,1" values="ui://pkgold0|ui://pkgold1"/>'
        b'</loader></displayList></component>'
    )
    gear = component.xpath("./displayList/loader/gearIcon")[0]

    assert _update_runtime_gear_icon(component, gear, "ui://pkgnew", {})

    assert gear.get("values") == "ui://pkgnew|ui://pkgold1"
    assert component.xpath("string(./controller/@selected)") == "1"


def test_nested_icon_gear_respects_parent_runtime_controller_override():
    from lxml import etree

    from figma_to_fgui.hifi_nested import _update_runtime_gear_icon

    component = etree.fromstring(
        b'<component><controller name="type" pages="0,runtime,1,alternate" selected="0"/>'
        b'<displayList><loader id="icon" url="ui://pkgnew">'
        b'<gearIcon controller="type" pages="0,1" values="ui://pkgold0|ui://pkgold1"/>'
        b'</loader></displayList></component>'
    )
    gear = component.xpath("./displayList/loader/gearIcon")[0]

    assert _update_runtime_gear_icon(component, gear, "ui://pkgnew", {"type": "1"})

    assert gear.get("values") == "ui://pkgold0|ui://pkgnew"


def test_nested_graph_updates_definition_once_and_preserves_instances_and_states(tmp_path):
    from lxml import etree

    from figma_to_fgui.apply import apply_bundle
    from figma_to_fgui.hifi_patch import build_hifi_change_bundle, validate_hifi_candidate

    root, inventory, source, mapping = nested_case(tmp_path)
    target_bytes = (root / inventory.target.component_relative_path).read_bytes()
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    bundle = build_hifi_change_bundle(root, inventory, source, mapping)
    assert not any(".figma-to-fgui" in f.relative_path for f in bundle.files)
    apply_bundle(candidate, bundle)
    assert (candidate / inventory.target.component_relative_path).read_bytes() == target_bytes
    child = etree.parse(str(candidate / "assets/MyVillage/Common/Button_Common.xml"))
    assert child.xpath("string(./displayList/graph/@fillColor)") == "#ff445566"
    assert child.xpath("./displayList/*/@id") == ["bg", "title"]
    assert child.xpath("string(./displayList/graph/gearDisplay/@controller)") == "enabled"
    assert child.xpath("string(./Button/@downEffect)") == "scale"
    review = validate_hifi_candidate(root, candidate, inventory, mapping, session_id="a" * 32)
    assert review.protected_checks_passed
    assert {d.old_object_id for d in review.object_diffs if d.kind == "changed"} == {"a:bg", "b:bg"}
    # Rendered state evidence moved to warnings; the Editor stage discharges it.
    assert review.approvable
    assert review.warnings


def test_expanded_component_wrappers_can_be_preserved_as_structure(tmp_path):
    from figma_to_fgui.apply import apply_bundle
    from figma_to_fgui.hifi_patch import build_hifi_change_bundle, validate_hifi_candidate

    root, inventory, source, mapping = nested_case(tmp_path)
    assert all(o.structural_only for o in inventory.objects if o.object_type == "component")
    mapping = mapping.model_copy(update={"items": tuple(
        item.model_copy(update={"action": "preserve_structure"})
        if item.old_object_id in {"a", "b"} else item for item in mapping.items
    )})
    bundle = build_hifi_change_bundle(root, inventory, source, mapping)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    apply_bundle(candidate, bundle)
    review = validate_hifi_candidate(root, candidate, inventory, mapping, session_id="a" * 32)
    assert review.protected_checks_passed


def test_nested_patch_translates_psd_viewport_to_local_component_coordinates(tmp_path):
    from figma_to_fgui.hifi_nested import _local_plans
    from figma_to_fgui.models import Bounds

    root, inventory, source, mapping = nested_case(tmp_path)
    frame = source.top_level_nodes[0]
    shifted = tuple(
        node.model_copy(update={"bounds": Bounds(
            x=node.bounds.x, y=node.bounds.y + 210,
            width=node.bounds.width, height=node.bounds.height,
        )})
        for node in frame.children
    )
    source = source.model_copy(update={"top_level_nodes": (frame.model_copy(update={
        "bounds": Bounds(x=0, y=0, width=750, height=630),
        "properties": {"psdViewportBounds": (0, 210, 750, 420)},
        "children": shifted,
    }),)})
    plans = _local_plans(root, inventory, source, mapping)
    child_source = next(selection for local, selection, _ in plans
                        if local.target.component_name == "Button_Common")
    assert [node.bounds.y for node in child_source.top_level_nodes[0].children] == [0]


def test_shared_definition_isolates_conflicting_instance_visuals(tmp_path):
    from lxml import etree

    from figma_to_fgui.apply import apply_bundle
    from figma_to_fgui.hifi_patch import build_hifi_change_bundle, validate_hifi_candidate

    root, inventory, source, mapping = nested_case(tmp_path)
    frame = source.top_level_nodes[0]
    nodes = (
        frame.children[0],
        frame.children[1].model_copy(
            update={
                "properties": {
                    "fguiGraph": {"shape": "rect", "fillColor": "#ff998877", "lineSize": 0}
                }
            }
        ),
    )
    source = source.model_copy(
        update={"top_level_nodes": (frame.model_copy(update={"children": nodes}),)}
    )
    bundle = build_hifi_change_bundle(root, inventory, source, mapping)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    apply_bundle(candidate, bundle)
    parent = etree.parse(str(candidate / inventory.target.component_relative_path))
    instances = parent.xpath("./displayList/component")
    assert [item.get("id") for item in instances] == ["a", "b"]
    assert instances[0].get("src") != instances[1].get("src")
    original = root / "assets/MyVillage/Common/Button_Common.xml"
    assert (candidate / original.relative_to(root)).read_bytes() == original.read_bytes()
    variants = list((candidate / "assets/MyVillage/Common").glob("Button_Common__hifi_*.xml"))
    assert len(variants) == 2
    assert {etree.parse(str(path)).xpath("string(./displayList/graph/@fillColor)")
            for path in variants} == {"#ff445566", "#ff998877"}
    assert all(etree.parse(str(path)).xpath("./displayList/*/@id") == ["bg", "title"]
               for path in variants)
    review = validate_hifi_candidate(root, candidate, inventory, mapping, session_id="b" * 32)
    assert review.protected_checks_passed
    # Per-state verification moved to warnings; the Editor stage discharges it.
    assert review.approvable
    assert review.warnings
    parent_path = candidate / inventory.target.component_relative_path
    parent = etree.parse(str(parent_path))
    parent.xpath("./displayList/component[@id='a']")[0].set("touchable", "false")
    parent.write(str(parent_path))
    with pytest.raises(ValueError, match="variant_instance_contract_changed"):
        validate_hifi_candidate(root, candidate, inventory, mapping, session_id="b" * 32)


def test_shared_definition_does_not_mutate_unselected_component_users(tmp_path):
    from figma_to_fgui.apply import apply_bundle
    from figma_to_fgui.hifi_patch import build_hifi_change_bundle

    root, inventory, source, mapping = nested_case(tmp_path)
    other = root / "assets/MyVillage/Panel/Other.xml"
    other.write_text(
        '<component size="100,100"><displayList><component id="other" src="sharedbtn1" xy="0,0"/></displayList></component>'
    )
    original_other = other.read_bytes()
    original_definition = (root / "assets/MyVillage/Common/Button_Common.xml").read_bytes()
    bundle = build_hifi_change_bundle(root, inventory, source, mapping)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    apply_bundle(candidate, bundle)
    assert (candidate / other.relative_to(root)).read_bytes() == original_other
    assert (candidate / "assets/MyVillage/Common/Button_Common.xml").read_bytes() == original_definition


def test_runtime_title_keeps_instance_value_but_uses_psd_geometry(tmp_path):
    from lxml import etree

    from figma_to_fgui.apply import apply_bundle
    from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
    from figma_to_fgui.hifi_mapping import build_mapping
    from figma_to_fgui.hifi_nested import inspect_component_tree
    from figma_to_fgui.hifi_patch import build_hifi_change_bundle
    from figma_to_fgui.models import Bounds

    root, inventory, _, _ = nested_case(tmp_path)
    panel_path = root / inventory.target.component_relative_path
    panel_path.write_text(
        panel_path.read_text().replace(
            '<component id="a" name="Delegate" src="sharedbtn1" xy="10,20"/>',
            '<component id="a" name="Delegate" src="sharedbtn1" xy="10,20">'
            '<Button title="Tower"/></component>',
        )
    )
    # Force per-instance isolation, just like the real shared title component.
    (root / "assets/MyVillage/Panel/Other.xml").write_text(
        '<component size="100,100"><displayList>'
        '<component id="other" src="sharedbtn1" xy="0,0"/>'
        '</displayList></component>'
    )
    inventory = inspect_component_tree(root, inventory.target)
    background = SelectionNode(
        id="background",
        name="Background",
        type="RECTANGLE",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        properties={"fguiGraph": {"shape": "rect", "fillColor": "#ff445566", "lineSize": 0}},
    )
    title = SelectionNode(
        id="runtime-title",
        name="Village Level",
        type="TEXT",
        bounds=Bounds(x=35, y=25, width=90, height=30),
        text="Village Level",
    )
    group = SelectionNode(
        id="title-group",
        name="Delegate",
        type="GROUP",
        bounds=Bounds(x=10, y=20, width=120, height=44),
        children=(background, title),
    )
    source = SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:title",
            name="PSD",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=750, height=420),
            children=(group,),
        ),),
    )
    draft = build_mapping(inventory, source)
    chosen = {"a": group.id, "a:bg": background.id, "a:title": title.id}
    mapping = draft.model_copy(update={
        "items": tuple(
            item.model_copy(update={
                "action": "retarget",
                "figma_node_id": chosen[item.old_object_id],
            })
            if item.old_object_id in chosen
            else item.model_copy(update={"action": "keep_old" if item.old_object_id else "exception"})
            for item in draft.items
        ),
        "unresolved_count": 0,
    })
    assert next(
        item for item in mapping.items if item.old_object_id == "a:title"
    ).preserve_runtime_text
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    apply_bundle(candidate, build_hifi_change_bundle(root, inventory, source, mapping))

    parent = etree.parse(str(candidate / inventory.target.component_relative_path))
    instance = parent.xpath("./displayList/component[@id='a']")[0]
    assert instance.xpath("string(./Button/@title)") == "Village Level"
    variant_name = Path(instance.get("fileName")).name
    variant = etree.parse(str(candidate / "assets/MyVillage/Common" / variant_name))
    mapped_title = variant.xpath("./displayList/text[@id='title']")[0]
    assert mapped_title.get("xy") == "25,5"
    assert mapped_title.get("size") == "90,30"
    assert mapped_title.get("text") == "Default"


def test_nested_change_rejects_external_user_of_affected_ancestor(tmp_path):
    from figma_to_fgui.hifi_nested import _assert_shared_scope, inspect_component_tree
    from figma_to_fgui.hifi_patch import HifiPatchError

    root, inventory, _, _ = nested_case(tmp_path)
    package = root / "assets/MyVillage/package.xml"
    package.write_text(
        package.read_text().replace(
            "</resources>", '<component id="outer" name="Outer.xml" path="/Common/"/></resources>'
        )
    )
    (root / "assets/MyVillage/Common/Outer.xml").write_text(
        '<component size="120,44"><displayList><component id="inner" src="sharedbtn1" xy="0,0"/></displayList></component>'
    )
    (root / inventory.target.component_relative_path).write_text(
        '<component size="750,420"><displayList><component id="outerInstance" src="outer" xy="0,0"/></displayList></component>'
    )
    (root / "assets/MyVillage/Panel/Other.xml").write_text(
        '<component size="750,420"><displayList><component id="other" src="outer" xy="0,0"/></displayList></component>'
    )
    inventory = inspect_component_tree(root, inventory.target)
    with pytest.raises(HifiPatchError, match="shared_scope"):
        _assert_shared_scope(root, inventory, {"assets/MyVillage/Common/Button_Common.xml"})
