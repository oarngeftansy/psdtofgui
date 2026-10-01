from __future__ import annotations

import base64
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image

from figma_to_fgui import api
from figma_to_fgui.api import create_app
from figma_to_fgui.artifacts import ArtifactStore
from figma_to_fgui.figma_selection import SelectionManifest
from figma_to_fgui.job_store import JobStore
from figma_to_fgui.models import ClassificationDecision, DecisionSource, NormalizedNode
from figma_to_fgui.project_upload import _upload_error
from figma_to_fgui.selection_store import SelectionStore
from figma_to_fgui.semantic_models import SemanticAnalysisOutcome
from figma_to_fgui.service_contracts import (
    ChangeBundle,
    ChangeFile,
    FileOperation,
    JobStatus,
    JobView,
    ProjectPackageStage,
    ProjectPackageView,
)
from tests.helpers.zip_projects import write_project_zip


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            allow_fixture_jobs=True,
        )
    )


def register_and_bind(client: TestClient, agent_id: str = "agent-1") -> None:
    response = client.post(
        "/v1/agents/register",
        json={"version": 1, "agent_id": agent_id, "name": "Desk"},
    )
    assert response.status_code == 200
    response = client.post(
        "/v1/projects/bind",
        json={"version": 1, "project_id": "project-1", "agent_id": agent_id},
    )
    assert response.status_code == 200


def create_job(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v1/jobs",
        json={
            "version": 1,
            "fixture_name": "simple-frame.json",
            "project_id": "project-1",
            "package_name": "Sample",
        },
    )
    assert response.status_code == 200
    return response.json()


def assert_designer_job_is_safe(payload: dict[str, object]) -> None:
    for forbidden in (
        "artifact_sha256",
        "project_fingerprint",
        "diagnostics",
        "rule_id",
        "relative_path",
        "changeset",
    ):
        assert forbidden not in payload


def assert_redacted_bundle(response: Response, job_id: str, migration: str) -> None:
    assert response.status_code == 200
    headers = response.headers
    assert headers["deprecation"] == "true"
    assert migration in headers["link"]
    payload = response.json()
    bundle = ChangeBundle.model_validate(payload)
    assert bundle.job_id == job_id
    assert bundle.files == ()
    for forbidden in ("sha256", "content_b64", "relative_path", "<component", "rule_id"):
        assert forbidden not in response.text


def test_job_public_routes_hide_operational_details(client: TestClient) -> None:
    register_and_bind(client)
    created = create_job(client)
    assert created["status"] == "ready_for_review"
    assert_designer_job_is_safe(created)
    job_id = created["job_id"]

    assert client.get(f"/v1/jobs/{job_id}").status_code == 200
    assert_designer_job_is_safe(client.get(f"/v1/jobs/{job_id}").json())
    preview = client.get(f"/v1/jobs/{job_id}/preview")
    assert_redacted_bundle(preview, job_id, "/designer-preview")
    assert_redacted_bundle(
        client.get(f"/v1/jobs/{job_id}/changeset"), job_id, "/assignments/"
    )

    approved = client.post(f"/v1/jobs/{job_id}/approve")
    assert approved.json()["status"] == "approved"
    assert_designer_job_is_safe(approved.json())
    assert client.post(f"/v1/jobs/{job_id}/approve").json() == approved.json()

    assignment = client.get("/v1/agents/agent-1/assignments/next")
    assert assignment.status_code == 200
    assert assignment.json()["status"] == "applying"
    assert client.get("/v1/agents/agent-1/assignments/next").status_code == 204

    bundle = client.get(f"/v1/agents/agent-1/assignments/{job_id}/artifact")
    assert bundle.status_code == 200
    assert bundle.json()["job_id"] == job_id
    assert bundle.json()["files"]
    assert client.get(f"/v1/agents/agent-2/assignments/{job_id}/artifact").status_code == 409

    applied = client.post(
        f"/v1/jobs/{job_id}/apply-result",
        json={
            "version": 1,
            "job_id": job_id,
            "agent_id": "agent-1",
            "project_id": "project-1",
            "status": "applied",
        },
    )
    assert applied.status_code == 200
    assert_designer_job_is_safe(applied.json())


def test_wrong_agent_cannot_claim_job(client: TestClient) -> None:
    register_and_bind(client, "agent-a")
    client.post(
        "/v1/agents/register",
        json={"version": 1, "agent_id": "agent-b", "name": "Other"},
    )
    job_id = create_job(client)["job_id"]
    client.post(f"/v1/jobs/{job_id}/approve")
    assert client.get("/v1/agents/agent-b/assignments/next").status_code == 204


def test_fixture_path_escape_is_rejected_without_leaking_paths(client: TestClient) -> None:
    register_and_bind(client)
    response = client.post(
        "/v1/jobs",
        json={
            "version": 1,
            "fixture_name": "../fgui/Sample/package.xml",
            "project_id": "project-1",
            "package_name": "Sample",
        },
    )
    assert response.status_code == 400
    assert "tests" not in response.text
    assert ":\\" not in response.text


def test_missing_job_returns_structured_not_found(client: TestClient) -> None:
    response = client.get("/v1/jobs/missing")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.json() == {"status": "ok"}
    assert "x-figma-to-fgui-instance" not in response.headers


def test_health_echoes_configured_instance_token_only_in_header(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            health_instance_token="test-instance-token",
        )
    )

    response = client.get("/health")

    assert response.json() == {"status": "ok"}
    assert response.headers["x-figma-to-fgui-instance"] == "test-instance-token"


