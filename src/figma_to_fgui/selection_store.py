from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from figma_to_fgui.figma_selection import (
    SelectionError,
    SelectionLimits,
    SelectionManifest,
    SelectionVersion,
    validate_selection_manifest,
)
from figma_to_fgui.image_preview import encode_webp_preview, encode_webp_preview_path
from figma_to_fgui.svg_security import MAX_SVG_BYTES, validate_safe_svg

_UPLOAD_TTL = timedelta(hours=1)
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
_RASTER_MIME = {"image/png": b"\x89PNG\r\n\x1a\n", "image/webp": b"RIFF"}
class UploadSession:
    def __init__(self, upload_id: str, state: str) -> None:
        self.upload_id = upload_id
        self.state = state

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UploadSession) and (self.upload_id, self.state) == (
            other.upload_id,
            other.state,
        )


class SelectionStore:
    def __init__(self, data_dir: Path, limits: SelectionLimits | None = None) -> None:
        self._data_dir = data_dir
        self._limits = limits or SelectionLimits()
        self._uploads = data_dir / "figma-uploads"
        self._selections = data_dir / "selections"
        self._database = data_dir / "selection.db"
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    @property
    def max_resource_bytes(self) -> int:
        return self._limits.max_resource_bytes

    def initialize(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS selection_uploads (
                    upload_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    manifest TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    selection_id TEXT,
                    UNIQUE(device_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS selection_upload_resources (
                    upload_id TEXT NOT NULL,
                    resource_key TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    declared_size INTEGER NOT NULL,
                    actual_size INTEGER,
                    sha256 TEXT,
                    PRIMARY KEY(upload_id, resource_key)
                );
                CREATE TABLE IF NOT EXISTS selections (
                    selection_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    manifest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    preview_count INTEGER NOT NULL
                );
                """
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _manifest_payload(manifest: SelectionManifest) -> bytes:
        return json.dumps(
            manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @staticmethod
    def _version(row: sqlite3.Row) -> SelectionVersion:
        return SelectionVersion(
            selection_id=row["selection_id"],
            device_id=row["device_id"],
            fingerprint=row["fingerprint"],
            manifest=SelectionManifest.model_validate_json(row["manifest"]),
            created_at=datetime.fromtimestamp(row["created_at"], UTC),
            preview_count=row["preview_count"],
        )

    def _upload_row(
        self, connection: sqlite3.Connection, upload_id: str, device_id: str
    ) -> sqlite3.Row:
        if not _UPLOAD_ID.fullmatch(upload_id):
            raise SelectionError("selection_not_found")
        row = connection.execute(
            "SELECT * FROM selection_uploads WHERE upload_id = ?", (upload_id,)
        ).fetchone()
        if row is None:
            raise SelectionError("selection_not_found")
        if row["device_id"] != device_id:
            raise SelectionError("selection_owner_denied")
        if row["state"] != "committed" and row["expires_at"] <= self._now().timestamp():
            raise SelectionError("selection_upload_expired")
        return cast(sqlite3.Row, row)

    def create_upload(self, device_id: str, idempotency_key: str) -> UploadSession:
        if not idempotency_key or len(idempotency_key) > 200:
            raise SelectionError("invalid_selection_upload")
        now = self._now().timestamp()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT upload_id, state FROM selection_uploads WHERE device_id = ? AND idempotency_key = ?",
                (device_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return UploadSession(existing["upload_id"], existing["state"])
            upload_id = uuid.uuid4().hex
            try:
                connection.execute(
                    "INSERT INTO selection_uploads VALUES (?, ?, ?, ?, NULL, ?, ?, NULL)",
                    (upload_id, device_id, idempotency_key, "created", now, now + _UPLOAD_TTL.total_seconds()),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT upload_id, state FROM selection_uploads WHERE device_id = ? AND idempotency_key = ?",
                    (device_id, idempotency_key),
                ).fetchone()
                if existing is None:
                    raise
                return UploadSession(existing["upload_id"], existing["state"])
        (self._uploads / upload_id).mkdir(parents=True, exist_ok=True)
        return UploadSession(upload_id, "created")

    def put_manifest(
        self, upload_id: str, device_id: str, manifest: SelectionManifest
    ) -> UploadSession:
        manifest = validate_selection_manifest(manifest, self._limits)
        payload = self._manifest_payload(manifest).decode("utf-8")
        with self._connect() as connection:
            row = self._upload_row(connection, upload_id, device_id)
            if row["state"] != "created":
                raise SelectionError("selection_upload_state")
            state = "manifest_received"
            connection.execute(
                "UPDATE selection_uploads SET manifest = ?, state = ? WHERE upload_id = ?",
                (payload, state, upload_id),
            )
            connection.executemany(
                "INSERT INTO selection_upload_resources (upload_id, resource_key, mime_type, declared_size) VALUES (?, ?, ?, ?)",
                [(upload_id, item.key, item.mime_type, item.size) for item in manifest.resources],
            )
        return UploadSession(upload_id, state)

    @staticmethod
    def _validate_svg(content: bytes) -> None:
        try:
            validate_safe_svg(content)
        except ValueError as error:
            raise SelectionError("unsupported_selection_content") from error

    @classmethod
    def _validate_svg_path(cls, path: Path) -> None:
        if path.stat().st_size > MAX_SVG_BYTES:
            raise SelectionError("unsupported_selection_content")
        try:
            content = path.read_bytes()
        except OSError as error:
            raise SelectionError("unsupported_selection_content") from error
        cls._validate_svg(content)

    @staticmethod
    def _resource_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_raster_path(mime_type: str, path: Path) -> bytes:
        with path.open("rb") as source:
            header = source.read(12)
        if not header.startswith(_RASTER_MIME[mime_type]) or (mime_type == "image/webp" and header[8:12] != b"WEBP"):
            raise SelectionError("unsupported_selection_content")
        preview = encode_webp_preview_path(path)
        if preview is None:
            raise SelectionError("unsupported_selection_content")
        return preview[2]

    def prepare_resource(self, upload_id: str, device_id: str, resource_key: str, mime_type: str) -> Path:
        with self._connect() as connection:
            row = self._upload_row(connection, upload_id, device_id)
            resource = connection.execute(
                "SELECT * FROM selection_upload_resources WHERE upload_id = ? AND resource_key = ?", (upload_id, resource_key)
            ).fetchone()
            if row["state"] not in {"resources_pending", "manifest_received"} or resource is None:
                raise SelectionError("selection_not_found")
            if resource["actual_size"] is not None or mime_type.split(";", 1)[0].strip().lower() != resource["mime_type"]:
                raise SelectionError("selection_resource_duplicate")
        incoming = self._uploads / upload_id / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="resource-", dir=incoming)
        os.close(descriptor)
        return Path(name)

    def put_resource_path(self, upload_id: str, device_id: str, resource_key: str, mime_type: str, path: Path) -> UploadSession:
        size = path.stat().st_size
        with self._connect() as connection:
            row = self._upload_row(connection, upload_id, device_id)
            resource = connection.execute("SELECT * FROM selection_upload_resources WHERE upload_id = ? AND resource_key = ?", (upload_id, resource_key)).fetchone()
            if row["state"] not in {"resources_pending", "manifest_received"} or resource is None:
                raise SelectionError("selection_not_found")
            if resource["actual_size"] is not None:
                raise SelectionError("selection_resource_duplicate")
            if mime_type.split(";", 1)[0].strip().lower() != resource["mime_type"]:
                raise SelectionError("selection_resource_mime")
            if size != resource["declared_size"]:
                raise SelectionError("selection_resource_size")
            if resource["mime_type"] == "image/svg+xml":
                self._validate_svg_path(path)
            else:
                self._validate_raster_path(resource["mime_type"], path)
            destination = self._uploads / upload_id / "resources" / resource_key
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, destination)
            connection.execute("UPDATE selection_upload_resources SET actual_size = ?, sha256 = ? WHERE upload_id = ? AND resource_key = ?", (size, self._resource_sha256(destination), upload_id, resource_key))
            connection.execute("UPDATE selection_uploads SET state = 'resources_pending' WHERE upload_id = ?", (upload_id,))
        return UploadSession(upload_id, "resources_pending")

    @staticmethod
    def _validate_raster(mime_type: str, content: bytes) -> bytes:
        preview = encode_webp_preview(content)
        if not content.startswith(_RASTER_MIME[mime_type]) or preview is None:
            raise SelectionError("unsupported_selection_content")
        if mime_type == "image/webp" and content[8:12] != b"WEBP":
            raise SelectionError("unsupported_selection_content")
        return preview[2]

    def put_resource(
        self, upload_id: str, device_id: str, resource_key: str, mime_type: str, content: bytes
    ) -> UploadSession:
        if len(content) > self._limits.max_resource_bytes:
            raise SelectionError("selection_too_large")
        with self._connect() as connection:
            row = self._upload_row(connection, upload_id, device_id)
            if row["state"] not in {"resources_pending", "manifest_received"}:
                raise SelectionError("selection_upload_state")
            resource = connection.execute(
                "SELECT * FROM selection_upload_resources WHERE upload_id = ? AND resource_key = ?",
                (upload_id, resource_key),
            ).fetchone()
            if resource is None:
                raise SelectionError("selection_resource_unknown")
            if resource["actual_size"] is not None:
                raise SelectionError("selection_resource_duplicate")
            if mime_type.split(";", 1)[0].strip().lower() != resource["mime_type"]:
                raise SelectionError("selection_resource_mime")
            if len(content) != resource["declared_size"]:
                raise SelectionError("selection_resource_size")
            if resource["mime_type"] == "image/svg+xml":
                self._validate_svg(content)
            else:
                self._validate_raster(resource["mime_type"], content)
            destination = self._uploads / upload_id / "resources" / resource_key
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(content)
            os.replace(temporary, destination)
            connection.execute(
                "UPDATE selection_upload_resources SET actual_size = ?, sha256 = ? WHERE upload_id = ? AND resource_key = ?",
                (len(content), hashlib.sha256(content).hexdigest(), upload_id, resource_key),
            )
            connection.execute(
                "UPDATE selection_uploads SET state = 'resources_pending' WHERE upload_id = ?",
                (upload_id,),
            )
        return UploadSession(upload_id, "resources_pending")

    @staticmethod
    def _fingerprint(manifest: bytes, resources: list[sqlite3.Row]) -> str:
        digest = hashlib.sha256(manifest)
        for resource in resources:
            digest.update(resource["resource_key"].encode("ascii"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(resource["sha256"]))
        return digest.hexdigest()

    def _artifact_for_fingerprint(self, fingerprint: str) -> Path:
        return self._selections / fingerprint

    def _verify_artifact(self, version: SelectionVersion) -> Path:
        artifact = self._artifact_for_fingerprint(version.fingerprint)
        try:
            manifest = (artifact / "manifest.json").read_bytes()
            resource_dir = artifact / "resources"
            resources = []
            for declared in version.manifest.resources:
                resource_path = resource_dir / declared.key
                size = 0
                digest = hashlib.sha256()
                with resource_path.open("rb") as source:
                    while chunk := source.read(64 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                if size != declared.size:
                    raise OSError("resource size mismatch")
                resources.append((declared.key, digest.hexdigest()))
        except OSError as error:
            raise SelectionError("selection_not_found") from error
        digest = hashlib.sha256(manifest)
        for key, sha256 in resources:
            digest.update(key.encode("ascii"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(sha256))
        if digest.hexdigest() != version.fingerprint:
            raise SelectionError("selection_not_found")
        return artifact

    def _publish(self, version: SelectionVersion, resources: list[sqlite3.Row]) -> None:
        target = self._artifact_for_fingerprint(version.fingerprint)
        if target.exists():
            self._verify_artifact(version)
            return
        self._selections.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        published = False
        try:
            temporary = Path(tempfile.mkdtemp(prefix="tmp-", dir=self._selections))
            (temporary / "manifest.json").write_bytes(self._manifest_payload(version.manifest))
            for resource in resources:
                source = self._uploads / resource["upload_id"] / "resources" / resource["resource_key"]
                destination = temporary / "resources" / resource["resource_key"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            previews = 0
            for resource in resources:
                if previews == 8 or resource["mime_type"] == "image/svg+xml":
                    continue
                source = temporary / "resources" / resource["resource_key"]
                preview = self._validate_raster_path(resource["mime_type"], source)
                destination = temporary / "previews" / f"{previews}.webp"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(preview)
                previews += 1
            try:
                os.replace(temporary, target)
                published = True
            except OSError:
                if not target.exists():
                    raise
            try:
                self._verify_artifact(version)
            except SelectionError:
                if published:
                    with suppress(OSError):
                        shutil.rmtree(target)
                raise
        finally:
            if temporary is not None and temporary.exists() and not published:
                with suppress(OSError):
                    shutil.rmtree(temporary)

    @staticmethod
    def _cleanup(path: Path) -> bool:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def commit(self, upload_id: str, device_id: str) -> SelectionVersion:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._upload_row(connection, upload_id, device_id)
            if row["state"] == "committed" and row["selection_id"] is not None:
                selected = connection.execute(
                    "SELECT * FROM selections WHERE selection_id = ?", (row["selection_id"],)
                ).fetchone()
                if selected is None:
                    raise SelectionError("selection_not_found")
                return self._version(selected)
            if row["state"] not in {"manifest_received", "resources_pending"} or row["manifest"] is None:
                raise SelectionError("selection_upload_state")
            resource_rows = connection.execute(
                "SELECT * FROM selection_upload_resources WHERE upload_id = ?", (upload_id,)
            ).fetchall()
            manifest = SelectionManifest.model_validate_json(row["manifest"])
            resources_by_key = {resource["resource_key"]: resource for resource in resource_rows}
            try:
                resources = [resources_by_key[declared.key] for declared in manifest.resources]
            except KeyError as error:
                raise SelectionError("selection_resources_missing") from error
            if len(resources_by_key) != len(manifest.resources):
                raise SelectionError("selection_resources_missing")
            if any(resource["actual_size"] is None for resource in resources):
                raise SelectionError("selection_resources_missing")
            fingerprint = self._fingerprint(self._manifest_payload(manifest), resources)
            preview_count = min(8, sum(resource["mime_type"] != "image/svg+xml" for resource in resources))
            version = SelectionVersion(
                selection_id=uuid.uuid4().hex,
                device_id=device_id,
                fingerprint=fingerprint,
                manifest=manifest,
                created_at=self._now(),
                preview_count=preview_count,
            )
            self._publish(version, resources)
            connection.execute(
                "INSERT INTO selections VALUES (?, ?, ?, ?, ?, ?)",
                (
                    version.selection_id,
                    version.device_id,
                    version.fingerprint,
                    self._manifest_payload(manifest).decode("utf-8"),
                    version.created_at.timestamp(),
                    version.preview_count,
                ),
            )
            connection.execute(
                "UPDATE selection_uploads SET state = 'committed', selection_id = ? WHERE upload_id = ?",
                (version.selection_id, upload_id),
            )
        self._cleanup(self._uploads / upload_id)
        return version

    def get(self, selection_id: str, device_id: str) -> SelectionVersion:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM selections WHERE selection_id = ?", (selection_id,)).fetchone()
        if row is None or row["device_id"] != device_id:
            raise SelectionError("selection_not_found")
        version = self._version(row)
        self._verify_artifact(version)
        return version

    def latest_for_device(self, device_id: str) -> SelectionVersion | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM selections WHERE device_id = ? ORDER BY created_at DESC LIMIT 1", (device_id,)
            ).fetchone()
        if row is None:
            return None
        version = self._version(row)
        self._verify_artifact(version)
        return version

    def artifact_path(self, selection_id: str) -> Path:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM selections WHERE selection_id = ?", (selection_id,)).fetchone()
        if row is None:
            raise SelectionError("selection_not_found")
        return self._verify_artifact(self._version(row))

    def preview_path(self, selection_id: str, device_id: str, index: int) -> Path:
        version = self.get(selection_id, device_id)
        if index < 0 or index >= version.preview_count:
            raise SelectionError("selection_not_found")
        preview = self._verify_artifact(version) / "previews" / f"{index}.webp"
        if not preview.is_file():
            raise SelectionError("selection_not_found")
        return preview

    def resource_path(self, selection_id: str, device_id: str, key: str) -> tuple[Path, str]:
        version = self.get(selection_id, device_id)
        resource = next((item for item in version.manifest.resources if item.key == key), None)
        if resource is None:
            raise SelectionError("selection_not_found")
        path = self._verify_artifact(version) / "resources" / key
        if not path.is_file():
            raise SelectionError("selection_not_found")
        return path, resource.mime_type

    def expire_uploads(self, limit: int = 50) -> int:
        with self._connect() as connection:
            expired = connection.execute(
                "SELECT upload_id FROM selection_uploads WHERE state != 'committed' AND expires_at <= ? LIMIT ?",
                (self._now().timestamp(), limit),
            ).fetchall()
        cleaned = [row["upload_id"] for row in expired if self._cleanup(self._uploads / row["upload_id"])]
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM selection_upload_resources WHERE upload_id = ?", [(upload_id,) for upload_id in cleaned]
            )
            connection.executemany("DELETE FROM selection_uploads WHERE upload_id = ?", [(upload_id,) for upload_id in cleaned])
        return len(cleaned)
