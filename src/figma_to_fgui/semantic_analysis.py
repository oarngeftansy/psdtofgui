from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from typing import Protocol

from figma_to_fgui.ai_client import (
    MAX_SUMMARY_DEPTH,
    MAX_SUMMARY_NODES,
    AIAnalysisError,
    AIReasonCode,
)
from figma_to_fgui.models import ClassificationDecision, Diagnostic, NormalizedNode, Severity
from figma_to_fgui.semantic_models import SemanticAnalysisOutcome, SemanticResponse
from figma_to_fgui.semantic_validation import validate_semantic_response

_MAX_TEXT_BYTES = 256
_MAX_METADATA_STRING_BYTES = 96
_MAX_METADATA_FIELDS = 16
_MAX_STYLE_ITEMS = 4
_PROPERTY_KEYS = frozenset(
    {
        "checked",
        "componentproperties",
        "disabled",
        "enabled",
        "mode",
        "selected",
        "size",
        "state",
        "status",
        "type",
        "value",
        "variant",
        "variantproperties",
    }
)
_STYLE_SCALAR_KEYS = frozenset(
    {
        "clipscontent",
        "cornerradius",
        "counteraxisalignitems",
        "counteraxissizingmode",
        "fontsize",
        "itemspacing",
        "letterspacing",
        "lineheight",
        "opacity",
        "paddingbottom",
        "paddingleft",
        "paddingright",
        "paddingtop",
        "primaryaxisalignitems",
        "primaryaxissizingmode",
        "textalignhorizontal",
        "textalignvertical",
        "textautoresize",
        "layoutalign",
        "layoutgrow",
        "layoutmode",
    }
)
_STYLE_COLLECTION_KEYS = frozenset({"effects", "fills", "strokes"})
_STYLE_ITEM_KEYS = frozenset(
    {"blendmode", "color", "offset", "opacity", "radius", "spread", "type", "visible", "weight"}
)
_STYLE_VECTOR_KEYS = frozenset({"a", "b", "g", "r", "x", "y"})
_KEY_NORMALIZER = re.compile(r"[^a-z0-9]")


class SemanticClient(Protocol):
    def analyze(
        self, summary: dict[str, object], screenshot: bytes | None = None
    ) -> SemanticResponse: ...


def _normalized_key(value: str) -> str:
    return _KEY_NORMALIZER.sub("", value.casefold())


def _bounded_string(value: str, byte_limit: int) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise AIAnalysisError(AIReasonCode.REQUEST_INVALID) from None
    if len(encoded) <= byte_limit:
        return value
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _safe_scalar(value: object) -> str | bool | int | float | None:
    if isinstance(value, str):
        return _bounded_string(value, _MAX_METADATA_STRING_BYTES)
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _safe_style_item(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, object] = {}
    for key, nested in value.items():
        if not isinstance(key, str) or len(result) >= _MAX_METADATA_FIELDS:
            continue
        normalized = _normalized_key(key)
        if normalized not in _STYLE_ITEM_KEYS:
            continue
        scalar = _safe_scalar(nested)
        if scalar is not None:
            result[key] = scalar
            continue
        if normalized in {"color", "offset"} and isinstance(nested, dict):
            vector: dict[str, object] = {}
            for vector_key, vector_value in nested.items():
                if (
                    isinstance(vector_key, str)
                    and _normalized_key(vector_key) in _STYLE_VECTOR_KEYS
                ):
                    safe_value = _safe_scalar(vector_value)
                    if safe_value is not None and not isinstance(safe_value, str):
                        vector[vector_key] = safe_value
            if vector:
                result[key] = vector
    return result or None


