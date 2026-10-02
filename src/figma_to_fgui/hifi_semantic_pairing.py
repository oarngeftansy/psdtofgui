from __future__ import annotations

import math

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_replacement_models import (
    FguiComponentInventory,
    FguiObjectRef,
    HifiMappingDraft,
)


def _normalized(value: str | None) -> str:
    return "".join(character.casefold() for character in (value or "") if character.isalnum())


def _flatten(manifest: SelectionManifest) -> tuple[SelectionNode, ...]:
    result: list[SelectionNode] = []
    pending = list(reversed(manifest.top_level_nodes))
    while pending:
        node = pending.pop()
        result.append(node)
        pending.extend(reversed(node.children))
    return tuple(result)


def _parents(manifest: SelectionManifest) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    pending: list[tuple[SelectionNode, str | None]] = [
        (root, None) for root in manifest.top_level_nodes
    ]
    while pending:
        node, parent = pending.pop()
        result[node.id] = parent
        pending.extend((child, node.id) for child in node.children)
    return result


def _depth(node_id: str, parents: dict[str, str | None]) -> int:
    value = 0
    current = parents.get(node_id)
    while current is not None:
        value += 1
        current = parents.get(current)
    return value


def _bundle_children(
    component_id: str,
    children_by_parent: dict[str, list[FguiObjectRef]],
) -> tuple[FguiObjectRef, ...]:
    """Return one semantic component's local bundle, crossing FGUI groups only."""
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


def _leaves(node: SelectionNode) -> tuple[SelectionNode, ...]:
    result: list[SelectionNode] = []
    pending = list(node.children)
    while pending:
        child = pending.pop()
        if not child.visible:
            continue
        if child.children:
            pending.extend(child.children)
        elif child.bounds.width > 0 and child.bounds.height > 0:
            result.append(child)
    return tuple(result)


def _source_box(
    root: SelectionNode,
    node: SelectionNode,
) -> tuple[float, float, float, float]:
    viewport = root.properties.get("psdViewportBounds")
    offset_x = 0.0
    offset_y = 0.0
    if isinstance(viewport, (tuple, list)) and len(viewport) >= 2:
        try:
            offset_x = float(viewport[0])
            offset_y = float(viewport[1])
        except (TypeError, ValueError):
            offset_x = offset_y = 0.0
    return (
        node.bounds.x - root.bounds.x - offset_x,
        node.bounds.y - root.bounds.y - offset_y,
        node.bounds.width,
        node.bounds.height,
    )


def _content_box(
    root: SelectionNode,
    group: SelectionNode,
) -> tuple[float, float, float, float] | None:
    boxes = [_source_box(root, leaf) for leaf in _leaves(group)]
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return left, top, right - left, bottom - top


def _geometry_score(
    old: FguiObjectRef,
    box: tuple[float, float, float, float],
) -> float:
    if min(old.width, old.height, box[2], box[3]) <= 0:
        return 0.0
    old_center = (old.x + old.width / 2, old.y + old.height / 2)
    new_center = (box[0] + box[2] / 2, box[1] + box[3] / 2)
    scale = max(old.width, old.height, box[2], box[3], 1.0)
    position = max(0.0, 1.0 - math.dist(old_center, new_center) / (scale * 1.35))
    old_area = old.width * old.height
    new_area = box[2] * box[3]
    size = min(old_area, new_area) / max(old_area, new_area)
    return position * 0.65 + size * 0.35


