from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Literal

from lxml import etree
from pydantic import Field

from figma_to_fgui.image_preview import encode_webp_preview
from figma_to_fgui.models import FrozenModel
from figma_to_fgui.paths import safe_relative_path

_IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
_IGNORED_DIRECTORIES = {".figma-to-fgui", ".figma-to-fgui-preview"}
_IGNORED_NAMES = {".DS_Store", "desktop.ini", "Thumbs.db"}
_TEMPORARY_SUFFIXES = (".bak", ".backup", ".temp", ".tmp", "~")


class UploadedFile(FrozenModel):
    relative_path: str
    kind: Literal["xml", "image", "other"]
    size: int = Field(ge=0)
    sha256: str
    package_name: str | None = None
    package_id: str | None = None
    asset_id: str | None = None
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    thumbnail_relative_path: str | None = None
    thumbnail_sha256: str | None = None


class UploadedPackage(FrozenModel):
    name: str
    id: str


class UploadedProjectVersion(FrozenModel):
    project_id: str
    original_name: str
    fingerprint: str
    packages: tuple[UploadedPackage, ...]
    files: tuple[UploadedFile, ...]


def _is_source_path(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    name = parts[-1]
    return not (
        any(part in _IGNORED_DIRECTORIES for part in parts)
        or name in _IGNORED_NAMES
        or name.startswith("~$")
        or name.lower().endswith(_TEMPORARY_SUFFIXES)
    )


def _source_paths(root: Path) -> tuple[str, ...]:
    paths: list[str] = []
    # Prune generated directories before descending into them. Filtering only
    # after ``Path.rglob`` has entered a backup tree races with apply cleanup
    # on Windows and can raise FileNotFoundError for a directory that is not a
    # source path in the first place.
    for current, directories, filenames in os.walk(root, topdown=True):
        directories[:] = [
            name for name in directories if name not in _IGNORED_DIRECTORIES
        ]
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            relative = path.relative_to(root).as_posix()
            if path.is_file() and _is_source_path(relative):
                paths.append(safe_relative_path(relative))
    return tuple(sorted(paths))


def _packages(
    root: Path,
) -> tuple[
    tuple[UploadedPackage, ...],
    dict[str, tuple[str, str]],
    dict[str, tuple[str, str]],
]:
    packages: list[UploadedPackage] = []
    resources: dict[str, tuple[str, str]] = {}
    package_roots: dict[str, tuple[str, str]] = {}
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    manifests = {*root.glob("*/package.xml"), *root.glob("assets/*/package.xml")}
    for manifest in sorted(manifests, key=lambda path: path.parent.name):
        package_name = manifest.parent.name
        package = etree.parse(str(manifest), parser).getroot()
        package_id = str(package.attrib["id"])
        packages.append(UploadedPackage(name=package_name, id=package_id))
        package_root = safe_relative_path(manifest.parent.relative_to(root).as_posix())
        package_roots[package_root] = (package_name, package_id)
        for resource in package.xpath("./resources/*"):
            if str(resource.tag) != "image":
                continue
            path = str(resource.attrib.get("path", "/")).strip("/")
            relative = safe_relative_path(f"{package_root}/{path}/{resource.attrib['name']}")
            resources[relative] = (package_id, str(resource.attrib["id"]))
    return tuple(packages), resources, package_roots


def _thumbnail(root: Path, relative_path: str) -> tuple[int | None, int | None, str | None, str | None]:
    source = root / relative_path
    try:
        rendered = encode_webp_preview(source.read_bytes())
    except OSError:
        rendered = None
    if rendered is None:
        return None, None, None, None
    width, height, preview = rendered
    preview_name = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16] + ".webp"
    relative_thumbnail = f".figma-to-fgui-preview/{preview_name}"
    target = root / relative_thumbnail
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(preview)
    return width, height, relative_thumbnail, hashlib.sha256(target.read_bytes()).hexdigest()


def _fingerprint(root: Path, paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative_path in paths:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256((root / relative_path).read_bytes()).digest())
    return digest.hexdigest()


def index_uploaded_project(root: Path, original_name: str) -> UploadedProjectVersion:
    source_paths = _source_paths(root)
    packages, assets, package_roots = _packages(root)
    files: list[UploadedFile] = []
    for relative_path in source_paths:
        source = root / relative_path
        package_match = next(
            (
                package
                for prefix, package in package_roots.items()
                if relative_path == prefix or relative_path.startswith(f"{prefix}/")
            ),
            None,
        )
        package_name = package_match[0] if package_match else None
        kind: Literal["xml", "image", "other"]
        suffix = source.suffix.lower()
        kind = "xml" if suffix == ".xml" else "image" if suffix in _IMAGE_SUFFIXES else "other"
        package_id = package_match[1] if package_match else None
        asset_id: str | None = None
        width: int | None = None
        height: int | None = None
        thumbnail_relative_path: str | None = None
        thumbnail_sha256: str | None = None
        if kind == "image":
            width, height, thumbnail_relative_path, thumbnail_sha256 = _thumbnail(root, relative_path)
            if relative_path in assets:
                package_id, asset_id = assets[relative_path]
        files.append(
            UploadedFile(
                relative_path=relative_path,
                kind=kind,
                size=source.stat().st_size,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                package_name=package_name,
                package_id=package_id,
                asset_id=asset_id,
                width=width,
                height=height,
                thumbnail_relative_path=thumbnail_relative_path,
                thumbnail_sha256=thumbnail_sha256,
            )
        )
    return UploadedProjectVersion(
        project_id=uuid.uuid4().hex,
        original_name=original_name,
        fingerprint=_fingerprint(root, source_paths),
        packages=packages,
        files=tuple(files),
    )
