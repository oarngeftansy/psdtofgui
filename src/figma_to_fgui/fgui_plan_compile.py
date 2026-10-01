"""Compile project-neutral UIR facts into deterministic FairyGUI primitive plans."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from figma_to_fgui.data_policy import private_data_violations
from figma_to_fgui.fgui_capabilities import (
    MaskCapability,
    TextRunCapability,
    analyze_capabilities,
    analyze_mask_capabilities,
    analyze_text_runs,
    base_decision_for_node,
    can_promote_native_clip_source,
    is_non_rasterizable_decision,
    is_reviewable_text_decision,
)
from figma_to_fgui.fgui_graph import graph_plan_for_node
from figma_to_fgui.fgui_plan_models import (
    CapabilityDecision,
    CapabilityStatus,
    FGUIPlanDocument,
    FGUIPlanNode,
    GraphPlan,
    MaskMode,
    MaskPlan,
    NineSlicePlan,
    PlanNodeType,
    ResourcePlan,
    TextPlan,
    TextRunPlan,
    TransformPlan,
)
from figma_to_fgui.fgui_plan_policy import (
    NATIVE_CLIP_SOURCE_RULE_ID,
    NATIVE_COMPONENT_REFERENCE_RULE_ID,
    NATIVE_IMAGE_RULE_ID,
    RASTER_SUBTREE_RULE_ID,
    RULE_TO_NODE_TYPE,
    node_type_for_capability,
)
from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.uir_models import (
    ConversionMode,
    MappingStatus,
    UIRAsset,
    UIRDocument,
    UIRNode,
)
from figma_to_fgui.uir_validate import uir_sha256, validate_uir


@dataclass(frozen=True)
class _MaskReconciliation:
    container_ref: str
    capability: MaskCapability
    target_node_ref: str | None
    emit: bool
    diagnostic_code: str | None = None
    diagnostic_message: str | None = None
    diagnostic_node_ref: str | None = None
    excluded_node_refs: tuple[str, ...] = ()
    suppressed_resource_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ReviewedDecisionIssue:
    code: str
    message: str
    node_ref: str
    evidence: tuple[str, ...]


def valid_nine_slice(asset: UIRAsset) -> bool:
    """Return whether explicitly supplied asset grid facts fit the asset dimensions."""
    grid = asset.nine_slice
    return grid is None or (
        asset.width is not None
        and asset.height is not None
        and grid.x + grid.width <= asset.width
        and grid.y + grid.height <= asset.height
    )


def _export_parameters_sha256(asset: UIRAsset) -> str:
    payload = json.dumps(
        {
            "exportFormat": asset.export_format,
            "height": asset.height,
            "mimeType": asset.mime_type,
            "width": asset.width,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_resource_id(
    asset: UIRAsset,
    export_parameters_sha256: str,
    usage: str,
) -> str:
    payload = json.dumps(
        {
            "exportParametersSha256": export_parameters_sha256,
            "sourceAssetRef": asset.id,
            "usage": usage,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"resource:{hashlib.sha256(payload).hexdigest()[:24]}"


def _stable_plan_node_id(
    source_uir_sha256: str,
    uir_node_id: str,
    profile_version: str,
    rule_version: int,
) -> str:
    payload = json.dumps(
        {
            "profileVersion": profile_version,
            "ruleVersion": rule_version,
            "sourceUirSha256": source_uir_sha256,
            "uirNodeId": uir_node_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"plan-node:{hashlib.sha256(payload).hexdigest()[:24]}"


def _stable_mask_id(
    source_uir_sha256: str,
    container_ref: str,
    profile_version: str,
    rule_version: int,
) -> str:
    payload = json.dumps(
        {
            "containerRef": container_ref,
            "profileVersion": profile_version,
            "ruleVersion": rule_version,
            "sourceUirSha256": source_uir_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"mask:{hashlib.sha256(payload).hexdigest()[:24]}"


def _text_plan(node: UIRNode, run_capability: TextRunCapability) -> TextPlan:
    source = node.text
    if source is None:
        return TextPlan(content="")
    style = source.style
    horizontal = (
        None
        if style.horizontal_align is None
        else {
            "left": "left",
            "center": "center",
            "right": "right",
            "justified": "justify",
            "justify": "justify",
        }.get(style.horizontal_align.casefold(), style.horizontal_align)
    )
    vertical = (
        None
        if style.vertical_align is None
        else {
            "top": "top",
            "center": "middle",
            "middle": "middle",
            "bottom": "bottom",
        }.get(style.vertical_align.casefold(), style.vertical_align)
    )
    style_facts = style.model_dump(mode="python", by_alias=True, exclude_none=True)
    if horizontal is not None:
        style_facts["textAlignHorizontal"] = horizontal
    if vertical is not None:
        style_facts["textAlignVertical"] = vertical
    return TextPlan(
        content=source.content,
        fontCandidates=style.font_candidates,
        fontSize=style.font_size,
        lineHeight=style.line_height,
        color=style.color,
        strokeColor=style.stroke_color,
        strokeSize=style.stroke_size,
        horizontalAlign=horizontal,
        verticalAlign=vertical,
        textAutoResize=style.auto_resize,
        runs=(
            tuple(
                TextRunPlan(
                    content=run.content,
                    fontCandidates=run.style.font_candidates,
                    fontSize=run.style.font_size,
                    lineHeight=run.style.line_height,
                    color=run.style.color,
                    strokeColor=run.style.stroke_color,
                    strokeSize=run.style.stroke_size,
                )
                for run in source.runs
            )
            if run_capability.kind == "native-rich-text"
            else ()
        ),
        styleFacts=style_facts,
    )


def _has_generatable_component_definition(
    document: UIRDocument, node: UIRNode
) -> bool:
    """Return whether UIR carries a complete component tree for this instance.

    UIR v1 component definitions contain provenance metadata but no node tree, so
    they cannot yet satisfy the self-contained Plan v2 contract.
    """
    decision_ref = node.semantic.decision_ref
    mapping = (
        None if decision_ref is None else document.mapping_decisions.get(decision_ref)
    )
    if mapping is None or mapping.status != MappingStatus.VERIFIED:
        return False
    if node.component is None or node.component.definition_ref is None:
        return False
    definition = document.component_definitions.get(node.component.definition_ref)
    if definition is None:
        return False
    return False  # UIRComponentDefinition has no root-node reference or node table.


def _diagnostic(
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    severity: Severity = Severity.ERROR,
    rule_id: str | None = None,
    rule_version: int | None = None,
    evidence: tuple[str, ...] = (),
    suggested_action: str | None = None,
    blocks_binding: bool = True,
) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=severity,
        message=message,
        node_id=node_id,
        rule_id=rule_id,
        rule_version=rule_version,
        evidence=evidence,
        suggested_action=suggested_action,
        blocks_binding=blocks_binding,
    )


def _complete_diagnostic(
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
            "suggested_action": item.suggested_action or "resolve_plan_error",
            "blocks_binding": (
                item.blocks_binding or item.severity == Severity.ERROR
            ),
        }
    )


def _reviewed_decision_issues(
    document: UIRDocument,
    decisions: Mapping[str, CapabilityDecision],
    *,
    rule_version: int,
    text_run_capabilities: Mapping[str, TextRunCapability],
) -> tuple[_ReviewedDecisionIssue, ...]:
    issues: list[_ReviewedDecisionIssue] = []
    node_ids = set(document.nodes)
    decision_keys = set(decisions)
    mask_capabilities = analyze_mask_capabilities(document)
    native_mask_source_rules: dict[str, str] = {}
    for analysis in mask_capabilities.values():
        if analysis.facts is None:
            continue
        if analysis.mode == MaskMode.NATIVE_CLIP:
            source = document.nodes[analysis.facts.mask_node_ref]
            current = base_decision_for_node(
                source,
                document,
                rule_version,
                text_run_capability=text_run_capabilities.get(source.id),
            )
            if can_promote_native_clip_source(source, analysis, current):
                native_mask_source_rules[
                    analysis.facts.mask_node_ref
                ] = NATIVE_CLIP_SOURCE_RULE_ID
        elif analysis.mode == MaskMode.NATIVE_MASK:
            native_mask_source_rules[analysis.facts.mask_node_ref] = NATIVE_IMAGE_RULE_ID

    for node_id in sorted(node_ids - decision_keys):
        issues.append(
            _ReviewedDecisionIssue(
                code="fgui.decision.missing",
                message="Reviewed capability decisions must cover every UIR node.",
                node_ref=node_id,
                evidence=("reviewed_decision.coverage=missing",),
            )
        )
    for key in sorted(decision_keys - node_ids):
        issues.append(
            _ReviewedDecisionIssue(
                code="fgui.decision.node_missing",
                message="Reviewed capability decision refers to an unknown UIR node.",
                node_ref=key,
                evidence=("reviewed_decision.node=unknown",),
            )
        )

    owners_by_id: dict[str, str] = {}
    for key in sorted(decisions):
        decision = decisions[key]
        if key != decision.node_ref:
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.key_mismatch",
                    message=(
                        "Reviewed decision mapping key must match its UIR node reference."
                    ),
                    node_ref=key,
                    evidence=("reviewed_decision.key_node_ref=mismatch",),
                )
            )
        if decision.node_ref not in document.nodes:
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.node_missing",
                    message="Reviewed capability decision refers to an unknown UIR node.",
                    node_ref=key,
                    evidence=("reviewed_decision.node_ref=unknown",),
                )
            )
        previous_owner = owners_by_id.get(decision.id)
        if previous_owner is not None and previous_owner != key:
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.id_duplicate",
                    message="Reviewed capability decision IDs must be unique.",
                    node_ref=key,
                    evidence=("reviewed_decision.id=duplicate",),
                )
            )
        else:
            owners_by_id[decision.id] = key
        if not decision.id.strip():
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.id_invalid",
                    message="Reviewed capability decision IDs must be non-blank.",
                    node_ref=key,
                    evidence=("reviewed_decision.id=blank",),
                )
            )
        if decision.rule_version != rule_version:
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.rule_version_mismatch",
                    message="Reviewed decision rule version must match the requested version.",
                    node_ref=key,
                    evidence=("reviewed_decision.rule_version=mismatch",),
                )
            )
        if not decision.evidence:
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.evidence_missing",
                    message="Reviewed capability decisions require stable public evidence.",
                    node_ref=key,
                    evidence=("reviewed_decision.evidence=missing",),
                )
            )
        elif any(not item.strip() for item in decision.evidence):
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.evidence_invalid",
                    message="Reviewed capability evidence items must be non-blank.",
                    node_ref=key,
                    evidence=("reviewed_decision.evidence=blank",),
                )
            )
        if private_data_violations(
            {
                "id": decision.id,
                "nodeRef": decision.node_ref,
                "ruleId": decision.rule_id,
                "evidence": decision.evidence,
                "reasons": decision.reasons,
            },
            f"$.decisions.{key}",
        ):
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.private_data_forbidden",
                    message="Reviewed capability metadata must contain only public facts.",
                    node_ref=key,
                    evidence=("reviewed_decision.public_metadata=false",),
                )
            )

        if decision.status == CapabilityStatus.NATIVE:
            coherent = decision.rule_id in RULE_TO_NODE_TYPE and (
                decision.rule_id != RASTER_SUBTREE_RULE_ID
            )
        elif decision.status == CapabilityStatus.RASTER_FALLBACK:
            coherent = decision.rule_id == RASTER_SUBTREE_RULE_ID
        else:
            coherent = decision.rule_id not in RULE_TO_NODE_TYPE
        if not coherent:
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.status_rule_incoherent",
                    message="Reviewed capability status and rule are incoherent.",
                    node_ref=key,
                    evidence=("reviewed_decision.status_rule=incoherent",),
                )
            )
        blocking_coherent = (
            decision.blocking or is_reviewable_text_decision(decision)
            if decision.status == CapabilityStatus.UNSUPPORTED
            else not decision.blocking
        )
        if not blocking_coherent:
            issues.append(
                _ReviewedDecisionIssue(
                    code="fgui.decision.blocking_incoherent",
                    message="Only unsupported reviewed decisions may block binding.",
                    node_ref=key,
                    evidence=("reviewed_decision.blocking=incoherent",),
                )
            )
        source_node = document.nodes.get(key)
        if source_node is not None:
            safety_decision = base_decision_for_node(
                source_node,
                document,
                rule_version,
                text_run_capability=text_run_capabilities.get(source_node.id),
            )
            if (
                is_reviewable_text_decision(decision)
                and (
                    decision.node_ref,
                    decision.status,
                    decision.rule_id,
                    decision.rule_version,
                    decision.evidence,
                    decision.reasons,
                    decision.blocking,
                )
                != (
                    safety_decision.node_ref,
                    safety_decision.status,
                    safety_decision.rule_id,
                    safety_decision.rule_version,
                    safety_decision.evidence,
                    safety_decision.reasons,
                    safety_decision.blocking,
                )
            ):
                issues.append(
                    _ReviewedDecisionIssue(
                        code="fgui.decision.reviewable_text_evidence_incoherent",
                        message=(
                            "Reviewed editable-text evidence must match the canonical "
                            "run capability decision."
                        ),
                        node_ref=key,
                        evidence=("reviewed_decision.rich_text_runs=noncanonical",),
                    )
                )
            mask_role_override = (
                decision.status == CapabilityStatus.NATIVE
                and native_mask_source_rules.get(key) == decision.rule_id
            )
            if is_non_rasterizable_decision(safety_decision) and not (
                decision.status == CapabilityStatus.UNSUPPORTED
                and decision.rule_id == safety_decision.rule_id
                and (decision.blocking or is_reviewable_text_decision(decision))
            ) and not mask_role_override:
                issues.append(
                    _ReviewedDecisionIssue(
                        code="fgui.decision.safety_override_forbidden",
                        message=(
                            "Reviewed decisions cannot override non-rasterizable safety rules."
                        ),
                        node_ref=key,
                        evidence=("reviewed_decision.safety_override=true",),
                    )
                )
            expected_native_rule = native_mask_source_rules.get(key)
            if expected_native_rule is None and safety_decision.status == CapabilityStatus.NATIVE:
                expected_native_rule = safety_decision.rule_id
            native_role_coherent = (
                decision.status != CapabilityStatus.NATIVE
                or (
                    expected_native_rule is not None
                    and decision.rule_id == expected_native_rule
                )
            )
            component_role_coherent = (
                decision.rule_id != NATIVE_COMPONENT_REFERENCE_RULE_ID
                or _has_generatable_component_definition(document, source_node)
            )
            if not native_role_coherent or not component_role_coherent:
                issues.append(
                    _ReviewedDecisionIssue(
                        code="fgui.decision.node_role_incoherent",
                        message=(
                            "Reviewed native decision conflicts with the source payload role."
                        ),
                        node_ref=key,
                        evidence=("reviewed_decision.node_role=incoherent",),
                    )
                )
            if (
                decision.rule_id == NATIVE_IMAGE_RULE_ID
                and source_node.conversion.asset_ref not in document.assets
            ):
                issues.append(
                    _ReviewedDecisionIssue(
                        code="fgui.decision.resource_requirement_incoherent",
                        message="Reviewed native-image decisions require a usable asset.",
                        node_ref=key,
                        evidence=("reviewed_decision.image_asset=missing",),
                    )
                )

    for analysis in mask_capabilities.values():
        if analysis.mode != MaskMode.RASTER_SUBTREE or analysis.facts is None:
            continue
        if not any(
            node_id in decisions
            and is_non_rasterizable_decision(decisions[node_id])
            for node_id in analysis.consumed_node_refs
        ):
            continue
        safe_root_ref = analysis.facts.safe_raster_root_ref or analysis.container_ref
        issues.append(
            _ReviewedDecisionIssue(
                code="fgui.decision.mask_safety_incoherent",
                message="Reviewed mask fallback cannot consume blocking behavior.",
                node_ref=safe_root_ref,
                evidence=("reviewed_decision.mask_consumes_behavior=true",),
            )
        )
    return tuple(issues)


def _quarantined_decision_id(node_ref: str, rule_version: int) -> str:
    payload = json.dumps(
        {"nodeRef": node_ref, "ruleVersion": rule_version},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"decision:review-invalid:{hashlib.sha256(payload).hexdigest()[:16]}"


def _quarantined_source_plan(
    document: UIRDocument,
    *,
    source_hash: str,
    profile_version: str,
    rule_version: int,
    source_diagnostics: tuple[Diagnostic, ...],
    header_invalid: bool,
) -> FGUIPlanDocument:
    diagnostics = list(source_diagnostics)
    if header_invalid:
        diagnostics.append(
            _diagnostic(
                "fgui.plan.private_data_leak",
                "Generation-plan headers must contain public stable metadata.",
                rule_id="fgui.plan.private_data_leak",
                rule_version=rule_version,
                evidence=("plan.header.private_data=true",),
                suggested_action="use_public_profile_and_document_identifiers",
                blocks_binding=True,
            )
        )
    return FGUIPlanDocument(
        documentId=(
            "uir:quarantined"
            if not isinstance(document.document_id, str)
            or not document.document_id.strip()
            or private_data_violations(document.document_id)
            else document.document_id
        ),
        sourceUirSha256=source_hash,
        profileVersion=(
            "fgui-profile:quarantined"
            if not isinstance(profile_version, str)
            or not profile_version.strip()
            or private_data_violations(profile_version)
            else profile_version
        ),
        ruleVersion=rule_version,
        bindable=False,
        roots=(),
        nodes={},
        resources={},
        masks={},
        decisions={},
        diagnostics=tuple(
            _complete_diagnostic(item, rule_version=rule_version)
            for item in diagnostics
        ),
    )


def _quarantined_review_plan(
    document: UIRDocument,
    *,
    source_hash: str,
    profile_version: str,
    rule_version: int,
    issues: tuple[_ReviewedDecisionIssue, ...],
    source_diagnostics: tuple[Diagnostic, ...],
) -> FGUIPlanDocument:
    issues_by_node: dict[str, list[_ReviewedDecisionIssue]] = {}
    for issue in issues:
        issues_by_node.setdefault(issue.node_ref, []).append(issue)

    diagnostics = [*document.diagnostics, *source_diagnostics]
    decisions: dict[str, CapabilityDecision] = {}
    for node_id in sorted(document.nodes):
        node_issues = issues_by_node.get(node_id, [])
        primary = (
            node_issues[0]
            if node_issues
            else _ReviewedDecisionIssue(
                code="fgui.decision.review_invalid",
                message="Reviewed decision set is invalid and was quarantined.",
                node_ref=node_id,
                evidence=("reviewed_decision.set=invalid",),
            )
        )
        if not node_issues:
            issues_by_node[node_id] = [primary]
        decisions[node_id] = CapabilityDecision(
            id=_quarantined_decision_id(node_id, rule_version),
            nodeRef=node_id,
            status=CapabilityStatus.UNSUPPORTED,
            ruleId=primary.code,
            ruleVersion=rule_version,
            evidence=primary.evidence,
            reasons=("reviewed_decision_quarantined",),
            blocking=True,
        )

    emitted_issue_keys: set[tuple[str, str]] = set()
    for node_id in sorted(issues_by_node):
        for issue in issues_by_node[node_id]:
            key = (issue.code, issue.node_ref)
            if key in emitted_issue_keys:
                continue
            emitted_issue_keys.add(key)
            diagnostics.append(
                _diagnostic(
                    issue.code,
                    issue.message,
                    node_id=issue.node_ref,
                    rule_id=issue.code,
                    rule_version=rule_version,
                    evidence=issue.evidence,
                    suggested_action="review_capability_decisions",
                    blocks_binding=True,
                )
            )

    complete_diagnostics = tuple(
        _complete_diagnostic(item, rule_version=rule_version)
        for item in diagnostics
    )
    return FGUIPlanDocument(
        documentId=document.document_id,
        sourceUirSha256=source_hash,
        profileVersion=profile_version,
        ruleVersion=rule_version,
        bindable=False,
        roots=(),
        nodes={},
        resources={},
        masks={},
        decisions=decisions,
        diagnostics=complete_diagnostics,
    )


def _node_type_for_decision(
    document: UIRDocument,
    node: UIRNode,
    decision: CapabilityDecision,
    run_capability: TextRunCapability | None = None,
) -> PlanNodeType | None:
    if (
        run_capability is not None
        and run_capability.kind == "editable-risk"
        and is_reviewable_text_decision(decision)
    ):
        return PlanNodeType.TEXT
    node_type = node_type_for_capability(decision.status, decision.rule_id)
    if (
        node_type == PlanNodeType.COMPONENT_REFERENCE
        and not _has_generatable_component_definition(document, node)
    ):
        return None
    if node_type in {PlanNodeType.IMAGE, PlanNodeType.RASTER_SUBTREE}:
        asset = (
            None
            if node.conversion.asset_ref is None
            else document.assets.get(node.conversion.asset_ref)
        )
        if asset is not None and asset.sha256 is None:
            return None
    if (
        node_type == PlanNodeType.RASTER_SUBTREE
        and node.conversion.asset_ref not in document.assets
    ):
        return None
    if node_type == PlanNodeType.RASTER_SUBTREE:
        asset = document.assets[node.conversion.asset_ref]  # type: ignore[index]
        if asset.mime_type != "image/png" or asset.export_format != "png":
            return None
    return node_type


def _expected_native_mask_rule(
    document: UIRDocument,
    node: UIRNode,
    *,
    mode: MaskMode,
    is_mask_source: bool,
) -> str | None:
    base = base_decision_for_node(node, document)
    if is_mask_source and mode == MaskMode.NATIVE_CLIP:
        return (
            None
            if node.conversion.mode == ConversionMode.UNSUPPORTED
            else NATIVE_CLIP_SOURCE_RULE_ID
        )
    if is_mask_source and mode == MaskMode.NATIVE_MASK:
        return (
            NATIVE_IMAGE_RULE_ID
            if base.status == CapabilityStatus.NATIVE
            and base.rule_id == NATIVE_IMAGE_RULE_ID
            else None
        )
    return base.rule_id if base.status == CapabilityStatus.NATIVE else None


def _decision_diagnostic(
    document: UIRDocument,
    node: UIRNode,
    decision: CapabilityDecision,
) -> Diagnostic:
    def unsupported_diagnostic(code: str, message: str) -> Diagnostic:
        return _diagnostic(
            code,
            message,
            node_id=node.id,
            rule_id=decision.rule_id,
            rule_version=decision.rule_version,
            evidence=decision.evidence,
            suggested_action="resolve_unsupported_feature",
            blocks_binding=True,
        )

    if decision.status == CapabilityStatus.UNSUPPORTED:
        if decision.rule_id in RULE_TO_NODE_TYPE:
            return unsupported_diagnostic(
                "fgui.decision.status_rule_incoherent",
                "Unsupported capability decisions cannot select a plan-node rule.",
            )
        return unsupported_diagnostic(
            decision.rule_id,
            "UIR node has an unsupported FairyGUI capability decision.",
        )
    if decision.status == CapabilityStatus.RASTER_FALLBACK:
        if decision.rule_id != RASTER_SUBTREE_RULE_ID:
            return unsupported_diagnostic(
                "fgui.decision.status_rule_incoherent",
                "Raster fallback decisions must select the raster-subtree rule.",
            )
        asset = (
            None
            if node.conversion.asset_ref is None
            else document.assets.get(node.conversion.asset_ref)
        )
        if asset is not None and (
            asset.mime_type != "image/png" or asset.export_format != "png"
        ):
            return unsupported_diagnostic(
                "fgui.resource.fallback_format_incoherent",
                "Raster fallback resources must use a PNG export recipe.",
            )
        return unsupported_diagnostic(
            "fgui.decision.raster_resource_missing",
            "Raster fallback decisions require an existing source asset.",
        )
    return unsupported_diagnostic(
        "fgui.decision.status_rule_incoherent",
        "Native capability decisions must select a native plan-node rule.",
    )


def compile_fgui_plan(
    document: UIRDocument,
    *,
    profile_version: str = "fgui-6.1.4-v1",
    rule_version: int = 1,
    decisions: Mapping[str, CapabilityDecision] | None = None,
) -> FGUIPlanDocument:
    """Compile native UIR primitives without assigning target-project binding IDs.

    When supplied, ``decisions`` are reviewed capability decisions and are used
    directly instead of deriving a new decision set.
    """
    source_hash = uir_sha256(document)
    source_diagnostics = validate_uir(document)
    header_invalid = (
        not isinstance(document.document_id, str)
        or not document.document_id.strip()
        or not isinstance(profile_version, str)
        or not profile_version.strip()
        or bool(
            private_data_violations(
                {
                    "documentId": document.document_id,
                    "profileVersion": profile_version,
                }
            )
        )
    )
    quarantined_source_codes = {
        "uir.asset_format_incoherent",
        "uir.conversion_reason_invalid",
        "uir.identity_invalid",
        "uir.private_data_forbidden",
        "uir.tree_empty",
        "uir.tree_depth_exceeded",
    }
    if header_invalid or any(
        item.code in quarantined_source_codes for item in source_diagnostics
    ):
        return _quarantined_source_plan(
            document,
            source_hash=source_hash,
            profile_version=profile_version,
            rule_version=rule_version,
            source_diagnostics=source_diagnostics,
            header_invalid=header_invalid,
        )
    text_run_capabilities = {
        node_id: analyze_text_runs(node)
        for node_id, node in document.nodes.items()
        if node.source.type == "TEXT"
    }
    if decisions is not None:
        reviewed_issues = _reviewed_decision_issues(
            document,
            decisions,
            rule_version=rule_version,
            text_run_capabilities=text_run_capabilities,
        )
        if reviewed_issues:
            return _quarantined_review_plan(
                document,
                source_hash=source_hash,
                profile_version=profile_version,
                rule_version=rule_version,
                issues=reviewed_issues,
                source_diagnostics=source_diagnostics,
            )
    export_hashes_by_asset = {
        asset_id: _export_parameters_sha256(asset)
        for asset_id, asset in document.assets.items()
    }
    resource_ids_by_usage = {
        (asset_id, usage): _stable_resource_id(
            asset,
            export_hashes_by_asset[asset_id],
            usage,
        )
        for asset_id, asset in document.assets.items()
        for usage in ("native", "rasterFallback")
    }
    mask_capabilities = analyze_mask_capabilities(document)
    native_clip_source_refs = {
        analysis.facts.mask_node_ref
        for analysis in mask_capabilities.values()
        if analysis.mode == MaskMode.NATIVE_CLIP
        and analysis.facts is not None
        and analysis.diagnostic_code is None
    }
    resolved_decisions = (
        analyze_capabilities(
            document,
            rule_version=rule_version,
            mask_capabilities=mask_capabilities,
            text_run_capabilities=text_run_capabilities,
        )
        if decisions is None
        else decisions
    )
    node_ids = {
        node_id: _stable_plan_node_id(
            source_hash, node_id, profile_version, rule_version
        )
        for node_id in document.nodes
    }
    diagnostics = [*document.diagnostics, *source_diagnostics]
    resource_consumers: dict[tuple[str, str], set[str]] = {
        (asset_id, usage): set()
        for asset_id in document.assets
        for usage in ("native", "rasterFallback")
    }
    resource_reasons: dict[tuple[str, str], str] = {}
    nodes: dict[str, FGUIPlanNode] = {}
    active_node_ids: set[str] = set()
    compiled_node_ids: set[str] = set()
    consumed_uir_nodes: set[str] = set()
    blocked_raster_roots: set[str] = set()
    blocked_component_roots: set[str] = set()
    mask_refs_by_node: dict[str, str] = {}
    masks: dict[str, MaskPlan] = {}

    def add_diagnostic_once(
        code: str,
        message: str,
        node_id: str,
        *,
        decision: CapabilityDecision | None = None,
        severity: Severity = Severity.ERROR,
        suggested_action: str = "resolve_plan_error",
        blocks_binding: bool = True,
    ) -> None:
        if not any(item.code == code and item.node_id == node_id for item in diagnostics):
            decision_metadata_matches = (
                decision is not None
                and (
                    severity != Severity.ERROR
                    or not blocks_binding
                    or decision.rule_id == code
                )
            )
            diagnostics.append(
                _diagnostic(
                    code,
                    message,
                    node_id=node_id,
                    severity=severity,
                    rule_id=(
                        decision.rule_id
                        if decision_metadata_matches and decision is not None
                        else code
                    ),
                    rule_version=(
                        rule_version if decision is None else decision.rule_version
                    ),
                    evidence=(
                        decision.evidence
                        if decision_metadata_matches and decision is not None
                        else (f"node.id={node_id}",)
                    ),
                    suggested_action=suggested_action,
                    blocks_binding=blocks_binding,
                )
            )

    reconciliations: dict[str, _MaskReconciliation] = {}
    for container_id, analysis in mask_capabilities.items():
        facts = analysis.facts
        mode = analysis.mode
        target_node_ref: str | None = container_id
        if mode == MaskMode.RASTER_SUBTREE and facts is not None:
            target_node_ref = facts.safe_raster_root_ref
        reconciliations[container_id] = _MaskReconciliation(
            container_ref=container_id,
            capability=analysis,
            target_node_ref=target_node_ref,
            emit=facts is not None and mode is not None and analysis.diagnostic_code is None,
            diagnostic_code=analysis.diagnostic_code,
            diagnostic_message=(
                None
                if analysis.diagnostic_code is None
                else "UIR mask facts cannot be compiled safely."
            ),
            diagnostic_node_ref=(
                None if analysis.diagnostic_code is None else container_id
            ),
        )

    def required_native_refs(state: _MaskReconciliation) -> tuple[str, ...]:
        facts = state.capability.facts
        if facts is None:
            return ()
        return (
            state.container_ref,
            facts.mask_node_ref,
            *facts.content_node_refs,
        )

    def resource_refs_for(state: _MaskReconciliation) -> tuple[str, ...]:
        refs: set[str] = set()
        if state.capability.resource_ref is not None:
            refs.add(state.capability.resource_ref)
        for node_id in required_native_refs(state):
            asset_ref = document.nodes[node_id].conversion.asset_ref
            if asset_ref is not None:
                refs.add(asset_ref)
        return tuple(sorted(refs))

    for container_id, state in tuple(reconciliations.items()):
        if not state.emit:
            continue
        analysis = state.capability
        if analysis.mode == MaskMode.RASTER_SUBTREE:
            affected_node_refs = analysis.consumed_node_refs
        else:
            affected_node_refs = required_native_refs(state)
        if any(
            (decision := resolved_decisions.get(node_id)) is not None
            and is_non_rasterizable_decision(decision)
            for node_id in affected_node_refs
        ):
            reconciliations[container_id] = replace(
                state,
                emit=False,
                diagnostic_code=None,
                diagnostic_message=None,
                diagnostic_node_ref=None,
                excluded_node_refs=(
                    (analysis.facts.mask_node_ref,)
                    if analysis.facts is not None
                    and analysis.mode in {MaskMode.NATIVE_CLIP, MaskMode.NATIVE_MASK}
                    else ()
                ),
                suppressed_resource_refs=resource_refs_for(state),
            )

    if decisions is not None:
        for container_id, state in tuple(reconciliations.items()):
            analysis = state.capability
            facts = analysis.facts
            if not state.emit or facts is None or analysis.mode is None:
                continue
            if analysis.mode == MaskMode.RASTER_SUBTREE:
                safe_root_id = facts.safe_raster_root_ref
                reviewed = (
                    None if safe_root_id is None else resolved_decisions.get(safe_root_id)
                )
                if safe_root_id is None or (
                    reviewed is None
                    or reviewed.status != CapabilityStatus.RASTER_FALLBACK
                    or reviewed.rule_id != RASTER_SUBTREE_RULE_ID
                ):
                    diagnostic_node = safe_root_id or container_id
                    reconciliations[container_id] = replace(
                        state,
                        emit=False,
                        diagnostic_code="fgui.decision.mask_requirement_incoherent",
                        diagnostic_message=(
                            "Reviewed capability decision conflicts with required mask fallback."
                        ),
                        diagnostic_node_ref=diagnostic_node,
                        excluded_node_refs=(diagnostic_node,),
                        suppressed_resource_refs=resource_refs_for(state),
                    )
                continue

    targets: dict[str, list[str]] = {}
    for container_id, state in reconciliations.items():
        if state.emit and state.target_node_ref is not None:
            targets.setdefault(state.target_node_ref, []).append(container_id)
    for target_node_id, container_ids in targets.items():
        if len(container_ids) < 2:
            continue
        for container_id in container_ids:
            state = reconciliations[container_id]
            reconciliations[container_id] = replace(
                state,
                emit=False,
                diagnostic_code="fgui.mask.target_collision",
                diagnostic_message="Multiple masks target the same plan node.",
                diagnostic_node_ref=target_node_id,
                excluded_node_refs=(target_node_id,),
                suppressed_resource_refs=resource_refs_for(state),
            )

    for state in reconciliations.values():
        if not state.emit:
            continue
        analysis = state.capability
        facts = analysis.facts
        if facts is None or analysis.mode is None:
            continue
        if analysis.mode == MaskMode.RASTER_SUBTREE:
            safe_root_id = facts.safe_raster_root_ref
            if safe_root_id is None:
                continue
            consumed_uir_nodes.update(analysis.consumed_node_refs)
            consumed_uir_nodes.discard(safe_root_id)
            if analysis.resource_ref is not None:
                resource_reasons[
                    (analysis.resource_ref, "rasterFallback")
                ] = "mask_raster_fallback"

    def descendants_of(node_id: str) -> tuple[str, ...]:
        descendants: list[str] = []
        pending = list(reversed(document.nodes[node_id].children))
        seen_descendants: set[str] = set()
        while pending:
            descendant_id = pending.pop()
            if descendant_id in seen_descendants or descendant_id not in document.nodes:
                continue
            seen_descendants.add(descendant_id)
            descendants.append(descendant_id)
            pending.extend(reversed(document.nodes[descendant_id].children))
        return tuple(descendants)

    for node_id in sorted(resolved_decisions):
        decision = resolved_decisions[node_id]
        if (
            decision.status != CapabilityStatus.RASTER_FALLBACK
            or node_id not in document.nodes
            or document.nodes[node_id].conversion.mode
            not in {
                ConversionMode.RASTER_FALLBACK,
                ConversionMode.COMPONENT_REFERENCE,
            }
            or node_id in consumed_uir_nodes
        ):
            continue
        descendants = descendants_of(node_id)
        blocking_descendant = next(
            (
                descendant_id
                for descendant_id in descendants
                if (
                    descendant_decision := resolved_decisions.get(descendant_id)
                ) is not None
                and is_non_rasterizable_decision(descendant_decision)
            ),
            None,
        )
        if blocking_descendant is not None:
            blocked_raster_roots.add(node_id)
            add_diagnostic_once(
                "fgui.raster.descendant_non_rasterizable",
                "Raster fallback cannot consume descendant behavior semantics.",
                node_id,
                suggested_action="remove_or_rebuild_descendant_behavior",
            )
            continue
        consumed_uir_nodes.update(descendants)

    for container_id in sorted(reconciliations):
        state = reconciliations[container_id]
        analysis = state.capability
        if not state.emit or analysis.mode != MaskMode.RASTER_SUBTREE:
            continue
        consumed_scope = analysis.consumed_node_refs
        if not (
            state.target_node_ref in consumed_uir_nodes
            or (
                bool(consumed_scope)
                and all(node_id in consumed_uir_nodes for node_id in consumed_scope)
            )
        ):
            continue
        reconciliations[container_id] = replace(
            state,
            emit=False,
            diagnostic_code=None,
            diagnostic_message=None,
            diagnostic_node_ref=None,
            suppressed_resource_refs=resource_refs_for(state),
        )

    for node_id in sorted(resolved_decisions):
        decision = resolved_decisions[node_id]
        if (
            decision.rule_id != NATIVE_COMPONENT_REFERENCE_RULE_ID
            or node_id not in document.nodes
            or document.nodes[node_id].conversion.mode
            != ConversionMode.COMPONENT_REFERENCE
            or node_id in consumed_uir_nodes
            or not _has_generatable_component_definition(
                document, document.nodes[node_id]
            )
        ):
            continue
        descendants = descendants_of(node_id)
        blocking_descendant = next(
            (
                descendant_id
                for descendant_id in descendants
                if (
                    descendant_decision := resolved_decisions.get(descendant_id)
                ) is not None
                and is_non_rasterizable_decision(descendant_decision)
            ),
            None,
        )
        if blocking_descendant is not None:
            blocked_component_roots.add(node_id)
            add_diagnostic_once(
                "fgui.component.descendant_non_rasterizable",
                "Component references cannot consume descendant behavior semantics.",
                node_id,
                suggested_action="represent_descendant_behavior_as_component_overrides",
            )
            continue
        consumed_uir_nodes.update(descendants)

    for container_id, state in tuple(reconciliations.items()):
        analysis = state.capability
        if not state.emit or analysis.mode == MaskMode.RASTER_SUBTREE:
            continue
        native_refs = required_native_refs(state)
        if native_refs and all(
            node_id in consumed_uir_nodes for node_id in native_refs
        ):
            reconciliations[container_id] = replace(
                state,
                emit=False,
                diagnostic_code=None,
                diagnostic_message=None,
                diagnostic_node_ref=None,
                suppressed_resource_refs=resource_refs_for(state),
            )
            continue
        facts = analysis.facts
        bad_node_id: str | None = None
        if facts is not None and analysis.mode is not None:
            for node_id in native_refs:
                decision = resolved_decisions.get(node_id)
                expected_rule = _expected_native_mask_rule(
                    document,
                    document.nodes[node_id],
                    mode=analysis.mode,
                    is_mask_source=node_id == facts.mask_node_ref,
                )
                if node_id in native_clip_source_refs:
                    expected_rule = NATIVE_CLIP_SOURCE_RULE_ID
                rasterized_content = (
                    node_id in facts.content_node_refs
                    and decision is not None
                    and decision.status == CapabilityStatus.RASTER_FALLBACK
                    and _node_type_for_decision(
                        document, document.nodes[node_id], decision
                    )
                    is not None
                )
                reviewable_text_content = (
                    node_id in facts.content_node_refs
                    and decision is not None
                    and is_reviewable_text_decision(decision)
                    and _node_type_for_decision(
                        document,
                        document.nodes[node_id],
                        decision,
                        text_run_capabilities.get(node_id),
                    )
                    == PlanNodeType.TEXT
                )
                if (
                    node_id in consumed_uir_nodes
                    or decision is None
                    or (
                        not rasterized_content
                        and not reviewable_text_content
                        and (
                            expected_rule is None
                            or decision.status != CapabilityStatus.NATIVE
                            or decision.rule_id != expected_rule
                            or _node_type_for_decision(
                                document, document.nodes[node_id], decision
                            )
                            is None
                        )
                    )
                ):
                    bad_node_id = node_id
                    break
        if bad_node_id is not None:
            reconciliations[container_id] = replace(
                state,
                emit=False,
                diagnostic_code="fgui.decision.mask_requirement_incoherent",
                diagnostic_message=(
                    "A required native mask node has an incoherent capability role."
                ),
                diagnostic_node_ref=bad_node_id,
                excluded_node_refs=tuple(
                    dict.fromkeys(
                        (
                            bad_node_id,
                            *(
                                ()
                                if facts is None
                                else (facts.mask_node_ref,)
                            ),
                        )
                    )
                ),
                suppressed_resource_refs=resource_refs_for(state),
            )

    incompatible_mask_nodes = {
        node_id
        for state in reconciliations.values()
        for node_id in state.excluded_node_refs
    }
    for container_id in sorted(reconciliations):
        state = reconciliations[container_id]
        if not state.emit:
            absorbed_invalid_mask = (
                container_id in consumed_uir_nodes
                and state.capability.diagnostic_code is not None
            )
            if (
                not absorbed_invalid_mask
                and state.diagnostic_code is not None
                and state.diagnostic_message is not None
                and state.diagnostic_node_ref is not None
            ):
                add_diagnostic_once(
                    state.diagnostic_code,
                    state.diagnostic_message,
                    state.diagnostic_node_ref,
                    decision=resolved_decisions.get(state.diagnostic_node_ref),
                )
            continue
        analysis = state.capability
        facts = analysis.facts
        mode = analysis.mode
        emission_target_id = state.target_node_ref
        if facts is None or mode is None or emission_target_id is None:
            continue
        mask_id = _stable_mask_id(source_hash, container_id, profile_version, rule_version)
        masks[mask_id] = MaskPlan(
            id=mask_id,
            mode=mode,
            kind=facts.kind,
            maskNodeRef=facts.mask_node_ref,
            contentNodeRefs=facts.content_node_refs,
            cornerRadii=facts.corner_radii,
            resourceRef=(
                None
                if analysis.resource_ref is None
                else resource_ids_by_usage[
                    (
                        analysis.resource_ref,
                        (
                            "rasterFallback"
                            if mode == MaskMode.RASTER_SUBTREE
                            else "native"
                        ),
                    )
                ]
            ),
        )
        mask_refs_by_node[emission_target_id] = mask_id

    def is_excluded(uir_node_id: str) -> bool:
        return (
            uir_node_id in consumed_uir_nodes
            or uir_node_id in incompatible_mask_nodes
            or uir_node_id in blocked_raster_roots
            or uir_node_id in blocked_component_roots
        )

    def is_compilable(uir_node_id: str) -> bool:
        if is_excluded(uir_node_id):
            return False
        node = document.nodes.get(uir_node_id)
        decision = resolved_decisions.get(uir_node_id)
        return (
            node is not None
            and decision is not None
            and _node_type_for_decision(
                document,
                node,
                decision,
                text_run_capabilities.get(uir_node_id),
            )
            is not None
        )

    def compile_node(uir_node_id: str, parent_plan_id: str | None) -> str | None:
        if is_excluded(uir_node_id):
            return None
        if uir_node_id in active_node_ids:
            diagnostics.append(
                _diagnostic(
                    "fgui.node.cycle",
                    "UIR child references form a cycle and cannot be compiled.",
                    node_id=uir_node_id,
                )
            )
            return None
        node = document.nodes.get(uir_node_id)
        decision = resolved_decisions.get(uir_node_id)
        if node is None or decision is None:
            diagnostics.append(
                _diagnostic(
                    "fgui.node.missing",
                    "UIR node could not be compiled because it is missing.",
                    node_id=uir_node_id,
                )
            )
            return None
        node_type = _node_type_for_decision(
            document,
            node,
            decision,
            text_run_capabilities.get(uir_node_id),
        )
        if node_type is None:
            source_asset = (
                None
                if node.conversion.asset_ref is None
                else document.assets.get(node.conversion.asset_ref)
            )
            item = (
                _diagnostic(
                    "fgui.component.definition_missing",
                    "Component references require a complete generatable UIR definition.",
                    node_id=node.id,
                    rule_id="fgui.component.definition_missing",
                    rule_version=decision.rule_version,
                    evidence=("component.definition=missing",),
                    suggested_action="recompile_with_component_definition_or_raster_fallback",
                    blocks_binding=True,
                )
                if decision.rule_id == NATIVE_COMPONENT_REFERENCE_RULE_ID
                else _diagnostic(
                    "fgui.plan.resource_content_hash_missing",
                    "Bindable resources require a source content SHA-256.",
                    node_id=node.id,
                    rule_id="fgui.plan.resource_content_hash_missing",
                    rule_version=decision.rule_version,
                    evidence=("resource.content_sha256=missing",),
                    suggested_action="reexport_resource_with_hash",
                    blocks_binding=True,
                )
                if source_asset is not None and source_asset.sha256 is None
                else _decision_diagnostic(document, node, decision)
            )
            if not any(
                existing.code == item.code and existing.node_id == node.id
                for existing in diagnostics
            ):
                diagnostics.append(item)
            return None
        plan_node_id = node_ids[node.id]
        if plan_node_id in compiled_node_ids:
            return plan_node_id
        active_node_ids.add(node.id)
        child_ids = tuple(
            node_ids[child_id]
            for child_id in node.children
            if child_id not in active_node_ids and is_compilable(child_id)
        )
        resource_asset_ref = (
            None
            if decision.rule_id == NATIVE_CLIP_SOURCE_RULE_ID
            or is_reviewable_text_decision(decision)
            else node.conversion.asset_ref
        )
        resource_usage = (
            "rasterFallback"
            if decision.status == CapabilityStatus.RASTER_FALLBACK
            else "native"
        )
        resource_key = (
            None
            if resource_asset_ref is None
            else (resource_asset_ref, resource_usage)
        )
        if resource_key is not None and resource_key in resource_consumers:
            resource_consumers[resource_key].add(plan_node_id)
            if decision.status == CapabilityStatus.RASTER_FALLBACK:
                resource_reasons.setdefault(
                    resource_key,
                    decision.reasons[0] if decision.reasons else "raster_fallback",
                )
        visible_fact = node.layout.get("visible")
        nodes[plan_node_id] = FGUIPlanNode(
            id=plan_node_id,
            uirNodeRef=node.id,
            parentId=parent_plan_id,
            children=child_ids,
            zIndex=node.z_index,
            type=node_type,
            transform=TransformPlan(
                bounds=node.geometry.resolved_bounds,
                rotation=node.geometry.rotation,
                opacity=node.geometry.opacity,
                visible=visible_fact if isinstance(visible_fact, bool) else True,
            ),
            text=(
                _text_plan(node, text_run_capabilities[node.id])
                if node_type in {PlanNodeType.TEXT, PlanNodeType.RICH_TEXT}
                else None
            ),
            graph=(
                graph_plan_for_node(node)
                or (
                    GraphPlan(shape="rect")
                    if decision.rule_id == NATIVE_CLIP_SOURCE_RULE_ID
                    else None
                )
                if node_type == PlanNodeType.GRAPH
                else None
            ),
            backgroundGraph=(
                graph_plan_for_node(node)
                if node_type == PlanNodeType.CONTAINER
                and node.source.type in {"FRAME", "COMPONENT"}
                else None
            ),
            resourceRef=(
                None
                if resource_key is None
                else resource_ids_by_usage[resource_key]
            ),
            component=None,
            maskRef=mask_refs_by_node.get(node.id),
            decisionRef=decision.id,
        )
        if decision.status == CapabilityStatus.RASTER_FALLBACK:
            add_diagnostic_once(
                (
                    "fgui.mask.raster_fallback"
                    if node.id in mask_refs_by_node
                    else "fgui.visual.raster_fallback"
                ),
                "UIR visual content uses an explicit raster fallback.",
                node.id,
                decision=decision,
                severity=Severity.WARNING,
                suggested_action="review_raster_fallback",
                blocks_binding=False,
            )
        compiled_node_ids.add(plan_node_id)
        if node_type != PlanNodeType.RASTER_SUBTREE:
            for child_id in node.children:
                compile_node(child_id, plan_node_id)
        active_node_ids.remove(node.id)
        return plan_node_id

    roots = tuple(
        compiled_id
        for root_id in document.roots
        if (compiled_id := compile_node(root_id, None)) is not None
    )
    emitted_uir_node_refs = {node.uir_node_ref for node in nodes.values()}
    for container_id in sorted(reconciliations):
        state = reconciliations[container_id]
        emission_target_id = state.target_node_ref
        if (
            not state.emit
            or emission_target_id is None
            or emission_target_id in emitted_uir_node_refs
        ):
            continue
        reconciliations[container_id] = replace(
            state,
            emit=False,
            diagnostic_code=None,
            diagnostic_message=None,
            diagnostic_node_ref=None,
            suppressed_resource_refs=resource_refs_for(state),
        )
        orphan_mask_id = mask_refs_by_node.get(emission_target_id)
        if orphan_mask_id is not None:
            mask_refs_by_node.pop(emission_target_id)
            masks.pop(orphan_mask_id, None)

    resources: dict[str, ResourcePlan] = {}
    for asset_id in sorted(document.assets):
        usages = tuple(
            usage
            for usage in ("native", "rasterFallback")
            if resource_consumers[(asset_id, usage)]
        )
        if not usages:
            continue
        asset = document.assets[asset_id]
        if asset.sha256 is None:
            diagnostics.append(
                _diagnostic(
                    "fgui.plan.resource_content_hash_missing",
                    "Bindable resources require a source content SHA-256.",
                    node_id=asset.source_node_id,
                    rule_id="fgui.plan.resource_content_hash_missing",
                    rule_version=rule_version,
                    evidence=("resource.content_sha256=missing",),
                    suggested_action="reexport_resource_with_hash",
                    blocks_binding=True,
                )
            )
        grid = asset.nine_slice
        if not valid_nine_slice(asset):
            diagnostics.append(
                _diagnostic(
                    "fgui.resource.nine_slice_invalid",
                    "Nine-slice grid must fit within explicit asset dimensions.",
                    node_id=asset.source_node_id,
                )
            )
            grid = None
        for usage in usages:
            resource_key = (asset_id, usage)
            resource_id = resource_ids_by_usage[resource_key]
            resources[resource_id] = ResourcePlan(
                id=resource_id,
                sourceAssetRef=asset.id,
                logicalAssetId=asset.logical_id,
                contentSha256=asset.sha256,
                exportParametersSha256=export_hashes_by_asset[asset_id],
                mimeType=asset.mime_type,
                exportFormat=asset.export_format,
                width=asset.width,
                height=asset.height,
                nineSlice=(
                    None
                    if grid is None
                    else NineSlicePlan(
                        x=grid.x,
                        y=grid.y,
                        width=grid.width,
                        height=grid.height,
                    )
                ),
                consumers=tuple(sorted(resource_consumers[resource_key])),
                reason=resource_reasons.get(resource_key),
            )
    emitted_decision_refs = {
        node.decision_ref for node in nodes.values() if node.decision_ref is not None
    }
    errors_by_node: dict[str, Diagnostic] = {}
    for item in diagnostics:
        if item.severity == Severity.ERROR and item.node_id is not None:
            errors_by_node.setdefault(item.node_id, item)
    plan_decisions: dict[str, CapabilityDecision] = {}
    for node_id in sorted(resolved_decisions):
        if node_id in consumed_uir_nodes:
            continue
        decision = resolved_decisions[node_id]
        if decision.id in emitted_decision_refs or decision.status == CapabilityStatus.UNSUPPORTED:
            plan_decisions[node_id] = decision
            continue
        error = errors_by_node.get(node_id)
        if error is not None:
            plan_decisions[node_id] = decision.model_copy(
                update={
                    "status": CapabilityStatus.UNSUPPORTED,
                    "rule_id": error.code,
                    "evidence": error.evidence or (f"node.id={node_id}",),
                    "reasons": (*decision.reasons, "not_emitted"),
                    "blocking": True,
                }
            )
    for node_id in sorted(plan_decisions):
        decision = plan_decisions[node_id]
        if decision.status != CapabilityStatus.UNSUPPORTED:
            continue
        if decision.id in emitted_decision_refs and is_reviewable_text_decision(decision):
            continue
        if any(
            item.code == decision.rule_id
            and item.node_id == decision.node_ref
            and item.rule_id == decision.rule_id
            and item.rule_version == decision.rule_version
            and item.evidence == decision.evidence
            and item.blocks_binding
            for item in diagnostics
        ):
            continue
        diagnostics.append(
            _decision_diagnostic(document, document.nodes[node_id], decision)
        )
    diagnostics = [
        _complete_diagnostic(item, rule_version=rule_version)
        for item in diagnostics
    ]
    bindable = not any(
        item.severity == Severity.ERROR or item.blocks_binding
        for item in diagnostics
    ) and not any(item.blocking for item in plan_decisions.values())
    return FGUIPlanDocument(
        documentId=document.document_id,
        sourceUirSha256=source_hash,
        profileVersion=profile_version,
        ruleVersion=rule_version,
        bindable=bindable,
        roots=roots,
        nodes=nodes,
        resources=resources,
        masks=masks,
        decisions=plan_decisions,
        diagnostics=tuple(diagnostics),
    )
