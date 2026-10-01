from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from figma_to_fgui.models import Diagnostic
from figma_to_fgui.service_contracts import (
    AgentRegistration,
    ApplyResult,
    ApplyStatus,
    ChangeBundle,
    ChangeFile,
    FileOperation,
    JobCreate,
    JobStatus,
    JobView,
    NewFguiProjectRequest,
    NewFguiProjectView,
    NewProjectAdjustmentRequest,
    NewProjectAdjustmentStrategy,
    NewProjectApprovalRequest,
    NewProjectConversionDisposition,
    ProjectBinding,
    ProjectPackageStage,
    ProjectPackageView,
)


def test_change_file_rejects_unsafe_paths_and_delete() -> None:
    with pytest.raises(ValidationError):
        ChangeFile(
            operation="delete",
            relative_path="../outside.xml",
            after_sha256="0" * 64,
            content_b64="",
        )


def test_replace_requires_before_hash() -> None:
    with pytest.raises(ValidationError):
        ChangeFile(
            operation=FileOperation.REPLACE,
            relative_path="Sample/Main.xml",
            after_sha256="0" * 64,
            content_b64="",
        )


def test_create_forbids_before_hash() -> None:
    with pytest.raises(ValidationError):
        ChangeFile(
            operation=FileOperation.CREATE,
            relative_path="Sample/Main.xml",
            before_sha256="1" * 64,
            after_sha256="0" * 64,
            content_b64="",
        )


def test_bundle_round_trip_is_versioned() -> None:
    bundle = ChangeBundle(job_id="job-1", project_id="project-1", files=())
    assert ChangeBundle.model_validate_json(bundle.model_dump_json()) == bundle
    assert bundle.version == 1


def test_protocol_models_are_versioned_and_frozen() -> None:
    models = (
        AgentRegistration(agent_id="agent-1", name="Desk"),
        ProjectBinding(project_id="project-1", agent_id="agent-1"),
        JobCreate(fixture_name="simple-frame.json", project_id="project-1", package_name="Sample"),
        JobView(
            job_id="job-1",
            project_id="project-1",
            status=JobStatus.CREATED,
            diagnostics=(Diagnostic(code="ok", severity="INFO", message="ok"),),
        ),
        ApplyResult(
            job_id="job-1",
            agent_id="agent-1",
            project_id="project-1",
            status=ApplyStatus.APPLIED,
        ),
    )
    assert all(item.version == 1 for item in models)
    with pytest.raises(ValidationError):
        AgentRegistration(agent_id="agent-1", name="Desk", extra="forbidden")


def test_package_screenshot_reason_is_stripped_and_blank_becomes_absent() -> None:
    package = ProjectPackageView(
        job_id="job-1",
        status=ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
        stage=ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
        progress=70,
        screenshot_reason="  Need screenshot.  ",
    )
    blank = package.model_copy(update={"screenshot_reason": "   "})
    blank = ProjectPackageView.model_validate(blank.model_dump())

    assert package.screenshot_reason == "Need screenshot."
    assert blank.screenshot_reason is None


def test_new_project_writer_contract_is_strict_and_path_free() -> None:
    request = NewFguiProjectRequest(version=1, project_name="Inventory")
    view = NewFguiProjectView(
        build_id="a" * 32,
        status="awaiting_review",
        stage="awaiting_review",
        progress=100,
        download_name="Inventory-FairyGUI.zip",
        sha256="b" * 64,
        byte_size=42,
    )

    assert request.project_name == "Inventory"
    assert "path" not in view.model_dump(mode="json")
    with pytest.raises(ValidationError):
        NewFguiProjectRequest(version=True, project_name="Inventory")
    with pytest.raises(ValidationError):
        NewFguiProjectRequest(version=1, project_name="../Inventory")
    with pytest.raises(ValidationError):
        NewFguiProjectRequest(version=1, project_name="Inventory", extra="forbidden")
    with pytest.raises(ValidationError):
        NewFguiProjectView(
            build_id="a" * 32,
            status="awaiting_review",
            stage="awaiting_review",
            progress=True,
        )


def test_adjustment_and_approval_requests_bind_candidate_generation() -> None:
    adjustment = NewProjectAdjustmentRequest(
        version=1,
        candidate_id="a" * 32,
        generation=2,
        issue_id="issue-1",
        uir_node_id="node-1",
        strategy=NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE,
    )
    approval = NewProjectApprovalRequest(version=1, generation=2, warning_ids=("warning-1",))

    assert adjustment.strategy == "rasterize-subtree"
    assert approval.warning_ids == ("warning-1",)
    with pytest.raises(ValidationError):
        NewProjectAdjustmentRequest(
            version=1,
            candidate_id="a" * 32,
            generation=2,
            selection_fingerprint="b" * 64,
            issue_id="issue-1",
            uir_node_id="node-1",
            strategy=NewProjectAdjustmentStrategy.RASTERIZE_SUBTREE,
        )
    with pytest.raises(ValidationError):
        NewProjectAdjustmentRequest(
            version=1,
            candidate_id="a" * 32,
            generation=2,
            issue_id="issue-1",
            uir_node_id="node-1",
            strategy="arbitrary",
        )


