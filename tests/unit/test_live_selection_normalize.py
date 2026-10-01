import json
from hashlib import sha256
from pathlib import Path

import pytest
from lxml import etree

from figma_to_fgui.component_mapping import (
    ComponentMapping,
    ComponentMappingCatalog,
    FguiMappingTarget,
    FigmaMappingTarget,
)
from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_validate import validate_fgui_plan
from figma_to_fgui.figma_selection import (
    SelectionManifest,
    SelectionNode,
    SelectionResource,
    SelectionWarning,
)
from figma_to_fgui.models import Bounds
from figma_to_fgui.normalize import (
    SelectionAsset,
    normalize_document,
    selection_conversion_document,
    selection_document,
)
from figma_to_fgui.pipeline import (
    MAX_SELECTION_CONVERSION_BYTES,
    ConversionLimitError,
    ConversionRequest,
    convert,
    convert_document,
)
from figma_to_fgui.project_index import index_project
from figma_to_fgui.uir_compile import compile_uir
from figma_to_fgui.uir_validate import validate_uir


def test_selection_document_normalizes_live_nodes_without_figma_rest_shape(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    (resources / "hero").write_bytes(b"raster")
    (resources / "mark").write_text("<svg/>", "utf-8")
    manifest = SelectionManifest(
        display_name="Checkout",
        resources=(
            SelectionResource(key="hero", mime_type="image/png", size=6),
            SelectionResource(key="mark", mime_type="image/svg+xml", size=6),
        ),
        top_level_nodes=(
            SelectionNode(
                id="private-frame-id",
                name="Checkout Panel",
                type="FRAME",
                bounds=Bounds(x=10, y=20, width=600, height=400),
                style={"layoutMode": "VERTICAL", "itemSpacing": 12},
                properties={
                    "State": "Default",
                    "export_strategy": "composite_png",
                    "raster_reasons": ["gradient_paint", "visual_effect"],
                },
                resource_keys=("hero", "mark"),
                children=(
                    SelectionNode(
                        id="private-text-id",
                        name="Purchase label",
                        type="TEXT",
                        bounds=Bounds(x=30, y=44, width=160, height=28),
                        text="Buy now",
                        source_order=1,
                        style={"fontSize": 20},
                    ),
                    SelectionNode(
                        id="private-instance-id",
                        name="Button instance",
                        type="INSTANCE",
                        bounds=Bounds(x=30, y=90, width=160, height=44),
                        source_order=2,
                    ),
                ),
            ),
            SelectionNode(
                id="private-second-frame-id",
                name="Second panel",
                type="FRAME",
                bounds=Bounds(x=700, y=20, width=600, height=400),
                source_order=4,
            ),
        ),
    )

    raw = selection_document(manifest, resources)
    roots, diagnostics = normalize_document(raw)

    assert diagnostics == ()
    assert [node.name for node in roots] == ["Checkout Panel", "Second panel"]
    assert [node.name for node in roots[0].children] == ["Purchase label", "Button instance"]
    assert roots[0].bounds == Bounds(x=10, y=20, width=600, height=400)
    assert roots[0].children[0].text == "Buy now"
    assert roots[1].source_order == 4
    assert roots[0].properties == {
        "State": "Default",
        "export_strategy": "composite_png",
        "raster_reasons": ["gradient_paint", "visual_effect"],
    }
    references = tuple(
        reference.model_dump(mode="json", by_alias=True, exclude_none=True)
        for reference in roots[0].resource_refs
    )
    assert roots[0].raw_style == {
        "layoutMode": "VERTICAL",
        "itemSpacing": 12,
    }
    raster_digest = sha256(b"raster").hexdigest()
    svg_digest = sha256(b"<svg/>").hexdigest()
    assert references == (
        {
            "asset": "asset_" + sha256(f"|0|image/png|6|{raster_digest}".encode()).hexdigest()[:24],
            "mimeType": "image/png",
            "sha256": raster_digest,
            "width": 600,
            "height": 400,
            "exportFormat": "png",
        },
        {
            "asset": "asset_"
            + sha256(f"|1|image/svg+xml|6|{svg_digest}".encode()).hexdigest()[:24],
            "mimeType": "image/svg+xml",
            "sha256": svg_digest,
            "width": 600,
            "height": 400,
            "exportFormat": "svg",
        },
    )
    for raw_identifier in (
        "private-frame-id",
        "private-text-id",
        "private-instance-id",
        "hero",
        "mark",
    ):
        assert raw_identifier not in str(raw)
        assert raw_identifier not in str(roots)
    assert "absoluteBoundingBox" not in str(manifest.model_dump())


def test_live_svg_uses_intrinsic_dimensions_when_node_bounds_are_zero(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="82" height="166" viewBox="0 0 82 166"><path d="M0 0h82v166H0z"/></svg>'
    (resources / "vector").write_bytes(svg)
    manifest = SelectionManifest(
        display_name="Zero-width vector",
        resources=(
            SelectionResource(key="vector", mime_type="image/svg+xml", size=len(svg)),
        ),
        top_level_nodes=(
            SelectionNode(
                id="private-vector",
                name="Vector",
                type="VECTOR",
                bounds=Bounds(x=0, y=0, width=0, height=0),
                resource_keys=("vector",),
            ),
        ),
    )

    converted = selection_conversion_document(manifest, resources)
    roots, diagnostics = normalize_document(converted.raw)

    assert diagnostics == ()
    assert roots[0].resource_refs[0].width == 82
    assert roots[0].resource_refs[0].height == 166


@pytest.mark.parametrize(
    ("mime_type", "rotation", "message"),
    [
        ("image/svg+xml", 0, "vector asset must use PNG"),
        ("image/png", 15, "vector PNG rotation must be baked"),
    ],
)
def test_new_vector_asset_contract_rejects_svg_and_double_rotation(
    tmp_path: Path, mime_type: str, rotation: float, message: str
) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    content = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        if mime_type == "image/svg+xml"
        else (
            Path(__file__).parents[1]
            / "fixtures"
            / "fgui-new-project"
            / "resources"
            / "one-pixel.png"
        ).read_bytes()
    )
    (resources / "vector").write_bytes(content)
    manifest = SelectionManifest(
        display_name="Vector",
        resources=(SelectionResource(key="vector", mime_type=mime_type, size=len(content)),),
        top_level_nodes=(
            SelectionNode(
                id="vector",
                name="Vector",
                type="VECTOR",
                bounds=Bounds(x=10, y=20, width=1, height=1),
                rotation=rotation,
                properties={"export_strategy": "vector_asset"},
                resource_keys=("vector",),
            ),
        ),
    )

    with pytest.raises(ValueError, match=message):
        selection_conversion_document(manifest, resources)


@pytest.mark.parametrize("strategy", ["image_asset", "composite_png"])
def test_exported_png_contract_rejects_double_rotation(
    tmp_path: Path, strategy: str
) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    content = (
        Path(__file__).parents[1]
        / "fixtures"
        / "fgui-new-project"
        / "resources"
        / "one-pixel.png"
    ).read_bytes()
    (resources / "bitmap").write_bytes(content)
    manifest = SelectionManifest(
        display_name="Rotated bitmap",
        resources=(
            SelectionResource(key="bitmap", mime_type="image/png", size=len(content)),
        ),
        top_level_nodes=(
            SelectionNode(
                id="bitmap",
                name="Bitmap",
                type="RECTANGLE",
                bounds=Bounds(x=10, y=20, width=1, height=1),
                rotation=45,
                properties={"export_strategy": strategy},
                resource_keys=("bitmap",),
            ),
        ),
    )

    with pytest.raises(ValueError, match="exported PNG rotation must be baked"):
        selection_conversion_document(manifest, resources)


def test_live_simple_rectangle_mask_reaches_a_valid_native_clip_plan(tmp_path: Path) -> None:
    manifest = SelectionManifest(
        display_name="Masked group",
        top_level_nodes=(
            SelectionNode(
                id="node-1",
                name="Masked group",
                type="GROUP",
                bounds=Bounds(x=0, y=0, width=200, height=120),
                style={
                    "mask": {
                        "kind": "rectangle",
                        "maskNodeRef": "node-2",
                        "contentNodeRefs": ["node-3"],
                        "effects": [],
                    }
                },
                children=(
                    SelectionNode(
                        id="node-2",
                        name="Mask",
                        type="RECTANGLE",
                        bounds=Bounds(x=0, y=0, width=200, height=120),
                        style={
                            "fills": [
                                {
                                    "type": "SOLID",
                                    "color": {"r": 1, "g": 1, "b": 1},
                                }
                            ]
                        },
                    ),
                    SelectionNode(
                        id="node-3",
                        name="Editable",
                        type="TEXT",
                        bounds=Bounds(x=10, y=10, width=100, height=24),
                        text="Label",
                    ),
                ),
            ),
        ),
    )
    converted = selection_conversion_document(manifest, tmp_path, "f" * 64)
    roots, diagnostics = normalize_document(converted.raw)
    document = compile_uir(
        roots,
        source_revision="a" * 64,
        selection_id="live-mask",
    )
    plan = compile_fgui_plan(document)

    assert diagnostics == ()
    assert validate_uir(document) == ()
    assert plan.bindable is True
    assert len(plan.masks) == 1
    assert next(iter(plan.masks.values())).mode.value == "nativeClip"
    assert validate_fgui_plan(plan) == ()


def test_raw_figma_rest_features_reach_the_normalized_contract() -> None:
    raw = {
        "id": "frame",
        "name": "Scrollable panel",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 320, "height": 180},
        "layoutMode": "HORIZONTAL",
        "layoutWrap": "WRAP",
        "counterAxisSpacing": 12,
        "minWidth": 100,
        "maxWidth": 640,
        "clipsContent": True,
        "cornerRadius": 16,
        "reactions": [
            {
                "trigger": {"type": "ON_CLICK"},
                "action": {"destinationId": "private-target-node"},
            }
        ],
        "children": [],
    }

    (root,), diagnostics = normalize_document(raw)

    assert diagnostics == ()
    assert root.properties == {
        "counter_axis_spacing": 12,
        "layout_mode": "HORIZONTAL",
        "layout_wrap": "WRAP",
        "max_width": 640,
        "min_width": 100,
        "clips_content": True,
        "corner_radius": 16,
        "interactions": {"present": True, "reaction_count": 1},
    }
    assert "private-target-node" not in str(root)