def test_public_origin_cors_allows_only_the_configured_https_origin(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            public_origin="https://fgui.corp.example",
        )
    )

    allowed = client.options(
        "/v1/figma/pairings", headers={
            "Origin": "https://fgui.corp.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    blocked = client.options(
        "/v1/figma/pairings", headers={
            "Origin": "https://other.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "https://fgui.corp.example"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in blocked.headers


def test_plugin_access_accepts_only_configured_token_and_null_origin(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
        )
    )

    preflight = client.options(
        "/v1/figma/selections/uploads",
        headers={
            "Origin": "null",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-figma-plugin-token,content-type",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "null"
    blocked_origin = client.options(
        "/v1/figma/selections/uploads",
        headers={"Origin": "https://other.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in blocked_origin.headers
    assert (
        client.post("/v1/figma/selections/uploads", json={"version": 1, "idempotency_key": "k"}).status_code
        == 401
    )
    assert client.post(
        "/v1/figma/selections/uploads",
        json={"version": 1, "idempotency_key": "k"},
        headers={"X-Figma-Plugin-Token": "wrong"},
    ).status_code == 401
    assert (
        client.post(
            "/v1/figma/selections/uploads",
            json={"version": 1, "idempotency_key": "k"},
            headers={"X-Figma-Plugin-Token": "test-plugin-token"},
        ).status_code
        == 201
    )


def test_gateway_secret_blocks_raw_api_requests_before_endpoint_logic(tmp_path: Path) -> None:
    token = b"g" * 32
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_secret=b"p" * 32,
            gateway_secret=token,
            public_origin="https://fgui.corp.example",
            allow_fixture_jobs=True,
        )
    )
    requests = (
        ("POST", "/v1/agents/register"),
        ("POST", "/v1/projects/bind"),
        ("POST", "/v1/jobs/missing/approve"),
        ("GET", "/v1/agents/agent-1/assignments/next"),
        ("GET", "/v1/agents/agent-1/assignments/missing/artifact"),
        ("POST", "/v1/jobs/missing/apply-result"),
        ("POST", "/v1/figma/pairings"),
        ("POST", "/v1/figma/selections/uploads"),
    )

    for method, path in requests:
        assert client.request(method, path).status_code == 401
        assert client.request(
            method, path, headers={"X-Figma-Gateway-Token": "wrong"}
        ).status_code == 401

    headers = {"X-Figma-Gateway-Token": token.decode("ascii")}
    assert client.post(
        "/v1/agents/register", headers=headers, json={"version": 1, "agent_id": "agent-1", "name": "Desk"}
    ).status_code == 200
    assert client.post("/v1/figma/pairings", headers=headers).status_code == 201
    assert client.get("/health").status_code == 200

    preflight = client.options(
        "/v1/figma/pairings", headers={
            "Origin": "https://fgui.corp.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://fgui.corp.example"


def test_plugin_access_bypasses_the_gateway_boundary_for_plugin_routes(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            gateway_secret=b"g" * 32,
        )
    )

    response = client.post(
        "/v1/figma/selections/uploads",
        json={"version": 1, "idempotency_key": "k"},
        headers={"X-Figma-Plugin-Token": "test-plugin-token"},
    )

    assert response.status_code == 201


def test_template_project_routes_use_approved_catalog_and_project_store(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    template = templates / "fgui-2024-web"
    package = template / "Starter"
    package.mkdir(parents=True)
    (template / "template.json").write_text(
        '{"template_id":"fgui-2024-web","fairygui_version":"2024.2",'
        '"target_platform":"web","display_name":"Web starter"}',
        "utf-8",
    )
    (package / "package.xml").write_text("<package id='starter'><resources/></package>", "utf-8")
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            templates_root=templates,
            plugin_access_token=b"test-plugin-token",
            gateway_secret=b"g" * 32,
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}

    options = client.get("/v1/figma/project-options", headers=headers)
    unknown = client.post(
        "/v1/projects/from-template",
        headers=headers,
        json={"version": 1, "template_id": "missing", "project_name": "Quiz"},
    )
    invalid_name = client.post(
        "/v1/projects/from-template",
        headers=headers,
        json={"version": 1, "template_id": "fgui-2024-web", "project_name": "../Quiz"},
    )
    created = client.post(
        "/v1/projects/from-template",
        headers=headers,
        json={"version": 1, "template_id": "fgui-2024-web", "project_name": "Quiz"},
    )

    assert options.json() == {
        "version": 1,
        "options": [
            {
                "template_id": "fgui-2024-web",
                "fairygui_version": "2024.2",
                "target_platform": "web",
                "display_name": "Web starter",
            }
        ],
    }
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "template_not_found"
    assert invalid_name.status_code == 400
    assert invalid_name.json()["detail"]["code"] == "invalid_project_name"
    assert created.status_code == 201
    project = created.json()
    assert project["display_name"] == "Quiz"
    assert project["packages"] == [{"name": "Quiz", "resource_count": 1}]
    assert client.get(f"/v1/projects/{project['project_id']}", headers=headers).json() == project

@pytest.mark.parametrize("package_xml", ["<package><resources/></package>", "<package"])
def test_template_creation_maps_malformed_approved_project_trees_to_safe_errors(
    tmp_path: Path, package_xml: str
) -> None:
    template = tmp_path / "templates" / "fgui-2024-web"
    package = template / "Starter"
    package.mkdir(parents=True)
    (template / "template.json").write_text(
        '{"template_id":"fgui-2024-web","fairygui_version":"2024.2",'
        '"target_platform":"web","display_name":"Web starter"}',
        "utf-8",
    )
    (package / "package.xml").write_text(package_xml, "utf-8")
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            templates_root=tmp_path / "templates",
        )
    )

    response = client.post(
        "/v1/projects/from-template",
        json={"version": 1, "template_id": "fgui-2024-web", "project_name": "Quiz"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "invalid_fgui_project",
        "message": _upload_error("invalid_fgui_project").user_message,
    }


def _plugin_manifest() -> dict[str, object]:
    image = _image("red", "PNG", (1, 1))
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


def _plugin_project_zip(tmp_path: Path) -> Path:
    return write_project_zip(
        tmp_path / "GameUI.zip",
        {
            "Sample/package.xml": (
                b"<package id='pkg-sample'><resources>"
                b"<image id='img-background' name='Background.png' path='assets'/>"
                b"</resources></package>"
            ),
            "Sample/Panel/Main.xml": b"<component/>",
            "Sample/assets/Background.png": _image("blue", "PNG", (1, 1)),
        },
    )


def test_direct_plugin_policy_rejects_wrong_tokens_and_allows_every_task_one_route(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            gateway_secret=b"g" * 32,
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    wrong = {"X-Figma-Plugin-Token": "wrong"}
    image = _image("red", "PNG", (1, 1))

    assert client.post(
        "/v1/figma/selections/uploads",
        headers=wrong,
        json={"version": 1, "idempotency_key": "direct"},
    ).status_code == 401
    upload_id = client.post(
        "/v1/figma/selections/uploads",
        headers=headers,
        json={"version": 1, "idempotency_key": "direct"},
    ).json()["upload_id"]

    manifest_url = f"/v1/figma/selections/uploads/{upload_id}/manifest"
    assert client.put(manifest_url, headers=wrong, json=_plugin_manifest()).status_code == 401
    assert client.put(manifest_url, headers=headers, json=_plugin_manifest()).status_code == 200

    resource_url = f"/v1/figma/selections/uploads/{upload_id}/resources/hero"
    assert client.put(resource_url, headers={**wrong, "content-type": "image/png"}, content=image).status_code == 401
    assert client.put(resource_url, headers={**headers, "content-type": "image/png"}, content=image).status_code == 200

    commit_url = f"/v1/figma/selections/uploads/{upload_id}/commit"
    assert client.post(commit_url, headers=wrong).status_code == 401
    selection = client.post(commit_url, headers=headers)
    assert selection.status_code == 200
    selection_id = selection.json()["selection_id"]

    selection_url = f"/v1/figma/selections/{selection_id}"
    preview_url = f"{selection_url}/previews/0"
    assert client.get(selection_url, headers=wrong).status_code == 401
    assert client.get(selection_url, headers=headers).status_code == 200
    assert client.get(preview_url, headers=wrong).status_code == 401
    assert client.get(preview_url, headers=headers).status_code == 200

    project_zip = _plugin_project_zip(tmp_path)
    with project_zip.open("rb") as content:
        assert client.post(
            "/v1/projects/uploads",
            headers=wrong,
            files={"project": (project_zip.name, content, "application/zip")},
        ).status_code == 401
    with project_zip.open("rb") as content:
        project = client.post(
            "/v1/projects/uploads",
            headers=headers,
            files={"project": (project_zip.name, content, "application/zip")},
        )
    assert project.status_code == 201
    project_id = project.json()["project_id"]

    project_url = f"/v1/projects/{project_id}"
    packages_url = f"{project_url}/packages"
    thumbnail_url = f"{project_url}/assets/img-background/thumbnail"
    for url in (project_url, packages_url, thumbnail_url):
        assert client.get(url, headers=wrong).status_code == 401
        assert client.get(url, headers=headers).status_code == 200

    job_url = f"/v1/figma/selections/{selection_id}/projects/{project_id}/jobs"
    job_payload = {
        "version": 1,
        "selection_id": selection_id,
        "project_id": project_id,
        "package_name": "Sample",
    }
    assert client.post(job_url, headers=wrong, json=job_payload).status_code == 401
    job = client.post(job_url, headers=headers, json=job_payload)
    assert job.status_code == 200
    status_url = f"/v1/jobs/{job.json()['job_id']}"
    assert client.get(status_url, headers=wrong).status_code == 401
    assert client.get(status_url, headers=headers).status_code == 200


def test_direct_token_mode_preserves_authenticated_console_job_status(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    JobStore(data_dir / "server.db").initialize()
    JobStore(data_dir / "server.db").create_job(
        JobView(job_id="console-job", project_id="project-1", status=JobStatus.CREATED)
    )
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            gateway_secret=b"g" * 32,
        )
    )

    response = client.get(
        "/v1/jobs/console-job", headers={"X-Figma-Gateway-Token": "g" * 32}
    )

    assert response.status_code == 200


def test_direct_token_mode_preserves_console_session_selection_job_creation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    gateway_headers = {"X-Figma-Gateway-Token": "g" * 32}
    plugin_headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_secret=b"p" * 32,
            plugin_access_token=b"test-plugin-token",
            gateway_secret=b"g" * 32,
        )
    )
    pairing = client.post("/v1/figma/pairings", headers=gateway_headers).json()
    exchanged = client.post(
        "/v1/figma/pairings/exchange",
        headers=gateway_headers,
        json={"version": 1, "code": pairing["code"], "device_name": "Figma desktop"},
    ).json()
    device_id = exchanged["device"]["device_id"]
    selection_store = SelectionStore(data_dir)
    upload = selection_store.create_upload(device_id, "console-selection")
    manifest = SelectionManifest.model_validate(_plugin_manifest())
    selection_store.put_manifest(upload.upload_id, device_id, manifest)
    selection_store.put_resource(
        upload.upload_id, device_id, "hero", "image/png", _image("red", "PNG", (1, 1))
    )
    selection = selection_store.commit(upload.upload_id, device_id)
    project_zip = _plugin_project_zip(tmp_path)
    with project_zip.open("rb") as content:
        project = client.post(
            "/v1/projects/uploads",
            headers=plugin_headers,
            files={"project": (project_zip.name, content, "application/zip")},
        ).json()
    job_url = f"/v1/figma/selections/{selection.selection_id}/projects/{project['project_id']}/jobs"

    response = client.post(
        job_url,
        headers={**gateway_headers, "X-Figma-Console-Session": pairing["console_credential"]},
        json={
            "version": 1,
            "selection_id": selection.selection_id,
            "project_id": project["project_id"],
            "package_name": "Sample",
        },
    )

    assert response.status_code == 200, response.text


def test_gateway_still_protects_legacy_pairing_routes(tmp_path: Path) -> None:
    gateway_token = b"g" * 32
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_secret=b"p" * 32,
            gateway_secret=gateway_token,
        )
    )
    gateway_headers = {"X-Figma-Gateway-Token": gateway_token.decode("ascii")}
    code = client.post("/v1/figma/pairings", headers=gateway_headers).json()["code"]
    credential = client.post(
        "/v1/figma/pairings/exchange",
        headers=gateway_headers,
        json={"version": 1, "code": code, "device_name": "Figma desktop"},
    ).json()["credential"]
    request_headers = {"authorization": f"Bearer {credential}"}

    assert client.post(
        "/v1/figma/selections/uploads",
        headers=request_headers,
        json={"version": 1, "idempotency_key": "k"},
    ).status_code == 401
    assert client.post(
        "/v1/figma/selections/uploads",
        headers={**gateway_headers, **request_headers},
        json={"version": 1, "idempotency_key": "k"},
    ).status_code == 201


