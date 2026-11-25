import hashlib
import os
import sqlite3
import stat

import pytest
from fastapi.testclient import TestClient
from starlette.types import ASGIApp

from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import remote_db_path, subject_dir
from groundly.core.store import QuestionWrite, SubjectStore, TopicWrite
from groundly.core.subject import Subject
from groundly.ingestion.extract import ChunkData
from groundly.mcp.server import mcp
from groundly.web import app as web_app
from groundly.web.app import HOSTS, create_app
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


def _socket_wrapper(app: ASGIApp, server_ip: str, client_ip: str) -> ASGIApp:
    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            scope = {
                **scope,
                "server": (server_ip, 8765),
                "client": (client_ip, 51234),
            }
        await app(scope, receive, send)

    return wrapped


def _seed_pdss(home_path) -> dict[str, int]:
    Subject("PDSS").initialize()
    (Subject("PDSS").materials_dir / "lec.pdf").write_bytes(b"%PDF-1.4 remote test")
    s = SubjectStore(subject_dir("PDSS") / "store.db")
    chunks = [ChunkData("Mutex text", "Intro > Locks", 3, 2)]
    mat_id = s.add_indexed("lec.pdf", "a" * 64, 1, chunks, zip([[0.1] * EMBEDDING_DIM], [{1: 0.5}]))
    conn = s.connect()
    try:
        chunk_id = conn.execute(
            "SELECT id FROM chunks WHERE material_id = ?", (mat_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    s.replace_tree([TopicWrite(None, "Locks", "Mutual exclusion", [chunk_id])])
    leaf_id = s.tree()[0]["id"]
    s.add_questions(
        leaf_id,
        [
            QuestionWrite(
                "single",
                "What does a lock guarantee?",
                None,
                None,
                [("Mutual exclusion", True), ("Zero latency", False)],
                [chunk_id],
            )
        ],
    )
    q = s.questions()[0]
    correct_opt = next(o["id"] for o in q["options"] if o["correct"])
    return {
        "material_id": mat_id,
        "topic_id": leaf_id,
        "question_id": q["id"],
        "option_id": correct_opt,
    }


_MCP_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) Safari/604.1"
_IPAD_UA = "Mozilla/5.0 (iPad; CPU OS 18_0) Safari/604.1"


def test_socket_perimeter_pairing_multi_device_and_revocation(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    seeded = _seed_pdss(tmp_path / "home")

    ts_info = TailscaleInfo(ipv4="100.101.102.103", dns_name="macbook.tail1234.ts.net")
    paired_events: list[tuple[str, str, str]] = []
    runtime = RemoteRuntime(
        ts_info,
        8765,
        on_paired=lambda label, ip, url: paired_events.append((label, ip, url)),
    )
    mcp_app = mcp.http_app(path="/mcp", host_origin_protection="auto", allowed_hosts=HOSTS)
    app = create_app(mcp_app, remote=runtime)

    loopback_headers = {"Origin": "http://127.0.0.1:8765"}
    mixed_ts_host = "MacBook.Tail1234.ts.net:8765"
    ts_origin = {"Host": mixed_ts_host, "Origin": "http://macbook.tail1234.ts.net:8765"}

    with TestClient(
        _socket_wrapper(app, "127.0.0.1", "127.0.0.1"), base_url="http://127.0.0.1:8765"
    ) as loopback:
        # 1. Loopback works without cookies; /mcp and /api/remote work; POST /api/pair is 404.
        auth_lb = loopback.get("/api/auth")
        assert auth_lb.status_code == 200
        assert auth_lb.json() == {
            "access": "loopback",
            "paired": True,
            "tailscale_enabled": True,
            "paired_devices": 0,
        }
        assert loopback.get("/api/subjects").status_code == 200
        remote_lb = loopback.get("/api/remote")
        assert remote_lb.status_code == 200
        assert remote_lb.json()["active"] is True
        assert remote_lb.json()["host"] == "macbook.tail1234.ts.net"
        assert remote_lb.json()["pairing_url"] == runtime.pairing.pairing_url
        assert remote_lb.json()["qr_matrix"]
        assert remote_lb.json()["devices"] == []
        assert loopback.post("/mcp", json=_MCP_INIT, headers=_MCP_HEADERS).status_code == 200
        assert loopback.post("/api/pair", json={"token": "x" * 32}).status_code == 404
        assert (
            loopback.get("/api/subjects", headers={"Host": "100.101.102.103:8765"}).status_code
            == 400
        )

        phone = TestClient(
            _socket_wrapper(app, ts_info.ipv4, "100.64.0.10"),
            base_url="http://macbook.tail1234.ts.net:8765",
        )
        tablet = TestClient(
            _socket_wrapper(app, ts_info.ipv4, "100.64.0.11"),
            base_url="http://macbook.tail1234.ts.net:8765",
        )

        # 2. Pre-auth Tailscale client checks: Host enforcement, no subject oracle, 401/404 gates.
        for bad_host in ("localhost:8765", "127.0.0.1:8765", "evil.example:8765"):
            assert phone.get("/api/auth", headers={"Host": bad_host}).status_code == 400

        called_open: list[str] = []
        orig_open = web_app._open_subject

        def guard_open(name: str):
            called_open.append(name)
            return orig_open(name)

        monkeypatch.setattr(web_app, "_open_subject", guard_open)
        shell_root = phone.get("/")
        shell_pdss = phone.get("/s/PDSS")
        shell_nope = phone.get("/s/NOPE")
        shell_test = phone.get("/s/PDSS/test")
        shell_attempt = phone.get("/s/NOPE/attempts/1")
        shell_pair = phone.get("/pair")
        assert (
            shell_root.status_code
            == shell_pdss.status_code
            == shell_nope.status_code
            == shell_test.status_code
            == shell_attempt.status_code
            == shell_pair.status_code
            == 200
        )
        assert shell_pdss.text == shell_nope.text == shell_test.text == shell_attempt.text
        assert (
            shell_pdss.headers["content-security-policy"]
            == shell_nope.headers["content-security-policy"]
        )
        assert called_open == []
        monkeypatch.setattr(web_app, "_open_subject", orig_open)

        assert phone.get("/s/PDSS/attempts/abc").status_code == 401
        assert phone.get("/api/auth", headers={"Host": f"{ts_info.ipv4}:8765"}).status_code == 200
        assert phone.get("/static/page.css").status_code == 200
        auth_ts = phone.get("/api/auth")
        assert auth_ts.status_code == 200
        assert auth_ts.json() == {
            "access": "tailscale",
            "paired": False,
            "tailscale_enabled": True,
            "paired_devices": 0,
        }
        for meth, path in (
            ("get", "/api/subjects"),
            ("get", "/api/runs"),
            ("get", "/api/runs/stream"),
            ("get", f"/s/PDSS/materials/{seeded['material_id']}"),
            ("post", "/api/subjects/PDSS/attempts"),
        ):
            r = getattr(phone, meth)(path)
            assert r.status_code == 401, path
            assert r.json()["error"] == "unpaired"

        assert phone.get("/api/remote").status_code == 404
        assert phone.post("/mcp", json=_MCP_INIT, headers=_MCP_HEADERS).status_code == 404

        # 3. Pair phone with mixed-case Host + lowercased Origin; check cookie, callback, rotation.
        initial_url = runtime.pairing.pairing_url
        token_1 = initial_url.split("#", 1)[1]
        assert (
            phone.post(
                "/api/pair",
                json={"token": token_1},
                headers={"Host": mixed_ts_host, "Origin": "http://evil.example"},
            ).status_code
            == 403
        )
        assert (
            phone.post("/api/pair", json={"token": "short"}, headers=ts_origin).status_code == 400
        )
        assert (
            phone.post(
                "/api/pair", json={"token": token_1, "extra": 1}, headers=ts_origin
            ).status_code
            == 400
        )

        pair_res = phone.post(
            "/api/pair",
            json={"token": token_1},
            headers={**ts_origin, "User-Agent": _IPHONE_UA},
        )
        assert pair_res.status_code == 200
        assert pair_res.json() == {"paired": True}
        set_cookie = pair_res.headers["set-cookie"]
        set_cookie_low = set_cookie.lower()
        assert f"{SESSION_COOKIE}=" in set_cookie
        assert "httponly" in set_cookie_low
        assert "samesite=strict" in set_cookie_low
        assert "path=/" in set_cookie_low
        assert f"max-age={SESSION_TTL_S}" in set_cookie_low
        assert "secure" not in set_cookie_low
        phone_cookie_1 = phone.cookies.get(SESSION_COOKIE)
        assert phone_cookie_1

        second_url = runtime.pairing.pairing_url
        assert second_url != initial_url
        assert paired_events == [("iPhone · Safari", "100.64.0.10", second_url)]
        assert runtime.store.count_devices() == 1

        # Replaying burned token_1 fails with 401 invalid_pairing_token (and doesn't break phone).
        replay = phone.post("/api/pair", json={"token": token_1}, headers=ts_origin)
        assert (replay.status_code, replay.json()["error"]) == (401, "invalid_pairing_token")

        # 4. Paired Tailscale client accesses SPA routes, stays blocked from /mcp and /api/remote*.
        for hidden_meth, hidden_path in (
            ("post", "/mcp"),
            ("get", "/mcp/anything"),
            ("get", "/api/remote"),
            ("post", "/api/remote/revoke-all"),
            ("post", "/api/remote/devices/1/revoke"),
        ):
            assert getattr(phone, hidden_meth)(hidden_path).status_code == 404

        assert phone.get("/api/auth").json() == {
            "access": "tailscale",
            "paired": True,
            "tailscale_enabled": True,
            "paired_devices": 0,
        }
        assert phone.get("/api/subjects").status_code == 200
        assert phone.get("/api/subjects/PDSS/tree").status_code == 200
        assert phone.get("/s/NOPE").status_code == 404
        pdf_res = phone.get(f"/s/PDSS/materials/{seeded['material_id']}")
        assert pdf_res.status_code == 200
        assert pdf_res.headers["content-type"] == "application/pdf"
        attempt_res = phone.post(
            "/api/subjects/PDSS/attempts",
            json={
                "token": "attempt-token-01",
                "topic_id": seeded["topic_id"],
                "answers": [
                    {"question_id": seeded["question_id"], "selected": [seeded["option_id"]]}
                ],
            },
            headers=ts_origin,
        )
        assert attempt_res.status_code == 201
        sse_res = phone.get("/api/runs/stream")
        assert sse_res.status_code == 200
        assert sse_res.headers["content-type"].startswith("text/event-stream")

        # Re-pairing same phone with rotated token replaces its session row (still 1 device).
        token_2 = second_url.split("#", 1)[1]
        repair_res = phone.post(
            "/api/pair",
            json={"token": token_2},
            headers={**ts_origin, "User-Agent": _IPHONE_UA},
        )
        assert repair_res.status_code == 200
        assert runtime.store.count_devices() == 1
        assert runtime.store.authenticate(phone_cookie_1) is False
        phone_cookie_2 = phone.cookies.get(SESSION_COOKIE)
        assert phone_cookie_2 and phone_cookie_2 != phone_cookie_1
        assert phone.get("/api/subjects").status_code == 200

        # 5. Tablet pairs concurrently with third token; loopback inspects and revokes individually & all.
        token_3 = runtime.pairing.pairing_url.split("#", 1)[1]
        assert (
            tablet.post(
                "/api/pair",
                json={"token": token_3},
                headers={**ts_origin, "User-Agent": _IPAD_UA},
            ).status_code
            == 200
        )
        assert tablet.get("/api/subjects").status_code == 200
        assert tablet.get("/api/runs/stream").status_code == 200

        assert loopback.get("/api/auth").json()["paired_devices"] == 2
        devices = loopback.get("/api/remote").json()["devices"]
        assert len(devices) == 2
        tablet_id = next(d["id"] for d in devices if d["label"] == "iPad · Safari")
        phone_id = next(d["id"] for d in devices if d["label"] == "iPhone · Safari")
        assert tablet_id != phone_id

        assert loopback.post("/api/remote/revoke-all", json={}).status_code == 403
        assert (
            loopback.post(
                "/api/remote/revoke-all", json=["not-a-dict"], headers=loopback_headers
            ).status_code
            == 400
        )
        assert (
            loopback.post(
                f"/api/remote/devices/{tablet_id}/revoke",
                json=["not-a-dict"],
                headers=loopback_headers,
            ).status_code
            == 400
        )

        rev_one = loopback.post(
            f"/api/remote/devices/{tablet_id}/revoke", json={}, headers=loopback_headers
        )
        assert rev_one.status_code == 200 and rev_one.json() == {"revoked": True}
        assert tablet.get("/api/subjects").status_code == 401
        assert phone.get("/api/subjects").status_code == 200
        assert loopback.post(
            "/api/remote/devices/999999/revoke", json={}, headers=loopback_headers
        ).json() == {"revoked": False}
        assert (
            loopback.post(
                f"/api/remote/devices/{2**70}/revoke", json={}, headers=loopback_headers
            ).status_code
            == 404
        )

        before_revoke_all_url = runtime.pairing.pairing_url
        rev_all = loopback.post("/api/remote/revoke-all", json={}, headers=loopback_headers)
        assert rev_all.status_code == 200 and rev_all.json() == {"revoked": 1}
        assert phone.get("/api/subjects").status_code == 401
        assert runtime.pairing.pairing_url != before_revoke_all_url

        # Rate-limit via POST /api/pair returns 429 rate_limited after MAX_PAIR_FAILURES bad attempts.
        for _ in range(MAX_PAIR_FAILURES):
            r = tablet.post(
                "/api/pair",
                json={"token": "wrong-pairing-token-0000"},
                headers=ts_origin,
            )
            assert (r.status_code, r.json()["error"]) == (401, "invalid_pairing_token")
        limited = tablet.post(
            "/api/pair",
            json={"token": runtime.pairing.pairing_url.split("#", 1)[1]},
            headers=ts_origin,
        )
        assert (limited.status_code, limited.json()["error"]) == (429, "rate_limited")


def test_loopback_without_runtime_does_not_create_db_and_reads_existing_db(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    client = TestClient(create_app(remote=None), base_url="http://127.0.0.1:8765")
    assert not remote_db_path().exists()

    auth = client.get("/api/auth")
    assert auth.status_code == 200
    assert auth.json() == {
        "access": "loopback",
        "paired": True,
        "tailscale_enabled": False,
        "paired_devices": 0,
    }
    remote = client.get("/api/remote")
    assert remote.status_code == 200
    assert remote.json() == {
        "active": False,
        "host": None,
        "pairing_url": None,
        "qr_matrix": None,
        "devices": [],
    }
    assert not remote_db_path().exists()

    # Pre-existing remote.db is read cleanly when remote=None.
    store = RemoteStore(remote_db_path())
    store.create_session("tok-phone", _IPHONE_UA, "100.64.0.10")
    store.create_session("tok-tablet", _IPAD_UA, "100.64.0.11")

    assert client.get("/api/auth").json()["paired_devices"] == 2
    info = client.get("/api/remote").json()
    assert info["active"] is False
    assert info["host"] is None
    assert info["pairing_url"] is None
    assert info["qr_matrix"] is None
    assert len(info["devices"]) == 2

    origin = {"Origin": "http://127.0.0.1:8765"}
    rev_all = client.post("/api/remote/revoke-all", json={}, headers=origin)
    assert rev_all.status_code == 200 and rev_all.json() == {"revoked": 2}
    assert client.get("/api/auth").json()["paired_devices"] == 0
