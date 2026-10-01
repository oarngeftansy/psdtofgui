from __future__ import annotations

import httpx

from figma_to_fgui.ai_client import (
    MAX_SUMMARY_DEPTH,
    MAX_SUMMARY_NODES,
    AIAnalysisError,
    AIClientConfig,
    AIReasonCode,
    OpenAICompatibleSemanticClient,
)
from figma_to_fgui.models import (
    Bounds,
    ClassificationDecision,
    DecisionSource,
    NormalizedNode,
    Severity,
)
from figma_to_fgui.semantic_analysis import analyze_semantics, build_selection_summary
from figma_to_fgui.semantic_models import SemanticDecision, SemanticResponse


def _roots() -> tuple[NormalizedNode, ...]:
    return (
        NormalizedNode(
            id="root",
            name="Main",
            type="GROUP",
            bounds=Bounds(x=0, y=0, width=100, height=100),
            rotation=12.5,
            source_order=3,
            children=(
                NormalizedNode(
                    id="button",
                    name="Submit",
                    type="TEXT",
                    bounds=Bounds(x=10, y=10, width=40, height=20),
                    text="Pay now",
                    source_order=7,
                    properties={"State": "Default", "api_key": "must-not-leave-server"},
                    raw_style={
                        "fontSize": 16,
                        "textAlignHorizontal": "CENTER",
                        "private": "must-not-leave-server",
                        "resourceRefs": ({"asset": "secret-asset"},),
                    },
                ),
            ),
        ),
    )


def test_summary_includes_bounded_semantic_evidence_without_raw_unknown_metadata() -> None:
    summary = build_selection_summary(_roots())

    assert summary == {
        "version": 1,
        "nodes": [
            {
                "id": "root",
                "parent_id": None,
                "name": "Main",
                "type": "GROUP",
                "bounds": {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0},
                "rotation": 12.5,
                "source_order": 3,
            },
            {
                "id": "button",
                "parent_id": "root",
                "name": "Submit",
                "type": "TEXT",
                "bounds": {"x": 10.0, "y": 10.0, "width": 40.0, "height": 20.0},
                "rotation": 0.0,
                "source_order": 7,
                "text": "Pay now",
                "properties": {"State": "Default"},
                "style": {"fontSize": 16, "textAlignHorizontal": "CENTER"},
            },
        ],
    }
    encoded = str(summary)
    assert "api_key" not in encoded
    assert "must-not-leave-server" not in encoded
    assert "resourceRefs" not in encoded
    assert "secret-asset" not in encoded


def test_summary_strictly_bounds_allowed_strings_and_style_collections() -> None:
    long_value = "界" * 300
    roots = (
        NormalizedNode(
            id="bounded",
            name=long_value,
            type="TEXT",
            bounds=Bounds(x=0, y=0, width=1, height=1),
            text=long_value,
            properties={"State": long_value},
            raw_style={
                "textAlignHorizontal": long_value,
                "fills": tuple(
                    {"type": "SOLID", "opacity": 0.5, "unknown": long_value}
                    for _ in range(10)
                ),
            },
        ),
    )

    node = build_selection_summary(roots)["nodes"][0]  # type: ignore[index]

    assert len(node["name"].encode("utf-8")) <= 256
    assert len(node["text"].encode("utf-8")) <= 256
    assert len(node["properties"]["State"].encode("utf-8")) <= 96
    assert len(node["style"]["textAlignHorizontal"].encode("utf-8")) <= 96
    assert len(node["style"]["fills"]) == 4
    assert "unknown" not in str(node["style"])


def test_analysis_without_client_uses_deterministic_warning() -> None:
    outcome = analyze_semantics(_roots(), None)

    assert outcome.overrides == ()
    assert outcome.used_fallback is True
    assert [(item.code, item.severity, item.message) for item in outcome.diagnostics] == [
        ("ai.disabled", Severity.WARNING, "AI semantic analysis was unavailable; rule classification remains active.")
    ]


def test_analysis_falls_back_without_exposing_node_text() -> None:
    class FailingClient:
        def analyze(self, summary: dict[str, object]) -> SemanticResponse:
            raise AIAnalysisError(AIReasonCode.TRANSPORT)

    outcome = analyze_semantics(_roots(), FailingClient())

    assert outcome.overrides == ()
    assert outcome.used_fallback is True
    assert outcome.diagnostics[0].code == "ai.transport"
    assert "private node text" not in outcome.diagnostics[0].message


def test_analysis_validates_client_response_and_preserves_screenshot_signal() -> None:
    class Client:
        def analyze(self, summary: dict[str, object]) -> SemanticResponse:
            assert summary["nodes"]
            return SemanticResponse(
                decisions=(SemanticDecision(node_id="button", semantic_type="Text", confidence=0.9),),
                screenshot_recommended=True,
                screenshot_reason="Ambiguous grouping.",
            )

    outcome = analyze_semantics(
        _roots(),
        Client(),
        rule_candidates=(
            ClassificationDecision(
                node_id="button",
                output_type="TEXT",
                rule_id="node.text",
                rule_version=1,
                evidence=("type=TEXT",),
                confidence=1,
            ),
        ),
    )

    assert [item.node_id for item in outcome.overrides] == ["button"]
    assert outcome.diagnostics == ()
    assert outcome.used_fallback is False
    assert outcome.screenshot_recommended is True
    assert outcome.screenshot_reason == "Ambiguous grouping."


