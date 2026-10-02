from __future__ import annotations

from lxml import etree

from figma_to_fgui.hifi_replacement_models import (
    HIFI_MAPPING_POLICY_REVISION,
    HifiMappingDraft,
    HifiMappingEvidence,
    HifiMappingItem,
)
from figma_to_fgui.hifi_review import build_object_diffs


def _evidence() -> HifiMappingEvidence:
    return HifiMappingEvidence(
        name_score=1.0,
        position_score=1.0,
        size_score=1.0,
        type_score=1.0,
        parent_score=1.0,
        order_score=1.0,
    )


def _doc() -> etree._ElementTree:
    root = etree.Element("component", size="300,80")
    display = etree.SubElement(root, "displayList")
    etree.SubElement(display, "component", id="button", name="button", xy="0,0", size="300,80")
    etree.SubElement(display, "image", id="other_state", name="gray", xy="0,0", size="300,80")
    return etree.ElementTree(root)


def test_semantic_component_pair_reports_modify_even_when_root_geometry_is_unchanged() -> None:
    item = HifiMappingItem(
        item_id="component",
        old_object_id="button",
        old_name="button",
        old_object_type="component",
        figma_node_id="button_group",
        figma_name="Button_Group",
        status="matched",
        score=1.0,
        evidence=_evidence(),
        action="accept",
        owned_group_id="button_group",
    )
    mapping = HifiMappingDraft(
        policy_revision=HIFI_MAPPING_POLICY_REVISION,
        mapping_revision=1,
        items=(item,),
        unresolved_count=0,
    )

    diff = build_object_diffs(_doc(), _doc(), mapping)[0]

    assert diff.kind == "changed"
    assert "visualBundle" in diff.changed_fields
    assert "MODIFY" in diff.summary


def test_other_state_visual_reports_keep_without_current_pixel_contribution() -> None:
    item = HifiMappingItem(
        item_id="other",
        old_object_id="other_state",
        old_name="gray",
        old_object_type="image",
        status="fgui_only",
        score=0.0,
        evidence=_evidence(),
        action="keep_old",
        visual_disposition="other_state",
    )
    mapping = HifiMappingDraft(
        policy_revision=HIFI_MAPPING_POLICY_REVISION,
        mapping_revision=1,
        items=(item,),
        unresolved_count=0,
    )

    diff = build_object_diffs(_doc(), _doc(), mapping)[0]

    assert diff.kind == "kept"
    assert "当前目标状态不贡献像素" in diff.summary
