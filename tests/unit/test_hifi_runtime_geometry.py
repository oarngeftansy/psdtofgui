"""Runtime-authoritative geometry facts for advanced layout groups and relations.

FairyGUI recomputes the design-time ``xy`` of an object at runtime when the
object carries a relation, or when its parent group is an advanced auto-layout
group. A HIFI PSD depicts the rendered runtime result, so the XML ``xy`` of
such an object is not evidence for or against a correspondence. These tests
lock the inventory facts and the honest scoring that follows from them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_mapping import build_mapping
from figma_to_fgui.hifi_project_inspector import (
    inspect_component,
    inspect_hifi_targets,
    target_from_option,
)
from figma_to_fgui.models import Bounds
from figma_to_fgui.uploaded_project import index_uploaded_project

PANEL = """<?xml version="1.0" encoding="utf-8"?>
<component size="1000,800">
  <displayList>
    <text id="plain" name="plain" xy="10,10" size="100,40" font="ui://lab01fnt" fontSize="20" text="Plain"/>
    <text id="flow_a" name="flow_a" xy="200,100" size="100,40" group="flow" font="ui://lab01fnt" fontSize="20" text="Alpha"/>
    <text id="flow_b" name="flow_b" xy="310,100" size="100,40" group="flow" font="ui://lab01fnt" fontSize="20" text="Beta"/>
    <graph id="anchored_bar" name="anchored_bar" xy="60,610" size="80,30" group="anchored" type="rect" lineSize="0" fillColor="#ff000000"/>
    <group id="flow" name="flow" xy="200,100" size="400,60" advanced="true" layout="hz" colGap="5"/>
    <group id="anchored" name="anchored" xy="50,600" size="300,100" advanced="true">
      <relation target="" sidePair="left-left,bottom-bottom"/>
    </group>
  </displayList>
