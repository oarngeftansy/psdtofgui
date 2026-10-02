from __future__ import annotations

import math
import re
from collections.abc import Callable

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.hifi_replacement_models import (
    HIFI_MAPPING_POLICY_REVISION,
    FguiComponentInventory,
    FguiObjectRef,
    HifiMappingDecision,
    HifiMappingDraft,
    HifiMappingEvidence,
    HifiMappingItem,
    HifiMappingStatus,
)


class HifiMappingError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require_psd_coverage(draft: HifiMappingDraft, manifest: SelectionManifest) -> None:
    if not manifest.top_level_nodes[0].id.startswith("psd-root:"):
        return
    required = {node.id for node in _nodes(manifest) if not node.children and node.visible
                and not is_psd_visual_empty(node)}
    nodes_by_id = {node.id: node for node in _nodes(manifest)}

    def decided_subtree(node_id: str) -> set[str]:
        node = nodes_by_id.get(node_id)
        if node is None:
            return {node_id}
        return {child.id for child in _nodes(
            manifest.model_copy(update={"top_level_nodes": (node,)})
        )}

    # An occluded subtree renders nothing; the covering fact resolves all of
    # its leaves, not only the group record.
    required -= {
        node_id
        for item in draft.items
        if item.occluded and item.figma_node_id
        for node_id in decided_subtree(item.figma_node_id)
    }
    covered = {
        item.figma_node_id
        for item in draft.items
        if item.action in {"accept", "retarget"}
    }
    # A scope decision, not a correspondence, keeps the old visuals of the
    # shared region; the layer is still accounted for.
    covered.update(
        node_id
        for item in draft.items
        if item.out_of_scope and item.figma_node_id
        for node_id in decided_subtree(item.figma_node_id)
    )
    covered.update(node_id for item in draft.items if item.action in {"accept", "retarget"}
                   for node_id in item.owned_source_ids)
    covered.update(node_id for item in draft.items if item.action in {"accept", "retarget"}
                   for node_id in item.composite_source_ids)
    if required - covered:
        raise HifiMappingError("hifi_mapping_coverage_incomplete")


def is_empty_psd_group(node: SelectionNode) -> bool:
    return (node.type == "GROUP" and node.properties.get("psdKind") == "group"
            and not node.children and not node.resource_keys and node.text is None
            and node.properties.get("hasEffects") is False
            and node.properties.get("hasPixelMask") is False
            and node.properties.get("hasVectorMask") is False
            and node.properties.get("clipping") is False
            and node.properties.get("blendMode") in {"normal", "pass_through"})


def is_psd_visual_empty(node: SelectionNode) -> bool:
    """True when a PSD node cannot contribute pixels.

    A mask can only remove pixels, never add them, so zero-bounds content
    stays empty in any compositing context. Rasters are never empty, and
    nodes from Figma selections never carry psdKind.
    """
    if node.properties.get("psdKind") is None:
        return False
    if node.resource_keys:
        return False
    if is_empty_psd_group(node):
        return True
    return node.bounds.width <= 0 and node.bounds.height <= 0


def _normalized_name(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _normalized_text(value: str) -> str:
    return "".join(value.casefold().split())


def _nodes(manifest: SelectionManifest) -> tuple[SelectionNode, ...]:
    result: list[SelectionNode] = []
    pending = list(reversed(manifest.top_level_nodes))
    while pending:
        node = pending.pop()
        result.append(node)
        pending.extend(reversed(node.children))
    if len(manifest.top_level_nodes) == 1 and result[0].type.upper() in {"FRAME", "COMPONENT", "GROUP"}:
        return tuple(result[1:])
    return tuple(result)


def _figma_parents(manifest: SelectionManifest) -> dict[str, str | None]:
    parents: dict[str, str | None] = {}
    root_ids = {node.id for node in manifest.top_level_nodes}
    pending: list[tuple[SelectionNode, str | None]] = [
        (node, None) for node in reversed(manifest.top_level_nodes)
    ]
    while pending:
        node, parent_id = pending.pop()
        parents[node.id] = None if parent_id in root_ids else parent_id
        pending.extend((child, node.id) for child in reversed(node.children))
    return parents


def _old_bounds(
    old: FguiObjectRef, inventory: FguiComponentInventory
) -> tuple[float, float, float, float]:
    return (
        max(0.0, min(1.0, old.x / inventory.width)),
        max(0.0, min(1.0, old.y / inventory.height)),
        max(0.0, min(1.0, old.width / inventory.width)),
        max(0.0, min(1.0, old.height / inventory.height)),
    )


def _selection_box(
    manifest: SelectionManifest,
    node: SelectionNode,
    inventory: FguiComponentInventory,
) -> tuple[float, float, float, float]:
    if len(manifest.top_level_nodes) != 1:
        raise HifiMappingError("hifi_selection_requires_single_root")
    root = manifest.top_level_nodes[0]
    if root.bounds.width <= 0 or root.bounds.height <= 0:
        raise HifiMappingError("hifi_selection_root_invalid")
    if root.id.startswith("psd-root:"):
        viewport = root.properties.get("psdViewportBounds")
        offset_x, offset_y = (viewport[0], viewport[1]) if viewport else (0, 0)
        return (
            node.bounds.x - root.bounds.x - offset_x,
            node.bounds.y - root.bounds.y - offset_y,
            node.bounds.width,
            node.bounds.height,
        )
    scale_x = inventory.width / root.bounds.width
    scale_y = inventory.height / root.bounds.height
    return (
        (node.bounds.x - root.bounds.x) * scale_x,
        (node.bounds.y - root.bounds.y) * scale_y,
        node.bounds.width * scale_x,
        node.bounds.height * scale_y,
    )


def _figma_bounds(
    manifest: SelectionManifest,
    node: SelectionNode,
    inventory: FguiComponentInventory,
) -> tuple[float, float, float, float]:
    # The UI overlays these bounds on the source composite, whose canvas is
    # independent of the legacy component's height (including PSD overflow).
    root = manifest.top_level_nodes[0]
    x, y = node.bounds.x - root.bounds.x, node.bounds.y - root.bounds.y
    return (
        max(0.0, min(1.0, x / root.bounds.width)),
        max(0.0, min(1.0, y / root.bounds.height)),
        max(0.0, min(1.0, node.bounds.width / root.bounds.width)),
        max(0.0, min(1.0, node.bounds.height / root.bounds.height)),
    )


def _compatible(old_type: str, figma_type: str) -> float:
    old = old_type.casefold()
    figma = figma_type.casefold()
    if old == "text":
        return 1.0 if figma == "text" else 0.0
    if old == "component":
        return 1.0 if figma in {"instance", "component", "component_set", "group"} else 0.25
    if old == "image":
        return 1.0 if figma in {"rectangle", "vector", "image"} else 0.25
    if old in {"graph", "mystery"}:
        return 1.0 if figma in {"rectangle", "ellipse", "vector", "line", "polygon", "star"} else 0.25
    return 0.5


def psd_types_compatible(old_type: str, node: SelectionNode, *, conversion_allowed: bool = False) -> bool:
    """Exclude impossible contracts before scoring, decisions and writing."""
    # Some PSD layers report a visible flag but have no drawable bounds. They
    # cannot supply an in-place image and must not steal an old object merely
    # because their type or name happens to score well.
    if not node.visible or node.bounds.width <= 0 or node.bounds.height <= 0:
        return False
    if old_type.casefold() == "graph":
        if conversion_allowed and not node.children and node.type.upper() in {"IMAGE", "VECTOR", "RECTANGLE"}:
            return True
        return "fguiGraph" in node.properties and not node.resource_keys
    allowed = {
        "text": {"TEXT"}, "richtext": {"TEXT"},
        "image": {"IMAGE", "VECTOR", "RECTANGLE"},
        "loader": {"IMAGE", "VECTOR", "RECTANGLE"},
        "component": {"GROUP", "COMPONENT", "INSTANCE"},
        "group": {"GROUP"},
    }
    return node.type.upper() in allowed.get(old_type.casefold(), set())


def _score(
    old: FguiObjectRef,
    node: SelectionNode,
    inventory: FguiComponentInventory,
    old_count: int,
    figma_count: int,
    parent_score: float,
    selection_box: tuple[float, float, float, float],
) -> tuple[float, HifiMappingEvidence]:
    name_score = 1.0 if _normalized_name(old.name) == _normalized_name(node.name) else 0.0
    old_center = (
        (old.x + old.width / 2) / inventory.width,
        (old.y + old.height / 2) / inventory.height,
    )
    node_x, node_y, node_width, node_height = selection_box
    node_center = (
        (node_x + node_width / 2) / inventory.width,
        (node_y + node_height / 2) / inventory.height,
    )
    position_score = max(0.0, 1.0 - math.dist(old_center, node_center) / math.sqrt(2))
    old_area = max(1.0, old.width * old.height)
    new_area = max(1.0, node_width * node_height)
    size_score = min(old_area, new_area) / max(old_area, new_area)
    type_score = _compatible(old.object_type, node.type)
    order_score = 1.0 - abs(
        old.child_index / max(1, old_count - 1) - node.source_order / max(1, figma_count - 1)
    )
    # An object that FairyGUI repositions at runtime has an XML xy that is not
    # the rendered position, so its position term is evidence in neither
    # direction. The remaining weights are renormalised rather than awarding a
    # free position score.
    authoritative = not old.position_runtime_bound
    evidence = HifiMappingEvidence(
        version=1,
        name_score=name_score,
        position_score=position_score,
        position_authoritative=authoritative,
        size_score=size_score,
        type_score=type_score,
        parent_score=parent_score,
        order_score=max(0.0, order_score),
    )
    weighted = (
        name_score * 0.36
        + size_score * 0.14
        + type_score * 0.14
        + parent_score * 0.06
        + max(0.0, order_score) * 0.06
    )
    weighted = weighted / 0.76 if not authoritative else weighted + position_score * 0.24
    return round(weighted, 6), evidence


def _full_bleed_background(old: FguiObjectRef, node: SelectionNode,
                           inventory: FguiComponentInventory,
                           manifest: SelectionManifest) -> bool:
    """Identify a unique clean PSD pixel layer occupying the old backdrop."""
    if (old.object_type != "image" or old.width < inventory.width * .9
        or old.height < inventory.height * .9 or node.type != "IMAGE"
        or node.children or node.properties.get("psdKind") != "pixel"
        or node.properties.get("hasEffects") or node.properties.get("hasPixelMask")
        or node.properties.get("hasVectorMask") or node.properties.get("clipping")
        or node.properties.get("blendMode") != "normal"):
        return False
    x, y, width, height = _selection_box(manifest, node, inventory)
    return (abs(x - old.x) <= inventory.width * .05
            and abs(y - old.y) <= inventory.height * .05
            and abs(width - old.width) <= inventory.width * .05
            and abs(height - old.height) <= inventory.height * .05)


def _graph_shape_overlap(old: FguiObjectRef, node: SelectionNode,
                         inventory: FguiComponentInventory,
                         manifest: SelectionManifest) -> float:
    if (old.object_type not in {"graph", "image", "loader"} or old.structural_only or node.children
        or node.type.upper() not in {"VECTOR", "RECTANGLE", "IMAGE"}
        or node.properties.get("psdKind") not in {"shape", "pixel"}
        or node.properties.get("clipping")
        or node.properties.get("blendMode") != "normal"):
        # Layer effects and masks are baked into the isolated PSD raster, so
        # they no longer block a graph placeholder from taking the bitmap.
        return 0.0
    x, y, width, height = _selection_box(manifest, node, inventory)
    if min(old.width, old.height, width, height) <= 0:
        return 0.0
    intersection = max(0.0, min(old.x + old.width, x + width) - max(old.x, x)) * max(
        0.0, min(old.y + old.height, y + height) - max(old.y, y))
    union = old.width * old.height + width * height - intersection
    return intersection / union if union else 0.0


def _box_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    tolerance: float = 0.5,
) -> bool:
    return (
        outer[0] - tolerance <= inner[0]
        and outer[1] - tolerance <= inner[1]
        and outer[0] + outer[2] + tolerance >= inner[0] + inner[2]
        and outer[1] + outer[3] + tolerance >= inner[1] + inner[3]
    )


