from __future__ import annotations

import shutil
import uuid
from hashlib import sha256
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image

from figma_to_fgui.apply import apply_bundle
from figma_to_fgui.figma_selection import SelectionManifest, SelectionNode
from figma_to_fgui.fixed_fonts import FixedFontSpec
from figma_to_fgui.hifi_mapping import apply_mapping_decision, build_mapping
from figma_to_fgui.hifi_patch import (
    HifiPatchError,
    _behavior_occlusions,
    _protected_object,
    build_hifi_change_bundle,
    validate_hifi_candidate,
)
from figma_to_fgui.hifi_project_inspector import (
    inspect_component,
    inspect_hifi_targets,
    target_from_option,
)
from figma_to_fgui.hifi_replacement_models import HifiMappingDecision
from figma_to_fgui.models import Bounds
from figma_to_fgui.uploaded_project import index_uploaded_project

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement"


def _confirmed():
    root = FIXTURE / "old_project"
    project = index_uploaded_project(root, "old.zip")
    tree = inspect_hifi_targets(root, project)
    package = tree.packages[0]
    directory = next(item for item in package.directories if item.path == "Panel")
    component = next(item for item in directory.components if item.resource_id == "sketch01")
    inventory = inspect_component(root, target_from_option(project, package, directory, component))
    manifest = SelectionManifest.model_validate_json((FIXTURE / "hifi-selection.json").read_text("utf-8"))
    frame = manifest.top_level_nodes[0]
    manifest = manifest.model_copy(update={"top_level_nodes": (frame.model_copy(update={"children": tuple(
        node.model_copy(update={"properties": {**node.properties, "fguiGraph": {
            "shape": "rect", "fillColor": "#ff445566", "lineSize": 0}}})
        if node.id in {"hifi-silhouette", "ambiguous-b"} else node for node in frame.children)}),)})
    mapping = build_mapping(inventory, manifest)
    for item in tuple(mapping.items):
        current = next(current for current in mapping.items if current.item_id == item.item_id)
        if current.action is None:
            if current.status in {"uncertain", "suggested"}:
                action = "retarget"
            elif current.status == "hifi_added" and current.figma_node_id == "progress-bubble":
                action = "add_visual"
            elif current.status in {"hifi_added", "blocked"}:
                action = "exception"
            else:
                action = "keep_old"
            mapping = apply_mapping_decision(
                mapping,
                HifiMappingDecision(
                    version=1,
                    mapping_revision=mapping.mapping_revision,
                    item_id=current.item_id,
                    action=action,
                    figma_node_id=current.candidates[0] if action == "retarget" else None,
                ),
                manifest,
            )
    return root, inventory, manifest, mapping


def _without_unowned_additions(mapping):
    return mapping.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"action": "exception"})
                if item.action == "add_visual"
                else item
                for item in mapping.items
            )
        }
    )


def test_incomplete_parse_never_produces_approvable_review() -> None:
    root, inventory, _, mapping = _confirmed()
    review = validate_hifi_candidate(root, root,
        inventory.model_copy(update={"parse_complete": False}),
        _without_unowned_additions(mapping), session_id="0" * 32)
    assert not review.approvable


def test_modified_controller_target_needs_state_evidence(tmp_path: Path) -> None:
    root, inventory, manifest, mapping = _confirmed()
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    bundle = build_hifi_change_bundle(candidate, inventory, manifest,
        _without_unowned_additions(mapping), selection_root=FIXTURE / "selection")
    apply_bundle(candidate, bundle)
    review = validate_hifi_candidate(root, candidate,
        inventory.model_copy(update={"parse_complete": True}),
        _without_unowned_additions(mapping), session_id="0" * 32)
    # State evidence stays a warning for the Editor stage instead of blocking.
    assert review.approvable
    assert any("状态" in warning for warning in review.warnings)


@pytest.mark.parametrize("mutation", ["extra", "reorder", "duplicate", "type"])
def test_candidate_rejects_unplanned_display_list_changes(tmp_path: Path, mutation: str) -> None:
    root, inventory, _, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    path = candidate / inventory.target.component_relative_path
    doc = etree.parse(str(path))
    display = doc.getroot().find("displayList")
    assert display is not None
    if mutation == "extra":
        etree.SubElement(display, "image", id="not_a_hifi_prefix", name="overlay", xy="0,0", size="750,420")
    elif mutation == "duplicate":
        import copy
        display.append(copy.deepcopy(display[0]))
    elif mutation == "type":
        display[0].tag = "loader"
    else:
        display.append(display[0])
    doc.write(str(path), encoding="utf-8", xml_declaration=True)
    with pytest.raises(HifiPatchError, match="hifi_display_list_changed"):
        validate_hifi_candidate(root, candidate, inventory, mapping, session_id="test")


def test_build_rejects_duplicate_psd_pixel_ownership() -> None:
    root, inventory, manifest, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    accepted = [item for item in mapping.items if item.action in {"accept", "retarget"}]
    assert len(accepted) >= 2
    duplicated = {accepted[0].item_id, accepted[1].item_id}
    mapping = mapping.model_copy(update={"items": tuple(
        item.model_copy(update={"owned_source_ids": ("same-visible-leaf",)})
        if item.item_id in duplicated else item
        for item in mapping.items
    )})

    with pytest.raises(HifiPatchError, match="duplicate_psd_pixel_ownership"):
        build_hifi_change_bundle(
            root,
            inventory,
            manifest,
            mapping,
            selection_root=FIXTURE / "selection",
        )


