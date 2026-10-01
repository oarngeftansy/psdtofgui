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
    x: float = 100.0,
    y: float = 100.0,
    width: float = 300.0,
    height: float = 80.0,
    effective_text: str | None = None,
    raster_conversion_allowed: bool = False,
    behavior_roles: tuple[str, ...] = (),
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
        effective_text=effective_text,
        raster_conversion_allowed=raster_conversion_allowed,
        behavior_roles=behavior_roles,
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


def _node(
    node_id: str,
    kind: str,
    *,
    node_type: str = "IMAGE",
    x: float = 100.0,
    y: float = 100.0,
    width: float = 300.0,
    height: float = 80.0,
    text: str | None = None,
    children: tuple[SelectionNode, ...] = (),
    order: int = 0,
) -> SelectionNode:
    return SelectionNode(
        id=node_id,
        name=node_id,
        type=node_type,
        bounds=Bounds(x=x, y=y, width=width, height=height),
        children=children,
        source_order=order,
        text=text,
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
        old_resource_id=old.resource_id if old else None,
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


def test_group_reskin_absorbs_decoration_and_retires_static_legacy_graph() -> None:
    component = _old("button", "component")
    legacy_graph = _old("legacy_bg", "graph", parent_id="button", child_index=0)
    loader = _old(
        "icon_bg",
        "loader",
        parent_id="button",
        child_index=1,
        behavior_roles=("runtime_object", "nested_instance"),
    )
    title = _old(
        "title",
        "text",
        parent_id="button",
        child_index=2,
        effective_text="Noble Reception",
    )
    bg = _node("bg", "shape", node_type="VECTOR", order=0)
    ornament = _node("ornament", "shape", node_type="VECTOR", x=108.0, y=106.0, width=284.0, height=68.0, order=1)
    text = _node(
        "title_psd",
        "type",
        node_type="TEXT",
        x=145.0,
        y=120.0,
        width=210.0,
        height=35.0,
        text="Noble Reception",
        order=2,
    )
    group = _node("button_group", "group", node_type="GROUP", children=(bg, ornament, text))

    draft = _draft(
        _item("old:button", old=component, node=group, status="matched", action="accept"),
        _item("old:legacy", old=legacy_graph),
        _item("old:loader", old=loader, node=bg, status="matched", action="accept"),
        _item("old:title", old=title, node=text, status="matched", action="accept"),
        _item("new:ornament", node=ornament, status="hifi_added", action="add_visual"),
    )

    result = normalize_psd_semantic_reskin(
        _inventory(component, legacy_graph, loader, title),
        _manifest(group),
        draft,
        owned_visual_validator=lambda _group, _owned, _retained: True,
    )
    by_old = {item.old_object_id: item for item in result.items if item.old_object_id}

    assert set(by_old["icon_bg"].owned_source_ids) == {"bg", "ornament"}
    assert by_old["icon_bg"].owned_group_id == "button_group"
    assert by_old["legacy_bg"].action == "keep_old"
    assert by_old["legacy_bg"].visual_disposition == "retire"
    assert not any(item.status == "hifi_added" and item.figma_node_id == "ornament" for item in result.items)


def test_group_reskin_reuses_multiple_existing_visual_hosts_before_adding() -> None:
    component = _old("challenge", "component")
    old_back = _old("bg_common_gary", "image", parent_id="challenge", child_index=0)
    old_front = _old(
        "icon_bg",
        "loader",
        parent_id="challenge",
        child_index=1,
        behavior_roles=("runtime_object", "nested_instance"),
    )
    back = _node("back", "pixel", order=0)
    front = _node("front", "shape", node_type="VECTOR", width=296.0, height=76.0, x=102.0, y=102.0, order=1)
    ornament = _node("center_ornament", "shape", node_type="VECTOR", x=220.0, y=105.0, width=60.0, height=70.0, order=2)
    group = _node("challenge_group", "group", node_type="GROUP", children=(back, front, ornament))

    draft = _draft(
        _item("old:challenge", old=component, node=group, status="matched", action="accept"),
        _item("old:back", old=old_back),
        _item("old:front", old=old_front, node=front, status="matched", action="accept"),
        _item("new:back", node=back, status="hifi_added", action="add_visual"),
        _item("new:ornament", node=ornament, status="hifi_added", action="add_visual"),
    )

    result = normalize_psd_semantic_reskin(
        _inventory(component, old_back, old_front),
        _manifest(group),
        draft,
        owned_visual_validator=lambda _group, _owned, _retained: True,
    )
    by_old = {item.old_object_id: item for item in result.items if item.old_object_id}

    assert by_old["bg_common_gary"].action == "accept"
    assert by_old["icon_bg"].action == "accept"
    assert by_old["bg_common_gary"].visual_disposition == "preserve"
    assert by_old["icon_bg"].visual_disposition == "preserve"
    assert {"back", "front", "center_ornament"} == (
        set(by_old["bg_common_gary"].owned_source_ids)
        | set(by_old["icon_bg"].owned_source_ids)
    )
    assert not any(item.status == "hifi_added" for item in result.items)


def test_unabsorbed_visual_inside_matched_group_fails_safe_instead_of_add() -> None:
    component = _old("label_only", "component")
    title = _old("title", "text", parent_id="label_only", effective_text="Only text")
    new_art = _node("new_art", "pixel")
    text = _node("text", "type", node_type="TEXT", text="Only text")
    group = _node("label_group", "group", node_type="GROUP", children=(new_art, text))
    draft = _draft(
        _item("old:component", old=component, node=group, status="matched", action="accept"),
        _item("old:title", old=title, node=text, status="matched", action="accept"),
        _item("new:art", node=new_art, status="hifi_added", action="add_visual"),
    )

    result = normalize_psd_semantic_reskin(
        _inventory(component, title),
        _manifest(group),
        draft,
        owned_visual_validator=lambda _group, _owned, _retained: True,
    )
    blocked = next(item for item in result.items if item.figma_node_id == "new_art")
    assert blocked.status == "blocked"
    assert blocked.action is None
    assert result.unresolved_count == 1
