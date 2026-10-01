import hashlib

from figma_to_fgui.models import Bounds, Diagnostic, Severity
from figma_to_fgui.uir_models import (
    ConversionMode,
    MappingStatus,
    UIFontPolicy,
    UIRAsset,
    UIRComponentDefinition,
    UIRConversion,
    UIRDocument,
    UIRGeometry,
    UIRMappingDecision,
    UIRNode,
    UIRNodeSource,
    UIRSemantic,
    UIRSource,
    UIRText,
    UIRTextRun,
    UIRTextStyle,
)
from figma_to_fgui.uir_validate import canonical_uir_bytes, uir_sha256, validate_uir


def valid_document() -> UIRDocument:
    node = UIRNode(
        id="node:root",
        source=UIRNodeSource(
            nodeId="root", type="FRAME", name="Root", fingerprint="b" * 64
        ),
        semantic=UIRSemantic(),
        zIndex=0,
        geometry=UIRGeometry(
            resolvedBounds=Bounds(x=0, y=0, width=100, height=100)
        ),
        conversion=UIRConversion(mode=ConversionMode.NATIVE),
    )
    return UIRDocument(
        documentId="uir:test",
        compilerVersion="uir-v1",
        source=UIRSource(revision="a" * 64, selectionId="selection"),
        roots=(node.id,),
        nodes={node.id: node},
    )


def test_validation_rejects_dangling_root_child_and_decision_refs() -> None:
    document = valid_document()
    root = document.nodes["node:root"].model_copy(
        update={
            "children": ("node:missing",),
            "semantic": UIRSemantic(decisionRef="decision:missing"),
        }
    )
    document = document.model_copy(
        update={"roots": ("node:absent",), "nodes": {root.id: root}}
    )
    assert {item.code for item in validate_uir(document)} >= {
        "uir.root_missing",
        "uir.child_missing",
        "uir.decision_missing",
    }


def test_empty_uir_document_is_not_a_successful_conversion() -> None:
    document = valid_document().model_copy(update={"roots": (), "nodes": {}})

    assert "uir.tree_empty" in {item.code for item in validate_uir(document)}


def test_validation_blocks_unresolved_mapping_conflicts() -> None:
    document = valid_document()
    decision = UIRMappingDecision(
        id="decision:conflict",
        nodeRef="node:root",
        candidateKey="button",
        status=MappingStatus.CONFLICT,
        evidence=("fixture",),
        confidence=0,
        ruleSource="fixture",
    )
    root = document.nodes["node:root"].model_copy(
        update={
            "semantic": UIRSemantic(decisionRef=decision.id),
            "conversion": UIRConversion(mode=ConversionMode.UNSUPPORTED),
        }
    )
    document = document.model_copy(
        update={
            "nodes": {root.id: root},
            "mapping_decisions": {decision.id: decision},
        }
    )
    diagnostics = validate_uir(document)
    assert any(
        item.code == "uir.mapping_conflict" and item.severity == "ERROR"
        for item in diagnostics
    )


def test_private_mapping_and_diagnostic_metadata_is_rejected_and_redacted() -> None:
    document = valid_document()
    decision = UIRMappingDecision(
        id="decision:conflict",
        nodeRef="node:root",
        candidateKey="button",
        status=MappingStatus.CONFLICT,
        evidence=("fixture",),
        confidence=0,
        ruleSource=r"C:\private\mapping.txt",
    )
    root = document.nodes["node:root"].model_copy(
        update={
            "semantic": UIRSemantic(decisionRef=decision.id),
            "conversion": UIRConversion(mode=ConversionMode.UNSUPPORTED),
        }
    )
    private_diagnostic = Diagnostic(
        code="fixture.warning",
        severity=Severity.WARNING,
        message=r"C:\private\diagnostic.txt",
    )
    document = document.model_copy(
        update={
            "nodes": {root.id: root},
            "mapping_decisions": {decision.id: decision},
            "diagnostics": (private_diagnostic,),
        }
    )

    diagnostics = validate_uir(document)
    encoded = canonical_uir_bytes(document)

    assert any(item.code == "uir.private_data_forbidden" for item in diagnostics)
    assert b"mapping.txt" not in encoded
    assert b"diagnostic.txt" not in encoded


