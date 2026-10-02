from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from figma_to_fgui.hifi_replacement_models import (
    HIFI_MAPPING_POLICY_REVISION,
    HifiEditorVerification,
    HifiMappingDraft,
    HifiReplacementReview,
    HifiReplacementView,
    HifiTargetRef,
)


class HifiReplacementStoreError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StoredHifiReplacement:
    view: HifiReplacementView
    owner_device_id: str
    mapping: HifiMappingDraft
    review: HifiReplacementReview | None
    artifact_path: Path | None
    artifact_name: str | None
    artifact_sha256: str | None
    editor_verification: HifiEditorVerification | None

    @property
    def approval_ready(self) -> bool:
        review, verification = self.review, self.editor_verification
        return bool(
            self.mapping.policy_revision == HIFI_MAPPING_POLICY_REVISION
            and self.mapping.unresolved_count == 0
            and review is not None and review.policy_revision == HIFI_MAPPING_POLICY_REVISION
            and review.approvable and review.protected_checks_passed
            and review.parse_coverage_complete
            and verification is not None and verification.approvable
            and verification.full_frame and verification.render_captured
            and verification.project_opened and verification.component_opened
            and verification.candidate_sha256 == self.artifact_sha256
            and review.candidate_sha256 == self.artifact_sha256
        )


