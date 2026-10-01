from __future__ import annotations

import math
from collections.abc import Callable

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_replacement_models import (
    FguiComponentInventory,
    FguiObjectRef,
    HifiMappingDraft,
    HifiMappingItem,
)
from figma_to_fgui.hifi_semantic_pairing import recover_semantic_component_pairs

OwnedVisualValidator = Callable[[str, frozenset[str], frozenset[str]], bool]

_VISUAL_OLD_TYPES = {"graph", "image", "loader"}
_VISUAL_PSD_KINDS = {"shape", "pixel", "smartobject"}
_DANGEROUS_RETIRE_ROLES = {
    "controller_driven",
    "transition_target",
    "controller_action_target",
    "runtime_data",
    "unresolved_component_reference",
}
_STATE_DECISION_ROLES = {
    "controller_driven",
    "transition_target",
    "controller_action_target",
}


def _flatten(manifest: SelectionManifest) -> dict[str, SelectionNode]:
    result: dict[str, SelectionNode] = {}
    pending = list(manifest.top_level_nodes)
    while pending:
        node = pending.pop()
        result[node.id] = node
        pending.extend(node.children)
    return result


def _parent_ids(manifest: SelectionManifest) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    pending = [(root, None) for root in manifest.top_level_nodes]
    while pending:
        node, parent_id = pending.pop()
        result[node.id] = parent_id
        pending.extend((child, node.id) for child in node.children)
    return result


def _descendant_leaves(node: SelectionNode) -> tuple[SelectionNode, ...]:
    leaves: list[SelectionNode] = []
    pending = list(node.children)
    while pending:
        child = pending.pop()
        if not child.visible:
            continue
        if child.children:
            pending.extend(child.children)
        elif child.bounds.width > 0 and child.bounds.height > 0:
            leaves.append(child)
    return tuple(leaves)


def _is_descendant(node_id: str, ancestor_id: str, parents: dict[str, str | None]) -> bool:
    current = parents.get(node_id)
    seen: set[str] = set()
    while current is not None and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        current = parents.get(current)
    return False


def _bundle_children(
    component_id: str,
    children_by_parent: dict[str, list[FguiObjectRef]],
) -> tuple[FguiObjectRef, ...]:
    """Visual/text children owned by one semantic component.

    FairyGUI ``group`` nodes are layout/structure, not independent semantic UI
    entities. Their descendants therefore remain part of the component bundle.
    Nested component instances are semantic boundaries and are not traversed.
    """
    result: list[FguiObjectRef] = []
    pending = list(children_by_parent.get(component_id, ()))
    while pending:
        child = pending.pop()
        result.append(child)
        if child.object_type.casefold() == "component":
            continue
        if child.object_type.casefold() == "group" or child.structural_only:
            pending.extend(children_by_parent.get(child.object_id, ()))
    return tuple(result)


def _safe_to_retire(
    old: FguiObjectRef,
    old_by_id: dict[str, FguiObjectRef],
) -> bool:
    """Retire only paint whose visibility is not part of a known state contract."""
    if (
        not old.default_visible
        or old.structural_only
        or old.object_type.casefold() not in {"graph", "image"}
        or old.dynamic_properties
        or old.controller_refs
        or old.transition_refs
        or _DANGEROUS_RETIRE_ROLES.intersection(old.behavior_roles)
    ):
        return False
    parent = old_by_id.get(old.parent_id or "")
    if parent is not None and parent.auto_layout is not None and parent.layout_excludes_invisible:
        return False
    return True


