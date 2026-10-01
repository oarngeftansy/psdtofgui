from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient
from lxml import etree
from PIL import Image
from psd_tools import PSDImage
from pytest import MonkeyPatch

from figma_to_fgui.api import create_app
from figma_to_fgui.hifi_replacement_models import HifiEditorVerification

FIXTURE = Path(__file__).parents[1] / "fixtures/hifi_replacement"
HEADERS = {"x-figma-plugin-token": "test-token"}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"test-token",
        )
    )


def _upload_project(client: TestClient, tmp_path: Path, *, only_image: bool = False) -> str:
    source = FIXTURE / "old_project"
    archive = tmp_path / "OldVillage.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        for path in source.rglob("*"):
            if path.is_file() and ".figma-to-fgui-preview" not in path.parts:
                relative = path.relative_to(source).as_posix()
                if only_image and path.name == "Panel_MyVillage_Sketchboard.xml":
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


def _upload_selection(client: TestClient) -> str:
    created = client.post(
        "/v1/figma/selections/uploads",
        json={"version": 1, "idempotency_key": "hifi-selection"},
        headers=HEADERS,
    )
    upload_id = created.json()["upload_id"]
    manifest = (FIXTURE / "hifi-selection.json").read_text("utf-8")
    assert client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest",
        content=manifest.encode("utf-8"),
        headers={**HEADERS, "content-type": "application/json"},
    ).status_code == 200
    resource = (FIXTURE / "selection/resources/hifi-board").read_bytes()
    assert client.put(
        f"/v1/figma/selections/uploads/{upload_id}/resources/hifi-board",
        content=resource,
        headers={**HEADERS, "content-type": "image/png"},
    ).status_code == 200
    committed = client.post(
        f"/v1/figma/selections/uploads/{upload_id}/commit", headers=HEADERS
    )
    assert committed.status_code == 200, committed.text
    return committed.json()["selection_id"]


def _target(client: TestClient, project_id: str) -> dict[str, object]:
    response = client.get(f"/v1/projects/{project_id}/hifi-targets", headers=HEADERS)
    assert response.status_code == 200, response.text
    tree = response.json()
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


