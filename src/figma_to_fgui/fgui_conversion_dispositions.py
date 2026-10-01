"""Backend-authored conversion dispositions for committed Figma selections."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from figma_to_fgui.fgui_plan_models import (
    CapabilityDecision,
    CapabilityStatus,
    FGUIPlanDocument,
)
from figma_to_fgui.fgui_plan_policy import (
    NATIVE_CONTAINER_RULE_ID,
    NATIVE_IMAGE_RULE_ID,
)
from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.models import Diagnostic
from figma_to_fgui.service_contracts import (
    NewProjectAdjustmentStrategy,
    NewProjectConversionDisposition,
    NewProjectDispositionDetails,
    NewProjectDispositionLevel,
    NewProjectDispositionReason,
)

_REASON_ALIASES = {"composite_visual": NewProjectDispositionReason.VISUAL_STYLE}
_EDITABLE_RISKS = frozenset(
    {
        NewProjectDispositionReason.RICH_TEXT_RUNS,
        NewProjectDispositionReason.INSTANCE_COMPOSITE,
    }
)
_RICH_TEXT_RULE = "fgui.text.runs_unsupported"
_TEXT_STYLE_RULE = "fgui.text.style_unsupported"
_TEXT_RISK_RULES = {
    _RICH_TEXT_RULE: NewProjectDispositionReason.RICH_TEXT_RUNS,
    _TEXT_STYLE_RULE: NewProjectDispositionReason.TEXT_STYLE_PROPERTIES,
}
_RICH_TEXT_EVIDENCE_PREFIXES = (
    "text.runs.count=",
    "text.runs.preserved=",
    "text.runs.unsupported=",
)
_MAX_RICH_TEXT_RUN_COUNT = 9_999_999_999
_MAX_RICH_TEXT_PROPERTIES = 32


def _source_nodes(manifest: SelectionManifest) -> dict[str, SelectionNode]:
    result: dict[str, SelectionNode] = {}
    pending = list(manifest.top_level_nodes)
    while pending:
        node = pending.pop()
        if node.id in result:
            raise ValueError("duplicate source node identity")
        result[node.id] = node
        pending.extend(node.children)
    return result


def _reason(value: str) -> NewProjectDispositionReason:
    alias = _REASON_ALIASES.get(value)
    if alias is not None:
        return alias
    return NewProjectDispositionReason(value)


def _disposition_id(source_node_id: str, reason: NewProjectDispositionReason) -> str:
    encoded = (
        len(source_node_id.encode("utf-8")).to_bytes(4, "big")
        + source_node_id.encode("utf-8")
        + len(reason.value.encode("utf-8")).to_bytes(4, "big")
        + reason.value.encode("utf-8")
    )
    return f"disposition:{hashlib.sha256(encoded).hexdigest()[:16]}"


def _native_reason(source_type: str, rule_id: str) -> NewProjectDispositionReason:
    if rule_id == NATIVE_IMAGE_RULE_ID:
        return NewProjectDispositionReason.NATIVE_IMAGE
    if source_type == "TEXT":
        return NewProjectDispositionReason.NATIVE_TEXT
    if source_type in {"RECTANGLE", "ELLIPSE", "VECTOR", "BOOLEAN_OPERATION", "STAR", "LINE", "POLYGON"}:
        return NewProjectDispositionReason.NATIVE_SHAPE
    if source_type == "INSTANCE" and rule_id == NATIVE_CONTAINER_RULE_ID:
        return NewProjectDispositionReason.NATIVE_INSTANCE_STRUCTURE
    if source_type == "INSTANCE":
        return NewProjectDispositionReason.NATIVE_COMPONENT
    return NewProjectDispositionReason.NATIVE_STRUCTURE


def _canonical_properties(value: str) -> tuple[str, ...]:
    properties = tuple(value.split(","))
    if (
        not properties
        or len(properties) > _MAX_RICH_TEXT_PROPERTIES
        or any(not item for item in properties)
        or len(properties) != len(set(properties))
        or tuple(sorted(properties)) != properties
    ):
        raise ValueError("rich-text decision properties are not canonical")
    return properties


def _parse_rich_text_evidence(
    evidence: tuple[str, ...],
) -> NewProjectDispositionDetails:
    if len(evidence) != len(_RICH_TEXT_EVIDENCE_PREFIXES):
        raise ValueError("rich-text decision evidence is incomplete")
    facts: dict[str, str] = {}
    for item in evidence:
        matches = tuple(
            prefix for prefix in _RICH_TEXT_EVIDENCE_PREFIXES if item.startswith(prefix)
        )
        if len(matches) != 1 or matches[0] in facts:
            raise ValueError("rich-text decision evidence prefixes must be exact and unique")
        prefix = matches[0]
        facts[prefix] = item.removeprefix(prefix)
    if set(facts) != set(_RICH_TEXT_EVIDENCE_PREFIXES):
        raise ValueError("rich-text decision evidence is incomplete")
    raw_count = facts["text.runs.count="]
    if (
        not raw_count.isascii()
        or not raw_count.isdecimal()
        or raw_count.startswith("0")
    ):
        raise ValueError("rich-text run count must be a canonical positive integer")
    run_count = int(raw_count)
    if run_count > _MAX_RICH_TEXT_RUN_COUNT:
        raise ValueError("rich-text run count exceeds the public contract")
    return NewProjectDispositionDetails(
        runCount=run_count,
        preservedProperties=_canonical_properties(facts["text.runs.preserved="]),
        unsupportedProperties=_canonical_properties(facts["text.runs.unsupported="]),
    )


def _reviewed_text_details(
    decision: CapabilityDecision,
) -> NewProjectDispositionDetails:
    if (
        decision.status is not CapabilityStatus.UNSUPPORTED
        or decision.rule_id not in _TEXT_RISK_RULES
        or decision.blocking
        or decision.reasons != (_TEXT_RISK_RULES[decision.rule_id].value,)
    ):
        raise ValueError("rich-text decision is not the registered nonblocking decision")
    return _parse_rich_text_evidence(decision.evidence)


def _optional_raster_rich_text_details(
    decision: CapabilityDecision,
) -> NewProjectDispositionDetails | None:
    if not any(
        item.startswith(_RICH_TEXT_EVIDENCE_PREFIXES) for item in decision.evidence
    ):
        return None
    return _parse_rich_text_evidence(decision.evidence)


def build_conversion_dispositions(
    manifest: SelectionManifest,
    plan: FGUIPlanDocument,
    source_node_ids: Mapping[str, str],
) -> tuple[NewProjectConversionDisposition, ...]:
    """Project raster decisions into stable public review dispositions."""
    source_nodes = _source_nodes(manifest)
    projected: list[NewProjectConversionDisposition] = []
    seen: set[tuple[str, NewProjectDispositionReason]] = set()
    for decision in sorted(plan.decisions.values(), key=lambda item: item.node_ref):
        source_node_id = source_node_ids.get(decision.node_ref)
        source = None if source_node_id is None else source_nodes.get(source_node_id)
        if source_node_id is None or source is None:
            if decision.status is CapabilityStatus.NATIVE:
                continue
            raise ValueError("conversion decision is missing source provenance")
        if decision.status is CapabilityStatus.NATIVE:
            if (
                decision.rule_id == NATIVE_IMAGE_RULE_ID
                and source.properties.get("export_strategy") == "vector_asset"
            ):
                node = next(
                    (
                        item
                        for item in plan.nodes.values()
                        if item.uir_node_ref == decision.node_ref
                    ),
                    None,
                )
                resource = (
                    None
                    if node is None or node.resource_ref is None
                    else plan.resources.get(node.resource_ref)
                )
                svg_preserved = (
                    resource is not None
                    and resource.export_format == "svg"
                    and resource.mime_type == "image/svg+xml"
                )
                reason = (
                    NewProjectDispositionReason.NATIVE_VECTOR_RESOURCE
                    if svg_preserved
                    else NewProjectDispositionReason.RASTERIZED_VECTOR
                )
                projected.append(
                    NewProjectConversionDisposition(
                        id=_disposition_id(source_node_id, reason),
                        sourceNodeId=source_node_id,
                        sourceName=source.name,
                        sourceType=source.type.upper(),
                        level=(
                            NewProjectDispositionLevel.NATIVE
                            if svg_preserved
                            else NewProjectDispositionLevel.RASTER_PRESERVED
                        ),
                        reason=reason,
                        defaultStrategy=(
                            None
                            if svg_preserved
                            else NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE
                        ),
                        allowedStrategies=(
                            ()
                            if svg_preserved
                            else (NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE,)
                        ),
                        visualImpact="unchanged" if svg_preserved else "visual_preserved",
                        editabilityImpact="vector_path_not_editable",
                        componentImpact="unchanged",
                        blocksApproval=False,
                        details=None,
                    )
                )
                continue
            static_layout = (
                source.type.upper() in {"FRAME", "GROUP", "COMPONENT"}
                and str(source.properties.get("layout_mode", "")).upper()
                in {"HORIZONTAL", "VERTICAL"}
            )
            reason = (
                NewProjectDispositionReason.NATIVE_STATIC_LAYOUT
                if static_layout
                else _native_reason(source.type.upper(), decision.rule_id)
            )
            inline_instance = reason is NewProjectDispositionReason.NATIVE_INSTANCE_STRUCTURE
            projected.append(
                NewProjectConversionDisposition(
                    id=_disposition_id(source_node_id, reason),
                    sourceNodeId=source_node_id,
                    sourceName=source.name,
                    sourceType=source.type.upper(),
                    level=NewProjectDispositionLevel.NATIVE,
                    reason=reason,
                    defaultStrategy=None,
                    allowedStrategies=(),
                    visualImpact="unchanged",
                    editabilityImpact=(
                        "layout_reflow_not_editable" if static_layout else "unchanged"
                    ),
                    componentImpact=("instance_not_reusable" if inline_instance else "unchanged"),
                    blocksApproval=False,
                    details=None,
                )
            )
            continue
        if decision.rule_id in _TEXT_RISK_RULES:
            details = _reviewed_text_details(decision)
            reason = _TEXT_RISK_RULES[decision.rule_id]
            identity = (source_node_id, reason)
            if identity in seen:
                raise ValueError("duplicate rich-text conversion disposition")
            seen.add(identity)
            projected.append(
                NewProjectConversionDisposition(
                    id=_disposition_id(source_node_id, reason),
                    sourceNodeId=source_node_id,
                    sourceName=source.name,
                    sourceType=source.type.upper(),
                    level=NewProjectDispositionLevel.EDITABLE_RISK,
                    reason=reason,
                    defaultStrategy=NewProjectAdjustmentStrategy.PRESERVE_EDITABLE,
                    allowedStrategies=(
                        NewProjectAdjustmentStrategy.PRESERVE_EDITABLE,
                        NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE,
                    ),
                    visualImpact="may_differ",
                    editabilityImpact="unchanged",
                    componentImpact="unchanged",
                    blocksApproval=False,
                    details=details,
                )
            )
            continue
        if decision.status is not CapabilityStatus.RASTER_FALLBACK:
            continue
        for raw_reason in decision.reasons or ("composite_visual",):
            reason = _reason(raw_reason)
            identity = (source_node_id, reason)
            if identity in seen:
                continue
            seen.add(identity)
            editable_risk = reason in _EDITABLE_RISKS
            allowed = (
                (
                    NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE,
                    NewProjectAdjustmentStrategy.PRESERVE_EDITABLE,
                )
                if reason is NewProjectDispositionReason.RICH_TEXT_RUNS
                else (
                    NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE,
                    NewProjectAdjustmentStrategy.PRESERVE_EDITABLE,
                )
                if editable_risk
                else (NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE,)
            )
            raster_details = (
                _optional_raster_rich_text_details(decision)
                if reason is NewProjectDispositionReason.RICH_TEXT_RUNS
                else None
            )
            projected.append(
                NewProjectConversionDisposition(
                    id=_disposition_id(source_node_id, reason),
                    sourceNodeId=source_node_id,
                    sourceName=source.name,
                    sourceType=source.type.upper(),
                    level=(
                        NewProjectDispositionLevel.EDITABLE_RISK
                        if editable_risk
                        else NewProjectDispositionLevel.RASTER_PRESERVED
                    ),
                    reason=reason,
                    defaultStrategy=NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE,
                    allowedStrategies=allowed,
                    visualImpact="visual_preserved",
                    editabilityImpact=(
                        "text_not_editable"
                        if reason is NewProjectDispositionReason.RICH_TEXT_RUNS
                        else "subtree_not_editable"
                    ),
                    componentImpact=(
                        "instance_not_reusable"
                        if reason is NewProjectDispositionReason.INSTANCE_COMPOSITE
                        else "unchanged"
                    ),
                    blocksApproval=False,
                    details=raster_details,
                )
            )
    return tuple(
        sorted(projected, key=lambda item: (item.level.value, item.source_node_id, item.reason.value))
    )


def build_blocked_dispositions(
    manifest: SelectionManifest, diagnostics: tuple[Diagnostic, ...]
) -> tuple[NewProjectConversionDisposition, ...]:
    """Return source-addressable red review items for known pre-build blockers."""
    codes = {item.code for item in diagnostics}
    projected: list[NewProjectConversionDisposition] = []
    for source in sorted(_source_nodes(manifest).values(), key=lambda item: item.id):
        reason: NewProjectDispositionReason | None = None
        component_impact: Literal["unchanged", "instance_not_reusable"] = "unchanged"
        if (
            "fgui.component.definition_missing" in codes
            and source.type.upper() == "INSTANCE"
            and source.properties.get("export_strategy") != "composite_png"
        ):
            reason = NewProjectDispositionReason.COMPONENT_DEFINITION_MISSING
            component_impact = "instance_not_reusable"
        elif (
            "fgui.writer.workflow.validation_failed" in codes
            and bool(source.properties.get("interactions"))
        ):
            reason = NewProjectDispositionReason.INTERACTION_UNSUPPORTED
        if reason is None:
            continue
        projected.append(
            NewProjectConversionDisposition(
                id=_disposition_id(source.id, reason),
                sourceNodeId=source.id,
                sourceName=source.name,
                sourceType=source.type.upper(),
                level=NewProjectDispositionLevel.BLOCKED,
                reason=reason,
                defaultStrategy=None,
                allowedStrategies=(),
                visualImpact="may_differ",
                editabilityImpact="unchanged",
                componentImpact=component_impact,
                blocksApproval=True,
                details=None,
            )
        )
    return tuple(projected)
