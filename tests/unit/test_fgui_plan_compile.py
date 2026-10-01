import hashlib
import json

import pytest

import figma_to_fgui.fgui_plan_compile as plan_compile
from figma_to_fgui import fgui_capabilities
from figma_to_fgui.fgui_asset_payloads import NewProjectInputError
from figma_to_fgui.fgui_new_project_compile import compile_new_project_manifest
from figma_to_fgui.fgui_new_project_models import NewProjectConfig
from figma_to_fgui.fgui_new_project_review import build_new_project_designer_review
from figma_to_fgui.fgui_plan_compile import compile_fgui_plan
from figma_to_fgui.fgui_plan_models import (
    CapabilityDecision,
    CapabilityStatus,
    FGUIPlanDocument,
    MaskMode,
)
from figma_to_fgui.fgui_plan_validate import canonical_plan_bytes, validate_fgui_plan
from figma_to_fgui.fgui_xml_dialect_614 import serialize_project_files
from figma_to_fgui.models import Bounds
from figma_to_fgui.uir_models import (
    ConversionMode,
    MappingStatus,
    SemanticStatus,
    UIRAsset,
    UIRComponentInstance,
    UIRConversion,
    UIRDocument,
    UIRGeometry,
    UIRMappingDecision,
    UIRNineSlice,
    UIRNode,
    UIRNodeSource,
    UIRSemantic,
    UIRSource,
    UIRText,
    UIRTextRun,
    UIRTextStyle,
)


def _node(
    node_id: str,
    source_type: str,
    *,
    parent_id: str | None = None,
    children: tuple[str, ...] = (),
    z_index: int = 0,
    bounds: Bounds | None = None,
    text: dict[str, object] | None = None,
    component: UIRComponentInstance | None = None,
    decision_ref: str | None = None,
    asset_ref: str | None = None,
    visual: dict[str, object] | None = None,
) -> UIRNode:
    return UIRNode(
        id=node_id,
        source=UIRNodeSource(
            nodeId=node_id.removeprefix("node:"),
            type=source_type,
            name=node_id,
            fingerprint="b" * 64,
        ),
        semantic=UIRSemantic(decisionRef=decision_ref),
        parentId=parent_id,
        children=children,
        zIndex=z_index,
        geometry=UIRGeometry(
            resolvedBounds=bounds or Bounds(x=0, y=0, width=100, height=100)
        ),
        visual=visual or {},
        text=text,
        component=component,
        conversion=UIRConversion(mode=ConversionMode.NATIVE, assetRef=asset_ref),
    )


def _document(
    roots: tuple[str, ...],
    nodes: dict[str, UIRNode],
    *,
    assets: dict[str, UIRAsset] | None = None,
    mapping_decisions: dict[str, UIRMappingDecision] | None = None,
) -> UIRDocument:
    return UIRDocument(
        documentId="uir:compile-fixture",
        compilerVersion="uir-v1",
        source=UIRSource(revision="a" * 64, selectionId="selection"),
        roots=roots,
        nodes=nodes,
        assets=assets or {},
        mappingDecisions=mapping_decisions or {},
    )


def generic_primitives_document() -> UIRDocument:
    root = _node(
        "node:root",
        "FRAME",
        children=("node:text", "node:image"),
        bounds=Bounds(x=0, y=0, width=400, height=300),
    )
    text = _node(
        "node:text",
        "TEXT",
        parent_id=root.id,
        z_index=0,
        bounds=Bounds(x=12, y=20, width=240, height=44),
        text={
            "content": "Generic title",
            "style": {
                "fontSize": 32,
                "textAlignHorizontal": "CENTER",
                "textAlignVertical": "CENTER",
                "textAutoResize": "WIDTH_AND_HEIGHT",
            },
        },
    )
    image = _node(
        "node:image",
        "RECTANGLE",
        parent_id=root.id,
        z_index=1,
        asset_ref="asset:image",
    )
    asset = UIRAsset(
        id="asset:image",
        logicalId="image",
        mimeType="image/png",
        sha256="c" * 64,
    )
    return _document((root.id,), {root.id: root, text.id: text, image.id: image}, assets={asset.id: asset})


def raster_subtree_document(*, interactive_child: bool = False) -> UIRDocument:
    document = generic_primitives_document()
    root = document.nodes["node:root"].model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.RASTER_FALLBACK,
                reasons=("composite_visual",),
                assetRef="asset:root-raster",
            )
        }
    )
    text = document.nodes["node:text"]
    if interactive_child:
        text = text.model_copy(
            update={"interactions": ({"trigger": "ON_CLICK"},)}
        )
    asset = UIRAsset(
        id="asset:root-raster",
        logicalId="root-raster",
        mimeType="image/png",
        sha256="d" * 64,
    )
    return document.model_copy(
        update={
            "nodes": {**document.nodes, root.id: root, text.id: text},
            "assets": {**document.assets, asset.id: asset},
        }
    )


def test_ordinary_raster_subtree_consumes_its_safe_descendants() -> None:
    plan = compile_fgui_plan(raster_subtree_document())

    assert plan.bindable is True
    assert {node.uir_node_ref for node in plan.nodes.values()} == {"node:root"}
    assert only_node(plan).children == ()
    assert validate_fgui_plan(plan) == ()


def test_raster_subtree_may_consume_nonblocking_editable_text_risk() -> None:
    document = raster_subtree_document()
    text = document.nodes["node:text"].model_copy(
        update={
            "text": UIRText(
                content="Buy now",
                style=UIRTextStyle(fontSize=20),
                runs=(
                    UIRTextRun(content="Buy ", style=UIRTextStyle(fontSize=20)),
                    UIRTextRun(content="now", style=UIRTextStyle(fontSize=24)),
                ),
            )
        }
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, text.id: text}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert {node.uir_node_ref for node in plan.nodes.values()} == {"node:root"}
    assert validate_fgui_plan(plan) == ()


