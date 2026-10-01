import pytest
from pydantic import ValidationError

from figma_to_fgui.models import ClassificationDecision, DecisionSource
from figma_to_fgui.semantic_models import (
    ReparentSuggestion,
    SemanticAnalysisOutcome,
    SemanticDecision,
    SemanticResponse,
    SemanticType,
)


def test_semantic_response_rejects_unknown_type_and_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticResponse.model_validate(
            {
                "version": 1,
                "decisions": [
                    {
                        "node_id": "1:2",
                        "semantic_type": "SCRIPT",
                        "confidence": 0.9,
                        "unexpected": "value",
                    }
                ],
            }
        )


def test_semantic_decision_accepts_bounded_safe_fields() -> None:
    decision = SemanticDecision.model_validate(
        {
            "node_id": "1:2",
            "semantic_type": "Button",
            "fgui_name": "SubmitButton",
            "children_roles": {"1:3": "title"},
            "state_pages": {"0": "normal"},
            "confidence": 0.91,
            "risks": ["hover state absent"],
        }
    )

    assert decision.semantic_type is SemanticType.BUTTON


def test_semantic_models_are_frozen_and_reparent_target_is_bounded() -> None:
    suggestion = ReparentSuggestion(new_parent="1:1")
    decision = SemanticDecision(node_id="1:2", semantic_type="Panel", confidence=0, reparent=suggestion)

    with pytest.raises(ValidationError):
        decision.confidence = 0.9
    with pytest.raises(ValidationError):
        ReparentSuggestion(new_parent="")


def test_semantic_analysis_outcome_has_only_safe_transport_fields() -> None:
    outcome = SemanticAnalysisOutcome(
        screenshot_recommended=True,
        screenshot_reason="Need visual context for ambiguous layers.",
    )

    assert outcome.overrides == ()
    assert outcome.diagnostics == ()
    with pytest.raises(ValidationError):
        SemanticAnalysisOutcome.model_validate({"unexpected": "value"})


@pytest.mark.parametrize("model", [SemanticResponse, SemanticAnalysisOutcome])
def test_screenshot_flag_and_reason_must_be_consistent(model: object) -> None:
    payload: dict[str, object] = {
        "screenshot_recommended": True,
        "screenshot_reason": "  Need visual context.  ",
    }
    if model is SemanticResponse:
        payload["decisions"] = []

    parsed = model.model_validate(payload)  # type: ignore[attr-defined]
    assert parsed.screenshot_reason == "Need visual context."

    payload["screenshot_reason"] = "   "
    with pytest.raises(ValidationError):
        model.model_validate(payload)  # type: ignore[attr-defined]

    payload["screenshot_recommended"] = False
    payload["screenshot_reason"] = "Not allowed without the flag."
    with pytest.raises(ValidationError):
        model.model_validate(payload)  # type: ignore[attr-defined]


def test_classification_decision_tracks_semantic_source_and_name() -> None:
    decision = ClassificationDecision(
        node_id="1:2",
        output_type="PANEL",
        rule_id="ai.semantic.v1",
        rule_version=1,
        evidence=("validated structured AI decision",),
        confidence=0.91,
        source=DecisionSource.AI,
        semantic_name="SubmitButton",
        semantic_type=SemanticType.BUTTON,
    )

    assert decision.source is DecisionSource.AI
    assert decision.semantic_name == "SubmitButton"
    assert decision.semantic_type is SemanticType.BUTTON