def test_gateway_secret_leaves_health_and_plugin_shell_accessible(tmp_path: Path) -> None:
    web_dist = tmp_path / "web-dist"
    (web_dist / "assets").mkdir(parents=True)
    (web_dist / "index.html").write_text("<div id='root'></div>", "utf-8")
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            web_dist=web_dist,
            gateway_secret=b"g" * 32,
        )
    )

    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/figma-plugin").status_code == 200


def test_web_console_html_is_not_cached_across_local_app_updates(tmp_path: Path) -> None:
    web_dist = tmp_path / "web-dist"
    (web_dist / "assets").mkdir(parents=True)
    (web_dist / "index.html").write_text("<div id='root'></div>", "utf-8")
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            web_dist=web_dist,
        )
    )

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def _image(color: str, image_format: str, size: tuple[int, int] = (2, 2)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, image_format)
    return output.getvalue()


def test_designer_image_routes_only_serve_declared_image_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixtures = tmp_path / "fixtures"
    before = _image("red", "TIFF", (1200, 700))
    after = _image("blue", "BMP", (900, 800))
    image_path = fixtures / "fgui" / "Sample" / "assets" / "Hero.tiff"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(before)
    app = create_app(
        data_dir=tmp_path / "data",
        fixtures_root=fixtures,
        rules_path=Path("rules/default/classification.yaml"),
    )
    bundle = ChangeBundle(
        job_id="image-job",
        project_id="project-1",
        files=(
            ChangeFile(
                operation=FileOperation.REPLACE,
                relative_path="Sample/assets/Hero.tiff",
                before_sha256=hashlib.sha256(before).hexdigest(),
                after_sha256=hashlib.sha256(after).hexdigest(),
                content_b64=base64.b64encode(after).decode("ascii"),
            ),
            ChangeFile(
                operation=FileOperation.REPLACE,
                relative_path="Sample/Panel/Main.xml",
                before_sha256="a" * 64,
                after_sha256="b" * 64,
                content_b64=base64.b64encode(b"<component/>").decode("ascii"),
            ),
        ),
    )
    artifact_sha256 = ArtifactStore(tmp_path / "data" / "artifacts").put(bundle)
    JobStore(tmp_path / "data" / "server.db").create_job(
        JobView(
            job_id=bundle.job_id,
            project_id=bundle.project_id,
            status=JobStatus.READY_FOR_REVIEW,
            artifact_sha256=artifact_sha256,
        )
    )
    browser = TestClient(app)

    preview = browser.get("/v1/jobs/image-job/designer-preview")
    assert preview.status_code == 200
    change = preview.json()["changes"][0]
    assert change["before_image_url"] == "/v1/jobs/image-job/designer-preview/images/0/before"
    assert change["after_image_url"] == "/v1/jobs/image-job/designer-preview/images/0/after"
    assert "Sample/assets" not in preview.text

    before_response = browser.get(change["before_image_url"])
    after_response = browser.get(change["after_image_url"])
    assert before_response.status_code == after_response.status_code == 200
    assert before_response.headers["content-type"] == after_response.headers["content-type"] == "image/webp"
    for response in (before_response, after_response):
        with Image.open(BytesIO(response.content)) as preview_image:
            assert preview_image.format == "WEBP"
            assert max(preview_image.size) <= 512
    assert before_response.content != before
    assert after_response.content != after
    assert browser.get("/v1/jobs/image-job/designer-preview/images/1/after").status_code == 404
    assert browser.get("/v1/jobs/image-job/designer-preview/images/9/before").status_code == 404

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 3)
    unavailable = browser.get("/v1/jobs/image-job/designer-preview").json()["changes"][0]
    assert unavailable["before_image_url"] is None
    assert unavailable["after_image_url"] is None
    assert browser.get("/v1/jobs/image-job/designer-preview/images/0/before").status_code == 404
    assert browser.get("/v1/jobs/image-job/designer-preview/images/0/after").status_code == 404


class _ScreenshotRecommendingAnalyzer:
    def __init__(self) -> None:
        self.screenshots: list[bytes | None] = []

    def analyze(
        self,
        roots: tuple[object, ...],
        *,
        rule_candidates: tuple[ClassificationDecision, ...],
        screenshot: bytes | None = None,
    ) -> SemanticAnalysisOutcome:
        assert roots
        assert rule_candidates
        self.screenshots.append(screenshot)
        return SemanticAnalysisOutcome(
            screenshot_recommended=screenshot is None,
            screenshot_reason=(
                "Visual hierarchy needs confirmation from the current selection."
                if screenshot is None
                else None
            ),
        )


class _ScreenshotChangingAnalyzer(_ScreenshotRecommendingAnalyzer):
    def analyze(
        self,
        roots: tuple[object, ...],
        *,
        rule_candidates: tuple[ClassificationDecision, ...],
        screenshot: bytes | None = None,
    ) -> SemanticAnalysisOutcome:
        outcome = super().analyze(
            roots, rule_candidates=rule_candidates, screenshot=screenshot
        )
        if screenshot is None:
            return outcome
        assert isinstance(roots[0], NormalizedNode)
        candidate = next(
            decision
            for decision in rule_candidates
            if decision.output_type == "PANEL"
        )
        return SemanticAnalysisOutcome(
            overrides=(
                ClassificationDecision(
                    node_id=candidate.node_id,
                    output_type=candidate.output_type,
                    rule_id="ai.semantic.v1",
                    rule_version=1,
                    evidence=("screenshot-confirmed hierarchy",),
                    confidence=0.99,
                    source=DecisionSource.AI,
                    semantic_name="ScreenshotPanel",
                ),
            )
        )


