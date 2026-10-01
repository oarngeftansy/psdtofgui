import re
from collections import Counter
from collections.abc import Iterable

from figma_to_fgui.models import (
    ClassificationDecision,
    DecisionSource,
    Diagnostic,
    NormalizedNode,
    Severity,
)
from figma_to_fgui.semantic_models import SemanticDecision, SemanticResponse, SemanticType
from figma_to_fgui.semantic_names import is_valid_semantic_name, validate_semantic_overrides

_OUTPUT_TYPES = {
    SemanticType.PANEL: "PANEL",
    SemanticType.TEXT: "TEXT",
}

_ALLOWED_CHILD_ROLES = {
    SemanticType.BUTTON: frozenset({"title", "icon"}),
    SemanticType.LABEL: frozenset({"title", "icon"}),
    SemanticType.SLIDER: frozenset({"bar", "grip", "bg"}),
}
_STATEFUL_TYPES = frozenset({SemanticType.BUTTON, SemanticType.SLIDER})
_STATE_PAGE_KEY = re.compile(r"(?:0|[1-9][0-9]{0,2})\Z")


def _index_tree(
    roots: Iterable[NormalizedNode],
) -> tuple[dict[str, NormalizedNode], dict[str, str | None]]:
    nodes: dict[str, NormalizedNode] = {}
    parents: dict[str, str | None] = {}

    def visit(node: NormalizedNode, parent_id: str | None) -> None:
        nodes[node.id] = node
        parents[node.id] = parent_id
        for child in node.children:
            visit(child, node.id)

    for root in roots:
        visit(root, None)
    return nodes, parents


def _is_descendant(node_id: str, ancestor_id: str, parents: dict[str, str | None]) -> bool:
    current_id = parents.get(node_id)
    while current_id is not None:
        if current_id == ancestor_id:
            return True
        current_id = parents.get(current_id)
    return False


def _contains(parent: NormalizedNode, child: NormalizedNode) -> bool:
    parent_right = parent.bounds.x + parent.bounds.width
    parent_bottom = parent.bounds.y + parent.bounds.height
    child_right = child.bounds.x + child.bounds.width
    child_bottom = child.bounds.y + child.bounds.height
    return (
        parent.bounds.x <= child.bounds.x
        and parent.bounds.y <= child.bounds.y
        and child_right <= parent_right
        and child_bottom <= parent_bottom
    )


def _cycle_nodes(parents: dict[str, str | None]) -> set[str]:
    cycle_nodes: set[str] = set()
    visited: set[str] = set()
    for start in parents:
        if start in visited:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current is not None and current not in visited:
            previous_position = positions.get(current)
            if previous_position is not None:
                cycle_nodes.update(path[previous_position:])
                break
            positions[current] = len(path)
            path.append(current)
            current = parents.get(current)
        visited.update(path)
    return cycle_nodes


def _warning(code: str, node_id: str, message: str) -> Diagnostic:
    return Diagnostic(code=code, severity=Severity.WARNING, message=message, node_id=node_id)


