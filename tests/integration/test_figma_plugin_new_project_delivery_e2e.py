from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from urllib.parse import unquote

from fastapi.testclient import TestClient
from httpx import Response

from figma_to_fgui.api import create_app
from figma_to_fgui.fgui_new_project_validate import validate_project_archive

PLUGIN_HEADERS = {"X-Figma-Plugin-Token": "writer-e2e-token"}
ONE_PIXEL_PNG = (
    Path(__file__).parents[1] / "fixtures" / "fgui-new-project" / "resources" / "one-pixel.png"
).read_bytes()


def _json_keys(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(
            key
            for item_key, item_value in value.items()
            for key in (str(item_key), *_json_keys(item_value))
        )
    if isinstance(value, list):
        return tuple(key for item in value for key in _json_keys(item))
    return ()


class PublicFlow:
    """Record only public paths and JSON field names used by this E2E."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Response:
        self.calls.append((method, path, _json_keys(kwargs.get("json"))))
        return self.client.request(method, path, **kwargs)

    def await_candidate(self, response: Response) -> Response:
        assert response.status_code == 202, response.text
        assert response.json()["status"] in {"converting", "regenerating"}
        deadline = monotonic() + 5
        current = response
        while current.json()["status"] not in {
            "awaiting_review",
            "approved",
            "rejected",
            "failed",
        }:
            assert monotonic() < deadline, current.text
            sleep(0.01)
            current = self.request(
                "GET",
                f"/v1/new-fgui-projects/{current.json()['build_id']}",
                headers=PLUGIN_HEADERS,
            )
        return current


def _download_name(response: Response) -> str:
    disposition = response.headers["content-disposition"]
    encoded = re.search(r"filename\*=[^']*''([^;]+)", disposition, flags=re.IGNORECASE)
    if encoded is not None:
        return unquote(encoded.group(1).strip('"'))
    plain = re.search(r'filename="?([^";]+)', disposition, flags=re.IGNORECASE)
    assert plain is not None, disposition
    return plain.group(1)


def test_public_plugin_writer_delivery_is_approval_gated_and_generation_safe(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=Path("tests/fixtures"),
            rules_path=Path("rules/default/classification.yaml"),
            plugin_access_token=b"writer-e2e-token",
            gateway_secret=b"e" * 32,
        )
    )
    public = PublicFlow(client)

    created = public.request(
        "POST",
        "/v1/figma/selections/uploads",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "idempotency_key": "neutral-image-delivery-e2e"},
    )
    assert created.status_code == 201, created.text
    upload_id = created.json()["upload_id"]
    manifest = {
        "version": 1,
        "display_name": "Neutral Image Delivery",
        "top_level_nodes": [
            {
                "id": "neutral-frame",
                "name": "NeutralFrame",
                "type": "FRAME",
                "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
                "resource_keys": ["neutral_png"],
                "properties": {
                    "export_strategy": "composite_png",
                    "raster_reasons": ["visual_effect"],
                },
            }
        ],
        "resources": [
            {"key": "neutral_png", "mime_type": "image/png", "size": len(ONE_PIXEL_PNG)}
        ],
        "warnings": [
            {"code": "selection-review", "message": "Review the neutral source selection."}
        ],
    }
    accepted = public.request(
        "PUT",
        f"/v1/figma/selections/uploads/{upload_id}/manifest",
        headers=PLUGIN_HEADERS,
        json=manifest,
    )
    assert accepted.status_code == 200, accepted.text
    resource = public.request(
        "PUT",
        f"/v1/figma/selections/uploads/{upload_id}/resources/neutral_png",
        headers={**PLUGIN_HEADERS, "content-type": "image/png"},
        content=ONE_PIXEL_PNG,
    )
    assert resource.status_code == 200, resource.text
    committed = public.request(
        "POST",
        f"/v1/figma/selections/uploads/{upload_id}/commit",
        headers=PLUGIN_HEADERS,
    )
    assert committed.status_code == 200, committed.text
    selection_id = committed.json()["selection_id"]

    started = public.request(
        "POST",
        f"/v1/figma/selections/{selection_id}/new-fgui-projects",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "project_name": "NeutralDelivery"},
    )
    started = public.await_candidate(started)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "awaiting_review", started.text
    first_build_id = started.json()["build_id"]
    first_sha256 = started.json()["sha256"]

    blocked = public.request(
        "GET",
        f"/v1/new-fgui-projects/{first_build_id}/download",
        headers=PLUGIN_HEADERS,
    )
    assert blocked.status_code == 409
    first_review_response = public.request(
        "GET",
        f"/v1/new-fgui-projects/{first_build_id}/review",
        headers=PLUGIN_HEADERS,
    )
    assert first_review_response.status_code == 200, first_review_response.text
    first_review = first_review_response.json()
    assert first_review["generation"] == 1
    assert [
        (item["sourceNodeId"], item["level"], item["reason"], item["blocksApproval"])
        for item in first_review["dispositions"]
    ] == [("neutral-frame", "raster_preserved", "visual_effect", False)]
    assert [item["evidence_kind"] for item in first_review["image_reviews"]] == [
        "source-image"
    ]
    assert {item["evidence_kind"] for item in first_review["component_reviews"]} <= {
        "rendered",
        "structured-summary",
    }
    assert first_review["component_reviews"]
    assert first_review["package_review"] == {
        "package_name": "Generated",
        "fairy_gui_version": "6.1.4",
        "publish_target": "unity",
        "components_added": 1,
        "resources_added": 1,
        "component_names": [first_review["component_reviews"][0]["label"]],
        "resource_names": [first_review["image_reviews"][0]["label"]],
        "resource_closure_valid": True,
        "naming_conflicts": [],
        "integrity_valid": True,
    }
    assert first_review["warning_ids"]
    generated_url = first_review["image_reviews"][0]["generated_asset_url"]
    generated = public.request("GET", generated_url, headers=PLUGIN_HEADERS)
    assert generated.status_code == 200
    assert generated.headers["content-type"].startswith("image/png")
    assert generated.content == ONE_PIXEL_PNG

    projected = first_review
    declared_check = next(item for item in projected["checks"] if item["actionable"])
    assert declared_check["issue_kind"] == "raster-fallback"
    assert declared_check["source_node_id"] == "neutral-frame"
    assert declared_check["allowed_strategies"] == ["preserve-editable"]

    adjusted = public.request(
        "POST",
        f"/v1/new-fgui-projects/{first_build_id}/adjustments",
        headers=PLUGIN_HEADERS,
        json={
            "version": 1,
            "candidate_id": first_build_id,
            "generation": 1,
            "issue_id": declared_check["issue_id"],
            "uir_node_id": declared_check["uir_node_id"],
            "strategy": declared_check["allowed_strategies"][0],
        },
    )
    assert adjusted.status_code == 200, adjusted.text
    assert adjusted.json()["status"] == "adjusting"
    regenerated = public.request(
        "POST",
        f"/v1/new-fgui-projects/{first_build_id}/regenerate",
        headers=PLUGIN_HEADERS,
        json={"version": 1, "generation": 1},
    )
    regenerated = public.await_candidate(regenerated)
    assert regenerated.status_code == 200, regenerated.text
    second_build_id = regenerated.json()["build_id"]
    assert second_build_id != first_build_id
    assert regenerated.json()["status"] == "awaiting_review"
    assert regenerated.json()["sha256"] != first_sha256

    for method, suffix, payload in (
        ("GET", "/review", None),
        (
            "POST",
            "/approve",
            {"version": 1, "generation": 1, "warning_ids": projected["warning_ids"]},
        ),
        ("GET", "/download", None),
    ):
        stale = public.request(
            method,
            f"/v1/new-fgui-projects/{first_build_id}{suffix}",
            headers=PLUGIN_HEADERS,
            **({"json": payload} if payload is not None else {}),
        )
        assert stale.status_code == 409

    second_review_response = public.request(
        "GET",
        f"/v1/new-fgui-projects/{second_build_id}/review",
        headers=PLUGIN_HEADERS,
    )
    assert second_review_response.status_code == 200, second_review_response.text
    second_review = second_review_response.json()
    assert second_review["generation"] == 2
    assert not any(item["issue_kind"] == "raster-fallback" for item in second_review["checks"])
    assert second_review["warning_ids"]
    assert tuple(
        item["id"] for item in second_review["checks"] if item["severity"] == "WARNING"
    ) == tuple(second_review["warning_ids"])
    approved = public.request(
        "POST",
        f"/v1/new-fgui-projects/{second_build_id}/approve",
        headers=PLUGIN_HEADERS,
        json={
            "version": 1,
            "generation": 2,
            "warning_ids": second_review["warning_ids"],
        },
    )
    assert approved.status_code == 200, approved.text
    approved_view = approved.json()
    assert approved_view["status"] == "approved"

    first_download = public.request(
        "GET",
        f"/v1/new-fgui-projects/{second_build_id}/download",
        headers=PLUGIN_HEADERS,
    )
    second_download = public.request(
        "GET",
        f"/v1/new-fgui-projects/{second_build_id}/download",
        headers=PLUGIN_HEADERS,
    )
    assert first_download.status_code == second_download.status_code == 200
    assert first_download.content == second_download.content
    assert len(first_download.content) == approved_view["byte_size"]
    assert hashlib.sha256(first_download.content).hexdigest() == approved_view["sha256"]
    assert _download_name(first_download) == _download_name(second_download)
    assert _download_name(first_download) == approved_view["download_name"]

    archive_path = tmp_path / "downloaded-current-candidate.zip"
    archive_path.write_bytes(first_download.content)
    stored_second = client.app.state.job_store.get_new_project(
        second_build_id, "bundled-figma-plugin"
    )
    assert stored_second.manifest is not None
    assert validate_project_archive(archive_path, stored_second.manifest) == ()

    observed_paths = tuple(path for _method, path, _keys in public.calls)
    observed_fields = {key for _method, _path, keys in public.calls for key in keys}
    assert any(path.endswith("/adjustments") for path in observed_paths)
    assert any(path.endswith("/regenerate") for path in observed_paths)
    assert observed_paths.count(f"/v1/new-fgui-projects/{second_build_id}/download") == 2
    for forbidden_path in (
        "/v1/templates",
        "/v1/projects/from-template",
        "/v1/projects/uploads",
        "/v1/projects/bind",
        "/v1/agents/",
        "/v1/figma/pairings",
    ):
        assert all(forbidden_path not in path for path in observed_paths)
    assert observed_fields.isdisjoint(
        {
            "agent_id",
            "existing_project",
            "existing_resource",
            "pairing_code",
            "project_binding",
            "project_id",
            "template_id",
        }
    )
    assert "template" not in json.dumps(public.calls, ensure_ascii=False).casefold()
