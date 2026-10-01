from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from zipfile import BadZipFile, ZipFile, ZipInfo

import py7zr
import rarfile
from lxml import etree

from figma_to_fgui.paths import safe_relative_path


@dataclass(frozen=True)
class UploadLimits:
    max_compressed_bytes: int = 500 * 1024 * 1024
    max_total_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024
    max_entries: int = 20_000
    max_file_bytes: int = 100 * 1024 * 1024
    max_compression_ratio: float = 20.0


class UploadError(RuntimeError):
    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True)
class ExtractedProject:
    root: Path
    files: tuple[str, ...]


_INVALID_PROJECT = "这个压缩包不是有效的 FairyGUI 工程。"
_ARCHIVE_TOO_LARGE = "压缩包过大或包含过多文件，请缩小后重试。"
_UNSAFE_ARCHIVE = "无法安全读取这个压缩包。"
DEFAULT_UPLOAD_LIMITS = UploadLimits()
SUPPORTED_PROJECT_ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz")


def _project_root(files: tuple[str, ...], extracted_root: Path) -> tuple[Path, tuple[str, ...]]:
    package_paths = tuple(path for path in files if path.endswith("/package.xml"))
    if not package_paths:
        raise UploadError("invalid_fgui_project", _INVALID_PROJECT)

    project_markers = tuple(path for path in files if path.lower().endswith(".fairy"))
    if project_markers:
        marker_parents = {PurePosixPath(path).parent.as_posix() for path in project_markers}
        if len(marker_parents) != 1:
            raise UploadError("invalid_fgui_project", _INVALID_PROJECT)
        marker_parent = marker_parents.pop()
        wrapper = "" if marker_parent == "." else marker_parent
        if wrapper and len(PurePosixPath(wrapper).parts) != 1:
            raise UploadError("invalid_fgui_project", _INVALID_PROJECT)
    else:
        package_depths = {len(PurePosixPath(path).parts) - 2 for path in package_paths}
        if package_depths - {0, 1} or len(package_depths) != 1:
            raise UploadError("invalid_fgui_project", _INVALID_PROJECT)
        depth = package_depths.pop()
        wrapper = "" if depth == 0 else PurePosixPath(package_paths[0]).parts[0]
    if wrapper and any(PurePosixPath(path).parts[0] != wrapper for path in files):
        raise UploadError("invalid_fgui_project", _INVALID_PROJECT)

    relative_files = tuple(
        path if not wrapper else "/".join(PurePosixPath(path).parts[1:]) for path in files
    )
    if project_markers and any(
        len(PurePosixPath(path).parts) != 3 or PurePosixPath(path).parts[0] != "assets"
        for path in relative_files
        if path.endswith("/package.xml")
    ):
        raise UploadError("invalid_fgui_project", _INVALID_PROJECT)
    root = extracted_root / wrapper if wrapper else extracted_root
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        for path in relative_files:
            if path.endswith("/package.xml"):
                document = etree.parse(str(root / path), parser)
                if etree.QName(document.getroot()).localname not in {
                    "package",
                    "packageDescription",
                }:
                    raise UploadError("invalid_fgui_project", _INVALID_PROJECT)
    except etree.XMLSyntaxError as error:
        raise UploadError("invalid_fgui_project", _INVALID_PROJECT) from error
    return root, relative_files


def _upload_error(code: str) -> UploadError:
    messages = {
        "archive_too_large": _ARCHIVE_TOO_LARGE,
        "invalid_fgui_project": _INVALID_PROJECT,
        "unsafe_archive": _UNSAFE_ARCHIVE,
    }
    return UploadError(code, messages[code])


def _safe_name(entry_name: str) -> str:
    if "\x00" in entry_name:
        raise _upload_error("unsafe_archive")
    try:
        relative = safe_relative_path(entry_name)
    except ValueError as error:
        raise _upload_error("unsafe_archive") from error
    if relative == ".":
        raise _upload_error("unsafe_archive")
    return relative


