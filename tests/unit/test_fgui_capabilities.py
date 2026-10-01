import pytest

from figma_to_fgui import fgui_capabilities
from figma_to_fgui.fgui_capabilities import analyze_capabilities, decision_for_node
from figma_to_fgui.fgui_plan_models import CapabilityDecision
from figma_to_fgui.models import Bounds
from figma_to_fgui.uir_models import (
    ConversionMode,
    MappingStatus,
    UIRAsset,
    UIRConversion,
    UIRDocument,
    UIRGeometry,
    UIRMappingDecision,
    UIRNode,
    UIRNodeSource,
    UIRSemantic,
    UIRSource,
)


def _node(
    source_type: str,
    *,
    node_id: str = "node:fixture",
    name: str = "Node",
    width: float = 100,
    conversion: UIRConversion | None = None,
    decision_ref: str | None = None,
    parent_id: str | None = None,
    children: tuple[str, ...] = (),
    visual: dict[str, object] | None = None,
    layout: dict[str, object] | None = None,
    interactions: tuple[dict[str, object], ...] = (),
    text: dict[str, object] | None = None,
    semantic_role: str | None = None,
    bounds: Bounds | None = None,
) -> UIRNode:
    return UIRNode(
        id=node_id,
        source=UIRNodeSource(
            nodeId=node_id.removeprefix("node:"),
            type=source_type,
            name=name,
            fingerprint="b" * 64,
        ),
        semantic=UIRSemantic(decisionRef=decision_ref, role=semantic_role),
        parentId=parent_id,
        children=children,
        zIndex=0,
        geometry=UIRGeometry(
            resolvedBounds=bounds or Bounds(x=0, y=0, width=width, height=100)
        ),
        layout=layout or {},
        visual=visual or {},
        interactions=interactions,
        text=text,
        conversion=conversion or UIRConversion(mode=ConversionMode.NATIVE),
    )


def _document(
    node: UIRNode,
    *,
    assets: dict[str, UIRAsset] | None = None,
    mapping_decisions: dict[str, UIRMappingDecision] | None = None,
) -> UIRDocument:
    return UIRDocument(
        documentId="uir:fixture",
        compilerVersion="uir-v1",
        source=UIRSource(revision="a" * 64, selectionId="selection"),
        roots=(node.id,),
        nodes={node.id: node},
        assets=assets or {},
        mappingDecisions=mapping_decisions or {},
    )


@pytest.mark.parametrize("source_type", ["INSTANCE", "VECTOR"])
def test_hidden_opaque_visual_node_does_not_hide_a_missing_conversion(
    source_type: str,
) -> None:
    node = _node(
        source_type,
        layout={"visible": False},
        visual={"effects": ({"type": "DROP_SHADOW"},)},
    )

    decision = analyze_capabilities(_document(node))[node.id]

    assert decision.status.value == "unsupported"
    assert decision.rule_id == "fgui.unsupported.visual_style"


def _text_node(
    *,
    content: str = "Buy now",
    base_size: float = 20,
    base_font_candidates: list[str] | None = None,
    base_stroke_color: str | None = None,
    base_stroke_size: float | None = None,
    base_horizontal_align: str | None = None,
    base_vertical_align: str | None = None,
    runs: list[dict[str, object]],
) -> UIRNode:
    return _node(
        "TEXT",
        text={
            "content": content,
            "style": {
                "fontSize": base_size,
                "fontCandidates": base_font_candidates or [],
                "strokeColor": base_stroke_color,
                "strokeSize": base_stroke_size,
                "textAlignHorizontal": base_horizontal_align,
                "textAlignVertical": base_vertical_align,
            },
            "runs": runs,
        },
    )


def test_color_only_runs_are_native_but_size_and_font_differences_are_reviewable() -> None:
    native = _text_node(
        runs=[
            {"content": "Buy ", "style": {"fontSize": 20}},
            {"content": "now", "style": {"fontSize": 20, "color": "#ff0000"}},
        ]
    )
    sized = _text_node(
        runs=[
            {"content": "Buy ", "style": {"fontSize": 20}},
            {"content": "now", "style": {"fontSize": 24}},
        ]
    )
    different_font = _text_node(
        base_font_candidates=["Inter"],
        runs=[
            {
                "content": "Buy now",
                "style": {"fontSize": 20, "fontCandidates": ["Arial"]},
            }
        ],
    )

    assert fgui_capabilities.analyze_text_runs(native).kind == "native-rich-text"
    assert fgui_capabilities.analyze_text_runs(native).preserved_properties == (
        "content",
        "color",
    )
    assert fgui_capabilities.analyze_text_runs(sized).kind == "editable-risk"
    assert fgui_capabilities.analyze_text_runs(sized).unsupported_properties == (
        "fontSize",
    )
    assert fgui_capabilities.analyze_text_runs(different_font).unsupported_properties == (
        "fontCandidates",
    )


