"""Five-gate builder for fresh, deterministic FairyGUI project archives."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory, mkstemp
from typing import TypeVar

from pydantic import Field

from figma_to_fgui.fgui_asset_payloads import ValidatedAssetPayload, validate_asset_payloads
from figma_to_fgui.fgui_new_project_compile import compile_new_project_manifest
from figma_to_fgui.fgui_new_project_models import (
    AssetPayloadSet,
    NewProjectConfig,
    NewProjectManifest,
)
from figma_to_fgui.fgui_new_project_validate import (
    required_package_directories,
    validate_project_archive,
    validate_project_directory,
    validate_xml_files,
)
from figma_to_fgui.fgui_plan_models import FGUIPlanDocument
from figma_to_fgui.fgui_xml_dialect_614 import serialize_project_files
from figma_to_fgui.filesystem_paths import io_path
from figma_to_fgui.models import Diagnostic, FrozenModel, Severity
from figma_to_fgui.project_package import _sha256_file, write_deterministic_zip

_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_GateResult = TypeVar("_GateResult")
_PUBLIC_BOUNDARIES = frozenset(
    {
        "input",
        "manifest",
        "xml",
        "directory-write",
        "directory",
        "zip-write",
        "archive",
        "publish",
        "output_invalid",
    }
)


class BuiltNewProject(FrozenModel):
    """One validated archive that has crossed the atomic publish boundary."""

    path: Path
    download_name: str
    sha256: str
    byte_size: int
    project_name: str
    manifest: NewProjectManifest
    diagnostics: tuple[Diagnostic, ...] = ()
    source_node_ids: dict[str, str] = Field(default_factory=dict)
    plan: FGUIPlanDocument | None = Field(default=None, exclude=True)
    source_resource_keys: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_verified(
        cls,
        path: Path,
        manifest: NewProjectManifest,
        *,
        sha256: str,
        byte_size: int,
    ) -> BuiltNewProject:
        return cls(
            path=path,
            download_name=f"{manifest.project.project_name}-FairyGUI.zip",
            sha256=sha256,
            byte_size=byte_size,
            project_name=manifest.project.project_name,
            manifest=manifest,
        )


class NewProjectBuildError(Exception):
    """A build boundary failed; diagnostics never expose private exception details."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("New FairyGUI project build failed.")


def _diagnostic(boundary: str) -> Diagnostic:
    return Diagnostic(
        code=f"fgui.writer.build.{boundary}_failed",
        severity=Severity.ERROR,
        message="A new-project build gate failed.",
        rule_id=f"fgui.writer.build.{boundary}_failed",
        rule_version=1,
        evidence=(f"gate={boundary}",),
        suggested_action="Repair the build input or destination and retry.",
        blocks_binding=True,
    )


def _fail(boundary: str) -> NewProjectBuildError:
    return NewProjectBuildError((_diagnostic(boundary),))


def _validated_failure_boundary(error: NewProjectBuildError, fallback: str) -> str:
    """Use only an exact allow-listed code from an existing public error."""
    try:
        diagnostics = error.diagnostics
        if type(diagnostics) is not tuple or len(diagnostics) != 1:
            return fallback
        diagnostic = diagnostics[0]
        if type(diagnostic) is not Diagnostic or type(diagnostic.code) is not str:
            return fallback
        prefix = "fgui.writer.build."
        suffix = "_failed"
        code = diagnostic.code
        if not code.startswith(prefix) or not code.endswith(suffix):
            return fallback
        boundary = code[len(prefix) : -len(suffix)]
        return boundary if boundary in _PUBLIC_BOUNDARIES else fallback
    except Exception:  # noqa: BLE001 - hostile public-error objects fail closed.
        return fallback


def _run_gate(boundary: str, operation: Callable[[], _GateResult]) -> _GateResult:
    """Close exception chaining before a public build error crosses the boundary."""
    failure: NewProjectBuildError | None = None
    try:
        return operation()
    except NewProjectBuildError as error:
        failure = _fail(_validated_failure_boundary(error, boundary))
    except Exception:  # noqa: BLE001 - public gate closes operational exception details.
        failure = _fail(boundary)
    if failure is not None:
        raise failure from None
    raise AssertionError("unreachable build gate state")


def _require_clean(diagnostics: tuple[Diagnostic, ...]) -> None:
    if diagnostics:
        raise ValueError("build gate returned diagnostics")


def _is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & _REPARSE_POINT)


def _prepare_output_directory(output_directory: Path) -> Path:
    failure: NewProjectBuildError | None = None
    try:
        if output_directory.exists() or output_directory.is_symlink():
            if not output_directory.is_dir() or _is_link_or_reparse(output_directory):
                raise OSError
        else:
            output_directory.mkdir(parents=True)
        return output_directory.resolve(strict=True)
    except Exception:  # noqa: BLE001 - output preparation is a public boundary.
        failure = _fail("output_invalid")
    if failure is not None:
        raise failure from None
    raise AssertionError("unreachable output preparation state")


