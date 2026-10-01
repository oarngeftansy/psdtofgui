from __future__ import annotations

import hashlib
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient
from httpx import Response

from figma_to_fgui.api import create_app

PLUGIN_HEADERS = {"X-Figma-Plugin-Token": "writer-token"}
ONE_PIXEL_PNG = (
    Path(__file__).parents[1] / "fixtures" / "fgui-new-project" / "resources" / "one-pixel.png"
).read_bytes()


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"writer-token",
            gateway_secret=b"g" * 32,
        )
    )


def _await_candidate(
    client: TestClient, response: Response, headers: dict[str, str] | None = None
) -> Response:
    assert response.status_code == 202, response.text
    assert response.json()["status"] in {"converting", "regenerating"}
    deadline = monotonic() + 5
    current = response
    while current.json()["status"] not in {"awaiting_review", "approved", "rejected", "failed"}:
        assert monotonic() < deadline, current.text
        sleep(0.01)
        current = client.get(
            f"/v1/new-fgui-projects/{current.json()['build_id']}",
            headers=headers or PLUGIN_HEADERS,
        )
    return current


def _upload_neutral_selection(
    client: TestClient,
    key: str = "writer-neutral",
    *,
    headers: dict[str, str] | None = None,
    node_type: str = "FRAME",
) -> str:
    headers = headers or PLUGIN_HEADERS
    created = client.post(
        "/v1/figma/selections/uploads",
        headers=headers,
        json={"version": 1, "idempotency_key": key},
    )
    assert created.status_code == 201, created.text
    upload_id = created.json()["upload_id"]
    manifest = {
        "version": 1,
        "display_name": "Writer Neutral",
        "top_level_nodes": [
            {
                "id": "frame-1",
                "name": "WriterNeutral",
                "type": node_type,
                "bounds": {"x": 0, "y": 0, "width": 100, "height": 80},
            }
        ],
    }
    accepted = client.put(
        f"/v1/figma/selections/uploads/{upload_id}/manifest",
        headers=headers,
        json=manifest,
    )
    assert accepted.status_code == 200, accepted.text
    committed = client.post(f"/v1/figma/selections/uploads/{upload_id}/commit", headers=headers)
    assert committed.status_code == 200, committed.text
    return str(committed.json()["selection_id"])


def _upload_image_selection(client: TestClient, *, raster: bool = False) -> str:
    created = client.post(
        "/v1/figma/selections/uploads",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "idempotency_key": "writer-image"},
    )
    upload_id = created.json()["upload_id"]
    manifest = {
        "version": 1,
        "display_name": "Writer Image",
        "top_level_nodes": [
            {
                "id": "image-1",
                "name": "WriterImage",
                "type": "FRAME",
                "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                "resource_keys": ["hero"],
                **(
                    {
                        "properties": {
                            "export_strategy": "composite_png",
                            "raster_reasons": ["visual_effect"],
                        }
                    }
                    if raster
                    else {}
                ),
            }
        ],
        "resources": [{"key": "hero", "mime_type": "image/png", "size": len(ONE_PIXEL_PNG)}],
    }
    assert (
        client.put(
            f"/v1/figma/selections/uploads/{upload_id}/manifest",
            headers=PLUGIN_HEADERS,
            json=manifest,
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/v1/figma/selections/uploads/{upload_id}/resources/hero",
            headers={**PLUGIN_HEADERS, "content-type": "image/png"},
            content=ONE_PIXEL_PNG,
        ).status_code
        == 200
    )
    committed = client.post(
        f"/v1/figma/selections/uploads/{upload_id}/commit", headers=PLUGIN_HEADERS
    )
    assert committed.status_code == 200
    return str(committed.json()["selection_id"])