def test_raster_subtree_cannot_consume_non_rasterizable_descendant_behavior() -> None:
    plan = compile_fgui_plan(raster_subtree_document(interactive_child=True))

    assert plan.bindable is False
    assert any(
        item.code == "fgui.raster.descendant_non_rasterizable"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def component_document(status: MappingStatus) -> UIRDocument:
    mapping = UIRMappingDecision(
        id="decision:component",
        nodeRef="node:instance",
        candidateKey="common_primary_button",
        status=status,
        confidence=1.0 if status == MappingStatus.VERIFIED else 0.0,
        ruleSource="fixture",
        evidence=("fixture.mapping",),
    )
    node = _node(
        "node:instance",
        "INSTANCE",
        decision_ref=mapping.id,
        component=UIRComponentInstance(variantProperties={"state": "normal"}),
    ).model_copy(
        update={
            "semantic": UIRSemantic(
                name=mapping.candidate_key,
                role="component",
                status="confirmed",
                decisionRef=mapping.id,
            ),
            "conversion": UIRConversion(mode=ConversionMode.COMPONENT_REFERENCE),
        }
    )
    return _document((node.id,), {node.id: node}, mapping_decisions={mapping.id: mapping})


def image_document(
    *, size: tuple[int, int], nine_slice: tuple[int, int, int, int] | None
) -> UIRDocument:
    asset = UIRAsset(
        id="asset:image",
        logicalId="image",
        mimeType="image/png",
        sha256="c" * 64,
        width=size[0],
        height=size[1],
        nineSlice=(
            None
            if nine_slice is None
            else UIRNineSlice(
                x=nine_slice[0],
                y=nine_slice[1],
                width=nine_slice[2],
                height=nine_slice[3],
            )
        ),
    )
    node = _node("node:image", "RECTANGLE", asset_ref=asset.id)
    return _document((node.id,), {node.id: node}, assets={asset.id: asset})


def only_node(plan: FGUIPlanDocument):
    return plan.nodes[plan.roots[0]]


def only_resource(plan: FGUIPlanDocument):
    return next(iter(plan.resources.values()))


def only_mask(plan: FGUIPlanDocument):
    return next(iter(plan.masks.values()))


def mask_document(
    *,
    kind: str,
    safe_raster: bool = False,
    source_missing: bool = False,
    cross_parent: bool = False,
) -> UIRDocument:
    mask_ref = "node:missing" if source_missing else "node:mask"
    mask_facts = {
        "kind": kind,
        "maskNodeRef": mask_ref,
        "contentNodeRefs": ["node:content", "node:group"],
        "safeRasterRootRef": "node:root" if safe_raster else None,
        "effects": [kind] if kind in {"boolean", "gradient", "blur", "blend"} else [],
        **({"cornerRadii": [12, 12, 12, 12]} if kind == "roundedRectangle" else {}),
    }
    raster_asset_ref = "asset:mask-raster" if safe_raster else None
    root = _node(
        "node:root",
        "FRAME",
        children=("node:mask", "node:content", "node:group"),
        asset_ref=raster_asset_ref,
        visual={"mask": mask_facts},
    )
    mask = _node(
        "node:mask",
        "RECTANGLE",
        parent_id="node:elsewhere" if cross_parent else root.id,
        asset_ref="asset:mask-source",
    )
    content = _node("node:content", "TEXT", parent_id=root.id, text={"content": "masked"})
    group = _node(
        "node:group",
        "GROUP",
        parent_id=root.id,
        children=("node:grandchild",),
    )
    grandchild = _node(
        "node:grandchild",
        "TEXT",
        parent_id=group.id,
        text={"content": "nested"},
    )
    nodes = {node.id: node for node in (root, mask, content, group, grandchild)}
    assets = {
        "asset:mask-source": UIRAsset(
            id="asset:mask-source",
            logicalId="mask-source",
            mimeType="image/png",
            sha256="d" * 64,
            sourceNodeId=mask.id,
        )
    }
    if safe_raster:
        assets["asset:mask-raster"] = UIRAsset(
            id="asset:mask-raster",
            logicalId="mask-raster",
            mimeType="image/png",
            sha256="e" * 64,
            sourceNodeId=root.id,
        )
    return _document((root.id,), nodes, assets=assets)


def colliding_mask_document(*, safe_id: str, raster_container_id: str) -> UIRDocument:
    native_mask_id = f"{safe_id}:mask"
    native_content_id = f"{safe_id}:content"
    raster_mask_id = f"{raster_container_id}:mask"
    raster_content_id = f"{raster_container_id}:content"
    safe_root = _node(
        safe_id,
        "FRAME",
        children=(native_mask_id, native_content_id, raster_container_id),
        asset_ref="asset:collision-raster",
        visual={
            "mask": {
                "kind": "image",
                "maskNodeRef": native_mask_id,
                "contentNodeRefs": [native_content_id],
                "safeRasterRootRef": None,
                "effects": [],
            }
        },
    )
    native_mask = _node(
        native_mask_id,
        "IMAGE",
        parent_id=safe_id,
        asset_ref="asset:collision-native",
    )
    native_content = _node(native_content_id, "TEXT", parent_id=safe_id)
    raster_container = _node(
        raster_container_id,
        "FRAME",
        parent_id=safe_id,
        children=(raster_mask_id, raster_content_id),
        visual={
            "mask": {
                "kind": "blur",
                "maskNodeRef": raster_mask_id,
                "contentNodeRefs": [raster_content_id],
                "safeRasterRootRef": safe_id,
                "effects": ["blur"],
            }
        },
    )
    raster_mask = _node(raster_mask_id, "RECTANGLE", parent_id=raster_container_id)
    raster_content = _node(raster_content_id, "TEXT", parent_id=raster_container_id)
    assets = {
        "asset:collision-raster": UIRAsset(
            id="asset:collision-raster",
            logicalId="collision-raster",
            mimeType="image/png",
            sha256="f" * 64,
            sourceNodeId=safe_id,
        ),
        "asset:collision-native": UIRAsset(
            id="asset:collision-native",
            logicalId="collision-native",
            mimeType="image/png",
            sha256="a" * 64,
            sourceNodeId=native_mask_id,
        ),
    }
    nodes = {
        node.id: node
        for node in (
            safe_root,
            native_mask,
            native_content,
            raster_container,
            raster_mask,
            raster_content,
        )
    }
    return _document((safe_id,), nodes, assets=assets)


def nested_native_mask_document(nested_kind: str) -> UIRDocument:
    document = mask_document(kind="boolean", safe_raster=True)
    group = document.nodes["node:group"].model_copy(
        update={
            "children": (
                "node:nested-mask",
                "node:nested-content",
                "node:grandchild",
            ),
            "visual": {
                "mask": {
                    "kind": nested_kind,
                    "maskNodeRef": "node:nested-mask",
                    "contentNodeRefs": ["node:nested-content"],
                    "safeRasterRootRef": None,
                    "effects": [],
                }
            },
        }
    )
    nested_mask = _node(
        "node:nested-mask",
        "IMAGE",
        parent_id=group.id,
        asset_ref="asset:nested-mask",
    )
    nested_content = _node("node:nested-content", "TEXT", parent_id=group.id)
    nested_asset = UIRAsset(
        id="asset:nested-mask",
        logicalId="nested-mask",
        mimeType="image/png",
        sha256="9" * 64,
        sourceNodeId=nested_mask.id,
    )
    return document.model_copy(
        update={
            "nodes": {
                **document.nodes,
                group.id: group,
                nested_mask.id: nested_mask,
                nested_content.id: nested_content,
            },
            "assets": {**document.assets, nested_asset.id: nested_asset},
        }
    )


def test_compile_preserves_tree_transform_and_text_facts() -> None:
    plan = compile_fgui_plan(generic_primitives_document())

    root = plan.nodes[plan.roots[0]]
    text = next(plan.nodes[item] for item in root.children if plan.nodes[item].type == "text")

    assert text.transform.bounds == Bounds(x=12, y=20, width=240, height=44)
    assert text.text is not None
    assert text.text.content == "Generic title"
    assert text.text.font_size == 32
    assert text.text.horizontal_align == "center"
    assert text.text.vertical_align == "middle"
    assert text.text.auto_resize == "WIDTH_AND_HEIGHT"
    assert text.text.style_facts["textAlignHorizontal"] == "center"
    assert text.text.style_facts["textAlignVertical"] == "middle"
    assert text.text.style_facts["textAutoResize"] == "WIDTH_AND_HEIGHT"


def test_compile_preserves_hidden_visibility_in_typed_transform() -> None:
    node = _node("node:hidden", "FRAME").model_copy(
        update={"layout": {"visible": False}}
    )

    plan = compile_fgui_plan(_document((node.id,), {node.id: node}))

    assert only_node(plan).transform.visible is False
    assert validate_fgui_plan(plan) == ()


def test_expressible_text_runs_compile_as_rich_text_without_loss() -> None:
    node = _node(
        "node:rich-text",
        "TEXT",
        text={
            "content": "Buy now",
            "style": {"fontSize": 20, "color": "#112233"},
            "runs": [
                {"content": "Buy ", "style": {"color": "#112233"}},
                {
                    "content": "now",
                    "style": {"color": "#ff0000"},
                },
            ],
        },
    )

    plan = compile_fgui_plan(_document((node.id,), {node.id: node}))

    compiled = only_node(plan)
    assert compiled.type == "richText"
    assert compiled.text is not None
    assert [run.content for run in compiled.text.runs] == ["Buy ", "now"]
    assert compiled.text.runs[1].color == "#ff0000"
    assert plan.resources == {}
    assert not any(decision.reasons for decision in plan.decisions.values())
    assert validate_fgui_plan(plan) == ()

    manifest = compile_new_project_manifest(
        plan,
        NewProjectConfig(
            projectName="RichText",
            packageName="Generated",
            fairyGuiVersion="6.1.4",
            publishTarget="unity",
        ),
        (),
    )
    component_xml = next(
        content
        for path, content in serialize_project_files(manifest, ()).items()
        if "/Panel/" in path
    )
    assert b'<richtext ' in component_xml
    assert b'ubb="true"' in component_xml
    assert b'text="Buy [color=#ff0000]now[/color]"' in component_xml


@pytest.mark.parametrize(
    ("content", "base_style", "runs", "unsupported"),
    (
        (
            "Buy now",
            {"fontSize": 20, "color": "#112233"},
            (
                {"content": "Buy ", "style": {"fontSize": 20}},
                {"content": "now", "style": {"fontSize": 24}},
            ),
            "fontSize",
        ),
        (
            "Buy now",
            {"fontSize": 20, "fontCandidates": ["Inter"]},
            (
                {"content": "Buy ", "style": {"fontCandidates": ["Inter"]}},
                {"content": "now", "style": {"fontCandidates": ["Arial"]}},
            ),
            "fontCandidates",
        ),
        (
            "Buy now",
            {"fontSize": 20, "strokeColor": "#112233", "strokeSize": 1},
            (
                {"content": "Buy ", "style": {"strokeColor": "#112233"}},
                {"content": "now", "style": {"strokeColor": "#ff0000"}},
            ),
            "strokeColor",
        ),
        (
            "[Buy now]",
            {"fontSize": 20, "color": "#112233"},
            (
                {"content": "[Buy ", "style": {"color": "#112233"}},
                {"content": "now]", "style": {"color": "#ff0000"}},
            ),
            "ubbEncoding",
        ),
    ),
    ids=("size", "font", "stroke", "ubb-ambiguity"),
)
def test_unsupported_run_differences_compile_as_reviewable_plain_text(
    content: str,
    base_style: dict[str, object],
    runs: tuple[dict[str, object], ...],
    unsupported: str,
) -> None:
    node = _node(
        f"node:rich-text-{unsupported}",
        "TEXT",
        text={
            "content": content,
            "style": base_style,
            "runs": runs,
        },
    )

    plan = compile_fgui_plan(_document((node.id,), {node.id: node}))

    compiled = only_node(plan)
    decision = plan.decisions[node.id]
    assert plan.bindable is True
    assert compiled.type == "text"
    assert compiled.text is not None
    assert compiled.text.content == content
    assert compiled.text.font_size == 20
    assert compiled.text.font_candidates == tuple(base_style.get("fontCandidates", ()))
    assert compiled.text.color == base_style.get("color")
    assert compiled.text.stroke_color == base_style.get("strokeColor")
    assert compiled.text.stroke_size == base_style.get("strokeSize")
    assert compiled.text.runs == ()
    assert decision.reasons == ("rich_text_runs",)
    assert decision.status == CapabilityStatus.UNSUPPORTED
    assert decision.rule_id == "fgui.text.runs_unsupported"
    assert "text.runs.count=2" in decision.evidence
    assert f"text.runs.unsupported={unsupported}" in decision.evidence
    assert decision.blocking is False
    assert plan.resources == {}
    assert validate_fgui_plan(plan) == ()


def test_reviewable_plain_text_suppresses_requested_raster_resource() -> None:
    asset = UIRAsset(
        id="asset:text-raster",
        logicalId="text-raster",
        mimeType="image/png",
        sha256="d" * 64,
        sourceNodeId="node:raster-rich-text",
    )
    node = _node(
        "node:raster-rich-text",
        "TEXT",
        asset_ref=asset.id,
        text={
            "content": "Buy now",
            "style": {"fontSize": 20, "color": "#112233"},
            "runs": (
                {"content": "Buy ", "style": {"fontSize": 20}},
                {"content": "now", "style": {"fontSize": 24}},
            ),
        },
    ).model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.RASTER_FALLBACK,
                reasons=("rich_text_runs",),
                assetRef=asset.id,
            )
        }
    )

    plan = compile_fgui_plan(
        _document((node.id,), {node.id: node}, assets={asset.id: asset})
    )

    compiled = only_node(plan)
    decision = plan.decisions[node.id]
    assert plan.bindable is True
    assert compiled.type == "text"
    assert compiled.text is not None
    assert compiled.text.content == "Buy now"
    assert compiled.text.font_size == 20
    assert compiled.text.color == "#112233"
    assert compiled.text.runs == ()
    assert compiled.resource_ref is None
    assert plan.resources == {}
    assert decision.status == CapabilityStatus.UNSUPPORTED
    assert decision.rule_id == "fgui.text.runs_unsupported"
    assert decision.reasons == ("rich_text_runs",)
    assert decision.evidence == (
        "text.runs.count=2",
        "text.runs.preserved=content",
        "text.runs.unsupported=fontSize",
    )
    assert validate_fgui_plan(plan) == ()