def test_raw_transform_is_preserved_and_unrepresentable_geometry_blocks() -> None:
    base = {
        "id": "translated",
        "name": "Translated",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 152, "y": 1562, "width": 100, "height": 50},
        "relativeTransform": [[1, 0, 152], [0, 1, 1562]],
    }
    roots, _ = normalize_document(base)
    document = compile_uir(
        roots, source_revision="a" * 64, selection_id="rest-transform"
    )

    assert document.nodes[document.roots[0]].geometry.local_transform == (
        1,
        0,
        0,
        1,
        152,
        1562,
    )
    assert compile_fgui_plan(document).bindable is True

    skewed = {**base, "relativeTransform": [[1, 0.25, 152], [0, 1, 1562]]}
    skewed_roots, _ = normalize_document(skewed)
    skewed_document = compile_uir(
        skewed_roots, source_revision="a" * 64, selection_id="rest-skew"
    )
    skewed_plan = compile_fgui_plan(skewed_document)

    assert skewed_plan.bindable is False
    assert any(
        decision.rule_id == "fgui.unsupported.transform"
        for decision in skewed_plan.decisions.values()
    )
    assert validate_fgui_plan(skewed_plan) == ()


@pytest.mark.parametrize("feature", ["blend", "explicit_mask"])
def test_raw_composition_features_block_instead_of_disappearing(feature: str) -> None:
    raw: dict[str, object] = {
        "id": "root",
        "name": "Composed",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 100, "height": 80},
        "children": [],
    }
    if feature == "blend":
        raw["blendMode"] = "MULTIPLY"
    else:
        raw["children"] = [
            {
                "id": "mask",
                "name": "Mask",
                "type": "FRAME",
                "isMask": True,
                "absoluteBoundingBox": {"x": 0, "y": 0, "width": 100, "height": 80},
            },
            {
                "id": "content",
                "name": "Content",
                "type": "FRAME",
                "absoluteBoundingBox": {"x": 0, "y": 0, "width": 100, "height": 80},
            },
        ]
    roots, _ = normalize_document(raw)
    plan = compile_fgui_plan(
        compile_uir(
            roots,
            source_revision="a" * 64,
            selection_id=f"rest-{feature}",
        )
    )

    assert plan.bindable is False
    assert validate_fgui_plan(plan) == ()