class _BlankScreenshotReasonAnalyzer:
    def analyze(
        self,
        roots: tuple[object, ...],
        *,
        rule_candidates: tuple[ClassificationDecision, ...],
        screenshot: bytes | None = None,
    ) -> SemanticAnalysisOutcome:
        assert roots
        return SemanticAnalysisOutcome(
            screenshot_recommended=screenshot is None,
            screenshot_reason="   " if screenshot is None else None,
        )


class _RacingScreenshotAnalyzer(_ScreenshotRecommendingAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.slow_started = Event()
        self.release_slow = Event()
        self._lock = Lock()
        self._screenshot_calls = 0

    def analyze(
        self,
        roots: tuple[object, ...],
        *,
        rule_candidates: tuple[ClassificationDecision, ...],
        screenshot: bytes | None = None,
    ) -> SemanticAnalysisOutcome:
        if screenshot is None:
            return super().analyze(
                roots, rule_candidates=rule_candidates, screenshot=screenshot
            )
        with self._lock:
            self._screenshot_calls += 1
            call = self._screenshot_calls
        if call == 1:
            self.slow_started.set()
            assert self.release_slow.wait(timeout=10)
            semantic_name = "SlowCandidate"
        else:
            semantic_name = "FastCandidate"
        assert isinstance(roots[0], NormalizedNode)
        return SemanticAnalysisOutcome(
            overrides=(
                ClassificationDecision(
                    node_id=roots[0].id,
                    output_type="PANEL",
                    rule_id="ai.semantic.v1",
                    rule_version=1,
                    evidence=("racing screenshot conversion",),
                    confidence=0.99,
                    source=DecisionSource.AI,
                    semantic_name=semantic_name,
                ),
            )
        )


class _SuccessFallbackRaceAnalyzer(_ScreenshotRecommendingAnalyzer):
    def __init__(self, fallback_wins: bool) -> None:
        super().__init__()
        self.fallback_wins = fallback_wins
        self.slow_started = Event()
        self.release_slow = Event()
        self._lock = Lock()
        self._screenshot_calls = 0

    def analyze(
        self,
        roots: tuple[object, ...],
        *,
        rule_candidates: tuple[ClassificationDecision, ...],
        screenshot: bytes | None = None,
    ) -> SemanticAnalysisOutcome:
        if screenshot is None:
            return super().analyze(
                roots, rule_candidates=rule_candidates, screenshot=screenshot
            )
        with self._lock:
            self._screenshot_calls += 1
            call = self._screenshot_calls
        if call == 1:
            self.slow_started.set()
            assert self.release_slow.wait(timeout=10)
        assert isinstance(roots[0], NormalizedNode)
        return SemanticAnalysisOutcome(
            overrides=(
                ClassificationDecision(
                    node_id=roots[0].id,
                    output_type="PANEL",
                    rule_id="ai.semantic.v1",
                    rule_version=1,
                    evidence=("completion CAS race",),
                    confidence=0.99,
                    source=DecisionSource.AI,
                    semantic_name="SlowCompletion" if call == 1 else "FastCompletion",
                ),
            )
        )


def _create_semantic_package_job(
    client: TestClient, tmp_path: Path, idempotency_key: str
) -> str:
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    upload = client.post(
        "/v1/figma/selections/uploads",
        headers=headers,
        json={"version": 1, "idempotency_key": idempotency_key},
    ).json()
    manifest = _plugin_manifest()
    assert client.put(
        f"/v1/figma/selections/uploads/{upload['upload_id']}/manifest",
        headers=headers,
        json=manifest,
    ).status_code == 200
    image = _image("red", "PNG", (1, 1))
    assert client.put(
        f"/v1/figma/selections/uploads/{upload['upload_id']}/resources/hero",
        headers={**headers, "content-type": "image/png"},
        content=image,
    ).status_code == 200
    selection_id = client.post(
        f"/v1/figma/selections/uploads/{upload['upload_id']}/commit", headers=headers
    ).json()["selection_id"]
    project_zip = _plugin_project_zip(tmp_path)
    with project_zip.open("rb") as source:
        project = client.post(
            "/v1/projects/uploads",
            headers=headers,
            files={"project": (project_zip.name, source, "application/zip")},
        ).json()
    created = client.post(
        f"/v1/figma/selections/{selection_id}/projects/{project['project_id']}/jobs",
        headers=headers,
        json={
            "version": 1,
            "selection_id": selection_id,
            "project_id": project["project_id"],
            "package_name": "Sample",
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job_id"]
    started = client.post(
        f"/v1/jobs/{job_id}/package",
        headers=headers,
        json={"version": 1, "mode": "update", "project_name": "Sample"},
    )
    assert started.status_code == 202, started.text
    return str(job_id)


def test_blank_screenshot_reason_does_not_pause_packaging(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=_BlankScreenshotReasonAnalyzer(),
        )
    )

    job_id = _create_semantic_package_job(client, tmp_path, "blank-reason")
    package = client.get(
        f"/v1/jobs/{job_id}/package",
        headers={"X-Figma-Plugin-Token": "test-plugin-token"},
    )

    assert package.status_code == 200
    assert package.json()["stage"] == "ready"
    assert package.json()["screenshot_reason"] is None


def test_failed_screenshot_generation_retry_decline_uses_baseline_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=_ScreenshotChangingAnalyzer(),
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    job_id = _create_semantic_package_job(client, tmp_path, "baseline-retry")
    job_store = JobStore(data_dir / "server.db")
    baseline_digest = job_store.get_job(job_id).artifact_sha256
    assert baseline_digest is not None
    baseline = ArtifactStore(data_dir / "artifacts").get(baseline_digest)
    observed: list[ChangeBundle] = []
    original_build = api.build_project_package

    def fail_first_build(
        project_root: Path,
        bundle: ChangeBundle,
        mode: object,
        project_name: str,
        output_directory: Path,
    ) -> object:
        observed.append(bundle)
        if len(observed) == 1:
            raise OSError("first generation failed")
        return original_build(
            project_root,
            bundle,
            mode,  # type: ignore[arg-type]
            project_name,
            output_directory,
        )

    monkeypatch.setattr(api, "build_project_package", fail_first_build)
    screenshot = _image("blue", "PNG", (2, 2))
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    ).status_code == 202
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=screenshot,
    ).status_code == 202
    assert client.get(f"/v1/jobs/{job_id}/package", headers=headers).json()[
        "stage"
    ] == "failed"
    restarted = client.post(
        f"/v1/jobs/{job_id}/package",
        headers=headers,
        json={"version": 1, "mode": "update", "project_name": "Sample"},
    )
    assert restarted.json()["stage"] == "awaiting_screenshot_consent"
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": False},
    ).status_code == 202

    assert observed[1] == baseline
    assert job_store.get_job(job_id).artifact_sha256 == baseline_digest


