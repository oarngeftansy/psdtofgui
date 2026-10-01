from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal

from figma_to_fgui.fgui_new_project_models import NewProjectManifest
from figma_to_fgui.fgui_new_project_review import NewProjectDesignerReview
from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.semantic_screenshot_storage import unlink_semantic_screenshot
from figma_to_fgui.service_contracts import (
    AgentRegistration,
    ApplyResult,
    ApplyStatus,
    JobStatus,
    JobView,
    NewFguiProjectStage,
    NewFguiProjectView,
    ProjectBinding,
    ProjectPackageStage,
    ProjectPackageView,
)


class StoreError(RuntimeError):
    code = "store_error"


class NotFound(StoreError):
    code = "not_found"


class InvalidTransition(StoreError):
    code = "invalid_transition"


class OwnershipMismatch(StoreError):
    code = "ownership_mismatch"


class PackageRequestConflict(StoreError):
    code = "package_request_conflict"


class PackageConsentConflict(StoreError):
    code = "package_consent_conflict"


class ScreenshotConsentRequired(StoreError):
    code = "screenshot_consent_required"


@dataclass(frozen=True)
class StoredPackage:
    view: ProjectPackageView
    artifact_path: Path | None
    request_identity: str
    generation: int
    owner_id: str | None
    lease_expires_at: datetime | None
    screenshot_consent: bool | None
    screenshot_digest: str | None
    screenshot_path: Path | None
    request_payload: str | None
    screenshot_candidate: JobView | None
    screenshot_completed: bool
    screenshot_completion_diagnostics: tuple[Diagnostic, ...]
    screenshot_analysis_owner_id: str | None
    screenshot_analysis_lease_expires_at: datetime | None


@dataclass(frozen=True)
class JobConversionReference:
    source: str
    source_id: str
    package_name: str
    selection_fingerprint: str | None


@dataclass(frozen=True)
class PackageAttempt:
    view: ProjectPackageView
    generation: int
    should_build: bool
    owner_id: str | None
    lease_expires_at: datetime | None


@dataclass(frozen=True)
class ScreenshotConversionCommit:
    package: StoredPackage
    committed: bool


@dataclass(frozen=True)
class ScreenshotAnalysisClaim:
    package: StoredPackage
    claimed: bool


class ScreenshotAnalysisStartState(StrEnum):
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    ACCEPTED = "accepted"


@dataclass(frozen=True)
class ScreenshotAnalysisStart:
    package: StoredPackage
    state: ScreenshotAnalysisStartState


@dataclass(frozen=True)
class ScreenshotConsentRecord:
    package: StoredPackage
    cleanup_path: Path | None = None


@dataclass(frozen=True)
class StoredNewProject:
    view: NewFguiProjectView
    owner_device_id: str
    selection_id: str
    selection_fingerprint: str
    request_identity: str
    project_name: str
    generation: int
    artifact_path: Path | None
    manifest: NewProjectManifest | None
    review: NewProjectDesignerReview | None
    adjustments: tuple[dict[str, object], ...]
    superseded_by: str | None
    lease_owner: str | None
    lease_expires_at: datetime | None


@dataclass(frozen=True)
class NewProjectAttempt:
    project: StoredNewProject
    should_build: bool


_PRESERVE_ARTIFACT = object()
_PACKAGE_TRANSITIONS = {
    ProjectPackageStage.CHECKING: {
        ProjectPackageStage.PACKAGING,
        ProjectPackageStage.FAILED,
    },
    ProjectPackageStage.PACKAGING: {
        ProjectPackageStage.READY,
        ProjectPackageStage.FAILED,
    },
    ProjectPackageStage.READY: {ProjectPackageStage.FAILED},
}


