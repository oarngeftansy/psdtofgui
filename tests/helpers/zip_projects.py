from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from warnings import catch_warnings, simplefilter
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def write_project_zip(
    path: Path,
    entries: Mapping[str, bytes] | Sequence[tuple[str, bytes]],
    *,
    symlinks: Sequence[str] = (),
) -> Path:
    with catch_warnings():
        simplefilter("ignore", UserWarning)
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in entries.items() if isinstance(entries, Mapping) else entries:
                archive.writestr(name, content)
            for name in symlinks:
                entry = ZipInfo(name)
                entry.external_attr = 0o120777 << 16
                archive.writestr(entry, b"target")
    return path


def write_nul_name_zip(path: Path) -> Path:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("bad1.xml", b"x")
    path.write_bytes(path.read_bytes().replace(b"bad1.xml", b"bad\x00.xml", 1))
    return path


def write_nul_directory_zip(path: Path, entries: Mapping[str, bytes]) -> Path:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("bad1-dir/", b"")
        for name, content in entries.items():
            archive.writestr(name, content)
    path.write_bytes(path.read_bytes().replace(b"bad1-dir/", b"bad\x00-dir/", 1))
    return path