def write_declared_files(
    temporary: Path,
    manifest: NewProjectManifest,
    files: dict[str, bytes],
    assets: tuple[ValidatedAssetPayload, ...],
) -> Path:
    """Materialize only serializer-declared files beneath one fresh project root."""
    del assets
    project_root = temporary / manifest.project.project_name
    project_root.mkdir()
    try:
        for relative in required_package_directories(manifest):
            project_root.joinpath(*PurePosixPath(relative).parts).mkdir(
                parents=True, exist_ok=True
            )
        for relative, content in files.items():
            parts = relative.split("/")
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise OSError
            target = project_root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as destination:
                destination.write(content)
    except (OSError, ValueError) as error:
        raise RuntimeError("declared file write failed") from error
    return project_root


def atomic_publish(candidate: Path, output_directory: Path, manifest: NewProjectManifest) -> Path:
    """Publish the validated candidate in one filesystem replacement."""
    del manifest
    # Each build owns its output directory.  A fixed short storage name keeps
    # the returned ordinary Path usable by tools that still apply MAX_PATH;
    # the user-facing download name and the full integrity hash remain separate.
    published = output_directory / "artifact.zip"
    filesystem_target = io_path(published)
    if filesystem_target.exists() and _is_link_or_reparse(filesystem_target):
        raise OSError("publish target is a link")
    os.replace(io_path(candidate), filesystem_target)
    return published


def _stage_validated_candidate(candidate: Path, output_directory: Path) -> Path:
    descriptor: int | None = None
    staged: Path | None = None
    failed = False
    try:
        descriptor, raw_path = mkstemp(
            prefix=".fgui-new-project-", suffix=".tmp", dir=io_path(output_directory)
        )
        staged = Path(raw_path)
        os.close(descriptor)
        descriptor = None
        os.replace(io_path(candidate), io_path(staged))
    except Exception:  # noqa: BLE001 - caller converts the static failure publicly.
        failed = True
    finally:
        if failed and descriptor is not None:
            try:
                os.close(descriptor)
            except Exception:  # noqa: BLE001,S110 - continue to path cleanup.
                pass
        if failed and staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001,S110 - staging was never published.
                pass
    if failed or staged is None:
        raise RuntimeError("validated candidate staging failed") from None
    return staged


def build_new_project(
    plan: FGUIPlanDocument,
    config: NewProjectConfig,
    payloads: AssetPayloadSet,
    output_directory: Path,
    *,
    source_names: Mapping[str, str] | None = None,
) -> BuiltNewProject:
    """Validate, build, reopen, and atomically publish one fresh project ZIP."""
    output = _prepare_output_directory(output_directory)
    assets = _run_gate("input", lambda: validate_asset_payloads(plan.resources, payloads))
    manifest = _run_gate(
        "manifest",
        lambda: compile_new_project_manifest(
            plan, config, assets, source_names=source_names
        ),
    )

    def serialize_and_validate() -> dict[str, bytes]:
        files = serialize_project_files(manifest, assets)
        _require_clean(validate_xml_files(files))
        return files

    files = _run_gate("xml", serialize_and_validate)

    staged_candidate: Path | None = None

    def prepare_candidate() -> tuple[Path, str, int]:
        nonlocal staged_candidate
        # Expand the editable project tree under the system's short temporary
        # root. API artifact directories are already deep enough that readable
        # source names can otherwise cross the legacy Windows path boundary.
        # The validated ZIP is still staged back into ``output`` before the
        # final same-directory atomic publication.
        with TemporaryDirectory(prefix="fgui-new-project-") as raw:
            temporary = Path(raw)
            project_root = _run_gate(
                "directory-write",
                lambda: write_declared_files(temporary, manifest, files, assets),
            )
            _run_gate(
                "directory",
                lambda: _require_clean(validate_project_directory(project_root, manifest)),
            )
            candidate = temporary / "candidate.zip"
            directory_members = tuple(
                f"{manifest.project.project_name}/{path}/"
                for path in required_package_directories(manifest)
            )
            _run_gate(
                "zip-write",
                lambda: write_deterministic_zip(
                    temporary,
                    candidate,
                    directory_members=directory_members,
                ),
            )

            def validate_and_measure_archive() -> tuple[str, int]:
                _require_clean(validate_project_archive(candidate, manifest))
                return _sha256_file(candidate), candidate.stat().st_size

            archive_sha256, archive_size = _run_gate("archive", validate_and_measure_archive)
            staged_candidate = _run_gate(
                "zip-write", lambda: _stage_validated_candidate(candidate, output)
            )
        return staged_candidate, archive_sha256, archive_size

    try:
        staged, archive_sha256, archive_size = _run_gate(
            "directory-write", prepare_candidate
        )
    except NewProjectBuildError:
        if staged_candidate is not None:
            try:
                staged_candidate.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001,S110 - preserve the closed failure.
                pass
        raise
    try:
        published = _run_gate("publish", lambda: atomic_publish(staged, output, manifest))
    except NewProjectBuildError:
        try:
            staged.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001,S110 - preserve the closed failure.
            pass
        raise
    return BuiltNewProject.from_verified(
        published,
        manifest,
        sha256=archive_sha256,
        byte_size=archive_size,
    )