def test_concurrent_identical_uploads_run_one_screenshot_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    analyzer = _RacingScreenshotAnalyzer()
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=analyzer,
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    job_id = _create_semantic_package_job(client, tmp_path, "slow-candidate")
    store = JobStore(data_dir / "server.db")
    baseline_digest = store.get_job(job_id).artifact_sha256
    assert baseline_digest is not None
    observed_builds: list[ChangeBundle] = []
    original_build = api.build_project_package

    def record_build(
        project_root: Path,
        bundle: ChangeBundle,
        mode: object,
        project_name: str,
        output_directory: Path,
    ) -> object:
        observed_builds.append(bundle)
        return original_build(
            project_root,
            bundle,
            mode,  # type: ignore[arg-type]
            project_name,
            output_directory,
        )

    monkeypatch.setattr(api, "build_project_package", record_build)
    screenshot = _image("blue", "PNG", (2, 2))
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    ).status_code == 202

    def upload() -> Response:
        return client.post(
            f"/v1/jobs/{job_id}/semantic-screenshot",
            headers={**headers, "content-type": "image/png"},
            content=screenshot,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        slow = executor.submit(upload)
        assert analyzer.slow_started.wait(timeout=10)
        try:
            duplicate = upload()
            assert duplicate.status_code == 202, duplicate.text
            assert duplicate.json()["stage"] == "awaiting_screenshot_consent"
            assert client.get(f"/v1/jobs/{job_id}/package", headers=headers).json()[
                "stage"
            ] == "awaiting_screenshot_consent"
            assert analyzer._screenshot_calls == 1
        finally:
            analyzer.release_slow.set()
        slow_response = slow.result(timeout=10)

    assert slow_response.status_code == 202, slow_response.text
    assert store.get_job(job_id).artifact_sha256 == baseline_digest
    package = store.get_package(job_id)
    assert package.screenshot_candidate is not None
    assert package.screenshot_candidate.artifact_sha256 is not None
    candidate = ArtifactStore(data_dir / "artifacts").get(
        package.screenshot_candidate.artifact_sha256
    )
    assert observed_builds == [candidate]
    assert analyzer._screenshot_calls == 1


@pytest.mark.parametrize("fallback_wins", [False, True])
def test_single_claimed_upload_commits_success_or_fallback_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fallback_wins: bool,
) -> None:
    data_dir = tmp_path / "data"
    analyzer = _SuccessFallbackRaceAnalyzer(fallback_wins)
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=analyzer,
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    job_id = _create_semantic_package_job(
        client, tmp_path, f"completion-race-{fallback_wins}"
    )
    store = JobStore(data_dir / "server.db")
    baseline_digest = store.get_job(job_id).artifact_sha256
    assert baseline_digest is not None
    baseline = ArtifactStore(data_dir / "artifacts").get(baseline_digest)
    observed_builds: list[ChangeBundle] = []
    original_build = api.build_project_package

    def record_build(
        project_root: Path,
        bundle: ChangeBundle,
        mode: object,
        project_name: str,
        output_directory: Path,
    ) -> object:
        observed_builds.append(bundle)
        return original_build(
            project_root,
            bundle,
            mode,  # type: ignore[arg-type]
            project_name,
            output_directory,
        )

    monkeypatch.setattr(api, "build_project_package", record_build)
    original_put = ArtifactStore.put
    put_lock = Lock()
    put_calls = 0

    def racing_put(artifact_store: ArtifactStore, bundle: ChangeBundle) -> str:
        nonlocal put_calls
        with put_lock:
            put_calls += 1
            call = put_calls
        should_fail = (call == 1) if fallback_wins else (call == 2)
        if should_fail:
            raise OSError("artifact publish failed")
        return original_put(artifact_store, bundle)

    monkeypatch.setattr(ArtifactStore, "put", racing_put)
    screenshot = _image("blue", "PNG", (2, 2))
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    ).status_code == 202

    def upload() -> Response:
        return client.post(
            f"/v1/jobs/{job_id}/semantic-screenshot",
            headers={**headers, "content-type": "image/png"},
            content=screenshot,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        slow = executor.submit(upload)
        assert analyzer.slow_started.wait(timeout=10)
        assert store.get_package(job_id).view.stage is ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
        fast = upload()
        assert fast.status_code == 202, fast.text
        analyzer.release_slow.set()
        slow_response = slow.result(timeout=10)

    assert slow_response.status_code == 202, slow_response.text
    assert analyzer._screenshot_calls == 1
    assert put_calls == 1
    package = store.get_package(job_id)
    assert package.view.stage is ProjectPackageStage.READY
    assert package.screenshot_completed is True
    assert (package.screenshot_candidate is None) is fallback_wins
    if fallback_wins:
        expected = baseline
        assert "semantic.screenshot_fallback" in {
            diagnostic.code for diagnostic in package.view.diagnostics
        }
    else:
        assert package.screenshot_candidate is not None
        candidate_digest = package.screenshot_candidate.artifact_sha256
        assert candidate_digest is not None
        expected = ArtifactStore(data_dir / "artifacts").get(candidate_digest)
    assert observed_builds == [expected]


def test_screenshot_consent_upload_and_decline_resume_package_without_leaks(
    tmp_path: Path,
) -> None:
    analyzer = _ScreenshotRecommendingAnalyzer()
    data_dir = tmp_path / "data"
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=analyzer,
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    job_id = _create_semantic_package_job(client, tmp_path, "semantic-upload")
    view = client.get(f"/v1/jobs/{job_id}/package", headers=headers)

    assert view.status_code == 200
    assert view.json()["stage"] == "awaiting_screenshot_consent"
    assert view.json()["screenshot_reason"]
    assert "screenshot_digest" not in view.json()
    assert "screenshot_path" not in view.json()
    assert "screenshot_consent" not in view.json()
    assert analyzer.screenshots == [None]
    invalid_type = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "text/plain"},
        content=b"bad",
    )
    assert invalid_type.status_code == 415
    screenshot = _image("blue", "PNG", (2, 2))
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=screenshot,
    ).status_code == 409
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "image/png; charset=binary"},
        content=screenshot,
    ).status_code == 415
    approved = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    )
    duplicate = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    )
    assert approved.status_code == duplicate.status_code == 202
    assert approved.json() == duplicate.json()
    assert approved.json()["stage"] == "awaiting_screenshot_consent"
    assert analyzer.screenshots == [None]
    bad_magic = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=b"not-a-png",
    )
    assert bad_magic.status_code == 415
    uploaded = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=screenshot,
    )
    assert uploaded.status_code == 202, uploaded.text
    assert uploaded.json()["stage"] == "packaging"
    uploaded_view = client.get(f"/v1/jobs/{job_id}/package", headers=headers).json()
    assert uploaded_view["stage"] == "ready"
    assert analyzer.screenshots == [None, screenshot]
    screenshot_root = data_dir / "semantic-screenshots"
    assert not screenshot_root.exists() or not list(screenshot_root.iterdir())
    duplicate_upload = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=screenshot,
    )
    conflicting_upload = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=_image("green", "PNG", (2, 2)),
    )
    conflicting_consent = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": False},
    )
    assert duplicate_upload.status_code == 202
    assert duplicate_upload.json()["stage"] == "ready"
    assert conflicting_upload.status_code == 409
    assert conflicting_consent.status_code == 409
    assert not list(screenshot_root.iterdir())

    declined_job = _create_semantic_package_job(client, tmp_path, "semantic-decline")
    declined = client.post(
        f"/v1/jobs/{declined_job}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": False},
    )
    repeated = client.post(
        f"/v1/jobs/{declined_job}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": False},
    )
    conflict = client.post(
        f"/v1/jobs/{declined_job}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    )
    assert declined.status_code == repeated.status_code == 202
    assert declined.json()["stage"] == "packaging"
    declined_view = client.get(
        f"/v1/jobs/{declined_job}/package", headers=headers
    ).json()
    assert repeated.json() == declined_view
    assert declined_view["stage"] == "ready"
    assert "semantic.screenshot_declined" in {
        item["code"] for item in declined_view["diagnostics"]
    }
    assert conflict.status_code == 409


def test_approved_screenshot_consent_can_fallback_before_upload(tmp_path: Path) -> None:
    analyzer = _ScreenshotRecommendingAnalyzer()
    data_dir = tmp_path / "data"
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=analyzer,
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    job_id = _create_semantic_package_job(client, tmp_path, "semantic-fallback")

    approved = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    )
    fallback = client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": False},
    )
    finished = client.get(f"/v1/jobs/{job_id}/package", headers=headers)

    assert approved.status_code == fallback.status_code == 202
    assert approved.json()["stage"] == "awaiting_screenshot_consent"
    assert fallback.json()["stage"] == "packaging"
    assert finished.status_code == 200
    assert finished.json()["stage"] == "ready"
    assert analyzer.screenshots == [None]
    assert "semantic.screenshot_declined" in {
        item["code"] for item in finished.json()["diagnostics"]
    }
    screenshot_root = data_dir / "semantic-screenshots"
    assert not screenshot_root.exists() or not list(screenshot_root.iterdir())


