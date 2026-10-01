from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, cast

from pydantic import ConfigDict, Field, field_validator, model_validator

from figma_to_fgui.data_policy import freeze_json_value, freeze_mapping
from figma_to_fgui.models import Bounds, Diagnostic, FrozenModel

SHA256_PATTERN = r"^[0-9a-f]{64}$"
NonBlankString = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]


class UIRModel(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class SemanticStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    FALLBACK = "fallback"


class ConversionMode(StrEnum):
    NATIVE = "native"
    COMPONENT_REFERENCE = "componentReference"
    EXISTING_RESOURCE = "existingResource"
    RASTER_FALLBACK = "rasterFallback"
    UNSUPPORTED = "unsupported"


class MappingStatus(StrEnum):
    VERIFIED = "verified"
    MISSING = "missing"
    CONFLICT = "conflict"


class UIRSource(UIRModel):
    kind: Literal["figma"] = "figma"
    revision: str = Field(pattern=SHA256_PATTERN)
    selection_id: NonBlankString = Field(alias="selectionId", max_length=256)


class UIRNodeSource(UIRModel):
    node_id: NonBlankString = Field(alias="nodeId", max_length=128)
    type: NonBlankString = Field(max_length=64)
    name: str = Field(max_length=256)
    fingerprint: str = Field(pattern=SHA256_PATTERN)


class UIRGeometry(UIRModel):
    resolved_bounds: Bounds = Field(alias="resolvedBounds")
    local_transform: tuple[float, float, float, float, float, float] | None = Field(
        default=None, alias="localTransform"
    )
    rotation: float = 0
    opacity: float = Field(default=1, ge=0, le=1)


class UIRSemantic(UIRModel):
    name: NonBlankString | None = Field(default=None, max_length=128)
    role: NonBlankString | None = Field(default=None, max_length=64)
    status: SemanticStatus = SemanticStatus.CANDIDATE
    decision_ref: NonBlankString | None = Field(default=None, alias="decisionRef")


class UIRConversion(UIRModel):
    mode: ConversionMode
    reasons: tuple[NonBlankString, ...] = ()
    asset_ref: NonBlankString | None = Field(default=None, alias="assetRef")


class UIRComponentInstance(UIRModel):
    definition_ref: NonBlankString | None = Field(default=None, alias="definitionRef")
    variant_properties: dict[str, str] = Field(
        default_factory=dict, alias="variantProperties"
    )
    overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("variant_properties", "overrides", mode="after")
    @classmethod
    def freeze_component_facts(cls, value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], freeze_json_value(value))


class UIRTextStyle(UIRModel):
    font_candidates: tuple[NonBlankString, ...] = Field(
        default=(), alias="fontCandidates"
    )
    font_size: float | None = Field(default=None, alias="fontSize", gt=0)
    line_height: float | None = Field(default=None, alias="lineHeight", gt=0)
    color: str | None = Field(default=None, max_length=64)
    stroke_color: str | None = Field(default=None, alias="strokeColor", max_length=64)
    stroke_size: float | None = Field(default=None, alias="strokeSize", gt=0)
    horizontal_align: str | None = Field(
        default=None, alias="textAlignHorizontal", max_length=32
    )
    vertical_align: str | None = Field(
        default=None, alias="textAlignVertical", max_length=32
    )
    auto_resize: str | None = Field(
        default=None,
        alias="textAutoResize",
        max_length=32,
        exclude_if=lambda value: value is None,
    )


class UIRTextRun(UIRModel):
    content: str
    style: UIRTextStyle = Field(default_factory=UIRTextStyle)
    unsupported_features: tuple[str, ...] = Field(
        default=(), alias="unsupportedFeatures"
    )


class UIFontPolicy(UIRModel):
    allow_fallback: bool = Field(default=True, alias="allowFallback")
    resolved_font: NonBlankString | None = Field(
        default=None, alias="resolvedFont", max_length=256
    )


