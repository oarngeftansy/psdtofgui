"""Pure validation and canonical serialization for new-project manifests."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import unicodedata
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import TypeAlias, cast
from zipfile import BadZipFile, ZipFile

from lxml import etree

from figma_to_fgui.data_policy import private_data_violations
from figma_to_fgui.fgui_asset_payloads import diagnostic_sort_key
from figma_to_fgui.fgui_new_project_ids import (
    PACKAGE_DIRECTORIES,
    ComponentSourceKey,
    TargetIdAllocator,
    TargetIdCollisionError,
    TargetNamingError,
    component_logical_key,
    component_path,
    object_logical_key,
    package_logical_key,
    resource_logical_key,
    resource_path,
    validate_target_name,
    validate_unique_target_paths,
)
from figma_to_fgui.fgui_new_project_models import (
    ManifestComponent,
    ManifestObject,
    NewProjectManifest,
)
from figma_to_fgui.fgui_plan_models import MaskKind, MaskMode, PlanNodeType
from figma_to_fgui.models import Diagnostic, Severity

MAX_MANIFEST_DEPTH = 256
MAX_MANIFEST_COMPONENT_DEPTH = MAX_MANIFEST_DEPTH
_TARGET_ID = re.compile(r"^[0-9a-f]{8}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_NODE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_PUBLIC_PATH = re.compile(r"^\$[A-Za-z0-9_.|\[\]-]{0,255}$")
_RESOURCE_OBJECT_TYPES = frozenset(
    {PlanNodeType.IMAGE, PlanNodeType.LOADER, PlanNodeType.RASTER_SUBTREE}
)
_TEXT_OBJECT_TYPES = frozenset({PlanNodeType.TEXT, PlanNodeType.RICH_TEXT})
_MISSING = object()

ExactBuiltinHeader: TypeAlias = tuple[str, str, type[object], object]
_CONFIG_HEADERS: tuple[ExactBuiltinHeader, ...] = (
    ("fairy_gui_version", "fairyGuiVersion", str, "6.1.4"),
    ("publish_target", "publishTarget", str, "unity"),
    ("naming_policy_version", "namingPolicyVersion", int, 1),
)
_MANIFEST_HEADERS: tuple[ExactBuiltinHeader, ...] = (("schema_version", "schemaVersion", int, 1),)


class NewProjectManifestError(Exception):
    """A manifest failed the final pure in-memory gate."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("New FairyGUI project manifest validation failed.")


def _exact_builtin_header_failure(
    value: object,
    headers: tuple[ExactBuiltinHeader, ...],
    *,
    payload: bool = False,
) -> str | None:
    """Return the first non-exact header without coercion or hostile comparison."""
    try:
        if payload:
            if type(value) is not dict:
                return "$"
            values = value
            for field_name, alias, expected_type, expected in headers:
                current = values.get(alias, _MISSING)
                if type(current) is not expected_type or current != expected:
                    return field_name
            return None
        for field_name, _, expected_type, expected in headers:
            current = getattr(value, field_name, _MISSING)
            if type(current) is not expected_type or current != expected:
                return field_name
    except Exception:  # noqa: BLE001 - hostile runtime attributes fail closed.
        return "$"
    return None


def _manifest_header_failure(manifest: object, *, payload: bool = False) -> str | None:
    failure = _exact_builtin_header_failure(manifest, _MANIFEST_HEADERS, payload=payload)
    if failure is not None:
        return failure
    try:
        project = (
            manifest.get("project", _MISSING)
            if payload and type(manifest) is dict
            else getattr(manifest, "project", _MISSING)
        )
    except Exception:  # noqa: BLE001 - hostile runtime attributes fail closed.
        return "$"
    return _exact_builtin_header_failure(project, _CONFIG_HEADERS, payload=payload)


def _is_visible_text_content(path: tuple[str | int, ...]) -> bool:
    return (
        len(path) == 6
        and path[0] == "components"
        and type(path[1]) is int
        and path[2] == "objects"
        and type(path[3]) is int
        and path[4:] == ("text", "content")
    ) or (
        len(path) == 8
        and path[0] == "components"
        and type(path[1]) is int
        and path[2] == "objects"
        and type(path[3]) is int
        and path[4:6] == ("text", "runs")
        and type(path[6]) is int
        and path[7] == "content"
    )


def _manifest_public_data_is_closed(payload: object) -> bool:
    """Apply public-data policy to every string except visible text content."""
    pending: list[tuple[object, tuple[str | int, ...]]] = [(payload, ())]
    while pending:
        current, path = pending.pop()
        if isinstance(current, Mapping):
            for key, nested in current.items():
                if not isinstance(key, str) or private_data_violations({key: None}):
                    return False
                pending.append((nested, (*path, key)))
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            pending.extend((nested, (*path, index)) for index, nested in enumerate(current))
        elif (
            isinstance(current, str)
            and not _is_visible_text_content(path)
            and private_data_violations({current: None})
        ):
            return False
    return True


def _diagnostic(
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    path: str | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=Severity.ERROR,
        message=message,
        node_id=node_id,
        path=path,
        rule_id=code,
        rule_version=1,
        evidence=(f"rule={code}",),
        suggested_action="Repair the manifest before serialization.",
        blocks_binding=True,
    )


def _append_once(
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    path: str | None = None,
) -> None:
    node_id = _safe_public_locator(node_id, _PUBLIC_NODE_REF)
    path = _safe_public_locator(path, _PUBLIC_PATH)
    key = (code, node_id, path)
    if key in seen:
        return
    seen.add(key)
    diagnostics.append(_diagnostic(code, message, node_id=node_id, path=path))


def _safe_public_locator(value: str | None, pattern: re.Pattern[str]) -> str | None:
    if value is None or pattern.fullmatch(value) is None:
        return None
    if private_data_violations({value: None}):
        return None
    return value


def _validate_target_ids(
    manifest: NewProjectManifest,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    owners: dict[str, str] = {}
    entries: list[tuple[str, str, str]] = [(manifest.package.id, "package", "$.package.id")]
    entries.extend(
        (component.id, "component", f"$.components.{index}.id")
        for index, component in enumerate(manifest.components)
    )
    entries.extend(
        (resource.id, "resource", f"$.resources.{index}.id")
        for index, resource in enumerate(manifest.resources)
    )
    for component_index, component in enumerate(manifest.components):
        entries.extend(
            (
                object_.id,
                "object",
                f"$.components.{component_index}.objects.{object_index}.id",
            )
            for object_index, object_ in enumerate(component.objects)
        )

    counts: dict[str, int] = defaultdict(int)
    for target_id, _, _ in entries:
        counts[target_id] += 1
    for target_id, kind, path in entries:
        if not isinstance(target_id, str) or _TARGET_ID.fullmatch(target_id) is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.target_id_invalid",
                "Every target identity must use the fixed Writer v1 format.",
                node_id=target_id,
                path=path,
            )
        previous_kind = owners.setdefault(target_id, kind)
        if previous_kind != kind or counts[target_id] > 1:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.target_id_conflict",
                "A target identity is declared more than once.",
                node_id=target_id,
            )


def _validate_paths(
    manifest: NewProjectManifest,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    package_path = PurePosixPath(manifest.package.relative_path)
    try:
        validate_unique_target_paths((package_path,))
    except TargetNamingError:
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.package_path_invalid",
            "The generated package path is not a safe relative target path.",
            path="$.package.relativePath",
        )
    expected_package_path = PurePosixPath("assets", manifest.package.name)
    if package_path != expected_package_path:
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.package_path_incoherent",
            "The package path must match the generated package name.",
            path="$.package.relativePath",
        )

    file_paths = tuple(
        PurePosixPath(component.relative_path) for component in manifest.components
    ) + tuple(PurePosixPath(resource.relative_path) for resource in manifest.resources)
    try:
        validate_unique_target_paths(file_paths)
    except TargetNamingError:
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.target_path_invalid",
            "Generated component and resource paths must be safe and unique.",
            path="$.components|$.resources",
        )

    for index, component in enumerate(manifest.components):
        path = PurePosixPath(component.relative_path)
        try:
            expected = component_path(
                validate_target_name(component.name, "component"),
                component.id,
                component.source_component_kind,
            )
        except TargetNamingError:
            expected = None
        if path != expected:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.component_path_incoherent",
                "Root components must use Panel and definitions must use Component.",
                node_id=component.id,
                path=f"$.components.{index}.relativePath",
            )
    for index, resource in enumerate(manifest.resources):
        path = PurePosixPath(resource.relative_path)
        try:
            expected = resource_path(
                validate_target_name(resource.name, "resource"), resource.id
            )
        except TargetNamingError:
            expected = None
        if (
            resource.export_format != "png"
            or resource.mime_type != "image/png"
            or path != expected
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.resource_path_incoherent",
                "Image resources must be PNG files directly inside the Img directory.",
                node_id=resource.id,
                path=f"$.resources.{index}.relativePath",
            )