class HifiReplacementStore:
    def __init__(self, data_dir: Path) -> None:
        self._database = data_dir / "hifi-replacements.db"
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hifi_replacements (
                    session_id TEXT PRIMARY KEY,
                    owner_device_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    selection_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target_json TEXT NOT NULL,
                    mapping_json TEXT NOT NULL,
                    review_json TEXT,
                    artifact_path TEXT,
                    artifact_name TEXT,
                    artifact_sha256 TEXT,
                    editor_checks_json TEXT,
                    editor_verification_json TEXT,
                    UNIQUE(owner_device_id, idempotency_key)
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(hifi_replacements)")
            }
            if "editor_checks_json" not in columns:
                connection.execute(
                    "ALTER TABLE hifi_replacements ADD COLUMN editor_checks_json TEXT"
                )
            if "editor_verification_json" not in columns:
                connection.execute(
                    "ALTER TABLE hifi_replacements ADD COLUMN editor_verification_json TEXT"
                )

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredHifiReplacement:
        target = HifiTargetRef.model_validate_json(row["target_json"])
        mapping = HifiMappingDraft.model_validate_json(row["mapping_json"])
        review = (
            HifiReplacementReview.model_validate_json(row["review_json"])
            if row["review_json"]
            else None
        )
        editor_verification = (
            HifiEditorVerification.model_validate_json(row["editor_verification_json"])
            if row["editor_verification_json"]
            else None
        )
        view = HifiReplacementView(
            version=1,
            session_id=row["session_id"],
            status=row["status"],
            selection_id=row["selection_id"],
            target=target,
            mapping_revision=mapping.mapping_revision,
            unresolved_count=mapping.unresolved_count,
            artifact_ready=row["artifact_path"] is not None,
        )
        return StoredHifiReplacement(
            view=view,
            owner_device_id=row["owner_device_id"],
            mapping=mapping,
            review=review,
            artifact_path=Path(row["artifact_path"]) if row["artifact_path"] else None,
            artifact_name=row["artifact_name"],
            artifact_sha256=row["artifact_sha256"],
            editor_verification=editor_verification,
        )

    def begin(
        self,
        owner_device_id: str,
        selection_id: str,
        target: HifiTargetRef,
        mapping: HifiMappingDraft,
        idempotency_key: str,
    ) -> StoredHifiReplacement:
        # Mapping policy is part of the meaning of a replacement request. A
        # caller may legitimately reuse the same request key after a policy
        # upgrade; returning a cached older-policy session to current-policy
        # code would skip the new semantic mapping entirely. Namespace
        # idempotency by the effective policy so every policy gets one stable
        # session of its own.
        policy_key = f"{idempotency_key}:policy:{mapping.policy_revision}"
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM hifi_replacements WHERE owner_device_id=? AND idempotency_key=?",
                (owner_device_id, policy_key),
            ).fetchone()
            if existing is not None:
                return self._stored(existing)
            session_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO hifi_replacements (
                    session_id, owner_device_id, idempotency_key, project_id,
                    selection_id, status, target_json, mapping_json, review_json,
                    artifact_path, artifact_name, artifact_sha256, editor_checks_json,
                    editor_verification_json
                ) VALUES (?, ?, ?, ?, ?, 'mapping', ?, ?, NULL, NULL, NULL, NULL, NULL, NULL)
                """,
                (
                    session_id,
                    owner_device_id,
                    policy_key,
                    target.project_id,
                    selection_id,
                    target.model_dump_json(),
                    mapping.model_dump_json(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM hifi_replacements WHERE session_id=?", (session_id,)
            ).fetchone()
        return self._stored(cast(sqlite3.Row, row))

    def get(self, session_id: str, owner_device_id: str) -> StoredHifiReplacement:
        if len(session_id) != 32:
            raise HifiReplacementStoreError("hifi_replacement_not_found")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hifi_replacements WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None or row["owner_device_id"] != owner_device_id:
            raise HifiReplacementStoreError("hifi_replacement_not_found")
        return self._stored(row)

    def save_mapping(
        self,
        session_id: str,
        owner_device_id: str,
        expected_revision: int,
        mapping: HifiMappingDraft,
    ) -> StoredHifiReplacement:
        current = self.get(session_id, owner_device_id)
        if current.mapping.mapping_revision != expected_revision:
            raise HifiReplacementStoreError("hifi_mapping_stale")
        if current.view.status not in {"mapping", "building", "review_ready", "failed"}:
            raise HifiReplacementStoreError("hifi_candidate_stale")
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE hifi_replacements
                SET status='mapping', mapping_json=?, review_json=NULL,
                    artifact_path=NULL, artifact_name=NULL, artifact_sha256=NULL,
                    editor_verification_json=NULL
                WHERE session_id=? AND owner_device_id=? AND mapping_json=?
                """,
                (
                    mapping.model_dump_json(),
                    session_id,
                    owner_device_id,
                    current.mapping.model_dump_json(),
                ),
            )
            if updated.rowcount != 1:
                raise HifiReplacementStoreError("hifi_mapping_stale")
        return self.get(session_id, owner_device_id)

    def mark_building(
        self, session_id: str, owner_device_id: str, mapping_revision: int
    ) -> StoredHifiReplacement:
        current = self.get(session_id, owner_device_id)
        if current.mapping.policy_revision != HIFI_MAPPING_POLICY_REVISION:
            raise HifiReplacementStoreError("hifi_mapping_policy_stale")
        if current.mapping.mapping_revision != mapping_revision:
            raise HifiReplacementStoreError("hifi_mapping_stale")
        if current.mapping.unresolved_count:
            raise HifiReplacementStoreError("hifi_mapping_incomplete")
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE hifi_replacements SET status='building'
                WHERE session_id=? AND owner_device_id=?
                  AND status IN ('mapping', 'review_ready', 'failed')
                  AND mapping_json=?
                """,
                (
                    session_id,
                    owner_device_id,
                    current.mapping.model_dump_json(),
                ),
            )
            if updated.rowcount != 1:
                raise HifiReplacementStoreError("hifi_build_in_progress")
        return self.get(session_id, owner_device_id)

    def publish_candidate(
        self,
        session_id: str,
        owner_device_id: str,
        mapping_revision: int,
        review: HifiReplacementReview,
        artifact_path: Path,
        artifact_name: str,
        artifact_sha256: str,
    ) -> StoredHifiReplacement:
        current = self.get(session_id, owner_device_id)
        if current.mapping.mapping_revision != mapping_revision or current.view.status != "building":
            raise HifiReplacementStoreError("hifi_candidate_stale")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE hifi_replacements
                SET status='review_ready', review_json=?, artifact_path=?,
                    artifact_name=?, artifact_sha256=?, editor_verification_json=NULL
                WHERE session_id=?
                """,
                (
                    review.model_dump_json(),
                    str(artifact_path),
                    artifact_name,
                    artifact_sha256,
                    session_id,
                ),
            )
        return self.get(session_id, owner_device_id)

    def save_editor_verification(
        self,
        session_id: str,
        owner_device_id: str,
        verification: HifiEditorVerification,
    ) -> StoredHifiReplacement:
        current = self.get(session_id, owner_device_id)
        if current.view.status not in {"review_ready", "approved"}:
            raise HifiReplacementStoreError("hifi_candidate_stale")
        if current.artifact_sha256 != verification.candidate_sha256:
            raise HifiReplacementStoreError("hifi_candidate_stale")
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE hifi_replacements SET editor_verification_json=?
                WHERE session_id=? AND owner_device_id=? AND artifact_sha256=?
                """,
                (
                    verification.model_dump_json(),
                    session_id,
                    owner_device_id,
                    verification.candidate_sha256,
                ),
            )
            if updated.rowcount != 1:
                raise HifiReplacementStoreError("hifi_candidate_stale")
        return self.get(session_id, owner_device_id)

    def approve(
        self, session_id: str, owner_device_id: str, editor_checks_json: str
    ) -> StoredHifiReplacement:
        current = self.get(session_id, owner_device_id)
        if current.view.status != "review_ready":
            raise HifiReplacementStoreError("hifi_candidate_stale")
        if not current.approval_ready:
            raise HifiReplacementStoreError("hifi_download_blocked")
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE hifi_replacements
                SET status='approved', editor_checks_json=?
                WHERE session_id=? AND owner_device_id=? AND status='review_ready'
                """,
                (editor_checks_json, session_id, owner_device_id),
            )
            if updated.rowcount != 1:
                raise HifiReplacementStoreError("hifi_candidate_stale")
        return self.get(session_id, owner_device_id)

    def reject(self, session_id: str, owner_device_id: str) -> StoredHifiReplacement:
        current = self.get(session_id, owner_device_id)
        if current.view.status not in {"mapping", "review_ready"}:
            raise HifiReplacementStoreError("hifi_candidate_stale")
        with self._connect() as connection:
            connection.execute(
                "UPDATE hifi_replacements SET status='rejected' WHERE session_id=?",
                (session_id,),
            )
        return self.get(session_id, owner_device_id)

    def mark_failed(
        self,
        session_id: str,
        owner_device_id: str,
        mapping_revision: int,
    ) -> StoredHifiReplacement:
        current = self.get(session_id, owner_device_id)
        if current.mapping.mapping_revision != mapping_revision:
            return current
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE hifi_replacements SET status='failed'
                WHERE session_id=? AND owner_device_id=? AND status='building'
                """,
                (session_id, owner_device_id),
            )
        return self.get(session_id, owner_device_id)

    @staticmethod
    def verified_artifact(stored: StoredHifiReplacement) -> Path:
        if stored.artifact_path is None or stored.artifact_sha256 is None:
            raise HifiReplacementStoreError("hifi_download_blocked")
        try:
            digest = hashlib.sha256(stored.artifact_path.read_bytes()).hexdigest()
        except OSError as error:
            raise HifiReplacementStoreError("hifi_build_failed") from error
        if digest != stored.artifact_sha256:
            raise HifiReplacementStoreError("hifi_build_failed")
        return stored.artifact_path