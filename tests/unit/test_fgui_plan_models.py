import json

import pytest
from pydantic import ValidationError

from figma_to_fgui.fgui_plan_models import (
    CapabilityStatus,
    ComponentDefinitionPlan,
    ComponentReferencePlan,
    FGUIPlanDocument,
    FGUIPlanNode,
    FGUIPlanV1Document,
    MaskMode,
    MaskPlan,
    NineSlicePlan,
    PlanNodeType,
    ResourcePlan,
    TextPlan,
    migrate_plan_v1_without_components,
)
from figma_to_fgui.fgui_plan_validate import validate_fgui_plan


def _minimal_plan() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "documentId": "plan:example",
        "sourceUirSha256": "a" * 64,
        "profileVersion": "fgui-6.1.4-v1",
        "ruleVersion": 1,
        "bindable": True,
        "roots": ("node:root",),
        "nodes": {
            "node:root": {
                "id": "plan-node:root",
                "uirNodeRef": "node:root",
                "parentId": None,
                "children": (),
                "zIndex": 0,
                "type": "container",
                "transform": {
                    "bounds": {"x": 0, "y": 0, "width": 100, "height": 100}
                },
            }
        },
        "componentDefinitions": {},
        "resources": {},
        "masks": {},
        "decisions": {},
        "diagnostics": (),
    }


@pytest.fixture
def minimal_plan() -> dict[str, object]:
    return _minimal_plan()


@pytest.fixture
def valid_plan() -> FGUIPlanDocument:
    return FGUIPlanDocument.model_validate(_minimal_plan())


def test_plan_enums_are_closed() -> None:
    assert set(CapabilityStatus) == {"native", "rasterFallback", "unsupported"}
    assert set(PlanNodeType) == {
        "container",
        "graph",
        "text",
        "richText",
        "image",
        "loader",
        "componentReference",
        "rasterSubtree",
    }
    assert set(MaskMode) == {"nativeClip", "nativeMask", "rasterSubtree"}


def test_plan_rejects_unknown_and_target_binding_fields(
    minimal_plan: dict[str, object],
) -> None:
    minimal_plan["packageId"] = "real-id"
    with pytest.raises(ValidationError):
        FGUIPlanDocument.model_validate(minimal_plan)


def test_serialized_plan_contains_no_project_binding_fields(
    valid_plan: FGUIPlanDocument,
) -> None:
    encoded = json.dumps(valid_plan.model_dump(mode="json", by_alias=True))
    for forbidden in ("packageId", "componentId", '"src"', '"pkg"'):
        assert forbidden not in encoded


def test_plan_accepts_python_names_and_is_immutable() -> None:
    plan = FGUIPlanDocument(
        schema_version=2,
        document_id="plan:example",
        source_uir_sha256="a" * 64,
        profile_version="fgui-6.1.4-v1",
        rule_version=1,
        bindable=True,
        roots=("node:root",),
        nodes={
            "node:root": {
                "id": "plan-node:root",
                "uir_node_ref": "node:root",
                "z_index": 0,
                "type": PlanNodeType.CONTAINER,
                "transform": {
                    "bounds": {"x": 0, "y": 0, "width": 100, "height": 100}
                },
            }
        },
    )
    with pytest.raises(ValidationError):
        plan.bindable = False


def test_plan_v2_component_models_are_strict_and_immutable() -> None:
    component = ComponentReferencePlan(
        candidateKey="common_button",
        definitionRef="definition:button",
        variantProperties={"state": "normal"},
    )
    definition = ComponentDefinitionPlan(
        id="definition:button",
        name="Button",
        rootNodeRef="definition-node:button",
        nodes={
            "definition-node:button": {
                "id": "definition-node:button",
                "uirNodeRef": "uir:definition-button",
                "zIndex": 0,
                "type": "container",
                "transform": {
                    "bounds": {"x": 0, "y": 0, "width": 100, "height": 40}
                },
            }
        },
    )

    assert component.definition_ref == definition.id
    with pytest.raises(TypeError):
        definition.nodes["other"] = definition.nodes["definition-node:button"]
    with pytest.raises(ValidationError):
        ComponentReferencePlan(candidateKey="common_button")
    with pytest.raises(ValidationError):
        ComponentReferencePlan(
            candidateKey="common_button",
            definitionRef="definition:button",
            variantProperties={"state": " "},
        )


def test_plan_v1_without_components_migrates_explicitly_to_v2() -> None:
    payload = _minimal_plan()
    payload.pop("componentDefinitions")
    payload["schemaVersion"] = 1
    migrated = migrate_plan_v1_without_components(
        FGUIPlanV1Document.model_validate(payload)
    )

    assert migrated.schema_version == 2
    assert migrated.component_definitions == {}


def test_plan_v1_component_reference_requires_recompilation() -> None:
    payload = _minimal_plan()
    payload.pop("componentDefinitions")
    payload["schemaVersion"] = 1
    node = payload["nodes"]["node:root"]  # type: ignore[index]
    node["type"] = "componentReference"
    node["component"] = {"candidateKey": "common_button"}
    plan = FGUIPlanV1Document.model_validate(payload)

    with pytest.raises(ValueError, match="must be recompiled"):
        migrate_plan_v1_without_components(plan)


def test_plan_identity_and_reference_fields_reject_whitespace() -> None:
    payload = _minimal_plan()
    payload["roots"] = (" ",)
    with pytest.raises(ValidationError):
        FGUIPlanDocument.model_validate(payload)
    with pytest.raises(ValidationError):
        FGUIPlanNode.model_validate(
            {
                "id": " ",
                "uirNodeRef": " ",
                "zIndex": 0,
                "type": "container",
                "transform": {
                    "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}
                },
            }
        )
    with pytest.raises(ValidationError):
        MaskPlan(
            id=" ",
            mode="nativeClip",
            kind="rectangle",
            maskNodeRef=" ",
            contentNodeRefs=(" ",),
        )


