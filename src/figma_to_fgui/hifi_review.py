from __future__ import annotations

from typing import Literal

from lxml import etree

from figma_to_fgui.hifi_replacement_models import HifiMappingDraft, HifiObjectDiff

_VISUAL_ATTRIBUTES = (
    "xy",
    "size",
    "text",
    "color",
    "font",
    "fontSize",
    "align",
    "bold",
    "italic",
    "leading",
    "letterSpacing",
    "strokeColor",
    "strokeSize",
    "alpha",
    "rotation",
    "src",
    "fileName",
    "pkg",
    "url", "type", "fillColor", "lineColor", "lineSize", "corner",
)


def build_object_diffs(
    before_document: etree._ElementTree,
    after_document: etree._ElementTree,
    mapping: HifiMappingDraft,
) -> tuple[HifiObjectDiff, ...]:
    before_by_id = {
        str(element.attrib["id"]): element
        for element in before_document.xpath("./displayList/*[@id]")
    }
    after_by_id = {
        str(element.attrib["id"]): element
        for element in after_document.xpath("./displayList/*[@id]")
    }
    result: list[HifiObjectDiff] = []
    for item in mapping.items:
        if item.action in {"accept", "retarget"} and item.old_object_id is not None:
            before = before_by_id.get(item.old_object_id)
            after = after_by_id.get(item.old_object_id)
            changed_fields = tuple(
                attribute
                for attribute in _VISUAL_ATTRIBUTES
                if (before.attrib.get(attribute) if before is not None else None)
                != (after.attrib.get(attribute) if after is not None else None)
            )
            if before is not None and after is not None and before.tag != after.tag:
                changed_fields = ("objectType", *changed_fields)
            kind: Literal["changed", "added", "kept", "exception"] = (
                "changed" if changed_fields else "kept"
            )
            summary = (
                f"修改视觉字段：{', '.join(changed_fields)}"
                if changed_fields
                else "映射已确认，候选内容无需修改"
            )
        elif item.action == "add_visual":
            kind = "added"
            changed_fields = ("displayList",)
            summary = "新增私有静态视觉对象"
        elif item.action == "preserve_structure":
            kind = "kept"
            changed_fields = ()
            summary = "非绘制结构原样保留；子对象与源视觉仍分别核验"
        elif item.action == "keep_old":
            if item.visual_disposition == "retire":
                kind = "changed"
                changed_fields = ("visible",)
                summary = "保留旧对象身份与程序关系；目标状态停止贡献旧视觉"
            else:
                kind = "kept"
                changed_fields = ()
                summary = "HIFI 中无对应对象，保留旧 FGUI 对象"
        else:
            kind = "exception"
            changed_fields = ()
            summary = (
                "PSD 未绘制该对象；候选隐藏其默认视觉，对象与程序逻辑保留"
                if item.status == "fgui_only"
                else "此项超出安全自动修改范围，候选未修改"
            )
        result.append(
            HifiObjectDiff(
                version=1,
                item_id=item.item_id,
                kind=kind,
                old_object_id=item.old_object_id,
                old_name=item.old_name,
                figma_node_id=item.figma_node_id,
                figma_name=item.figma_name,
                changed_fields=changed_fields,
                summary=summary,
            )
        )
    return tuple(result)
