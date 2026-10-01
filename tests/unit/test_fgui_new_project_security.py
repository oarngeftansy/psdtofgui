from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from figma_to_fgui.cli import (
    _load_new_project_config,
    _load_plan_v2,
    load_declared_asset_directory,
)
from figma_to_fgui.fgui_new_project_build import (
    BuiltNewProject,
    build_new_project,
    validate_project_archive,
)
from figma_to_fgui.fgui_new_project_validate import validate_xml_files

FIXTURE = Path("tests/fixtures/fgui-new-project")


def _build(tmp_path: Path) -> BuiltNewProject:
    plan = _load_plan_v2(FIXTURE / "generic-plan-v2.json")
    config = _load_new_project_config(FIXTURE / "config.json")
    payloads = load_declared_asset_directory(FIXTURE / "assets", plan.resources)
    return build_new_project(plan, config, payloads, tmp_path)


@pytest.mark.parametrize("member", ("../escape", "/absolute", "C:/absolute"))
def test_archive_gate_rejects_escape_and_absolute_members(
    tmp_path: Path, member: str
) -> None:
    built = _build(tmp_path / "built")
    attacked = tmp_path / "attacked.zip"
    with ZipFile(built.path) as source, ZipFile(attacked, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr(member, b"hostile")

    assert [item.code for item in validate_project_archive(attacked, built.manifest)] == [
        "fgui.writer.project.archive_invalid"
    ]


def test_archive_gate_rejects_duplicate_casefold_and_symlink_members(
    tmp_path: Path,
) -> None:
    built = _build(tmp_path / "built")
    attacks = (
        ("duplicate", ""),
        ("casefold", "genericwriterfixture/GENERICWRITERFIXTURE.FAIRY"),
        ("symlink", "GenericWriterFixture/host-link"),
    )
    with ZipFile(built.path) as source:
        entries = [(item, source.read(item)) for item in source.infolist()]
    marker = next(item for item, _ in entries if item.filename.endswith(".fairy"))
    marker_bytes = next(content for item, content in entries if item is marker)

    for attack, member in attacks:
        attacked = tmp_path / f"{attack}.zip"
        with ZipFile(attacked, "w", compression=ZIP_DEFLATED) as target:
            for item, content in entries:
                target.writestr(item, content)
            if attack == "duplicate":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    target.writestr(marker, marker_bytes)
            elif attack == "symlink":
                link = ZipInfo(member)
                link.create_system = 3
                link.external_attr = 0o120777 << 16
                target.writestr(link, b"target")
            else:
                target.writestr(member, marker_bytes)

        assert validate_project_archive(attacked, built.manifest)


def test_xml_gate_rejects_doctype_without_parsing_external_entity(tmp_path: Path) -> None:
    built = _build(tmp_path / "built")
    with ZipFile(built.path) as archive:
        files = {
            name.removeprefix("GenericWriterFixture/"): archive.read(name)
            for name in archive.namelist()
        }
    component = next(
        path
        for path in files
        if path.endswith(".xml") and not path.endswith("package.xml")
    )
    files[component] = (
        b'<!DOCTYPE component [<!ENTITY xxe SYSTEM "file:///private">]>\n'
        + files[component]
    )

    codes = {item.code for item in validate_xml_files(files)}
    assert "fgui.writer.xml.doctype_forbidden" in codes
    assert "fgui.writer.xml.component_invalid" in codes


def test_failed_hostile_asset_build_publishes_no_artifact(tmp_path: Path) -> None:
    plan = _load_plan_v2(FIXTURE / "generic-plan-v2.json")
    config = _load_new_project_config(FIXTURE / "config.json")
    payloads = load_declared_asset_directory(FIXTURE / "assets", plan.resources)
    payload = payloads.payload_for("resource:image")
    hostile = type(payloads).from_items(
        (payload.model_copy(update={"content": b"not an image"}),)
    )
    output = tmp_path / "out"

    from figma_to_fgui.fgui_new_project_build import NewProjectBuildError

    with pytest.raises(NewProjectBuildError):
        build_new_project(plan, config, hostile, output)
    assert not output.exists() or list(output.glob("*.zip")) == []