def _summarize_properties(properties: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in properties.items():
        if len(result) >= _MAX_METADATA_FIELDS or _normalized_key(key) not in _PROPERTY_KEYS:
            continue
        bounded_key = _bounded_string(key, _MAX_METADATA_STRING_BYTES)
        bounded_value = _bounded_string(value, _MAX_METADATA_STRING_BYTES)
        if bounded_key and bounded_value:
            result[bounded_key] = bounded_value
    return result


def _summarize_style(style: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in style.items():
        if not isinstance(key, str) or len(result) >= _MAX_METADATA_FIELDS:
            continue
        normalized = _normalized_key(key)
        if normalized in _STYLE_SCALAR_KEYS:
            scalar = _safe_scalar(value)
            if scalar is not None:
                result[key] = scalar
        elif normalized in _STYLE_COLLECTION_KEYS and isinstance(value, (list, tuple)):
            items = tuple(
                item
                for item in (_safe_style_item(raw) for raw in value[:_MAX_STYLE_ITEMS])
                if item is not None
            )
            if items:
                result[key] = items
    return result


def _summarize(
    node: NormalizedNode,
    parent_id: str | None,
    rule_candidate: ClassificationDecision | None,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "id": node.id,
        "parent_id": parent_id,
        "name": _bounded_string(node.name, _MAX_TEXT_BYTES),
        "type": node.type,
        "bounds": node.bounds.model_dump(mode="json"),
        "rotation": node.rotation,
        "source_order": node.source_order,
    }
    if node.text is not None:
        summary["text"] = _bounded_string(node.text, _MAX_TEXT_BYTES)
    properties = _summarize_properties(node.properties)
    if properties:
        summary["properties"] = properties
    style = _summarize_style(node.raw_style)
    if style:
        summary["style"] = style
    if rule_candidate is not None:
        summary["rule_candidate"] = {
            "output_type": _bounded_string(
                rule_candidate.output_type, _MAX_METADATA_STRING_BYTES
            ),
            "evidence": [
                _bounded_string(item, _MAX_METADATA_STRING_BYTES)
                for item in rule_candidate.evidence[:_MAX_METADATA_FIELDS]
            ],
            "confidence": rule_candidate.confidence,
        }
    return summary


def build_selection_summary(
    roots: Iterable[NormalizedNode],
    rule_candidates: Iterable[ClassificationDecision] = (),
) -> dict[str, object]:
    """Produce a bounded, allow-listed semantic summary of only the normalized selection."""
    candidate_by_id = {item.node_id: item for item in rule_candidates}
    nodes: list[dict[str, object]] = []
    stack: list[tuple[Iterator[NormalizedNode], str | None, int]] = [
        (iter(roots), None, 0)
    ]
    while stack:
        siblings, parent_id, depth = stack[-1]
        try:
            node = next(siblings)
        except StopIteration:
            stack.pop()
            continue
        if depth > MAX_SUMMARY_DEPTH or len(nodes) >= MAX_SUMMARY_NODES:
            raise AIAnalysisError(AIReasonCode.REQUEST_INVALID)
        nodes.append(_summarize(node, parent_id, candidate_by_id.get(node.id)))
        stack.append((iter(node.children), node.id, depth + 1))
    return {"version": 1, "nodes": nodes}


def fallback_warning(reason: AIReasonCode) -> Diagnostic:
    return Diagnostic(
        code=reason,
        severity=Severity.WARNING,
        message="AI semantic analysis was unavailable; rule classification remains active.",
    )


def analyze_semantics(
    roots: tuple[NormalizedNode, ...],
    client: SemanticClient | None,
    screenshot: bytes | None = None,
    *,
    rule_candidates: tuple[ClassificationDecision, ...] = (),
) -> SemanticAnalysisOutcome:
    if client is None:
        return SemanticAnalysisOutcome(
            overrides=(),
            diagnostics=(fallback_warning(AIReasonCode.DISABLED),),
            used_fallback=True,
        )
    try:
        summary = build_selection_summary(roots, rule_candidates)
        response = (
            client.analyze(summary)
            if screenshot is None
            else client.analyze(summary, screenshot=screenshot)
        )
    except AIAnalysisError as error:
        return SemanticAnalysisOutcome(
            overrides=(),
            diagnostics=(fallback_warning(error.code),),
            used_fallback=True,
        )
    overrides, diagnostics = validate_semantic_response(
        roots, response, rule_candidates=rule_candidates
    )
    return SemanticAnalysisOutcome(
        overrides=overrides,
        diagnostics=diagnostics,
        screenshot_recommended=response.screenshot_recommended,
        screenshot_reason=response.screenshot_reason,
    )
