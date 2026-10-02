from __future__ import annotations

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_replacement_models import (
    HIFI_MAPPING_POLICY_REVISION,
    FguiBehaviorSummary,
    FguiComponentInventory,
    FguiObjectRef,
    HifiMappingDraft,
    HifiMappingEvidence,
    HifiMappingItem,
    HifiTargetRef,
)
from figma_to_fgui.hifi_semantic_reskin import normalize_psd_semantic_reskin
from figma_to_fgui.models import Bounds

_SHA = "0" * 64


def _evidence() -> HifiMappingEvidence:
    return HifiMappingEvidence(
        name_score=1.0,
        position_score=1.0,
        size_score=1.0,
        type_score=1.0,
        parent_score=1.0,
        order_score=1.0,
    )


def test_geometry_geared_loader_cannot_become_whole_bundle_owner() -> None:
    target = HifiTargetRef(
        project_id="project",
        project_fingerprint=_SHA,
        package_id="pkg",
        package_name="Package",
        directory="/",
        component_id="root",
        component_name="Root",
        component_relative_path="assets/Package/Root.xml",
    )
    component = FguiObjectRef(
        object_id="button",
        name="button",
        object_type="component",
        child_index=0,
        x=100.0,
        y=100.0,
        width=300.0,
        height=80.0,
        protected_sha256=_SHA,
    )
    unsafe_loader = FguiObjectRef(
        object_id="skin",
        name="skin",
        object_type="loader",
        parent_id="button",
        child_index=1,
        x=100.0,
        y=100.0,
        width=300.0,
        height=80.0,
        protected_sha256=_SHA,
        behavior_roles=("controller_driven", "runtime_object"),
        dynamic_properties=("visible", "icon", "xy", "size"),
        default_visible=True,
    )
    inventory = FguiComponentInventory(
        target=target,
        width=1080.0,
        height=1920.0,
        objects=(component, unsafe_loader),
        behavior=FguiBehaviorSummary(
            protected_sha256=_SHA,
            gear_count=2,
            relation_count=0,
            action_count=0,
        ),
        parse_complete=True,
        expanded_instances=True,
    )
    leaf = SelectionNode(
        id="target_skin",
        name="target_skin",
        type="IMAGE",
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        properties={"psdKind": "pixel"},
    )
    group = SelectionNode(
        id="button_group",
        name="button_group",
        type="GROUP",
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        children=(leaf,),
        properties={"psdKind": "group"},
    )
    manifest = SelectionManifest(
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:" + "a" * 64,
            name="PSD",
            type="FRAME",
            bounds=Bounds(x=0.0, y=0.0, width=1080.0, height=1920.0),
            children=(group,),
        ),),
    )
    draft = HifiMappingDraft(
        policy_revision=HIFI_MAPPING_POLICY_REVISION,
        mapping_revision=1,
        items=(
            HifiMappingItem(
                item_id="component",
                old_object_id="button",
                old_name="button",
                old_object_type="component",
                figma_node_id="button_group",
                figma_name="button_group",
                status="matched",
                score=1.0,
                evidence=_evidence(),
                action="accept",
            ),
            HifiMappingItem(
                item_id="skin",
                old_object_id="skin",
                old_name="skin",
                old_object_type="loader",
                status="fgui_only",
                score=0.0,
                evidence=_evidence(),
                action="keep_old",
            ),
            HifiMappingItem(
                item_id="new",
                figma_node_id="target_skin",
                figma_name="target_skin",
                status="hifi_added",
                score=0.0,
                evidence=_evidence(),
                action="add_visual",
            ),
        ),
        unresolved_count=0,
    )

    result = normalize_psd_semantic_reskin(
        inventory,
        manifest,
        draft,
        owned_visual_validator=lambda _group, _owned, _retained: True,
    )
    runtime = next(item for item in result.items if item.old_object_id == "skin")

    assert runtime.status == "blocked"
    assert runtime.action is None
    assert not runtime.owned_source_ids
    assert result.unresolved_count >= 1