def test_stroke_and_ubb_encoding_risks_are_reviewable() -> None:
    stroke = _text_node(
        runs=[
            {
                "content": "Buy now",
                "style": {"fontSize": 20, "strokeColor": "#ff0000"},
            }
        ]
    )
    ubb = _text_node(
        content="[now]",
        runs=[
            {"content": "[", "style": {"fontSize": 20}},
            {"content": "now]", "style": {"fontSize": 20, "color": "#ff0000"}},
        ],
    )

    assert fgui_capabilities.analyze_text_runs(stroke).unsupported_properties == (
        "strokeColor",
    )
    assert fgui_capabilities.analyze_text_runs(ubb).unsupported_properties == (
        "ubbEncoding",
    )


def test_matching_run_alignment_is_native_but_different_alignment_is_reviewable() -> None:
    matching = _text_node(
        base_horizontal_align="CENTER",
        runs=[
            {
                "content": "Buy now",
                "style": {"fontSize": 20, "textAlignHorizontal": "CENTER"},
            }
        ],
    )
    different = _text_node(
        base_horizontal_align="CENTER",
        runs=[
            {
                "content": "Buy now",
                "style": {"fontSize": 20, "textAlignHorizontal": "RIGHT"},
            }
        ],
    )

    assert fgui_capabilities.analyze_text_runs(matching).kind == "native-rich-text"
    assert fgui_capabilities.analyze_text_runs(different).unsupported_properties == (
        "paragraphAlignment",
    )


def test_run_content_mismatch_is_blocking() -> None:
    valid = _text_node(
        content="Buy",
        runs=[{"content": "Buy", "style": {"fontSize": 20}}],
    )
    assert valid.text is not None
    mismatch = valid.model_copy(
        update={"text": valid.text.model_copy(update={"content": "Buy now"})}
    )

    capability = fgui_capabilities.analyze_text_runs(mismatch)
    decision = decision_for_node(mismatch, _document(mismatch))

    assert capability.kind == "blocked"
    assert "contentClosure" in capability.unsupported_properties
    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.text.runs_content_mismatch"
    assert decision.blocking is True


def test_reviewable_rich_text_decision_has_stable_run_evidence() -> None:
    node = _text_node(
        runs=[
            {"content": "Buy ", "style": {"fontSize": 20}},
            {"content": "now", "style": {"fontSize": 24}},
        ]
    )

    decision = decision_for_node(node, _document(node))

    assert decision.status == "unsupported"
    assert decision.blocking is False
    assert decision.reasons == ("rich_text_runs",)
    assert decision.evidence == (
        "text.runs.count=2",
        "text.runs.preserved=content",
        "text.runs.unsupported=fontSize",
    )


def test_unresolved_required_font_blocks_before_reviewable_rich_text_runs() -> None:
    node = _text_node(
        runs=[
            {"content": "Buy ", "style": {"fontSize": 20}},
            {"content": "now", "style": {"fontSize": 24}},
        ]
    )
    assert node.text is not None
    node = node.model_copy(
        update={
            "text": node.text.model_copy(
                update={
                    "font_policy": node.text.font_policy.model_copy(
                        update={"allow_fallback": False, "resolved_font": None}
                    )
                }
            )
        }
    )

    decision = decision_for_node(node, _document(node))

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.text.font_unresolved"
    assert decision.blocking is True


def document_with_node(source_type: str) -> tuple[UIRNode, UIRDocument]:
    asset_ref = "asset:fixture" if source_type == "RECTANGLE" else None
    node = _node(
        source_type,
        conversion=UIRConversion(mode=ConversionMode.NATIVE, assetRef=asset_ref),
    )
    assets = (
        {asset_ref: UIRAsset(id=asset_ref, logicalId="fixture", mimeType="image/png")}
        if asset_ref is not None
        else {}
    )
    return node, _document(node, assets=assets)