def test_hifi_api_requires_mapping_review_editor_check_and_approval(tmp_path: Path) -> None:
    client = _client(tmp_path)
    project_id = _upload_project(client, tmp_path)
    selection_id = _upload_selection(client)
    target = _target(client, project_id)
    created = client.post(
        "/v1/hifi-replacements",
        json={
            "version": 1,
            "project_id": project_id,
            "selection_id": selection_id,
            "target": target,
            "idempotency_key": "replacement-1",
        },
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    assert client.get(
        f"/v1/hifi-replacements/{session_id}/download", headers=HEADERS
    ).status_code == 409

    while True:
        mapping = client.get(
            f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
        ).json()
        unresolved = next((item for item in mapping["items"] if item["action"] is None), None)
        if unresolved is None:
            break
        if unresolved["status"] in {"uncertain", "suggested"}:
            action = "retarget"
        elif unresolved["status"] == "hifi_added" and unresolved["figma_node_id"] == "progress-bubble":
            action = "add_visual"
        elif unresolved["status"] in {"hifi_added", "blocked"}:
            action = "exception"
        else:
            action = "keep_old"
        response = client.post(
            f"/v1/hifi-replacements/{session_id}/mapping-decisions",
            json={
                "version": 1,
                "mapping_revision": mapping["mapping_revision"],
                "item_id": unresolved["item_id"],
                "action": action,
                **(
                    {"figma_node_id": unresolved["candidates"][0]}
                    if action == "retarget"
                    else {}
                ),
            },
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text

    mapping = client.get(
        f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
    ).json()
    built = client.post(
        f"/v1/hifi-replacements/{session_id}/build",
        json={"version": 1, "mapping_revision": mapping["mapping_revision"]},
        headers=HEADERS,
    )
    assert built.status_code == 200, built.text
    assert built.json()["status"] == "review_ready"
    review = client.get(
        f"/v1/hifi-replacements/{session_id}/review", headers=HEADERS
    )
    assert review.status_code == 200
    assert review.json()["protected_checks_passed"] is True
    assert review.json()["approvable"] is False  # Unknown legacy tag blocks approval.
    assert len(review.json()["candidate_sha256"]) == 64
    assert {item["kind"] for item in review.json()["object_diffs"]} >= {
        "changed",
        "added",
        "kept",
    }
    candidate = client.get(
        f"/v1/hifi-replacements/{session_id}/candidate/download", headers=HEADERS
    )
    assert candidate.status_code == 200
    formal = client.get(
        f"/v1/hifi-replacements/{session_id}/download", headers=HEADERS
    )
    assert formal.status_code == 409
    assert formal.json()["detail"]["code"] == "hifi_download_blocked"
    assert candidate.content.startswith(b"PK")
    candidate_sha256 = review.json()["candidate_sha256"]
    assert client.get(
        f"/v1/hifi-replacements/{session_id}/download", headers=HEADERS
    ).status_code == 409

    stale = client.post(
        f"/v1/hifi-replacements/{session_id}/approve",
        json={
            "version": 1,
            "layout_checked": True,
            "references_checked": True,
            "interactions_checked": True,
            "editor_version": "6.1.4",
            "candidate_sha256": "0" * 64,
        },
        headers=HEADERS,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "hifi_candidate_stale"

    incomplete = client.post(
        f"/v1/hifi-replacements/{session_id}/approve",
        json={
            "version": 1,
            "layout_checked": True,
            "references_checked": False,
            "interactions_checked": True,
            "editor_version": "6.1.4",
            "candidate_sha256": candidate_sha256,
        },
        headers=HEADERS,
    )
    assert incomplete.status_code == 409
    assert incomplete.json()["detail"]["code"] == "hifi_editor_checks_incomplete"
    approved = client.post(
        f"/v1/hifi-replacements/{session_id}/approve",
        json={
            "version": 1,
            "layout_checked": True,
            "references_checked": True,
            "interactions_checked": True,
            "editor_version": "6.1.4",
            "candidate_sha256": candidate_sha256,
        },
        headers=HEADERS,
    )
    assert approved.status_code == 409
    assert approved.json()["detail"]["code"] == "hifi_download_blocked"
    delivered = client.get(
        f"/v1/hifi-replacements/{session_id}/download", headers=HEADERS
    )
    assert delivered.status_code == 409


def test_hifi_routes_require_plugin_access(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/v1/hifi-replacements", json={})
    assert response.status_code == 401


def test_psd_source_starts_existing_mapping_without_figma_selection(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = _client(tmp_path)
    project_id = _upload_project(client, tmp_path, only_image=True)
    target = _target(client, project_id)
    document = PSDImage.new(mode="RGB", size=(750, 600), depth=8)
    document.create_pixel_layer(
        Image.new("RGBA", (750, 420), (255, 255, 255, 255)),
        name="BoardBg",
        left=0,
        top=0,
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
    source_id = uploaded.json()["source_id"]

    created = client.post(
        "/v1/hifi-replacements/from-psd",
        json={
            "version": 1,
            "project_id": project_id,
            "psd_source_id": source_id,
            "target": target,
            "idempotency_key": "psd-replacement-1",
        },
        headers=HEADERS,
    )

    assert created.status_code == 201, created.text
    assert created.json()["selection_id"] == source_id
    session_id = created.json()["session_id"]
    mapping = client.get(
        f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
    )
    assert mapping.status_code == 200
    assert any(
        str(item["figma_node_id"]).startswith(f"psd-layer:{source_id}:")
        for item in mapping.json()["items"]
        if item["figma_node_id"] is not None
    )
    while True:
        current = client.get(
            f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
        ).json()
        unresolved = next((item for item in current["items"] if item["action"] is None), None)
        if unresolved is None:
            break
        if unresolved["status"] in {"uncertain", "suggested"}:
            action = "retarget"
        elif unresolved["status"] == "hifi_added":
            action = "add_visual"
        elif unresolved["status"] == "blocked":
            action = "exception"
        else:
            action = "keep_old"
        decided = client.post(
            f"/v1/hifi-replacements/{session_id}/mapping-decisions",
            json={
                "version": 1,
                "mapping_revision": current["mapping_revision"],
                "item_id": unresolved["item_id"],
                "action": action,
                **(
                    {"figma_node_id": unresolved["candidates"][0]}
                    if action == "retarget"
                    else {}
                ),
            },
            headers=HEADERS,
        )
        assert decided.status_code == 200, decided.text
    mapping = client.get(
        f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
    )
    built = client.post(
        f"/v1/hifi-replacements/{session_id}/build",
        json={"version": 1, "mapping_revision": mapping.json()["mapping_revision"]},
        headers=HEADERS,
    )
    assert built.status_code == 200, built.text
    assert built.json()["status"] == "review_ready"
    review = client.get(
        f"/v1/hifi-replacements/{session_id}/review", headers=HEADERS
    )
    assert review.status_code == 200, review.text
    assert review.json()["approvable"] is True  # Lossless codes await the Editor stage.
    assert any("pixel_layers_require_equivalence_check" in warning for warning in review.json()["warnings"])
    candidate_sha256 = review.json()["candidate_sha256"]

    def verified(**kwargs: object) -> HifiEditorVerification:
        # The component canvas (750x420) is smaller than the PSD canvas
        # (750x600), so verification targets the cropped viewport region.
        assert kwargs["expected_height"] == 420
        assert Image.open(kwargs["reference"]).size == (750, 420)
        return HifiEditorVerification(
            version=1,
            session_id=session_id,
            candidate_sha256=candidate_sha256,
            editor_found=True,
            editor_version="6.1.4",
            project_opened=True,
            component_opened=True,
            render_captured=True,
            expected_width=int(kwargs["expected_width"]),
            expected_height=int(kwargs["expected_height"]),
            full_frame=False,
            approvable=False,
            warnings=("尚未获得完整画面。",),
        )

    monkeypatch.setattr("figma_to_fgui.api.verify_in_fairygui_editor", verified)
    verification = client.post(
        f"/v1/hifi-replacements/{session_id}/editor-verify", headers=HEADERS
    )
    assert verification.status_code == 200, verification.text
    assert verification.json()["component_opened"] is True
    assert verification.json()["full_frame"] is False
    blocked_approval = client.post(
        f"/v1/hifi-replacements/{session_id}/approve",
        json={
            "version": 1,
            "layout_checked": True,
            "references_checked": True,
            "interactions_checked": True,
            "editor_version": "6.1.4",
            "candidate_sha256": candidate_sha256,
        },
        headers=HEADERS,
    )
    assert blocked_approval.status_code == 409
    assert blocked_approval.json()["detail"]["code"] == "hifi_download_blocked"
    candidate = client.get(
        f"/v1/hifi-replacements/{session_id}/candidate/download", headers=HEADERS
    )
    assert candidate.status_code == 200
    archive = tmp_path / "psd-candidate.zip"
    archive.write_bytes(candidate.content)
    with ZipFile(archive) as package:
        component_bytes = package.read(
            "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml"
        )
        component_xml = component_bytes.decode("utf-8")
        assert "HIFI_PSD_Parity" not in component_xml
        assert "BoardBg" in component_xml
        component = etree.fromstring(component_bytes)
        assert component.attrib["size"] == "750,420"
        assert not component.xpath("./displayList/image[starts-with(@name, 'HIFI_PSD_Default_')]")
        manifest = etree.fromstring(package.read("assets/MyVillage/package.xml"))
        assert not manifest.xpath("./resources/image[starts-with(@name, 'PSD_Default_')]")


def test_psd_editor_verify_compares_the_component_viewport_region(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = _client(tmp_path)
    project_id = _upload_project(client, tmp_path, only_image=True)
    target = _target(client, project_id)
    stripes = Image.new("RGBA", (750, 300), (255, 0, 0, 255))
    stripes.paste(Image.new("RGBA", (750, 300), (0, 0, 255, 255)), (0, 300))
    document = PSDImage.new(mode="RGB", size=(750, 600), depth=8)
    document.create_pixel_layer(stripes, name="BoardBg", left=0, top=0)
    psd = tmp_path / "stripes.psd"
    document.save(psd)
    with psd.open("rb") as content:
        uploaded = client.post(
            "/v1/hifi-sources/psd",
            files={"psd": (psd.name, content, "image/vnd.adobe.photoshop")},
            headers=HEADERS,
        )
    assert uploaded.status_code == 201, uploaded.text
    source_id = uploaded.json()["source_id"]

    created = client.post(
        "/v1/hifi-replacements/from-psd",
        json={
            "version": 1,
            "project_id": project_id,
            "psd_source_id": source_id,
            "target": target,
            "idempotency_key": "psd-viewport-verify-1",
        },
        headers=HEADERS,
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    while True:
        current = client.get(
            f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
        ).json()
        unresolved = next((item for item in current["items"] if item["action"] is None), None)
        if unresolved is None:
            break
        if unresolved["status"] in {"uncertain", "suggested"}:
            action = "retarget"
        elif unresolved["status"] == "hifi_added":
            action = "add_visual"
        elif unresolved["status"] in {"blocked", "fgui_only"}:
            action = "exception"
        else:
            action = "keep_old"
        decided = client.post(
            f"/v1/hifi-replacements/{session_id}/mapping-decisions",
            json={
                "version": 1,
                "mapping_revision": current["mapping_revision"],
                "item_id": unresolved["item_id"],
                "action": action,
                **(
                    {"figma_node_id": unresolved["candidates"][0]}
                    if action == "retarget"
                    else {}
                ),
            },
            headers=HEADERS,
        )
        assert decided.status_code == 200, decided.text
    mapping = client.get(
        f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
    )
    built = client.post(
        f"/v1/hifi-replacements/{session_id}/build",
        json={"version": 1, "mapping_revision": mapping.json()["mapping_revision"]},
        headers=HEADERS,
    )
    assert built.status_code == 200, built.text

    captured: dict[str, object] = {}

    def verified(**kwargs: object) -> HifiEditorVerification:
        captured.update(kwargs)
        return HifiEditorVerification(
            version=1,
            session_id=session_id,
            candidate_sha256=str(kwargs["candidate_sha256"]),
            editor_found=True,
            editor_version="6.1.4",
            project_opened=True,
            component_opened=True,
            render_captured=True,
            expected_width=int(kwargs["expected_width"]),
            expected_height=int(kwargs["expected_height"]),
            full_frame=True,
            approvable=True,
        )

    monkeypatch.setattr("figma_to_fgui.api.verify_in_fairygui_editor", verified)
    verification = client.post(
        f"/v1/hifi-replacements/{session_id}/editor-verify", headers=HEADERS
    )
    assert verification.status_code == 200, verification.text

    # The component canvas (750x420) is a centered viewport inside the PSD
    # canvas (750x600); the reference must be that cropped region.
    composite = tmp_path / "data" / "hifi-sources" / "psd" / source_id / "composite.png"
    assert captured["expected_width"] == 750
    assert captured["expected_height"] == 420
    with Image.open(str(captured["reference"])) as reference, Image.open(composite) as full:
        assert reference.size == (750, 420)
        expected_region = full.crop((0, 90, 750, 510)).convert("RGB")
        assert reference.convert("RGB").tobytes() == expected_region.tobytes()


def test_psd_open_preserves_old_extras_and_blocks_unmatched_psd_visuals(
    tmp_path: Path
) -> None:
    client = _client(tmp_path)
    project_id = _upload_project(client, tmp_path)
    target = _target(client, project_id)
    document = PSDImage.new(mode="RGB", size=(750, 600), depth=8)
    document.create_pixel_layer(
        Image.new("RGBA", (356, 46), (255, 255, 255, 255)),
        name="TitleBar",
        left=48,
        top=30,
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
    source_id = uploaded.json()["source_id"]

    created = client.post(
        "/v1/hifi-replacements/from-psd",
        json={
            "version": 1,
            "project_id": project_id,
            "psd_source_id": source_id,
            "target": target,
            "idempotency_key": "psd-replacement-1",
        },
        headers=HEADERS,
    )

    assert created.status_code == 201, created.text
    assert created.json()["selection_id"] == source_id
    session_id = created.json()["session_id"]
    mapping = client.get(
        f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
    )
    assert mapping.status_code == 200
    assert any(
        str(item["figma_node_id"]).startswith(f"psd-layer:{source_id}:")
        for item in mapping.json()["items"]
        if item["figma_node_id"] is not None
    )
    draft = mapping.json()
    assert draft["unresolved_count"] > 0
    item = next(item for item in draft["items"] if item["old_object_id"])
    assert item["action"] == "keep_old"
    decision = client.post(
        f"/v1/hifi-replacements/{session_id}/mapping-decisions",
        json={"version": 1, "mapping_revision": draft["mapping_revision"],
              "item_id": item["item_id"], "action": "keep_old"}, headers=HEADERS,
    )
    assert decision.status_code == 200, decision.text
    revised_response = client.get(
        f"/v1/hifi-replacements/{session_id}/mapping", headers=HEADERS
    )
    assert revised_response.status_code == 200, revised_response.text
    revised = revised_response.json()
    assert next(i for i in revised["items"] if i["item_id"] == item["item_id"])["action"] == "keep_old"
    assert any(
        not item["old_object_id"]
        and item["figma_node_id"]
        and item["action"] is None
        and item["status"] == "hifi_added"
        for item in draft["items"]
    )
    built = client.post(
        f"/v1/hifi-replacements/{session_id}/build",
        json={"version": 1, "mapping_revision": revised["mapping_revision"]}, headers=HEADERS,
    )
    assert built.status_code == 409
    assert built.json()["detail"]["code"] == "hifi_mapping_incomplete"


def test_psd_renderer_blocker_is_returned_without_generic_retry_error(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from figma_to_fgui.psd_source_store import PsdSourceStoreError

    def blocked(*_args, **_kwargs):
        raise PsdSourceStoreError("psd_stroke_only_raster_unsupported")

    monkeypatch.setattr("figma_to_fgui.hifi_replacement_workflow.HifiReplacementWorkflow.build", blocked)
    response = _client(tmp_path).post(
        "/v1/hifi-replacements/" + "a"*32 + "/build",
        json={"version":1,"mapping_revision":1}, headers=HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "psd_stroke_only_raster_unsupported"
