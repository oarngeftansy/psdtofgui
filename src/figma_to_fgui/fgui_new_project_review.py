"""Strict, byte-free designer review projection for Writer candidates."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from figma_to_fgui.fgui_new_project_models import (
    ManifestComponent,
    ManifestObject,
    NewProjectManifest,
)
from figma_to_fgui.fgui_plan_models import FGUIPlanDocument
from figma_to_fgui.image_preview import encode_webp_preview
from figma_to_fgui.models import Diagnostic, FrozenModel, Severity
from figma_to_fgui.service_contracts import (
    NewProjectAdjustmentStrategy,
    NewProjectConversionDisposition,
    NewProjectIssueKind,
)


class _StrictReviewModel(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False, strict=True)


class NewProjectImageReview(_StrictReviewModel):
    resource_id: str = Field(min_length=1, max_length=128)
    source_node_id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=160)
    evidence_kind: Literal["source-image", "generated-only"]
    source_preview_url: str | None = Field(default=None, pattern=r"^/v1/[A-Za-z0-9_./-]+$")
    generated_asset_url: str = Field(pattern=r"^/v1/[A-Za-z0-9_./-]+$")
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    nine_slice: bool
    crop_bounds_match: bool
    transparency_preserved: bool


class NewProjectComponentReview(_StrictReviewModel):
    component_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    evidence_kind: Literal["rendered", "structured-summary"]
    rendered_preview_url: str | None = Field(default=None, pattern=r"^/v1/[A-Za-z0-9_./-]+$")
    object_count: int = Field(ge=0)
    text_count: int = Field(ge=0)
    resource_refs: int = Field(ge=0)
    component_refs: int = Field(ge=0)
    hierarchy_valid: bool
    geometry_valid: bool
    text_valid: bool

    @model_validator(mode="after")
    def validate_evidence(self) -> NewProjectComponentReview:
        if (self.evidence_kind == "rendered") != (self.rendered_preview_url is not None):
            raise ValueError("rendered evidence requires one real preview URL")
        return self


class NewProjectPackageReview(_StrictReviewModel):
    package_name: str = Field(min_length=1, max_length=128)
    fairy_gui_version: Literal["6.1.4"]
    publish_target: Literal["unity"]
    components_added: int = Field(ge=0)
    resources_added: int = Field(ge=0)
    component_names: tuple[str, ...] = ()
    resource_names: tuple[str, ...] = ()
    resource_closure_valid: bool
    naming_conflicts: tuple[str, ...] = ()
    integrity_valid: bool


class DesignerCheck(_StrictReviewModel):
    id: str = Field(pattern=r"^review:[0-9a-f]{16}$")
    severity: Severity
    message: str = Field(min_length=1, max_length=500)
    issue_id: str = Field(pattern=r"^review:[0-9a-f]{16}$")
    issue_kind: NewProjectIssueKind | None = None
    uir_node_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_node_id: str | None = Field(default=None, min_length=1, max_length=256)
    actionable: bool
    allowed_strategies: tuple[NewProjectAdjustmentStrategy, ...] = ()

    @model_validator(mode="after")
    def validate_adjustment_projection(self) -> DesignerCheck:
        if len(self.allowed_strategies) != len(set(self.allowed_strategies)):
            raise ValueError("allowed adjustment strategies must be unique")
        if self.actionable != bool(self.allowed_strategies) or (
            self.actionable and (self.uir_node_id is None or self.source_node_id is None)
        ):
            raise ValueError("actionable checks must declare a closed adjustment set")
        return self


class NewProjectDesignerReview(_StrictReviewModel):
    version: Literal[1] = 1
    build_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    generation: int = Field(ge=1)
    dispositions: tuple[NewProjectConversionDisposition, ...] = ()
    image_reviews: tuple[NewProjectImageReview, ...]
    component_reviews: tuple[NewProjectComponentReview, ...]
    package_review: NewProjectPackageReview
    checks: tuple[DesignerCheck, ...]
    warning_ids: tuple[str, ...]
    approvable: bool

    @model_validator(mode="after")
    def validate_check_projection(self) -> NewProjectDesignerReview:
        ids = tuple(check.id for check in self.checks)
        if len(ids) != len(set(ids)):
            raise ValueError("review check IDs must be unique")
        disposition_ids = tuple(item.id for item in self.dispositions)
        if len(disposition_ids) != len(set(disposition_ids)):
            raise ValueError("review disposition IDs must be unique")
        expected_warnings = tuple(
            check.id for check in self.checks if check.severity is Severity.WARNING
        )
        if self.warning_ids != expected_warnings:
            raise ValueError("warning_ids must exactly project warning checks")
        if self.approvable and (
            not self.package_review.resource_closure_valid
            or not self.package_review.integrity_valid
            or bool(self.package_review.naming_conflicts)
            or any(check.severity is Severity.ERROR for check in self.checks)
            or any(item.blocks_approval for item in self.dispositions)
            or any(item.source_preview_url is None for item in self.image_reviews)
            or any(not item.crop_bounds_match or not item.transparency_preserved for item in self.image_reviews)
            or any(not item.hierarchy_valid or not item.geometry_valid or not item.text_valid for item in self.component_reviews)
        ):
            raise ValueError("an invalid review cannot be approvable")
        return self


def _check_id(diagnostic: Diagnostic, index: int) -> str:
    payload = "\0".join((diagnostic.code, diagnostic.node_id or "", str(index))).encode("utf-8")
    return f"review:{hashlib.sha256(payload).hexdigest()[:16]}"


def _resource_closure_valid(manifest: NewProjectManifest) -> bool:
    objects = {item.id: item for component in manifest.components for item in component.objects}
    referenced = {item.resource_ref for item in objects.values() if item.resource_ref is not None}
    resources = {item.id: item for item in manifest.resources}
    if referenced != set(resources):
        return False
    return all(
        set(resource.consumer_object_refs)
        == {item.id for item in objects.values() if item.resource_ref == resource.id}
        for resource in resources.values()
    )


def _component_hierarchy_valid(component: ManifestComponent) -> bool:
    objects = {item.id: item for item in component.objects}
    return bool(objects) and all(
        (item.parent_object_ref is None or item.parent_object_ref in objects)
        and all(child in objects and objects[child].parent_object_ref == item.id for child in item.child_object_refs)
        for item in objects.values()
    )


def strategy_allowed_for_check(
    check: DesignerCheck, strategy: NewProjectAdjustmentStrategy
) -> bool:
    """Return the deliberately closed strategy set for one actionable issue."""
    return strategy in check.allowed_strategies


_ACTIONABLE_DIAGNOSTIC_POLICY: dict[
    str, tuple[NewProjectIssueKind, tuple[NewProjectAdjustmentStrategy, ...]]
] = {
    "fgui.visual.raster_fallback": (
        NewProjectIssueKind.RASTER_FALLBACK,
        (NewProjectAdjustmentStrategy.PRESERVE_EDITABLE,),
    ),
    "fgui.mask.raster_fallback": (
        NewProjectIssueKind.RASTER_FALLBACK,
        (NewProjectAdjustmentStrategy.PRESERVE_EDITABLE,),
    ),
}


def _actionable_policy(
    diagnostic: Diagnostic,
) -> tuple[NewProjectIssueKind | None, tuple[NewProjectAdjustmentStrategy, ...]]:
    if diagnostic.node_id is None:
        return None, ()
    return _ACTIONABLE_DIAGNOSTIC_POLICY.get(diagnostic.code, (None, ()))


def build_new_project_designer_review(
    manifest: NewProjectManifest,
    plan: FGUIPlanDocument | None = None,
    diagnostics: tuple[Diagnostic, ...] = (),
    *,
    build_id: str,
    generation: int,
    source_preview_urls_by_resource: Mapping[str, str] | None = None,
    rendered_component_previews: Mapping[str, bytes] | None = None,
    source_node_ids: Mapping[str, str] | None = None,
    dispositions: tuple[NewProjectConversionDisposition, ...] = (),
) -> NewProjectDesignerReview:
    """Project immutable candidate facts without inventing rendered evidence."""
    rendered_component_previews = rendered_component_previews or {}
    source_preview_urls_by_resource = source_preview_urls_by_resource or {}
    source_node_ids = source_node_ids or {}
    manifest_objects = {
        item.id: item for component in manifest.components for item in component.objects
    }

    def source_node_id_for_resource(resource: object) -> str:
        consumer_refs = getattr(resource, "consumer_object_refs", ())
        source_ids = {
            source_node_ids[object_.uir_node_ref]
            for object_ref in consumer_refs
            if (object_ := manifest_objects.get(object_ref)) is not None
            and object_.uir_node_ref in source_node_ids
        }
        if not source_ids:
            raise ValueError("each review image must resolve to a source node")
        return min(source_ids)

    image_reviews = tuple(
        NewProjectImageReview(
            resource_id=resource.id,
            source_node_id=source_node_id_for_resource(resource),
            label=resource.name,
            evidence_kind=("source-image" if source_preview_urls_by_resource.get(resource.source_resource_ref) else "generated-only"),
            source_preview_url=(
                source_preview_urls_by_resource.get(resource.source_resource_ref)
            ),
            generated_asset_url=(
                f"/v1/new-fgui-projects/{build_id}/previews/resources/{resource.id}"
            ),
            width=resource.width or 0,
            height=resource.height or 0,
            nine_slice=resource.nine_slice is not None,
            crop_bounds_match=bool(plan and (planned := plan.resources.get(resource.source_resource_ref)) and planned.width == resource.width and planned.height == resource.height),
            transparency_preserved=bool(plan and (planned := plan.resources.get(resource.source_resource_ref)) and planned.content_sha256 == resource.content_sha256 and planned.mime_type == resource.mime_type),
        )
        for resource in manifest.resources
    )

    component_reviews: list[NewProjectComponentReview] = []
    plan_nodes = {} if plan is None else dict(plan.nodes)
    if plan is not None:
        for definition in plan.component_definitions.values():
            plan_nodes.update(definition.nodes)
    for component in manifest.components:
        rendered = rendered_component_previews.get(component.id)
        has_real_preview = type(rendered) is bytes and encode_webp_preview(rendered) is not None
        writer_generated_definition = bool(
            plan
            and component.source_component_kind == "definition"
            and component.source_component_ref not in plan.component_definitions
        )

        def generated_root(
            item: ManifestObject,
            *,
            generated: bool = writer_generated_definition,
            component_size: object = component.size,
        ) -> bool:
            return bool(
                generated
                and item.parent_object_ref is None
                and item.transform.bounds == component_size
            )

        component_reviews.append(
            NewProjectComponentReview(
                component_id=component.id,
                label=component.name,
                evidence_kind="rendered" if has_real_preview else "structured-summary",
                rendered_preview_url=(
                    f"/v1/new-fgui-projects/{build_id}/previews/components/{component.id}"
                    if has_real_preview
                    else None
                ),
                object_count=len(component.objects),
                text_count=sum(item.text is not None for item in component.objects),
                resource_refs=sum(item.resource_ref is not None for item in component.objects),
                component_refs=sum(item.component_ref is not None for item in component.objects),
                hierarchy_valid=_component_hierarchy_valid(component),
                geometry_valid=bool(plan and all(
                    ((node := plan_nodes.get(item.source_node_ref)) is not None and node.transform == item.transform)
                    or generated_root(item)
                    for item in component.objects
                )),
                text_valid=bool(plan and all(
                    ((node := plan_nodes.get(item.source_node_ref)) is not None and node.text == item.text)
                    or (generated_root(item) and item.text is None)
                    for item in component.objects
                )),
            )
        )

    checks_list: list[DesignerCheck] = []
    for index, diagnostic in enumerate(diagnostics):
        issue_kind, allowed_strategies = _actionable_policy(diagnostic)
        checks_list.append(DesignerCheck(
            id=_check_id(diagnostic, index),
            severity=diagnostic.severity,
            message=diagnostic.message,
            issue_id=_check_id(diagnostic, index),
            issue_kind=issue_kind,
            uir_node_id=diagnostic.node_id,
            source_node_id=(
                None if diagnostic.node_id is None else source_node_ids.get(diagnostic.node_id)
            ),
            actionable=bool(allowed_strategies and source_node_ids.get(diagnostic.node_id or "")),
            allowed_strategies=(
                allowed_strategies if source_node_ids.get(diagnostic.node_id or "") else ()
            ),
        ))
    checks = tuple(checks_list)
    closure_valid = _resource_closure_valid(manifest)
    names = [item.name.casefold() for item in manifest.components]
    names.extend(item.name.casefold() for item in manifest.resources)
    naming_conflicts = tuple(sorted({name for name in names if names.count(name) > 1}))
    return NewProjectDesignerReview(
        build_id=build_id,
        generation=generation,
        dispositions=dispositions,
        image_reviews=image_reviews,
        component_reviews=tuple(component_reviews),
        package_review=NewProjectPackageReview(
            package_name=manifest.package.name,
            fairy_gui_version=manifest.project.fairy_gui_version,
            publish_target=manifest.project.publish_target,
            components_added=len(manifest.components),
            resources_added=len(manifest.resources),
            component_names=tuple(item.name for item in manifest.components),
            resource_names=tuple(item.name for item in manifest.resources),
            resource_closure_valid=closure_valid,
            naming_conflicts=naming_conflicts,
            integrity_valid=closure_valid and not naming_conflicts,
        ),
        checks=checks,
        warning_ids=tuple(check.id for check in checks if check.severity is Severity.WARNING),
        approvable=(
            closure_valid
            and not naming_conflicts
            and all(item.source_preview_url is not None for item in image_reviews)
            and all(item.crop_bounds_match and item.transparency_preserved for item in image_reviews)
            and all(
                item.hierarchy_valid and item.geometry_valid and item.text_valid
                for item in component_reviews
            )
            and all(check.severity is not Severity.ERROR for check in checks)
        ),
    )


def build_blocked_new_project_designer_review(
    *,
    build_id: str,
    generation: int,
    dispositions: tuple[NewProjectConversionDisposition, ...],
) -> NewProjectDesignerReview:
    """Build a reviewable, non-approvable analysis without inventing an artifact."""
    if not dispositions or any(not item.blocks_approval for item in dispositions):
        raise ValueError("blocked review requires only blocking dispositions")
    return NewProjectDesignerReview(
        build_id=build_id,
        generation=generation,
        dispositions=dispositions,
        image_reviews=(),
        component_reviews=(),
        package_review=NewProjectPackageReview(
            package_name="Generated",
            fairy_gui_version="6.1.4",
            publish_target="unity",
            components_added=0,
            resources_added=0,
            component_names=(),
            resource_names=(),
            resource_closure_valid=False,
            naming_conflicts=(),
            integrity_valid=False,
        ),
        checks=(),
        warning_ids=(),
        approvable=False,
    )
