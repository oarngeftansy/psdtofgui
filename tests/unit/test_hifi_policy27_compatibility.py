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
        name_score=0.0,
        position_score=1.0,
        size_score=1.0,
        type_score=1.0,
        parent_score=1.0,
        order_score=1.0,
    )


def _target() -> HifiTargetRef:
    return HifiTargetRef(
        project_id="project",
        project_fingerprint=_SHA,
        package_id="pkg",
        package_name="Package",
        directory="/",
        component_id="root",
        component_name="Root",
        component_relative_path="assets/Package/Root.xml",
    )


def test_policy27_recomputes_semantic_group_and_discards_legacy_owned_bundle() -> None:
    component = FguiObjectRef(
        object_id="button",
        name="btn_delegate",
        object_type="component",
        child_index=0,
        x=100.0,
        y=100.0,
        width=300.0,
        height=80.0,
        protected_sha256=_SHA,
    )
    loader = FguiObjectRef(
        object_id="icon",
        name="icon",
        object_type="loader",
        parent_id="button",
        child_index=1,
        x=100.0,
        y=100.0,
        width=300.0,
        height=80.0,
        protected_sha256=_SHA,
    )
    title = FguiObjectRef(
        object_id="title",
        name="title",
        object_type="text",
        parent_id="button",
        child_index=2,
        x=130.0,
        y=120.0,
        width=220.0,
        height=32.0,
        protected_sha256=_SHA,
        effective_text="Noble Reception",
    )
    inventory = FguiComponentInventory(
        target=_target(),
        width=1080.0,
        height=1920.0,
        objects=(component, loader, title),
        behavior=FguiBehaviorSummary(
            protected_sha256=_SHA,
            gear_count=0,
            relation_count=0,
            action_count=0,
        ),
        parse_complete=True,
        expanded_instances=True,
    )

    wrong_leaf = SelectionNode(
        id="wrong_leaf",
        name="wrong_leaf",
        type="IMAGE",
        bounds=Bounds(x=500.0, y=500.0, width=300.0, height=80.0),
        properties={"psdKind": "pixel"},
    )
    wrong_group = SelectionNode(
        id="wrong_group",
        name="wrong_group",
        type="GROUP",
        bounds=Bounds(x=500.0, y=500.0, width=300.0, height=80.0),
        children=(wrong_leaf,),
        properties={"psdKind": "group"},
    )
    bg = SelectionNode(
        id="bg",
        name="bg",
        type="IMAGE",
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        properties={"psdKind": "pixel"},
    )
    ornament = SelectionNode(
        id="ornament",
        name="ornament",
        type="VECTOR",
        bounds=Bounds(x=108.0, y=106.0, width=284.0, height=68.0),
        properties={"psdKind": "shape"},
    )
    psd_title = SelectionNode(
        id="psd_title",
        name="title",
        type="TEXT",
        bounds=Bounds(x=130.0, y=120.0, width=220.0, height=32.0),
        text="Noble Reception",
        properties={"psdKind": "type"},
    )
    right_group = SelectionNode(
        id="right_group",
        name="Noble Reception",
        type="GROUP",
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        children=(bg, ornament, psd_title),
        properties={"psdKind": "group"},
    )
    manifest = SelectionManifest(
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:" + "a" * 64,
            name="PSD",
            type="FRAME",
            bounds=Bounds(x=0.0, y=0.0, width=1080.0, height=1920.0),
            children=(wrong_group, right_group),
        ),),
    )

    # Simulate output from the compatibility-era group promoter: both the
    # component and visual host already claim the wrong group/pixels.
    draft = HifiMappingDraft(
        policy_revision=HIFI_MAPPING_POLICY_REVISION,
        mapping_revision=1,
        old_canvas_size=(1080.0, 1920.0),
        source_canvas_size=(1080.0, 1920.0),
        items=(
            HifiMappingItem(
                item_id="component",
                old_object_id="button",
                old_name="btn_delegate",
                old_object_type="component",
                figma_node_id="wrong_group",
                figma_name="wrong_group",
                status="matched",
                score=1.0,
                evidence=_evidence(),
                action="accept",
                owned_group_id="wrong_group",
            ),
            HifiMappingItem(
                item_id="loader",
                old_object_id="icon",
                old_name="icon",
                old_object_type="loader",
                figma_node_id="wrong_leaf",
                figma_name="wrong_leaf",
                status="matched",
                score=1.0,
                evidence=_evidence(),
                action="accept",
                owned_group_id="wrong_group",
                owned_source_ids=("wrong_leaf",),
            ),
            HifiMappingItem(
                item_id="title",
                old_object_id="title",
                old_name="title",
                old_object_type="text",
                status="fgui_only",
                score=0.0,
                evidence=_evidence(),
                action="keep_old",
            ),
            HifiMappingItem(
                item_id="new:bg",
                figma_node_id="bg",
                figma_name="bg",
                status="hifi_added",
                score=0.0,
                evidence=_evidence(),
                action="add_visual",
            ),
            HifiMappingItem(
                item_id="new:ornament",
                figma_node_id="ornament",
                figma_name="ornament",
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
    by_old = {item.old_object_id: item for item in result.items if item.old_object_id}

    assert by_old["button"].figma_node_id == "right_group"
    assert by_old["button"].owned_group_id == "right_group"
    assert by_old["title"].figma_node_id == "psd_title"
    assert by_old["icon"].owned_group_id == "right_group"
    assert set(by_old["icon"].owned_source_ids) == {"bg", "ornament"}
    assert "wrong_leaf" not in by_old["icon"].owned_source_ids
    assert not any(
        item.action == "add_visual" and item.figma_node_id in {"bg", "ornament"}
        for item in result.items
    )