def test_reviewed_rich_text_risk_decision_remains_reviewable_plain_text() -> None:
    node = _node(
        "node:reviewed-rich-text-risk",
        "TEXT",
        text={
            "content": "Buy now",
            "style": {"fontSize": 20},
            "runs": (
                {"content": "Buy ", "style": {"fontSize": 20}},
                {"content": "now", "style": {"fontSize": 24}},
            ),
        },
    )
    document = _document((node.id,), {node.id: node})
    reviewed = plan_compile.analyze_capabilities(document)
    reviewed = {
        node.id: reviewed[node.id].model_copy(update={"id": "decision:reviewed-risk"})
    }

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert only_node(plan).type == "text"
    assert plan.decisions[node.id] == reviewed[node.id]
    assert plan.bindable is True
    assert validate_fgui_plan(plan) == ()


@pytest.mark.parametrize(
    "evidence",
    (
        (
            "text.runs.count=999",
            "text.runs.preserved=content",
            "text.runs.unsupported=fontSize",
        ),
        (
            "text.runs.count=2",
            "text.runs.preserved=content",
            "text.runs.unsupported=inventedFeature",
        ),
    ),
    ids=("forged-count", "forged-property"),
)
def test_reviewed_rich_text_risk_must_match_canonical_evidence(
    evidence: tuple[str, ...],
) -> None:
    node = _node(
        "node:forged-rich-text-risk",
        "TEXT",
        text={
            "content": "Buy now",
            "style": {"fontSize": 20},
            "runs": (
                {"content": "Buy ", "style": {"fontSize": 20}},
                {"content": "now", "style": {"fontSize": 24}},
            ),
        },
    )
    document = _document((node.id,), {node.id: node})
    reviewed = plan_compile.analyze_capabilities(document)
    forged = {
        node.id: reviewed[node.id].model_copy(update={"evidence": evidence})
    }

    plan = compile_fgui_plan(document, decisions=forged)

    assert plan.bindable is False
    assert plan.roots == ()
    assert any(
        item.code == "fgui.decision.reviewable_text_evidence_incoherent"
        for item in plan.diagnostics
    )


@pytest.mark.parametrize(
    "count",
    ("²", "9" * 5000),
    ids=("unicode-numeric", "oversized-ascii-decimal"),
)
def test_malformed_reviewed_rich_text_count_is_quarantined_not_raised(
    count: str,
) -> None:
    node = _node(
        "node:malformed-rich-text-risk",
        "TEXT",
        text={
            "content": "Buy now",
            "style": {"fontSize": 20},
            "runs": (
                {"content": "Buy ", "style": {"fontSize": 20}},
                {"content": "now", "style": {"fontSize": 24}},
            ),
        },
    )
    document = _document((node.id,), {node.id: node})
    reviewed = plan_compile.analyze_capabilities(document)
    malformed = {
        node.id: reviewed[node.id].model_copy(
            update={
                "evidence": (
                    f"text.runs.count={count}",
                    "text.runs.preserved=content",
                    "text.runs.unsupported=fontSize",
                )
            }
        )
    }

    plan = compile_fgui_plan(document, decisions=malformed)

    assert plan.bindable is False
    assert plan.roots == ()
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.blocking_incoherent"
        for item in plan.diagnostics
    )


def test_compile_analyzes_each_text_node_once_for_plan_emission(monkeypatch) -> None:
    first = _node("node:first-text", "TEXT", text={"content": "First"})
    second = _node("node:second-text", "TEXT", text={"content": "Second"})
    document = _document((first.id, second.id), {first.id: first, second.id: second})
    original = plan_compile.analyze_text_runs
    calls: list[str] = []

    def counted(node: UIRNode):
        calls.append(node.id)
        return original(node)

    def unexpected_reanalysis(node: UIRNode):
        pytest.fail(f"text run capability was recomputed for {node.id}")

    monkeypatch.setattr(plan_compile, "analyze_text_runs", counted)
    monkeypatch.setattr(fgui_capabilities, "analyze_text_runs", unexpected_reanalysis)

    plan = compile_fgui_plan(document)

    assert calls == [first.id, second.id]
    assert len(plan.nodes) == 2


def test_run_content_mismatch_stays_non_bindable_and_cannot_reach_writer() -> None:
    valid = _node(
        "node:rich-text-mismatch",
        "TEXT",
        text={
            "content": "Buy now",
            "style": {"fontSize": 20},
            "runs": (
                {"content": "Buy ", "style": {"fontSize": 20}},
                {"content": "now", "style": {"fontSize": 20}},
            ),
        },
    )
    assert valid.text is not None
    mismatched = valid.model_copy(
        update={"text": valid.text.model_copy(update={"content": "Buy later"})}
    )

    plan = compile_fgui_plan(_document((mismatched.id,), {mismatched.id: mismatched}))

    assert plan.bindable is False
    assert plan.roots == ()
    assert plan.nodes == {}
    assert plan.decisions[mismatched.id].rule_id == "fgui.text.runs_content_mismatch"
    assert any(item.code == "fgui.text.runs_content_mismatch" for item in plan.diagnostics)
    with pytest.raises(NewProjectInputError):
        compile_new_project_manifest(
            plan,
            NewProjectConfig(
                projectName="BlockedRichText",
                packageName="Generated",
                fairyGuiVersion="6.1.4",
                publishTarget="unity",
            ),
            (),
        )


def test_verified_component_candidate_without_definition_is_blocked() -> None:
    plan = compile_fgui_plan(component_document(MappingStatus.VERIFIED))

    assert plan.schema_version == 2
    assert plan.component_definitions == {}
    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.component.definition_missing"
        for item in plan.diagnostics
    )
    encoded = plan.model_dump_json(by_alias=True).encode("utf-8")
    assert b"packageId" not in encoded and b"componentId" not in encoded


def test_component_candidate_overrides_do_not_create_a_definition() -> None:
    document = component_document(MappingStatus.VERIFIED)
    source = document.nodes["node:instance"]
    assert source.component is not None
    source = source.model_copy(
        update={
            "component": source.component.model_copy(
                update={"overrides": {"label": "Changed", "visible": False}}
            )
        }
    )
    document = document.model_copy(update={"nodes": {source.id: source}})

    plan = compile_fgui_plan(document)
    assert plan.nodes == {}
    assert plan.component_definitions == {}
    assert plan.bindable is False
    assert validate_fgui_plan(plan) == ()


def test_component_candidate_uses_its_explicit_png_raster_fallback() -> None:
    document = component_document(MappingStatus.MISSING)
    source = document.nodes["node:instance"].model_copy(
        update={
            "semantic": document.nodes["node:instance"].semantic.model_copy(
                update={"status": SemanticStatus.FALLBACK}
            ),
            "conversion": UIRConversion(
                mode=ConversionMode.RASTER_FALLBACK,
                reasons=("component_definition_unavailable",),
                assetRef="asset:instance-raster",
            )
        }
    )
    asset = UIRAsset(
        id="asset:instance-raster",
        logicalId="instance-raster",
        mimeType="image/png",
        sha256="d" * 64,
    )
    document = document.model_copy(
        update={"nodes": {source.id: source}, "assets": {asset.id: asset}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert plan.component_definitions == {}
    assert only_node(plan).type == "rasterSubtree"
    assert validate_fgui_plan(plan) == ()


def component_with_child_document(*, interactive_child: bool = False) -> UIRDocument:
    document = component_document(MappingStatus.VERIFIED)
    instance = document.nodes["node:instance"].model_copy(
        update={"children": ("node:instance-child",)}
    )
    child = _node(
        "node:instance-child",
        "TEXT",
        parent_id=instance.id,
        text={"content": "Internal label"},
    )
    if interactive_child:
        child = child.model_copy(
            update={"interactions": ({"trigger": "ON_CLICK"},)}
        )
    return document.model_copy(
        update={"nodes": {instance.id: instance, child.id: child}}
    )


def test_component_candidate_without_definition_does_not_consume_source_internals() -> None:
    plan = compile_fgui_plan(component_with_child_document())

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.component.definition_missing"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_component_candidate_without_definition_keeps_behavior_fail_closed() -> None:
    plan = compile_fgui_plan(
        component_with_child_document(interactive_child=True)
    )

    assert plan.bindable is False
    assert any(
        item.code == "fgui.component.definition_missing"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def reviewed_component_raster_fallback(
    *, interactive_child: bool = False
) -> tuple[UIRDocument, dict[str, CapabilityDecision]]:
    document = component_with_child_document(interactive_child=interactive_child)
    instance = document.nodes["node:instance"]
    asset = UIRAsset(
        id="asset:component-raster",
        logicalId="component-raster",
        mimeType="image/png",
        sha256="e" * 64,
    )
    instance = instance.model_copy(
        update={
            "conversion": instance.conversion.model_copy(
                update={"asset_ref": asset.id}
            )
        }
    )
    document = document.model_copy(
        update={
            "nodes": {**document.nodes, instance.id: instance},
            "assets": {asset.id: asset},
        }
    )
    decisions = dict(plan_compile.analyze_capabilities(document))
    decisions[instance.id] = decisions[instance.id].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
            "reasons": ("reviewed_component_raster_fallback",),
        }
    )
    return document, decisions


def reviewed_component_raster_with_descendant_mask() -> tuple[
    UIRDocument, dict[str, CapabilityDecision]
]:
    outer_document, _ = reviewed_component_raster_fallback()
    document = component_with_descendant_raster_mask(outer_document)
    decisions = dict(plan_compile.analyze_capabilities(document))
    instance = document.nodes["node:instance"]
    decisions[instance.id] = decisions[instance.id].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
            "reasons": ("reviewed_component_raster_fallback",),
        }
    )
    return document, decisions


