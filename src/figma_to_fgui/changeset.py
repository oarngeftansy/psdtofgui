import hashlib
from pathlib import Path

from figma_to_fgui.models import ChangeSet, Diagnostic, GeneratedFile, Severity
from figma_to_fgui.paths import safe_relative_path


def hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = safe_relative_path(path.relative_to(root).as_posix())
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build_changeset(
    source_root: Path,
    staging_root: Path,
    diagnostics: tuple[Diagnostic, ...],
) -> ChangeSet:
    files: list[GeneratedFile] = []
    for path in sorted(item for item in staging_root.rglob("*") if item.is_file()):
        payload = path.read_bytes()
        files.append(
            GeneratedFile(
                relative_path=safe_relative_path(path.relative_to(staging_root).as_posix()),
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
            )
        )
    return ChangeSet(
        applicable=not any(item.severity is Severity.ERROR for item in diagnostics),
        source_snapshot_sha256=hash_tree(source_root),
        files=tuple(files),
        diagnostics=diagnostics,
    )
