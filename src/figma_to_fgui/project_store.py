from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from figma_to_fgui.paths import safe_relative_path
from figma_to_fgui.uploaded_project import UploadedProjectVersion, _fingerprint, _source_paths

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_PROJECT_ID = re.compile(r"^[0-9a-f]{32}$")


class ProjectIntegrityError(ValueError):
    pass


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.projects = root / "projects"

    @property
    def _artifacts(self) -> Path:
        return self.projects / "artifacts"

    @property
    def _versions(self) -> Path:
        return self.projects / "versions"

    @staticmethod
    def _metadata_payload(version: UploadedProjectVersion) -> bytes:
        return json.dumps(
            version.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @classmethod
    def _metadata_document(cls, version: UploadedProjectVersion) -> bytes:
        payload = cls._metadata_payload(version)
        return json.dumps(
            {"payload": json.loads(payload), "sha256": hashlib.sha256(payload).hexdigest()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _metadata_path(self, project_id: str) -> Path:
        if not _PROJECT_ID.fullmatch(project_id):
            raise ProjectIntegrityError("invalid project identifier")
        return self._versions / f"{project_id}.json"

    @staticmethod
    def _artifact_for_fingerprint(artifacts: Path, fingerprint: str) -> Path:
        if not _FINGERPRINT.fullmatch(fingerprint):
            raise ProjectIntegrityError("invalid project fingerprint")
        return artifacts / fingerprint

    def _read_metadata(self, project_id: str) -> UploadedProjectVersion:
        try:
            document: Any = json.loads(self._metadata_path(project_id).read_text("utf-8"))
            payload = document["payload"]
            digest = document["sha256"]
            serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if not isinstance(digest, str) or hashlib.sha256(serialized).hexdigest() != digest:
                raise ProjectIntegrityError("project metadata integrity check failed")
            version = UploadedProjectVersion.model_validate(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValidationError) as error:
            raise ProjectIntegrityError("project metadata is unavailable") from error
        if version.project_id != project_id:
            raise ProjectIntegrityError("project metadata integrity check failed")
        return version

    def _artifact_for(self, version: UploadedProjectVersion) -> Path:
        artifact = self._artifact_for_fingerprint(self._artifacts, version.fingerprint)
        if not artifact.is_dir():
            raise ProjectIntegrityError("project artifact is unavailable")
        if _fingerprint(artifact, _source_paths(artifact)) != version.fingerprint:
            raise ProjectIntegrityError("project artifact integrity check failed")
        for file in version.files:
            if file.thumbnail_relative_path is None or file.thumbnail_sha256 is None:
                continue
            thumbnail = artifact / safe_relative_path(file.thumbnail_relative_path)
            try:
                thumbnail_hash = hashlib.sha256(thumbnail.read_bytes()).hexdigest()
            except OSError as error:
                raise ProjectIntegrityError("project artifact integrity check failed") from error
            if thumbnail_hash != file.thumbnail_sha256:
                raise ProjectIntegrityError("project artifact integrity check failed")
        return artifact

    def _write_metadata(self, version: UploadedProjectVersion) -> None:
        target = self._metadata_path(version.project_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(self._metadata_document(version))
        os.replace(temporary, target)

    def _copy_artifact(self, version: UploadedProjectVersion, extracted_root: Path) -> Path:
        target = self._artifact_for_fingerprint(self._artifacts, version.fingerprint)
        if target.exists():
            return self._artifact_for(version)
        self._artifacts.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        committed = False
        try:
            temporary = Path(tempfile.mkdtemp(prefix="tmp-", dir=self._artifacts))
            shutil.copytree(extracted_root, temporary, dirs_exist_ok=True)
            try:
                os.replace(temporary, target)
                committed = True
            except OSError:
                if target.exists():
                    return self._artifact_for(version)
                raise
            return target
        finally:
            if not committed and temporary is not None and temporary.exists():
                shutil.rmtree(temporary)

    def create(self, version: UploadedProjectVersion, extracted_root: Path) -> UploadedProjectVersion:
        if _fingerprint(extracted_root, _source_paths(extracted_root)) != version.fingerprint:
            raise ProjectIntegrityError("uploaded project integrity check failed")
        metadata = self._metadata_path(version.project_id)
        if metadata.exists():
            if self.get(version.project_id) != version:
                raise ProjectIntegrityError("project version already exists")
            return version
        self._copy_artifact(version, extracted_root)
        self._write_metadata(version)
        return version

    def replace_version(self, version: UploadedProjectVersion, extracted_root: Path) -> UploadedProjectVersion:
        if _fingerprint(extracted_root, _source_paths(extracted_root)) != version.fingerprint:
            raise ProjectIntegrityError("uploaded project integrity check failed")
        self._copy_artifact(version, extracted_root)
        self._write_metadata(version)
        return version

    def get(self, project_id: str) -> UploadedProjectVersion:
        version = self._read_metadata(project_id)
        self._artifact_for(version)
        return version

    def artifact_path(self, project_id: str) -> Path:
        return self._artifact_for(self._read_metadata(project_id))

    def thumbnail_path(self, project_id: str, asset_id: str) -> Path:
        version = self.get(project_id)
        matches = [
            file.thumbnail_relative_path
            for file in version.files
            if file.asset_id == asset_id and file.thumbnail_relative_path is not None
        ]
        if len(matches) != 1:
            raise ProjectIntegrityError("unknown image asset")
        relative_path = safe_relative_path(matches[0])
        artifact = self._artifact_for(version)
        thumbnail = artifact / relative_path
        try:
            thumbnail.resolve().relative_to(artifact.resolve())
        except ValueError as error:
            raise ProjectIntegrityError("thumbnail integrity check failed") from error
        if not thumbnail.is_file():
            raise ProjectIntegrityError("thumbnail integrity check failed")
        return thumbnail