def test_raw_complex_text_visuals_block_instead_of_disappearing() -> None:
    raw = {
        "id": "text",
        "name": "Gradient label",
        "type": "TEXT",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 120, "height": 24},
        "characters": "Gradient",
        "fills": [{"type": "GRADIENT_LINEAR"}],
        "effects": [{"type": "DROP_SHADOW", "visible": True}],
        "style": {"fontFamily": "Inter", "fontSize": 18},
    }
    roots, _ = normalize_document(raw)
    document = compile_uir(
        roots, source_revision="a" * 64, selection_id="rest-gradient-text"
    )
    plan = compile_fgui_plan(document)

    assert document.nodes[document.roots[0]].visual["effects"]
    assert plan.bindable is False
    assert any(
        decision.rule_id == "fgui.unsupported.visual_style"
        for decision in plan.decisions.values()
    )
    assert validate_fgui_plan(plan) == ()


def test_raw_figma_rest_text_color_and_font_reach_uir_and_plan() -> None:
    raw = {
        "id": "text",
        "name": "Label",
        "type": "TEXT",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 120, "height": 24},
        "characters": "Red label",
        "fills": [
            {
                "type": "SOLID",
                "color": {"r": 1, "g": 0, "b": 0, "a": 1},
                "opacity": 1,
            }
        ],
        "strokes": [
            {
                "type": "SOLID",
                "color": {"r": 0, "g": 0, "b": 1, "a": 1},
            }
        ],
        "style": {
            "fontFamily": "Inter",
            "fontPostScriptName": "Inter-Bold",
            "fontWeight": 700,
            "fontSize": 18,
            "strokeWeight": 2,
        },
    }

    roots, _ = normalize_document(raw)
    document = compile_uir(
        roots, source_revision="a" * 64, selection_id="rest-text"
    )
    plan = compile_fgui_plan(document)
    text = document.nodes[document.roots[0]].text
    planned_text = next(iter(plan.nodes.values())).text

    assert text is not None and planned_text is not None
    assert text.style.color == "#ff0000ff"
    assert text.style.font_candidates == ("Inter-Bold", "Inter")
    assert text.style.stroke_color == "#0000ffff"
    assert text.style.stroke_size == 2
    assert planned_text.color == "#ff0000ff"
    assert planned_text.font_candidates == ("Inter-Bold", "Inter")
    assert planned_text.stroke_color == "#0000ffff"
    assert planned_text.stroke_size == 2
    assert plan.bindable is True
    assert validate_fgui_plan(plan) == ()