def test_private_source_header_metadata_is_rejected_and_redacted() -> None:
    document = valid_document().model_copy(
        update={
            "source": UIRSource(
                revision="a" * 64,
                selectionId=r"C:\private\selection.json",
            )
        }
    )

    diagnostics = validate_uir(document)
    encoded = canonical_uir_bytes(document)

    assert any(item.code == "uir.private_data_forbidden" for item in diagnostics)
    assert b"selection.json" not in encoded


def test_private_typed_node_and_component_provenance_is_rejected_and_redacted() -> None:
    document = valid_document()
    root = document.nodes["node:root"].model_copy(
        update={
            "source": document.nodes["node:root"].source.model_copy(
                update={
                    "node_id": r"C:\private\node.fig",
                    "name": r"C:\private\layer.txt",
                }
            )
        }
    )
    definition = UIRComponentDefinition(
        id="definition:private",
        sourceNodeId=r"C:\private\component.fig",
        name=r"C:\private\component.txt",
    )
    document = document.model_copy(
        update={
            "nodes": {root.id: root},
            "component_definitions": {definition.id: definition},
        }
    )

    diagnostics = validate_uir(document)
    encoded = canonical_uir_bytes(document)

    assert any(item.code == "uir.private_data_forbidden" for item in diagnostics)
    assert b"node.fig" not in encoded
    assert b"layer.txt" not in encoded
    assert b"component.fig" not in encoded
    assert b"component.txt" not in encoded


def test_all_typed_metadata_except_user_text_is_private_policy_scoped() -> None:
    document = valid_document()
    root = document.nodes["node:root"].model_copy(
        update={
            "semantic": UIRSemantic(
                name=r"C:\private\semantic.txt",
                role=r"C:\private\role.txt",
            ),
            "text": UIRText(
                content=r"C:\UI\visible-content",
                style=UIRTextStyle(fontCandidates=(r"C:\private\base.ttf",)),
                runs=(
                    UIRTextRun(
                        content=r"C:\UI\visible-content",
                        style=UIRTextStyle(
                            fontCandidates=(r"C:\private\run.ttf",)
                        ),
                    ),
                ),
                fontPolicy=UIFontPolicy(
                    resolvedFont=r"C:\private\resolved.ttf"
                ),
            ),
            "conversion": UIRConversion(
                mode=ConversionMode.UNSUPPORTED,
                reasons=(r"C:\private\reason.txt",),
            ),
        }
    )
    document = document.model_copy(update={"nodes": {root.id: root}})

    diagnostics = validate_uir(document)
    encoded = canonical_uir_bytes(document)

    assert any(item.code == "uir.private_data_forbidden" for item in diagnostics)
    for private_name in (
        b"semantic.txt",
        b"role.txt",
        b"base.ttf",
        b"run.ttf",
        b"resolved.ttf",
        b"reason.txt",
    ):
        assert private_name not in encoded
    assert b"visible-content" in encoded


def test_deep_tree_is_rejected_without_recursive_validator_failure() -> None:
    document = valid_document()
    template = document.nodes["node:root"]
    node_count = 1_100
    nodes = {}
    for index in range(node_count):
        node_id = f"node:{index}"
        child_id = f"node:{index + 1}" if index + 1 < node_count else None
        nodes[node_id] = template.model_copy(
            update={
                "id": node_id,
                "source": template.source.model_copy(update={"node_id": node_id}),
                "parent_id": None if index == 0 else f"node:{index - 1}",
                "children": () if child_id is None else (child_id,),
            }
        )
    deep = document.model_copy(update={"roots": ("node:0",), "nodes": nodes})

    diagnostics = validate_uir(deep)

    assert any(item.code == "uir.tree_depth_exceeded" for item in diagnostics)


