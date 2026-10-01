from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree

from figma_to_fgui.api import create_app
from figma_to_fgui.project_store import ProjectStore
from figma_to_fgui.semantic_config import (
    build_semantic_analyzer,
    load_semantic_service_settings,
)
from tests.helpers.zip_projects import write_project_zip

PLUGIN_HEADERS = {"X-Figma-Plugin-Token": "test-plugin-token"}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeOpenAIService:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.requests: list[dict[str, object]] = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert isinstance(payload, dict)
        self.requests.append(payload)
        if self.scenario == "ai_failure":
            return httpx.Response(500, text="raw private fake response")
        content = payload["messages"][1]["content"]  # type: ignore[index]
        is_screenshot_request = isinstance(content, list)
        needs_screenshot = self.scenario == "screenshot_success" and not is_screenshot_request
        summary_text = content[0]["text"] if is_screenshot_request else content
        summary = json.loads(summary_text)
        node_id = summary["nodes"][0]["id"]
        decisions = []
        if self.scenario == "structure_success":
            decisions = [{
                "node_id": node_id,
                "semantic_type": "Button",
                "fgui_name": "AI",
                "confidence": 0.99,
            }]
        elif self.scenario == "screenshot_success" and is_screenshot_request:
            decisions = [{
                "node_id": node_id,
                "semantic_type": "Panel",
                "fgui_name": "Shot",
                "confidence": 0.99,
            }]
        result = {
            "version": 1,
            "decisions": decisions,
            "screenshot_recommended": needs_screenshot,
            "screenshot_reason": "Visual grouping needs confirmation." if needs_screenshot else None,
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result)}}]},
        )


@pytest.fixture
def fake_ai_server() -> type[FakeOpenAIService]:
    return FakeOpenAIService


@dataclass(frozen=True)
class PackageWorkflowResult:
    archive_is_valid: bool
    xml_is_valid: bool
    original_project_sha256: str
    original_project_sha256_after: str
    diagnostics: tuple[str, ...]
    ai_requests: tuple[dict[str, object], ...]
    archive_members: dict[str, bytes]


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _commit_selection(client: TestClient, scenario: str) -> str:
    started = client.post(
        "/v1/figma/selections/uploads",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "idempotency_key": f"ai-e2e-{scenario}"},
    )
    assert started.status_code == 201, started.text
    upload_id = started.json()["upload_id"]
    manifest = {
        "version": 1,
        "display_name": "AI delivery fixture",
        "top_level_nodes": [
            {
                "id": "private-checkout-frame",
                "name": "Checkout",
                "type": "FRAME",
                "bounds": {"x": 0, "y": 0, "width": 600, "height": 180},
                "children": [
                    {
                        "id": "private-checkout-background",
                        "name": "Checkout background",
                        "type": "RECTANGLE",
                        "bounds": {"x": 0, "y": 0, "width": 600, "height": 180},
                        "resource_keys": ["preview"],
                    },
                    {
                        "id": "private-checkout-label",
                        "name": "Checkout label",
                        "type": "TEXT",
                        "bounds": {"x": 20, "y": 20, "width": 140, "height": 24},
                        "text": "Non-sensitive fixture text",
                    }
                ],
            }
        ],
        "resources": [{"key": "preview", "mime_type": "image/png", "size": len(PNG)}],
    }
    assert client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest",
        headers=PLUGIN_HEADERS,
        json=manifest,
    ).status_code == 200
    assert client.put(
        f"/v1/figma/selections/uploads/{upload_id}/resources/preview",
        headers={**PLUGIN_HEADERS, "content-type": "image/png"},
        content=PNG,
    ).status_code == 200
    committed = client.post(
        f"/v1/figma/selections/uploads/{upload_id}/commit", headers=PLUGIN_HEADERS
    )
    assert committed.status_code == 200, committed.text
    return str(committed.json()["selection_id"])


