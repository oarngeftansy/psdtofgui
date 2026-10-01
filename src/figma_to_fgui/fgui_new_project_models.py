"""Strict, project-local contracts for building a new FairyGUI project.

These models deliberately describe only a newly generated project.  They are
not a Project Binding contract and therefore do not identify or inspect an
existing FairyGUI project.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

from pydantic import ConfigDict, Field, field_validator, model_validator

from figma_to_fgui.data_policy import freeze_json_value, freeze_mapping, private_data_violations
from figma_to_fgui.fgui_plan_models import (
    GraphPlan,
    MaskKind,
    MaskMode,
    NineSlicePlan,
    PlanNodeType,
    TextPlan,
    TransformPlan,
)
from figma_to_fgui.models import Bounds, FrozenModel

SHA256_PATTERN = r"^[0-9a-f]{64}$"
NonBlankString = Annotated[str, Field(min_length=1, pattern=r".*\S.*")]


class NewProjectModel(FrozenModel):
    """Common strict configuration for public new-project contracts."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


def _freeze_public_provenance(value: dict[str, Any]) -> dict[str, Any]:
    """Accept only immutable, public, JSON-safe provenance facts."""
    if private_data_violations(value):
        raise ValueError("public provenance contains private or non-public data")
    frozen = freeze_json_value(value)
    return cast(dict[str, Any], frozen)


def _validate_public_source_ref(value: str) -> str:
    if private_data_violations({value: None}):
        raise ValueError("source reference contains private or binding data")
    return value


def _text_metadata_for_public_policy(value: TextPlan) -> dict[str, Any]:
    """Return typed text metadata while exempting only visible user content."""
    metadata = value.model_dump(mode="json", by_alias=True)
    metadata.pop("content")
    for run in metadata["runs"]:
        run.pop("content")
    return metadata


class NewProjectConfig(NewProjectModel):
    """Version-pinned configuration for one freshly generated project."""

    project_name: NonBlankString = Field(alias="projectName")
    package_name: NonBlankString = Field(alias="packageName")
    fairy_gui_version: Literal["6.1.4"] = Field(alias="fairyGuiVersion")
    publish_target: Literal["unity"] = Field(alias="publishTarget")
    naming_policy_version: Literal[1] = Field(default=1, alias="namingPolicyVersion")


class AssetPayload(NewProjectModel):
    """An internal byte payload matched later against one Plan resource."""

    resource_id: NonBlankString = Field(alias="resourceId")
    declared_mime_type: NonBlankString = Field(alias="declaredMimeType")
    content: bytes = Field(repr=False, exclude=True)


@dataclass(frozen=True, init=False)
class AssetPayloadSet:
    """An immutable, resource-ID-keyed collection of asset bytes.

    This type is intentionally separate from ``NewProjectManifest``: raw bytes
    never form part of a manifest's canonical representation or diagnostics.
    """

    items: tuple[AssetPayload, ...]
    by_resource_id: Mapping[str, AssetPayload]

    def __init__(self, items: Iterable[AssetPayload]) -> None:
        indexed: dict[str, AssetPayload] = {}
        for item in items:
            if item.resource_id in indexed:
                raise ValueError(f"duplicate resource id: {item.resource_id}")
            indexed[item.resource_id] = item
        ordered_items = tuple(indexed[key] for key in sorted(indexed))
        object.__setattr__(self, "items", ordered_items)
        object.__setattr__(self, "by_resource_id", freeze_mapping(indexed))

    @classmethod
    def from_items(cls, items: Iterable[AssetPayload]) -> AssetPayloadSet:
        return cls(items)

    def payload_for(self, resource_id: str) -> AssetPayload:
        return self.by_resource_id[resource_id]


