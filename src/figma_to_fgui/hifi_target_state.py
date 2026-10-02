from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lxml import etree

TargetStateDisposition = Literal["target_visible", "other_state_only", "static_visible", "static_hidden"]


@dataclass(frozen=True)
class TargetStateVisibility:
    disposition: TargetStateDisposition
    controller_pages: tuple[tuple[str, str], ...] = ()


def runtime_controller_pages(component: etree._Element) -> dict[str, str]:
    """Resolve the runtime-initial page for each controller in a component.

    FairyGUI component instances start on the first declared page unless an
    instance override changes it later. The local component definition therefore
    uses the first page as the target-state baseline for visual ownership.
    """
    result: dict[str, str] = {}
    for controller in component.xpath("./controller[@name]"):
        raw_pages = str(controller.get("pages", "")).split(",")
        page_ids = raw_pages[0::2]
        if page_ids:
            result[str(controller.get("name"))] = page_ids[0]
    return result


def gear_display_visible(
    element: etree._Element,
    controller_pages: dict[str, str],
) -> bool:
    """Evaluate existing gearDisplay gates for the runtime-initial target state."""
    if element.get("visible") == "false":
        return False
    for gear in element.findall("gearDisplay"):
        controller_name = gear.get("controller")
        if not controller_name:
            continue
        selected = controller_pages.get(controller_name)
        if selected is None:
            continue
        pages = {page for page in str(gear.get("pages", "")).split(",") if page}
        if pages and selected not in pages:
            return False
    return True


def target_state_visibility(element: etree._Element) -> TargetStateVisibility:
    """Classify whether an object contributes pixels to the runtime target state.

    This deliberately does not mutate gearDisplay. It is evidence for the
    mapping layer: an object hidden by its existing controller state is
    ``other_state_only`` and must not be treated as a current-state KEEP visual.
    A controller-driven object visible in the target state remains
    ``target_visible`` and still needs explicit ownership or a state-specific
    replacement decision.
    """
    component = element.getparent()
    while component is not None and component.tag != "component":
        component = component.getparent()
    if component is None:
        return TargetStateVisibility(
            disposition="static_hidden" if element.get("visible") == "false" else "static_visible"
        )

    controller_pages = runtime_controller_pages(component)
    gear_displays = tuple(element.findall("gearDisplay"))
    if not gear_displays:
        return TargetStateVisibility(
            disposition="static_hidden" if element.get("visible") == "false" else "static_visible",
            controller_pages=tuple(sorted(controller_pages.items())),
        )

    visible = gear_display_visible(element, controller_pages)
    return TargetStateVisibility(
        disposition="target_visible" if visible else "other_state_only",
        controller_pages=tuple(sorted(controller_pages.items())),
    )
