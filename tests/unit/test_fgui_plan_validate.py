import hashlib
import json

import pytest

from figma_to_fgui.fgui_plan_models import (
    CapabilityDecision,
    CapabilityStatus,
    ComponentReferencePlan,
    FGUIPlanDocument,
    MaskPlan,
    NineSlicePlan,
    PlanNodeType,
    TextPlan,
    TextRunPlan,
)
from figma_to_fgui.fgui_plan_validate import (
    canonical_plan_bytes,
    plan_sha256,
    validate_fgui_plan,
)
from figma_to_fgui.models import Diagnostic, Severity


def export_parameters_sha256(
    *, mime_type: str = "image/png", export_format: str = "png", width=None, height=None
) -> str:
    payload = json.dumps(
        {
            "exportFormat": export_format,
            "height": height,
            "mimeType": mime_type,
            "width": width,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def valid_plan() -> FGUIPlanDocument:
    return FGUIPlanDocument.model_validate(
        {
            "documentId": "plan:test",
            "sourceUirSha256": "a" * 64,
            "profileVersion": "fgui-6.1.4-v1",
            "ruleVersion": 1,
            "bindable": True,
            "roots": ("plan:root",),
            "nodes": {
                "plan:root": {
                    "id": "plan:root",
                    "uirNodeRef": "uir:root",
                    "children": ("plan:image", "plan:text"),
                    "zIndex": 0,
                    "type": "container",
                    "transform": {
                        "bounds": {"x": 0, "y": 0, "width": 100, "height": 100}
                    },
                    "decisionRef": "decision:root",
                },
                "plan:text": {
                    "id": "plan:text",
                    "uirNodeRef": "uir:text",
                    "parentId": "plan:root",
                    "zIndex": 0,
                    "type": "text",
                    "transform": {
                        "bounds": {"x": 0, "y": 0, "width": 50, "height": 20}
                    },
                    "text": {"content": "Hello"},
                    "decisionRef": "decision:text",
                },
                "plan:image": {
                    "id": "plan:image",
                    "uirNodeRef": "uir:image",
                    "parentId": "plan:root",
                    "zIndex": 1,
                    "type": "image",
                    "transform": {
                        "bounds": {"x": 0, "y": 20, "width": 50, "height": 50}
                    },
                    "resourceRef": "resource:image",
                    "decisionRef": "decision:image",
                },
            },
            "resources": {
                "resource:image": {
                    "id": "resource:image",
                    "sourceAssetRef": "asset:image",
                    "logicalAssetId": "logical:image",
                    "contentSha256": "b" * 64,
                    "exportParametersSha256": export_parameters_sha256(),
                    "mimeType": "image/png",
                    "exportFormat": "png",
                    "consumers": ("plan:image",),
                }
            },
            "decisions": {
                "uir:root": {
                    "id": "decision:root",
                    "nodeRef": "uir:root",
                    "status": "native",
                    "ruleId": "fgui.native.container",
                    "ruleVersion": 1,
                    "evidence": ("fixture.root",),
                },
                "uir:text": {
                    "id": "decision:text",
                    "nodeRef": "uir:text",
                    "status": "native",
                    "ruleId": "fgui.native.text",
                    "ruleVersion": 1,
                    "evidence": ("fixture.text",),
                },
                "uir:image": {
                    "id": "decision:image",
                    "nodeRef": "uir:image",
                    "status": "native",
                    "ruleId": "fgui.native.image",
                    "ruleVersion": 1,
                    "evidence": ("fixture.image",),
                },
            },
        }
    )


def component_node(
    node_id: str,
    *,
    definition_ref: str,
    uir_node_ref: str,
    parent_id: str | None = None,
) -> dict[str, object]:
    return {
        "id": node_id,
        "uirNodeRef": uir_node_ref,
        "parentId": parent_id,
        "zIndex": 0,
        "type": "componentReference",
        "transform": {"bounds": {"x": 0, "y": 0, "width": 100, "height": 40}},
        "component": {
            "candidateKey": "common_button",
            "definitionRef": definition_ref,
        },
    }


def valid_component_plan() -> FGUIPlanDocument:
    payload = valid_plan().model_dump(mode="json", by_alias=True)
    instance = component_node(
        "plan:instance",
        definition_ref="definition:button",
        uir_node_ref="uir:instance",
    )
    instance["decisionRef"] = "decision:instance"
    definition_root = {
        "id": "definition-node:button",
        "uirNodeRef": "uir:definition-button",
        "zIndex": 0,
        "type": "container",
        "transform": {
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 40}
        },
        "decisionRef": "decision:definition-button",
    }
    payload.update(
        {
            "roots": ["plan:instance"],
            "nodes": {"plan:instance": instance},
            "componentDefinitions": {
                "definition:button": {
                    "id": "definition:button",
                    "name": "Button",
                    "rootNodeRef": "definition-node:button",
                    "nodes": {"definition-node:button": definition_root},
                }
            },
            "resources": {},
            "decisions": {
                "uir:instance": {
                    "id": "decision:instance",
                    "nodeRef": "uir:instance",
                    "status": "native",
                    "ruleId": "fgui.native.component_reference",
                    "ruleVersion": 1,
                    "evidence": ["fixture.instance"],
                },
                "uir:definition-button": {
                    "id": "decision:definition-button",
                    "nodeRef": "uir:definition-button",
                    "status": "native",
                    "ruleId": "fgui.native.container",
                    "ruleVersion": 1,
                    "evidence": ["fixture.definition"],
                },
            },
        }
    )
    return FGUIPlanDocument.model_validate(payload)


def test_plan_v2_component_reference_requires_owned_definition() -> None:
    plan = valid_plan()
    instance = component_node(
        "plan:instance",
        definition_ref="definition:button",
        uir_node_ref="uir:instance",
    )
    plan = plan.model_copy(
        update={
            "bindable": False,
            "roots": ("plan:instance",),
            "nodes": {"plan:instance": plan.nodes["plan:root"].model_validate(instance)},
            "resources": {},
            "decisions": {},
            "component_definitions": {},
        }
    )

    codes = {item.code for item in validate_fgui_plan(plan)}
    assert "fgui.plan.component_definition_missing" in codes


def test_plan_v2_accepts_reachable_self_contained_definition() -> None:
    assert validate_fgui_plan(valid_component_plan()) == ()


def test_plan_v2_validates_definition_identity_tree_reachability_and_ownership() -> None:
    plan = valid_component_plan()
    definition = plan.component_definitions["definition:button"]
    root = definition.nodes[definition.root_node_ref]
    orphan = root.model_copy(
        update={
            "id": "definition-node:orphan",
            "uir_node_ref": "uir:definition-orphan",
            "decision_ref": None,
        }
    )
    malformed = definition.model_copy(
        update={
            "id": "definition:other",
            "nodes": {
                definition.root_node_ref: root,
                "definition-node:wrong-key": orphan,
            },
        }
    )
    plan = plan.model_copy(
        update={"bindable": False, "component_definitions": {"definition:button": malformed}}
    )

    codes = {item.code for item in validate_fgui_plan(plan)}
    assert "fgui.plan.component_definition_key_mismatch" in codes
    assert "fgui.plan.component_definition_node_key_mismatch" in codes
    assert "fgui.plan.component_definition_node_unowned" in codes
    assert "fgui.plan.component_definition_node_unreachable" in codes

    colliding_root = root.model_copy(
        update={"id": "plan:instance", "uir_node_ref": "uir:colliding-definition"}
    )
    colliding_definition = definition.model_copy(
        update={"root_node_ref": "plan:instance", "nodes": {"plan:instance": colliding_root}}
    )
    collision_plan = plan.model_copy(
        update={
            "component_definitions": {"definition:button": colliding_definition}
        }
    )
    assert "fgui.plan.component_definition_node_multiple_owners" in {
        item.code for item in validate_fgui_plan(collision_plan)
    }


def test_plan_v2_rejects_unused_component_definition() -> None:
    plan = valid_component_plan()
    definition = plan.component_definitions["definition:button"]
    unused_root = definition.nodes[definition.root_node_ref].model_copy(
        update={
            "id": "definition-node:unused",
            "uir_node_ref": "uir:definition-unused",
            "decision_ref": None,
        }
    )
    unused = definition.model_copy(
        update={
            "id": "definition:unused",
            "name": "Unused",
            "root_node_ref": unused_root.id,
            "nodes": {unused_root.id: unused_root},
        }
    )
    plan = plan.model_copy(
        update={
            "bindable": False,
            "component_definitions": {
                **plan.component_definitions,
                unused.id: unused,
            },
        }
    )

    assert "fgui.plan.component_definition_unused" in {
        item.code for item in validate_fgui_plan(plan)
    }


def component_definition_reference_chain_plan(
    length: int, *, reverse_ids: bool
) -> FGUIPlanDocument:
    payload = valid_component_plan().model_dump(mode="json", by_alias=True)
    definitions: dict[str, object] = {}
    definition_ids = [
        f"definition:{length - index - 1 if reverse_ids else index:03d}"
        for index in range(length)
    ]
    for index, definition_id in enumerate(definition_ids):
        node_id = f"definition-node:{definition_id.removeprefix('definition:')}"
        node = (
            component_node(
                node_id,
                definition_ref=definition_ids[index + 1],
                uir_node_ref=f"uir:definition-{index}",
            )
            if index < length - 1
            else {
                "id": node_id,
                "uirNodeRef": f"uir:definition-{index}",
                "zIndex": 0,
                "type": "container",
                "transform": {
                    "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}
                },
            }
        )
        definitions[definition_id] = {
            "id": definition_id,
            "name": f"Definition {index}",
            "rootNodeRef": node_id,
            "nodes": {node_id: node},
        }
    payload["bindable"] = False
    payload["nodes"]["plan:instance"]["component"]["definitionRef"] = definition_ids[0]
    payload["componentDefinitions"] = definitions
    payload["decisions"] = {}
    return FGUIPlanDocument.model_validate(payload)


@pytest.mark.parametrize("reverse_ids", [False, True])
@pytest.mark.parametrize(("length", "exceeds"), [(256, False), (257, True)])
def test_plan_v2_limits_component_definition_reference_depth_from_reachable_roots(
    length: int, exceeds: bool, reverse_ids: bool
) -> None:
    plan = component_definition_reference_chain_plan(
        length, reverse_ids=reverse_ids
    )
    codes = {item.code for item in validate_fgui_plan(plan)}

    assert ("fgui.plan.component_definition_depth_exceeded" in codes) is exceeds
    assert "fgui.plan.component_definition_cycle" not in codes


def component_definition_local_tree_plan(
    length: int, *, reverse_ids: bool
) -> FGUIPlanDocument:
    payload = valid_component_plan().model_dump(mode="json", by_alias=True)
    node_ids = [
        f"definition-node:{length - index - 1 if reverse_ids else index:03d}"
        for index in range(length)
    ]
    nodes: dict[str, object] = {}
    for index, node_id in enumerate(node_ids):
        nodes[node_id] = {
            "id": node_id,
            "uirNodeRef": f"uir:local-{index}",
            "parentId": None if index == 0 else node_ids[index - 1],
            "children": [] if index == length - 1 else [node_ids[index + 1]],
            "zIndex": 0,
            "type": "container",
            "transform": {
                "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}
            },
        }
    definition = payload["componentDefinitions"]["definition:button"]
    definition["rootNodeRef"] = node_ids[0]
    definition["nodes"] = nodes
    payload["bindable"] = False
    payload["decisions"] = {}
    return FGUIPlanDocument.model_validate(payload)


@pytest.mark.parametrize("reverse_ids", [False, True])
@pytest.mark.parametrize(("length", "exceeds"), [(256, False), (257, True)])
def test_plan_v2_limits_definition_local_tree_depth_from_its_root(
    length: int, exceeds: bool, reverse_ids: bool
) -> None:
    plan = component_definition_local_tree_plan(length, reverse_ids=reverse_ids)
    codes = {item.code for item in validate_fgui_plan(plan)}

    assert ("fgui.plan.component_definition_tree_depth_exceeded" in codes) is exceeds
    assert "fgui.plan.component_definition_node_cycle" not in codes


def test_plan_v2_local_definition_cycle_is_not_reported_as_depth() -> None:
    payload = component_definition_local_tree_plan(2, reverse_ids=True).model_dump(
        mode="json", by_alias=True
    )
    definition = payload["componentDefinitions"]["definition:button"]
    root_id = definition["rootNodeRef"]
    leaf_id = definition["nodes"][root_id]["children"][0]
    definition["nodes"][leaf_id]["children"] = [root_id]
    plan = FGUIPlanDocument.model_validate(payload)

    codes = {item.code for item in validate_fgui_plan(plan)}
    assert "fgui.plan.component_definition_node_cycle" in codes
    assert "fgui.plan.component_definition_tree_depth_exceeded" not in codes


def test_plan_v2_rejects_recursive_definition_graph() -> None:
    payload = valid_component_plan().model_dump(mode="json", by_alias=True)
    payload["nodes"]["plan:instance"]["component"]["definitionRef"] = "definition:a"
    payload.update(
        {
            "bindable": False,
            "componentDefinitions": {
                "definition:a": {
                    "id": "definition:a",
                    "name": "A",
                    "rootNodeRef": "definition-node:a",
                    "nodes": {
                        "definition-node:a": component_node(
                            "definition-node:a",
                            definition_ref="definition:b",
                            uir_node_ref="uir:definition-a",
                        )
                    },
                },
                "definition:b": {
                    "id": "definition:b",
                    "name": "B",
                    "rootNodeRef": "definition-node:b",
                    "nodes": {
                        "definition-node:b": component_node(
                            "definition-node:b",
                            definition_ref="definition:a",
                            uir_node_ref="uir:definition-b",
                        )
                    },
                },
            },
        }
    )
    plan = FGUIPlanDocument.model_validate(payload)

    codes = {item.code for item in validate_fgui_plan(plan)}
    assert "fgui.plan.component_definition_cycle" in codes
    assert "fgui.plan.component_definition_depth_exceeded" not in codes


def test_bindable_plan_cannot_be_empty() -> None:
    plan = valid_plan().model_copy(
        update={
            "roots": (),
            "nodes": {},
            "resources": {},
            "decisions": {},
        }
    )

    assert "fgui.plan.tree_empty" in {
        item.code for item in validate_fgui_plan(plan)
    }


def plan_with_dangling_refs() -> FGUIPlanDocument:
    plan = valid_plan()
    root = plan.nodes["plan:root"].model_copy(
        update={
            "children": ("plan:missing",),
            "resource_ref": "resource:missing",
            "mask_ref": "mask:missing",
            "decision_ref": "decision:missing",
        }
    )
    return plan.model_copy(
        update={
            "bindable": False,
            "roots": ("plan:absent", root.id),
            "nodes": {root.id: root},
            "resources": {},
            "masks": {},
            "decisions": {},
        }
    )


def inconsistent_capability_plan() -> FGUIPlanDocument:
    plan = valid_plan()
    fallback = plan.nodes["plan:image"].model_copy(
        update={"type": PlanNodeType.RASTER_SUBTREE, "resource_ref": None}
    )
    unsupported = CapabilityDecision(
        id="decision:unsupported",
        nodeRef="uir:unsupported",
        status="unsupported",
        ruleId="fgui.unsupported.node_type",
        ruleVersion=1,
        blocking=False,
    )
    fallback_decision = plan.decisions["uir:image"].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
        }
    )
    return plan.model_copy(
        update={
            "nodes": {**plan.nodes, fallback.id: fallback},
            "resources": {},
            "decisions": {
                **plan.decisions,
                "uir:image": fallback_decision,
                "uir:unsupported": unsupported,
            },
        }
    )