def test_canonical_bytes_and_digest_are_stable() -> None:
    document = valid_document()
    first = canonical_uir_bytes(document)
    second = canonical_uir_bytes(document)
    assert first == second
    assert first.endswith(b"\n")
    assert uir_sha256(document) == hashlib.sha256(first).hexdigest()


def test_validation_rejects_duplicate_ownership_and_parent_asymmetry() -> None:
    document = valid_document()
    child = document.nodes["node:root"].model_copy(
        update={"id": "node:child", "parent_id": "node:wrong", "children": ()}
    )
    first = document.nodes["node:root"].model_copy(
        update={"children": (child.id,)}
    )
    second = first.model_copy(update={"id": "node:other", "children": (child.id,)})
    document = document.model_copy(
        update={
            "roots": (first.id, second.id),
            "nodes": {first.id: first, second.id: second, child.id: child},
        }
    )
    codes = {item.code for item in validate_uir(document)}
    assert "uir.child_multiple_parents" in codes
    assert "uir.parent_mismatch" in codes


def test_tree_validation_reports_duplicates_cycles_unowned_and_unreachable_nodes() -> None:
    document = valid_document()
    root = document.nodes["node:root"].model_copy(
        update={
            "parent_id": "node:child",
            "children": ("node:child", "node:child"),
        }
    )
    child = root.model_copy(
        update={
            "id": "node:child",
            "parent_id": root.id,
            "children": (root.id,),
        }
    )
    orphan = root.model_copy(
        update={"id": "node:orphan", "parent_id": None, "children": ()}
    )
    document = document.model_copy(
        update={
            "roots": (root.id, root.id),
            "nodes": {root.id: root, child.id: child, orphan.id: orphan},
        }
    )

    codes = {item.code for item in validate_uir(document)}
    assert {
        "uir.root_duplicate",
        "uir.root_parent_incoherent",
        "uir.child_multiple_parents",
        "uir.node_cycle",
        "uir.node_unowned",
        "uir.node_unreachable",
    } <= codes


def test_tree_validation_reports_missing_parent_backlink_and_multiple_roots() -> None:
    document = valid_document()
    child = document.nodes["node:root"].model_copy(
        update={"id": "node:child", "parent_id": "node:root", "children": ()}
    )
    document = document.model_copy(
        update={
            "roots": ("node:root", child.id),
            "nodes": {**document.nodes, child.id: child},
        }
    )

    codes = {item.code for item in validate_uir(document)}
    assert "uir.parent_mismatch" in codes
    assert "uir.root_parent_incoherent" in codes


def _mapping_document(status: MappingStatus) -> UIRDocument:
    document = valid_document()
    asset = UIRAsset(
        id="asset:fallback",
        logicalId="fallback",
        mimeType="image/png",
        sha256="c" * 64,
    )
    if status == MappingStatus.VERIFIED:
        semantic_status = "confirmed"
        conversion = UIRConversion(mode=ConversionMode.COMPONENT_REFERENCE)
    elif status == MappingStatus.MISSING:
        semantic_status = "fallback"
        conversion = UIRConversion(
            mode=ConversionMode.RASTER_FALLBACK,
            reasons=("component_mapping_missing",),
            assetRef=asset.id,
        )
    else:
        semantic_status = "candidate"
        conversion = UIRConversion(
            mode=ConversionMode.UNSUPPORTED,
            reasons=("component_mapping_conflict",),
        )
    decision = UIRMappingDecision(
        id=f"decision:{status.value}",
        nodeRef="node:root",
        candidateKey="common_button",
        status=status,
        evidence=("fixture",),
        confidence=1 if status == MappingStatus.VERIFIED else 0,
        ruleSource="fixture",
    )
    node = document.nodes["node:root"].model_copy(
        update={
            "semantic": UIRSemantic(
                name="common_button",
                role="component",
                status=semantic_status,
                decisionRef=decision.id,
            ),
            "conversion": conversion,
        }
    )
    return document.model_copy(
        update={
            "nodes": {node.id: node},
            "assets": ({asset.id: asset} if status == MappingStatus.MISSING else {}),
            "mapping_decisions": {decision.id: decision},
        }
    )


