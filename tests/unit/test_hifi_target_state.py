from __future__ import annotations

from lxml import etree

from figma_to_fgui.hifi_target_state import (
    gear_display_visible,
    runtime_controller_pages,
    target_state_visibility,
)


def _component() -> etree._Element:
    return etree.fromstring(
        b"""
        <component size="402,132">
          <controller name="isGray" pages="0,false,1,true" selected="0"/>
          <controller name="type" pages="0,1,1,2,2,3" selected="2"/>
          <displayList>
            <image id="gray" name="bg_common_gary">
              <gearDisplay controller="isGray" pages="1"/>
            </image>
            <loader id="normal" name="icon_bg">
              <gearDisplay controller="isGray" pages="0"/>
            </loader>
            <text id="title" name="title" text="Challenge"/>
          </displayList>
        </component>
        """
    )


def test_runtime_controller_pages_use_first_declared_page_not_editor_selected() -> None:
    component = _component()
    assert runtime_controller_pages(component) == {"isGray": "0", "type": "0"}


def test_challenge_gray_background_is_other_state_only_in_runtime_target_state() -> None:
    component = _component()
    gray = component.xpath("./displayList/image[@id='gray']")[0]
    normal = component.xpath("./displayList/loader[@id='normal']")[0]

    assert target_state_visibility(gray).disposition == "other_state_only"
    assert target_state_visibility(normal).disposition == "target_visible"


def test_gear_display_visibility_flips_when_runtime_page_changes() -> None:
    component = _component()
    gray = component.xpath("./displayList/image[@id='gray']")[0]
    normal = component.xpath("./displayList/loader[@id='normal']")[0]

    pages = {"isGray": "1", "type": "0"}
    assert gear_display_visible(gray, pages)
    assert not gear_display_visible(normal, pages)


def test_static_visual_is_not_mislabeled_as_other_state() -> None:
    component = _component()
    title = component.xpath("./displayList/text[@id='title']")[0]
    assert target_state_visibility(title).disposition == "static_visible"