@pytest.mark.parametrize(
    ("source_type", "expected_rule"),
    [
        ("FRAME", "fgui.native.container"),
        ("GROUP", "fgui.native.container"),
        ("TEXT", "fgui.native.text"),
        ("RECTANGLE", "fgui.native.image"),
    ],
)
def test_generic_node_types_use_native_rules(
    source_type: str, expected_rule: str
) -> None:
    node, document = document_with_node(source_type)

    decision = decision_for_node(node, document, 1)

    assert decision.status == "native"
    assert decision.rule_id == expected_rule


def _instance_decision(status: MappingStatus) -> CapabilityDecision:
    mapping = UIRMappingDecision(
        id="decision:mapping",
        nodeRef="node:fixture",
        candidateKey="button",
        status=status,
        confidence=1 if status == MappingStatus.VERIFIED else 0,
        ruleSource="fixture",
        evidence=("fixture.mapping",),
    )
    conversion = (
        UIRConversion(mode=ConversionMode.COMPONENT_REFERENCE)
        if status == MappingStatus.VERIFIED
        else UIRConversion(
            mode=ConversionMode.UNSUPPORTED,
            reasons=("component_mapping_conflict",),
        )
    )
    node = _node("INSTANCE", conversion=conversion, decision_ref=mapping.id)
    return decision_for_node(node, _document(node, mapping_decisions={mapping.id: mapping}), 1)


def test_verified_component_is_native_but_conflict_is_blocking() -> None:
    verified = _instance_decision(MappingStatus.VERIFIED)
    conflict = _instance_decision(MappingStatus.CONFLICT)

    assert verified.rule_id == "fgui.native.component_reference"
    assert conflict.status == "unsupported"
    assert conflict.blocking is True


def _raster_decision(asset: bool) -> CapabilityDecision:
    asset_ref = "asset:raster"
    node = _node(
        "FRAME",
        conversion=UIRConversion(
            mode=ConversionMode.RASTER_FALLBACK,
            reasons=("composite_visual",),
            assetRef=asset_ref,
        ),
    )
    assets = (
        {asset_ref: UIRAsset(id=asset_ref, logicalId="raster", mimeType="image/png")}
        if asset
        else {}
    )
    return decision_for_node(node, _document(node, assets=assets), 1)


def test_explicit_raster_conversion_requires_an_asset() -> None:
    assert _raster_decision(asset=True).status == "rasterFallback"
    missing = _raster_decision(asset=False)

    assert missing.status == "unsupported"
    assert "raster_asset_missing" in missing.reasons


def test_rule_result_is_independent_of_name_and_dimensions() -> None:
    first = _node("FRAME", name="Village", width=1080)
    second = _node("FRAME", name="Inventory", width=750)

    first_decision = decision_for_node(first, _document(first), 1)
    second_decision = decision_for_node(second, _document(second), 1)

    assert (first_decision.status, first_decision.rule_id) == (
        second_decision.status,
        second_decision.rule_id,
    )


def test_analysis_uses_node_keys_in_sorted_order() -> None:
    first = _node("FRAME").model_copy(update={"id": "node:z"})
    second = _node("TEXT").model_copy(update={"id": "node:a"})
    document = _document(first).model_copy(update={"nodes": {first.id: first, second.id: second}})

    decisions = analyze_capabilities(document)

    assert list(decisions) == ["node:a", "node:z"]


@pytest.mark.parametrize(
    ("node", "expected_rule"),
    [
        (
            _node("FRAME", interactions=({"event": "click"},)),
            "fgui.unsupported.interaction",
        ),
        (_node("FRAME", semantic_role="list"), "fgui.unsupported.list"),
        (
            _node("FRAME", semantic_role="controller"),
            "fgui.unsupported.controller",
        ),
        (_node("FRAME", semantic_role="gear"), "fgui.unsupported.gear"),
        (
            _node("FRAME", visual={"controller": {"name": "state"}}),
            "fgui.unsupported.controller",
        ),
        (
            _node("FRAME", visual={"gear": {"property": "xy"}}),
            "fgui.unsupported.gear",
        ),
    ],
)
def test_out_of_scope_features_block_before_source_type_defaults(
    node: UIRNode, expected_rule: str
) -> None:
    decision = decision_for_node(node, _document(node))

    assert decision.status == "unsupported"
    assert decision.rule_id == expected_rule
    assert decision.blocking is True
    assert decision.evidence