def test_raw_figma_rest_mixed_text_overrides_remain_reviewable_editable_text() -> None:
    raw = {
        "id": "text",
        "name": "Mixed label",
        "type": "TEXT",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 120, "height": 24},
        "characters": "AB",
        "characterStyleOverrides": [0, 1],
        "styleOverrideTable": {"1": {"fontSize": 24}},
        "style": {"fontFamily": "Inter", "fontSize": 18},
    }

    roots, _ = normalize_document(raw)
    document = compile_uir(
        roots, source_revision="a" * 64, selection_id="rest-mixed-text"
    )
    plan = compile_fgui_plan(document)
    text = document.nodes[document.roots[0]].text
    planned = next(iter(plan.nodes.values()))
    decision = plan.decisions[document.roots[0]]

    assert text is not None and text.runs and planned.text is not None
    assert text.runs[0].unsupported_features == ("rest_text_style_overrides",)
    assert plan.bindable is True
    assert planned.type == "text"
    assert planned.text.content == text.content
    assert planned.text.font_candidates == text.style.font_candidates
    assert planned.text.font_size == text.style.font_size
    assert planned.text.color == text.style.color
    assert planned.text.stroke_color == text.style.stroke_color
    assert planned.text.stroke_size == text.style.stroke_size
    assert planned.text.runs == ()
    assert planned.resource_ref is None
    assert plan.resources == {}
    assert decision.rule_id == "fgui.text.runs_unsupported"
    assert decision.reasons == ("rich_text_runs",)
    assert decision.evidence == (
        "text.runs.count=1",
        "text.runs.preserved=content",
        "text.runs.unsupported=rest_text_style_overrides",
    )
    assert validate_fgui_plan(plan) == ()


def test_raw_figma_rest_nondefault_base_text_features_remain_reviewable_editable_text() -> None:
    raw = {
        "id": "text",
        "name": "Styled label",
        "type": "TEXT",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 120, "height": 24},
        "characters": "Styled",
        "style": {
            "fontFamily": "Inter",
            "fontSize": 18,
            "lineHeightPx": 24,
            "letterSpacing": 2,
            "textCase": "UPPER",
            "textDecoration": "UNDERLINE",
        },
    }

    roots, _ = normalize_document(raw)
    document = compile_uir(
        roots, source_revision="a" * 64, selection_id="rest-base-style"
    )
    plan = compile_fgui_plan(document)
    text = document.nodes[document.roots[0]].text
    planned = next(iter(plan.nodes.values()))
    decision = plan.decisions[document.roots[0]]

    assert text is not None and text.runs and planned.text is not None
    assert set(text.runs[0].unsupported_features) == {
        "letter_spacing",
        "text_case",
        "text_decoration",
    }
    assert plan.bindable is True
    assert planned.type == "text"
    assert planned.text.content == text.content
    assert planned.text.font_candidates == text.style.font_candidates
    assert planned.text.font_size == text.style.font_size
    assert planned.text.line_height == 24
    assert planned.text.color == text.style.color
    assert planned.text.stroke_color == text.style.stroke_color
    assert planned.text.stroke_size == text.style.stroke_size
    assert planned.text.runs == ()
    assert planned.resource_ref is None
    assert plan.resources == {}
    assert decision.rule_id == "fgui.text.style_unsupported"
    assert decision.reasons == ("text_style_properties",)
    assert decision.evidence == (
        "text.runs.count=1",
        "text.runs.preserved=content",
        "text.runs.unsupported=letter_spacing,text_case,text_decoration",
    )
    assert validate_fgui_plan(plan) == ()


