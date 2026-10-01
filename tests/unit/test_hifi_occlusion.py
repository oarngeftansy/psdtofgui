"""A fully occluded PSD leaf renders nothing and cannot map to anything.

An opaque pixel layer covering the whole canvas above a leaf hides that leaf
in the authored composite. Demanding a correspondence for content that never
renders is a coverage-requirement defect, so the occluded leaf resolves as a
fact instead of pending review. Occlusion is claimed only when a validator
confirms the covering layer's raster pixels are actually opaque; anything
less stays fail-closed and pending.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_mapping import (
    HifiMappingError,
    apply_mapping_decision,
    build_mapping,
    require_psd_coverage,
)
from figma_to_fgui.hifi_project_inspector import inspect_hifi_targets, target_from_option
from figma_to_fgui.hifi_replacement_models import HifiMappingDecision
from figma_to_fgui.models import Bounds
from figma_to_fgui.uploaded_project import index_uploaded_project

PANEL = """<?xml version="1.0" encoding="utf-8"?>
<component size="1000,800">
  <displayList>
    <graph id="n_bar" name="bar" xy="700,600" size="90,40" type="rect" lineSize="0" fillColor="#ff112233"/>
    <text id="n_txt" name="txt" xy="100,700" size="80,30" font="ui://lab01fnt" fontSize="20" text="LocalTitle"/>
  </displayList>
</component>
"""


def _project(tmp_path: Path) -> Path:
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


def _leaf(node_id: str, di: int, x: float, y: float, *, kind: str = "type") -> SelectionNode:
    return SelectionNode(
        id=node_id,
        name="Layer " + node_id,
        type="TEXT" if kind == "type" else "RECTANGLE",
        bounds=Bounds(x=x, y=y, width=80, height=30),
        properties={"psdKind": kind, "psdDocumentIndex": di},
    )


def _cover(node_id: str, di: int, *, opacity: float = 1.0,
           effects: bool = False, bounds: tuple[float, float, float, float] | None = None) -> SelectionNode:
    b = bounds or (-10, -10, 1020, 820)
    return SelectionNode(
        id=node_id,
        name="Layer " + node_id,
        type="IMAGE",
        bounds=Bounds(x=b[0], y=b[1], width=b[2], height=b[3]),
        opacity=opacity,
        properties={
            "psdKind": "pixel",
            "psdDocumentIndex": di,
            "blendMode": "normal",
            "hasEffects": effects,
            "hasPixelMask": False,
            "hasVectorMask": False,
            "clipping": False,
        },
    )


def test_occluded_leaf_resolves_as_fact_without_pending(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    manifest = _manifest((
        _leaf("psd:under", 0, 100, 100),
        _cover("psd:cover", 5),
        _leaf("psd:above", 9, 200, 200),
    ))
    draft = build_mapping(inventory, manifest, occlusion_validator=lambda node: True)
    under = next(i for i in draft.items if i.figma_node_id == "psd:under")
    assert under.status == "occluded"
    assert under.action == "preserve_structure"
    assert under.occluded is True
    assert under not in [i for i in draft.items if i.action is None]
    above = next(i for i in draft.items if i.figma_node_id == "psd:above")
    assert above.status == "hifi_added"
    # The occluded leaf is excluded from the coverage requirement; the other
    # leaves are decided, so coverage closes without it.
    decided = draft.model_copy(update={"items": tuple(
        i.model_copy(update={"action": "accept"}) if i.status == "hifi_added" else i
        for i in draft.items)})
    require_psd_coverage(decided, manifest)


def test_occlusion_requires_pixel_opacity_confirmation(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    manifest = _manifest((
        _leaf("psd:under", 0, 100, 100),
        _cover("psd:cover", 5),
    ))
    draft = build_mapping(inventory, manifest, occlusion_validator=lambda node: False)
    under = next(i for i in draft.items if i.figma_node_id == "psd:under")
    assert under.status == "hifi_added"


def test_occlusion_without_validator_stays_pending(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    manifest = _manifest((
        _leaf("psd:under", 0, 100, 100),
        _cover("psd:cover", 5),
    ))
    draft = build_mapping(inventory, manifest)
    under = next(i for i in draft.items if i.figma_node_id == "psd:under")
    assert under.status == "hifi_added"


def test_semi_transparent_cover_does_not_occlude(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    manifest = _manifest((
        _leaf("psd:under", 0, 100, 100),
        _cover("psd:cover", 5, opacity=0.83),
    ))
    draft = build_mapping(inventory, manifest, occlusion_validator=lambda node: True)
    under = next(i for i in draft.items if i.figma_node_id == "psd:under")
    assert under.status == "hifi_added"


def test_partial_cover_does_not_occlude(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    manifest = _manifest((
        _leaf("psd:under", 0, 100, 100),
        _cover("psd:cover", 5, bounds=(0, 0, 500, 800)),
    ))
    draft = build_mapping(inventory, manifest, occlusion_validator=lambda node: True)
    under = next(i for i in draft.items if i.figma_node_id == "psd:under")
    assert under.status == "hifi_added"


def test_cover_below_the_leaf_does_not_occlude(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    manifest = _manifest((
        _cover("psd:cover", 0),
        _leaf("psd:over", 5, 100, 100),
    ))
    draft = build_mapping(inventory, manifest, occlusion_validator=lambda node: True)
    over = next(i for i in draft.items if i.figma_node_id == "psd:over")
    assert over.status == "hifi_added"


def test_occluded_item_rejects_user_decisions(tmp_path: Path) -> None:
    inventory = _inventory(_project(tmp_path))
    manifest = _manifest((
        _leaf("psd:under", 0, 100, 100),
        _cover("psd:cover", 5),
    ))
    draft = build_mapping(inventory, manifest, occlusion_validator=lambda node: True)
    item = next(i for i in draft.items if i.figma_node_id == "psd:under")
    with pytest.raises(HifiMappingError):
        apply_mapping_decision(draft, HifiMappingDecision(
            version=1, item_id=item.item_id, action="accept",
            mapping_revision=draft.mapping_revision,
        ), manifest)