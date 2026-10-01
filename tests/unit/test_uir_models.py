import json

import pytest
from pydantic import ValidationError

from figma_to_fgui.uir_models import (
    ConversionMode,
    MappingStatus,
    SemanticStatus,
    UIRDocument,
)
from figma_to_fgui.uir_validate import canonical_uir_bytes, uir_sha256, validate_uir


def minimal_document() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "documentId": "uir_test",
        "compilerVersion": "uir-v1",
        "source": {
            "kind": "figma",
            "revision": "a" * 64,
            "selectionId": "selection_test",
        },
        "roots": ["node:root"],
        "nodes": {
            "node:root": {
                "id": "node:root",
                "source": {
                    "nodeId": "root",
                    "type": "FRAME",
                    "name": "村庄升阶",
                    "fingerprint": "b" * 64,
                },
                "semantic": {
                    "name": "villageAscend",
                    "role": "panel",
                    "status": "confirmed",
                },
                "children": [],
                "zIndex": 0,
                "geometry": {
                    "resolvedBounds": {
                        "x": 0,
                        "y": 0,
                        "width": 1080,
                        "height": 1923,
                    },
                    "rotation": 0,
                    "opacity": 1,
                },
                "layout": {},
                "visual": {},
                "interactions": [],
                "conversion": {"mode": "native", "reasons": []},
            }
        },
        "componentDefinitions": {},
        "assets": {},
        "mappingDecisions": {},
        "diagnostics": [],
    }


def test_uir_v1_rejects_unknown_fields() -> None:
    payload = minimal_document()
    payload["packageId"] = "forbidden"
    with pytest.raises(ValidationError):
        UIRDocument.model_validate(payload)


def test_uir_schema_has_only_declared_status_values() -> None:
    assert set(SemanticStatus) == {"candidate", "confirmed", "rejected", "fallback"}
    assert set(ConversionMode) == {
        "native",
        "componentReference",
        "existingResource",
        "rasterFallback",
        "unsupported",
    }
    assert set(MappingStatus) == {"verified", "missing", "conflict"}


@pytest.mark.parametrize(
    "update",
    [
        {"conversion": {"mode": "rasterFallback", "reasons": [" "]}},
        {
            "source": {"type": "TEXT"},
            "text": {
                "content": "Title",
                "fontPolicy": {"allowFallback": False, "resolvedFont": " "},
            },
        },
    ],
)
def test_conversion_reasons_and_resolved_fonts_must_be_nonblank(
    update: dict[str, object],
) -> None:
    payload = minimal_document()
    node = payload["nodes"]["node:root"]  # type: ignore[index]
    for key, value in update.items():
        if key == "source":
            node["source"].update(value)
        else:
            node[key] = value

    with pytest.raises(ValidationError):
        UIRDocument.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("documentId",), " "),
        (("compilerVersion",), ""),
        (("source", "selectionId"), " "),
        (("roots", 0), " "),
        (("nodes", "node:root", "id"), " "),
        (("nodes", "node:root", "source", "nodeId"), " "),
    ],
)
def test_uir_identity_fields_must_be_nonblank(
    path: tuple[object, ...], value: str
) -> None:
    payload = minimal_document()
    target: object = payload
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        UIRDocument.model_validate(payload)


def test_serialized_core_contains_no_fairygui_target_fields() -> None:
    encoded = json.dumps(
        UIRDocument.model_validate(minimal_document()).model_dump(mode="json")
    )
    for forbidden in (
        "packageId",
        "componentId",
        "gearDisplay",
        "gearFrame",
        "controller",
    ):
        assert forbidden not in encoded


def test_opaque_uir_facts_are_copied_deeply_frozen_and_hash_stable() -> None:
    payload = minimal_document()
    nested = [{"label": "safe"}]
    payload["nodes"]["node:root"]["layout"] = {"items": nested}  # type: ignore[index]

    document = UIRDocument.model_validate(payload)
    initial_hash = uir_sha256(document)
    node = document.nodes["node:root"]

    nested.append({"label": "input mutation"})
    with pytest.raises(TypeError):
        node.layout["items"][0]["label"] = "late mutation"  # type: ignore[index]
    with pytest.raises(TypeError):
        document.nodes["node:other"] = node
    assert node.layout == {"items": ({"label": "safe"},)}
    assert uir_sha256(document) == initial_hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("layout", {"metadata": {"accessToken": "private-value"}}),
        ("visual", {"localPath": "C:\\private\\asset.png"}),
        ("interactions", ({"targetPackageId": "private-target"},)),
        ("layout", {"targetPackageIdOverride": "private-target"}),
        ("layout", {"targetPackage": "private-target"}),
        ("layout", {"targetComponent": "private-target"}),
        ("layout", {"packageRef": "private-target"}),
        ("layout", {"packageKey": "private-target"}),
        ("visual", {"thumbnailBase64": "private-payload"}),
        ("layout", {"token": "private-token"}),
        ("layout", {"authToken": "private-token"}),
        ("layout", {"authTokenValue": "private-token"}),
        ("layout", {"sessionSecret": "private-value"}),
        ("layout", {"secretValue": "private-value"}),
        ("layout", {"privateSecretMaterial": "private-value"}),
        ("layout", {r"C:\private\key.txt": "safe-value"}),
        ("layout", {"message": r"failed(C:\private\asset.png)"}),
        ("visual", {"resourceBytes": "private-payload"}),
        ("visual", {"encodedBlob": "private-payload"}),
    ],
)
def test_private_values_in_node_fact_fields_become_safe_diagnostics(
    field: str, value: object
) -> None:
    payload = minimal_document()
    payload["nodes"]["node:root"][field] = value  # type: ignore[index]

    document = UIRDocument.model_validate(payload)
    diagnostics = validate_uir(document)
    encoded = canonical_uir_bytes(document)

    assert any(item.code == "uir.private_data_forbidden" for item in diagnostics)
    assert b"private-value" not in encoded
    assert b"private-target" not in encoded
    assert b"private-payload" not in encoded
    assert b"private-token" not in encoded
    assert b"C:\\private" not in encoded
    assert all("private-value" not in item.message for item in diagnostics)