@pytest.mark.parametrize("source_hash", ("a" * 63, "A" * 64, "g" * 64))
def test_plan_rejects_malformed_source_hash(source_hash: str) -> None:
    payload = _minimal_plan()
    payload["sourceUirSha256"] = source_hash
    with pytest.raises(ValidationError):
        FGUIPlanDocument.model_validate(payload)


def test_plan_mapping_fields_are_deeply_immutable_and_json_serializable() -> None:
    text = TextPlan(content="Title", styleFacts={"font": {"weight": 500}})
    component = ComponentReferencePlan(
        candidateKey="common_button",
        definitionRef="definition:button",
        variantProperties={"state": "normal"},
    )
    payload = _minimal_plan()
    payload["nodes"] = {
        "node:root": {
            "id": "plan-node:root",
            "uirNodeRef": "node:root",
            "zIndex": 0,
            "type": "container",
            "transform": {"bounds": {"x": 0, "y": 0, "width": 100, "height": 100}},
            "text": text,
            "component": component,
        }
    }
    plan = FGUIPlanDocument.model_validate(payload)

    with pytest.raises(TypeError):
        text.style_facts["new"] = True
    with pytest.raises(TypeError):
        text.style_facts["font"]["weight"] = 700  # type: ignore[index]
    with pytest.raises(TypeError):
        component.variant_properties["state"] = "pressed"
    for mapping in (plan.nodes, plan.resources, plan.masks, plan.decisions):
        with pytest.raises(TypeError):
            mapping["node:other"] = mapping.get("node:root")  # type: ignore[index]

    encoded = plan.model_dump(mode="json", by_alias=True)
    assert isinstance(encoded["nodes"], dict)
    assert encoded["nodes"]["node:root"]["text"]["styleFacts"] == {
        "font": {"weight": 500}
    }


def test_plan_reports_nested_project_binding_fields_without_leaking_values() -> None:
    payload = _minimal_plan()
    payload["nodes"] = {
        "node:root": {
            **payload["nodes"]["node:root"],  # type: ignore[index]
            "text": {"content": "Title", "styleFacts": {"src": "local.png"}},
        }
    }
    plan = FGUIPlanDocument.model_validate(payload)

    assert any(
        item.code == "fgui.plan.binding_field_leak"
        for item in validate_fgui_plan(plan)
    )


def test_plan_freezes_nested_sequence_mappings_and_blocks_late_binding_injection() -> None:
    nested_values = [{"label": "safe"}]
    text = TextPlan(content="Title", styleFacts={"items": nested_values})

    with pytest.raises(TypeError):
        text.style_facts["items"][0] = {"src": "late-leak"}  # type: ignore[index]
    with pytest.raises(TypeError):
        text.style_facts["items"][0]["label"] = "changed"  # type: ignore[index]

    nested_values.append({"src": "input-leak"})
    encoded = text.model_dump(mode="json", by_alias=True)
    assert encoded["styleFacts"] == {"items": [{"label": "safe"}]}
    assert '"src"' not in text.model_dump_json(by_alias=True)


def test_plan_freezes_sets_in_deterministic_order_for_json() -> None:
    text = TextPlan(content="Title", styleFacts={"tags": {"z", "a", "m"}})

    assert text.style_facts["tags"] == ("a", "m", "z")
    first = text.model_dump_json(by_alias=True)
    second = text.model_dump_json(by_alias=True)
    assert first == second
    assert text.model_dump(mode="json", by_alias=True)["styleFacts"]["tags"] == [
        "a",
        "m",
        "z",
    ]


def test_resource_identity_hashes_and_nine_slice_are_constrained() -> None:
    resource = ResourcePlan(
        id="resource:image",
        sourceAssetRef="asset:image",
        logicalAssetId="logical:image",
        contentSha256="b" * 64,
        exportParametersSha256="c" * 64,
        mimeType="image/png",
        exportFormat="png",
        width=100,
        height=80,
        nineSlice=NineSlicePlan(x=10, y=10, width=70, height=50),
        consumers=(),
    )

    assert resource.logical_asset_id == "logical:image"
    assert resource.nine_slice is not None
    invalid_hash = resource.model_dump(by_alias=True)
    invalid_hash["exportParametersSha256"] = "not-a-sha"
    with pytest.raises(ValidationError):
        ResourcePlan.model_validate(invalid_hash)
    with pytest.raises(ValidationError):
        NineSlicePlan(x=-1, y=0, width=10, height=10)
    with pytest.raises(ValidationError):
        NineSlicePlan(x=0, y=0, width=0, height=10)
    for field in ("id", "sourceAssetRef", "logicalAssetId", "mimeType"):
        blank = resource.model_dump(by_alias=True)
        blank[field] = " "
        with pytest.raises(ValidationError):
            ResourcePlan.model_validate(blank)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bounds_x", float("nan")),
        ("bounds_height", float("inf")),
        ("rotation", float("-inf")),
        ("font_size", float("nan")),
    ],
)
def test_typed_plan_numbers_must_be_finite(field: str, value: float) -> None:
    payload = _minimal_plan()
    node = payload["nodes"]["node:root"]  # type: ignore[index]
    if field == "bounds_x":
        node["transform"]["bounds"]["x"] = value
    elif field == "bounds_height":
        node["transform"]["bounds"]["height"] = value
    elif field == "rotation":
        node["transform"]["rotation"] = value
    else:
        node["type"] = "text"
        node["text"] = {"content": "Title", "fontSize": value}

    with pytest.raises(ValidationError):
        FGUIPlanDocument.model_validate(payload)