def run_package_workflow(
    scenario: str,
    fake_ai_server: type[FakeOpenAIService],
    project_fixture: Path,
    tmp_path: Path,
) -> PackageWorkflowResult:
    fake_scenario = "screenshot_success" if scenario == "screenshot_declined" else scenario
    fake = fake_ai_server(fake_scenario)
    settings = load_semantic_service_settings(
        {
            "AI_SEMANTIC_ENABLED": "true",
            "AI_SEMANTIC_PROVIDER": "openai_compatible",
            "AI_SEMANTIC_BASE_URL": "https://fake-ai.example.test/v1",
            "AI_SEMANTIC_MODEL": "fake-semantic-model",
            "AI_SEMANTIC_API_KEY": "fake-test-key",
            "AI_SEMANTIC_TIMEOUT_SECONDS": "2",
            "AI_SEMANTIC_CONFIDENCE_THRESHOLD": "0.75",
        }
    )
    analyzer = (
        None
        if scenario == "rules_baseline"
        else build_semantic_analyzer(settings, transport=fake.transport)
    )
    if scenario != "rules_baseline":
        assert analyzer is not None
    data_dir = tmp_path / f"data-{scenario}"
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=analyzer,
        )
    )
    selection_id = _commit_selection(client, scenario)
    source_archive_sha = hashlib.sha256(project_fixture.read_bytes()).hexdigest()
    with project_fixture.open("rb") as source:
        uploaded = client.post(
            "/v1/projects/uploads",
            headers=PLUGIN_HEADERS,
            files={"project": (project_fixture.name, source, "application/zip")},
        )
    assert uploaded.status_code == 201, uploaded.text
    project_id = str(uploaded.json()["project_id"])
    project_root = ProjectStore(data_dir).artifact_path(project_id)
    original_sha = _tree_sha256(project_root)
    created = client.post(
        f"/v1/figma/selections/{selection_id}/projects/{project_id}/jobs",
        headers=PLUGIN_HEADERS,
        json={
            "version": 1,
            "selection_id": selection_id,
            "project_id": project_id,
            "package_name": "Quiz",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "ready_for_review"
    job_id = str(created.json()["job_id"])
    started = client.post(
        f"/v1/jobs/{job_id}/package",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "mode": "update", "project_name": "Quiz"},
    )
    assert started.status_code == 202, started.text
    if scenario in {"screenshot_success", "screenshot_declined"}:
        assert started.json()["stage"] == "awaiting_screenshot_consent"
        consent = client.post(
            f"/v1/jobs/{job_id}/semantic-screenshot-consent",
            headers=PLUGIN_HEADERS,
            json={"version": 1, "approved": scenario == "screenshot_success"},
        )
        assert consent.status_code == 202, consent.text
        if scenario == "screenshot_success":
            uploaded_screenshot = client.post(
                f"/v1/jobs/{job_id}/semantic-screenshot",
                headers={**PLUGIN_HEADERS, "content-type": "image/png"},
                content=PNG,
            )
            assert uploaded_screenshot.status_code == 202, uploaded_screenshot.text
    status = client.get(f"/v1/jobs/{job_id}/package", headers=PLUGIN_HEADERS)
    assert status.status_code == 200, status.text
    assert status.json()["stage"] == "ready", status.text
    download = client.get(f"/v1/jobs/{job_id}/package/download", headers=PLUGIN_HEADERS)
    assert download.status_code == 200, download.text
    output = tmp_path / f"result-{scenario}.zip"
    output.write_bytes(download.content)
    xml_is_valid = True
    with ZipFile(output) as archive:
        archive_is_valid = archive.testzip() is None
        xml_names = [name for name in archive.namelist() if name.endswith(".xml")]
        archive_members = {name: archive.read(name) for name in archive.namelist()}
        try:
            for name in xml_names:
                etree.fromstring(archive.read(name))
        except etree.LxmlError:
            xml_is_valid = False
    assert hashlib.sha256(project_fixture.read_bytes()).hexdigest() == source_archive_sha
    result = PackageWorkflowResult(
        archive_is_valid=archive_is_valid,
        xml_is_valid=xml_is_valid and bool(xml_names),
        original_project_sha256=original_sha,
        original_project_sha256_after=_tree_sha256(project_root),
        diagnostics=tuple(item["code"] for item in status.json()["diagnostics"]),
        ai_requests=tuple(fake.requests),
        archive_members=archive_members,
    )
    if analyzer is not None:
        analyzer.close()
    return result


@pytest.fixture
def project_fixture(tmp_path: Path) -> Path:
    return write_project_zip(
        tmp_path / "Quiz.zip",
        {"Quiz/package.xml": b"<package id='quiz'><resources/></package>"},
    )


@pytest.mark.parametrize(
    "scenario",
    ["structure_success", "screenshot_success", "screenshot_declined", "ai_failure"],
)
def test_ai_semantic_package_scenarios(
    scenario: str,
    fake_ai_server: type[FakeOpenAIService],
    project_fixture: Path,
    tmp_path: Path,
) -> None:
    baseline = run_package_workflow("rules_baseline", fake_ai_server, project_fixture, tmp_path)
    result = run_package_workflow(scenario, fake_ai_server, project_fixture, tmp_path)

    assert result.archive_is_valid
    assert result.xml_is_valid
    assert result.original_project_sha256 == result.original_project_sha256_after
    expected_requests = {
        "structure_success": 1,
        "screenshot_success": 2,
        "screenshot_declined": 1,
        "ai_failure": 3,
    }
    assert len(result.ai_requests) == expected_requests[scenario]
    if scenario == "screenshot_success":
        assert isinstance(result.ai_requests[1]["messages"][1]["content"], list)  # type: ignore[index]
        assert "semantic.ai_applied" in result.diagnostics
        component = result.archive_members["Quiz/Panel/Panel_Quiz_Shot.xml"]
        assert etree.fromstring(component).attrib["name"] == "Panel_Quiz_Shot"
    elif scenario == "structure_success":
        assert "semantic.ai_applied" in result.diagnostics
        component = result.archive_members["Quiz/Panel/Panel_Quiz_AI.xml"]
        assert etree.fromstring(component).attrib["name"] == "Panel_Quiz_AI"
    else:
        assert result.archive_members == baseline.archive_members
    if scenario == "screenshot_declined":
        assert "semantic.screenshot_declined" in result.diagnostics
        assert "semantic.ai_applied" not in result.diagnostics
    elif scenario == "ai_failure":
        assert "ai.http_status" in result.diagnostics
        assert "semantic.fallback" in result.diagnostics
        assert "semantic.ai_applied" not in result.diagnostics