def component_with_descendant_raster_mask(
    outer_document: UIRDocument | None = None,
) -> UIRDocument:
    if outer_document is None:
        outer_document = component_document(MappingStatus.VERIFIED)
    mask_source = mask_document(kind="boolean", safe_raster=True)
    instance = outer_document.nodes["node:instance"].model_copy(
        update={"children": ("node:root",)}
    )
    inner_root = mask_source.nodes["node:root"].model_copy(
        update={"parent_id": instance.id}
    )
    nodes = {
        instance.id: instance,
        **{
            node_id: inner_root if node_id == inner_root.id else node
            for node_id, node in mask_source.nodes.items()
        },
    }
    document = outer_document.model_copy(
        update={
            "nodes": nodes,
            "assets": {**outer_document.assets, **mask_source.assets},
        }
    )
    return document


def reviewed_component_raster_with_mask_and_interaction() -> tuple[
    UIRDocument, dict[str, CapabilityDecision]
]:
    document, _ = reviewed_component_raster_with_descendant_mask()
    instance = document.nodes["node:instance"]
    interaction = _node(
        "node:interaction-sibling",
        "FRAME",
        parent_id=instance.id,
    ).model_copy(update={"interactions": ({"trigger": "ON_CLICK"},)})
    instance = instance.model_copy(
        update={"children": (*instance.children, interaction.id)}
    )
    document = document.model_copy(
        update={
            "nodes": {
                **document.nodes,
                instance.id: instance,
                interaction.id: interaction,
            }
        }
    )
    decisions = dict(plan_compile.analyze_capabilities(document))
    decisions[instance.id] = decisions[instance.id].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
            "reasons": ("reviewed_component_raster_fallback",),
        }
    )
    return document, decisions


def test_reviewed_component_raster_fallback_consumes_safe_descendants() -> None:
    document, decisions = reviewed_component_raster_fallback()

    plan = compile_fgui_plan(document, decisions=decisions)

    assert plan.bindable is True
    assert {node.uir_node_ref for node in plan.nodes.values()} == {"node:instance"}
    assert only_node(plan).type == "rasterSubtree"
    assert only_node(plan).children == ()
    assert validate_fgui_plan(plan) == ()


def test_reviewed_component_raster_suppresses_consumed_descendant_raster_mask() -> None:
    document, decisions = reviewed_component_raster_with_descendant_mask()

    plan = compile_fgui_plan(document, decisions=decisions)

    assert plan.bindable is True
    assert validate_fgui_plan(plan) == ()
    assert plan.masks == {}
    assert len(plan.nodes) == 1
    assert len(plan.resources) == 1
    node = only_node(plan)
    resource = only_resource(plan)
    assert node.type == "rasterSubtree"
    assert node.children == ()
    assert node.mask_ref is None
    assert resource.source_asset_ref == "asset:component-raster"
    assert resource.consumers == (node.id,)
    assert any(
        item.code == "fgui.visual.raster_fallback"
        and item.node_id == "node:instance"
        for item in plan.diagnostics
    )


def test_blocked_reviewed_component_does_not_emit_nested_raster_mask() -> None:
    document, decisions = reviewed_component_raster_with_mask_and_interaction()

    plan = compile_fgui_plan(document, decisions=decisions)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert plan.masks == {}
    assert plan.resources == {}
    assert any(
        item.code == "fgui.raster.descendant_non_rasterizable"
        and item.node_id == "node:instance"
        for item in plan.diagnostics
    )
    assert any(
        item.code == "fgui.unsupported.interaction"
        and item.node_id == "node:interaction-sibling"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_definition_missing_component_does_not_emit_nested_raster_mask() -> None:
    plan = compile_fgui_plan(component_with_descendant_raster_mask())

    assert plan.bindable is False
    assert plan.nodes == {}
    assert plan.masks == {}
    assert plan.resources == {}
    assert any(
        item.code == "fgui.component.definition_missing"
        and item.node_id == "node:instance"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_reviewed_component_raster_fallback_cannot_consume_behavior() -> None:
    document, decisions = reviewed_component_raster_fallback(interactive_child=True)

    plan = compile_fgui_plan(document, decisions=decisions)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.raster.descendant_non_rasterizable"
        for item in plan.diagnostics
    )
    assert any(
        item.code == "fgui.unsupported.interaction"
        and item.node_id == "node:instance-child"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_valid_nine_slice_is_preserved_and_invalid_grid_blocks() -> None:
    valid = compile_fgui_plan(
        image_document(size=(100, 80), nine_slice=(10, 10, 70, 50))
    )
    invalid = compile_fgui_plan(
        image_document(size=(100, 80), nine_slice=(10, 10, 100, 50))
    )

    assert only_resource(valid).nine_slice is not None
    assert only_resource(valid).nine_slice.model_dump() == {
        "x": 10,
        "y": 10,
        "width": 70,
        "height": 50,
    }
    assert invalid.bindable is False
    assert only_resource(invalid).nine_slice is None
    assert any(
        item.code == "fgui.resource.nine_slice_invalid" for item in invalid.diagnostics
    )


def test_ordinary_images_never_gain_inferred_nine_slice() -> None:
    plan = compile_fgui_plan(image_document(size=(100, 80), nine_slice=None))

    assert only_resource(plan).nine_slice is None


def test_plan_ids_and_resource_consumers_are_deterministic() -> None:
    first = compile_fgui_plan(generic_primitives_document())
    second = compile_fgui_plan(generic_primitives_document())

    assert first.model_dump(mode="json", by_alias=True) == second.model_dump(
        mode="json", by_alias=True
    )
    assert only_resource(first).consumers == tuple(sorted(only_resource(first).consumers))


def test_resource_plan_preserves_logical_content_and_export_recipe_identity() -> None:
    document = image_document(size=(100, 80), nine_slice=None)

    plan = compile_fgui_plan(document)

    resource = only_resource(plan)
    expected_export_hash = hashlib.sha256(
        json.dumps(
            {
                "exportFormat": "png",
                "height": 80,
                "mimeType": "image/png",
                "width": 100,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert resource.logical_asset_id == "image"
    assert resource.content_sha256 == "c" * 64
    assert resource.export_parameters_sha256 == expected_export_hash
    assert resource.id != resource.source_asset_ref


def test_export_recipe_hash_changes_only_with_export_recipe() -> None:
    base = image_document(size=(100, 80), nine_slice=None)
    renamed_asset = base.assets["asset:image"].model_copy(
        update={"logical_id": "renamed-image"}
    )
    renamed = base.model_copy(update={"assets": {renamed_asset.id: renamed_asset}})
    resized_asset = base.assets["asset:image"].model_copy(update={"width": 101})
    resized = base.model_copy(update={"assets": {resized_asset.id: resized_asset}})

    base_resource = only_resource(compile_fgui_plan(base))
    renamed_resource = only_resource(compile_fgui_plan(renamed))
    resized_resource = only_resource(compile_fgui_plan(resized))

    assert (
        renamed_resource.export_parameters_sha256
        == base_resource.export_parameters_sha256
    )
    assert renamed_resource.logical_asset_id != base_resource.logical_asset_id
    assert resized_resource.export_parameters_sha256 != base_resource.export_parameters_sha256


def test_missing_resource_content_hash_blocks_binding() -> None:
    document = image_document(size=(100, 80), nine_slice=None)
    asset = document.assets["asset:image"].model_copy(update={"sha256": None})
    document = document.model_copy(update={"assets": {asset.id: asset}})

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert any(
        item.code == "fgui.plan.resource_content_hash_missing"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


@pytest.mark.parametrize(
    ("mime_type", "export_format"),
    [("image/jpeg", "jpg"), ("image/webp", "webp")],
)
def test_raster_fallback_requires_png_recipe(
    mime_type: str, export_format: str
) -> None:
    asset = UIRAsset(
        id="asset:fallback",
        logicalId="fallback",
        mimeType=mime_type,
        sha256="d" * 64,
        exportFormat=export_format,
    )
    node = _node("node:fallback", "FRAME", asset_ref=asset.id).model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.RASTER_FALLBACK,
                reasons=("fixture",),
                assetRef=asset.id,
            )
        }
    )

    plan = compile_fgui_plan(
        _document((node.id,), {node.id: node}, assets={asset.id: asset})
    )

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.resource.fallback_format_incoherent"
        for item in plan.diagnostics
    )


def test_shared_asset_gets_distinct_native_and_raster_resource_plans() -> None:
    asset = UIRAsset(
        id="asset:shared",
        logicalId="shared",
        mimeType="image/png",
        sha256="d" * 64,
    )
    native = _node("node:native", "RECTANGLE", asset_ref=asset.id)
    fallback = _node("node:fallback", "FRAME", asset_ref=asset.id, z_index=1).model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.RASTER_FALLBACK,
                reasons=("composite_visual",),
                assetRef=asset.id,
            )
        }
    )

    plan = compile_fgui_plan(
        _document(
            (native.id, fallback.id),
            {native.id: native, fallback.id: fallback},
            assets={asset.id: asset},
        )
    )

    planned_by_source = {node.uir_node_ref: node for node in plan.nodes.values()}
    native_ref = planned_by_source[native.id].resource_ref
    fallback_ref = planned_by_source[fallback.id].resource_ref
    assert native_ref != fallback_ref
    assert len(plan.resources) == 2
    assert validate_fgui_plan(plan) == ()
    assert plan.bindable is True


def test_successful_raster_fallback_diagnostic_carries_review_metadata() -> None:
    asset = UIRAsset(
        id="asset:fallback",
        logicalId="fallback",
        mimeType="image/png",
        sha256="d" * 64,
    )
    node = _node("node:fallback", "FRAME", asset_ref=asset.id).model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.RASTER_FALLBACK,
                reasons=("composite_visual",),
                assetRef=asset.id,
            )
        }
    )

    plan = compile_fgui_plan(
        _document((node.id,), {node.id: node}, assets={asset.id: asset})
    )

    decision = plan.decisions[node.id]
    diagnostic = next(
        item for item in plan.diagnostics if item.code == "fgui.visual.raster_fallback"
    )
    assert diagnostic.severity == "WARNING"
    assert diagnostic.rule_id == decision.rule_id
    assert diagnostic.rule_version == decision.rule_version
    assert diagnostic.evidence == decision.evidence
    assert diagnostic.suggested_action == "review_raster_fallback"
    assert diagnostic.blocks_binding is False