def test_raw_figma_rest_tree_depth_is_rejected_before_recursive_normalization() -> None:
    raw = {
        "id": "leaf",
        "name": "Leaf",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 1, "height": 1},
    }
    for index in range(300):
        raw = {
            "id": f"node-{index}",
            "name": "Node",
            "type": "FRAME",
            "absoluteBoundingBox": {"x": 0, "y": 0, "width": 1, "height": 1},
            "children": [raw],
        }

    with pytest.raises(ValueError, match="tree depth"):
        normalize_document(raw)


def test_live_selection_assets_reach_a_bindable_generic_plan(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    payloads = {
        "image": b"image-png",
        "vector": b"<svg>vector</svg>",
        "composite": b"composite-png",
        "missing-component": b"missing-component-png",
    }
    for key, payload in payloads.items():
        (resources / key).write_bytes(payload)

    manifest = SelectionManifest(
        display_name="Production asset bridge",
        resources=tuple(
            SelectionResource(
                key=key,
                mime_type="image/svg+xml" if key == "vector" else "image/png",
                size=len(payload),
            )
            for key, payload in payloads.items()
        ),
        top_level_nodes=(
            SelectionNode(
                id="private-image",
                name="Image",
                type="RECTANGLE",
                bounds=Bounds(x=0, y=0, width=100, height=80),
                properties={
                    "nine_slice_insets": {
                        "left": 10,
                        "top": 8,
                        "right": 20,
                        "bottom": 12,
                    }
                },
                resource_keys=("image",),
            ),
            SelectionNode(
                id="private-vector",
                name="Vector",
                type="VECTOR",
                bounds=Bounds(x=120, y=0, width=40, height=30),
                resource_keys=("vector",),
            ),
            SelectionNode(
                id="private-composite",
                name="Composite",
                type="FRAME",
                bounds=Bounds(x=0, y=100, width=200, height=120),
                properties={
                    "export_strategy": "composite_png",
                    "raster_reasons": ["visual_effect"],
                },
                resource_keys=("composite",),
            ),
            SelectionNode(
                id="private-instance",
                name="Missing component",
                type="INSTANCE",
                bounds=Bounds(x=0, y=240, width=180, height=60),
                resource_keys=("missing-component",),
            ),
        ),
    )
    mapping_catalog = ComponentMappingCatalog(
        schemaVersion=1,
        sources=("fixture",),
        components=(
            ComponentMapping(
                key="missing_component",
                figma=FigmaMappingTarget(names=("Missing component",)),
                fgui=FguiMappingTarget(
                    package="Future",
                    component="Missing",
                    path="Missing.xml",
                ),
                source=("fixture",),
                status="missing",
                reason="component_not_found",
            ),
        ),
    )

    converted = selection_conversion_document(manifest, resources, "f" * 64)
    roots, normalize_diagnostics = normalize_document(converted.raw)
    assert normalize_diagnostics == ()
    assert all(root.resource_refs for root in roots)

    uir = compile_uir(
        roots,
        source_revision="a" * 64,
        selection_id="production-asset-bridge",
        mapping_catalog=mapping_catalog,
    )
    assert validate_uir(uir) == ()
    plan = compile_fgui_plan(uir)

    assert validate_fgui_plan(plan) == ()
    assert plan.bindable is True, [
        (
            item.code,
            item.node_id,
            None if item.node_id is None else uir.nodes[item.node_id].source.type,
            None if item.node_id is None else uir.nodes[item.node_id].conversion,
        )
        for item in plan.diagnostics
    ]
    planned_by_source_type = {
        uir.nodes[node.uir_node_ref].source.type: node for node in plan.nodes.values()
    }
    assert planned_by_source_type["RECTANGLE"].type == "image"
    assert planned_by_source_type["VECTOR"].type == "image"
    assert planned_by_source_type["FRAME"].type == "rasterSubtree"
    assert planned_by_source_type["INSTANCE"].type == "rasterSubtree"
    assert {resource.content_sha256 for resource in plan.resources.values()} == {
        asset.sha256 for asset in converted.assets
    }
    assert {resource.logical_asset_id for resource in plan.resources.values()} == {
        asset.asset for asset in converted.assets
    }
    for resource in plan.resources.values():
        assert resource.export_parameters_sha256
    image_resource = plan.resources[planned_by_source_type["RECTANGLE"].resource_ref]
    assert (image_resource.width, image_resource.height) == (100, 80)
    assert image_resource.nine_slice is not None
    assert image_resource.nine_slice.model_dump() == {
        "x": 10,
        "y": 8,
        "width": 70,
        "height": 60,
    }


def test_live_selection_blocking_warning_survives_into_capability_policy(
    tmp_path: Path,
) -> None:
    manifest = SelectionManifest(
        display_name="Prototype",
        top_level_nodes=(
            SelectionNode(
                id="private-frame",
                name="Prototype",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=100, height=80),
            ),
        ),
        warnings=(
            SelectionWarning(
                code="unsupported_prototype",
                message="Prototype behavior is unsupported.",
            ),
        ),
    )

    converted = selection_conversion_document(manifest, tmp_path, "f" * 64)
    roots, diagnostics = normalize_document(converted.raw)
    uir = compile_uir(
        roots,
        source_revision="a" * 64,
        selection_id="prototype-warning",
    )
    plan = compile_fgui_plan(uir)

    assert diagnostics == ()
    assert roots[0].properties["selection_warning_codes"] == (
        "unsupported_prototype",
    )
    assert uir.nodes[uir.roots[0]].conversion.mode == "unsupported"
    assert plan.bindable is False
    assert validate_fgui_plan(plan) == ()


