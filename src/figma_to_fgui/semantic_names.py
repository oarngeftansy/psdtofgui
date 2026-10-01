from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import TYPE_CHECKING

from figma_to_fgui.models import (
    ClassificationDecision,
    Diagnostic,
    NormalizedNode,
    Severity,
)
from figma_to_fgui.tree import walk_nodes

if TYPE_CHECKING:
    from figma_to_fgui.project_index import ProjectIndex

SEMANTIC_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
_SEMANTIC_NAME = re.compile(SEMANTIC_NAME_PATTERN)
_SUPPORTED_OUTPUT_OVERRIDES = frozenset({"PANEL", "TEXT"})


def is_valid_semantic_name(value: str) -> bool:
    return _SEMANTIC_NAME.fullmatch(value) is not None


def _warning(code: str, node_id: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity=Severity.WARNING, message=message, node_id=node_id)


def _index_nodes(roots: tuple[NormalizedNode, ...]) -> dict[str, NormalizedNode]:
    return {node.id: node for node in walk_nodes(roots)}


def validate_semantic_overrides(
    roots: tuple[NormalizedNode, ...],
    overrides: tuple[ClassificationDecision, ...],
    *,
    rule_candidates: Iterable[ClassificationDecision] | None = None,
    project_index: ProjectIndex | None = None,
    package_name: str | None = None,
) -> tuple[tuple[ClassificationDecision, ...], tuple[Diagnostic, ...]]:
    """Accept only tree-scoped overrides with globally unambiguous semantic names."""
    nodes = _index_nodes(roots)
    root_ids = {node.id for node in roots}
    direct_child_ids = {child.id for root in roots for child in root.children}
    candidate_by_id = (
        None
        if rule_candidates is None
        else {item.node_id: item for item in rule_candidates}
    )
    diagnostics: list[Diagnostic] = []
    counts = Counter(item.node_id for item in overrides)
    duplicate_nodes: set[str] = set()
    candidates: list[ClassificationDecision] = []
    for item in overrides:
        if counts[item.node_id] > 1:
            if item.node_id not in duplicate_nodes:
                diagnostics.append(
                    _warning(
                        "semantic.duplicate_decision",
                        item.node_id,
                        "Every node may have at most one semantic decision.",
                    )
                )
                duplicate_nodes.add(item.node_id)
            continue
        if item.node_id not in nodes:
            diagnostics.append(
                _warning(
                    "semantic.unknown_node",
                    item.node_id,
                    "Semantic decision references a node outside the normalized tree.",
                )
            )
            continue
        candidate = (
            None if candidate_by_id is None else candidate_by_id.get(item.node_id)
        )
        if candidate_by_id is None and item.output_type not in _SUPPORTED_OUTPUT_OVERRIDES:
            diagnostics.append(
                _warning(
                    "semantic.unsupported_type",
                    item.node_id,
                    "Semantic output is not supported by the deterministic generator.",
                )
            )
            continue
        incompatible_slot = candidate_by_id is not None and (
            candidate is None
            or candidate.output_type != item.output_type
            or (item.output_type == "PANEL" and item.node_id not in root_ids)
            or (item.output_type == "TEXT" and item.node_id not in direct_child_ids)
        )
        if incompatible_slot:
            diagnostics.append(
                _warning(
                    "semantic.unsupported_type",
                    item.node_id,
                    "Semantic output cannot replace this node's deterministic generator slot.",
                )
            )
            continue
        if item.semantic_name is not None and not is_valid_semantic_name(item.semantic_name):
            diagnostics.append(
                _warning(
                    "semantic.invalid_name",
                    item.node_id,
                    "Semantic names must use bounded canonical identifiers.",
                )
            )
            continue
        candidates.append(item)

    candidate_names = Counter(
        item.semantic_name.casefold()
        for item in candidates
        if item.semantic_name is not None
    )
    existing_names: dict[str, set[str]] = {}
    for node_id, node in nodes.items():
        existing_names.setdefault(node.name.casefold(), set()).add(node_id)

    accepted: list[ClassificationDecision] = []
    for item in candidates:
        if item.semantic_name is not None:
            normalized_name = item.semantic_name.casefold()
            existing_owners = existing_names.get(normalized_name, set())
            if candidate_names[normalized_name] > 1 or existing_owners - {item.node_id}:
                diagnostics.append(
                    _warning(
                        "semantic.name_conflict",
                        item.node_id,
                        "Semantic names must be unique across AI decisions and existing nodes.",
                    )
                )
                continue
            if (
                item.output_type == "PANEL"
                and project_index is not None
                and package_name is not None
            ):
                proposed = f"Panel_{package_name}_{item.semantic_name}.xml"
                baseline = f"Panel_{package_name}_{nodes[item.node_id].name}.xml"
                existing_components = (
                    resource
                    for resource in project_index.resources_by_package.get(
                        package_name, {}
                    ).values()
                    if resource.kind == "component"
                )
                if proposed != baseline and any(
                    resource.name.casefold() == proposed.casefold()
                    for resource in existing_components
                ):
                    diagnostics.append(
                        _warning(
                            "semantic.project_name_conflict",
                            item.node_id,
                            "Semantic name belongs to a different existing project component.",
                        )
                    )
                    continue
        accepted.append(item)
    return tuple(accepted), tuple(diagnostics)