def validate_semantic_response(
    roots: tuple[NormalizedNode, ...],
    response: SemanticResponse,
    *,
    rule_candidates: Iterable[ClassificationDecision] | None = None,
) -> tuple[tuple[ClassificationDecision, ...], tuple[Diagnostic, ...]]:
    """Convert only safe, tree-scoped semantic decisions into classification overrides."""
    nodes, parents = _index_tree(roots)
    diagnostics: list[Diagnostic] = []
    candidates: list[tuple[SemanticDecision, NormalizedNode]] = []
    decision_counts = Counter(item.node_id for item in response.decisions)
    duplicate_diagnostics: set[str] = set()
    for item in response.decisions:
        if decision_counts[item.node_id] > 1:
            if item.node_id not in duplicate_diagnostics:
                diagnostics.append(
                    _warning(
                        "semantic.duplicate_decision",
                        item.node_id,
                        "Every node may have at most one semantic decision.",
                    )
                )
                duplicate_diagnostics.add(item.node_id)
            continue
        node = nodes.get(item.node_id)
        if node is None:
            diagnostics.append(
                _warning(
                    "semantic.unknown_node",
                    item.node_id,
                    "Semantic decision references a node outside the normalized tree.",
                )
            )
            continue
        rejected = False
        child_ids = {child.id for child in node.children}
        roles = tuple(item.children_roles.values())
        allowed_roles = _ALLOWED_CHILD_ROLES.get(item.semantic_type, frozenset())
        if (
            any(child_id not in child_ids for child_id in item.children_roles)
            or any(role not in allowed_roles for role in roles)
            or len(roles) != len(set(roles))
        ):
            diagnostics.append(
                _warning(
                    "semantic.invalid_child_role",
                    item.node_id,
                    "Child roles must be unique, type-supported, and reference direct children.",
                )
            )
            rejected = True
        if item.state_pages and item.semantic_type not in _STATEFUL_TYPES:
            diagnostics.append(
                _warning(
                    "semantic.unsupported_state_pages",
                    item.node_id,
                    "State pages are only supported for Button and Slider decisions.",
                )
            )
            rejected = True
        elif any(
            _STATE_PAGE_KEY.fullmatch(key) is None or not is_valid_semantic_name(name)
            for key, name in item.state_pages.items()
        ) or len({name.casefold() for name in item.state_pages.values()}) != len(
            item.state_pages
        ):
            diagnostics.append(
                _warning(
                    "semantic.invalid_state_page",
                    item.node_id,
                    "State page keys and unique names must use bounded canonical identifiers.",
                )
            )
            rejected = True
        if item.reparent is not None:
            new_parent = nodes.get(item.reparent.new_parent)
            if item.reparent.new_parent == item.node_id or _is_descendant(
                item.reparent.new_parent, item.node_id, parents
            ):
                diagnostics.append(
                    _warning(
                        "semantic.reparent_cycle",
                        item.node_id,
                        "Suggested parent would create a tree cycle.",
                    )
                )
                rejected = True
            elif new_parent is None or not _contains(new_parent, node):
                diagnostics.append(
                    _warning(
                        "semantic.reparent_outside",
                        item.node_id,
                        "Suggested parent is missing or does not fully contain the node.",
                    )
                )
                rejected = True
        if rejected:
            continue
        candidates.append((item, node))

    proposed_parents = dict(parents)
    for item, _ in candidates:
        if item.reparent is not None:
            proposed_parents[item.node_id] = item.reparent.new_parent
    proposed_cycle_nodes = _cycle_nodes(proposed_parents)
    locally_safe_candidates = candidates
    candidates = []
    for item, node in locally_safe_candidates:
        if item.node_id in proposed_cycle_nodes:
            diagnostics.append(
                _warning(
                    "semantic.reparent_cycle",
                    item.node_id,
                    "Combined semantic reparent suggestions would create a tree cycle.",
                )
            )
            continue
        candidates.append((item, node))

    decisions: list[ClassificationDecision] = []
    candidate_by_id = (
        None
        if rule_candidates is None
        else {candidate.node_id: candidate for candidate in rule_candidates}
    )
    for item, node in candidates:
        rule_candidate = (
            None if candidate_by_id is None else candidate_by_id.get(item.node_id)
        )
        output_type = (
            _OUTPUT_TYPES.get(item.semantic_type)
            if candidate_by_id is None
            else None if rule_candidate is None else rule_candidate.output_type
        )
        if output_type is None:
            diagnostics.append(
                _warning(
                    "semantic.unsupported_type",
                    item.node_id,
                    "Semantic decision has no deterministic FairyGUI generator slot.",
                )
            )
            continue
        decisions.append(
            ClassificationDecision(
                node_id=item.node_id,
                output_type=output_type,
                rule_id="ai.semantic.v1",
                rule_version=1,
                evidence=("validated structured AI decision",),
                confidence=item.confidence,
                source=DecisionSource.AI,
                semantic_name=item.fgui_name,
                semantic_type=item.semantic_type,
            )
        )
    accepted, name_diagnostics = validate_semantic_overrides(
        roots, tuple(decisions), rule_candidates=rule_candidates
    )
    return accepted, tuple(diagnostics) + name_diagnostics