def _validate_entries(archive: ZipFile, limits: UploadLimits) -> tuple[tuple[ZipInfo, str], ...]:
    entries = archive.infolist()
    if len(entries) > limits.max_entries:
        raise _upload_error("archive_too_large")

    normalized: set[str] = set()
    total_uncompressed = 0
    total_compressed = 0
    safe_entries: list[tuple[ZipInfo, str]] = []
    for entry in entries:
        original_name = getattr(entry, "orig_filename", entry.filename)
        relative = _safe_name(original_name)
        if relative in normalized or stat.S_ISLNK(entry.external_attr >> 16):
            raise _upload_error("unsafe_archive")
        normalized.add(relative)
        with archive.open(entry):
            pass
        if entry.is_dir():
            continue
        if entry.file_size > limits.max_file_bytes:
            raise _upload_error("archive_too_large")
        total_uncompressed += entry.file_size
        total_compressed += entry.compress_size
        if total_uncompressed > limits.max_total_uncompressed_bytes:
            raise _upload_error("archive_too_large")
        safe_entries.append((entry, relative))

    if total_uncompressed and (
        not total_compressed or total_uncompressed / total_compressed > limits.max_compression_ratio
    ):
        raise _upload_error("archive_too_large")
    return tuple(safe_entries)


def _copy_entry(archive: ZipFile, entry: ZipInfo, target: Path, limits: UploadLimits) -> int:
    written = 0
    with archive.open(entry) as zipped, target.open("wb") as output:
        while chunk := zipped.read(64 * 1024):
            written += len(chunk)
            if written > limits.max_file_bytes:
                raise _upload_error("archive_too_large")
            output.write(chunk)
    return written


def project_archive_suffix(filename: str) -> str | None:
    lowered = Path(filename).name.lower()
    return next(
        (
            suffix
            for suffix in sorted(SUPPORTED_PROJECT_ARCHIVE_SUFFIXES, key=len, reverse=True)
            if lowered.endswith(suffix)
        ),
        None,
    )


class _ReadableEntry(Protocol):
    def read(self, size: int = -1) -> bytes: ...


def _copy_stream(source: _ReadableEntry, target: Path, limits: UploadLimits) -> int:
    written = 0
    with target.open("wb") as output:
        while chunk := source.read(64 * 1024):
            written += len(chunk)
            if written > limits.max_file_bytes:
                raise _upload_error("archive_too_large")
            output.write(chunk)
    return written


def _validate_common_entries(
    entries: list[tuple[object, str, bool, bool, int, int]],
    limits: UploadLimits,
    archive_compressed_bytes: int | None = None,
) -> tuple[tuple[object, str], ...]:
    if len(entries) > limits.max_entries:
        raise _upload_error("archive_too_large")
    normalized: set[str] = set()
    safe_entries: list[tuple[object, str]] = []
    total_uncompressed = 0
    total_compressed = 0
    for entry, name, is_directory, is_link, file_size, compressed_size in entries:
        relative = _safe_name(name)
        normalized_key = relative.casefold()
        if normalized_key in normalized or is_link:
            raise _upload_error("unsafe_archive")
        normalized.add(normalized_key)
        if is_directory:
            continue
        if file_size < 0 or compressed_size < 0 or file_size > limits.max_file_bytes:
            raise _upload_error("archive_too_large")
        total_uncompressed += file_size
        total_compressed += compressed_size
        if total_uncompressed > limits.max_total_uncompressed_bytes:
            raise _upload_error("archive_too_large")
        safe_entries.append((entry, relative))
    compressed_denominator = (
        archive_compressed_bytes if archive_compressed_bytes is not None else total_compressed
    )
    if total_uncompressed and (
        not compressed_denominator
        or total_uncompressed / compressed_denominator > limits.max_compression_ratio
    ):
        raise _upload_error("archive_too_large")
    return tuple(safe_entries)


def _extract_tar(source: Path, temporary: Path, limits: UploadLimits) -> tuple[str, ...]:
    with tarfile.open(source, "r:*") as archive:
        members = archive.getmembers()
        entries = _validate_common_entries(
            [
                (
                    member,
                    member.name,
                    member.isdir(),
                    member.issym() or member.islnk() or not (member.isfile() or member.isdir()),
                    member.size,
                    member.size,
                )
                for member in members
            ],
            limits,
            source.stat().st_size,
        )
        files: list[str] = []
        total_uncompressed = 0
        for item, relative in entries:
            member = item
            if not isinstance(member, tarfile.TarInfo):
                raise _upload_error("unsafe_archive")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise _upload_error("unsafe_archive")
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with extracted:
                total_uncompressed += _copy_stream(extracted, target, limits)
            if total_uncompressed > limits.max_total_uncompressed_bytes:
                raise _upload_error("archive_too_large")
            files.append(relative)
        return tuple(files)


