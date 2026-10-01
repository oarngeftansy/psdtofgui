from __future__ import annotations

import shutil
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from figma_to_fgui.agent import AgentClient, AgentConfig
from figma_to_fgui.api import create_app
from figma_to_fgui.changeset import hash_tree
from figma_to_fgui.service_contracts import ApplyStatus


def test_local_api_to_agent_apply_loop(tmp_path: Path) -> None:
    fixture_root = Path("tests/fixtures")
    fixture_hash = hash_tree(fixture_root / "fgui")
    project_root = tmp_path / "project"
    shutil.copytree(fixture_root / "fgui", project_root)
    server = TestClient(
        create_app(
            data_dir=tmp_path / "server",
            fixtures_root=fixture_root,
            rules_path=Path("rules/default/classification.yaml"),
            allow_fixture_jobs=True,
        )
    )

    assert server.post(
        "/v1/agents/register",
        json={"version": 1, "agent_id": "agent-1", "name": "Desk"},
    ).status_code == 200
    assert server.post(
        "/v1/projects/bind",
        json={"version": 1, "project_id": "project-1", "agent_id": "agent-1"},
    ).status_code == 200
    created = server.post(
        "/v1/jobs",
        json={
            "version": 1,
            "fixture_name": "simple-frame.json",
            "project_id": "project-1",
            "package_name": "Sample",
        },
    ).json()
    job_id = created["job_id"]
    assert server.post(f"/v1/jobs/{job_id}/approve").status_code == 200

    def relay(request: httpx.Request) -> httpx.Response:
        response = server.request(
            request.method,
            request.url.path,
            content=request.content,
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    result = AgentClient(
        AgentConfig(
            agent_id="agent-1",
            name="Desk",
            api_url="http://service.test",
            projects={"project-1": str(project_root)},
        ),
        transport=httpx.MockTransport(relay),
    ).poll_once()

    assert result is not None
    assert result.status is ApplyStatus.APPLIED
    assert server.get(f"/v1/jobs/{job_id}").json()["status"] == "applied"
    target = project_root / "Sample" / "Panel" / "Panel_Sample_Main.xml"
    assert target.is_file()
    assert b"<component" in target.read_bytes()
    assert (project_root / ".figma-to-fgui/backups" / job_id).is_dir()
    assert hash_tree(fixture_root / "fgui") == fixture_hash
