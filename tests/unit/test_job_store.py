from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from figma_to_fgui import api
from figma_to_fgui import job_store as job_store_module
from figma_to_fgui.job_store import (
    InvalidTransition,
    JobStore,
    PackageConsentConflict,
)
from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.service_contracts import (
    AgentRegistration,
    ApplyResult,
    ApplyStatus,
    JobStatus,
    JobView,
    NewFguiProjectStage,
    ProjectBinding,
    ProjectPackageStage,
    ProjectPackageView,
)


def test_new_project_progress_is_persisted_and_monotonic(store: JobStore) -> None:
    attempt = store.begin_new_project(
        build_id="a" * 32, owner_device_id="device", selection_id="b" * 32,
        selection_fingerprint="c" * 64, request_identity="d" * 64,
        project_name="Writer", lease_owner="worker",
    )
    checking = store.advance_new_project_stage(
        build_id=attempt.project.view.build_id, owner_device_id="device",
        lease_owner="worker", stage=NewFguiProjectStage.CHECKING, progress=55,
    )
    packaging = store.advance_new_project_stage(
        build_id=attempt.project.view.build_id, owner_device_id="device",
        lease_owner="worker", stage=NewFguiProjectStage.PACKAGING, progress=80,
    )

    assert (checking.stage, checking.progress) == (NewFguiProjectStage.CHECKING, 55)
    assert (packaging.stage, packaging.progress) == (NewFguiProjectStage.PACKAGING, 80)
    assert store.get_new_project("a" * 32, "device").view == packaging
    with pytest.raises(InvalidTransition, match="monotonic"):
        store.advance_new_project_stage(
            build_id="a" * 32, owner_device_id="device", lease_owner="worker",
            stage=NewFguiProjectStage.PACKAGING, progress=80,
        )


@pytest.mark.parametrize(
    ("stage", "progress"),
    [
        (NewFguiProjectStage.CHECKING, 55),
        (NewFguiProjectStage.PACKAGING, 80),
    ],
)
def test_expired_new_project_intermediate_stage_is_recovered(
    tmp_path: Path,
    stage: NewFguiProjectStage,
    progress: int,
) -> None:
    now = [datetime(2026, 8, 21, tzinfo=UTC)]
    store = JobStore(
        tmp_path / "jobs.db",
        clock=lambda: now[0],
        package_lease_duration=timedelta(seconds=1),
    )
    store.initialize()
    store.begin_new_project(
        build_id="a" * 32,
        owner_device_id="device",
        selection_id="b" * 32,
        selection_fingerprint="c" * 64,
        request_identity="d" * 64,
        project_name="Writer",
        lease_owner="worker",
    )
    store.advance_new_project_stage(
        build_id="a" * 32,
        owner_device_id="device",
        lease_owner="worker",
        stage=stage,
        progress=progress,
    )
    now[0] += timedelta(seconds=2)

    assert store.recover_expired_new_projects() == 1
    recovered = store.get_new_project("a" * 32, "device")
    assert recovered.view.stage == NewFguiProjectStage.FAILED
    assert recovered.lease_owner is None
    assert store.recover_expired_new_projects() == 0


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    result = JobStore(tmp_path / "jobs.db")
    result.initialize()
    return result


def ready_job(job_id: str = "job-1", project_id: str = "project-1") -> JobView:
    return JobView(
        job_id=job_id,
        project_id=project_id,
        status=JobStatus.READY_FOR_REVIEW,
        artifact_sha256="a" * 64,
    )


def register_bound_agent(store: JobStore) -> None:
    store.register_agent(AgentRegistration(agent_id="agent-a", name="A"))
    store.bind_project(ProjectBinding(project_id="project-1", agent_id="agent-a"))


def test_only_bound_agent_claims_approved_job(store: JobStore) -> None:
    register_bound_agent(store)
    store.register_agent(AgentRegistration(agent_id="agent-b", name="B"))
    store.create_job(ready_job())
    store.approve_job("job-1")
    assert store.claim_next("agent-b") is None
    assignment = store.claim_next("agent-a")
    assert assignment is not None
    assert assignment.status is JobStatus.APPLYING


