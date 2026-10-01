from typing import Literal, cast

import pytest

from figma_to_fgui.component_mapping import (
    ComponentMapping,
    ComponentMappingCatalog,
    FguiMappingTarget,
    FigmaMappingTarget,
    LegacyMappingHint,
    ResolvedMappingTarget,
)
from figma_to_fgui.fgui_capabilities import analyze_text_runs, decision_for_node
from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_validate import validate_fgui_plan
from figma_to_fgui.models import Bounds, NormalizedNode, NormalizedResourceReference
from figma_to_fgui.uir_compile import compile_uir
from figma_to_fgui.uir_models import ConversionMode
from figma_to_fgui.uir_validate import canonical_uir_bytes, validate_uir


def sample_roots() -> tuple[NormalizedNode, ...]:
    return (
        NormalizedNode(
            id="frame",
            name="村庄升阶",
            type="FRAME",
            bounds=Bounds(x=100, y=200, width=1080, height=1923),
            children=(
                NormalizedNode(
                    id="title",
                    name="标题",
                    type="TEXT",
                    bounds=Bounds(x=130, y=233, width=239, height=40),
                    text="Village Ascend",
                    source_order=0,
                    raw_style={"fontSize": 36, "textAlignHorizontal": "LEFT"},
                ),
            ),
        ),
    )


def test_compile_preserves_order_source_facts_and_resolved_geometry() -> None:
    document = compile_uir(
        sample_roots(), source_revision="a" * 64, selection_id="selection_village"
    )
    root = document.nodes[document.roots[0]]
    child = document.nodes[root.children[0]]
    assert root.source.name == "村庄升阶"
    assert child.source.name == "标题"
    assert child.geometry.resolved_bounds == Bounds(
        x=30, y=33, width=239, height=40
    )
    assert child.text is not None
    assert child.text.model_dump(mode="json", by_alias=True) == {
        "content": "Village Ascend",
            "style": {
                "fontCandidates": [],
                "fontSize": 36.0,
                "lineHeight": None,
                "color": None,
                "strokeColor": None,
                "strokeSize": None,
                "textAlignHorizontal": "LEFT",
                "textAlignVertical": None,
            },
        "runs": [],
        "baseUnsupportedFeatures": [],
        "fontPolicy": {"allowFallback": True, "resolvedFont": None},
    }


def test_compile_uses_expanded_resource_canvas_for_raster_geometry() -> None:
    raster = NormalizedNode(
        id="shadow-card",
        name="Rounded shadow card",
        type="RECTANGLE",
        bounds=Bounds(x=110, y=210, width=310, height=453),
        properties={
            "export_strategy": "composite_png",
            "raster_reasons": ("visual_effect",),
            "resource_canvas_bounds": {
                "x": 106,
                "y": 206,
                "width": 318,
                "height": 461,
            },
        },
    )
    root = NormalizedNode(
        id="root",
        name="Shop",
        type="FRAME",
        bounds=Bounds(x=100, y=200, width=1080, height=1920),
        children=(raster,),
    )

    document = compile_uir(
        (root,), source_revision="d" * 64, selection_id="expanded-resource-canvas"
    )
    compiled_root = document.nodes[document.roots[0]]
    compiled_raster = document.nodes[compiled_root.children[0]]

    assert compiled_raster.geometry.resolved_bounds == Bounds(
        x=6, y=6, width=318, height=461
    )


