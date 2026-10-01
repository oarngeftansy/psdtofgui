"""Fail-closed, server-owned grants for in-place FGUI object type changes.

An uploaded project cannot grant itself permission. Grants bind a local object
to an exact project fingerprint and source component digest.
"""

import hashlib
import json
import os
from pathlib import Path

from lxml import etree

from figma_to_fgui.figma_selection import SelectionNode
from figma_to_fgui.hifi_replacement_models import FguiComponentInventory, HifiMappingItem

GRAPH_ATTRIBUTES = frozenset({"type", "fillColor", "lineColor", "lineSize", "corner"})
_GRANTS_ENV = "HIFI_TYPE_CONVERSION_GRANTS_FILE"


def _grants(root: Path) -> tuple[dict[str, str], ...]:
    configured = os.environ.get(_GRANTS_ENV)
    if not configured:
        return ()
    path = Path(configured).resolve()
    if path.is_relative_to(root.resolve()):
        return ()
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return ()
    if not isinstance(document, dict) or document.get("version") != 1:
        return ()
    entries = document.get("graph_to_image")
    if not isinstance(entries, list):
        return ()
    required = {"project_fingerprint", "component_relative_path", "object_id", "component_sha256"}
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != required or not all(
            isinstance(entry[key], str) for key in required
        ):
            return ()
        result.append(entry)
    return tuple(result)


def can_convert(root: Path, inventory: FguiComponentInventory, object_id: str) -> bool:
    object_ref = next((item for item in inventory.objects if item.object_id == object_id), None)
    if object_ref is None or object_ref.object_type != "graph":
        return False
    path = inventory.target.component_relative_path
    try:
        source = (root / path).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            return False
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError:
        return False
    return any(
        grant["component_relative_path"] == path
        and grant["object_id"] == object_id
        and grant["project_fingerprint"] == inventory.target.project_fingerprint
        and grant["component_sha256"] == digest
        for grant in _grants(root)
    )


def can_convert_mapping(root: Path, inventory: FguiComponentInventory,
                        item: HifiMappingItem, node: SelectionNode | None = None) -> bool:
    if item.old_object_id is None or item.old_object_type != "graph":
        return False
    if can_convert(root, inventory, item.old_object_id):
        return True
    if (not item.graph_conversion_proven or node is None
        or node.id != item.figma_node_id or not node.resource_keys
        or node.children or node.properties.get("psdKind") not in {"shape", "pixel"}
        or node.properties.get("clipping")
        or node.properties.get("blendMode") != "normal"):
        # Effects and masks ride inside the isolated PSD raster; clipping and
        # non-normal blends change compositing and stay unauthorized.
        return False
    return any(obj.object_id == item.old_object_id and obj.object_type == "graph"
               and not obj.structural_only for obj in inventory.objects)


def convert_graph(element: etree._Element) -> None:
    if element.tag != "graph":
        raise ValueError("hifi_type_conversion_invalid")
    element.tag = "image"
    for attribute in GRAPH_ATTRIBUTES:
        element.attrib.pop(attribute, None)