def _object_tables(
    manifest: NewProjectManifest,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> tuple[dict[str, ManifestObject], dict[str, ManifestComponent]]:
    objects: dict[str, ManifestObject] = {}
    object_owners: dict[str, ManifestComponent] = {}
    component_ids: set[str] = set()
    component_sources: set[ComponentSourceKey] = set()
    for component in manifest.components:
        if component.id in component_ids:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.component_id_conflict",
                "A generated component identity is declared more than once.",
                node_id=component.id,
            )
        component_ids.add(component.id)
        component_source: ComponentSourceKey = (
            component.source_component_kind,
            component.source_component_ref,
        )
        if component_source in component_sources:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.component_source_conflict",
                "A source component is compiled more than once.",
                node_id=component.source_component_ref,
            )
        component_sources.add(component_source)

        local_sources: set[str] = set()
        local_uir_sources: set[str] = set()
        for object_ in component.objects:
            if object_.id in object_owners:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.object_owner_conflict",
                    "A manifest object must be owned by exactly one component.",
                    node_id=object_.id,
                )
            else:
                objects[object_.id] = object_
                object_owners[object_.id] = component
            if object_.source_node_ref in local_sources:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.object_source_conflict",
                    "A source node cannot be emitted twice in one component.",
                    node_id=object_.source_node_ref,
                )
            local_sources.add(object_.source_node_ref)
            if object_.uir_node_ref in local_uir_sources:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.object_uir_source_conflict",
                    "A UIR node cannot be emitted twice in one component.",
                    node_id=object_.uir_node_ref,
                )
            local_uir_sources.add(object_.uir_node_ref)
    return objects, object_owners


def _validate_key_id_agreement(
    manifest: NewProjectManifest,
    objects: dict[str, ManifestObject],
    object_owners: dict[str, ManifestComponent],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    try:
        declarations: list[tuple[str, str, str, str | None]] = [
            (
                "package",
                package_logical_key(manifest.package.source_document_ref, manifest.package.name),
                manifest.package.id,
                "$.package.id",
            )
        ]
        for component in manifest.components:
            source: ComponentSourceKey = (
                component.source_component_kind,
                component.source_component_ref,
            )
            declarations.append(("component", component_logical_key(source), component.id, None))
        for object_id, object_ in objects.items():
            owner = object_owners[object_id]
            source = (owner.source_component_kind, owner.source_component_ref)
            declarations.append(
                ("object", object_logical_key(source, object_.source_node_ref), object_id, None)
            )
        for resource in manifest.resources:
            declarations.append(
                (
                    "resource",
                    resource_logical_key(
                        resource.source_resource_ref,
                        resource.content_sha256,
                        resource.export_parameters_sha256,
                    ),
                    resource.id,
                    None,
                )
            )
        expected = TargetIdAllocator().allocate_all(
            (kind, logical_key) for kind, logical_key, _, _ in declarations
        )
    except (TargetIdCollisionError, TargetNamingError):
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.target_identity_policy_invalid",
            "Manifest logical identities violate the Writer v1 allocation policy.",
            path="$.package|$.components|$.resources",
        )
        return
    for kind, logical_key, target_id, path in declarations:
        if target_id != expected[(kind, logical_key)]:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.target_id_key_mismatch",
                "A target identity does not agree with its canonical logical key.",
                node_id=target_id,
                path=path,
            )


def _validate_component_tree(
    component: ManifestComponent,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    local: dict[str, ManifestObject] = {}
    for object_ in component.objects:
        local.setdefault(object_.id, object_)
    if not local:
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.component_empty",
            "Every generated component must own one object tree.",
            node_id=component.id,
        )
        return

    child_owners: dict[str, str] = {}
    for object_ in component.objects:
        if object_.parent_object_ref is not None and object_.parent_object_ref not in local:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.parent_missing",
                "An object parent reference must remain in the owning component.",
                node_id=object_.id,
            )
        local_children: set[str] = set()
        for child_index, child_id in enumerate(object_.child_object_refs):
            child = local.get(child_id)
            if child is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.child_missing",
                    "An object child reference must remain in the owning component.",
                    node_id=object_.id,
                )
                continue
            previous = child_owners.get(child_id)
            if child_id in local_children or previous is not None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.child_owner_conflict",
                    "A child object must have exactly one parent.",
                    node_id=child_id,
                )
            else:
                local_children.add(child_id)
                child_owners[child_id] = object_.id
            if child.parent_object_ref != object_.id:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.parent_child_mismatch",
                    "Parent and child object references must be symmetric.",
                    node_id=child.id,
                )
            if child.z_index != child_index:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.child_order_incoherent",
                    "Child order must agree exactly with z-index values.",
                    node_id=child.id,
                )

    roots = [object_ for object_ in component.objects if object_.parent_object_ref is None]
    if len(roots) != 1:
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.component_root_incoherent",
            "Every generated component must have exactly one object root.",
            node_id=component.id,
        )
    for object_ in component.objects:
        if (
            object_.parent_object_ref is not None
            and object_.id not in child_owners
            and object_.parent_object_ref in local
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.parent_child_mismatch",
                "Parent and child object references must be symmetric.",
                node_id=object_.id,
            )

    colors: dict[str, int] = {}
    cycle_found = False
    for start_id in sorted(local):
        if colors.get(start_id, 0) != 0:
            continue
        colors[start_id] = 1
        stack: list[tuple[str, int]] = [(start_id, 0)]
        while stack:
            object_id, child_index = stack[-1]
            children = local[object_id].child_object_refs
            if child_index >= len(children):
                colors[object_id] = 2
                stack.pop()
                continue
            child_id = children[child_index]
            stack[-1] = (object_id, child_index + 1)
            if child_id not in local:
                continue
            color = colors.get(child_id, 0)
            if color == 0:
                colors[child_id] = 1
                stack.append((child_id, 0))
            elif color == 1:
                cycle_found = True
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.object_cycle",
                    "Manifest child references cannot contain a cycle.",
                    node_id=child_id,
                )

    if not cycle_found:
        indegree = {object_id: 0 for object_id in local}
        depths: dict[str, int] = {}
        for object_ in local.values():
            for child_id in object_.child_object_refs:
                if child_id in local:
                    indegree[child_id] += 1
        ready = deque(sorted(object_id for object_id, degree in indegree.items() if degree == 0))
        for object_id in ready:
            depths[object_id] = 1
        longest = 0
        while ready:
            object_id = ready.popleft()
            depth = depths[object_id]
            longest = max(longest, depth)
            for child_id in local[object_id].child_object_refs:
                if child_id not in local:
                    continue
                depths[child_id] = max(depths.get(child_id, 1), depth + 1)
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    ready.append(child_id)
        if longest > MAX_MANIFEST_DEPTH:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_depth_exceeded",
                "The manifest object tree exceeds the supported depth.",
                node_id=component.id,
            )

    if len(roots) == 1:
        reachable: set[str] = set()
        pending = [roots[0].id]
        preorder: list[str] = []
        while pending:
            object_id = pending.pop()
            if object_id in reachable:
                continue
            reachable.add(object_id)
            preorder.append(object_id)
            pending.extend(
                reversed(
                    tuple(
                        child_id
                        for child_id in local[object_id].child_object_refs
                        if child_id in local
                    )
                )
            )
        for object_id in sorted(set(local) - reachable):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_unreachable",
                "Every object must be reachable from its component root.",
                node_id=object_id,
            )

        if tuple(preorder) != tuple(object_.id for object_ in component.objects):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_order_incoherent",
                "Component objects must use canonical display-tree preorder.",
                node_id=component.id,
            )