def test_build_rejects_visual_echo_even_without_a_new_display_object() -> None:
    root, inventory, manifest, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    owner = next(item for item in mapping.items if item.action in {"accept", "retarget"})
    mapping = mapping.model_copy(update={"items": tuple(
        item.model_copy(update={"visual_echo": True}) if item.item_id == owner.item_id else item
        for item in mapping.items
    )})

    with pytest.raises(HifiPatchError, match="hifi_visual_echo_forbidden"):
        build_hifi_change_bundle(
            root,
            inventory,
            manifest,
            mapping,
            selection_root=FIXTURE / "selection",
        )


def test_psd_patch_does_not_reorder_existing_program_objects(tmp_path: Path) -> None:
    root, inventory, manifest, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    source_root = manifest.top_level_nodes[0]
    manifest = manifest.model_copy(update={"top_level_nodes": (source_root.model_copy(update={
        "id": "psd-root:test",
        "children": tuple(node.model_copy(update={"properties": {**node.properties, "psdDocumentIndex": 100 - index}})
                          for index, node in enumerate(source_root.children)),
    }),)})
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    bundle = build_hifi_change_bundle(candidate, inventory, manifest, mapping,
                                     selection_root=FIXTURE / "selection")
    apply_bundle(candidate, bundle)
    before = etree.parse(str(root / inventory.target.component_relative_path)).xpath("./displayList/*/@id")
    after = etree.parse(str(candidate / inventory.target.component_relative_path)).xpath("./displayList/*/@id")
    assert after == before


def test_loader_raster_is_replaced_in_place_preserving_identity_and_relations(tmp_path: Path) -> None:
    root, inventory, manifest, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    path = candidate / inventory.target.component_relative_path
    doc = etree.parse(str(path))
    board = doc.xpath("./displayList/image[@id='board_bg']")[0]
    board.tag = "loader"
    board.attrib.pop("src")
    board.attrib.pop("fileName")
    board.set("url", "ui://old-package-old-image")
    board.set("fill", "scale")
    etree.SubElement(board, "relation", target="", sidePair="width-width")
    doc.write(str(path), encoding="utf-8", xml_declaration=True)
    inventory = inspect_component(candidate, inventory.target)
    before_ids = doc.xpath("./displayList/*/@id")
    bundle = build_hifi_change_bundle(candidate, inventory, manifest, mapping,
                                     selection_root=FIXTURE / "selection")
    apply_bundle(candidate, bundle)
    after = etree.parse(str(path))
    loader = after.xpath("./displayList/loader[@id='board_bg']")[0]
    package = etree.parse(str(candidate / "assets/MyVillage/package.xml"))
    added = package.xpath("./resources/image[starts-with(@id,'h')]")[0]
    assert loader.attrib["url"] == "ui://" + package.getroot().attrib["id"] + added.attrib["id"]
    assert loader.attrib["fill"] == "scaleFree"
    assert loader.xpath("./relation[@target=''][@sidePair='width-width']")
    assert after.xpath("./displayList/*/@id") == before_ids


def test_psd_group_cannot_be_accepted_as_geometry_only_component_replacement() -> None:
    root, inventory, manifest, mapping = _confirmed()
    source_root = manifest.top_level_nodes[0]
    child = source_root.children[0]
    source_root = source_root.model_copy(update={
        "id": "psd-root:test",
        "children": tuple(node.model_copy(update={"type": "GROUP", "children": (child,)})
                          if node.id == "hifi-next" else node for node in source_root.children),
    })
    manifest = manifest.model_copy(update={"top_level_nodes": (source_root,)})
    with pytest.raises(HifiPatchError, match="hifi_nested_visual_mapping_required"):
        build_hifi_change_bundle(root, inventory, manifest, _without_unowned_additions(mapping),
                                 selection_root=FIXTURE / "selection")


def test_patch_changes_visuals_without_rebuilding_or_deleting_old_objects(tmp_path: Path) -> None:
    root, inventory, manifest, mapping = _confirmed()
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        manifest,
        mapping,
        job_id=uuid.uuid4().hex,
        selection_root=FIXTURE / "selection",
    )
    apply_bundle(candidate, bundle)
    relative = inventory.target.component_relative_path
    before = etree.parse(str(root / relative))
    after = etree.parse(str(candidate / relative))
    before_ids = {str(node.attrib["id"]) for node in before.xpath("./displayList/*[@id]")}
    after_ids = {str(node.attrib["id"]) for node in after.xpath("./displayList/*[@id]")}
    assert before_ids <= after_ids
    added_visuals = after.xpath("./displayList/*[starts-with(@id, 'hifi_')]")
    assert added_visuals
    assert all(item.attrib.get("touchable") == "false" for item in added_visuals)
    reset_before = before.xpath("./displayList/*[@id='btn_reset']")[0]
    reset_after = after.xpath("./displayList/*[@id='btn_reset']")[0]
    assert etree.tostring(reset_before) == etree.tostring(reset_after)
    review = validate_hifi_candidate(
        root, candidate, inventory, mapping, session_id=uuid.uuid4().hex
    )
    assert review.protected_checks_passed is True
    assert {item.kind for item in review.object_diffs} >= {"changed", "added", "kept"}
    assert review.approvable is False  # Fixture intentionally contains an unknown tag.
    changed_paths = {item.relative_path for item in review.changed_files}
    assert relative in changed_paths
    assert "assets/MyVillage/package.xml" in changed_paths
    assert any(path.startswith("assets/MyVillage/Img/HIFI/") for path in changed_paths)
    assert review.editor_check_required is True


def test_patch_serializes_all_object_geometry_as_editor_int32_pairs(tmp_path: Path) -> None:
    root, inventory, manifest, mapping = _confirmed()
    candidate = tmp_path / "candidate-integer-geometry"
    shutil.copytree(root, candidate)
    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        manifest,
        mapping,
        job_id=uuid.uuid4().hex,
        selection_root=FIXTURE / "selection",
    )
    apply_bundle(candidate, bundle)

    component = etree.parse(str(candidate / inventory.target.component_relative_path))
    for element in component.xpath("./displayList/*[@xy or @size]"):
        for attribute in ("xy", "size"):
            value = element.attrib.get(attribute)
            if value is not None:
                assert all(part.lstrip("-").isdigit() for part in value.split(",")), (
                    element.attrib.get("id"),
                    attribute,
                    value,
                )


