import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from lxml import etree
from PIL import Image

from figma_to_fgui.classify import classify_tree
from figma_to_fgui.generate import generate_staging
from figma_to_fgui.models import (
    Bounds,
    ClassificationDecision,
    DecisionSource,
    NormalizedNode,
    NormalizedResourceReference,
    ProjectResource,
)
from figma_to_fgui.normalize import SelectionAsset, normalize_document
from figma_to_fgui.project_index import index_project
from figma_to_fgui.rules import load_rules


def test_generates_parseable_xml_and_reports_rounding(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    files, diagnostics = generate_staging(roots, decisions, "Sample", tmp_path)
    output = tmp_path / "Sample/Panel/Panel_Sample_Main.xml"
    etree.parse(str(output))
    text = output.read_text("utf-8")
    assert 'xy="100,49"' in text
    assert any(item.code == "geometry.rounded" for item in diagnostics)
    assert files[0].relative_path == "Sample/Panel/Panel_Sample_Main.xml"


def test_registers_no_asset_panel_in_package_xml(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project_root = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project_root)
    staging_root = tmp_path / "staging"

    files, _ = generate_staging(
        roots,
        decisions,
        "Sample",
        staging_root,
        project_root,
        index_project(project_root),
    )

    assert "Sample/Panel/Panel_Sample_Main.xml" in {item.relative_path for item in files}
    assert "Sample/package.xml" in {item.relative_path for item in files}
    package = etree.parse(str(staging_root / "Sample" / "package.xml"))
    registered = package.xpath(
        "./resources/component[@name='Panel_Sample_Main.xml' and @path='/Panel/']"
    )
    assert len(registered) == 1


def test_generates_into_standard_fairygui_assets_package_layout(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project_root = tmp_path / "project"
    package_root = project_root / "assets" / "Sample"
    package_root.mkdir(parents=True)
    (package_root / "package.xml").write_text(
        "<packageDescription id='sample'><resources/></packageDescription>", "utf-8"
    )
    staging_root = tmp_path / "staging"

    files, _ = generate_staging(
        roots, decisions, "Sample", staging_root, project_root, index_project(project_root)
    )

    paths = {item.relative_path for item in files}
    assert "assets/Sample/package.xml" in paths
    assert "assets/Sample/Panel/Panel_Sample_Main.xml" in paths


def test_repeat_update_reuses_same_content_in_modern_package_layout(
    tmp_path: Path,
) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    raw["children"][0]["style"] = {
        "resourceRefs": [{"asset": "asset_repeat", "mimeType": "image/png"}]
    }
    roots, _ = normalize_document(raw)
    decisions = classify_tree(
        roots, load_rules(Path("rules/default/classification.yaml"))
    )
    project_root = tmp_path / "project"
    package_root = project_root / "assets" / "Common"
    package_root.mkdir(parents=True)
    (package_root / "package.xml").write_text(
        "<packageDescription id='common'><resources/></packageDescription>",
        "utf-8",
    )
    source = tmp_path / "repeat.png"
    source.write_bytes(b"same-content")
    payload = source.read_bytes()
    asset = SelectionAsset(
        asset="asset_repeat",
        mime_type="image/png",
        source_path=source,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        artifact_fingerprint="fingerprint",
    )

    first_staging = tmp_path / "first-staging"
    generate_staging(
        roots,
        decisions,
        "Common",
        first_staging,
        project_root,
        index_project(project_root),
        (asset,),
    )
    first_package = first_staging / "assets/Common/package.xml"
    first_image = etree.parse(str(first_package)).xpath(
        "./resources/image[@name='asset_repeat.png']"
    )[0]
    first_resource_id = first_image.attrib["id"]
    shutil.copy2(first_package, package_root / "package.xml")
    project_asset = package_root / "assets/asset_repeat.png"
    project_asset.parent.mkdir()
    shutil.copy2(first_staging / "assets/Common/assets/asset_repeat.png", project_asset)

    second_staging = tmp_path / "second-staging"
    second_files, _ = generate_staging(
        roots,
        decisions,
        "Common",
        second_staging,
        project_root,
        index_project(project_root),
        (asset,),
    )

    second_package = etree.parse(str(second_staging / "assets/Common/package.xml"))
    registered = second_package.xpath(
        "./resources/image[@name='asset_repeat.png']"
    )
    assert len(second_package.xpath("./resources/image")) == 1
    assert len(registered) == 1
    assert registered[0].attrib["id"] == first_resource_id
    assert all(not item.relative_path.endswith(".png") for item in second_files)
    assert not (second_staging / "assets/Common/assets/asset_repeat.png").exists()


def test_generated_image_objects_include_fairygui_geometry_and_filename(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    raw["children"][0]["style"] = {
        "resourceRefs": [{"asset": "asset_test", "mimeType": "image/png"}]
    }
    raw["children"][0]["rotation"] = 45
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project_root = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project_root)
    source = tmp_path / "asset.png"
    source.write_bytes(b"asset")
    asset = SelectionAsset(
        asset="asset_test",
        mime_type="image/png",
        source_path=source,
        size=5,
        sha256=hashlib.sha256(b"asset").hexdigest(),
        artifact_fingerprint="fingerprint",
    )
    staging = tmp_path / "staging"

    generate_staging(
        roots, decisions, "Sample", staging, project_root, index_project(project_root), (asset,)
    )

    component = etree.parse(str(staging / "Sample/Panel/Panel_Sample_Main.xml")).getroot()
    image = component.find("./displayList/image")
    assert component.get("size") == "1080,1920"
    assert image is not None
    assert image.get("xy") == "100,51"
    assert image.get("size") == "300,40"
    assert image.get("fileName") == "assets/asset_test.png"
    assert image.get("file") is None
    assert image.get("rotation") is None


def test_recursively_renders_solid_graphs_and_nested_text_in_source_order(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    text = raw["children"][0]
    text["style"]["textAlignVertical"] = "CENTER"
    text["style"]["strokes"] = [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}]
    text["componentProperties"] = {"stroke_weight": {"value": 3}}
    text["opacity"] = 0.6
    raw["children"] = [{
        "id": "1:2",
        "name": "Group",
        "type": "GROUP",
        "absoluteBoundingBox": {"x": 50, "y": 40, "width": 500, "height": 200},
        "children": [
            {
                "id": "1:3",
                "name": "Card",
                "type": "RECTANGLE",
                "absoluteBoundingBox": {"x": 60, "y": 45, "width": 400, "height": 100},
                "opacity": 0.5,
                "style": {"fills": [{"type": "SOLID", "visible": True, "opacity": 1, "color": {"r": 1, "g": 0, "b": 0}}]},
            },
            {**text, "id": "1:4"},
        ],
    }]
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))

    generate_staging(roots, decisions, "Sample", tmp_path)

    display = etree.parse(str(tmp_path / "Sample/Panel/Panel_Sample_Main.xml")).xpath(
        "./displayList"
    )[0]
    assert [item.tag for item in display] == ["graph", "text"]
    assert display[0].get("xy") == "60,45"
    assert display[0].get("size") == "400,100"
    assert display[0].get("fillColor") == "#80ff0000"
    assert display[1].get("text") == "Hello"
    assert display[1].get("xy") == "100,49"
    assert display[1].get("size") == "300,43"
    assert display[1].get("fontSize") == "32"
    assert display[1].get("align") == "center"
    assert display[1].get("vAlign") == "middle"
    assert display[1].get("alpha") == "0.6"
    assert display[1].get("strokeSize") == "3"
    assert display[1].get("strokeColor") == "#ffffff"