def plan_with_style_fact(key: str, value: object) -> FGUIPlanDocument:
    plan = valid_plan()
    text = plan.nodes["plan:text"]
    assert text.text is not None
    leaky_text = text.text.model_copy(update={"style_facts": {key: value}})
    leaky_node = text.model_copy(update={"text": leaky_text})
    return plan.model_copy(update={"nodes": {**plan.nodes, leaky_node.id: leaky_node}})


def native_mask_plan() -> FGUIPlanDocument:
    plan = valid_plan()
    mask = MaskPlan(
        id="mask:root",
        mode="nativeMask",
        kind="image",
        maskNodeRef="uir:image",
        contentNodeRefs=("uir:text",),
    )
    root = plan.nodes["plan:root"].model_copy(update={"mask_ref": mask.id})
    return plan.model_copy(
        update={
            "nodes": {**plan.nodes, root.id: root},
            "masks": {mask.id: mask},
        }
    )


def test_valid_plan_has_no_validation_diagnostics() -> None:
    assert validate_fgui_plan(valid_plan()) == ()


@pytest.mark.parametrize(
    ("grid", "width", "height", "expected"),
    [
        (
            NineSlicePlan(x=0, y=0, width=10, height=10).model_copy(
                update={"x": -1}
            ),
            100,
            80,
            "fgui.plan.nine_slice_nonpositive",
        ),
        (
            NineSlicePlan(x=0, y=0, width=10, height=10),
            None,
            80,
            "fgui.plan.nine_slice_dimensions_missing",
        ),
        (
            NineSlicePlan(x=90, y=0, width=20, height=10),
            100,
            80,
            "fgui.plan.nine_slice_out_of_bounds",
        ),
    ],
)
def test_resource_nine_slice_is_revalidated_after_model_copy(
    grid: NineSlicePlan,
    width: int | None,
    height: int | None,
    expected: str,
) -> None:
    plan = valid_plan()
    resource = plan.resources["resource:image"].model_copy(
        update={"nine_slice": grid, "width": width, "height": height}
    )
    broken = plan.model_copy(
        update={"bindable": False, "resources": {resource.id: resource}}
    )

    assert expected in {item.code for item in validate_fgui_plan(broken)}