def test_analysis_sends_deterministic_rule_candidates_as_semantic_evidence() -> None:
    candidates = (
        ClassificationDecision(
            node_id="root",
            output_type="INLINE",
            rule_id="node.inline-fallback",
            rule_version=1,
            evidence=("fallback",),
            confidence=0.5,
            source=DecisionSource.RULE,
        ),
        ClassificationDecision(
            node_id="button",
            output_type="TEXT",
            rule_id="node.text",
            rule_version=1,
            evidence=("type=TEXT",),
            confidence=1,
            source=DecisionSource.RULE,
        ),
    )

    class Client:
        def analyze(self, summary: dict[str, object]) -> SemanticResponse:
            nodes = {str(item["id"]): item for item in summary["nodes"]}  # type: ignore[index]
            assert nodes["root"]["rule_candidate"] == {
                "output_type": "INLINE",
                "evidence": ["fallback"],
                "confidence": 0.5,
            }
            assert nodes["button"]["rule_candidate"] == {
                "output_type": "TEXT",
                "evidence": ["type=TEXT"],
                "confidence": 1.0,
            }
            return SemanticResponse(decisions=())

    outcome = analyze_semantics(_roots(), Client(), rule_candidates=candidates)

    assert outcome.used_fallback is False


def test_analysis_falls_back_when_summary_contains_non_finite_geometry() -> None:
    roots = (
        NormalizedNode(
            id="bad",
            name="Bad",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=1, height=1).model_copy(
                update={"x": float("nan")}
            ),
        ),
    )
    requests: list[httpx.Request] = []
    client = OpenAICompatibleSemanticClient(
        AIClientConfig(
            provider="openai",
            base_url="https://ai.example.test/v1",
            model="model",
            api_key="secret-value",
        ),
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(500)
        ),
    )

    outcome = analyze_semantics(roots, client)

    assert outcome.overrides == ()
    assert outcome.diagnostics[0].code == AIReasonCode.REQUEST_INVALID
    assert requests == []


def test_untrusted_error_code_cannot_become_a_diagnostic_code() -> None:
    class UntrustedClient:
        def analyze(self, summary: dict[str, object]) -> SemanticResponse:
            raise AIAnalysisError("attacker.controlled")  # type: ignore[arg-type]

    outcome = analyze_semantics(_roots(), UntrustedClient())

    assert outcome.diagnostics[0].code == AIReasonCode.INTERNAL
    assert "attacker" not in outcome.model_dump_json()


def test_analysis_falls_back_for_overdeep_response_json() -> None:
    overdeep_json = "[" * 5_000 + '"private response"' + "]" * 5_000
    client = OpenAICompatibleSemanticClient(
        AIClientConfig(
            provider="openai",
            base_url="https://ai.example.test/v1",
            model="model",
            api_key="secret-value",
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=overdeep_json.encode("utf-8"))
        ),
    )

    outcome = analyze_semantics(_roots(), client)

    assert outcome.overrides == ()
    assert outcome.diagnostics[0].code == AIReasonCode.RESPONSE_JSON
    assert "private response" not in outcome.model_dump_json()


def test_overdeep_normalized_tree_falls_back_before_calling_client() -> None:
    node = NormalizedNode(
        id="leaf",
        name="Leaf",
        type="RECTANGLE",
        bounds=Bounds(x=0, y=0, width=1, height=1),
    )
    for depth in range(MAX_SUMMARY_DEPTH + 1):
        node = NormalizedNode(
            id=f"node-{depth}",
            name="Private deep node",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=1, height=1),
            children=(node,),
        )
    calls: list[dict[str, object]] = []

    class RecordingClient:
        def analyze(self, summary: dict[str, object]) -> SemanticResponse:
            calls.append(summary)
            return SemanticResponse(decisions=())

    outcome = analyze_semantics((node,), RecordingClient())

    assert outcome.overrides == ()
    assert outcome.diagnostics[0].code == AIReasonCode.REQUEST_INVALID
    assert "Private deep node" not in outcome.model_dump_json()
    assert calls == []


def test_too_many_normalized_nodes_fall_back_before_calling_client() -> None:
    roots = tuple(
        NormalizedNode(
            id=f"node-{index}",
            name=f"Private {index}",
            type="RECTANGLE",
            bounds=Bounds(x=index, y=0, width=1, height=1),
            source_order=MAX_SUMMARY_NODES - index,
        )
        for index in range(MAX_SUMMARY_NODES + 1)
    )
    calls: list[dict[str, object]] = []

    class RecordingClient:
        def analyze(self, summary: dict[str, object]) -> SemanticResponse:
            calls.append(summary)
            return SemanticResponse(decisions=())

    outcome = analyze_semantics(roots, RecordingClient())

    assert outcome.overrides == ()
    assert outcome.diagnostics[0].code == AIReasonCode.REQUEST_INVALID
    assert calls == []


def test_summary_preserves_root_and_child_tuple_order() -> None:
    first_child = NormalizedNode(
        id="first-child",
        name="First child",
        type="RECTANGLE",
        bounds=Bounds(x=0, y=0, width=1, height=1),
        source_order=9,
    )
    second_child = NormalizedNode(
        id="second-child",
        name="Second child",
        type="RECTANGLE",
        bounds=Bounds(x=0, y=0, width=1, height=1),
        source_order=1,
    )
    first_root = NormalizedNode(
        id="first-root",
        name="First root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=1, height=1),
        children=(first_child, second_child),
        source_order=8,
    )
    second_root = NormalizedNode(
        id="second-root",
        name="Second root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=1, height=1),
        source_order=0,
    )

    summary = build_selection_summary((first_root, second_root))

    assert [node["id"] for node in summary["nodes"]] == [
        "first-root",
        "first-child",
        "second-child",
        "second-root",
    ]
