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
from figma_to_fgui.hifi_semantic_pairing import recover_semantic_component_pairs
from figma_to_fgui.models import Bounds

_SHA = "0" * 64


def _evidence() -> HifiMappingEvidence:
    return HifiMappingEvidence(
        name_score=0.0,
        position_score=0.0,
        size_score=0.0,
        type_score=0.0,
        parent_score=0.0,
        order_score=0.0,
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


def _old(
    object_id: str,
    object_type: str,
    *,
    parent_id: str | None = None,
    child_index: int = 0,
    x: float = 100.0,
    y: float = 100.0,
    width: float = 300.0,
    height: float = 80.0,
    text: str | None = None,
) -> FguiObjectRef:
    return FguiObjectRef(
        object_id=object_id,
        name=object_id,
        object_type=object_type,
        parent_id=parent_id,
        child_index=child_index,
        x=x,
        y=y,
        width=width,
        height=height,
        protected_sha256=_SHA,
        effective_text=text,
    )


def _inventory(*objects: FguiObjectRef) -> FguiComponentInventory:
    return FguiComponentInventory(
        target=_target(),
        width=1080.0,
        height=1920.0,
        objects=objects,
        behavior=FguiBehaviorSummary(
            protected_sha256=_SHA,
            gear_count=0,
            relation_count=0,
            action_count=0,
        ),
        parse_complete=True,
        expanded_instances=True,
    )


def _draft(*items: HifiMappingItem) -> HifiMappingDraft:
    return HifiMappingDraft(
        policy_revision=HIFI_MAPPING_POLICY_REVISION,
        mapping_revision=1,
        old_canvas_size=(1080.0, 1920.0),
        source_canvas_size=(1080.0, 1920.0),
        items=items,
        unresolved_count=sum(1 for item in items if item.action is None),
    )


def _root(*groups: SelectionNode) -> SelectionManifest:
    return SelectionManifest(
        display_name="PSD",
        top_level_nodes=(SelectionNode(
            id="psd-root:" + "a" * 64,
            name="PSD",
            type="FRAME",
            bounds=Bounds(x=0.0, y=0.0, width=1080.0, height=1920.0),
            children=groups,
        ),),
    )


def test_recovers_existing_component_from_psd_group_before_leaf_additions() -> None:
    component = _old("button", "component")
    loader = _old("icon_bg", "loader", parent_id="button", child_index=0)
    title = _old(
        "title", "text", parent_id="button", child_index=1,
        x=140.0, y=120.0, width=220.0, height=35.0,
        text="Noble Reception",
    )
    bg = SelectionNode(
        id="bg",
        name="background",
        type="IMAGE",
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        properties={"psdKind": "pixel"},
    )
    text = SelectionNode(
        id="psd_title",
        name="title",
        type="TEXT",
        bounds=Bounds(x=140.0, y=120.0, width=220.0, height=35.0),
        text="Noble Reception",
        properties={"psdKind": "type"},
    )
    group = SelectionNode(
        id="button_group",
        name="Noble Reception",
        type="GROUP",
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        children=(bg, text),
        properties={"psdKind": "group"},
    )
    draft = _draft(
        HifiMappingItem(
            item_id="old:button", old_object_id="button", old_name="button",
            old_object_type="component", status="fgui_only", score=0.0,
            evidence=_evidence(), action="keep_old",
        ),
        HifiMappingItem(
            item_id="old:loader", old_object_id="icon_bg", old_name="icon_bg",
            old_object_type="loader", status="fgui_only", score=0.0,
            evidence=_evidence(), action="keep_old",
        ),
        HifiMappingItem(
            item_id="old:title", old_object_id="title", old_name="title",
            old_object_type="text", status="fgui_only", score=0.0,
            evidence=_evidence(), action="keep_old",
        ),
        HifiMappingItem(
            item_id="new:bg", figma_node_id="bg", figma_name="background",
            status="hifi_added", score=0.0, evidence=_evidence(), action="add_visual",
        ),
    )

    result = recover_semantic_component_pairs(
        _inventory(component, loader, title), _root(group), draft
    )
    button = next(item for item in result.items if item.old_object_id == "button")

    assert result.policy_revision == HIFI_MAPPING_POLICY_REVISION
    assert button.action == "accept"
    assert button.status == "matched"
    assert button.figma_node_id == "button_group"


def test_does_not_guess_between_repeated_groups_without_a_clear_margin() -> None:
    component = _old("button", "component")
    loader = _old("icon", "loader", parent_id="button")
    groups = tuple(
        SelectionNode(
            id=f"group_{index}",
            name="button",
            type="GROUP",
            bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
            children=(SelectionNode(
                id=f"image_{index}",
                name="bg",
                type="IMAGE",
                bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
                properties={"psdKind": "pixel"},
            ),),
            properties={"psdKind": "group"},
        )
        for index in range(2)
    )
    draft = _draft(HifiMappingItem(
        item_id="old:button",
        old_object_id="button",
        old_name="button",
        old_object_type="component",
        status="fgui_only",
        score=0.0,
        evidence=_evidence(),
        action="keep_old",
    ))

    result = recover_semantic_component_pairs(
        _inventory(component, loader), _root(*groups), draft
    )
    button = result.items[0]

    assert result.policy_revision == HIFI_MAPPING_POLICY_REVISION
    assert button.action == "keep_old"
    assert button.figma_node_id is None