def test_resource_content_and_export_recipe_hashes_are_revalidated() -> None:
    plan = valid_plan()
    resource = plan.resources["resource:image"].model_copy(
        update={"content_sha256": None, "export_parameters_sha256": "f" * 64}
    )
    broken = plan.model_copy(
        update={"bindable": False, "resources": {resource.id: resource}}
    )

    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.resource_content_hash_missing" in codes
    assert "fgui.plan.export_parameters_hash_mismatch" in codes


def test_raster_fallback_resource_must_be_png_with_reason() -> None:
    plan = valid_plan()
    image = plan.nodes["plan:image"].model_copy(
        update={"type": PlanNodeType.RASTER_SUBTREE}
    )
    decision = plan.decisions["uir:image"].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
            "reasons": ("fixture",),
        }
    )
    resource = plan.resources["resource:image"].model_copy(
        update={
            "mime_type": "image/jpeg",
            "export_format": "jpg",
            "export_parameters_sha256": export_parameters_sha256(
                mime_type="image/jpeg", export_format="jpg"
            ),
            "reason": None,
        }
    )
    broken = plan.model_copy(
        update={
            "bindable": False,
            "nodes": {**plan.nodes, image.id: image},
            "decisions": {**plan.decisions, "uir:image": decision},
            "resources": {resource.id: resource},
        }
    )

    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.fallback_format_incoherent" in codes
    assert "fgui.plan.fallback_reason_missing" in codes


