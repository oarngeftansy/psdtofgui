from __future__ import annotations

import hashlib
import shutil
import sqlite3
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from figma_to_fgui import api
from figma_to_fgui.agent import AgentClient, AgentConfig, bind_local_project
from figma_to_fgui.api import create_app
from figma_to_fgui.project_index import index_project
from figma_to_fgui.service_contracts import ApplyStatus
from tests.helpers.zip_projects import write_project_zip


def _credential(client: TestClient) -> str:
    code = client.post("/v1/figma/pairings").json()["code"]
    return client.post(
        "/v1/figma/pairings/exchange",
        json={"version": 1, "code": code, "device_name": "Figma desktop"},
    ).json()["credential"]


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, "PNG")
    return output.getvalue()


def _commit_selection(client: TestClient, credential: str | None = None) -> tuple[str, str]:
    credential = credential or _credential(client)
    headers = {"authorization": f"Bearer {credential}"}
    image = _png()
    upload = client.post(
        "/v1/figma/selections/uploads",
        json={"version": 1, "idempotency_key": "live-checkout"},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    manifest = {
        "version": 1,
        "display_name": "Live checkout",
        "top_level_nodes": [
            {
                "id": "figma-private-frame",
                "name": "LiveCheckout",
                "type": "FRAME",
                "bounds": {"x": 0, "y": 0, "width": 600, "height": 400},
                "children": [
                    {
                        "id": "figma-private-background",
                        "name": "Live background",
                        "type": "RECTANGLE",
                        "bounds": {"x": 0, "y": 0, "width": 600, "height": 400},
                        "resource_keys": ["figma-resource-key"],
                    },
                    {
                        "id": "figma-private-text",
                        "name": "Live label",
                        "type": "TEXT",
                        "bounds": {"x": 20, "y": 30, "width": 200, "height": 30},
                        "text": "Live text wins",
                    }
                ],
            }
        ],
        "resources": [{"key": "figma-resource-key", "mime_type": "image/png", "size": len(image)}],
    }
    assert client.put(
        f"/v1/figma/selections/uploads/{upload.json()['upload_id']}/manifest",
        json=manifest,
        headers=headers,
    ).status_code == 200
    assert client.put(
        f"/v1/figma/selections/uploads/{upload.json()['upload_id']}/resources/figma-resource-key",
        content=image,
        headers={**headers, "content-type": "image/png"},
    ).status_code == 200
    committed = client.post(
        f"/v1/figma/selections/uploads/{upload.json()['upload_id']}/commit", headers=headers
    )
    assert committed.status_code == 200, committed.text
    return str(committed.json()["selection_id"]), credential


def test_live_selection_job_uses_committed_selection_and_not_fixture(tmp_path: Path) -> None:
    archive = write_project_zip(
        tmp_path / "GameUI.zip",
        {
            "Sample/package.xml": b"<package id='sample'><resources/></package>",
        },
    )
    local_project = tmp_path / "local-project"
    (local_project / "Sample").mkdir(parents=True)
    original_package = b"<package id='sample'><resources/></package>"
    (local_project / "Sample/package.xml").write_bytes(original_package)
    fixture_root = tmp_path / "fixtures"
    shutil.copytree(Path("tests/fixtures"), fixture_root)
    server = TestClient(
        create_app(
            data_dir=tmp_path / "server",
            fixtures_root=fixture_root,
            rules_path=Path("rules/default/classification.yaml"),
            plugin_secret=b"s" * 32,
        )
    )
    selection_id, owner_credential = _commit_selection(server)
    with archive.open("rb") as content:
        uploaded = server.post(
            "/v1/projects/uploads",
            files={"project": (archive.name, content, "application/zip")},
        )
    assert uploaded.status_code == 201, uploaded.text
    project_id = str(uploaded.json()["project_id"])
    assert server.post(
        "/v1/agents/register",
        json={"version": 1, "agent_id": "agent-1", "name": "Desk"},
    ).status_code == 200
    assert server.post(
        "/v1/projects/bind",
        json={"version": 1, "project_id": project_id, "agent_id": "agent-1"},
    ).status_code == 200
    fixture_job = server.post(
        f"/v1/projects/{project_id}/jobs",
        json={
            "version": 1,
            "project_id": project_id,
            "fixture_name": "simple-frame.json",
            "package_name": "Sample",
        },
    )
    assert fixture_job.status_code == 404
    assert server.post("/v1/jobs", content=b"not-json").status_code == 404
    openapi = server.get("/openapi.json").json()
    assert "/v1/jobs" not in openapi["paths"]
    assert "/v1/projects/{project_id}/jobs" not in openapi["paths"]
    assert "fixture_name" not in str(openapi)
    request_path = f"/v1/figma/selections/{selection_id}/projects/{project_id}/jobs"
    request_body = {
        "version": 1,
        "selection_id": selection_id,
        "project_id": project_id,
        "package_name": "Sample",
    }
    assert server.post(request_path, json=request_body).status_code == 401
    other_credential = _credential(server)
    assert server.post(
        request_path, json=request_body, headers={"authorization": f"Bearer {other_credential}"}
    ).status_code == 404
    created = server.post(
        request_path,
        json=request_body,
        headers={"authorization": f"Bearer {owner_credential}"},
    )
    assert created.status_code == 200, created.text
    job_id = str(created.json()["job_id"])
    for forbidden in ("selection_id", "fingerprint", "figma-private", "fixture"):
        assert forbidden not in created.text.lower()
    with sqlite3.connect(tmp_path / "server" / "server.db") as connection:
        source = connection.execute(
            "SELECT selection_id, selection_fingerprint FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    assert source == (selection_id, source[1])
    assert len(source[1]) == 64
    preview = server.get(f"/v1/jobs/{job_id}/designer-preview")
    assert preview.status_code == 200, preview.text
    assert "figma-private" not in preview.text.lower()
    advanced = server.get(f"/v1/jobs/{job_id}/designer-preview?details=advanced")
    assert advanced.status_code == 200, advanced.text
    for raw_identifier in ("figma-private-frame", "figma-private-text", "figma-resource-key"):
        assert raw_identifier not in advanced.text
    target_change = next(
        item
        for item in advanced.json()["details"]["files"]
        if item["relative_path"].endswith("Panel_Sample_LiveCheckout.xml")
    )
    package_change = next(
        item for item in advanced.json()["details"]["files"] if item["relative_path"] == "Sample/package.xml"
    )
    assert package_change["before_sha256"] == hashlib.sha256(original_package).hexdigest()
    (fixture_root / "figma" / "simple-frame.json").write_text('{"name":"fixture mutation"}', "utf-8")
    assert server.post(f"/v1/jobs/{job_id}/approve").status_code == 200

    def relay(request: httpx.Request) -> httpx.Response:
        response = server.request(
            request.method,
            request.url.path,
            content=request.content,
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    applied = AgentClient(
        AgentConfig(
            agent_id="agent-1",
            name="Desk",
            api_url="http://service.test",
            projects={project_id: bind_local_project(local_project)},
        ),
        transport=httpx.MockTransport(relay),
    ).poll_once()

    assert applied is not None and applied.status is ApplyStatus.APPLIED
    target = local_project / "Sample" / "Panel" / "Panel_Sample_LiveCheckout.xml"
    assert b"Live text wins" in target.read_bytes()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == target_change["after_sha256"]
    applied_index = index_project(local_project)
    asset = next(resource for name, resource in applied_index.by_name.items() if name.startswith("asset_"))
    assert (local_project / asset.relative_path).is_file()
    assert f'src="{asset.id}"' in target.read_text("utf-8")
    assert server.get(f"/v1/jobs/{job_id}").json()["status"] == "applied"


def test_live_job_over_bundle_limit_has_no_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = TestClient(
        create_app(
            data_dir=tmp_path / "server",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_secret=b"s" * 32,
        )
    )
    selection_id, credential = _commit_selection(server)
    archive = write_project_zip(
        tmp_path / "GameUI.zip",
        {"Sample/package.xml": b"<package id='sample'><resources/></package>"},
    )
    with archive.open("rb") as content:
        uploaded = server.post(
            "/v1/projects/uploads",
            files={"project": (archive.name, content, "application/zip")},
        )
    project_id = str(uploaded.json()["project_id"])
    monkeypatch.setattr(api, "_MAX_CHANGE_BUNDLE_BYTES", 1, raising=False)

    created = server.post(
        f"/v1/figma/selections/{selection_id}/projects/{project_id}/jobs",
        json={
            "version": 1,
            "selection_id": selection_id,
            "project_id": project_id,
            "package_name": "Sample",
        },
        headers={"authorization": f"Bearer {credential}"},
    )

    assert created.status_code == 200, created.text
    assert created.json()["status"] == "conversion_failed"
    assert not list((tmp_path / "server" / "artifacts").glob("*.json"))
