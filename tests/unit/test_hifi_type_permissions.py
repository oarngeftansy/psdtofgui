import hashlib
import json

import pytest
from lxml import etree
from test_hifi_nested import nested_case

from figma_to_fgui.hifi_project_inspector import inspect_component


def test_conversion_requires_exact_server_grant_and_preserves_behavior(tmp_path, monkeypatch):
    from figma_to_fgui import hifi_type_permissions as permissions

    root, tree, _, _ = nested_case(tmp_path)
    from figma_to_fgui.hifi_nested import component_target

    path = "assets/MyVillage/Common/Button_Common.xml"
    target = component_target(root, tree.target, path)
    inventory = inspect_component(root, target)
    original = etree.parse(str(root / path)).find("./displayList/graph")
    assert not permissions.can_convert(root, inventory, "bg")


    grants = tmp_path / "graph-grants.json"
    grants.write_text(json.dumps({"version": 1, "graph_to_image": [{
        "component_relative_path": path,
        "object_id": "bg",
        "component_sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
        "project_fingerprint": inventory.target.project_fingerprint,
    }]}), encoding="utf-8")
    monkeypatch.setenv("HIFI_TYPE_CONVERSION_GRANTS_FILE", str(grants))
    assert permissions.can_convert(root, inventory, "bg")
    assert not permissions.can_convert(root, inventory, "title")
    other_project = inventory.model_copy(
        update={"target": inventory.target.model_copy(update={"project_fingerprint": "f" * 64})}
    )
    assert not permissions.can_convert(root, other_project, "bg")
    before = etree.tostring(original.find("gearDisplay"))
    permissions.convert_graph(original)
    assert original.tag == "image"
    assert original.get("id") == "bg" and original.get("name") == "Background"
    assert etree.tostring(original.find("gearDisplay")) == before
    assert "fillColor" not in original.attrib and "type" not in original.attrib
    (root / path).write_bytes((root / path).read_bytes() + b" ")
    assert not permissions.can_convert(root, inventory, "bg")


def test_uploaded_project_cannot_supply_its_own_type_grant(tmp_path, monkeypatch):
    from figma_to_fgui import hifi_type_permissions as permissions
    from figma_to_fgui.hifi_nested import component_target

    root, tree, _, _ = nested_case(tmp_path)
    path = "assets/MyVillage/Common/Button_Common.xml"
    inspected = inspect_component(root, component_target(root, tree.target, path))
    forged = root / "graph-grants.json"
    forged.write_text(json.dumps({"version": 1, "graph_to_image": [{
        "component_relative_path": path,
        "object_id": "bg",
        "component_sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
        "project_fingerprint": inspected.target.project_fingerprint,
    }]}), encoding="utf-8")
    monkeypatch.setenv("HIFI_TYPE_CONVERSION_GRANTS_FILE", str(forged))
    assert not permissions.can_convert(root, inspected, "bg")


def test_nested_authorized_conversion_cannot_override_display_type_invariant(
    tmp_path, monkeypatch
):
    from PIL import Image

    from figma_to_fgui.figma_selection import SelectionResource
    from figma_to_fgui.hifi_patch import HifiPatchError, build_hifi_change_bundle

    root, inventory, source, mapping = nested_case(tmp_path)
    path = "assets/MyVillage/Common/Button_Common.xml"
    grants = tmp_path / "graph-grants.json"
    grants.write_text(json.dumps({"version": 1, "graph_to_image": [{
        "component_relative_path": path,
        "object_id": "bg",
        "component_sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
        "project_fingerprint": inventory.target.project_fingerprint,
    }]}), encoding="utf-8")
    monkeypatch.setenv("HIFI_TYPE_CONVERSION_GRANTS_FILE", str(grants))
    assets = tmp_path / "resources"
    assets.mkdir()
    Image.new("RGBA", (120, 44), (10, 20, 30, 255)).save(assets / "body", format="PNG")
    frame = source.top_level_nodes[0]
    source = source.model_copy(
        update={
            "top_level_nodes": (
                frame.model_copy(
                    update={
                        "children": tuple(
                            n.model_copy(update={"resource_keys": ("body",)})
                            for n in frame.children
                        )
                    }
                ),
            ),
            "resources": (
                SelectionResource(
                    key="body", mime_type="image/png", size=(assets / "body").stat().st_size
                ),
            ),
        }
    )
    with pytest.raises(HifiPatchError, match="hifi_display_type_change_forbidden:bg"):
        build_hifi_change_bundle(root, inventory, source, mapping, selection_root=tmp_path)


def test_proven_psd_shape_cannot_change_graph_tag_type(tmp_path, monkeypatch):
    from PIL import Image

    from figma_to_fgui.figma_selection import SelectionResource
    from figma_to_fgui.hifi_patch import HifiPatchError, build_hifi_change_bundle

    monkeypatch.delenv("HIFI_TYPE_CONVERSION_GRANTS_FILE", raising=False)
    root, inventory, source, mapping = nested_case(tmp_path)
    root_component = root / inventory.target.component_relative_path
    root_component.write_text(root_component.read_text().replace(
        '<component id="b" name="Reward" src="sharedbtn1" xy="200,100"/>', ""))
    from figma_to_fgui.hifi_nested import inspect_component_tree

    inventory = inspect_component_tree(root, inventory.target)
    mapping = mapping.model_copy(update={"items": tuple(
        item for item in mapping.items if not (item.old_object_id or "").startswith("b"))})
    assets = tmp_path / "resources"
    assets.mkdir()
    Image.new("RGBA", (120, 44), (10, 20, 30, 255)).save(assets / "body", format="PNG")
    frame = source.top_level_nodes[0]
    first, second = frame.children
    first = first.model_copy(update={
        "resource_keys": ("body",),
        "properties": {"psdKind": "shape", "blendMode": "normal",
                       "hasVectorMask": True, "hasEffects": False},
    })
    source = source.model_copy(update={
        "top_level_nodes": (frame.model_copy(update={"children": (first, second)}),),
        "resources": (SelectionResource(key="body", mime_type="image/png",
                                        size=(assets / "body").stat().st_size),),
    })
    mapping = mapping.model_copy(update={"items": tuple(
        item.model_copy(update={"graph_conversion_proven": True})
        if item.old_object_id == "a:bg" else
        item.model_copy(update={"action": "keep_old"})
        if item.old_object_id == "b:bg" else item
        for item in mapping.items
    )})
    with pytest.raises(HifiPatchError, match="hifi_display_type_change_forbidden:bg"):
        build_hifi_change_bundle(root, inventory, source, mapping, selection_root=tmp_path)
