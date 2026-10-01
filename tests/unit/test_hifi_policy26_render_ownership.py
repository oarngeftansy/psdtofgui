from __future__ import annotations

from figma_to_fgui.hifi_replacement_models import (
    HifiMappingDraft,
    HifiMappingEvidence,
    HifiMappingItem,
)


def _evidence() -> HifiMappingEvidence:
    return HifiMappingEvidence(
        version=1,
        name_score=0.0,
        position_score=1.0,
        size_score=1.0,
        type_score=1.0,
        parent_score=1.0,
        order_score=1.0,
    )


def _mapped_skin(
    *,
    object_id: str = "button:icon_bg",
    bounds: tuple[float, float, float, float] = (100.0, 200.0, 375.0, 72.0),
) -> HifiMappingItem:
    return HifiMappingItem(
        version=1,
        item_id="mapped-skin",
        old_object_id=object_id,
        old_name="icon_bg",
        old_object_type="loader",
        figma_node_id="psd-layer:skin",
        figma_name="skin",
        status="matched",
        score=1.0,
        evidence=_evidence(),
        action="accept",
        old_bounds=bounds,
        figma_bounds=bounds,
        default_visible=True,
    )


def _legacy(
    *,
    object_id: str = "button:bg_common_gary",
    object_type: str = "image",
    bounds: tuple[float, float, float, float] = (100.0, 200.0, 375.0, 77.0),
    visible: bool = True,
) -> HifiMappingItem:
    return HifiMappingItem(
        version=1,
        item_id="legacy",
        old_object_id=object_id,
        old_name="bg_common_gary",
        old_object_type=object_type,
        status="fgui_only",
        score=0.0,
        evidence=_evidence(),
        action="keep_old",
        old_bounds=bounds,
        default_visible=visible,
    )


def _draft(*items: HifiMappingItem) -> HifiMappingDraft:
    return HifiMappingDraft(
        version=1,
        policy_revision=25,
        mapping_revision=1,
        old_canvas_size=(1080.0, 1920.0),
        source_canvas_size=(1080.0, 1920.0),
        items=items,
        unresolved_count=0,
    )


def test_psd_skin_retires_overlapping_legacy_image_in_same_instance_scope() -> None:
    mapping = _draft(_legacy(), _mapped_skin())

    legacy = next(item for item in mapping.items if item.item_id == "legacy")
    assert legacy.action == "keep_old"
    assert legacy.visual_disposition == "retire"


def test_psd_skin_retires_overlapping_legacy_graph_in_same_instance_scope() -> None:
    mapping = _draft(_legacy(object_type="graph"), _mapped_skin())

    legacy = next(item for item in mapping.items if item.item_id == "legacy")
    assert legacy.visual_disposition == "retire"


def test_replacement_in_another_instance_cannot_retire_legacy_visual() -> None:
    mapping = _draft(
        _legacy(object_id="noble:n30"),
        _mapped_skin(object_id="merit:icon_bg"),
    )

    legacy = next(item for item in mapping.items if item.item_id == "legacy")
    assert legacy.visual_disposition == "preserve"


def test_full_screen_background_cannot_retire_small_control_visual() -> None:
    mapping = _draft(
        _legacy(bounds=(40.0, 100.0, 80.0, 80.0)),
        _mapped_skin(bounds=(0.0, 0.0, 1080.0, 1920.0)),
    )

    legacy = next(item for item in mapping.items if item.item_id == "legacy")
    assert legacy.visual_disposition == "preserve"


def test_hidden_other_state_visual_is_not_auto_retired() -> None:
    mapping = _draft(_legacy(visible=False), _mapped_skin())

    legacy = next(item for item in mapping.items if item.item_id == "legacy")
    assert legacy.visual_disposition == "preserve"


def test_non_psd_mapping_does_not_change_legacy_visual_disposition() -> None:
    skin = _mapped_skin().model_copy(update={"figma_node_id": "42:17"})
    mapping = _draft(_legacy(), skin)

    legacy = next(item for item in mapping.items if item.item_id == "legacy")
    assert legacy.visual_disposition == "preserve"
