from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, PositiveFloat, model_validator

from figma_to_fgui.service_contracts import Sha256, StrictVersionedModel

HifiMappingStatus = Literal[
    "matched", "suggested", "uncertain", "fgui_only", "hifi_added", "blocked",
    "structural", "out_of_scope", "occluded",
]
HifiMappingAction = Literal["accept", "retarget", "keep_old", "add_visual", "exception", "preserve_structure"]
LegacyVisualDisposition = Literal["preserve", "retire", "other_state", "structural"]
LogicalBoundsPolicy = Literal["preserve", "resize"]
# Keep the wire revision at 25 because the web/plugin strict parser is shared
# with deployed local clients. The policy-26 render-contribution correction is
# wire-compatible and is applied by HifiMappingDraft's PSD post-validation pass.
HIFI_MAPPING_POLICY_REVISION = 25


def _mapping_scope(object_id: str | None) -> str:
    if not object_id or ":" not in object_id:
        return ""
    return object_id.rsplit(":", 1)[0]


def _bounds_coverage(
    target: tuple[float, float, float, float],
    cover: tuple[float, float, float, float],
) -> float:
    tx, ty, tw, th = target
    cx, cy, cw, ch = cover
    if min(tw, th, cw, ch) <= 0:
        return 0.0
    left = max(tx, cx)
    top = max(ty, cy)
    right = min(tx + tw, cx + cw)
    bottom = min(ty + th, cy + ch)
    if right <= left or bottom <= top:
        return 0.0
    return ((right - left) * (bottom - top)) / (tw * th)


def _bounds_area_ratio(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    left_area = max(0.0, left[2]) * max(0.0, left[3])
    right_area = max(0.0, right[2]) * max(0.0, right[3])
    if min(left_area, right_area) <= 0:
        return float("inf")
    return max(left_area, right_area) / min(left_area, right_area)


class HifiTargetRef(StrictVersionedModel):
    project_id: str = Field(min_length=1, max_length=128)
    project_fingerprint: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    package_id: str = Field(min_length=1, max_length=128)
    package_name: str = Field(min_length=1, max_length=256)
    directory: str = Field(min_length=1, max_length=1024)
    component_id: str = Field(min_length=1, max_length=128)
    component_name: str = Field(min_length=1, max_length=256)
    component_relative_path: str = Field(min_length=1, max_length=2048)


class HifiComponentOption(StrictVersionedModel):
    resource_id: str
    name: str
    relative_path: str
    selectable: bool = True
    reason: str | None = None


class HifiDirectoryOption(StrictVersionedModel):
    path: str
    selectable: bool = True
    reason: str | None = None
    components: tuple[HifiComponentOption, ...] = ()


class HifiPackageOption(StrictVersionedModel):
    package_id: str
    name: str
    directories: tuple[HifiDirectoryOption, ...] = ()


class HifiProjectTreeView(StrictVersionedModel):
    project_id: str
    project_fingerprint: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    packages: tuple[HifiPackageOption, ...]


class FguiObjectRef(StrictVersionedModel):
    object_id: str
    name: str
    object_type: str
    parent_id: str | None = None
    child_index: int = Field(ge=0)
    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)
    resource_id: str | None = None
    shared_resource: bool = False
    protected_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    controller_refs: tuple[str, ...] = ()
    transition_refs: tuple[str, ...] = ()
    relation_refs: tuple[str, ...] = ()
    relation_side_pairs: tuple[str, ...] = ()
    auto_layout: Literal["hz", "vt"] | None = None
    layout_gap: float = Field(default=0.0, ge=0)
    layout_excludes_invisible: bool = False
    position_runtime_bound: bool = False
    out_of_scope: bool = False
    unknown_attributes: tuple[str, ...] = ()
    behavior_roles: tuple[str, ...] = ()
    dynamic_properties: tuple[str, ...] = ()
    instance_parameters: tuple[str, ...] = ()
    behavior_protected: bool = False
    component_relative_path: str | None = None
    local_object_id: str | None = None
    instance_path: tuple[str, ...] = ()
    owner_origin: tuple[float, float] = (0.0, 0.0)
    owner_scale: tuple[float, float] = (1.0, 1.0)
    write_blockers: tuple[str, ...] = ()
    raster_conversion_allowed: bool = False
    structural_only: bool = False
    default_visible: bool = True
    effective_text: str | None = None
    runtime_text_override: bool = False


class FguiControllerPage(StrictVersionedModel):
    page_id: str
    name: str
    index: int = Field(ge=0)


class FguiControllerContract(StrictVersionedModel):
    name: str
    raw_pages: str
    pages: tuple[FguiControllerPage, ...] = ()
    action_count: int = Field(ge=0)


class FguiTransitionContract(StrictVersionedModel):
    name: str
    autoplay: bool = False
    repeat: str | None = None
    item_count: int = Field(ge=0)
    target_ids: tuple[str, ...] = ()
    item_types: tuple[str, ...] = ()


