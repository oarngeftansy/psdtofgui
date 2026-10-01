from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path

_FINAL_SCREENSHOT_NAME = re.compile(
    r"[0-9a-f]{64}-[0-9a-f]{32}\.(?:png|webp)"
)
_TEMPORARY_SCREENSHOT_NAME = re.compile(
    r"\.[0-9a-f]{64}-[A-Za-z0-9_-]+\.tmp"
)


def _regular_non_reparse(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return (
        not path.is_symlink()
        and not attributes & 0x400
        and stat.S_ISREG(metadata.st_mode)
    )


def _semantic_screenshot_root_is_safe(root: Path) -> bool:
    metadata = root.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return (
        not root.is_symlink()
        and not attributes & 0x400
        and stat.S_ISDIR(metadata.st_mode)
        and root.resolve(strict=True) == root.absolute()
    )


def unlink_semantic_screenshot(path: Path, allowed_root: Path) -> bool:
    try:
        if (
            _semantic_screenshot_root_is_safe(allowed_root)
            and _regular_non_reparse(path)
            and path.resolve(strict=True).parent == allowed_root.resolve(strict=True)
        ):
            path.unlink(missing_ok=True)
            return True
    except OSError:
        pass
    return False


def write_semantic_screenshot(
    root: Path,
    job_id: str,
    generation: int,
    suffix: str,
    content: bytes,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if not _semantic_screenshot_root_is_safe(root):
        raise OSError("semantic screenshot directory is unsafe")
    binding = hashlib.sha256(f"{job_id}:{generation}".encode()).hexdigest()
    descriptor = -1
    temporary: Path | None = None
    destination: Path | None = None
    published = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{binding}-", suffix=".tmp", dir=root
        )
        temporary = Path(temporary_name)
        if not _regular_non_reparse(temporary):
            raise OSError("semantic screenshot temporary is unsafe")
        with os.fdopen(descriptor, "wb") as target:
            descriptor = -1
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        destination = root / f"{binding}-{uuid.uuid4().hex}{suffix}"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        reserved = os.open(destination, flags, 0o600)
        os.close(reserved)
        if not _regular_non_reparse(destination):
            raise OSError("semantic screenshot destination is unsafe")
        os.replace(temporary, destination)
        if (
            not _regular_non_reparse(destination)
            or destination.resolve(strict=True).parent != root.resolve(strict=True)
        ):
            raise OSError("semantic screenshot destination is unsafe")
        published = True
        return destination
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            unlink_semantic_screenshot(temporary, root)
        if not published and destination is not None:
            unlink_semantic_screenshot(destination, root)


def sweep_semantic_screenshot_orphans(
    root: Path, protected_paths: Iterable[Path]
) -> int:
    try:
        if not _semantic_screenshot_root_is_safe(root):
            return 0
        resolved_root = root.resolve(strict=True)
        candidates = tuple(root.iterdir())
    except OSError:
        return 0
    protected: set[Path] = set()
    for path in protected_paths:
        try:
            resolved = path.resolve(strict=True)
            if _regular_non_reparse(path) and resolved.parent == resolved_root:
                protected.add(resolved)
        except OSError:
            continue
    removed = 0
    for candidate in candidates:
        if not (
            _FINAL_SCREENSHOT_NAME.fullmatch(candidate.name)
            or _TEMPORARY_SCREENSHOT_NAME.fullmatch(candidate.name)
        ):
            continue
        try:
            if candidate.resolve(strict=True) in protected:
                continue
        except OSError:
            continue
        removed += int(unlink_semantic_screenshot(candidate, root))
    return removed
