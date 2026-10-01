from __future__ import annotations

import hashlib
import math
import re
import stat
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath
from typing import cast

from lxml import etree

from figma_to_fgui.fgui_asset_payloads import ValidatedAssetPayload
from figma_to_fgui.fgui_new_project_models import (
    ManifestComponent,
    ManifestObject,
    ManifestResource,
    NewProjectManifest,
)
from figma_to_fgui.fgui_new_project_validate import (
    NewProjectManifestError,
    canonical_manifest_bytes,
    validate_new_project_manifest,
    validate_xml_files,
)
from figma_to_fgui.fgui_plan_models import MaskKind, MaskMode, PlanNodeType, TextPlan
from figma_to_fgui.models import Diagnostic, FrozenModel

FAIRYGUI_VERSION = "6.1.4"
PROJECT_SUFFIX = ".fairy"
ASSETS_DIRECTORY = "assets"

_PROJECT_FILE_VERSION = "5.0"
_UNITY_TARGET = "Unity"
_ID = re.compile(r"^[a-z0-9]+$")
_SIZE = re.compile(r"^([1-9][0-9]*),([1-9][0-9]*)$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

SAFE_XML_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
)


class UnsupportedDialectFeature(ValueError):
    """A validated public value has no registered FairyGUI 6.1.4 encoding."""


