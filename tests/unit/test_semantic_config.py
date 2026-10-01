from __future__ import annotations

import httpx
import pytest

from figma_to_fgui.models import Bounds, ClassificationDecision, NormalizedNode
from figma_to_fgui.semantic_config import (
    SemanticConfigurationError,
    build_semantic_analyzer,
    load_semantic_service_settings,
)


def _enabled_environment() -> dict[str, str]:
    return {
        "AI_SEMANTIC_ENABLED": "true",
        "AI_SEMANTIC_PROVIDER": "openai_compatible",
        "AI_SEMANTIC_BASE_URL": "https://ai.example.test/v1",
        "AI_SEMANTIC_MODEL": "semantic-model",
        "AI_SEMANTIC_API_KEY": "private-api-key",
        "AI_SEMANTIC_TIMEOUT_SECONDS": "17.5",
        "AI_SEMANTIC_CONFIDENCE_THRESHOLD": "0.8",
        "AI_SEMANTIC_MAX_RETRIES": "3",
        "AI_SEMANTIC_MAX_CONCURRENCY": "7",
    }


def test_disabled_semantic_service_needs_no_credentials_and_builds_no_analyzer() -> None:
    settings = load_semantic_service_settings({"AI_SEMANTIC_ENABLED": "false"})

    assert settings.enabled is False
    assert build_semantic_analyzer(settings) is None


def test_enabled_semantic_service_assembles_every_explicit_setting() -> None:
    settings = load_semantic_service_settings(_enabled_environment())

    assert settings.enabled is True
    assert settings.provider == "openai_compatible"
    assert str(settings.base_url) == "https://ai.example.test/v1"
    assert settings.model == "semantic-model"
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "private-api-key"
    assert settings.timeout_seconds == 17.5
    assert settings.confidence_threshold == 0.8
    assert settings.max_retries == 3
    assert settings.max_concurrency == 7


@pytest.mark.parametrize(
    "missing",
    [
        "AI_SEMANTIC_PROVIDER",
        "AI_SEMANTIC_BASE_URL",
        "AI_SEMANTIC_MODEL",
        "AI_SEMANTIC_API_KEY",
    ],
)
def test_enabled_semantic_service_fails_closed_when_required_field_is_missing(
    missing: str,
) -> None:
    environment = _enabled_environment()
    environment.pop(missing)

    with pytest.raises(SemanticConfigurationError) as error:
        load_semantic_service_settings(environment)

    assert missing in str(error.value)
    assert "private-api-key" not in str(error.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AI_SEMANTIC_ENABLED", "sometimes"),
        ("AI_SEMANTIC_PROVIDER", "other"),
        ("AI_SEMANTIC_BASE_URL", "http://ai.example.test/v1"),
        ("AI_SEMANTIC_TIMEOUT_SECONDS", "0"),
        ("AI_SEMANTIC_CONFIDENCE_THRESHOLD", "1.1"),
        ("AI_SEMANTIC_MAX_RETRIES", "-1"),
        ("AI_SEMANTIC_MAX_RETRIES", "6"),
        ("AI_SEMANTIC_MAX_CONCURRENCY", "0"),
        ("AI_SEMANTIC_MAX_CONCURRENCY", "33"),
    ],
)
def test_enabled_semantic_service_rejects_invalid_values_without_exposing_key(
    name: str, value: str
) -> None:
    environment = _enabled_environment()
    environment[name] = value

    with pytest.raises(SemanticConfigurationError) as error:
        load_semantic_service_settings(environment)

    assert "private-api-key" not in str(error.value)


def test_configured_analyzer_applies_confidence_threshold() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"version":1,"decisions":['
                        '{"node_id":"node","semantic_type":"Panel","confidence":0.79}'
                        "]}"
                    )
                }
            }
        ]
    }
    analyzer = build_semantic_analyzer(
        load_semantic_service_settings(_enabled_environment()),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response)),
    )
    assert analyzer is not None
    roots = (
        NormalizedNode(
            id="node",
            name="Checkout",
            type="RECTANGLE",
            bounds=Bounds(x=0, y=0, width=20, height=10),
        ),
    )

    rule_candidate = ClassificationDecision(
        node_id="node",
        output_type="PANEL",
        rule_id="rule.panel",
        rule_version=1,
        evidence=("rectangle",),
        confidence=1.0,
    )

    outcome = analyzer.analyze(roots, rule_candidates=(rule_candidate,))

    assert outcome.overrides == ()
    assert outcome.used_fallback is False
    assert outcome.screenshot_recommended is True
    assert outcome.screenshot_reason == (
        "Structure-only analysis was below the configured confidence threshold."
    )