class FguiInstanceContract(StrictVersionedModel):
    object_id: str
    resource_id: str
    package_id: str | None = None
    controller_assignments: tuple[str, ...] = ()
    property_assignments: tuple[str, ...] = ()
    parameter_tags: tuple[str, ...] = ()
    referenced_component_path: str | None = None
    referenced_behavior_sha256: Sha256 | None = None
    referenced_controller_count: int = Field(default=0, ge=0)
    referenced_transition_count: int = Field(default=0, ge=0)
    referenced_action_count: int = Field(default=0, ge=0)
    referenced_gear_count: int = Field(default=0, ge=0)


class FguiBehaviorSummary(StrictVersionedModel):
    protected_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    controllers: tuple[FguiControllerContract, ...] = ()
    transitions: tuple[FguiTransitionContract, ...] = ()
    instances: tuple[FguiInstanceContract, ...] = ()
    gear_count: int = Field(ge=0)
    relation_count: int = Field(ge=0)
    action_count: int = Field(ge=0)
    dynamic_object_ids: tuple[str, ...] = ()
    runtime_bound_object_ids: tuple[str, ...] = ()
    referenced_component_paths: tuple[str, ...] = ()
    unresolved_instance_ids: tuple[str, ...] = ()


class FguiComponentInventory(StrictVersionedModel):
    target: HifiTargetRef
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    objects: tuple[FguiObjectRef, ...]
    behavior: FguiBehaviorSummary
    unknown_tags: tuple[str, ...] = ()
    parse_complete: bool
    expanded_instances: bool = False
    scope_issues: tuple[str, ...] = ()


class HifiMappingEvidence(StrictVersionedModel):
    name_score: float = Field(ge=0, le=1)
    position_authoritative: bool = True
    position_score: float = Field(ge=0, le=1)
    size_score: float = Field(ge=0, le=1)
    type_score: float = Field(ge=0, le=1)
    parent_score: float = Field(ge=0, le=1)
    order_score: float = Field(ge=0, le=1)


class HifiMappingItem(StrictVersionedModel):
    item_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    old_object_id: str | None = None
    old_name: str | None = None
    old_object_type: str | None = None
    old_resource_id: str | None = None
    figma_node_id: str | None = None
    figma_name: str | None = None
    status: HifiMappingStatus
    score: float = Field(ge=0, le=1)
    evidence: HifiMappingEvidence
    action: HifiMappingAction | None = None
    candidates: tuple[str, ...] = ()
    old_bounds: tuple[float, float, float, float] | None = None
    figma_bounds: tuple[float, float, float, float] | None = None
    owned_source_ids: tuple[str, ...] = ()
    owned_group_id: str | None = None
    retained_source_ids: tuple[str, ...] = ()
    visual_echo: bool = False
    composite_group_id: str | None = None
    composite_source_ids: tuple[str, ...] = ()
    out_of_scope: bool = False
    occluded: bool = False
    generated_state: bool = False
    graph_conversion_proven: bool = False
    default_visible: bool | None = None
    preserve_runtime_text: bool = False
    visual_disposition: LegacyVisualDisposition = "preserve"
    logical_bounds_policy: LogicalBoundsPolicy = "preserve"


class HifiMappingDecision(StrictVersionedModel):
    mapping_revision: int = Field(ge=1)
    item_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    action: HifiMappingAction
    figma_node_id: str | None = Field(default=None, max_length=256)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_action_node(self) -> Self:
        if self.action == "retarget" and self.figma_node_id is None:
            raise ValueError("retarget requires figma_node_id")
        if self.action != "retarget" and self.figma_node_id is not None:
            raise ValueError("figma_node_id is only valid for retarget")
        return self


