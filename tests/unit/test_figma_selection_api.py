from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from figma_to_fgui import api
from figma_to_fgui.api import create_app


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_secret=b"s" * 32,
        )
    )


def credential(client: TestClient) -> str:
    code = client.post("/v1/figma/pairings").json()["code"]
    return client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": code, "device_name": "Figma desktop"},
    ).json()["credential"]


def manifest() -> dict[str, object]:
    image = png_bytes()
    return {
        "version": 1,
        "display_name": "Checkout",
        "top_level_nodes": [
            {
                "id": "12:4",
                "name": "Checkout",
                "type": "FRAME",
                "bounds": {"x": 0, "y": 0, "width": 32, "height": 16},
                "resource_keys": ["hero"],
            }
        ],
        "resources": [{"key": "hero", "mime_type": "image/png", "size": len(image)}],
    }


def test_selection_upload_routes_require_plugin_auth_and_return_a_safe_view(client: TestClient) -> None:
    unauthorized = client.post("/v1/figma/selections/uploads", json={"idempotency_key": "a"})
    assert unauthorized.status_code == 401

    token = credential(client)
    headers = {"authorization": f"Bearer {token}"}
    created = client.post(
        "/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "a"}, headers=headers
    )
    assert created.status_code == 201
    upload_id = created.json()["upload_id"]
    assert "device" not in created.text.lower()

    assert client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest", json=manifest(), headers=headers
    ).status_code == 200
    resource = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/resources/hero",
        content=png_bytes(),
        headers={**headers, "content-type": "image/png"},
    )
    assert resource.status_code == 200
    committed = client.post(f"/v1/figma/selections/uploads/{upload_id}/commit", headers=headers)
    assert committed.status_code == 200

    view = committed.json()
    assert view["display_name"] == "Checkout"
    assert view["top_level_summaries"] == [{"name": "Checkout", "type": "FRAME"}]
    for forbidden in ("fingerprint", "hash", "12:4", "hero", "path", "device"):
        assert forbidden not in committed.text.lower()

    fetched = client.get(f"/v1/figma/selections/{view['selection_id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == view


def test_console_session_discovers_only_its_committed_selection(client: TestClient) -> None:
    issued = client.post("/v1/figma/pairings").json()
    credential = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": issued["code"], "device_name": "Figma desktop"},
    ).json()["credential"]
    headers = {"authorization": f"Bearer {credential}"}
    upload_id = client.post(
        "/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "console-view"}, headers=headers
    ).json()["upload_id"]
    assert client.put(f"/v1/figma/selections/uploads/{upload_id}/manifest", json=manifest(), headers=headers).status_code == 200
    assert client.put(
        f"/v1/figma/selections/uploads/{upload_id}/resources/hero", content=png_bytes(), headers={**headers, "content-type": "image/png"}
    ).status_code == 200
    committed = client.post(f"/v1/figma/selections/uploads/{upload_id}/commit", headers=headers).json()

    current = client.get("/v1/figma/pairings/current/selection", headers={"x-figma-console-session": issued["console_credential"]})
    assert current.status_code == 200
    assert current.json()["selection_id"] == committed["selection_id"]
    preview_url = f"/v1/figma/pairings/current/selections/{committed['selection_id']}/previews/0"
    assert current.json()["preview_urls"] == [preview_url]
    assert client.get(preview_url, headers={"x-figma-console-session": issued["console_credential"]}).status_code == 200
    for forbidden in ("credential", "device", "hero", "12:4"):
        assert forbidden not in current.text.lower()


def test_console_preview_url_stays_bound_to_its_original_selection(client: TestClient) -> None:
    issued = client.post("/v1/figma/pairings").json()
    credential = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": issued["code"], "device_name": "Figma desktop"},
    ).json()["credential"]
    headers = {"authorization": f"Bearer {credential}"}

    def commit(idempotency_key: str) -> str:
        upload_id = client.post(
            "/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": idempotency_key}, headers=headers
        ).json()["upload_id"]
        assert client.put(f"/v1/figma/selections/uploads/{upload_id}/manifest", json=manifest(), headers=headers).status_code == 200
        assert client.put(
            f"/v1/figma/selections/uploads/{upload_id}/resources/hero", content=png_bytes(), headers={**headers, "content-type": "image/png"}
        ).status_code == 200
        return client.post(f"/v1/figma/selections/uploads/{upload_id}/commit", headers=headers).json()["selection_id"]

    first = commit("first")
    session_headers = {"x-figma-console-session": issued["console_credential"]}
    first_url = client.get("/v1/figma/pairings/current/selection", headers=session_headers).json()["preview_urls"][0]
    second = commit("second")
    assert first != second
    assert first in first_url and second not in first_url
    assert client.get(first_url, headers=session_headers).status_code == 200