def _validate_object_payloads(
    manifest: NewProjectManifest,
    objects: dict[str, ManifestObject],
    object_owners: dict[str, ManifestComponent],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    component_ids = {component.id for component in manifest.components}
    resource_ids = {resource.id for resource in manifest.resources}
    emitted_sources = {object_.uir_node_ref for object_ in objects.values()}
    raster_owner: dict[str, str] = {}

    for object_id in sorted(objects):
        object_ = objects[object_id]
        owner = object_owners[object_id]
        bounds = object_.transform.bounds
        if (
            object_.z_index < 0
            or not all(
                math.isfinite(value)
                for value in (
                    bounds.x,
                    bounds.y,
                    bounds.width,
                    bounds.height,
                    object_.transform.rotation,
                    object_.transform.opacity,
                )
            )
            or bounds.width < 0
            or bounds.height < 0
            or not 0 <= object_.transform.opacity <= 1
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_geometry_invalid",
                "Manifest object geometry and display values must be finite and bounded.",
                node_id=object_id,
            )
        if object_.resource_ref is not None and object_.resource_ref not in resource_ids:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.resource_reference_missing",
                "An object resource reference must resolve in the manifest.",
                node_id=object_id,
            )
        if object_.type in _RESOURCE_OBJECT_TYPES and object_.resource_ref is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.resource_reference_missing",
                "A resource-bearing object requires a generated resource target.",
                node_id=object_id,
            )
        elif object_.type not in _RESOURCE_OBJECT_TYPES and object_.resource_ref is not None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_payload_incoherent",
                "This object type cannot own a resource target.",
                node_id=object_id,
            )
        if object_.type in _TEXT_OBJECT_TYPES and object_.text is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_payload_incoherent",
                "Text objects require typed text content.",
                node_id=object_id,
            )
        elif object_.type not in _TEXT_OBJECT_TYPES and object_.text is not None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_payload_incoherent",
                "Only text objects may carry typed text content.",
                node_id=object_id,
            )
        if object_.type == PlanNodeType.GRAPH and object_.graph is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_payload_incoherent",
                "Graph objects require typed graph content.",
                node_id=object_id,
            )
        elif object_.type != PlanNodeType.GRAPH and object_.graph is not None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_payload_incoherent",
                "Only graph objects may carry typed graph content.",
                node_id=object_id,
            )
        if object_.component_ref is not None and object_.component_ref not in component_ids:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.component_reference_missing",
                "An object component reference must resolve in the manifest.",
                node_id=object_id,
            )
        if object_.type == PlanNodeType.COMPONENT_REFERENCE and object_.component_ref is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.component_reference_missing",
                "A component-reference object requires a generated component target.",
                node_id=object_id,
            )
        elif object_.type != PlanNodeType.COMPONENT_REFERENCE and object_.component_ref is not None:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_payload_incoherent",
                "Only component-reference objects may target generated components.",
                node_id=object_id,
            )
        if object_.type == PlanNodeType.RASTER_SUBTREE and object_.child_object_refs:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.raster_descendant_duplicate",
                "Raster-subtree objects cannot retain emitted descendants.",
                node_id=object_id,
            )
        if object_.raster_consumed_node_refs and object_.type != PlanNodeType.RASTER_SUBTREE:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.object_payload_incoherent",
                "Only raster-subtree objects may consume source descendants.",
                node_id=object_id,
            )

        mask_values = (
            object_.mask_mode,
            object_.mask_kind,
            object_.mask_object_ref,
            object_.mask_content_object_refs,
            object_.mask_corner_radii,
        )
        if object_.mask_mode is None and any(value not in (None, ()) for value in mask_values[1:]):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.mask_incoherent",
                "Mask role fields require an explicit mask mode.",
                node_id=object_id,
            )
        elif object_.mask_mode in {MaskMode.NATIVE_CLIP, MaskMode.NATIVE_MASK}:
            if object_.mask_kind is None or object_.mask_object_ref is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.mask_incoherent",
                    "Native masks require complete source and kind roles.",
                    node_id=object_id,
                )
            refs = (
                () if object_.mask_object_ref is None else (object_.mask_object_ref,)
            ) + object_.mask_content_object_refs
            if len(refs) != len(set(refs)):
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.mask_reference_duplicate",
                    "Native mask participants must be unique.",
                    node_id=object_id,
                )
            for ref in refs:
                if ref not in objects or object_owners.get(ref) is not owner:
                    _append_once(
                        diagnostics,
                        seen,
                        "fgui.writer.manifest.mask_reference_missing",
                        "Native mask participants must resolve in the owning component.",
                        node_id=object_id,
                    )
            implicit_self_clip = (
                object_.mask_mode == MaskMode.NATIVE_CLIP and object_.mask_object_ref == object_id
            )
            local = {item.id: item for item in owner.objects}
            source = local.get(object_.mask_object_ref or "")
            if source is not None and (
                source.transform.bounds.width <= 0 or source.transform.bounds.height <= 0
            ):
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.mask_source_geometry_incoherent",
                    "Native mask sources require positive finite dimensions.",
                    node_id=object_id,
                )
            allowed_target_types = (
                {PlanNodeType.CONTAINER, PlanNodeType.GRAPH}
                if object_.mask_mode == MaskMode.NATIVE_CLIP
                else {PlanNodeType.CONTAINER}
            )
            if object_.type not in allowed_target_types or not object_.mask_content_object_refs:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.mask_target_incoherent",
                    "Native mask targets must use the declared non-empty target shape.",
                    node_id=object_id,
                )
            if object_.mask_mode == MaskMode.NATIVE_CLIP:
                if (
                    object_.mask_kind not in {MaskKind.RECTANGLE, MaskKind.ROUNDED_RECTANGLE}
                    or source is None
                    or source.type not in {PlanNodeType.CONTAINER, PlanNodeType.GRAPH}
                    or (source.type == PlanNodeType.GRAPH and source.graph is None)
                    or source.resource_ref is not None
                    or object_.resource_ref is not None
                ):
                    _append_once(
                        diagnostics,
                        seen,
                        "fgui.writer.manifest.mask_role_incoherent",
                        "Native clips require a resource-free rectangle source on the target.",
                        node_id=object_id,
                    )
            elif (
                object_.mask_kind != MaskKind.IMAGE
                or source is None
                or source.type != PlanNodeType.IMAGE
                or source.resource_ref is None
            ):
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.mask_role_incoherent",
                    "Native masks require an image source backed by a resource.",
                    node_id=object_id,
                )
            radii = object_.mask_corner_radii
            if object_.mask_kind == MaskKind.ROUNDED_RECTANGLE:
                radius_bounds = bounds if source is None else source.transform.bounds
                limit = min(radius_bounds.width, radius_bounds.height) / 2
                if radii is None or any(
                    not math.isfinite(radius) or radius < 0 or radius > limit for radius in radii
                ):
                    _append_once(
                        diagnostics,
                        seen,
                        "fgui.writer.manifest.mask_radii_incoherent",
                        "Rounded clips require four finite radii within the target bounds.",
                        node_id=object_id,
                    )
            elif radii is not None and any(radii):
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.mask_radii_incoherent",
                    "Only rounded-rectangle clips may declare corner radii.",
                    node_id=object_id,
                )
            participants = object_.mask_content_object_refs if implicit_self_clip else refs
            if any(
                ref not in local or local[ref].parent_object_ref != object_id
                for ref in participants
            ):
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.mask_scope_incoherent",
                    "Native mask participants must be direct children of the target.",
                    node_id=object_id,
                )
            elif participants:
                try:
                    start = object_.child_object_refs.index(participants[0])
                except ValueError:
                    start = -1
                if (
                    start < 0
                    or object_.child_object_refs[start : start + len(participants)] != participants
                ):
                    _append_once(
                        diagnostics,
                        seen,
                        "fgui.writer.manifest.mask_order_incoherent",
                        "Native mask participants must retain their contiguous display order.",
                        node_id=object_id,
                    )
            if object_.parent_object_ref is None:
                # FairyGUI's component-level overflow/mask applies to every emitted
                # display object.  The source itself is not mask content: a self-clip
                # root is not emitted, while graph/image sources remain visible only
                # to define the component mask.
                affected_display_ids = set(object_.child_object_refs) - {
                    object_.mask_object_ref
                }
                if set(object_.mask_content_object_refs) != affected_display_ids:
                    _append_once(
                        diagnostics,
                        seen,
                        "fgui.writer.manifest.mask_scope_incoherent",
                        "A root native mask must cover every affected display object.",
                        node_id=object_id,
                    )
            if object_.raster_consumed_node_refs:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.mask_incoherent",
                    "Native masks cannot declare raster-consumed descendants.",
                    node_id=object_id,
                )
        elif object_.mask_mode == MaskMode.RASTER_SUBTREE and (
            object_.type != PlanNodeType.RASTER_SUBTREE
            or object_.mask_kind is None
            or object_.mask_object_ref is not None
            or object_.mask_content_object_refs
            or object_.mask_corner_radii is not None
            or not object_.raster_consumed_node_refs
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.mask_incoherent",
                "Raster masks require one raster object and source-node consumption facts.",
                node_id=object_id,
            )

        if len(object_.raster_consumed_node_refs) != len(set(object_.raster_consumed_node_refs)):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.raster_descendant_duplicate",
                "A raster subtree cannot consume the same descendant more than once.",
                node_id=object_id,
            )
        for source_ref in object_.raster_consumed_node_refs:
            previous = raster_owner.setdefault(source_ref, object_id)
            if previous != object_id or source_ref in emitted_sources:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.raster_descendant_duplicate",
                    "A raster-consumed descendant cannot be emitted or consumed again.",
                    node_id=source_ref,
                )