def test_maps_figma_font_metadata_to_a_unique_cross_package_fairygui_font(
    tmp_path: Path,
) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    raw["children"][0]["style"]["font"] = {
        "family": "CoreSansESW01-55Medium",
        "style": "Regular",
    }
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project_root = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project_root)
    index = index_project(project_root)
    font = ProjectResource(
        id="kdwnp",
        name="TextFont.ttf",
        kind="font",
        package_id="ub7gxzj7",
        relative_path="Base0/Font/TextFont.ttf",
    )
    sdf_font = ProjectResource(
        id="sdf001",
        name="TextFontSDF.ttf",
        kind="font",
        package_id="ub7gxzj7",
        relative_path="Base0/Font/TextFontSDF.ttf",
    )
    index = index.model_copy(
        update={"font_aliases": {"coresansesw0155medium": (font, sdf_font)}}
    )

    generate_staging(
        roots, decisions, "Sample", tmp_path / "staging", project_root, index
    )

    text = etree.parse(
        str(tmp_path / "staging/Sample/Panel/Panel_Sample_Main.xml")
    ).find("./displayList/text")
    assert text is not None
    assert text.get("font") == "ui://ub7gxzj7kdwnp"


def test_skips_hidden_nodes_and_mask_companion_rectangles(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    raw["children"] = [
        {
            "id": "1:2",
            "name": "Mask Pair",
            "type": "GROUP",
            "absoluteBoundingBox": {"x": 10, "y": 10, "width": 100, "height": 100},
            "children": [
                {
                    "id": "1:3",
                    "name": "Artwork",
                    "type": "RECTANGLE",
                    "absoluteBoundingBox": {"x": 10, "y": 10, "width": 100, "height": 100},
                    "style": {"resourceRefs": [{"asset": "asset_test", "mimeType": "image/png"}]},
                },
                {
                    "id": "1:4",
                    "name": "Mask Fill",
                    "type": "RECTANGLE",
                    "absoluteBoundingBox": {"x": 20, "y": 20, "width": 50, "height": 50},
                    "style": {"fills": [{"type": "SOLID", "color": {"r": 0, "g": 1, "b": 0}}]},
                },
            ],
        },
        {
            "id": "1:5",
            "name": "Hidden",
            "type": "TEXT",
            "visible": False,
            "absoluteBoundingBox": {"x": 0, "y": 0, "width": 20, "height": 20},
            "characters": "hidden",
        },
    ]
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project_root = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project_root)
    source = tmp_path / "asset.png"
    source.write_bytes(b"asset")
    asset = SelectionAsset(
        asset="asset_test", mime_type="image/png", source_path=source, size=5,
        sha256=hashlib.sha256(b"asset").hexdigest(), artifact_fingerprint="fingerprint"
    )
    staging = tmp_path / "staging"

    generate_staging(
        roots, decisions, "Sample", staging, project_root, index_project(project_root), (asset,)
    )

    display = etree.parse(str(staging / "Sample/Panel/Panel_Sample_Main.xml")).xpath(
        "./displayList"
    )[0]
    assert [item.tag for item in display] == ["image"]


