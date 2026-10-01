from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote
from zipfile import ZipFile

from fastapi.testclient import TestClient

from figma_to_fgui.api import create_app
from tests.helpers.zip_projects import write_project_zip

PLUGIN_HEADERS = {"X-Figma-Plugin-Token": "test-plugin-token"}


def test_plugin_only_create_and_update_deliver_checked_archives_without_agents(
    tmp_path: Path,
) -> None:
    """Exercise the same public plugin HTTP contract for create and update."""

    templates = _write_template(tmp_path / "templates")
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
    calls: list[str] = []
    request = client.request

    def tracked_request(method: str, url: str, **kwargs: object):
        calls.append(url)
        return request(method, url, **kwargs)

    client.request = tracked_request  # type: ignore[method-assign]
    template_hash = _tree_hash(templates / "fgui-2024-web")
    selection_id = _upload_selection(client)

    created_project = client.post(
        "/v1/projects/from-template",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "template_id": "fgui-2024-web", "project_name": "Quiz"},
    )
    assert created_project.status_code == 201, created_project.text
    create_download = _build_and_download(client, selection_id, created_project.json()["project_id"], "Quiz", "create", "Quiz")

    original_zip = write_project_zip(
        tmp_path / "Existing.zip",
        {"Existing/package.xml": b"<package id='existing'><resources/></package>"},
    )
    original_hash = sha256(original_zip.read_bytes()).hexdigest()
    with original_zip.open("rb") as source:
        updated_project = client.post(
            "/v1/projects/uploads",
            headers=PLUGIN_HEADERS,
            files={"project": (original_zip.name, source, "application/zip")},
        )
    assert updated_project.status_code == 201, updated_project.text
    update_download = _build_and_download(client, selection_id, updated_project.json()["project_id"], "Existing", "update", "Existing")

    assert create_download.headers["content-disposition"] != update_download.headers["content-disposition"]
    assert "Quiz-Figma新建-" in _download_name(create_download.headers["content-disposition"])
    assert "Existing-Figma更新-" in _download_name(update_download.headers["content-disposition"])
    _assert_checked_project_archive(create_download.content, "Quiz")
    _assert_checked_project_archive(update_download.content, "Existing")
    assert sha256(original_zip.read_bytes()).hexdigest() == original_hash
    assert _tree_hash(templates / "fgui-2024-web") == template_hash
    assert all(not call.startswith("/v1/agents/") for call in calls)
    assert all(not call.startswith("/v1/figma/pairings") for call in calls)


def _write_template(root: Path) -> Path:
    template = root / "fgui-2024-web"
    package = template / "Starter"
    package.mkdir(parents=True)
    (template / "template.json").write_text(
        '{"template_id":"fgui-2024-web","fairygui_version":"2024.2","target_platform":"web","display_name":"FairyGUI 2024 Web"}',
        "utf-8",
    )
    (package / "package.xml").write_text("<package id='starter'><resources/></package>", "utf-8")
    return root


def _upload_selection(client: TestClient) -> str:
    created = client.post(
        "/v1/figma/selections/uploads",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "idempotency_key": "plugin-only-selection"},
    )
    assert created.status_code == 201, created.text
    upload_id = created.json()["upload_id"]
    manifest = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest",
        headers=PLUGIN_HEADERS,
        json=_plugin_selection_manifest(),
    )
    assert manifest.status_code == 200, manifest.text
    resource = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/resources/preview",
        headers={**PLUGIN_HEADERS, "content-type": "image/svg+xml"},
        content=b"<svg/>",
    )
    assert resource.status_code == 200, resource.text
    committed = client.post(
        f"/v1/figma/selections/uploads/{upload_id}/commit", headers=PLUGIN_HEADERS
    )
    assert committed.status_code == 200, committed.text
    return str(committed.json()["selection_id"])


def _build_and_download(
    client: TestClient,
    selection_id: str,
    project_id: str,
    package_name: str,
    mode: str,
    project_name: str,
):
    job = client.post(
        f"/v1/figma/selections/{selection_id}/projects/{project_id}/jobs",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "selection_id": selection_id, "project_id": project_id, "package_name": package_name},
    )
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "ready_for_review", job.text
    job_id = job.json()["job_id"]
    packaged = client.post(
        f"/v1/jobs/{job_id}/package",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "mode": mode, "project_name": project_name},
    )
    assert packaged.status_code == 202, packaged.text
    ready = client.get(f"/v1/jobs/{job_id}/package", headers=PLUGIN_HEADERS)
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready", ready.text
    downloaded = client.get(f"/v1/jobs/{job_id}/package/download", headers=PLUGIN_HEADERS)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["content-type"].startswith("application/zip")
    return downloaded


def _plugin_selection_manifest() -> dict[str, object]:
    return {
        "version": 1,
        "display_name": "Checkout",
        "top_level_nodes": [
            {
                "id": "frame-1",
                "name": "Checkout",
                "type": "FRAME",
                "bounds": {"x": 0, "y": 0, "width": 600, "height": 300},
                "children": [
                    {
                        "id": "vector-1",
                        "name": "Mark",
                        "type": "VECTOR",
                        "bounds": {"x": 0, "y": 0, "width": 2, "height": 2},
                        "resource_keys": ["preview"],
                    }
                ],
            }
        ],
        "resources": [{"key": "preview", "mime_type": "image/svg+xml", "size": 6}],
    }


def _assert_checked_project_archive(content: bytes, package_name: str) -> None:
    with ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        assert f"{package_name}/package.xml" in names
        assert sum(name.endswith(".xml") for name in names) == 2
        assert any("/assets/" in name for name in names)
        assert b"<package" in archive.read(f"{package_name}/package.xml")


def _download_name(header: str) -> str:
    return unquote(header.split("filename*=utf-8''", 1)[1])


def _tree_hash(root: Path) -> str:
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()
