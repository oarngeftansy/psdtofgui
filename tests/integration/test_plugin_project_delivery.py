from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sqlite3
from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from figma_to_fgui import api
from figma_to_fgui.api import create_app
from figma_to_fgui.artifacts import ArtifactStore
from figma_to_fgui.figma_selection import SelectionManifest
from figma_to_fgui.job_store import InvalidTransition, JobStore
from figma_to_fgui.project_store import ProjectStore
from figma_to_fgui.selection_store import SelectionStore
from figma_to_fgui.service_contracts import (
    ChangeBundle,
    ChangeFile,
    FileOperation,
    JobStatus,
    JobView,
    ProjectPackageRequest,
    ProjectPackageStage,
    ProjectPackageView,
)
from tests.helpers.zip_projects import write_project_zip

PLUGIN_HEADERS = {"X-Figma-Plugin-Token": "test-plugin-token"}
GATEWAY_HEADERS = {"X-Figma-Gateway-Token": "g" * 32}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 30, 15, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, duration: timedelta) -> None:
        self.now += duration


def _template(root: Path) -> Path:
    template = root / "fgui-2024-web"
    package = template / "Starter"
    package.mkdir(parents=True)
    (template / "template.json").write_text(
        json.dumps(
            {
                "template_id": "fgui-2024-web",
                "fairygui_version": "2024.2",
                "target_platform": "web",
                "display_name": "FairyGUI 2024 Web",
            }
        ),
        "utf-8",
    )
    (package / "package.xml").write_text(
        "<package id='quiz'><resources/></package>", "utf-8"
    )
    return root


def _client(tmp_path: Path, **overrides: object) -> tuple[TestClient, Path]:
    data_dir = tmp_path / "data"
    arguments: dict[str, object] = {
        "data_dir": data_dir,
        "fixtures_root": Path("tests/fixtures"),
        "rules_path": Path("rules/default/classification.yaml"),
        "templates_root": _template(tmp_path / "templates"),
        "plugin_access_token": b"test-plugin-token",
        "gateway_secret": b"g" * 32,
    }
    arguments.update(overrides)
    return TestClient(create_app(**arguments)), data_dir  # type: ignore[arg-type]


def _selection_manifest() -> dict[str, object]:
    return {
        "version": 1,
        "display_name": "Quiz screen",
        "top_level_nodes": [
            {
                "id": "frame-1",
                "name": "Quiz",
                "type": "FRAME",
                "bounds": {"x": 0, "y": 0, "width": 400, "height": 300},
                "children": [
                    {
                        "id": "image-1",
                        "name": "Background",
                        "type": "RECTANGLE",
                        "bounds": {"x": 0, "y": 0, "width": 400, "height": 300},
                        "resource_keys": ["preview"],
                    },
                    {
                        "id": "text-1",
                        "name": "Question",
                        "type": "TEXT",
                        "bounds": {"x": 20, "y": 20, "width": 200, "height": 30},
                        "text": "Which answer?",
                    }
                ],
            }
        ],
        "resources": [{"key": "preview", "mime_type": "image/png", "size": len(PNG)}],
    }


def _upload_selection(client: TestClient, headers: dict[str, str] = PLUGIN_HEADERS) -> str:
    upload = client.post(
        "/v1/figma/selections/uploads",
        headers=headers,
        json={"version": 1, "idempotency_key": "selection-1"},
    )
    assert upload.status_code == 201, upload.text
    upload_id = upload.json()["upload_id"]
    manifest = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest",
        headers=headers,
        json=_selection_manifest(),
    )
    assert manifest.status_code == 200, manifest.text
    resource = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/resources/preview",
        headers={**headers, "content-type": "image/png"},
        content=PNG,
    )
    assert resource.status_code == 200, resource.text
    committed = client.post(
        f"/v1/figma/selections/uploads/{upload_id}/commit", headers=headers
    )
    assert committed.status_code == 200, committed.text
    return str(committed.json()["selection_id"])


