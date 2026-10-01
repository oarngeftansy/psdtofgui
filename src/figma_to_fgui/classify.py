from figma_to_fgui.models import ClassificationDecision, DecisionSource, NormalizedNode
from figma_to_fgui.rules import Rule
from figma_to_fgui.tree import walk_nodes


def _matches(node: NormalizedNode, rule: Rule) -> tuple[bool, tuple[str, ...]]:
    evidence: list[str] = []
    expected_type = rule.when.get("type")
    if expected_type is not None and node.type != expected_type:
        return False, ()
    if expected_type is not None:
        evidence.append(f"type={node.type}")
    min_width = rule.when.get("minWidth")
    if min_width is not None and node.bounds.width < float(min_width):
        return False, ()
    if min_width is not None:
        evidence.append(f"width>={min_width}")
    min_children = rule.when.get("minChildren")
    if min_children is not None and len(node.children) < int(min_children):
        return False, ()
    if min_children is not None:
        evidence.append(f"children>={min_children}")
    return True, tuple(evidence or ("fallback",))


def classify_tree(
    roots: tuple[NormalizedNode, ...],
    rules: tuple[Rule, ...],
    overrides: tuple[ClassificationDecision, ...] = (),
) -> tuple[ClassificationDecision, ...]:
    override_by_id = {item.node_id: item for item in overrides}
    decisions: list[ClassificationDecision] = []
    for node in walk_nodes(roots):
        override = override_by_id.get(node.id)
        if override is not None:
            decisions.append(override)
            continue
        for rule in rules:
            matched, evidence = _matches(node, rule)
            if matched:
                decisions.append(
                    ClassificationDecision(
                        node_id=node.id,
                        output_type=rule.action["outputType"],
                        rule_id=rule.id,
                        rule_version=rule.version,
                        evidence=evidence,
                        confidence=rule.confidence,
                        source=DecisionSource.RULE,
                    )
                )
                break
    return tuple(decisions)


def merge_classification_decisions(
    roots: tuple[NormalizedNode, ...],
    rule_candidates: tuple[ClassificationDecision, ...],
    overrides: tuple[ClassificationDecision, ...],
) -> tuple[ClassificationDecision, ...]:
    """Merge validated overrides over precomputed deterministic rule candidates."""
    candidate_by_id = {item.node_id: item for item in rule_candidates}
    override_by_id = {item.node_id: item for item in overrides}
    return tuple(
        decision
        for node in walk_nodes(roots)
        if (decision := override_by_id.get(node.id, candidate_by_id.get(node.id))) is not None
    )
