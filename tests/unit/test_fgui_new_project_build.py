from __future__ import annotations

import hashlib
import traceback
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

import figma_to_fgui.fgui_new_project_build as builder
from figma_to_fgui.fgui_new_project_build import NewProjectBuildError, build_new_project
from figma_to_fgui.fgui_new_project_models import AssetPayloadSet, NewProjectConfig
from figma_to_fgui.fgui_plan_models import FGUIPlanDocument
from figma_to_fgui.models import Diagnostic, Severity


def _plan() -> FGUIPlanDocument:
    return FGUIPlanDocument.model_validate(
        {
            "documentId": "plan:build",
            "sourceUirSha256": "a" * 64,
            "profileVersion": "fgui-6.1.4-v1",
            "ruleVersion": 1,
            "bindable": True,
            "roots": ["plan:root"],
            "nodes": {
                "plan:root": {
                    "id": "plan:root",
                    "uirNodeRef": "uir:root",
                    "children": [],
                    "zIndex": 0,
                    "type": "container",
                    "transform": {"bounds": {"x": 0, "y": 0, "width": 320, "height": 180}},
                    "decisionRef": "decision:root",
                }
            },
            "resources": {},
            "decisions": {
                "uir:root": {
                    "id": "decision:root",
                    "nodeRef": "uir:root",
                    "status": "native",
                    "ruleId": "fgui.native.container",
                    "ruleVersion": 1,
                    "evidence": ["fixture.root"],
                }
            },
        }
    )


CONFIG = NewProjectConfig(
    projectName="BuildDemo",
    packageName="Generated",
    fairyGuiVersion="6.1.4",
    publishTarget="unity",
)
PAYLOADS = AssetPayloadSet.from_items(())


def _build(output: Path):
    return build_new_project(_plan(), CONFIG, PAYLOADS, output)


def test_repeated_builds_are_byte_identical(tmp_path: Path) -> None:
    first = _build(tmp_path / "one")
    second = _build(tmp_path / "two")

    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == second.path.read_bytes()
    assert first.sha256 == hashlib.sha256(first.path.read_bytes()).hexdigest()


def test_windows_long_output_path_keeps_staging_outside_build_directory(tmp_path: Path) -> None:
    output = tmp_path / ("candidate-" + "a" * 44) / ("build-" + "b" * 32)

    built = _build(output)

    assert built.path.parent == output
    assert built.path.is_file()


def test_published_archive_reopens_through_both_project_gates(tmp_path: Path) -> None:
    built = _build(tmp_path / "out")

    assert built.project_name == "BuildDemo"
    assert built.download_name == "BuildDemo-FairyGUI.zip"
    assert builder.validate_project_archive(built.path, built.manifest) == ()


def test_build_preserves_all_standard_package_directories_when_empty(tmp_path: Path) -> None:
    built = _build(tmp_path / "out")

    with ZipFile(built.path) as archive:
        names = set(archive.namelist())

    prefix = f"{built.project_name}/{built.manifest.package.relative_path}"
    assert f"{prefix}/Component/" in names
    assert f"{prefix}/Img/" in names
    assert f"{prefix}/Panel/" in names


def test_directory_gate_rejects_undeclared_file(tmp_path: Path) -> None:
    built = _build(tmp_path / "out")
    with ZipFile(built.path) as archive:
        archive.extractall(tmp_path / "reopened")
    project_root = tmp_path / "reopened/BuildDemo"
    (project_root / "undeclared.txt").write_bytes(b"not declared")

    diagnostics = builder.validate_project_directory(project_root, built.manifest)

    assert [item.code for item in diagnostics] == ["fgui.writer.project.file_closure_invalid"]


def test_directory_gate_rejects_undeclared_empty_directory(tmp_path: Path) -> None:
    built = _build(tmp_path / "out")
    with ZipFile(built.path) as archive:
        archive.extractall(tmp_path / "reopened")
    project_root = tmp_path / "reopened/BuildDemo"
    (project_root / "undeclared").mkdir()

    diagnostics = builder.validate_project_directory(project_root, built.manifest)

    assert [item.code for item in diagnostics] == ["fgui.writer.project.file_closure_invalid"]


