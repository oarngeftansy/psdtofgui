from __future__ import annotations

from collections.abc import Iterable

from figma_to_fgui.hifi_replacement_models import (
    FguiComponentInventory,
    FguiObjectRef,
    HifiMappingDraft,
    HifiMappingItem,
)


_VISUAL_SURFACE_TYPES = {"graph", "image", "loader"}
_REPLACEMENT_OWNER_TYPES = {"graph", "image", "loader", "component"}


def _scope_key(object_id: str | None) -> str:
    if not object_id or ":" not in object_id:
        return ""
    return object_id.rsplit(":", 1)[0]


def _coverage(
    target: tuple[float, float, float, float],
    cover: tuple[float, float, float, float],
) -> float:
    tx, ty, tw, th = target
    cx, cy, cw, ch = cover
    if min(tw, th, cw, ch) <= 0:
        return 0.0
    left = max(tx, cx)
    top = max(ty, cy)
    right = min(tx + tw, cx + cw)
    bottom = min(ty + th, cy + ch)
    if right <= left or bottom <= top:
        return 0.0
    return ((right - left) * (bottom - top)) / (tw * th)


def _is_static_visual(obj: FguiObjectRef) -> bool:
    return (
        obj.object_type.casefold() in _VISUAL_SURFACE_TYPES
        and not obj.structural_only
        and not obj.dynamic_properties
        and obj.default_visible
    )


def _mapped_visual_owners(
    mapping: HifiMappingDraft,
    inventory: FguiComponentInventory,
) -> Iterable[tuple[HifiMappingItem, FguiObjectRef]]:
    old_by_id = {obj.object_id: obj for obj in inventory.objects}
    for item in mapping.items:
        if item.action not in {"accept", "retarget"}:
            continue
        if item.old_object_id is None or item.figma_bounds is None:
            continue
        owner = old_by_id.get(item.old_object_id)
        if owner is None or owner.object_type.casefold() not in _REPLACEMENT_OWNER_TYPES:
            continue
        if owner.structural_only or not owner.default_visible:
            continue
        yield item, owner


def apply_render_contribution_policy(
    mapping: HifiMappingDraft,
    inventory: FguiComponentInventory,
    *,
    minimum_coverage: float = 0.80,
) -> HifiMappingDraft:
    """Retire legacy pixels while preserving their runtime object identities.

    Policy 25 only auto-retired unmatched static ``graph`` objects. Real FGUI
    projects also use images/loaders as legacy skins, which leaves old paint
    underneath newly mapped PSD visuals. This pass works on the expanded,
    instance-qualified inventory and only retires a static visual when a mapped
    visual owner in the same component/parent/scope covers it.

    Gear/state-driven visuals are deliberately not retired without explicit
    state evidence because the PSD describes only the selected target state.
    """
    old_by_id = {obj.object_id: obj for obj in inventory.objects}
    mapped = tuple(_mapped_visual_owners(mapping, inventory))
    revised: list[HifiMappingItem] = []

    for item in mapping.items:
        old = old_by_id.get(item.old_object_id or "")
        if old is None or item.status != "fgui_only" or item.action != "keep_old":
            revised.append(item)
            continue

        if not old.default_visible:
            revised.append(item.model_copy(update={"visual_disposition": "other_state"}))
            continue
        if old.structural_only:
            revised.append(item.model_copy(update={"visual_disposition": "structural"}))
            continue
        if not _is_static_visual(old) or item.old_bounds is None:
            revised.append(item)
            continue

        replacement = any(
            owner.object_id != old.object_id
            and owner.component_relative_path == old.component_relative_path
            and owner.parent_id == old.parent_id
            and _scope_key(owner.object_id) == _scope_key(old.object_id)
            and owner.child_index > old.child_index
            and mapped_item.figma_bounds is not None
            and _coverage(item.old_bounds, mapped_item.figma_bounds) >= minimum_coverage
            for mapped_item, owner in mapped
        )
        revised.append(
            item.model_copy(update={"visual_disposition": "retire"})
            if replacement
            else item
        )

    return mapping.model_copy(update={
        "policy_revision": 26,
        "items": tuple(revised),
        "unresolved_count": sum(item.action is None for item in revised),
    })
