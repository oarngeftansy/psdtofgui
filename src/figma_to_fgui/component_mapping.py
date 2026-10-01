from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from figma_to_fgui.models import FrozenModel
from figma_to_fgui.project_index import ProjectIndex


class FigmaMappingTarget(FrozenModel):
    names: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = Field(default=(), alias="nodeIds")


class FguiMappingTarget(FrozenModel):
    package: str
    component: str
    path: str


class LegacyMappingHint(FrozenModel):
    package_id: str | None = Field(default=None, alias="packageId")
    component_id: str | None = Field(default=None, alias="componentId")


class ResolvedMappingTarget(FrozenModel):
    package_id: str
    component_id: str
    relative_path: str


class ComponentMapping(FrozenModel):
    key: str
    figma: FigmaMappingTarget
    fgui: FguiMappingTarget
    legacy_hint: LegacyMappingHint | None = Field(default=None, alias="legacyHint")
    properties: dict[str, Any] = Field(default_factory=dict)
    source: tuple[str, ...]
    status: Literal["candidate", "verified", "missing", "conflict"] = "candidate"
    resolved: ResolvedMappingTarget | None = None
    reason: str | None = None


class ComponentMappingCatalog(FrozenModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    sources: tuple[str, ...]
    components: tuple[ComponentMapping, ...]


def load_mapping_catalog(path: Path) -> ComponentMappingCatalog:
    return ComponentMappingCatalog.model_validate_json(path.read_text("utf-8"))


def validate_mapping_catalog(
    catalog: ComponentMappingCatalog, index: ProjectIndex
) -> ComponentMappingCatalog:
    validated: list[ComponentMapping] = []
    for candidate in catalog.components:
        resources = index.resources_by_package.get(candidate.fgui.package)
        expected_name = f"{candidate.fgui.component}.xml"
        resource = None if resources is None else resources.get(expected_name)
        if resource is None:
            validated.append(
                candidate.model_copy(
                    update={"status": "missing", "resolved": None, "reason": "component_not_found"}
                )
            )
            continue
        if resource.kind != "component":
            validated.append(
                candidate.model_copy(
                    update={
                        "status": "conflict",
                        "resolved": None,
                        "reason": "target_is_not_component",
                    }
                )
            )
            continue
        expected_suffix = candidate.fgui.path.replace("\\", "/").strip("/")
        if expected_suffix and not resource.relative_path.endswith(expected_suffix):
            validated.append(
                candidate.model_copy(
                    update={"status": "conflict", "resolved": None, "reason": "path_mismatch"}
                )
            )
            continue
        validated.append(
            candidate.model_copy(
                update={
                    "status": "verified",
                    "resolved": ResolvedMappingTarget(
                        package_id=resource.package_id,
                        component_id=resource.id,
                        relative_path=resource.relative_path,
                    ),
                    "reason": None,
                }
            )
        )
    return catalog.model_copy(update={"components": tuple(validated)})
