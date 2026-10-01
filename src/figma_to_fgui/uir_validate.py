import hashlib
import json
from collections import defaultdict
from typing import cast

from figma_to_fgui.data_policy import private_data_violations, redact_private_data
from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.uir_models import (
    ConversionMode,
    MappingStatus,
    SemanticStatus,
    UIRDocument,
)

MAX_CONTRACT_TREE_DEPTH = 256
_USER_TEXT_SENTINEL = "<user-visible-text>"


def _metadata_projection(payload: dict[str, object]) -> dict[str, object]:
    """Exclude only user-visible text content from contract-wide metadata policy."""
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict):
        return payload
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


def _redact_metadata_preserving_user_text(payload: dict[str, object]) -> dict[str, object]:
    contents: dict[str, tuple[object, tuple[object, ...]]] = {}
    nodes = payload.get("nodes")
    if isinstance(nodes, dict):
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
    protected = _metadata_projection(payload)
    redacted = redact_private_data(protected)
    redacted_nodes = redacted.get("nodes") if isinstance(redacted, dict) else None
    if isinstance(redacted_nodes, dict):
        for node_id, (content, run_contents) in contents.items():
            node = redacted_nodes.get(node_id)
            if not isinstance(node, dict):
                continue
            text = node.get("text")
            if not isinstance(text, dict):
                continue
            text["content"] = content
            runs = text.get("runs")
            if isinstance(runs, list):
                for index, run_content in enumerate(run_contents):
                    if index < len(runs) and isinstance(runs[index], dict):
                        runs[index]["content"] = run_content
    return cast(dict[str, object], redacted)


def _error(
    code: str,
    message: str,
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
        evidence=(f"path={path}" if path is not None else f"node.id={node_id or 'document'}",),
        suggested_action="repair_uir_source",
        blocks_binding=True,
    )