def test_validation_reports_all_dangling_references() -> None:
    codes = {item.code for item in validate_fgui_plan(plan_with_dangling_refs())}
    assert codes == {
        "fgui.plan.root_missing",
        "fgui.plan.child_missing",
        "fgui.plan.resource_missing",
        "fgui.plan.mask_missing",
        "fgui.plan.decision_missing",
    }


def test_fallback_requires_resource_and_unsupported_requires_blocking_diagnostic() -> None:
    codes = {item.code for item in validate_fgui_plan(inconsistent_capability_plan())}
    assert "fgui.plan.fallback_resource_required" in codes
    assert "fgui.plan.unsupported_not_blocked" in codes


def test_fallback_decision_requires_matching_review_diagnostic() -> None:
    plan = valid_plan()
    image = plan.nodes["plan:image"].model_copy(
        update={"type": PlanNodeType.RASTER_SUBTREE}
    )
    decision = plan.decisions["uir:image"].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
            "reasons": ("fixture",),
        }
    )
    resource = plan.resources["resource:image"].model_copy(
        update={"reason": "fixture"}
    )
    without_diagnostic = plan.model_copy(
        update={
            "nodes": {**plan.nodes, image.id: image},
            "decisions": {**plan.decisions, "uir:image": decision},
            "resources": {resource.id: resource},
        }
    )

    codes = {item.code for item in validate_fgui_plan(without_diagnostic)}

    assert "fgui.plan.fallback_diagnostic_missing" in codes


@pytest.mark.parametrize(
    "key",
    [
        "pkg",
        "targetPackageId",
        "fguiComponentId",
        "fairyguiPackageId",
        "bindingComponentId",
        "targetPackageIdOverride",
    ],
)
def test_binding_field_leak_is_rejected_recursively(key: str) -> None:
    assert any(
        item.code == "fgui.plan.binding_field_leak"
        for item in validate_fgui_plan(plan_with_style_fact(key, "bad"))
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("apiKey", "private-value"),
        ("metadata", "C:\\private\\asset.png"),
        ("base64Data", "private-payload"),
        ("thumbnailBase64", "private-payload"),
        ("authToken", "private-value"),
        ("sessionSecret", "private-value"),
        ("resourceBytes", "private-payload"),
        ("encodedBlob", "private-payload"),
        ("value", b"private-bytes"),
    ],
)
def test_private_plan_fact_leaks_are_safe_diagnostics_and_redacted(
    key: str, value: object
) -> None:
    plan = plan_with_style_fact(key, value)

    diagnostics = validate_fgui_plan(plan)
    encoded = canonical_plan_bytes(plan)

    assert any(item.code == "fgui.plan.private_data_leak" for item in diagnostics)
    assert b"private-value" not in encoded
    assert b"private-payload" not in encoded
    assert b"private-bytes" not in encoded
    assert b"C:\\private" not in encoded


def test_private_typed_resource_identity_is_rejected_and_redacted() -> None:
    plan = valid_plan()
    key, resource = next(iter(plan.resources.items()))
    resource = resource.model_copy(
        update={"logical_asset_id": r"C:\private\asset.png"}
    )
    plan = plan.model_copy(update={"resources": {key: resource}})

    diagnostics = validate_fgui_plan(plan)
    encoded = canonical_plan_bytes(plan)

    assert any(item.code == "fgui.plan.private_data_leak" for item in diagnostics)
    assert b"asset.png" not in encoded


def test_private_plan_header_metadata_is_rejected_and_redacted() -> None:
    plan = valid_plan().model_copy(
        update={"profile_version": r"C:\private\profile.json"}
    )

    diagnostics = validate_fgui_plan(plan)
    encoded = canonical_plan_bytes(plan)

    assert any(item.code == "fgui.plan.private_data_leak" for item in diagnostics)
    assert b"profile.json" not in encoded


def test_all_typed_plan_metadata_except_user_text_is_private_policy_scoped() -> None:
    plan = valid_plan()
    text_node = plan.nodes["plan:text"]
    assert text_node.text is not None
    text = text_node.text.model_copy(
        update={
            "content": r"C:\UI\visible-content",
            "font_candidates": (r"C:\private\base.ttf",),
            "runs": (
                TextRunPlan(
                    content=r"C:\UI\visible-content",
                    fontCandidates=(r"C:\private\run.ttf",),
                ),
            ),
        }
    )
    text_node = text_node.model_copy(update={"type": "richText", "text": text})
    component_node = plan.nodes["plan:root"].model_copy(
        update={
                "type": "componentReference",
                "component": ComponentReferencePlan(
                    candidateKey=r"C:\private\candidate.txt",
                    definitionRef="definition:private",
                ),
        }
    )
    plan = plan.model_copy(
        update={
            "nodes": {
                **plan.nodes,
                text_node.id: text_node,
                component_node.id: component_node,
            }
        }
    )

    diagnostics = validate_fgui_plan(plan)
    encoded = canonical_plan_bytes(plan)

    assert any(item.code == "fgui.plan.private_data_leak" for item in diagnostics)
    for private_name in (b"base.ttf", b"run.ttf", b"candidate.txt"):
        assert private_name not in encoded
    assert b"visible-content" in encoded


