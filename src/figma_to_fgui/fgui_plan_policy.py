"""Shared capability rule policy for FairyGUI plan compilation and validation."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from figma_to_fgui.fgui_plan_models import CapabilityStatus, PlanNodeType

NATIVE_CONTAINER_RULE_ID: Final = "fgui.native.container"
NATIVE_GRAPH_RULE_ID: Final = "fgui.native.graph"
NATIVE_TEXT_RULE_ID: Final = "fgui.native.text"
NATIVE_RICH_TEXT_RULE_ID: Final = "fgui.native.rich_text"
NATIVE_IMAGE_RULE_ID: Final = "fgui.native.image"
NATIVE_COMPONENT_REFERENCE_RULE_ID: Final = "fgui.native.component_reference"
NATIVE_CLIP_SOURCE_RULE_ID: Final = "fgui.native.clip_source"
RASTER_SUBTREE_RULE_ID: Final = "fgui.fallback.raster_subtree"

NATIVE_RULE_TO_NODE_TYPE: Final[Mapping[str, PlanNodeType]] = MappingProxyType(
    {
        NATIVE_CONTAINER_RULE_ID: PlanNodeType.CONTAINER,
        NATIVE_GRAPH_RULE_ID: PlanNodeType.GRAPH,
        NATIVE_TEXT_RULE_ID: PlanNodeType.TEXT,
        NATIVE_RICH_TEXT_RULE_ID: PlanNodeType.RICH_TEXT,
        NATIVE_IMAGE_RULE_ID: PlanNodeType.IMAGE,
        NATIVE_COMPONENT_REFERENCE_RULE_ID: PlanNodeType.COMPONENT_REFERENCE,
        NATIVE_CLIP_SOURCE_RULE_ID: PlanNodeType.GRAPH,
    }
)
RULE_TO_NODE_TYPE: Final[Mapping[str, PlanNodeType]] = MappingProxyType(
    {**NATIVE_RULE_TO_NODE_TYPE, RASTER_SUBTREE_RULE_ID: PlanNodeType.RASTER_SUBTREE}
)


def node_type_for_capability(
    status: CapabilityStatus, rule_id: str
) -> PlanNodeType | None:
    """Resolve a supported capability status/rule pair to its plan-node type."""
    if status == CapabilityStatus.NATIVE:
        return NATIVE_RULE_TO_NODE_TYPE.get(rule_id)
    if (
        status == CapabilityStatus.RASTER_FALLBACK
        and rule_id == RASTER_SUBTREE_RULE_ID
    ):
        return PlanNodeType.RASTER_SUBTREE
    return None
