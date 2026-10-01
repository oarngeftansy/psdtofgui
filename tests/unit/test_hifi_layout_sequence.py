"""Layout-sequence proofs pair auto-layout strip members with PSD leaves.

An advanced auto-layout group places its members along the layout axis in
display order; that order is a runtime-authoritative fact. When one PSD group
holds exactly the strip's unclaimed visible leaves, the leaf order along the
axis and the member type sequence agree, and the region matches within a row
tolerance, the rank-to-rank pairing is structural evidence rather than a
score heuristic. Anything less than complete agreement stays pending.
"""

from __future__ import annotations

from pathlib import Path

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_mapping import build_mapping
from figma_to_fgui.hifi_project_inspector import (
    inspect_hifi_targets,
    target_from_option,
)
from figma_to_fgui.models import Bounds
from figma_to_fgui.uploaded_project import index_uploaded_project

PANEL = """<?xml version="1.0" encoding="utf-8"?>
<component size="1000,800">
  <displayList>
    <text id="s1" name="labelA" xy="100,100" size="120,30" group="strip" font="ui://lab01fnt" fontSize="20" text="Alpha"/>
    <loader id="s2" name="iconA" xy="230,100" size="40,40" group="strip" url="ui://pkg001res" fill="scale"/>
    <text id="s3" name="labelB" xy="280,100" size="80,30" group="strip" font="ui://lab01fnt" fontSize="20" text="Beta"/>
    <graph id="other" name="other" xy="600,600" size="90,40" type="rect" lineSize="0" fillColor="#ff112233"/>
    <group id="strip" name="strip" xy="100,100" size="260,40" advanced="true" layout="hz" colGap="10">
      <relation target="" sidePair="center-center"/>
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
        '<component id="panel01" name="Panel_S.xml" path="/Panel/"/>'
        "</resources><publish/></packageDescription>",
        "utf-8",
    )
    (root / "assets/Lab/Panel/Panel_S.xml").write_text(panel, "utf-8")
    return root


def _inventory(root: Path):
    project = index_uploaded_project(root, "lab.zip")
    tree = inspect_hifi_targets(root, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    component = next(item for item in directory.components if item.resource_id == "panel01")
    from figma_to_fgui.hifi_nested import inspect_component_tree
    return inspect_component_tree(root, target_from_option(project, package, directory, component))


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


def _group(children: tuple[SelectionNode, ...], name: str = "Strip") -> SelectionNode:
    return SelectionNode(
        id="psd-group:" + name,
        name=name,
        type="GROUP",
        bounds=Bounds(x=100, y=70, width=300, height=40),
        properties={"psdKind": "group"},
        children=children,
    )


def _text(node_id: str, x: float, y: float, text: str) -> SelectionNode:
    return SelectionNode(
        id=node_id, name="Layer " + node_id, type="TEXT",
        bounds=Bounds(x=x, y=y, width=110, height=26), text=text,
        properties={"psdKind": "type"},
    )


def _image(node_id: str, x: float, y: float) -> SelectionNode:
    return SelectionNode(
        id=node_id, name="Layer " + node_id, type="IMAGE",
        bounds=Bounds(x=x, y=y, width=36, height=36),
        properties={"psdKind": "smartobject"},
    )


def _strip_leaves(y_offset: float = -25.0) -> tuple[SelectionNode, ...]:
    # The PSD drew the strip slightly above the FGUI design position, exactly
    # like the real material's runtime-shifted strips.
    return (
        _text("psd:a", 105, 70 + y_offset, "First"),
        _image("psd:b", 240, 72 + y_offset),
        _text("psd:c", 285, 74 + y_offset, "999"),
    )


def test_strip_members_pair_by_layout_sequence(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    draft = build_mapping(inventory, _manifest((_group(_strip_leaves()),)))
    for old_id, node_id in (("s1", "psd:a"), ("s2", "psd:b"), ("s3", "psd:c")):
        item = next(i for i in draft.items if i.old_object_id == old_id)
        assert item.status == "matched", (old_id, item.status, item.score)
        assert item.action == "accept"
        assert item.figma_node_id == node_id
        assert item.evidence.order_score == 1.0
    assert not any(i.figma_node_id in {"psd:a", "psd:b", "psd:c"} and i.status == "hifi_added"
                   for i in draft.items)


def test_type_sequence_mismatch_keeps_the_strip_pending(tmp_path: Path) -> None:
    leaves = _strip_leaves()
    inventory = _inventory(_project(tmp_path))
    draft = build_mapping(inventory, _manifest((_group((
        leaves[0], _text("psd:mid", 240, 47, "Mid"), leaves[2],
    )),)))
    for old_id in ("s1", "s2", "s3"):
        item = next(i for i in draft.items if i.old_object_id == old_id)
        assert item.status == "fgui_only"


def test_leaf_count_mismatch_keeps_the_strip_pending(tmp_path: Path) -> None:
    extra = _text("psd:d", 400, 45, "Extra")
    inventory = _inventory(_project(tmp_path))
    draft = build_mapping(inventory, _manifest((_group((*_strip_leaves(), extra)),)))
    for old_id in ("s1", "s2", "s3"):
        item = next(i for i in draft.items if i.old_object_id == old_id)
        assert item.status == "fgui_only"


def test_visible_subgroup_inside_psd_group_blocks_pairing(tmp_path: Path) -> None:
    nested = SelectionNode(
        id="psd:sub", name="Sub", type="GROUP",
        bounds=Bounds(x=360, y=45, width=40, height=30),
        properties={"psdKind": "group"},
        children=(_image("psd:subleaf", 360, 45),),
    )
    inventory = _inventory(_project(tmp_path))
    draft = build_mapping(inventory, _manifest((_group((*_strip_leaves(), nested)),)))
    for old_id in ("s1", "s2", "s3"):
        item = next(i for i in draft.items if i.old_object_id == old_id)
        assert item.status == "fgui_only"


def test_far_away_leaves_do_not_pair(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    leaves = (
        _text("psd:a", 105, 500, "First"),
        _image("psd:b", 240, 500),
        _text("psd:c", 285, 500, "999"),
    )
    group = SelectionNode(
        id="psd-group:Far", name="Far", type="GROUP",
        bounds=Bounds(x=100, y=500, width=300, height=40),
        properties={"psdKind": "group"}, children=leaves,
    )
    draft = build_mapping(inventory, _manifest((group,)))
    for old_id in ("s1", "s2", "s3"):
        item = next(i for i in draft.items if i.old_object_id == old_id)
        assert item.status == "fgui_only"


def test_partially_decided_strip_is_not_force_paired(tmp_path: Path) -> None:
    # s3 has a unique text proof to one leaf, so the remaining members are not
    # silently paired around it.
    leaves = _strip_leaves()
    leaves = (leaves[0], leaves[1],
              SelectionNode(id="psd:c", name="Layer c", type="TEXT",
                            bounds=Bounds(x=285, y=49, width=70, height=24), text="Beta",
                            properties={"psdKind": "type"}))
    inventory = _inventory(_project(tmp_path))
    draft = build_mapping(inventory, _manifest((_group(leaves),)))
    s3 = next(i for i in draft.items if i.old_object_id == "s3")
    assert s3.status == "matched"          # text proof still works
    s1 = next(i for i in draft.items if i.old_object_id == "s1")
    assert s1.status == "fgui_only"        # incomplete strip stays manual


def test_group_without_layout_is_not_sequence_paired(tmp_path: Path) -> None:
    panel = PANEL.replace('layout="hz" colGap="10"', "")
    inventory = _inventory(_project(tmp_path, panel))
    draft = build_mapping(inventory, _manifest((_group(_strip_leaves()),)))
    for old_id in ("s1", "s2", "s3"):
        item = next(i for i in draft.items if i.old_object_id == old_id)
        assert item.status == "fgui_only"