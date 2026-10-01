from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from figma_to_fgui.models import (
    ClassificationDecision,
    DecisionSource,
    Diagnostic,
    FrozenModel,
    SemanticType,
)
from figma_to_fgui.semantic_names import SEMANTIC_NAME_PATTERN

__all__ = [
    "DecisionSource",
    "ReparentSuggestion",
    "SemanticAnalysisOutcome",
    "SemanticDecision",
    "SemanticResponse",
    "SemanticType",
]


class ReparentSuggestion(FrozenModel):
    new_parent: str = Field(min_length=1, max_length=160)


class SemanticDecision(FrozenModel):
    node_id: str = Field(min_length=1, max_length=160)
    semantic_type: SemanticType
    fgui_name: str | None = Field(
        default=None,
        pattern=SEMANTIC_NAME_PATTERN,
    )
    children_roles: dict[str, Literal["title", "icon", "bar", "grip", "bg"]] = Field(
        default_factory=dict
    )
    state_pages: dict[str, str] = Field(default_factory=dict)
    reparent: ReparentSuggestion | None = None
    confidence: float = Field(ge=0, le=1)
    risks: tuple[str, ...] = ()


class _ScreenshotReasonModel(FrozenModel):
    screenshot_reason: str | None = Field(default=None, max_length=240)

    @field_validator("screenshot_reason", mode="before")
    @classmethod
    def normalize_screenshot_reason(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_consistent_screenshot_signal(self) -> Self:
        recommended = bool(getattr(self, "screenshot_recommended", False))
        if recommended != (self.screenshot_reason is not None):
            raise ValueError("screenshot recommendation requires exactly one safe reason")
        return self


class SemanticResponse(_ScreenshotReasonModel):
    version: Literal[1] = 1
    decisions: tuple[SemanticDecision, ...]
    screenshot_recommended: bool = False


class SemanticAnalysisOutcome(_ScreenshotReasonModel):
    overrides: tuple[ClassificationDecision, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    used_fallback: bool = False
    screenshot_recommended: bool = False
