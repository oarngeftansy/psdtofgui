import hashlib
import json

import pytest
from PIL import Image
from test_hifi_nested import nested_case

from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode, SelectionResource
from figma_to_fgui.hifi_mapping import build_mapping, require_psd_coverage
from figma_to_fgui.hifi_nested import component_target
from figma_to_fgui.hifi_patch import HifiPatchError, build_hifi_change_bundle
from figma_to_fgui.hifi_project_inspector import inspect_component
from figma_to_fgui.models import Bounds


def test_owned_visual_cannot_change_existing_graph_type_even_with_conversion_grant(tmp_path, monkeypatch):
    root, tree, _, _ = nested_case(tmp_path)
    child_path = "assets/MyVillage/Common/Button_Common.xml"
    local = inspect_component(root, component_target(root, tree.target, child_path))
    grants = tmp_path / "grants.json"
    grants.write_text(json.dumps({"version": 1, "graph_to_image": [{
        "component_relative_path": child_path,
        "object_id": "bg",
        "component_sha256": hashlib.sha256((root / child_path).read_bytes()).hexdigest(),
        "project_fingerprint": local.target.project_fingerprint,
    }]}), encoding="utf-8")
    monkeypatch.setenv("HIFI_TYPE_CONVERSION_GRANTS_FILE", str(grants))
    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    Image.new("RGBA", (120, 44), (10, 80, 160, 255)).save(resource_dir / "body", format="PNG")
    visual = SelectionNode(id="body-leaf", name="Background", type="IMAGE",
                           bounds=Bounds(x=0, y=0, width=120, height=44),
                           properties={"psdKind": "shape"}, resource_keys=("body",))
    title = SelectionNode(id="text-leaf", name="title", type="TEXT", text="New title",
                          bounds=Bounds(x=10, y=10, width=90, height=24),
                          properties={"psdKind": "type"})
    group = SelectionNode(id="button-group", name="Button", type="GROUP",
                          bounds=Bounds(x=0, y=0, width=120, height=44),
                          children=(visual, title))
    source = SelectionManifest(version=1, display_name="PSD", top_level_nodes=(
        SelectionNode(id="psd-root:synthetic", name="PSD", type="FRAME",
                      bounds=Bounds(x=0, y=0, width=120, height=44), children=(group,)),
    ), resources=(SelectionResource(key="body", mime_type="image/png",
                                   size=(resource_dir / "body").stat().st_size),))
    draft = build_mapping(local, source)
    items = tuple(i.model_copy(update={
        "action": "accept", "figma_node_id": "body-leaf",
        "owned_source_ids": ("body-leaf",), "owned_group_id": "button-group",
        "retained_source_ids": ("text-leaf",),
    }) if i.old_object_id == "bg" else i.model_copy(update={
        "action": "accept", "figma_node_id": "text-leaf",
    }) if i.old_object_id == "title" else i.model_copy(update={"action": "exception"})
    for i in draft.items)
    mapping = draft.model_copy(update={"items": items, "unresolved_count": 0})
    require_psd_coverage(mapping, source)
    with pytest.raises(HifiPatchError, match="hifi_display_type_change_forbidden"):
        build_hifi_change_bundle(root, local, source, mapping, selection_root=tmp_path)
