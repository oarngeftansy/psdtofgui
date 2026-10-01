from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from figma_to_fgui.agent import AgentClient, AgentConfig, bind_local_project
from figma_to_fgui.api import create_app
from figma_to_fgui.service_contracts import ApplyStatus
from tests.helpers.zip_projects import write_project_zip


def _write_web_dist(root: Path) -> Path:
    root.mkdir()
    (root / "assets").mkdir()
    (root / "index.html").write_text("<html><body><div id='root'></div></body></html>", "utf-8")
    (root / "assets" / "app-abc12345.js").write_text("console.log('app')", "utf-8")
    (root / "assets" / "runtime.js").write_text("console.log('runtime')", "utf-8")
    return root


def test_static_console_serves_shell_without_shadowing_api_routes(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            web_dist=_write_web_dist(tmp_path / "dist"),
        )
    )

    assert client.get("/").text == "<html><body><div id='root'></div></body></html>"
    assert client.get("/jobs/designer-route").text == "<html><body><div id='root'></div></body></html>"
    asset = client.get("/assets/app-abc12345.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "immutable" not in client.get("/assets/runtime.js").headers.get("cache-control", "")
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/v1/jobs/missing").status_code == 404


@pytest.mark.parametrize("web_dist", ["missing", "not-a-directory", "missing-assets"])
def test_static_console_rejects_missing_or_invalid_production_build(tmp_path: Path, web_dist: str) -> None:
    candidate = tmp_path / web_dist
    if web_dist == "not-a-directory":
        candidate.write_text("not a build", "utf-8")
    if web_dist == "missing-assets":
        candidate.mkdir()
        (candidate / "index.html").write_text("<div id='root'></div>", "utf-8")

    with pytest.raises(ValueError, match="web_dist.*index.html"):
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            web_dist=candidate,
        )


def test_uploaded_zip_review_approval_and_agent_preserve_local_changes(tmp_path: Path) -> None:
    fixture_root = Path("tests/fixtures")
    original = b"<component name='uploaded'/>"
    archive = write_project_zip(
        tmp_path / "GameUI.zip",
        {
            "Sample/package.xml": b"<package id='sample01'><resources/></package>",
            "Sample/Panel/Panel_Sample_Main.xml": original,
        },
    )
    local_project = tmp_path / "local-project"
    (local_project / "Sample" / "Panel").mkdir(parents=True)
    (local_project / "Sample" / "package.xml").write_bytes(
        b"<package id='sample01'><resources/></package>"
    )
    target = local_project / "Sample" / "Panel" / "Panel_Sample_Main.xml"
    target.write_bytes(original)
    server = TestClient(
        create_app(
            data_dir=tmp_path / "server",
            fixtures_root=fixture_root,
            rules_path=Path("rules/default/classification.yaml"),
            allow_fixture_jobs=True,
        )
    )
    with archive.open("rb") as content:
        uploaded = server.post(
            "/v1/projects/uploads",
            files={"project": (archive.name, content, "application/zip")},
        )
    assert uploaded.status_code == 201, uploaded.text
    project_id = uploaded.json()["project_id"]
    assert uploaded.json()["packages"] == [{"name": "Sample", "resource_count": 2}]
    assert server.post(
        "/v1/agents/register",
        json={"version": 1, "agent_id": "agent-1", "name": "Desk"},
    ).status_code == 200
    assert server.post(
        "/v1/projects/bind",
        json={"version": 1, "project_id": project_id, "agent_id": "agent-1"},
    ).status_code == 200
    created = server.post(
        f"/v1/projects/{project_id}/jobs",
        json={
            "version": 1,
            "project_id": project_id,
            "fixture_name": "simple-frame.json",
            "package_name": "Sample",
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job_id"]
    preview = server.get(f"/v1/jobs/{job_id}/designer-preview")
    assert preview.status_code == 200, preview.text
    for forbidden in ("sha256", "relative_path", "<component", "changeset", "rule_id"):
        assert forbidden not in preview.text
    advanced = server.get(f"/v1/jobs/{job_id}/designer-preview?details=advanced")
    changed = next(
        item
        for item in advanced.json()["details"]["files"]
        if item["relative_path"] == "Sample/Panel/Panel_Sample_Main.xml"
    )
    assert server.post(f"/v1/jobs/{job_id}/approve").json()["status"] == "approved"

    def relay(request: httpx.Request) -> httpx.Response:
        response = server.request(
            request.method,
            request.url.path,
            content=request.content,
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    agent = AgentClient(
        AgentConfig(
            agent_id="agent-1",
            name="Desk",
            api_url="http://service.test",
            projects={project_id: bind_local_project(local_project)},
        ),
        transport=httpx.MockTransport(relay),
    )
    applied = agent.poll_once()
    assert applied is not None and applied.status is ApplyStatus.APPLIED
    assert server.get(f"/v1/jobs/{job_id}").json()["status"] == "applied"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == changed["after_sha256"]
    backup = local_project / ".figma-to-fgui" / "backups" / job_id / target.relative_to(local_project)
    assert backup.read_bytes() == original

    second_archive = write_project_zip(
        tmp_path / "GameUI-after-apply.zip",
        {
            "Sample/package.xml": (local_project / "Sample" / "package.xml").read_bytes(),
            "Sample/Panel/Panel_Sample_Main.xml": target.read_bytes(),
        },
    )
    with second_archive.open("rb") as content:
        uploaded_second = server.post(
            "/v1/projects/uploads",
            files={"project": (second_archive.name, content, "application/zip")},
        )
    assert uploaded_second.status_code == 201, uploaded_second.text
    second_project_id = uploaded_second.json()["project_id"]
    assert server.post(
        "/v1/projects/bind",
        json={"version": 1, "project_id": second_project_id, "agent_id": "agent-1"},
    ).status_code == 200
    agent = AgentClient(
        agent.config.model_copy(
            update={"projects": {**agent.config.projects, second_project_id: bind_local_project(local_project)}}
        ),
        transport=httpx.MockTransport(relay),
    )
    second = server.post(
        f"/v1/projects/{second_project_id}/jobs",
        json={
            "version": 1,
            "fixture_name": "simple-frame.json",
            "project_id": second_project_id,
            "package_name": "Sample",
        },
    )
    assert second.status_code == 200, second.text
    second_job_id = second.json()["job_id"]
    assert server.post(f"/v1/jobs/{second_job_id}/approve").status_code == 200
    def stale_relay(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/{second_job_id}/artifact"):
            target.write_bytes(b"<component name='local-change'/>")
        return relay(request)

    blocked = AgentClient(agent.config, transport=httpx.MockTransport(stale_relay)).poll_once()
    assert blocked is not None and blocked.status is ApplyStatus.FAILED
    assert blocked.diagnostics[0].code == "local_project_changed"
    assert target.read_bytes() == b"<component name='local-change'/>"
    assert not (local_project / ".figma-to-fgui" / "backups" / second_job_id).exists()
