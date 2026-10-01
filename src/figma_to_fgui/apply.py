from __future__ import annotations

import base64
import binascii
import hashlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from figma_to_fgui.models import FrozenModel
from figma_to_fgui.service_contracts import ChangeBundle, ChangeFile, FileOperation

_WINDOWS_MAX_PATH = 260
_SHORT_BACKUP_DIRECTORY = ".short"


class ApplyError(RuntimeError):
    pass


class UnsafeTarget(ApplyError):
    pass


class SourceConflict(ApplyError):
    pass


class ApplyFailed(ApplyError):
    def __init__(self, message: str, rollback_succeeded: bool | None = None) -> None:
        super().__init__(message)
        self.rollback_succeeded = rollback_succeeded


class ApplySummary(FrozenModel):
    changed_paths: tuple[str, ...]
    backup_root: str


@dataclass(frozen=True)
class _PreparedFile:
    change: ChangeFile
    target: Path
    payload: bytes
    temporary: Path
    backup: Path


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _io_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    absolute = Path(os.path.abspath(path))
    value = str(absolute)
    if value.startswith("\\\\?\\"):
        return absolute
    if value.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{value[2:]}")
    return Path(f"\\\\?\\{value}")


def _temporary_path(target: Path, job_id: str, relative_path: str) -> Path:
    identity = f"{job_id}\0{relative_path}".encode()
    token = hashlib.sha256(identity).hexdigest()[:16]
    return target.with_name(f".fgui-{token}.tmp")


def _backup_path(backup_root: Path, change_index: int, relative_path: str) -> Path:
    mirrored = backup_root / relative_path
    first_part = Path(relative_path).parts[0]
    reserved = first_part.casefold() == _SHORT_BACKUP_DIRECTORY.casefold()
    if not reserved and len(str(mirrored)) < _WINDOWS_MAX_PATH:
        return mirrored
    token = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]
    return backup_root / _SHORT_BACKUP_DIRECTORY / f"{change_index:08x}-{token}.bak"


def _contained_target(root: Path, relative_path: str) -> Path:
    target = root / relative_path
    resolved = target.resolve(strict=False)
    try:
        if os.path.commonpath((root, resolved)) != str(root):
            raise UnsafeTarget(f"target escapes project root: {relative_path}")
    except ValueError as error:
        raise UnsafeTarget(f"target escapes project root: {relative_path}") from error
    return target


def verify_pre_write(project_root: Path, bundle: ChangeBundle) -> None:
    root = project_root.resolve()
    for change in bundle.files:
        target = _contained_target(root, change.relative_path)
        io_target = _io_path(target)
        if change.operation is FileOperation.CREATE:
            if io_target.exists():
                raise SourceConflict(f"create target already exists: {change.relative_path}")
        elif not io_target.is_file() or _hash(io_target.read_bytes()) != change.before_sha256:
            raise SourceConflict(f"source changed: {change.relative_path}")


def _prepare(project_root: Path, bundle: ChangeBundle) -> tuple[_PreparedFile, ...]:
    root = project_root.resolve()
    verify_pre_write(root, bundle)
    prepared: list[_PreparedFile] = []
    backup_root = root / ".figma-to-fgui" / "backups" / bundle.job_id
    for change_index, change in enumerate(bundle.files):
        target = _contained_target(root, change.relative_path)
        try:
            payload = base64.b64decode(change.content_b64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ApplyFailed(f"invalid content encoding: {change.relative_path}") from error
        if _hash(payload) != change.after_sha256:
            raise ApplyFailed(f"content hash mismatch: {change.relative_path}")
        if target.suffix.lower() == ".xml":
            try:
                etree.fromstring(payload)
            except etree.XMLSyntaxError as error:
                raise ApplyFailed(f"invalid XML: {change.relative_path}") from error

        prepared.append(
            _PreparedFile(
                change=change,
                target=target,
                payload=payload,
                temporary=_temporary_path(target, bundle.job_id, change.relative_path),
                backup=_backup_path(backup_root, change_index, change.relative_path),
            )
        )
    return tuple(prepared)


def _rollback(committed: list[_PreparedFile]) -> bool:
    succeeded = True
    for item in reversed(committed):
        try:
            if item.change.operation is FileOperation.CREATE:
                _io_path(item.target).unlink(missing_ok=True)
            else:
                _io_path(item.target.parent).mkdir(parents=True, exist_ok=True)
                shutil.copy2(_io_path(item.backup), _io_path(item.target))
        except OSError:
            succeeded = False
    return succeeded


def apply_bundle(
    project_root: Path,
    bundle: ChangeBundle,
    replace_file: Callable[[Path, Path], None] = os.replace,
) -> ApplySummary:
    prepared = _prepare(project_root, bundle)
    backup_root = project_root.resolve() / ".figma-to-fgui" / "backups" / bundle.job_id
    committed: list[_PreparedFile] = []
    try:
        _io_path(backup_root).mkdir(parents=True, exist_ok=True)
        for item in prepared:
            _io_path(item.target.parent).mkdir(parents=True, exist_ok=True)
            if item.change.operation is FileOperation.REPLACE:
                _io_path(item.backup.parent).mkdir(parents=True, exist_ok=True)
                shutil.copy2(_io_path(item.target), _io_path(item.backup))
            _io_path(item.temporary.parent).mkdir(parents=True, exist_ok=True)
            _io_path(item.temporary).write_bytes(item.payload)

        for item in prepared:
            replace_file(_io_path(item.temporary), _io_path(item.target))
            committed.append(item)

        for item in prepared:
            if _hash(_io_path(item.target).read_bytes()) != item.change.after_sha256:
                raise OSError(f"final hash mismatch: {item.change.relative_path}")
    except OSError as error:
        rollback_succeeded = _rollback(committed)
        raise ApplyFailed("apply transaction failed", rollback_succeeded) from error
    finally:
        for item in prepared:
            _io_path(item.temporary).unlink(missing_ok=True)

    return ApplySummary(
        changed_paths=tuple(item.change.relative_path for item in prepared),
        backup_root=backup_root.relative_to(project_root.resolve()).as_posix(),
    )
