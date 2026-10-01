"""Z-order attribution under policy 24.

FairyGUI displayList order determines what renders on top. A leaf that sits
over a shared component's region is no longer auto-preserved: scope follows
real references, writes to shared definitions are isolated per instance at
build time, and a leaf that pairs with no old object stays an explicit
hifi_added decision for a human instead of a silent preserve.
"""

from __future__ import annotations

from pathlib import Path

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_mapping import build_mapping
from figma_to_fgui.hifi_project_inspector import inspect_hifi_targets, target_from_option
from figma_to_fgui.models import Bounds
from figma_to_fgui.uploaded_project import index_uploaded_project

SHARED_TAG = """<?xml version="1.0" encoding="utf-8"?>
<component size="80,30">
  <displayList>
    <text id="label" name="label" xy="5,5" size="70,20" font="ui://labsharefnt01" fontSize="14" text="Tag"/>
  </displayList>
</component>
"""
PANEL = """<?xml version="1.0" encoding="utf-8"?>
<component size="1000,800">
  <displayList>
    <graph id="n_bar" name="bar" xy="20,300" size="200,300" type="rect" lineSize="0" fillColor="#ff445566"/>
    <component id="n_sbtn" name="sbtn" src="tag01" fileName="Tag/Tag_S.xml" pkg="labshare" xy="25,350"/>
    <graph id="n_other" name="other" xy="700,600" size="90,40" type="rect" lineSize="0" fillColor="#ff112233"/>
    <text id="n_txt" name="txt" xy="100,700" size="80,30" font="ui://lab01fnt" fontSize="20" text="Local"/>
  </displayList>
</component>
"""


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "assets/LabP/Panel").mkdir(parents=True)
    (root / "assets/Shared/Tag").mkdir(parents=True)
    (root / "Lab.fairy").write_text(
        '<projectDescription id="1234567890abcdef1234567890abcdef" type="Unity" version="5.0"/>',
        "utf-8",
    )
    (root / "assets/LabP/package.xml").write_text(
        '<packageDescription id="lablocal"><resources>'
        '<component id="panel01" name="Panel_S.xml" path="/Panel/"/>'
        "</resources><publish/></packageDescription>",
        "utf-8",
    )
    (root / "assets/Shared/package.xml").write_text(
        '<packageDescription id="labshare"><resources>'
        '<component id="tag01" name="Tag_S.xml" path="/Tag/"/>'
        "</resources><publish/></packageDescription>",
        "utf-8",
    )
    (root / "assets/LabP/Panel/Panel_S.xml").write_text(PANEL, "utf-8")
    (root / "assets/Shared/Tag/Tag_S.xml").write_text(SHARED_TAG, "utf-8")
    return root


def _inventory(root: Path):
    project = index_uploaded_project(root, "lab.zip")
    tree = inspect_hifi_targets(root, project)
    package = next(item for item in tree.packages if item.name == "LabP")
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


def _leaf(node_id: str, name: str, x: float, y: float, w: float = 60, h: float = 30) -> SelectionNode:
    return SelectionNode(
        id=node_id,
        name=name,
        type="RECTANGLE",
        bounds=Bounds(x=x, y=y, width=w, height=h),
        properties={"psdKind": "shape"},
    )


def test_leaf_fully_inside_shared_button_stays_an_explicit_decision(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    # n_sbtn (pkg=labshare, 80x30 at (25,350)) sits ON TOP of n_bar (200x300
    # at (20,300)). A leaf at (30,355,50,20) is fully inside n_bar AND fully
    # inside n_sbtn's subtree. The tag definition is private to this panel,
    # so the leaf is neither auto-preserved nor silently claimed: it stays a
    # human decision while shared writes would be variant-isolated.
    draft = build_mapping(inventory, _manifest((
        _leaf("psd:deco", "Deco", 30, 355, 50, 20),
    )))
    deco = next(i for i in draft.items if i.figma_node_id == "psd:deco")
    assert deco.status == "hifi_added"
    assert deco.out_of_scope is False


def test_leaf_majority_on_shared_button_stays_an_explicit_decision(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    # A leaf at (28,352,60,26) extends slightly beyond n_sbtn (25,350,80,30)
    # but >50% of its area overlaps n_sbtn's subtree. Overlap alone does not
    # claim or preserve the leaf; it remains a human decision.
    draft = build_mapping(inventory, _manifest((
        _leaf("psd:partial", "Partial", 28, 352, 60, 26),
    )))
    item = next(i for i in draft.items if i.figma_node_id == "psd:partial")
    assert item.status == "hifi_added"


def test_leaf_far_from_shared_stays_pending(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    draft = build_mapping(inventory, _manifest((
        _leaf("psd:far", "Far", 600, 500, 60, 30),
    )))
    item = next(i for i in draft.items if i.figma_node_id == "psd:far")
    assert item.status in {"hifi_added", "blocked"}


def test_leaf_minor_overlap_on_shared_stays_pending(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    # A leaf at (100,400,60,30) barely overlaps n_sbtn (25,350,80,30) —
    # x 100-105 = 5 px overlap, y 400-380 = 0 overlap. It stays pending.
    draft = build_mapping(inventory, _manifest((
        _leaf("psd:minor", "Minor", 100, 400, 60, 30),
    )))
    item = next(i for i in draft.items if i.figma_node_id == "psd:minor")
    assert item.status in {"hifi_added", "blocked"}