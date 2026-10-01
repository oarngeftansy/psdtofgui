from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from lxml import etree

from figma_to_fgui.project_package import (
    _sha256_file,
    _write_deterministic_zip,
    build_project_package,
    write_deterministic_zip,
)
from figma_to_fgui.service_contracts import ChangeBundle, ChangeFile, FileOperation


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _project(root: Path) -> Path:
    package = root / "Quiz"
    package.mkdir(parents=True)
    (package / "package.xml").write_text(
        "<package id='quiz'><resources><component id='main' name='Main.xml'/></resources></package>",
        "utf-8",
    )
    (package / "Main.xml").write_text("<component name='old'/>", "utf-8")
    return root


def _bundle(before: bytes, after: bytes, job_id: str = "job-1") -> ChangeBundle:
    return ChangeBundle(
        job_id=job_id,
        project_id="project-1",
        files=(
            ChangeFile(
                operation=FileOperation.REPLACE,
                relative_path="Quiz/Main.xml",
                before_sha256=_digest(before),
                after_sha256=_digest(after),
                content_b64=base64.b64encode(after).decode("ascii"),
            ),
        ),
    )


def _create_bundle(relative_path: str, payload: bytes) -> ChangeBundle:
    return ChangeBundle(
        job_id="job-create",
        project_id="project-1",
        files=(
            ChangeFile(
                operation=FileOperation.CREATE,
                relative_path=relative_path,
                after_sha256=_digest(payload),
                content_b64=base64.b64encode(payload).decode("ascii"),
            ),
        ),
    )