def test_plugin_base_text_facts_become_concrete_editable_risk_and_defaults_stay_native() -> None:
    risky = NormalizedNode(
        id="text-risk",
        name="Risky text",
        type="TEXT",
        bounds=Bounds(x=0, y=0, width=120, height=24),
        text="AB",
        properties={
            "line_height": {"unit": "PIXELS", "value": 24},
            "letter_spacing": {"unit": "PIXELS", "value": 2},
            "text_auto_resize": "HEIGHT",
        },
        raw_style={
            "fontSize": 18,
            "runs": (
                {"content": "A", "style": {"fontSize": 18}},
                {"content": "B", "style": {"fontSize": 18, "color": "#ff0000ff"}},
            )
        },
    )
    defaulted = risky.model_copy(
        update={
            "id": "text-default",
            "properties": {
                "line_height": {"unit": "AUTO"},
                "letter_spacing": {"unit": "PIXELS", "value": 0},
                "text_auto_resize": "NONE",
            },
        }
    )

    risky_document = compile_uir(
        (risky,), source_revision="b" * 64, selection_id="plugin-base-risk"
    )
    default_document = compile_uir(
        (defaulted,), source_revision="c" * 64, selection_id="plugin-base-default"
    )
    risky_node = risky_document.nodes[risky_document.roots[0]]
    default_node = default_document.nodes[default_document.roots[0]]
    risky_decision = decision_for_node(risky_node, risky_document)
    default_decision = decision_for_node(default_node, default_document)

    assert risky_decision.rule_id == "fgui.text.style_unsupported"
    assert risky_decision.reasons == ("text_style_properties",)
    assert risky_decision.evidence == (
        "text.runs.count=2",
        "text.runs.preserved=content",
        "text.runs.unsupported=letter_spacing",
    )
    assert risky_node.text is not None
    assert risky_node.text.style.line_height == 24
    assert risky_node.text.style.auto_resize == "HEIGHT"
    assert default_decision.rule_id == "fgui.native.rich_text"


def test_compile_is_deterministic_for_the_same_inputs() -> None:
    first = compile_uir(
        sample_roots(), source_revision="a" * 64, selection_id="same"
    )
    second = compile_uir(
        sample_roots(), source_revision="a" * 64, selection_id="same"
    )
    assert first == second
    assert first.document_id == second.document_id


def test_compile_preserves_child_order_not_source_order_sorting() -> None:
    root = sample_roots()[0]
    later = root.children[0].model_copy(update={"id": "later", "source_order": 99})
    earlier = root.children[0].model_copy(update={"id": "earlier", "source_order": 0})
    document = compile_uir(
        (root.model_copy(update={"children": (later, earlier)}),),
        source_revision="a" * 64,
        selection_id="ordered",
    )
    compiled_root = document.nodes[document.roots[0]]
    assert [document.nodes[item].source.node_id for item in compiled_root.children] == [
        "later",
        "earlier",
    ]


def mapping_catalog(status: str) -> ComponentMappingCatalog:
    resolved = (
        ResolvedMappingTarget(
            package_id="qil5i1mk",
            component_id="v27f1nupomj",
            relative_path="assets/Common/Core/Button/Common_Btn_Primary.xml",
        )
        if status == "verified"
        else None
    )
    component = ComponentMapping(
        key="common_primary_button",
        figma=FigmaMappingTarget(names=("通用一级按钮",)),
        fgui=FguiMappingTarget(
            package="Common",
            component="Common_Btn_Primary",
            path="Core/Button/Common_Btn_Primary.xml",
        ),
        legacyHint=LegacyMappingHint(
            packageId="qil5i1mk", componentId="v27f1nupomj"
        ),
        source=("figma-to-fgui", "auto-panel"),
        status=cast(Literal["candidate", "verified", "missing", "conflict"], status),
        resolved=resolved,
        reason=None if status == "verified" else f"fixture_{status}",
    )
    return ComponentMappingCatalog(
        schemaVersion=1, sources=("figma-to-fgui", "auto-panel"), components=(component,)
    )


def instance_roots() -> tuple[NormalizedNode, ...]:
    return (
        NormalizedNode(
            id="button",
            name="通用一级按钮",
            type="INSTANCE",
            bounds=Bounds(x=0, y=0, width=300, height=80),
        ),
    )


def compile_with_status(status: str):
    return compile_uir(
        instance_roots(),
        source_revision="a" * 64,
        selection_id=f"selection_{status}",
        mapping_catalog=mapping_catalog(status),
    )


