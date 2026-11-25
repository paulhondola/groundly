import hashlib
import os
import sqlite3
import stat

import pytest

from groundly.web.remote import (
    MAX_PAIR_FAILURES,
    PAIR_WINDOW_S,
    SESSION_COOKIE,
    SESSION_TTL_S,
    PairAuthError,
    PairingManager,
    RemoteRuntime,
    RemoteStore,
    parse_device_label,
    qr_matrix,
    render_terminal_qr,
)
from groundly.web.tailscale import TailscaleInfo


def test_remote_store_permissions_sha256_dedupe_throttle_and_revoke(tmp_path):
    clock = [1_700_000_000]
    db_path = tmp_path / "remote.db"
    store = RemoteStore(db_path, now=lambda: clock[0])

    d1 = store.create_session(
        "raw-phone-tok-1", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1", "100.64.0.10"
    )
    assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600
    assert d1["label"] == "iPhone · Safari"
    assert store.authenticate("raw-phone-tok-1") is True
    assert store.authenticate("wrong-token") is False

    # Raw token is never stored in remote.db; only its sha256 digest is stored.
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT token_hash FROM sessions").fetchall()
        assert rows == [(hashlib.sha256(b"raw-phone-tok-1").digest(),)]
    assert b"raw-phone-tok-1" not in db_path.read_bytes()

    # last_seen_at is throttled within 60s and updates once >= 60s elapse.
    clock[0] += 30
    assert store.authenticate("raw-phone-tok-1") is True
    assert store.list_devices()[0]["last_seen_at"] == 1_700_000_000
    clock[0] += 40
    assert store.authenticate("raw-phone-tok-1") is True
    assert store.list_devices()[0]["last_seen_at"] == 1_700_000_070

    # Re-pairing an already-paired phone supersedes its old row instead of duplicating.
    d1_new = store.create_session(
        "raw-phone-tok-2",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1",
        "100.64.0.10",
        replace_raw_token="raw-phone-tok-1",
    )
    assert store.authenticate("raw-phone-tok-1") is False
    assert store.authenticate("raw-phone-tok-2") is True
    assert store.count_devices() == 1

    # Tablet gets an independent revocable row.
    d2 = store.create_session(
        "raw-tablet-tok", "Mozilla/5.0 (iPad; CPU OS 18_0) Safari/604.1", "100.64.0.11"
    )
    assert d2["id"] != d1_new["id"]
    assert store.count_devices() == 2
    assert store.revoke_device(d2["id"]) is True
    assert store.authenticate("raw-tablet-tok") is False
    assert store.authenticate("raw-phone-tok-2") is True

    # Expiry pruning after 30 days.
    clock[0] += SESSION_TTL_S + 1
    assert store.authenticate("raw-phone-tok-2") is False
    assert store.list_devices() == []


def test_pairing_manager_single_use_rotation_and_rate_limit():
    now = [100.0]
    ts = TailscaleInfo(ipv4="100.101.102.103", dns_name="macbook.tail1234.ts.net")
    mgr = PairingManager(ts, 8765, monotonic=lambda: now[0])

    first_url = mgr.pairing_url
    assert first_url.startswith("http://macbook.tail1234.ts.net:8765/pair#")
    first_token = first_url.split("#", 1)[1]

    second_url = mgr.consume_and_rotate(first_token, "100.64.0.10")
    assert second_url != first_url

    # Replaying a burned token fails with 401 and counts toward the rate limit.
    for _ in range(MAX_PAIR_FAILURES):
        with pytest.raises(PairAuthError) as exc:
            mgr.consume_and_rotate(first_token, "100.64.0.10")
        assert (exc.value.status_code, exc.value.error) == (401, "invalid_pairing_token")

    with pytest.raises(PairAuthError) as exc:
        mgr.consume_and_rotate(second_url.split("#", 1)[1], "100.64.0.10")
    assert (exc.value.status_code, exc.value.error) == (429, "rate_limited")

    # Different peer IP is unaffected; original peer recovers after window expires.
    third_url = mgr.consume_and_rotate(second_url.split("#", 1)[1], "100.64.0.11")
    now[0] += PAIR_WINDOW_S + 1.0
    assert mgr.consume_and_rotate(third_url.split("#", 1)[1], "100.64.0.10") != third_url


def test_device_label_and_qr_helpers():
    assert parse_device_label("Mozilla/5.0 (iPad; CPU OS 18) CriOS/130 Safari") == "iPad · Chrome"
    assert (
        parse_device_label("Mozilla/5.0 (Linux; Android 15; Pixel 9) Firefox/132")
        == "Android · Firefox"
    )
    assert parse_device_label("curl/8.7\x1b[31m") == "Device · Browser"
    matrix = qr_matrix("http://100.101.102.103:8765/pair#tok")
    assert len(matrix) >= 21 and all(len(row) == len(matrix) for row in matrix)
    assert render_terminal_qr("http://100.101.102.103:8765/pair#tok").strip()


def test_remote_store_enforces_mode_version_and_revoke_all(tmp_path):
    db_path = tmp_path / "remote.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 2")
    os.chmod(db_path, 0o644)

    store = RemoteStore(db_path)
    with pytest.raises(RuntimeError, match="Unsupported remote.db schema version"):
        store.list_devices()
    assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600

    db2_path = tmp_path / "remote2.db"
    store2 = RemoteStore(db2_path)
    store2.create_session("tok-a", "Mozilla/5.0 (Macintosh; Intel Mac OS X) Edg/130", "100.64.0.1")
    store2.create_session("tok-b", "Mozilla/5.0 (Windows NT 10.0) Firefox/132", "100.64.0.2")
    assert store2.count_devices() == 2
    assert store2.revoke_all() == 2
    assert store2.count_devices() == 0
    assert store2.revoke_device(999) is False
    assert store2.authenticate("") is False


def test_remote_runtime_rotate_and_additional_device_labels(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path))
    ts = TailscaleInfo(ipv4="100.101.102.103", dns_name=None)
    callbacks: list[tuple[str, str, str]] = []
    runtime = RemoteRuntime(ts, 8765, on_paired=lambda l, ip, u: callbacks.append((l, ip, u)))

    assert SESSION_COOKIE == "groundly_session"
    assert runtime.on_paired is not None
    initial = runtime.pairing.pairing_url
    rotated = runtime.pairing.rotate()
    assert rotated != initial
    assert rotated.startswith("http://100.101.102.103:8765/pair#")
    assert runtime.store.count_devices() == 0
    assert (tmp_path / "remote.db").exists()

    with pytest.raises(PairAuthError) as exc:
        runtime.pairing.consume_and_rotate("", "100.64.0.10")
    assert (exc.value.status_code, exc.value.error) == (401, "invalid_pairing_token")

    assert (
        parse_device_label("Mozilla/5.0 (Macintosh; Intel Mac OS X) Safari/605") == "Mac · Safari"
    )
    assert parse_device_label("Mozilla/5.0 (X11; Linux x86_64) Chrome/130") == "Linux · Chrome"
    assert parse_device_label("Mozilla/5.0 (Windows NT 10.0) Edg/130") == "Windows · Edge"