def test_live_selection_nine_slice_warning_remains_reviewable(tmp_path: Path) -> None:
    manifest = SelectionManifest(
        display_name="Nine slice",
        top_level_nodes=(
            SelectionNode(
                id="private-frame",
                name="Panel",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=100, height=80),
            ),
        ),
        warnings=(
            SelectionWarning(
                code="nine_slice_invalid",
                message="Private plugin message is not copied.",
            ),
        ),
    )

    converted = selection_conversion_document(manifest, tmp_path, "f" * 64)
    roots, _ = normalize_document(converted.raw)
    document = compile_uir(
        roots, source_revision="a" * 64, selection_id="nine-slice-warning"
    )

    assert roots[0].properties["selection_warning_codes"] == (
        "nine_slice_invalid",
    )
    assert [item.code for item in document.diagnostics] == [
        "uir.selection.nine_slice_invalid"
    ]
    assert validate_uir(document) == ()
    assert "Private plugin message" not in str(document)


def test_convert_document_preserves_fixture_conversion_bytes(tmp_path: Path) -> None:
    raw = json.loads(Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"))
    converted = convert_document(
        raw,
        Path("tests/fixtures/fgui"),
        "Sample",
        tmp_path / "document",
        Path("rules/default/classification.yaml"),
    )
    legacy = convert(
        ConversionRequest(
            figma_json=Path("tests/fixtures/figma/simple-frame.json"),
            project_root=Path("tests/fixtures/fgui"),
            package_name="Sample",
            staging_root=tmp_path / "legacy",
            classification_rules=Path("rules/default/classification.yaml"),
        )
    )

    assert converted == legacy
    assert (tmp_path / "document/Sample/Panel/Panel_Sample_Main.xml").read_bytes() == (
        tmp_path / "legacy/Sample/Panel/Panel_Sample_Main.xml"
    ).read_bytes()


def test_selection_resources_materialize_with_opaque_references(tmp_path: Path) -> None:
    resources = tmp_path / "selection-resources"
    resources.mkdir()
    content = b"selection-raster"
    (resources / "figma-resource-key").write_bytes(content)
    manifest = SelectionManifest(
        display_name="Asset panel",
        resources=(
            SelectionResource(key="figma-resource-key", mime_type="image/png", size=len(content)),
        ),
        top_level_nodes=(
            SelectionNode(
                id="figma-node-id",
                name="AssetPanel",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=600, height=400),
                children=(
                    SelectionNode(
                        id="figma-image-id",
                        name="Background",
                        type="RECTANGLE",
                        bounds=Bounds(x=0, y=0, width=600, height=400),
                        resource_keys=("figma-resource-key",),
                    ),
                    SelectionNode(
                        id="figma-text-id",
                        name="Label",
                        type="TEXT",
                        bounds=Bounds(x=10, y=10, width=100, height=20),
                        text="Asset label",
                    ),
                ),
            ),
        ),
    )

    document = selection_conversion_document(manifest, resources, "f" * 64)
    result = convert_document(
        document.raw,
        Path("tests/fixtures/fgui"),
        "Sample",
        tmp_path / "staging",
        Path("rules/default/classification.yaml"),
        selection_assets=document.assets,
    )

    asset = next(item for item in result.files if item.relative_path.endswith(".png"))
    panel = next(item for item in result.files if "/Panel/" in item.relative_path)
    package = next(item for item in result.files if item.relative_path.endswith("package.xml"))
    assert (tmp_path / "staging" / asset.relative_path).read_bytes() == content
    xml = (tmp_path / "staging" / panel.relative_path).read_text("utf-8")
    package_tree = etree.parse(str(tmp_path / "staging" / package.relative_path))
    registered = package_tree.xpath(
        "./resources/image[@name=$name]", name=Path(asset.relative_path).name
    )
    assert len(registered) == 1
    assert registered[0].attrib["path"] == "/assets/"
    assert registered[0].attrib["id"] not in {"img00001", "cmp00001"}
    assert f'src="{registered[0].attrib["id"]}"' in xml
    assert f'fileName="assets/{Path(asset.relative_path).name}"' in xml
    staged_index = index_project(tmp_path / "staging")
    assert staged_index.by_name[Path(asset.relative_path).name].id == registered[0].attrib["id"]
    for raw_identifier in ("figma-node-id", "figma-text-id", "figma-resource-key"):
        assert raw_identifier not in xml
        assert raw_identifier not in "\n".join(item.relative_path for item in result.files)

    repeated = convert_document(
        document.raw,
        Path("tests/fixtures/fgui"),
        "Sample",
        tmp_path / "repeat",
        Path("rules/default/classification.yaml"),
        selection_assets=document.assets,
    )
    assert result == repeated
    assert (tmp_path / "staging" / package.relative_path).read_bytes() == (
        tmp_path / "repeat" / package.relative_path
    ).read_bytes()