def _drawable_leaves(node: SelectionNode) -> list[SelectionNode]:
    """Visible PSD leaves that can contribute pixels or editable text."""
    result: list[SelectionNode] = []
    pending = list(node.children)
    while pending:
        child = pending.pop()
        if child.children:
            pending.extend(child.children)
        elif child.visible and not is_psd_visual_empty(child):
            result.append(child)
    return result


def _content_box(
    manifest: SelectionManifest,
    node: SelectionNode,
    inventory: FguiComponentInventory,
) -> tuple[float, float, float, float] | None:
    """Bounds from drawable descendants, not an unreliable PSD Group box."""
    leaves = _drawable_leaves(node)
    boxes = [_selection_box(manifest, leaf, inventory) for leaf in leaves]
    boxes = [box for box in boxes if box[2] > 0 and box[3] > 0]
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return left, top, right - left, bottom - top


def _region_affinity(
    old: FguiObjectRef,
    box: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    """Return combined, position and size evidence in rendered coordinates."""
    old_center = old.x + old.width / 2, old.y + old.height / 2
    new_center = box[0] + box[2] / 2, box[1] + box[3] / 2
    scale = max(1.0, math.hypot(max(old.width, box[2]), max(old.height, box[3])))
    position = max(0.0, 1.0 - math.dist(old_center, new_center) / scale)
    old_area = max(1.0, old.width * old.height)
    new_area = max(1.0, box[2] * box[3])
    size = min(old_area, new_area) / max(old_area, new_area)
    return position * 0.75 + size * 0.25, position, size


def _scroll_region_covered(
    manifest: SelectionManifest,
    node: SelectionNode,
    inventory: FguiComponentInventory,
) -> bool:
    """Return True when a leaf predominantly sits in the off-viewport scroll region.

    An object whose bounds extend beyond the component viewport exists because
    the original project scrolls. A PSD leaf that is predominantly (>50%)
    outside the viewport and predominantly (>60%) covered by a visible old
    object that itself extends beyond the viewport is scroll-region design
    content. The per-object mapping cannot express scroll-skin replacement
    (the covering object is already matched to its own PSD counterpart), so
    the leaf resolves as out_of_scope.
    """
    box = _selection_box(manifest, node, inventory)
    if box[2] <= 0 or box[3] <= 0:
        return False
    viewport = (0.0, 0.0, inventory.width, inventory.height)
    in_viewport_x = max(0.0, min(box[0] + box[2], viewport[2]) - max(box[0], viewport[0]))
    in_viewport_y = max(0.0, min(box[1] + box[3], viewport[3]) - max(box[1], viewport[1]))
    in_viewport = in_viewport_x * in_viewport_y
    total = box[2] * box[3]
    if in_viewport / total > 0.5:
        return False
    for old in inventory.objects:
        if (not old.default_visible or old.width <= 0 or old.height <= 0
                or old.x >= viewport[0] and old.x + old.width <= viewport[2]
                and old.y >= viewport[1] and old.y + old.height <= viewport[3]):
            continue
        overlap_x = max(0.0, min(box[0] + box[2], old.x + old.width) - max(box[0], old.x))
        overlap_y = max(0.0, min(box[1] + box[3], old.y + old.height) - max(box[1], old.y))
        overlap = overlap_x * overlap_y
        if overlap / total > 0.6:
            return True
    return False


def _overlapping_shared_owner(
    manifest: SelectionManifest,
    node: SelectionNode,
    inventory: FguiComponentInventory,
) -> FguiObjectRef | None:
    """Smallest out-of-scope object overlapping the leaf by more than half its area.

    A leaf may geometrically sit inside an in-scope container (the smallest
    fully-containing object) while predominantly rendering on top of a
    smaller out-of-scope object that only partially overlaps it. Sorting by
    area avoids the full-screen window: a shared button is always more
    specific than the panel-wide container.
    """
    box = _selection_box(manifest, node, inventory)
    if box[2] <= 0 or box[3] <= 0:
        return None
    leaf_area = box[2] * box[3]
    best: FguiObjectRef | None = None
    for old in inventory.objects:
        if not old.out_of_scope or not old.default_visible:
            continue
        if old.width <= 0 or old.height <= 0:
            continue
        overlap_x = max(0.0, min(box[0] + box[2], old.x + old.width) - max(box[0], old.x))
        overlap_y = max(0.0, min(box[1] + box[3], old.y + old.height) - max(box[1], old.y))
        if overlap_x * overlap_y / leaf_area > 0.5 and (
            best is None or old.width * old.height < best.width * best.height
        ):
            best = old
    return best


def _shared_region_owner(
    manifest: SelectionManifest,
    node: SelectionNode,
    inventory: FguiComponentInventory,
) -> FguiObjectRef | None:
    """Return the smallest visible old object containing the node, when out of scope.

    A HIFI leaf whose smallest containing runtime object belongs to a
    cross-package component renders that shared component's pixels. It has no
    in-scope owner this round, and overlaying it as a new visual would fake the
    replacement, so the scope decision resolves it instead.
    """
    box = _selection_box(manifest, node, inventory)
    if box[2] <= 0 or box[3] <= 0:
        return None
    tolerance = min(inventory.width, inventory.height) * 0.005
    best: FguiObjectRef | None = None
    for old in inventory.objects:
        contained = (
            old.default_visible and old.width > 0 and old.height > 0
            and old.x - tolerance <= box[0] and old.y - tolerance <= box[1]
            and old.x + old.width + tolerance >= box[0] + box[2]
            and old.y + old.height + tolerance >= box[1] + box[3]
        )
        if contained and (best is None or old.width * old.height < best.width * best.height):
            best = old
    return best if best is not None and best.out_of_scope else None


def build_mapping(
    inventory: FguiComponentInventory,
    manifest: SelectionManifest,
    *,
    owned_visual_validator: Callable[[str, frozenset[str], frozenset[str]], bool] | None = None,
    occlusion_validator: Callable[[SelectionNode], bool] | None = None,
    proven_source_owners: dict[str, str] | None = None,
    full_bleed_visual_validator: Callable[[FguiObjectRef, SelectionNode], float] | None = None,
    graph_raster_validator: Callable[[SelectionNode], bool] | None = None,
) -> HifiMappingDraft:
    nodes = _nodes(manifest)
    is_psd = manifest.top_level_nodes[0].id.startswith("psd-root:")
    real_nodes = tuple(node for node in nodes if not is_psd or "generatedStateOwner" not in node.properties)
    generated_by_owner = {
        node.properties["generatedStateOwner"]: node
        for node in nodes if is_psd and isinstance(node.properties.get("generatedStateOwner"), str)
    }
    old_by_id = {old.object_id: old for old in inventory.objects}
    proven_source_owners = proven_source_owners or {}
    if is_psd:
        proven_source_owners = {
            owner: source
            for owner, source in proven_source_owners.items()
            if owner in old_by_id and old_by_id[owner].default_visible
        }
    source_owner = {source: owner for owner, source in proven_source_owners.items()}
    direct_old_children: dict[str, list[FguiObjectRef]] = {}
    for old in inventory.objects:
        if old.parent_id is not None:
            direct_old_children.setdefault(old.parent_id, []).append(old)
    for children in direct_old_children.values():
        children.sort(key=lambda child: child.child_index)

    # PSD groups describe visual regions, while nested FGUI component names
    # are commonly generic ids. Pair compact component regions first, using
    # the union of drawable PSD descendants because Photoshop Group bounds
    # may include masks or stale coordinates far outside their visible art.
    component_group_pairs: dict[str, SelectionNode] = {}
    owned_group_hints: dict[str, SelectionNode] = {}
    hierarchy_proofs: dict[str, str] = {}
    hierarchy_candidates: dict[str, frozenset[str]] = {}
    if is_psd:
        groups = [
            node for node in real_nodes
            if node.type.upper() == "GROUP" and node.children
            and _content_box(manifest, node, inventory) is not None
        ]
        component_candidates: dict[str, list[tuple[float, SelectionNode]]] = {}
        for old in inventory.objects:
            children = direct_old_children.get(old.object_id, ())
            direct_visuals = [
                child for child in children
                if child.default_visible and not child.structural_only
                and child.object_type in {"graph", "image", "loader", "text", "richtext"}
            ]
            if (
                old.object_type != "component"
                or old.out_of_scope
                or not old.default_visible
                or not direct_visuals
                or old.width * old.height > inventory.width * inventory.height * 0.2
            ):
                continue
            expects_text = any(
                child.default_visible and child.object_type in {"text", "richtext"}
                for child in children
            )
            scored: list[tuple[float, SelectionNode]] = []
            for group in groups:
                has_text = any(
                    leaf.type.upper() == "TEXT" for leaf in _drawable_leaves(group)
                )
                if has_text != expects_text:
                    continue
                box = _content_box(manifest, group, inventory)
                assert box is not None
                score, position, size = _region_affinity(old, box)
                overlap = (
                    max(0.0, min(old.x + old.width, box[0] + box[2]) - max(old.x, box[0]))
                    * max(0.0, min(old.y + old.height, box[1] + box[3]) - max(old.y, box[1]))
                )
                if position >= 0.52 and size >= 0.45 and overlap > 0:
                    scored.append((score, group))
            component_candidates[old.object_id] = sorted(
                scored, key=lambda pair: (-pair[0], pair[1].id)
            )

        # A unique runtime label is stronger component identity than the old
        # XML position. It prevents vertically shifted Rank/Record/Shop skins
        # from being cross-assigned before geometry is rewritten.
        used_groups: set[str] = set()
        reserved_groups: set[str] = set()
        for old_id in component_candidates:
            old_texts = [
                child for child in direct_old_children.get(old_id, ())
                if child.default_visible and child.object_type in {"text", "richtext"}
                and child.effective_text and not child.effective_text.startswith("@")
            ]
            if len(old_texts) != 1:
                continue
            content = _normalized_text(old_texts[0].effective_text or "")
            matching_groups = [
                group for group in groups
                if any(
                    leaf.type.upper() == "TEXT" and leaf.text
                    and _normalized_text(leaf.text) == content
                    for leaf in _drawable_leaves(group)
                )
            ]
            matching_groups.sort(key=lambda group: (
                (_content_box(manifest, group, inventory) or (0, 0, math.inf, math.inf))[2]
                * (_content_box(manifest, group, inventory) or (0, 0, math.inf, math.inf))[3],
                group.id,
            ))
            if matching_groups and matching_groups[0].id not in reserved_groups:
                matched_group = matching_groups[0]
                component_group_pairs[old_id] = matched_group
                used_groups.add(matched_group.id)
                reserved_groups.update(
                    node.id for node in _nodes(
                        manifest.model_copy(update={"top_level_nodes": (matched_group,)})
                    )
                    if node.type.upper() == "GROUP"
                )

        # Greedy highest-confidence one-to-one pairing is deterministic. A
        # small margin protects repeated same-size controls from accidental
        # cross-assignment after the text-anchored controls are removed.
        proposals: list[tuple[float, str, SelectionNode]] = []
        for old_id, choices in component_candidates.items():
            if old_id in component_group_pairs:
                continue
            available = [choice for choice in choices if choice[1].id not in reserved_groups]
            if available and (
                len(available) == 1 or available[0][0] - available[1][0] >= 0.02
            ):
                proposals.append((available[0][0], old_id, available[0][1]))
        proposals.sort(key=lambda proposal: (-proposal[0], proposal[1], proposal[2].id))
        for _, old_id, group in proposals:
            if group.id not in used_groups and group.id not in reserved_groups:
                component_group_pairs[old_id] = group
                used_groups.add(group.id)
                reserved_groups.update(
                    node.id for node in _nodes(
                        manifest.model_copy(update={"top_level_nodes": (group,)})
                    )
                    if node.type.upper() == "GROUP"
                )

        # When a paired compact component has several direct visual hosts,
        # retain its structure and prove its direct children individually.
        # This is the header pattern: background, icon, title and info child.
        for component_id, group in list(component_group_pairs.items()):
            children = direct_old_children.get(component_id, ())
            visual_hosts = [
                child for child in children
                if child.default_visible and not child.structural_only
                and child.object_type in {"graph", "image", "loader"}
            ]
            direct_groups = [
                child for child in group.children
                if child.type.upper() == "GROUP" and _drawable_leaves(child)
            ]
            direct_components = [
                child for child in children
                if child.default_visible and child.object_type == "component"
            ]
            if len(visual_hosts) > 1 and len(direct_components) == len(direct_groups) == 1:
                nested_component = direct_components[0]
                nested_group = direct_groups[0]
                component_group_pairs[nested_component.object_id] = nested_group

            old_texts = [
                child for child in children
                if child.default_visible and child.object_type in {"text", "richtext"}
            ]
            source_texts = [leaf for leaf in _drawable_leaves(group) if leaf.type.upper() == "TEXT"]
            if len(old_texts) == len(source_texts) == 1:
                hierarchy_proofs[old_texts[0].object_id] = source_texts[0].id

            if len(visual_hosts) <= 1:
                continue
            if sum(host.object_type == "graph" and host.raster_conversion_allowed
                   for host in visual_hosts) == 1:
                continue
            nested_source_ids = {
                leaf.id for direct_group in direct_groups for leaf in _drawable_leaves(direct_group)
            }
            source_visuals = [
                child for child in group.children
                if not child.children and child.id not in nested_source_ids
                and child.type.upper() != "TEXT" and not is_psd_visual_empty(child)
            ]
            available = {source.id for source in source_visuals}
            for host in visual_hosts:
                compatible = [
                    source for source in source_visuals
                    if source.id in available and psd_types_compatible(host.object_type, source)
                ]
                ranked_sources = sorted(
                    (
                        (_region_affinity(host, _selection_box(manifest, source, inventory))[0], source)
                        for source in compatible
                    ),
                    key=lambda pair: (-pair[0], pair[1].id),
                )
                if not ranked_sources or ranked_sources[0][0] < 0.48:
                    continue
                if len(ranked_sources) > 1 and ranked_sources[0][0] - ranked_sources[1][0] < 0.08:
                    continue
                source = ranked_sources[0][1]
                hierarchy_proofs[host.object_id] = source.id
                available.remove(source.id)

        # The component instance is the layout owner for its nested visuals.
        # Map that existing component to the PSD group as well as mapping its
        # children. Otherwise the children are localized against the old
        # instance origin and remain clipped by the old component bounds.
        # This changes only the existing instance geometry; it does not add a
        # wrapper, replace its src, or alter its display-list position.
        for component_id, group in component_group_pairs.items():
            hierarchy_proofs[component_id] = group.id

        # A component with one visible visual host represents a composited
        # skin. All PSD visual leaves belong to that existing object; texts
        # remain mapped to the component's existing text children.
        for component_id, group in component_group_pairs.items():
            children = direct_old_children.get(component_id, ())
            hosts = [
                child for child in children
                if child.default_visible and not child.structural_only
                and child.object_type in {"graph", "image", "loader"}
                and (child.object_type != "graph" or child.raster_conversion_allowed)
            ]
            raster_hosts = [
                host for host in hosts if host.object_type in {"image", "loader"}
            ]
            convertible_graphs = [host for host in hosts if host.object_type == "graph"]
            if len(raster_hosts) == 1:
                # Keep the GGraph tag and its runtime state intact.  An
                # existing image/loader can carry the complete PSD skin over
                # it without changing the display list or converting types.
                hosts = raster_hosts
            elif len(convertible_graphs) == 1 and not raster_hosts:
                hosts = convertible_graphs
            if len(hosts) != 1:
                continue
            visual_ids = frozenset(
                leaf.id for leaf in _drawable_leaves(group)
                if leaf.properties.get("psdKind", "").casefold()
                in {"shape", "pixel", "smartobject"}
            )
            if not visual_ids:
                continue
            host = hosts[0]
            owned_group_hints[host.object_id] = group
            hierarchy_candidates[host.object_id] = visual_ids

        hierarchy_candidates.update({
            old_id: frozenset({source_id}) for old_id, source_id in hierarchy_proofs.items()
        })
        for old_id, source_ids in hierarchy_candidates.items():
            for source_id in source_ids:
                source_owner.setdefault(source_id, old_id)
    background_scores: dict[str, list[tuple[float, str]]] = {}
    if is_psd and full_bleed_visual_validator is not None:
        for old in inventory.objects:
            if old.out_of_scope or not old.default_visible:
                continue
            scores = []
            for node in real_nodes:
                if _full_bleed_background(old, node, inventory, manifest):
                    similarity = full_bleed_visual_validator(old, node)
                    if similarity >= .98:
                        scores.append((similarity, node.id))
            background_scores[old.object_id] = sorted(scores, reverse=True)
    background_owners: dict[str, list[str]] = {}
    for old_id, scores in background_scores.items():
        for _, source_id in scores:
            background_owners.setdefault(source_id, []).append(old_id)
    proven_backdrops = {
        old_id: scores[0] for old_id, scores in background_scores.items()
        if scores and len(background_owners[scores[0][1]]) == 1
        and (len(scores) == 1 or scores[0][0] - scores[1][0] >= .1)
        and scores[0][1] not in source_owner
    }
    source_owner.update({source_id: old_id for old_id, (_, source_id) in proven_backdrops.items()})
    graph_proofs: dict[str, str] = {}
    if is_psd and graph_raster_validator is not None:
        root_node = manifest.top_level_nodes[0]
        canvas_box = (
            root_node.bounds.x,
            root_node.bounds.y,
            root_node.bounds.width,
            root_node.bounds.height,
        )
        opaque_covers: list[tuple[tuple[float, float, float, float], int]] = []
        if occlusion_validator is not None:
            for candidate in real_nodes:
                cover_index = candidate.properties.get("psdDocumentIndex")
                cover_box = (
                    candidate.bounds.x,
                    candidate.bounds.y,
                    candidate.bounds.width,
                    candidate.bounds.height,
                )
                if (
                    candidate.type.upper() == "IMAGE"
                    and not candidate.children
                    and candidate.properties.get("psdKind") == "pixel"
                    and candidate.opacity >= 0.999
                    and candidate.properties.get("blendMode") == "normal"
                    and not candidate.properties.get("hasEffects")
                    and not candidate.properties.get("hasPixelMask")
                    and not candidate.properties.get("hasVectorMask")
                    and not candidate.properties.get("clipping")
                    and isinstance(cover_index, int)
                    and _box_contains(cover_box, canvas_box)
                    and occlusion_validator(candidate)
                ):
                    opaque_covers.append((cover_box, cover_index))

        def unavailable_graph_source(node: SelectionNode) -> bool:
            if node.children or node.type.upper() not in {"IMAGE", "VECTOR", "RECTANGLE"}:
                return True
            if node.properties.get("psdKind") not in {"shape", "pixel", "smartobject"}:
                return True
            if _scroll_region_covered(manifest, node, inventory):
                return True
            node_index = node.properties.get("psdDocumentIndex")
            node_box = (node.bounds.x, node.bounds.y, node.bounds.width, node.bounds.height)
            return isinstance(node_index, int) and any(
                cover_index > node_index and _box_contains(cover_box, node_box)
                for cover_box, cover_index in opaque_covers
            )

        def graph_candidate_strength(old: FguiObjectRef, node: SelectionNode) -> float:
            overlap = _graph_shape_overlap(old, node, inventory, manifest)
            if overlap >= 0.25:
                return overlap
            if not old.position_runtime_bound:
                return 0.0
            box = _selection_box(manifest, node, inventory)
            affinity, position, size = _region_affinity(old, box)
            return affinity if position >= 0.82 and size >= 0.15 else 0.0

        graph_candidates: dict[str, list[tuple[float, str]]] = {}
        for old in inventory.objects:
            if (
                old.object_type != "graph"
                or old.raster_conversion_allowed
                or old.out_of_scope
                or not old.default_visible
            ):
                continue
            graph_candidates[old.object_id] = sorted((
                (overlap, node.id) for node in real_nodes
                if node.id not in source_owner
                and not unavailable_graph_source(node)
                and (overlap := graph_candidate_strength(old, node)) >= .25
            ), reverse=True)
        for old_id, overlap_scores in graph_candidates.items():
            if not overlap_scores or overlap_scores[0][0] < .60:
                continue
            best_overlap, best_id = overlap_scores[0]
            if len(overlap_scores) > 1 and best_overlap - overlap_scores[1][0] < .25:
                continue
            if any(other != old_id and scores and scores[0][1] == best_id
                   and scores[0][0] >= .25 for other, scores in graph_candidates.items()):
                continue
            if best_id in source_owner:
                continue
            node = next(node for node in real_nodes if node.id == best_id)
            # A shape may also sit over an image/loader. Geometry alone does
            # not prove which existing visual object owns those pixels.
            committed_old_ids = set(source_owner.values())
            if any(other.object_id != old_id and other.object_id not in committed_old_ids
                   and not other.structural_only
                   and other.object_type in {"graph", "image", "loader"}
                   and _graph_shape_overlap(other, node, inventory, manifest) >= best_overlap - .05
                   for other in inventory.objects):
                continue
            if graph_raster_validator(node):
                graph_proofs[old_id] = best_id
                source_owner[best_id] = old_id
    text_proofs: dict[str, str] = {}
    if is_psd:
        for old in inventory.objects:
            if old.out_of_scope:
                continue
            if (old.object_type not in {"text", "richtext"} or not old.default_visible
                or not old.effective_text or old.effective_text.startswith("@")):
                continue
            content = _normalized_text(old.effective_text)
            if len(content) < 3 or sum(
                other.object_type in {"text", "richtext"}
                and _normalized_text(other.effective_text or "") == content
                for other in inventory.objects
            ) != 1:
                continue
            choices = [node for node in real_nodes if node.type.upper() == "TEXT"
                       and node.visible and node.text is not None
                       and _normalized_text(node.text) == content]
            if len(choices) != 1 or choices[0].id in source_owner:
                continue
            node = choices[0]
            x, y, width, height = _selection_box(manifest, node, inventory)
            if math.dist((old.x + old.width / 2, old.y + old.height / 2),
                         (x + width / 2, y + height / 2)) > max(old.width, old.height) * 1.5:
                continue
            text_proofs[old.object_id] = node.id
            source_owner[node.id] = old.object_id
    figma_parents = _figma_parents(manifest)
    old_names = {item.object_id: _normalized_name(item.name) for item in inventory.objects}
    figma_names = {item.id: _normalized_name(item.name) for item in nodes}
    ranked: dict[str, list[tuple[float, SelectionNode, HifiMappingEvidence]]] = {}
    for old in inventory.objects:
        candidates: list[tuple[float, SelectionNode, HifiMappingEvidence]] = []
        if old.object_id in generated_by_owner:
            ranked[old.object_id] = []
            continue
        if is_psd and (
            old.out_of_scope
            or not old.default_visible
            or old.structural_only and old.object_id not in hierarchy_proofs
        ):
            ranked[old.object_id] = []
            continue
        for node in real_nodes:
            if (old.object_id in hierarchy_candidates
                and node.id not in hierarchy_candidates[old.object_id]
                or old.object_id in proven_source_owners
                and node.id != proven_source_owners[old.object_id]
                or node.id in source_owner and source_owner[node.id] != old.object_id):
                continue
            if is_psd and not psd_types_compatible(old.object_type, node, conversion_allowed=(
                old.raster_conversion_allowed or graph_proofs.get(old.object_id) == node.id)):
                continue
            figma_parent = figma_parents.get(node.id)
            if old.parent_id is None and figma_parent is None:
                parent_score = 1.0
            elif old.parent_id is not None and figma_parent is not None:
                parent_score = (
                    1.0
                    if old_names.get(old.parent_id) == figma_names.get(figma_parent)
                    else 0.5
                )
            else:
                parent_score = 0.0
            score, evidence = _score(
                old,
                node,
                inventory,
                len(inventory.objects),
                len(real_nodes),
                parent_score,
                _selection_box(manifest, node, inventory),
            )
            candidates.append((score, node, evidence))
        ranked[old.object_id] = sorted(candidates, key=lambda item: (-item[0], item[1].id))

    claimed: set[str] = set()
    items: list[HifiMappingItem] = []
    for old in inventory.objects:
        generated = generated_by_owner.get(old.object_id)
        if generated is not None:
            claimed.add(generated.id)
            if is_psd and old.out_of_scope:
                # A derived state visual matched an object inside a shared
                # region. Writing it would change every screen using that
                # definition, and nested-instance isolation is not provable,
                # so the shared subtree keeps its old visuals; the derived
                # node still counts as covered for PSD coverage.
                items.append(HifiMappingItem(
                    version=1, item_id=f"old:{old.object_id}", old_object_id=old.object_id,
                    old_name=old.name, old_object_type=old.object_type,
                    old_resource_id=old.resource_id, figma_node_id=generated.id,
                    figma_name=generated.name, status="out_of_scope", score=1.0,
                    action="preserve_structure", out_of_scope=True,
                    generated_state=True,
                    evidence=HifiMappingEvidence(version=1, name_score=1, position_score=1,
                        size_score=1, type_score=1, parent_score=1, order_score=1),
                    candidates=(generated.id,), old_bounds=_old_bounds(old, inventory),
                    figma_bounds=_figma_bounds(manifest, generated, inventory),
                ))
                continue
            items.append(HifiMappingItem(
                version=1, item_id=f"old:{old.object_id}", old_object_id=old.object_id,
                old_name=old.name, old_object_type=old.object_type,
                old_resource_id=old.resource_id, figma_node_id=generated.id,
                figma_name=generated.name, status="matched", score=1.0,
                action="accept", generated_state=True, out_of_scope=old.out_of_scope,
                evidence=HifiMappingEvidence(version=1, name_score=1, position_score=1,
                    size_score=1, type_score=1, parent_score=1, order_score=1),
                candidates=(generated.id,), old_bounds=_old_bounds(old, inventory),
                figma_bounds=_figma_bounds(manifest, generated, inventory),
            ))
            continue
        proven_source = proven_source_owners.get(old.object_id)
        if proven_source is not None and proven_source not in claimed:
            proof = next(
                (
                    candidate
                    for candidate in ranked[old.object_id]
                    if candidate[1].id == proven_source
                ),
                None,
            )
            if proof is None:
                source_node = next(
                    (node for node in real_nodes if node.id == proven_source),
                    None,
                )
                if source_node is not None:
                    figma_parent = figma_parents.get(source_node.id)
                    parent_score = (
                        1.0
                        if old.parent_id is not None
                        and figma_parent is not None
                        and old_names.get(old.parent_id) == figma_names.get(figma_parent)
                        else 0.5
                    )
                    score, evidence = _score(
                        old,
                        source_node,
                        inventory,
                        len(inventory.objects),
                        len(real_nodes),
                        parent_score,
                        _selection_box(manifest, source_node, inventory),
                    )
                    proof = (score, source_node, evidence)
            if proof is not None:
                claimed.add(proven_source)
                items.append(HifiMappingItem(
                    version=1,
                    item_id=f"old:{old.object_id}",
                    old_object_id=old.object_id,
                    old_name=old.name,
                    old_object_type=old.object_type,
                    old_resource_id=old.resource_id,
                    figma_node_id=proven_source,
                    figma_name=proof[1].name,
                    status="matched",
                    score=proof[0],
                    evidence=proof[2],
                    action="accept",
                    candidates=(proven_source,),
                    graph_conversion_proven=old.object_type == "graph",
                    old_bounds=_old_bounds(old, inventory),
                    figma_bounds=_figma_bounds(manifest, proof[1], inventory),
                ))
                continue
        hierarchy_source = hierarchy_proofs.get(old.object_id)
        if hierarchy_source is not None and hierarchy_source not in claimed:
            proof = next(
                (candidate for candidate in ranked[old.object_id]
                 if candidate[1].id == hierarchy_source),
                None,
            )
            if proof is not None:
                claimed.add(hierarchy_source)
                items.append(HifiMappingItem(
                    version=1,
                    item_id=f"old:{old.object_id}",
                    old_object_id=old.object_id,
                    old_name=old.name,
                    old_object_type=old.object_type,
                    old_resource_id=old.resource_id,
                    figma_node_id=hierarchy_source,
                    figma_name=proof[1].name,
                    status="matched",
                    score=max(proof[0], 0.78),
                    evidence=proof[2],
                    action="accept",
                    candidates=(hierarchy_source,),
                    preserve_runtime_text=(
                        old.runtime_text_override
                        and old.object_type in {"text", "richtext"}
                    ),
                    old_bounds=_old_bounds(old, inventory),
                    figma_bounds=_figma_bounds(manifest, proof[1], inventory),
                ))
                continue
        backdrop = proven_backdrops.get(old.object_id)
        if backdrop is not None and backdrop[1] not in claimed:
            similarity, backdrop_id = backdrop
            proof = next((candidate for candidate in ranked[old.object_id]
                          if candidate[1].id == backdrop_id), None)
            if proof is not None:
                claimed.add(backdrop_id)
                items.append(HifiMappingItem(
                    version=1, item_id=f"old:{old.object_id}", old_object_id=old.object_id,
                    old_name=old.name, old_object_type=old.object_type,
                    old_resource_id=old.resource_id, figma_node_id=backdrop_id,
                    figma_name=proof[1].name, status="matched", score=similarity,
                    evidence=proof[2], action="accept", candidates=(backdrop_id,),
                    old_bounds=_old_bounds(old, inventory),
                    figma_bounds=_figma_bounds(manifest, proof[1], inventory),
                ))
                continue
        proven_graph = graph_proofs.get(old.object_id)
        if proven_graph is not None and proven_graph not in claimed:
            proof = next((candidate for candidate in ranked[old.object_id]
                          if candidate[1].id == proven_graph), None)
            if proof is not None:
                claimed.add(proven_graph)
                items.append(HifiMappingItem(
                    version=1, item_id=f"old:{old.object_id}", old_object_id=old.object_id,
                    old_name=old.name, old_object_type=old.object_type,
                    old_resource_id=old.resource_id, figma_node_id=proven_graph,
                    figma_name=proof[1].name, status="matched", score=proof[0],
                    evidence=proof[2], action="accept", candidates=(proven_graph,),
                    graph_conversion_proven=True,
                    old_bounds=_old_bounds(old, inventory),
                    figma_bounds=_figma_bounds(manifest, proof[1], inventory),
                ))
                continue
        proven_text = text_proofs.get(old.object_id)
        if proven_text is not None and proven_text not in claimed:
            proof = next((candidate for candidate in ranked[old.object_id]
                          if candidate[1].id == proven_text), None)
            if proof is not None:
                claimed.add(proven_text)
                items.append(HifiMappingItem(
                    version=1, item_id=f"old:{old.object_id}", old_object_id=old.object_id,
                    old_name=old.name, old_object_type=old.object_type,
                    old_resource_id=old.resource_id, figma_node_id=proven_text,
                    figma_name=proof[1].name,
                    status="matched",
                    score=proof[0], evidence=proof[2],
                    action="accept",
                    candidates=(proven_text,),
                    preserve_runtime_text=old.runtime_text_override,
                    old_bounds=_old_bounds(old, inventory),
                    figma_bounds=_figma_bounds(manifest, proof[1], inventory),
                ))
                continue
        if is_psd and old.out_of_scope:
            items.append(HifiMappingItem(
                version=1, item_id=f"old:{old.object_id}",
                old_object_id=old.object_id, old_name=old.name,
                old_object_type=old.object_type, old_resource_id=old.resource_id,
                status="out_of_scope", action="preserve_structure", out_of_scope=True,
                score=0,
                evidence=HifiMappingEvidence(version=1, name_score=0, position_score=0,
                    size_score=0, type_score=0, parent_score=0, order_score=0),
                old_bounds=_old_bounds(old, inventory),
            ))
            continue
        if is_psd and not old.default_visible:
            items.append(HifiMappingItem(
                version=1,
                item_id=f"old:{old.object_id}",
                old_object_id=old.object_id,
                old_name=old.name,
                old_object_type=old.object_type,
                old_resource_id=old.resource_id,
                status="fgui_only",
                action="keep_old",
                score=0,
                evidence=HifiMappingEvidence(
                    version=1,
                    name_score=0,
                    position_score=0,
                    size_score=0,
                    type_score=0,
                    parent_score=0,
                    order_score=0,
                ),
                old_bounds=_old_bounds(old, inventory),
            ))
            continue
        if is_psd and old.structural_only:
            items.append(HifiMappingItem(version=1,item_id=f"old:{old.object_id}",
                old_object_id=old.object_id,old_name=old.name,old_object_type=old.object_type,
                status="structural",action="preserve_structure",score=0,
                evidence=HifiMappingEvidence(version=1,name_score=0,position_score=0,size_score=0,
                    type_score=0,parent_score=0,order_score=0),old_bounds=_old_bounds(old,inventory)))
            continue
        candidates = [item for item in ranked[old.object_id] if item[1].id not in claimed]
        top = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) > 1 else None
        if top is None or top[0] < 0.55:
            evidence = top[2] if top else HifiMappingEvidence(
                version=1, name_score=0.0, position_score=0.0, size_score=0.0,
                type_score=0.0, parent_score=0.0, order_score=0.0,
            )
            items.append(
                HifiMappingItem(
                    version=1,
                    item_id=f"old:{old.object_id}",
                    old_object_id=old.object_id,
                    old_name=old.name,
                    old_object_type=old.object_type,
                    old_resource_id=old.resource_id,
                    status="fgui_only",
                    score=top[0] if top else 0.0,
                    evidence=evidence,
                    action=None if manifest.top_level_nodes[0].id.startswith("psd-root:") else "keep_old",
                    candidates=tuple(item[1].id for item in candidates[:3]),
                    old_bounds=_old_bounds(old, inventory),
                )
            )
            continue
        ambiguous = second is not None and top[0] - second[0] < 0.08
        exact_name = top[2].name_score == 1.0
        status: HifiMappingStatus = (
            "uncertain"
            if ambiguous
            else "matched"
            if exact_name and top[0] >= 0.78
            else "suggested"
        )
        # A tentative correspondence reserves its PSD node too. Otherwise the
        # same node appears again as a supposed HIFI addition before review.
        claimed.add(top[1].id)
        items.append(
            HifiMappingItem(
                version=1,
                item_id=f"old:{old.object_id}",
                old_object_id=old.object_id,
                old_name=old.name,
                old_object_type=old.object_type,
                old_resource_id=old.resource_id,
                figma_node_id=top[1].id,
                figma_name=top[1].name,
                status=status,
                score=top[0],
                evidence=top[2],
                action="accept" if status == "matched" and not (is_psd and top[1].children) else None,
                candidates=tuple(item[1].id for item in candidates[:3]),
                old_bounds=_old_bounds(old, inventory),
                figma_bounds=_figma_bounds(manifest, top[1], inventory),
            )
        )
    if is_psd and owned_visual_validator is not None:
        by_node = {node.id: node for node in nodes}
        assigned = {item.figma_node_id: item for item in items if item.old_object_id and item.figma_node_id}
        old_by_id = {old.object_id: old for old in inventory.objects}
        item_index_by_old = {
            item.old_object_id: index
            for index, item in enumerate(items)
            if item.old_object_id
        }
        def old_descends_from(candidate: FguiObjectRef, ancestor_id: str) -> bool:
            parent_id = candidate.parent_id
            while parent_id is not None:
                if parent_id == ancestor_id:
                    return True
                parent = old_by_id.get(parent_id)
                parent_id = parent.parent_id if parent is not None else None
            return False

        # A visible PSD accessory may correspond to an old component that is
        # hidden only because its exported controller starts on page zero.
        # Reuse that component and its loader so the existing gear/actions
        # still own visibility; never bake the accessory into a visible
        # sibling's PNG.
        for source_group in nodes:
            if source_group.type.upper() != "GROUP" or not source_group.children:
                continue
            leaves = _drawable_leaves(source_group)
            visuals = [
                leaf for leaf in leaves
                if leaf.properties.get("psdKind", "").casefold()
                in {"shape", "pixel", "smartobject"}
            ]
            retained = [leaf for leaf in leaves if leaf.type.upper() == "TEXT"]
            if (
                not visuals
                or any(leaf.id in claimed for leaf in leaves)
                or len(visuals) + len(retained) != len(leaves)
            ):
                continue
            source_parent = figma_parents.get(source_group.id)
            container_item = assigned.get(source_parent or "")
            container = old_by_id.get(
                container_item.old_object_id or "" if container_item is not None else ""
            )
            if container is None or container.object_type != "component":
                continue
            candidates: list[tuple[FguiObjectRef, FguiObjectRef]] = []
            for accessory in inventory.objects:
                accessory_index = item_index_by_old.get(accessory.object_id)
                if (
                    accessory_index is None
                    or accessory.parent_id != container.object_id
                    or accessory.object_type != "component"
                    or accessory.default_visible
                    or not accessory.position_runtime_bound
                    or "visible" not in accessory.dynamic_properties
                    or not accessory.controller_refs
                    or items[accessory_index].figma_node_id is not None
                ):
                    continue
                loaders = [
                    child for child in inventory.objects
                    if child.object_type == "loader"
                    and not child.structural_only
                    and child.position_runtime_bound
                    and old_descends_from(child, accessory.object_id)
                    and (child_index := item_index_by_old.get(child.object_id)) is not None
                    and items[child_index].figma_node_id is None
                ]
                if len(loaders) == 1:
                    candidates.append((accessory, loaders[0]))
            if len(candidates) != 1:
                continue
            accessory, loader = candidates[0]
            visual_ids = frozenset(leaf.id for leaf in visuals)
            retained_ids = frozenset(leaf.id for leaf in retained)
            if not owned_visual_validator(source_group.id, visual_ids, retained_ids):
                continue
            anchor = min(
                visuals,
                key=lambda leaf: (
                    int(leaf.properties.get("psdDocumentIndex", 1 << 30)),
                    leaf.id,
                ),
            )
            accessory_index = item_index_by_old[accessory.object_id]
            loader_index = item_index_by_old[loader.object_id]
            accessory_score, accessory_evidence = _score(
                accessory,
                source_group,
                inventory,
                len(inventory.objects),
                len(real_nodes),
                1.0,
                _selection_box(manifest, source_group, inventory),
            )
            loader_score, loader_evidence = _score(
                loader,
                anchor,
                inventory,
                len(inventory.objects),
                len(real_nodes),
                1.0,
                _selection_box(manifest, anchor, inventory),
            )
            items[accessory_index] = items[accessory_index].model_copy(update={
                "figma_node_id": source_group.id,
                "figma_name": source_group.name,
                "figma_bounds": _figma_bounds(manifest, source_group, inventory),
                "status": "matched",
                "action": "accept",
                "score": accessory_score,
                "evidence": accessory_evidence,
            })
            items[loader_index] = items[loader_index].model_copy(update={
                "figma_node_id": anchor.id,
                "figma_name": anchor.name,
                "figma_bounds": _figma_bounds(manifest, anchor, inventory),
                "owned_source_ids": tuple(sorted(visual_ids)),
                "owned_group_id": source_group.id,
                "retained_source_ids": tuple(sorted(retained_ids)),
                "status": "matched",
                "action": "accept",
                "score": loader_score,
                "evidence": loader_evidence,
                "visual_echo": False,
            })
            assigned[source_group.id] = items[accessory_index]
            assigned[anchor.id] = items[loader_index]
            claimed.add(source_group.id)
            claimed.update(visual_ids)
            claimed.update(retained_ids)

        promoted_anchors: set[str] = {
            item.figma_node_id
            for item in items
            if item.figma_node_id and item.owned_source_ids
        }
        promoted_groups: set[str] = {
            item.owned_group_id for item in items if item.owned_group_id
        }
        promoted_source_ids: set[str] = {
            source_id
            for item in items
            for source_id in item.owned_source_ids
        }
        promoted_source_ids.update({
            item.figma_node_id
            for item in items
            if item.action == "accept"
            and item.figma_node_id
            and item.old_object_id
            and old_by_id[item.old_object_id].object_type == "graph"
        })

        def group_leaves(group_node: SelectionNode) -> list[SelectionNode]:
            return _drawable_leaves(group_node)

        for index, item in enumerate(items):
            owner_graph = old_by_id.get(item.old_object_id or "")
            # A position-less score is renormalised over 0.76 of the evidence
            # weight, so the promotion gate scales by the same factor.
            gate = 0.55 if item.evidence.position_authoritative else 0.55 * 0.76
            if (
                owner_graph is None
                or owner_graph.object_type not in {"graph", "image", "loader"}
                # A PSD owned bundle is a raster resource. Assigning it to a
                # GGraph would require changing the display-list tag to a
                # loader/image, which violates the structural contract. A
                # compatible existing image/loader sibling may own the outer
                # partition; otherwise the mapping must remain unresolved.
                or owner_graph.object_type == "graph"
            ):
                continue
            # A sub-threshold item carries no tentative node, but its best
            # candidate is still the anchor of the owned partition.
            anchor = item.figma_node_id or (item.candidates[0] if item.candidates else None)
            group = by_node.get(figma_parents.get(anchor or "") or "")
            hinted_group = owned_group_hints.get(owner_graph.object_id)
            if hinted_group is not None:
                group = hinted_group
                hinted_visuals = [
                    leaf for leaf in group_leaves(group)
                    if leaf.properties.get("psdKind", "").casefold()
                    in {"shape", "pixel", "smartobject"}
                ]
                if not hinted_visuals:
                    continue
                if anchor not in {leaf.id for leaf in hinted_visuals}:
                    old_center = (
                        owner_graph.x + owner_graph.width / 2,
                        owner_graph.y + owner_graph.height / 2,
                    )
                    anchor = min(
                        hinted_visuals,
                        key=lambda leaf: math.dist(
                            old_center,
                            (
                                _selection_box(manifest, leaf, inventory)[0]
                                + _selection_box(manifest, leaf, inventory)[2] / 2,
                                _selection_box(manifest, leaf, inventory)[1]
                                + _selection_box(manifest, leaf, inventory)[3] / 2,
                            ),
                        ),
                    ).id
            elif (
                anchor is None
                or item.score < gate and not item.graph_conversion_proven
                or group is None
            ):
                continue
            if anchor in promoted_anchors:
                continue
            if group is None or group.type.upper() != "GROUP":
                continue
            if group.id in promoted_groups:
                continue
            descendants = group_leaves(group)
            all_visual = [
                node for node in descendants
                if node.properties.get("psdKind", "").casefold()
                in {"shape", "pixel", "smartobject"}
            ]
            absorbed_graph_items = [
                assigned[node.id]
                for node in all_visual
                if node.id in assigned
                and (
                    assigned_old := old_by_id.get(
                        assigned[node.id].old_object_id or ""
                    )
                ) is not None
                and assigned_old.object_type == "graph"
                and assigned_old.parent_id == owner_graph.parent_id
            ]
            # Inner owned groups are rendered by their existing legacy object.
            # When an outer group is promoted later, those same PSD leaves are
            # a delegated part of its partition, never pixels for the outer
            # loader as well.
            delegated_visual_ids = frozenset(
                node.id
                for node in all_visual
                if node.id in promoted_source_ids
                and assigned.get(node.id) not in absorbed_graph_items
            )
            visual = [node for node in all_visual if node.id not in delegated_visual_ids]
            if visual and anchor in delegated_visual_ids:
                old_center = (
                    owner_graph.x + owner_graph.width / 2,
                    owner_graph.y + owner_graph.height / 2,
                )
                anchor = min(
                    visual,
                    key=lambda leaf: math.dist(
                        old_center,
                        (
                            _selection_box(manifest, leaf, inventory)[0]
                            + _selection_box(manifest, leaf, inventory)[2] / 2,
                            _selection_box(manifest, leaf, inventory)[1]
                            + _selection_box(manifest, leaf, inventory)[3] / 2,
                        ),
                    ),
                ).id
            retained = [node for node in descendants if node.type.upper() == "TEXT"]
            assigned_visuals = [
                assigned[node.id]
                for node in visual
                if node.id in assigned and node.id != anchor
            ]
            assigned_to_siblings = all(
                (assigned_old := old_by_id.get(assigned_item.old_object_id or ""))
                is not None
                and assigned_old.parent_id == owner_graph.parent_id
                for assigned_item in assigned_visuals
            )
            if (not visual or anchor not in {node.id for node in visual}
                or len(all_visual) + len(retained) != len(descendants)
                or assigned_visuals and not assigned_to_siblings):
                continue
            retained_items: dict[str, HifiMappingItem] = {}
            unpaired: list[SelectionNode] = []
            paired_ids: set[str] = set()
            blocked_pairing = False
            for node in retained:
                item_text = assigned.get(node.id)
                owner = (old_by_id.get(item_text.old_object_id or "")
                         if item_text is not None else None)
                if (item_text is not None and owner is not None
                        and owner.object_type in {"text", "richtext"}
                        and owner.parent_id == owner_graph.parent_id):
                    retained_items[node.id] = item_text
                    paired_ids.add(item_text.old_object_id or "")
                elif node.id in claimed:
                    # The text is tentatively held by an unrelated object; the
                    # partition cannot take it without stealing.
                    blocked_pairing = True
                    break
                else:
                    unpaired.append(node)
            if blocked_pairing:
                continue
            if unpaired:
                # A retained text without a tentative match may still pair with
                # the sole unresolved text object under the same parent; the
                # button definition has exactly one title child.
                free_texts = [
                    text_item for text_item in items
                    if text_item.old_object_id
                    and text_item.old_object_id not in paired_ids
                    and text_item.figma_node_id is None
                    and text_item.status in {"fgui_only", "suggested", "uncertain"}
                    and (owner := old_by_id.get(text_item.old_object_id)) is not None
                    and owner.object_type in {"text", "richtext"}
                    and owner.parent_id == owner_graph.parent_id
                ]
                if len(free_texts) != len(unpaired):
                    continue
                ordered_texts = sorted(
                    free_texts,
                    key=lambda text_item: old_by_id[text_item.old_object_id or ""].child_index,
                )
                for node, text_item in zip(unpaired, ordered_texts):
                    retained_items[node.id] = text_item
                    paired_ids.add(text_item.old_object_id or "")
            text_ids = frozenset(node.id for node in retained)
            group_box = _selection_box(manifest, group, inventory)

            def overlap_fraction(
                old: FguiObjectRef,
                mapped_group: tuple[float, float, float, float] = group_box,
                mapped_owner: FguiObjectRef = owner_graph,
            ) -> float:
                # Compare in the prospective mapped component position.  The
                # old instance itself may move a long way to the PSD group,
                # while its children keep their local coordinates.
                mapped_x = mapped_group[0] + old.x - mapped_owner.x
                mapped_y = mapped_group[1] + old.y - mapped_owner.y
                left = max(mapped_x, mapped_group[0])
                top = max(mapped_y, mapped_group[1])
                right = min(mapped_x + old.width, mapped_group[0] + mapped_group[2])
                bottom = min(mapped_y + old.height, mapped_group[1] + mapped_group[3])
                intersection = max(0.0, right - left) * max(0.0, bottom - top)
                return intersection / max(1.0, old.width * old.height)

            # A legacy component often splits one visual into a background
            # graph plus an icon loader, while the PSD splits that same art
            # into several shape leaves. Leaving the loader as keep_old draws
            # the old icon over the new button. Partition the PSD leaves across
            # those existing siblings in display order so every old identity
            # remains, but every visible payload comes from the PSD.
            compact_group = (
                group_box[2] * group_box[3]
                < inventory.width * inventory.height * 0.2
            )
            assigned_echo_items = [
                assigned_item
                for assigned_item in assigned_visuals
                if compact_group
                and assigned_item.old_object_id
                and (assigned_old := old_by_id.get(assigned_item.old_object_id)) is not None
                and assigned_old.object_type in {"image", "loader"}
                and assigned_old.default_visible
                and overlap_fraction(assigned_old) >= 0.8
            ]
            spare_items = [
                candidate
                for candidate in items
                if candidate.old_object_id
                and candidate.figma_node_id is None
                and candidate.action is None
                and candidate.status in {"fgui_only", "suggested", "uncertain"}
                and (spare := old_by_id.get(candidate.old_object_id)) is not None
                and spare.parent_id == owner_graph.parent_id
                and spare.object_id != owner_graph.object_id
                and spare.object_type in {"image", "loader"}
                and spare.default_visible
                and overlap_fraction(spare) >= 0.8
            ]
            if not compact_group:
                spare_items = []
            spare_items.sort(
                key=lambda candidate: old_by_id[candidate.old_object_id or ""].child_index
            )
            spare_items = spare_items[: max(0, len(visual) - 1)]
            ordered_visuals = sorted(
                visual,
                key=lambda node: (
                    int(node.properties.get("psdDocumentIndex", 1 << 30)),
                    node.id,
                ),
            )
            all_visual_ids = frozenset(node.id for node in visual)
            delegated_ids = text_ids | delegated_visual_ids
            if not owned_visual_validator(group.id, all_visual_ids, delegated_ids):
                continue
            if not retained and not text_ids and not compact_group:
                base_candidates = [
                    (base_index, base_item, base_old)
                    for base_index, base_item in enumerate(items)
                    if base_item.old_object_id
                    and base_item.figma_node_id
                    and not base_item.owned_source_ids
                    and not base_item.composite_source_ids
                    and base_item.action == "accept"
                    and (base_old := old_by_id.get(base_item.old_object_id)) is not None
                    and base_old.parent_id is None
                    and base_old.object_type in {"image", "loader"}
                    and base_old.width >= inventory.width * 0.9
                    and base_old.height >= inventory.height * 0.9
                ]
                if len(base_candidates) == 1:
                    base_index, base_item, _ = base_candidates[0]
                    items[base_index] = base_item.model_copy(update={
                        "composite_group_id": group.id,
                        "composite_source_ids": tuple(sorted(all_visual_ids)),
                    })
                    items[index] = item.model_copy(update={
                        "figma_node_id": None,
                        "figma_name": None,
                        "figma_bounds": None,
                        "owned_source_ids": (),
                        "owned_group_id": None,
                        "retained_source_ids": (),
                        "status": "fgui_only",
                        "action": "keep_old",
                        "graph_conversion_proven": False,
                    })
                    claimed.update(all_visual_ids)
                    promoted_anchors.add(anchor)
                    promoted_groups.add(group.id)
                    promoted_source_ids.update(all_visual_ids)
                    continue
            promoted_anchors.add(anchor)
            promoted_groups.add(group.id)
            visual_items = [item, *assigned_echo_items, *spare_items]
            visual_items = list({
                visual_item.old_object_id: visual_item
                for visual_item in visual_items
                if visual_item.old_object_id
            }.values())
            visual_items.sort(
                key=lambda visual_item: old_by_id[
                    visual_item.old_object_id or ""
                ].child_index
            )
            # Partition one PSD group across the existing visual siblings.
            # Giving the complete group to only the highest-z object leaves
            # lower legacy images visible through translucent pixels (for
            # example a title pill is then drawn twice). Anchored matches keep
            # their leaf, free siblings take the closest-size remaining leaf,
            # and any residual decoration stays with the nearest anchored
            # owner. Every PSD leaf consequently has exactly one old owner.
            ownership: dict[str, set[str]] = {
                visual_item.old_object_id or "": set()
                for visual_item in visual_items
            }
            source_by_id = {node.id: node for node in ordered_visuals}
            for visual_item in visual_items:
                if visual_item.figma_node_id in source_by_id:
                    ownership[visual_item.old_object_id or ""].add(
                        visual_item.figma_node_id or ""
                    )
            assigned_source_ids = set().union(*ownership.values()) if ownership else set()
            remaining_sources = [
                node for node in ordered_visuals if node.id not in assigned_source_ids
            ]
            for visual_item in visual_items:
                owner_id = visual_item.old_object_id or ""
                if ownership[owner_id] or not remaining_sources:
                    continue
                visual_owner = old_by_id[owner_id]
                old_area = max(1.0, visual_owner.width * visual_owner.height)

                def spare_score(
                    node: SelectionNode, owner_area: float = old_area
                ) -> tuple[float, int]:
                    box = _selection_box(manifest, node, inventory)
                    node_area = max(1.0, box[2] * box[3])
                    size_similarity = min(owner_area, node_area) / max(owner_area, node_area)
                    return size_similarity, -int(node.properties.get("psdDocumentIndex", 0))

                selected = max(remaining_sources, key=spare_score)
                ownership[owner_id].add(selected.id)
                remaining_sources.remove(selected)
            anchored_owners = [
                owner_id for owner_id, source_ids in ownership.items() if source_ids
            ]
            # Decorations without their own legacy object stay with the
            # highest-z existing owner. They must never be folded back into a
            # lower background image, where runtime foreground siblings would
            # paint over them again.
            residual_owner = anchored_owners[-1]
            for node in remaining_sources:
                ownership[residual_owner].add(node.id)
            for visual_item in visual_items:
                visual_item_index = item_index_by_old[visual_item.old_object_id]
                visual_owner = old_by_id[visual_item.old_object_id or ""]
                owned_ids = frozenset(ownership[visual_item.old_object_id or ""])
                if not owned_ids:
                    items[visual_item_index] = visual_item.model_copy(update={
                        "figma_node_id": None,
                        "figma_name": None,
                        "figma_bounds": None,
                        "owned_source_ids": (),
                        "owned_group_id": None,
                        "retained_source_ids": (),
                        "status": "fgui_only",
                        "action": "keep_old",
                        "graph_conversion_proven": False,
                        "visual_echo": False,
                    })
                    continue
                assigned_anchor = next(
                    (
                        node for node in ordered_visuals
                        if node.id == visual_item.figma_node_id and node.id in owned_ids
                    ),
                    next(node for node in ordered_visuals if node.id in owned_ids),
                )
                items[visual_item_index] = visual_item.model_copy(update={
                    "owned_source_ids": tuple(sorted(owned_ids)),
                    "owned_group_id": group.id,
                    "retained_source_ids": tuple(sorted(
                        delegated_ids | (all_visual_ids - owned_ids)
                    )),
                    "figma_node_id": assigned_anchor.id,
                    "figma_name": assigned_anchor.name,
                    "figma_bounds": _figma_bounds(manifest, assigned_anchor, inventory),
                    "status": "matched",
                    "action": "accept",
                    "graph_conversion_proven": visual_owner.object_type == "graph",
                    "visual_echo": False,
                })
            for graph_item in absorbed_graph_items:
                graph_index = item_index_by_old[graph_item.old_object_id]
                items[graph_index] = graph_item.model_copy(update={
                    "figma_node_id": None,
                    "figma_name": None,
                    "figma_bounds": None,
                    "owned_source_ids": (),
                    "owned_group_id": None,
                    "retained_source_ids": (),
                    "status": "fgui_only",
                    "action": "keep_old",
                    "graph_conversion_proven": False,
                    "visual_echo": False,
                })
            for node in retained:
                text_item = retained_items[node.id]
                text_index = item_index_by_old[text_item.old_object_id]
                text_owner = old_by_id[text_item.old_object_id]
                updates: dict[str, object] = {
                    "status": "matched",
                    "action": "accept",
                    "preserve_runtime_text": text_owner.runtime_text_override,
                }
                if text_item.figma_node_id is None:
                    updates["figma_node_id"] = node.id
                    updates["figma_name"] = by_node[node.id].name
                items[text_index] = text_item.model_copy(update=updates)
            claimed.update(all_visual_ids)
            claimed.update(node.id for node in retained)
            promoted_source_ids.update(all_visual_ids)
    if is_psd:
        item_index_by_old = {item.old_object_id: index
                             for index, item in enumerate(items) if item.old_object_id}
        for layout_group in inventory.objects:
            # The layout group itself is a non-rendering container; only its
            # members receive visuals, so structural_only does not disqualify
            # it from anchoring the sequence proof.
            if (layout_group.auto_layout is None or layout_group.out_of_scope
                    or not layout_group.default_visible):
                continue
            members = sorted(
                (old for old in inventory.objects if old.parent_id == layout_group.object_id),
                key=lambda old: old.child_index,
            )
            if len(members) < 2 or any(
                member.structural_only or member.out_of_scope or not member.default_visible
                for member in members
            ):
                continue
            if any(
                (member_index := item_index_by_old.get(member.object_id)) is None
                or items[member_index].figma_node_id is not None
                or items[member_index].action is not None
                for member in members
            ):
                continue
            horizontal = layout_group.auto_layout == "hz"
            axis = 0 if horizontal else 1
            cross_center = (
                (layout_group.y + layout_group.height / 2) if horizontal
                else (layout_group.x + layout_group.width / 2)
            )
            cross_tolerance = (layout_group.height if horizontal else layout_group.width)
            main_low, main_high = (
                (layout_group.x, layout_group.x + layout_group.width) if horizontal
                else (layout_group.y, layout_group.y + layout_group.height)
            )
            # An auto-layout group renders its members along the axis in
            # display order. A PSD group holding exactly the strip's
            # unclaimed visible leaves, with an agreeing type sequence and
            # region, proves the rank-to-rank correspondence.
            matches: list[tuple[SelectionNode, list[SelectionNode]]] = []
            for node in real_nodes:
                if node.type.upper() != "GROUP" or not node.children:
                    continue
                if any(child.children for child in node.children):
                    continue
                leaves = [
                    child for child in node.children
                    if child.id not in claimed and child.id not in source_owner
                    and not is_empty_psd_group(child)
                ]
                if len(leaves) != len(members):
                    continue
                boxes = [_selection_box(manifest, child, inventory) for child in leaves]
                if any(box[2] <= 0 or box[3] <= 0 for box in boxes):
                    continue
                axis_ordered = sorted(zip(leaves, boxes), key=lambda pair: pair[1][axis])
                if any(
                    not psd_types_compatible(
                        member.object_type, leaf,
                        conversion_allowed=(member.raster_conversion_allowed
                                            or graph_proofs.get(member.object_id) == leaf.id),
                    )
                    or box[axis] + box[axis + 2] <= main_low
                    or box[axis] >= main_high
                    or abs(((box[1] + box[3] / 2) if horizontal else (box[0] + box[2] / 2))
                           - cross_center) > 2 * cross_tolerance + box[3 - axis]
                    for member, (leaf, box) in zip(members, axis_ordered)
                ):
                    continue
                matches.append((node, [leaf for leaf, _ in axis_ordered]))
            if len(matches) != 1:
                continue
            _, ordered_leaves = matches[0]
            for member, leaf in zip(members, ordered_leaves):
                member_index = item_index_by_old[member.object_id]
                parent_score = (
                    1.0 if old_names.get(member.parent_id or "") == figma_names.get(
                        figma_parents.get(leaf.id) or "")
                    else 0.5
                )
                score, evidence = _score(
                    member, leaf, inventory, len(inventory.objects),
                    len(real_nodes), parent_score,
                    _selection_box(manifest, leaf, inventory),
                )
                # The rank agreement is the order evidence for the pairing.
                order_gain = (1.0 - evidence.order_score) * 0.06
                evidence = evidence.model_copy(update={"order_score": 1.0})
                items[member_index] = items[member_index].model_copy(update={
                    "figma_node_id": leaf.id,
                    "figma_name": leaf.name,
                    "status": "matched",
                    "action": "accept",
                    "score": min(1.0, round(score + order_gain, 6)),
                    "evidence": evidence,
                })
                claimed.add(leaf.id)
        # A PSD group may contain several overlapping visual leaves while the
        # legacy component represents them as adjacent display-list objects.
        # Once one leaf proves the parent correspondence, pair the remaining
        # leaves with still-unmapped visual siblings whose bounds they cover.
        # This catches stacked shade/decoration layers without relying on
        # mojibake-prone names and without adding overlay objects.
        item_index_by_old = {
            item.old_object_id: index
            for index, item in enumerate(items)
            if item.old_object_id
        }
        item_by_source = {
            item.figma_node_id: item
            for item in items
            if item.old_object_id and item.figma_node_id
        }
        for group in real_nodes:
            if group.type.upper() != "GROUP" or not group.children:
                continue
            direct_visuals = [
                child
                for child in group.children
                if not child.children
                and child.properties.get("psdKind", "").casefold()
                in {"shape", "pixel", "smartobject"}
                and not is_psd_visual_empty(child)
            ]
            matched = [
                item_by_source[child.id]
                for child in direct_visuals
                if child.id in item_by_source
            ]
            remaining = [child for child in direct_visuals if child.id not in claimed]
            if not matched or not remaining:
                continue
            old_parents = {
                old_by_id[item.old_object_id or ""].parent_id
                for item in matched
                if item.old_object_id in old_by_id
            }
            if len(old_parents) != 1:
                continue
            old_parent = next(iter(old_parents))
            free_siblings = [
                old
                for old in inventory.objects
                if old.parent_id == old_parent
                and old.default_visible
                and not old.structural_only
                and old.object_type in {"graph", "image", "loader"}
                and (old.object_type != "graph" or old.raster_conversion_allowed)
                and (candidate_index := item_index_by_old.get(old.object_id)) is not None
                and items[candidate_index].figma_node_id is None
                and items[candidate_index].action is None
            ]
            for leaf in remaining:
                box = _selection_box(manifest, leaf, inventory)
                candidates: list[tuple[float, FguiObjectRef]] = []
                for old in free_siblings:
                    if not psd_types_compatible(
                        old.object_type,
                        leaf,
                        conversion_allowed=old.raster_conversion_allowed,
                    ):
                        continue
                    intersection = (
                        max(0.0, min(old.x + old.width, box[0] + box[2]) - max(old.x, box[0]))
                        * max(0.0, min(old.y + old.height, box[1] + box[3]) - max(old.y, box[1]))
                    )
                    coverage = intersection / max(1.0, old.width * old.height)
                    if coverage >= 0.8:
                        candidates.append((coverage, old))
                candidates.sort(key=lambda pair: (-pair[0], pair[1].child_index))
                if not candidates or (
                    len(candidates) > 1 and candidates[0][0] == candidates[1][0]
                ):
                    continue
                old = candidates[0][1]
                item_index = item_index_by_old[old.object_id]
                parent_score = (
                    1.0
                    if old_names.get(old.parent_id or "")
                    == figma_names.get(figma_parents.get(leaf.id) or "")
                    else 0.5
                )
                score, evidence = _score(
                    old,
                    leaf,
                    inventory,
                    len(inventory.objects),
                    len(real_nodes),
                    parent_score,
                    box,
                )
                items[item_index] = items[item_index].model_copy(update={
                    "figma_node_id": leaf.id,
                    "figma_name": leaf.name,
                    "figma_bounds": _figma_bounds(manifest, leaf, inventory),
                    "status": "matched",
                    "action": "accept",
                    "score": score,
                    "evidence": evidence,
                })
                claimed.add(leaf.id)
                item_by_source[leaf.id] = items[item_index]
                free_siblings.remove(old)
    if is_psd and owned_visual_validator is not None:
        # PSDs commonly keep the scene background as a stack: one opaque
        # painted base followed by full-width shades or colour-grade layers,
        # then the interactive UI.  FGUI normally has one existing root image
        # for that stack.  The extra PSD leaves must therefore be baked into
        # that image; mapping them to hidden state objects loses pixels, while
        # adding them as display objects changes program structure and z-order.
        root = manifest.top_level_nodes[0]
        by_node = {node.id: node for node in nodes}
        base_candidates: list[tuple[int, HifiMappingItem, FguiObjectRef, SelectionNode]] = []
        for index, item in enumerate(items):
            if (
                item.action != "accept"
                or not item.old_object_id
                or not item.figma_node_id
                or item.composite_source_ids
            ):
                continue
            old = old_by_id.get(item.old_object_id)
            source = by_node.get(item.figma_node_id)
            if (
                old is None
                or source is None
                or old.parent_id is not None
                or not old.default_visible
                or old.object_type not in {"image", "loader"}
                or old.width < inventory.width * 0.9
                or old.height < inventory.height * 0.9
                or source.type.upper() != "IMAGE"
            ):
                continue
            base_candidates.append((index, item, old, source))
        if len(base_candidates) == 1:
            base_index, base_item, _, base_source = base_candidates[0]
            base_document_index = base_source.properties.get("psdDocumentIndex")
            foreground_indexes = [
                source.properties["psdDocumentIndex"]
                for item in items
                if item.action == "accept"
                and item.old_object_id != base_item.old_object_id
                and item.figma_node_id
                and (source := by_node.get(item.figma_node_id)) is not None
                and isinstance(source.properties.get("psdDocumentIndex"), int)
            ]
            if isinstance(base_document_index, int) and foreground_indexes:
                foreground_index = min(foreground_indexes)
                root_box = _selection_box(manifest, root, inventory)

                def simple_backdrop_context(node: SelectionNode) -> bool:
                    parent_id = figma_parents.get(node.id)
                    while parent_id is not None and parent_id != root.id:
                        parent = by_node[parent_id]
                        if (
                            parent.opacity < 0.999
                            or parent.properties.get("blendMode") not in {"normal", "pass_through"}
                            or parent.properties.get("hasEffects")
                            or parent.properties.get("hasPixelMask")
                            or parent.properties.get("hasVectorMask")
                            or parent.properties.get("clipping")
                        ):
                            return False
                        parent_id = figma_parents.get(parent_id)
                    # _figma_parents intentionally reports top-level PSD
                    # children with no parent because the frame is a canvas,
                    # not a semantic design parent.
                    return parent_id in {None, root.id}

                overlays: list[SelectionNode] = []
                for node in real_nodes:
                    document_index = node.properties.get("psdDocumentIndex")
                    if (
                        node.id in claimed
                        or node.children
                        or node.type.upper() not in {"IMAGE", "VECTOR", "RECTANGLE"}
                        or node.properties.get("psdKind", "").casefold()
                        not in {"shape", "pixel", "smartobject"}
                        or not isinstance(document_index, int)
                        or not base_document_index < document_index < foreground_index
                        or not simple_backdrop_context(node)
                        or is_psd_visual_empty(node)
                    ):
                        continue
                    box = _selection_box(manifest, node, inventory)
                    horizontal_overlap = max(
                        0.0,
                        min(box[0] + box[2], root_box[0] + root_box[2])
                        - max(box[0], root_box[0]),
                    )
                    vertical_overlap = max(
                        0.0,
                        min(box[1] + box[3], root_box[1] + root_box[3])
                        - max(box[1], root_box[1]),
                    )
                    if (
                        horizontal_overlap >= root_box[2] * 0.9
                        and vertical_overlap > 0
                    ):
                        overlays.append(node)
                overlay_ids = frozenset(node.id for node in overlays)
                if overlay_ids:
                    visible_leaf_ids = frozenset(
                        leaf.id
                        for leaf in real_nodes
                        if not leaf.children
                        and leaf.visible
                        and leaf.properties.get("psdKind", "").casefold()
                        in {"type", "shape", "pixel", "smartobject"}
                    )
                    if owned_visual_validator(
                        root.id,
                        overlay_ids,
                        visible_leaf_ids - overlay_ids,
                    ):
                        items[base_index] = base_item.model_copy(update={
                            "composite_group_id": root.id,
                            "composite_source_ids": tuple(sorted(overlay_ids)),
                        })
                        claimed.update(overlay_ids)
    descendants_with_matches = set(claimed)
    for node in reversed(nodes):
        if any(child.id in descendants_with_matches for child in node.children):
            descendants_with_matches.add(node.id)
    new_roots: list[SelectionNode] = []

    def collect_unmatched(node: SelectionNode) -> None:
        if is_psd and node.children:
            for child in node.children:
                collect_unmatched(child)
            return
        if node.id in claimed:
            return
        if node.id in descendants_with_matches:
            for child in node.children:
                collect_unmatched(child)
            return
        new_roots.append(node)

    for root in manifest.top_level_nodes:
        if root.type.upper() in {"FRAME", "COMPONENT", "GROUP"} and len(manifest.top_level_nodes) == 1:
            for child in root.children:
                collect_unmatched(child)
        else:
            collect_unmatched(root)

    canvas_box = None
    occluders: list[tuple[tuple[float, float, float, float], int]] = []
    if is_psd and occlusion_validator is not None:
        root_node = manifest.top_level_nodes[0]
        canvas_box = (root_node.bounds.x, root_node.bounds.y,
                      root_node.bounds.width, root_node.bounds.height)
        for node in real_nodes:
            if (node.type.upper() != "IMAGE" or node.children
                    or node.properties.get("psdKind") != "pixel"
                    or node.opacity < 0.999
                    or node.properties.get("blendMode") != "normal"
                    or node.properties.get("hasEffects")
                    or node.properties.get("hasPixelMask")
                    or node.properties.get("hasVectorMask")
                    or node.properties.get("clipping")):
                continue
            cover_box = (node.bounds.x, node.bounds.y,
                         node.bounds.width, node.bounds.height)
            if not _box_contains(cover_box, canvas_box):
                continue
            cover_index = node.properties.get("psdDocumentIndex")
            if not isinstance(cover_index, int):
                continue
            if occlusion_validator(node):
                occluders.append((cover_box, cover_index))
    for node in new_roots:
        if is_psd and is_psd_visual_empty(node):
            # A zero-size node has no pixels in any context; a mask can only
            # remove pixels, so empty layers cannot contribute artwork.
            items.append(HifiMappingItem(
                version=1, item_id=f"new:{re.sub(r'[^A-Za-z0-9_.:-]', '_', node.id)}",
                figma_node_id=node.id, figma_name=node.name,
                status="structural", action="preserve_structure", score=0,
                evidence=HifiMappingEvidence(version=1, name_score=0, position_score=0,
                    size_score=0, type_score=0, parent_score=0, order_score=0),
                figma_bounds=_figma_bounds(manifest, node, inventory),
            ))
            continue
        if occluders:
            node_box = (node.bounds.x, node.bounds.y,
                        node.bounds.width, node.bounds.height)
            node_index = node.properties.get("psdDocumentIndex")
            if isinstance(node_index, int) and canvas_box is not None and any(
                cover_index > node_index and _box_contains(cover_box, node_box)
                for cover_box, cover_index in occluders
            ):
                items.append(HifiMappingItem(
                    version=1, item_id=f"new:{re.sub(r'[^A-Za-z0-9_.:-]', '_', node.id)}",
                    figma_node_id=node.id, figma_name=node.name,
                    status="occluded", action="preserve_structure", occluded=True,
                    score=0,
                    evidence=HifiMappingEvidence(version=1, name_score=0, position_score=0,
                        size_score=0, type_score=0, parent_score=0, order_score=0),
                    figma_bounds=_figma_bounds(manifest, node, inventory),
                ))
                continue
        if is_psd and _scroll_region_covered(manifest, node, inventory):
            items.append(HifiMappingItem(
                version=1, item_id=f"new:{re.sub(r'[^A-Za-z0-9_.:-]', '_', node.id)}",
                figma_node_id=node.id, figma_name=node.name,
                status="out_of_scope", action="preserve_structure", out_of_scope=True,
                score=0,
                evidence=HifiMappingEvidence(version=1, name_score=0, position_score=0,
                    size_score=0, type_score=0, parent_score=0, order_score=0),
                figma_bounds=_figma_bounds(manifest, node, inventory),
            ))
            continue
        if is_psd and _shared_region_owner(manifest, node, inventory) is not None:
            items.append(HifiMappingItem(
                version=1, item_id=f"new:{re.sub(r'[^A-Za-z0-9_.:-]', '_', node.id)}",
                figma_node_id=node.id, figma_name=node.name,
                status="out_of_scope", action="preserve_structure", out_of_scope=True,
                score=0,
                evidence=HifiMappingEvidence(version=1, name_score=0, position_score=0,
                    size_score=0, type_score=0, parent_score=0, order_score=0),
                figma_bounds=_figma_bounds(manifest, node, inventory),
            ))
            continue
        if is_psd and _overlapping_shared_owner(manifest, node, inventory) is not None:
            items.append(HifiMappingItem(
                version=1, item_id=f"new:{re.sub(r'[^A-Za-z0-9_.:-]', '_', node.id)}",
                figma_node_id=node.id, figma_name=node.name,
                status="out_of_scope", action="preserve_structure", out_of_scope=True,
                score=0,
                evidence=HifiMappingEvidence(version=1, name_score=0, position_score=0,
                    size_score=0, type_score=0, parent_score=0, order_score=0),
                figma_bounds=_figma_bounds(manifest, node, inventory),
            ))
            continue
        node_type = node.type.upper()
        addable = (
            (not node.children or node.properties.get("psdCompositeGroup") is True)
            and node.bounds.width > 0
            and node.bounds.height > 0
            and node_type in {
                "TEXT",
                "RECTANGLE",
                "ELLIPSE",
                "VECTOR",
                "IMAGE",
                "LINE",
                "POLYGON",
                "STAR",
                "GROUP",
            }
        )
        evidence = HifiMappingEvidence(
            version=1, name_score=0.0, position_score=0.0, size_score=0.0,
            type_score=1.0, parent_score=1.0, order_score=1.0,
        )
        items.append(
            HifiMappingItem(
                version=1,
                item_id=f"new:{re.sub(r'[^A-Za-z0-9_.:-]', '_', node.id)}",
                figma_node_id=node.id,
                figma_name=node.name,
                status=(
                    "structural"
                    if is_psd
                    and is_empty_psd_group(node)
                    and node.properties.get("psdCompositeGroup") is not True
                    else "hifi_added" if addable else "blocked"
                ),
                score=1.0,
                evidence=evidence,
                action=(
                    "preserve_structure"
                    if is_psd
                    and is_empty_psd_group(node)
                    and node.properties.get("psdCompositeGroup") is not True
                    else None
                ),
                figma_bounds=_figma_bounds(manifest, node, inventory),
            )
        )
    visibility = {old.object_id: old.default_visible for old in inventory.objects}
    runtime_text_ids = {
        old.object_id
        for old in inventory.objects
        if old.runtime_text_override and old.object_type in {"text", "richtext"}
    }
    items = [
        item.model_copy(update={
            "default_visible": visibility[item.old_object_id],
            # This is an invariant of the old object, not of the matching
            # heuristic that happened to claim it. Layout-sequence and other
            # late promotion passes may replace an earlier mapping item; the
            # instance parameter must still remain the owner of its runtime
            # text while PSD geometry/style is applied to the inner field.
            "preserve_runtime_text": item.old_object_id in runtime_text_ids,
        })
        if item.old_object_id in visibility
        else item
        for item in items
    ]
    if is_psd:
        # A PSD reskin owns the target-state pixels, while the old FGUI owns the
        # runtime contract.  Keep old-only objects by identity, but do not assume
        # that every legacy pixel must remain visible.  A static primitive can be
        # retired when a later mapped sibling clearly replaces the same region.
        old_by_id = {old.object_id: old for old in inventory.objects}
        mapped = [
            (item, old_by_id.get(item.old_object_id or ""))
            for item in items
            if item.action in {"accept", "retarget"} and item.figma_bounds is not None
        ]

        def coverage(
            inner: tuple[float, float, float, float],
            outer: tuple[float, float, float, float],
        ) -> float:
            ix = max(inner[0], outer[0])
            iy = max(inner[1], outer[1])
            ir = min(inner[0] + inner[2], outer[0] + outer[2])
            ib = min(inner[1] + inner[3], outer[1] + outer[3])
            if ir <= ix or ib <= iy or inner[2] <= 0 or inner[3] <= 0:
                return 0.0
            return (ir - ix) * (ib - iy) / (inner[2] * inner[3])

        revised: list[HifiMappingItem] = []
        for item in items:
            if item.action is None and item.status == "fgui_only":
                item = item.model_copy(update={"action": "keep_old"})
            old = old_by_id.get(item.old_object_id or "")
            if old is not None and item.status == "fgui_only":
                if not old.default_visible:
                    item = item.model_copy(update={"visual_disposition": "other_state"})
                elif old.structural_only:
                    item = item.model_copy(update={"visual_disposition": "structural"})
                elif (
                    old.object_type == "graph"
                    and not old.dynamic_properties
                    and item.old_bounds is not None
                ):
                    replacement = any(
                        owner is not None
                        and owner.component_relative_path == old.component_relative_path
                        and owner.parent_id == old.parent_id
                        and owner.child_index > old.child_index
                        and owner.object_type in {"image", "loader", "component"}
                        and mapped_item.figma_bounds is not None
                        and coverage(item.old_bounds, mapped_item.figma_bounds) >= 0.80
                        for mapped_item, owner in mapped
                    )
                    if replacement:
                        item = item.model_copy(update={"visual_disposition": "retire"})
            revised.append(item)
        items = revised
    unresolved = sum(item.action is None for item in items)
    return HifiMappingDraft(
        version=1,
        policy_revision=HIFI_MAPPING_POLICY_REVISION,
        mapping_revision=1,
        old_canvas_size=(inventory.width, inventory.height),
        source_canvas_size=(manifest.top_level_nodes[0].bounds.width, manifest.top_level_nodes[0].bounds.height),
        items=tuple(items),
        unresolved_count=unresolved,
    )


