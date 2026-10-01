from __future__ import annotations

import json
from pathlib import Path

from lxml import etree

from figma_to_fgui.fgui_xml_dialect_614 import parse_editor_fixture
from figma_to_fgui.figma_selection import SelectionManifest

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement"


def _walk(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    pending = list(nodes)
    while pending:
        node = pending.pop()
        result.append(node)
        pending.extend(node.get("children", []))  # type: ignore[arg-type]
    return result


def test_hifi_fixture_covers_first_release_cases() -> None:
    root = FIXTURE / "old_project"
    parsed = parse_editor_fixture(root)
    assert parsed.project_name == "OldVillage"
    assert parsed.package_name == "MyVillage"

    component_path = root / "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml"
    component = etree.parse(str(component_path))
    ids = {str(node.get("id")) for node in component.xpath(".//*[@id]")}
    assert {"title_bar", "silhouette_01", "btn_next", "btn_reset"} <= ids
    assert component.xpath(".//gearDisplay | .//transition")

    raw = json.loads((FIXTURE / "hifi-selection.json").read_text("utf-8"))
    manifest = SelectionManifest.model_validate(raw)
    assert manifest.display_name == "速记板 / HIFI v4"
    assert any(node["name"] == "ProgressBubble" for node in _walk(raw["top_level_nodes"]))
    assert manifest.resources[0].key == "hifi-board"
    assert (FIXTURE / "selection/resources/hifi-board").stat().st_size == manifest.resources[0].size

    expected = json.loads((FIXTURE / "expected-scope.json").read_text("utf-8"))
    assert expected["preserve_old_ids"] == ["btn_reset"]
    assert expected["add_figma_ids"] == ["progress-bubble"]