def test_no_wrap_auto_layout_defaults_do_not_trigger_the_complex_layout_gate() -> None:
    node = _node(
        "FRAME",
        layout={
            "layoutMode": "HORIZONTAL",
            "layoutWrap": "NO_WRAP",
            "counterAxisSpacing": 0,
        },
    )

    decision = decision_for_node(node, _document(node))

    assert decision.status == "native"
    assert decision.rule_id == "fgui.native.container"


def test_expressible_text_runs_are_rich_text_but_unsupported_runs_are_reviewable() -> None:
    expressible = _node(
        "TEXT",
        text={
            "content": "Buy now",
            "style": {"fontSize": 20},
            "runs": [
                {"content": "Buy ", "style": {"fontSize": 20}},
                {"content": "now", "style": {"fontSize": 20, "color": "#ff0000"}},
            ],
        },
    )
    unsupported = _node(
        "TEXT",
        text={
            "content": "Warped",
            "runs": [
                {
                    "content": "Warped",
                    "style": {"fontSize": 20},
                    "unsupportedFeatures": ["perGlyphTransform"],
                }
            ],
        },
    )

    rich = decision_for_node(expressible, _document(expressible))
    reviewable = decision_for_node(unsupported, _document(unsupported))

    assert rich.status == "native"
    assert rich.rule_id == "fgui.native.rich_text"
    assert reviewable.status == "unsupported"
    assert reviewable.rule_id == "fgui.text.runs_unsupported"
    assert reviewable.blocking is False


def test_font_policy_failure_is_blocking() -> None:
    node = _node(
        "TEXT",
        text={
            "content": "Unavailable font",
            "style": {"fontCandidates": ["Unavailable"]},
            "fontPolicy": {"allowFallback": False, "resolvedFont": None},
        },
    )

    decision = decision_for_node(node, _document(node))

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.text.font_unresolved"
    assert decision.blocking is True


def test_whitespace_resolved_font_cannot_bypass_required_resolution() -> None:
    node = _node(
        "TEXT",
        text={
            "content": "Unavailable font",
            "fontPolicy": {"allowFallback": False, "resolvedFont": "Inter"},
        },
    )
    assert node.text is not None
    text = node.text.model_copy(
        update={
            "font_policy": node.text.font_policy.model_copy(
                update={"resolved_font": " "}
            )
        }
    )
    node = node.model_copy(update={"text": text})

    decision = decision_for_node(node, _document(node))

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.text.font_unresolved"


@pytest.mark.parametrize(
    ("text", "expected_rule", "expected_blocking"),
    [
        (
            {
                "content": "Unsafe",
                "runs": [
                    {
                        "content": "Unsafe",
                        "unsupportedFeatures": ["text_decoration"],
                    }
                ],
            },
            "fgui.text.runs_unsupported",
            False,
        ),
        (
            {
                "content": "Unavailable",
                "fontPolicy": {"allowFallback": False, "resolvedFont": None},
            },
            "fgui.text.font_unresolved",
            True,
        ),
    ],
)
def test_native_text_safety_precedes_resource_defaults(
    text: dict[str, object],
    expected_rule: str,
    expected_blocking: bool,
) -> None:
    node = _node("TEXT", text=text, conversion=UIRConversion(mode=ConversionMode.NATIVE, assetRef="asset:text"))
    asset = UIRAsset(
        id="asset:text",
        logicalId="text",
        mimeType="image/png",
        sha256="c" * 64,
    )

    decision = decision_for_node(node, _document(node, assets={asset.id: asset}))

    assert decision.status == "unsupported"
    assert decision.rule_id == expected_rule
    assert decision.blocking is expected_blocking


def test_explicit_raster_fallback_absorbs_visual_transform() -> None:
    asset = UIRAsset(id="asset:raster", logicalId="raster", mimeType="image/png", sha256="c" * 64)
    raster = UIRConversion(mode=ConversionMode.RASTER_FALLBACK, reasons=("visual_fallback",), assetRef=asset.id)
    transformed = _node("BOOLEAN_OPERATION", conversion=raster).model_copy(
        update={"geometry": UIRGeometry(resolvedBounds=Bounds(x=0, y=0, width=10, height=10), localTransform=(1, 0.25, 0, 1, 0, 0))}
    )
    decision = decision_for_node(transformed, _document(transformed, assets={asset.id: asset}))

    assert decision.status == "rasterFallback"
    assert decision.rule_id == "fgui.fallback.raster_subtree"
    assert decision.blocking is False


