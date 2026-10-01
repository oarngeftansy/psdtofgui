import hashlib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lxml import etree
from PIL import Image

from figma_to_fgui.assets import make_resource_id
from figma_to_fgui.models import (
    ClassificationDecision,
    Diagnostic,
    GeneratedFile,
    NormalizedNode,
    Severity,
)
from figma_to_fgui.normalize import SelectionAsset
from figma_to_fgui.paths import safe_relative_path
from figma_to_fgui.project_index import ProjectIndex, normalize_font_name
from figma_to_fgui.semantic_names import is_valid_semantic_name
from figma_to_fgui.tree import walk_nodes
from figma_to_fgui.uir_compile import (
    unsupported_base_text_features,
    unsupported_behavior_reason,
    unsupported_text_visual_features,
)

_ASSET_SUFFIX = {
    "image/png": ".png",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


@dataclass(frozen=True)
class _RegisteredAsset:
    asset: str
    resource_id: str
    reused: bool


def _preflight_legacy_generation(roots: tuple[NormalizedNode, ...]) -> None:
    """Fail closed before the legacy XML writer can discard typed semantics."""
    for node in walk_nodes(roots):
        if unsupported_behavior_reason(node) is not None:
            raise ValueError("selection uses unsupported generation features")
        runs = node.raw_style.get("runs")
        if isinstance(runs, (list, tuple)) and runs:
            raise ValueError("selection uses unsupported generation features")
        if node.type == "TEXT" and unsupported_base_text_features(node.raw_style):
            raise ValueError("selection uses unsupported generation features")
        if node.type == "TEXT" and unsupported_text_visual_features(
            node.raw_style, node.properties
        ):
            raise ValueError("selection uses unsupported generation features")
        for transform_key in (
            "relativeTransform",
            "relative_transform",
            "absoluteTransform",
            "absolute_transform",
        ):
            transform = node.raw_style.get(transform_key)
            if transform is None:
                continue
            if not (
                isinstance(transform, (list, tuple))
                and len(transform) == 2
                and all(isinstance(row, (list, tuple)) and len(row) == 3 for row in transform)
            ):
                raise ValueError("selection uses unsupported generation features")
            a, c, _tx = transform[0]
            b, d, _ty = transform[1]
            if not all(isinstance(value, (int, float)) for value in (a, b, c, d)) or (
                a != 1 or b != 0 or c != 0 or d != 1
            ):
                raise ValueError("selection uses unsupported generation features")
        if node.properties.get("clips_content") is True:
            raise ValueError("selection uses unsupported generation features")
        if node.raw_style.get("mask"):
            raise ValueError("selection uses unsupported generation features")
        if node.resource_refs and node.children:
            raise ValueError("selection uses unsupported generation features")
        if str(node.properties.get("layout_mode", "")).upper() in {
            "HORIZONTAL",
            "VERTICAL",
        }:
            if str(node.properties.get("layout_wrap", "NO_WRAP")).upper() not in {
                "NONE",
                "NO_WRAP",
            }:
                raise ValueError("selection uses unsupported generation features")
            if any(
                key in node.properties
                for key in ("min_width", "max_width", "min_height", "max_height")
            ):
                raise ValueError("selection uses unsupported generation features")
            if str(node.properties.get("primary_axis_sizing_mode", "")).upper() == "AUTO":
                raise ValueError("selection uses unsupported generation features")
            if str(node.properties.get("counter_axis_sizing_mode", "")).upper() == "AUTO":
                raise ValueError("selection uses unsupported generation features")
            if str(node.properties.get("primary_axis_align_items", "")).upper() == "SPACE_BETWEEN":
                raise ValueError("selection uses unsupported generation features")


def _generated_name(node: NormalizedNode, decision: ClassificationDecision) -> str:
    """Return the validated AI name or the existing normalized node name."""
    semantic_name = decision.semantic_name
    if semantic_name is None:
        return node.name
    if not is_valid_semantic_name(semantic_name):
        raise ValueError("semantic name is invalid")
    return semantic_name


def _integer(value: float, node_id: str, field: str) -> tuple[int, Diagnostic | None]:
    result = round(value)
    if result == value:
        return result, None
    return result, Diagnostic(
        code="geometry.rounded",
        severity=Severity.INFO,
        message=f"Rounded {field} from {value} to {result}",
        node_id=node_id,
        rule_id="geometry.integer-output",
        rule_version=1,
    )


def _geometry(
    node: NormalizedNode, root: NormalizedNode
) -> tuple[dict[str, str], tuple[Diagnostic, ...]]:
    x, dx = _integer(node.bounds.x - root.bounds.x, node.id, "x")
    y, dy = _integer(node.bounds.y - root.bounds.y, node.id, "y")
    width, dw = _integer(node.bounds.width, node.id, "width")
    height, dh = _integer(node.bounds.height, node.id, "height")
    attributes = {"xy": f"{x},{y}", "size": f"{width},{height}"}
    if node.rotation:
        attributes["rotation"] = str(round(node.rotation, 3))
    return attributes, tuple(item for item in (dx, dy, dw, dh) if item is not None)


def _text_geometry(
    node: NormalizedNode,
    root: NormalizedNode,
    font_size: float,
    horizontal: str | None,
) -> dict[str, str]:
    height = max(round(node.bounds.height), round(font_size * 4 / 3))
    weights = sum(
        1.0 if unicodedata.east_asian_width(character) in {"W", "F"}
        else 0.3 if character.isspace()
        else 0.52
        for character in (node.text or "")
    )
    width = max(round(node.bounds.width), round(weights * font_size))
    x = node.bounds.x - root.bounds.x
    if horizontal == "CENTER":
        x -= (width - node.bounds.width) / 2
    elif horizontal == "RIGHT":
        x -= width - node.bounds.width
    y = node.bounds.y - root.bounds.y - (height - node.bounds.height) / 2
    return {"xy": f"{round(x)},{round(y)}", "size": f"{width},{height}"}


def _font_uri(node: NormalizedNode, index: ProjectIndex | None) -> str | None:
    if index is None:
        return None
    font = node.raw_style.get("font")
    if not isinstance(font, dict):
        return None
    family = font.get("family")
    style = font.get("style")
    if not isinstance(family, str):
        return None
    aliases = []
    if isinstance(style, str) and style.casefold() not in {"regular", "normal"}:
        aliases.append(normalize_font_name(f"{family} {style}"))
    aliases.append(normalize_font_name(family))
    for alias in aliases:
        matches = index.font_aliases.get(alias, ())
        if len(matches) == 1:
            resource = matches[0]
            return f"ui://{resource.package_id}{resource.id}"
        regular_matches = tuple(
            resource
            for resource in matches
            if "sdf" not in normalize_font_name(resource.name)
        )
        if len(regular_matches) == 1:
            resource = regular_matches[0]
            return f"ui://{resource.package_id}{resource.id}"
    return None


def _solid_fill(node: NormalizedNode) -> dict[str, Any] | None:
    fills = node.raw_style.get("fills", ())
    if not isinstance(fills, (list, tuple)):
        return None
    for fill in fills:
        if (
            isinstance(fill, dict)
            and fill.get("type") == "SOLID"
            and fill.get("visible", True) is not False
            and isinstance(fill.get("color"), dict)
        ):
            return fill
    return None


def _solid_stroke(node: NormalizedNode) -> dict[str, Any] | None:
    strokes = node.raw_style.get("strokes", ())
    if not isinstance(strokes, (list, tuple)):
        return None
    return next(
        (
            stroke
            for stroke in strokes
            if isinstance(stroke, dict)
            and stroke.get("type") == "SOLID"
            and stroke.get("visible", True) is not False
            and isinstance(stroke.get("color"), dict)
        ),
        None,
    )


def _color(fill: dict[str, Any], include_alpha: bool, opacity: float = 1) -> str:
    color = fill["color"]
    channels = [max(0, min(255, round(float(color.get(key, default)) * 255))) for key, default in (("r", 0), ("g", 0), ("b", 0))]
    if include_alpha:
        alpha = float(color.get("a", 1)) * float(fill.get("opacity", 1)) * opacity
        channels.insert(0, max(0, min(255, round(alpha * 255))))
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _direct_asset_references(node: NormalizedNode) -> tuple[dict[str, Any], ...]:
    return tuple(
        reference.model_dump(mode="python", by_alias=True, exclude_none=True)
        for reference in node.resource_refs
        if reference.asset.startswith("asset_")
        and reference.mime_type in _ASSET_SUFFIX
    )


def _asset_references(node: NormalizedNode) -> tuple[dict[str, Any], ...]:
    result = list(_direct_asset_references(node))
    for child in node.children:
        result.extend(_asset_references(child))
    return tuple(result)


def _visible_nodes(nodes: tuple[NormalizedNode, ...]) -> tuple[NormalizedNode, ...]:
    result: list[NormalizedNode] = []
    pending = list(reversed(nodes))
    while pending:
        node = pending.pop()
        if not node.visible:
            continue
        result.append(node)
        if not _direct_asset_references(node):
            pending.extend(reversed(node.children))
    return tuple(result)


def _mask_companion_ids(root: NormalizedNode) -> frozenset[str]:
    result: set[str] = set()
    for parent in walk_nodes((root,)):
        if parent.type == "BOOLEAN_OPERATION" and any(
            _direct_asset_references(child) for child in parent.children
        ):
            result.update(
                child.id
                for child in parent.children
                if child.type in {"RECTANGLE", "ELLIPSE"}
                and not _direct_asset_references(child)
            )
        if len(parent.children) != 2:
            continue
        artwork, companion = parent.children
        if (
            _direct_asset_references(artwork)
            and companion.type == "RECTANGLE"
            and not _direct_asset_references(companion)
        ):
            result.add(companion.id)
    return frozenset(result)


def _sha256_matches(path: Path, selection_asset: SelectionAsset) -> bool:
    if not path.is_file() or path.stat().st_size != selection_asset.size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == selection_asset.sha256


def _matches_registered_asset(
    item: etree._Element,
    project_root: Path,
    package_root: str,
    name: str,
    selection_asset: SelectionAsset,
) -> bool:
    path = item.attrib.get("path")
    resource_id = item.attrib.get("id")
    if not isinstance(path, str) or not resource_id:
        return False
    try:
        expected = safe_relative_path(f"{package_root}/assets/{name}")
        actual = safe_relative_path(f"{package_root}/{path.strip('/')}/{name}")
    except ValueError:
        return False
    return actual == expected and _sha256_matches(project_root / actual, selection_asset)


def _resolved_asset_name(
    asset: str,
    selection_asset: SelectionAsset,
    existing: dict[str, etree._Element],
    project_root: Path,
    package_root: str,
) -> tuple[str, etree._Element | None]:
    suffix = _ASSET_SUFFIX[selection_asset.mime_type]
    attempt = 0
    while True:
        collision_suffix = hashlib.sha256(
            f"{asset}|{selection_asset.sha256}|{attempt}".encode()
        ).hexdigest()[:16]
        resolved = asset if attempt == 0 else f"{asset}_{collision_suffix}"
        name = f"{resolved}{suffix}"
        previous = existing.get(name)
        if previous is None:
            return resolved, None
        if _matches_registered_asset(
            previous, project_root, package_root, name, selection_asset
        ):
            return resolved, previous
        attempt += 1


def _write_package_resources(
    project_root: Path,
    package_name: str,
    staging_root: Path,
    assets: dict[str, SelectionAsset],
    panel_names: tuple[str, ...],
    index: ProjectIndex,
    nine_slice_by_asset: dict[str, tuple[str, str]],
) -> tuple[dict[str, _RegisteredAsset], GeneratedFile, tuple[Diagnostic, ...]]:
    package_root = index.package_roots.get(package_name, package_name)
    source = project_root / package_root / "package.xml"
    tree = etree.parse(str(source), etree.XMLParser(resolve_entities=False, no_network=True))
    resources = tree.getroot().find("resources")
    if resources is None:
        raise ValueError("package resources are unavailable")
    occupied = set(index.ids_by_package.get(package_name, frozenset()))
    existing = {str(item.attrib.get("name", "")): item for item in resources.findall("image")}
    registered: dict[str, _RegisteredAsset] = {}
    diagnostics: list[Diagnostic] = []
    for asset, selection_asset in sorted(assets.items()):
        mime_type = selection_asset.mime_type
        resolved, previous = _resolved_asset_name(
            asset, selection_asset, existing, project_root, package_root
        )
        name = f"{resolved}{_ASSET_SUFFIX[mime_type]}"
        if previous is not None:
            desired = nine_slice_by_asset.get(asset)
            existing_resource = index.resources_by_package.get(package_name, {}).get(name)
            existing_grid = existing_resource.scale9grid if existing_resource is not None else None
            if desired is not None and existing_grid is not None:
                existing_value = f"{existing_grid.x},{existing_grid.y},{existing_grid.width},{existing_grid.height}"
                if existing_value != desired[0]:
                    diagnostics.append(Diagnostic(code="nine_slice_conflict", severity=Severity.WARNING, message="Existing FairyGUI nine-slice metadata overrides the Figma annotation.", node_id=desired[1]))
            elif desired is not None:
                previous.attrib["scale9grid"] = desired[0]
            registered[asset] = _RegisteredAsset(
                asset=resolved,
                resource_id=str(previous.attrib["id"]),
                reused=True,
            )
            continue
        resource_id = make_resource_id(f"{resolved}|{name}", frozenset(occupied))
        occupied.add(resource_id)
        previous = etree.SubElement(
            resources,
            "image",
            id=resource_id,
            name=name,
            path="/assets/",
            exported="true",
        )
        desired = nine_slice_by_asset.get(asset)
        if desired is not None:
            previous.attrib["scale9grid"] = desired[0]
        existing[name] = previous
        registered[asset] = _RegisteredAsset(
            asset=resolved,
            resource_id=resource_id,
            reused=False,
        )
    existing_components: dict[str, set[str]] = {}
    for item in resources.findall("component"):
        name = str(item.attrib.get("name", ""))
        existing_components.setdefault(name.casefold(), set()).add(name)
    for panel_name in panel_names:
        file_name = f"{panel_name}.xml"
        matches = existing_components.get(file_name.casefold(), set())
        if any(match != file_name for match in matches):
            raise ValueError("project contains a case-insensitive panel collision")
        if file_name in matches:
            continue
        resource_id = make_resource_id(f"{panel_name}|{file_name}", frozenset(occupied))
        occupied.add(resource_id)
        etree.SubElement(
            resources,
            "component",
            id=resource_id,
            name=file_name,
            path="/Panel/",
            exported="true",
        )
        existing_components.setdefault(file_name.casefold(), set()).add(file_name)
    relative = safe_relative_path(f"{package_root}/package.xml")
    payload = etree.tostring(tree, encoding="utf-8", xml_declaration=True, pretty_print=True)
    target = staging_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return registered, GeneratedFile(
        relative_path=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    ), tuple(diagnostics)


def _validate_existing_panel_collisions(
    project_root: Path | None,
    package_name: str,
    panel_names: tuple[str, ...],
    project_index: ProjectIndex | None = None,
) -> None:
    if project_root is None or not panel_names:
        return
    package_root = project_root / (
        project_index.package_roots.get(package_name, package_name)
        if project_index is not None
        else package_name
    )
    package_path = package_root / "package.xml"
    if not package_path.is_file():
        return
    tree = etree.parse(str(package_path), etree.XMLParser(resolve_entities=False, no_network=True))
    existing_names = tuple(
        str(item.attrib.get("name", "")) for item in tree.xpath("./resources/component")
    )
    existing_paths = tuple(
        candidate.relative_to(package_root).as_posix()
        for candidate in package_root.rglob("*")
        if candidate.is_file()
    )
    for panel_name in panel_names:
        file_name = f"{panel_name}.xml"
        if any(
            existing.casefold() == file_name.casefold() and existing != file_name
            for existing in existing_names
        ):
            raise ValueError("project contains a case-insensitive panel collision")
        expected_path = f"Panel/{file_name}"
        if any(
            existing_path.casefold() == expected_path.casefold()
            and existing_path != expected_path
            for existing_path in existing_paths
        ):
            raise ValueError("project contains a case-insensitive panel collision")


def _selection_nine_slices(
    roots: tuple[NormalizedNode, ...], assets: dict[str, SelectionAsset]
) -> tuple[dict[str, tuple[str, str]], tuple[Diagnostic, ...]]:
    result: dict[str, tuple[str, str]] = {}
    diagnostics: list[Diagnostic] = []
    for node in walk_nodes(roots):
        insets = node.properties.get("nine_slice_insets")
        if not isinstance(insets, dict):
            continue
        values = tuple(insets.get(key) for key in ("left", "top", "right", "bottom"))
        if not all(type(value) is int and value >= 0 for value in values):
            continue
        left, top, right, bottom = cast(tuple[int, int, int, int], values)
        for reference in _direct_asset_references(node):
            asset_name = str(reference["asset"])
            asset = assets.get(asset_name)
            if asset is None or asset.mime_type not in {"image/png", "image/webp"}:
                continue
            try:
                with Image.open(asset.source_path) as image:
                    width, height = image.size
            except (OSError, ValueError):
                continue
            if left + right >= width or top + bottom >= height:
                diagnostics.append(Diagnostic(code="nine_slice_out_of_bounds", severity=Severity.WARNING, message="Nine-slice insets exceed the exported image dimensions.", node_id=node.id))
                continue
            grid = f"{left},{top},{width - left - right},{height - top - bottom}"
            previous = result.get(asset_name)
            if previous is not None and previous[0] != grid:
                diagnostics.append(Diagnostic(code="nine_slice_conflict", severity=Severity.WARNING, message="Conflicting Figma nine-slice annotations reference the same image.", node_id=node.id))
                continue
            result.setdefault(asset_name, (grid, node.id))
    return result, tuple(diagnostics)


def generate_staging(
    roots: tuple[NormalizedNode, ...],
    decisions: tuple[ClassificationDecision, ...],
    package_name: str,
    staging_root: Path,
    project_root: Path | None = None,
    project_index: ProjectIndex | None = None,
    selection_assets: tuple[SelectionAsset, ...] = (),
) -> tuple[tuple[GeneratedFile, ...], tuple[Diagnostic, ...]]:
    _preflight_legacy_generation(roots)
    if project_index is not None and package_name not in project_index.packages:
        raise ValueError("project package is unavailable")
    decision_by_id = {item.node_id: item for item in decisions}
    generated_names = {
        node.id: _generated_name(node, decision_by_id[node.id])
        for node in walk_nodes(roots)
    }
    diagnostics: list[Diagnostic] = []
    files: list[GeneratedFile] = []
    asset_sources = {asset.asset: asset for asset in selection_assets}
    assets: dict[str, SelectionAsset] = {}
    panel_assets: dict[str, dict[str, SelectionAsset]] = {}
    for root in roots:
        if decision_by_id[root.id].output_type != "PANEL":
            continue
        root_assets: dict[str, SelectionAsset] = {}
        for reference in _asset_references(root):
            asset = str(reference["asset"])
            entry = asset_sources.get(asset)
            if entry is None or entry.mime_type != reference["mimeType"]:
                raise ValueError("selection asset reference is unavailable")
            if asset in assets and assets[asset] != entry:
                raise ValueError("selection asset reference conflict")
            assets[asset] = entry
            root_assets[asset] = entry
        panel_assets[root.id] = root_assets
    panel_names = tuple(
        f"Panel_{package_name}_{generated_names[root.id]}"
        for root in roots
        if decision_by_id[root.id].output_type == "PANEL"
    )
    if len(panel_names) != len({name.casefold() for name in panel_names}):
        raise ValueError("selection contains duplicate panel names")
    _validate_existing_panel_collisions(project_root, package_name, panel_names, project_index)
    for root in roots:
        if decision_by_id[root.id].output_type != "PANEL":
            continue
        object_names = list(panel_assets[root.id])
        object_names.extend(
            generated_names[child.id]
            for child in root.children
            if decision_by_id[child.id].output_type == "TEXT"
        )
        if len(object_names) != len({name.casefold() for name in object_names}):
            raise ValueError("selection contains duplicate generated object names")
    registrations: dict[str, _RegisteredAsset] = {}
    package_file: GeneratedFile | None = None
    nine_slice_by_asset, nine_slice_diagnostics = _selection_nine_slices(roots, assets)
    diagnostics.extend(nine_slice_diagnostics)
    if assets or panel_names:
        if project_root is None or project_index is None:
            if assets:
                raise ValueError("project package resources are unavailable")
        else:
            registrations, package_file, package_diagnostics = _write_package_resources(
                project_root, package_name, staging_root, assets, panel_names, project_index,
                nine_slice_by_asset,
            )
            diagnostics.extend(package_diagnostics)
    if assets:
        if package_file is None or project_index is None:
            raise ValueError("project package resources are unavailable")
        for asset, selection_asset in sorted(assets.items()):
            registration = registrations[asset]
            if registration.reused:
                continue
            mime_type = selection_asset.mime_type
            package_root = project_index.package_roots.get(package_name, package_name)
            asset_relative = safe_relative_path(
                f"{package_root}/assets/{registration.asset}{_ASSET_SUFFIX[mime_type]}"
            )
            asset_target = staging_root / asset_relative
            asset_target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with selection_asset.source_path.open("rb") as source, asset_target.open("wb") as destination:
                while chunk := source.read(64 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
                    destination.write(chunk)
            if size != selection_asset.size or digest.hexdigest() != selection_asset.sha256:
                raise ValueError("selection asset is unavailable")
            files.append(
                GeneratedFile(
                    relative_path=asset_relative,
                    sha256=digest.hexdigest(),
                    size=size,
                )
            )
    for root in roots:
        if decision_by_id[root.id].output_type != "PANEL":
            continue
        root_assets = panel_assets[root.id]
        root_name = generated_names[root.id]
        package_root = (
            project_index.package_roots.get(package_name, package_name)
            if project_index is not None
            else package_name
        )
        relative = safe_relative_path(
            f"{package_root}/Panel/Panel_{package_name}_{root_name}.xml"
        )
        target = staging_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        root_width, dw = _integer(root.bounds.width, root.id, "width")
        root_height, dh = _integer(root.bounds.height, root.id, "height")
        diagnostics.extend(item for item in (dw, dh) if item is not None)
        component = etree.Element(
            "component", name=f"Panel_{package_name}_{root_name}", size=f"{root_width},{root_height}"
        )
        display = etree.SubElement(component, "displayList")
        mask_companions = _mask_companion_ids(root)
        visible_nodes = _visible_nodes(root.children)
        if _direct_asset_references(root):
            visible_nodes = (root, *visible_nodes)
        for node in visible_nodes:
            if node.id in mask_companions:
                continue
            references = _direct_asset_references(node)
            geometry, geometry_diagnostics = _geometry(node, root)
            diagnostics.extend(geometry_diagnostics)
            for reference_index, reference in enumerate(references):
                asset_geometry = {key: value for key, value in geometry.items() if key != "rotation"}
                asset = str(reference["asset"])
                selection_asset = root_assets[asset]
                registration = registrations[asset]
                etree.SubElement(
                    display,
                    "image",
                    id=f"image_{node.id.replace(':', '_')}_{reference_index}",
                    name=registration.asset,
                    src=registration.resource_id,
                    fileName=f"assets/{registration.asset}{_ASSET_SUFFIX[selection_asset.mime_type]}",
                    **asset_geometry,
                )
            if references:
                continue
            if decision_by_id[node.id].output_type == "TEXT":
                attributes = dict(geometry)
                attributes.update(
                    id=node.id.replace(":", "_"),
                    name=generated_names[node.id],
                    autoSize="none",
                    text=node.text or "",
                )
                fill = _solid_fill(node)
                if fill is not None:
                    attributes["color"] = _color(fill, False)
                if node.opacity < 1:
                    attributes["alpha"] = f"{node.opacity:g}"
                font_size = node.properties.get("font_size", node.raw_style.get("fontSize"))
                horizontal = node.properties.get(
                    "text_align_horizontal", node.raw_style.get("textAlignHorizontal")
                )
                if isinstance(font_size, (int, float)):
                    attributes.update(
                        _text_geometry(
                            node, root, font_size, horizontal if isinstance(horizontal, str) else None
                        )
                    )
                    attributes["fontSize"] = str(round(font_size))
                    raw_line_height = node.properties.get(
                        "line_height",
                        node.raw_style.get("lineHeight", node.raw_style.get("lineHeightPx")),
                    )
                    if isinstance(raw_line_height, dict):
                        raw_line_height = (
                            raw_line_height.get("value")
                            if str(raw_line_height.get("unit", "")).upper() == "PIXELS"
                            else None
                        )
                    if isinstance(raw_line_height, (int, float)):
                        attributes["leading"] = str(round(raw_line_height - font_size))
                font_uri = _font_uri(node, project_index)
                if font_uri is not None:
                    attributes["font"] = font_uri
                if isinstance(horizontal, str):
                    attributes["align"] = horizontal.lower()
                vertical = node.properties.get(
                    "text_align_vertical", node.raw_style.get("textAlignVertical")
                )
                if isinstance(vertical, str):
                    attributes["vAlign"] = {"CENTER": "middle"}.get(
                        vertical.upper(), vertical.lower()
                    )
                stroke = _solid_stroke(node)
                stroke_weight = node.properties.get("stroke_weight")
                if stroke is not None and isinstance(stroke_weight, (int, float)):
                    attributes["strokeSize"] = str(round(stroke_weight))
                    attributes["strokeColor"] = _color(stroke, False)
                etree.SubElement(
                    display,
                    "text",
                    **attributes,
                )
                continue
            fill = _solid_fill(node)
            if node.type in {"RECTANGLE", "ELLIPSE"} and fill is not None:
                attributes = dict(geometry)
                attributes.update(
                    id=node.id.replace(":", "_"),
                    name=generated_names[node.id],
                    type="ellipse" if node.type == "ELLIPSE" else "rect",
                    lineSize="0",
                    fillColor=_color(fill, True, node.opacity),
                )
                stroke = _solid_stroke(node)
                stroke_weight = node.properties.get("stroke_weight")
                if stroke is not None and isinstance(stroke_weight, (int, float)):
                    attributes["lineSize"] = str(round(stroke_weight))
                    attributes["lineColor"] = _color(stroke, False)
                corner = node.properties.get("corner_radius")
                if isinstance(corner, (int, float)) and corner > 0:
                    attributes["corner"] = str(round(corner))
                etree.SubElement(display, "graph", **attributes)
        payload = etree.tostring(
            component, encoding="utf-8", xml_declaration=True, pretty_print=True
        )
        target.write_bytes(payload)
        files.append(
            GeneratedFile(
                relative_path=relative,
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
            )
        )
    if package_file is not None:
        files.append(package_file)
    return tuple(files), tuple(diagnostics)
