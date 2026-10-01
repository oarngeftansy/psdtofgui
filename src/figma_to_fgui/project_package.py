from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from lxml import etree

from figma_to_fgui.apply import apply_bundle
from figma_to_fgui.models import Diagnostic, FrozenModel
from figma_to_fgui.paths import safe_relative_path, validate_project_name
from figma_to_fgui.service_contracts import ChangeBundle
from figma_to_fgui.uploaded_project import index_uploaded_project

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_GENERATED_DIRECTORIES = {".figma-to-fgui", ".figma-to-fgui-preview"}
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class BuiltProjectPackage(FrozenModel):
    path: Path
    download_name: str
    sha256: str
    changed_paths: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


def _is_link(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & _REPARSE_POINT)


def _reject_links(root: Path) -> None:
    if _is_link(root) or any(_is_link(path) for path in root.rglob("*")):
        raise ValueError("project contains a symlink or reparse point")


def write_deterministic_zip(
    project_root: Path,
    target: Path,
    *,
    directory_members: tuple[str, ...] = (),
) -> None:
    """Write a byte-stable archive without mutating the source tree."""
    members = sorted(
        (safe_relative_path(path.relative_to(project_root).as_posix()), path)
        for path in project_root.rglob("*")
        if path.is_file()
    )
    directories: list[str] = []
    for value in directory_members:
        if not isinstance(value, str) or not value.endswith("/") or value.endswith("//"):
            raise ValueError("invalid ZIP directory member")
        directories.append(f"{safe_relative_path(value[:-1])}/")
    if len(directories) != len(set(directories)):
        raise ValueError("duplicate ZIP directory member")
    entries = sorted(
        [(relative_path, source) for relative_path, source in members]
        + [(relative_path, None) for relative_path in directories],
        key=lambda item: item[0],
    )
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for relative_path, source in entries:
            info = ZipInfo(relative_path, date_time=_ZIP_TIMESTAMP)
            info.create_system = 3
            if source is None:
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
                archive.writestr(info, b"")
                continue
            info.compress_type = ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            with (
                source.open("rb") as input_file,
                archive.open(info, "w", force_zip64=True) as output,
            ):
                shutil.copyfileobj(input_file, output, length=64 * 1024)


# Kept for compatibility with the existing update-mode package tests.
_write_deterministic_zip = write_deterministic_zip


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def remove_generated_directories(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and path.name in _GENERATED_DIRECTORIES:
            shutil.rmtree(path)


_remove_generated_directories = remove_generated_directories


def _validate_structure(root: Path, original_name: str) -> None:
    indexed = index_uploaded_project(root, original_name)
    if not indexed.packages:
        raise ValueError("project contains no FairyGUI package")

    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    package_ids: dict[str, set[str]] = {}
    components: list[tuple[Path, str]] = []
    for manifest in sorted(root.glob("*/package.xml"), key=lambda path: path.parent.name):
        document = etree.parse(str(manifest), parser)
        package = document.getroot()
        if etree.QName(package).localname not in {"package", "packageDescription"}:
            raise ValueError(f"invalid package XML: {manifest.parent.name}")
        package_id = str(package.attrib.get("id", ""))
        resources = package.find("resources")
        if not package_id or package_id in package_ids or resources is None:
            raise ValueError(f"invalid package XML: {manifest.parent.name}")
        ids: set[str] = set()
        package_ids[package_id] = ids
        for resource in resources:
            resource_id = str(resource.attrib.get("id", ""))
            name = str(resource.attrib.get("name", ""))
            if not resource_id or resource_id in ids or not name:
                raise ValueError(f"invalid package resource: {manifest.parent.name}")
            ids.add(resource_id)
            relative = safe_relative_path(
                f"{manifest.parent.name}/{str(resource.attrib.get('path', '/')).strip('/')}/{name}"
            )
            target = root / relative
            if not target.is_file():
                raise ValueError(f"missing resource file: {relative}")
            if etree.QName(resource).localname == "component":
                components.append((target, package_id))

    for component_path, package_id in components:
        component = etree.parse(str(component_path), parser).getroot()
        if etree.QName(component).localname != "component":
            raise ValueError(
                f"invalid component XML: {component_path.relative_to(root).as_posix()}"
            )
        for element in component.iter():
            source_id = element.attrib.get("src")
            if source_id is None:
                continue
            source_package = element.attrib.get("pkg", package_id)
            if source_id not in package_ids.get(source_package, set()):
                raise ValueError(f"missing component reference: {source_id}")


def _validate_bundle_paths(bundle: ChangeBundle) -> None:
    for change in bundle.files:
        if _GENERATED_DIRECTORIES.intersection(Path(change.relative_path).parts):
            raise ValueError("bundle changes a generated directory")


def _reject_output_overlap(project_root: Path, output_directory: Path) -> tuple[Path, Path]:
    source = project_root.resolve(strict=True)
    output = output_directory.resolve(strict=False)
    if output == source or source in output.parents:
        raise ValueError("output directory overlaps project root")
    return source, output


def build_project_package(
    project_root: Path,
    bundle: ChangeBundle,
    mode: Literal["create", "update"],
    project_name: str,
    output_directory: Path,
    clock: Callable[[], datetime] = datetime.now,
) -> BuiltProjectPackage:
    if mode not in ("create", "update"):
        raise ValueError("invalid package mode")
    validate_project_name(project_name)
    if not project_root.is_dir():
        raise ValueError("project root must be a directory")
    _reject_links(project_root)
    project_root, output_directory = _reject_output_overlap(project_root, output_directory)
    _validate_bundle_paths(bundle)

    action = "新建" if mode == "create" else "更新"
    download_name = f"{project_name}-Figma{action}-{clock():%Y%m%d-%H%M}.zip"
    output_directory.mkdir(parents=True, exist_ok=True)
    artifact_name = hashlib.sha256(bundle.job_id.encode("utf-8")).hexdigest() + ".zip"
    published = output_directory / artifact_name

    with tempfile.TemporaryDirectory(prefix="project-package-", dir=output_directory) as temporary:
        temporary_root = Path(temporary)
        working = temporary_root / "project"
        shutil.copytree(project_root, working)
        summary = apply_bundle(working, bundle)
        _validate_structure(working, download_name)
        remove_generated_directories(working)

        archive_path = temporary_root / "package.zip"
        write_deterministic_zip(working, archive_path)
        os.replace(archive_path, published)

    return BuiltProjectPackage(
        path=published,
        download_name=download_name,
        sha256=_sha256_file(published),
        changed_paths=summary.changed_paths,
    )