def test_deep_plan_tree_is_rejected_without_recursive_validator_failure() -> None:
    plan = valid_plan()
    template = plan.nodes["plan:root"]
    node_count = 1_100
    nodes = {}
    decisions = {}
    for index in range(node_count):
        plan_id = f"plan:{index}"
        uir_id = f"uir:{index}"
        child_id = f"plan:{index + 1}" if index + 1 < node_count else None
        decision = plan.decisions["uir:root"].model_copy(
            update={"id": f"decision:{index}", "node_ref": uir_id}
        )
        decisions[uir_id] = decision
        nodes[plan_id] = template.model_copy(
            update={
                "id": plan_id,
                "uir_node_ref": uir_id,
                "parent_id": None if index == 0 else f"plan:{index - 1}",
                "children": () if child_id is None else (child_id,),
                "decision_ref": decision.id,
            }
        )
    deep = plan.model_copy(
        update={
            "roots": ("plan:0",),
            "nodes": nodes,
            "resources": {},
            "decisions": decisions,
        }
    )

    diagnostics = validate_fgui_plan(deep)

    assert any(item.code == "fgui.plan.tree_depth_exceeded" for item in diagnostics)


def test_absolute_path_shaped_text_content_is_not_opaque_private_metadata() -> None:
    plan = valid_plan()
    text_node = plan.nodes["plan:text"]
    assert text_node.text is not None
    text = text_node.text.model_copy(update={"content": "C:\\UI\\Label"})
    text_node = text_node.model_copy(update={"text": text})
    plan = plan.model_copy(update={"nodes": {**plan.nodes, text_node.id: text_node}})

    assert not any(
        item.code == "fgui.plan.private_data_leak"
        for item in validate_fgui_plan(plan)
    )
    assert b"C:\\\\UI\\\\Label" in canonical_plan_bytes(plan)


def test_private_decision_and_diagnostic_metadata_is_rejected_and_redacted() -> None:
    plan = valid_plan()
    decision = plan.decisions["uir:root"].model_copy(
        update={"evidence": (r"C:\private\decision.txt",)}
    )
    embedded = Diagnostic(
        code="fixture.warning",
        severity=Severity.WARNING,
        message=r"C:\private\diagnostic.txt",
        rule_id="fixture.warning",
        rule_version=1,
        evidence=("fixture.warning=true",),
        suggested_action="review_fixture",
    )
    plan = plan.model_copy(
        update={
            "decisions": {**plan.decisions, "uir:root": decision},
            "diagnostics": (embedded,),
        }
    )

    diagnostics = validate_fgui_plan(plan)
    encoded = canonical_plan_bytes(plan)

    assert any(item.code == "fgui.plan.private_data_leak" for item in diagnostics)
    assert b"decision.txt" not in encoded
    assert b"diagnostic.txt" not in encoded


def test_canonical_bytes_and_hash_are_stable() -> None:
    first = canonical_plan_bytes(valid_plan())
    second = canonical_plan_bytes(valid_plan())
    assert first == second and first.endswith(b"\n")
    assert plan_sha256(valid_plan()) == hashlib.sha256(first).hexdigest()


def test_dictionary_keys_and_tree_ownership_are_validated() -> None:
    plan = valid_plan()
    text = plan.nodes["plan:text"].model_copy(update={"parent_id": "plan:image"})
    image = plan.nodes["plan:image"].model_copy(
        update={"id": "plan:other", "children": ("plan:text",)}
    )
    broken = plan.model_copy(
        update={
            "nodes": {
                "plan:root": plan.nodes["plan:root"],
                "plan:text": text,
                "plan:image": image,
            }
        }
    )
    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.node_key_mismatch" in codes
    assert "fgui.plan.child_multiple_parents" in codes
    assert "fgui.plan.parent_mismatch" in codes


def test_every_emitted_node_must_be_a_root_or_owned_child() -> None:
    plan = valid_plan()
    root = plan.nodes["plan:root"].model_copy(update={"children": ("plan:image",)})
    text = plan.nodes["plan:text"].model_copy(update={"parent_id": None})
    broken = plan.model_copy(
        update={"nodes": {**plan.nodes, root.id: root, text.id: text}}
    )

    assert any(
        item.code == "fgui.plan.node_unowned"
        for item in validate_fgui_plan(broken)
    )


def test_roots_must_be_unique() -> None:
    plan = valid_plan().model_copy(update={"roots": ("plan:root", "plan:root")})

    assert any(
        item.code == "fgui.plan.root_duplicate"
        for item in validate_fgui_plan(plan)
    )


def test_self_cycles_are_rejected() -> None:
    plan = valid_plan()
    root = plan.nodes["plan:root"].model_copy(
        update={"children": (*plan.nodes["plan:root"].children, "plan:root")}
    )
    broken = plan.model_copy(update={"nodes": {**plan.nodes, root.id: root}})

    assert any(
        item.code == "fgui.plan.node_cycle"
        for item in validate_fgui_plan(broken)
    )


def test_disconnected_cycles_are_rejected_as_cycles_and_unreachable() -> None:
    plan = valid_plan()
    root = plan.nodes["plan:root"].model_copy(update={"children": ()})
    text = plan.nodes["plan:text"].model_copy(
        update={"parent_id": "plan:image", "children": ("plan:image",)}
    )
    image = plan.nodes["plan:image"].model_copy(
        update={"parent_id": "plan:text", "children": ("plan:text",)}
    )
    broken = plan.model_copy(
        update={"nodes": {root.id: root, text.id: text, image.id: image}}
    )

    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.node_cycle" in codes
    assert "fgui.plan.node_unreachable" in codes


def test_nodes_reachable_from_more_than_one_root_are_rejected() -> None:
    plan = valid_plan().model_copy(
        update={"roots": ("plan:root", "plan:text")}
    )

    assert any(
        item.code == "fgui.plan.node_multiple_roots"
        for item in validate_fgui_plan(plan)
    )