def test_psd_patch_preserves_full_document_coordinates_without_resizing_fgui_viewport(
    tmp_path: Path,
) -> None:
    root, inventory, manifest, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    source_root = manifest.top_level_nodes[0]
    source_children = tuple(
        child.model_copy(
            update={"bounds": Bounds(x=0, y=500, width=900, height=500)}
        )
        if child.id == "hifi-board"
        else child
        for child in source_root.children
    )
    psd_manifest = manifest.model_copy(
        update={
            "top_level_nodes": (
                source_root.model_copy(
                    update={
                        "id": "psd-root:" + "a" * 64,
                        "bounds": Bounds(x=0, y=0, width=1100, height=2100),
                        "children": source_children,
                    }
                ),
            )
        }
    )
    candidate = tmp_path / "candidate-psd-canvas"
    shutil.copytree(root, candidate)
    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        psd_manifest,
        mapping,
        job_id=uuid.uuid4().hex,
        selection_root=FIXTURE / "selection",
    )
    apply_bundle(candidate, bundle)

    component = etree.parse(str(candidate / inventory.target.component_relative_path))
    assert component.getroot().attrib["size"] == "750,420"
    assert component.xpath("./displayList/*[@id='board_bg']")[0].attrib["size"] == "900,500"
    assert component.xpath("./displayList/*[@id='board_bg']")[0].attrib["xy"] == "0,500"
    assert component.xpath("./displayList/*[@id='title_bar']")[0].attrib["xy"] == "48,30"
    review = validate_hifi_candidate(
        root, candidate, inventory, mapping, session_id=uuid.uuid4().hex
    )
    assert review.protected_checks_passed is True


def test_psd_reference_is_never_embedded_as_a_full_component_overlay(
    tmp_path: Path,
) -> None:
    root, inventory, manifest, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    source_root = manifest.top_level_nodes[0]
    psd_manifest = manifest.model_copy(
        update={
            "top_level_nodes": (
                source_root.model_copy(
                    update={
                        "id": "psd-root:" + "a" * 64,
                        "bounds": Bounds(x=0, y=0, width=750, height=420),
                    }
                ),
            )
        }
    )
    parity_reference = tmp_path / "viewport.png"
    Image.new("RGB", (750, 420), (17, 34, 51)).save(parity_reference)
    candidate = tmp_path / "candidate-parity"
    shutil.copytree(root, candidate)

    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        psd_manifest,
        mapping,
        job_id=uuid.uuid4().hex,
        selection_root=FIXTURE / "selection",
        parity_reference=parity_reference,
    )
    apply_bundle(candidate, bundle)

    component = etree.parse(str(candidate / inventory.target.component_relative_path))
    assert not component.xpath("./displayList/image[starts-with(@id, 'hifi_psd_default_')]")
    package = etree.parse(str(candidate / "assets/MyVillage/package.xml"))
    assert not package.xpath("./resources/image[starts-with(@name, 'PSD_Default_')]")

    review = validate_hifi_candidate(
        root, candidate, inventory, mapping, session_id=uuid.uuid4().hex
    )
    assert all("PSD 默认参考图" not in warning for warning in review.warnings)


def test_psd_candidate_rejects_additions_when_no_old_object_is_mapped(
    tmp_path: Path,
) -> None:
    root, inventory, manifest, mapping = _confirmed()
    source_root = manifest.top_level_nodes[0]
    psd_manifest = manifest.model_copy(
        update={
            "top_level_nodes": (
                source_root.model_copy(update={"id": "psd-root:" + "b" * 64}),
            )
        }
    )
    unmapped = mapping.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"action": "keep_old"})
                if item.old_object_id is not None
                else item.model_copy(update={"action": "exception"})
                for item in mapping.items
            )
        }
    )

    with pytest.raises(HifiPatchError, match="hifi_mapping_requires_replacements"):
        build_hifi_change_bundle(
            root,
            inventory,
            psd_manifest,
            unmapped,
            job_id=uuid.uuid4().hex,
            selection_root=FIXTURE / "selection",
        )


def test_psd_candidate_rejects_unowned_new_root_visuals(tmp_path: Path) -> None:
    root, inventory, manifest, mapping = _confirmed()
    source_root = manifest.top_level_nodes[0]
    psd_manifest = manifest.model_copy(
        update={
            "top_level_nodes": (
                source_root.model_copy(update={"id": "psd-root:" + "c" * 64}),
            )
        }
    )

    with pytest.raises(HifiPatchError, match="hifi_psd_visual_requires_existing_object"):
        build_hifi_change_bundle(
            root,
            inventory,
            psd_manifest,
            mapping,
            job_id=uuid.uuid4().hex,
            selection_root=FIXTURE / "selection",
        )


def test_mapped_graph_rejects_raster_skin_instead_of_adding_overlay(
    tmp_path: Path,
) -> None:
    root, inventory, manifest, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    source_root = manifest.top_level_nodes[0]
    skinned_children = tuple(
        child.model_copy(
            update={
                "resource_keys": ("hifi-board",),
                "properties": {
                    **child.properties,
                    "psdDocumentIndex": 10,
                },
            }
        )
        if child.id == "hifi-silhouette"
        else child
        for child in source_root.children
    )
    psd_manifest = manifest.model_copy(
        update={
            "top_level_nodes": (
                source_root.model_copy(
                    update={
                        "id": "psd-root:" + "a" * 64,
                        "children": skinned_children,
                    }
                ),
            )
        }
    )
    candidate = tmp_path / "candidate-graph-skin"
    shutil.copytree(root, candidate)

    before = (candidate / inventory.target.component_relative_path).read_bytes()
    with pytest.raises(HifiPatchError, match="hifi_display_type_change_forbidden"):
        build_hifi_change_bundle(
            candidate, inventory, psd_manifest, mapping,
            selection_root=FIXTURE / "selection",
        )
    assert (candidate / inventory.target.component_relative_path).read_bytes() == before


