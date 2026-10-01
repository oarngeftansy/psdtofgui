from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from figma_to_fgui.api import create_app
from figma_to_fgui.figma_pairing import PairingError
from figma_to_fgui.service_contracts import PluginPrincipal, PluginScope


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


def test_pairing_routes_exchange_a_code_once_and_never_list_credentials(client: TestClient) -> None:
    issued = client.post("/v1/figma/pairings")
    assert issued.status_code == 201
    code = issued.json()["code"]
    assert len(code) == 6 and code.isdecimal()

    exchanged = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": code, "device_name": "Figma desktop"},
    )
    assert exchanged.status_code == 200
    credential = exchanged.json()["credential"]
    device_id = exchanged.json()["device"]["device_id"]
    assert credential not in issued.text

    devices = client.get("/v1/figma/devices", headers={"x-figma-console-session": issued.json()["console_credential"]})
    assert devices.status_code == 200
    assert devices.json()[0]["device_id"] == device_id
    for forbidden in ("credential", "digest", "pairing", "code"):
        assert forbidden not in devices.text.lower()

    repeated = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": code, "device_name": "Figma desktop"},
    )
    assert repeated.status_code == 400
    assert repeated.json()["detail"]["code"] == "pairing_code_invalid"

    malformed = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": "not-a-code", "device_name": "Figma desktop"},
    )
    assert malformed.status_code == 400
    assert malformed.json()["detail"] == {
        "code": "pairing_code_invalid",
        "message": "Pairing request could not be completed.",
    }


def test_console_pairing_session_can_only_observe_its_paired_device(client: TestClient) -> None:
    issued = client.post("/v1/figma/pairings")
    session = issued.json()["console_credential"]
    headers = {"x-figma-console-session": session}

    waiting = client.get("/v1/figma/pairings/status", headers=headers)
    assert waiting.status_code == 200
    assert waiting.json()["state"] == "waiting_for_device"
    assert "code" not in waiting.text.lower()

    paired = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": issued.json()["code"], "device_name": "Figma desktop"},
    )
    assert paired.status_code == 200
    paired_status = client.get("/v1/figma/pairings/status", headers=headers)
    assert paired_status.status_code == 200
    assert paired_status.json()["state"] == "paired"
    assert paired_status.json()["device"]["device_name"] == "Figma desktop"
    for forbidden in ("credential", "digest", "code"):
        assert forbidden not in paired_status.text.lower()

    assert client.get("/v1/figma/pairings/current/selection", headers=headers).status_code == 204
    assert client.delete("/v1/figma/pairings/current", headers=headers).status_code == 204
    assert client.get("/v1/figma/pairings/status", headers=headers).status_code == 401


def test_console_session_is_required_for_device_management_and_revoke_invalidates_it(client: TestClient) -> None:
    issued = client.post("/v1/figma/pairings").json()
    paired = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": issued["code"], "device_name": "Figma desktop"},
    ).json()
    headers = {"x-figma-console-session": issued["console_credential"]}
    device_id = paired["device"]["device_id"]

    assert client.get("/v1/figma/devices").status_code == 401
    assert client.delete(f"/v1/figma/devices/{device_id}").status_code == 401
    assert client.get("/v1/figma/devices", headers=headers).json()[0]["device_id"] == device_id

    other = client.post("/v1/figma/pairings").json()
    assert client.delete(
        f"/v1/figma/devices/{device_id}", headers={"x-figma-console-session": other["console_credential"]}
    ).status_code == 401
    assert client.delete(f"/v1/figma/devices/{device_id}", headers=headers).status_code == 200
    assert client.get("/v1/figma/pairings/status", headers=headers).status_code == 401
    assert client.get("/v1/figma/pairings/current/selection", headers=headers).status_code == 401
    assert client.get(
        "/v1/figma/pairings/current/selections/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/previews/0", headers=headers
    ).status_code == 401
    assert client.post(
        "/v1/figma/selections/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/projects/project/jobs",
        json={"version": 1, "selection_id": "a" * 32, "project_id": "project", "package_name": "Sample"},
        headers=headers,
    ).status_code == 401
    with pytest.raises(PairingError, match="plugin_credential_revoked"):
        client.app.state.authenticate_plugin(f"Bearer {paired['credential']}")