def _requires_target_state_decision(old: FguiObjectRef) -> bool:
    """Return True when legacy paint cannot be globally kept or retired safely.

    A PSD reskin describes one target state. If an old visual inside the paired
    semantic component is controller/transition driven and owns no PSD pixels,
    silently keeping it recreates the old-skin overlay. Globally hiding it is
    equally unsafe because other controller states may still need it. Such an
    object must therefore remain unresolved until the writer has page-specific
    retirement evidence.
    """
    return (
        old.default_visible
        and not old.structural_only
        and old.object_type.casefold() in _VISUAL_OLD_TYPES
        and bool(
            old.dynamic_properties
            or old.controller_refs
            or old.transition_refs
            or _STATE_DECISION_ROLES.intersection(old.behavior_roles)
        )
    )


def _can_own_raster(old: FguiObjectRef) -> bool:
    kind = old.object_type.casefold()
    return kind in {"image", "loader"} or (kind == "graph" and old.raster_conversion_allowed)


def _psd_visual_leaf(node: SelectionNode) -> bool:
    return (
        node.visible
        and node.bounds.width > 0
        and node.bounds.height > 0
        and node.properties.get("psdKind", "").casefold() in _VISUAL_PSD_KINDS
    )


def _normalized_text(value: str | None) -> str:
    return "".join((value or "").casefold().split())


def _component_pairs(
    draft: HifiMappingDraft,
    nodes: dict[str, SelectionNode],
    old_by_id: dict[str, FguiObjectRef],
) -> tuple[tuple[HifiMappingItem, FguiObjectRef, SelectionNode], ...]:
    pairs: list[tuple[HifiMappingItem, FguiObjectRef, SelectionNode]] = []
    for item in draft.items:
        if item.action not in {"accept", "retarget"} or not item.old_object_id or not item.figma_node_id:
            continue
        old = old_by_id.get(item.old_object_id)
        node = nodes.get(item.figma_node_id)
        if old is None or node is None:
            continue
        if old.object_type.casefold() != "component" or node.type.upper() not in {"GROUP", "COMPONENT", "INSTANCE"}:
            continue
        if not node.children:
            continue
        pairs.append((item, old, node))
    return tuple(pairs)


def _match_text_children(
    items: list[HifiMappingItem],
    bundle_children: tuple[FguiObjectRef, ...],
    leaves: tuple[SelectionNode, ...],
) -> set[str]:
    """Use unique runtime text as a semantic correspondence inside a bundle."""
    old_texts = [old for old in bundle_children if old.object_type.casefold() in {"text", "richtext"}]
    source_texts = [node for node in leaves if node.type.upper() == "TEXT" and _normalized_text(node.text)]
    if not old_texts or not source_texts:
        return set()
    matched_ids: set[str] = set()
    item_index = {item.old_object_id: index for index, item in enumerate(items) if item.old_object_id}
    for old in old_texts:
        key = _normalized_text(old.effective_text)
        if not key:
            continue
        matches = [node for node in source_texts if _normalized_text(node.text) == key]
        if len(matches) != 1:
            continue
        index = item_index.get(old.object_id)
        if index is None:
            continue
        item = items[index]
        node = matches[0]
        if item.action in {"accept", "retarget"} and item.figma_node_id not in {None, node.id}:
            continue
        items[index] = item.model_copy(update={
            "figma_node_id": node.id,
            "figma_name": node.name,
            "status": "matched",
            "action": "accept",
            "visual_disposition": "preserve",
            "preserve_runtime_text": old.runtime_text_override,
        })
        matched_ids.add(node.id)
    return matched_ids


def _source_box(root: SelectionNode, node: SelectionNode) -> tuple[float, float, float, float]:
    viewport = root.properties.get("psdViewportBounds")
    offset_x = float(viewport[0]) if isinstance(viewport, (tuple, list)) and len(viewport) >= 2 else 0.0
    offset_y = float(viewport[1]) if isinstance(viewport, (tuple, list)) and len(viewport) >= 2 else 0.0
    return (
        node.bounds.x - root.bounds.x - offset_x,
        node.bounds.y - root.bounds.y - offset_y,
        node.bounds.width,
        node.bounds.height,
    )