def test_unsupported_diagnostic_carries_rule_evidence_and_binding_impact() -> None:
    node = _node("node:interactive", "FRAME").model_copy(
        update={"interactions": ({"trigger": "ON_CLICK"},)}
    )

    plan = compile_fgui_plan(_document((node.id,), {node.id: node}))

    decision = plan.decisions[node.id]
    diagnostic = next(
        item for item in plan.diagnostics if item.code == decision.rule_id
    )
    assert diagnostic.rule_version == decision.rule_version
    assert diagnostic.evidence == decision.evidence
    assert diagnostic.suggested_action == "resolve_unsupported_feature"
    assert diagnostic.blocks_binding is True


def test_private_uir_facts_block_compilation_without_leaking_or_crashing() -> None:
    document = generic_primitives_document()
    root = document.nodes["node:root"].model_copy(
        update={"visual": {"apiKey": "private-value"}}
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, root.id: root}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert any(item.code == "uir.private_data_forbidden" for item in plan.diagnostics)
    assert "private-value" not in plan.model_dump_json(by_alias=True)


def test_supplied_coherent_reviewed_decisions_are_used_without_reanalysis(monkeypatch) -> None:
    node = _node("node:text", "TEXT", text={"content": "Reviewed"})
    document = _document((node.id,), {node.id: node})
    reviewed = {
        node.id: CapabilityDecision(
            id="decision:reviewed",
            nodeRef=node.id,
            status=CapabilityStatus.NATIVE,
            ruleId="fgui.native.text",
            ruleVersion=7,
            evidence=("reviewed.override",),
        )
    }

    def analysis_must_not_run(*args, **kwargs):
        raise AssertionError("reviewed decisions must bypass capability analysis")

    monkeypatch.setattr(plan_compile, "analyze_capabilities", analysis_must_not_run)

    plan = compile_fgui_plan(document, rule_version=7, decisions=reviewed)

    compiled = only_node(plan)
    assert compiled.type == "text"
    assert compiled.decision_ref == "decision:reviewed"
    assert plan.decisions == reviewed


def test_swapped_reviewed_decisions_are_quarantined_before_emission() -> None:
    document = generic_primitives_document()
    analyzed = dict(plan_compile.analyze_capabilities(document))
    swapped = {
        "node:root": analyzed["node:text"],
        "node:text": analyzed["node:root"],
        "node:image": analyzed["node:image"],
    }

    plan = compile_fgui_plan(document, decisions=swapped)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert plan.roots == ()
    assert all(item.blocking for item in plan.decisions.values())
    assert {
        item.code for item in plan.diagnostics
    } >= {"fgui.decision.key_mismatch"}


def test_private_reviewed_decision_metadata_is_quarantined_and_redacted() -> None:
    node = _node("node:text", "TEXT", text={"content": "Reviewed"})
    document = _document((node.id,), {node.id: node})
    reviewed = {
        node.id: CapabilityDecision(
            id="decision:reviewed",
            nodeRef=node.id,
            status=CapabilityStatus.NATIVE,
            ruleId="fgui.native.text",
            ruleVersion=1,
            evidence=(r"C:\private\review.txt",),
        )
    }

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.private_data_forbidden"
        for item in plan.diagnostics
    )
    assert "review.txt" not in plan.model_dump_json(by_alias=True)
    assert validate_fgui_plan(plan) == ()


def test_blank_reviewed_decision_identity_and_evidence_are_quarantined() -> None:
    document = generic_primitives_document()
    reviewed = dict(plan_compile.analyze_capabilities(document))
    reviewed["node:root"] = reviewed["node:root"].model_copy(
        update={"id": "", "evidence": ("",)}
    )

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert {
        item.code for item in plan.diagnostics
    } >= {
        "fgui.decision.id_invalid",
        "fgui.decision.evidence_invalid",
    }
    assert validate_fgui_plan(plan) == ()


def test_reviewed_decisions_cannot_override_non_rasterizable_safety_gates() -> None:
    node = _node("node:interactive", "FRAME").model_copy(
        update={"interactions": ({"trigger": "ON_CLICK"},)}
    )
    document = _document((node.id,), {node.id: node})
    reviewed = {
        node.id: CapabilityDecision(
            id="decision:unsafe-override",
            nodeRef=node.id,
            status=CapabilityStatus.NATIVE,
            ruleId="fgui.native.container",
            ruleVersion=1,
            evidence=("reviewed.override",),
        )
    }

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.safety_override_forbidden"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_reviewed_native_image_requires_a_usable_source_asset() -> None:
    node = _node("node:frame", "FRAME")
    document = _document((node.id,), {node.id: node})
    reviewed = {
        node.id: CapabilityDecision(
            id="decision:image-without-asset",
            nodeRef=node.id,
            status=CapabilityStatus.NATIVE,
            ruleId="fgui.native.image",
            ruleVersion=1,
            evidence=("reviewed.override",),
        )
    }

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.resource_requirement_incoherent"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_reviewed_native_rule_must_match_the_source_payload_role() -> None:
    document = image_document(size=(100, 80), nine_slice=None)
    reviewed = dict(plan_compile.analyze_capabilities(document))
    reviewed["node:image"] = reviewed["node:image"].model_copy(
        update={"rule_id": "fgui.native.container"}
    )

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.node_role_incoherent"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_reviewed_clip_source_requires_an_actual_native_clip_role() -> None:
    document = generic_primitives_document()
    reviewed = dict(plan_compile.analyze_capabilities(document))
    reviewed["node:root"] = reviewed["node:root"].model_copy(
        update={"rule_id": "fgui.native.clip_source"}
    )

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.node_role_incoherent"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_component_reference_without_verified_mapping_is_not_emitted() -> None:
    document = component_document(MappingStatus.VERIFIED)
    node = document.nodes["node:instance"].model_copy(
        update={"semantic": UIRSemantic(status="candidate")}
    )
    document = document.model_copy(
        update={"nodes": {node.id: node}, "mapping_decisions": {}}
    )
    reviewed = {
        node.id: CapabilityDecision(
            id="decision:reviewed-component",
            nodeRef=node.id,
            status=CapabilityStatus.NATIVE,
            ruleId="fgui.native.component_reference",
            ruleVersion=1,
            evidence=("fixture.reviewed",),
        )
    }

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.node_role_incoherent"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_duplicate_reviewed_decision_ids_are_quarantined() -> None:
    document = generic_primitives_document()
    analyzed = dict(plan_compile.analyze_capabilities(document))
    duplicate = analyzed["node:text"].model_copy(
        update={"id": analyzed["node:root"].id}
    )
    reviewed = {**analyzed, "node:text": duplicate}

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.id_duplicate" for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_reviewed_decision_rule_version_mismatch_is_quarantined() -> None:
    document = generic_primitives_document()
    analyzed = dict(plan_compile.analyze_capabilities(document, rule_version=1))

    plan = compile_fgui_plan(document, rule_version=2, decisions=analyzed)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.rule_version_mismatch"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_cyclic_uir_graph_is_blocked_without_recursion_error() -> None:
    first = _node("node:first", "FRAME", parent_id="node:second", children=("node:second",))
    second = _node("node:second", "FRAME", parent_id=first.id, children=(first.id,))
    document = _document((first.id,), {first.id: first, second.id: second})

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert any(item.code == "fgui.node.cycle" for item in plan.diagnostics)


def test_unsupported_reviewed_decision_never_emits_a_native_node() -> None:
    node = _node("node:text", "TEXT", text={"content": "Do not compile"})
    document = _document((node.id,), {node.id: node})
    decisions = {
        node.id: CapabilityDecision(
            id="decision:unsupported",
            nodeRef=node.id,
            status=CapabilityStatus.UNSUPPORTED,
            ruleId="fgui.native.text",
            ruleVersion=1,
            evidence=("fixture.reviewed",),
            blocking=True,
        )
    }

    plan = compile_fgui_plan(document, decisions=decisions)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(
        item.code == "fgui.decision.status_rule_incoherent"
        for item in plan.diagnostics
    )


def test_raster_fallback_decisions_require_raster_rule_and_resource() -> None:
    node = _node("node:frame", "FRAME")
    document = _document((node.id,), {node.id: node})
    native_rule = {
        node.id: CapabilityDecision(
            id="decision:raster-native-rule",
            nodeRef=node.id,
            status=CapabilityStatus.RASTER_FALLBACK,
            ruleId="fgui.native.container",
            ruleVersion=1,
            evidence=("fixture.reviewed",),
        )
    }
    missing_resource = {
        node.id: CapabilityDecision(
            id="decision:raster-missing-resource",
            nodeRef=node.id,
            status=CapabilityStatus.RASTER_FALLBACK,
            ruleId="fgui.fallback.raster_subtree",
            ruleVersion=1,
            evidence=("fixture.reviewed",),
        )
    }

    incoherent = compile_fgui_plan(document, decisions=native_rule)
    missing = compile_fgui_plan(document, decisions=missing_resource)

    assert incoherent.bindable is False
    assert incoherent.nodes == {}
    assert any(
        item.code == "fgui.decision.status_rule_incoherent"
        for item in incoherent.diagnostics
    )
    assert missing.bindable is False
    assert missing.nodes == {}
    assert any(
        item.code == "fgui.decision.raster_resource_missing"
        for item in missing.diagnostics
    )


def test_plan_decisions_are_sorted_independently_of_caller_mapping_order() -> None:
    document = generic_primitives_document()
    analyzed = plan_compile.analyze_capabilities(document)
    ascending = {node_id: analyzed[node_id] for node_id in sorted(analyzed)}
    descending = {node_id: analyzed[node_id] for node_id in sorted(analyzed, reverse=True)}

    first = compile_fgui_plan(document, decisions=ascending)
    second = compile_fgui_plan(document, decisions=descending)

    assert list(first.decisions) == sorted(ascending)
    assert first.model_dump(mode="json", by_alias=True) == second.model_dump(
        mode="json", by_alias=True
    )