def test_approved_job_can_only_be_claimed_once(store: JobStore) -> None:
    register_bound_agent(store)
    store.create_job(ready_job())
    store.approve_job("job-1")
    assert store.claim_next("agent-a") is not None
    assert store.claim_next("agent-a") is None


def test_approval_is_idempotent(store: JobStore) -> None:
    register_bound_agent(store)
    store.create_job(ready_job())
    first = store.approve_job("job-1")
    second = store.approve_job("job-1")
    assert first == second
    assert second.status is JobStatus.APPROVED


def test_job_with_error_cannot_be_approved(store: JobStore) -> None:
    store.create_job(
        JobView(
            job_id="job-1",
            project_id="project-1",
            status=JobStatus.CONVERSION_FAILED,
            diagnostics=(Diagnostic(code="bad", severity=Severity.ERROR, message="bad"),),
        )
    )
    with pytest.raises(InvalidTransition):
        store.approve_job("job-1")


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.CREATED,
        JobStatus.CONVERSION_FAILED,
        JobStatus.APPROVED,
        JobStatus.APPLYING,
        JobStatus.APPLIED,
        JobStatus.FAILED,
    ],
)
def test_only_ready_for_review_jobs_can_be_rejected(store: JobStore, status: JobStatus) -> None:
    store.create_job(
        JobView(
            job_id="job-1",
            project_id="project-1",
            status=status,
            artifact_sha256="a" * 64,
        )
    )

    with pytest.raises(InvalidTransition):
        store.reject_job("job-1")


def test_repeated_terminal_result_is_idempotent(store: JobStore) -> None:
    register_bound_agent(store)
    store.create_job(ready_job())
    store.approve_job("job-1")
    assert store.claim_next("agent-a") is not None
    result = ApplyResult(
        job_id="job-1",
        agent_id="agent-a",
        project_id="project-1",
        status=ApplyStatus.APPLIED,
        changed_paths=("Sample/Main.xml",),
    )
    store.record_apply_result(result)
    store.record_apply_result(result)
    assert store.get_job("job-1").status is JobStatus.APPLIED


def _checking_package() -> ProjectPackageView:
    return ProjectPackageView(
        job_id="job-1",
        status=ProjectPackageStage.CHECKING,
        stage=ProjectPackageStage.CHECKING,
        progress=70,
    )


def _seed_screenshot_path(database: Path, job_id: str, path: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE project_packages SET screenshot_digest = ?, screenshot_path = ? "
            "WHERE job_id = ?",
            ("a" * 64, str(path), job_id),
        )


def test_conversion_reference_preserves_selection_fingerprint(store: JobStore) -> None:
    store.create_job(
        ready_job(),
        selection_id="selection-1",
        selection_fingerprint="selection-fingerprint",
        conversion_source="selection",
        conversion_source_id="selection-1",
        conversion_package_name="SelectionPackage",
    )

    reference = store.get_job_conversion_reference("job-1")

    assert reference.selection_fingerprint == "selection-fingerprint"


def test_screenshot_consent_is_generation_bound_idempotent_and_conflict_safe(
    store: JobStore,
) -> None:
    store.create_job(ready_job())
    attempt = store.begin_package("job-1", "request", "instance", _checking_package())
    waiting = _checking_package().model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Visual hierarchy needs confirmation.",
        }
    )

    store.await_screenshot_consent(
        "job-1", "request", attempt.generation, "instance", waiting
    )
    first = store.record_screenshot_consent("job-1", attempt.generation, False)
    duplicate = store.record_screenshot_consent("job-1", attempt.generation, False)

    assert first.package == duplicate.package
    assert duplicate.package.screenshot_consent is False
    with pytest.raises(PackageConsentConflict):
        store.record_screenshot_consent("job-1", attempt.generation, True)
    with pytest.raises(InvalidTransition):
        store.record_screenshot_consent("job-1", attempt.generation + 1, False)


def test_approved_consent_can_fallback_once_before_any_screenshot_is_attached(
    store: JobStore,
) -> None:
    store.create_job(ready_job())
    attempt = store.begin_package("job-1", "request", "instance", _checking_package())
    waiting = _checking_package().model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    store.await_screenshot_consent(
        "job-1", "request", attempt.generation, "instance", waiting
    )

    approved = store.record_screenshot_consent("job-1", attempt.generation, True)
    fallback = store.record_screenshot_consent("job-1", attempt.generation, False)
    duplicate = store.record_screenshot_consent("job-1", attempt.generation, False)

    assert approved.package.screenshot_consent is True
    assert fallback.package.screenshot_consent is False
    assert duplicate.package == fallback.package
    with pytest.raises(PackageConsentConflict):
        store.record_screenshot_consent("job-1", attempt.generation, True)