def _geometry_affinity(old: FguiObjectRef, node: SelectionNode, root: SelectionNode) -> float:
    """Conservative geometry affinity for assigning a PSD leaf to a free host."""
    if min(old.width, old.height, node.bounds.width, node.bounds.height) <= 0:
        return 0.0
    node_x, node_y, node_width, node_height = _source_box(root, node)
    old_center = (old.x + old.width / 2, old.y + old.height / 2)
    new_center = (node_x + node_width / 2, node_y + node_height / 2)
    scale = max(old.width, old.height, node_width, node_height, 1.0)
    center_score = max(0.0, 1.0 - math.dist(old_center, new_center) / (scale * 1.5))
    old_area = old.width * old.height
    new_area = node_width * node_height
    area_score = min(old_area, new_area) / max(old_area, new_area)
    return center_score * 0.6 + area_score * 0.4


def _allocate_free_hosts(
    items: list[HifiMappingItem],
    visual_hosts: list[FguiObjectRef],
    owners: list[tuple[FguiObjectRef, int, HifiMappingItem]],
    visual_nodes: tuple[SelectionNode, ...],
    root: SelectionNode,
) -> None:
    """Prefer MODIFY of existing hosts before absorbing leaves into one owner."""
    item_index = {item.old_object_id: index for index, item in enumerate(items) if item.old_object_id}
    owner_ids = {old.object_id for old, _, _ in owners}
    claimed_nodes = {item.figma_node_id for _, _, item in owners if item.figma_node_id}
    remaining = [node for node in visual_nodes if node.id not in claimed_nodes]
    free_hosts = [old for old in visual_hosts if old.object_id not in owner_ids and _can_own_raster(old)]
    for old in sorted(free_hosts, key=lambda value: value.child_index, reverse=True):
        if not remaining:
            break
        ranked = sorted(
            ((_geometry_affinity(old, node, root), node) for node in remaining),
            key=lambda pair: (pair[0], pair[1].bounds.width * pair[1].bounds.height),
            reverse=True,
        )
        affinity, node = ranked[0]
        # A tiny ornament should not resize a full-size runtime loader merely
        # to reduce ADD count. Low-affinity residuals are bundled later.
        if affinity < 0.48:
            continue
        index = item_index.get(old.object_id)
        if index is None:
            continue
        item = items[index].model_copy(update={
            "figma_node_id": node.id,
            "figma_name": node.name,
            "status": "matched",
            "action": "accept",
            "visual_disposition": "preserve",
            "graph_conversion_proven": (
                items[index].graph_conversion_proven
                or (old.object_type.casefold() == "graph" and old.raster_conversion_allowed)
            ),
        })
        owners.append((old, index, item))
        remaining.remove(node)


