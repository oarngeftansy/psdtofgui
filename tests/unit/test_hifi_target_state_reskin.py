from __future__ import annotations

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_replacement_models import (
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
    default_visible: bool = True,
    state_driven: bool = False,
) -> FguiObjectRef:
    return FguiObjectRef(
        object_id=object_id,
        name=object_id,
        object_type=object_type,
        parent_id=parent_id,
        child_index=child_index,
        x=100.0,
        y=100.0,
        width=300.0,
        height=80.0,
        protected_sha256=_SHA,
        default_visible=default_visible,
        behavior_roles=("controller_driven",) if state_driven else (),
        dynamic_properties=("visible",) if state_driven else (),
    )


def _inventory(*objects: FguiObjectRef) -> FguiComponentInventory:
    return FguiComponentInventory(
        target=_target(),
        width=1080.0,
        height=1920.0,
        objects=objects,
        behavior=FguiBehaviorSummary(
            protected_sha256=_SHA,
            gear_count=1,
            relation_count=0,
            action_count=0,
        ),
        parse_complete=True,
        expanded_instances=True,
    )


def _node(node_id: str, *, node_type: str = "IMAGE", kind: str = "pixel") -> SelectionNode:
    return SelectionNode(
        id=node_id,
        name=node_id,
        type=node_type,
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        properties={"psdKind": kind},
    )


def _manifest(group: SelectionNode) -> SelectionManifest:
    root = SelectionNode(
        id="psd-root:" + "a" * 64,
        name="PSD",
        type="FRAME",
        bounds=Bounds(x=0.0, y=0.0, width=1080.0, height=1920.0),
        children=(group,),
    )
    return SelectionManifest(display_name="PSD", top_level_nodes=(root,))


def _evidence() -> HifiMappingEvidence:
    return HifiMappingEvidence(
        name_score=0.0,
        position_score=0.0,
        size_score=0.0,
        type_score=0.0,
        parent_score=0.0,
        order_score=0.0,
    )


def _item(
    item_id: str,
    *,
    old: FguiObjectRef | None = None,
    node: SelectionNode | None = None,
    status: str = "fgui_only",
    action: str | None = "keep_old",
) -> HifiMappingItem:
    return HifiMappingItem(
        item_id=item_id,
        old_object_id=old.object_id if old else None,
        old_name=old.name if old else None,
        old_object_type=old.object_type if old else None,
        figma_node_id=node.id if node else None,
        figma_name=node.name if node else None,
        status=status,
        score=1.0 if old and node else 0.0,
        evidence=_evidence(),
        action=action,
    )


def _draft(*items: HifiMappingItem) -> HifiMappingDraft:
    return HifiMappingDraft(
        policy_revision=26,
        mapping_revision=1,
        old_canvas_size=(1080.0, 1920.0),
        source_canvas_size=(1080.0, 1920.0),
        items=items,
        unresolved_count=sum(1 for item in items if item.action is None),
    )


def test_other_state_only_visual_is_kept_without_becoming_target_owner_or_blocker() -> None:
    component = _old("challenge", "component")
    inactive_gray = _old(
        "bg_common_gary",
        "image",
        parent_id="challenge",
        child_index=0,
        default_visible=False,
        state_driven=True,
    )
    active_skin = _old(
        "icon_bg",
        "loader",
        parent_id="challenge",
        child_index=1,
        default_visible=True,
        state_driven=True,
    )
    skin = _node("skin")
    group = SelectionNode(
        id="challenge_group",
        name="challenge_group",
        type="GROUP",
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        children=(skin,),
        properties={"psdKind": "group"},
    )
    draft = _draft(
        _item("component", old=component, node=group, status="matched", action="accept"),
        _item("inactive", old=inactive_gray),
        _item("active", old=active_skin, node=skin, status="matched", action="accept"),
    )

    result = normalize_psd_semantic_reskin(
        _inventory(component, inactive_gray, active_skin),
        _manifest(group),
        draft,
        owned_visual_validator=lambda _group, _owned, _retained: True,
    )
    by_old = {item.old_object_id: item for item in result.items if item.old_object_id}

    assert by_old["bg_common_gary"].action == "keep_old"
    assert by_old["bg_common_gary"].visual_disposition == "other_state"
    assert by_old["bg_common_gary"].figma_node_id is None
    assert by_old["icon_bg"].action == "accept"
    assert by_old["icon_bg"].owned_group_id == "challenge_group"
    assert result.unresolved_count == 0


def test_target_visible_state_driven_visual_without_psd_ownership_blocks() -> None:
    component = _old("challenge", "component")
    owner = _old("icon_bg", "loader", parent_id="challenge", child_index=1)
    unowned_dynamic = _old(
        "state_overlay",
        "image",
        parent_id="challenge",
        child_index=2,
        default_visible=True,
        state_driven=True,
    )
    skin = _node("skin")
    group = SelectionNode(
        id="challenge_group",
        name="challenge_group",
        type="GROUP",
        bounds=Bounds(x=100.0, y=100.0, width=300.0, height=80.0),
        children=(skin,),
        properties={"psdKind": "group"},
    )
    draft = _draft(
        _item("component", old=component, node=group, status="matched", action="accept"),
        _item("owner", old=owner, node=skin, status="matched", action="accept"),
        _item("dynamic", old=unowned_dynamic),
    )

    result = normalize_psd_semantic_reskin(
        _inventory(component, owner, unowned_dynamic),
        _manifest(group),
        draft,
        owned_visual_validator=lambda _group, _owned, _retained: True,
    )
    dynamic = next(item for item in result.items if item.old_object_id == "state_overlay")

    assert dynamic.status == "blocked"
    assert dynamic.action is None
    assert dynamic.visual_disposition == "other_state"
    assert result.unresolved_count == 1