class HifiMappingDraft(StrictVersionedModel):
    policy_revision: int = Field(default=0, ge=0)
    mapping_revision: int = Field(ge=1)
    old_canvas_size: tuple[PositiveFloat, PositiveFloat] | None = None
    source_canvas_size: tuple[PositiveFloat, PositiveFloat] | None = None
    items: tuple[HifiMappingItem, ...]
    unresolved_count: int = Field(ge=0)

    @model_validator(mode="after")
    def retire_replaced_psd_visuals(self) -> Self:
        """Apply policy-26 render ownership without changing the wire schema.

        A PSD target is pixel authority for the selected state. If an old
        graph/image/loader has no PSD correspondence but another mapped visual
        in the same instance scope covers roughly the same visual footprint,
        keeping the old pixels is a duplicate render contribution. Preserve the
        old object identity but retire its target-state visual contribution.
        """
        psd_mapping = any(
            (item.figma_node_id or "").startswith("psd-")
            or (item.owned_group_id or "").startswith("psd-")
            or (item.composite_group_id or "").startswith("psd-")
            for item in self.items
        )
        if not psd_mapping:
            return self

        visual_types = {"graph", "image", "loader"}
        mapped = tuple(
            item
            for item in self.items
            if item.action in {"accept", "retarget"}
            and item.old_object_id is not None
            and (item.old_object_type or "").casefold() in visual_types | {"component"}
            and item.figma_bounds is not None
            and item.default_visible is not False
        )
        revised: list[HifiMappingItem] = []
        changed = False
        for item in self.items:
            if (
                item.status != "fgui_only"
                or item.action != "keep_old"
                or item.old_object_id is None
                or item.old_bounds is None
                or (item.old_object_type or "").casefold() not in visual_types
                or item.default_visible is False
                or item.visual_disposition in {"other_state", "structural"}
            ):
                revised.append(item)
                continue

            scope = _mapping_scope(item.old_object_id)
            replacement = any(
                candidate.old_object_id != item.old_object_id
                and _mapping_scope(candidate.old_object_id) == scope
                and candidate.figma_bounds is not None
                and _bounds_coverage(item.old_bounds, candidate.figma_bounds) >= 0.80
                and _bounds_area_ratio(item.old_bounds, candidate.figma_bounds) <= 2.25
                for candidate in mapped
            )
            if replacement and item.visual_disposition != "retire":
                item = item.model_copy(update={"visual_disposition": "retire"})
                changed = True
            revised.append(item)

        if changed:
            object.__setattr__(self, "items", tuple(revised))
            object.__setattr__(
                self,
                "unresolved_count",
                sum(item.action is None for item in revised),
            )
        return self


class HifiDiffItem(StrictVersionedModel):
    relative_path: str
    operation: Literal["create", "replace"]
    before_sha256: Sha256 | None = None
    after_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str


class HifiObjectDiff(StrictVersionedModel):
    item_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    kind: Literal["changed", "added", "kept", "exception"]
    old_object_id: str | None = Field(default=None, max_length=128)
    old_name: str | None = Field(default=None, max_length=256)
    figma_node_id: str | None = Field(default=None, max_length=256)
    figma_name: str | None = Field(default=None, max_length=256)
    changed_fields: tuple[str, ...] = ()
    summary: str = Field(min_length=1, max_length=500)


class HifiReplacementReview(StrictVersionedModel):
    policy_revision: int = Field(default=0, ge=0)
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    mapping_revision: int = Field(ge=1)
    target: HifiTargetRef
    changed_files: tuple[HifiDiffItem, ...]
    object_diffs: tuple[HifiObjectDiff, ...]
    protected_checks_passed: bool
    parse_coverage_complete: bool
    approvable: bool
    candidate_sha256: Sha256 | None = None
    warnings: tuple[str, ...] = ()
    editor_check_required: bool = True


class HifiReplacementView(StrictVersionedModel):
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal[
        "mapping", "building", "review_ready", "approved", "rejected", "failed", "superseded"
    ]
    selection_id: str = Field(pattern=r"^(?:[0-9a-f]{32}|[0-9a-f]{64})$")
    target: HifiTargetRef
    mapping_revision: int = Field(ge=1)
    unresolved_count: int = Field(ge=0)
    artifact_ready: bool = False


class HifiReplacementCreate(StrictVersionedModel):
    project_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    selection_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    target: HifiTargetRef
    idempotency_key: str = Field(min_length=1, max_length=200)


class HifiPsdReplacementCreate(StrictVersionedModel):
    project_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    psd_source_id: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    target: HifiTargetRef
    idempotency_key: str = Field(min_length=1, max_length=200)


class HifiPsdBatchReplacementCreate(StrictVersionedModel):
    psd_source_id: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    targets: list[HifiTargetRef] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=200)


class HifiReplacementBuildRequest(StrictVersionedModel):
    mapping_revision: int = Field(ge=1)


class HifiEditorChecks(StrictVersionedModel):
    layout_checked: bool
    references_checked: bool
    interactions_checked: bool
    editor_version: Literal["6.1.4"]
    candidate_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    export_mode: Literal["package", "overwrite"] = "package"

    @model_validator(mode="after")
    def require_every_check(self) -> Self:
        if not (self.layout_checked and self.references_checked and self.interactions_checked):
            raise ValueError("all editor checks are required")
        return self


class HifiEditorVerification(StrictVersionedModel):
    session_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    candidate_sha256: Sha256 = Field(pattern=r"^[0-9a-f]{64}$")
    editor_found: bool
    editor_version: Literal["6.1.4"] | None = None
    project_opened: bool
    component_opened: bool
    render_captured: bool
    screenshot_url: str | None = None
    screenshot_sha256: Sha256 | None = None
    screenshot_width: int | None = Field(default=None, gt=0)
    screenshot_height: int | None = Field(default=None, gt=0)
    expected_width: int = Field(gt=0)
    expected_height: int = Field(gt=0)
    full_frame: bool
    mean_pixel_difference: float | None = Field(default=None, ge=0, le=1)
    approvable: bool
    warnings: tuple[str, ...] = ()


class HifiReplacementRejectRequest(StrictVersionedModel):
    reason: str = Field(min_length=1, max_length=500)