def test_resource_consumers_are_symmetric_and_existing() -> None:
    plan = valid_plan()
    resource = plan.resources["resource:image"].model_copy(
        update={"consumers": ("plan:text", "plan:missing")}
    )
    broken = plan.model_copy(update={"resources": {resource.id: resource}})
    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.resource_consumer_missing" in codes
    assert "fgui.plan.resource_consumer_mismatch" in codes


def test_resource_identity_consumers_and_mime_recipe_are_coherent() -> None:
    plan = valid_plan()
    resource = plan.resources["resource:image"].model_copy(
        update={
            "source_asset_ref": " ",
            "logical_asset_id": "",
            "mime_type": "image/png",
            "export_format": "svg",
            "export_parameters_sha256": export_parameters_sha256(
                mime_type="image/png", export_format="svg"
            ),
            "consumers": ("plan:image", "plan:image"),
        }
    )
    broken = plan.model_copy(update={"resources": {resource.id: resource}})

    codes = {item.code for item in validate_fgui_plan(broken)}

    assert "fgui.plan.resource_identity_invalid" in codes
    assert "fgui.plan.resource_consumers_duplicate" in codes
    assert "fgui.plan.resource_format_incoherent" in codes


def test_resource_requires_an_owner_and_nonblank_fallback_reason() -> None:
    plan = valid_plan()
    resource = plan.resources["resource:image"]
    orphaned = plan.model_copy(
        update={
            "resources": {
                **plan.resources,
                "resource:orphan": resource.model_copy(
                    update={"id": "resource:orphan", "consumers": ()}
                ),
            }
        }
    )
    raster_node = plan.nodes["plan:image"].model_copy(update={"type": "rasterSubtree"})
    blank_reason = plan.model_copy(
        update={
            "nodes": {**plan.nodes, raster_node.id: raster_node},
            "resources": {
                resource.id: resource.model_copy(update={"reason": " "})
            },
        }
    )

    assert any(
        item.code == "fgui.plan.resource_unowned"
        for item in validate_fgui_plan(orphaned)
    )
    assert any(
        item.code == "fgui.plan.fallback_reason_missing"
        for item in validate_fgui_plan(blank_reason)
    )


def test_masks_must_have_exactly_one_target() -> None:
    plan = native_mask_plan()
    mask = plan.masks["mask:root"]
    orphaned = plan.model_copy(
        update={
            "nodes": valid_plan().nodes,
            "masks": {mask.id: mask},
        }
    )
    assert any(
        item.code == "fgui.plan.mask_orphan"
        for item in validate_fgui_plan(orphaned)
    )

    second_target = plan.nodes["plan:text"].model_copy(update={"mask_ref": mask.id})
    shared = plan.model_copy(
        update={"nodes": {**plan.nodes, second_target.id: second_target}}
    )
    assert any(
        item.code == "fgui.plan.mask_multiple_targets"
        for item in validate_fgui_plan(shared)
    )


def test_masks_require_nonempty_source_and_content_references() -> None:
    plan = native_mask_plan()
    mask = plan.masks["mask:root"].model_copy(
        update={"mask_node_ref": "", "content_node_refs": ()}
    )
    broken = plan.model_copy(update={"masks": {mask.id: mask}})

    assert any(
        item.code == "fgui.plan.mask_hierarchy_incoherent"
        for item in validate_fgui_plan(broken)
    )


def test_native_mask_mode_requires_matching_source_role_and_hierarchy() -> None:
    plan = native_mask_plan()
    source = plan.nodes["plan:image"].model_copy(
        update={
            "type": PlanNodeType.TEXT,
            "resource_ref": None,
            "text": TextPlan(content="x"),
        }
    )
    decision = plan.decisions["uir:image"].model_copy(
        update={"rule_id": "fgui.native.text"}
    )
    broken = plan.model_copy(
        update={
            "nodes": {**plan.nodes, source.id: source},
            "decisions": {**plan.decisions, "uir:image": decision},
        }
    )
    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.mask_node_type_incoherent" in codes
    assert "fgui.plan.mask_decision_incoherent" in codes


@pytest.mark.parametrize("invalid_width", [0.0, float("inf")])
def test_native_mask_source_geometry_is_revalidated(invalid_width: float) -> None:
    plan = native_mask_plan()
    source = plan.nodes["plan:image"]
    bounds = source.transform.bounds.model_copy(update={"width": invalid_width})
    transform = source.transform.model_copy(update={"bounds": bounds})
    source = source.model_copy(update={"transform": transform})
    broken = plan.model_copy(
        update={"bindable": False, "nodes": {**plan.nodes, source.id: source}}
    )

    assert any(
        item.code == "fgui.plan.mask_geometry_incoherent"
        for item in validate_fgui_plan(broken)
    )


def test_native_mask_content_requires_a_coherent_native_decision() -> None:
    plan = native_mask_plan()
    decision = plan.decisions["uir:text"].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
        }
    )
    broken = plan.model_copy(
        update={"decisions": {**plan.decisions, "uir:text": decision}}
    )

    assert any(
        item.code == "fgui.plan.mask_decision_incoherent"
        and item.node_id == "plan:text"
        for item in validate_fgui_plan(broken)
    )


def test_raster_nodes_cannot_retain_native_descendants_or_duplicate_resource_ownership() -> None:
    plan = valid_plan()
    root = plan.nodes["plan:root"].model_copy(
        update={
            "type": PlanNodeType.RASTER_SUBTREE,
            "resource_ref": "resource:image",
            "decision_ref": "decision:root",
        }
    )
    decision = plan.decisions["uir:root"].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
        }
    )
    resource = plan.resources["resource:image"].model_copy(
        update={"consumers": (root.id, "plan:image")}
    )
    broken = plan.model_copy(
        update={
            "nodes": {**plan.nodes, root.id: root},
            "resources": {resource.id: resource},
            "decisions": {**plan.decisions, "uir:root": decision},
        }
    )
    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.raster_descendant_duplicate" in codes
    assert "fgui.plan.raster_native_resource_duplicate" in codes