def test_verified_and_missing_mapping_statuses_are_coherent() -> None:
    assert validate_uir(_mapping_document(MappingStatus.VERIFIED)) == ()
    assert validate_uir(_mapping_document(MappingStatus.MISSING)) == ()


def test_mapping_decisions_reject_key_owner_and_shared_ownership_mismatches() -> None:
    document = _mapping_document(MappingStatus.VERIFIED)
    decision = next(iter(document.mapping_decisions.values()))
    root = document.nodes["node:root"]
    second = root.model_copy(
        update={"id": "node:second", "semantic": root.semantic, "parent_id": None}
    )
    wrong_key = document.model_copy(
        update={"mapping_decisions": {"decision:wrong-key": decision}}
    )
    shared = document.model_copy(
        update={
            "roots": (*document.roots, second.id),
            "nodes": {**document.nodes, second.id: second},
        }
    )

    assert "uir.decision_key_mismatch" in {
        item.code for item in validate_uir(wrong_key)
    }
    codes = {item.code for item in validate_uir(shared)}
    assert {
        "uir.decision_multiple_nodes",
        "uir.decision_node_mismatch",
    } <= codes


def test_mapping_decision_orphan_and_status_conversion_incoherence_are_rejected() -> None:
    document = _mapping_document(MappingStatus.MISSING)
    root = document.nodes["node:root"].model_copy(
        update={
            "semantic": UIRSemantic(),
            "conversion": UIRConversion(mode=ConversionMode.NATIVE),
        }
    )
    broken = document.model_copy(update={"nodes": {root.id: root}})

    codes = {item.code for item in validate_uir(broken)}
    assert "uir.decision_orphan" in codes
    assert "uir.mapping_status_incoherent" in codes


def test_asset_map_identity_and_raster_conversion_invariants_are_checked() -> None:
    document = _mapping_document(MappingStatus.MISSING)
    asset = next(iter(document.assets.values())).model_copy(
        update={"id": "asset:actual", "mime_type": "image/jpeg", "export_format": "jpg"}
    )
    root = document.nodes["node:root"].model_copy(
        update={
            "conversion": document.nodes["node:root"].conversion.model_copy(
                update={"asset_ref": "asset:wrong-key"}
            )
        }
    )
    broken = document.model_copy(
        update={
            "nodes": {root.id: root},
            "assets": {"asset:wrong-key": asset},
        }
    )

    codes = {item.code for item in validate_uir(broken)}
    assert "uir.asset_key_mismatch" in codes
    assert "uir.raster_fallback_format_incoherent" in codes


def test_private_typed_asset_identity_is_rejected_and_redacted() -> None:
    document = valid_document()
    asset = UIRAsset(
        id="asset:image",
        logicalId=r"C:\private\asset.png",
        mimeType="image/png",
        sha256="d" * 64,
    )
    root = document.nodes["node:root"].model_copy(
        update={
            "conversion": UIRConversion(
                mode=ConversionMode.NATIVE,
                assetRef=asset.id,
            )
        }
    )
    document = document.model_copy(
        update={"nodes": {root.id: root}, "assets": {asset.id: asset}}
    )

    diagnostics = validate_uir(document)
    encoded = canonical_uir_bytes(document)

    assert any(item.code == "uir.private_data_forbidden" for item in diagnostics)
    assert b"asset.png" not in encoded


def test_embedded_uir_diagnostics_require_complete_public_metadata() -> None:
    blank = Diagnostic(
        code=" ",
        severity=Severity.WARNING,
        message=" ",
        rule_id=" ",
        rule_version=1,
        evidence=("",),
        suggested_action=" ",
        node_id="node:missing",
    )
    document = valid_document().model_copy(update={"diagnostics": (blank,)})

    codes = {item.code for item in validate_uir(document)}

    assert "uir.diagnostic_contract_incomplete" in codes
    assert "uir.diagnostic_node_missing" in codes