def opaque_facts_document(
    style: dict[str, object], variants: dict[str, str]
) -> UIRDocument:
    mapping = UIRMappingDecision(
        id="decision:component",
        nodeRef="node:instance",
        candidateKey="common_primary_button",
        status=MappingStatus.VERIFIED,
        confidence=1.0,
        ruleSource="fixture",
        evidence=("fixture.mapping",),
    )
    root = _node("node:root", "FRAME", children=("node:text", "node:instance"))
    text = _node(
        "node:text",
        "TEXT",
        parent_id=root.id,
        text={"content": "Stable facts", "style": style},
    )
    instance = _node(
        "node:instance",
        "INSTANCE",
        parent_id=root.id,
        decision_ref=mapping.id,
        component=UIRComponentInstance(variantProperties=variants),
    ).model_copy(
        update={"conversion": UIRConversion(mode=ConversionMode.COMPONENT_REFERENCE)}
    )
    return _document(
        (root.id,),
        {root.id: root, text.id: text, instance.id: instance},
        mapping_decisions={mapping.id: mapping},
    )


def test_typed_plan_facts_are_canonicalized_independently_of_insertion_order() -> None:
    first = opaque_facts_document(
        {"fontCandidates": ["Inter", "Arial"], "color": "#ffffff"},
        {"state": "normal", "size": "large"},
    )
    second = opaque_facts_document(
        {"color": "#ffffff", "fontCandidates": ["Inter", "Arial"]},
        {"size": "large", "state": "normal"},
    )

    first_plan = compile_fgui_plan(first)
    second_plan = compile_fgui_plan(second)

    text = next(node.text for node in first_plan.nodes.values() if node.type == "text")
    assert text is not None
    assert list(text.style_facts) == ["color", "fontCandidates"]
    assert text.style_facts["fontCandidates"] == ("Inter", "Arial")
    assert any(
        item.code == "fgui.component.definition_missing"
        for item in first_plan.diagnostics
    )
    assert first_plan.model_dump_json(by_alias=True) == second_plan.model_dump_json(
        by_alias=True
    )


@pytest.mark.parametrize("kind", ["rectangle", "roundedRectangle"])
def test_rectangle_masks_compile_as_native_clip(kind: str) -> None:
    plan = compile_fgui_plan(mask_document(kind=kind))

    mask = only_mask(plan)
    assert mask.mode == "nativeClip"
    assert mask.kind == kind
    assert mask.mask_node_ref == "node:mask"
    assert mask.content_node_refs == ("node:content", "node:group")
    assert mask.corner_radii == ((12.0, 12.0, 12.0, 12.0) if kind == "roundedRectangle" else None)
    assert only_node(plan).mask_ref == mask.id
    assert plan.bindable is True


def test_clips_content_compiles_as_a_native_container_clip() -> None:
    root = _node(
        "node:root",
        "FRAME",
        children=("node:content",),
        visual={"clipsContent": True, "cornerRadius": 12},
    )
    content = _node(
        "node:content",
        "TEXT",
        parent_id=root.id,
        text={"content": "Clipped"},
    )
    document = _document((root.id,), {root.id: root, content.id: content})

    plan = compile_fgui_plan(document)

    mask = only_mask(plan)
    target = only_node(plan)
    assert mask.mode == "nativeClip"
    assert mask.kind == "roundedRectangle"
    assert mask.mask_node_ref == root.id
    assert mask.content_node_refs == (content.id,)
    assert mask.corner_radii == (12.0, 12.0, 12.0, 12.0)
    assert target.mask_ref == mask.id
    assert plan.bindable is True
    assert validate_fgui_plan(plan) == ()


def test_nested_native_clips_keep_the_inner_frame_in_the_root_display_tree() -> None:
    root = _node(
        "node:root",
        "FRAME",
        children=("node:inner",),
        visual={"clipsContent": True},
    )
    inner = _node(
        "node:inner",
        "FRAME",
        parent_id=root.id,
        children=("node:content",),
        visual={"clipsContent": True},
    )
    content = _node(
        "node:content",
        "TEXT",
        parent_id=inner.id,
        text={"content": "Nested"},
    )
    document = _document(
        (root.id,),
        {root.id: root, inner.id: inner, content.id: content},
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert len(plan.masks) == 2
    assert validate_fgui_plan(plan) == ()
    manifest = compile_new_project_manifest(
        plan,
        NewProjectConfig(
            projectName="NestedClips",
            packageName="Generated",
            fairyGuiVersion="6.1.4",
            publishTarget="unity",
        ),
        (),
    )
    assert len(manifest.components) == 1
    root_component = manifest.components[0]
    root_object = next(
        item for item in root_component.objects if item.parent_object_ref is None
    )
    inner_object = next(
        item for item in root_component.objects if item.uir_node_ref == inner.id
    )
    content_object = next(
        item for item in root_component.objects if item.uir_node_ref == content.id
    )
    assert root_object.mask_mode == MaskMode.NATIVE_CLIP
    assert inner_object.mask_mode is None
    assert inner_object.component_ref is None
    assert content_object.parent_object_ref == inner_object.id
    review = build_new_project_designer_review(
        manifest,
        plan,
        build_id="a" * 32,
        generation=1,
    )
    assert review.approvable is True
    assert all(item.geometry_valid and item.text_valid for item in review.component_reviews)


def test_instance_container_clips_content_natively() -> None:
    root = _node(
        "node:root",
        "FRAME",
        children=("node:instance",),
    )
    instance = _node(
        "node:instance",
        "INSTANCE",
        parent_id=root.id,
        children=("node:content",),
        visual={"clipsContent": True},
    )
    content = _node(
        "node:content",
        "TEXT",
        parent_id=instance.id,
        text={"content": "Clipped instance"},
    )
    document = _document(
        (root.id,),
        {root.id: root, instance.id: instance, content.id: content},
    )

    plan = compile_fgui_plan(document)

    assert not any(
        item.code == "fgui.mask.source_role_invalid"
        for item in plan.diagnostics
    )
    assert only_mask(plan).mask_node_ref == instance.id
    assert validate_fgui_plan(plan) == ()


def test_transform_group_preserves_child_hierarchy_as_native_container() -> None:
    root = _node(
        "node:root",
        "FRAME",
        children=("node:transform",),
    )
    transform = _node(
        "node:transform",
        "TRANSFORM_GROUP",
        parent_id=root.id,
        children=("node:content",),
    )
    content = _node(
        "node:content",
        "TEXT",
        parent_id=transform.id,
        text={"content": "Rotated child"},
    )
    document = _document(
        (root.id,),
        {root.id: root, transform.id: transform, content.id: content},
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    transformed = next(
        node for node in plan.nodes.values() if node.uir_node_ref == transform.id
    )
    assert transformed.type == "container"
    assert transformed.children
    assert validate_fgui_plan(plan) == ()


def test_explicit_mask_and_clips_content_block_until_mask_composition_is_supported() -> None:
    document = mask_document(kind="image")
    root = document.nodes["node:root"]
    root = root.model_copy(
        update={"visual": {**root.visual, "clipsContent": True}}
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, root.id: root}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert plan.masks == {}
    assert any(
        item.code == "fgui.mask.clip_composition_unsupported"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_simple_image_mask_compiles_as_native_mask() -> None:
    plan = compile_fgui_plan(mask_document(kind="image"))

    assert only_mask(plan).mode == "nativeMask"
    assert only_mask(plan).kind == "image"
    assert plan.bindable is True


def test_native_clip_accepts_rasterized_visual_content() -> None:
    document = mask_document(kind="rectangle")
    asset = UIRAsset(id="asset:clipped-raster", logicalId="clipped-raster", mimeType="image/png", sha256="c" * 64)
    content = document.nodes["node:content"].model_copy(
        update={"conversion": UIRConversion(mode=ConversionMode.RASTER_FALLBACK, reasons=("gradient_paint",), assetRef=asset.id)}
    )
    document = document.model_copy(update={"nodes": {**document.nodes, content.id: content}, "assets": {**document.assets, asset.id: asset}})

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert only_mask(plan).mode == "nativeClip"
    assert plan.decisions[content.id].status == "rasterFallback"
    assert validate_fgui_plan(plan) == ()


def test_native_clip_accepts_nonblocking_editable_text_content() -> None:
    document = mask_document(kind="rectangle")
    content = document.nodes["node:content"].model_copy(
        update={
            "source": document.nodes["node:content"].source.model_copy(
                update={"type": "TEXT"}
            ),
            "text": UIRText(
                content="Buy now",
                style=UIRTextStyle(fontSize=20),
                runs=(
                    UIRTextRun(content="Buy ", style=UIRTextStyle(fontSize=20)),
                    UIRTextRun(content="now", style=UIRTextStyle(fontSize=24)),
                ),
            ),
        }
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, content.id: content}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert any(node.type == "text" for node in plan.nodes.values())
    assert validate_fgui_plan(plan) == ()


@pytest.mark.parametrize("kind", ["boolean", "gradient", "blur", "blend"])
def test_complex_mask_rasterizes_only_safe_subtree(kind: str) -> None:
    document = mask_document(kind=kind, safe_raster=True)

    plan = compile_fgui_plan(document)

    raster = next(node for node in plan.nodes.values() if node.type == "rasterSubtree")
    emitted_source_ids = {node.uir_node_ref for node in plan.nodes.values()}
    source_descendant_ids = set(document.nodes["node:root"].children) | {
        "node:grandchild"
    }
    assert raster.uir_node_ref == "node:root"
    assert raster.resource_ref in plan.resources
    assert only_mask(plan).mode == "rasterSubtree"
    assert source_descendant_ids.isdisjoint(emitted_source_ids)
    assert plan.bindable is True


@pytest.mark.parametrize("feature_node_id", ["node:root", "node:content"])
def test_mask_rasterization_never_consumes_blocking_behavior(
    feature_node_id: str,
) -> None:
    document = mask_document(kind="blur", safe_raster=True)
    feature_node = document.nodes[feature_node_id].model_copy(
        update={"interactions": ({"trigger": "ON_CLICK"},)}
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, feature_node.id: feature_node}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert plan.masks == {}
    assert feature_node_id in plan.decisions
    assert plan.decisions[feature_node_id].rule_id == "fgui.unsupported.interaction"
    assert validate_fgui_plan(plan) == ()


def test_native_mask_never_overrides_blocking_source_behavior() -> None:
    document = mask_document(kind="rectangle")
    source = document.nodes["node:mask"].model_copy(
        update={"interactions": ({"trigger": "ON_CLICK"},)}
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, source.id: source}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert plan.masks == {}
    assert plan.decisions[source.id].rule_id == "fgui.unsupported.interaction"
    assert validate_fgui_plan(plan) == ()


def test_reviewed_blocking_mask_content_suppresses_the_orphan_clip_source() -> None:
    document = mask_document(kind="rectangle")
    reviewed = dict(plan_compile.analyze_capabilities(document))
    reviewed["node:content"] = CapabilityDecision(
        id="decision:reviewed-content-unsupported",
        nodeRef="node:content",
        status=CapabilityStatus.UNSUPPORTED,
        ruleId="fgui.unsupported.interaction",
        ruleVersion=1,
        evidence=("fixture.reviewed",),
        reasons=("interaction_semantics_out_of_scope",),
        blocking=True,
    )

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.masks == {}
    assert not any(
        node.uir_node_ref == "node:mask" for node in plan.nodes.values()
    )
    assert validate_fgui_plan(plan) == ()


def test_unsupported_native_mask_content_suppresses_the_orphan_clip_source() -> None:
    document = mask_document(kind="rectangle")
    content = document.nodes["node:content"].model_copy(
        update={
            "source": document.nodes["node:content"].source.model_copy(
                update={"type": "SLICE"}
            )
        }
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, content.id: content}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert plan.masks == {}
    assert not any(
        node.uir_node_ref == "node:mask" for node in plan.nodes.values()
    )
    assert validate_fgui_plan(plan) == ()


def test_missing_or_cross_parent_mask_is_blocking() -> None:
    for document in (
        mask_document(kind="rectangle", source_missing=True),
        mask_document(kind="rectangle", cross_parent=True),
    ):
        plan = compile_fgui_plan(document)
        assert plan.bindable is False
        assert any(
            item.code
            in {"fgui.mask.source_missing", "fgui.mask.invalid_hierarchy"}
            for item in plan.diagnostics
        )


def test_invalid_mask_produces_a_semantically_valid_review_artifact() -> None:
    plan = compile_fgui_plan(mask_document(kind="rectangle", source_missing=True))

    assert plan.bindable is False
    assert validate_fgui_plan(plan) == ()
    assert any(
        item.code == "fgui.mask.source_missing" and item.node_id == "node:root"
        for item in plan.diagnostics
    )


def test_complex_mask_without_a_valid_raster_asset_is_blocking() -> None:
    plan = compile_fgui_plan(mask_document(kind="blur", safe_raster=False))

    assert plan.bindable is False
    assert any(
        item.code == "fgui.visual.effect_unsupported" for item in plan.diagnostics
    )


def test_raster_consumed_unsupported_descendants_do_not_block_or_emit() -> None:
    document = mask_document(kind="boolean", safe_raster=True)
    unsupported_mask = document.nodes["node:mask"].model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.UNSUPPORTED,
                reasons=("complex_mask_source",),
            )
        }
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, unsupported_mask.id: unsupported_mask}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert "node:mask" not in {node.uir_node_ref for node in plan.nodes.values()}
    assert "node:mask" not in plan.decisions