def _extract_7z(source: Path, temporary: Path, limits: UploadLimits) -> tuple[str, ...]:
    with py7zr.SevenZipFile(source, mode="r") as archive:
        information = archive.list()
        entries = _validate_common_entries(
            [
                (
                    item,
                    item.filename,
                    item.is_directory,
                    item.is_symlink or not (item.is_file or item.is_directory),
                    item.uncompressed,
                    item.compressed or item.uncompressed,
                )
                for item in information
            ],
            limits,
            source.stat().st_size,
        )
        archive.extractall(path=temporary)
    return tuple(relative for _, relative in entries)


def _rar_backend() -> str:
    tool = shutil.which("bsdtar") or shutil.which("tar")
    if tool is None:
        raise _upload_error("unsafe_archive")
    return tool


def _extract_rar(source: Path, temporary: Path, limits: UploadLimits) -> tuple[str, ...]:
    with rarfile.RarFile(source) as archive:
        information = archive.infolist()
        entries = _validate_common_entries(
            [
                (
                    item,
                    item.filename,
                    item.is_dir(),
                    item.is_symlink() or not (item.is_file() or item.is_dir()),
                    item.file_size,
                    item.compress_size,
                )
                for item in information
            ],
            limits,
            source.stat().st_size,
        )
    completed = subprocess.run(
        [
            _rar_backend(),
            "-xf",
            str(source),
            "-C",
            str(temporary),
            "--no-same-owner",
            "--no-same-permissions",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise _upload_error("unsafe_archive")
    return tuple(relative for _, relative in entries)


def _verify_extracted_files(temporary: Path, expected: tuple[str, ...]) -> None:
    actual: set[str] = set()
    for path in temporary.rglob("*"):
        metadata = path.lstat()
        attributes = getattr(metadata, "st_file_attributes", 0)
        if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise _upload_error("unsafe_archive")
        if path.is_file():
            relative = path.relative_to(temporary).as_posix()
            actual.add(relative.casefold())
        elif not path.is_dir():
            raise _upload_error("unsafe_archive")
    if actual != {name.casefold() for name in expected}:
        raise _upload_error("unsafe_archive")


def extract_project_archive(
    source: Path,
    destination: Path,
    filename: str | None = None,
    limits: UploadLimits = DEFAULT_UPLOAD_LIMITS,
) -> ExtractedProject:
    suffix = project_archive_suffix(filename or source.name)
    if suffix is None:
        raise _upload_error("unsafe_archive")
    if suffix == ".zip":
        return extract_project_zip(source, destination, limits)

    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        if source.stat().st_size > limits.max_compressed_bytes:
            raise _upload_error("archive_too_large")
        if suffix in {".tar", ".tar.gz", ".tgz"}:
            files = _extract_tar(source, temporary, limits)
        elif suffix == ".7z":
            files = _extract_7z(source, temporary, limits)
        else:
            files = _extract_rar(source, temporary, limits)
        _verify_extracted_files(temporary, files)
        root, relative_files = _project_root(files, temporary)
        relative_root = root.relative_to(temporary)
        os.replace(temporary, destination)
        return ExtractedProject(destination / relative_root, relative_files)
    except UploadError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    except (
        EOFError,
        OSError,
        RuntimeError,
        tarfile.TarError,
        py7zr.Bad7zFile,
        rarfile.Error,
    ) as error:
        shutil.rmtree(temporary, ignore_errors=True)
        raise _upload_error("unsafe_archive") from error


def extract_project_zip(
    source: Path,
    destination: Path,
    limits: UploadLimits = DEFAULT_UPLOAD_LIMITS,
) -> ExtractedProject:
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        if source.stat().st_size > limits.max_compressed_bytes:
            raise _upload_error("archive_too_large")
        files: list[str] = []
        with ZipFile(source) as archive:
            entries = _validate_entries(archive, limits)
            total_uncompressed = 0
            for entry, relative in entries:
                files.append(relative)
                target = temporary / relative
                try:
                    target.resolve(strict=False).relative_to(temporary.resolve())
                except ValueError as error:
                    raise _upload_error("unsafe_archive") from error
                target.parent.mkdir(parents=True, exist_ok=True)
                total_uncompressed += _copy_entry(archive, entry, target, limits)
                if total_uncompressed > limits.max_total_uncompressed_bytes:
                    raise _upload_error("archive_too_large")

        root, relative_files = _project_root(tuple(files), temporary)
        relative_root = root.relative_to(temporary)
        os.replace(temporary, destination)
        return ExtractedProject(destination / relative_root, relative_files)
    except UploadError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    except (BadZipFile, EOFError, OSError, RuntimeError) as error:
        shutil.rmtree(temporary, ignore_errors=True)
        raise _upload_error("unsafe_archive") from error
