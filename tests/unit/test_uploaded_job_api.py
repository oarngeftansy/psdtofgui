from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from figma_to_fgui.api import create_app
from tests.helpers.zip_projects import write_project_zip


def upload(client: TestClient, archive: Path) -> str:
    with archive.open("rb") as content:
        response = client.post(
            "/v1/projects/uploads",
            files={"project": (archive.name, content, "application/zip")},
        )
    assert response.status_code == 201, response.text
    return str(response.json()["project_id"])


def test_job_uses_the_immutable_uploaded_project_baseline(tmp_path: Path) -> None:
    fixtures_root = tmp_path / "fixtures"
    (fixtures_root / "figma").mkdir(parents=True)
    (fixtures_root / "figma" / "simple-frame.json").write_text(
        Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"), "utf-8"
    )
    legacy_file = fixtures_root / "fgui" / "Sample" / "Panel" / "Panel_Sample_Main.xml"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("<component name='legacy'/>", "utf-8")
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=fixtures_root,
            rules_path=Path("rules/default/classification.yaml"),
            allow_fixture_jobs=True,
        )
    )
    uploaded_content = b"<component name='uploaded'/>"
    archive = write_project_zip(
        tmp_path / "GameUI.zip",
        {
            "Sample/package.xml": b"<package id='sample'><resources/></package>",
            "Sample/Panel/Panel_Sample_Main.xml": uploaded_content,
        },
    )

    project_id = upload(client, archive)
    response = client.post(
        f"/v1/projects/{project_id}/jobs",
        json={
            "version": 1,
            "project_id": project_id,
            "fixture_name": "simple-frame.json",
            "package_name": "Sample",
        },
    )

    assert response.status_code == 200, response.text
    created = response.json()
    assert created["status"] == "ready_for_review"
    assert "project_fingerprint" not in created
    changes = client.get(
        f"/v1/jobs/{created['job_id']}/designer-preview?details=advanced"
    ).json()["details"]["files"]
    change = next(item for item in changes if item["relative_path"].endswith("/Panel_Sample_Main.xml"))
    assert change["operation"] == "replace"
    assert change["before_sha256"] == hashlib.sha256(uploaded_content).hexdigest()
    assert client.post(
        "/v1/agents/register", json={"version": 1, "agent_id": "agent-1", "name": "Desk"}
    ).status_code == 200
    assert client.post(
        "/v1/projects/bind",
        json={"version": 1, "project_id": project_id, "agent_id": "agent-1"},
    ).status_code == 200
    assert client.post(f"/v1/jobs/{created['job_id']}/approve").status_code == 200
    assignment = client.get("/v1/agents/agent-1/assignments/next")
    assert assignment.status_code == 200
    assert len(assignment.json()["project_fingerprint"]) == 64
    assert assignment.json()["package_names"] == ["Sample"]

    legacy_file.write_text("<component name='mutated legacy fixture'/>", "utf-8")
    second_project_id = upload(client, archive)
    second = client.post(
        f"/v1/projects/{second_project_id}/jobs",
        json={
            "version": 1,
            "project_id": second_project_id,
            "fixture_name": "simple-frame.json",
            "package_name": "Sample",
        },
    )

    assert second.status_code == 200, second.text
    second_changes = client.get(
        f"/v1/jobs/{second.json()['job_id']}/designer-preview?details=advanced"
    ).json()["details"]["files"]
    second_change = next(
        item for item in second_changes if item["relative_path"].endswith("/Panel_Sample_Main.xml")
    )
    assert second_change["before_sha256"] == hashlib.sha256(uploaded_content).hexdigest()


def test_designer_preview_hides_details_and_rejection_is_idempotent(tmp_path: Path) -> None:
    fixtures_root = tmp_path / "fixtures"
    (fixtures_root / "figma").mkdir(parents=True)
    (fixtures_root / "figma" / "simple-frame.json").write_text(
        Path("tests/fixtures/figma/simple-frame.json").read_text("utf-8"), "utf-8"
    )
    client = TestClient(
        create_app(
            data_dir=tmp_path / "data",
            fixtures_root=fixtures_root,
            rules_path=Path("rules/default/classification.yaml"),
            allow_fixture_jobs=True,
        )
    )
    project_id = upload(
        client,
        write_project_zip(
            tmp_path / "GameUI.zip",
            {
                "Sample/package.xml": b"<package id='sample'><resources/></package>",
                "Sample/Panel/Panel_Sample_Main.xml": b"<component/>",
            },
        ),
    )
    created = client.post(
        f"/v1/projects/{project_id}/jobs",
        json={
            "version": 1,
            "project_id": project_id,
            "fixture_name": "simple-frame.json",
            "package_name": "Sample",
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job_id"]

    normal = client.get(f"/v1/jobs/{job_id}/designer-preview")
    assert normal.status_code == 200, normal.text
    assert "界面：Panel_Sample_Main" in {
        change["label"] for change in normal.json()["changes"]
    }
    for forbidden in ("sha256", "changeset", "resource_id", "<component", "relative_path"):
        assert forbidden not in normal.text

    advanced = client.get(f"/v1/jobs/{job_id}/designer-preview?details=advanced")
    assert advanced.status_code == 200, advanced.text
    panel = next(
        item
        for item in advanced.json()["details"]["files"]
        if item["relative_path"] == "Sample/Panel/Panel_Sample_Main.xml"
    )
    assert panel["before_xml"] == "<component/>"

    rejected = client.post(f"/v1/jobs/{job_id}/reject")
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert client.post(f"/v1/jobs/{job_id}/reject").json() == rejected.json()
    assert client.post(f"/v1/jobs/{job_id}/approve").status_code == 409