def test_one_pixel_viewport_artifact_does_not_replace_existing_graph(tmp_path: Path) -> None:
    root, inventory, manifest, mapping = _confirmed()
    mapping = _without_unowned_additions(mapping)
    source_root = manifest.top_level_nodes[0]
    artifact_children = tuple(
        child.model_copy(update={
            "bounds": Bounds(x=0, y=0, width=1, height=1),
            "resource_keys": ("hifi-board",),
        })
        if child.id == "hifi-silhouette" else child
        for child in source_root.children
    )
    psd_manifest = manifest.model_copy(update={"top_level_nodes": (
        source_root.model_copy(update={
            "id": "psd-root:" + "d" * 64,
            "children": artifact_children,
        }),
    )})
    candidate = tmp_path / "candidate-one-pixel-artifact"
    shutil.copytree(root, candidate)
    before = etree.parse(str(candidate / inventory.target.component_relative_path))
    before_graph = etree.tostring(before.xpath("./displayList/graph[@id='silhouette_01']")[0])

    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        psd_manifest,
        mapping,
        selection_root=FIXTURE / "selection",
    )
    apply_bundle(candidate, bundle)

    after = etree.parse(str(candidate / inventory.target.component_relative_path))
    assert etree.tostring(after.xpath("./displayList/graph[@id='silhouette_01']")[0]) == before_graph


def test_patch_registers_uploaded_hifi_image_and_retargets_private_image(tmp_path: Path) -> None:
    root, inventory, manifest, mapping = _confirmed()
    selection_root = FIXTURE / "selection"
    png = (selection_root / "resources/hifi-board").read_bytes()
    candidate = tmp_path / "candidate-with-image"
    shutil.copytree(root, candidate)
    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        manifest,
        mapping,
        job_id=uuid.uuid4().hex,
        selection_root=selection_root,
    )
    apply_bundle(candidate, bundle)
    component = etree.parse(str(candidate / inventory.target.component_relative_path))
    board_after = component.xpath("./displayList/*[@id='board_bg']")[0]
    assert str(board_after.attrib["src"]).startswith("h")
    package = etree.parse(str(candidate / "assets/MyVillage/package.xml"))
    resource = package.xpath(f"./resources/image[@id='{board_after.attrib['src']}']")
    assert len(resource) == 1
    assert resource[0].attrib["atlas"] == "0"
    assert resource[0].attrib["exported"] == "true"
    created = candidate / "assets/MyVillage" / resource[0].attrib["path"].strip("/") / resource[0].attrib["name"]
    assert created.read_bytes() == png
    review = validate_hifi_candidate(
        root, candidate, inventory, mapping, session_id=uuid.uuid4().hex
    )
    assert any(item.operation == "create" for item in review.changed_files)


def test_patch_writes_exact_psd_text_style_with_project_font_resource(
    tmp_path: Path, monkeypatch
) -> None:
    root, inventory, manifest, mapping = _confirmed()
    candidate = tmp_path / "candidate-with-text-style"
    shutil.copytree(root, candidate)

    font_bytes = b"exact-project-font"
    font_root = candidate / "assets/Fonts"
    (font_root / "Font").mkdir(parents=True)
    (font_root / "Font/Core.ttf").write_bytes(font_bytes)
    (font_root / "package.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<packageDescription id="fontpkg1"><resources>'
        '<font id="core1" name="Core.ttf" path="/Font/"/>'
        '</resources></packageDescription>',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "figma_to_fgui.hifi_patch.PROJECT_FIXED_FONTS",
        (
            FixedFontSpec(
                family="CoreSansESW01-55Medium",
                postscript_name="CoreSansESW01-55Medium",
                source_filename="core.ttf",
                sha256=sha256(font_bytes).hexdigest(),
            ),
        ),
    )

    root_node = manifest.top_level_nodes[0]
    styled_children = tuple(
        child.model_copy(
            update={
                "style": {
                    "psdTextStyle": {
                        "runs": ({
                            "start": 0,
                            "length": 14,
                            "font_name": "CoreSansESW01-55Medium",
                            "font_size": 40.0,
                            "faux_bold": True,
                            "faux_italic": False,
                            "leading": 44.0,
                            "tracking": 50.0,
                            "fill_rgba": (0.1, 0.2, 0.3, 1.0),
                        },),
                        "transform": (1.1, 0.0, 0.0, 1.1, 0.0, 0.0),
                        "paragraph_justification": 2,
                        "anti_alias": 4,
                    }
                },
                "properties": {
                    "psdEffects": ({
                        "kind": "ColorOverlay",
                        "enabled": True,
                        "blend_mode": "normal",
                        "opacity": 100.0,
                        "color_rgba": (1.0, 0.8, 0.2, 1.0),
                        "size": None,
                        "angle": None,
                        "distance": None,
                        "spread": None,
                        "choke": None,
                        "position": None,
                    }, {
                        "kind": "Stroke",
                        "enabled": True,
                        "blend_mode": "normal",
                        "opacity": 100.0,
                        "color_rgba": (0.2, 0.3, 0.4, 1.0),
                        "size": 3.0,
                        "angle": None,
                        "distance": None,
                        "spread": None,
                        "choke": None,
                        "position": "outside",
                    })
                },
            }
        ) if child.id == "hifi-title" else child
        for child in root_node.children
    )
    styled_manifest = manifest.model_copy(
        update={"top_level_nodes": (root_node.model_copy(update={"children": styled_children}),)}
    )

    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        styled_manifest,
        mapping,
        job_id=uuid.uuid4().hex,
        selection_root=FIXTURE / "selection",
    )
    apply_bundle(candidate, bundle)
    component = etree.parse(str(candidate / inventory.target.component_relative_path))
    title = component.xpath("./displayList/text[@id='title_bar']")[0]
    assert title.attrib["font"] == "ui://fontpkg1core1"
    assert title.attrib["fontSize"] == "44"
    assert title.attrib["color"] == "#ffcc33"
    assert title.attrib["align"] == "center"
    assert title.attrib["bold"] == "true"
    assert title.attrib["leading"] == "4.4"
    assert title.attrib["letterSpacing"] == "2.2"
    assert title.attrib["strokeColor"] == "#334c66"
    assert title.attrib["strokeSize"] == "1.5"