def validate_uir(document: UIRDocument) -> tuple[Diagnostic, ...]:
    diagnostics: list[Diagnostic] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    def append(
        code: str,
        message: str,
        node_id: str | None = None,
        *,
        path: str | None = None,
    ) -> None:
        key = (code, node_id, path)
        if key not in seen:
            seen.add(key)
            diagnostics.append(_error(code, message, node_id, path))

    if not document.roots or not document.nodes:
        append(
            "uir.tree_empty",
            "A successful UIR conversion requires at least one root node.",
            path="$.roots",
        )

    projection = _metadata_projection(
        document.model_dump(mode="python", by_alias=True)
    )
    for violation in private_data_violations(projection):
        append(
            "uir.private_data_forbidden",
            "UIR contains private, raw, local-path, or target-binding data.",
            path=violation.path,
        )

    for index, item in enumerate(document.diagnostics):
        complete = (
            bool(item.code.strip())
            and bool(item.message.strip())
            and isinstance(item.rule_id, str)
            and bool(item.rule_id.strip())
            and isinstance(item.rule_version, int)
            and item.rule_version >= 1
            and bool(item.evidence)
            and all(bool(evidence.strip()) for evidence in item.evidence)
            and isinstance(item.suggested_action, str)
            and bool(item.suggested_action.strip())
            and (item.severity != Severity.ERROR or item.blocks_binding)
        )
        if not complete:
            append(
                "uir.diagnostic_contract_incomplete",
                "Embedded UIR diagnostics require stable review metadata.",
                path=f"$.diagnostics[{index}]",
            )
        if item.node_id is not None and item.node_id not in document.nodes:
            append(
                "uir.diagnostic_node_missing",
                "Embedded UIR diagnostic refers to an unknown node.",
                path=f"$.diagnostics[{index}].nodeId",
            )

    identity_values: list[tuple[str, object]] = [
        ("$.documentId", document.document_id),
        ("$.compilerVersion", document.compiler_version),
        ("$.source.selectionId", document.source.selection_id),
    ]
    identity_values.extend(
        (f"$.roots[{index}]", root_id)
        for index, root_id in enumerate(document.roots)
    )
    for index, (key, node) in enumerate(sorted(document.nodes.items())):
        identity_values.extend(
            (
                (f"$.nodes.<key:{index}>", key),
                (f"$.nodes.<key:{index}>.id", node.id),
                (f"$.nodes.<key:{index}>.source.nodeId", node.source.node_id),
                (f"$.nodes.<key:{index}>.source.type", node.source.type),
                (f"$.nodes.<key:{index}>.parentId", node.parent_id),
                (f"$.nodes.<key:{index}>.semantic.decisionRef", node.semantic.decision_ref),
                (f"$.nodes.<key:{index}>.conversion.assetRef", node.conversion.asset_ref),
            )
        )
        identity_values.extend(
            (f"$.nodes.<key:{index}>.children[{child_index}]", child_id)
            for child_index, child_id in enumerate(node.children)
        )
    for index, (key, asset) in enumerate(sorted(document.assets.items())):
        identity_values.extend(
            (
                (f"$.assets.<key:{index}>", key),
                (f"$.assets.<key:{index}>.id", asset.id),
                (f"$.assets.<key:{index}>.logicalId", asset.logical_id),
                (f"$.assets.<key:{index}>.mimeType", asset.mime_type),
            )
        )
    for index, (key, decision) in enumerate(sorted(document.mapping_decisions.items())):
        identity_values.extend(
            (
                (f"$.mappingDecisions.<key:{index}>", key),
                (f"$.mappingDecisions.<key:{index}>.id", decision.id),
                (f"$.mappingDecisions.<key:{index}>.nodeRef", decision.node_ref),
                (f"$.mappingDecisions.<key:{index}>.candidateKey", decision.candidate_key),
                (f"$.mappingDecisions.<key:{index}>.ruleSource", decision.rule_source),
            )
        )
    for path, value in identity_values:
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            append(
                "uir.identity_invalid",
                "UIR identity and reference fields must be non-blank strings.",
                path=path,
            )

    owners: dict[str, str] = {}
    root_counts: dict[str, int] = defaultdict(int)
    for root_id in document.roots:
        root_counts[root_id] += 1
        if root_counts[root_id] > 1:
            append("uir.root_duplicate", "UIR roots must be unique.", root_id)
        root = document.nodes.get(root_id)
        if root is None:
            append("uir.root_missing", "UIR root does not exist.", root_id)
        elif root.parent_id is not None:
            append(
                "uir.root_parent_incoherent",
                "UIR roots cannot have a parent.",
                root.id,
            )

    decision_owners: dict[str, list[str]] = defaultdict(list)
    for key in sorted(document.nodes):
        node = document.nodes[key]
        if key != node.id:
            append("uir.node_key_mismatch", "UIR node key differs from its ID.", node.id)
        if node.parent_id is not None and node.parent_id not in document.nodes:
            append("uir.parent_missing", "UIR parent node does not exist.", node.id)

        local_children: set[str] = set()
        for child_id in node.children:
            child = document.nodes.get(child_id)
            if child is None:
                append("uir.child_missing", "UIR child node does not exist.", node.id)
                continue
            previous_owner = owners.get(child_id)
            if child_id in local_children or (
                previous_owner is not None and previous_owner != node.id
            ):
                append(
                    "uir.child_multiple_parents",
                    "UIR child is owned more than once.",
                    child_id,
                )
            else:
                local_children.add(child_id)
                owners[child_id] = node.id
            if child.parent_id != node.id:
                append(
                    "uir.parent_mismatch",
                    "UIR child and parent references are not symmetric.",
                    child_id,
                )

        decision_ref = node.semantic.decision_ref
        if decision_ref is not None:
            owned_decision = document.mapping_decisions.get(decision_ref)
            if owned_decision is None:
                append(
                    "uir.decision_missing",
                    "UIR semantic mapping decision does not exist.",
                    node.id,
                )
            else:
                decision_owners[owned_decision.id].append(node.id)
                if owned_decision.node_ref != node.id:
                    append(
                        "uir.decision_node_mismatch",
                        "Mapping decision and consuming node do not match.",
                        node.id,
                    )

        asset_ref = node.conversion.asset_ref
        referenced_asset = None if asset_ref is None else document.assets.get(asset_ref)
        if asset_ref is not None and referenced_asset is None:
            append("uir.asset_missing", "UIR conversion asset does not exist.", node.id)
        if node.conversion.mode == ConversionMode.RASTER_FALLBACK:
            if referenced_asset is None:
                append(
                    "uir.raster_fallback_asset_required",
                    "Raster fallback conversion requires an asset.",
                    node.id,
                )
            elif (
                referenced_asset.mime_type != "image/png"
                or referenced_asset.export_format != "png"
            ):
                append(
                    "uir.raster_fallback_format_incoherent",
                    "Raster fallback conversion requires a PNG asset.",
                    node.id,
                )
            if not node.conversion.reasons:
                append(
                    "uir.raster_fallback_reason_required",
                    "Raster fallback conversion requires a stable reason.",
                    node.id,
                )
        if any(not reason.strip() for reason in node.conversion.reasons):
            append(
                "uir.conversion_reason_invalid",
                "UIR conversion reasons must be non-blank.",
                node.id,
            )
        if (
            node.component is not None
            and node.component.definition_ref is not None
            and node.component.definition_ref not in document.component_definitions
        ):
            append(
                "uir.component_definition_missing",
                "UIR component definition does not exist.",
                node.id,
            )
        if (
            node.conversion.mode == ConversionMode.COMPONENT_REFERENCE
            and decision_ref is None
        ):
            append(
                "uir.component_decision_required",
                "Component reference conversion requires a mapping decision.",
                node.id,
            )

    for node_id in sorted(document.nodes):
        node = document.nodes[node_id]
        if node.parent_id is None or node.parent_id not in document.nodes:
            if node.id not in document.roots:
                append(
                    "uir.node_unowned",
                    "Every UIR node must be a root or an owned child.",
                    node.id,
                )
        elif node.id not in document.nodes[node.parent_id].children:
            append(
                "uir.parent_mismatch",
                "UIR child and parent references are not symmetric.",
                node.id,
            )

    colors: dict[str, int] = {}
    depth_reported = False
    for start_id in sorted(document.nodes):
        if colors.get(start_id, 0) != 0:
            continue
        colors[start_id] = 1
        stack: list[tuple[str, int, int]] = [(start_id, 0, 1)]
        while stack:
            node_id, child_index, depth = stack[-1]
            if depth > MAX_CONTRACT_TREE_DEPTH and not depth_reported:
                depth_reported = True
                append(
                    "uir.tree_depth_exceeded",
                    "UIR tree depth exceeds the supported contract limit.",
                    path="$.nodes",
                )
            children = document.nodes[node_id].children
            if child_index >= len(children):
                colors[node_id] = 2
                stack.pop()
                continue
            child_id = children[child_index]
            stack[-1] = (node_id, child_index + 1, depth)
            if child_id not in document.nodes:
                continue
            color = colors.get(child_id, 0)
            if color == 0:
                colors[child_id] = 1
                stack.append((child_id, 0, depth + 1))
            elif color == 1:
                append(
                    "uir.node_cycle",
                    "UIR child references contain a cycle.",
                    child_id,
                )

    roots_by_node: dict[str, set[str]] = defaultdict(set)
    for root_id in dict.fromkeys(document.roots):
        if root_id not in document.nodes:
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
                for child_id in document.nodes[node_id].children
                if child_id in document.nodes
            )
    for node_id in sorted(document.nodes):
        owning_roots = roots_by_node.get(node_id, set())
        if not owning_roots:
            append(
                "uir.node_unreachable",
                "UIR node is not reachable from any root.",
                node_id,
            )
        elif len(owning_roots) > 1:
            append(
                "uir.node_multiple_roots",
                "UIR node is reachable from more than one root.",
                node_id,
            )

    for key in sorted(document.assets):
        asset = document.assets[key]
        if key != asset.id:
            append(
                "uir.asset_key_mismatch",
                "UIR asset key differs from its ID.",
                asset.source_node_id,
            )
        expected_format = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/svg+xml": "svg",
            "image/webp": "webp",
        }.get(asset.mime_type)
        if expected_format != asset.export_format:
            append(
                "uir.asset_format_incoherent",
                "UIR asset MIME type and export format are inconsistent.",
                asset.source_node_id,
                path=f"$.assets.{key}",
            )
    for key in sorted(document.component_definitions):
        definition = document.component_definitions[key]
        if key != definition.id:
            append(
                "uir.component_definition_key_mismatch",
                "UIR component-definition key differs from its ID.",
                definition.source_node_id,
            )

    decisions_by_id: dict[str, str] = {}
    for key in sorted(document.mapping_decisions):
        decision = document.mapping_decisions[key]
        if not decision.evidence or any(
            not isinstance(item, str) or not item.strip() for item in decision.evidence
        ):
            append(
                "uir.decision_evidence_invalid",
                "UIR mapping decisions require non-blank review evidence.",
                decision.node_ref,
            )
        if key != decision.id:
            append(
                "uir.decision_key_mismatch",
                "UIR mapping-decision key differs from its ID.",
                decision.node_ref,
            )
        previous_key = decisions_by_id.get(decision.id)
        if previous_key is not None and previous_key != key:
            append(
                "uir.decision_id_duplicate",
                "UIR mapping-decision ID is not unique.",
                decision.node_ref,
            )
        else:
            decisions_by_id[decision.id] = key

        owning_nodes = decision_owners.get(decision.id, [])
        if not owning_nodes:
            append(
                "uir.decision_orphan",
                "UIR mapping decision is not owned by a node.",
                decision.node_ref,
            )
        elif len(owning_nodes) > 1:
            append(
                "uir.decision_multiple_nodes",
                "UIR mapping decision is owned by more than one node.",
                decision.node_ref,
            )
        if owning_nodes and decision.node_ref not in owning_nodes:
            append(
                "uir.decision_node_mismatch",
                "Mapping decision owner differs from its node reference.",
                decision.node_ref,
            )

        owner = document.nodes.get(decision.node_ref)
        coherent = owner is not None
        if owner is not None and decision.status == MappingStatus.VERIFIED:
            coherent = (
                owner.semantic.status == SemanticStatus.CONFIRMED
                and owner.semantic.name == decision.candidate_key
                and owner.conversion.mode == ConversionMode.COMPONENT_REFERENCE
            )
        elif owner is not None and decision.status == MappingStatus.MISSING:
            mapping_asset = (
                None
                if owner.conversion.asset_ref is None
                else document.assets.get(owner.conversion.asset_ref)
            )
            coherent = (
                owner.semantic.status == SemanticStatus.FALLBACK
                and owner.semantic.name == decision.candidate_key
                and owner.conversion.mode == ConversionMode.RASTER_FALLBACK
                and bool(owner.conversion.reasons)
                and mapping_asset is not None
                and mapping_asset.mime_type == "image/png"
                and mapping_asset.export_format == "png"
            )
        elif owner is not None:
            coherent = owner.conversion.mode == ConversionMode.UNSUPPORTED
        if not coherent:
            append(
                "uir.mapping_status_incoherent",
                "Mapping status, semantic status, and conversion are incoherent.",
                decision.node_ref,
            )
        if decision.status == MappingStatus.CONFLICT:
            append(
                "uir.mapping_conflict",
                "UIR contains an unresolved component mapping conflict.",
                decision.node_ref,
            )

    return tuple(diagnostics)


def canonical_uir_bytes(document: UIRDocument) -> bytes:
    payload = document.model_dump(mode="python", by_alias=True)
    payload = _redact_metadata_preserving_user_text(payload)
    assets = payload.get("assets", {})
    if isinstance(assets, dict):
        for asset in assets.values():
            if not isinstance(asset, dict):
                continue
            for key in ("width", "height", "nineSlice"):
                if asset.get(key) is None:
                    asset.pop(key, None)
            if asset.get("exportFormat") == "png":
                asset.pop("exportFormat", None)
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def uir_sha256(document: UIRDocument) -> str:
    return hashlib.sha256(canonical_uir_bytes(document)).hexdigest()