def test_native_rectangle_clip_preserves_geometry_without_an_image_asset() -> None:
    document = mask_document(kind="rectangle")
    mask_source = document.nodes["node:mask"].model_copy(
        update={"conversion": UIRConversion(mode=ConversionMode.NATIVE)}
    )
    document = document.model_copy(
        update={
            "nodes": {**document.nodes, mask_source.id: mask_source},
            "assets": {},
        }
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert only_mask(plan).mode == "nativeClip"
    planned_mask_source = next(
        node for node in plan.nodes.values() if node.uir_node_ref == "node:mask"
    )
    assert planned_mask_source.type == "graph"
    assert planned_mask_source.graph is not None
    assert planned_mask_source.resource_ref is None
    assert planned_mask_source.transform.bounds == document.nodes["node:mask"].geometry.resolved_bounds


def test_native_kind_with_complex_effects_uses_safe_raster_fallback() -> None:
    document = mask_document(kind="rectangle", safe_raster=True)
    root = document.nodes["node:root"]
    mask_facts = dict(root.visual["mask"])
    mask_facts["effects"] = ["blur"]
    root = root.model_copy(update={"visual": {"mask": mask_facts}})
    document = document.model_copy(
        update={"nodes": {**document.nodes, root.id: root}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert only_mask(plan).mode == "rasterSubtree"


def test_invalid_nested_mask_is_consumed_by_valid_outer_raster() -> None:
    document = mask_document(kind="boolean", safe_raster=True)
    group = document.nodes["node:group"].model_copy(
        update={"visual": {"mask": {"unexpected": True}}}
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, group.id: group}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert len(plan.masks) == 1
    assert not any(item.node_id == group.id for item in plan.diagnostics)


@pytest.mark.parametrize("nested_kind", ["image", "rectangle"])
def test_native_mask_fully_inside_raster_is_absorbed(nested_kind: str) -> None:
    document = nested_native_mask_document(nested_kind)
    group = document.nodes["node:group"]

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert len(plan.masks) == 1
    assert {node.uir_node_ref for node in plan.nodes.values()} == {"node:root"}
    assert "asset:nested-mask" not in plan.resources
    assert not any(item.node_id == group.id for item in plan.diagnostics)


def test_raster_mask_resource_records_fallback_reason() -> None:
    plan = compile_fgui_plan(mask_document(kind="blend", safe_raster=True))

    mask = only_mask(plan)
    assert mask.resource_ref is not None
    assert plan.resources[mask.resource_ref].reason == "mask_raster_fallback"


def test_strict_ancestor_raster_root_keeps_mask_plan_and_consumes_container() -> None:
    document = mask_document(kind="gradient", safe_raster=True)
    container = document.nodes["node:root"]
    mask_facts = dict(container.visual["mask"])
    mask_facts["safeRasterRootRef"] = "node:safe-root"
    container = container.model_copy(
        update={
            "parent_id": "node:safe-root",
            "visual": {"mask": mask_facts},
            "conversion": UIRConversion(mode=ConversionMode.NATIVE),
        }
    )
    safe_root = _node(
        "node:safe-root",
        "FRAME",
        children=(container.id,),
        asset_ref="asset:mask-raster",
    )
    document = document.model_copy(
        update={
            "roots": (safe_root.id,),
            "nodes": {**document.nodes, container.id: container, safe_root.id: safe_root},
        }
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is True
    assert only_mask(plan).mode == "rasterSubtree"
    assert {node.uir_node_ref for node in plan.nodes.values()} == {safe_root.id}
    assert only_node(plan).mask_ref == only_mask(plan).id


def test_reviewed_native_decision_cannot_override_complex_mask_requirement() -> None:
    document = mask_document(kind="boolean", safe_raster=True)
    reviewed = dict(plan_compile.analyze_capabilities(document))
    reviewed["node:root"] = CapabilityDecision(
        id="decision:reviewed-native-mask-root",
        nodeRef="node:root",
        status=CapabilityStatus.NATIVE,
        ruleId="fgui.native.container",
        ruleVersion=1,
        evidence=("fixture.reviewed",),
    )

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert not any(node.uir_node_ref == "node:root" for node in plan.nodes.values())
    assert plan.masks == {}
    assert "asset:mask-raster" not in plan.resources
    assert plan.decisions["node:root"].id.startswith("decision:review-invalid:")
    assert any(
        item.code == "fgui.decision.node_role_incoherent"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


def test_native_clip_does_not_override_explicit_unsupported_source() -> None:
    document = mask_document(kind="rectangle")
    mask_source = document.nodes["node:mask"].model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.UNSUPPORTED,
                reasons=("clip_geometry_unresolved",),
            )
        }
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, mask_source.id: mask_source}}
    )

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert plan.masks == {}
    assert plan.decisions[mask_source.id].status == "unsupported"
    assert not any(node.uir_node_ref == mask_source.id for node in plan.nodes.values())


def test_reviewed_native_promotion_cannot_override_unsupported_clip_source() -> None:
    document = mask_document(kind="rectangle")
    mask_source = document.nodes["node:mask"].model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.UNSUPPORTED,
                reasons=("clip_geometry_unresolved",),
            )
        }
    )
    document = document.model_copy(
        update={"nodes": {**document.nodes, mask_source.id: mask_source}}
    )
    reviewed = dict(plan_compile.analyze_capabilities(document))
    reviewed[mask_source.id] = CapabilityDecision(
        id="decision:reviewed-native-clip-source",
        nodeRef=mask_source.id,
        status=CapabilityStatus.NATIVE,
        ruleId="fgui.native.clip_source",
        ruleVersion=1,
        evidence=("fixture.reviewed",),
    )

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.masks == {}
    assert not any(node.uir_node_ref == mask_source.id for node in plan.nodes.values())
    assert plan.decisions[mask_source.id].id.startswith("decision:review-invalid:")
    assert any(
        item.code == "fgui.decision.node_role_incoherent"
        for item in plan.diagnostics
    )
    assert any(
        item.code in {
            "fgui.decision.node_role_incoherent",
            "fgui.decision.safety_override_forbidden",
        }
        and item.node_id == mask_source.id
        for item in plan.diagnostics
    )


def test_native_and_raster_target_collision_is_order_independent_and_atomic() -> None:
    documents = (
        colliding_mask_document(
            safe_id="node:a-safe", raster_container_id="node:z-raster"
        ),
        colliding_mask_document(
            safe_id="node:z-safe", raster_container_id="node:a-raster"
        ),
    )

    outcomes = []
    for document in documents:
        plan = compile_fgui_plan(document)
        outcomes.append(
            (
                plan.bindable,
                len(plan.masks),
                len(plan.nodes),
                len(plan.resources),
                tuple(sorted(item.code for item in plan.diagnostics)),
            )
        )

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][:4] == (False, 0, 0, 0)
    assert set(outcomes[0][4]) == {"fgui.mask.target_collision"}