def test_psd_text_style_updates_only_the_runtime_default_gear_color() -> None:
    from figma_to_fgui.hifi_patch import _apply_psd_text_style

    component = etree.fromstring(
        b'<component><controller name="titleColor" '
        b'pages="0,normal,1,current,2,disabled" selected="1"/>'
        b'<displayList><text id="title" color="#111111" strokeColor="#222222">'
        b'<gearColor controller="titleColor" pages="1,2" '
        b'values="#cccccc,#dddddd|#eeeeee,#ffffff" '
        b'default="#123456,#654321"/>'
        b'</text></displayList></component>'
    )
    element = component.xpath("./displayList/text")[0]
    node = SelectionNode(
        id="psd-title",
        name="Rank",
        type="TEXT",
        text="Rank",
        bounds=Bounds(x=0, y=0, width=76, height=26),
        style={
            "psdTextStyle": {
                "runs": [{
                    "font_name": "CoreSansESW01-55Medium",
                    "font_size": 40.0,
                    "faux_bold": True,
                    "faux_italic": False,
                    "leading": 40.0,
                    "tracking": 0.0,
                    "fill_rgba": (1.0, 1.0, 1.0, 1.0),
                }],
                "transform": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
                "paragraph_justification": 0,
            }
        },
        properties={
            "psdEffects": ({
                "kind": "ColorOverlay",
                "enabled": True,
                "blend_mode": "normal",
                "opacity": 100.0,
                "color_rgba": (1.0, 0.8, 0.2, 1.0),
            }, {
                "kind": "Stroke",
                "enabled": True,
                "blend_mode": "normal",
                "opacity": 50.0,
                "color_rgba": (0.2, 0.3, 0.4, 1.0),
                "size": 2.0,
                "position": "outside",
            }),
        },
    )

    _apply_psd_text_style(element, node, {})

    gear = element.find("gearColor")
    assert gear is not None
    # FairyGUI initializes an instantiated component on controller page zero.
    # This gear has no explicit page-zero value, so the rendered root screen
    # uses ``default`` even though the source editor stored ``selected=1``.
    # Keep every explicit (inactive) page untouched and replace only the
    # runtime-default visual state represented by the PSD.
    assert gear.get("values") == (
        "#cccccc,#dddddd|#eeeeee,#ffffff"
    )
    assert gear.get("default") == "#ffcc33,#80334c66"


def test_text_visual_attributes_do_not_trip_structure_protection() -> None:
    before = etree.fromstring(
        b'<text id="title" name="Title" group="layout" text="Old" fontSize="30"/>'
    )
    after = etree.fromstring(
        b'<text id="title" name="Title" group="layout" text="New" fontSize="44" '
        b'font="ui://fontpkg1core1" color="#ffcc33" align="center" bold="true" '
        b'italic="true" leading="4.4" letterSpacing="2.2" '
        b'strokeColor="#334c66" strokeSize="3"/>'
    )

    assert _protected_object(before) == _protected_object(after)

    component_before = etree.fromstring(
        b'<component id="button" name="Button" src="component-a" xy="0,0"/>'
    )
    component_after = etree.fromstring(
        b'<component id="button" name="Button" src="component-b" xy="10,20"/>'
    )
    assert _protected_object(component_before) != _protected_object(component_after)


def test_psd_zero_tracking_is_not_replaced_with_synthetic_condensation() -> None:
    from figma_to_fgui.hifi_patch import _apply_psd_text_style

    element = etree.fromstring(
        b'<text id="title" name="title" text="Noble Reception" fontSize="30"/>'
    )
    node = SelectionNode(
        id="psd-title",
        name="Noble Reception",
        type="TEXT",
        text="Noble Reception",
        bounds=Bounds(x=67, y=967, width=285, height=36),
        style={
            "psdTextStyle": {
                "runs": [{
                    "font_name": "CoreSansESW01-55Medium",
                    "font_size": 93.75124,
                    "faux_bold": True,
                    "faux_italic": False,
                    "leading": 52.16326,
                    "tracking": 0.0,
                    "fill_rgba": (1.0, 1.0, 1.0, 1.0),
                }],
                "transform": (0.405328, 0.0, 0.0, 0.405328, 0.0, 0.0),
                "paragraph_justification": 0,
            }
        },
    )

    _apply_psd_text_style(element, node, {})

    assert element.attrib["letterSpacing"] == "0"


