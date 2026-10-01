from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient
from lxml import etree
from PIL import Image
from psd_tools import PSDImage

from figma_to_fgui.api import create_app
from figma_to_fgui.project_store import ProjectStore

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement"
HEADERS = {"x-figma-plugin-token": "test-token"}
COMPONENT = "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml"


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-token",
        )
    )


def _upload_project(client: TestClient, tmp_path: Path) -> str:
    source = FIXTURE / "old_project"
    archive = tmp_path / "OldVillage.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        for path in source.rglob("*"):
            if path.is_file() and ".figma-to-fgui-preview" not in path.parts:
                relative = path.relative_to(source).as_posix()
                if path.name == "Panel_MyVillage_Sketchboard.xml":
                    doc = etree.parse(str(path))
                    display = doc.getroot().find("displayList")
                    for child in tuple(display):
                        if child.get("id") != "board_bg":
                            display.remove(child)
                    for child in tuple(doc.getroot()):
                        if child.tag != "displayList":
                            doc.getroot().remove(child)
                    output.writestr(relative, etree.tostring(doc, encoding="utf-8"))
                else:
                    output.write(path, relative)
    with archive.open("rb") as content:
        response = client.post(
            "/v1/projects/uploads",
            files={"project": (archive.name, content, "application/zip")},
            headers=HEADERS,
        )
    assert response.status_code == 201, response.text
    return response.json()["project_id"]


def _target(client: TestClient, project_id: str) -> dict[str, object]:
    tree = client.get(f"/v1/projects/{project_id}/hifi-targets", headers=HEADERS).json()
    package = next(item for item in tree["packages"] if item["name"] == "MyVillage")
    directory = next(item for item in package["directories"] if item["path"] == "Panel")
    component = next(
        item for item in directory["components"] if item["resource_id"] == "sketch01"
    )
    return {
        "version": 1,
        "project_id": project_id,
        "project_fingerprint": tree["project_fingerprint"],
        "package_id": package["package_id"],
        "package_name": package["name"],
        "directory": directory["path"],
        "component_id": component["resource_id"],
        "component_name": component["name"],
        "component_relative_path": component["relative_path"],
    }


def _upload_psd(client: TestClient, tmp_path: Path) -> str:
    document = PSDImage.new(mode="RGB", size=(750, 600), depth=8)
    document.create_pixel_layer(
        Image.new("RGBA", (750, 420), (255, 255, 255, 255)), name="BoardBg", left=0, top=0
    )
    psd = tmp_path / "screen.psd"
    document.save(psd)
    with psd.open("rb") as content:
        uploaded = client.post(
            "/v1/hifi-sources/psd",
            files={"psd": (psd.name, content, "image/vnd.adobe.photoshop")},
            headers=HEADERS,
        )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()["source_id"]