class _ManifestModel(NewProjectModel):
    """Base for manifest entries that may retain only public provenance."""

    public_provenance: dict[NonBlankString, Any] = Field(
        default_factory=dict,
        alias="publicProvenance",
    )

    @field_validator("public_provenance", mode="after")
    @classmethod
    def freeze_public_provenance(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _freeze_public_provenance(value)


class ManifestPackage(_ManifestModel):
    """The one package owned by a newly generated project."""

    id: NonBlankString
    source_document_ref: NonBlankString = Field(alias="sourceDocumentRef")
    name: NonBlankString
    relative_path: NonBlankString = Field(alias="relativePath")

    _public_source = field_validator("source_document_ref")(_validate_public_source_ref)


class ManifestObject(_ManifestModel):
    """A typed object emitted into exactly one generated component."""

    id: NonBlankString
    name: NonBlankString | None = None
    source_node_ref: NonBlankString = Field(alias="sourceNodeRef")
    uir_node_ref: NonBlankString = Field(alias="uirNodeRef")
    parent_object_ref: NonBlankString | None = Field(default=None, alias="parentObjectRef")
    child_object_refs: tuple[NonBlankString, ...] = Field(default=(), alias="childObjectRefs")
    z_index: int = Field(alias="zIndex", ge=0)
    type: PlanNodeType
    transform: TransformPlan
    text: TextPlan | None = None
    graph: GraphPlan | None = Field(default=None, exclude_if=lambda value: value is None)
    background_graph: GraphPlan | None = Field(
        default=None, alias="backgroundGraph", exclude_if=lambda value: value is None
    )
    resource_ref: NonBlankString | None = Field(default=None, alias="resourceRef")
    component_ref: NonBlankString | None = Field(default=None, alias="componentRef")
    mask_mode: MaskMode | None = Field(default=None, alias="maskMode")
    mask_kind: MaskKind | None = Field(default=None, alias="maskKind")
    mask_object_ref: NonBlankString | None = Field(default=None, alias="maskObjectRef")
    mask_content_object_refs: tuple[NonBlankString, ...] = Field(
        default=(), alias="maskContentObjectRefs"
    )
    raster_consumed_node_refs: tuple[NonBlankString, ...] = Field(
        default=(), alias="rasterConsumedNodeRefs"
    )
    mask_corner_radii: tuple[float, float, float, float] | None = Field(
        default=None, alias="maskCornerRadii"
    )

    _public_sources = field_validator("source_node_ref", "uir_node_ref")(
        _validate_public_source_ref
    )

    @field_validator("raster_consumed_node_refs")
    @classmethod
    def validate_raster_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_validate_public_source_ref(item) for item in value)

    @field_validator("text", mode="after")
    @classmethod
    def reject_private_text_metadata(cls, value: TextPlan | None) -> TextPlan | None:
        if value is not None and private_data_violations(_text_metadata_for_public_policy(value)):
            raise ValueError("manifest text metadata contains private or non-public data")
        return value


class ManifestComponent(_ManifestModel):
    """A root or self-contained definition component in the new package."""

    id: NonBlankString
    source_component_kind: Literal["definition", "root"] = Field(alias="sourceComponentKind")
    source_component_ref: NonBlankString = Field(alias="sourceComponentRef")
    name: NonBlankString
    relative_path: NonBlankString = Field(alias="relativePath")
    size: Bounds
    objects: tuple[ManifestObject, ...] = ()

    _public_source = field_validator("source_component_ref")(_validate_public_source_ref)


class ManifestResource(_ManifestModel):
    """A validated resource declared and owned by the new package."""

    id: NonBlankString
    source_resource_ref: NonBlankString = Field(alias="sourceResourceRef")
    name: NonBlankString
    relative_path: NonBlankString = Field(alias="relativePath")
    mime_type: NonBlankString = Field(alias="mimeType")
    content_sha256: str = Field(alias="contentSha256", pattern=SHA256_PATTERN)
    export_parameters_sha256: str = Field(
        alias="exportParametersSha256", pattern=SHA256_PATTERN
    )
    export_format: Literal["png", "jpg", "webp", "svg"] = Field(alias="exportFormat")
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    nine_slice: NineSlicePlan | None = Field(default=None, alias="nineSlice")
    consumer_object_refs: tuple[NonBlankString, ...] = Field(alias="consumerObjectRefs")

    _public_source = field_validator("source_resource_ref")(_validate_public_source_ref)


class NewProjectManifest(NewProjectModel):
    """Canonical, byte-free declaration of a single new FairyGUI project."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    project: NewProjectConfig
    package: ManifestPackage
    components: tuple[ManifestComponent, ...]
    resources: tuple[ManifestResource, ...]

    @model_validator(mode="after")
    def reject_duplicate_resource_ids(self) -> Self:
        resource_ids = [resource.id for resource in self.resources]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("duplicate resource id in manifest")
        return self


class BuiltNewProject(NewProjectModel):
    """The published artifact together with its byte-free manifest contract."""

    path: Path = Field(exclude=True)
    manifest: NewProjectManifest

    @classmethod
    def from_path(cls, path: Path, manifest: NewProjectManifest) -> BuiltNewProject:
        return cls(path=path, manifest=manifest)
