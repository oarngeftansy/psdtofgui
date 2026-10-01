from __future__ import annotations

from pathlib import Path

from figma_to_fgui.hifi_project_inspector import inspect_hifi_targets, target_from_option
from figma_to_fgui.uploaded_project import index_uploaded_project

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement/old_project"


def test_inspector_returns_package_directory_and_component_identity() -> None:
    project = index_uploaded_project(FIXTURE, "old.zip")
    tree = inspect_hifi_targets(FIXTURE, project)
    package = next(item for item in tree.packages if item.name == "MyVillage")
    panel = next(node for node in package.directories if node.path == "Panel")
    component = next(node for node in panel.components if node.name == "Panel_MyVillage_Sketchboard")
    target = target_from_option(project, package, panel, component)
    assert target.component_id == "sketch01"
    assert target.component_relative_path == (
        "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml"
    )
    assert "C:" not in tree.model_dump_json()


def test_invalid_component_is_visible_but_not_selectable(tmp_path: Path) -> None:
    root = tmp_path / "project"
    package_root = root / "assets/Bad"
    package_root.mkdir(parents=True)
    (root / "Bad.fairy").write_text(
        '<projectDescription id="1234567890abcdef1234567890abcdef" type="Unity" version="5.0"/>',
        "utf-8",
    )
    (package_root / "package.xml").write_text(
        '<packageDescription id="bad01"><resources>'
        '<component id="badcmp" name="Broken.xml" path="/Panel/"/>'
        '</resources><publish/></packageDescription>',
        "utf-8",
    )
    (package_root / "Panel").mkdir()
    (package_root / "Panel/Broken.xml").write_text("<component", "utf-8")
    project = index_uploaded_project(root, "bad.zip")
    component = inspect_hifi_targets(root, project).packages[0].directories[0].components[0]
    assert component.selectable is False
    assert component.reason == "component_unavailable"


def test_unsupported_editor_version_disables_every_component(tmp_path: Path) -> None:
    root = tmp_path / "project"
    package_root = root / "assets/Old"
    (package_root / "Panel").mkdir(parents=True)
    (root / "Old.fairy").write_text(
        '<projectDescription id="1234567890abcdef1234567890abcdef" type="Unity" version="4.0"/>',
        "utf-8",
    )
    (package_root / "package.xml").write_text(
        '<packageDescription id="old01"><resources>'
        '<component id="oldcmp" name="Root.xml" path="/Panel/"/>'
        '</resources><publish/></packageDescription>',
        "utf-8",
    )
    (package_root / "Panel/Root.xml").write_text(
        '<component size="10,10"><displayList/></component>', "utf-8"
    )
    project = index_uploaded_project(root, "old.zip")
    component = inspect_hifi_targets(root, project).packages[0].directories[0].components[0]
    assert component.selectable is False
    assert component.reason == "unsupported_fairygui_version"