def test_verified_candidate_creates_engine_neutral_component_decision() -> None:
    document = compile_with_status("verified")
    node = next(item for item in document.nodes.values() if item.source.type == "INSTANCE")
    assert node.semantic.decision_ref is not None
    decision = document.mapping_decisions[node.semantic.decision_ref]
    assert decision.status == "verified"
    assert decision.node_ref == node.id
    assert decision.candidate_key == "common_primary_button"
    assert node.conversion.mode == "componentReference"
    encoded = document.model_dump_json(by_alias=True)
    assert "qil5i1mk" not in encoded
    assert "v27f1nupomj" not in encoded


def test_verified_mapping_cannot_override_blocking_instance_behavior() -> None:
    instance = instance_roots()[0].model_copy(
        update={"properties": {"interactions": {"present": True}}}
    )

    document = compile_uir(
        (instance,),
        source_revision="a" * 64,
        selection_id="interactive-instance",
        mapping_catalog=mapping_catalog("verified"),
    )
    node = document.nodes[document.roots[0]]
    plan = compile_fgui_plan(document)

    assert node.interactions == ({"present": True},)
    assert node.conversion.mode == "unsupported"
    assert node.conversion.reasons == ("interaction_semantics_out_of_scope",)
    assert document.mapping_decisions == {}
    assert plan.bindable is False
    assert validate_fgui_plan(plan) == ()


def test_missing_candidate_explicitly_falls_back_but_conflict_stays_blocking() -> None:
    missing = compile_with_status("missing")
    conflict = compile_with_status("conflict")
    missing_node = next(iter(missing.nodes.values()))
    conflict_node = next(iter(conflict.nodes.values()))
    assert missing_node.conversion.mode == "rasterFallback"
    assert conflict_node.conversion.mode == "unsupported"
    assert conflict_node.semantic.decision_ref is not None
    assert (
        conflict.mapping_decisions[conflict_node.semantic.decision_ref].status
        == "conflict"
    )


def test_unvalidated_candidate_catalog_is_rejected() -> None:
    with pytest.raises(ValueError, match="validated"):
        compile_with_status("candidate")


def test_ambiguous_component_mappings_block_without_guessing_a_candidate() -> None:
    first = mapping_catalog("verified").components[0]
    second = first.model_copy(
        update={
            "key": "other_button",
            "fgui": first.fgui.model_copy(update={"component": "OtherButton"}),
        }
    )
    catalog = ComponentMappingCatalog(
        schemaVersion=1,
        sources=("fixture",),
        components=(first, second),
    )
    asset = NormalizedResourceReference(
        asset="asset_button",
        mimeType="image/png",
        sha256="d" * 64,
    )
    instance = instance_roots()[0].model_copy(
        update={
            "properties": {
                "export_strategy": "composite_png",
                "raster_reasons": ("instance_composite",),
            },
            "resource_refs": (asset,),
        }
    )

    document = compile_uir(
        (instance,),
        source_revision="a" * 64,
        selection_id="ambiguous-mapping",
        mapping_catalog=catalog,
    )
    node = document.nodes[document.roots[0]]
    plan = compile_fgui_plan(document)

    assert node.conversion.mode == "unsupported"
    assert node.conversion.reasons == ("component_mapping_ambiguous",)
    assert document.mapping_decisions == {}
    assert plan.bindable is False
    assert validate_fgui_plan(plan) == ()