def test_same_name_unrelated_package_resource_gets_a_distinct_registration(tmp_path: Path) -> None:
    resources = tmp_path / "selection-resources"
    resources.mkdir()
    content = b"selection-raster"
    (resources / "figma-resource-key").write_bytes(content)
    manifest = SelectionManifest(
        display_name="Asset panel",
        resources=(
            SelectionResource(key="figma-resource-key", mime_type="image/png", size=len(content)),
        ),
        top_level_nodes=(
            SelectionNode(
                id="figma-node-id",
                name="AssetPanel",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=600, height=400),
                children=(
                    SelectionNode(
                        id="figma-image-id",
                        name="Background",
                        type="RECTANGLE",
                        bounds=Bounds(x=0, y=0, width=600, height=400),
                        resource_keys=("figma-resource-key",),
                    ),
                    SelectionNode(
                        id="figma-text-id",
                        name="Label",
                        type="TEXT",
                        bounds=Bounds(x=10, y=10, width=100, height=20),
                        text="Asset label",
                    ),
                ),
            ),
        ),
    )
    document = selection_conversion_document(manifest, resources, "f" * 64)
    original_asset = document.assets[0].asset
    project = tmp_path / "project"
    package = project / "Sample"
    (package / "assets").mkdir(parents=True)
    unrelated = b"unrelated-raster"
    (package / "assets" / f"{original_asset}.png").write_bytes(unrelated)
    (package / "package.xml").write_text(
        "<package id='sample'><resources>"
        f"<image id='unrelated' name='{original_asset}.png' path='/assets/' exported='true'/>"
        "</resources></package>",
        "utf-8",
    )

    result = convert_document(
        document.raw,
        project,
        "Sample",
        tmp_path / "staging",
        Path("rules/default/classification.yaml"),
        selection_assets=document.assets,
    )

    package_xml = tmp_path / "staging" / "Sample" / "package.xml"
    tree = etree.parse(str(package_xml))
    images = tree.xpath("./resources/image")
    old = next(item for item in images if item.attrib["id"] == "unrelated")
    new = next(item for item in images if item.attrib["id"] != "unrelated")
    panel = next(item for item in result.files if "/Panel/" in item.relative_path)
    panel_xml = (tmp_path / "staging" / panel.relative_path).read_text("utf-8")
    generated_asset = next(item for item in result.files if item.relative_path.endswith(".png"))

    assert old.attrib == {
        "id": "unrelated",
        "name": f"{original_asset}.png",
        "path": "/assets/",
        "exported": "true",
    }
    assert new.attrib["name"] != f"{original_asset}.png"
    assert new.attrib["name"].startswith(f"{original_asset}_")
    assert len(new.attrib["name"].removesuffix(".png").removeprefix(f"{original_asset}_")) == 16
    assert (tmp_path / "staging" / generated_asset.relative_path).read_bytes() == content
    assert generated_asset.relative_path.endswith(new.attrib["name"])
    assert f'src="{new.attrib["id"]}"' in panel_xml
    assert f'fileName="assets/{new.attrib["name"]}"' in panel_xml
    assert (package / "assets" / f"{original_asset}.png").read_bytes() == unrelated
    reindexed = index_project(tmp_path / "staging")
    assert reindexed.by_name[new.attrib["name"]].id == new.attrib["id"]