def _fingerprint(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _clock() -> datetime:
    return datetime(2026, 7, 30, 15, 30, tzinfo=UTC)


def _empty_bundle() -> ChangeBundle:
    return ChangeBundle(job_id="job-empty", project_id="project-1", files=())


def test_package_applies_bundle_to_copy_and_keeps_source_unchanged(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    before = _fingerprint(source)
    replacement = b"<component name='new'/>"

    built = build_project_package(
        source,
        _bundle((source / "Quiz/Main.xml").read_bytes(), replacement),
        "update",
        "Quiz",
        tmp_path / "out",
        _clock,
    )

    assert _fingerprint(source) == before
    with ZipFile(built.path) as archive:
        assert etree.fromstring(archive.read("Quiz/Main.xml")).attrib["name"] == "new"
        assert etree.fromstring(archive.read("Quiz/package.xml")).tag == "package"
    assert built.download_name == "Quiz-Figma更新-20260730-1530.zip"
    assert built.changed_paths == ("Quiz/Main.xml",)


def test_create_name_sha256_and_diagnostics_match_published_archive(tmp_path: Path) -> None:
    built = build_project_package(
        _project(tmp_path / "source"),
        _empty_bundle(),
        "create",
        "Quiz",
        tmp_path / "out",
        _clock,
    )

    assert built.download_name == "Quiz-Figma新建-20260730-1530.zip"
    assert built.path.name != built.download_name
    assert built.path.suffix == ".zip"
    assert built.sha256 == hashlib.sha256(built.path.read_bytes()).hexdigest()
    assert built.changed_paths == ()
    assert built.diagnostics == ()


def test_zip_is_deterministic_and_excludes_generated_directories(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    (source / ".figma-to-fgui" / "backups" / "old").mkdir(parents=True)
    (source / ".figma-to-fgui" / "backups" / "old" / "Main.xml").write_text("old", "utf-8")
    (source / "Quiz" / ".figma-to-fgui-preview").mkdir()
    (source / "Quiz" / ".figma-to-fgui-preview" / "preview.webp").write_bytes(b"preview")

    first = build_project_package(
        source, _empty_bundle(), "update", "Quiz", tmp_path / "first", _clock
    )
    second = build_project_package(
        source, _empty_bundle(), "update", "Quiz", tmp_path / "second", _clock
    )

    assert first.path.read_bytes() == second.path.read_bytes()
    with ZipFile(first.path) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert {item.date_time for item in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert not any(".figma-to-fgui" in name for name in archive.namelist())
    assert (source / ".figma-to-fgui/backups/old/Main.xml").read_text("utf-8") == "old"
    assert (source / "Quiz/.figma-to-fgui-preview/preview.webp").read_bytes() == b"preview"


def test_deterministic_zip_writes_only_explicit_directory_members(tmp_path: Path) -> None:
    root = tmp_path / "Project"
    (root / "assets/Shop/Panel").mkdir(parents=True)
    (root / "Shop.fairy").write_bytes(b"marker")
    target = tmp_path / "project.zip"

    write_deterministic_zip(
        root,
        target,
        directory_members=(
            "assets/Shop/Component/",
            "assets/Shop/Img/",
            "assets/Shop/Panel/",
        ),
    )

    with ZipFile(target) as archive:
        names = archive.namelist()
        assert "assets/Shop/Component/" in names
        assert "assets/Shop/Img/" in names
        assert "assets/Shop/Panel/" in names
        assert archive.getinfo("assets/Shop/Img/").is_dir()


def test_invalid_project_fails_without_publishing_partial_zip(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    (source / "Quiz" / "package.xml").write_text("<package>", "utf-8")
    output = tmp_path / "out"

    with pytest.raises((etree.XMLSyntaxError, ValueError)):
        build_project_package(source, _empty_bundle(), "update", "Quiz", output, _clock)

    assert not list(output.glob("*.zip"))


@pytest.mark.parametrize("project_name", ("../Quiz", "Quiz/Other", "", "x" * 65))
def test_unsafe_project_name_is_rejected(project_name: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid project name"):
        build_project_package(
            _project(tmp_path / "source"),
            _empty_bundle(),
            "create",
            project_name,
            tmp_path / "out",
            _clock,
        )

    assert not (tmp_path / "out").exists()


def test_source_symlink_is_rejected_without_reading_outside_project(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    outside = tmp_path / "outside.txt"
    outside.write_text("private", "utf-8")
    link = source / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Windows user cannot create symlinks")

    with pytest.raises(ValueError, match="symlink"):
        build_project_package(source, _empty_bundle(), "update", "Quiz", tmp_path / "out", _clock)

    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("output_name", (".", "downloads"))
def test_output_directory_must_not_overlap_source(output_name: str, tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    before = _fingerprint(source)
    output = source if output_name == "." else source / output_name

    with pytest.raises(ValueError, match="overlap"):
        build_project_package(source, _empty_bundle(), "update", "Quiz", output, _clock)

    assert _fingerprint(source) == before
    assert not (source / "downloads").exists()


def test_same_minute_jobs_keep_unique_stable_artifacts(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    old = (source / "Quiz/Main.xml").read_bytes()
    first = build_project_package(
        source,
        _bundle(old, b"<component name='first'/>", "job-first"),
        "update",
        "Quiz",
        tmp_path / "out",
        _clock,
    )
    first_bytes = first.path.read_bytes()
    second = build_project_package(
        source,
        _bundle(old, b"<component name='second'/>", "job-second"),
        "update",
        "Quiz",
        tmp_path / "out",
        _clock,
    )

    assert first.download_name == second.download_name
    assert first.path != second.path
    assert first.path.read_bytes() == first_bytes
    assert first.sha256 == hashlib.sha256(first_bytes).hexdigest()
    assert second.sha256 != first.sha256


def test_missing_package_resource_fails_without_publishing(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    (source / "Quiz/Main.xml").unlink()

    with pytest.raises(ValueError, match="missing resource"):
        build_project_package(source, _empty_bundle(), "update", "Quiz", tmp_path / "out", _clock)

    assert not list((tmp_path / "out").glob("*.zip"))


def test_bundle_created_non_component_xml_fails_post_apply(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    package = source / "Quiz/package.xml"
    package.write_text(
        "<package id='quiz'><resources><component id='bad' name='Bad.xml'/></resources></package>",
        "utf-8",
    )

    with pytest.raises(ValueError, match="component XML"):
        build_project_package(
            source,
            _create_bundle("Quiz/Bad.xml", b"<package/>"),
            "update",
            "Quiz",
            tmp_path / "out",
            _clock,
        )

    assert not list((tmp_path / "out").glob("*.zip"))


def test_missing_component_resource_reference_fails_validation(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")
    (source / "Quiz/Main.xml").write_text(
        "<component><displayList><image src='missing'/></displayList></component>", "utf-8"
    )

    with pytest.raises(ValueError, match="missing component reference"):
        build_project_package(source, _empty_bundle(), "update", "Quiz", tmp_path / "out", _clock)

    assert not list((tmp_path / "out").glob("*.zip"))


def test_bundle_cannot_change_generated_directory_that_is_removed(tmp_path: Path) -> None:
    source = _project(tmp_path / "source")

    with pytest.raises(ValueError, match="generated directory"):
        build_project_package(
            source,
            _create_bundle(".figma-to-fgui-preview/new.webp", b"preview"),
            "update",
            "Quiz",
            tmp_path / "out",
            _clock,
        )

    assert not (tmp_path / "out").exists()


def test_zip_and_sha_helpers_stream_without_path_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "file.bin").write_bytes(b"payload")
    archive = tmp_path / "archive.zip"
    monkeypatch.setattr(Path, "read_bytes", lambda _path: pytest.fail("read_bytes used"))

    _write_deterministic_zip(project, archive)
    digest = _sha256_file(archive)

    assert len(digest) == 64


def test_windows_reparse_point_is_rejected_without_symlink_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _project(tmp_path / "source")
    linked = source / "linked.xml"
    linked.write_text("linked", "utf-8")
    original_lstat = Path.lstat
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path: (
            SimpleNamespace(st_file_attributes=0x400)
            if path.name == "linked.xml"
            else original_lstat(path)
        ),
    )

    with pytest.raises(ValueError, match="symlink or reparse"):
        build_project_package(source, _empty_bundle(), "update", "Quiz", tmp_path / "out", _clock)

    assert not (tmp_path / "out").exists()