def test_semantic_screenshot_routes_preserve_plugin_auth_and_size_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyzer = _ScreenshotRecommendingAnalyzer()
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=analyzer,
        )
    )
    job_id = _create_semantic_package_job(client, tmp_path, "semantic-auth")
    url = f"/v1/jobs/{job_id}/semantic-screenshot"
    monkeypatch.setattr("figma_to_fgui.api.MAX_SEMANTIC_SCREENSHOT_BYTES", 8)

    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        json={"version": 1, "approved": True},
    ).status_code == 401
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers={"X-Figma-Plugin-Token": "test-plugin-token"},
        json={"version": 1, "approved": True},
    ).status_code == 202
    oversized = client.post(
        url,
        headers={
            "X-Figma-Plugin-Token": "test-plugin-token",
            "content-type": "image/png",
        },
        content=b"x" * 9,
    )
    assert oversized.status_code == 413
    invalid_length = client.post(
        url,
        headers={
            "X-Figma-Plugin-Token": "test-plugin-token",
            "content-type": "image/png",
            "content-length": "-1",
        },
        content=b"x",
    )
    assert invalid_length.status_code == 400


def test_semantic_screenshot_routes_hide_another_devices_job(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    gateway_headers = {"X-Figma-Gateway-Token": "g" * 32}
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_secret=b"p" * 32,
            gateway_secret=b"g" * 32,
        )
    )
    pairings: list[tuple[str, str]] = []
    for name in ("Owner", "Other"):
        pairing = client.post("/v1/figma/pairings", headers=gateway_headers).json()
        exchanged = client.post(
            "/v1/figma/pairings/exchange",
            headers=gateway_headers,
            json={"version": 1, "code": pairing["code"], "device_name": name},
        ).json()
        pairings.append((pairing["console_credential"], exchanged["device"]["device_id"]))
    owner_session, owner_device = pairings[0]
    other_session, _ = pairings[1]
    selections = SelectionStore(data_dir)
    upload = selections.create_upload(owner_device, "owned-semantic")
    selections.put_manifest(
        upload.upload_id, owner_device, SelectionManifest.model_validate(_plugin_manifest())
    )
    selections.put_resource(
        upload.upload_id,
        owner_device,
        "hero",
        "image/png",
        _image("red", "PNG", (1, 1)),
    )
    selection = selections.commit(upload.upload_id, owner_device)
    store = JobStore(data_dir / "server.db")
    store.create_job(
        JobView(job_id="owned-semantic", project_id="project", status=JobStatus.READY_FOR_REVIEW),
        selection.selection_id,
        selection.fingerprint,
    )
    checking = ProjectPackageView(
        job_id="owned-semantic",
        status=ProjectPackageStage.CHECKING,
        stage=ProjectPackageStage.CHECKING,
        progress=70,
    )
    attempt = store.begin_package("owned-semantic", "request", "instance", checking)
    waiting = checking.model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    store.await_screenshot_consent(
        "owned-semantic", "request", attempt.generation, "instance", waiting
    )
    owner_headers = {
        **gateway_headers,
        "X-Figma-Console-Session": owner_session,
    }
    other_headers = {
        **gateway_headers,
        "X-Figma-Console-Session": other_session,
    }

    assert client.post(
        "/v1/jobs/owned-semantic/semantic-screenshot-consent",
        headers=other_headers,
        json={"version": 1, "approved": True},
    ).status_code == 404
    assert client.post(
        "/v1/jobs/owned-semantic/semantic-screenshot",
        headers={**other_headers, "content-type": "image/png"},
        content=_image("blue", "PNG", (1, 1)),
    ).status_code == 404
    assert client.post(
        "/v1/jobs/owned-semantic/semantic-screenshot-consent",
        headers=owner_headers,
        json={"version": 1, "approved": True},
    ).status_code == 202


def test_waiting_screenshot_jobs_resume_decline_and_upload_after_restart(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    first_analyzer = _ScreenshotRecommendingAnalyzer()
    first = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=first_analyzer,
            package_owner_id="before-restart",
        )
    )
    declined_job = _create_semantic_package_job(first, tmp_path, "restart-decline")
    uploaded_job = _create_semantic_package_job(first, tmp_path, "restart-upload")

    restarted_analyzer = _ScreenshotRecommendingAnalyzer()
    restarted = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=restarted_analyzer,
            package_owner_id="after-restart",
        )
    )
    package_payload = {
        "version": 1,
        "mode": "update",
        "project_name": "Sample",
    }
    repeated = restarted.post(
        f"/v1/jobs/{declined_job}/package",
        headers=headers,
        json=package_payload,
    )
    assert repeated.status_code == 202
    assert repeated.json()["stage"] == "awaiting_screenshot_consent"
    declined = restarted.post(
        f"/v1/jobs/{declined_job}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": False},
    )
    assert declined.status_code == 202, declined.text
    assert restarted.get(
        f"/v1/jobs/{declined_job}/package", headers=headers
    ).json()["stage"] == "ready"

    assert restarted.post(
        f"/v1/jobs/{uploaded_job}/package",
        headers=headers,
        json=package_payload,
    ).json()["stage"] == "awaiting_screenshot_consent"
    assert restarted.post(
        f"/v1/jobs/{uploaded_job}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    ).status_code == 202
    screenshot = _image("blue", "PNG", (2, 2))
    uploaded = restarted.post(
        f"/v1/jobs/{uploaded_job}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=screenshot,
    )
    assert uploaded.status_code == 202, uploaded.text
    assert restarted.get(
        f"/v1/jobs/{uploaded_job}/package", headers=headers
    ).json()["stage"] == "ready"
    assert restarted_analyzer.screenshots == [screenshot]


def test_restart_can_cancel_an_attached_claim_without_waiting_for_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    first = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=_ScreenshotRecommendingAnalyzer(),
            package_owner_id="before-crash",
        )
    )
    job_id = _create_semantic_package_job(first, tmp_path, "cancel-claimed-restart")
    store = JobStore(data_dir / "server.db")
    baseline_digest = store.get_job(job_id).artifact_sha256
    assert baseline_digest is not None
    package = store.get_package(job_id)
    store.record_screenshot_consent(job_id, package.generation, True)
    screenshot = _image("blue", "PNG", (2, 2))
    digest = hashlib.sha256(screenshot).hexdigest()
    screenshot_path = api._write_semantic_screenshot(
        data_dir / "semantic-screenshots",
        job_id,
        package.generation,
        ".png",
        screenshot,
    )
    store.attach_screenshot(job_id, package.generation, digest, screenshot_path)
    claim = store.claim_screenshot_analysis(
        job_id, package.generation, digest, "crashed-analysis"
    )
    assert claim.claimed is True
    original_attach = JobStore.attach_and_claim_screenshot_analysis
    before_late_attach = Event()
    release_late_attach = Event()

    def delayed_late_attach(
        current_store: JobStore,
        current_job_id: str,
        generation: int,
        current_digest: str,
        path: Path,
        owner_id: str,
    ) -> object:
        if current_job_id == job_id:
            before_late_attach.set()
            assert release_late_attach.wait(timeout=10)
        return original_attach(
            current_store,
            current_job_id,
            generation,
            current_digest,
            path,
            owner_id,
        )

    monkeypatch.setattr(
        JobStore, "attach_and_claim_screenshot_analysis", delayed_late_attach
    )

    restarted = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=_ScreenshotRecommendingAnalyzer(),
            package_owner_id="after-crash",
        )
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        late_retry = executor.submit(
            restarted.post,
            f"/v1/jobs/{job_id}/semantic-screenshot",
            headers={**headers, "content-type": "image/png"},
            content=screenshot,
        )
        assert before_late_attach.wait(timeout=10)
        cancelled = restarted.post(
            f"/v1/jobs/{job_id}/semantic-screenshot-consent",
            headers=headers,
            json={"version": 1, "approved": False},
        )
        release_late_attach.set()
        late_retry_response = late_retry.result(timeout=10)
    monkeypatch.setattr(
        JobStore, "attach_and_claim_screenshot_analysis", original_attach
    )

    assert cancelled.status_code == 202, cancelled.text
    assert late_retry_response.status_code == 409, late_retry_response.text
    assert late_retry_response.json()["detail"]["code"] == "screenshot_consent_required"
    assert cancelled.json()["stage"] == "packaging"
    assert restarted.get(f"/v1/jobs/{job_id}/package", headers=headers).json()[
        "stage"
    ] == "ready"
    cancelled_retry = restarted.post(
        f"/v1/jobs/{job_id}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=screenshot,
    )
    assert cancelled_retry.status_code == 409
    assert cancelled_retry.json()["detail"]["code"] == "screenshot_consent_required"
    recovered_store = JobStore(data_dir / "server.db")
    assert recovered_store.get_job(job_id).artifact_sha256 == baseline_digest
    assert not screenshot_path.exists()
    current = recovered_store.get_package(job_id)
    assert current.screenshot_analysis_owner_id is None
    assert current.screenshot_analysis_lease_expires_at is None
    late = recovered_store.complete_screenshot_conversion(
        job_id,
        package.generation,
        digest,
        recovered_store.get_job(job_id),
        claim_owner_id="crashed-analysis",
    )
    assert late.committed is False


