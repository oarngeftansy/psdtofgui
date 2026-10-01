import struct
from pathlib import Path

from lxml import etree
from pydantic import Field

from figma_to_fgui.models import FrozenModel, NineSliceGrid, ProjectResource
from figma_to_fgui.paths import safe_relative_path


class ProjectIndex(FrozenModel):
    packages: dict[str, str] = Field(default_factory=dict)
    package_roots: dict[str, str] = Field(default_factory=dict)
    by_name: dict[str, ProjectResource] = Field(default_factory=dict)
    resources_by_package: dict[str, dict[str, ProjectResource]] = Field(default_factory=dict)
    ids_by_package: dict[str, frozenset[str]] = Field(default_factory=dict)
    font_aliases: dict[str, tuple[ProjectResource, ...]] = Field(default_factory=dict)


def normalize_font_name(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _scale9grid(value: str | None) -> NineSliceGrid | None:
    if value is None:
        return None
    parts = value.split(",")
    if len(parts) != 4 or any(not part.strip().isdigit() for part in parts):
        return None
    x, y, width, height = (int(part.strip()) for part in parts)
    if width <= 0 or height <= 0:
        return None
    return NineSliceGrid(x=x, y=y, width=width, height=height)


def _ttf_names(path: Path) -> tuple[str, ...]:
    try:
        with path.open("rb") as source:
            header = source.read(12)
            if len(header) != 12:
                return ()
            _, table_count, _, _, _ = struct.unpack(">IHHHH", header)
            if table_count > 256:
                return ()
            name_offset = name_length = None
            for _ in range(table_count):
                record = source.read(16)
                if len(record) != 16:
                    return ()
                tag, _, offset, length = struct.unpack(">4sIII", record)
                if tag == b"name":
                    name_offset, name_length = offset, length
            if name_offset is None or name_length is None or name_length > 1024 * 1024:
                return ()
            source.seek(name_offset)
            table = source.read(name_length)
    except OSError:
        return ()
    if len(table) < 6:
        return ()
    _, count, strings_offset = struct.unpack_from(">HHH", table)
    if count > 4096 or 6 + count * 12 > len(table) or strings_offset > len(table):
        return ()
    by_id: dict[int, list[str]] = {}
    for index in range(count):
        platform, _, _, name_id, length, offset = struct.unpack_from(
            ">HHHHHH", table, 6 + index * 12
        )
        if name_id not in {1, 2, 4, 6}:
            continue
        start = strings_offset + offset
        end = start + length
        if start < strings_offset or end > len(table):
            continue
        try:
            value = table[start:end].decode("utf-16-be" if platform in {0, 3} else "mac_roman")
        except (UnicodeDecodeError, LookupError):
            continue
        if value and value not in by_id.setdefault(name_id, []):
            by_id[name_id].append(value)
    names = [value for name_id in (1, 4, 6) for value in by_id.get(name_id, [])]
    for family in by_id.get(1, []):
        for style in by_id.get(2, []):
            names.append(f"{family} {style}")
    return tuple(dict.fromkeys(names))


def index_project(root: Path) -> ProjectIndex:
    packages: dict[str, str] = {}
    package_roots: dict[str, str] = {}
    by_name: dict[str, ProjectResource] = {}
    resources_by_package: dict[str, dict[str, ProjectResource]] = {}
    ids: dict[str, set[str]] = {}
    font_alias_lists: dict[str, list[ProjectResource]] = {}
    manifests = set(root.glob("*/package.xml")) | set(root.glob("assets/*/package.xml"))
    for manifest in sorted(manifests):
        tree = etree.parse(str(manifest))
        package = tree.getroot()
        package_name = manifest.parent.name
        package_root = manifest.parent.relative_to(root).as_posix()
        package_id = str(package.attrib["id"])
        packages[package_name] = package_id
        package_roots[package_name] = package_root
        ids[package_name] = set()
        resources_by_package[package_name] = {}
        for element in package.xpath("./resources/*"):
            kind = str(element.tag)
            resource_id = str(element.attrib["id"])
            name = str(element.attrib["name"])
            path = str(element.attrib.get("path", "/"))
            relative = safe_relative_path(f"{package_root}/{path.strip('/')}/{name}")
            resource = ProjectResource(
                id=resource_id,
                name=name,
                kind=kind,
                package_id=package_id,
                relative_path=relative,
                scale9grid=_scale9grid(element.attrib.get("scale9grid")) if kind == "image" else None,
            )
            by_name[name] = resource
            resources_by_package[package_name][name] = resource
            ids[package_name].add(resource_id)
            if kind == "font":
                aliases = {normalize_font_name(Path(name).stem)}
                aliases.update(
                    normalize_font_name(value) for value in _ttf_names(root / relative)
                )
                for alias in aliases - {""}:
                    candidates = font_alias_lists.setdefault(alias, [])
                    if resource not in candidates:
                        candidates.append(resource)
    return ProjectIndex(
        packages=packages,
        package_roots=package_roots,
        by_name=by_name,
        resources_by_package=resources_by_package,
        ids_by_package={name: frozenset(values) for name, values in ids.items()},
        font_aliases={name: tuple(values) for name, values in font_alias_lists.items()},
    )