def test_raster_native_resource_duplication_does_not_depend_on_consumer_metadata() -> None:
    plan = valid_plan()
    root = plan.nodes["plan:root"].model_copy(
        update={
            "type": PlanNodeType.RASTER_SUBTREE,
            "resource_ref": "resource:image",
            "children": (),
        }
    )
    text = plan.nodes["plan:text"].model_copy(update={"parent_id": None})
    image = plan.nodes["plan:image"].model_copy(update={"parent_id": None})
    decision = plan.decisions["uir:root"].model_copy(
        update={
            "status": CapabilityStatus.RASTER_FALLBACK,
            "rule_id": "fgui.fallback.raster_subtree",
        }
    )
    resource = plan.resources["resource:image"].model_copy(update={"consumers": ()})
    broken = plan.model_copy(
        update={
            "bindable": False,
            "roots": (root.id, text.id, image.id),
            "nodes": {root.id: root, text.id: text, image.id: image},
            "resources": {resource.id: resource},
            "decisions": {**plan.decisions, "uir:root": decision},
        }
    )

    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.resource_consumer_mismatch" in codes
    assert "fgui.plan.raster_native_resource_duplicate" in codes


def test_node_payload_and_decision_semantics_must_match_node_type() -> None:
    plan = valid_plan()
    text = plan.nodes["plan:text"].model_copy(update={"text": None})
    decision = plan.decisions["uir:text"].model_copy(
        update={"rule_id": "fgui.native.container"}
    )
    broken = plan.model_copy(
        update={
            "nodes": {**plan.nodes, text.id: text},
            "decisions": {**plan.decisions, "uir:text": decision},
        }
    )
    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.node_payload_required" in codes
    assert "fgui.plan.decision_node_type_mismatch" in codes


def test_rich_text_runs_must_reconstruct_content_and_plain_text_has_no_runs() -> None:
    plan = valid_plan()
    original = plan.nodes["plan:text"]
    rich = original.model_copy(
        update={
            "type": PlanNodeType.RICH_TEXT,
            "text": TextPlan(
                content="Hello",
                runs=(TextRunPlan(content="different"),),
            ),
        }
    )
    rich_decision = plan.decisions["uir:text"].model_copy(
        update={"rule_id": "fgui.native.rich_text"}
    )
    rich_plan = plan.model_copy(
        update={
            "nodes": {**plan.nodes, rich.id: rich},
            "decisions": {**plan.decisions, "uir:text": rich_decision},
        }
    )
    plain = original.model_copy(
        update={
            "text": TextPlan(
                content="Hello",
                runs=(TextRunPlan(content="Hello"),),
            )
        }
    )
    plain_plan = plan.model_copy(update={"nodes": {**plan.nodes, plain.id: plain}})

    assert any(
        item.code == "fgui.plan.rich_text_content_mismatch"
        for item in validate_fgui_plan(rich_plan)
    )
    assert any(
        item.code == "fgui.plan.plain_text_runs_forbidden"
        for item in validate_fgui_plan(plain_plan)
    )


def reviewable_plain_text_plan() -> FGUIPlanDocument:
    plan = valid_plan()
    decision = plan.decisions["uir:text"].model_copy(
        update={
            "status": CapabilityStatus.UNSUPPORTED,
            "rule_id": "fgui.text.runs_unsupported",
            "blocking": False,
            "reasons": ("rich_text_runs",),
            "evidence": (
                "text.runs.count=2",
                "text.runs.preserved=content",
                "text.runs.unsupported=fontSize",
            ),
        }
    )
    return plan.model_copy(
        update={"decisions": {**plan.decisions, "uir:text": decision}}
    )


def test_nonblocking_reviewed_rich_text_risk_may_own_plain_text() -> None:
    assert validate_fgui_plan(reviewable_plain_text_plan()) == ()


@pytest.mark.parametrize(
    "mutation",
    (
        lambda plan: plan.model_copy(
            update={
                "nodes": {
                    **plan.nodes,
                    "plan:text": plan.nodes["plan:text"].model_copy(
                        update={
                            "type": PlanNodeType.RICH_TEXT,
                            "text": TextPlan(
                                content="Hello",
                                runs=(TextRunPlan(content="Hello"),),
                            ),
                        }
                    ),
                }
            }
        ),
        lambda plan: plan.model_copy(
            update={
                "nodes": {
                    **plan.nodes,
                    "plan:text": plan.nodes["plan:text"].model_copy(
                        update={
                            "text": TextPlan(
                                content="Hello",
                                runs=(TextRunPlan(content="Hello"),),
                            )
                        }
                    ),
                }
            }
        ),
        lambda plan: plan.model_copy(
            update={
                "nodes": {
                    **plan.nodes,
                    "plan:text": plan.nodes["plan:text"].model_copy(
                        update={"resource_ref": "resource:image"}
                    ),
                }
            }
        ),
        lambda plan: plan.model_copy(
            update={
                "decisions": {
                    **plan.decisions,
                    "uir:text": plan.decisions["uir:text"].model_copy(
                        update={"reasons": ()}
                    ),
                }
            }
        ),
    ),
    ids=("rich-node", "runs", "resource", "missing-review-reason"),
)
def test_reviewable_plain_text_exception_rejects_near_misses(mutation) -> None:
    assert "fgui.plan.unsupported_node_emitted" in {
        item.code for item in validate_fgui_plan(mutation(reviewable_plain_text_plan()))
    }


@pytest.mark.parametrize(
    "count",
    ("²", "9" * 5000),
    ids=("unicode-numeric", "oversized-ascii-decimal"),
)
def test_malformed_reviewable_text_count_is_diagnostic_not_exception(
    count: str,
) -> None:
    plan = reviewable_plain_text_plan()
    malformed = plan.decisions["uir:text"].model_copy(
        update={
            "evidence": (
                f"text.runs.count={count}",
                "text.runs.preserved=content",
                "text.runs.unsupported=fontSize",
            )
        }
    )
    broken = plan.model_copy(
        update={"decisions": {**plan.decisions, "uir:text": malformed}}
    )

    codes = {item.code for item in validate_fgui_plan(broken)}

    assert "fgui.plan.unsupported_not_blocked" in codes
    assert "fgui.plan.unsupported_node_emitted" in codes


