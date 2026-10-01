from __future__ import annotations

from pathlib import Path

from figma_to_fgui.hifi_project_inspector import (
    inspect_component,
    inspect_hifi_targets,
    target_from_option,
)
from figma_to_fgui.uploaded_project import index_uploaded_project

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement/old_project"


def _inventory():
    project = index_uploaded_project(FIXTURE, "old.zip")
    tree = inspect_hifi_targets(FIXTURE, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    component = next(item for item in directory.components if item.resource_id == "sketch01")
    return inspect_component(FIXTURE, target_from_option(project, package, directory, component))


def test_inventory_keeps_identity_behavior_and_unknown_xml() -> None:
    inventory = _inventory()
    reset = next(item for item in inventory.objects if item.object_id == "btn_reset")
    assert reset.name == "Btn_Reset"
    assert reset.controller_refs == ("page",)
    assert reset.transition_refs == ("intro",)
    assert len(reset.protected_sha256) == 64
    assert inventory.parse_complete is False
    assert inventory.unknown_tags == ("mystery",)


def test_shared_component_instance_is_marked_but_definition_is_not_opened() -> None:
    inventory = _inventory()
    button = next(item for item in inventory.objects if item.object_id == "btn_next")
    assert button.shared_resource is True
    assert button.resource_id == "sharedbtn1"


def test_inventory_builds_behavior_graph_for_states_instances_and_runtime_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    package_root = root / "assets/Book"
    (package_root / "Panel").mkdir(parents=True)
    (package_root / "Component").mkdir()
    (root / "Book.fairy").write_text(
        '<projectDescription id="1234567890abcdef1234567890abcdef" type="Unity" version="5.0"/>',
        "utf-8",
    )
    (package_root / "package.xml").write_text(
        '<packageDescription id="book01"><resources>'
        '<component id="root01" name="Root.xml" path="/Panel/"/>'
        '<component id="child01" name="Child.xml" path="/Component/"/>'
        '</resources><publish/></packageDescription>',
        "utf-8",
    )
    (package_root / "Component/Child.xml").write_text(
        '<component size="100,40" extention="Button">'
        '<controller name="button" pages="0,up,1,down,3,selectedOver"/>'
        '<displayList><text id="title" text="@title" size="100,40">'
        '<gearText controller="button" values="Idle,Pressed,Selected"/>'
        '</text></displayList></component>',
        "utf-8",
    )
    (package_root / "Panel/Root.xml").write_text(
        '<component size="300,200">'
        '<controller name="status" pages="0,lock,1,ready,5,finish">'
        '<action type="change_page" target="gift" controller="state" pages="0,1"/>'
        '</controller>'
        '<displayList>'
        '<group id="giftGroup" name="GiftGroup"/>'
        '<component id="gift" name="Gift" src="child01" group="giftGroup" xy="20,20">'
        '<Button title="领取" controller="button,1"/>'
        '<property target="title" propertyId="0" value="领取奖励"/>'
        '<gearDisplay controller="status" pages="1,5"/>'
        '</component>'
        '<component id="external" name="External" src="missing01" pkg="external01" '
        'xy="160,20" size="100,40"/>'
        '<text id="timer" name="Timer" text="{time}" xy="20,80" size="100,20"/>'
        '</displayList>'
        '<transition name="show" autoPlay="true" repeat="-1">'
        '<item type="Alpha" target="gift" time="0" duration="0.2" startValue="0" endValue="1"/>'
        '</transition>'
        '</component>',
        "utf-8",
    )
    project = index_uploaded_project(root, "book.zip")
    tree = inspect_hifi_targets(root, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    target = target_from_option(project, package, directory, directory.components[0])

    inventory = inspect_component(root, target)

    assert inventory.behavior.controllers[0].pages[2].page_id == "5"
    assert inventory.behavior.controllers[0].pages[2].name == "finish"
    assert inventory.behavior.action_count == 1
    assert inventory.behavior.gear_count == 1
    assert inventory.behavior.transitions[0].autoplay is True
    assert inventory.behavior.transitions[0].repeat == "-1"
    assert inventory.behavior.transitions[0].target_ids == ("gift",)
    instance = inventory.behavior.instances[0]
    assert instance.controller_assignments == ("button,1", "status")
    assert instance.property_assignments == (
        "property:propertyId=0,target=title,value=领取奖励",
    )
    assert instance.referenced_component_path == "assets/Book/Component/Child.xml"
    assert instance.referenced_behavior_sha256 is not None
    assert instance.referenced_controller_count == 1
    assert instance.referenced_transition_count == 0
    assert instance.referenced_action_count == 0
    assert instance.referenced_gear_count == 1
    assert inventory.behavior.referenced_component_paths == (
        "assets/Book/Component/Child.xml",
    )
    gift = next(item for item in inventory.objects if item.object_id == "gift")
    assert (gift.width, gift.height) == (100, 40)
    assert set(gift.behavior_roles) >= {
        "controller_driven",
        "transition_target",
        "controller_action_target",
        "component_instance",
        "instance_parameterized",
        "nested_behavior",
        "group_member",
    }
    assert gift.dynamic_properties == ("visible",)
    group = next(item for item in inventory.objects if item.object_id == "giftGroup")
    assert "behavior_group" in group.behavior_roles
    assert inventory.behavior.runtime_bound_object_ids == ("timer",)
    assert inventory.behavior.unresolved_instance_ids == ("external",)
    external = next(item for item in inventory.objects if item.object_id == "external")
    assert "unresolved_component_reference" in external.behavior_roles
    assert inventory.parse_complete is False