def test_resource_backed_parent_with_children_is_blocked_before_legacy_generation(
    tmp_path: Path,
) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    child = deepcopy(raw["children"][0])
    child["id"] = "1:3"
    raw["children"] = [{
        "id": "1:2",
        "name": "Village node",
        "type": "INSTANCE",
        "absoluteBoundingBox": {"x": 50, "y": 40, "width": 300, "height": 200},
        "style": {"resourceRefs": [{"asset": "asset_instance", "mimeType": "image/png"}]},
        "componentProperties": {
            "export_strategy": {"value": "composite_png"},
            "raster_reasons": {"value": ["gradient_paint"]},
        },
        "children": [child],
    }]
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project_root = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project_root)
    source = tmp_path / "instance.png"
    source.write_bytes(b"asset")
    asset = SelectionAsset(
        asset="asset_instance", mime_type="image/png", source_path=source, size=5,
        sha256=hashlib.sha256(b"asset").hexdigest(), artifact_fingerprint="fingerprint"
    )
    staging = tmp_path / "staging"

    with pytest.raises(ValueError, match="unsupported generation features"):
        generate_staging(
            roots,
            decisions,
            "Sample",
            staging,
            project_root,
            index_project(project_root),
            (asset,),
        )

    assert not staging.exists()


def test_rejects_duplicate_top_level_panel_names(tmp_path: Path) -> None:
    first = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    second = deepcopy(first)
    second["id"] = "2:1"
    second["name"] = "main"
    second["children"][0]["id"] = "2:2"
    roots, _ = normalize_document({"roots": [first, second]})
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))

    with pytest.raises(ValueError, match="duplicate panel names"):
        generate_staging(roots, decisions, "Sample", tmp_path)


