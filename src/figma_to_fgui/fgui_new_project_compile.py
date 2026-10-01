"""Pure compilation from a validated FGUI Plan into a target manifest."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import NoReturn

from figma_to_fgui.data_policy import private_data_violations
from figma_to_fgui.fgui_asset_payloads import (
    NewProjectInputError,
    ValidatedAssetPayload,
    diagnostic_sort_key,
)
from figma_to_fgui.fgui_new_project_ids import (
    ComponentSourceKey,
    TargetIdAllocator,
    TargetIdCollisionError,
    TargetNamingError,
    component_logical_key,
    component_path,
    object_logical_key,
    package_logical_key,
    readable_target_name,
    resource_logical_key,
    resource_path,
    validate_target_name,
)
from figma_to_fgui.fgui_new_project_models import (
    ManifestComponent,
    ManifestObject,
    ManifestPackage,
    ManifestResource,
    NewProjectConfig,
    NewProjectManifest,
)
from figma_to_fgui.fgui_new_project_validate import (
    _CONFIG_HEADERS,
    ExactBuiltinHeader,
    NewProjectManifestError,
    _exact_builtin_header_failure,
    validate_new_project_manifest,
)
from figma_to_fgui.fgui_plan_models import (
    FGUIPlanDocument,
    FGUIPlanNode,
    MaskMode,
    PlanNodeType,
    ResourcePlan,
)
from figma_to_fgui.fgui_plan_policy import (
    NATIVE_CLIP_SOURCE_RULE_ID,
    NATIVE_CONTAINER_RULE_ID,
    NATIVE_GRAPH_RULE_ID,
)
from figma_to_fgui.fgui_plan_validate import validate_fgui_plan
from figma_to_fgui.models import Bounds, Diagnostic, Severity
from figma_to_fgui.validate import has_errors

_PUBLIC_NODE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,255}$")
_PUBLIC_PATH = re.compile(r"^\$[A-Za-z0-9_.|\[\]-]{0,255}$")
_PLAN_HEADERS: tuple[ExactBuiltinHeader, ...] = (
    ("schema_version", "schemaVersion", int, 2),
    ("profile_version", "profileVersion", str, "fgui-6.1.4-v1"),
    ("rule_version", "ruleVersion", int, 1),
)


def _safe_locator(value: str | None, pattern: re.Pattern[str]) -> str | None:
    if value is None or pattern.fullmatch(value) is None or private_data_violations({value: None}):
        return None
    return value


def _input_diagnostic(
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    path: str | None = None,
    evidence: tuple[str, ...] | None = None,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=Severity.ERROR,
        message=message,
        node_id=_safe_locator(node_id, _PUBLIC_NODE_REF),
        path=_safe_locator(path, _PUBLIC_PATH),
        rule_id=code,
        rule_version=1,
        evidence=evidence or (f"rule={code}",),
        suggested_action="Repair or recompile the Writer inputs.",
        blocks_binding=True,
    )


def _raise_input(diagnostics: list[Diagnostic]) -> NoReturn:
    raise NewProjectInputError(tuple(sorted(diagnostics, key=diagnostic_sort_key)))


def _writer_input_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> list[Diagnostic]:
    """Translate Plan-gate details into static public Writer diagnostics."""
    return [
        _input_diagnostic(
            "fgui.writer.input.plan_invalid",
            "The FGUI Plan failed semantic revalidation.",
            node_id=diagnostic.node_id,
            path=diagnostic.path,
            evidence=(f"source.code={diagnostic.code}",),
        )
        for diagnostic in diagnostics
    ]


def _reject_plan_header_failure(value: object, *, payload: bool = False) -> None:
    failure = _exact_builtin_header_failure(value, _PLAN_HEADERS, payload=payload)
    if failure is None:
        return
    if failure == "schema_version":
        try:
            schema_version = (
                value.get("schemaVersion")
                if payload and type(value) is dict
                else getattr(value, "schema_version", None)
            )
        except Exception:  # noqa: BLE001 - hostile runtime attributes fail closed.
            schema_version = None
        if type(schema_version) is int:
            code = "fgui.writer.input.unsupported_plan_schema"
            message = "The Writer supports only FGUI Plan schema v2."
        else:
            code = "fgui.writer.input.plan_schema_invalid"
            message = "The FGUI Plan does not satisfy its strict typed schema."
    elif failure == "profile_version":
        code = "fgui.writer.input.unsupported_profile"
        message = "The Writer supports only the fgui-6.1.4-v1 Plan profile."
    elif failure == "rule_version":
        code = "fgui.writer.input.unsupported_rule_version"
        message = "The Writer supports only Plan rule version 1."
    else:
        code = "fgui.writer.input.plan_schema_invalid"
        message = "The FGUI Plan does not satisfy its strict typed schema."
    _raise_input([_input_diagnostic(code, message)])


def _canonical_plan_input(plan: FGUIPlanDocument) -> FGUIPlanDocument:
    """Round-trip through the strict v2 schema before semantic validation."""
    _reject_plan_header_failure(plan)
    try:
        payload = plan.model_dump(mode="json", by_alias=True, warnings="error")
    except Exception:  # noqa: BLE001 - corrupted trusted models can fail in arbitrary serializers.
        _raise_input(
            [
                _input_diagnostic(
                    "fgui.writer.input.plan_schema_invalid",
                    "The FGUI Plan does not satisfy its strict typed schema.",
                )
            ]
        )
    _reject_plan_header_failure(payload, payload=True)
    try:
        canonical = FGUIPlanDocument.model_validate(payload)
    except Exception:  # noqa: BLE001 - corrupted trusted models can fail in validation.
        _raise_input(
            [
                _input_diagnostic(
                    "fgui.writer.input.plan_schema_invalid",
                    "The FGUI Plan does not satisfy its strict typed schema.",
                )
            ]
        )
    _reject_plan_header_failure(canonical)
    return canonical


def _canonical_config_input(config: NewProjectConfig) -> NewProjectConfig:
    if _exact_builtin_header_failure(config, _CONFIG_HEADERS) is not None:
        _raise_input(
            [
                _input_diagnostic(
                    "fgui.writer.input.config_schema_invalid",
                    "The new-project configuration does not satisfy Writer v1.",
                )
            ]
        )
    try:
        payload = config.model_dump(mode="json", by_alias=True, warnings="error")
        if _exact_builtin_header_failure(payload, _CONFIG_HEADERS, payload=True) is not None:
            raise ValueError("invalid exact config header types")
        canonical = NewProjectConfig.model_validate(payload)
        if _exact_builtin_header_failure(canonical, _CONFIG_HEADERS) is not None:
            raise ValueError("invalid exact config header types")
        return canonical
    except Exception:  # noqa: BLE001 - model_copy corruption may fail in serializers.
        _raise_input(
            [
                _input_diagnostic(
                    "fgui.writer.input.config_schema_invalid",
                    "The new-project configuration does not satisfy Writer v1.",
                )
            ]
        )


def _normalize_nested_native_clips(
    plan: FGUIPlanDocument,
) -> tuple[FGUIPlanDocument, dict[str, str]]:
    """Keep nested frames in their owning component's ordinary display tree.

    FairyGUI 6.1.4 only has an observed native ``overflow=hidden`` encoding at a
    component root.  Turning every nested Figma frame with ``clipsContent`` into
    a generated component preserves that flag, but changes an ordinary source
    frame into a separate library resource.  Prefer the editable source tree and
    drop only the unrepresentable nested clipping role.  Root clips are left
    untouched and retain the native component overflow encoding.
    """
    nodes = dict(plan.nodes)
    decisions = dict(plan.decisions)
    masks = dict(plan.masks)
    changed = False
    for node_id, node in tuple(nodes.items()):
        if node.parent_id is None or node.mask_ref is None:
            continue
        mask = masks.get(node.mask_ref)
        if mask is None or mask.mode != MaskMode.NATIVE_CLIP:
            continue
        ordinary_container = node.type == PlanNodeType.GRAPH and bool(node.children)
        meaningful_background = (
            node.graph is not None
            and (
                node.graph.fill_color is not None
                or node.graph.line_color is not None
                or node.graph.line_size > 0
            )
        )
        nodes[node_id] = node.model_copy(
            update={
                "mask_ref": None,
                "type": PlanNodeType.CONTAINER if ordinary_container else node.type,
                "background_graph": (
                    node.graph if ordinary_container and meaningful_background else node.background_graph
                ),
                "graph": None if ordinary_container else node.graph,
            }
        )
        decision = decisions.get(node.uir_node_ref)
        if decision is not None and decision.rule_id == NATIVE_CLIP_SOURCE_RULE_ID:
            decisions[node.uir_node_ref] = decision.model_copy(
                update={
                    "rule_id": (
                        NATIVE_CONTAINER_RULE_ID
                        if ordinary_container
                        else NATIVE_GRAPH_RULE_ID
                    )
                }
            )
        masks.pop(node.mask_ref, None)
        changed = True

    if not changed:
        return plan, {}
    return (
        plan.model_copy(
            update={
                "nodes": nodes,
                "decisions": decisions,
                "masks": masks,
            }
        ),
        {},
    )
def _validate_inputs(
    plan: FGUIPlanDocument,
    config: NewProjectConfig,
    assets: tuple[ValidatedAssetPayload, ...],
) -> tuple[FGUIPlanDocument, NewProjectConfig, dict[str, ValidatedAssetPayload]]:
    config = _canonical_config_input(config)
    plan = _canonical_plan_input(plan)
    plan_diagnostics = validate_fgui_plan(plan)
    if not plan.bindable or has_errors(plan_diagnostics):
        public = _writer_input_diagnostics(plan_diagnostics)
        if not plan.bindable:
            public.append(
                _input_diagnostic(
                    "fgui.writer.input.plan_not_bindable",
                    "The FGUI Plan is not bindable and cannot enter the project writer.",
                )
            )
        _raise_input(public)

    try:
        validate_target_name(config.project_name, "project")
        validate_target_name(config.package_name, "package")
    except TargetNamingError:
        _raise_input(
            [
                _input_diagnostic(
                    "fgui.writer.input.target_name_invalid",
                    "Project and package names must be safe generated target names.",
                )
            ]
        )

    indexed: dict[str, ValidatedAssetPayload] = {}
    diagnostics: list[Diagnostic] = []
    for resource_id, resource in sorted(plan.resources.items()):
        if resource.export_format != "png" or resource.mime_type != "image/png":
            diagnostics.append(
                _input_diagnostic(
                    "fgui.writer.input.asset_format_unsupported",
                    "New-project image resources must use PNG exports.",
                    node_id=resource_id,
                )
            )
    for asset in assets:
        resource_id = asset.resource.id
        if resource_id in indexed:
            diagnostics.append(
                _input_diagnostic(
                    "fgui.writer.input.asset_duplicate",
                    "A validated resource payload is supplied more than once.",
                    node_id=resource_id,
                )
            )
            continue
        indexed[resource_id] = asset
    for resource_id in sorted(set(plan.resources) - set(indexed)):
        diagnostics.append(
            _input_diagnostic(
                "fgui.writer.input.asset_missing",
                "A Plan resource has no validated payload.",
                node_id=resource_id,
            )
        )
    for resource_id in sorted(set(indexed) - set(plan.resources)):
        diagnostics.append(
            _input_diagnostic(
                "fgui.writer.input.asset_unexpected",
                "A validated payload is not declared by the Plan.",
                node_id=resource_id,
            )
        )
    for resource_id in sorted(set(indexed) & set(plan.resources)):
        asset = indexed[resource_id]
        if (
            asset.resource != plan.resources[resource_id]
            or asset.payload.resource_id != resource_id
            or asset.payload.declared_mime_type != asset.resource.mime_type
        ):
            diagnostics.append(
                _input_diagnostic(
                    "fgui.writer.input.asset_incoherent",
                    "A validated payload no longer agrees with its Plan resource.",
                    node_id=resource_id,
                )
            )
    if diagnostics:
        _raise_input(diagnostics)
    return plan, config, indexed


def _target_name_from_logical_ref(value: str) -> str:
    """Use the final logical identity segment without silently rewriting it."""
    return validate_target_name(value.rsplit(":", maxsplit=1)[-1], "component")


def _component_sources(plan: FGUIPlanDocument) -> tuple[ComponentSourceKey, ...]:
    return (
        *(("definition", source_ref) for source_ref in sorted(plan.component_definitions)),
        *(("root", source_ref) for source_ref in sorted(plan.roots)),
    )


def _owner_tables(
    plan: FGUIPlanDocument,
) -> tuple[
    dict[str, ComponentSourceKey],
    dict[ComponentSourceKey, Mapping[str, FGUIPlanNode]],
    dict[ComponentSourceKey, str],
]:
    """Keep document roots and each definition in distinct ownership domains."""
    nodes_by_component: dict[ComponentSourceKey, Mapping[str, FGUIPlanNode]] = {}
    root_by_component: dict[ComponentSourceKey, str] = {}
    node_owner: dict[str, ComponentSourceKey] = {}

    for definition_id in sorted(plan.component_definitions):
        definition = plan.component_definitions[definition_id]
        source: ComponentSourceKey = ("definition", definition_id)
        nodes_by_component[source] = definition.nodes
        root_by_component[source] = definition.root_node_ref
        for node_id in definition.nodes:
            node_owner[node_id] = source

    for root_id in sorted(plan.roots):
        reachable: dict[str, FGUIPlanNode] = {}
        pending = [root_id]
        while pending:
            node_id = pending.pop()
            if node_id in reachable:
                continue
            node = plan.nodes[node_id]
            reachable[node_id] = node
            pending.extend(reversed(node.children))
        source = ("root", root_id)
        nodes_by_component[source] = reachable
        root_by_component[source] = root_id
        for node_id in reachable:
            node_owner[node_id] = source
    return node_owner, nodes_by_component, root_by_component


def _allocation_requests(
    plan: FGUIPlanDocument,
    config: NewProjectConfig,
    nodes_by_component: Mapping[ComponentSourceKey, Mapping[str, FGUIPlanNode]],
) -> tuple[tuple[str, str], ...]:
    requests: list[tuple[str, str]] = [
        ("package", package_logical_key(plan.document_id, config.package_name))
    ]
    requests.extend(
        ("component", component_logical_key(source)) for source in _component_sources(plan)
    )
    for component_source in sorted(nodes_by_component):
        requests.extend(
            ("object", object_logical_key(component_source, node_id))
            for node_id in sorted(nodes_by_component[component_source])
        )
    for resource_id in sorted(plan.resources):
        resource = plan.resources[resource_id]
        if resource.content_sha256 is None:
            _raise_input(
                [
                    _input_diagnostic(
                        "fgui.writer.input.resource_hash_missing",
                        "A Writer resource requires a validated content hash.",
                        node_id=resource_id,
                    )
                ]
            )
        requests.append(
            (
                "resource",
                resource_logical_key(
                    resource.id,
                    resource.content_sha256,
                    resource.export_parameters_sha256,
                ),
            )
        )
    return tuple(requests)


def _target_ids(
    plan: FGUIPlanDocument,
    config: NewProjectConfig,
    nodes_by_component: Mapping[ComponentSourceKey, Mapping[str, FGUIPlanNode]],
) -> dict[tuple[str, str], str]:
    try:
        return TargetIdAllocator().allocate_all(
            _allocation_requests(plan, config, nodes_by_component)
        )
    except (TargetIdCollisionError, TargetNamingError):
        _raise_input(
            [
                _input_diagnostic(
                    "fgui.writer.input.target_identity_invalid",
                    "The input cannot be assigned unambiguous Writer v1 target identities.",
                )
            ]
        )


def _object_id(
    ids: Mapping[tuple[str, str], str], component_source: ComponentSourceKey, node_id: str
) -> str:
    return ids[("object", object_logical_key(component_source, node_id))]


def _resource_id(ids: Mapping[tuple[str, str], str], resource: ResourcePlan) -> str:
    assert resource.content_sha256 is not None
    return ids[
        (
            "resource",
            resource_logical_key(
                resource.id,
                resource.content_sha256,
                resource.export_parameters_sha256,
            ),
        )
    ]


def _component_public_provenance(node: FGUIPlanNode) -> dict[str, object]:
    provenance: dict[str, object] = {}
    if node.component is not None:
        provenance.update(
            {
                "candidateKey": node.component.candidate_key,
                "variantProperties": node.component.variant_properties,
                "overrides": node.component.overrides,
            }
        )
    return provenance


def _compile_objects(
    plan: FGUIPlanDocument,
    component_source: ComponentSourceKey,
    nodes: Mapping[str, FGUIPlanNode],
    root_node_ref: str,
    ids: Mapping[tuple[str, str], str],
    source_names: Mapping[str, str],
) -> tuple[ManifestObject, ...]:
    by_uir_ref = {node.uir_node_ref: node for node in nodes.values()}
    ordered_nodes: list[FGUIPlanNode] = []
    pending = [root_node_ref]
    while pending:
        node_id = pending.pop()
        node = nodes[node_id]
        ordered_nodes.append(node)
        pending.extend(reversed(node.children))

    objects: list[ManifestObject] = []
    for node in ordered_nodes:
        mask_mode = None
        mask_kind = None
        mask_object_ref = None
        mask_content_object_refs: tuple[str, ...] = ()
        raster_consumed_node_refs: tuple[str, ...] = ()
        public_provenance = _component_public_provenance(node)
        if node.mask_ref is not None:
            mask = plan.masks[node.mask_ref]
            mask_mode = mask.mode
            mask_kind = mask.kind
            if mask.mode in {MaskMode.NATIVE_CLIP, MaskMode.NATIVE_MASK}:
                mask_source = by_uir_ref[mask.mask_node_ref]
                mask_object_ref = _object_id(ids, component_source, mask_source.id)
                mask_content_object_refs = tuple(
                    _object_id(ids, component_source, by_uir_ref[source_ref].id)
                    for source_ref in mask.content_node_refs
                )
            else:
                raster_consumed_node_refs = tuple(
                    source_ref
                    for source_ref in dict.fromkeys((mask.mask_node_ref, *mask.content_node_refs))
                    if source_ref != node.uir_node_ref
                )

        resource_ref = (
            None
            if node.resource_ref is None
            else _resource_id(ids, plan.resources[node.resource_ref])
        )
        component_ref = (
            None
            if node.component is None
            else ids[
                (
                    "component",
                    component_logical_key(("definition", node.component.definition_ref)),
                )
            ]
        )
        target_object_id = _object_id(ids, component_source, node.id)
        objects.append(
            ManifestObject(
                id=target_object_id,
                name=(
                    target_object_id
                    if node.uir_node_ref not in source_names
                    else readable_target_name(source_names[node.uir_node_ref], target_object_id)
                ),
                sourceNodeRef=node.id,
                uirNodeRef=node.uir_node_ref,
                parentObjectRef=(
                    None
                    if node.parent_id is None
                    else _object_id(ids, component_source, node.parent_id)
                ),
                childObjectRefs=tuple(
                    _object_id(ids, component_source, child_id) for child_id in node.children
                ),
                zIndex=node.z_index,
                type=node.type,
                transform=node.transform,
                text=node.text,
                graph=node.graph,
                backgroundGraph=node.background_graph,
                resourceRef=resource_ref,
                componentRef=component_ref,
                maskMode=mask_mode,
                maskKind=mask_kind,
                maskObjectRef=mask_object_ref,
                maskContentObjectRefs=mask_content_object_refs,
                rasterConsumedNodeRefs=raster_consumed_node_refs,
                maskCornerRadii=(
                    None if node.mask_ref is None else plan.masks[node.mask_ref].corner_radii
                ),
                publicProvenance=public_provenance,
            )
        )
    return tuple(objects)


def _definition_order(
    plan: FGUIPlanDocument, ids: Mapping[tuple[str, str], str]
) -> tuple[str, ...]:
    dependencies: dict[str, set[str]] = {
        definition_id: {
            node.component.definition_ref
            for node in definition.nodes.values()
            if node.component is not None
        }
        for definition_id, definition in plan.component_definitions.items()
    }
    dependents: dict[str, set[str]] = defaultdict(set)
    remaining = {definition_id: len(refs) for definition_id, refs in dependencies.items()}
    for definition_id, refs in dependencies.items():
        for dependency in refs:
            dependents[dependency].add(definition_id)
    ready = sorted(
        (definition_id for definition_id, count in remaining.items() if count == 0),
        key=lambda item: ids[("component", component_logical_key(("definition", item)))],
        reverse=True,
    )
    ordered: list[str] = []
    while ready:
        definition_id = ready.pop()
        ordered.append(definition_id)
        for dependent in sorted(
            dependents[definition_id],
            key=lambda item: ids[("component", component_logical_key(("definition", item)))],
        ):
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                ready.append(dependent)
                ready.sort(
                    key=lambda item: ids[
                        ("component", component_logical_key(("definition", item)))
                    ],
                    reverse=True,
                )
    return tuple(ordered)


def _compile_component(
    plan: FGUIPlanDocument,
    source: ComponentSourceKey,
    name: str,
    nodes: Mapping[str, FGUIPlanNode],
    root_node_ref: str,
    ids: Mapping[tuple[str, str], str],
    source_names: Mapping[str, str],
) -> ManifestComponent:
    source_kind, source_ref = source
    target_id = ids[("component", component_logical_key(source))]
    safe_name = validate_target_name(name, "component")
    root_bounds = nodes[root_node_ref].transform.bounds
    return ManifestComponent(
        id=target_id,
        sourceComponentKind=source_kind,
        sourceComponentRef=source_ref,
        name=safe_name,
        relativePath=component_path(safe_name, target_id, source_kind).as_posix(),
        size=Bounds(x=0, y=0, width=root_bounds.width, height=root_bounds.height),
        objects=_compile_objects(plan, source, nodes, root_node_ref, ids, source_names),
    )


def _compile_components(
    plan: FGUIPlanDocument,
    nodes_by_component: Mapping[ComponentSourceKey, Mapping[str, FGUIPlanNode]],
    root_by_component: Mapping[ComponentSourceKey, str],
    ids: Mapping[tuple[str, str], str],
    source_names: Mapping[str, str],
) -> tuple[ManifestComponent, ...]:
    definitions = tuple(
        _compile_component(
            plan,
            ("definition", definition_id),
            plan.component_definitions[definition_id].name,
            nodes_by_component[("definition", definition_id)],
            root_by_component[("definition", definition_id)],
            ids,
            source_names,
        )
        for definition_id in _definition_order(plan, ids)
    )
    sorted_roots = sorted(
        plan.roots,
        key=lambda item: ids[("component", component_logical_key(("root", item)))],
    )
    roots = tuple(
        _compile_component(
            plan,
            ("root", root_id),
            (
                readable_target_name(
                    source_names[plan.nodes[root_id].uir_node_ref],
                    _target_name_from_logical_ref(plan.nodes[root_id].uir_node_ref),
                )
                if plan.nodes[root_id].uir_node_ref in source_names
                else _target_name_from_logical_ref(plan.nodes[root_id].uir_node_ref)
            ),
            nodes_by_component[("root", root_id)],
            root_by_component[("root", root_id)],
            ids,
            source_names,
        )
        for root_id in sorted_roots
    )
    return (*definitions, *roots)


def _compile_resources(
    plan: FGUIPlanDocument,
    node_owner: Mapping[str, ComponentSourceKey],
    nodes_by_component: Mapping[ComponentSourceKey, Mapping[str, FGUIPlanNode]],
    ids: Mapping[tuple[str, str], str],
    source_names: Mapping[str, str],
    reserved_names: set[str],
) -> tuple[ManifestResource, ...]:
    resources: list[ManifestResource] = []
    all_nodes = {
        node_id: node
        for component_nodes in nodes_by_component.values()
        for node_id, node in component_nodes.items()
    }
    used_names = {name.casefold() for name in reserved_names}
    for resource in sorted(plan.resources.values(), key=lambda item: item.id):
        target_id = _resource_id(ids, resource)
        consumer_names = tuple(
            source_names[all_nodes[consumer].uir_node_ref]
            for consumer in resource.consumers
            if all_nodes[consumer].uir_node_ref in source_names
        )
        name = (
            readable_target_name(
                consumer_names[0], _target_name_from_logical_ref(resource.logical_asset_id)
            )
            if consumer_names
            else _target_name_from_logical_ref(resource.logical_asset_id)
        )
        base_name = name
        if name.casefold() in used_names:
            name = readable_target_name(f"{base_name}_resource", target_id)
        suffix_number = 2
        while name.casefold() in used_names:
            name = readable_target_name(f"{base_name}_resource_{suffix_number}", target_id)
            suffix_number += 1
        used_names.add(name.casefold())
        if resource.export_format != "png" or resource.mime_type != "image/png":
            raise TargetNamingError("new-project image resources must be PNG")
        consumer_refs = tuple(
            sorted(
                _object_id(ids, node_owner[consumer], consumer) for consumer in resource.consumers
            )
        )
        provenance: dict[str, object] = {
            "exportParametersSha256": resource.export_parameters_sha256
        }
        if resource.reason is not None:
            provenance["reason"] = resource.reason
        assert resource.content_sha256 is not None
        resources.append(
            ManifestResource(
                id=target_id,
                sourceResourceRef=resource.id,
                name=name,
                relativePath=resource_path(name, target_id).as_posix(),
                mimeType=resource.mime_type,
                contentSha256=resource.content_sha256,
                exportParametersSha256=resource.export_parameters_sha256,
                exportFormat=resource.export_format,
                width=resource.width,
                height=resource.height,
                nineSlice=resource.nine_slice,
                consumerObjectRefs=consumer_refs,
                publicProvenance={
                    key: value
                    for key, value in provenance.items()
                    if key != "exportParametersSha256"
                },
            )
        )
    return tuple(sorted(resources, key=lambda item: item.id))


def compile_new_project_manifest(
    plan: FGUIPlanDocument,
    config: NewProjectConfig,
    assets: tuple[ValidatedAssetPayload, ...],
    source_names: Mapping[str, str] | None = None,
) -> NewProjectManifest:
    """Compile validated inputs without reading or writing a target project."""
    plan, config, _ = _validate_inputs(plan, config, assets)
    plan, source_name_aliases = _normalize_nested_native_clips(plan)
    lifted_diagnostics = validate_fgui_plan(plan)
    if has_errors(lifted_diagnostics):
        _raise_input(_writer_input_diagnostics(lifted_diagnostics))
    names = dict(source_names or {})
    for target_ref, source_ref in source_name_aliases.items():
        if source_ref in names:
            names[target_ref] = names[source_ref]
    node_owner, nodes_by_component, root_by_component = _owner_tables(plan)
    ids = _target_ids(plan, config, nodes_by_component)
    try:
        package_id = ids[("package", package_logical_key(plan.document_id, config.package_name))]
        components = _compile_components(
            plan, nodes_by_component, root_by_component, ids, names
        )
        manifest = NewProjectManifest(
            project=config,
            package=ManifestPackage(
                id=package_id,
                sourceDocumentRef=plan.document_id,
                name=config.package_name,
                relativePath=PurePosixPath("assets", config.package_name).as_posix(),
            ),
            components=components,
            resources=_compile_resources(
                plan,
                node_owner,
                nodes_by_component,
                ids,
                names,
                {component.name for component in components},
            ),
        )
    except TargetNamingError:
        _raise_input(
            [
                _input_diagnostic(
                    "fgui.writer.input.target_name_invalid",
                    "Plan names cannot be represented as safe generated target names.",
                )
            ]
        )
    diagnostics = validate_new_project_manifest(manifest)
    if has_errors(diagnostics):
        raise NewProjectManifestError(diagnostics)
    return manifest