def test_readable_instance_ignores_its_container_transform() -> None:
    readable = _node(
        "INSTANCE",
        children=("node:child",),
        visual={"relativeTransform": ((1, 0.25, 0), (0, 1, 0))},
    ).model_copy(
        update={
            "geometry": UIRGeometry(
                resolvedBounds=Bounds(x=0, y=0, width=100, height=100),
                localTransform=(1, 0.25, 0, 1, 0, 0),
            )
        }
    )

    decision = decision_for_node(readable, _document(readable))

    assert decision.status == "native"
    assert decision.rule_id == "fgui.native.container"
    assert decision.blocking is False


@pytest.mark.parametrize(
    "source_type", ["FRAME", "GROUP", "COMPONENT", "SECTION", "INSTANCE"]
)
def test_readable_structural_container_ignores_its_container_transform(
    source_type: str,
) -> None:
    readable = _node(source_type, children=("node:child",)).model_copy(
        update={
            "geometry": UIRGeometry(
                resolvedBounds=Bounds(x=0, y=0, width=100, height=100),
                localTransform=(1, 0.25, 0, 1, 0, 0),
            )
        }
    )

    decision = decision_for_node(readable, _document(readable))

    assert decision.status == "native"
    assert decision.rule_id == "fgui.native.container"
    assert decision.blocking is False


@pytest.mark.parametrize(
    "source_type", ["FRAME", "GROUP", "COMPONENT", "SECTION", "INSTANCE"]
)
def test_readable_structural_container_ignores_its_container_visual_style(
    source_type: str,
) -> None:
    readable = _node(
        source_type,
        children=("node:child",),
        visual={"effects": ({"type": "DROP_SHADOW"},)},
    )

    decision = decision_for_node(readable, _document(readable))

    assert decision.status == "native"
    assert decision.rule_id == "fgui.native.container"
    assert decision.blocking is False


def test_opaque_instance_still_rejects_an_unrepresentable_transform() -> None:
    opaque = _node("INSTANCE").model_copy(
        update={
            "geometry": UIRGeometry(
                resolvedBounds=Bounds(x=0, y=0, width=100, height=100),
                localTransform=(1, 0.25, 0, 1, 0, 0),
            )
        }
    )

    decision = decision_for_node(opaque, _document(opaque))

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.unsupported.transform"


def test_auto_layout_uses_native_static_container_geometry() -> None:
    node = _node(
        "FRAME",
        layout={
            "layoutMode": "HORIZONTAL",
            "primaryAxisSizingMode": "AUTO",
        },
    )

    decision = decision_for_node(node, _document(node))

    assert decision.status == "native"
    assert decision.rule_id == "fgui.native.container"
    assert decision.blocking is False


def test_explicit_raster_fallback_keeps_reviewable_rich_text_evidence() -> None:
    asset = UIRAsset(id="asset:raster", logicalId="raster", mimeType="image/png", sha256="c" * 64)
    raster = UIRConversion(mode=ConversionMode.RASTER_FALLBACK, reasons=("visual_fallback",), assetRef=asset.id)
    rich_text = _node(
        "TEXT",
        node_id="node:text-raster",
        conversion=raster,
        text={"content": "AB", "runs": [{"content": "AB", "unsupportedFeatures": ["text_decoration"]}]},
    )

    decision = decision_for_node(rich_text, _document(rich_text, assets={asset.id: asset}))

    assert decision.status == "unsupported"
    assert decision.blocking is False
    assert decision.reasons == ("rich_text_runs",)
    assert decision.evidence[-1] == "text.runs.unsupported=text_decoration"


def test_raster_fallback_never_hides_interaction_behavior() -> None:
    asset = UIRAsset(id="asset:raster", logicalId="raster", mimeType="image/png", sha256="c" * 64)
    node = _node(
        "FRAME",
        conversion=UIRConversion(mode=ConversionMode.RASTER_FALLBACK, reasons=("visual_fallback",), assetRef=asset.id),
        interactions=({"trigger": "ON_CLICK"},),
    )

    decision = decision_for_node(node, _document(node, assets={asset.id: asset}))

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.unsupported.interaction"


def test_every_automatic_capability_decision_has_stable_evidence() -> None:
    for source_type in ("FRAME", "TEXT", "RECTANGLE"):
        node, document = document_with_node(source_type)
        first = decision_for_node(node, document)
        second = decision_for_node(node, document)
        assert first.evidence
        assert first.evidence == second.evidence