class XMLDialectValidationError(ValueError):
    """Generated files failed the independent XML/file-closure gate."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("Generated FairyGUI XML validation failed.")


_FGUI_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
_ALIGNMENTS = frozenset({"left", "center", "right", "justify"})
_VERTICAL_ALIGNMENTS = frozenset({"top", "middle", "bottom"})


def _xml_bytes(root: etree._Element) -> bytes:
    serialized = etree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
        standalone=None,
    ).replace(b"\r\n", b"\n")
    result = serialized if serialized.endswith(b"\n") else serialized + b"\n"
    return cast(bytes, result)


def _canonical_decimal(value: float) -> str:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise UnsupportedDialectFeature("a numeric XML value has an unsupported type")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise UnsupportedDialectFeature("a numeric XML value is not finite")
    formatted = format(Decimal(str(value)), "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return "0" if formatted in {"", "-0"} else formatted


def _editor_int32(value: float) -> str:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise UnsupportedDialectFeature("an integer XML value has an unsupported type")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise UnsupportedDialectFeature("an integer XML value is not finite")
    rounded = int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))
    if not -(2**31) <= rounded < 2**31:
        raise UnsupportedDialectFeature("an integer XML value is outside Int32")
    return str(rounded)


def _editor_int32_pair(first: float, second: float) -> str:
    return f"{_editor_int32(first)},{_editor_int32(second)}"


def _quad(values: Sequence[float]) -> str:
    if len(values) != 4:
        raise UnsupportedDialectFeature("a rounded clip requires exactly four radii")
    # Plans keep Figma's TL, TR, BR, BL order. FairyGUI 6.1.4 serializes
    # rounded rectangles as TL, TR, BL, BR.
    top_left, top_right, bottom_right, bottom_left = values
    return ",".join(
        _canonical_decimal(value)
        for value in (top_left, top_right, bottom_left, bottom_right)
    )


def _checked_manifest(manifest: NewProjectManifest) -> NewProjectManifest:
    diagnostics = validate_new_project_manifest(manifest)
    if diagnostics:
        raise NewProjectManifestError(diagnostics)
    return NewProjectManifest.model_validate_json(canonical_manifest_bytes(manifest))


def _project_marker_id(manifest: NewProjectManifest) -> str:
    value = f"project:{manifest.project.project_name}\0{manifest.package.id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def serialize_project_marker(manifest: NewProjectManifest) -> bytes:
    """Serialize the deterministic Unity project marker observed in Editor 6.1.4."""
    manifest = _checked_manifest(manifest)
    root = etree.Element(
        "projectDescription",
        id=_project_marker_id(manifest),
        type=_UNITY_TARGET,
        version=_PROJECT_FILE_VERSION,
    )
    return _xml_bytes(root)


def _package_virtual_directory(relative_path: str) -> str:
    parent = PurePosixPath(relative_path).parent
    return f"/{parent.as_posix()}/"


def serialize_package_xml(manifest: NewProjectManifest) -> bytes:
    """Serialize package resources in manifest order with editor-proven paths."""
    manifest = _checked_manifest(manifest)
    root = etree.Element("packageDescription", id=manifest.package.id)
    resources_element = etree.SubElement(root, "resources")
    for component in manifest.components:
        etree.SubElement(
            resources_element,
            "component",
            id=component.id,
            name=PurePosixPath(component.relative_path).name,
            path=_package_virtual_directory(component.relative_path),
        )
    for resource in manifest.resources:
        attributes = {
            "id": resource.id,
            "name": PurePosixPath(resource.relative_path).name,
            "path": _package_virtual_directory(resource.relative_path),
        }
        if resource.nine_slice is not None:
            attributes["scale"] = "9grid"
            attributes["scale9grid"] = ",".join(
                str(value)
                for value in (
                    resource.nine_slice.x,
                    resource.nine_slice.y,
                    resource.nine_slice.width,
                    resource.nine_slice.height,
                )
            )
        etree.SubElement(resources_element, "image", **attributes)
    etree.SubElement(root, "publish")
    return _xml_bytes(root)


@dataclass(frozen=True)
class _ComponentContext:
    package_id: str | None
    resources: Mapping[str, ManifestResource]
    components: Mapping[str, ManifestComponent]
    objects: Mapping[str, ManifestObject]
    root_id: str
    positions: Mapping[str, tuple[float, float]]
    mask_sources: Mapping[str, tuple[MaskKind, tuple[float, float, float, float] | None]]


def _object_common_attributes(
    object_: ManifestObject, context: _ComponentContext
) -> dict[str, str]:
    bounds = object_.transform.bounds
    x, y = context.positions[object_.id]
    attributes = {
        "id": object_.id,
        "name": object_.name or object_.id,
        "xy": _editor_int32_pair(x, y),
        "size": _editor_int32_pair(bounds.width, bounds.height),
    }
    parent_id = object_.parent_object_ref
    if parent_id is not None and parent_id != context.root_id:
        attributes["group"] = parent_id
    if object_.transform.rotation != 0:
        attributes["rotation"] = _editor_int32(object_.transform.rotation)
    if object_.transform.opacity != 1:
        attributes["alpha"] = _canonical_decimal(object_.transform.opacity)
    if not object_.transform.visible:
        attributes["visible"] = "false"
    return attributes


def _require_resource(
    object_: ManifestObject, context: _ComponentContext
) -> ManifestResource:
    resource = context.resources.get(object_.resource_ref or "")
    if resource is None:
        raise UnsupportedDialectFeature(
            f"object {object_.id} has no registered package resource"
        )
    return resource


def _serialize_container(
    object_: ManifestObject, context: _ComponentContext
) -> etree._Element:
    attributes = _object_common_attributes(object_, context)
    mask_details = context.mask_sources.get(object_.id)
    if mask_details is None:
        return etree.Element("group", **attributes)
    kind, radii = mask_details
    attributes["type"] = "rect"
    if kind == MaskKind.ROUNDED_RECTANGLE:
        if radii is None:
            raise UnsupportedDialectFeature("rounded clip radii are missing")
        attributes["corner"] = _quad(radii)
    elif kind != MaskKind.RECTANGLE:
        raise UnsupportedDialectFeature(f"container mask kind {kind} is not registered")
    return etree.Element("graph", **attributes)


def _serialize_graph(object_: ManifestObject, context: _ComponentContext) -> etree._Element:
    graph = object_.graph
    if graph is None:
        raise UnsupportedDialectFeature(f"graph object {object_.id} has no graph payload")
    attributes = _object_common_attributes(object_, context)
    attributes["type"] = graph.shape
    attributes["lineSize"] = _canonical_decimal(graph.line_size)
    if (line_color := _validate_color(graph.line_color)) is not None:
        attributes["lineColor"] = line_color
    if (fill_color := _validate_color(graph.fill_color)) is not None:
        attributes["fillColor"] = fill_color
    if graph.corner_radius is not None:
        if graph.shape != "rect":
            raise UnsupportedDialectFeature("only rectangle graphs may declare a corner radius")
        attributes["corner"] = _canonical_decimal(graph.corner_radius)
    elif graph.corner_radii is not None:
        if graph.shape != "rect":
            raise UnsupportedDialectFeature("only rectangle graphs may declare corner radii")
        attributes["corner"] = _quad(graph.corner_radii)
    mask_details = context.mask_sources.get(object_.id)
    if mask_details is not None:
        kind, radii = mask_details
        if kind == MaskKind.ROUNDED_RECTANGLE:
            if radii is None or graph.shape != "rect":
                raise UnsupportedDialectFeature("rounded graph clip radii are missing")
            attributes["corner"] = _quad(radii)
    return etree.Element("graph", **attributes)


def _serialize_background_graph(
    object_: ManifestObject, context: _ComponentContext
) -> etree._Element | None:
    if object_.background_graph is None:
        return None
    background_id = hashlib.sha256(
        f"{object_.id}:background".encode()
    ).hexdigest()[:8]
    if background_id in context.objects:
        raise UnsupportedDialectFeature("background graph identity collides with an object")
    background = object_.model_copy(
        update={
            "id": background_id,
            "parent_object_ref": (
                None if object_.id == context.root_id else object_.id
            ),
            "child_object_refs": (),
            "type": PlanNodeType.GRAPH,
            "graph": object_.background_graph,
            "background_graph": None,
        }
    )
    background_context = _ComponentContext(
        package_id=context.package_id,
        resources=context.resources,
        components=context.components,
        objects=context.objects,
        root_id=context.root_id,
        positions={
            **context.positions,
            background_id: context.positions[object_.id],
        },
        mask_sources=context.mask_sources,
    )
    return _serialize_graph(background, background_context)


def _validate_color(value: str | None) -> str | None:
    if value is not None and _FGUI_COLOR.fullmatch(value) is None:
        raise UnsupportedDialectFeature("a color is not a FairyGUI hexadecimal color")
    return value


def _editor_text_color(value: str | None) -> str | None:
    value = _validate_color(value)
    if value is None or len(value) == 7:
        return value
    # Figma/Plan carries #RRGGBBAA; FairyGUI Editor XML 6.1.4 reads #AARRGGBB.
    return f"#{value[7:9]}{value[1:7]}"


def _rich_text_content(text: TextPlan) -> tuple[str, bool]:
    if not text.runs:
        return text.content, False
    if "".join(run.content for run in text.runs) != text.content:
        raise UnsupportedDialectFeature("rich-text runs do not reproduce the declared content")
    markup_required = any(run.color is not None and run.color != text.color for run in text.runs)
    if markup_required and any("[" in run.content or "]" in run.content for run in text.runs):
        raise UnsupportedDialectFeature("styled rich-text content contains ambiguous UBB markup")
    rendered: list[str] = []
    for run in text.runs:
        if (
            run.font_candidates not in {(), text.font_candidates}
            or run.font_size not in {None, text.font_size}
            or run.line_height not in {None, text.line_height}
            or run.stroke_color not in {None, text.stroke_color}
            or run.stroke_size not in {None, text.stroke_size}
        ):
            raise UnsupportedDialectFeature(
                "per-run font or stroke styling has no observed 6.1.4 encoding"
            )
        color = _editor_text_color(run.color)
        if color is not None and run.color != text.color:
            rendered.append(f"[color={color}]{run.content}[/color]")
        else:
            rendered.append(run.content)
    return "".join(rendered), markup_required


def _serialize_text_like(
    object_: ManifestObject,
    context: _ComponentContext,
    *,
    rich: bool,
) -> etree._Element:
    text = object_.text
    if text is None:
        raise UnsupportedDialectFeature(f"text object {object_.id} has no text payload")
    if not rich and text.runs:
        raise UnsupportedDialectFeature("plain text cannot carry rich-text runs")
    expected_style_facts = {
        "fontCandidates": text.font_candidates,
        **({} if text.font_size is None else {"fontSize": text.font_size}),
        **({} if text.line_height is None else {"lineHeight": text.line_height}),
        **({} if text.color is None else {"color": text.color}),
        **({} if text.stroke_color is None else {"strokeColor": text.stroke_color}),
        **({} if text.stroke_size is None else {"strokeSize": text.stroke_size}),
        **(
            {}
            if text.horizontal_align is None
            else {"textAlignHorizontal": text.horizontal_align}
        ),
        **(
            {}
            if text.vertical_align is None
            else {"textAlignVertical": text.vertical_align}
        ),
        **({} if text.auto_resize is None else {"textAutoResize": text.auto_resize}),
    }
    if text.style_facts and text.style_facts != expected_style_facts:
        raise UnsupportedDialectFeature("text style facts disagree with the typed XML payload")
    attributes = _object_common_attributes(object_, context)
    if text.font_candidates:
        font = text.font_candidates[0]
        if not font or any(unicodedata.category(character).startswith("C") for character in font):
            raise UnsupportedDialectFeature("text font candidate is not a safe editor value")
        attributes["font"] = font
    if text.font_size is not None:
        attributes["fontSize"] = _canonical_decimal(text.font_size)
    if text.line_height is not None:
        if text.font_size is None:
            raise UnsupportedDialectFeature("text line height requires a font size")
        attributes["leading"] = _editor_int32(text.line_height - text.font_size)
    if (color := _editor_text_color(text.color)) is not None:
        attributes["color"] = color
    if text.horizontal_align is not None:
        if text.horizontal_align not in _ALIGNMENTS:
            raise UnsupportedDialectFeature("horizontal text alignment is not registered")
        attributes["align"] = text.horizontal_align
    if text.vertical_align is not None:
        if text.vertical_align not in _VERTICAL_ALIGNMENTS:
            raise UnsupportedDialectFeature("vertical text alignment is not registered")
        attributes["vAlign"] = text.vertical_align
    auto_resize = (text.auto_resize or "NONE").upper()
    try:
        attributes["autoSize"] = {
            "NONE": "none",
            "TRUNCATE": "none",
            "HEIGHT": "height",
            "WIDTH_AND_HEIGHT": "both",
        }[auto_resize]
    except KeyError as error:
        raise UnsupportedDialectFeature("text auto resize mode is not registered") from error
    if (stroke_color := _editor_text_color(text.stroke_color)) is not None:
        attributes["strokeColor"] = stroke_color
    if text.stroke_size is not None:
        attributes["strokeSize"] = _canonical_decimal(text.stroke_size)
    content, uses_ubb = _rich_text_content(text) if rich else (text.content, False)
    if uses_ubb:
        attributes["ubb"] = "true"
    attributes["text"] = content
    return etree.Element("richtext" if rich else "text", **attributes)


def _serialize_text(object_: ManifestObject, context: _ComponentContext) -> etree._Element:
    return _serialize_text_like(object_, context, rich=False)


def _serialize_rich_text(
    object_: ManifestObject, context: _ComponentContext
) -> etree._Element:
    return _serialize_text_like(object_, context, rich=True)


def _serialize_image(object_: ManifestObject, context: _ComponentContext) -> etree._Element:
    resource = _require_resource(object_, context)
    attributes = _object_common_attributes(object_, context)
    attributes["src"] = resource.id
    attributes["fileName"] = resource.relative_path
    return etree.Element("image", **attributes)


def _serialize_loader(object_: ManifestObject, context: _ComponentContext) -> etree._Element:
    resource = _require_resource(object_, context)
    if context.package_id is None:
        raise UnsupportedDialectFeature("loader serialization requires the owning package identity")
    attributes = _object_common_attributes(object_, context)
    attributes["url"] = f"ui://{context.package_id}{resource.id}"
    return etree.Element("loader", **attributes)


def _serialize_component_reference(
    object_: ManifestObject, context: _ComponentContext
) -> etree._Element:
    target = context.components.get(object_.component_ref or "")
    if target is None or context.package_id is None:
        raise UnsupportedDialectFeature(
            f"component object {object_.id} has no registered package target"
        )
    attributes = _object_common_attributes(object_, context)
    attributes["src"] = target.id
    attributes["fileName"] = target.relative_path
    attributes["pkg"] = context.package_id
    return etree.Element("component", **attributes)


_ObjectSerializer = Callable[[ManifestObject, _ComponentContext], etree._Element]
_OBJECT_SERIALIZERS: Mapping[PlanNodeType, _ObjectSerializer] = {
    PlanNodeType.CONTAINER: _serialize_container,
    PlanNodeType.GRAPH: _serialize_graph,
    PlanNodeType.TEXT: _serialize_text,
    PlanNodeType.RICH_TEXT: _serialize_rich_text,
    PlanNodeType.IMAGE: _serialize_image,
    PlanNodeType.LOADER: _serialize_loader,
    PlanNodeType.COMPONENT_REFERENCE: _serialize_component_reference,
    PlanNodeType.RASTER_SUBTREE: _serialize_image,
}


def _object_serializer(object_type: object) -> _ObjectSerializer:
    try:
        return _OBJECT_SERIALIZERS[object_type]  # type: ignore[index]
    except (KeyError, TypeError):
        value = _safe_feature_value(getattr(object_type, "value", object_type))
        raise UnsupportedDialectFeature(f"unregistered FairyGUI node type: {value}") from None


def _safe_feature_value(value: object) -> str:
    if (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", value) is not None
    ):
        return value
    return "<invalid>"


def _check_mask_dispatch(object_: ManifestObject) -> None:
    mode = object_.mask_mode
    kind = object_.mask_kind
    supported = (
        mode is None
        or (
            mode == MaskMode.NATIVE_CLIP
            and kind in {MaskKind.RECTANGLE, MaskKind.ROUNDED_RECTANGLE}
        )
        or (mode == MaskMode.NATIVE_MASK and kind == MaskKind.IMAGE)
        or (
            mode == MaskMode.RASTER_SUBTREE
            and isinstance(kind, MaskKind)
            and object_.type == PlanNodeType.RASTER_SUBTREE
        )
    )
    if not supported:
        mode_value = _safe_feature_value(getattr(mode, "value", mode))
        kind_value = _safe_feature_value(getattr(kind, "value", kind))
        raise UnsupportedDialectFeature(
            f"unregistered FairyGUI mask path: {mode_value}/{kind_value}"
        )


def _component_positions(
    component: ManifestComponent, root_id: str
) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {root_id: (0.0, 0.0)}
    for object_ in component.objects:
        if object_.id == root_id:
            continue
        parent_id = object_.parent_object_ref
        if parent_id is None or parent_id not in positions:
            raise UnsupportedDialectFeature("component geometry is not in canonical preorder")
        parent_x, parent_y = positions[parent_id]
        bounds = object_.transform.bounds
        positions[object_.id] = (parent_x + bounds.x, parent_y + bounds.y)
    return positions


def _mask_sources(
    component: ManifestComponent,
) -> dict[str, tuple[MaskKind, tuple[float, float, float, float] | None]]:
    sources: dict[str, tuple[MaskKind, tuple[float, float, float, float] | None]] = {}
    for object_ in component.objects:
        if object_.mask_mode not in {MaskMode.NATIVE_CLIP, MaskMode.NATIVE_MASK}:
            continue
        if object_.parent_object_ref is not None:
            raise UnsupportedDialectFeature(
                "nested native masks have no observed FairyGUI 6.1.4 project encoding"
            )
        if object_.mask_object_ref is None or object_.mask_kind is None:
            raise UnsupportedDialectFeature("native mask roles are incomplete")
        if object_.mask_kind in {MaskKind.RECTANGLE, MaskKind.ROUNDED_RECTANGLE}:
            if (
                object_.mask_kind == MaskKind.RECTANGLE
                and object_.mask_object_ref == object_.id
            ):
                continue
            sources[object_.mask_object_ref] = (
                object_.mask_kind,
                object_.mask_corner_radii,
            )
    return sources


def serialize_component_xml(
    component: ManifestComponent,
    *,
    package_id: str | None = None,
    resources: Mapping[str, ManifestResource] | None = None,
    components: Mapping[str, ManifestComponent] | None = None,
) -> bytes:
    """Serialize one component with a closed seven-type dispatch table."""
    if not component.objects:
        raise UnsupportedDialectFeature("a generated component has no root object")
    objects = {object_.id: object_ for object_ in component.objects}
    roots = tuple(object_ for object_ in component.objects if object_.parent_object_ref is None)
    if len(roots) != 1:
        raise UnsupportedDialectFeature("a generated component must have exactly one root object")
    root_object = roots[0]
    for object_ in component.objects:
        _object_serializer(object_.type)
        _check_mask_dispatch(object_)
    root_bounds = root_object.transform.bounds
    if root_object.type == PlanNodeType.CONTAINER and (
        root_bounds.width != component.size.width
        or root_bounds.height != component.size.height
        or root_object.transform.rotation != 0
        or root_object.transform.opacity != 1
        or not root_object.transform.visible
    ):
        raise UnsupportedDialectFeature(
            "component-root container display state has no lossless project encoding"
        )
    context = _ComponentContext(
        package_id=package_id,
        resources={} if resources is None else resources,
        components={} if components is None else components,
        objects=objects,
        root_id=root_object.id,
        positions=_component_positions(component, root_object.id),
        mask_sources=_mask_sources(component),
    )
    root_attributes = {
        "size": _editor_int32_pair(component.size.width, component.size.height),
        "opaque": "false",
    }
    if root_object.mask_mode == MaskMode.NATIVE_CLIP:
        if (
            root_object.mask_kind == MaskKind.RECTANGLE
            and root_object.mask_object_ref == root_object.id
        ):
            root_attributes["overflow"] = "hidden"
        else:
            if root_object.mask_object_ref is None:
                raise UnsupportedDialectFeature("native clip source is missing")
            root_attributes["mask"] = root_object.mask_object_ref
    elif root_object.mask_mode == MaskMode.NATIVE_MASK:
        if root_object.mask_kind != MaskKind.IMAGE or root_object.mask_object_ref is None:
            raise UnsupportedDialectFeature("native image-mask roles are unsupported")
        root_attributes["mask"] = root_object.mask_object_ref

    xml_root = etree.Element("component", **root_attributes)
    display_list = etree.SubElement(xml_root, "displayList")

    def append_subtree(object_id: str) -> None:
        object_ = objects[object_id]
        serializer = _object_serializer(object_.type)
        is_plain_group = (
            object_.type == PlanNodeType.CONTAINER
            and object_.id not in context.mask_sources
        )
        if is_plain_group:
            background = _serialize_background_graph(object_, context)
            if background is not None:
                display_list.append(background)
            for child_id in object_.child_object_refs:
                append_subtree(child_id)
            display_list.append(serializer(object_, context))
        else:
            display_list.append(serializer(object_, context))
            for child_id in object_.child_object_refs:
                append_subtree(child_id)

    if root_object.type == PlanNodeType.CONTAINER:
        background = _serialize_background_graph(root_object, context)
        if background is not None:
            display_list.append(background)
        if root_object.id in context.mask_sources:
            display_list.append(_serialize_container(root_object, context))
        for child_id in root_object.child_object_refs:
            append_subtree(child_id)
    else:
        append_subtree(root_object.id)
    return _xml_bytes(xml_root)


def _validated_payload_content(
    manifest: NewProjectManifest,
    payloads: tuple[ValidatedAssetPayload, ...],
) -> dict[str, bytes]:
    expected = {resource.source_resource_ref: resource for resource in manifest.resources}
    if not isinstance(payloads, tuple):
        raise UnsupportedDialectFeature("validated payloads must use the immutable tuple contract")
    actual: dict[str, ValidatedAssetPayload] = {}
    for item in payloads:
        if not isinstance(item, ValidatedAssetPayload):
            raise UnsupportedDialectFeature("payload input is not a validated asset payload")
        source_id = item.resource.id
        if source_id in actual:
            raise UnsupportedDialectFeature("validated payload input contains duplicate resources")
        actual[source_id] = item
    if set(actual) != set(expected):
        raise UnsupportedDialectFeature("validated payload file set does not match the manifest")
    content: dict[str, bytes] = {}
    for source_id, manifest_resource in expected.items():
        item = actual[source_id]
        resource = item.resource
        raw = item.content
        if (
            not isinstance(raw, bytes)
            or hashlib.sha256(raw).hexdigest() != manifest_resource.content_sha256
            or resource.content_sha256 != manifest_resource.content_sha256
            or resource.export_parameters_sha256
            != manifest_resource.export_parameters_sha256
            or resource.mime_type != manifest_resource.mime_type
            or resource.export_format != manifest_resource.export_format
            or resource.width != manifest_resource.width
            or resource.height != manifest_resource.height
            or resource.nine_slice != manifest_resource.nine_slice
            or item.payload.resource_id != source_id
            or item.payload.declared_mime_type != manifest_resource.mime_type
        ):
            raise UnsupportedDialectFeature(
                f"validated payload facts do not match manifest resource {source_id}"
            )
        content[source_id] = raw
    return content


def serialize_project_files(
    manifest: NewProjectManifest,
    payloads: tuple[ValidatedAssetPayload, ...],
) -> dict[str, bytes]:
    """Serialize and independently reparse one exact in-memory project file set."""
    for component in manifest.components:
        for object_ in component.objects:
            _object_serializer(object_.type)
            _check_mask_dispatch(object_)
    manifest = _checked_manifest(manifest)
    payload_content = _validated_payload_content(manifest, payloads)
    component_index = {component.id: component for component in manifest.components}
    resource_index = {resource.id: resource for resource in manifest.resources}
    files: dict[str, bytes] = {
        f"{manifest.project.project_name}{PROJECT_SUFFIX}": serialize_project_marker(manifest),
        f"{manifest.package.relative_path}/package.xml": serialize_package_xml(manifest),
    }
    for component in manifest.components:
        path = f"{manifest.package.relative_path}/{component.relative_path}"
        files[path] = serialize_component_xml(
            component,
            package_id=manifest.package.id,
            resources=resource_index,
            components=component_index,
        )
    for resource in manifest.resources:
        path = f"{manifest.package.relative_path}/{resource.relative_path}"
        files[path] = payload_content[resource.source_resource_ref]
    canonical_files = {path: files[path] for path in sorted(files)}
    diagnostics = validate_xml_files(canonical_files)
    if diagnostics:
        raise XMLDialectValidationError(diagnostics)
    return canonical_files


class EditorDialectFixture(FrozenModel):
    project_name: str
    package_name: str
    component_names: tuple[str, ...]
    component_sizes: tuple[tuple[int, int], ...]


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


def _is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & _REPARSE_POINT)


def _reject_link_or_reparse(path: Path) -> None:
    if _is_link_or_reparse(path):
        raise ValueError("fixture contains a symlink or reparse point")


def _require_directory(path: Path, label: str) -> None:
    try:
        _reject_link_or_reparse(path)
        metadata = path.lstat()
    except OSError as error:
        raise ValueError(f"fixture {label} is missing") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"fixture {label} must be a directory")


def _require_regular_file(path: Path, label: str) -> None:
    try:
        _reject_link_or_reparse(path)
        metadata = path.lstat()
    except OSError as error:
        raise ValueError(f"fixture {label} is missing") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"fixture {label} must be a regular file")


def _parse_xml(path: Path, label: str) -> etree._Element:
    _require_regular_file(path, label)
    try:
        return etree.parse(str(path), SAFE_XML_PARSER.copy()).getroot()
    except (OSError, etree.XMLSyntaxError) as error:
        raise ValueError(f"invalid {label} XML") from error


def _validate_id(value: str | None, label: str) -> str:
    if value is None or _ID.fullmatch(value) is None:
        raise ValueError(f"invalid {label} id")
    return value


def _validate_safe_name(name: str, label: str) -> str:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or ":" in name
        or _contains_control_character(name)
    ):
        raise ValueError(f"invalid {label} name")
    return name


def _resource_relative_path(path_value: str, name: str) -> PurePosixPath:
    _validate_safe_name(name, "resource")
    if path_value.startswith("//"):
        raise ValueError("unsafe resource path")
    normalized_path = path_value.removeprefix("/")
    if (
        "\\" in normalized_path
        or _contains_control_character(normalized_path)
        or _DRIVE_PATH.match(normalized_path)
    ):
        raise ValueError("unsafe resource path")

    if normalized_path:
        without_trailing_slash = normalized_path.removesuffix("/")
        parts = without_trailing_slash.split("/")
        if not without_trailing_slash or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("unsafe resource path")
    else:
        parts = []
    return PurePosixPath(*parts, name)


def _require_contained_resource(package_root: Path, relative: PurePosixPath) -> Path:
    target = package_root.joinpath(*relative.parts)
    current = package_root
    for part in relative.parts:
        current /= part
        _require_regular_file(current, "resource") if current == target else _require_directory(
            current, "resource directory"
        )

    package_resolved = package_root.resolve(strict=True)
    try:
        target.resolve(strict=True).relative_to(package_resolved)
    except (OSError, ValueError) as error:
        raise ValueError("resource path escapes package root") from error
    return target


def _project_marker(marker_path: Path) -> None:
    marker = _parse_xml(marker_path, "project marker")
    if marker.tag != "projectDescription":
        raise ValueError("invalid project marker root")
    _validate_id(marker.attrib.get("id"), "project marker")
    if marker.attrib.get("type") != _UNITY_TARGET:
        raise ValueError("invalid project marker type")
    if marker.attrib.get("version") != _PROJECT_FILE_VERSION:
        raise ValueError("invalid project marker version")


def _component_details(path: Path, expected_name: str) -> tuple[str, tuple[int, int]]:
    component = _parse_xml(path, "component")
    if component.tag != "component":
        raise ValueError("invalid component root")
    xml_name = component.attrib.get("name")
    if xml_name is not None:
        _validate_safe_name(xml_name, "component")
    size = component.attrib.get("size", "")
    match = _SIZE.fullmatch(size)
    if match is None:
        raise ValueError("invalid component size")
    if len(component.findall("displayList")) != 1:
        raise ValueError("invalid component displayList")
    return expected_name, (int(match.group(1)), int(match.group(2)))


def parse_editor_fixture(root: Path) -> EditorDialectFixture:
    _require_directory(root, "root")
    markers = tuple(path for path in root.iterdir() if path.name.endswith(PROJECT_SUFFIX))
    assets = root / ASSETS_DIRECTORY
    _require_directory(assets, "assets directory")

    manifests: list[Path] = []
    for candidate in assets.iterdir():
        _reject_link_or_reparse(candidate)
        if not candidate.is_dir():
            continue
        manifest = candidate / "package.xml"
        if manifest.exists() or manifest.is_symlink():
            manifests.append(manifest)

    if len(markers) != 1 or len(manifests) != 1:
        raise ValueError("fixture must contain one project marker and one package")
    marker_path = markers[0]
    manifest_path = manifests[0]
    _project_marker(marker_path)

    package_root = manifest_path.parent
    package = _parse_xml(manifest_path, "package")
    if package.tag != "packageDescription":
        raise ValueError("invalid package root")
    _validate_id(package.attrib.get("id"), "package")
    resource_elements = package.findall("resources")
    publish_elements = package.findall("publish")
    if len(resource_elements) != 1:
        raise ValueError("invalid package resources")
    if len(publish_elements) != 1:
        raise ValueError("invalid package publish")
    publish = publish_elements[0]
    if len(publish) != 0 or (publish.text is not None and publish.text.strip()):
        raise ValueError("invalid package publish structure")

    resources = resource_elements[0]
    resource_ids: set[str] = set()
    components: list[tuple[Path, str]] = []
    for resource in resources:
        resource_id = _validate_id(resource.attrib.get("id"), "package resource")
        if resource_id in resource_ids:
            raise ValueError("duplicate package resource id")
        resource_ids.add(resource_id)
        name = resource.attrib.get("name", "")
        relative = _resource_relative_path(resource.attrib.get("path", ""), name)
        target = _require_contained_resource(package_root, relative)
        if resource.tag == "component":
            components.append((target, Path(name).stem))

    component_details = tuple(
        _component_details(component_path, expected_name)
        for component_path, expected_name in components
    )
    return EditorDialectFixture(
        project_name=marker_path.stem,
        package_name=package_root.name,
        component_names=tuple(name for name, _size in component_details),
        component_sizes=tuple(size for _name, size in component_details),
    )
