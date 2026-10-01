from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from figma_to_fgui.cli import (
    _load_new_project_config,
    _load_plan_v2,
    load_declared_asset_directory,
)
from figma_to_fgui.fgui_new_project_build import (
    build_new_project,
    validate_project_archive,
)

FIXTURE = Path("tests/fixtures/fgui-new-project")
# This is deliberately a reviewed byte-level oracle, not a second generated file.
GENERIC_ARCHIVE_SHA256 = "bd04f9775d4cdf47ff8df9b830a884e0614b25b682110dbc7932fd3a727425d2"
LAYOUT_TRANSCRIPT = Path(
    "docs/validation/2026-08-31-standard-fgui-package-layout-transcript.json"
)


def test_generic_project_zip_matches_reviewed_byte_golden(tmp_path: Path) -> None:
    plan = _load_plan_v2(FIXTURE / "generic-plan-v2.json")
    config = _load_new_project_config(FIXTURE / "config.json")
    payloads = load_declared_asset_directory(FIXTURE / "assets", plan.resources)

    built = build_new_project(plan, config, payloads, tmp_path)
    content = built.path.read_bytes()

    assert hashlib.sha256(content).hexdigest() == GENERIC_ARCHIVE_SHA256
    assert built.sha256 == GENERIC_ARCHIVE_SHA256
    assert validate_project_archive(built.path, built.manifest) == ()
    with ZipFile(built.path) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == sorted(archive.namelist())
        component = next(
            name
            for name in archive.namelist()
            if name.endswith(".xml") and not name.endswith("package.xml")
        )
        assert b"<image " in archive.read(component)


def test_standard_layout_transcript_matches_rebuilt_archive_members(tmp_path: Path) -> None:
    transcript = json.loads(LAYOUT_TRANSCRIPT.read_text("utf-8"))
    plan = _load_plan_v2(FIXTURE / "generic-plan-v2.json")
    config = _load_new_project_config(FIXTURE / "config.json")
    payloads = load_declared_asset_directory(FIXTURE / "assets", plan.resources)
    built = build_new_project(plan, config, payloads, tmp_path)

    assert transcript["schemaVersion"] == 1
    assert transcript["projectName"] == "GenericWriterFixture"
    assert transcript["editorVersion"] == "6.1.4"
    assert transcript["artifact"] == {
        "byteSize": 1900,
        "sha256": GENERIC_ARCHIVE_SHA256,
    }
    assert transcript["gate"] == {
        "automatedValidation": ["manifest", "xml", "directory", "archive"],
        "editorRoundTrip": "not-rerun",
    }
    assert len(transcript["files"]) == 4
    assert transcript["directories"] == [
        "assets/Generated/Component/",
        "assets/Generated/Img/",
        "assets/Generated/Panel/",
    ]
    assert all(item["match"] is True for item in transcript["files"])
    prefix = f'{transcript["projectName"]}/'
    expected_members = {
        prefix + item["path"]: item["sha256"] for item in transcript["files"]
    }
    with ZipFile(built.path) as archive:
        directory_members = {
            prefix + path for path in transcript["directories"]
        }
        assert set(archive.namelist()) == set(expected_members) | directory_members
        assert {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if not name.endswith("/")
        } == expected_members
    assert transcript["provenance"]["fileHashSource"] == (
        "deterministic-standard-layout-archive-members"
    )