def _mask_capability_document(
    *,
    kind: str = "rectangle",
    facts_update: dict[str, object] | None = None,
    raster_asset: bool = True,
) -> UIRDocument:
    mask_facts: dict[str, object] = {
        "kind": kind,
        "maskNodeRef": "node:mask",
        "contentNodeRefs": ["node:first", "node:second"],
        "safeRasterRootRef": "node:container" if kind == "boolean" else None,
        "effects": [],
    }
    mask_facts.update(facts_update or {})
    asset_ref = "asset:mask-raster" if kind == "boolean" else None
    container = _node(
        "FRAME",
        node_id="node:container",
        children=("node:mask", "node:first", "node:second"),
        visual={"mask": mask_facts},
        conversion=UIRConversion(mode=ConversionMode.NATIVE, assetRef=asset_ref),
    )
    mask = _node("RECTANGLE", node_id="node:mask", parent_id=container.id)
    first = _node("TEXT", node_id="node:first", parent_id=container.id)
    second = _node("TEXT", node_id="node:second", parent_id=container.id)
    assets = (
        {
            "asset:mask-raster": UIRAsset(
                id="asset:mask-raster",
                logicalId="mask-raster",
                mimeType="image/png",
            )
        }
        if asset_ref is not None and raster_asset
        else {}
    )
    return UIRDocument(
        documentId="uir:mask-capability",
        compilerVersion="uir-v1",
        source=UIRSource(revision="a" * 64, selectionId="selection"),
        roots=(container.id,),
        nodes={node.id: node for node in (container, mask, first, second)},
        assets=assets,
    )


def test_unknown_mask_fact_keys_are_blocking() -> None:
    document = _mask_capability_document(facts_update={"unexpected": True})

    decision = analyze_capabilities(document)["node:container"]

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.mask.facts_invalid"
    assert decision.blocking is True


def test_invalid_mask_ownership_and_order_are_blocking() -> None:
    cross_parent = _mask_capability_document().model_copy(
        update={
            "nodes": {
                **_mask_capability_document().nodes,
                "node:mask": _mask_capability_document().nodes["node:mask"].model_copy(
                    update={"parent_id": "node:other"}
                ),
            }
        }
    )
    reversed_content = _mask_capability_document(
        facts_update={"contentNodeRefs": ["node:second", "node:first"]}
    )

    for document in (cross_parent, reversed_content):
        decision = analyze_capabilities(document)["node:container"]
        assert decision.status == "unsupported"
        assert decision.rule_id == "fgui.mask.invalid_hierarchy"


def test_mask_source_must_immediately_precede_contiguous_content() -> None:
    document = _mask_capability_document()
    container = document.nodes["node:container"].model_copy(
        update={"children": ("node:first", "node:second", "node:mask")}
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, container.id: container}}
    )

    decision = analyze_capabilities(document)[container.id]

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.mask.invalid_hierarchy"


def test_complex_mask_requires_a_safe_raster_asset() -> None:
    valid = analyze_capabilities(_mask_capability_document(kind="boolean"))
    missing = analyze_capabilities(
        _mask_capability_document(kind="boolean", raster_asset=False)
    )

    assert valid["node:container"].status == "rasterFallback"
    assert valid["node:container"].rule_id == "fgui.fallback.raster_subtree"
    assert missing["node:container"].status == "unsupported"
    assert missing["node:container"].rule_id == "fgui.visual.effect_unsupported"


def test_raster_mask_subtree_cannot_absorb_text_run_content_closure_mismatch() -> None:
    document = _mask_capability_document(kind="boolean")
    valid = _text_node(
        content="Buy",
        runs=[{"content": "Buy", "style": {"fontSize": 20}}],
    )
    assert valid.text is not None
    mismatch = valid.text.model_copy(update={"content": "Buy now"})
    first = document.nodes["node:first"].model_copy(update={"text": mismatch})
    document = document.model_copy(update={"nodes": {**document.nodes, first.id: first}})

    decisions = analyze_capabilities(document)

    assert decisions[first.id].rule_id == "fgui.text.runs_content_mismatch"
    assert decisions[first.id].blocking is True
    assert decisions["node:container"].status != "rasterFallback"