def normalize_psd_semantic_reskin(
    inventory: FguiComponentInventory,
    manifest: SelectionManifest,
    draft: HifiMappingDraft,
    *,
    owned_visual_validator: OwnedVisualValidator | None = None,
) -> HifiMappingDraft:
    """Normalize a PSD reskin at semantic component/group granularity.

    The existing mapper remains responsible for evidence and initial pairing.
    This pass changes the *unit of action*: a paired FairyGUI component and PSD
    group form one semantic reskin bundle. PSD decoration leaves are allocated
    to existing image/loader/convertible-graph hosts before any leaf is allowed
    to become a new display object. Obsolete static legacy paint may retire;
    state-driven paint without target-page evidence is blocked instead of being
    silently kept or globally hidden.
    """
    if not manifest.top_level_nodes or not manifest.top_level_nodes[0].id.startswith("psd-root:"):
        return draft

    # Establish the semantic component ↔ PSD Group correspondence before
    # allocating individual leaves. This is the policy-26 unit of action.
    draft = recover_semantic_component_pairs(inventory, manifest, draft)

    nodes = _flatten(manifest)
    parents = _parent_ids(manifest)
    old_by_id = {old.object_id: old for old in inventory.objects}
    children_by_parent: dict[str, list[FguiObjectRef]] = {}
    for old in inventory.objects:
        if old.parent_id:
            children_by_parent.setdefault(old.parent_id, []).append(old)
    items = list(draft.items)
    component_pairs = _component_pairs(draft, nodes, old_by_id)
    if not component_pairs:
        return draft

    pair_by_old = {old.object_id: node.id for _, old, node in component_pairs}
    pair_group_ids = set(pair_by_old.values())
    absorbed_source_ids: set[str] = set()
    reused_text_ids: set[str] = set()
    matched_group_visual_ids: set[str] = set()

    depth: dict[str, int] = {}
    for group_id in pair_group_ids:
        value = 0
        current = parents.get(group_id)
        while current is not None:
            value += 1
            current = parents.get(current)
        depth[group_id] = value

    for _, old_component, group in sorted(component_pairs, key=lambda pair: depth[pair[2].id], reverse=True):
        bundle_children = _bundle_children(old_component.object_id, children_by_parent)
        if not bundle_children:
            continue

        leaves = _descendant_leaves(group)
        nested_group_ids = {
            source_group_id
            for child in bundle_children
            if child.object_type.casefold() == "component"
            for source_group_id in (pair_by_old.get(child.object_id),)
            if source_group_id and source_group_id != group.id
        }
        delegated_ids = {
            leaf.id
            for leaf in leaves
            if any(_is_descendant(leaf.id, nested_id, parents) or leaf.id == nested_id for nested_id in nested_group_ids)
        }
        local_leaves = tuple(leaf for leaf in leaves if leaf.id not in delegated_ids)
        if not local_leaves:
            continue

        reused_text_ids.update(_match_text_children(items, bundle_children, local_leaves))

        visual_nodes = tuple(leaf for leaf in local_leaves if _psd_visual_leaf(leaf))
        visual_ids = {node.id for node in visual_nodes}
        if not visual_ids:
            continue
        matched_group_visual_ids.update(visual_ids)
        all_local_ids = {leaf.id for leaf in local_leaves}

        item_index = {item.old_object_id: index for index, item in enumerate(items) if item.old_object_id}
        visual_hosts = [
            old for old in bundle_children
            if old.object_type.casefold() in _VISUAL_OLD_TYPES and not old.structural_only and not old.out_of_scope
        ]
        if not visual_hosts:
            continue

        owners: list[tuple[FguiObjectRef, int, HifiMappingItem]] = []
        for old in visual_hosts:
            index = item_index.get(old.object_id)
            if index is None:
                continue
            item = items[index]
            if (
                item.action in {"accept", "retarget"}
                and item.figma_node_id in visual_ids
                and _can_own_raster(old)
            ):
                owners.append((old, index, item))

        _allocate_free_hosts(items, visual_hosts, owners, visual_nodes, manifest.top_level_nodes[0])

        if not owners:
            candidates = [old for old in visual_hosts if _can_own_raster(old)]
            if not candidates:
                continue
            chosen = max(
                candidates,
                key=lambda old: (
                    old.object_type.casefold() in {"image", "loader"},
                    old.child_index,
                    old.width * old.height,
                ),
            )
            index = item_index.get(chosen.object_id)
            if index is None:
                continue
            anchor = max(
                visual_nodes,
                key=lambda node: (node.bounds.width * node.bounds.height, node.source_order, node.id),
            )
            item = items[index]
            owners.append((chosen, index, item.model_copy(update={
                "figma_node_id": anchor.id,
                "figma_name": anchor.name,
                "status": "matched",
                "action": "accept",
                "visual_disposition": "preserve",
                "graph_conversion_proven": (
                    item.graph_conversion_proven
                    or (chosen.object_type.casefold() == "graph" and chosen.raster_conversion_allowed)
                ),
            })))

        owner_anchor_ids = {item.figma_node_id for _, _, item in owners if item.figma_node_id in visual_ids}
        residual_ids = visual_ids - owner_anchor_ids
        residual_owner = max(owners, key=lambda entry: entry[0].child_index)

        proposed: list[tuple[FguiObjectRef, int, HifiMappingItem, set[str]]] = []
        for old, index, item in owners:
            owned_ids = {item.figma_node_id} if item.figma_node_id in visual_ids else set()
            if old.object_id == residual_owner[0].object_id:
                owned_ids |= residual_ids
            if not owned_ids:
                continue
            retained_ids = all_local_ids - owned_ids
            if owned_visual_validator is not None and not owned_visual_validator(
                group.id, frozenset(owned_ids), frozenset(retained_ids)
            ):
                proposed = []
                break
            proposed.append((old, index, item, owned_ids))

        if not proposed:
            continue

        owner_ids = {old.object_id for old, _, _, _ in proposed}
        for old, index, item, owned_ids in proposed:
            anchor_id = item.figma_node_id if item.figma_node_id in owned_ids else min(owned_ids)
            anchor = nodes.get(anchor_id)
            items[index] = item.model_copy(update={
                "figma_node_id": anchor_id,
                "figma_name": anchor.name if anchor is not None else item.figma_name,
                "owned_source_ids": tuple(sorted(owned_ids)),
                "owned_group_id": group.id,
                "retained_source_ids": tuple(sorted(all_local_ids - owned_ids)),
                "status": "matched",
                "action": "accept",
                "visual_disposition": "preserve",
                "graph_conversion_proven": (
                    item.graph_conversion_proven
                    or (old.object_type.casefold() == "graph" and old.raster_conversion_allowed)
                ),
            })
            absorbed_source_ids.update(owned_ids)

        for old in visual_hosts:
            if old.object_id in owner_ids:
                continue
            index = item_index.get(old.object_id)
            if index is None:
                continue
            item = items[index]
            if item.figma_node_id is not None and item.figma_node_id not in visual_ids:
                continue
            if _safe_to_retire(old, old_by_id):
                items[index] = item.model_copy(update={
                    "figma_node_id": None,
                    "figma_name": None,
                    "figma_bounds": None,
                    "owned_source_ids": (),
                    "owned_group_id": None,
                    "retained_source_ids": (),
                    "status": "fgui_only",
                    "action": "keep_old",
                    "visual_disposition": "retire",
                    "graph_conversion_proven": False,
                })
                continue
            if _requires_target_state_decision(old):
                # The component/group pairing proves this object belongs to the
                # reskinned semantic region, but its controller/transition state
                # means neither KEEP nor global RETIRE is justified. Stop here
                # instead of generating a candidate with old pixels layered on
                # top of the new PSD skin.
                items[index] = item.model_copy(update={
                    "status": "blocked",
                    "action": None,
                    "visual_disposition": "other_state",
                })

    reused_source_ids = absorbed_source_ids | reused_text_ids
    if reused_source_ids:
        items = [
            item for item in items
            if not (
                item.old_object_id is None
                and item.status == "hifi_added"
                and item.figma_node_id in reused_source_ids
            )
        ]

    # Inside a semantic component/group pair a decorative leaf is not allowed
    # to silently become a new FairyGUI object. If bundle ownership could not
    # absorb it, keep the mapping unresolved so the pipeline fails safe.
    normalized: list[HifiMappingItem] = []
    for item in items:
        if (
            item.old_object_id is None
            and item.status == "hifi_added"
            and item.figma_node_id in matched_group_visual_ids
            and item.figma_node_id not in absorbed_source_ids
        ):
            normalized.append(item.model_copy(update={
                "status": "blocked",
                "action": None,
            }))
        else:
            normalized.append(item)

    unresolved = sum(1 for item in normalized if item.action is None)
    return draft.model_copy(update={
        "items": tuple(normalized),
        "unresolved_count": unresolved,
    })