def test_uses_validated_semantic_name_for_panel_output(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = list(classify_tree(roots, load_rules(Path("rules/default/classification.yaml"))))
    decisions[0] = ClassificationDecision(
        node_id="1:1",
        output_type="PANEL",
        rule_id="ai.semantic.v1",
        rule_version=1,
        evidence=("validated structured AI decision",),
        confidence=0.9,
        source=DecisionSource.AI,
        semantic_name="Checkout",
    )

    generate_staging(roots, tuple(decisions), "Sample", tmp_path)

    assert (tmp_path / "Sample/Panel/Panel_Sample_Checkout.xml").is_file()


def test_rejects_unvalidated_semantic_name_before_writing_paths(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = list(classify_tree(roots, load_rules(Path("rules/default/classification.yaml"))))
    decisions[0] = ClassificationDecision(
        node_id="1:1",
        output_type="PANEL",
        rule_id="ai.semantic.v1",
        rule_version=1,
        evidence=("unvalidated external override",),
        confidence=0.9,
        source=DecisionSource.AI,
        semantic_name="../escape",
    )

    with pytest.raises(ValueError, match="semantic name"):
        generate_staging(roots, tuple(decisions), "Sample", tmp_path)

    assert not (tmp_path / "Sample").exists()


def test_rejects_case_insensitive_generated_object_name_collisions(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    duplicate = deepcopy(raw["children"][0])
    duplicate["id"] = "1:3"
    duplicate["name"] = "title"
    raw["children"].append(duplicate)
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))

    with pytest.raises(ValueError, match="duplicate generated object names"):
        generate_staging(roots, decisions, "Sample", tmp_path)

    assert not (tmp_path / "Sample").exists()


def test_rejects_case_only_existing_package_component_before_writing(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = list(classify_tree(roots, load_rules(Path("rules/default/classification.yaml"))))
    decisions[0] = ClassificationDecision(
        node_id="1:1",
        output_type="PANEL",
        rule_id="ai.semantic.v1",
        rule_version=1,
        evidence=("validated structured AI decision",),
        confidence=0.9,
        source=DecisionSource.AI,
        semantic_name="main",
    )
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project)
    package_path = project / "Sample/package.xml"
    package = etree.parse(str(package_path))
    etree.SubElement(
        package.getroot().find("resources"),
        "component",
        id="cmp-case",
        name="Panel_Sample_Main.xml",
        path="/Panel/",
        exported="true",
    )
    package.write(str(package_path), encoding="utf-8", xml_declaration=True)
    staging = tmp_path / "staging"

    with pytest.raises(ValueError, match="case-insensitive panel collision"):
        generate_staging(
            roots,
            tuple(decisions),
            "Sample",
            staging,
            project,
            index_project(project),
        )

    assert not staging.exists()


def test_rejects_case_only_existing_panel_file_before_writing(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = list(classify_tree(roots, load_rules(Path("rules/default/classification.yaml"))))
    decisions[0] = ClassificationDecision(
        node_id="1:1",
        output_type="PANEL",
        rule_id="ai.semantic.v1",
        rule_version=1,
        evidence=("validated structured AI decision",),
        confidence=0.9,
        source=DecisionSource.AI,
        semantic_name="main",
    )
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project)
    existing = project / "Sample/Panel/Panel_Sample_Main.xml"
    existing.parent.mkdir()
    existing.write_text("<component name='Panel_Sample_Main'/>", "utf-8")
    staging = tmp_path / "staging"

    with pytest.raises(ValueError, match="case-insensitive panel collision"):
        generate_staging(
            roots,
            tuple(decisions),
            "Sample",
            staging,
            project,
            index_project(project),
        )

    assert not staging.exists()


def test_allows_exact_existing_panel_name_as_an_update(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project)
    package_path = project / "Sample/package.xml"
    package = etree.parse(str(package_path))
    etree.SubElement(
        package.getroot().find("resources"),
        "component",
        id="cmp-main",
        name="Panel_Sample_Main.xml",
        path="/Panel/",
        exported="true",
    )
    package.write(str(package_path), encoding="utf-8", xml_declaration=True)
    existing = project / "Sample/Panel/Panel_Sample_Main.xml"
    existing.parent.mkdir()
    existing.write_text("<component name='Panel_Sample_Main'/>", "utf-8")
    staging = tmp_path / "staging"

    generate_staging(
        roots,
        decisions,
        "Sample",
        staging,
        project,
        index_project(project),
    )

    assert (staging / "Sample/Panel/Panel_Sample_Main.xml").is_file()
    staged_package = etree.parse(str(staging / "Sample/package.xml"))
    assert len(staged_package.xpath("./resources/component[@name='Panel_Sample_Main.xml']")) == 1


def test_writes_figma_nine_slice_insets_as_fairygui_scale9grid(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    raw["children"] = [{
        "id": "1:2",
        "name": "Button",
        "type": "RECTANGLE",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 100, "height": 60},
        "componentProperties": {"nine_slice_insets": {"value": {"left": 10, "top": 8, "right": 20, "bottom": 12}}},
        "style": {"resourceRefs": [{"asset": "asset_nine", "mimeType": "image/png"}]},
    }]
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project)
    source = tmp_path / "nine.png"
    Image.new("RGBA", (100, 60), (255, 255, 255, 255)).save(source)
    payload = source.read_bytes()
    asset = SelectionAsset(asset="asset_nine", mime_type="image/png", source_path=source, size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), artifact_fingerprint="fingerprint")

    _, diagnostics = generate_staging(roots, decisions, "Sample", tmp_path / "staging", project, index_project(project), (asset,))

    package = etree.parse(str(tmp_path / "staging/Sample/package.xml"))
    image = package.xpath("./resources/image[@name='asset_nine.png']")[0]
    assert image.attrib["scale9grid"] == "10,8,70,40"
    assert diagnostics == ()