def _validate_resources(
    manifest: NewProjectManifest,
    objects: dict[str, ManifestObject],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    resource_sources: set[str] = set()
    resource_ids: set[str] = set()
    for resource in manifest.resources:
        if resource.id in resource_ids:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.resource_id_conflict",
                "A generated resource identity is declared more than once.",
                node_id=resource.id,
            )
        resource_ids.add(resource.id)
        if resource.source_resource_ref in resource_sources:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.resource_source_conflict",
                "A source resource is compiled more than once.",
                node_id=resource.source_resource_ref,
            )
        resource_sources.add(resource.source_resource_ref)
        consumers = resource.consumer_object_refs
        if not consumers:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.resource_unconsumed",
                "Every generated resource must have at least one consumer.",
                node_id=resource.id,
            )
        if len(consumers) != len(set(consumers)):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.resource_consumer_duplicate",
                "Resource consumers must be unique.",
                node_id=resource.id,
            )
        for consumer_id in consumers:
            consumer = objects.get(consumer_id)
            if consumer is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.resource_consumer_missing",
                    "Every declared resource consumer must exist.",
                    node_id=consumer_id,
                )
            elif consumer.resource_ref != resource.id:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.resource_consumer_mismatch",
                    "Resource declarations and object references must be reciprocal.",
                    node_id=consumer_id,
                )

    consumers_by_resource: dict[str, set[str]] = defaultdict(set)
    for object_ in objects.values():
        if object_.resource_ref is not None:
            consumers_by_resource[object_.resource_ref].add(object_.id)
    for resource in manifest.resources:
        if consumers_by_resource[resource.id] != set(resource.consumer_object_refs):
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.resource_consumer_mismatch",
                "Resource declarations and object references must be reciprocal.",
                node_id=resource.id,
            )


def _validate_component_graph(
    manifest: NewProjectManifest,
    objects: dict[str, ManifestObject],
    object_owners: dict[str, ManifestComponent],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    graph: dict[str, set[str]] = {component.id: set() for component in manifest.components}
    components_by_id = {component.id: component for component in manifest.components}
    component_indexes = {component.id: index for index, component in enumerate(manifest.components)}
    for object_id, object_ in objects.items():
        if object_.component_ref not in graph:
            continue
        owner_id = object_owners[object_id].id
        graph[owner_id].add(object_.component_ref)
        if components_by_id[object_.component_ref].source_component_kind != "definition":
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.component_reference_role_incoherent",
                "Component references may target generated definitions only.",
                node_id=object_id,
            )
        if component_indexes[object_.component_ref] >= component_indexes[owner_id]:
            _append_once(
                diagnostics,
                seen,
                "fgui.writer.manifest.component_order_incoherent",
                "Referenced component definitions must precede their consumers.",
                node_id=object_id,
            )

    definitions = {
        component.id
        for component in manifest.components
        if component.source_component_kind == "definition"
    }
    dependency_sets = {
        component_id: graph[component_id] & definitions for component_id in definitions
    }
    dependents: dict[str, set[str]] = defaultdict(set)
    remaining = {
        component_id: len(dependencies) for component_id, dependencies in dependency_sets.items()
    }
    for component_id, dependencies in dependency_sets.items():
        for dependency in dependencies:
            dependents[dependency].add(component_id)
    ready_definitions = sorted(
        (component_id for component_id, count in remaining.items() if count == 0),
        reverse=True,
    )
    ordered_definitions: list[str] = []
    while ready_definitions:
        component_id = ready_definitions.pop()
        ordered_definitions.append(component_id)
        for dependent in sorted(dependents[component_id]):
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                ready_definitions.append(dependent)
                ready_definitions.sort(reverse=True)
    expected_order = tuple(ordered_definitions) + tuple(
        sorted(
            component.id
            for component in manifest.components
            if component.source_component_kind == "root"
        )
    )
    if len(expected_order) == len(manifest.components) and expected_order != tuple(
        component.id for component in manifest.components
    ):
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.component_order_incoherent",
            "Definitions must use canonical dependency order before canonical root order.",
            path="$.components",
        )

    colors: dict[str, int] = {}
    for start_id in sorted(graph):
        if colors.get(start_id, 0) != 0:
            continue
        colors[start_id] = 1
        stack: list[tuple[str, tuple[str, ...], int]] = [
            (start_id, tuple(sorted(graph[start_id])), 0)
        ]
        while stack:
            component_id, edges, edge_index = stack[-1]
            if edge_index >= len(edges):
                colors[component_id] = 2
                stack.pop()
                continue
            target_id = edges[edge_index]
            stack[-1] = (component_id, edges, edge_index + 1)
            color = colors.get(target_id, 0)
            if color == 0:
                colors[target_id] = 1
                stack.append((target_id, tuple(sorted(graph[target_id])), 0))
            elif color == 1:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.writer.manifest.component_cycle",
                    "Generated component references cannot be recursive.",
                    node_id=target_id,
                )

    indegree = {component_id: 0 for component_id in graph}
    for dependencies in graph.values():
        for dependency in dependencies:
            indegree[dependency] += 1
    ready = deque(sorted(key for key, value in indegree.items() if value == 0))
    depths = {component_id: 1 for component_id in ready}
    longest = 0
    processed = 0
    while ready:
        component_id = ready.popleft()
        processed += 1
        depth = depths[component_id]
        longest = max(longest, depth)
        for dependency in sorted(graph[component_id]):
            depths[dependency] = max(depths.get(dependency, 1), depth + 1)
            indegree[dependency] -= 1
            if indegree[dependency] == 0:
                ready.append(dependency)
    if processed == len(graph) and longest > MAX_MANIFEST_COMPONENT_DEPTH:
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.component_depth_exceeded",
            "Generated component references exceed the supported depth.",
            path="$.components",
        )


def validate_new_project_manifest(
    manifest: NewProjectManifest,
) -> tuple[Diagnostic, ...]:
    """Return all independently detectable manifest errors in stable public order."""
    if _manifest_header_failure(manifest) is not None:
        return (
            _diagnostic(
                "fgui.writer.manifest.schema_invalid",
                "The manifest does not satisfy its strict public schema.",
            ),
        )
    try:
        payload = manifest.model_dump(mode="json", by_alias=True, warnings="error")
        if _manifest_header_failure(
            payload, payload=True
        ) is not None or not _manifest_public_data_is_closed(payload):
            raise ValueError("invalid manifest public contract")
        manifest = NewProjectManifest.model_validate(payload)
        validate_target_name(manifest.project.project_name, "project")
    except Exception:  # noqa: BLE001 - contain arbitrary model_copy serializer corruption.
        return (
            _diagnostic(
                "fgui.writer.manifest.schema_invalid",
                "The manifest does not satisfy its strict public schema.",
            ),
        )
    diagnostics: list[Diagnostic] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    if manifest.package.name != manifest.project.package_name:
        _append_once(
            diagnostics,
            seen,
            "fgui.writer.manifest.package_name_incoherent",
            "Manifest package metadata must agree with project configuration.",
            path="$.package.name",
        )
    _validate_target_ids(manifest, diagnostics, seen)
    _validate_paths(manifest, diagnostics, seen)
    objects, object_owners = _object_tables(manifest, diagnostics, seen)
    _validate_key_id_agreement(manifest, objects, object_owners, diagnostics, seen)
    for component in manifest.components:
        _validate_component_tree(component, diagnostics, seen)
    _validate_object_payloads(manifest, objects, object_owners, diagnostics, seen)
    _validate_resources(manifest, objects, diagnostics, seen)
    _validate_component_graph(manifest, objects, object_owners, diagnostics, seen)
    return tuple(sorted(diagnostics, key=diagnostic_sort_key))