def _seed_completed_waiting_screenshot(
    client: TestClient,
    data_dir: Path,
    tmp_path: Path,
    idempotency_key: str,
) -> tuple[str, bytes]:
    job_id = _create_semantic_package_job(client, tmp_path, idempotency_key)
    store = JobStore(data_dir / "server.db")
    package = store.get_package(job_id)
    screenshot = _image("blue", "PNG", (2, 2))
    digest = hashlib.sha256(screenshot).hexdigest()
    store.record_screenshot_consent(job_id, package.generation, True)
    screenshot_root = data_dir / "semantic-screenshots"
    screenshot_root.mkdir()
    screenshot_path = screenshot_root / f"seed-{job_id}.png"
    screenshot_path.write_bytes(screenshot)
    store.attach_screenshot(job_id, package.generation, digest, screenshot_path)
    claim = store.claim_screenshot_analysis(
        job_id, package.generation, digest, "seed-completion"
    )
    assert claim.claimed is True
    completion = store.complete_screenshot_conversion(
        job_id,
        package.generation,
        digest,
        store.get_job(job_id),
        claim_owner_id="seed-completion",
    )
    assert completion.committed is True
    assert completion.package.view.stage is ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
    return job_id, screenshot


@pytest.mark.parametrize("recovery_route", ["upload", "consent", "package"])
def test_completed_waiting_screenshot_recovers_after_restart(
    tmp_path: Path, recovery_route: str
) -> None:
    data_dir = tmp_path / "data"
    first = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=_ScreenshotRecommendingAnalyzer(),
            package_owner_id="before-crash",
        )
    )
    job_id, screenshot = _seed_completed_waiting_screenshot(
        first, data_dir, tmp_path, f"crash-window-{recovery_route}"
    )
    restarted_analyzer = _ScreenshotRecommendingAnalyzer()
    restarted = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=restarted_analyzer,
            package_owner_id="after-crash",
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    if recovery_route == "upload":
        different = restarted.post(
            f"/v1/jobs/{job_id}/semantic-screenshot",
            headers={**headers, "content-type": "image/png"},
            content=_image("green", "PNG", (2, 2)),
        )
        assert different.status_code == 409
        assert JobStore(data_dir / "server.db").get_package(
            job_id
        ).view.stage is ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
        recovered = restarted.post(
            f"/v1/jobs/{job_id}/semantic-screenshot",
            headers={**headers, "content-type": "image/png"},
            content=screenshot,
        )
    elif recovery_route == "consent":
        recovered = restarted.post(
            f"/v1/jobs/{job_id}/semantic-screenshot-consent",
            headers=headers,
            json={"version": 1, "approved": True},
        )
    else:
        recovered = restarted.post(
            f"/v1/jobs/{job_id}/package",
            headers=headers,
            json={"version": 1, "mode": "update", "project_name": "Sample"},
        )

    assert recovered.status_code == 202, recovered.text
    assert restarted.get(f"/v1/jobs/{job_id}/package", headers=headers).json()[
        "stage"
    ] == "ready"
    assert restarted_analyzer.screenshots == []