def test_screenshot_attachment_requires_approval_and_binds_digest_and_path(
    store: JobStore, tmp_path: Path
) -> None:
    store.create_job(ready_job())
    attempt = store.begin_package("job-1", "request", "instance", _checking_package())
    waiting = _checking_package().model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    store.await_screenshot_consent(
        "job-1", "request", attempt.generation, "instance", waiting
    )
    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(b"png")

    with pytest.raises(InvalidTransition):
        store.attach_screenshot(
            "job-1", attempt.generation, "a" * 64, screenshot
        )

    store.record_screenshot_consent("job-1", attempt.generation, True)
    attached = store.attach_screenshot(
        "job-1", attempt.generation, "a" * 64, screenshot
    )
    duplicate = store.attach_screenshot(
        "job-1", attempt.generation, "a" * 64, screenshot
    )

    assert attached == duplicate
    assert attached.screenshot_digest == "a" * 64
    assert attached.screenshot_path == screenshot
    with pytest.raises(PackageConsentConflict):
        store.attach_screenshot(
            "job-1", attempt.generation, "b" * 64, tmp_path / "other.png"
        )
    cancelled = store.record_screenshot_consent("job-1", attempt.generation, False)
    assert cancelled.package.screenshot_consent is False
    assert cancelled.cleanup_path == screenshot


def test_screenshot_conversion_commit_is_generation_bound_compare_and_swap(
    store: JobStore, tmp_path: Path
) -> None:
    store.create_job(ready_job())
    checking = _checking_package()
    first = store.begin_package("job-1", "request", "instance-a", checking)
    waiting = checking.model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    store.await_screenshot_consent(
        "job-1", "request", first.generation, "instance-a", waiting
    )
    store.record_screenshot_consent("job-1", first.generation, True)
    screenshot = tmp_path / "semantic-screenshots" / "screenshot.png"
    screenshot.parent.mkdir()
    screenshot.write_bytes(b"png")
    store.attach_screenshot(
        "job-1", first.generation, "a" * 64, screenshot
    )
    first_candidate = ready_job().model_copy(update={"artifact_sha256": "b" * 64})
    late_candidate = ready_job().model_copy(update={"artifact_sha256": "c" * 64})
    packaging = waiting.model_copy(
        update={
            "status": ProjectPackageStage.PACKAGING,
            "stage": ProjectPackageStage.PACKAGING,
            "screenshot_reason": None,
        }
    )

    with pytest.raises(InvalidTransition, match="incomplete"):
        store.resume_package_after_screenshot(
            "job-1", first.generation, "instance-a", packaging
        )

    claim = store.claim_screenshot_analysis(
        "job-1", first.generation, "a" * 64, "analysis-a"
    )
    assert claim.claimed is True

    winner = store.complete_screenshot_conversion(
        "job-1",
        first.generation,
        "a" * 64,
        first_candidate,
        claim_owner_id="analysis-a",
    )
    loser = store.complete_screenshot_conversion(
        "job-1",
        first.generation,
        "a" * 64,
        late_candidate,
        claim_owner_id="analysis-a",
    )

    assert winner.committed is True
    assert loser.committed is False
    assert loser.package.screenshot_completed is True
    assert loser.package.screenshot_candidate == first_candidate
    store.resume_package_after_screenshot(
        "job-1", first.generation, "instance-a", packaging
    )
    failed = packaging.model_copy(
        update={
            "status": ProjectPackageStage.FAILED,
            "stage": ProjectPackageStage.FAILED,
        }
    )
    store.transition_package(
        "job-1",
        "request",
        first.generation,
        "instance-a",
        (ProjectPackageStage.PACKAGING,),
        failed,
    )
    second = store.begin_package("job-1", "request", "instance-b", checking)
    stale = store.complete_screenshot_conversion(
        "job-1",
        first.generation,
        "a" * 64,
        late_candidate,
        claim_owner_id="analysis-a",
    )

    assert second.generation == first.generation + 1
    assert stale.committed is False
    assert stale.package.generation == second.generation
    assert stale.package.screenshot_completed is False
    assert stale.package.screenshot_candidate is None