def _normalized_bounds(
    root: SelectionNode,
    box: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    width = max(root.bounds.width, 1.0)
    height = max(root.bounds.height, 1.0)
    return (
        max(0.0, min(1.0, box[0] / width)),
        max(0.0, min(1.0, box[1] / height)),
        max(0.0, min(1.0, box[2] / width)),
        max(0.0, min(1.0, box[3] / height)),
    )


def _semantic_texts(children: tuple[FguiObjectRef, ...]) -> set[str]:
    return {
        _normalized(child.effective_text)
        for child in children
        if child.object_type.casefold() in {"text", "richtext"}
        and _normalized(child.effective_text)
    }


def _group_texts(group: SelectionNode) -> set[str]:
    return {
        _normalized(leaf.text)
        for leaf in _leaves(group)
        if leaf.type.upper() == "TEXT" and _normalized(leaf.text)
    }


def _candidate_score(
    old: FguiObjectRef,
    bundle: tuple[FguiObjectRef, ...],
    group: SelectionNode,
    root: SelectionNode,
    parents: dict[str, str | None],
) -> tuple[float, int, float] | None:
    box = _content_box(root, group)
    if box is None:
        return None
    geometry = _geometry_score(old, box)
    text_matches = len(_semantic_texts(bundle) & _group_texts(group))
    name = 1.0 if _normalized(old.name) and _normalized(old.name) == _normalized(group.name) else 0.0

    # A unique semantic text is the strongest signal for generic-named FGUI
    # instances. Without text evidence, require very close geometry so a broad
    # panel group cannot steal a repeated button/card instance.
    if text_matches:
        if geometry < 0.18:
            return None
        score = min(1.0, 0.58 + min(text_matches, 3) * 0.08 + geometry * 0.26 + name * 0.08)
    else:
        if geometry < 0.80:
            return None
        score = min(1.0, geometry * 0.88 + name * 0.12)
    return score, text_matches, geometry


def recover_semantic_component_pairs(
    inventory: FguiComponentInventory,
    manifest: SelectionManifest,
    draft: HifiMappingDraft,
) -> HifiMappingDraft:
    """Recover component↔PSD-group pairs before leaf ownership is decided.

    The object matcher is intentionally conservative and can leave an existing
    FGUI component as ``keep_old`` while its PSD descendants become additions.
    In PSD reskin mode this reverses the intended semantics. This pass promotes
    a group to the existing component when either unique semantic text plus
    compatible geometry, or very strong geometry alone, proves the bundle.
    Ambiguous repeated controls are left unresolved rather than guessed.
    """
    if not manifest.top_level_nodes or not manifest.top_level_nodes[0].id.startswith("psd-root:"):
        return draft
    root = manifest.top_level_nodes[0]
    nodes = _flatten(manifest)
    parents = _parents(manifest)
    groups = tuple(
        node for node in nodes
        if node.type.upper() == "GROUP"
        and node.visible
        and node.children
        and node.properties.get("psdKind") == "group"
    )
    if not groups:
        return draft

    old_by_id = {old.object_id: old for old in inventory.objects}
    children_by_parent: dict[str, list[FguiObjectRef]] = {}
    for old in inventory.objects:
        if old.parent_id:
            children_by_parent.setdefault(old.parent_id, []).append(old)

    items = list(draft.items)
    item_index = {item.old_object_id: index for index, item in enumerate(items) if item.old_object_id}
    paired_components = {
        item.old_object_id
        for item in items
        if item.old_object_id
        and item.figma_node_id
        and item.action in {"accept", "retarget"}
        and old_by_id.get(item.old_object_id) is not None
        and old_by_id[item.old_object_id].object_type.casefold() == "component"
        and any(node.id == item.figma_node_id and node.type.upper() == "GROUP" for node in groups)
    }
    reserved_groups = {
        item.figma_node_id
        for item in items
        if item.figma_node_id
        and item.action in {"accept", "retarget"}
        and item.old_object_id in paired_components
    }

    proposals: list[tuple[float, int, float, int, str, str]] = []
    for old in inventory.objects:
        if old.object_type.casefold() != "component" or old.object_id in paired_components or old.out_of_scope:
            continue
        index = item_index.get(old.object_id)
        if index is None:
            continue
        current = items[index]
        # Never override a correspondence already accepted to a different
        # semantic node. This pass only rescues keep/uncertain component roots.
        if current.action in {"accept", "retarget"} and current.figma_node_id:
            continue
        bundle = _bundle_children(old.object_id, children_by_parent)
        if not bundle:
            continue
        ranked: list[tuple[float, int, float, int, SelectionNode]] = []
        for group in groups:
            if group.id in reserved_groups:
                continue
            candidate = _candidate_score(old, bundle, group, root, parents)
            if candidate is None:
                continue
            score, text_matches, geometry = candidate
            ranked.append((score, text_matches, geometry, _depth(group.id, parents), group))
        ranked.sort(
            key=lambda value: (
                value[0], value[1], value[2], value[3],
                -(value[4].bounds.width * value[4].bounds.height),
            ),
            reverse=True,
        )
        if not ranked:
            continue
        best = ranked[0]
        runner_up = ranked[1] if len(ranked) > 1 else None
        margin = best[0] - runner_up[0] if runner_up else 1.0
        # Exact semantic text can tolerate close geometric alternatives, but a
        # geometry-only repeated control needs a clear margin.
        required_margin = 0.015 if best[1] else 0.08
        if margin < required_margin:
            continue
        proposals.append((best[0], best[1], best[2], best[3], old.object_id, best[4].id))

    # Greedy highest-confidence one-to-one assignment. Nested/smaller groups
    # win score ties, preventing a broad parent region from consuming a child.
    proposals.sort(key=lambda value: (-value[0], -value[1], -value[2], -value[3], value[4], value[5]))
    claimed_groups = set(reserved_groups)
    for score, _, _, _, old_id, group_id in proposals:
        if group_id in claimed_groups or old_id in paired_components:
            continue
        index = item_index[old_id]
        old = old_by_id[old_id]
        group = next(node for node in groups if node.id == group_id)
        box = _content_box(root, group)
        if box is None:
            continue
        item = items[index]
        items[index] = item.model_copy(update={
            "figma_node_id": group.id,
            "figma_name": group.name,
            "figma_bounds": _normalized_bounds(root, box),
            "status": "matched",
            "action": "accept",
            "score": max(item.score, round(score, 6)),
            "visual_disposition": "structural" if old.structural_only else item.visual_disposition,
        })
        paired_components.add(old_id)
        claimed_groups.add(group_id)

    unresolved = sum(1 for item in items if item.action is None)
    return draft.model_copy(update={"items": tuple(items), "unresolved_count": unresolved})