def test_stale_generation_upload_cannot_resume_completed_new_generation(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    analyzer = _RacingScreenshotAnalyzer()
    client = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=analyzer,
            package_owner_id="api-owner",
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    job_id = _create_semantic_package_job(client, tmp_path, "stale-generation")
    screenshot = _image("blue", "PNG", (2, 2))
    digest = hashlib.sha256(screenshot).hexdigest()
    assert client.post(
        f"/v1/jobs/{job_id}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    ).status_code == 202

    def upload() -> Response:
        return client.post(
            f"/v1/jobs/{job_id}/semantic-screenshot",
            headers={**headers, "content-type": "image/png"},
            content=screenshot,
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_upload = executor.submit(upload)
        assert analyzer.slow_started.wait(timeout=10)
        store = JobStore(data_dir / "server.db")
        first = store.get_package(job_id)
        assert first.screenshot_analysis_owner_id is not None
        store.complete_screenshot_conversion(
            job_id,
            first.generation,
            digest,
            store.get_job(job_id),
            claim_owner_id=first.screenshot_analysis_owner_id,
        )
        packaging = first.view.model_copy(
            update={
                "status": ProjectPackageStage.PACKAGING,
                "stage": ProjectPackageStage.PACKAGING,
                "progress": 90,
                "screenshot_reason": None,
            }
        )
        store.resume_package_after_screenshot(
            job_id, first.generation, "manual-gen1", packaging
        )
        failed = packaging.model_copy(
            update={
                "status": ProjectPackageStage.FAILED,
                "stage": ProjectPackageStage.FAILED,
            }
        )
        store.transition_package(
            job_id,
            first.request_identity,
            first.generation,
            "manual-gen1",
            (ProjectPackageStage.PACKAGING,),
            failed,
        )
        checking = ProjectPackageView(
            job_id=job_id,
            status=ProjectPackageStage.CHECKING,
            stage=ProjectPackageStage.CHECKING,
            progress=70,
        )
        second = store.begin_package(
            job_id,
            first.request_identity,
            "manual-gen2",
            checking,
            first.request_payload,
        )
        waiting = checking.model_copy(
            update={
                "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                "screenshot_reason": "Need screenshot.",
            }
        )
        store.await_screenshot_consent(
            job_id,
            first.request_identity,
            second.generation,
            "manual-gen2",
            waiting,
        )
        store.record_screenshot_consent(job_id, second.generation, True)
        second_path = data_dir / "semantic-screenshots" / "second.png"
        second_path.write_bytes(screenshot)
        store.attach_screenshot(job_id, second.generation, digest, second_path)
        second_claim = store.claim_screenshot_analysis(
            job_id, second.generation, digest, "manual-gen2-analysis"
        )
        assert second_claim.claimed is True
        store.complete_screenshot_conversion(
            job_id,
            second.generation,
            digest,
            store.get_job(job_id),
            claim_owner_id="manual-gen2-analysis",
        )
        analyzer.release_slow.set()
        response = stale_upload.result(timeout=10)

    assert response.status_code == 202, response.text
    current = JobStore(data_dir / "server.db").get_package(job_id)
    assert current.generation == second.generation
    assert current.view.stage is ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT


def test_concurrent_identical_screenshot_decisions_are_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyzer = _ScreenshotRecommendingAnalyzer()
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-plugin-token",
            semantic_analyzer=analyzer,
        )
    )
    headers = {"X-Figma-Plugin-Token": "test-plugin-token"}
    declined_job = _create_semantic_package_job(client, tmp_path, "concurrent-decline")
    uploaded_job = _create_semantic_package_job(client, tmp_path, "concurrent-upload")
    assert client.post(
        f"/v1/jobs/{uploaded_job}/semantic-screenshot-consent",
        headers=headers,
        json={"version": 1, "approved": True},
    ).status_code == 202

    def decline(_: int) -> int:
        return client.post(
            f"/v1/jobs/{declined_job}/semantic-screenshot-consent",
            headers=headers,
            json={"version": 1, "approved": False},
        ).status_code

    screenshot = _image("blue", "PNG", (2, 2))
    upload_responses: list[tuple[int, str]] = []
    upload_response_lock = Lock()

    def upload(_: int) -> int:
        response = client.post(
            f"/v1/jobs/{uploaded_job}/semantic-screenshot",
            headers={**headers, "content-type": "image/png"},
            content=screenshot,
        )
        with upload_response_lock:
            upload_responses.append((response.status_code, response.text))
        return response.status_code

    original_consent = JobStore.record_screenshot_consent
    consent_barrier = Barrier(2)

    def synchronized_consent(
        store: JobStore, job_id: str, generation: int, approved: bool
    ) -> object:
        result = original_consent(store, job_id, generation, approved)
        if job_id == declined_job:
            consent_barrier.wait()
        return result

    monkeypatch.setattr(JobStore, "record_screenshot_consent", synchronized_consent)
    with ThreadPoolExecutor(max_workers=2) as executor:
        decline_statuses = list(executor.map(decline, range(2)))
    monkeypatch.setattr(JobStore, "record_screenshot_consent", original_consent)

    original_replace = api.os.replace
    upload_barrier = Barrier(2)
    screenshot_temporaries: list[Path] = []

    def synchronized_replace(source: object, destination: object) -> None:
        source_path = Path(source)  # type: ignore[arg-type]
        if source_path.parent.name == "semantic-screenshots":
            screenshot_temporaries.append(source_path)
            upload_barrier.wait()
        original_replace(source, destination)  # type: ignore[arg-type]

    monkeypatch.setattr(api.os, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        upload_statuses = list(executor.map(upload, range(2)))
    monkeypatch.setattr(api.os, "replace", original_replace)

    assert decline_statuses == [202, 202]
    assert upload_statuses == [202, 202], upload_responses
    assert len(set(screenshot_temporaries)) == 2
    assert analyzer.screenshots.count(screenshot) == 1
    assert client.get(
        f"/v1/jobs/{declined_job}/package", headers=headers
    ).json()["stage"] == "ready"
    assert client.get(
        f"/v1/jobs/{uploaded_job}/package", headers=headers
    ).json()["stage"] == "ready"
    accepted_retry = client.post(
        f"/v1/jobs/{uploaded_job}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=screenshot,
    )
    different_retry = client.post(
        f"/v1/jobs/{uploaded_job}/semantic-screenshot",
        headers={**headers, "content-type": "image/png"},
        content=_image("red", "PNG", (2, 2)),
    )
    assert accepted_retry.status_code == 202, accepted_retry.text
    assert accepted_retry.json()["stage"] == "ready"
    assert different_retry.status_code == 409


def test_semantic_screenshot_writer_cleans_every_partial_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "semantic-screenshots"
    monkeypatch.setattr(api.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError()))

    with pytest.raises(OSError):
        api._write_semantic_screenshot(root, "job", 1, ".png", b"png")

    assert root.is_dir()
    assert not list(root.iterdir())


def test_semantic_screenshot_writer_rejects_reparse_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "semantic-screenshots"
    root.mkdir()
    original_lstat = Path.lstat

    def reparse_lstat(path: Path) -> object:
        if path == root:
            metadata = original_lstat(path)
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=0x400,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    with pytest.raises(OSError):
        api._write_semantic_screenshot(root, "job", 1, ".png", b"png")

    assert not list(root.iterdir())


def test_startup_reconciles_terminal_screenshots_and_sweeps_controlled_orphans(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    database = data_dir / "server.db"
    store = JobStore(database)
    store.initialize()
    checking = ProjectPackageView(
        job_id="terminal-job",
        status=ProjectPackageStage.CHECKING,
        stage=ProjectPackageStage.CHECKING,
        progress=70,
    )
    store.create_job(
        JobView(
            job_id="terminal-job",
            project_id="project",
            status=JobStatus.READY_FOR_REVIEW,
            artifact_sha256="a" * 64,
        )
    )
    terminal_attempt = store.begin_package(
        "terminal-job", "request", "owner", checking
    )
    failed = checking.model_copy(
        update={
            "status": ProjectPackageStage.FAILED,
            "stage": ProjectPackageStage.FAILED,
        }
    )
    store.transition_package(
        "terminal-job",
        "request",
        terminal_attempt.generation,
        "owner",
        (ProjectPackageStage.CHECKING,),
        failed,
    )
    screenshot_root = data_dir / "semantic-screenshots"
    screenshot_root.mkdir()
    legacy_terminal = screenshot_root / "legacy-terminal.png"
    legacy_terminal.write_bytes(b"terminal")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE project_packages SET screenshot_path = ? WHERE job_id = ?",
            (str(legacy_terminal), "terminal-job"),
        )

    protected_name = f"{'1' * 64}-{'2' * 32}.png"
    protected = screenshot_root / protected_name
    protected.write_bytes(b"protected")
    waiting = checking.model_copy(
        update={
            "job_id": "waiting-job",
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    waiting_checking = checking.model_copy(update={"job_id": "waiting-job"})
    store.create_job(
        JobView(
            job_id="waiting-job",
            project_id="project",
            status=JobStatus.READY_FOR_REVIEW,
            artifact_sha256="b" * 64,
        )
    )
    waiting_attempt = store.begin_package(
        "waiting-job", "request", "owner", waiting_checking
    )
    store.await_screenshot_consent(
        "waiting-job",
        "request",
        waiting_attempt.generation,
        "owner",
        waiting,
    )
    store.record_screenshot_consent("waiting-job", waiting_attempt.generation, True)
    store.attach_screenshot(
        "waiting-job", waiting_attempt.generation, "c" * 64, protected
    )

    orphan = screenshot_root / f"{'3' * 64}-{'4' * 32}.webp"
    temporary = screenshot_root / f".{'5' * 64}-stale.tmp"
    unrelated = screenshot_root / "do-not-delete.png"
    expected_directory = screenshot_root / f"{'6' * 64}-{'7' * 32}.png"
    orphan.write_bytes(b"orphan")
    temporary.write_bytes(b"temporary")
    unrelated.write_bytes(b"unrelated")
    expected_directory.mkdir()

    create_app(
        data_dir=data_dir,
        fixtures_root=Path("tests/fixtures"),
        rules_path=Path("rules/default/classification.yaml"),
    )

    restarted_store = JobStore(database)
    assert not legacy_terminal.exists()
    assert restarted_store.get_package("terminal-job").screenshot_path is None
    assert protected.read_bytes() == b"protected"
    assert restarted_store.get_package("waiting-job").screenshot_path == protected
    assert not orphan.exists()
    assert not temporary.exists()
    assert unrelated.read_bytes() == b"unrelated"
    assert expected_directory.is_dir()


def test_orphan_sweep_ignores_missing_references_and_reparse_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "semantic-screenshots"
    root.mkdir()
    orphan = root / f"{'8' * 64}-{'9' * 32}.png"
    reparse_candidate = root / f"{'a' * 64}-{'b' * 32}.webp"
    orphan.write_bytes(b"orphan")
    reparse_candidate.write_bytes(b"reparse")
    original_lstat = Path.lstat

    def reparse_lstat(path: Path) -> object:
        metadata = original_lstat(path)
        if path == reparse_candidate:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=0x400,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", reparse_lstat)

    removed = api._sweep_semantic_screenshot_orphans(
        root, (root / "missing-reference.png",)
    )

    assert removed == 1
    assert not orphan.exists()
    assert reparse_candidate.read_bytes() == b"reparse"
