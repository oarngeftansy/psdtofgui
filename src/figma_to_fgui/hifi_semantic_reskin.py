from __future__ import annotations

from collections.abc import Callable

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_replacement_models import (
    FguiComponentInventory,
    FguiObjectRef,
    HifiMappingDraft,
    HifiMappingItem,
)

OwnedVisualValidator = Callable[[str, frozenset[str], frozenset[str]], bool]

_VISUAL_OLD_TYPES = {"graph", "image", "loader"}
_VISUAL_PSD_KINDS = {"shape", "pixel", "smartobject"}


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


def _safe_to_retire(old: FguiObjectRef) -> bool:
    """Only globally retire paint that has no state/runtime visual contract.

    Policy 26 still implements retirement as a default visibility change in the
    patcher. Therefore a gear/controller/transition-driven child is not safe
    to retire globally. It remains visible until a state-aware writer can
    prove a target-page-only change.
    """
    return (
        old.default_visible
        and not old.structural_only
        and old.object_type.casefold() in _VISUAL_OLD_TYPES
        and not old.dynamic_properties
        and not old.controller_refs
        and not old.transition_refs
        and not old.behavior_roles
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
    direct_children: tuple[FguiObjectRef, ...],
    leaves: tuple[SelectionNode, ...],
) -> None:
    """Use unique runtime text as a semantic correspondence inside a bundle."""
    old_texts = [old for old in direct_children if old.object_type.casefold() in {"text", "richtext"}]
    source_texts = [node for node in leaves if node.type.upper() == "TEXT" and _normalized_text(node.text)]
    if not old_texts or not source_texts:
        return
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
        # Do not displace a confident mapping to another source text. This is
        # a semantic rescue for fgui_only/uncertain children, not a remapper.
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


def normalize_psd_semantic_reskin(
    inventory: FguiComponentInventory,
    manifest: SelectionManifest,
    draft: HifiMappingDraft,
    *,
    owned_visual_validator: OwnedVisualValidator | None = None,
) -> HifiMappingDraft:
    """Normalize a PSD reskin at semantic component/group granularity.

    ``build_mapping`` deliberately starts from object-level correspondences.
    For a reskin that is only the evidence-gathering layer: a PSD button/card
    group is the target visual bundle for the already existing FairyGUI
    component. Extra PSD decoration leaves are absorbed by existing visual
    hosts instead of becoming new display objects, and obsolete *static* old
    paint is retired while its object identity stays intact.

    The pass is intentionally conservative. It never retires controller,
    transition or runtime-driven visual children, and it leaves genuinely new
    PSD entities outside an already paired semantic group as ``add_visual``.
    """
    if not manifest.top_level_nodes or not manifest.top_level_nodes[0].id.startswith("psd-root:"):
        return draft

    nodes = _flatten(manifest)
    parents = _parent_ids(manifest)
    old_by_id = {old.object_id: old for old in inventory.objects}
    items = list(draft.items)
    component_pairs = _component_pairs(draft, nodes, old_by_id)
    if not component_pairs:
        return draft

    pair_by_old = {old.object_id: node.id for _, old, node in component_pairs}
    pair_group_ids = set(pair_by_old.values())
    absorbed_source_ids: set[str] = set()

    # Inner semantic components claim their own PSD subtree first. A parent
    # bundle must not rasterize a nested button/red-dot/card into its skin.
    depth: dict[str, int] = {}
    for group_id in pair_group_ids:
        value = 0
        current = parents.get(group_id)
        while current is not None:
            value += 1
            current = parents.get(current)
        depth[group_id] = value

    for _, old_component, group in sorted(component_pairs, key=lambda pair: depth[pair[2].id], reverse=True):
        direct_children = tuple(old for old in inventory.objects if old.parent_id == old_component.object_id)
        if not direct_children:
            continue

        leaves = _descendant_leaves(group)
        nested_group_ids = {
            source_group_id
            for child in direct_children
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

        _match_text_children(items, direct_children, local_leaves)

        visual_nodes = tuple(leaf for leaf in local_leaves if _psd_visual_leaf(leaf))
        visual_ids = {node.id for node in visual_nodes}
        if not visual_ids:
            continue
        all_local_ids = {leaf.id for leaf in local_leaves}

        item_index = {item.old_object_id: index for index, item in enumerate(items) if item.old_object_id}
        visual_hosts = [
            old for old in direct_children
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

        if not owners:
            candidates = [old for old in visual_hosts if _can_own_raster(old)]
            if not candidates:
                continue
            # Existing top-most image/loader is the least invasive host for a
            # whole reskinned surface; Graphs are only eligible when explicit
            # raster conversion was already authorized by the inventory.
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
                "graph_conversion_proven": (
                    item.graph_conversion_proven
                    or (chosen.object_type.casefold() == "graph" and chosen.raster_conversion_allowed)
                ),
            })))

        owner_anchor_ids = {item.figma_node_id for _, _, item in owners if item.figma_node_id in visual_ids}
        residual_ids = visual_ids - owner_anchor_ids
        # Decorative leaves do not represent new UI entities. Assign them to
        # the highest-z existing visual host so the PSD group becomes one
        # semantic MODIFY operation rather than MODIFY + ADD + ADD.
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

        # Any old static paint inside this already matched semantic component
        # that owns no target pixels is legacy skin, not a KEEP visual.
        for old in visual_hosts:
            if old.object_id in owner_ids:
                continue
            index = item_index.get(old.object_id)
            if index is None:
                continue
            item = items[index]
            if not _safe_to_retire(old):
                continue
            # Only retire an old child when its previous source correspondence
            # belongs to this same group or it was already unmatched. This
            # prevents a broad parent group from stealing a sibling entity.
            if item.figma_node_id is not None and item.figma_node_id not in visual_ids:
                continue
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

    if absorbed_source_ids:
        items = [
            item for item in items
            if not (
                item.old_object_id is None
                and item.status == "hifi_added"
                and item.figma_node_id in absorbed_source_ids
            )
        ]

    unresolved = sum(1 for item in items if item.action is None)
    return draft.model_copy(update={
        "items": tuple(items),
        "unresolved_count": unresolved,
    })