def test_psd_text_style_clears_legacy_style_not_present_in_psd() -> None:
    from figma_to_fgui.hifi_patch import _apply_psd_text_style

    element = etree.fromstring(
        b'<text id="value" text="2000" bold="true" italic="true" '
        b'strokeColor="#ffffff" strokeSize="2" letterSpacing="4" faceDilate=".22"/>'
    )
    node = SelectionNode(
        id="psd-value",
        name="2000",
        type="TEXT",
        text="2000",
        bounds=Bounds(x=307, y=1042, width=73, height=31),
        style={
            "psdTextStyle": {
                "runs": [{
                    "font_name": "CoreSansESW01-55Medium",
                    "font_size": 30.90909,
                    "faux_bold": False,
                    "faux_italic": False,
                    "leading": 44.0,
                    "tracking": 0.0,
                    "fill_rgba": (0.1, 0.2, 0.3, 1.0),
                }],
                "transform": (1.1, 0.0, 0.0, 1.1, 0.0, 0.0),
                "paragraph_justification": 0,
            }
        },
        properties={
            "psdEffects": ({
                "kind": "Stroke",
                "enabled": False,
                "blend_mode": "normal",
                "opacity": 100.0,
                "color_rgba": (1.0, 1.0, 1.0, 1.0),
                "size": 2.0,
                "position": "outside",
            },),
        },
    )

    _apply_psd_text_style(element, node, {})

    assert element.get("bold") is None
    assert element.get("italic") is None
    assert element.get("strokeColor") is None
    assert element.get("strokeSize") is None
    assert element.get("faceDilate") is None
    assert element.get("letterSpacing") == "0"
    assert element.get("fontSize") == "34"


def test_psd_raster_replacement_clears_old_pixel_modifiers_and_neutralizes_current_gear() -> None:
    from figma_to_fgui.hifi_patch import _prepare_psd_raster_target

    component = etree.fromstring(
        b'<component><controller name="type" pages="0,normal,1,disabled,2,pressed" '
        b'selected="1"/><displayList><image id="skin" color="#6d4b44" '
        b'align="center" vAlign="middle" autoSize="true" aspect="true" shrinkOnly="true">'
        b'<gearColor controller="type" pages="0,1,2" '
        b'values="#6d4b44,#112233,#445566" default="#778899"/>'
        b'</image></displayList></component>'
    )
    image = component.xpath("./displayList/image")[0]

    _prepare_psd_raster_target(image)

    for attribute in ("color", "align", "vAlign", "autoSize", "aspect", "shrinkOnly"):
        assert image.get(attribute) is None
    gear = image.find("gearColor")
    assert gear is not None
    assert gear.get("pages") == "0,1,2"
    assert gear.get("values") == "#ffffff,#112233,#445566"
    assert gear.get("default") == "#778899"


def test_psd_raster_replacement_neutralizes_runtime_default_gear_tint() -> None:
    from figma_to_fgui.hifi_patch import _prepare_psd_raster_target

    component = etree.fromstring(
        b'<component><controller name="type" pages="1,scene,0,panel" selected="1"/>'
        b'<displayList><image id="skin">'
        b'<gearColor controller="type" pages="0" values="#ffffff" default="#4d3630"/>'
        b'</image></displayList></component>'
    )
    image = component.xpath("./displayList/image")[0]

    _prepare_psd_raster_target(image)

    gear = image.find("gearColor")
    assert gear is not None
    assert gear.get("values") == "#ffffff"
    assert gear.get("default") == "#ffffff"


def test_psd_loader_replacement_uses_one_to_one_scale_free_geometry() -> None:
    from figma_to_fgui.hifi_patch import _prepare_psd_raster_target

    loader = etree.fromstring(
        b'<loader id="skin" fill="scale" align="center" vAlign="middle" '
        b'autoSize="true" aspect="true" shrinkOnly="true" color="#887766"/>'
    )

    _prepare_psd_raster_target(loader)

    assert loader.get("fill") == "scaleFree"
    for attribute in ("color", "align", "vAlign", "autoSize", "aspect", "shrinkOnly"):
        assert loader.get(attribute) is None


def test_psd_odd_height_root_middle_component_compensates_editor_half_pixel() -> None:
    from figma_to_fgui.hifi_patch import _set_visual

    _, inventory, _, _ = _confirmed()
    inventory = inventory.model_copy(update={"width": 1080, "height": 1920})
    root = SelectionNode(
        id="psd-root:test",
        name="root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=1080, height=1920),
    )
    node = SelectionNode(
        id="psd-merit",
        name="Merit Rewards",
        type="GROUP",
        bounds=Bounds(x=625, y=1097, width=374, height=77),
    )
    component = etree.fromstring(
        b'<component id="reward"><relation target="" '
        b'sidePair="center-center,middle-middle"/></component>'
    )

    _set_visual(component, node, root, inventory, {})

    assert component.get("xy") == "625,1098"
    assert component.get("size") == "374,77"


def test_psd_grouped_middle_component_does_not_receive_root_half_pixel_correction() -> None:
    from figma_to_fgui.hifi_patch import _set_visual

    _, inventory, _, _ = _confirmed()
    inventory = inventory.model_copy(update={"width": 1080, "height": 1920})
    root = SelectionNode(
        id="psd-root:test",
        name="root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=1080, height=1920),
    )
    node = SelectionNode(
        id="psd-noble",
        name="Noble Reception",
        type="GROUP",
        bounds=Bounds(x=23, y=734, width=374, height=77),
    )
    component = etree.fromstring(
        b'<component id="noble" group="row"><relation target="" '
        b'sidePair="center-center,middle-middle"/></component>'
    )

    _set_visual(component, node, root, inventory, {})

    assert component.get("xy") == "23,734"
    assert component.get("size") == "374,77"