def test_old_generation_cleanup_cannot_delete_new_generation_screenshot(
    store: JobStore, tmp_path: Path
) -> None:
    store.create_job(ready_job())
    checking = _checking_package()
    first = store.begin_package("job-1", "request", "instance-a", checking)
    failed = checking.model_copy(
        update={
            "status": ProjectPackageStage.FAILED,
            "stage": ProjectPackageStage.FAILED,
        }
    )
    store.transition_package(
        "job-1",
        "request",
        first.generation,
        "instance-a",
        (ProjectPackageStage.CHECKING,),
        failed,
    )
    second = store.begin_package("job-1", "request", "instance-b", checking)
    waiting = checking.model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    store.await_screenshot_consent(
        "job-1", "request", second.generation, "instance-b", waiting
    )
    store.record_screenshot_consent("job-1", second.generation, True)
    screenshot_root = tmp_path / "semantic-screenshots"
    screenshot_root.mkdir()
    new_path = screenshot_root / "new-generation.png"
    new_path.write_bytes(b"new")
    store.attach_screenshot("job-1", second.generation, "b" * 64, new_path)

    api._cleanup_semantic_screenshot(
        store,
        "job-1",
        first.generation,
        "a" * 64,
        new_path,
        screenshot_root,
    )

    assert new_path.read_bytes() == b"new"
    assert store.get_package("job-1").screenshot_path == new_path

    outside_path = tmp_path / "outside-screenshot.png"
    outside_path.write_bytes(b"outside")
    api._cleanup_semantic_screenshot(
        store,
        "job-1",
        first.generation,
        "a" * 64,
        outside_path,
        screenshot_root,
    )

    assert outside_path.read_bytes() == b"outside"


