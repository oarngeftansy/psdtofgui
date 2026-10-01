"""Off-viewport PSD leaves resolve as scroll-region content of covering objects.

A FairyGUI component's viewport clips its display; objects extending beyond
it exist because the original project scrolls. A PSD leaf that predominantly
sits in the off-viewport region and predominantly overlaps a visible old
object that itself extends beyond the viewport is scroll-region design
content. The current per-object mapping mechanism cannot express scroll-skin
replacement (the old object is already matched to its own PSD counterpart;
the scroll decorations are additional visual layers in the same region),
so the leaf resolves as out_of_scope rather than as a pending review item.
"""

from __future__ import annotations

from pathlib import Path

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_mapping import build_mapping, require_psd_coverage
from figma_to_fgui.hifi_project_inspector import inspect_hifi_targets, target_from_option
from figma_to_fgui.models import Bounds
from figma_to_fgui.uploaded_project import index_uploaded_project

PANEL = """<?xml version="1.0" encoding="utf-8"?>
<component size="1000,800">
  <displayList>
    <image id="n_bg" name="bg" src="imgbg" fileName="Img/Bg.png" xy="0,-200" size="1000,1200"/>
    <graph id="n_bar" name="bar" xy="700,600" size="90,40" type="rect" lineSize="0" fillColor="#ff112233"/>
    <text id="n_txt" name="txt" xy="100,700" size="80,30" font="ui://lab01fnt" fontSize="20" text="Local"/>
  </displayList>
</component>
"""


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "assets/Lab/Panel").mkdir(parents=True)
    (root / "assets/Lab/Img").mkdir(parents=True)
    (root / "Lab.fairy").write_text(
        '<projectDescription id="1234567890abcdef1234567890abcdef" type="Unity" version="5.0"/>',
        "utf-8",
    )
    (root / "assets/Lab/package.xml").write_text(
        '<packageDescription id="lab01"><resources>'
        '<image id="imgbg" name="Bg.png" path="/Img/"/>'
        '<component id="panel01" name="Panel_S.xml" path="/Panel/"/>'
        "</resources><publish/></packageDescription>",
        "utf-8",
    )
    (root / "assets/Lab/Img/Bg.png").write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6260010000050001"
            "0d0a2db40000000049454e44ae426082"
        )
    )
    (root / "assets/Lab/Panel/Panel_S.xml").write_text(PANEL, "utf-8")
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


def _leaf(node_id: str, name: str, x: float, y: float, w: float = 80, h: float = 30) -> SelectionNode:
    return SelectionNode(
        id=node_id,
        name=name,
        type="RECTANGLE",
        bounds=Bounds(x=x, y=y, width=w, height=h),
        properties={"psdKind": "shape"},
    )


def test_scroll_region_leaf_resolves_out_of_scope(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    # n_bg extends from y=-200 to y=1000 (viewport is 0..800).
    # A PSD leaf at y=850..950 is >50% outside the viewport and
    # predominantly covered by n_bg which also extends beyond it.
    draft = build_mapping(inventory, _manifest((
        _leaf("psd:scroll", "ScrollArt", 100, 850, 200, 100),
    )))
    scroll = next(i for i in draft.items if i.figma_node_id == "psd:scroll")
    assert scroll.status == "out_of_scope"
    assert scroll.action == "preserve_structure"
    assert scroll.out_of_scope is True
    assert scroll not in [i for i in draft.items if i.action is None]


def test_in_viewport_leaf_stays_pending(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    draft = build_mapping(inventory, _manifest((
        _leaf("psd:inview", "InView", 100, 100, 80, 30),
    )))
    item = next(i for i in draft.items if i.figma_node_id == "psd:inview")
    assert item.status in {"hifi_added", "blocked"}


def test_barely_off_viewport_stays_pending(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    # 30 of 80 height is off-viewport (y=780..810, viewport ends at 800).
    # That is <50%, so it stays pending.
    draft = build_mapping(inventory, _manifest((
        _leaf("psd:edge", "Edge", 100, 780, 80, 30),
    )))
    item = next(i for i in draft.items if i.figma_node_id == "psd:edge")
    assert item.status in {"hifi_added", "blocked"}


def test_off_viewport_without_extending_container_stays_pending(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    # A leaf far beyond the viewport with no visible old object that
    # both extends beyond the viewport and covers it stays pending.
    draft = build_mapping(inventory, _manifest((
        _leaf("psd:far", "Far", 100, 2000, 80, 30),
    )))
    item = next(i for i in draft.items if i.figma_node_id == "psd:far")
    assert item.status in {"hifi_added", "blocked"}


def test_scroll_region_counts_as_covered(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    manifest = _manifest((
        _leaf("psd:scroll", "ScrollArt", 100, 850, 200, 100),
    ))
    draft = build_mapping(inventory, manifest)
    require_psd_coverage(draft, manifest)