def test_psd_text_origin_has_no_fixed_three_pixel_inset() -> None:
    from figma_to_fgui.hifi_patch import _set_visual

    _, inventory, _, _ = _confirmed()
    root = SelectionNode(
        id="psd-root:test",
        name="root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=750, height=420),
    )
    node = SelectionNode(
        id="psd-title",
        name="title",
        type="TEXT",
        text="Rank",
        bounds=Bounds(x=40, y=100, width=80, height=28),
        style={
            "psdTextStyle": {
                "runs": [{
                    "font_name": "CoreSansESW01-55Medium",
                    "font_size": 30.0,
                    "faux_bold": False,
                    "faux_italic": False,
                    "leading": 30.0,
                    "tracking": 0.0,
                    "fill_rgba": (1.0, 1.0, 1.0, 1.0),
                }],
                "transform": (1.0, 0.0, 0.0, 1.0, 50.0, 140.0),
                "paragraph_justification": 0,
            }
        },
        properties={"hifiPsdTextOrigin": (50.0, 140.0)},
    )
    element = etree.fromstring(b'<text id="rank" text="Old"/>')

    _set_visual(element, node, root, inventory, {})

    assert element.get("xy") == "50,110"


def test_psd_stroked_text_compensates_editor_outline_gutter() -> None:
    from figma_to_fgui.hifi_patch import _set_visual

    _, inventory, _, _ = _confirmed()
    root = SelectionNode(
        id="psd-root:test",
        name="root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=750, height=420),
    )
    node = SelectionNode(
        id="psd-title",
        name="title",
        type="TEXT",
        text="Rank",
        bounds=Bounds(x=40, y=100, width=80, height=28),
        style={
            "psdTextStyle": {
                "runs": [{
                    "font_name": "CoreSansESW01-55Medium",
                    "font_size": 30.0,
                    "faux_bold": False,
                    "faux_italic": False,
                    "leading": 30.0,
                    "tracking": 0.0,
                    "fill_rgba": (1.0, 1.0, 1.0, 1.0),
                }],
                "transform": (1.0, 0.0, 0.0, 1.0, 50.0, 140.0),
                "paragraph_justification": 0,
            }
        },
        properties={
            "hifiPsdTextOrigin": (50.0, 140.0),
            "psdEffects": ({
                "kind": "Stroke",
                "enabled": True,
                "blend_mode": "normal",
                "opacity": 100.0,
                "color_rgba": (0.2, 0.3, 0.4, 1.0),
                "size": 2.0,
                "position": "outside",
            },),
        },
    )
    element = etree.fromstring(b'<text id="rank" text="Old"/>')

    _set_visual(element, node, root, inventory, {})

    assert element.get("xy") == "49,109"


def test_psd_anchored_title_compensates_editor_pivot_gutter() -> None:
    from figma_to_fgui.hifi_patch import _set_visual

    _, inventory, _, _ = _confirmed()
    root = SelectionNode(
        id="psd-root:test",
        name="root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=750, height=420),
    )
    node = SelectionNode(
        id="psd-title",
        name="title",
        type="TEXT",
        text="Noble",
        bounds=Bounds(x=40, y=100, width=80, height=28),
        style={
            "psdTextStyle": {
                "runs": [{
                    "font_name": "CoreSansESW01-55Medium",
                    "font_size": 30.0,
                    "faux_bold": True,
                    "faux_italic": False,
                    "leading": 30.0,
                    "tracking": 0.0,
                    "fill_rgba": (1.0, 1.0, 1.0, 1.0),
                }],
                "transform": (1.0, 0.0, 0.0, 1.0, 50.0, 140.0),
                "paragraph_justification": 0,
            }
        },
        properties={"hifiPsdTextOrigin": (50.0, 140.0)},
    )
    element = etree.fromstring(
        b'<text id="title" pivot="0.5,0.5" anchor="true" text="Old"/>'
    )

    _set_visual(element, node, root, inventory, {})

    assert element.get("xy") == "91,123"


def test_psd_text_line_box_uses_font_relative_room_for_last_bold_glyph() -> None:
    from figma_to_fgui.hifi_patch import _set_visual

    _, inventory, _, _ = _confirmed()
    inventory = inventory.model_copy(update={"width": 1080, "height": 1920})
    root = SelectionNode(
        id="psd-root:test",
        name="root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=1080, height=1920),
    )
    node = SelectionNode(
        id="psd-elf-rank",
        name="Elf Queen lv.1 ",
        type="TEXT",
        text="Elf Queen lv.1 ",
        bounds=Bounds(x=788, y=1704, width=212, height=26),
        style={
            "psdTextStyle": {
                "runs": [{
                    "font_name": "CoreSansESW01-55Medium",
                    "font_size": 69.38776,
                    "faux_bold": True,
                    "faux_italic": False,
                    "leading": 52.16326,
                    "tracking": 0.0,
                    "fill_rgba": (1.0, 1.0, 1.0, 1.0),
                }],
                "transform": (0.49, 0.0, 0.0, 0.49, 786.105, 1728.675),
                "paragraph_justification": 0,
            }
        },
        properties={"hifiPsdTextOrigin": (786.105, 1728.675)},
    )
    component = etree.fromstring(
        b'<component><displayList>'
        b'<text id="rank" group="row" vAlign="middle" text="Old"/>'
        b'<group id="row" layout="hz"/>'
        b'</displayList></component>'
    )
    element = component.xpath("./displayList/text")[0]

    _set_visual(element, node, root, inventory, {})

    assert element.get("xy") == "783,1691"
    assert element.get("size") == "234,39"
    assert element.get("vAlign") == "top"
    assert element.get("singleLine") == "true"