def canonical_manifest_bytes(manifest: NewProjectManifest) -> bytes:
    """Serialize a byte-free manifest with stable keys and whitespace."""
    diagnostics = validate_new_project_manifest(manifest)
    if diagnostics:
        raise NewProjectManifestError(diagnostics)
    try:
        raw_payload = manifest.model_dump(mode="json", by_alias=True, warnings="error")
        if _manifest_header_failure(
            raw_payload, payload=True
        ) is not None or not _manifest_public_data_is_closed(raw_payload):
            raise ValueError("invalid manifest public contract")
        canonical = NewProjectManifest.model_validate(raw_payload)
        validate_target_name(canonical.project.project_name, "project")
        payload = canonical.model_dump(mode="json", by_alias=True, warnings="error")
        if _manifest_header_failure(
            payload, payload=True
        ) is not None or not _manifest_public_data_is_closed(payload):
            raise ValueError("invalid manifest public contract")
        serialized = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    except Exception:  # noqa: BLE001 - race-free immutable input can still be corrupted.
        raise NewProjectManifestError(
            (
                _diagnostic(
                    "fgui.writer.manifest.schema_invalid",
                    "The manifest does not satisfy its strict public schema.",
                ),
            )
        ) from None
    return serialized


def validate_xml_files(files: Mapping[str, bytes]) -> tuple[Diagnostic, ...]:
    """Validate the generated FairyGUI XML/file closure."""
    diagnostics: list[Diagnostic] = []
    seen: set[tuple[str, str | None]] = set()

    def append(code: str, message: str, path: str | None = None) -> None:
        safe_path = (
            path
            if path is not None
            and _WRITER_FILE_PATH.fullmatch(path)
            and not private_data_violations({path: None})
            else None
        )
        key = (code, safe_path)
        if key in seen:
            return
        seen.add(key)
        diagnostics.append(
            Diagnostic(
                code=code,
                severity=Severity.ERROR,
                message=message,
                path=safe_path,
                rule_id=code,
                rule_version=1,
                evidence=(f"rule={code}",),
                suggested_action="Regenerate the project from a validated manifest and payload set.",
                blocks_binding=True,
            )
        )

    if not isinstance(files, Mapping):
        append("fgui.writer.xml.file_set_incoherent", "Generated files must be one path mapping.")
        return tuple(sorted(diagnostics, key=diagnostic_sort_key))

    normalized: dict[str, bytes] = {}
    comparison_paths: set[tuple[str, ...]] = set()
    try:
        items = tuple(files.items())
    except Exception:  # noqa: BLE001 - hostile mapping fails closed.
        append(
            "fgui.writer.xml.file_set_incoherent", "Generated files cannot be enumerated safely."
        )
        return tuple(sorted(diagnostics, key=diagnostic_sort_key))
    for path, content in items:
        if not _safe_writer_file_path(path) or path in normalized:
            append(
                "fgui.writer.xml.file_set_incoherent",
                "Every generated file path must be unique, normalized, and relative.",
                path if isinstance(path, str) else None,
            )
            continue
        if not isinstance(content, bytes):
            append(
                "fgui.writer.xml.file_bytes_invalid",
                "Generated file values must be immutable bytes.",
                path,
            )
            continue
        comparison_path = tuple(
            unicodedata.normalize("NFC", part).casefold() for part in PurePosixPath(path).parts
        )
        if comparison_path in comparison_paths:
            append(
                "fgui.writer.xml.file_set_incoherent",
                "Generated files collide under the Windows target path policy.",
                path,
            )
        comparison_paths.add(comparison_path)
        normalized[path] = content

    try:
        validate_unique_target_paths(tuple(PurePosixPath(path) for path in normalized))
    except TargetNamingError:
        append(
            "fgui.writer.xml.file_set_incoherent",
            "Generated file paths must satisfy the Windows target path policy.",
        )

    parsed: dict[str, etree._Element] = {}
    for path, content in normalized.items():
        if not path.endswith((".xml", ".fairy")):
            continue
        if b"\r" in content or not content.endswith(b"\n") or content.endswith(b"\n\n"):
            append(
                "fgui.writer.xml.encoding_invalid",
                "Generated XML must use UTF-8 and LF line endings with one final LF.",
                path,
            )
        if not content.startswith(_WRITER_XML_DECLARATION):
            append(
                "fgui.writer.xml.encoding_invalid",
                "Generated XML must use the canonical UTF-8 declaration.",
                path,
            )
        upper_content = content.upper()
        if b"<!DOCTYPE" in upper_content or b"<!ENTITY" in upper_content:
            append(
                "fgui.writer.xml.doctype_forbidden",
                "DOCTYPE and entity declarations are forbidden in generated XML.",
                path,
            )
            continue
        try:
            content.decode("utf-8", errors="strict")
            root = etree.fromstring(content, parser=_WRITER_SAFE_XML_PARSER.copy())
        except (UnicodeDecodeError, ValueError, etree.XMLSyntaxError):
            append(
                "fgui.writer.xml.parse_invalid",
                "Generated XML must reparse with the safe XML parser.",
                path,
            )
            continue
        if root.getroottree().docinfo.doctype:
            append(
                "fgui.writer.xml.doctype_forbidden",
                "Generated XML cannot retain a document type.",
                path,
            )
            continue
        _validate_writer_xml_tree(root, content, path, append)
        parsed[path] = root

    marker_paths = tuple(path for path in normalized if path.endswith(".fairy"))
    package_paths = tuple(path for path in normalized if path.endswith("/package.xml"))
    if len(marker_paths) != 1 or len(package_paths) != 1:
        append(
            "fgui.writer.xml.file_set_incoherent",
            "A generated project must contain exactly one marker and one package declaration.",
        )
        return tuple(sorted(diagnostics, key=diagnostic_sort_key))
    marker_path = marker_paths[0]
    package_path = package_paths[0]
    package_parts = PurePosixPath(package_path).parts
    if (
        "/" in marker_path
        or len(package_parts) != 3
        or package_parts[0] != "assets"
        or package_parts[-1] != "package.xml"
    ):
        append(
            "fgui.writer.xml.file_set_incoherent",
            "The project marker and package declaration are not at their canonical locations.",
        )

    marker = parsed.get(marker_path)
    if marker is not None and (
        marker.tag != "projectDescription"
        or tuple(marker.attrib) != ("id", "type", "version")
        or _WRITER_PROJECT_ID.fullmatch(marker.attrib.get("id", "")) is None
        or marker.attrib.get("type") != "Unity"
        or marker.attrib.get("version") != "5.0"
        or len(marker) != 0
        or (marker.text is not None and marker.text.strip())
    ):
        append(
            "fgui.writer.xml.project_invalid",
            "The project marker does not match the pinned 6.1.4 Unity dialect.",
            marker_path,
        )

    package = parsed.get(package_path)
    declared_paths: set[str] = set()
    component_declarations: dict[str, tuple[str, str]] = {}
    image_declarations: dict[str, tuple[str, str]] = {}
    package_id = ""
    if package is not None:
        package_id = package.attrib.get("id", "")
        if (
            package.tag != "packageDescription"
            or tuple(package.attrib) != ("id",)
            or _TARGET_ID.fullmatch(package_id) is None
            or [child.tag for child in package] != ["resources", "publish"]
        ):
            append(
                "fgui.writer.xml.package_invalid",
                "The package declaration has an invalid root or child order.",
                package_path,
            )
        else:
            resources_element, publish = package
            if resources_element.attrib:
                append(
                    "fgui.writer.xml.package_invalid",
                    "The generated resources element must not carry attributes.",
                    package_path,
                )
            if (
                publish.attrib
                or len(publish)
                or (publish.text is not None and publish.text.strip())
            ):
                append(
                    "fgui.writer.xml.package_invalid",
                    "The generated publish element must use the observed empty form.",
                    package_path,
                )
            package_directory = PurePosixPath(package_path).parent.as_posix()
            declared_ids: set[str] = set()
            for resource_element in resources_element:
                tag = resource_element.tag
                attribute_order = tuple(resource_element.attrib)
                expected_orders = (
                    {("id", "name", "path")}
                    if tag == "component"
                    else {
                        ("id", "name", "path"),
                        ("id", "name", "path", "scale", "scale9grid"),
                    }
                )
                resource_id = resource_element.attrib.get("id", "")
                name = resource_element.attrib.get("name", "")
                virtual_path = resource_element.attrib.get("path", "")
                if (
                    tag not in {"component", "image"}
                    or attribute_order not in expected_orders
                    or _TARGET_ID.fullmatch(resource_id) is None
                    or resource_id in declared_ids
                    or not _safe_writer_resource_name(name)
                    or len(resource_element) != 0
                    or (resource_element.text is not None and resource_element.text.strip())
                ):
                    append(
                        "fgui.writer.xml.resource_declaration_invalid",
                        "A package resource declaration is malformed or duplicated.",
                        package_path,
                    )
                    continue
                declared_ids.add(resource_id)
                relative_directory = _writer_virtual_directory(virtual_path)
                if relative_directory is None:
                    append(
                        "fgui.writer.xml.resource_declaration_invalid",
                        "A package resource uses an unsafe virtual path.",
                        package_path,
                    )
                    continue
                relative = PurePosixPath(relative_directory, name).as_posix()
                target_path = PurePosixPath(package_directory, relative).as_posix()
                if target_path in declared_paths:
                    append(
                        "fgui.writer.xml.resource_declaration_invalid",
                        "Two package resources declare the same target file.",
                        package_path,
                    )
                    continue
                declared_paths.add(target_path)
                declaration = (target_path, relative)
                if tag == "component":
                    if not name.endswith(".xml"):
                        append(
                            "fgui.writer.xml.resource_declaration_invalid",
                            "Component resources must declare XML files.",
                            package_path,
                        )
                    component_declarations[resource_id] = declaration
                else:
                    if not name.endswith((".png", ".jpg", ".webp", ".svg")):
                        append(
                            "fgui.writer.xml.resource_declaration_invalid",
                            "Image resources must use a supported image extension.",
                            package_path,
                        )
                    image_declarations[resource_id] = declaration
                    scale = resource_element.attrib.get("scale")
                    grid = resource_element.attrib.get("scale9grid")
                    if (scale is None) != (grid is None) or (
                        grid is not None and (scale != "9grid" or not _valid_nine_slice_grid(grid))
                    ):
                        append(
                            "fgui.writer.xml.nine_slice_invalid",
                            "Nine-slice resources require one canonical 9-grid declaration.",
                            package_path,
                        )

    expected_paths = {marker_path, package_path, *declared_paths}
    if set(normalized) != expected_paths:
        append(
            "fgui.writer.xml.file_set_incoherent",
            "Generated files must match the package-declared file set exactly.",
        )

    for component_id, (component_file_path, _relative_path) in component_declarations.items():
        component = parsed.get(component_file_path)
        if component is None:
            if component_file_path in normalized:
                append(
                    "fgui.writer.xml.component_invalid",
                    "A declared component did not safely reparse.",
                    component_file_path,
                )
            continue
        _validate_generated_component_xml(
            component,
            component_file_path,
            component_id,
            package_id,
            component_declarations,
            image_declarations,
            append,
        )

    for path, root in parsed.items():
        if path not in {marker_path, package_path} and path not in {
            declaration[0] for declaration in component_declarations.values()
        }:
            append(
                "fgui.writer.xml.file_set_incoherent",
                "An XML file is not declared as a component resource.",
                path,
            )
        if path.endswith(".xml") and path != package_path and root.tag != "component":
            append(
                "fgui.writer.xml.component_invalid",
                "Declared component XML must use the component root.",
                path,
            )

    target_ids = [package_id, *component_declarations, *image_declarations]
    for component_file_path, _relative_path in component_declarations.values():
        component = parsed.get(component_file_path)
        if component is not None and component.tag == "component":
            display_lists = component.findall("displayList")
            if len(display_lists) == 1:
                target_ids.extend(child.attrib.get("id", "") for child in display_lists[0])
    if len(target_ids) != len(set(target_ids)):
        append(
            "fgui.writer.xml.target_id_conflict",
            "Every package, resource, component, and object target ID must be globally unique.",
        )

    return tuple(sorted(diagnostics, key=diagnostic_sort_key))