def test_request_cleanup_keeps_database_reference_when_unlink_fails(
    store: JobStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.create_job(ready_job())
    checking = _checking_package()
    attempt = store.begin_package("job-1", "request", "instance", checking)
    waiting = checking.model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    store.await_screenshot_consent(
        "job-1", "request", attempt.generation, "instance", waiting
    )
    store.record_screenshot_consent("job-1", attempt.generation, True)
    screenshot_root = tmp_path / "semantic-screenshots"
    screenshot_root.mkdir()
    screenshot = screenshot_root / "locked-request.png"
    screenshot.write_bytes(b"sensitive")
    digest = "a" * 64
    store.attach_screenshot("job-1", attempt.generation, digest, screenshot)
    monkeypatch.setattr(api, "_unlink_semantic_screenshot", lambda *_args: False)

    api._cleanup_semantic_screenshot(
        store, "job-1", attempt.generation, digest, screenshot, screenshot_root
    )

    assert screenshot.exists()
    assert store.get_package("job-1").screenshot_path == screenshot


def test_terminal_package_transition_removes_screenshot_before_clearing_reference(
    tmp_path: Path,
) -> None:
    database = tmp_path / "jobs.db"
    store = JobStore(database)
    store.initialize()
    store.create_job(ready_job())
    checking = _checking_package()
    attempt = store.begin_package("job-1", "request", "instance", checking)
    packaging = checking.model_copy(
        update={
            "status": ProjectPackageStage.PACKAGING,
            "stage": ProjectPackageStage.PACKAGING,
            "progress": 90,
        }
    )
    store.transition_package(
        "job-1",
        "request",
        attempt.generation,
        "instance",
        (ProjectPackageStage.CHECKING,),
        packaging,
    )
    screenshot_root = tmp_path / "semantic-screenshots"
    screenshot_root.mkdir()
    screenshot = screenshot_root / "legacy-ready.png"
    screenshot.write_bytes(b"sensitive")
    _seed_screenshot_path(database, "job-1", screenshot)
    ready = packaging.model_copy(
        update={
            "status": ProjectPackageStage.READY,
            "stage": ProjectPackageStage.READY,
            "progress": 100,
            "download_name": "Quiz.zip",
            "sha256": "b" * 64,
        }
    )

    store.transition_package(
        "job-1",
        "request",
        attempt.generation,
        "instance",
        (ProjectPackageStage.PACKAGING,),
        ready,
    )

    assert not screenshot.exists()
    assert store.get_package("job-1").screenshot_path is None


def test_failed_terminal_unlink_retains_reference_until_startup_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "jobs.db"
    store = JobStore(database)
    store.initialize()
    store.create_job(ready_job())
    checking = _checking_package()
    attempt = store.begin_package("job-1", "request", "instance", checking)
    packaging = checking.model_copy(
        update={
            "status": ProjectPackageStage.PACKAGING,
            "stage": ProjectPackageStage.PACKAGING,
            "progress": 90,
        }
    )
    store.transition_package(
        "job-1",
        "request",
        attempt.generation,
        "instance",
        (ProjectPackageStage.CHECKING,),
        packaging,
    )
    screenshot_root = tmp_path / "semantic-screenshots"
    screenshot_root.mkdir()
    screenshot = screenshot_root / "locked-ready.png"
    screenshot.write_bytes(b"sensitive")
    _seed_screenshot_path(database, "job-1", screenshot)
    ready = packaging.model_copy(
        update={
            "status": ProjectPackageStage.READY,
            "stage": ProjectPackageStage.READY,
            "progress": 100,
            "download_name": "Quiz.zip",
            "sha256": "b" * 64,
        }
    )
    original_unlink = job_store_module.unlink_semantic_screenshot
    monkeypatch.setattr(
        job_store_module, "unlink_semantic_screenshot", lambda *_args: False
    )

    store.transition_package(
        "job-1",
        "request",
        attempt.generation,
        "instance",
        (ProjectPackageStage.PACKAGING,),
        ready,
    )

    assert screenshot.exists()
    assert store.get_package("job-1").screenshot_path == screenshot
    monkeypatch.setattr(job_store_module, "unlink_semantic_screenshot", original_unlink)
    assert JobStore(database).cleanup_terminal_screenshot_paths() == 1
    assert not screenshot.exists()
    assert JobStore(database).get_package("job-1").screenshot_path is None


def test_screenshot_analysis_claim_is_exclusive_and_recovers_after_expiry(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 8, 4, tzinfo=UTC)]
    database = tmp_path / "jobs.db"
    store = JobStore(
        database,
        clock=lambda: now[0],
        screenshot_analysis_lease_duration=timedelta(seconds=1),
    )
    store.initialize()
    store.create_job(ready_job())
    checking = _checking_package()
    attempt = store.begin_package("job-1", "request", "instance", checking)
    waiting = checking.model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    store.await_screenshot_consent(
        "job-1", "request", attempt.generation, "instance", waiting
    )
    store.record_screenshot_consent("job-1", attempt.generation, True)
    screenshot = tmp_path / "semantic-screenshots" / "claim.png"
    screenshot.parent.mkdir()
    screenshot.write_bytes(b"png")
    store.attach_screenshot("job-1", attempt.generation, "a" * 64, screenshot)

    first = store.claim_screenshot_analysis(
        "job-1", attempt.generation, "a" * 64, "worker-a"
    )
    blocked = store.claim_screenshot_analysis(
        "job-1", attempt.generation, "a" * 64, "worker-b"
    )
    now[0] += timedelta(seconds=2)
    restarted = JobStore(
        database,
        clock=lambda: now[0],
        screenshot_analysis_lease_duration=timedelta(seconds=1),
    )
    recovered = restarted.claim_screenshot_analysis(
        "job-1", attempt.generation, "a" * 64, "worker-b"
    )
    stale = restarted.complete_screenshot_conversion(
        "job-1",
        attempt.generation,
        "a" * 64,
        ready_job().model_copy(update={"artifact_sha256": "b" * 64}),
        claim_owner_id="worker-a",
    )
    committed = restarted.complete_screenshot_conversion(
        "job-1",
        attempt.generation,
        "a" * 64,
        ready_job().model_copy(update={"artifact_sha256": "c" * 64}),
        claim_owner_id="worker-b",
    )

    assert first.claimed is True
    assert blocked.claimed is False
    assert recovered.claimed is True
    assert stale.committed is False
    assert committed.committed is True
    assert committed.package.screenshot_candidate is not None
    assert committed.package.screenshot_candidate.artifact_sha256 == "c" * 64
    assert committed.package.screenshot_analysis_owner_id is None
    assert committed.package.screenshot_analysis_lease_expires_at is None
    assert restarted.get_package("job-1").screenshot_path is None
    assert not screenshot.exists()


