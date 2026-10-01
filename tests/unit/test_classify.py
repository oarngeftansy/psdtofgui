import json
from pathlib import Path

from figma_to_fgui.classify import classify_tree
from figma_to_fgui.models import ClassificationDecision, DecisionSource
from figma_to_fgui.normalize import normalize_document
from figma_to_fgui.rules import load_rules


def test_classification_records_rule_evidence_and_confidence() -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    by_node = {decision.node_id: decision for decision in decisions}
    assert by_node["1:1"].output_type == "PANEL"
    assert by_node["1:1"].rule_id == "node.top-level-panel"
    assert by_node["1:2"].output_type == "TEXT"
    assert by_node["1:2"].confidence == 1.0


def test_ai_override_wins_and_rules_fill_missing_nodes() -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    ai_button = ClassificationDecision(
        node_id="1:1",
        output_type="BUTTON",
        rule_id="ai.semantic.v1",
        rule_version=1,
        evidence=("validated structured AI decision",),
        confidence=0.9,
        source=DecisionSource.AI,
    )

    decisions = classify_tree(
        roots,
        load_rules(Path("rules/default/classification.yaml")),
        overrides=(ai_button,),
    )
    by_node = {decision.node_id: decision for decision in decisions}

    assert by_node["1:1"].source is DecisionSource.AI
    assert by_node["1:2"].source is DecisionSource.RULE