_WRITER_SAFE_XML_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
)
_WRITER_XML_DECLARATION = b"<?xml version='1.0' encoding='utf-8'?>\n"
_WRITER_PROJECT_ID = re.compile(r"^[0-9a-f]{32}$")
_WRITER_FILE_PATH = re.compile(r"^[^\x00-\x1f\x7f\\:]+$")
_WRITER_DECIMAL = re.compile(r"^(?:0|-?(?:[1-9][0-9]*)(?:\.[0-9]*[1-9])?|-?0\.[0-9]*[1-9])$")


def _safe_writer_file_path(value: object) -> bool:
    if not isinstance(value, str) or _WRITER_FILE_PATH.fullmatch(value) is None:
        return False
    if (
        unicodedata.normalize("NFC", value) != value
        or value.startswith("/")
        or "//" in value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        return False
    path = PurePosixPath(value)
    if not path.parts or path.as_posix() != value:
        return False
    try:
        validate_unique_target_paths((path,))
    except TargetNamingError:
        return False
    return True


def _canonical_writer_xml(root: etree._Element) -> bytes:
    content = etree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
        standalone=None,
    ).replace(b"\r\n", b"\n")
    return cast(bytes, content if content.endswith(b"\n") else content + b"\n")


def _validate_writer_xml_tree(
    root: etree._Element,
    content: bytes,
    path: str,
    append: Callable[[str, str, str | None], None],
) -> None:
    tree = root.getroottree()
    outside_nodes: list[etree._Element] = []
    previous = root.getprevious()
    while previous is not None:
        outside_nodes.append(previous)
        previous = previous.getprevious()
    following = root.getnext()
    while following is not None:
        outside_nodes.append(following)
        following = following.getnext()
    nodes = (*outside_nodes, *tuple(tree.iter()))
    for node in nodes:
        if isinstance(node, etree._ProcessingInstruction):
            append(
                "fgui.writer.xml.processing_instruction_forbidden",
                "Processing instructions are forbidden in generated XML.",
                path,
            )
        elif isinstance(node, etree._Comment):
            append(
                "fgui.writer.xml.comment_forbidden",
                "Comments are forbidden in generated XML.",
                path,
            )
        if node.text is not None and node.text.strip():
            append(
                "fgui.writer.xml.text_invalid",
                "Generated XML elements cannot contain character data.",
                path,
            )
        if node.tail is not None and node.tail.strip():
            append(
                "fgui.writer.xml.tail_invalid",
                "Generated XML element tails may contain canonical whitespace only.",
                path,
            )
    stack: list[tuple[etree._Element, int]] = [(root, 0)]
    while stack:
        element, depth = stack.pop()
        children = tuple(element)
        expected_text = f"\n{'  ' * (depth + 1)}" if children else None
        if element.text != expected_text:
            append(
                "fgui.writer.xml.tail_invalid",
                "Generated XML indentation must match the canonical serializer output.",
                path,
            )
        for index, child in enumerate(children):
            expected_tail = (
                f"\n{'  ' * depth}" if index == len(children) - 1 else f"\n{'  ' * (depth + 1)}"
            )
            if child.tail != expected_tail:
                append(
                    "fgui.writer.xml.tail_invalid",
                    "Generated XML element tails must use canonical indentation.",
                    path,
                )
            stack.append((child, depth + 1))
    if content != _canonical_writer_xml(root):
        append(
            "fgui.writer.xml.tail_invalid",
            "Generated XML whitespace must match the canonical serializer output.",
            path,
        )