def _start_psd_session(client: TestClient, project_id: str, source_id: str, key: str) -> str:
    created = client.post(
        "/v1/hifi-replacements/from-psd",
        json={
            "version": 1,
            "project_id": project_id,
            "psd_source_id": source_id,
            "target": _target(client, project_id),
            "idempotency_key": key,
        },
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _resolve_and_build(client: TestClient, session_id: str) -> str:
    while True:
        mapping = client.get(
            f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
        ).json()
        unresolved = next((item for item in mapping["items"] if item["action"] is None), None)
        if unresolved is None:
            break
        if unresolved["status"] in {"uncertain", "suggested"}:
            action, node_id = "retarget", unresolved["candidates"][0]
        elif unresolved["status"] == "hifi_added":
            action, node_id = "add_visual", None
        else:
            action, node_id = "exception", None
        decided = client.post(
            f"/v1/hifi-replacements/{session_id}/mapping-decisions",
            json={
                "version": 1,
                "mapping_revision": mapping["mapping_revision"],
                "item_id": unresolved["item_id"],
                "action": action,
                **({"figma_node_id": node_id} if node_id else {}),
            },
            headers=HEADERS,
        )
        assert decided.status_code == 200, decided.text
    mapping = client.get(f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS).json()
    built = client.post(
        f"/v1/hifi-replacements/{session_id}/build",
        json={"version": 1, "mapping_revision": mapping["mapping_revision"]},
        headers=HEADERS,
    )
    assert built.status_code == 200, built.text
    review = client.get(f"/v1/hifi-replacements/{session_id}/review", headers=HEADERS)
    assert review.status_code == 200, review.text
    return review.json()["candidate_sha256"]


def test_batch_psd_start_creates_one_session_per_target_and_is_idempotent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = _upload_project(client, tmp_path)
    second = _upload_project(client, tmp_path)
    assert first != second
    source_id = _upload_psd(client, tmp_path)
    payload = {
        "version": 1,
        "psd_source_id": source_id,
        "targets": [_target(client, first), _target(client, second)],
        "idempotency_key": "batch-1",
    }
    created = client.post("/v1/hifi-replacements/from-psd-batch", json=payload, headers=HEADERS)
    assert created.status_code == 201, created.text
    views = created.json()
    assert len(views) == 2
    assert {view["target"]["project_id"] for view in views} == {first, second}
    for view in views:
        assert view["status"] == "mapping"
        assert view["selection_id"] == source_id

    retry = client.post("/v1/hifi-replacements/from-psd-batch", json=payload, headers=HEADERS)
    assert retry.status_code == 201, retry.text
    assert [view["session_id"] for view in retry.json()] == [view["session_id"] for view in views]


def test_batch_psd_start_rejects_an_empty_target_list(tmp_path: Path) -> None:
    client = _client(tmp_path)
    source_id = _upload_psd(client, tmp_path)
    response = client.post(
        "/v1/hifi-replacements/from-psd-batch",
        json={"version": 1, "psd_source_id": source_id, "targets": [], "idempotency_key": "batch-2"},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_overwrite_delivery_registers_a_new_project_version_and_keeps_history(tmp_path: Path) -> None:
    client = _client(tmp_path)
    project_id = _upload_project(client, tmp_path)
    source_id = _upload_psd(client, tmp_path)
    session_id = _start_psd_session(client, project_id, source_id, "overwrite-1")
    candidate_sha256 = _resolve_and_build(client, session_id)

    store = ProjectStore(tmp_path / "data")
    before = store.get(project_id)
    before_artifact = store.artifact_path(project_id)
    before_component = (before_artifact / COMPONENT).read_bytes()
    before_names = {path.relative_to(before_artifact).as_posix() for path in before_artifact.rglob("*") if path.is_file()}

    workflow = client.app.state.hifi_replacement_workflow
    delivered = workflow.deliver_overwrite(session_id, "bundled-figma-plugin", candidate_sha256)
    assert delivered.view.status == "review_ready"

    after = store.get(project_id)
    assert after.project_id == before.project_id
    assert after.fingerprint != before.fingerprint
    after_artifact = store.artifact_path(project_id)
    assert after_artifact != before_artifact
    assert before_artifact.is_dir(), "the previous version must remain readable"
    assert (before_artifact / COMPONENT).read_bytes() == before_component
    after_component = (after_artifact / COMPONENT).read_bytes()
    assert after_component != before_component
    after_names = {path.relative_to(after_artifact).as_posix() for path in after_artifact.rglob("*") if path.is_file()}
    added = sorted(after_names - before_names)
    assert added, "overwriting must write the reviewed visual"
    assert any(name.startswith("assets/MyVillage/Img/HIFI/") for name in added), added


def test_overwrite_delivery_rejects_a_candidate_hash_mismatch(tmp_path: Path) -> None:
    client = _client(tmp_path)
    project_id = _upload_project(client, tmp_path)
    source_id = _upload_psd(client, tmp_path)
    session_id = _start_psd_session(client, project_id, source_id, "overwrite-2")
    _resolve_and_build(client, session_id)

    store = ProjectStore(tmp_path / "data")
    fingerprint = store.get(project_id).fingerprint
    workflow = client.app.state.hifi_replacement_workflow
    try:
        workflow.deliver_overwrite(session_id, "bundled-figma-plugin", "0" * 64)
        raise AssertionError("a mismatched candidate hash must not be delivered")
    except Exception as error:  # noqa: BLE001 - the store error is the contract
        assert type(error).__name__ == "HifiReplacementStoreError"
    assert store.get(project_id).fingerprint == fingerprint


def test_overwrite_export_never_bypasses_the_approval_gate(tmp_path: Path) -> None:
    client = _client(tmp_path)
    project_id = _upload_project(client, tmp_path)
    source_id = _upload_psd(client, tmp_path)
    session_id = _start_psd_session(client, project_id, source_id, "overwrite-3")
    candidate_sha256 = _resolve_and_build(client, session_id)

    response = client.post(
        f"/v1/hifi-replacements/{session_id}/approve",
        json={
            "version": 1,
            "layout_checked": True,
            "references_checked": True,
            "interactions_checked": True,
            "editor_version": "6.1.4",
            "candidate_sha256": candidate_sha256,
            "export_mode": "overwrite",
        },
        headers=HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "hifi_download_blocked"
    store = ProjectStore(tmp_path / "data")
    assert store.artifact_path(project_id) is not None