def test_existing_fairygui_nine_slice_wins_and_reports_conflict(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    raw["children"] = [{
        "id": "1:2", "name": "Button", "type": "RECTANGLE",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 100, "height": 60},
        "componentProperties": {"nine_slice_insets": {"value": {"left": 10, "top": 8, "right": 20, "bottom": 12}}},
        "style": {"resourceRefs": [{"asset": "asset_nine", "mimeType": "image/png"}]},
    }]
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project)
    source = tmp_path / "nine.png"
    Image.new("RGBA", (100, 60), (255, 255, 255, 255)).save(source)
    payload = source.read_bytes()
    asset_dir = project / "Sample/assets"
    asset_dir.mkdir(exist_ok=True)
    (asset_dir / "asset_nine.png").write_bytes(payload)
    package_path = project / "Sample/package.xml"
    package = etree.parse(str(package_path))
    etree.SubElement(package.getroot().find("resources"), "image", id="existing-nine", name="asset_nine.png", path="/assets/", scale9grid="4,4,92,52")
    package.write(str(package_path), encoding="utf-8", xml_declaration=True)
    asset = SelectionAsset(asset="asset_nine", mime_type="image/png", source_path=source, size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), artifact_fingerprint="fingerprint")

    _, diagnostics = generate_staging(roots, decisions, "Sample", tmp_path / "staging", project, index_project(project), (asset,))

    output = etree.parse(str(tmp_path / "staging/Sample/package.xml"))
    assert output.xpath("./resources/image[@name='asset_nine.png']")[0].attrib["scale9grid"] == "4,4,92,52"
    assert [item.code for item in diagnostics] == ["nine_slice_conflict"]