def test_selection_upload_does_not_allow_a_second_device_to_read_or_write(client: TestClient) -> None:
    owner, other = credential(client), credential(client)
    owner_headers = {"authorization": f"Bearer {owner}"}
    created = client.post(
        "/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "a"}, headers=owner_headers
    ).json()
    upload_id = created["upload_id"]

    denied = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest",
        json=manifest(),
        headers={"authorization": f"Bearer {other}"},
    )
    assert denied.status_code == 404
    assert "device" not in denied.text.lower()


def test_resource_endpoint_enforces_actual_bytes_without_internal_error_details(client: TestClient) -> None:
    token = credential(client)
    headers = {"authorization": f"Bearer {token}"}
    upload_id = client.post(
        "/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "bytes"}, headers=headers
    ).json()["upload_id"]
    assert client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest", json=manifest(), headers=headers
    ).status_code == 200

    response = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/resources/hero",
        content=png_bytes() + b"extra",
        headers={**headers, "content-type": "image/png"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "selection_resource_size",
        "message": "Selection upload could not be completed.",
    }


def test_manifest_body_is_bounded_before_json_parsing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token = credential(client)
    headers = {"authorization": f"Bearer {token}"}
    upload_id = client.post(
        "/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "manifest-limit"}, headers=headers
    ).json()["upload_id"]
    monkeypatch.setattr(api, "_SELECTION_MANIFEST_BYTES", 32, raising=False)

    response = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest", json=manifest(), headers=headers
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "selection_too_large"


def test_owner_mismatch_uses_the_same_not_found_code_as_a_missing_upload(client: TestClient) -> None:
    owner, other = credential(client), credential(client)
    created = client.post(
        "/v1/figma/selections/uploads",
        json={"version": 1, "idempotency_key": "owner-code"},
        headers={"authorization": f"Bearer {owner}"},
    ).json()
    response = client.put(
        f"/v1/figma/selections/uploads/{created['upload_id']}/manifest",
        json=manifest(),
        headers={"authorization": f"Bearer {other}"},
    )
    assert response.json()["detail"]["code"] == "selection_not_found"


def test_resource_upload_writes_multi_chunk_input_to_a_temporary_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = credential(client)
    headers = {"authorization": f"Bearer {token}"}
    upload_id = client.post(
        "/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "chunks"}, headers=headers
    ).json()["upload_id"]
    assert client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest", json=manifest(), headers=headers
    ).status_code == 200
    monkeypatch.setattr(api, "_UPLOAD_CHUNK_BYTES", 7)
    monkeypatch.setattr(api.Path, "read_bytes", lambda path: pytest.fail("must not buffer upload"))
    response = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/resources/hero",
        content=png_bytes(),
        headers={**headers, "content-type": "image/png"},
    )
    assert response.status_code == 200
    assert response.status_code == 200


def test_encoded_upload_identifier_never_creates_an_incoming_directory(client: TestClient) -> None:
    token = credential(client)
    response = client.put(
        "/v1/figma/selections/uploads/%2e%2e/resources/hero",
        content=b"x",
        headers={"authorization": f"Bearer {token}", "content-type": "image/png"},
    )
    assert response.status_code == 404
    assert not (client.app.state.data_dir / "incoming").exists()


def test_deep_manifest_json_returns_safe_error(client: TestClient) -> None:
    token = credential(client)
    upload_id = client.post(
        "/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "deep"}, headers={"authorization": f"Bearer {token}"}
    ).json()["upload_id"]
    body = '{"style":' * 1500 + "{}" + "}" * 1500
    response = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest",
        content=body,
        headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_selection_manifest"
