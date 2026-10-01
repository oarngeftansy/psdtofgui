"""Public HTTP helpers shared by live Figma selection integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image


@dataclass(frozen=True)
class PairedPluginDevice:
    credential: str
    console_session: str


@dataclass(frozen=True)
class CommittedSelection:
    selection_id: str
    display_name: str


def pair_plugin_device(client: TestClient, device_name: str) -> PairedPluginDevice:
    issued = client.post("/v1/figma/pairings")
    assert issued.status_code == 201, issued.text
    exchange = client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": issued.json()["code"], "device_name": device_name},
    )
    assert exchange.status_code == 200, exchange.text
    return PairedPluginDevice(
        credential=str(exchange.json()["credential"]),
        console_session=str(issued.json()["console_credential"]),
    )


def png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGBA", (2, 2), "red").save(output, "PNG")
    return output.getvalue()


def svg_bytes() -> bytes:
    return b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><path fill="#00ff00" d="M0 0h2v2H0z"/></svg>'


def live_selection_manifest(name: str, text: str) -> tuple[dict[str, object], dict[str, tuple[str, bytes]]]:
    png, svg = png_bytes(), svg_bytes()
    resources = {
        "asset-png": ("image/png", png),
        "asset-svg": ("image/svg+xml", svg),
    }
    return (
        {
            "version": 1,
            "display_name": name,
            "top_level_nodes": [
                {
                    "id": "private-node-1",
                    "name": name,
                    "type": "FRAME",
                    "bounds": {"x": 0, "y": 0, "width": 600, "height": 400},
                    "resource_keys": ["asset-png"],
                    "children": [
                        {
                            "id": "private-node-2",
                            "name": "Live label",
                            "type": "TEXT",
                            "bounds": {"x": 20, "y": 30, "width": 200, "height": 30},
                            "text": text,
                        },
                        {
                            "id": "private-node-3",
                            "name": "Live vector",
                            "type": "VECTOR",
                            "bounds": {"x": 0, "y": 0, "width": 2, "height": 2},
                            "resource_keys": ["asset-svg"],
                        },
                    ],
                }
            ],
            "resources": [
                {"key": key, "mime_type": mime_type, "size": len(payload)}
                for key, (mime_type, payload) in resources.items()
            ],
            "warnings": [],
        },
        resources,
    )


def commit_live_selection(
    client: TestClient, credential: str, *, name: str, text: str
) -> CommittedSelection:
    headers = {"authorization": f"Bearer {credential}"}
    upload = client.post(
        "/v1/figma/selections/uploads",
        json={"version": 1, "idempotency_key": f"selection-{name}-{text}"},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    upload_id = str(upload.json()["upload_id"])
    manifest, resources = live_selection_manifest(name, text)
    stored_manifest = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest", json=manifest, headers=headers
    )
    assert stored_manifest.status_code == 200, stored_manifest.text
    for key, (mime_type, payload) in resources.items():
        stored = client.put(
            f"/v1/figma/selections/uploads/{upload_id}/resources/{key}",
            content=payload,
            headers={**headers, "content-type": mime_type},
        )
        assert stored.status_code == 200, stored.text
    committed = client.post(f"/v1/figma/selections/uploads/{upload_id}/commit", headers=headers)
    assert committed.status_code == 200, committed.text
    return CommittedSelection(
        selection_id=str(committed.json()["selection_id"]), display_name=str(committed.json()["display_name"])
    )