def test_compile_allowlists_text_layout_and_visual_facts_without_private_data() -> None:
    root = NormalizedNode(
        id="text",
        name="Title",
        type="TEXT",
        bounds=Bounds(x=0, y=0, width=200, height=40),
        text="Title",
        properties={
            "layout_mode": "HORIZONTAL",
            "item_spacing": 8,
            "unknown_private": {"accessToken": "property-secret"},
        },
        raw_style={
            "font": {"family": "Inter", "style": "Bold"},
            "fontSize": 20,
            "textAlignHorizontal": "CENTER",
            "apiKey": "style-secret",
            "localPath": "C:\\private\\font.ttf",
        },
    )

    document = compile_uir(
        (root,), source_revision="a" * 64, selection_id="allowlisted"
    )
    node = document.nodes[document.roots[0]]
    encoded = canonical_uir_bytes(document)

    assert node.layout == {
        "itemSpacing": 8,
        "layoutMode": "HORIZONTAL",
        "sourceOrder": 0,
        "visible": True,
    }
    assert node.visual == {}
    assert node.text is not None
    assert node.text.style.font_candidates == ("Inter Bold", "Inter")
    assert node.text.style.font_size == 20
    assert node.text.style.horizontal_align == "CENTER"
    assert b"style-secret" not in encoded
    assert b"property-secret" not in encoded
    assert b"C:\\private" not in encoded
    assert validate_uir(document) == ()


def test_compile_preserves_extractable_run_style_facts_and_registers_font_weight() -> None:
    solid_black = [{"type": "SOLID", "color": {"r": 0, "g": 0, "b": 0, "a": 1}}]
    root = NormalizedNode(
        id="text-runs",
        name="Text runs",
        type="TEXT",
        bounds=Bounds(x=0, y=0, width=200, height=40),
        text="Buy now",
        raw_style={
            "fontSize": 20,
            "textAlignHorizontal": "CENTER",
            "strokes": solid_black,
            "strokeWeight": 2,
            "runs": [
                {
                    "content": "Buy ",
                    "style": {
                        "fontSize": 20,
                        "textAlignHorizontal": "CENTER",
                        "strokes": solid_black,
                        "strokeWeight": 2,
                    },
                },
                {
                    "content": "now",
                    "style": {
                        "fontSize": 20,
                        "textAlignHorizontal": "CENTER",
                        "strokes": solid_black,
                        "strokeWeight": 2,
                        "fontWeight": 700,
                    },
                },
            ],
        },
    )

    document = compile_uir(
        (root,), source_revision="a" * 64, selection_id="extractable-run-facts"
    )
    node = document.nodes[document.roots[0]]
    assert node.text is not None

    assert node.text.runs[0].style.horizontal_align == "CENTER"
    assert node.text.runs[0].style.stroke_color == "#000000ff"
    assert node.text.runs[0].style.stroke_size == 2
    assert node.text.runs[1].unsupported_features == ("fontWeight",)
    assert analyze_text_runs(node).unsupported_properties == ("fontWeight",)
    decision = decision_for_node(node, document)
    assert decision.evidence[-1] == "text.runs.unsupported=fontWeight"


def _compile_weight_run(second_style: dict[str, object]):
    root = NormalizedNode(
        id="weight-runs",
        name="Weight runs",
        type="TEXT",
        bounds=Bounds(x=0, y=0, width=200, height=40),
        text="Buy now",
        raw_style={
            "fontSize": 20,
            "fontWeight": 700,
            "runs": [
                {"content": "Buy ", "style": {"fontSize": 20}},
                {"content": "now", "style": second_style},
            ],
        },
    )
    document = compile_uir(
        (root,), source_revision="a" * 64, selection_id="weight-run-inheritance"
    )
    return document.nodes[document.roots[0]]


def test_omitted_run_weight_inherits_explicit_base_weight_for_native_color_runs() -> None:
    node = _compile_weight_run({"fontSize": 20, "color": "#ff0000"})

    assert node.text is not None
    assert node.text.runs[0].unsupported_features == ()
    assert node.text.runs[1].unsupported_features == ()
    assert analyze_text_runs(node).kind == "native-rich-text"


def test_explicitly_equal_run_weight_has_no_editable_risk() -> None:
    node = _compile_weight_run({"fontSize": 20, "fontWeight": 700})

    assert node.text is not None
    assert node.text.runs[1].unsupported_features == ()
    assert analyze_text_runs(node).kind == "native-rich-text"


def test_explicitly_different_run_weight_is_a_concrete_editable_risk() -> None:
    node = _compile_weight_run({"fontSize": 20, "fontWeight": 400})

    assert node.text is not None
    assert node.text.runs[1].unsupported_features == ("fontWeight",)
    assert analyze_text_runs(node).unsupported_properties == ("fontWeight",)