def test_build_review_approve_and_download_are_owner_gated(tmp_path: Path) -> None:
    client = _client(tmp_path)
    selection_id = _upload_neutral_selection(client)

    started = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Inventory"},
    )
    started = _await_candidate(client, started)
    assert started.status_code == 200, started.text
    assert "path" not in started.text.lower()
    build_id = started.json()["build_id"]
    candidate = client.get(f"/v1/new-fgui-projects/{build_id}", headers=PLUGIN_HEADERS)
    assert candidate.status_code == 200
    assert candidate.json()["status"] == "awaiting_review"
    blocked = client.get(f"/v1/new-fgui-projects/{build_id}/download", headers=PLUGIN_HEADERS)
    assert blocked.status_code == 409

    review = client.get(f"/v1/new-fgui-projects/{build_id}/review", headers=PLUGIN_HEADERS)
    assert review.status_code == 200, review.text
    assert review.json()["generation"] == 1
    approved = client.post(
        f"/v1/new-fgui-projects/{build_id}/approve",
        headers=PLUGIN_HEADERS,
        json={
            "version": 1,
            "generation": 1,
            "warning_ids": review.json()["warning_ids"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert (
        client.post(
            f"/v1/new-fgui-projects/{build_id}/approve",
            headers=PLUGIN_HEADERS,
            json={
                "version": 1,
                "generation": 1,
                "warning_ids": review.json()["warning_ids"],
            },
        ).status_code
        == 200
    )
    downloaded = client.get(
        f"/v1/new-fgui-projects/{build_id}/download",
        headers={**PLUGIN_HEADERS, "Origin": "null"},
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("application/zip")
    exposed = {
        value.strip().lower()
        for value in downloaded.headers["access-control-expose-headers"].split(",")
    }
    assert {"content-disposition", "content-length"} <= exposed
    fresh = _await_candidate(client, client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Inventory"},
    ))
    assert fresh.json()["build_id"] != build_id
    assert fresh.json()["generation"] == 2
    assert fresh.json()["status"] == "awaiting_review"
    assert (
        client.post(
            f"/v1/new-fgui-projects/{build_id}/reject",
            headers=PLUGIN_HEADERS,
            json={"version": 1, "generation": 1},
        ).status_code
        == 409
    )


def test_blocked_component_still_returns_actionable_review(tmp_path: Path) -> None:
    client = _client(tmp_path)
    selection_id = _upload_neutral_selection(
        client, key="blocked-review", node_type="INSTANCE"
    )

    started = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "BlockedReview"},
    )
    candidate = _await_candidate(client, started)

    assert candidate.json()["status"] == "awaiting_review"
    assert candidate.json()["artifact_ready"] is False
    build_id = candidate.json()["build_id"]
    review = client.get(
        f"/v1/new-fgui-projects/{build_id}/review", headers=PLUGIN_HEADERS
    )
    assert review.status_code == 200
    assert review.json()["dispositions"][0] | {"id": "stable"} == {
        "version": 1,
        "id": "stable",
        "sourceNodeId": "frame-1",
        "sourceName": "WriterNeutral",
        "sourceType": "INSTANCE",
        "level": "blocked",
        "reason": "component_definition_missing",
        "defaultStrategy": None,
        "allowedStrategies": [],
        "visualImpact": "may_differ",
        "editabilityImpact": "unchanged",
        "componentImpact": "instance_not_reusable",
        "blocksApproval": True,
        "details": None,
    }
    assert review.json()["approvable"] is False
    assert (
        client.post(
            f"/v1/new-fgui-projects/{build_id}/approve",
            headers=PLUGIN_HEADERS,
            json={"version": 1, "generation": 1, "warning_ids": []},
        ).status_code
        == 409
    )


def test_request_is_idempotent_strict_and_plugin_only(tmp_path: Path) -> None:
    client = _client(tmp_path)
    selection_id = _upload_neutral_selection(client)
    url = f"/v1/figma/selections/{selection_id}/new-fgui-projects"

    assert client.post(url, json={"version": 1, "project_name": "Inventory"}).status_code == 401
    duplicate = client.post(
        url,
        headers={**PLUGIN_HEADERS, "content-type": "application/json"},
        content=b'{"version":1,"project_name":"One","project_name":"Two"}',
    )
    assert duplicate.status_code == 422
    first = client.post(
        url,
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Inventory"},
    )
    second = client.post(
        url,
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Inventory"},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["build_id"] == second.json()["build_id"]


def test_new_project_json_body_is_bounded_before_parsing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    selection_id = _upload_neutral_selection(client)
    response = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers={**PLUGIN_HEADERS, "content-type": "application/json"},
        content=b'{"version":1,"project_name":"' + b"x" * (64 * 1024) + b'"}',
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "new_project_request_too_large"


def test_image_review_joins_source_resource_by_stable_key(tmp_path: Path) -> None:
    client = _client(tmp_path)
    selection_id = _upload_image_selection(client)
    started = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Images"},
    )
    started = _await_candidate(client, started)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "awaiting_review"
    review = client.get(
        f"/v1/new-fgui-projects/{started.json()['build_id']}/review",
        headers=PLUGIN_HEADERS,
    )
    assert review.status_code == 200, review.text
    image = review.json()["image_reviews"][0]
    assert image["evidence_kind"] == "source-image"
    assert image["source_preview_url"] == f"/v1/figma/selections/{selection_id}/resources/hero"
    source = client.get(image["source_preview_url"], headers=PLUGIN_HEADERS)
    assert source.status_code == 200
    assert source.content == ONE_PIXEL_PNG
    assert hashlib.sha256(source.content).hexdigest() == hashlib.sha256(ONE_PIXEL_PNG).hexdigest()
    assert image["crop_bounds_match"] is True
    assert image["transparency_preserved"] is True
    generated = client.get(image["generated_asset_url"], headers=PLUGIN_HEADERS)
    assert generated.status_code == 200, generated.text
    assert generated.content == ONE_PIXEL_PNG


def test_rejection_is_terminal_and_warning_acknowledgement_is_exact(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    selection_id = _upload_image_selection(client, raster=True)
    started = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Rejected"},
    )
    started = _await_candidate(client, started)
    build_id = started.json()["build_id"]
    wrong = client.post(
        f"/v1/new-fgui-projects/{build_id}/approve",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "generation": 1, "warning_ids": ["review:0000000000000000"]},
    )
    assert wrong.status_code == 409
    rejected = client.post(
        f"/v1/new-fgui-projects/{build_id}/reject",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "generation": 1},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert (
        client.get(f"/v1/new-fgui-projects/{build_id}/download", headers=PLUGIN_HEADERS).status_code
        == 409
    )
    retried = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Rejected"},
    )
    retried = _await_candidate(client, retried)
    assert retried.status_code == 200
    assert retried.json()["build_id"] != build_id
    assert retried.json()["generation"] == 2
    assert (
        client.post(
            f"/v1/new-fgui-projects/{build_id}/approve",
            headers=PLUGIN_HEADERS,
            json={"version": 1, "generation": 1, "warning_ids": []},
        ).status_code
        == 409
    )