def _create_job(
    client: TestClient,
    selection_id: str,
    project_id: str,
    headers: dict[str, str] = PLUGIN_HEADERS,
) -> str:
    created = client.post(
        f"/v1/figma/selections/{selection_id}/projects/{project_id}/jobs",
        headers=headers,
        json={
            "version": 1,
            "selection_id": selection_id,
            "project_id": project_id,
            "package_name": "Quiz",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "ready_for_review"
    return str(created.json()["job_id"])


def _build(client: TestClient, job_id: str, mode: str = "create") -> dict[str, object]:
    response = client.post(
        f"/v1/jobs/{job_id}/package",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "mode": mode, "project_name": "Quiz"},
    )
    assert response.status_code == 202, response.text
    status = client.get(f"/v1/jobs/{job_id}/package", headers=PLUGIN_HEADERS)
    assert status.status_code == 200, status.text
    return status.json()


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _seed_empty_job(
    client: TestClient, data_dir: Path, tmp_path: Path, job_id: str
) -> tuple[str, str]:
    archive = write_project_zip(
        tmp_path / f"{job_id}.zip",
        {"Quiz/package.xml": b"<package id='quiz'><resources/></package>"},
    )
    with archive.open("rb") as source:
        project = client.post(
            "/v1/projects/uploads",
            headers=GATEWAY_HEADERS,
            files={"project": (archive.name, source, "application/zip")},
        ).json()
    project_id = str(project["project_id"])
    bundle = ChangeBundle(job_id=job_id, project_id=project_id, files=())
    artifact = ArtifactStore(data_dir / "artifacts").put(bundle)
    JobStore(data_dir / "server.db").create_job(
        JobView(
            job_id=job_id,
            project_id=project_id,
            status=JobStatus.READY_FOR_REVIEW,
            artifact_sha256=artifact,
        )
    )
    return project_id, artifact


@pytest.mark.parametrize("mode", ("create", "update"))
def test_plugin_create_and_update_flows_return_openable_archives(
    tmp_path: Path, mode: str
) -> None:
    client, data_dir = _client(tmp_path)
    selection_id = _upload_selection(client)
    if mode == "create":
        project = client.post(
            "/v1/projects/from-template",
            headers=PLUGIN_HEADERS,
            json={
                "version": 1,
                "template_id": "fgui-2024-web",
                "project_name": "Quiz",
            },
        )
    else:
        archive = write_project_zip(
            tmp_path / "Quiz.zip",
            {"Quiz/package.xml": b"<package id='quiz'><resources/></package>"},
        )
        original = archive.read_bytes()
        with archive.open("rb") as source:
            project = client.post(
                "/v1/projects/uploads",
                headers=PLUGIN_HEADERS,
                files={"project": ("Quiz.zip", source, "application/zip")},
            )
        assert archive.read_bytes() == original
    assert project.status_code == 201, project.text
    project_id = str(project.json()["project_id"])
    source_root = ProjectStore(data_dir).artifact_path(project_id)
    source_fingerprint = _fingerprint(source_root)

    package = _build(client, _create_job(client, selection_id, project_id), mode)

    assert package["status"] == package["stage"] == "ready"
    assert package["progress"] == 100
    assert package["sha256"]
    assert package["download_name"].startswith(f"Quiz-Figma{'新建' if mode == 'create' else '更新'}-")
    assert "path" not in package
    downloaded = client.get(
        f"/v1/jobs/{package['job_id']}/package/download", headers=PLUGIN_HEADERS
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"] == "application/zip"
    assert downloaded.headers["cache-control"] == "no-store"
    assert "filename*=utf-8''" in downloaded.headers["content-disposition"].lower()
    output = tmp_path / "result.zip"
    output.write_bytes(downloaded.content)
    with ZipFile(output) as result:
        assert result.testzip() is None
        assert "Quiz/package.xml" in result.namelist()
    assert hashlib.sha256(downloaded.content).hexdigest() == package["sha256"]
    assert _fingerprint(source_root) == source_fingerprint


def test_package_requests_are_idempotent_and_incompatible_duplicates_fail(tmp_path: Path) -> None:
    client, data_dir = _client(tmp_path)
    selection_id = _upload_selection(client)
    project = client.post(
        "/v1/projects/from-template",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "template_id": "fgui-2024-web", "project_name": "Quiz"},
    ).json()
    job_id = _create_job(client, selection_id, str(project["project_id"]))
    request = {"version": 1, "mode": "create", "project_name": "Quiz"}

    first = client.post(f"/v1/jobs/{job_id}/package", headers=PLUGIN_HEADERS, json=request)
    duplicate = client.post(f"/v1/jobs/{job_id}/package", headers=PLUGIN_HEADERS, json=request)
    incompatible = client.post(
        f"/v1/jobs/{job_id}/package",
        headers=PLUGIN_HEADERS,
        json={**request, "mode": "update"},
    )

    assert first.status_code == duplicate.status_code == 202
    assert first.json()["job_id"] == duplicate.json()["job_id"] == job_id
    assert duplicate.json()["status"] == "ready"
    assert incompatible.status_code == 409
    assert incompatible.json()["detail"]["code"] == "package_request_conflict"

    job = JobStore(data_dir / "server.db").get_job(job_id)
    assert job.artifact_sha256 is not None
    (data_dir / "artifacts" / f"{job.artifact_sha256}.json").unlink()
    shutil.rmtree(ProjectStore(data_dir).artifact_path(str(project["project_id"])))
    after_sources_removed = client.post(
        f"/v1/jobs/{job_id}/package", headers=PLUGIN_HEADERS, json=request
    )
    assert after_sources_removed.status_code == 202
    assert after_sources_removed.json() == duplicate.json()
    assert client.get(f"/v1/jobs/{job_id}/package", headers=PLUGIN_HEADERS).json() == duplicate.json()
    assert client.get(
        f"/v1/jobs/{job_id}/package/download", headers=PLUGIN_HEADERS
    ).status_code == 200


def test_missing_inputs_and_invalid_request_fail_safely(tmp_path: Path) -> None:
    client, data_dir = _client(tmp_path)
    store = JobStore(data_dir / "server.db")
    missing_bundle = JobView(
        job_id="missing-bundle", project_id="missing-project", status=JobStatus.CONVERSION_FAILED
    )
    store.create_job(missing_bundle)
    bundle = ChangeBundle(job_id="missing-project-job", project_id="missing-project", files=())
    digest = ArtifactStore(data_dir / "artifacts").put(bundle)
    store.create_job(
        JobView(
            job_id=bundle.job_id,
            project_id=bundle.project_id,
            status=JobStatus.READY_FOR_REVIEW,
            artifact_sha256=digest,
        )
    )

    assert client.post(
        "/v1/jobs/does-not-exist/package",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "mode": "create", "project_name": "Quiz"},
    ).status_code == 404
    unavailable = client.post(
        "/v1/jobs/missing-bundle/package",
        headers=GATEWAY_HEADERS,
        json={"version": 1, "mode": "create", "project_name": "Quiz"},
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "artifact_unavailable"
    missing_project = client.post(
        "/v1/jobs/missing-project-job/package",
        headers=GATEWAY_HEADERS,
        json={"version": 1, "mode": "create", "project_name": "Quiz"},
    )
    assert missing_project.status_code == 404
    invalid = client.post(
        "/v1/jobs/missing-project-job/package",
        headers=GATEWAY_HEADERS,
        json={"version": 1, "mode": "create", "project_name": "../Quiz"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_project_name"
    before_ready = client.get(
        "/v1/jobs/missing-project-job/package/download", headers=GATEWAY_HEADERS
    )
    assert before_ready.status_code == 409
    assert before_ready.json()["detail"]["code"] == "package_not_ready"


@pytest.mark.parametrize("failure", ("conflict", "validation"))
def test_background_failures_become_stable_sanitized_status(
    tmp_path: Path, failure: str
) -> None:
    client, data_dir = _client(tmp_path)
    archive = write_project_zip(
        tmp_path / "Quiz.zip",
        {"Quiz/package.xml": b"<package id='quiz'><resources/></package>"},
    )
    with archive.open("rb") as source:
        project = client.post(
            "/v1/projects/uploads",
            headers=GATEWAY_HEADERS,
            files={"project": ("Quiz.zip", source, "application/zip")},
        ).json()
    job_id = f"{failure}-job"
    before = b"<package id='quiz'><resources/></package>"
    after = (
        b"<package id='quiz'><resources/></package>"
        if failure == "conflict"
        else b"<package>"
    )
    bundle = ChangeBundle(
        job_id=job_id,
        project_id=project["project_id"],
        files=(
            ChangeFile(
                operation=FileOperation.REPLACE,
                relative_path="Quiz/package.xml",
                before_sha256=(
                    "0" * 64 if failure == "conflict" else hashlib.sha256(before).hexdigest()
                ),
                after_sha256=hashlib.sha256(after).hexdigest(),
                content_b64=base64.b64encode(after).decode("ascii"),
            ),
        ),
    )
    artifact = ArtifactStore(data_dir / "artifacts").put(bundle)
    JobStore(data_dir / "server.db").create_job(
        JobView(
            job_id=job_id,
            project_id=project["project_id"],
            status=JobStatus.READY_FOR_REVIEW,
            artifact_sha256=artifact,
        )
    )
    started = client.post(
        f"/v1/jobs/{job_id}/package",
        headers=GATEWAY_HEADERS,
        json={"version": 1, "mode": "create", "project_name": "Quiz"},
    )
    assert started.status_code == 202
    status = client.get(f"/v1/jobs/{job_id}/package", headers=GATEWAY_HEADERS)
    assert status.status_code == 200
    assert status.json()["status"] == status.json()["stage"] == "failed"
    assert status.json()["progress"] < 100
    assert status.json()["diagnostics"][0]["code"] == "package_failed"
    for secret in ("traceback", "before digest", "xmlsyntaxerror", str(data_dir)):
        assert secret.lower() not in status.text.lower()
    assert client.get(
        f"/v1/jobs/{job_id}/package/download", headers=GATEWAY_HEADERS
    ).status_code == 409


def test_all_package_routes_require_plugin_or_gateway_and_denials_keep_cors(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    selection_id = _upload_selection(client)
    project = client.post(
        "/v1/projects/from-template",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "template_id": "fgui-2024-web", "project_name": "Quiz"},
    ).json()
    job_id = _create_job(client, selection_id, str(project["project_id"]))
    payload = {"version": 1, "mode": "create", "project_name": "Quiz"}

    denied = client.post(
        f"/v1/jobs/{job_id}/package",
        headers={"Origin": "null", "X-Figma-Plugin-Token": "wrong"},
        json=payload,
    )
    assert denied.status_code == 401
    assert denied.headers["access-control-allow-origin"] == "null"
    assert client.post(f"/v1/jobs/{job_id}/package", json=payload).status_code == 401
    built = client.post(
        f"/v1/jobs/{job_id}/package", headers=GATEWAY_HEADERS, json=payload
    )
    assert built.status_code == 202

    for suffix in ("package", "package/download"):
        assert client.get(f"/v1/jobs/{job_id}/{suffix}").status_code == 401
        assert client.get(
            f"/v1/jobs/{job_id}/{suffix}", headers={"X-Figma-Plugin-Token": "wrong"}
        ).status_code == 401
        assert client.get(f"/v1/jobs/{job_id}/{suffix}", headers=PLUGIN_HEADERS).status_code == 200
        assert client.get(f"/v1/jobs/{job_id}/{suffix}", headers=GATEWAY_HEADERS).status_code == 200


def test_console_sessions_cannot_read_another_devices_package(tmp_path: Path) -> None:
    client, data_dir = _client(tmp_path, plugin_secret=b"s" * 32)
    pairings: list[tuple[str, str]] = []
    for name in ("First", "Second"):
        code = client.post("/v1/figma/pairings", headers=GATEWAY_HEADERS).json()
        exchange = client.post(
            "/v1/figma/pairings/exchange",
            headers=GATEWAY_HEADERS,
            json={"version": 1, "code": code["code"], "device_name": name},
        ).json()
        pairings.append((code["console_credential"], exchange["device"]["device_id"]))
    owner_session, owner_device = pairings[0]
    other_session, _ = pairings[1]

    selections = SelectionStore(data_dir)
    upload = selections.create_upload(owner_device, "owned")
    selections.put_manifest(
        upload.upload_id, owner_device, SelectionManifest.model_validate(_selection_manifest())
    )
    selections.put_resource(upload.upload_id, owner_device, "preview", "image/png", PNG)
    selection = selections.commit(upload.upload_id, owner_device)
    project_zip = write_project_zip(
        tmp_path / "Quiz.zip",
        {"Quiz/package.xml": b"<package id='quiz'><resources/></package>"},
    )
    with project_zip.open("rb") as source:
        project = client.post(
            "/v1/projects/uploads",
            headers=GATEWAY_HEADERS,
            files={"project": ("Quiz.zip", source, "application/zip")},
        ).json()
    bundle = ChangeBundle(job_id="owned-job", project_id=project["project_id"], files=())
    artifact = ArtifactStore(data_dir / "artifacts").put(bundle)
    JobStore(data_dir / "server.db").create_job(
        JobView(
            job_id=bundle.job_id,
            project_id=bundle.project_id,
            status=JobStatus.READY_FOR_REVIEW,
            artifact_sha256=artifact,
        ),
        selection.selection_id,
        selection.fingerprint,
    )
    owner_headers = {**GATEWAY_HEADERS, "X-Figma-Console-Session": owner_session}
    other_headers = {**GATEWAY_HEADERS, "X-Figma-Console-Session": other_session}
    assert client.post(
        "/v1/jobs/owned-job/package",
        headers=owner_headers,
        json={"version": 1, "mode": "update", "project_name": "Quiz"},
    ).status_code == 202
    assert client.get("/v1/jobs/owned-job/package", headers=owner_headers).status_code == 200
    assert client.get("/v1/jobs/owned-job/package", headers=other_headers).status_code == 404
    assert client.get(
        "/v1/jobs/owned-job/package/download", headers=other_headers
    ).status_code == 404


def test_download_filename_rejects_header_injection(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    selection_id = _upload_selection(client)
    project = client.post(
        "/v1/projects/from-template",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "template_id": "fgui-2024-web", "project_name": "Quiz"},
    ).json()
    job_id = _create_job(client, selection_id, str(project["project_id"]))
    rejected = client.post(
        f"/v1/jobs/{job_id}/package",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "mode": "create", "project_name": "Quiz\r\nX-Evil: yes"},
    )

    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "invalid_project_name"
    assert "x-evil" not in str(rejected.headers).lower()


@pytest.mark.parametrize("damage", ("missing", "tampered", "outside", "directory"))
def test_ready_artifact_damage_becomes_failed_and_can_be_rebuilt(
    tmp_path: Path, damage: str
) -> None:
    client, data_dir = _client(tmp_path)
    _seed_empty_job(client, data_dir, tmp_path, "artifact-job")
    payload = {"version": 1, "mode": "update", "project_name": "Quiz"}
    assert client.post(
        "/v1/jobs/artifact-job/package", headers=GATEWAY_HEADERS, json=payload
    ).status_code == 202
    artifact = next((data_dir / "project-packages").rglob("*.zip"))
    if damage == "missing":
        artifact.unlink()
    elif damage == "tampered":
        artifact.write_bytes(b"tampered")
    elif damage == "outside":
        outside = tmp_path / "outside.zip"
        shutil.copy2(artifact, outside)
        with sqlite3.connect(data_dir / "server.db") as connection:
            connection.execute(
                "UPDATE project_packages SET artifact_path = ? WHERE job_id = ?",
                (str(outside), "artifact-job"),
            )
    else:
        with sqlite3.connect(data_dir / "server.db") as connection:
            connection.execute(
                "UPDATE project_packages SET artifact_path = ? WHERE job_id = ?",
                (str(artifact.parent), "artifact-job"),
            )

    reconciled = client.get(
        "/v1/jobs/artifact-job/package", headers=GATEWAY_HEADERS
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["status"] == reconciled.json()["stage"] == "failed"
    assert reconciled.json()["diagnostics"][0]["code"] == "package_artifact_invalid"
    assert client.get(
        "/v1/jobs/artifact-job/package/download", headers=GATEWAY_HEADERS
    ).status_code == 409

    retried = client.post(
        "/v1/jobs/artifact-job/package", headers=GATEWAY_HEADERS, json=payload
    )
    assert retried.status_code == 202
    ready = client.get("/v1/jobs/artifact-job/package", headers=GATEWAY_HEADERS).json()
    assert ready["status"] == "ready"
    downloaded = client.get(
        "/v1/jobs/artifact-job/package/download", headers=GATEWAY_HEADERS
    )
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == ready["sha256"]


def test_restart_recovers_interrupted_attempt_and_identical_retry_reschedules(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    lease = timedelta(seconds=30)
    client, data_dir = _client(
        tmp_path,
        package_clock=clock,
        package_lease_duration=lease,
        package_owner_id="instance-a",
    )
    _seed_empty_job(client, data_dir, tmp_path, "restart-job")
    store = JobStore(data_dir / "server.db", clock=clock, package_lease_duration=lease)
    request = ProjectPackageRequest(mode="update", project_name="Quiz")
    identity = hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()
    interrupted = ProjectPackageView(
        job_id="restart-job",
        status=ProjectPackageStage.CHECKING,
        stage=ProjectPackageStage.CHECKING,
        progress=70,
    )
    attempt = store.begin_package("restart-job", identity, "instance-a", interrupted)
    assert attempt.should_build
    clock.advance(lease + timedelta(microseconds=1))

    restarted = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            templates_root=_template(tmp_path / "restart-templates"),
            plugin_access_token=b"test-plugin-token",
            gateway_secret=b"g" * 32,
            package_clock=clock,
            package_lease_duration=lease,
            package_owner_id="instance-b",
        )
    )
    recovered = restarted.get(
        "/v1/jobs/restart-job/package", headers=GATEWAY_HEADERS
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "failed"
    assert recovered.json()["diagnostics"][0]["code"] == "package_interrupted"

    retried = restarted.post(
        "/v1/jobs/restart-job/package",
        headers=GATEWAY_HEADERS,
        json=request.model_dump(mode="json"),
    )
    assert retried.status_code == 202
    assert restarted.get(
        "/v1/jobs/restart-job/package", headers=GATEWAY_HEADERS
    ).json()["status"] == "ready"


def test_package_store_enforces_cas_transitions_and_preserves_artifact_path(
    tmp_path: Path,
) -> None:
    client, data_dir = _client(tmp_path)
    _seed_empty_job(client, data_dir, tmp_path, "cas-job")
    store = JobStore(data_dir / "server.db")
    checking = ProjectPackageView(
        job_id="cas-job",
        status=ProjectPackageStage.CHECKING,
        stage=ProjectPackageStage.CHECKING,
        progress=70,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = tuple(
            executor.map(
                lambda _: JobStore(data_dir / "server.db").begin_package(
                    "cas-job", "identity", "instance-a", checking
                ),
                range(2),
            )
        )
    assert sum(item.should_build for item in attempts) == 1
    attempt = next(item for item in attempts if item.should_build)
    duplicate = next(item for item in attempts if not item.should_build)
    assert not duplicate.should_build and duplicate.generation == attempt.generation
    packaging = checking.model_copy(
        update={
            "status": ProjectPackageStage.PACKAGING,
            "stage": ProjectPackageStage.PACKAGING,
            "progress": 90,
        }
    )
    store.transition_package(
        "cas-job",
        "identity",
        attempt.generation,
        "instance-a",
        (ProjectPackageStage.CHECKING,),
        packaging,
    )
    artifact = tmp_path / "result.zip"
    artifact.write_bytes(b"zip")
    ready = packaging.model_copy(
        update={
            "status": ProjectPackageStage.READY,
            "stage": ProjectPackageStage.READY,
            "progress": 100,
            "download_name": "Quiz.zip",
            "sha256": hashlib.sha256(b"zip").hexdigest(),
        }
    )
    store.transition_package(
        "cas-job",
        "identity",
        attempt.generation,
        "instance-a",
        (ProjectPackageStage.PACKAGING,),
        ready,
        artifact_path=artifact,
    )
    terminal = store.get_package("cas-job")
    assert terminal.owner_id is None and terminal.lease_expires_at is None

    with pytest.raises(InvalidTransition):
        store.transition_package(
            "cas-job",
            "identity",
            attempt.generation,
            "instance-a",
            (ProjectPackageStage.CHECKING,),
            ready,
        )
    failed = ready.model_copy(
        update={
            "status": ProjectPackageStage.FAILED,
            "stage": ProjectPackageStage.FAILED,
            "progress": 90,
            "download_name": None,
            "sha256": None,
        }
    )
    store.transition_package(
        "cas-job",
        "identity",
        attempt.generation,
        None,
        (ProjectPackageStage.READY,),
        failed,
    )
    assert store.get_package("cas-job").artifact_path == artifact
    retry = store.begin_package("cas-job", "identity", "instance-b", checking)
    assert retry.should_build and retry.generation == attempt.generation + 1
    assert store.get_package("cas-job").artifact_path is None
    with pytest.raises(InvalidTransition):
        store.transition_package(
            "cas-job",
            "identity",
            attempt.generation,
            "instance-a",
            (ProjectPackageStage.PACKAGING,),
            ready,
            artifact_path=artifact,
        )


def test_cancelled_background_attempt_becomes_failed_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, data_dir = _client(tmp_path)
    _seed_empty_job(client, data_dir, tmp_path, "cancel-job")
    original = api.build_project_package
    calls = 0

    def cancel_once(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CancelledError
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(api, "build_project_package", cancel_once)
    payload = {"version": 1, "mode": "update", "project_name": "Quiz"}
    assert client.post(
        "/v1/jobs/cancel-job/package", headers=GATEWAY_HEADERS, json=payload
    ).status_code == 202
    cancelled = client.get("/v1/jobs/cancel-job/package", headers=GATEWAY_HEADERS)
    assert cancelled.json()["status"] == "failed"
    assert client.post(
        "/v1/jobs/cancel-job/package", headers=GATEWAY_HEADERS, json=payload
    ).status_code == 202
    assert client.get(
        "/v1/jobs/cancel-job/package", headers=GATEWAY_HEADERS
    ).json()["status"] == "ready"


@pytest.mark.parametrize("stage", (ProjectPackageStage.CHECKING, ProjectPackageStage.PACKAGING))
def test_second_instance_preserves_live_lease_then_recovers_after_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: ProjectPackageStage
) -> None:
    clock = _Clock()
    lease = timedelta(seconds=30)
    first, data_dir = _client(
        tmp_path,
        package_clock=clock,
        package_lease_duration=lease,
        package_owner_id="instance-a",
    )
    _seed_empty_job(first, data_dir, tmp_path, "lease-job")
    request = ProjectPackageRequest(mode="update", project_name="Quiz")
    identity = hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()
    store = JobStore(data_dir / "server.db", clock=clock, package_lease_duration=lease)
    checking = ProjectPackageView(
        job_id="lease-job",
        status=ProjectPackageStage.CHECKING,
        stage=ProjectPackageStage.CHECKING,
        progress=70,
    )
    attempt = store.begin_package("lease-job", identity, "instance-a", checking)
    if stage is ProjectPackageStage.PACKAGING:
        packaging = checking.model_copy(
            update={
                "status": ProjectPackageStage.PACKAGING,
                "stage": ProjectPackageStage.PACKAGING,
                "progress": 90,
            }
        )
        store.transition_package(
            "lease-job",
            identity,
            attempt.generation,
            "instance-a",
            (ProjectPackageStage.CHECKING,),
            packaging,
        )

    builds = 0
    original = api.build_project_package

    def count_builds(*args: object, **kwargs: object) -> object:
        nonlocal builds
        builds += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(api, "build_project_package", count_builds)
    second = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            templates_root=_template(tmp_path / f"second-{stage}"),
            plugin_access_token=b"test-plugin-token",
            gateway_secret=b"g" * 32,
            package_clock=clock,
            package_lease_duration=lease,
            package_owner_id="instance-b",
        )
    )
    live = second.get("/v1/jobs/lease-job/package", headers=GATEWAY_HEADERS)
    assert live.json()["status"] == stage
    duplicate = second.post(
        "/v1/jobs/lease-job/package",
        headers=GATEWAY_HEADERS,
        json=request.model_dump(mode="json"),
    )
    assert duplicate.json()["status"] == stage
    assert builds == 0

    clock.advance(lease + timedelta(microseconds=1))
    expired = second.get("/v1/jobs/lease-job/package", headers=GATEWAY_HEADERS)
    assert expired.json()["status"] == "failed"
    retried = second.post(
        "/v1/jobs/lease-job/package",
        headers=GATEWAY_HEADERS,
        json=request.model_dump(mode="json"),
    )
    assert retried.status_code == 202
    assert builds == 1
    ready = second.get("/v1/jobs/lease-job/package", headers=GATEWAY_HEADERS)
    assert ready.json()["status"] == "ready"


def test_lease_renewal_extends_attempt_and_stale_owner_cannot_write(tmp_path: Path) -> None:
    clock = _Clock()
    lease = timedelta(seconds=30)
    client, data_dir = _client(
        tmp_path,
        package_clock=clock,
        package_lease_duration=lease,
        package_owner_id="instance-a",
    )
    _seed_empty_job(client, data_dir, tmp_path, "renew-job")
    store = JobStore(data_dir / "server.db", clock=clock, package_lease_duration=lease)
    checking = ProjectPackageView(
        job_id="renew-job",
        status=ProjectPackageStage.CHECKING,
        stage=ProjectPackageStage.CHECKING,
        progress=70,
    )
    attempt = store.begin_package("renew-job", "identity", "instance-a", checking)
    clock.advance(timedelta(seconds=20))
    renewed_until = store.renew_package_lease(
        "renew-job", "identity", attempt.generation, "instance-a"
    )
    assert renewed_until == clock() + lease
    clock.advance(timedelta(seconds=15))
    duplicate = store.begin_package("renew-job", "identity", "instance-b", checking)
    assert not duplicate.should_build
    clock.advance(timedelta(seconds=16))
    replacement = store.begin_package("renew-job", "identity", "instance-b", checking)
    assert replacement.should_build and replacement.generation == attempt.generation + 1
    failed = checking.model_copy(
        update={
            "status": ProjectPackageStage.FAILED,
            "stage": ProjectPackageStage.FAILED,
            "progress": 90,
        }
    )
    with pytest.raises(InvalidTransition):
        store.transition_package(
            "renew-job",
            "identity",
            attempt.generation,
            "instance-a",
            (ProjectPackageStage.CHECKING,),
            failed,
        )


def test_long_build_heartbeat_renews_lease_without_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    lease = timedelta(milliseconds=90)
    client, data_dir = _client(
        tmp_path,
        package_clock=clock,
        package_lease_duration=lease,
        package_owner_id="instance-a",
    )
    _seed_empty_job(client, data_dir, tmp_path, "heartbeat-job")
    started = Event()
    release = Event()
    renewed = Event()
    original_build = api.build_project_package
    original_renew = JobStore.renew_package_lease

    def blocking_build(*args: object, **kwargs: object) -> object:
        started.set()
        assert release.wait(2)
        return original_build(*args, **kwargs)  # type: ignore[arg-type]

    def observe_renewal(self: JobStore, *args: object, **kwargs: object) -> datetime:
        result = original_renew(self, *args, **kwargs)  # type: ignore[arg-type]
        renewed.set()
        return result

    monkeypatch.setattr(api, "build_project_package", blocking_build)
    monkeypatch.setattr(JobStore, "renew_package_lease", observe_renewal)
    payload = {"version": 1, "mode": "update", "project_name": "Quiz"}
    with ThreadPoolExecutor(max_workers=1) as executor:
        response = executor.submit(
            client.post,
            "/v1/jobs/heartbeat-job/package",
            headers=GATEWAY_HEADERS,
            json=payload,
        )
        assert started.wait(2)
        clock.advance(timedelta(milliseconds=60))
        assert renewed.wait(2)
        clock.advance(timedelta(milliseconds=31))
        contender = JobStore(
            data_dir / "server.db", clock=clock, package_lease_duration=lease
        ).begin_package(
            "heartbeat-job",
            hashlib.sha256(
                ProjectPackageRequest(mode="update", project_name="Quiz")
                .model_dump_json(exclude_none=True)
                .encode()
            ).hexdigest(),
            "instance-b",
            ProjectPackageView(
                job_id="heartbeat-job",
                status=ProjectPackageStage.CHECKING,
                stage=ProjectPackageStage.CHECKING,
                progress=70,
            ),
        )
        assert not contender.should_build
        release.set()
        assert response.result(timeout=2).status_code == 202
    assert client.get(
        "/v1/jobs/heartbeat-job/package", headers=GATEWAY_HEADERS
    ).json()["status"] == "ready"
