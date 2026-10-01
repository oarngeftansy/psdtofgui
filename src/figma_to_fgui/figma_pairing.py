from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from figma_to_fgui.service_contracts import (
    ConsolePairingStatusView,
    FigmaDeviceView,
    PairingCodeView,
    PluginCredentialView,
    PluginPrincipal,
    PluginScope,
)

_CODE_TTL = timedelta(minutes=10)
_CONSOLE_TTL = timedelta(hours=2)
_RATE_LIMIT_WINDOW = timedelta(minutes=10)
_RATE_LIMIT_ATTEMPTS = 5
_PLUGIN_SCOPES = (PluginScope.SELECTION_UPLOAD, PluginScope.SELECTION_READ_OWN_STATUS)


class PairingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _digest(secret: bytes, purpose: bytes, value: str) -> str:
    return hmac.new(secret, purpose + b"\0" + value.encode("utf-8"), hashlib.sha256).hexdigest()


def require_scope(principal: PluginPrincipal, required_scope: PluginScope | str) -> PluginPrincipal:
    if required_scope not in principal.scopes:
        raise PairingError("plugin_scope_denied")
    return principal


class PairingStore:
    def __init__(self, db_path: Path, secret: bytes, clock: Callable[[], datetime]) -> None:
        self._db_path = db_path
        self._secret = secret
        self._clock = clock

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_digest TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    consumed_at REAL
                );
                CREATE TABLE IF NOT EXISTS plugin_devices (
                    device_id TEXT PRIMARY KEY,
                    device_name TEXT NOT NULL,
                    credential_digest TEXT NOT NULL UNIQUE,
                    scopes TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    revoked_at REAL
                );
                """
            )
            code_columns = {row["name"] for row in connection.execute("PRAGMA table_info(pairing_codes)")}
            for column in ("console_digest", "console_expires_at", "cancelled_at", "device_id"):
                if column not in code_columns:
                    connection.execute(f"ALTER TABLE pairing_codes ADD COLUMN {column}")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS pairing_codes_console_digest "
                "ON pairing_codes(console_digest) WHERE console_digest IS NOT NULL"
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(plugin_devices)")}
            if "scopes" not in columns:
                connection.execute(
                    "ALTER TABLE plugin_devices ADD COLUMN scopes TEXT NOT NULL "
                    "DEFAULT '[\"selection:upload\", \"selection:read-own-status\"]'"
                )
            failure_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(pairing_failures)")
            }
            if failure_columns and "bucket_digest" not in failure_columns:
                connection.execute("DROP TABLE pairing_failures")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS pairing_failures (
                    bucket_digest TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    attempts INTEGER NOT NULL CHECK(attempts BETWEEN 1 AND 5)
                );
                CREATE INDEX IF NOT EXISTS pairing_failures_expiry ON pairing_failures(expires_at);
                """
            )

    def _now(self) -> datetime:
        now = self._clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    @staticmethod
    def _timestamp(value: datetime) -> float:
        return value.timestamp()

    @staticmethod
    def _datetime(value: float | None) -> datetime | None:
        return None if value is None else datetime.fromtimestamp(value, tz=UTC)

    def create_code(self, *, source_key: str = "unknown") -> PairingCodeView:
        now = self._now()
        expires_at = now + _CODE_TTL
        console_credential = secrets.token_urlsafe(32)
        source_key = source_key or "unknown"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now_value = self._timestamp(now)
            self._clear_expired_failures(connection, now_value)
            connection.execute(
                "DELETE FROM pairing_codes WHERE console_expires_at <= ? "
                "OR (consumed_at IS NULL AND expires_at <= ?)",
                (now_value, now_value),
            )
            source_bucket = self._failure_bucket(b"pairing-create-source", source_key)
            if self._is_rate_limited(connection, (source_bucket,)):
                raise PairingError("pairing_rate_limited")
            connection.execute(
                "INSERT INTO pairing_failures(bucket_digest, expires_at, attempts) VALUES (?, ?, 1) "
                "ON CONFLICT(bucket_digest) DO UPDATE SET attempts = "
                "MIN(?, pairing_failures.attempts + 1)",
                (source_bucket, now_value + _RATE_LIMIT_WINDOW.total_seconds(), _RATE_LIMIT_ATTEMPTS),
            )
            for _ in range(3):
                code = f"{secrets.randbelow(1_000_000):06d}"
                try:
                    connection.execute(
                        "INSERT INTO pairing_codes(code_digest, expires_at, console_digest, console_expires_at) VALUES (?, ?, ?, ?)",
                        (
                            _digest(self._secret, b"pairing-code", code),
                            self._timestamp(expires_at),
                            _digest(self._secret, b"console-credential", console_credential),
                            self._timestamp(now + _CONSOLE_TTL),
                        ),
                    )
                except sqlite3.IntegrityError:
                    continue
                return PairingCodeView(code=code, expires_at=expires_at, console_credential=console_credential)
        raise RuntimeError("unable to issue pairing code")

    def _failure_bucket(self, purpose: bytes, value: str) -> str:
        return _digest(self._secret, purpose, value)

    @staticmethod
    def _clear_expired_failures(connection: sqlite3.Connection, now_value: float) -> None:
        connection.execute("DELETE FROM pairing_failures WHERE expires_at <= ?", (now_value,))

    @staticmethod
    def _is_rate_limited(connection: sqlite3.Connection, buckets: tuple[str, ...]) -> bool:
        placeholders = ", ".join("?" for _ in buckets)
        row = connection.execute(
            f"SELECT 1 FROM pairing_failures WHERE bucket_digest IN ({placeholders}) "
            "AND attempts >= ? LIMIT 1",
            (*buckets, _RATE_LIMIT_ATTEMPTS),
        ).fetchone()
        return row is not None

    def _invalid_attempt(
        self, connection: sqlite3.Connection, now: datetime, source_key: str, code: str
    ) -> PairingError:
        now_value = self._timestamp(now)
        buckets = (
            self._failure_bucket(b"pairing-failure-source", source_key),
            self._failure_bucket(b"pairing-failure-code", code),
        )
        self._clear_expired_failures(connection, now_value)
        for bucket in buckets:
            connection.execute(
                "INSERT INTO pairing_failures(bucket_digest, expires_at, attempts) VALUES (?, ?, 1) "
                "ON CONFLICT(bucket_digest) DO UPDATE SET attempts = "
                "MIN(?, pairing_failures.attempts + 1)",
                (bucket, now_value + _RATE_LIMIT_WINDOW.total_seconds(), _RATE_LIMIT_ATTEMPTS),
            )
        limited = self._is_rate_limited(connection, buckets)
        connection.commit()
        return PairingError("pairing_rate_limited" if limited else "pairing_code_invalid")

    def _device_view(self, row: sqlite3.Row) -> FigmaDeviceView:
        return FigmaDeviceView(
            device_id=row["device_id"],
            device_name=row["device_name"],
            created_at=datetime.fromtimestamp(row["created_at"], tz=UTC),
            revoked_at=self._datetime(row["revoked_at"]),
        )

    def exchange(
        self, code: str, device_name: str, *, source_key: str = "unknown"
    ) -> PluginCredentialView:
        now = self._now()
        source_key = source_key or "unknown"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now_value = self._timestamp(now)
            buckets = (
                self._failure_bucket(b"pairing-failure-source", source_key),
                self._failure_bucket(b"pairing-failure-code", code),
            )
            self._clear_expired_failures(connection, now_value)
            if self._is_rate_limited(connection, buckets):
                raise PairingError("pairing_rate_limited")
            if len(code) != 6 or not code.isascii() or not code.isdigit():
                raise self._invalid_attempt(connection, now, source_key, code)
            row = connection.execute(
                "SELECT expires_at, consumed_at, cancelled_at FROM pairing_codes WHERE code_digest = ?",
                (_digest(self._secret, b"pairing-code", code),),
            ).fetchone()
            if row is None or row["consumed_at"] is not None or row["cancelled_at"] is not None:
                raise self._invalid_attempt(connection, now, source_key, code)
            if self._timestamp(now) >= row["expires_at"]:
                raise PairingError("pairing_code_expired")
            device_id = uuid.uuid4().hex
            credential = secrets.token_urlsafe(32)
            connection.execute(
                "UPDATE pairing_codes SET consumed_at = ?, device_id = ? WHERE code_digest = ?",
                (self._timestamp(now), device_id, _digest(self._secret, b"pairing-code", code)),
            )
            connection.execute(
                "INSERT INTO plugin_devices(device_id, device_name, credential_digest, scopes, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    device_id,
                    device_name,
                    _digest(self._secret, b"plugin-credential", credential),
                    json.dumps([scope.value for scope in _PLUGIN_SCOPES]),
                    self._timestamp(now),
                ),
            )
        return PluginCredentialView(
            credential=credential,
            device=FigmaDeviceView(
                device_id=device_id,
                device_name=device_name,
                created_at=now,
            ),
        )

    def _console_row(self, credential: str) -> sqlite3.Row:
        now = self._timestamp(self._now())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pairing_codes WHERE console_digest = ?",
                (_digest(self._secret, b"console-credential", credential),),
            ).fetchone()
        if row is None or row["cancelled_at"] is not None or row["console_expires_at"] is None or row["console_expires_at"] <= now:
            raise PairingError("pairing_console_invalid")
        return cast(sqlite3.Row, row)

    def console_status(self, credential: str) -> ConsolePairingStatusView:
        row = self._console_row(credential)
        if row["device_id"] is None:
            return ConsolePairingStatusView(
                state="waiting_for_device", expires_at=datetime.fromtimestamp(row["expires_at"], tz=UTC)
            )
        with self._connect() as connection:
            device = connection.execute(
                "SELECT device_id, device_name, created_at, revoked_at FROM plugin_devices WHERE device_id = ?",
                (row["device_id"],),
            ).fetchone()
        if device is None or device["revoked_at"] is not None:
            raise PairingError("pairing_console_invalid")
        return ConsolePairingStatusView(
            state="paired", expires_at=datetime.fromtimestamp(row["expires_at"], tz=UTC), device=self._device_view(device)
        )

    def console_device_id(self, credential: str) -> str:
        status = self.console_status(credential)
        if status.device is None:
            raise PairingError("pairing_console_invalid")
        return status.device.device_id

    def cancel_console_pairing(self, credential: str) -> None:
        row = self._console_row(credential)
        with self._connect() as connection:
            connection.execute(
                "UPDATE pairing_codes SET cancelled_at = ? WHERE code_digest = ?",
                (self._timestamp(self._now()), row["code_digest"]),
            )

    def console_devices(self, credential: str) -> tuple[FigmaDeviceView, ...]:
        status = self.console_status(credential)
        return () if status.device is None else (status.device,)

    def revoke_console_device(self, credential: str, device_id: str) -> FigmaDeviceView:
        if self.console_device_id(credential) != device_id:
            raise PairingError("pairing_console_invalid")
        return self.revoke(device_id)

    def list_devices(self) -> tuple[FigmaDeviceView, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT device_id, device_name, created_at, revoked_at FROM plugin_devices "
                "WHERE revoked_at IS NULL ORDER BY created_at, device_id"
            ).fetchall()
        return tuple(self._device_view(row) for row in rows)

    def revoke(self, device_id: str) -> FigmaDeviceView:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT device_id, device_name, created_at, revoked_at FROM plugin_devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if row is None:
                raise PairingError("plugin_credential_invalid")
            if row["revoked_at"] is None:
                connection.execute(
                    "UPDATE plugin_devices SET revoked_at = ? WHERE device_id = ?",
                    (self._timestamp(now), device_id),
                )
                connection.execute(
                    "UPDATE pairing_codes SET cancelled_at = ? WHERE device_id = ? AND cancelled_at IS NULL",
                    (self._timestamp(now), device_id),
                )
                row = connection.execute(
                    "SELECT device_id, device_name, created_at, revoked_at FROM plugin_devices "
                    "WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
        assert row is not None
        return self._device_view(row)

    def authenticate(self, credential: str) -> PluginPrincipal:
        credential_digest = _digest(self._secret, b"plugin-credential", credential)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT device_id, credential_digest, scopes, revoked_at FROM plugin_devices "
                "WHERE credential_digest = ?",
                (credential_digest,),
            ).fetchone()
        if row is None or not hmac.compare_digest(row["credential_digest"], credential_digest):
            raise PairingError("plugin_credential_invalid")
        if row["revoked_at"] is not None:
            raise PairingError("plugin_credential_revoked")
        try:
            scopes = tuple(PluginScope(value) for value in json.loads(row["scopes"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise PairingError("plugin_credential_invalid") from None
        if scopes != _PLUGIN_SCOPES:
            raise PairingError("plugin_credential_invalid")
        return PluginPrincipal(device_id=row["device_id"], scopes=scopes)