class UIRText(UIRModel):
    content: str
    style: UIRTextStyle = Field(default_factory=UIRTextStyle)
    runs: tuple[UIRTextRun, ...] = ()
    base_unsupported_features: tuple[str, ...] = Field(
        default=(), alias="baseUnsupportedFeatures"
    )
    font_policy: UIFontPolicy = Field(default_factory=UIFontPolicy, alias="fontPolicy")

    @model_validator(mode="after")
    def validate_runs_cover_content(self) -> UIRText:
        if self.runs and "".join(run.content for run in self.runs) != self.content:
            raise ValueError("text runs must reproduce the complete text content")
        return self


class UIRNode(UIRModel):
    id: NonBlankString = Field(max_length=128)
    source: UIRNodeSource
    semantic: UIRSemantic
    parent_id: NonBlankString | None = Field(default=None, alias="parentId")
    children: tuple[NonBlankString, ...] = ()
    z_index: int = Field(alias="zIndex", ge=0)
    geometry: UIRGeometry
    layout: dict[str, Any] = Field(default_factory=dict)
    visual: dict[str, Any] = Field(default_factory=dict)
    text: UIRText | None = None
    component: UIRComponentInstance | None = None
    interactions: tuple[dict[str, Any], ...] = ()
    conversion: UIRConversion

    @field_validator("layout", "visual", mode="after")
    @classmethod
    def freeze_fact_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], freeze_json_value(value))

    @field_validator("interactions", mode="after")
    @classmethod
    def freeze_interactions(
        cls, value: tuple[dict[str, Any], ...]
    ) -> tuple[dict[str, Any], ...]:
        return tuple(freeze_json_value(item) for item in value)


class UIRComponentDefinition(UIRModel):
    id: NonBlankString = Field(max_length=128)
    source_node_id: NonBlankString = Field(alias="sourceNodeId", max_length=128)
    name: str = Field(max_length=256)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("properties", mode="after")
    @classmethod
    def freeze_properties(cls, value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], freeze_json_value(value))


class UIRNineSlice(UIRModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class UIRAsset(UIRModel):
    id: NonBlankString = Field(max_length=128)
    logical_id: NonBlankString = Field(alias="logicalId", max_length=256)
    mime_type: NonBlankString = Field(alias="mimeType", max_length=128)
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    source_node_id: NonBlankString | None = Field(
        default=None, alias="sourceNodeId", max_length=128
    )
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    nine_slice: UIRNineSlice | None = Field(default=None, alias="nineSlice")
    export_format: Literal["png", "jpg", "webp", "svg"] = Field(
        default="png", alias="exportFormat"
    )


class UIRMappingDecision(UIRModel):
    id: NonBlankString = Field(max_length=128)
    node_ref: NonBlankString = Field(alias="nodeRef", max_length=128)
    candidate_key: NonBlankString = Field(alias="candidateKey", max_length=128)
    status: MappingStatus
    evidence: tuple[NonBlankString, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    rule_source: NonBlankString = Field(alias="ruleSource", max_length=256)
    human_decision: str | None = Field(
        default=None, alias="humanDecision", max_length=256
    )


class UIRDocument(UIRModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    document_id: NonBlankString = Field(alias="documentId", max_length=128)
    compiler_version: NonBlankString = Field(alias="compilerVersion", max_length=64)
    source: UIRSource
    roots: tuple[NonBlankString, ...]
    nodes: dict[str, UIRNode]
    component_definitions: dict[str, UIRComponentDefinition] = Field(
        default_factory=dict, alias="componentDefinitions"
    )
    assets: dict[str, UIRAsset] = Field(default_factory=dict)
    mapping_decisions: dict[str, UIRMappingDecision] = Field(
        default_factory=dict, alias="mappingDecisions"
    )
    diagnostics: tuple[Diagnostic, ...] = ()

    @field_validator(
        "nodes",
        "component_definitions",
        "assets",
        "mapping_decisions",
        mode="after",
    )
    @classmethod
    def freeze_document_mappings(cls, value: dict[str, Any]) -> dict[str, Any]:
        return freeze_mapping(value)
