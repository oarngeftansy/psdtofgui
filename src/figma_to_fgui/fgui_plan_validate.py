"""Semantic validation and canonical serialization for FairyGUI generation plans."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import cast

from figma_to_fgui.data_policy import private_data_violations, redact_private_data
from figma_to_fgui.fgui_capabilities import is_reviewable_text_decision
from figma_to_fgui.fgui_plan_models import (
    CapabilityDecision,
    CapabilityStatus,
    FGUIPlanDocument,
    FGUIPlanNode,
    MaskKind,
    MaskMode,
    MaskPlan,
    PlanNodeType,
)
from figma_to_fgui.fgui_plan_policy import (
    NATIVE_CLIP_SOURCE_RULE_ID,
    NATIVE_IMAGE_RULE_ID,
    NATIVE_RULE_TO_NODE_TYPE,
    RASTER_SUBTREE_RULE_ID,
    node_type_for_capability,
)
from figma_to_fgui.models import Diagnostic, Severity

_RESOURCE_NODE_TYPES = frozenset(
    {PlanNodeType.IMAGE, PlanNodeType.LOADER, PlanNodeType.RASTER_SUBTREE}
)
_TEXT_NODE_TYPES = frozenset({PlanNodeType.TEXT, PlanNodeType.RICH_TEXT})
MAX_CONTRACT_TREE_DEPTH = 256
_USER_TEXT_SENTINEL = "<user-visible-text>"


def _metadata_projection(payload: dict[str, object]) -> dict[str, object]:
    for nodes in _serialized_node_tables(payload):
        for node in nodes.values():
            if not isinstance(node, dict):
                continue
            text = node.get("text")
            if not isinstance(text, dict):
                continue
            if "content" in text:
                text["content"] = _USER_TEXT_SENTINEL
            runs = text.get("runs")
            if isinstance(runs, (list, tuple)):
                for run in runs:
                    if isinstance(run, dict) and "content" in run:
                        run["content"] = _USER_TEXT_SENTINEL
    return payload


def _serialized_node_tables(
    payload: dict[str, object],
) -> tuple[dict[object, object], ...]:
    tables: list[dict[object, object]] = []
    nodes = payload.get("nodes")
    if isinstance(nodes, dict):
        tables.append(nodes)
    definitions = payload.get("componentDefinitions")
    if isinstance(definitions, dict):
        for definition in definitions.values():
            if isinstance(definition, dict) and isinstance(definition.get("nodes"), dict):
                tables.append(definition["nodes"])
    return tuple(tables)


def _text_contents_by_node(
    payload: dict[str, object],
) -> dict[str, tuple[object, tuple[object, ...]]]:
    contents: dict[str, tuple[object, tuple[object, ...]]] = {}
    for nodes in _serialized_node_tables(payload):
        for node_id, node in nodes.items():
            if not isinstance(node, dict):
                continue
            text = node.get("text")
            if not isinstance(text, dict):
                continue
            runs = text.get("runs")
            run_contents = tuple(
                run.get("content") if isinstance(run, dict) else None
                for run in runs
            ) if isinstance(runs, (list, tuple)) else ()
            contents[str(node_id)] = (text.get("content"), run_contents)
    return contents


def _restore_text_contents(
    payload: dict[str, object],
    contents: Mapping[str, tuple[object, tuple[object, ...]]],
) -> None:
    for nodes in _serialized_node_tables(payload):
        for node_id, node in nodes.items():
            if str(node_id) not in contents or not isinstance(node, dict):
                continue
            content, run_contents = contents[str(node_id)]
            text = node.get("text")
            if not isinstance(text, dict):
                continue
            text["content"] = content
            runs = text.get("runs")
            if isinstance(runs, list):
                for index, run_content in enumerate(run_contents):
                    if index < len(runs) and isinstance(runs[index], dict):
                        runs[index]["content"] = run_content


def _redact_metadata_preserving_user_text(payload: dict[str, object]) -> dict[str, object]:
    contents = _text_contents_by_node(payload)
    redacted = redact_private_data(_metadata_projection(payload))
    if isinstance(redacted, dict):
        _restore_text_contents(redacted, contents)
    return cast(dict[str, object], redacted)


def _error(
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
    key = (code, node_id, path)
    if key not in seen:
        seen.add(key)
        diagnostics.append(
            _error(code, message, node_id=node_id, path=path)
        )


def _decision_for_node(
    node: FGUIPlanNode,
    decisions_by_id: Mapping[str, CapabilityDecision],
) -> CapabilityDecision | None:
    if node.decision_ref is None:
        return None
    return decisions_by_id.get(node.decision_ref)


def _longest_reachable_acyclic_depth(
    graph: Mapping[str, set[str] | tuple[str, ...]], roots: set[str]
) -> int:
    """Return longest root-originating DAG depth without recursing.

    Kahn processing intentionally leaves cyclic regions unranked; cycle diagnostics
    are emitted independently by the caller and cannot masquerade as depth errors.
    """
    reachable: set[str] = set()
    pending = list(roots)
    while pending:
        node_id = pending.pop()
        if node_id in reachable or node_id not in graph:
            continue
        reachable.add(node_id)
        pending.extend(child for child in graph[node_id] if child in graph)
    if not reachable:
        return 0

    indegree = {node_id: 0 for node_id in reachable}
    for node_id in reachable:
        for child_id in graph[node_id]:
            if child_id in reachable:
                indegree[child_id] += 1
    ready = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    depths = {root_id: 1 for root_id in roots if root_id in reachable}
    longest = max(depths.values(), default=0)
    while ready:
        node_id = ready.popleft()
        depth = depths.get(node_id)
        if depth is not None:
            longest = max(longest, depth)
        for child_id in sorted(graph[node_id]):
            if child_id not in reachable:
                continue
            if depth is not None:
                depths[child_id] = max(depths.get(child_id, 0), depth + 1)
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
    return longest


def _validate_tree(
    plan: FGUIPlanDocument,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    owners: dict[str, str] = {}
    root_counts: dict[str, int] = defaultdict(int)

    if plan.bindable and (not plan.roots or not plan.nodes):
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.tree_empty",
            "A bindable FairyGUI plan requires at least one root node.",
            path="$.roots",
        )

    for root_id in plan.roots:
        root_counts[root_id] += 1
        if root_counts[root_id] > 1:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.root_duplicate",
                "FairyGUI plan roots must be unique.",
                node_id=root_id,
            )
        root = plan.nodes.get(root_id)
        if root is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.root_missing",
                "FairyGUI plan root does not exist.",
                node_id=root_id,
            )
        elif root.parent_id is not None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.root_parent_incoherent",
                "FairyGUI plan roots cannot have a parent.",
                node_id=root.id,
            )

    for key in sorted(plan.nodes):
        node = plan.nodes[key]
        if key != node.id:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_key_mismatch",
                "FairyGUI plan node key differs from its ID.",
                node_id=node.id,
            )
        if node.parent_id is not None and node.parent_id not in plan.nodes:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.parent_missing",
                "FairyGUI plan parent node does not exist.",
                node_id=node.id,
            )

        local_children: set[str] = set()
        for child_id in node.children:
            child = plan.nodes.get(child_id)
            if child is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.child_missing",
                    "FairyGUI plan child node does not exist.",
                    node_id=node.id,
                )
                continue
            previous_owner = owners.get(child_id)
            if child_id in local_children or (
                previous_owner is not None and previous_owner != node.id
            ):
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.child_multiple_parents",
                    "FairyGUI plan child is owned more than once.",
                    node_id=child_id,
                )
            else:
                owners[child_id] = node.id
                local_children.add(child_id)
            if child.parent_id != node.id:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.parent_mismatch",
                    "FairyGUI plan child and parent references are not symmetric.",
                    node_id=child_id,
                )

    for node in plan.nodes.values():
        if node.parent_id is None or node.parent_id not in plan.nodes:
            if node.id not in plan.roots:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.node_unowned",
                    "Every emitted plan node must be a root or an owned child.",
                    node_id=node.id,
                )
        elif node.id not in plan.nodes[node.parent_id].children:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.parent_mismatch",
                "FairyGUI plan child and parent references are not symmetric.",
                node_id=node.id,
            )

    colors: dict[str, int] = {}
    depth_reported = False
    for start_id in sorted(plan.nodes):
        if colors.get(start_id, 0) != 0:
            continue
        colors[start_id] = 1
        stack: list[tuple[str, int, int]] = [(start_id, 0, 1)]
        while stack:
            node_id, child_index, depth = stack[-1]
            if depth > MAX_CONTRACT_TREE_DEPTH and not depth_reported:
                depth_reported = True
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.tree_depth_exceeded",
                    "FairyGUI plan tree depth exceeds the supported contract limit.",
                    path="$.nodes",
                )
            children = plan.nodes[node_id].children
            if child_index >= len(children):
                colors[node_id] = 2
                stack.pop()
                continue
            child_id = children[child_index]
            stack[-1] = (node_id, child_index + 1, depth)
            if child_id not in plan.nodes:
                continue
            color = colors.get(child_id, 0)
            if color == 0:
                colors[child_id] = 1
                stack.append((child_id, 0, depth + 1))
            elif color == 1:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.node_cycle",
                    "FairyGUI plan child references contain a cycle.",
                    node_id=child_id,
                )

    roots_by_node: dict[str, set[str]] = defaultdict(set)
    for root_id in dict.fromkeys(plan.roots):
        if root_id not in plan.nodes:
            continue
        pending = [root_id]
        visited: set[str] = set()
        while pending:
            node_id = pending.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            roots_by_node[node_id].add(root_id)
            pending.extend(
                child_id
                for child_id in plan.nodes[node_id].children
                if child_id in plan.nodes
            )

    for node_id in sorted(plan.nodes):
        owning_roots = roots_by_node.get(node_id, set())
        if not owning_roots:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_unreachable",
                "FairyGUI plan node is not reachable from any root.",
                node_id=node_id,
            )
        elif len(owning_roots) > 1:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_multiple_roots",
                "FairyGUI plan node is reachable from more than one root.",
                node_id=node_id,
            )


def _validate_component_definition_tree(
    definition_id: str,
    root_node_ref: str,
    nodes: Mapping[str, FGUIPlanNode],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    path = f"$.componentDefinitions.{definition_id}"
    root = nodes.get(root_node_ref)
    if root is None:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.component_definition_root_missing",
            "Component definition root node does not exist in its local node table.",
            path=f"{path}.rootNodeRef",
        )
    elif root.parent_id is not None:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.component_definition_root_parent_incoherent",
            "Component definition root node cannot have a parent.",
            node_id=root.id,
        )

    owners: dict[str, str] = {}
    for key in sorted(nodes):
        node = nodes[key]
        if key != node.id:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.component_definition_node_key_mismatch",
                "Component definition node key differs from its ID.",
                path=f"{path}.nodes.{key}",
            )
        if node.parent_id is not None and node.parent_id not in nodes:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.component_definition_parent_missing",
                "Component definition parent does not exist in the same definition.",
                node_id=node.id,
            )
        local_children: set[str] = set()
        for child_id in node.children:
            child = nodes.get(child_id)
            if child is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.component_definition_child_missing",
                    "Component definition child does not exist in the same definition.",
                    node_id=node.id,
                )
                continue
            if child_id in local_children or child_id in owners:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.component_definition_child_multiple_parents",
                    "Component definition child is owned more than once.",
                    node_id=child_id,
                )
            else:
                local_children.add(child_id)
                owners[child_id] = node.id
            if child.parent_id != node.id:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.component_definition_parent_mismatch",
                    "Component definition child and parent references are not symmetric.",
                    node_id=child_id,
                )

    for node in nodes.values():
        if node.id == root_node_ref:
            continue
        if node.parent_id is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.component_definition_node_unowned",
                "Every component definition node must belong to its local root.",
                node_id=node.id,
            )
        elif node.parent_id in nodes and node.id not in nodes[node.parent_id].children:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.component_definition_parent_mismatch",
                "Component definition child and parent references are not symmetric.",
                node_id=node.id,
            )

    colors: dict[str, int] = {}
    for start_id in sorted(nodes):
        if colors.get(start_id, 0) != 0:
            continue
        colors[start_id] = 1
        stack: list[tuple[str, int]] = [(start_id, 0)]
        while stack:
            node_id, child_index = stack[-1]
            children = nodes[node_id].children
            if child_index >= len(children):
                colors[node_id] = 2
                stack.pop()
                continue
            child_id = children[child_index]
            stack[-1] = (node_id, child_index + 1)
            if child_id not in nodes:
                continue
            color = colors.get(child_id, 0)
            if color == 0:
                colors[child_id] = 1
                stack.append((child_id, 0))
            elif color == 1:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.component_definition_node_cycle",
                    "Component definition child references contain a cycle.",
                    node_id=child_id,
                )

    local_graph = {
        node_id: tuple(child_id for child_id in node.children if child_id in nodes)
        for node_id, node in nodes.items()
    }
    if (
        _longest_reachable_acyclic_depth(local_graph, {root_node_ref})
        > MAX_CONTRACT_TREE_DEPTH
    ):
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.component_definition_tree_depth_exceeded",
            "Component definition tree exceeds the supported contract limit.",
            path=f"{path}.nodes",
        )

    reachable: set[str] = set()
    pending = [root_node_ref] if root_node_ref in nodes else []
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(
            child_id for child_id in nodes[node_id].children if child_id in nodes
        )
    for node_id in sorted(set(nodes) - reachable):
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.component_definition_node_unreachable",
            "Component definition node is unreachable from its local root.",
            node_id=node_id,
        )


def _validate_component_definitions(
    plan: FGUIPlanDocument,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> dict[str, FGUIPlanNode]:
    all_nodes = dict(plan.nodes)
    node_owners: dict[str, str | None] = {node_id: None for node_id in plan.nodes}
    definition_graph: dict[str, set[str]] = {
        definition_id: set() for definition_id in plan.component_definitions
    }
    top_level_references: set[str] = set()

    for key in sorted(plan.component_definitions):
        definition = plan.component_definitions[key]
        if key != definition.id:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.component_definition_key_mismatch",
                "Component definition key differs from its ID.",
                path=f"$.componentDefinitions.{key}",
            )
        _validate_component_definition_tree(
            key, definition.root_node_ref, definition.nodes, diagnostics, seen
        )
        for node_id, node in definition.nodes.items():
            if node_id in node_owners:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.component_definition_node_multiple_owners",
                    "A plan node ID cannot be owned by multiple component trees.",
                    node_id=node_id,
                )
            else:
                node_owners[node_id] = key
                all_nodes[node_id] = node
            if node.type != PlanNodeType.COMPONENT_REFERENCE or node.component is None:
                continue
            definition_ref = node.component.definition_ref
            if definition_ref not in plan.component_definitions:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.component_definition_missing",
                    "Component reference does not own a self-contained definition.",
                    node_id=node_id,
                )
            else:
                definition_graph[key].add(definition_ref)

    for node_id, node in plan.nodes.items():
        if node.type != PlanNodeType.COMPONENT_REFERENCE or node.component is None:
            continue
        definition_ref = node.component.definition_ref
        if definition_ref not in plan.component_definitions:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.component_definition_missing",
                "Component reference does not own a self-contained definition.",
                node_id=node_id,
            )
            continue
        top_level_references.add(definition_ref)

    reachable_definitions: set[str] = set()
    pending_definitions = sorted(top_level_references, reverse=True)
    while pending_definitions:
        definition_id = pending_definitions.pop()
        if definition_id in reachable_definitions:
            continue
        reachable_definitions.add(definition_id)
        pending_definitions.extend(
            sorted(definition_graph.get(definition_id, ()), reverse=True)
        )
    for definition_id in sorted(set(plan.component_definitions) - reachable_definitions):
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.component_definition_unused",
            "Every component definition must be reachable from a root plan component.",
            path=f"$.componentDefinitions.{definition_id}",
        )

    colors: dict[str, int] = {}
    for start_id in sorted(definition_graph):
        if colors.get(start_id, 0) != 0:
            continue
        colors[start_id] = 1
        children = sorted(definition_graph[start_id])
        stack: list[tuple[str, tuple[str, ...], int]] = [
            (start_id, tuple(children), 0)
        ]
        while stack:
            definition_id, edges, edge_index = stack[-1]
            if edge_index >= len(edges):
                colors[definition_id] = 2
                stack.pop()
                continue
            target_id = edges[edge_index]
            stack[-1] = (definition_id, edges, edge_index + 1)
            color = colors.get(target_id, 0)
            if color == 0:
                colors[target_id] = 1
                stack.append(
                    (target_id, tuple(sorted(definition_graph[target_id])), 0)
                )
            elif color == 1:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.component_definition_cycle",
                    "Component definitions cannot recursively reference one another.",
                    path=f"$.componentDefinitions.{target_id}",
                )
    if (
        _longest_reachable_acyclic_depth(definition_graph, top_level_references)
        > MAX_CONTRACT_TREE_DEPTH
    ):
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.component_definition_depth_exceeded",
            "Component definition reference depth exceeds the contract limit.",
            path="$.componentDefinitions",
        )
    return all_nodes


def _validate_resources(
    plan: FGUIPlanDocument,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    allowed_formats = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/svg+xml": "svg",
        "image/webp": "webp",
    }
    for key in sorted(plan.resources):
        resource = plan.resources[key]
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                resource.id,
                resource.source_asset_ref,
                resource.logical_asset_id,
                resource.mime_type,
            )
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_identity_invalid",
                "FairyGUI resource identity fields must be non-blank.",
                path=f"$.resources.{key}",
            )
        if len(resource.consumers) != len(set(resource.consumers)):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_consumers_duplicate",
                "FairyGUI resource consumers must be unique.",
                path=f"$.resources.{key}.consumers",
            )
        elif resource.consumers != tuple(sorted(resource.consumers)):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_consumers_noncanonical",
                "FairyGUI resource consumers must use canonical sorted order.",
                path=f"$.resources.{key}.consumers",
            )
        if not resource.consumers:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_unowned",
                "Every FairyGUI resource must have at least one plan-node consumer.",
                path=f"$.resources.{key}.consumers",
            )
        if allowed_formats.get(resource.mime_type) != resource.export_format:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_format_incoherent",
                "Resource MIME type and export format are inconsistent.",
                path=f"$.resources.{key}",
            )
        if key != resource.id:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_key_mismatch",
                "FairyGUI resource key differs from its ID.",
                path=f"$.resources.{key}",
            )
        if resource.content_sha256 is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_content_hash_missing",
                "Bindable resources require a source content SHA-256.",
                path=f"$.resources.{key}.contentSha256",
            )
        export_payload = json.dumps(
            {
                "exportFormat": resource.export_format,
                "height": resource.height,
                "mimeType": resource.mime_type,
                "width": resource.width,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        expected_export_hash = hashlib.sha256(export_payload).hexdigest()
        if resource.export_parameters_sha256 != expected_export_hash:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.export_parameters_hash_mismatch",
                "Resource export-parameter hash does not match its export recipe.",
                path=f"$.resources.{key}.exportParametersSha256",
            )

        grid = resource.nine_slice
        if grid is not None:
            if grid.x < 0 or grid.y < 0 or grid.width <= 0 or grid.height <= 0:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.nine_slice_nonpositive",
                    "Nine-slice coordinates and dimensions must be nonnegative and positive.",
                    path=f"$.resources.{key}.nineSlice",
                )
            if resource.width is None or resource.height is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.nine_slice_dimensions_missing",
                    "Nine-slice validation requires explicit resource dimensions.",
                    path=f"$.resources.{key}.nineSlice",
                )
            elif (
                grid.x + grid.width > resource.width
                or grid.y + grid.height > resource.height
            ):
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.nine_slice_out_of_bounds",
                    "Nine-slice grid must fit within the resource dimensions.",
                    path=f"$.resources.{key}.nineSlice",
                )

        raster_consumers = tuple(
            node
            for node in plan.nodes.values()
            if node.resource_ref == resource.id
            and node.type == PlanNodeType.RASTER_SUBTREE
        )
        if raster_consumers:
            if resource.mime_type != "image/png" or resource.export_format != "png":
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.fallback_format_incoherent",
                    "Raster fallback resources must use PNG.",
                    path=f"$.resources.{key}",
                )
            if not isinstance(resource.reason, str) or not resource.reason.strip():
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.fallback_reason_missing",
                    "Raster fallback resources require a stable fallback reason.",
                    path=f"$.resources.{key}.reason",
                )
        for consumer_id in resource.consumers:
            consumer = plan.nodes.get(consumer_id)
            if consumer is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.resource_consumer_missing",
                    "FairyGUI resource consumer does not exist.",
                    node_id=consumer_id,
                )
            elif consumer.resource_ref != resource.id:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.resource_consumer_mismatch",
                    "FairyGUI resource ownership is not symmetric.",
                    node_id=consumer.id,
                )

    for node in plan.nodes.values():
        if node.resource_ref is None:
            continue
        referenced_resource = plan.resources.get(node.resource_ref)
        if referenced_resource is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_missing",
                "FairyGUI plan resource does not exist.",
                node_id=node.id,
            )
        elif node.id not in referenced_resource.consumers:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_consumer_mismatch",
                "FairyGUI resource ownership is not symmetric.",
                node_id=node.id,
            )

    nodes_by_resource: dict[str, list[FGUIPlanNode]] = defaultdict(list)
    for node in plan.nodes.values():
        if node.resource_ref is not None:
            nodes_by_resource[node.resource_ref].append(node)
    for resource_ref, consumers in nodes_by_resource.items():
        has_raster = any(node.type == PlanNodeType.RASTER_SUBTREE for node in consumers)
        has_native = any(node.type != PlanNodeType.RASTER_SUBTREE for node in consumers)
        if has_raster and has_native:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.raster_native_resource_duplicate",
                "A resource cannot be owned by both raster and native plan nodes.",
                path=f"$.resources.{resource_ref}",
            )


def _validate_decisions(
    plan: FGUIPlanDocument,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> dict[str, CapabilityDecision]:
    decisions_by_id: dict[str, CapabilityDecision] = {}
    for key in sorted(plan.decisions):
        decision = plan.decisions[key]
        if key != decision.node_ref:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_key_mismatch",
                "Capability decision key differs from its node reference.",
                node_id=decision.node_ref,
            )
        if decision.id in decisions_by_id:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_id_duplicate",
                "Capability decision ID is not unique.",
                node_id=decision.node_ref,
            )
        else:
            decisions_by_id[decision.id] = decision
        if not decision.id.strip():
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_id_invalid",
                "Capability decision IDs must be non-blank.",
                node_id=decision.node_ref,
            )
        if decision.rule_version != plan.rule_version:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_rule_version_mismatch",
                "Capability decision rule version differs from the plan.",
                node_id=decision.node_ref,
            )
        if not decision.evidence:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_evidence_missing",
                "Capability decisions require stable public evidence.",
                node_id=decision.node_ref,
            )
        elif any(not item.strip() for item in decision.evidence):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_evidence_invalid",
                "Capability decision evidence items must be non-blank.",
                node_id=decision.node_ref,
            )
        if decision.status == CapabilityStatus.UNSUPPORTED:
            if is_reviewable_text_decision(decision):
                continue
            has_blocking_diagnostic = any(
                item.severity == Severity.ERROR
                and item.node_id == decision.node_ref
                and item.code == decision.rule_id
                and item.rule_id == decision.rule_id
                and item.rule_version == decision.rule_version
                and item.evidence == decision.evidence
                and bool(item.suggested_action)
                and item.blocks_binding
                for item in plan.diagnostics
            )
            if not decision.blocking or not has_blocking_diagnostic:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.unsupported_not_blocked",
                    "Unsupported capability requires a blocking decision and diagnostic.",
                    node_id=decision.node_ref,
                )
        elif decision.status == CapabilityStatus.RASTER_FALLBACK:
            has_fallback_diagnostic = any(
                item.code
                in {"fgui.visual.raster_fallback", "fgui.mask.raster_fallback"}
                and item.severity == Severity.WARNING
                and item.node_id == decision.node_ref
                and item.rule_id == decision.rule_id
                and item.rule_version == decision.rule_version
                and item.evidence == decision.evidence
                and bool(item.suggested_action)
                and not item.blocks_binding
                for item in plan.diagnostics
            )
            if not has_fallback_diagnostic:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.fallback_diagnostic_missing",
                    "Raster fallback capability requires a matching review diagnostic.",
                    node_id=decision.node_ref,
                )
            if decision.blocking:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.decision_blocking_incoherent",
                    "Only unsupported capability decisions may be blocking.",
                    node_id=decision.node_ref,
                )
        elif decision.blocking:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_blocking_incoherent",
                "Only unsupported capability decisions may be blocking.",
                node_id=decision.node_ref,
            )
    return decisions_by_id


def _validate_embedded_diagnostics(
    plan: FGUIPlanDocument,
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    for index, item in enumerate(plan.diagnostics):
        complete = (
            isinstance(item.rule_id, str)
            and bool(item.rule_id.strip())
            and item.rule_version == plan.rule_version
            and bool(item.evidence)
            and all(
                isinstance(evidence, str) and bool(evidence.strip())
                for evidence in item.evidence
            )
            and isinstance(item.suggested_action, str)
            and bool(item.suggested_action.strip())
            and (item.severity != Severity.ERROR or item.blocks_binding)
        )
        if not complete:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.diagnostic_contract_incomplete",
                "Embedded diagnostics require rule, evidence, action, and binding impact.",
                path=f"$.diagnostics[{index}]",
            )


def _complete_validation_diagnostic(
    item: Diagnostic,
    *,
    rule_version: int,
) -> Diagnostic:
    evidence = item.evidence or (
        f"path={item.path}"
        if item.path is not None
        else f"node.id={item.node_id or 'document'}",
    )
    return item.model_copy(
        update={
            "rule_id": item.rule_id or item.code,
            "rule_version": rule_version,
            "evidence": evidence,
            "suggested_action": item.suggested_action or "repair_generation_plan",
            "blocks_binding": item.severity == Severity.ERROR,
        }
    )


def _validate_node_payload_and_decision(
    plan: FGUIPlanDocument,
    decisions_by_id: Mapping[str, CapabilityDecision],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    decision_owners: dict[str, str] = {}
    for node in plan.nodes.values():
        if node.type in _TEXT_NODE_TYPES and node.text is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_payload_required",
                "Text plan nodes require a text payload.",
                node_id=node.id,
            )
        elif node.type not in _TEXT_NODE_TYPES and node.text is not None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_payload_forbidden",
                "Only text plan nodes may contain a text payload.",
                node_id=node.id,
            )
        if node.type == PlanNodeType.GRAPH and node.graph is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_payload_required",
                "Graph plan nodes require a graph payload.",
                node_id=node.id,
            )
        elif node.type != PlanNodeType.GRAPH and node.graph is not None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_payload_forbidden",
                "Only graph plan nodes may contain a graph payload.",
                node_id=node.id,
            )
        if node.type == PlanNodeType.RICH_TEXT and node.text is not None:
            if not node.text.runs:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.rich_text_runs_required",
                    "Rich-text plan nodes require at least one text run.",
                    node_id=node.id,
                )
            elif "".join(run.content for run in node.text.runs) != node.text.content:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.rich_text_content_mismatch",
                    "Rich-text runs must reconstruct the full text content.",
                    node_id=node.id,
                )
        elif (
            node.type == PlanNodeType.TEXT
            and node.text is not None
            and node.text.runs
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.plain_text_runs_forbidden",
                "Plain-text plan nodes cannot contain styled text runs.",
                node_id=node.id,
            )

        if node.type == PlanNodeType.COMPONENT_REFERENCE and node.component is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_payload_required",
                "Component-reference nodes require a component payload.",
                node_id=node.id,
            )
        elif (
            node.type == PlanNodeType.COMPONENT_REFERENCE
            and node.component is not None
            and not node.component.candidate_key.strip()
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.component_candidate_invalid",
                "Component-reference candidates must be non-blank.",
                node_id=node.id,
            )
        elif node.type != PlanNodeType.COMPONENT_REFERENCE and node.component is not None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_payload_forbidden",
                "Only component-reference nodes may contain a component payload.",
                node_id=node.id,
            )

        if node.type in _RESOURCE_NODE_TYPES and node.resource_ref is None:
            code = (
                "fgui.plan.fallback_resource_required"
                if node.type == PlanNodeType.RASTER_SUBTREE
                else "fgui.plan.node_payload_required"
            )
            _append_once(
                diagnostics,
                seen,
                code,
                "Resource-bearing plan node requires a resource.",
                node_id=node.id,
            )
        elif (
            node.type not in _RESOURCE_NODE_TYPES
            and node.resource_ref is not None
            and node.resource_ref in plan.resources
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.node_payload_forbidden",
                "This plan-node type cannot own a raster resource.",
                node_id=node.id,
            )

        if node.type == PlanNodeType.RASTER_SUBTREE and node.children:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.raster_descendant_duplicate",
                "Raster-subtree nodes cannot retain native descendants.",
                node_id=node.id,
            )

        if node.decision_ref is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_missing",
                "FairyGUI plan node requires a capability decision.",
                node_id=node.id,
            )
            continue
        decision = decisions_by_id.get(node.decision_ref)
        if decision is None:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_missing",
                "FairyGUI plan capability decision does not exist.",
                node_id=node.id,
            )
            continue
        previous_owner = decision_owners.get(decision.id)
        if previous_owner is not None and previous_owner != node.id:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_multiple_nodes",
                "Capability decision is owned by more than one plan node.",
                node_id=node.id,
            )
        else:
            decision_owners[decision.id] = node.id
        if decision.node_ref != node.uir_node_ref:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_node_mismatch",
                "Capability decision and plan node refer to different UIR nodes.",
                node_id=node.id,
            )

        if decision.status == CapabilityStatus.NATIVE:
            expected_type = NATIVE_RULE_TO_NODE_TYPE.get(decision.rule_id)
            if expected_type is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.decision_status_rule_incoherent",
                    "Native capability decision uses an unknown native rule.",
                    node_id=node.id,
                )
            elif node.type != expected_type:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.decision_node_type_mismatch",
                    "Native capability rule does not match the plan-node type.",
                    node_id=node.id,
                )
        elif decision.status == CapabilityStatus.RASTER_FALLBACK:
            if decision.rule_id != RASTER_SUBTREE_RULE_ID:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.decision_status_rule_incoherent",
                    "Raster fallback must use the raster-subtree rule.",
                    node_id=node.id,
                )
            if node.type != PlanNodeType.RASTER_SUBTREE:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.decision_node_type_mismatch",
                    "Raster fallback decision requires a raster-subtree node.",
                    node_id=node.id,
                )
            if node.resource_ref is None:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.fallback_resource_required",
                    "Raster fallback decision requires a resource.",
                    node_id=node.id,
                )
        elif not (
            is_reviewable_text_decision(decision)
            and node.type == PlanNodeType.TEXT
            and node.text is not None
            and not node.text.runs
            and node.resource_ref is None
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.unsupported_node_emitted",
                "Unsupported capability decisions cannot own emitted plan nodes.",
                node_id=node.id,
            )

    for decision in plan.decisions.values():
        if (
            decision.status != CapabilityStatus.UNSUPPORTED
            or is_reviewable_text_decision(decision)
        ) and decision.id not in decision_owners:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_orphan",
                "Native and raster-fallback decisions require an emitted plan node.",
                node_id=decision.node_ref,
            )


def _native_mask_nodes(
    mask: MaskPlan,
    nodes_by_uir_ref: Mapping[str, list[FGUIPlanNode]],
) -> tuple[FGUIPlanNode | None, tuple[FGUIPlanNode, ...]]:
    source_matches = nodes_by_uir_ref.get(mask.mask_node_ref, [])
    source = source_matches[0] if len(source_matches) == 1 else None
    contents = tuple(
        matches[0]
        for ref in mask.content_node_refs
        if len(matches := nodes_by_uir_ref.get(ref, [])) == 1
    )
    return source, contents


def _validate_masks(
    plan: FGUIPlanDocument,
    decisions_by_id: Mapping[str, CapabilityDecision],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    mask_targets: dict[str, list[FGUIPlanNode]] = defaultdict(list)
    nodes_by_uir_ref: dict[str, list[FGUIPlanNode]] = defaultdict(list)
    for node in plan.nodes.values():
        nodes_by_uir_ref[node.uir_node_ref].append(node)
        if node.mask_ref is None:
            continue
        if node.mask_ref not in plan.masks:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_missing",
                "FairyGUI plan mask does not exist.",
                node_id=node.id,
            )
        else:
            mask_targets[node.mask_ref].append(node)

    for uir_ref, nodes in nodes_by_uir_ref.items():
        if len(nodes) > 1:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.uir_node_duplicate",
                "More than one plan node owns the same UIR node.",
                node_id=uir_ref,
            )

    for key in sorted(plan.masks):
        mask = plan.masks[key]
        if key != mask.id:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_key_mismatch",
                "FairyGUI mask key differs from its ID.",
                path=f"$.masks.{key}",
            )
        targets = mask_targets.get(mask.id, [])
        if not targets:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_orphan",
                "FairyGUI mask is not owned by a plan node.",
                path=f"$.masks.{key}",
            )
        elif len(targets) > 1:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_multiple_targets",
                "FairyGUI mask is assigned to more than one target node.",
                path=f"$.masks.{key}",
            )

        if mask.resource_ref is not None and mask.resource_ref not in plan.resources:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.resource_missing",
                "FairyGUI mask resource does not exist.",
                path=f"$.masks.{key}.resourceRef",
            )
        if (
            not mask.mask_node_ref
            or not mask.content_node_refs
            or mask.mask_node_ref in mask.content_node_refs
            or len(set(mask.content_node_refs)) != len(mask.content_node_refs)
            or any(not content_ref for content_ref in mask.content_node_refs)
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_hierarchy_incoherent",
                "Mask source and content references must be distinct.",
                path=f"$.masks.{key}",
            )

        if mask.mode == MaskMode.RASTER_SUBTREE:
            _validate_raster_mask(
                plan, mask, targets, nodes_by_uir_ref, decisions_by_id, diagnostics, seen
            )
        else:
            _validate_native_mask(
                mask, targets, nodes_by_uir_ref, decisions_by_id, diagnostics, seen
            )

    native_clip_sources = {
        mask.mask_node_ref
        for mask in plan.masks.values()
        if mask.mode == MaskMode.NATIVE_CLIP
        and len(mask_targets.get(mask.id, [])) == 1
    }
    for node in plan.nodes.values():
        decision = _decision_for_node(node, decisions_by_id)
        if (
            decision is not None
            and decision.rule_id == NATIVE_CLIP_SOURCE_RULE_ID
            and node.uir_node_ref not in native_clip_sources
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.decision_mask_role_incoherent",
                "Native clip-source decisions require a native clip mask role.",
                node_id=node.id,
            )


def _validate_raster_mask(
    plan: FGUIPlanDocument,
    mask: MaskPlan,
    targets: list[FGUIPlanNode],
    nodes_by_uir_ref: Mapping[str, list[FGUIPlanNode]],
    decisions_by_id: Mapping[str, CapabilityDecision],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    if mask.resource_ref is None:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.mask_resource_incoherent",
            "Raster-subtree masks require a resource.",
            path=f"$.masks.{mask.id}",
        )
    for target in targets:
        decision = _decision_for_node(target, decisions_by_id)
        if target.type != PlanNodeType.RASTER_SUBTREE:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_node_type_incoherent",
                "Raster-subtree mask target must be a raster-subtree node.",
                node_id=target.id,
            )
        if target.resource_ref != mask.resource_ref:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_resource_incoherent",
                "Raster mask and target must own the same resource.",
                node_id=target.id,
            )
        if (
            decision is None
            or decision.status != CapabilityStatus.RASTER_FALLBACK
            or decision.rule_id != RASTER_SUBTREE_RULE_ID
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_decision_incoherent",
                "Raster mask target requires a raster-fallback decision.",
                node_id=target.id,
            )

    emitted_refs = {mask.mask_node_ref, *mask.content_node_refs}.intersection(
        nodes_by_uir_ref
    )
    if emitted_refs:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.raster_descendant_duplicate",
            "Raster mask source/content nodes cannot also be emitted natively.",
            path=f"$.masks.{mask.id}",
        )


def _validate_native_mask(
    mask: MaskPlan,
    targets: list[FGUIPlanNode],
    nodes_by_uir_ref: Mapping[str, list[FGUIPlanNode]],
    decisions_by_id: Mapping[str, CapabilityDecision],
    diagnostics: list[Diagnostic],
    seen: set[tuple[str, str | None, str | None]],
) -> None:
    source, contents = _native_mask_nodes(mask, nodes_by_uir_ref)
    if source is None:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.mask_source_missing",
            "Native mask source node does not exist exactly once.",
            path=f"$.masks.{mask.id}.maskNodeRef",
        )
    for content_ref in mask.content_node_refs:
        if len(nodes_by_uir_ref.get(content_ref, [])) != 1:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_content_missing",
                "Native mask content node does not exist exactly once.",
                node_id=content_ref,
            )
    if mask.resource_ref is not None:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.mask_resource_incoherent",
            "Native masks cannot own a raster-subtree resource.",
            path=f"$.masks.{mask.id}.resourceRef",
        )

    for content in contents:
        content_decision = _decision_for_node(content, decisions_by_id)
        reviewable_text_content = (
            content.type == PlanNodeType.TEXT
            and content_decision is not None
            and is_reviewable_text_decision(content_decision)
        )
        if (
            not reviewable_text_content
            and (
                content_decision is None
                or content_decision.status
                not in {CapabilityStatus.NATIVE, CapabilityStatus.RASTER_FALLBACK}
                or node_type_for_capability(
                    content_decision.status, content_decision.rule_id
                )
                != content.type
            )
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_decision_incoherent",
                "Native mask content requires a coherent emitted decision.",
                node_id=content.id,
            )

    for target in targets:
        if target.type not in {PlanNodeType.CONTAINER, PlanNodeType.GRAPH}:
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_node_type_incoherent",
                "Native mask target must be a container or editable graph node.",
                node_id=target.id,
            )
        implicit_self_clip = (
            source is target
            and mask.mode == MaskMode.NATIVE_CLIP
            and mask.mask_node_ref == target.uir_node_ref
        )
        participants = (
            contents
            if implicit_self_clip
            else (() if source is None else (source,)) + contents
        )
        if any(node.parent_id != target.id for node in participants):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_hierarchy_incoherent",
                "Native mask participants must be children of the target.",
                node_id=target.id,
            )
        if source is not None and len(contents) == len(mask.content_node_refs):
            expected = (
                tuple(node.id for node in contents)
                if implicit_self_clip
                else (source.id, *(node.id for node in contents))
            )
            try:
                start = target.children.index(expected[0])
            except ValueError:
                start = -1
            if start < 0 or target.children[start : start + len(expected)] != expected:
                _append_once(
                    diagnostics,
                    seen,
                    "fgui.plan.mask_hierarchy_incoherent",
                    "Native mask content must immediately follow its source.",
                    node_id=target.id,
                )

    if source is None:
        return
    bounds = source.transform.bounds
    if not (
        all(math.isfinite(value) for value in (bounds.x, bounds.y, bounds.width, bounds.height))
        and bounds.width > 0
        and bounds.height > 0
    ):
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.mask_geometry_incoherent",
            "Native mask sources require finite positive bounds.",
            node_id=source.id,
        )
    if mask.kind == MaskKind.ROUNDED_RECTANGLE:
        radii = mask.corner_radii
        if (
            radii is None
            or any(not math.isfinite(value) or value < 0 for value in radii)
            or any(value > min(bounds.width, bounds.height) / 2 for value in radii)
        ):
            _append_once(
                diagnostics,
                seen,
                "fgui.plan.mask_corner_radius_incoherent",
                "Rounded native clips require bounded corner radii.",
                node_id=source.id,
            )
    elif mask.corner_radii is not None and any(mask.corner_radii):
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.mask_corner_radius_incoherent",
            "Only rounded native clips may carry nonzero corner radii.",
            node_id=source.id,
        )
    decision = _decision_for_node(source, decisions_by_id)
    if mask.mode == MaskMode.NATIVE_CLIP:
        role_valid = (
            mask.kind in {MaskKind.RECTANGLE, MaskKind.ROUNDED_RECTANGLE}
            and source.type in {PlanNodeType.CONTAINER, PlanNodeType.GRAPH}
            and (source.type != PlanNodeType.GRAPH or source.graph is not None)
            and source.resource_ref is None
        )
        decision_valid = (
            decision is not None
            and decision.status == CapabilityStatus.NATIVE
            and decision.rule_id == NATIVE_CLIP_SOURCE_RULE_ID
        )
    else:
        role_valid = (
            mask.mode == MaskMode.NATIVE_MASK
            and mask.kind == MaskKind.IMAGE
            and source.type == PlanNodeType.IMAGE
            and source.resource_ref is not None
        )
        decision_valid = (
            decision is not None
            and decision.status == CapabilityStatus.NATIVE
            and decision.rule_id == NATIVE_IMAGE_RULE_ID
        )
    if not role_valid:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.mask_node_type_incoherent",
            "Native mask mode and source node role are inconsistent.",
            node_id=source.id,
        )
    if not decision_valid:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.mask_decision_incoherent",
            "Native mask mode and source decision are inconsistent.",
            node_id=source.id,
        )


def validate_fgui_plan(plan: FGUIPlanDocument) -> tuple[Diagnostic, ...]:
    """Return every independently detectable semantic error in stable order."""
    diagnostics: list[Diagnostic] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    projection = _metadata_projection(plan.model_dump(mode="python", by_alias=True))
    for violation in private_data_violations(projection):
        code = (
            "fgui.plan.binding_field_leak"
            if violation.category == "target_binding"
            else "fgui.plan.private_data_leak"
        )
        _append_once(
            diagnostics,
            seen,
            code,
            "Generation plans cannot contain private or target-binding data.",
            path=violation.path,
        )

    _validate_tree(plan, diagnostics, seen)
    all_nodes = _validate_component_definitions(plan, diagnostics, seen)
    validation_plan = plan.model_copy(update={"nodes": all_nodes})
    _validate_resources(validation_plan, diagnostics, seen)
    decisions_by_id = _validate_decisions(validation_plan, diagnostics, seen)
    _validate_embedded_diagnostics(validation_plan, diagnostics, seen)
    _validate_node_payload_and_decision(
        validation_plan, decisions_by_id, diagnostics, seen
    )
    _validate_masks(validation_plan, decisions_by_id, diagnostics, seen)

    is_blocked = bool(diagnostics) or any(
        item.severity == Severity.ERROR or item.blocks_binding
        for item in plan.diagnostics
    ) or any(decision.blocking for decision in plan.decisions.values())
    if plan.bindable == is_blocked:
        _append_once(
            diagnostics,
            seen,
            "fgui.plan.bindable_inconsistent",
            "Plan bindability disagrees with embedded errors or blocking decisions.",
        )
    return tuple(
        _complete_validation_diagnostic(item, rule_version=plan.rule_version)
        for item in diagnostics
    )


def canonical_plan_bytes(plan: FGUIPlanDocument) -> bytes:
    """Serialize a plan with stable object ordering and no insignificant whitespace."""
    payload = plan.model_dump(mode="python", by_alias=True)
    payload = _redact_metadata_preserving_user_text(payload)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def plan_sha256(plan: FGUIPlanDocument) -> str:
    """Return the SHA-256 digest of the canonical plan representation."""
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()
