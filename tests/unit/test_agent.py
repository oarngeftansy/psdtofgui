from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from figma_to_fgui.agent import AgentClient, AgentConfig, BoundProject, fingerprint_local_project
from figma_to_fgui.cli import app
from figma_to_fgui.service_contracts import ApplyStatus


def make_handler(project_root: Path, *, stale: bool = False) -> tuple[httpx.MockTransport, list[dict[str, object]]]:
    payload = b"<component/>"
    reports: list[dict[str, object]] = []
    assignment = {
        "version": 1,
        "job_id": "job-1",
        "project_id": "project-1",
        "status": "applying",
        "diagnostics": [],
        "artifact_sha256": "a" * 64,
    }
    change = {
        "operation": "replace" if stale else "create",
        "relative_path": "Sample.xml",
        "before_sha256": "0" * 64 if stale else None,
        "after_sha256": hashlib.sha256(payload).hexdigest(),
        "content_b64": base64.b64encode(payload).decode("ascii"),
    }
    bundle = {"version": 1, "job_id": "job-1", "project_id": "project-1", "files": [change]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/assignments/next"):
            return httpx.Response(200, json=assignment)
        if "/agents/agent-1/assignments/job-1/artifact" in request.url.path:
            return httpx.Response(200, json=bundle)
        if request.url.path.endswith("/apply-result"):
            reports.append(json.loads(request.content))
            return httpx.Response(200, json={**assignment, "status": reports[-1]["status"]})
        raise AssertionError(request.url.path)

    return httpx.MockTransport(handler), reports


def bound_config(project_root: Path) -> AgentConfig:
    return AgentConfig(
        agent_id="agent-1",
        name="Desk",
        api_url="http://service.test",
        projects={
            "project-1": BoundProject(
                path=project_root,
                fingerprint=fingerprint_local_project(project_root),
                package_names=(),
            )
        },
    )


def test_config_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "agent.json"
    expected = bound_config(tmp_path / "project")
    expected.save(path)
    assert AgentConfig.load(path) == expected


def test_bind_command_persists_local_project_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    (project / "Sample").mkdir(parents=True)
    (project / "Sample" / "package.xml").write_text("<package id='sample'/>", "utf-8")
    config_path = tmp_path / "agent.json"
    AgentConfig(agent_id="agent-1", name="Desk").save(config_path)
    monkeypatch.setattr(AgentClient, "bind", lambda self, project_id: None)

    result = CliRunner().invoke(app, ["agent", "bind", "project-1", str(project), "--config-path", str(config_path)])

    assert result.exit_code == 0
    binding = AgentConfig.load(config_path).projects["project-1"]
    assert binding.path == project.resolve()
    assert binding.fingerprint == fingerprint_local_project(project)
    assert binding.package_names == ("Sample",)


def test_poll_once_applies_assignment_and_reports_success(tmp_path: Path) -> None:
    transport, reports = make_handler(tmp_path)
    result = AgentClient(bound_config(tmp_path), transport=transport).poll_once()
    assert result is not None
    assert result.status is ApplyStatus.APPLIED
    assert (tmp_path / "Sample.xml").read_bytes() == b"<component/>"
    assert reports == [result.model_dump(mode="json")]


def test_poll_failure_reports_without_absolute_project_path(tmp_path: Path) -> None:
    (tmp_path / "Sample.xml").write_bytes(b"local edit")
    transport, reports = make_handler(tmp_path, stale=True)
    result = AgentClient(bound_config(tmp_path), transport=transport).poll_once()
    assert result is not None
    assert result.status is ApplyStatus.FAILED
    assert str(tmp_path) not in result.model_dump_json()
    assert str(tmp_path) not in json.dumps(reports)


def test_poll_refuses_newer_local_work_without_backup(tmp_path: Path) -> None:
    expected = b"<component name='uploaded'/>"
    target = tmp_path / "Sample.xml"
    target.write_bytes(expected)
    config = bound_config(tmp_path)
    reports: list[dict[str, object]] = []
    assignment = {
        "version": 1,
        "job_id": "job-1",
        "project_id": "project-1",
        "project_fingerprint": config.projects["project-1"].fingerprint,
        "status": "applying",
    }
    change = {
        "operation": "replace",
        "relative_path": "Sample.xml",
        "before_sha256": hashlib.sha256(expected).hexdigest(),
        "after_sha256": hashlib.sha256(b"<component/>").hexdigest(),
        "content_b64": base64.b64encode(b"<component/>").decode("ascii"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/assignments/next"):
            return httpx.Response(200, json=assignment)
        if request.url.path.endswith("/artifact"):
            target.write_bytes(b"local edit")
            return httpx.Response(
                200,
                json={"version": 1, "job_id": "job-1", "project_id": "project-1", "files": [change]},
            )
        if request.url.path.endswith("/apply-result"):
            reports.append(json.loads(request.content))
            return httpx.Response(200, json={"version": 1, "job_id": "job-1", "project_id": "project-1", "status": "failed"})
        raise AssertionError(request.url.path)

    result = AgentClient(config, transport=httpx.MockTransport(handler)).poll_once()

    assert result is not None
    assert result.status is ApplyStatus.FAILED
    assert result.diagnostics[0].code == "local_project_changed"
    assert result.diagnostics[0].message == "本地工程已有新修改，请重新上传最新版"
    assert target.read_bytes() == b"local edit"
    assert not (tmp_path / ".figma-to-fgui" / "backups" / "job-1").exists()
    assert reports == [result.model_dump(mode="json")]


def test_poll_returns_none_when_no_assignment(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(204))
    assert AgentClient(bound_config(tmp_path), transport=transport).poll_once() is None
