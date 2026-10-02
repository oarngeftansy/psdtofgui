from __future__ import annotations

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
_STATE_DRIVEN_ROLES = {
    "controller_driven",
    "transition_target",
    "controller_action_target",
}
_RUNTIME_VISUAL_ROLES = {
    "runtime_data",
    "runtime_object",
    "nested_behavior",
    "instance_parameterized",
    "unresolved_component_reference",
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


def _is_descendant(node_id: str, ancestor_id: str, parents: dict[str, str | None]) -> bool:
    current = parents.get(node_id)
    seen: set[str] = set()
    while current is not None and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        current = parents.get(current)
    return False


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


def _bundle_children(
    component_id: str,
    children_by_parent: dict[str, list[FguiObjectRef]],
) -> tuple[FguiObjectRef, ...]:
    """Return the local legacy bundle, crossing FGUI groups but not components."""
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


def _state_driven(old: FguiObjectRef) -> bool:
    return bool(
        old.dynamic_properties
        or old.controller_refs
        or old.transition_refs
        or _STATE_DRIVEN_ROLES.intersection(old.behavior_roles)
    )


def _runtime_visual(old: FguiObjectRef) -> bool:
    return bool(_RUNTIME_VISUAL_ROLES.intersection(old.behavior_roles))


def _can_own_raster(old: FguiObjectRef) -> bool:
    kind = old.object_type.casefold()
    return kind in {"image", "loader"} or (
        kind == "graph" and old.raster_conversion_allowed
    )


def _safe_static_retire(old: FguiObjectRef, old_by_id: dict[str, FguiObjectRef]) -> bool:
    """Retire only static target-state paint; never retire runtime/state mechanics."""
    if (
        not old.default_visible
        or old.structural_only
        or old.object_type.casefold() not in {"graph", "image"}
        or _state_driven(old)
        or _runtime_visual(old)
    ):
        return False
    parent = old_by_id.get(old.parent_id or "")
    if parent is not None and parent.auto_layout is not None and parent.layout_excludes_invisible:
        return False
    return True


def _psd_visual_leaf(node: SelectionNode) -> bool:
    return (
        node.visible
        and node.bounds.width > 0
        and node.bounds.height > 0
        and node.properties.get("psdKind", "").casefold() in _VISUAL_PSD_KINDS
    )


def _normalized_text(value: str | None) -> str:
    return "".join((value or "").casefold().split())


def _strip_legacy_owned_visuals(draft: HifiMappingDraft) -> HifiMappingDraft:
    """Quarantine pre-Policy-27 leaf/group ownership from the raw matcher.

    `build_mapping` still contains compatibility heuristics used by older/direct
    mapping flows. In the PSD semantic-reskin path those ownership decisions are
    not authoritative: Policy 27 is the single owner of Component/Group bundle
    allocation. We retain correspondence evidence (`figma_node_id`, score,
    action) as a hint, but discard prior group/composite pixel ownership.
    """
    items = tuple(
        item.model_copy(update={
            "owned_source_ids": (),
            "owned_group_id": None,
            "retained_source_ids": (),
            "visual_echo": False,
            "composite_group_id": None,
            "composite_source_ids": (),
        })
        for item in draft.items
    )
    return draft.model_copy(update={"items": items})


def _component_pairs(
    draft: HifiMappingDraft,
    nodes: dict[str, SelectionNode],
    old_by_id: dict[str, FguiObjectRef],
) -> tuple[tuple[int, HifiMappingItem, FguiObjectRef, SelectionNode], ...]:
    pairs: list[tuple[int, HifiMappingItem, FguiObjectRef, SelectionNode]] = []
    for index, item in enumerate(draft.items):
        if (
            item.action not in {"accept", "retarget"}
            or not item.old_object_id
            or not item.figma_node_id
        ):
            continue
        old = old_by_id.get(item.old_object_id)
        node = nodes.get(item.figma_node_id)
        if old is None or node is None:
            continue
        if (
            old.object_type.casefold() != "component"
            or node.type.upper() not in {"GROUP", "COMPONENT", "INSTANCE"}
            or not node.children
        ):
            continue
        pairs.append((index, item, old, node))
    return tuple(pairs)


def _match_text_children(
    items: list[HifiMappingItem],
    bundle_children: tuple[FguiObjectRef, ...],
    leaves: tuple[SelectionNode, ...],
) -> set[str]:
    """Keep native FairyGUI text and use PSD text only as restyle evidence."""
    old_texts = [
        old
        for old in bundle_children
        if old.object_type.casefold() in {"text", "richtext"}
    ]
    source_texts = [
        node
        for node in leaves
        if node.type.upper() == "TEXT" and _normalized_text(node.text)
    ]
    matched_ids: set[str] = set()
    if not old_texts or not source_texts:
        return matched_ids

    item_index = {
        item.old_object_id: index
        for index, item in enumerate(items)
        if item.old_object_id
    }
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
        if (
            item.action in {"accept", "retarget"}
            and item.figma_node_id not in {None, node.id}
        ):
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


def _host_priority(old: FguiObjectRef, item: HifiMappingItem) -> tuple[int, int, int, float, float]:
    """Prefer static raster hosts, then proven correspondence, then z/area."""
    kind = old.object_type.casefold()
    static = not _state_driven(old) and not _runtime_visual(old)
    raster_host = kind in {"image", "loader"}
    return (
        1 if static else 0,
        1 if raster_host else 0,
        1 if item.figma_node_id else 0,
        item.score,
        old.child_index + old.width * old.height / 1_000_000_000,
    )


def _anchor_node(
    visual_nodes: tuple[SelectionNode, ...],
    preferred_id: str | None,
) -> SelectionNode:
    for node in visual_nodes:
        if node.id == preferred_id:
            return node
    # Stable visual anchor: prefer the largest rendered leaf, then document/z
    # order, then id. Never depend on lexicographic min(owned_ids).
    return max(
        visual_nodes,
        key=lambda node: (
            node.bounds.width * node.bounds.height,
            node.source_order,
            node.id,
        ),
    )


def normalize_psd_semantic_reskin(
    inventory: FguiComponentInventory,
    manifest: SelectionManifest,
    draft: HifiMappingDraft,
    *,
    owned_visual_validator: OwnedVisualValidator | None = None,
) -> HifiMappingDraft:
    """Policy 27: Component -> Visual Bundle -> PSD Group is the PSD default.

    The runtime Component remains the same object. A matched PSD Group replaces
    its current visual bundle as one semantic operation. Leaf correspondences
    are hints only; decorative leaves never create business-level ADD objects.
    """
    if (
        not manifest.top_level_nodes
        or not manifest.top_level_nodes[0].id.startswith("psd-root:")
    ):
        return draft

    # The raw matcher may still emit compatibility-era ownership metadata. Do
    # not let a legacy leaf allocator compete with Policy 27.
    draft = _strip_legacy_owned_visuals(draft)
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

    pair_by_old = {old.object_id: group.id for _, _, old, group in component_pairs}
    matched_group_leaf_ids: set[str] = set()
    absorbed_source_ids: set[str] = set()
    reused_text_ids: set[str] = set()

    depth: dict[str, int] = {}
    for group_id in pair_by_old.values():
        value = 0
        current = parents.get(group_id)
        while current is not None:
            value += 1
            current = parents.get(current)
        depth[group_id] = value

    # Nested semantic groups are handled first so a parent cannot steal child
    # pixels or flatten a nested component's runtime contract.
    for component_index, component_item, old_component, group in sorted(
        component_pairs,
        key=lambda pair: depth[pair[3].id],
        reverse=True,
    ):
        items[component_index] = component_item.model_copy(update={
            "owned_group_id": group.id,
            "status": "matched",
            "action": "accept",
        })

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
            if any(
                leaf.id == nested_id or _is_descendant(leaf.id, nested_id, parents)
                for nested_id in nested_group_ids
            )
        }
        local_leaves = tuple(leaf for leaf in leaves if leaf.id not in delegated_ids)
        if not local_leaves:
            continue

        local_ids = {leaf.id for leaf in local_leaves}
        matched_group_leaf_ids.update(local_ids)
        reused_text_ids.update(_match_text_children(items, bundle_children, local_leaves))

        visual_nodes = tuple(leaf for leaf in local_leaves if _psd_visual_leaf(leaf))
        visual_ids = {node.id for node in visual_nodes}
        if not visual_ids:
            continue

        item_index = {
            item.old_object_id: index
            for index, item in enumerate(items)
            if item.old_object_id
        }
        visual_hosts = [
            old
            for old in bundle_children
            if (
                old.object_type.casefold() in _VISUAL_OLD_TYPES
                and not old.structural_only
                and not old.out_of_scope
            )
        ]
        current_hosts = [
            old
            for old in visual_hosts
            if old.default_visible and _can_own_raster(old)
        ]

        # A semantic bundle has one default raster owner. This intentionally
        # replaces the old per-leaf partitioner, which was the source of
        # KEEP+ADD/duplicate-pixel instability. Existing leaf matches influence
        # host choice but do not create multiple business owners.
        owner: FguiObjectRef | None = None
        owner_index: int | None = None
        owner_item: HifiMappingItem | None = None
        candidates: list[tuple[FguiObjectRef, int, HifiMappingItem]] = []
        for old in current_hosts:
            index = item_index.get(old.object_id)
            if index is None:
                continue
            candidates.append((old, index, items[index]))
        if candidates:
            owner, owner_index, owner_item = max(
                candidates,
                key=lambda entry: _host_priority(entry[0], entry[2]),
            )

        owner_ids: set[str] = set()
        if owner is not None and owner_index is not None and owner_item is not None:
            retained_ids = local_ids - visual_ids
            valid = (
                owned_visual_validator is None
                or owned_visual_validator(
                    group.id,
                    frozenset(visual_ids),
                    frozenset(retained_ids),
                )
            )
            if valid:
                anchor = _anchor_node(visual_nodes, owner_item.figma_node_id)
                items[owner_index] = owner_item.model_copy(update={
                    "figma_node_id": anchor.id,
                    "figma_name": anchor.name,
                    "owned_source_ids": tuple(sorted(visual_ids)),
                    "owned_group_id": group.id,
                    "retained_source_ids": tuple(sorted(retained_ids)),
                    "status": "matched",
                    "action": "accept",
                    "visual_disposition": "preserve",
                    "graph_conversion_proven": (
                        owner_item.graph_conversion_proven
                        or (
                            owner.object_type.casefold() == "graph"
                            and owner.raster_conversion_allowed
                        )
                    ),
                })
                absorbed_source_ids.update(visual_ids)
                owner_ids.add(owner.object_id)

        # Every old visual in a matched semantic bundle receives an explicit
        # target-state disposition. Silent current-state KEEP is forbidden.
        for old in visual_hosts:
            if old.object_id in owner_ids:
                continue
            index = item_index.get(old.object_id)
            if index is None:
                continue
            item = items[index]

            # A source match outside this semantic group belongs to another
            # proven region and is not rewritten by this bundle.
            if item.figma_node_id is not None and item.figma_node_id not in visual_ids:
                continue

            if not old.default_visible:
                items[index] = item.model_copy(update={
                    "figma_node_id": None,
                    "figma_name": None,
                    "figma_bounds": None,
                    "owned_source_ids": (),
                    "owned_group_id": None,
                    "retained_source_ids": (),
                    "status": "fgui_only",
                    "action": "keep_old",
                    "visual_disposition": "other_state",
                    "graph_conversion_proven": False,
                })
                continue

            if _safe_static_retire(old, old_by_id):
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

            # Current-state dynamic/runtime paint without bundle ownership is
            # unsafe: keeping it overlays old skin; hiding it can break another
            # runtime state. Preserve logic and stop candidate generation.
            items[index] = item.model_copy(update={
                "status": "blocked",
                "action": None,
                "visual_disposition": "preserve",
            })

    reused_source_ids = absorbed_source_ids | reused_text_ids
    if reused_source_ids:
        items = [
            item
            for item in items
            if not (
                item.old_object_id is None
                and item.status == "hifi_added"
                and item.figma_node_id in reused_source_ids
            )
        ]

    # Inside a matched semantic group, residual leaves are implementation
    # details, never independent ADDs. If a bundle could not absorb them, fail
    # safe and surface the mapping conflict.
    normalized: list[HifiMappingItem] = []
    for item in items:
        if (
            item.old_object_id is None
            and item.status == "hifi_added"
            and item.figma_node_id in matched_group_leaf_ids
            and item.figma_node_id not in reused_source_ids
        ):
            normalized.append(item.model_copy(update={
                "status": "blocked",
                "action": None,
            }))
        else:
            normalized.append(item)

    # Defensive invariant: one PSD visual leaf can have only one target owner.
    seen_source_ids: set[str] = set()
    duplicate_source_ids: set[str] = set()
    for item in normalized:
        if item.action not in {"accept", "retarget"}:
            continue
        for source_id in item.owned_source_ids:
            if source_id in seen_source_ids:
                duplicate_source_ids.add(source_id)
            seen_source_ids.add(source_id)
    if duplicate_source_ids:
        normalized = [
            item.model_copy(update={"status": "blocked", "action": None})
            if set(item.owned_source_ids).intersection(duplicate_source_ids)
            else item
            for item in normalized
        ]

    unresolved = sum(1 for item in normalized if item.action is None)
    return draft.model_copy(update={
        "items": tuple(normalized),
        "unresolved_count": unresolved,
    })