def _safe_writer_resource_name(value: str) -> bool:
    return (
        bool(value)
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and ":" not in value
        and unicodedata.normalize("NFC", value) == value
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


def _writer_virtual_directory(value: str) -> str | None:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or not value.endswith("/")
        or "\\" in value
    ):
        return None
    inner = value[1:-1]
    if not inner:
        return ""
    parts = inner.split("/")
    if any(not _safe_writer_resource_name(part) for part in parts):
        return None
    return PurePosixPath(*parts).as_posix()


def _valid_writer_decimal(value: str) -> bool:
    if _WRITER_DECIMAL.fullmatch(value) is None:
        return False
    try:
        return Decimal(value).is_finite()
    except InvalidOperation:
        return False


def _valid_editor_int32(value: str) -> bool:
    if re.fullmatch(r"(?:0|-?[1-9][0-9]*)", value) is None:
        return False
    return -(2**31) <= int(value) < 2**31


def _valid_editor_int32_pair(value: str, *, nonnegative: bool = False) -> bool:
    parts = value.split(",")
    if len(parts) != 2 or not all(_valid_editor_int32(part) for part in parts):
        return False
    return not nonnegative or all(int(part) >= 0 for part in parts)


def _valid_writer_pair(value: str, *, positive: bool = False) -> bool:
    parts = value.split(",")
    if len(parts) != 2 or not all(_valid_writer_decimal(part) for part in parts):
        return False
    return not positive or all(Decimal(part) > 0 for part in parts)


def _valid_writer_nonnegative_pair(value: str) -> bool:
    parts = value.split(",")
    return (
        len(parts) == 2
        and all(_valid_writer_decimal(part) for part in parts)
        and all(Decimal(part) >= 0 for part in parts)
    )


def _valid_nine_slice_grid(value: str) -> bool:
    parts = value.split(",")
    if len(parts) != 4 or any(re.fullmatch(r"(?:0|[1-9][0-9]*)", part) is None for part in parts):
        return False
    return int(parts[2]) > 0 and int(parts[3]) > 0


def _attribute_order_is_valid(keys: tuple[str, ...], order: tuple[str, ...]) -> bool:
    positions = {name: index for index, name in enumerate(order)}
    return all(name in positions for name in keys) and all(
        positions[first] < positions[second] for first, second in pairwise(keys)
    )


_XML_COMMON_ATTRIBUTE_ORDER = (
    "id",
    "name",
    "xy",
    "size",
    "group",
    "rotation",
    "alpha",
    "visible",
)
_XML_OBJECT_ATTRIBUTE_ORDER: dict[str, tuple[str, ...]] = {
    "group": _XML_COMMON_ATTRIBUTE_ORDER,
    "graph": (*_XML_COMMON_ATTRIBUTE_ORDER, "type", "lineSize", "lineColor", "fillColor", "corner"),
    "text": (
        *_XML_COMMON_ATTRIBUTE_ORDER,
        "font",
        "fontSize",
        "leading",
        "color",
        "align",
        "vAlign",
        "autoSize",
        "strokeColor",
        "strokeSize",
        "text",
    ),
    "richtext": (
        *_XML_COMMON_ATTRIBUTE_ORDER,
        "font",
        "fontSize",
        "leading",
        "color",
        "align",
        "vAlign",
        "autoSize",
        "strokeColor",
        "strokeSize",
        "ubb",
        "text",
    ),
    "image": (*_XML_COMMON_ATTRIBUTE_ORDER, "src", "fileName"),
    "loader": (*_XML_COMMON_ATTRIBUTE_ORDER, "url"),
    "component": (*_XML_COMMON_ATTRIBUTE_ORDER, "src", "fileName", "pkg"),
}


def _validate_generated_component_xml(
    component: etree._Element,
    component_path: str,
    component_id: str,
    package_id: str,
    component_declarations: Mapping[str, tuple[str, str]],
    image_declarations: Mapping[str, tuple[str, str]],
    append: Callable[[str, str, str | None], None],
) -> None:
    del component_id  # The package declaration already binds this path to its target ID.
    root_attribute_order = tuple(component.attrib)
    if (
        component.tag != "component"
        or root_attribute_order
        not in {
            ("size", "opaque"),
            ("size", "opaque", "overflow"),
            ("size", "opaque", "mask"),
        }
        or component.attrib.get("opaque") != "false"
        or not _valid_editor_int32_pair(component.attrib.get("size", ""), nonnegative=True)
        or "0" in component.attrib.get("size", "").split(",")
        or component.attrib.get("overflow") not in {None, "hidden"}
        or [child.tag for child in component] != ["displayList"]
    ):
        append(
            "fgui.writer.xml.component_invalid",
            "A component root does not match the canonical generated dialect.",
            component_path,
        )
        return
    display_list = component[0]
    if display_list.attrib or (display_list.text is not None and display_list.text.strip()):
        append(
            "fgui.writer.xml.component_invalid",
            "The component display list wrapper must not carry attributes or text.",
            component_path,
        )

    object_ids: dict[str, etree._Element] = {}
    object_positions: dict[str, int] = {}
    group_references: list[tuple[str, int]] = []
    for index, object_element in enumerate(display_list):
        tag = object_element.tag
        order = _XML_OBJECT_ATTRIBUTE_ORDER.get(tag)
        object_id = object_element.attrib.get("id", "")
        if (
            order is None
            or not _attribute_order_is_valid(tuple(object_element.attrib), order)
            or _TARGET_ID.fullmatch(object_id) is None
            or not _safe_writer_resource_name(object_element.attrib.get("name", ""))
            or object_id in object_ids
            or len(object_element) != 0
            or (object_element.text is not None and object_element.text.strip())
        ):
            append(
                "fgui.writer.xml.object_invalid",
                "A display object has an invalid tag, attribute order, identity, or geometry.",
                component_path,
            )
            continue
        object_ids[object_id] = object_element
        object_positions[object_id] = index
        if not _valid_editor_int32_pair(
            object_element.attrib.get("xy", "")
        ) or not _valid_editor_int32_pair(
            object_element.attrib.get("size", ""), nonnegative=True
        ):
            append(
                "fgui.writer.xml.decimal_invalid",
                "Object geometry must use FairyGUI 6.1.4 Int32 pairs.",
                component_path,
            )
        group_id = object_element.attrib.get("group")
        if group_id is not None:
            group_references.append((group_id, index))
        rotation = object_element.attrib.get("rotation")
        alpha = object_element.attrib.get("alpha")
        if (
            (rotation is not None and not _valid_editor_int32(rotation))
            or (
                alpha is not None
                and (
                    not _valid_writer_decimal(alpha) or not 0 <= Decimal(alpha) <= 1 or alpha == "1"
                )
            )
            or object_element.attrib.get("visible") not in {None, "false"}
        ):
            append(
                "fgui.writer.xml.decimal_invalid",
                "Display values must use the FairyGUI 6.1.4 numeric types and omitted defaults.",
                component_path,
            )
        if tag == "graph":
            corner = object_element.attrib.get("corner")
            corner_parts = () if corner is None else tuple(corner.split(","))
            line_size = object_element.attrib.get("lineSize")
            if (
                object_element.attrib.get("type") not in {"rect", "ellipse"}
                or (line_size is not None and (not _valid_writer_decimal(line_size) or Decimal(line_size) < 0))
                or not _valid_writer_color(object_element.attrib.get("lineColor"))
                or not _valid_writer_color(object_element.attrib.get("fillColor"))
                or (
                    corner is not None
                    and (
                        len(corner_parts) not in {1, 4}
                        or not all(_valid_writer_decimal(item) for item in corner_parts)
                        or any(Decimal(item) < 0 for item in corner_parts)
                        or object_element.attrib.get("type") != "rect"
                    )
                )
            ):
                append(
                    "fgui.writer.xml.graph_invalid",
                    "Graph objects must use canonical shape, paint, stroke, and corner values.",
                    component_path,
                )
        elif tag in {"text", "richtext"}:
            font_size = object_element.attrib.get("fontSize")
            leading = object_element.attrib.get("leading")
            stroke_size = object_element.attrib.get("strokeSize")
            if (
                "text" not in object_element.attrib
                or object_element.attrib.get("autoSize") not in {"none", "height", "both"}
                or (font_size is not None and not _positive_writer_decimal(font_size))
                or (leading is not None and not _valid_editor_int32(leading))
                or (stroke_size is not None and not _positive_writer_decimal(stroke_size))
                or not _valid_writer_color(object_element.attrib.get("color"))
                or not _valid_writer_color(object_element.attrib.get("strokeColor"))
                or object_element.attrib.get("align")
                not in {None, "left", "center", "right", "justify"}
                or object_element.attrib.get("vAlign") not in {None, "top", "middle", "bottom"}
                or object_element.attrib.get("ubb") not in {None, "true"}
            ):
                append(
                    "fgui.writer.xml.text_invalid",
                    "Text attributes are not a canonical supported 6.1.4 subset.",
                    component_path,
                )
        elif tag == "image":
            source_id = object_element.attrib.get("src", "")
            declaration = image_declarations.get(source_id)
            if declaration is None or object_element.attrib.get("fileName") != declaration[1]:
                append(
                    "fgui.writer.xml.image_reference_invalid",
                    "Image objects must reference one declared same-package image.",
                    component_path,
                )
        elif tag == "loader":
            url = object_element.attrib.get("url", "")
            expected_prefix = f"ui://{package_id}"
            source_id = url[len(expected_prefix) :] if url.startswith(expected_prefix) else ""
            if source_id not in image_declarations or len(source_id) != 8:
                append(
                    "fgui.writer.xml.loader_reference_invalid",
                    "Loaders must use one declared same-package ui:// resource URL.",
                    component_path,
                )
        elif tag == "component":
            source_id = object_element.attrib.get("src", "")
            declaration = component_declarations.get(source_id)
            if (
                declaration is None
                or object_element.attrib.get("fileName")
                != (None if declaration is None else declaration[1])
                or object_element.attrib.get("pkg") != package_id
            ):
                append(
                    "fgui.writer.xml.component_reference_invalid",
                    "Component objects must reference a declared component and owning package ID.",
                    component_path,
                )

    for group_id, member_position in group_references:
        target = object_ids.get(group_id)
        if (
            target is None
            or target.tag != "group"
            or object_positions.get(group_id, -1) <= member_position
        ):
            append(
                "fgui.writer.xml.group_reference_invalid",
                "Group membership must resolve to a following group object.",
                component_path,
            )

    mask_id = component.attrib.get("mask")
    if mask_id is not None:
        mask = object_ids.get(mask_id)
        if (
            mask is None
            or mask.tag not in {"graph", "image"}
            or object_positions.get(mask_id) != 0
            or len(object_ids) < 2
        ):
            append(
                "fgui.writer.xml.mask_invalid",
                "A native mask must be the first local graph/image before masked content.",
                component_path,
            )