def test_rejected_private_visual_fact_blocks_instead_of_becoming_clean_native() -> None:
    root = NormalizedNode(
        id="private-paint",
        name="Private paint",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=100, height=100),
        raw_style={
            "fills": (
                {
                    "type": "SOLID",
                    "color": {"r": 1, "g": 0, "b": 0},
                    "token": "private-token",
                },
            )
        },
    )

    document = compile_uir(
        (root,), source_revision="a" * 64, selection_id="private-paint"
    )
    node = document.nodes[document.roots[0]]
    plan = compile_fgui_plan(document)

    assert node.visual == {}
    assert node.conversion.mode == ConversionMode.UNSUPPORTED
    assert node.conversion.reasons == ("private_or_invalid_fact",)
    assert any(item.code == "uir.private_data_forbidden" for item in document.diagnostics)
    assert plan.bindable is False
    assert b"private-token" not in canonical_uir_bytes(document)


@pytest.mark.parametrize(
    ("feature_key", "reason"),
    [
        ("interactions", "interaction_semantics_out_of_scope"),
        ("list_items", "list_semantics_out_of_scope"),
        ("controller", "controller_semantics_out_of_scope"),
        ("gear_display", "gear_semantics_out_of_scope"),
    ],
)
def test_normalized_out_of_scope_behavior_is_preserved_as_blocking_conversion(
    feature_key: str,
    reason: str,
) -> None:
    root = NormalizedNode(
        id="behavior",
        name="Behavior",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=100, height=100),
        properties={feature_key: {"enabled": True}},
    )

    document = compile_uir(
        (root,), source_revision="a" * 64, selection_id=feature_key
    )
    node = document.nodes[document.roots[0]]

    assert node.conversion.mode == "unsupported"
    assert node.conversion.reasons == (reason,)


def test_multiple_direct_resources_block_and_shared_logical_resources_deduplicate() -> None:
    first_ref = NormalizedResourceReference(
        asset="asset_shared",
        mimeType="image/png",
        sha256="a" * 64,
        width=100,
        height=100,
    )
    second_ref = NormalizedResourceReference(
        asset="asset_other",
        mimeType="image/png",
        sha256="b" * 64,
    )
    ambiguous = NormalizedNode(
        id="ambiguous",
        name="Ambiguous",
        type="RECTANGLE",
        bounds=Bounds(x=0, y=0, width=100, height=100),
        resource_refs=(first_ref, second_ref),
    )
    shared_children = tuple(
        NormalizedNode(
            id=f"shared-{index}",
            name="Shared",
            type="RECTANGLE",
            bounds=Bounds(x=index * 100, y=0, width=100, height=100),
            resource_refs=(first_ref,),
        )
        for index in range(2)
    )
    shared_root = NormalizedNode(
        id="root",
        name="Root",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=200, height=100),
        children=shared_children,
    )

    ambiguous_document = compile_uir(
        (ambiguous,), source_revision="a" * 64, selection_id="ambiguous"
    )
    shared_document = compile_uir(
        (shared_root,), source_revision="a" * 64, selection_id="shared"
    )

    ambiguous_node = ambiguous_document.nodes[ambiguous_document.roots[0]]
    assert ambiguous_node.conversion.mode == "unsupported"
    assert ambiguous_node.conversion.reasons == ("multiple_resource_references",)
    assert ambiguous_document.assets == {}
    assert len(shared_document.assets) == 1
    shared_asset_refs = {
        shared_document.nodes[node_id].conversion.asset_ref
        for node_id in shared_document.nodes[shared_document.roots[0]].children
    }
    assert len(shared_asset_refs) == 1