def test_native_or_fallback_decisions_require_an_emitted_node() -> None:
    plan = valid_plan()
    orphan = CapabilityDecision(
        id="decision:orphan",
        nodeRef="uir:orphan",
        status=CapabilityStatus.NATIVE,
        ruleId="fgui.native.container",
        ruleVersion=1,
    )
    broken = plan.model_copy(
        update={"decisions": {**plan.decisions, orphan.node_ref: orphan}}
    )

    assert any(
        item.code == "fgui.plan.decision_orphan"
        for item in validate_fgui_plan(broken)
    )


def test_unsupported_decisions_each_require_their_own_node_diagnostic() -> None:
    plan = valid_plan()
    first = CapabilityDecision(
        id="decision:unsupported-first",
        nodeRef="uir:unsupported-first",
        status=CapabilityStatus.UNSUPPORTED,
        ruleId="fgui.unsupported.node_type",
        ruleVersion=1,
        evidence=("fixture.unsupported",),
        blocking=True,
    )
    second = first.model_copy(
        update={"id": "decision:unsupported-second", "node_ref": "uir:unsupported-second"}
    )
    error = Diagnostic(
        code="fgui.unsupported.node_type",
        severity=Severity.ERROR,
        message="Only the first node is diagnosed.",
        node_id=first.node_ref,
        rule_id=first.rule_id,
        rule_version=first.rule_version,
        evidence=first.evidence,
        suggested_action="resolve_unsupported_feature",
        blocks_binding=True,
    )
    broken = plan.model_copy(
        update={
            "bindable": False,
            "decisions": {
                **plan.decisions,
                first.node_ref: first,
                second.node_ref: second,
            },
            "diagnostics": (error,),
        }
    )

    unsupported_errors = [
        item
        for item in validate_fgui_plan(broken)
        if item.code == "fgui.plan.unsupported_not_blocked"
    ]
    assert [item.node_id for item in unsupported_errors] == [second.node_ref]


def test_unsupported_diagnostic_must_match_rule_version_evidence_and_impact() -> None:
    plan = valid_plan()
    decision = CapabilityDecision(
        id="decision:unsupported",
        nodeRef="uir:unsupported",
        status=CapabilityStatus.UNSUPPORTED,
        ruleId="fgui.unsupported.interaction",
        ruleVersion=1,
        evidence=("feature=interaction",),
        blocking=True,
    )
    wrong = Diagnostic(
        code=decision.rule_id,
        severity=Severity.ERROR,
        message="Wrong review metadata.",
        node_id=decision.node_ref,
        rule_id="fgui.unsupported.other",
        rule_version=2,
        evidence=("feature=other",),
        suggested_action="resolve_unsupported_feature",
        blocks_binding=True,
    )
    broken = plan.model_copy(
        update={
            "bindable": False,
            "decisions": {**plan.decisions, decision.node_ref: decision},
            "diagnostics": (wrong,),
        }
    )

    assert any(
        item.code == "fgui.plan.unsupported_not_blocked"
        and item.node_id == decision.node_ref
        for item in validate_fgui_plan(broken)
    )


def test_decisions_and_embedded_diagnostics_require_review_metadata() -> None:
    plan = valid_plan()
    root_decision = plan.decisions["uir:root"].model_copy(update={"evidence": ()})
    incomplete = Diagnostic(
        code="fixture.error",
        severity=Severity.ERROR,
        message="Missing review metadata.",
        node_id="uir:root",
    )
    broken = plan.model_copy(
        update={
            "bindable": False,
            "decisions": {**plan.decisions, "uir:root": root_decision},
            "diagnostics": (incomplete,),
        }
    )

    codes = {item.code for item in validate_fgui_plan(broken)}
    assert "fgui.plan.decision_evidence_missing" in codes
    assert "fgui.plan.diagnostic_contract_incomplete" in codes


def test_embedded_diagnostic_metadata_must_be_nonblank() -> None:
    plan = valid_plan()
    blank = Diagnostic(
        code="fixture.warning",
        severity=Severity.WARNING,
        message="Review metadata is blank.",
        rule_id=" ",
        rule_version=1,
        evidence=("",),
        suggested_action=" ",
    )
    broken = plan.model_copy(update={"diagnostics": (blank,)})

    assert any(
        item.code == "fgui.plan.diagnostic_contract_incomplete"
        for item in validate_fgui_plan(broken)
    )


def test_native_clip_source_decision_requires_a_native_clip_mask_role() -> None:
    plan = valid_plan()
    decision = plan.decisions["uir:root"].model_copy(
        update={"rule_id": "fgui.native.clip_source"}
    )
    broken = plan.model_copy(
        update={"decisions": {**plan.decisions, "uir:root": decision}}
    )

    assert any(
        item.code == "fgui.plan.decision_mask_role_incoherent"
        for item in validate_fgui_plan(broken)
    )


def test_component_reference_candidate_must_be_nonblank() -> None:
    plan = valid_plan()
    root = plan.nodes["plan:root"].model_copy(
        update={
            "type": "componentReference",
            "component": ComponentReferencePlan(
                candidateKey="valid", definitionRef="definition:valid"
            ).model_copy(update={"candidate_key": " "}),
        }
    )
    broken = plan.model_copy(update={"nodes": {**plan.nodes, root.id: root}})

    assert any(
        item.code == "fgui.plan.component_candidate_invalid"
        for item in validate_fgui_plan(broken)
    )


def test_bindable_flag_matches_errors_and_blocking_decisions() -> None:
    plan = valid_plan()
    embedded_error = Diagnostic(
        code="fixture.error",
        severity=Severity.ERROR,
        message="blocked",
        rule_id="fixture.error",
        rule_version=1,
        evidence=("fixture.blocked",),
        suggested_action="repair_fixture",
        blocks_binding=True,
    )
    broken = plan.model_copy(update={"diagnostics": (embedded_error,)})
    assert any(
        item.code == "fgui.plan.bindable_inconsistent"
        for item in validate_fgui_plan(broken)
    )


def test_bindable_flag_accounts_for_validation_errors() -> None:
    plan = valid_plan().model_copy(update={"roots": ("plan:missing",)})

    assert any(
        item.code == "fgui.plan.bindable_inconsistent"
        for item in validate_fgui_plan(plan)
    )


def test_valid_native_mask_has_no_validation_diagnostics() -> None:
    assert validate_fgui_plan(native_mask_plan()) == ()