def test_artifact_tamper_fails_closed_and_restart_reconciles_valid_candidate(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    client = _client(tmp_path)
    selection_id = _upload_neutral_selection(client)
    first = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Restarted"},
    )
    first = _await_candidate(client, first)
    assert first.status_code == 200
    build_id = first.json()["build_id"]

    restarted = TestClient(
        create_app(
            data_dir=data_dir,
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"writer-token",
            gateway_secret=b"g" * 32,
        )
    )
    assert (
        restarted.get(
            f"/v1/new-fgui-projects/{build_id}/review", headers=PLUGIN_HEADERS
        ).status_code
        == 200
    )
    artifact = next((data_dir / "new-fgui-projects" / "artifacts" / build_id).glob("*.zip"))
    artifact.write_bytes(b"tampered")
    assert (
        restarted.get(
            f"/v1/new-fgui-projects/{build_id}/review", headers=PLUGIN_HEADERS
        ).status_code
        == 409
    )
    assert (
        restarted.get(
            f"/v1/new-fgui-projects/{build_id}/download", headers=PLUGIN_HEADERS
        ).status_code
        == 409
    )


def test_adjustment_regenerates_from_immutable_selection_and_invalidates_old_candidate(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    selection_id = _upload_image_selection(client, raster=True)
    first = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Adjusted"},
    )
    first = _await_candidate(client, first)
    old_build_id = first.json()["build_id"]
    old_sha = first.json()["sha256"]
    first_review = client.get(
        f"/v1/new-fgui-projects/{old_build_id}/review", headers=PLUGIN_HEADERS
    ).json()
    assert first_review["dispositions"] == [
        {
            "version": 1,
            "id": first_review["dispositions"][0]["id"],
            "sourceNodeId": "image-1",
            "sourceName": "WriterImage",
            "sourceType": "FRAME",
            "level": "raster_preserved",
            "reason": "visual_effect",
            "defaultStrategy": "rasterize-subtree",
            "allowedStrategies": ["rasterize-subtree"],
            "visualImpact": "visual_preserved",
            "editabilityImpact": "subtree_not_editable",
            "componentImpact": "unchanged",
            "blocksApproval": False,
            "details": None,
        }
    ]
    check = next(
        item
        for item in first_review["checks"]
        if item["issue_kind"] == "raster-fallback"
    )
    assert check["source_node_id"] == "image-1"

    adjusted = client.post(
        f"/v1/new-fgui-projects/{old_build_id}/adjustments",
        headers=PLUGIN_HEADERS,
        json={
            "version": 1,
            "candidate_id": old_build_id,
            "generation": 1,
            "issue_id": check["issue_id"],
            "uir_node_id": check["uir_node_id"],
            "strategy": "preserve-editable",
        },
    )
    assert adjusted.status_code == 200, adjusted.text
    assert adjusted.json()["status"] == "adjusting"
    regenerated = client.post(
        f"/v1/new-fgui-projects/{old_build_id}/regenerate",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "generation": 1},
    )
    regenerated = _await_candidate(client, regenerated)
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["status"] == "awaiting_review", regenerated.text
    assert regenerated.json()["sha256"] != old_sha
    new_build_id = regenerated.json()["build_id"]
    assert new_build_id != old_build_id
    new_review = client.get(f"/v1/new-fgui-projects/{new_build_id}/review", headers=PLUGIN_HEADERS)
    assert new_review.status_code == 200
    assert new_review.json()["generation"] == 2
    assert not any(
        item["issue_kind"] == "raster-fallback"
        for item in new_review.json()["checks"]
    )
    assert (
        client.get(
            f"/v1/new-fgui-projects/{old_build_id}/review", headers=PLUGIN_HEADERS
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/v1/new-fgui-projects/{old_build_id}/download", headers=PLUGIN_HEADERS
        ).status_code
        == 409
    )