def test_private_values_in_component_opaque_fields_are_diagnosed() -> None:
    payload = minimal_document()
    payload["nodes"]["node:root"]["component"] = {  # type: ignore[index]
        "variantProperties": {},
        "overrides": {"rawBytes": "payload"},
    }
    payload["componentDefinitions"] = {
        "definition:root": {
            "id": "definition:root",
            "sourceNodeId": "root",
            "name": "Root",
            "properties": {"base64Data": "payload"},
        }
    }

    document = UIRDocument.model_validate(payload)

    assert len(
        [item for item in validate_uir(document) if item.code == "uir.private_data_forbidden"]
    ) == 2


def test_opaque_uir_facts_reject_raw_bytes_at_schema_boundary() -> None:
    payload = minimal_document()
    payload["nodes"]["node:root"]["visual"] = {"value": b"private"}  # type: ignore[index]

    with pytest.raises(ValidationError, match="raw bytes"):
        UIRDocument.model_validate(payload)


def test_absolute_path_shaped_user_text_is_not_treated_as_opaque_metadata() -> None:
    payload = minimal_document()
    payload["nodes"]["node:root"]["source"]["type"] = "TEXT"  # type: ignore[index]
    payload["nodes"]["node:root"]["text"] = {"content": "C:\\UI\\Label"}  # type: ignore[index]

    document = UIRDocument.model_validate(payload)

    assert not any(item.code == "uir.private_data_forbidden" for item in validate_uir(document))
    assert b"C:\\\\UI\\\\Label" in canonical_uir_bytes(document)


def test_unicode_layer_name_with_internal_slash_is_not_an_absolute_path() -> None:
    payload = minimal_document()
    payload["nodes"]["node:root"]["source"]["name"] = "组件/状态"  # type: ignore[index]

    document = UIRDocument.model_validate(payload)

    assert not any(item.code == "uir.private_data_forbidden" for item in validate_uir(document))
    assert "组件/状态".encode() in canonical_uir_bytes(document)


def test_named_design_token_fact_is_not_mistaken_for_a_bearer_token() -> None:
    payload = minimal_document()
    payload["nodes"]["node:root"]["layout"] = {"designToken": "spacing.large"}  # type: ignore[index]

    document = UIRDocument.model_validate(payload)

    assert not any(item.code == "uir.private_data_forbidden" for item in validate_uir(document))
    assert b"spacing.large" in canonical_uir_bytes(document)


def test_deep_opaque_metadata_is_diagnosed_and_canonicalized_without_recursion() -> None:
    payload = minimal_document()
    nested: dict[str, object] = {}
    for _ in range(1_100):
        nested = {"next": nested}
    document = UIRDocument.model_validate(payload)
    root = document.nodes["node:root"].model_copy(update={"layout": nested})
    document = document.model_copy(update={"nodes": {root.id: root}})

    diagnostics = validate_uir(document)
    encoded = canonical_uir_bytes(document)

    assert any(item.code == "uir.private_data_forbidden" for item in diagnostics)
    assert b"redacted-depth" in encoded


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bounds_x", float("nan")),
        ("bounds_width", float("inf")),
        ("rotation", float("-inf")),
        ("font_size", float("nan")),
    ],
)
def test_typed_uir_numbers_must_be_finite(field: str, value: float) -> None:
    payload = minimal_document()
    node = payload["nodes"]["node:root"]  # type: ignore[index]
    if field == "bounds_x":
        node["geometry"]["resolvedBounds"]["x"] = value
    elif field == "bounds_width":
        node["geometry"]["resolvedBounds"]["width"] = value
    elif field == "rotation":
        node["geometry"]["rotation"] = value
    else:
        node["source"]["type"] = "TEXT"
        node["text"] = {"content": "Title", "style": {"fontSize": value}}

    with pytest.raises(ValidationError):
        UIRDocument.model_validate(payload)


def test_text_font_candidates_must_be_nonblank() -> None:
    payload = minimal_document()
    node = payload["nodes"]["node:root"]  # type: ignore[index]
    node["source"]["type"] = "TEXT"
    node["text"] = {
        "content": "Title",
        "style": {"fontCandidates": [" "]},
    }

    with pytest.raises(ValidationError):
        UIRDocument.model_validate(payload)