def test_pairing_creation_rate_limits_each_request_source(client: TestClient) -> None:
    source_a = TestClient(client.app, client=("source-a", 50000))
    source_b = TestClient(client.app, client=("source-b", 50000))
    for _ in range(5):
        assert source_a.post("/v1/figma/pairings").status_code == 201
    limited = source_a.post("/v1/figma/pairings")
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "pairing_rate_limited"
    assert source_b.post("/v1/figma/pairings").status_code == 201


@pytest.mark.parametrize(
    ("kwargs",),
    [
        ({"json": {"version": 1, "device_name": "Figma desktop"}},),
        ({"json": {"version": 1, "code": 123456, "device_name": "Figma desktop"}},),
        ({"json": {"version": 2, "code": "123456", "device_name": "Figma desktop"}},),
        ({"content": b"not json", "headers": {"content-type": "application/json"}},),
        ({"json": ["not", "an", "object"]},),
    ],
)
def test_exchange_maps_malformed_requests_to_the_safe_pairing_code(client: TestClient, kwargs: dict[str, object]) -> None:
    response = client.post("/v1/figma/pairings/exchange", **kwargs)

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "pairing_code_invalid",
        "message": "Pairing request could not be completed.",
    }


def test_exchange_rejects_a_missing_version_before_consuming_a_valid_code(client: TestClient) -> None:
    issued = client.post("/v1/figma/pairings").json()
    code = issued["code"]

    response = client.post(
        "/v1/figma/pairings/exchange",
        json={"code": code, "device_name": "Figma desktop"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "pairing_code_invalid"
    assert client.post(
        "/v1/figma/pairings/exchange", json={"version": 1, "code": code, "device_name": "Figma desktop"}
    ).status_code == 200


def test_exchange_rate_limit_uses_the_request_source(client: TestClient) -> None:
    source_a = TestClient(client.app, client=("source-a", 50000))
    source_b = TestClient(client.app, client=("source-b", 50000))

    for _ in range(4):
        response = source_a.post(
            "/v1/figma/pairings/exchange",
            json={"version": 1, "code": "111111", "device_name": "Figma desktop"},
        )
        assert response.json()["detail"]["code"] == "pairing_code_invalid"
    limited = source_a.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": "111111", "device_name": "Figma desktop"},
    )
    assert limited.json()["detail"]["code"] == "pairing_rate_limited"

    code = source_b.post("/v1/figma/pairings").json()["code"]
    assert source_b.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": code, "device_name": "Figma browser"},
    ).status_code == 200


def test_app_plugin_authenticator_rejects_invalid_and_revoked_credentials(client: TestClient) -> None:
    issued = client.post("/v1/figma/pairings").json()
    code = issued["code"]
    exchanged = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": code, "device_name": "Figma browser"},
    ).json()
    authenticate = client.app.state.authenticate_plugin
    credential = exchanged["credential"]
    device_id = exchanged["device"]["device_id"]

    principal = authenticate(f"Bearer {credential}")
    assert principal == PluginPrincipal(
        device_id=device_id,
        scopes=(PluginScope.SELECTION_UPLOAD, PluginScope.SELECTION_READ_OWN_STATUS),
    )
    assert authenticate(
        f"Bearer {credential}", required_scope=PluginScope.SELECTION_READ_OWN_STATUS
    ) == principal
    for unrelated_scope in ("project:write", "agent:claim", "admin:manage"):
        with pytest.raises(PairingError, match="plugin_scope_denied"):
            authenticate(f"Bearer {credential}", required_scope=unrelated_scope)
    with pytest.raises(PairingError, match="plugin_credential_invalid"):
        authenticate("Bearer not-a-credential")

    revoked = client.delete(
        f"/v1/figma/devices/{device_id}", headers={"x-figma-console-session": issued["console_credential"]}
    )
    assert revoked.status_code == 200
    assert revoked.json()["device_id"] == device_id
    assert credential not in revoked.text
    with pytest.raises(PairingError, match="plugin_credential_revoked"):
        authenticate(f"Bearer {credential}")


def test_pairing_routes_fail_safely_without_a_configured_secret(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
        )
    )

    response = client.post("/v1/figma/pairings")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "plugin_credential_invalid"
    assert "secret" not in response.text.lower()