def test_nine_slice_outside_exported_raster_degrades_to_plain_image(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    raw["children"] = [{
        "id": "1:2", "name": "Button", "type": "RECTANGLE",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 100, "height": 60},
        "componentProperties": {"nine_slice_insets": {"value": {"left": 15, "top": 8, "right": 15, "bottom": 8}}},
        "style": {"resourceRefs": [{"asset": "asset_small", "mimeType": "image/png"}]},
    }]
    roots, _ = normalize_document(raw)
    decisions = classify_tree(roots, load_rules(Path("rules/default/classification.yaml")))
    project = tmp_path / "project"
    shutil.copytree(Path("tests/fixtures/fgui"), project)
    source = tmp_path / "small.png"
    Image.new("RGBA", (20, 20), (255, 255, 255, 255)).save(source)
    payload = source.read_bytes()
    asset = SelectionAsset(asset="asset_small", mime_type="image/png", source_path=source, size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), artifact_fingerprint="fingerprint")

    _, diagnostics = generate_staging(roots, decisions, "Sample", tmp_path / "staging", project, index_project(project), (asset,))

    image = etree.parse(str(tmp_path / "staging/Sample/package.xml")).xpath("./resources/image[@name='asset_small.png']")[0]
    assert "scale9grid" not in image.attrib
    assert [item.code for item in diagnostics] == ["nine_slice_out_of_bounds"]


@pytest.mark.parametrize(
    "properties",
    [
        {"interactions": {"present": True}},
        {"controllers": ({"name": "State"},)},
        {"list_items": ("first",)},
        {"layout_mode": "HORIZONTAL", "layout_wrap": "WRAP"},
    ],
)
def test_legacy_generation_blocks_out_of_scope_behavior_before_writing(
    tmp_path: Path, properties: dict[str, object]
) -> None:
    root = NormalizedNode(
        id="root",
        name="Main",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=100, height=80),
        properties=properties,
    )
    decision = ClassificationDecision(
        node_id=root.id,
        output_type="PANEL",
        rule_id="fixture",
        rule_version=1,
        evidence=("fixture",),
        confidence=1,
    )
    staging = tmp_path / "staging"

    with pytest.raises(ValueError, match="unsupported generation features"):
        generate_staging((root,), (decision,), "Sample", staging)

    assert not staging.exists()


@pytest.mark.parametrize(
    "case",
    [
        "rich_text",
        "base_text_style",
        "complex_text_visual",
        "transform",
        "asset_subtree",
        "clip",
    ],
)
def test_legacy_generation_blocks_unfaithful_visual_semantics_before_writing(
    tmp_path: Path, case: str
) -> None:
    child = NormalizedNode(
        id="text",
        name="Label",
        type="TEXT",
        bounds=Bounds(x=0, y=0, width=80, height=20),
        text="Title",
        raw_style=(
            {
                "runs": (
                    {
                        "content": "Title",
                        "style": {"fontSize": 14, "color": "#ffffffff"},
                    },
                )
            }
            if case == "rich_text"
            else {"lineHeightPx": 24}
            if case == "base_text_style"
            else {"effects": ({"type": "DROP_SHADOW", "visible": True},)}
            if case == "complex_text_visual"
            else {"relativeTransform": ((1, 0.25, 0), (0, 1, 0))}
            if case == "transform"
            else {}
        ),
    )
    root = NormalizedNode(
        id="root",
        name="Main",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=100, height=80),
        children=(child,),
        properties={"clips_content": True} if case == "clip" else {},
        resource_refs=(
            (
                NormalizedResourceReference(
                    asset="asset_root",
                    mimeType="image/png",
                    sha256="a" * 64,
                ),
            )
            if case == "asset_subtree"
            else ()
        ),
    )
    decisions = (
        ClassificationDecision(
            node_id=root.id,
            output_type="PANEL",
            rule_id="fixture",
            rule_version=1,
            evidence=("fixture",),
            confidence=1,
        ),
        ClassificationDecision(
            node_id=child.id,
            output_type="TEXT",
            rule_id="fixture",
            rule_version=1,
            evidence=("fixture",),
            confidence=1,
        ),
    )
    staging = tmp_path / "staging"

    with pytest.raises(ValueError, match="unsupported generation features"):
        generate_staging((root,), decisions, "Sample", staging)

    assert not staging.exists()