def test_rasterized_image_mask_source_is_promoted_to_native_image_role() -> None:
    document = _mask_capability_document(kind="image")
    asset = UIRAsset(id="asset:mask-source", logicalId="mask-source", mimeType="image/png", sha256="d" * 64)
    mask = document.nodes["node:mask"].model_copy(
        update={"conversion": UIRConversion(mode=ConversionMode.RASTER_FALLBACK, reasons=("gradient_paint",), assetRef=asset.id)}
    )
    document = document.model_copy(update={"nodes": {**document.nodes, mask.id: mask}, "assets": {asset.id: asset}})

    decisions = analyze_capabilities(document)

    assert decisions[mask.id].status == "native"
    assert decisions[mask.id].rule_id == "fgui.native.image"


def test_safe_raster_root_must_own_every_consumed_descendant() -> None:
    document = _mask_capability_document(kind="boolean")
    container = document.nodes["node:container"].model_copy(
        update={"children": (*document.nodes["node:container"].children, "node:orphan")}
    )
    orphan = _node("TEXT", node_id="node:orphan", parent_id="node:elsewhere")
    document = document.model_copy(
        update={"nodes": {**document.nodes, container.id: container, orphan.id: orphan}}
    )

    decision = analyze_capabilities(document)["node:container"]

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.mask.invalid_hierarchy"


def test_overlapping_safe_raster_subtrees_are_blocking() -> None:
    document = _mask_capability_document(kind="boolean")
    outer = document.nodes["node:container"].model_copy(
        update={
            "children": (*document.nodes["node:container"].children, "node:inner")
        }
    )
    inner = _node(
        "FRAME",
        node_id="node:inner",
        parent_id=outer.id,
        children=("node:inner-mask", "node:inner-content"),
        visual={
            "mask": {
                "kind": "blur",
                "maskNodeRef": "node:inner-mask",
                "contentNodeRefs": ["node:inner-content"],
                "safeRasterRootRef": "node:inner",
                "effects": ["blur"],
            }
        },
        conversion=UIRConversion(
            mode=ConversionMode.NATIVE, assetRef="asset:inner-raster"
        ),
    )
    inner_mask = _node("RECTANGLE", node_id="node:inner-mask", parent_id=inner.id)
    inner_content = _node("TEXT", node_id="node:inner-content", parent_id=inner.id)
    inner_asset = UIRAsset(
        id="asset:inner-raster",
        logicalId="inner-raster",
        mimeType="image/png",
    )
    document = document.model_copy(
        update={
            "nodes": {
                **document.nodes,
                outer.id: outer,
                inner.id: inner,
                inner_mask.id: inner_mask,
                inner_content.id: inner_content,
            },
            "assets": {**document.assets, inner_asset.id: inner_asset},
        }
    )

    decisions = analyze_capabilities(document)

    assert decisions[outer.id].rule_id == "fgui.mask.invalid_hierarchy"
    assert decisions[inner.id].rule_id == "fgui.mask.invalid_hierarchy"


def test_native_clip_requires_a_compatible_geometric_source() -> None:
    document = _mask_capability_document()
    mask = document.nodes["node:mask"].model_copy(
        update={
            "source": document.nodes["node:mask"].source.model_copy(
                update={"type": "TEXT"}
            )
        }
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, mask.id: mask}}
    )

    decision = analyze_capabilities(document)["node:container"]

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.mask.source_role_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("width", 0),
        ("x", float("inf")),
        ("width", float("inf")),
    ],
)
def test_native_masks_require_finite_positive_source_bounds(
    field: str, value: float
) -> None:
    document = _mask_capability_document()
    bounds = Bounds(x=0, y=0, width=100, height=100).model_copy(
        update={field: value}
    )
    mask = document.nodes["node:mask"].model_copy(
        update={
            "geometry": document.nodes["node:mask"].geometry.model_copy(
                update={"resolved_bounds": bounds}
            )
        }
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, mask.id: mask}}
    )

    decision = analyze_capabilities(document)["node:container"]

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.mask.bounds_invalid"


def test_image_mask_requires_a_usable_image_resource() -> None:
    document = _mask_capability_document(kind="image")
    mask = document.nodes["node:mask"].model_copy(
        update={"conversion": UIRConversion(mode=ConversionMode.NATIVE)}
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, mask.id: mask}, "assets": {}}
    )

    decision = analyze_capabilities(document)["node:container"]

    assert decision.status == "unsupported"
    assert decision.rule_id == "fgui.mask.image_resource_missing"
