from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, cast

from pydantic import ConfigDict, Field, field_validator, model_validator

from figma_to_fgui.data_policy import freeze_json_value, freeze_mapping
from figma_to_fgui.models import Bounds, Diagnostic, FrozenModel

SHA256_PATTERN = r"^[0-9a-f]{64}$"
NonBlankString = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]


class PlanModel(FrozenModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )


class CapabilityStatus(StrEnum):
    NATIVE = "native"
    RASTER_FALLBACK = "rasterFallback"
    UNSUPPORTED = "unsupported"


class PlanNodeType(StrEnum):
    CONTAINER = "container"
    GRAPH = "graph"
    TEXT = "text"
    RICH_TEXT = "richText"
    IMAGE = "image"
    LOADER = "loader"
    COMPONENT_REFERENCE = "componentReference"
    RASTER_SUBTREE = "rasterSubtree"


class MaskMode(StrEnum):
    NATIVE_CLIP = "nativeClip"
    NATIVE_MASK = "nativeMask"
    RASTER_SUBTREE = "rasterSubtree"


class MaskKind(StrEnum):
    RECTANGLE = "rectangle"
    ROUNDED_RECTANGLE = "roundedRectangle"
    IMAGE = "image"
    BOOLEAN = "boolean"
    GRADIENT = "gradient"
    BLUR = "blur"
    BLEND = "blend"


class TransformPlan(PlanModel):
    bounds: Bounds
    rotation: float = 0
    opacity: float = Field(default=1, ge=0, le=1)
    visible: bool = True


class GraphPlan(PlanModel):
    shape: Literal["rect", "ellipse"]
    fill_color: str | None = Field(default=None, alias="fillColor", pattern=r"^#[0-9a-f]{8}$")
    line_color: str | None = Field(default=None, alias="lineColor", pattern=r"^#[0-9a-f]{6}$")
    line_size: float = Field(default=0, alias="lineSize", ge=0)
    corner_radius: float | None = Field(default=None, alias="cornerRadius", ge=0)
    corner_radii: tuple[float, float, float, float] | None = Field(
        default=None, alias="cornerRadii"
    )

    @model_validator(mode="after")
    def validate_corners(self) -> GraphPlan:
        if self.corner_radius is not None and self.corner_radii is not None:
            raise ValueError("graph corners must use one typed representation")
        if self.corner_radii is not None and any(value < 0 for value in self.corner_radii):
            raise ValueError("graph corner radii must be nonnegative")
        return self


class TextRunPlan(PlanModel):
    content: str
    font_candidates: tuple[NonBlankString, ...] = Field(default=(), alias="fontCandidates")
    font_size: float | None = Field(default=None, alias="fontSize", gt=0)
    line_height: float | None = Field(default=None, alias="lineHeight", gt=0)
    color: str | None = None
    stroke_color: str | None = Field(default=None, alias="strokeColor")
    stroke_size: float | None = Field(default=None, alias="strokeSize", gt=0)


class TextPlan(PlanModel):
    content: str
    font_candidates: tuple[NonBlankString, ...] = Field(default=(), alias="fontCandidates")
    font_size: float | None = Field(default=None, alias="fontSize", gt=0)
    line_height: float | None = Field(default=None, alias="lineHeight", gt=0)
    color: str | None = None
    stroke_color: str | None = Field(default=None, alias="strokeColor")
    stroke_size: float | None = Field(default=None, alias="strokeSize", gt=0)
    horizontal_align: str | None = Field(default=None, alias="horizontalAlign")
    vertical_align: str | None = Field(default=None, alias="verticalAlign")
    auto_resize: str | None = Field(
        default=None,
        alias="textAutoResize",
        exclude_if=lambda value: value is None,
    )
    runs: tuple[TextRunPlan, ...] = ()
    style_facts: dict[str, object] = Field(default_factory=dict, alias="styleFacts")

    @field_validator("style_facts", mode="after")
    @classmethod
    def freeze_style_facts(cls, value: dict[str, object]) -> dict[str, object]:
        return cast(dict[str, object], freeze_json_value(value))


class NineSlicePlan(PlanModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ResourcePlan(PlanModel):
    id: NonBlankString
    source_asset_ref: NonBlankString = Field(alias="sourceAssetRef")
    logical_asset_id: NonBlankString = Field(alias="logicalAssetId")
    content_sha256: str | None = Field(default=None, alias="contentSha256", pattern=SHA256_PATTERN)
    export_parameters_sha256: str = Field(
        alias="exportParametersSha256", pattern=SHA256_PATTERN
    )
    mime_type: NonBlankString = Field(alias="mimeType")
    export_format: Literal["png", "jpg", "webp", "svg"] = Field(alias="exportFormat")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    nine_slice: NineSlicePlan | None = Field(default=None, alias="nineSlice")
    consumers: tuple[NonBlankString, ...]
    reason: str | None = None


class ComponentReferenceV1Plan(PlanModel):
    candidate_key: NonBlankString = Field(alias="candidateKey")
    variant_properties: dict[str, str] = Field(default_factory=dict, alias="variantProperties")
    overrides: dict[str, object] = Field(default_factory=dict)

    @field_validator("variant_properties", "overrides", mode="after")
    @classmethod
    def freeze_component_facts(cls, value: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], freeze_json_value(value))