</component>
"""


def _project(tmp_path: Path, panel: str = PANEL) -> Path:
    root = tmp_path / "project"
    (root / "assets/Lab/Panel").mkdir(parents=True)
    (root / "Lab.fairy").write_text(
        '<projectDescription id="1234567890abcdef1234567890abcdef" type="Unity" version="5.0"/>',
        "utf-8",
    )
    (root / "assets/Lab/package.xml").write_text(
        '<packageDescription id="lab01"><resources>'
        '<component id="panel01" name="Panel_Lab.xml" path="/Panel/"/>'
        "</resources><publish/></packageDescription>",
        "utf-8",
    )
    (root / "assets/Lab/Panel/Panel_Lab.xml").write_text(panel, "utf-8")
    return root


def _target(root: Path, resource_id: str = "panel01"):
    project = index_uploaded_project(root, "lab.zip")
    tree = inspect_hifi_targets(root, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    component = next(item for item in directory.components if item.resource_id == resource_id)
    return target_from_option(project, package, directory, component)


def _inventory(root: Path, resource_id: str = "panel01"):
    return inspect_component(root, _target(root, resource_id))


def _solo_panel(*, layout: bool, anchored: bool = False) -> str:
    """One text object, optionally inside an advanced auto-layout group.

    The group is the only competitor and is non-rendering, so the text object
    is the sole mapping candidate and its score is fully determined. With
    ``anchored`` the group carries a relation, so its origin and the member
    positions are not computable from the XML.
    """
    group_attribute = ' group="flowz"' if layout else ""
    relation = '<relation target="" sidePair="center-center"/>' if anchored else ""
    group_element = (
        f'<group id="flowz" name="flowz" xy="200,100" size="400,60" advanced="true" '
        f'layout="hz">{relation}</group>'
        if layout
        else ""
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<component size="1000,800">\n  <displayList>\n'
        '    <text id="solo" name="solo" xy="200,100" size="100,40"'
        + group_attribute
        + ' font="ui://lab01fnt" fontSize="20" text="Alpha"/>\n'
        f"    {group_element}\n  </displayList>\n</component>\n"
    )


def _manifest(children: tuple[SelectionNode, ...]) -> SelectionManifest:
    return SelectionManifest(
        version=1,
        display_name="PSD",
        top_level_nodes=(
            SelectionNode(
                id="psd-root:lab",
                name="PSD",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=1000, height=800),
                properties={"psdKind": "group"},
                children=children,
            ),
        ),
    )


def _text(node_id: str, name: str, x: float, y: float, text: str) -> SelectionNode:
    return SelectionNode(
        id=node_id,
        name=name,
        type="TEXT",
        bounds=Bounds(x=x, y=y, width=100, height=40),
        text=text,
        properties={"psdKind": "type"},
    )


def test_auto_layout_members_get_computed_runtime_positions(tmp_path: Path) -> None:
    objects = {o.object_id: o for o in _inventory(_project(tmp_path)).objects}
    assert objects["flow"].auto_layout == "hz"
    assert objects["flow"].position_runtime_bound is False
    # hz layout places members from the group origin in display order.
    assert objects["flow_a"].position_runtime_bound is False
    assert objects["flow_a"].x == pytest.approx(200.0)
    assert objects["flow_a"].y == pytest.approx(100.0)
    assert objects["flow_b"].position_runtime_bound is False
    assert objects["flow_b"].x == pytest.approx(305.0)  # 200 + width 100 + colGap 5


def test_layout_member_of_repositioned_group_keeps_design_geometry(tmp_path: Path) -> None:
    panel = PANEL.replace(
        '<group id="flow" name="flow" xy="200,100" size="400,60" advanced="true" layout="hz" colGap="5"/>',
        '<group id="flow" name="flow" xy="200,100" size="400,60" advanced="true" '
        'layout="hz" colGap="5"><relation target="" sidePair="center-center"/></group>',
    )
    objects = {o.object_id: o for o in _inventory(_project(tmp_path, panel)).objects}
    assert objects["flow_a"].position_runtime_bound is True
    assert objects["flow_a"].x == pytest.approx(200.0)
    assert objects["flow_b"].position_runtime_bound is True
    assert objects["flow_b"].x == pytest.approx(310.0)


def test_layout_gap_skips_excluded_invisible_members(tmp_path: Path) -> None:
    panel = PANEL.replace(
        '<text id="flow_b" name="flow_b" xy="310,100" size="100,40" group="flow" '
        'font="ui://lab01fnt" fontSize="20" text="Beta"/>',
        '<text id="flow_b" name="flow_b" xy="310,100" size="100,40" group="flow" '
        'visible="false" font="ui://lab01fnt" fontSize="20" text="Beta"/>'
        '<text id="flow_c" name="flow_c" xy="420,100" size="100,40" group="flow" '
        'font="ui://lab01fnt" fontSize="20" text="Gamma"/>',
    ).replace('layout="hz" colGap="5"', 'layout="hz" colGap="5" excludeInvisibles="true"')
    objects = {o.object_id: o for o in _inventory(_project(tmp_path, panel)).objects}
    assert objects["flow_a"].x == pytest.approx(200.0)
    # The invisible member is excluded from the layout and occupies no space.
    assert objects["flow_c"].x == pytest.approx(305.0)


def test_parent_anchored_relation_binds_group_and_its_members(tmp_path: Path) -> None:
    objects = {o.object_id: o for o in _inventory(_project(tmp_path)).objects}
    # A relation with an empty target is anchored to the parent. It still
    # repositions the object at runtime and must not be silently dropped.
    assert objects["anchored"].relation_side_pairs
    assert objects["anchored"].position_runtime_bound is True
    assert objects["anchored_bar"].position_runtime_bound is True


def test_plain_object_keeps_authoritative_geometry(tmp_path: Path) -> None:
    plain = {o.object_id: o for o in _inventory(_project(tmp_path)).objects}["plain"]
    assert plain.relation_side_pairs == ()
    assert plain.auto_layout is None
    assert plain.position_runtime_bound is False


def test_layout_none_and_empty_side_pair_do_not_bind_geometry(tmp_path: Path) -> None:
    panel = PANEL.replace('layout="hz"', 'layout="none"').replace(
        'sidePair="left-left,bottom-bottom"', 'sidePair=""'
    )
    objects = {o.object_id: o for o in _inventory(_project(tmp_path, panel)).objects}
    assert objects["flow"].auto_layout is None
    assert objects["flow_a"].position_runtime_bound is False
    assert objects["anchored"].relation_side_pairs == ()
    assert objects["anchored"].position_runtime_bound is False
    assert objects["anchored_bar"].position_runtime_bound is False


def test_nested_expansion_preserves_runtime_bound_facts(tmp_path: Path) -> None:
    from figma_to_fgui.hifi_nested import inspect_component_tree

    root = _project(tmp_path)
    objects = {o.object_id: o for o in inspect_component_tree(root, _target(root)).objects}
    assert objects["flow_a"].position_runtime_bound is False
    assert objects["flow_a"].x == pytest.approx(200.0)
    assert objects["flow_b"].x == pytest.approx(305.0)
    assert objects["anchored_bar"].position_runtime_bound is True
    assert objects["plain"].position_runtime_bound is False


def test_runtime_bound_score_drops_non_authoritative_position(tmp_path: Path) -> None:
    # Same size, same type, exactly on the design-time xy. The name differs, so
    # only the position term could lift this wrong pair over the 0.55 threshold.
    # The anchored layout group makes the member position non-authoritative.
    inventory = _inventory(_project(tmp_path / "a", _solo_panel(layout=True, anchored=True)))
    draft = build_mapping(inventory, _manifest((_text("psd:z", "Zzz", 200, 100, "Zzz"),)))
    item = next(i for i in draft.items if i.old_object_id == "solo")
    evidence = item.evidence
    assert evidence.position_authoritative is False
    assert evidence.position_score == pytest.approx(1.0)
    expected = (
        evidence.name_score * 0.36
        + evidence.size_score * 0.14
        + evidence.type_score * 0.14
        + evidence.parent_score * 0.06
        + evidence.order_score * 0.06
    ) / 0.76
    assert item.score == pytest.approx(expected, abs=1e-6)
    assert item.score < 0.55
    assert item.status == "fgui_only"


def test_authoritative_object_still_uses_position_evidence(tmp_path: Path) -> None:
    # The identical panel without the auto-layout group keeps the position term
    # and reaches the review threshold, proving the flag is what changed.
    inventory = _inventory(_project(tmp_path / "b", _solo_panel(layout=False)))
    draft = build_mapping(inventory, _manifest((_text("psd:z", "Zzz", 200, 100, "Zzz"),)))
    item = next(i for i in draft.items if i.old_object_id == "solo")
    evidence = item.evidence
    assert evidence.position_authoritative is True
    expected = (
        evidence.name_score * 0.36
        + evidence.position_score * 0.24
        + evidence.size_score * 0.14
        + evidence.type_score * 0.14
        + evidence.parent_score * 0.06
        + evidence.order_score * 0.06
    )
    assert item.score == pytest.approx(expected, abs=1e-6)
    assert item.status == "suggested"


def test_computed_layout_position_restores_position_evidence(tmp_path: Path) -> None:
    # Without the relation the layout group origin is authoritative, so the
    # member's runtime position is computed and the position term returns.
    inventory = _inventory(_project(tmp_path / "c", _solo_panel(layout=True)))
    draft = build_mapping(inventory, _manifest((_text("psd:z", "Zzz", 200, 100, "Zzz"),)))
    item = next(i for i in draft.items if i.old_object_id == "solo")
    assert item.evidence.position_authoritative is True
    assert item.evidence.position_score == pytest.approx(1.0)
    assert item.score >= 0.55
    assert item.status == "suggested"
    assert item.status == "suggested"


def test_runtime_bound_object_still_matches_on_exact_name(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path / "a", _solo_panel(layout=True, anchored=True)))
    # Runtime reflow moved the artwork; the name is still decisive evidence.
    draft = build_mapping(inventory, _manifest((_text("psd:a", "solo", 480, 320, "Alpha"),)))
    item = next(i for i in draft.items if i.old_object_id == "solo")
    assert item.evidence.position_authoritative is False
    assert item.figma_node_id == "psd:a"
    assert item.status == "matched"


def test_existing_fixture_panel_has_no_runtime_bound_objects() -> None:
    fixture = Path(__file__).parents[1] / "fixtures/hifi_replacement/old_project"
    objects = {o.object_id: o for o in _inventory(fixture, "sketch01").objects}
    assert objects["title_bar"].position_runtime_bound is False


def test_fixture_copy_with_layout_group_is_runtime_bound(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/hifi_replacement/old_project"
    root = tmp_path / "village"
    shutil.copytree(fixture, root)
    panel = root / "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml"
    text = panel.read_text("utf-8").replace(
        "</displayList>",
        '<group id="flowgrp" name="flowgrp" xy="0,0" size="100,40" advanced="true" layout="hz"/>'
        "</displayList>",
    ).replace('<text id="title_bar"', '<text id="title_bar" group="flowgrp"')
    panel.write_text(text, "utf-8")
    objects = {o.object_id: o for o in _inventory(root, "sketch01").objects}
    # The hz group has no relation, so the member keeps authoritative geometry
    # with its computed layout position instead of a runtime-bound flag.
    assert objects["title_bar"].position_runtime_bound is False
    assert objects["title_bar"].x == pytest.approx(0.0)
    assert objects["board_bg"].position_runtime_bound is False