def test_unfinished_claimed_screenshot_can_be_cancelled_immediately(
    tmp_path: Path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    store.initialize()
    store.create_job(ready_job())
    checking = _checking_package()
    attempt = store.begin_package("job-1", "request", "instance", checking)
    waiting = checking.model_copy(
        update={
            "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
            "screenshot_reason": "Need screenshot.",
        }
    )
    store.await_screenshot_consent(
        "job-1", "request", attempt.generation, "instance", waiting
    )
    store.record_screenshot_consent("job-1", attempt.generation, True)
    screenshot = tmp_path / "semantic-screenshots" / "cancel.png"
    screenshot.parent.mkdir()
    screenshot.write_bytes(b"png")
    digest = "a" * 64
    store.attach_screenshot("job-1", attempt.generation, digest, screenshot)
    claim = store.claim_screenshot_analysis(
        "job-1", attempt.generation, digest, "crashed-worker"
    )
    assert claim.claimed is True

    cancellation = store.record_screenshot_consent(
        "job-1", attempt.generation, False
    )
    late = store.complete_screenshot_conversion(
        "job-1",
        attempt.generation,
        digest,
        ready_job().model_copy(update={"artifact_sha256": "b" * 64}),
        claim_owner_id="crashed-worker",
    )

    assert cancellation.cleanup_path == screenshot
    assert cancellation.package.screenshot_consent is False
    assert cancellation.package.screenshot_analysis_owner_id is None
    assert cancellation.package.screenshot_analysis_lease_expires_at is None
    assert cancellation.package.screenshot_completed is False
    assert late.committed is False


def test_package_retry_removes_stale_screenshot_from_failed_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "jobs.db"
    store = JobStore(database)
    store.initialize()
    store.create_job(ready_job())
    checking = _checking_package()
    first = store.begin_package("job-1", "request", "instance-a", checking)
    failed = checking.model_copy(
        update={
            "status": ProjectPackageStage.FAILED,
            "stage": ProjectPackageStage.FAILED,
        }
    )
    store.transition_package(
        "job-1",
        "request",
        first.generation,
        "instance-a",
        (ProjectPackageStage.CHECKING,),
        failed,
    )
    screenshot_root = tmp_path / "semantic-screenshots"
    screenshot_root.mkdir()
    screenshot = screenshot_root / "legacy-retry.png"
    screenshot.write_bytes(b"sensitive")
    _seed_screenshot_path(database, "job-1", screenshot)

    second = store.begin_package("job-1", "request", "instance-b", checking)

    assert second.generation == first.generation + 1
    assert not screenshot.exists()
    assert store.get_package("job-1").screenshot_path is None


def test_expired_package_recovery_removes_attached_screenshot(tmp_path: Path) -> None:
    now = [datetime(2026, 8, 4, tzinfo=UTC)]
    database = tmp_path / "jobs.db"
    store = JobStore(
        database,
        clock=lambda: now[0],
        package_lease_duration=timedelta(seconds=1),
    )
    store.initialize()
    store.create_job(ready_job())
    checking = _checking_package()
    attempt = store.begin_package("job-1", "request", "instance", checking)
    packaging = checking.model_copy(
        update={
            "status": ProjectPackageStage.PACKAGING,
            "stage": ProjectPackageStage.PACKAGING,
            "progress": 90,
        }
    )
    store.transition_package(
        "job-1",
        "request",
        attempt.generation,
        "instance",
        (ProjectPackageStage.CHECKING,),
        packaging,
    )
    screenshot_root = tmp_path / "semantic-screenshots"
    screenshot_root.mkdir()
    screenshot = screenshot_root / "legacy-expired.png"
    screenshot.write_bytes(b"sensitive")
    _seed_screenshot_path(database, "job-1", screenshot)
    now[0] += timedelta(seconds=2)

    assert store.recover_expired_packages() == 1

    recovered = store.get_package("job-1")
    assert recovered.view.stage is ProjectPackageStage.FAILED
    assert recovered.screenshot_path is None
    assert not screenshot.exists()