def test_selection_document_composes_directly_without_public_path_context(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    content = b"resource"
    source = resources / "figma-resource-key"
    source.write_bytes(content)
    manifest = SelectionManifest(
        display_name="Composition",
        resources=(
            SelectionResource(key="figma-resource-key", mime_type="image/png", size=len(content)),
        ),
        top_level_nodes=(
            SelectionNode(
                id="figma-node-id",
                name="Panel",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=600, height=400),
                children=(
                    SelectionNode(
                        id="figma-image-id",
                        name="Background",
                        type="RECTANGLE",
                        bounds=Bounds(x=0, y=0, width=600, height=400),
                        resource_keys=("figma-resource-key",),
                    ),
                    SelectionNode(
                        id="figma-text-id",
                        name="Label",
                        type="TEXT",
                        bounds=Bounds(x=10, y=10, width=100, height=20),
                        text="Asset label",
                    ),
                ),
            ),
        ),
    )
    document = selection_document(manifest, resources)

    assert not hasattr(document, "selection_assets")

    result = convert_document(
        document,
        Path("tests/fixtures/fgui"),
        "Sample",
        tmp_path / "staging",
        Path("rules/default/classification.yaml"),
    )

    assert any(item.relative_path.endswith("package.xml") for item in result.files)
    assert any(item.relative_path.endswith(".png") for item in result.files)
    assert str(source) not in json.dumps(document)
    assert "figma-resource-key" not in json.dumps(document)
    assert any(
        item.relative_path.endswith(".png")
        for item in convert_document(
            document.copy(),
            Path("tests/fixtures/fgui"),
            "Sample",
            tmp_path / "copied",
            Path("rules/default/classification.yaml"),
        ).files
    )
    with pytest.raises(ValueError, match="selection asset reference"):
        convert_document(
            json.loads(json.dumps(document)),
            Path("tests/fixtures/fgui"),
            "Sample",
            tmp_path / "serialized",
            Path("rules/default/classification.yaml"),
        )


def test_convert_document_rejects_unknown_package_before_staging(tmp_path: Path) -> None:
    raw = {
        "id": "frame",
        "name": "Main",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 600, "height": 400},
        "children": [],
    }

    with pytest.raises(ValueError, match="package"):
        convert_document(
            raw,
            Path("tests/fixtures/fgui"),
            "../outside",
            tmp_path / "staging",
            Path("rules/default/classification.yaml"),
        )

    assert not (tmp_path / "staging").exists()


def test_selection_document_does_not_read_resource_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = tmp_path / "resources"
    resources.mkdir()
    resource = resources / "figma-resource-key"
    resource.write_bytes(b"resource")
    manifest = SelectionManifest(
        display_name="No buffering",
        resources=(SelectionResource(key="figma-resource-key", mime_type="image/png", size=8),),
        top_level_nodes=(
            SelectionNode(
                id="figma-node-id",
                name="Panel",
                type="FRAME",
                bounds=Bounds(x=0, y=0, width=600, height=400),
                resource_keys=("figma-resource-key",),
            ),
        ),
    )
    original = Path.read_bytes

    def no_resource_buffering(path: Path) -> bytes:
        if path == resource:
            pytest.fail("selection adapter must not buffer resource bytes")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", no_resource_buffering)

    raw = selection_document(manifest, resources)

    assert "figma-resource-key" not in str(raw)
    document = selection_conversion_document(manifest, resources, "f" * 64)
    assert document.assets[0].sha256 == sha256(b"resource").hexdigest()


def test_convert_document_rejects_multi_resource_selection_before_staging(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"x")
    second.write_bytes(b"y")
    size = MAX_SELECTION_CONVERSION_BYTES // 2 + 1
    assets = (
        SelectionAsset("asset_first", "image/png", first, size, sha256(b"x").hexdigest(), "f" * 64),
        SelectionAsset(
            "asset_second", "image/png", second, size, sha256(b"y").hexdigest(), "f" * 64
        ),
    )
    raw = {
        "id": "frame",
        "name": "Main",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 600, "height": 400},
        "children": [],
    }

    with pytest.raises(ConversionLimitError, match="too large"):
        convert_document(
            raw,
            Path("tests/fixtures/fgui"),
            "Sample",
            tmp_path / "staging",
            Path("rules/default/classification.yaml"),
            selection_assets=assets,
        )

    assert not (tmp_path / "staging").exists()