def test_private_build_failure_publishes_no_archive(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path)
    selection_id = _upload_neutral_selection(client)

    def private_failure(**_kwargs: object) -> object:
        raise RuntimeError("C:/private/secret-marker")

    monkeypatch.setattr("figma_to_fgui.api.build_selection_new_project", private_failure)
    failed = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "Failure"},
    )
    failed = _await_candidate(client, failed)
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert "secret-marker" not in failed.text
    build_id = failed.json()["build_id"]
    assert (
        list((tmp_path / "data" / "new-fgui-projects" / "artifacts" / build_id).glob("*.zip")) == []
    )
    assert (
        client.get(f"/v1/new-fgui-projects/{build_id}/download", headers=PLUGIN_HEADERS).status_code
        == 409
    )


def test_component_definition_failure_is_reviewable_and_non_downloadable(tmp_path: Path) -> None:
    client = _client(tmp_path)
    selection_id = _upload_neutral_selection(client, key="component-missing", node_type="INSTANCE")
    failed = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "ComponentMissing"},
    )
    failed = _await_candidate(client, failed)
    assert failed.status_code == 200
    assert failed.json()["status"] == "awaiting_review"
    assert failed.json()["artifact_ready"] is False
    review = client.get(
        f"/v1/new-fgui-projects/{failed.json()['build_id']}/review",
        headers=PLUGIN_HEADERS,
    )
    assert review.status_code == 200
    assert [item["reason"] for item in review.json()["dispositions"]] == [
        "component_definition_missing"
    ]
    assert (
        client.get(
            f"/v1/new-fgui-projects/{failed.json()['build_id']}/download",
            headers=PLUGIN_HEADERS,
        ).status_code
        == 409
    )


def test_other_paired_device_cannot_guess_selection_or_build(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "paired-data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_secret=b"s" * 32,
        )
    )

    def pair(name: str) -> dict[str, str]:
        issued = client.post("/v1/figma/pairings").json()
        exchanged = client.post(
            "/v1/figma/pairings/exchange",
            json={"version": 1, "code": issued["code"], "device_name": name},
        ).json()
        return {"Authorization": f"Bearer {exchanged['credential']}"}

    owner = pair("Owner")
    other = pair("Other")
    selection_id = _upload_neutral_selection(client, key="paired-owner", headers=owner)
    started = client.post(
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=owner,
        json={"version": 1, "project_name": "Private"},
    )
    started = _await_candidate(client, started, owner)
    assert started.status_code == 200
    build_id = started.json()["build_id"]
    assert (
        client.post(
            f"/v1/figma/selections/{selection_id}/new-fgui-projects",
            headers=other,
            json={"version": 1, "project_name": "Private"},
        ).status_code
        == 404
    )
    assert client.get(f"/v1/new-fgui-projects/{build_id}", headers=other).status_code == 404