def test_reviewed_clip_source_cannot_use_text_role() -> None:
    document = mask_document(kind="rectangle")
    reviewed = dict(plan_compile.analyze_capabilities(document))
    reviewed["node:mask"] = CapabilityDecision(
        id="decision:reviewed-clip-as-text",
        nodeRef="node:mask",
        status=CapabilityStatus.NATIVE,
        ruleId="fgui.native.text",
        ruleVersion=1,
        evidence=("fixture.reviewed",),
    )

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.masks == {}
    assert not any(node.uir_node_ref == "node:mask" for node in plan.nodes.values())
    assert any(
        item.code == "fgui.decision.node_role_incoherent"
        and item.node_id == "node:mask"
        for item in plan.diagnostics
    )
    assert validate_fgui_plan(plan) == ()


@pytest.mark.parametrize("missing_resource", [False, True])
def test_reviewed_image_mask_source_requires_image_role_and_resource(
    missing_resource: bool,
) -> None:
    document = mask_document(kind="image")
    if missing_resource:
        mask_source = document.nodes["node:mask"].model_copy(
            update={"conversion": UIRConversion(mode=ConversionMode.NATIVE)}
        )
        document = document.model_copy(
            update={
                "nodes": {**document.nodes, mask_source.id: mask_source},
                "assets": {},
            }
        )
    reviewed = dict(plan_compile.analyze_capabilities(document))
    reviewed["node:mask"] = CapabilityDecision(
        id="decision:reviewed-image-wrong-role",
        nodeRef="node:mask",
        status=CapabilityStatus.NATIVE,
        ruleId=(
            "fgui.native.image" if missing_resource else "fgui.native.container"
            ),
            ruleVersion=1,
            evidence=("fixture.reviewed",),
    )

    plan = compile_fgui_plan(document, decisions=reviewed)

    assert plan.bindable is False
    assert plan.masks == {}
    assert not any(node.uir_node_ref == "node:mask" for node in plan.nodes.values())
    expected_codes = (
        {"fgui.decision.node_role_incoherent", "fgui.decision.resource_requirement_incoherent"}
        if missing_resource
        else {"fgui.decision.node_role_incoherent"}
    )
    assert expected_codes.issubset(
        {item.code for item in plan.diagnostics if item.node_id == "node:mask"}
    )


@pytest.mark.parametrize("case", ["nested_consumed", "invalid_image_source"])
def test_default_and_canonical_reviewed_mask_plans_are_equivalent(case: str) -> None:
    if case == "nested_consumed":
        document = nested_native_mask_document("image")
    else:
        document = mask_document(kind="image")
        mask_source = document.nodes["node:mask"].model_copy(
            update={"conversion": UIRConversion(mode=ConversionMode.NATIVE)}
        )
        document = document.model_copy(
            update={
                "nodes": {**document.nodes, mask_source.id: mask_source},
                "assets": {},
            }
        )

    default_plan = compile_fgui_plan(document)
    reviewed_plan = compile_fgui_plan(
        document,
        decisions=plan_compile.analyze_capabilities(document),
    )

    assert default_plan.model_dump_json(by_alias=True) == reviewed_plan.model_dump_json(
        by_alias=True
    )


@pytest.mark.parametrize("private_field", ["document", "profile"])
def test_private_compiler_headers_are_quarantined_before_stable_id_generation(
    private_field: str,
) -> None:
    document = generic_primitives_document()
    profile_version = "fgui-6.1.4-v1"
    if private_field == "document":
        document = document.model_copy(
            update={"document_id": r"C:\private\document.json"}
        )
    else:
        profile_version = r"C:\private\profile.json"

    plan = compile_fgui_plan(document, profile_version=profile_version)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert validate_fgui_plan(plan) == ()
    encoded = canonical_plan_bytes(plan)
    assert b"document.json" not in encoded
    assert b"profile.json" not in encoded


def test_blank_profile_is_quarantined_before_stable_id_generation() -> None:
    plan = compile_fgui_plan(generic_primitives_document(), profile_version=" ")

    assert plan.bindable is False
    assert plan.nodes == {}
    assert plan.profile_version == "fgui-profile:quarantined"
    assert validate_fgui_plan(plan) == ()


@pytest.mark.parametrize("field", ["document", "profile"])
def test_non_string_compiler_headers_are_quarantined_without_exception(field: str) -> None:
    document = generic_primitives_document()
    profile_version: str | None = "fgui-6.1.4-v1"
    if field == "document":
        document = document.model_copy(update={"document_id": None})
    else:
        profile_version = None

    plan = compile_fgui_plan(document, profile_version=profile_version)  # type: ignore[arg-type]

    assert plan.bindable is False
    assert plan.nodes == {}
    assert validate_fgui_plan(plan) == ()


def test_invalid_uir_asset_identity_is_quarantined_without_exception() -> None:
    document = image_document(size=(100, 80), nine_slice=None)
    asset = next(iter(document.assets.values())).model_copy(
        update={"logical_id": " "}
    )
    document = document.model_copy(update={"assets": {asset.id: asset}})

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(item.code == "uir.identity_invalid" for item in plan.diagnostics)
    assert validate_fgui_plan(plan) == ()


def test_deep_uir_compilation_blocks_without_recursive_failure() -> None:
    template = _node("node:template", "FRAME")
    node_count = 1_100
    nodes = {}
    for index in range(node_count):
        node_id = f"node:{index}"
        child_id = f"node:{index + 1}" if index + 1 < node_count else None
        nodes[node_id] = template.model_copy(
            update={
                "id": node_id,
                "source": template.source.model_copy(
                    update={"node_id": node_id, "name": node_id}
                ),
                "parent_id": None if index == 0 else f"node:{index - 1}",
                "children": () if child_id is None else (child_id,),
            }
        )
    document = _document(("node:0",), nodes)

    plan = compile_fgui_plan(document)

    assert plan.bindable is False
    assert plan.nodes == {}
    assert any(item.code == "uir.tree_depth_exceeded" for item in plan.diagnostics)
    assert validate_fgui_plan(plan) == ()


def test_root_container_solid_visual_style_compiles_as_editable_graph() -> None:
    root = _node(
        "node:root",
        "FRAME",
        visual={
            "fills": (
                {"type": "SOLID", "color": {"r": 1, "g": 0, "b": 0}},
            )
        },
    )

    plan = compile_fgui_plan(_document((root.id,), {root.id: root}))

    assert plan.bindable is True
    planned = next(iter(plan.nodes.values()))
    assert planned.type == "graph"
    assert planned.graph is not None
    assert planned.graph.fill_color == "#ffff0000"
    assert validate_fgui_plan(plan) == ()


def test_editable_graph_ignores_invisible_effects() -> None:
    rectangle = _node(
        "node:rectangle",
        "RECTANGLE",
        visual={
            "fills": ({"type": "SOLID", "color": {"r": 1, "g": 0, "b": 0}},),
            "effects": ({"type": "DROP_SHADOW", "visible": False},),
            "cornerRadius": 12,
        },
    )

    plan = compile_fgui_plan(_document((rectangle.id,), {rectangle.id: rectangle}))

    assert plan.bindable is True
    assert only_node(plan).type == "graph"
    assert validate_fgui_plan(plan) == ()


def test_explicit_raster_fallback_absorbs_auto_layout_rendering() -> None:
    asset = UIRAsset(
        id="asset:raster",
        logicalId="raster",
        mimeType="image/png",
        sha256="d" * 64,
    )
    frame = _node("node:frame", "FRAME", asset_ref=asset.id).model_copy(
        update={
            "layout": {
                "layoutMode": "HORIZONTAL",
                "primaryAxisSizingMode": "FIXED",
                "counterAxisSizingMode": "AUTO",
            },
            "conversion": UIRConversion(
                mode=ConversionMode.RASTER_FALLBACK,
                reasons=("mask_composite",),
                assetRef=asset.id,
            ),
        }
    )

    plan = compile_fgui_plan(
        _document((frame.id,), {frame.id: frame}, assets={asset.id: asset})
    )

    assert plan.bindable is True
    assert only_node(plan).type == "rasterSubtree"
    assert validate_fgui_plan(plan) == ()


def test_auto_layout_keeps_native_children_at_resolved_geometry() -> None:
    child = _node("node:child", "FRAME", parent_id="node:frame")
    frame = _node("node:frame", "FRAME", children=(child.id,)).model_copy(
        update={
            "layout": {
                "layoutMode": "HORIZONTAL",
                "primaryAxisSizingMode": "AUTO",
                "counterAxisSizingMode": "AUTO",
            }
        }
    )

    plan = compile_fgui_plan(
        _document((frame.id,), {frame.id: frame, child.id: child})
    )

    assert plan.bindable is True
    assert {node.type.value for node in plan.nodes.values()} == {"container"}
    assert validate_fgui_plan(plan) == ()


def test_readable_instance_ignores_only_invisible_paints() -> None:
    instance = _node(
        "node:instance",
        "INSTANCE",
        children=("node:child",),
        visual={
            "fills": (
                {"type": "SOLID", "visible": False, "color": {"r": 1, "g": 0, "b": 0}},
            ),
            "strokeWeight": 1,
        },
    )
    child = _node("node:child", "FRAME", parent_id=instance.id)

    plan = compile_fgui_plan(
        _document((instance.id,), {instance.id: instance, child.id: child})
    )

    assert plan.bindable is True
    assert len(plan.nodes) == 2
    assert validate_fgui_plan(plan) == ()


def test_readable_instance_visible_container_paint_does_not_collapse_child_tree() -> None:
    instance = _node(
        "node:instance",
        "INSTANCE",
        children=("node:child",),
        visual={
            "fills": (
                {"type": "SOLID", "color": {"r": 1, "g": 0, "b": 0}},
            )
        },
    )
    child = _node("node:child", "FRAME", parent_id=instance.id)

    plan = compile_fgui_plan(
        _document((instance.id,), {instance.id: instance, child.id: child})
    )

    assert plan.bindable is True
    assert len(plan.nodes) == 2
    assert all(item.code != "fgui.unsupported.visual_style" for item in plan.diagnostics)