def test_psd_text_first_in_horizontal_group_compensates_smooth_font_bearing() -> None:
    from figma_to_fgui.hifi_patch import _set_visual

    _, inventory, _, _ = _confirmed()
    inventory = inventory.model_copy(update={"width": 1080, "height": 1920})
    root = SelectionNode(
        id="psd-root:test",
        name="root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=1080, height=1920),
    )
    node = SelectionNode(
        id="psd-available",
        name="Available Today",
        type="TEXT",
        text="Available Today",
        bounds=Bounds(x=37, y=832, width=235, height=31),
        style={
            "psdTextStyle": {
                "runs": [{
                    "font_name": "CoreSansESW01-55Medium",
                    "font_size": 30.90909,
                    "faux_bold": False,
                    "faux_italic": False,
                    "leading": 41.01563,
                    "tracking": 0.0,
                    "fill_rgba": (1.0, 1.0, 1.0, 1.0),
                }],
                "transform": (1.1, 0.0, 0.0, 1.1, 36.95, 856.28),
                "paragraph_justification": 0,
            }
        },
        properties={"hifiPsdTextOrigin": (36.95, 856.28)},
    )
    component = etree.fromstring(
        b'<component><displayList>'
        b'<text id="title" group="row" text="Old"/>'
        b'<loader id="icon" group="row"/>'
        b'<group id="row" xy="37,819" layout="hz"/>'
        b'</displayList></component>'
    )
    element = component.xpath("./displayList/text")[0]

    _set_visual(element, node, root, inventory, {})

    assert component.xpath("./displayList/group")[0].get("xy") == "35,819"


def test_behavior_audit_detects_new_skin_above_controller_driven_object() -> None:
    _, inventory, _, _ = _confirmed()
    document = etree.parse(
        str(FIXTURE / "old_project" / inventory.target.component_relative_path)
    )
    display_list = document.getroot().find("displayList")
    assert display_list is not None
    display_list.append(
        etree.Element(
            "image",
            id="hifi_cover",
            name="HifiCover",
            xy="50,100",
            size="210,250",
            touchable="false",
        )
    )

    assert _behavior_occlusions(document, inventory) == ("silhouette_01",)


def test_candidate_validation_rejects_existing_gear_and_transition_changes(
    tmp_path: Path,
) -> None:
    root, inventory, manifest, mapping = _confirmed()
    candidate = tmp_path / "candidate-program-protection"
    shutil.copytree(root, candidate)
    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        manifest,
        mapping,
        job_id=uuid.uuid4().hex,
        selection_root=FIXTURE / "selection",
    )
    apply_bundle(candidate, bundle)
    component_path = candidate / inventory.target.component_relative_path

    document = etree.parse(str(component_path))
    gear = document.xpath("./displayList/graph[@id='silhouette_01']/gearDisplay")[0]
    original_pages = gear.attrib["pages"]
    gear.attrib["pages"] = "0"
    document.write(str(component_path), encoding="utf-8", xml_declaration=True)
    with pytest.raises(HifiPatchError, match="hifi_protected_structure_changed"):
        validate_hifi_candidate(
            root, candidate, inventory, mapping, session_id=uuid.uuid4().hex
        )

    gear.attrib["pages"] = original_pages
    component = document.xpath("./displayList/component[@id='btn_next']")[0]
    original_component_source = component.attrib["src"]
    component.attrib["src"] = "different-component"
    document.write(str(component_path), encoding="utf-8", xml_declaration=True)
    with pytest.raises(HifiPatchError, match="hifi_protected_structure_changed"):
        validate_hifi_candidate(
            root, candidate, inventory, mapping, session_id=uuid.uuid4().hex
        )

    component.attrib["src"] = original_component_source
    transition = document.xpath("./transition[@name='intro']/item")[0]
    transition.attrib["duration"] = "9.9"
    document.write(str(component_path), encoding="utf-8", xml_declaration=True)
    with pytest.raises(HifiPatchError, match="hifi_protected_structure_changed"):
        validate_hifi_candidate(
            root, candidate, inventory, mapping, session_id=uuid.uuid4().hex
        )


def test_psd_component_mapping_preserves_existing_logical_size_by_default() -> None:
    from lxml import etree

    from figma_to_fgui.figma_selection import SelectionNode
    from figma_to_fgui.hifi_patch import _set_visual
    from figma_to_fgui.models import Bounds

    _root, inventory, _manifest, _mapping = _confirmed()
    element = etree.fromstring(
        b'<component id="synthetic" name="button" xy="0,0" size="120,44"/>'
    )
    source_root = SelectionNode(
        id="psd-root:logical-bounds",
        name="PSD",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=750, height=420),
    )
    target = SelectionNode(
        id="target-group",
        name="Button",
        type="GROUP",
        bounds=Bounds(x=10, y=20, width=180, height=60),
    )

    _set_visual(element, target, source_root, inventory, {})

    assert element.get("xy") == "10,20"
    assert element.get("size") == "120,44"


def test_psd_keep_old_retirement_keeps_object_but_turns_off_default_pixels(tmp_path: Path) -> None:
    from figma_to_fgui.hifi_replacement_models import HifiMappingDraft

    root, inventory, manifest, mapping = _confirmed()
    candidate = tmp_path / "candidate"
    shutil.copytree(root, candidate)
    target = next(item for item in mapping.items if item.old_object_id is not None)
    retired = target.model_copy(update={
        "action": "keep_old",
        "figma_node_id": None,
        "figma_name": None,
        "visual_disposition": "retire",
        "owned_source_ids": (),
        "composite_source_ids": (),
    })
    items = tuple(retired if item.item_id == target.item_id else item for item in mapping.items)
    forced = HifiMappingDraft(
        version=1,
        policy_revision=mapping.policy_revision,
        mapping_revision=mapping.mapping_revision,
        old_canvas_size=mapping.old_canvas_size,
        source_canvas_size=mapping.source_canvas_size,
        items=items,
        unresolved_count=0,
    )

    bundle = build_hifi_change_bundle(
        candidate,
        inventory,
        manifest,
        forced,
        selection_root=FIXTURE / "selection",
    )
    apply_bundle(candidate, bundle)
    document = etree.parse(str(candidate / inventory.target.component_relative_path))
    element = document.xpath("./displayList/*[@id=$id]", id=target.old_object_id)[0]
    assert element.get("id") == target.old_object_id
    assert element.get("visible") == "false"