@pytest.mark.parametrize("unsafe_name", ("../escape.txt", "BuildDemo/../escape.txt"))
def test_archive_gate_rejects_traversal_member(tmp_path: Path, unsafe_name: str) -> None:
    built = _build(tmp_path / "out")
    malicious = tmp_path / "malicious.zip"
    with ZipFile(built.path) as source, ZipFile(malicious, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr(unsafe_name, b"escape")

    diagnostics = builder.validate_project_archive(malicious, built.manifest)

    assert [item.code for item in diagnostics] == ["fgui.writer.project.archive_invalid"]


def test_archive_gate_rejects_duplicate_members(tmp_path: Path) -> None:
    built = _build(tmp_path / "out")
    duplicate = tmp_path / "duplicate.zip"
    with ZipFile(built.path) as source, ZipFile(duplicate, "w", compression=ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item)
            target.writestr(item, content)
            if item.filename.endswith(".fairy"):
                repeated = ZipInfo(item.filename)
                repeated.compress_type = ZIP_DEFLATED
                with pytest.warns(UserWarning, match="Duplicate name"):
                    target.writestr(repeated, content)

    diagnostics = builder.validate_project_archive(duplicate, built.manifest)

    assert [item.code for item in diagnostics] == ["fgui.writer.project.archive_invalid"]


@pytest.mark.parametrize("mutation", ("missing", "lowercase", "regular-mode"))
def test_archive_gate_rejects_malformed_standard_directory_members(
    tmp_path: Path, mutation: str
) -> None:
    built = _build(tmp_path / "out")
    malformed = tmp_path / f"{mutation}.zip"
    expected = f"{built.project_name}/{built.manifest.package.relative_path}/Img/"
    with ZipFile(built.path) as source, ZipFile(malformed, "w") as target:
        for item in source.infolist():
            if item.filename != expected:
                target.writestr(item, source.read(item))
                continue
            if mutation == "missing":
                continue
            replacement = ZipInfo(
                expected.replace("/Img/", "/img/")
                if mutation == "lowercase"
                else expected
            )
            replacement.create_system = 3
            replacement.external_attr = (
                (0o40755 if mutation == "lowercase" else 0o100644) << 16
            )
            target.writestr(replacement, b"")

    diagnostics = builder.validate_project_archive(malformed, built.manifest)

    assert [item.code for item in diagnostics] == ["fgui.writer.project.archive_invalid"]


def test_archive_gate_rejects_unix_symlink_member(tmp_path: Path) -> None:
    built = _build(tmp_path / "out")
    malicious = tmp_path / "symlink.zip"
    with ZipFile(built.path) as source, ZipFile(malicious, "w") as target:
        marker_content = b""
        for item in source.infolist():
            content = source.read(item)
            if item.filename.endswith(".fairy"):
                marker_content = content
            else:
                target.writestr(item, content)
        link = ZipInfo("BuildDemo/BuildDemo.fairy")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        target.writestr(link, marker_content)

    diagnostics = builder.validate_project_archive(malicious, built.manifest)

    assert [item.code for item in diagnostics] == ["fgui.writer.project.archive_invalid"]


@pytest.mark.parametrize(
    ("boundary", "attribute"),
    [
        ("input", "validate_asset_payloads"),
        ("manifest", "compile_new_project_manifest"),
        ("xml", "validate_xml_files"),
        ("directory-write", "write_declared_files"),
        ("directory", "validate_project_directory"),
        ("zip-write", "write_deterministic_zip"),
        ("archive", "validate_project_archive"),
        ("publish", "atomic_publish"),
    ],
)
def test_failure_at_each_boundary_never_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    attribute: str,
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    unrelated = output / "keep.txt"
    unrelated.write_bytes(b"keep")

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("private injected detail")

    monkeypatch.setattr(builder, attribute, fail)
    with pytest.raises(NewProjectBuildError) as caught:
        _build(output)

    assert caught.value.diagnostics[0].code == f"fgui.writer.build.{boundary}_failed"
    assert "private injected detail" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "private injected detail" not in "".join(
        traceback.format_exception(caught.value)
    )
    assert unrelated.read_bytes() == b"keep"
    assert list(output.glob("*.zip")) == []


def test_failed_rebuild_preserves_previous_published_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    original = _build(output)
    before = original.path.read_bytes()
    monkeypatch.setattr(
        builder,
        "validate_project_archive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fail")),
    )

    with pytest.raises(NewProjectBuildError):
        _build(output)

    assert original.path.read_bytes() == before


def test_publish_is_the_last_fallible_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = False
    original_publish = builder.atomic_publish
    original_hash = builder._sha256_file

    def track_publish(*args: object, **kwargs: object) -> Path:
        nonlocal published
        result = original_publish(*args, **kwargs)  # type: ignore[arg-type]
        published = True
        return result

    def reject_post_publish_hash(path: Path) -> str:
        assert not published, "archive hash attempted after publish"
        return original_hash(path)

    monkeypatch.setattr(builder, "atomic_publish", track_publish)
    monkeypatch.setattr(builder, "_sha256_file", reject_post_publish_hash)

    built = _build(tmp_path / "out")

    assert published
    assert built.sha256 == hashlib.sha256(built.path.read_bytes()).hexdigest()
    assert built.byte_size == built.path.stat().st_size


