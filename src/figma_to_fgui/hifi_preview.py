from __future__ import annotations

from pydantic import Field

from figma_to_fgui.hifi_replacement_models import FguiComponentInventory
from figma_to_fgui.service_contracts import StrictVersionedModel


class HifiPreviewObject(StrictVersionedModel):
    object_id: str
    name: str
    object_type: str
    bounds: tuple[float, float, float, float]
    shared_resource: bool = False
    behavior_protected: bool = False
    behavior_roles: tuple[str, ...] = ()
    dynamic_properties: tuple[str, ...] = ()


class HifiStructurePreview(StrictVersionedModel):
    evidence_kind: str = Field(pattern=r"^structure_preview$")
    objects: tuple[HifiPreviewObject, ...]


def build_structure_preview(inventory: FguiComponentInventory) -> HifiStructurePreview:
    return HifiStructurePreview(
        version=1,
        evidence_kind="structure_preview",
        objects=tuple(
            HifiPreviewObject(
                version=1,
                object_id=item.object_id,
                name=item.name,
                object_type=item.object_type,
                bounds=(
                    max(0.0, min(1.0, item.x / inventory.width)),
                    max(0.0, min(1.0, item.y / inventory.height)),
                    max(0.0, min(1.0, item.width / inventory.width)),
                    max(0.0, min(1.0, item.height / inventory.height)),
                ),
                shared_resource=item.shared_resource,
                behavior_protected=item.behavior_protected,
                behavior_roles=item.behavior_roles,
                dynamic_properties=item.dynamic_properties,
            )
            for item in inventory.objects
        ),
    )