class JobStore:
    def __init__(
        self,
        database: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        package_lease_duration: timedelta = timedelta(minutes=2),
        screenshot_analysis_lease_duration: timedelta = timedelta(minutes=15),
    ) -> None:
        if package_lease_duration.total_seconds() <= 0:
            raise ValueError("package lease duration must be positive")
        if screenshot_analysis_lease_duration.total_seconds() <= 0:
            raise ValueError("screenshot analysis lease duration must be positive")
        self.database = database
        self.clock = clock
        self.package_lease_duration = package_lease_duration
        self.screenshot_analysis_lease_duration = screenshot_analysis_lease_duration
        self.semantic_screenshot_root = database.parent / "semantic-screenshots"

    @property
    def package_heartbeat_interval(self) -> float:
        return max(0.01, self.package_lease_duration.total_seconds() / 3)

    def _new_lease(self) -> tuple[datetime, float]:
        expires_at = self.clock() + self.package_lease_duration
        return expires_at, expires_at.timestamp()

    def _new_screenshot_analysis_lease(self) -> tuple[datetime, float]:
        expires_at = self.clock() + self.screenshot_analysis_lease_duration
        return expires_at, expires_at.timestamp()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _remove_screenshot_file(self, row: sqlite3.Row) -> bool:
        path = row["screenshot_path"]
        if isinstance(path, str) and path:
            return unlink_semantic_screenshot(Path(path), self.semantic_screenshot_root)
        return path is None

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_bindings (
                    project_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    selection_id TEXT,
                    selection_fingerprint TEXT,
                    payload TEXT NOT NULL,
                    conversion_source TEXT,
                    conversion_source_id TEXT,
                    conversion_package_name TEXT,
                    screenshot_reason TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_status_project
                    ON jobs(status, project_id, job_id);
                CREATE TABLE IF NOT EXISTS apply_results (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_packages (
                    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                    request_identity TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    owner_id TEXT,
                    lease_expires_at REAL,
                    payload TEXT NOT NULL,
                    artifact_path TEXT,
                    screenshot_consent INTEGER,
                    screenshot_digest TEXT,
                    screenshot_path TEXT,
                    request_payload TEXT,
                    screenshot_candidate_payload TEXT,
                    screenshot_completed INTEGER NOT NULL DEFAULT 0,
                    screenshot_completion_diagnostics TEXT,
                    screenshot_analysis_owner_id TEXT,
                    screenshot_analysis_lease_expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS new_fgui_projects (
                    build_id TEXT PRIMARY KEY,
                    owner_device_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL,
                    selection_fingerprint TEXT NOT NULL,
                    request_identity TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    public_payload TEXT NOT NULL,
                    artifact_path TEXT,
                    manifest_payload TEXT,
                    review_payload TEXT,
                    adjustments_payload TEXT NOT NULL DEFAULT '[]',
                    superseded_by TEXT,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    UNIQUE(owner_device_id, request_identity, generation)
                );
                CREATE INDEX IF NOT EXISTS new_fgui_projects_owner
                    ON new_fgui_projects(owner_device_id, build_id);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "selection_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN selection_id TEXT")
            if "selection_fingerprint" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN selection_fingerprint TEXT")
            if "conversion_source" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN conversion_source TEXT")
            if "conversion_source_id" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN conversion_source_id TEXT")
            if "conversion_package_name" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN conversion_package_name TEXT")
            if "screenshot_reason" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN screenshot_reason TEXT")
            package_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(project_packages)")
            }
            added_stage = "stage" not in package_columns
            if added_stage:
                connection.execute(
                    "ALTER TABLE project_packages ADD COLUMN stage TEXT NOT NULL DEFAULT 'checking'"
                )
            if "generation" not in package_columns:
                connection.execute(
                    "ALTER TABLE project_packages ADD COLUMN generation INTEGER NOT NULL DEFAULT 1"
                )
            if "owner_id" not in package_columns:
                connection.execute("ALTER TABLE project_packages ADD COLUMN owner_id TEXT")
            if "lease_expires_at" not in package_columns:
                connection.execute("ALTER TABLE project_packages ADD COLUMN lease_expires_at REAL")
            if "screenshot_consent" not in package_columns:
                connection.execute(
                    "ALTER TABLE project_packages ADD COLUMN screenshot_consent INTEGER"
                )
            if "screenshot_digest" not in package_columns:
                connection.execute("ALTER TABLE project_packages ADD COLUMN screenshot_digest TEXT")
            if "screenshot_path" not in package_columns:
                connection.execute("ALTER TABLE project_packages ADD COLUMN screenshot_path TEXT")
            if "request_payload" not in package_columns:
                connection.execute("ALTER TABLE project_packages ADD COLUMN request_payload TEXT")
            if "screenshot_candidate_payload" not in package_columns:
                connection.execute(
                    "ALTER TABLE project_packages ADD COLUMN screenshot_candidate_payload TEXT"
                )
            if "screenshot_completed" not in package_columns:
                connection.execute(
                    "ALTER TABLE project_packages ADD COLUMN screenshot_completed INTEGER NOT NULL DEFAULT 0"
                )
            if "screenshot_completion_diagnostics" not in package_columns:
                connection.execute(
                    "ALTER TABLE project_packages ADD COLUMN screenshot_completion_diagnostics TEXT"
                )
            if "screenshot_analysis_owner_id" not in package_columns:
                connection.execute(
                    "ALTER TABLE project_packages ADD COLUMN screenshot_analysis_owner_id TEXT"
                )
            if "screenshot_analysis_lease_expires_at" not in package_columns:
                connection.execute(
                    "ALTER TABLE project_packages "
                    "ADD COLUMN screenshot_analysis_lease_expires_at REAL"
                )
            if added_stage:
                for row in connection.execute(
                    "SELECT job_id, payload FROM project_packages"
                ).fetchall():
                    view = ProjectPackageView.model_validate_json(row["payload"])
                    connection.execute(
                        "UPDATE project_packages SET stage = ? WHERE job_id = ?",
                        (view.stage, row["job_id"]),
                    )

    @staticmethod
    def _stored_new_project(row: sqlite3.Row) -> StoredNewProject:
        lease_timestamp = row["lease_expires_at"]
        raw_adjustments = json.loads(row["adjustments_payload"])
        if not isinstance(raw_adjustments, list) or any(
            not isinstance(item, dict) for item in raw_adjustments
        ):
            raise InvalidTransition("new-project adjustment storage is invalid")
        return StoredNewProject(
            view=NewFguiProjectView.model_validate_json(row["public_payload"]),
            owner_device_id=row["owner_device_id"],
            selection_id=row["selection_id"],
            selection_fingerprint=row["selection_fingerprint"],
            request_identity=row["request_identity"],
            project_name=row["project_name"],
            generation=int(row["generation"]),
            artifact_path=(None if row["artifact_path"] is None else Path(row["artifact_path"])),
            manifest=(
                None
                if row["manifest_payload"] is None
                else NewProjectManifest.model_validate_json(row["manifest_payload"])
            ),
            review=(
                None
                if row["review_payload"] is None
                else NewProjectDesignerReview.model_validate_json(row["review_payload"])
            ),
            adjustments=tuple(dict(item) for item in raw_adjustments),
            superseded_by=row["superseded_by"],
            lease_owner=row["lease_owner"],
            lease_expires_at=(
                None
                if lease_timestamp is None
                else datetime.fromtimestamp(float(lease_timestamp), UTC)
            ),
        )

    def begin_new_project(
        self,
        *,
        build_id: str,
        owner_device_id: str,
        selection_id: str,
        selection_fingerprint: str,
        request_identity: str,
        project_name: str,
        lease_owner: str,
    ) -> NewProjectAttempt:
        expires_at, lease_timestamp = self._new_lease()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE owner_device_id = ? "
                "AND request_identity = ? ORDER BY generation DESC LIMIT 1",
                (owner_device_id, request_identity),
            ).fetchone()
            if existing is not None:
                stored_existing = self._stored_new_project(existing)
                if stored_existing.view.stage not in {
                    NewFguiProjectStage.FAILED,
                    NewFguiProjectStage.REJECTED,
                    NewFguiProjectStage.APPROVED,
                }:
                    return NewProjectAttempt(stored_existing, False)
                generation = int(existing["generation"]) + 1
            else:
                generation = 1
            view = NewFguiProjectView(
                build_id=build_id,
                generation=generation,
                status="converting",
                stage="converting",
                progress=5,
            )
            try:
                connection.execute(
                    "INSERT INTO new_fgui_projects("
                    "build_id, owner_device_id, selection_id, selection_fingerprint, "
                    "request_identity, project_name, generation, stage, public_payload, "
                    "lease_owner, lease_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        build_id,
                        owner_device_id,
                        selection_id,
                        selection_fingerprint,
                        request_identity,
                        project_name,
                        generation,
                        NewFguiProjectStage.CONVERTING,
                        view.model_dump_json(),
                        lease_owner,
                        lease_timestamp,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM new_fgui_projects WHERE owner_device_id = ? "
                    "AND request_identity = ? ORDER BY generation DESC LIMIT 1",
                    (owner_device_id, request_identity),
                ).fetchone()
                if existing is None:
                    raise
                return NewProjectAttempt(self._stored_new_project(existing), False)
            row = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
        if row is None:
            raise InvalidTransition("new-project attempt was not persisted")
        stored = self._stored_new_project(row)
        return NewProjectAttempt(
            project=StoredNewProject(
                **{
                    **stored.__dict__,
                    "lease_expires_at": expires_at,
                }
            ),
            should_build=True,
        )

    def get_new_project(self, build_id: str, owner_device_id: str) -> StoredNewProject:
        self.recover_expired_new_projects(build_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
        if row is None or row["owner_device_id"] != owner_device_id:
            raise NotFound("new project not found")
        invalid = False
        stored: StoredNewProject | None = None
        try:
            stored = self._stored_new_project(row)
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid = True
        if invalid or stored is None:
            raise InvalidTransition("new-project storage is invalid")
        return stored

    def complete_new_project(
        self,
        *,
        build_id: str,
        owner_device_id: str,
        lease_owner: str,
        view: NewFguiProjectView,
        artifact_path: Path,
        manifest: NewProjectManifest,
        review: NewProjectDesignerReview,
    ) -> StoredNewProject:
        if (
            view.build_id != build_id
            or view.stage != "awaiting_review"
            or review.build_id != build_id
        ):
            raise InvalidTransition("new-project completion payload is invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE new_fgui_projects SET stage = ?, public_payload = ?, artifact_path = ?, "
                "manifest_payload = ?, review_payload = ?, lease_owner = NULL, lease_expires_at = NULL "
                "WHERE build_id = ? AND owner_device_id = ? AND lease_owner = ? "
                "AND stage IN (?, ?) AND lease_expires_at > ?",
                (
                    NewFguiProjectStage.AWAITING_REVIEW,
                    view.model_dump_json(),
                    str(artifact_path),
                    manifest.model_dump_json(by_alias=True),
                    review.model_dump_json(by_alias=True),
                    build_id,
                    owner_device_id,
                    lease_owner,
                    NewFguiProjectStage.CHECKING,
                    NewFguiProjectStage.PACKAGING,
                    self.clock().timestamp(),
                ),
            )
            if updated.rowcount != 1:
                raise InvalidTransition("new-project build lease was lost")
            row = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
        if row is None:
            raise InvalidTransition("new-project completion was lost")
        return self._stored_new_project(row)

    def fail_new_project(
        self,
        *,
        build_id: str,
        owner_device_id: str,
        lease_owner: str,
        diagnostics: tuple[Diagnostic, ...],
    ) -> NewFguiProjectView:
        view = NewFguiProjectView(
            build_id=build_id,
            generation=self.get_new_project(build_id, owner_device_id).generation,
            status="failed",
            stage="failed",
            progress=100,
            diagnostics=diagnostics,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE new_fgui_projects SET stage = ?, public_payload = ?, artifact_path = NULL, "
                "manifest_payload = NULL, review_payload = NULL, lease_owner = NULL, "
                "lease_expires_at = NULL WHERE build_id = ? AND owner_device_id = ? "
                "AND lease_owner = ? AND stage IN (?, ?, ?, ?)",
                (
                    NewFguiProjectStage.FAILED,
                    view.model_dump_json(),
                    build_id,
                    owner_device_id,
                    lease_owner,
                    NewFguiProjectStage.CONVERTING,
                    NewFguiProjectStage.CHECKING,
                    NewFguiProjectStage.PACKAGING,
                    NewFguiProjectStage.REGENERATING,
                ),
            )
            if updated.rowcount != 1:
                raise InvalidTransition("new-project failure lost its lease")
        return view

    def renew_new_project_lease(
        self,
        *,
        build_id: str,
        owner_device_id: str,
        lease_owner: str,
    ) -> datetime:
        expires_at, lease_timestamp = self._new_lease()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE new_fgui_projects SET lease_expires_at = ? WHERE build_id = ? "
                "AND owner_device_id = ? AND lease_owner = ? AND stage IN (?, ?, ?, ?) "
                "AND lease_expires_at > ?",
                (
                    lease_timestamp,
                    build_id,
                    owner_device_id,
                    lease_owner,
                    NewFguiProjectStage.CONVERTING,
                    NewFguiProjectStage.CHECKING,
                    NewFguiProjectStage.PACKAGING,
                    NewFguiProjectStage.REGENERATING,
                    self.clock().timestamp(),
                ),
            )
            if updated.rowcount != 1:
                raise InvalidTransition("new-project lease is no longer owned")
        return expires_at

    def advance_new_project_stage(
        self, *, build_id: str, owner_device_id: str, lease_owner: str,
        stage: NewFguiProjectStage, progress: int,
    ) -> NewFguiProjectView:
        if stage not in {NewFguiProjectStage.CHECKING, NewFguiProjectStage.PACKAGING}:
            raise InvalidTransition("invalid new-project progress stage")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)).fetchone()
            if row is None or row["owner_device_id"] != owner_device_id or row["lease_owner"] != lease_owner or row["stage"] not in {NewFguiProjectStage.CONVERTING, NewFguiProjectStage.REGENERATING, NewFguiProjectStage.CHECKING, NewFguiProjectStage.PACKAGING}:
                raise InvalidTransition("new-project progress lost its lease")
            previous = self._stored_new_project(row).view
            if progress <= previous.progress:
                raise InvalidTransition("new-project progress must be monotonic")
            view = previous.model_copy(update={"status": stage, "stage": stage, "progress": progress, "download_name": None, "sha256": None, "byte_size": None})
            connection.execute("UPDATE new_fgui_projects SET stage = ?, public_payload = ? WHERE build_id = ?", (stage, view.model_dump_json(), build_id))
        return view

    def set_new_project_adjustment(
        self,
        *,
        build_id: str,
        owner_device_id: str,
        generation: int,
        adjustment: dict[str, object],
    ) -> StoredNewProject:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
            if (
                row is None
                or row["owner_device_id"] != owner_device_id
                or int(row["generation"]) != generation
                or row["stage"] != NewFguiProjectStage.AWAITING_REVIEW
                or row["superseded_by"] is not None
            ):
                raise InvalidTransition("new-project candidate cannot be adjusted")
            adjustments = json.loads(row["adjustments_payload"])
            adjustments.append(adjustment)
            view = NewFguiProjectView(
                build_id=build_id,
                generation=generation,
                status="adjusting",
                stage="adjusting",
                progress=100,
                download_name=json.loads(row["public_payload"]).get("download_name"),
                sha256=json.loads(row["public_payload"]).get("sha256"),
                byte_size=json.loads(row["public_payload"]).get("byte_size"),
            )
            connection.execute(
                "UPDATE new_fgui_projects SET stage = ?, public_payload = ?, adjustments_payload = ? "
                "WHERE build_id = ? AND generation = ? AND stage = ?",
                (
                    NewFguiProjectStage.ADJUSTING,
                    view.model_dump_json(),
                    json.dumps(adjustments, sort_keys=True, separators=(",", ":")),
                    build_id,
                    generation,
                    NewFguiProjectStage.AWAITING_REVIEW,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
        if updated is None:
            raise InvalidTransition("new-project adjustment was lost")
        return self._stored_new_project(updated)

    def begin_new_project_regeneration(
        self,
        *,
        old_build_id: str,
        new_build_id: str,
        owner_device_id: str,
        generation: int,
        lease_owner: str,
    ) -> StoredNewProject:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (old_build_id,)
            ).fetchone()
            if (
                old is None
                or old["owner_device_id"] != owner_device_id
                or int(old["generation"]) != generation
                or old["stage"] != NewFguiProjectStage.ADJUSTING
                or old["superseded_by"] is not None
            ):
                raise InvalidTransition("new-project candidate cannot regenerate")
            new_generation = generation + 1
            view = NewFguiProjectView(
                build_id=new_build_id,
                generation=new_generation,
                status="regenerating",
                stage="regenerating",
                progress=5,
            )
            _, lease_timestamp = self._new_lease()
            connection.execute(
                "UPDATE new_fgui_projects SET stage = ?, superseded_by = ? WHERE build_id = ?",
                (NewFguiProjectStage.ADJUSTING, new_build_id, old_build_id),
            )
            connection.execute(
                "INSERT INTO new_fgui_projects("
                "build_id, owner_device_id, selection_id, selection_fingerprint, "
                "request_identity, project_name, generation, stage, public_payload, "
                "adjustments_payload, lease_owner, lease_expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_build_id,
                    owner_device_id,
                    old["selection_id"],
                    old["selection_fingerprint"],
                    old["request_identity"],
                    old["project_name"],
                    new_generation,
                    NewFguiProjectStage.REGENERATING,
                    view.model_dump_json(),
                    old["adjustments_payload"],
                    lease_owner,
                    lease_timestamp,
                ),
            )
            new = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (new_build_id,)
            ).fetchone()
        if new is None:
            raise InvalidTransition("new-project regeneration was not persisted")
        return self._stored_new_project(new)

    def decide_new_project(
        self,
        *,
        build_id: str,
        owner_device_id: str,
        generation: int,
        target: Literal["approved", "rejected"],
    ) -> StoredNewProject:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
            if row is None or row["owner_device_id"] != owner_device_id:
                raise NotFound("new project not found")
            if int(row["generation"]) != generation or row["superseded_by"] is not None:
                raise InvalidTransition("new-project candidate generation changed")
            if row["stage"] == target:
                return self._stored_new_project(row)
            if row["stage"] != NewFguiProjectStage.AWAITING_REVIEW:
                raise InvalidTransition("new-project candidate is already terminal")
            previous = NewFguiProjectView.model_validate_json(row["public_payload"])
            view = previous.model_copy(update={"status": target, "stage": target, "progress": 100})
            connection.execute(
                "UPDATE new_fgui_projects SET stage = ?, public_payload = ? "
                "WHERE build_id = ? AND generation = ? AND stage = ?",
                (
                    target,
                    view.model_dump_json(),
                    build_id,
                    generation,
                    NewFguiProjectStage.AWAITING_REVIEW,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
        if updated is None:
            raise InvalidTransition("new-project decision was lost")
        return self._stored_new_project(updated)

    def complete_new_project_analysis(
        self,
        *,
        build_id: str,
        owner_device_id: str,
        lease_owner: str,
        view: NewFguiProjectView,
        review: NewProjectDesignerReview,
    ) -> StoredNewProject:
        if (
            view.build_id != build_id
            or view.stage != "awaiting_review"
            or view.artifact_ready
            or review.build_id != build_id
            or review.approvable
            or not any(item.blocks_approval for item in review.dispositions)
        ):
            raise InvalidTransition("new-project analysis payload is invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE new_fgui_projects SET stage = ?, public_payload = ?, "
                "artifact_path = NULL, manifest_payload = NULL, review_payload = ?, "
                "lease_owner = NULL, lease_expires_at = NULL "
                "WHERE build_id = ? AND owner_device_id = ? AND lease_owner = ? "
                "AND stage IN (?, ?, ?, ?) AND lease_expires_at > ?",
                (
                    NewFguiProjectStage.AWAITING_REVIEW,
                    view.model_dump_json(),
                    review.model_dump_json(by_alias=True),
                    build_id,
                    owner_device_id,
                    lease_owner,
                    NewFguiProjectStage.CONVERTING,
                    NewFguiProjectStage.CHECKING,
                    NewFguiProjectStage.PACKAGING,
                    NewFguiProjectStage.REGENERATING,
                    self.clock().timestamp(),
                ),
            )
            if updated.rowcount != 1:
                raise InvalidTransition("new-project analysis lease was lost")
            row = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
        if row is None:
            raise InvalidTransition("new-project analysis was lost")
        return self._stored_new_project(row)

    def invalidate_new_project_artifact(self, build_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT public_payload FROM new_fgui_projects WHERE build_id = ?", (build_id,)
            ).fetchone()
            if row is None:
                return
            previous = NewFguiProjectView.model_validate_json(row["public_payload"])
            failed = NewFguiProjectView(
                build_id=build_id,
                generation=previous.generation,
                status="failed",
                stage="failed",
                progress=100,
                diagnostics=(
                    Diagnostic(
                        code="fgui.writer.api.artifact_invalid",
                        severity=Severity.ERROR,
                        message="The generated archive is no longer available.",
                    ),
                ),
            )
            if previous.stage not in {"failed", "rejected"}:
                connection.execute(
                    "UPDATE new_fgui_projects SET stage = ?, public_payload = ?, "
                    "artifact_path = NULL, manifest_payload = NULL, review_payload = NULL, "
                    "lease_owner = NULL, lease_expires_at = NULL WHERE build_id = ?",
                    (NewFguiProjectStage.FAILED, failed.model_dump_json(), build_id),
                )

    def recover_expired_new_projects(self, build_id: str | None = None) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            query = (
                "SELECT build_id, generation FROM new_fgui_projects WHERE stage IN (?, ?, ?, ?) "
                "AND (lease_expires_at IS NULL OR lease_expires_at <= ?)"
            )
            parameters: list[object] = [
                NewFguiProjectStage.CONVERTING,
                NewFguiProjectStage.CHECKING,
                NewFguiProjectStage.PACKAGING,
                NewFguiProjectStage.REGENERATING,
                self.clock().timestamp(),
            ]
            if build_id is not None:
                query += " AND build_id = ?"
                parameters.append(build_id)
            rows = connection.execute(query, parameters).fetchall()
            recovered = 0
            for row in rows:
                failed = NewFguiProjectView(
                    build_id=row["build_id"],
                    generation=int(row["generation"]),
                    status="failed",
                    stage="failed",
                    progress=100,
                    diagnostics=(
                        Diagnostic(
                            code="fgui.writer.api.build_interrupted",
                            severity=Severity.ERROR,
                            message="The new-project build was interrupted.",
                        ),
                    ),
                )
                updated = connection.execute(
                    "UPDATE new_fgui_projects SET stage = ?, public_payload = ?, "
                    "artifact_path = NULL, manifest_payload = NULL, review_payload = NULL, "
                    "lease_owner = NULL, lease_expires_at = NULL WHERE build_id = ? "
                    "AND stage IN (?, ?, ?, ?)",
                    (
                        NewFguiProjectStage.FAILED,
                        failed.model_dump_json(),
                        row["build_id"],
                        NewFguiProjectStage.CONVERTING,
                        NewFguiProjectStage.CHECKING,
                        NewFguiProjectStage.PACKAGING,
                        NewFguiProjectStage.REGENERATING,
                    ),
                )
                recovered += updated.rowcount
        return recovered

    def list_new_project_artifacts(self) -> tuple[StoredNewProject, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM new_fgui_projects WHERE artifact_path IS NOT NULL "
                "AND stage IN (?, ?)",
                (
                    NewFguiProjectStage.AWAITING_REVIEW,
                    NewFguiProjectStage.APPROVED,
                ),
            ).fetchall()
        projects: list[StoredNewProject] = []
        for row in rows:
            try:
                projects.append(self._stored_new_project(row))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(projects)

    def register_agent(self, agent: AgentRegistration) -> AgentRegistration:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO agents(agent_id, payload) VALUES (?, ?) "
                "ON CONFLICT(agent_id) DO UPDATE SET payload = excluded.payload",
                (agent.agent_id, agent.model_dump_json()),
            )
        return agent

    def bind_project(self, binding: ProjectBinding) -> ProjectBinding:
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO project_bindings(project_id, agent_id, payload) VALUES (?, ?, ?) "
                    "ON CONFLICT(project_id) DO UPDATE SET "
                    "agent_id = excluded.agent_id, payload = excluded.payload",
                    (binding.project_id, binding.agent_id, binding.model_dump_json()),
                )
            except sqlite3.IntegrityError as error:
                raise NotFound("agent is not registered") from error
        return binding

    def create_job(
        self,
        job: JobView,
        selection_id: str | None = None,
        selection_fingerprint: str | None = None,
        conversion_source: str | None = None,
        conversion_source_id: str | None = None,
        conversion_package_name: str | None = None,
        screenshot_reason: str | None = None,
    ) -> JobView:
        reference = (
            conversion_source,
            conversion_source_id,
            conversion_package_name,
        )
        if any(value is None for value in reference) and any(
            value is not None for value in reference
        ):
            raise ValueError("conversion reference must be complete")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs(job_id, project_id, status, selection_id, selection_fingerprint, "
                "payload, conversion_source, conversion_source_id, conversion_package_name, "
                "screenshot_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.job_id,
                    job.project_id,
                    job.status,
                    selection_id,
                    selection_fingerprint,
                    job.model_dump_json(),
                    conversion_source,
                    conversion_source_id,
                    conversion_package_name,
                    screenshot_reason,
                ),
            )
        return job

    def get_job(self, job_id: str) -> JobView:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise NotFound("job not found")
        return JobView.model_validate_json(row["payload"])

    def update_job_conversion(self, job: JobView) -> JobView:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?", (job.job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("job not found")
            current = JobView.model_validate_json(row["payload"])
            if (
                current.project_id != job.project_id
                or current.status not in {JobStatus.READY_FOR_REVIEW, JobStatus.CONVERSION_FAILED}
                or job.status not in {JobStatus.READY_FOR_REVIEW, JobStatus.CONVERSION_FAILED}
            ):
                raise InvalidTransition("job conversion cannot be replaced")
            self._save_job(connection, job)
        return job

    def get_job_selection_id(self, job_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT selection_id FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise NotFound("job not found")
        selection_id = row["selection_id"]
        return selection_id if isinstance(selection_id, str) else None

    def get_job_conversion_reference(self, job_id: str) -> JobConversionReference:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT conversion_source, conversion_source_id, conversion_package_name, "
                "selection_fingerprint "
                "FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise NotFound("job not found")
        source = row["conversion_source"]
        source_id = row["conversion_source_id"]
        package_name = row["conversion_package_name"]
        if not all(isinstance(value, str) and value for value in (source, source_id, package_name)):
            raise NotFound("job conversion reference not found")
        return JobConversionReference(
            source=str(source),
            source_id=str(source_id),
            package_name=str(package_name),
            selection_fingerprint=(
                str(row["selection_fingerprint"])
                if row["selection_fingerprint"] is not None
                else None
            ),
        )

    def get_job_screenshot_reason(self, job_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT screenshot_reason FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise NotFound("job not found")
        reason = row["screenshot_reason"]
        return str(reason) if reason is not None else None

    @staticmethod
    def _stored_package(row: sqlite3.Row) -> StoredPackage:
        artifact = Path(row["artifact_path"]) if row["artifact_path"] is not None else None
        lease = (
            datetime.fromtimestamp(float(row["lease_expires_at"]), UTC)
            if row["lease_expires_at"] is not None
            else None
        )
        screenshot_analysis_lease = (
            datetime.fromtimestamp(float(row["screenshot_analysis_lease_expires_at"]), UTC)
            if row["screenshot_analysis_lease_expires_at"] is not None
            else None
        )
        return StoredPackage(
            view=ProjectPackageView.model_validate_json(row["payload"]),
            artifact_path=artifact,
            request_identity=str(row["request_identity"]),
            generation=int(row["generation"]),
            owner_id=row["owner_id"] if isinstance(row["owner_id"], str) else None,
            lease_expires_at=lease,
            screenshot_consent=(
                bool(row["screenshot_consent"]) if row["screenshot_consent"] is not None else None
            ),
            screenshot_digest=(
                str(row["screenshot_digest"]) if row["screenshot_digest"] is not None else None
            ),
            screenshot_path=(
                Path(row["screenshot_path"]) if row["screenshot_path"] is not None else None
            ),
            request_payload=(
                str(row["request_payload"]) if row["request_payload"] is not None else None
            ),
            screenshot_candidate=(
                JobView.model_validate_json(row["screenshot_candidate_payload"])
                if row["screenshot_candidate_payload"] is not None
                else None
            ),
            screenshot_completed=bool(row["screenshot_completed"]),
            screenshot_completion_diagnostics=tuple(
                Diagnostic.model_validate(item)
                for item in json.loads(row["screenshot_completion_diagnostics"] or "[]")
            ),
            screenshot_analysis_owner_id=(
                str(row["screenshot_analysis_owner_id"])
                if row["screenshot_analysis_owner_id"] is not None
                else None
            ),
            screenshot_analysis_lease_expires_at=screenshot_analysis_lease,
        )

    def begin_package(
        self,
        job_id: str,
        request_identity: str,
        owner_id: str,
        package: ProjectPackageView,
        request_payload: str | None = None,
    ) -> PackageAttempt:
        if (
            not owner_id
            or package.job_id != job_id
            or package.status is not ProjectPackageStage.CHECKING
            or package.stage is not ProjectPackageStage.CHECKING
        ):
            raise InvalidTransition("a package attempt must begin in checking")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if job is None:
                raise NotFound("job not found")
            existing = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if existing is not None:
                if existing["request_identity"] != request_identity:
                    raise PackageRequestConflict("a different package request already exists")
                stored = self._stored_package(existing)
                now = self.clock()
                live = (
                    stored.view.stage
                    in {ProjectPackageStage.CHECKING, ProjectPackageStage.PACKAGING}
                    and stored.lease_expires_at is not None
                    and stored.lease_expires_at > now
                )
                if stored.view.stage is not ProjectPackageStage.FAILED and (
                    stored.view.stage
                    in {
                        ProjectPackageStage.READY,
                        ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                    }
                    or live
                ):
                    return PackageAttempt(
                        stored.view,
                        stored.generation,
                        False,
                        stored.owner_id,
                        stored.lease_expires_at,
                    )
                generation = stored.generation + 1
                expires_at, lease_timestamp = self._new_lease()
                if not self._remove_screenshot_file(existing):
                    raise InvalidTransition("screenshot cleanup is pending")
                updated = connection.execute(
                    "UPDATE project_packages SET stage = ?, generation = ?, payload = ?, "
                    "artifact_path = NULL, owner_id = ?, lease_expires_at = ?, "
                    "screenshot_consent = NULL, screenshot_digest = NULL, screenshot_path = NULL, "
                    "screenshot_candidate_payload = NULL, screenshot_completed = 0, "
                    "screenshot_completion_diagnostics = NULL, "
                    "screenshot_analysis_owner_id = NULL, "
                    "screenshot_analysis_lease_expires_at = NULL, "
                    "request_payload = COALESCE(?, request_payload) "
                    "WHERE job_id = ? AND request_identity = ? AND generation = ? AND stage = ?",
                    (
                        package.stage,
                        generation,
                        package.model_dump_json(),
                        owner_id,
                        lease_timestamp,
                        request_payload,
                        job_id,
                        request_identity,
                        stored.generation,
                        stored.view.stage,
                    ),
                )
                if updated.rowcount != 1:
                    raise InvalidTransition("package retry lost its state lease")
                return PackageAttempt(package, generation, True, owner_id, expires_at)
            expires_at, lease_timestamp = self._new_lease()
            connection.execute(
                "INSERT INTO project_packages"
                "(job_id, request_identity, stage, generation, owner_id, lease_expires_at, payload, "
                "request_payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    request_identity,
                    package.stage,
                    1,
                    owner_id,
                    lease_timestamp,
                    package.model_dump_json(),
                    request_payload,
                ),
            )
        return PackageAttempt(package, 1, True, owner_id, expires_at)

    def await_screenshot_consent(
        self,
        job_id: str,
        request_identity: str,
        generation: int,
        owner_id: str,
        package: ProjectPackageView,
    ) -> ProjectPackageView:
        if (
            package.job_id != job_id
            or package.status is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
            or package.stage is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
            or package.screenshot_reason is None
        ):
            raise InvalidTransition("screenshot consent requires a safe reason")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE project_packages SET stage = ?, payload = ?, owner_id = NULL, "
                "lease_expires_at = NULL, screenshot_analysis_owner_id = NULL, "
                "screenshot_analysis_lease_expires_at = NULL "
                "WHERE job_id = ? AND request_identity = ? "
                "AND generation = ? AND stage = ? AND owner_id = ? AND lease_expires_at > ?",
                (
                    package.stage,
                    package.model_dump_json(),
                    job_id,
                    request_identity,
                    generation,
                    ProjectPackageStage.CHECKING,
                    owner_id,
                    self.clock().timestamp(),
                ),
            )
            if updated.rowcount != 1:
                raise InvalidTransition("package transition lost its state lease")
        return package

    def record_screenshot_consent(
        self, job_id: str, generation: int, approved: bool
    ) -> ScreenshotConsentRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("package not found")
            if int(row["generation"]) != generation:
                raise InvalidTransition("screenshot consent generation changed")
            recorded = row["screenshot_consent"]
            if recorded is not None:
                if bool(recorded) == approved:
                    cleanup_path = (
                        Path(row["screenshot_path"])
                        if not approved and row["screenshot_path"] is not None
                        else None
                    )
                    return ScreenshotConsentRecord(self._stored_package(row), cleanup_path)
                can_cancel_unfinished_screenshot = (
                    bool(recorded)
                    and not approved
                    and ProjectPackageStage(row["stage"])
                    is ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
                    and not bool(row["screenshot_completed"])
                )
                if not can_cancel_unfinished_screenshot:
                    raise PackageConsentConflict("screenshot consent is already recorded")
                cleanup_path = (
                    Path(row["screenshot_path"]) if row["screenshot_path"] is not None else None
                )
                updated = connection.execute(
                    "UPDATE project_packages SET screenshot_consent = 0, "
                    "screenshot_candidate_payload = NULL, screenshot_completed = 0, "
                    "screenshot_completion_diagnostics = NULL, "
                    "screenshot_analysis_owner_id = NULL, "
                    "screenshot_analysis_lease_expires_at = NULL "
                    "WHERE job_id = ? AND generation = ? AND stage = ? "
                    "AND screenshot_consent = 1 AND screenshot_completed = 0",
                    (
                        job_id,
                        generation,
                        ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                    ),
                )
                if updated.rowcount != 1:
                    raise PackageConsentConflict("screenshot consent is already recorded")
                fallback = connection.execute(
                    "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
                ).fetchone()
                if fallback is None:
                    raise NotFound("package not found")
                return ScreenshotConsentRecord(self._stored_package(fallback), cleanup_path)
            if (
                ProjectPackageStage(row["stage"])
                is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
            ):
                raise InvalidTransition("package is not awaiting screenshot consent")
            connection.execute(
                "UPDATE project_packages SET screenshot_consent = ? WHERE job_id = ? "
                "AND generation = ? AND screenshot_consent IS NULL",
                (int(approved), job_id, generation),
            )
            updated = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if updated is None:
                raise NotFound("package not found")
            return ScreenshotConsentRecord(self._stored_package(updated))

    def claim_screenshot_analysis(
        self,
        job_id: str,
        generation: int,
        digest: str,
        owner_id: str,
    ) -> ScreenshotAnalysisClaim:
        if not owner_id:
            raise InvalidTransition("screenshot analysis owner is required")
        now = self.clock().timestamp()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("package not found")
            if (
                int(row["generation"]) != generation
                or row["screenshot_digest"] != digest
                or row["screenshot_consent"] != 1
                or ProjectPackageStage(row["stage"])
                is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
            ):
                raise InvalidTransition("screenshot analysis is not authorized")
            if bool(row["screenshot_completed"]):
                return ScreenshotAnalysisClaim(self._stored_package(row), False)
            current_owner = row["screenshot_analysis_owner_id"]
            current_lease = row["screenshot_analysis_lease_expires_at"]
            live = (
                isinstance(current_owner, str)
                and current_lease is not None
                and float(current_lease) > now
            )
            if live:
                return ScreenshotAnalysisClaim(self._stored_package(row), current_owner == owner_id)
            _, lease_timestamp = self._new_screenshot_analysis_lease()
            updated = connection.execute(
                "UPDATE project_packages SET screenshot_analysis_owner_id = ?, "
                "screenshot_analysis_lease_expires_at = ? "
                "WHERE job_id = ? AND generation = ? AND stage = ? "
                "AND screenshot_consent = 1 AND screenshot_digest = ? "
                "AND screenshot_completed = 0 "
                "AND (screenshot_analysis_owner_id IS NULL "
                "OR screenshot_analysis_lease_expires_at IS NULL "
                "OR screenshot_analysis_lease_expires_at <= ?)",
                (
                    owner_id,
                    lease_timestamp,
                    job_id,
                    generation,
                    ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                    digest,
                    now,
                ),
            )
            current = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if current is None:
                raise NotFound("package not found")
            return ScreenshotAnalysisClaim(self._stored_package(current), updated.rowcount == 1)

    def attach_and_claim_screenshot_analysis(
        self,
        job_id: str,
        generation: int,
        digest: str,
        path: Path,
        owner_id: str,
    ) -> ScreenshotAnalysisStart:
        if not owner_id:
            raise InvalidTransition("screenshot analysis owner is required")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise InvalidTransition("screenshot digest is invalid")
        now = self.clock().timestamp()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("package not found")
            if int(row["generation"]) != generation:
                raise InvalidTransition("screenshot analysis is not authorized")
            if row["screenshot_consent"] != 1:
                raise ScreenshotConsentRequired("screenshot consent is required")
            if (
                ProjectPackageStage(row["stage"])
                is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
            ):
                raise InvalidTransition("screenshot analysis is not authorized")
            attached_digest = row["screenshot_digest"]
            if attached_digest is not None and attached_digest != digest:
                raise PackageConsentConflict("a different screenshot is already attached")
            if attached_digest == digest and bool(row["screenshot_completed"]):
                return ScreenshotAnalysisStart(
                    self._stored_package(row), ScreenshotAnalysisStartState.ACCEPTED
                )
            current_owner = row["screenshot_analysis_owner_id"]
            current_lease = row["screenshot_analysis_lease_expires_at"]
            if (
                attached_digest == digest
                and isinstance(current_owner, str)
                and current_lease is not None
                and float(current_lease) > now
            ):
                return ScreenshotAnalysisStart(
                    self._stored_package(row), ScreenshotAnalysisStartState.IN_PROGRESS
                )
            _, lease_timestamp = self._new_screenshot_analysis_lease()
            updated = connection.execute(
                "UPDATE project_packages SET screenshot_digest = ?, "
                "screenshot_path = CASE WHEN screenshot_digest IS NULL THEN ? "
                "ELSE screenshot_path END, screenshot_analysis_owner_id = ?, "
                "screenshot_analysis_lease_expires_at = ? "
                "WHERE job_id = ? AND generation = ? AND stage = ? "
                "AND screenshot_consent = 1 AND screenshot_completed = 0 "
                "AND (screenshot_digest IS NULL OR screenshot_digest = ?) "
                "AND (screenshot_analysis_owner_id IS NULL "
                "OR screenshot_analysis_lease_expires_at IS NULL "
                "OR screenshot_analysis_lease_expires_at <= ?)",
                (
                    digest,
                    str(path),
                    owner_id,
                    lease_timestamp,
                    job_id,
                    generation,
                    ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                    digest,
                    now,
                ),
            )
            current = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if current is None:
                raise NotFound("package not found")
            if updated.rowcount != 1:
                raise InvalidTransition("screenshot analysis is not authorized")
            return ScreenshotAnalysisStart(
                self._stored_package(current), ScreenshotAnalysisStartState.CLAIMED
            )

    def release_screenshot_analysis_claim(
        self,
        job_id: str,
        generation: int,
        digest: str,
        owner_id: str,
    ) -> bool:
        if not owner_id:
            raise InvalidTransition("screenshot analysis owner is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE project_packages SET screenshot_analysis_owner_id = NULL, "
                "screenshot_analysis_lease_expires_at = NULL "
                "WHERE job_id = ? AND generation = ? AND screenshot_digest = ? "
                "AND screenshot_analysis_owner_id = ? AND screenshot_completed = 0",
                (job_id, generation, digest, owner_id),
            )
            return updated.rowcount == 1

    def complete_screenshot_conversion(
        self,
        job_id: str,
        generation: int,
        digest: str,
        candidate: JobView | None,
        fallback_diagnostics: tuple[Diagnostic, ...] = (),
        *,
        claim_owner_id: str,
    ) -> ScreenshotConversionCommit:
        if not claim_owner_id:
            raise InvalidTransition("screenshot analysis owner is required")
        candidate_is_valid = candidate is not None and (
            candidate.job_id == job_id
            and candidate.artifact_sha256 is not None
            and candidate.status in {JobStatus.READY_FOR_REVIEW, JobStatus.CONVERSION_FAILED}
        )
        fallback_is_valid = candidate is None and bool(fallback_diagnostics)
        if not candidate_is_valid and not fallback_is_valid:
            raise InvalidTransition("screenshot conversion candidate is invalid")
        if candidate is not None and fallback_diagnostics:
            raise InvalidTransition("screenshot conversion outcome is ambiguous")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("package not found")
            baseline_row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if baseline_row is None:
                raise NotFound("job not found")
            baseline = JobView.model_validate_json(baseline_row["payload"])
            if candidate is not None and baseline.project_id != candidate.project_id:
                raise InvalidTransition("screenshot conversion project changed")
            now = self.clock().timestamp()
            matches = (
                int(row["generation"]) == generation
                and row["screenshot_digest"] == digest
                and row["screenshot_consent"] == 1
                and row["screenshot_analysis_owner_id"] == claim_owner_id
                and row["screenshot_analysis_lease_expires_at"] is not None
                and float(row["screenshot_analysis_lease_expires_at"]) > now
            )
            if (
                not matches
                or bool(row["screenshot_completed"])
                or ProjectPackageStage(row["stage"])
                is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
            ):
                return ScreenshotConversionCommit(self._stored_package(row), False)
            screenshot_removed = self._remove_screenshot_file(row)
            updated = connection.execute(
                "UPDATE project_packages SET screenshot_candidate_payload = ?, "
                "screenshot_completed = 1, screenshot_completion_diagnostics = ?, "
                "screenshot_path = CASE WHEN ? THEN NULL ELSE screenshot_path END, "
                "screenshot_analysis_owner_id = NULL, "
                "screenshot_analysis_lease_expires_at = NULL "
                "WHERE job_id = ? AND generation = ? AND stage = ? "
                "AND screenshot_consent = 1 AND screenshot_digest = ? "
                "AND screenshot_completed = 0 AND screenshot_analysis_owner_id = ? "
                "AND screenshot_analysis_lease_expires_at > ?",
                (
                    candidate.model_dump_json() if candidate is not None else None,
                    json.dumps(
                        [item.model_dump(mode="json") for item in fallback_diagnostics],
                        separators=(",", ":"),
                    ),
                    screenshot_removed,
                    job_id,
                    generation,
                    ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                    digest,
                    claim_owner_id,
                    now,
                ),
            )
            committed = updated.rowcount == 1
            current = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if current is None:
                raise NotFound("package not found")
            return ScreenshotConversionCommit(self._stored_package(current), committed)

    def attach_screenshot(
        self, job_id: str, generation: int, digest: str, path: Path
    ) -> StoredPackage:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise InvalidTransition("screenshot digest is invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("package not found")
            if int(row["generation"]) != generation:
                raise InvalidTransition("screenshot upload generation changed")
            if row["screenshot_digest"] is not None:
                if row["screenshot_digest"] != digest:
                    raise PackageConsentConflict("a different screenshot is already attached")
                return self._stored_package(row)
            if (
                ProjectPackageStage(row["stage"])
                is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
                or row["screenshot_consent"] != 1
            ):
                raise InvalidTransition("screenshot upload is not authorized")
            connection.execute(
                "UPDATE project_packages SET screenshot_digest = ?, screenshot_path = ? "
                "WHERE job_id = ? AND generation = ? AND screenshot_digest IS NULL",
                (digest, str(path), job_id, generation),
            )
            updated = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if updated is None:
                raise NotFound("package not found")
            return self._stored_package(updated)

    def resume_package_after_screenshot(
        self,
        job_id: str,
        generation: int,
        owner_id: str,
        package: ProjectPackageView,
    ) -> StoredPackage:
        if (
            not owner_id
            or package.job_id != job_id
            or package.status is not ProjectPackageStage.PACKAGING
            or package.stage is not ProjectPackageStage.PACKAGING
        ):
            raise InvalidTransition("resumed package must enter packaging")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("package not found")
            approved = row["screenshot_consent"] == 1
            upload_ready = row["screenshot_digest"] is not None
            conversion_completed = bool(row["screenshot_completed"])
            if (
                int(row["generation"]) != generation
                or ProjectPackageStage(row["stage"])
                is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
                or row["screenshot_consent"] is None
                or (approved and (not upload_ready or not conversion_completed))
            ):
                raise InvalidTransition("screenshot decision is incomplete")
            expires_at, lease_timestamp = self._new_lease()
            connection.execute(
                "UPDATE project_packages SET stage = ?, payload = ?, owner_id = ?, "
                "lease_expires_at = ? WHERE job_id = ? AND generation = ? AND stage = ?",
                (
                    package.stage,
                    package.model_dump_json(),
                    owner_id,
                    lease_timestamp,
                    job_id,
                    generation,
                    ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if updated is None:
                raise NotFound("package not found")
            stored = self._stored_package(updated)
            return StoredPackage(
                view=stored.view,
                artifact_path=stored.artifact_path,
                request_identity=stored.request_identity,
                generation=stored.generation,
                owner_id=stored.owner_id,
                lease_expires_at=expires_at,
                screenshot_consent=stored.screenshot_consent,
                screenshot_digest=stored.screenshot_digest,
                screenshot_path=stored.screenshot_path,
                request_payload=stored.request_payload,
                screenshot_candidate=stored.screenshot_candidate,
                screenshot_completed=stored.screenshot_completed,
                screenshot_completion_diagnostics=stored.screenshot_completion_diagnostics,
                screenshot_analysis_owner_id=stored.screenshot_analysis_owner_id,
                screenshot_analysis_lease_expires_at=(stored.screenshot_analysis_lease_expires_at),
            )

    def clear_screenshot_path(self, job_id: str, generation: int, digest: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE project_packages SET screenshot_path = NULL WHERE job_id = ? "
                "AND generation = ? AND screenshot_digest = ?",
                (job_id, generation, digest),
            )

    def get_package(self, job_id: str, request_identity: str | None = None) -> StoredPackage:
        self.recover_expired_packages(job_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise NotFound("package not found")
        if request_identity is not None and row["request_identity"] != request_identity:
            raise PackageRequestConflict("a different package request already exists")
        return self._stored_package(row)

    def transition_package(
        self,
        job_id: str,
        request_identity: str,
        generation: int,
        owner_id: str | None,
        expected_stages: tuple[ProjectPackageStage, ...],
        package: ProjectPackageView,
        artifact_path: Path | None | object = _PRESERVE_ARTIFACT,
    ) -> ProjectPackageView:
        if package.job_id != job_id or package.status is not package.stage:
            raise InvalidTransition("package status and stage must match")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM project_packages WHERE job_id = ?", (job_id,)
            ).fetchone()
            if existing is None:
                raise NotFound("package not found")
            if existing["request_identity"] != request_identity:
                raise PackageRequestConflict("package request identity changed")
            current = ProjectPackageStage(existing["stage"])
            active = current in {
                ProjectPackageStage.CHECKING,
                ProjectPackageStage.PACKAGING,
            }
            lease_is_live = (
                existing["lease_expires_at"] is not None
                and float(existing["lease_expires_at"]) > self.clock().timestamp()
            )
            if (
                int(existing["generation"]) != generation
                or current not in expected_stages
                or package.stage not in _PACKAGE_TRANSITIONS.get(current, set())
                or (active and (existing["owner_id"] != owner_id or not lease_is_live))
                or (not active and owner_id is not None)
            ):
                raise InvalidTransition("package transition lost its state lease")
            assignments = "stage = ?, payload = ?"
            values: list[object] = [package.stage, package.model_dump_json()]
            if package.stage in {ProjectPackageStage.READY, ProjectPackageStage.FAILED}:
                screenshot_removed = self._remove_screenshot_file(existing)
                assignments += (
                    ", owner_id = NULL, lease_expires_at = NULL, "
                    "screenshot_path = CASE WHEN ? THEN NULL ELSE screenshot_path END, "
                    "screenshot_analysis_owner_id = NULL, "
                    "screenshot_analysis_lease_expires_at = NULL"
                )
                values.append(screenshot_removed)
            else:
                _, lease_timestamp = self._new_lease()
                assignments += ", lease_expires_at = ?"
                values.append(lease_timestamp)
            if artifact_path is not _PRESERVE_ARTIFACT:
                assignments += ", artifact_path = ?"
                values.append(None if artifact_path is None else str(artifact_path))
            values.extend((job_id, request_identity, generation, current))
            owner_predicate = "owner_id IS NULL" if owner_id is None else "owner_id = ?"
            if owner_id is not None:
                values.append(owner_id)
            updated = connection.execute(
                f"UPDATE project_packages SET {assignments} WHERE job_id = ? "
                f"AND request_identity = ? AND generation = ? AND stage = ? AND {owner_predicate}",
                values,
            )
            if updated.rowcount != 1:
                raise InvalidTransition("package transition lost its state lease")
        return package

    def renew_package_lease(
        self,
        job_id: str,
        request_identity: str,
        generation: int,
        owner_id: str,
    ) -> datetime:
        expires_at, lease_timestamp = self._new_lease()
        now = self.clock().timestamp()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE project_packages SET lease_expires_at = ? WHERE job_id = ? "
                "AND request_identity = ? AND generation = ? AND owner_id = ? "
                "AND stage IN (?, ?) AND lease_expires_at > ?",
                (
                    lease_timestamp,
                    job_id,
                    request_identity,
                    generation,
                    owner_id,
                    ProjectPackageStage.CHECKING,
                    ProjectPackageStage.PACKAGING,
                    now,
                ),
            )
            if updated.rowcount != 1:
                raise InvalidTransition("package lease is no longer owned")
        return expires_at

    def recover_expired_packages(self, job_id: str | None = None) -> int:
        recovered = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            query = (
                "SELECT job_id, stage, generation, screenshot_path FROM project_packages "
                "WHERE stage IN (?, ?) AND (lease_expires_at IS NULL OR lease_expires_at <= ?)"
            )
            parameters: list[object] = [
                ProjectPackageStage.CHECKING,
                ProjectPackageStage.PACKAGING,
                self.clock().timestamp(),
            ]
            if job_id is not None:
                query += " AND job_id = ?"
                parameters.append(job_id)
            rows = connection.execute(query, parameters).fetchall()
            for row in rows:
                screenshot_removed = self._remove_screenshot_file(row)
                failed = ProjectPackageView(
                    job_id=row["job_id"],
                    status=ProjectPackageStage.FAILED,
                    stage=ProjectPackageStage.FAILED,
                    progress=90,
                    diagnostics=(
                        Diagnostic(
                            code="package_interrupted",
                            severity=Severity.ERROR,
                            message="Project package build was interrupted. Retry the request.",
                        ),
                    ),
                )
                updated = connection.execute(
                    "UPDATE project_packages SET stage = ?, payload = ?, owner_id = NULL, "
                    "lease_expires_at = NULL, "
                    "screenshot_path = CASE WHEN ? THEN NULL ELSE screenshot_path END, "
                    "screenshot_analysis_owner_id = NULL, "
                    "screenshot_analysis_lease_expires_at = NULL "
                    "WHERE job_id = ? AND generation = ? AND stage = ? "
                    "AND (lease_expires_at IS NULL OR lease_expires_at <= ?)",
                    (
                        ProjectPackageStage.FAILED,
                        failed.model_dump_json(),
                        screenshot_removed,
                        row["job_id"],
                        row["generation"],
                        row["stage"],
                        self.clock().timestamp(),
                    ),
                )
                recovered += updated.rowcount
        return recovered

    def cleanup_terminal_screenshot_paths(self) -> int:
        cleaned = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT job_id, generation, screenshot_path FROM project_packages "
                "WHERE stage IN (?, ?) AND (screenshot_path IS NOT NULL "
                "OR screenshot_analysis_owner_id IS NOT NULL "
                "OR screenshot_analysis_lease_expires_at IS NOT NULL)",
                (ProjectPackageStage.READY, ProjectPackageStage.FAILED),
            ).fetchall()
            for row in rows:
                screenshot_removed = self._remove_screenshot_file(row)
                updated = connection.execute(
                    "UPDATE project_packages SET "
                    "screenshot_path = CASE WHEN ? THEN NULL ELSE screenshot_path END, "
                    "screenshot_analysis_owner_id = NULL, "
                    "screenshot_analysis_lease_expires_at = NULL "
                    "WHERE job_id = ? AND generation = ? AND stage IN (?, ?)",
                    (
                        screenshot_removed,
                        row["job_id"],
                        row["generation"],
                        ProjectPackageStage.READY,
                        ProjectPackageStage.FAILED,
                    ),
                )
                cleaned += updated.rowcount
        return cleaned

    def list_screenshot_paths(self) -> tuple[Path, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT screenshot_path FROM project_packages WHERE screenshot_path IS NOT NULL"
            ).fetchall()
        return tuple(Path(row["screenshot_path"]) for row in rows)

    def _save_job(self, connection: sqlite3.Connection, job: JobView) -> None:
        connection.execute(
            "UPDATE jobs SET status = ?, payload = ? WHERE job_id = ?",
            (job.status, job.model_dump_json(), job.job_id),
        )

    def approve_job(self, job_id: str) -> JobView:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("job not found")
            job = JobView.model_validate_json(row["payload"])
            if job.status is JobStatus.APPROVED:
                return job
            if job.status is not JobStatus.READY_FOR_REVIEW:
                raise InvalidTransition("job is not ready for approval")
            job = job.model_copy(update={"status": JobStatus.APPROVED})
            self._save_job(connection, job)
            return job

    def reject_job(self, job_id: str) -> JobView:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise NotFound("job not found")
            job = JobView.model_validate_json(row["payload"])
            if job.status is JobStatus.REJECTED:
                return job
            if job.status is not JobStatus.READY_FOR_REVIEW:
                raise InvalidTransition("job is not ready for rejection")
            job = job.model_copy(update={"status": JobStatus.REJECTED})
            self._save_job(connection, job)
            return job

    def claim_next(self, agent_id: str) -> JobView | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT jobs.payload
                FROM jobs
                JOIN project_bindings USING(project_id)
                WHERE jobs.status = ? AND project_bindings.agent_id = ?
                ORDER BY jobs.job_id
                LIMIT 1
                """,
                (JobStatus.APPROVED, agent_id),
            ).fetchone()
            if row is None:
                return None
            job = JobView.model_validate_json(row["payload"])
            job = job.model_copy(update={"status": JobStatus.APPLYING})
            self._save_job(connection, job)
            return job

    def get_assignment_artifact_job(self, agent_id: str, job_id: str) -> JobView:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT jobs.payload, project_bindings.agent_id
                FROM jobs
                JOIN project_bindings USING(project_id)
                WHERE jobs.job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            raise NotFound("job or project binding not found")
        if row["agent_id"] != agent_id:
            raise OwnershipMismatch("assignment does not belong to this agent")
        job = JobView.model_validate_json(row["payload"])
        if job.status is not JobStatus.APPLYING:
            raise InvalidTransition("job is not applying")
        return job

    def record_apply_result(self, result: ApplyResult) -> JobView:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload FROM apply_results WHERE job_id = ?", (result.job_id,)
            ).fetchone()
            if existing is not None:
                if ApplyResult.model_validate_json(existing["payload"]) != result:
                    raise InvalidTransition("a different terminal result already exists")
                return self.get_job(result.job_id)

            row = connection.execute(
                """
                SELECT jobs.payload, project_bindings.agent_id
                FROM jobs
                JOIN project_bindings USING(project_id)
                WHERE jobs.job_id = ?
                """,
                (result.job_id,),
            ).fetchone()
            if row is None:
                raise NotFound("job or project binding not found")
            job = JobView.model_validate_json(row["payload"])
            if row["agent_id"] != result.agent_id or job.project_id != result.project_id:
                raise OwnershipMismatch("apply result does not own this job")
            if job.status is not JobStatus.APPLYING:
                raise InvalidTransition("job is not applying")
            status = JobStatus.APPLIED if result.status is ApplyStatus.APPLIED else JobStatus.FAILED
            job = job.model_copy(update={"status": status})
            connection.execute(
                "INSERT INTO apply_results(job_id, payload) VALUES (?, ?)",
                (result.job_id, result.model_dump_json()),
            )
            self._save_job(connection, job)
            return job