class ComponentReferencePlan(ComponentReferenceV1Plan):
    definition_ref: NonBlankString = Field(alias="definitionRef")
    variant_properties: dict[NonBlankString, NonBlankString] = Field(
        default_factory=dict, alias="variantProperties"
    )


class MaskPlan(PlanModel):
    id: NonBlankString
    mode: MaskMode
    kind: MaskKind
    mask_node_ref: NonBlankString = Field(alias="maskNodeRef")
    content_node_refs: tuple[NonBlankString, ...] = Field(alias="contentNodeRefs")
    resource_ref: NonBlankString | None = Field(default=None, alias="resourceRef")
    corner_radii: tuple[float, float, float, float] | None = Field(
        default=None, alias="cornerRadii"
    )

    @field_validator("corner_radii", mode="after")
    @classmethod
    def validate_corner_radii(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is not None and any(item < 0 for item in value):
            raise ValueError("mask corner radii must be nonnegative")
        return value


class CapabilityDecision(PlanModel):
    id: NonBlankString
    node_ref: NonBlankString = Field(alias="nodeRef")
    status: CapabilityStatus
    rule_id: NonBlankString = Field(alias="ruleId")
    rule_version: int = Field(alias="ruleVersion", ge=1)
    evidence: tuple[NonBlankString, ...] = ()
    reasons: tuple[str, ...] = ()
    blocking: bool = False


class _FGUIPlanNodeBase(PlanModel):
    id: NonBlankString
    uir_node_ref: NonBlankString = Field(alias="uirNodeRef")
    parent_id: NonBlankString | None = Field(default=None, alias="parentId")
    children: tuple[NonBlankString, ...] = ()
    z_index: int = Field(alias="zIndex", ge=0)
    type: PlanNodeType
    transform: TransformPlan
    text: TextPlan | None = None
    graph: GraphPlan | None = Field(default=None, exclude_if=lambda value: value is None)
    background_graph: GraphPlan | None = Field(
        default=None, alias="backgroundGraph", exclude_if=lambda value: value is None
    )
    resource_ref: NonBlankString | None = Field(default=None, alias="resourceRef")
    mask_ref: NonBlankString | None = Field(default=None, alias="maskRef")
    decision_ref: NonBlankString | None = Field(default=None, alias="decisionRef")


class FGUIPlanNode(_FGUIPlanNodeBase):
    component: ComponentReferencePlan | None = None


class FGUIPlanV1Node(_FGUIPlanNodeBase):
    component: ComponentReferenceV1Plan | None = None


class ComponentDefinitionPlan(PlanModel):
    id: NonBlankString
    name: NonBlankString
    root_node_ref: NonBlankString = Field(alias="rootNodeRef")
    nodes: dict[str, FGUIPlanNode]

    @field_validator("nodes", mode="after")
    @classmethod
    def freeze_nodes(cls, value: dict[str, FGUIPlanNode]) -> dict[str, FGUIPlanNode]:
        return freeze_mapping(value)


class FGUIPlanV1Document(PlanModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    document_id: NonBlankString = Field(alias="documentId")
    source_uir_sha256: str = Field(alias="sourceUirSha256", pattern=SHA256_PATTERN)
    profile_version: NonBlankString = Field(alias="profileVersion")
    rule_version: int = Field(alias="ruleVersion", ge=1)
    bindable: bool
    roots: tuple[NonBlankString, ...]
    nodes: dict[str, FGUIPlanV1Node]
    resources: dict[str, ResourcePlan] = Field(default_factory=dict)
    masks: dict[str, MaskPlan] = Field(default_factory=dict)
    decisions: dict[str, CapabilityDecision] = Field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()

    @field_validator("nodes", "resources", "masks", "decisions", mode="after")
    @classmethod
    def freeze_document_mappings(cls, value: dict[str, Any]) -> dict[str, Any]:
        return freeze_mapping(value)


class FGUIPlanDocument(PlanModel):
    schema_version: Literal[2] = Field(default=2, alias="schemaVersion")
    document_id: NonBlankString = Field(alias="documentId")
    source_uir_sha256: str = Field(alias="sourceUirSha256", pattern=SHA256_PATTERN)
    profile_version: NonBlankString = Field(alias="profileVersion")
    rule_version: int = Field(alias="ruleVersion", ge=1)
    bindable: bool
    roots: tuple[NonBlankString, ...]
    nodes: dict[str, FGUIPlanNode]
    component_definitions: dict[str, ComponentDefinitionPlan] = Field(
        default_factory=dict, alias="componentDefinitions"
    )
    resources: dict[str, ResourcePlan] = Field(default_factory=dict)
    masks: dict[str, MaskPlan] = Field(default_factory=dict)
    decisions: dict[str, CapabilityDecision] = Field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()

    @field_validator(
        "nodes", "component_definitions", "resources", "masks", "decisions", mode="after"
    )
    @classmethod
    def freeze_document_mappings(cls, value: dict[str, Any]) -> dict[str, Any]:
        return freeze_mapping(value)


def migrate_plan_v1_without_components(plan: FGUIPlanV1Document) -> FGUIPlanDocument:
    """Explicitly migrate a v1 plan only when it has no component references."""
    if any(node.type == PlanNodeType.COMPONENT_REFERENCE for node in plan.nodes.values()):
        raise ValueError("Plan v1 with component references must be recompiled")
    payload = plan.model_dump(mode="json", by_alias=True)
    payload["schemaVersion"] = 2
    payload["componentDefinitions"] = {}
    return FGUIPlanDocument.model_validate(payload)