def apply_mapping_decision(
    draft: HifiMappingDraft,
    decision: HifiMappingDecision,
    manifest: SelectionManifest,
    *, conversion_object_ids: frozenset[str] = frozenset(),
) -> HifiMappingDraft:
    if decision.action == "preserve_structure":
        raise HifiMappingError("mapping_action_not_allowed")
    if decision.mapping_revision != draft.mapping_revision:
        raise HifiMappingError("stale_mapping")
    by_id = {item.item_id: item for item in draft.items}
    if decision.item_id not in by_id:
        raise HifiMappingError("mapping_item_not_found")
    selected_item = by_id[decision.item_id]
    if selected_item.occluded:
        raise HifiMappingError("mapping_action_not_allowed")
    if selected_item.out_of_scope:
        # The scope policy already decided this item; no user action can
        # overwrite it in this round.
        raise HifiMappingError("mapping_action_not_allowed")
    if selected_item.owned_source_ids and decision.action != "accept":
        raise HifiMappingError("hifi_owned_visual_requires_regeneration")
    if selected_item.status == "blocked" and decision.action != "exception":
        raise HifiMappingError("mapping_action_not_allowed")
    is_psd = manifest.top_level_nodes[0].id.startswith("psd-root:")
    if selected_item.status == "hifi_added" and (
        is_psd or decision.action not in {"add_visual", "exception"}
    ):
        raise HifiMappingError("mapping_action_not_allowed")
    if decision.action == "add_visual" and selected_item.status != "hifi_added":
        raise HifiMappingError("mapping_action_not_allowed")
    if decision.action == "keep_old" and selected_item.old_object_id is None:
        raise HifiMappingError("mapping_action_not_allowed")
    if decision.action == "exception" and is_psd:
        # The PSD is a reskin, not an independent composition. Every PSD
        # visual must replace an existing object, while every legacy-only
        # object remains visible. Neither side may be waived.
        raise HifiMappingError("hifi_old_visual_retention_not_allowed")
    if decision.action == "accept" and (
        selected_item.old_object_id is None or selected_item.figma_node_id is None
    ):
        raise HifiMappingError("mapping_action_not_allowed")
    if decision.action == "retarget" and selected_item.old_object_id is None:
        raise HifiMappingError("mapping_action_not_allowed")
    node_ids = {node.id for node in _nodes(manifest)}
    if decision.figma_node_id is not None and decision.figma_node_id not in node_ids:
        raise HifiMappingError("figma_node_not_found")
    if decision.action == "retarget":
        occupied = {
            item.figma_node_id
            for item in draft.items
            if item.item_id != decision.item_id and item.action in {"accept", "retarget"}
        }
        if decision.figma_node_id in occupied:
            raise HifiMappingError("figma_node_already_mapped")
    selected_figma_id = (
        decision.figma_node_id
        if decision.action == "retarget"
        else by_id[decision.item_id].figma_node_id
    )
    selected_node = next((node for node in _nodes(manifest) if node.id == selected_figma_id), None)
    if (
        manifest.top_level_nodes[0].id.startswith("psd-root:")
        and decision.action in {"accept", "retarget"}
        and selected_node is not None
        and not psd_types_compatible(selected_item.old_object_type or "", selected_node,
                                     conversion_allowed=(selected_item.old_object_id in conversion_object_ids
                                                         or selected_item.graph_conversion_proven
                                                         and selected_item.figma_node_id == selected_figma_id))
    ):
        raise HifiMappingError("hifi_incompatible_mapping")
    selected_bounds = next(
        (
            item.figma_bounds
            for item in draft.items
            if item.figma_node_id == selected_figma_id and item.figma_bounds is not None
        ),
        None,
    )
    updated_items: list[HifiMappingItem] = []
    for item in draft.items:
        if item.item_id == decision.item_id:
            updated_items.append(
                item.model_copy(
                    update={
                        "action": decision.action,
                        "visual_disposition": (
                            "preserve" if decision.action == "keep_old"
                            else item.visual_disposition
                        ),
                        "figma_node_id": selected_figma_id,
                        "figma_name": selected_node.name if selected_node else item.figma_name,
                        "figma_bounds": selected_bounds or item.figma_bounds,
                        "graph_conversion_proven": item.graph_conversion_proven
                        and item.figma_node_id == selected_figma_id,
                    }
                )
            )
        elif (
            decision.action in {"accept", "retarget"}
            and item.status in {"hifi_added", "blocked"}
            and item.figma_node_id == selected_figma_id
        ):
            updated_items.append(item.model_copy(update={"action": "exception"}))
        else:
            updated_items.append(item)
    items = tuple(updated_items)
    return draft.model_copy(update={
        "mapping_revision": draft.mapping_revision + 1,
        "items": items,
        "unresolved_count": sum(item.action is None for item in items),
    })