def _positive_writer_decimal(value: str) -> bool:
    return _valid_writer_decimal(value) and Decimal(value) > 0


def _valid_writer_color(value: str | None) -> bool:
    return value is None or re.fullmatch(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?", value) is not None


def _declared_project_paths(manifest: NewProjectManifest) -> tuple[str, ...]:
    paths = [
        f"{manifest.project.project_name}.fairy",
        f"{manifest.package.relative_path}/package.xml",
    ]
    paths.extend(
        f"{manifest.package.relative_path}/{component.relative_path}"
        for component in manifest.components
    )
    paths.extend(
        f"{manifest.package.relative_path}/{resource.relative_path}"
        for resource in manifest.resources
    )
    return tuple(sorted(paths))


def required_package_directories(manifest: NewProjectManifest) -> tuple[str, ...]:
    """Return the fixed package directories relative to the project root."""
    return tuple(
        f"{manifest.package.relative_path}/{directory}"
        for directory in PACKAGE_DIRECTORIES
    )


def _filesystem_entry_is_link(path: Path) -> bool:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _project_file_diagnostics(
    manifest: NewProjectManifest, files: Mapping[str, bytes]
) -> tuple[Diagnostic, ...]:
    diagnostics = list(validate_xml_files(files))
    for resource in manifest.resources:
        path = f"{manifest.package.relative_path}/{resource.relative_path}"
        content = files.get(path)
        if content is None or hashlib.sha256(content).hexdigest() != resource.content_sha256:
            diagnostics.append(
                _diagnostic(
                    "fgui.writer.project.resource_hash_mismatch",
                    "A project resource does not match its declared content hash.",
                    node_id=resource.id,
                    path=path,
                )
            )
    return tuple(sorted(diagnostics, key=diagnostic_sort_key))


def validate_project_directory(
    project_root: Path, manifest: NewProjectManifest
) -> tuple[Diagnostic, ...]:
    """Reopen an on-disk project and verify its exact declared file closure."""
    try:
        if not project_root.is_dir() or _filesystem_entry_is_link(project_root):
            raise OSError
        entries = tuple(project_root.rglob("*"))
        if any(_filesystem_entry_is_link(path) for path in entries):
            raise OSError
        if any(
            not (stat.S_ISREG(path.lstat().st_mode) or stat.S_ISDIR(path.lstat().st_mode))
            for path in entries
        ):
            raise OSError
        file_entries = tuple(path for path in entries if path.is_file())
        if any(not stat.S_ISREG(path.lstat().st_mode) for path in file_entries):
            raise OSError
        actual_paths = tuple(
            sorted(path.relative_to(project_root).as_posix() for path in file_entries)
        )
        declared_paths = _declared_project_paths(manifest)
        expected_directories = {
            PurePosixPath(*path.parts[:index]).as_posix()
            for declared in declared_paths
            for path in (PurePosixPath(declared),)
            for index in range(1, len(path.parts))
        }
        expected_directories.update(required_package_directories(manifest))
        actual_directories = {
            path.relative_to(project_root).as_posix() for path in entries if path.is_dir()
        }
        if actual_paths != declared_paths or actual_directories != expected_directories:
            return (
                _diagnostic(
                    "fgui.writer.project.file_closure_invalid",
                    "The project directory does not contain exactly the declared files.",
                    path="$",
                ),
            )
        files = {
            path.relative_to(project_root).as_posix(): path.read_bytes() for path in file_entries
        }
    except OSError:
        return (
            _diagnostic(
                "fgui.writer.project.directory_invalid",
                "The project directory cannot be safely reopened.",
                path="$",
            ),
        )
    return _project_file_diagnostics(manifest, files)


def validate_project_archive(
    archive_path: Path, manifest: NewProjectManifest
) -> tuple[Diagnostic, ...]:
    """Reopen a ZIP and reject unsafe members, bad CRCs, and content drift."""
    invalid = (
        _diagnostic(
            "fgui.writer.project.archive_invalid",
            "The project archive cannot be safely reopened.",
            path="$",
        ),
    )
    prefix = f"{manifest.project.project_name}/"
    try:
        if not archive_path.is_file() or _filesystem_entry_is_link(archive_path):
            return invalid
        with ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            directory_infos = tuple(item for item in infos if item.is_dir())
            file_infos = tuple(item for item in infos if not item.is_dir())
            expected_directory_names = tuple(
                sorted(
                    f"{prefix}{path}/"
                    for path in required_package_directories(manifest)
                )
            )
            if (
                len(names) != len(set(names))
                or len(names)
                != len({unicodedata.normalize("NFC", name).casefold() for name in names})
                or any(
                    item.flag_bits & 0x1
                    or (
                        item.create_system == 3
                        and stat.S_IFMT(item.external_attr >> 16)
                        not in (
                            {0, stat.S_IFDIR} if item.is_dir() else {0, stat.S_IFREG}
                        )
                    )
                    or "\\" in item.filename
                    or not item.filename.startswith(prefix)
                    or not _safe_writer_file_path(
                        item.filename[len(prefix) : -1]
                        if item.is_dir()
                        else item.filename[len(prefix) :]
                    )
                    for item in infos
                )
                or tuple(sorted(item.filename for item in directory_infos))
                != expected_directory_names
                or tuple(sorted(item.filename[len(prefix) :] for item in file_infos))
                != _declared_project_paths(manifest)
                or archive.testzip() is not None
            ):
                return invalid
            files = {
                item.filename[len(prefix) :]: archive.read(item) for item in file_infos
            }
    except (BadZipFile, KeyError, OSError, RuntimeError, ValueError):
        return invalid
    return _project_file_diagnostics(manifest, files)