def test_conversion_disposition_contract_is_closed() -> None:
    payload = {
        "version": 1,
        "id": "disposition:0011223344556677",
        "sourceNodeId": "node-17",
        "sourceName": "Rank label",
        "sourceType": "TEXT",
        "level": "editable_risk",
        "reason": "rich_text_runs",
        "defaultStrategy": "preserve-editable",
        "allowedStrategies": ["preserve-editable", "rasterize-subtree"],
        "visualImpact": "may_differ",
        "editabilityImpact": "unchanged",
        "componentImpact": "unchanged",
        "blocksApproval": False,
        "details": {
            "runCount": 2,
            "preservedProperties": ["content"],
            "unsupportedProperties": ["fontSize"],
        },
    }
    item = NewProjectConversionDisposition.model_validate_json(json.dumps(payload))

    assert item.model_dump(mode="json", by_alias=True)["details"] == {
        "runCount": 2,
        "preservedProperties": ["content"],
        "unsupportedProperties": ["fontSize"],
    }
    for update in (
        {"level": "unknown"},
        {"reason": "unknown"},
        {"allowedStrategies": ["rasterize-subtree", "rasterize-subtree"]},
        {"defaultStrategy": "include-contained-definition"},
        {"blocksApproval": True},
        {"privateFact": "forbidden"},
        {
            "details": {
                "runCount": 2,
                "preservedProperties": ["content", "content"],
                "unsupportedProperties": ["fontSize"],
            }
        },
        {
            "details": {
                "runCount": 10_000_000_000,
                "preservedProperties": ["content"],
                "unsupportedProperties": ["fontSize"],
            }
        },
        {
            "details": {
                "runCount": 2,
                "preservedProperties": ["content"],
                "unsupportedProperties": ["fontSize"],
                "privateFact": "forbidden",
            }
        },
    ):
        with pytest.raises(ValidationError):
            NewProjectConversionDisposition.model_validate_json(json.dumps(payload | update))

    missing_details = dict(payload)
    del missing_details["details"]
    with pytest.raises(ValidationError):
        NewProjectConversionDisposition.model_validate_json(json.dumps(missing_details))

    snake_case_details = {
        **payload,
        "details": {
            "run_count": 2,
            "preserved_properties": ["content"],
            "unsupported_properties": ["fontSize"],
        },
    }
    with pytest.raises(ValidationError):
        NewProjectConversionDisposition.model_validate_json(
            json.dumps(snake_case_details)
        )


def test_legacy_rich_text_raster_risk_may_have_null_details() -> None:
    payload = {
        "version": 1,
        "id": "disposition:0011223344556677",
        "sourceNodeId": "node-17",
        "sourceName": "Rank label",
        "sourceType": "TEXT",
        "level": "editable_risk",
        "reason": "rich_text_runs",
        "defaultStrategy": "rasterize-subtree",
        "allowedStrategies": ["rasterize-subtree", "preserve-editable"],
        "visualImpact": "visual_preserved",
        "editabilityImpact": "text_not_editable",
        "componentImpact": "unchanged",
        "blocksApproval": False,
        "details": None,
    }

    item = NewProjectConversionDisposition.model_validate_json(json.dumps(payload))

    assert item.details is None


def test_fabricated_non_text_legacy_rich_text_risk_rejects_null_details() -> None:
    payload = {
        "version": 1,
        "id": "disposition:0011223344556677",
        "sourceNodeId": "node-17",
        "sourceName": "Fabricated frame",
        "sourceType": "FRAME",
        "level": "editable_risk",
        "reason": "rich_text_runs",
        "defaultStrategy": "rasterize-subtree",
        "allowedStrategies": ["rasterize-subtree", "preserve-editable"],
        "visualImpact": "visual_preserved",
        "editabilityImpact": "text_not_editable",
        "componentImpact": "unchanged",
        "blocksApproval": False,
        "details": None,
    }

    with pytest.raises(ValidationError):
        NewProjectConversionDisposition.model_validate_json(json.dumps(payload))


def test_reviewed_rich_text_preserve_editable_risk_requires_details() -> None:
    payload = {
        "version": 1,
        "id": "disposition:0011223344556677",
        "sourceNodeId": "node-17",
        "sourceName": "Rank label",
        "sourceType": "TEXT",
        "level": "editable_risk",
        "reason": "rich_text_runs",
        "defaultStrategy": "preserve-editable",
        "allowedStrategies": ["preserve-editable", "rasterize-subtree"],
        "visualImpact": "may_differ",
        "editabilityImpact": "unchanged",
        "componentImpact": "unchanged",
        "blocksApproval": False,
        "details": None,
    }

    with pytest.raises(ValidationError):
        NewProjectConversionDisposition.model_validate_json(json.dumps(payload))


def test_non_rich_text_disposition_requires_null_details() -> None:
    payload = {
        "version": 1,
        "id": "disposition:0011223344556677",
        "sourceNodeId": "node-17",
        "sourceName": "Card",
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

    item = NewProjectConversionDisposition.model_validate_json(json.dumps(payload))
    assert item.details is None

    payload["details"] = {
        "runCount": 2,
        "preservedProperties": ["content"],
        "unsupportedProperties": ["fontSize"],
    }
    with pytest.raises(ValidationError):
        NewProjectConversionDisposition.model_validate_json(json.dumps(payload))