def test_output_directory_link_is_rejected_before_build(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("Windows user cannot create directory symlinks")

    with pytest.raises(NewProjectBuildError) as caught:
        _build(linked)

    assert caught.value.diagnostics[0].code == "fgui.writer.build.output_invalid"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert list(real.iterdir()) == []


def test_output_failure_closes_private_exception_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "out"
    marker = "private-output-marker"
    original_mkdir = Path.mkdir

    def fail_output_mkdir(path: Path, *args: object, **kwargs: object) -> None:
        if path == output:
            raise RuntimeError(marker)
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", fail_output_mkdir)

    with pytest.raises(NewProjectBuildError) as caught:
        _build(output)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert marker not in "".join(traceback.format_exception(caught.value))


def test_hostile_existing_build_error_is_reconstructed_from_static_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "private-hostile-build-error-marker"
    hostile = NewProjectBuildError(
        (
            Diagnostic(
                code="fgui.writer.build.xml_failed",
                severity=Severity.ERROR,
                message=marker,
                evidence=(marker,),
                blocks_binding=True,
            ),
        )
    )

    def raise_hostile(*_args: object, **_kwargs: object) -> object:
        try:
            raise RuntimeError(marker)
        except RuntimeError:
            raise hostile

    monkeypatch.setattr(builder, "validate_xml_files", raise_hostile)

    with pytest.raises(NewProjectBuildError) as caught:
        _build(tmp_path / "out")

    assert caught.value is not hostile
    assert caught.value.diagnostics == (builder._diagnostic("xml"),)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert marker not in "".join(traceback.format_exception(caught.value))


def test_temporary_cleanup_failure_is_also_a_closed_public_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "private-cleanup-marker"
    original_temporary_directory = builder.TemporaryDirectory

    class FailingCleanup:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._delegate = original_temporary_directory(*args, **kwargs)  # type: ignore[arg-type]

        def __enter__(self) -> str:
            return self._delegate.__enter__()

        def __exit__(self, *args: object) -> None:
            self._delegate.__exit__(*args)  # type: ignore[arg-type]
            raise RuntimeError(marker)

    monkeypatch.setattr(builder, "TemporaryDirectory", FailingCleanup)

    with pytest.raises(NewProjectBuildError) as caught:
        _build(tmp_path / "out")

    assert caught.value.diagnostics[0].code == "fgui.writer.build.directory-write_failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert marker not in "".join(traceback.format_exception(caught.value))
    assert list((tmp_path / "out").glob("*.zip")) == []
    assert list((tmp_path / "out").glob("*.tmp")) == []


def test_project_tree_uses_short_system_temporary_root_before_atomic_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    original_temporary_directory = builder.TemporaryDirectory

    def recording_temporary_directory(*args: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))
        return original_temporary_directory(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builder, "TemporaryDirectory", recording_temporary_directory)

    built = _build(tmp_path / "deep" / "candidate" / "out")

    assert built.path.is_file()
    assert calls
    assert "dir" not in calls[0]


def test_staging_double_failure_attempts_fd_and_path_cleanup_without_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    marker = "private-stage-double-failure"
    staged = output / ".candidate.tmp"
    close_attempts = 0
    unlink_attempts = 0
    original_unlink = Path.unlink

    def fake_mkstemp(**_kwargs: object) -> tuple[int, str]:
        staged.write_bytes(b"reserved")
        return 987654, str(staged)

    def fail_close(_descriptor: int) -> None:
        nonlocal close_attempts
        close_attempts += 1
        raise RuntimeError(marker)

    def fail_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal unlink_attempts
        if path == staged:
            unlink_attempts += 1
            raise RuntimeError(marker)
        original_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builder, "mkstemp", fake_mkstemp)
    monkeypatch.setattr(builder.os, "close", fail_close)
    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(NewProjectBuildError) as caught:
        _build(output)

    assert caught.value.diagnostics == (builder._diagnostic("zip-write"),)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert marker not in "".join(traceback.format_exception(caught.value))
    assert close_attempts >= 2
    assert unlink_attempts == 1
    assert list(output.glob("*.zip")) == []

    monkeypatch.undo()
    original_unlink(staged)
