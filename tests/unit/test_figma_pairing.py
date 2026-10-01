from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from figma_to_fgui.figma_pairing import PairingError, PairingStore, require_scope
from figma_to_fgui.service_contracts import PluginPrincipal, PluginScope


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 28, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: int) -> None:
        self.now += timedelta(**delta)


def test_pairing_code_is_single_use_and_expires(tmp_path: Path) -> None:
    clock = FakeClock()
    store = PairingStore(tmp_path / "pairing.db", b"s" * 32, clock)
    store.initialize()

    issued = store.create_code()
    first = store.exchange(issued.code, "Figma browser")
    assert first.credential

    with pytest.raises(PairingError, match="pairing_code_invalid"):
        store.exchange(issued.code, "Repeated device")

    expired = store.create_code()
    clock.advance(minutes=11)
    with pytest.raises(PairingError, match="pairing_code_expired"):
        store.exchange(expired.code, "Expired device")


def test_pairing_store_does_not_disclose_secrets_and_revocation_is_immediate(tmp_path: Path) -> None:
    clock = FakeClock()
    database = tmp_path / "pairing.db"
    store = PairingStore(database, b"s" * 32, clock)
    store.initialize()

    issued = store.create_code()
    exchanged = store.exchange(issued.code, "Figma desktop")

    stored = database.read_bytes()
    assert issued.code.encode() not in stored
    assert exchanged.credential.encode() not in stored
    device = store.list_devices()[0]
    assert set(device.model_dump()) == {"version", "device_id", "device_name", "created_at", "revoked_at"}
    assert store.authenticate(exchanged.credential) == PluginPrincipal(
        device_id=device.device_id,
        scopes=(PluginScope.SELECTION_UPLOAD, PluginScope.SELECTION_READ_OWN_STATUS),
    )

    store.revoke(device.device_id)
    with pytest.raises(PairingError, match="plugin_credential_revoked"):
        store.authenticate(exchanged.credential)


def test_five_invalid_exchanges_are_rate_limited_without_creating_a_device(tmp_path: Path) -> None:
    store = PairingStore(tmp_path / "pairing.db", b"s" * 32, FakeClock())
    store.initialize()

    for _ in range(4):
        with pytest.raises(PairingError, match="pairing_code_invalid"):
            store.exchange("not-a-code", "Figma desktop")
    with pytest.raises(PairingError, match="pairing_rate_limited"):
        store.exchange("not-a-code", "Figma desktop")

    assert store.list_devices() == ()


def test_rate_limits_are_hmac_keyed_per_source_and_per_code(tmp_path: Path) -> None:
    clock = FakeClock()
    database = tmp_path / "pairing.db"
    store = PairingStore(database, b"s" * 32, clock)
    store.initialize()

    for _ in range(4):
        with pytest.raises(PairingError, match="pairing_code_invalid"):
            store.exchange("111111", "Figma desktop", source_key="source-a")
    with pytest.raises(PairingError, match="pairing_rate_limited"):
        store.exchange("111111", "Figma desktop", source_key="source-a")

    assert store.exchange(store.create_code().code, "Figma browser", source_key="source-b").credential

    for index in range(4):
        with pytest.raises(PairingError, match="pairing_code_invalid"):
            store.exchange("222222", "Figma desktop", source_key=f"source-{index}")
    with pytest.raises(PairingError, match="pairing_rate_limited"):
        store.exchange("222222", "Figma desktop", source_key="other-source")

    stored = database.read_bytes()
    assert b"source-a" not in stored
    assert b"other-source" not in stored
    clock.advance(minutes=11)
    assert store.exchange(store.create_code().code, "Figma desktop", source_key="source-a").credential


def test_pairing_code_creation_is_limited_per_source_and_cleans_expired_rows(tmp_path: Path) -> None:
    clock = FakeClock()
    database = tmp_path / "pairing.db"
    store = PairingStore(database, b"s" * 32, clock)
    store.initialize()

    for _ in range(5):
        store.create_code(source_key="source-a")
    with pytest.raises(PairingError, match="pairing_rate_limited"):
        store.create_code(source_key="source-a")
    assert store.create_code(source_key="source-b").code

    clock.advance(minutes=11)
    assert store.create_code(source_key="source-a").code
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM pairing_codes").fetchone()[0] == 1


def test_plugin_credentials_have_only_the_selection_scopes(tmp_path: Path) -> None:
    database = tmp_path / "pairing.db"
    store = PairingStore(database, b"s" * 32, FakeClock())
    store.initialize()
    credential = store.exchange(store.create_code().code, "Figma desktop").credential

    principal = store.authenticate(credential)
    assert principal == PluginPrincipal(
        device_id=principal.device_id,
        scopes=(PluginScope.SELECTION_UPLOAD, PluginScope.SELECTION_READ_OWN_STATUS),
    )
    assert b"selection:upload" in database.read_bytes()
    assert require_scope(principal, PluginScope.SELECTION_UPLOAD) is principal
    for unrelated_scope in ("project:write", "agent:claim", "admin:manage"):
        with pytest.raises(PairingError, match="plugin_scope_denied"):
            require_scope(principal, unrelated_scope)