@pytest.mark.parametrize(
    ("source_type", "mime_type", "export_format"),
    [
        ("FRAME", "image/png", "png"),
        ("RECTANGLE", "image/png", "png"),
        ("ELLIPSE", "image/svg+xml", "svg"),
        ("VECTOR", "image/png", "png"),
        ("BOOLEAN_OPERATION", "image/png", "png"),
        ("STAR", "image/png", "png"),
        ("LINE", "image/png", "png"),
        ("POLYGON", "image/png", "png"),
    ],
)
def test_resource_backed_plugin_visual_types_compile_as_native_images(
    source_type: str,
    mime_type: str,
    export_format: str,
) -> None:
    reference = NormalizedResourceReference(
        asset=f"asset_{source_type.casefold()}",
        mimeType=mime_type,
        sha256="d" * 64,
        exportFormat=export_format,
    )
    normalized = NormalizedNode(
        id="visual",
        name="Visual",
        type=source_type,
        bounds=Bounds(x=0, y=0, width=100, height=80),
        properties={
            "export_strategy": (
                "vector_asset"
                if source_type
                in {"VECTOR", "BOOLEAN_OPERATION", "STAR", "LINE", "POLYGON"}
                else "image_asset"
                if mime_type == "image/png"
                else "vector_asset"
            )
        },
        resource_refs=(reference,),
    )

    document = compile_uir(
        (normalized,), source_revision="a" * 64, selection_id=source_type
    )
    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert next(iter(plan.nodes.values())).type == "image"
    assert validate_fgui_plan(plan) == ()


def test_asset_backed_leaf_absorbs_baked_transform_but_not_interactions() -> None:
    reference = NormalizedResourceReference(
        asset="asset_vector",
        mimeType="image/png",
        sha256="d" * 64,
        width=81,
        height=64,
        exportFormat="png",
    )
    transformed = NormalizedNode(
        id="vector",
        name="Vector",
        type="BOOLEAN_OPERATION",
        bounds=Bounds(x=145, y=260, width=81, height=64),
        properties={"export_strategy": "vector_asset"},
        raw_style={"relative_transform": ((0.8, 0.2, 145), (-0.2, 0.8, 260))},
        resource_refs=(reference,),
    )

    document = compile_uir(
        (transformed,), source_revision="a" * 64, selection_id="vector-transform"
    )
    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert next(iter(plan.nodes.values())).type == "image"
    assert validate_fgui_plan(plan) == ()

    interactive = transformed.model_copy(
        update={
            "properties": {
                **transformed.properties,
                "interactions": ({"trigger": "ON_CLICK"},),
            }
        }
    )
    blocked = compile_fgui_plan(
        compile_uir(
            (interactive,),
            source_revision="a" * 64,
            selection_id="vector-interaction",
        )
    )
    assert blocked.bindable is False
    assert {item.code for item in blocked.diagnostics} >= {
        "fgui.unsupported.interaction"
    }


def test_component_property_labels_are_not_mistaken_for_runtime_features() -> None:
    normalized = NormalizedNode(
        id="frame",
        name="Frame",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=100, height=80),
        properties={
            "component_properties": {
                "List": False,
                "Controller": "None",
                "Gear": "",
                "Interactions": 0,
            }
        },
    )

    document = compile_uir(
        (normalized,), source_revision="a" * 64, selection_id="labels"
    )
    plan = compile_fgui_plan(document)

    assert document.nodes[document.roots[0]].conversion.mode == ConversionMode.NATIVE
    assert plan.bindable is True
    assert validate_fgui_plan(plan) == ()


def test_deep_normalized_tree_is_rejected_before_recursive_compilation() -> None:
    node = NormalizedNode(
        id="leaf",
        name="Leaf",
        type="FRAME",
        bounds=Bounds(x=0, y=0, width=1, height=1),
    )
    for index in range(300):
        node = NormalizedNode(
            id=f"node-{index}",
            name="Node",
            type="FRAME",
            bounds=Bounds(x=0, y=0, width=1, height=1),
            children=(node,),
        )

    with pytest.raises(ValueError, match="tree depth"):
        compile_uir(
            (node,), source_revision="a" * 64, selection_id="too-deep"
        )
