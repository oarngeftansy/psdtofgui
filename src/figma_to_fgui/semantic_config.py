from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import httpx
from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError

from figma_to_fgui.ai_client import AIClientConfig, OpenAICompatibleSemanticClient
from figma_to_fgui.models import ClassificationDecision, FrozenModel, NormalizedNode
from figma_to_fgui.semantic_analysis import analyze_semantics
from figma_to_fgui.semantic_models import SemanticAnalysisOutcome

_ENABLED = "AI_SEMANTIC_ENABLED"
_PROVIDER = "AI_SEMANTIC_PROVIDER"
_BASE_URL = "AI_SEMANTIC_BASE_URL"
_MODEL = "AI_SEMANTIC_MODEL"
_API_KEY = "AI_SEMANTIC_API_KEY"
_TIMEOUT = "AI_SEMANTIC_TIMEOUT_SECONDS"
_CONFIDENCE = "AI_SEMANTIC_CONFIDENCE_THRESHOLD"
_MAX_RETRIES = "AI_SEMANTIC_MAX_RETRIES"
_MAX_CONCURRENCY = "AI_SEMANTIC_MAX_CONCURRENCY"
_LOW_CONFIDENCE_SCREENSHOT_REASON = (
    "Structure-only analysis was below the configured confidence threshold."
)


class SemanticConfigurationError(ValueError):
    """A safe startup failure that never includes configuration values."""


class SemanticServiceSettings(FrozenModel):
    enabled: bool = False
    provider: Literal["openai", "openai_compatible"] | None = None
    base_url: AnyHttpUrl | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=20, ge=1, le=120)
    confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_concurrency: int = Field(default=4, ge=1, le=32)

    def client_config(self) -> AIClientConfig:
        if (
            not self.enabled
            or self.provider is None
            or self.base_url is None
            or self.model is None
            or self.api_key is None
        ):
            raise SemanticConfigurationError("AI semantic service is not enabled")
        return AIClientConfig(
            provider=self.provider,
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            max_concurrency=self.max_concurrency,
        )


class ConfiguredSemanticAnalyzer:
    def __init__(
        self,
        client: OpenAICompatibleSemanticClient,
        confidence_threshold: float,
    ) -> None:
        self._client = client
        self._confidence_threshold = confidence_threshold

    def analyze(
        self,
        roots: tuple[NormalizedNode, ...],
        *,
        rule_candidates: tuple[ClassificationDecision, ...],
        screenshot: bytes | None = None,
    ) -> SemanticAnalysisOutcome:
        outcome = analyze_semantics(
            roots,
            self._client,
            screenshot=screenshot,
            rule_candidates=rule_candidates,
        )
        accepted = tuple(
            decision
            for decision in outcome.overrides
            if decision.confidence >= self._confidence_threshold
        )
        low_confidence_filtered = len(accepted) != len(outcome.overrides)
        updates: dict[str, object] = {"overrides": accepted}
        if low_confidence_filtered:
            updates.update(
                {
                    "screenshot_recommended": True,
                    "screenshot_reason": _LOW_CONFIDENCE_SCREENSHOT_REASON,
                }
            )
        return outcome.model_copy(update=updates)

    def close(self) -> None:
        self._client.close()


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise SemanticConfigurationError(f"{name} is required when AI semantics are enabled")
    return value


def _enabled_value(environment: Mapping[str, str]) -> bool:
    value = environment.get(_ENABLED, "false").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise SemanticConfigurationError(f"{_ENABLED} must be true or false")


def load_semantic_service_settings(
    environment: Mapping[str, str],
) -> SemanticServiceSettings:
    """Parse the complete AI environment once, failing closed without echoing values."""
    if not _enabled_value(environment):
        return SemanticServiceSettings(enabled=False)
    provider = _required(environment, _PROVIDER)
    base_url = _required(environment, _BASE_URL)
    model = _required(environment, _MODEL)
    api_key = _required(environment, _API_KEY)
    timeout = environment.get(_TIMEOUT, "20").strip()
    confidence = environment.get(_CONFIDENCE, "0.75").strip()
    max_retries = environment.get(_MAX_RETRIES, "2").strip()
    max_concurrency = environment.get(_MAX_CONCURRENCY, "4").strip()
    try:
        client = AIClientConfig.model_validate(
            {
                "provider": provider,
                "base_url": base_url,
                "model": model,
                "api_key": SecretStr(api_key),
                "timeout_seconds": timeout,
                "max_retries": max_retries,
                "max_concurrency": max_concurrency,
            }
        )
        return SemanticServiceSettings.model_validate(
            {
                "enabled": True,
                "provider": client.provider,
                "base_url": client.base_url,
                "model": client.model,
                "api_key": client.api_key,
                "timeout_seconds": client.timeout_seconds,
                "confidence_threshold": confidence,
                "max_retries": client.max_retries,
                "max_concurrency": client.max_concurrency,
            }
        )
    except (ValidationError, ValueError, TypeError):
        raise SemanticConfigurationError("AI semantic service configuration is invalid") from None


def build_semantic_analyzer(
    settings: SemanticServiceSettings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ConfiguredSemanticAnalyzer | None:
    if not settings.enabled:
        return None
    return ConfiguredSemanticAnalyzer(
        OpenAICompatibleSemanticClient(settings.client_config(), transport=transport),
        settings.confidence_threshold,
    )
