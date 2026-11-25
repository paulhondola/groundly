# Tailscale Remote SPA Access & Per-Device Pairing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable opt-in Tailscale SPA access (`web.tailscale` + `--tailscale/--no-tailscale`) over a direct dual-socket listener with socket-scoped `/mcp` isolation, `~/.groundly/remote.db` per-device 30-day cookie sessions, single-use `#fragment` QR pairing, loopback device management, and mobile browser resilience.

**Architecture:** `groundly serve` and `groundly start` pre-bind `127.0.0.1:{port}` and `<tailscale-ipv4>:{port}` into one `uvicorn.Server.run(sockets=...)` sharing `Runner` SSE pub-sub and `/mcp` lifespan. `_SocketPerimeterMiddleware` classifies requests by kernel `scope["server"][0]`: loopback stays no-auth with `/mcp` and `/api/remote*` enabled, while the Tailscale socket enforces Tailscale `Host`, hard-`404`s `/mcp` and `/api/remote*`, serves a no-oracle HTML shell pre-auth, and gates `/api/*` and `/s/*/materials/*` behind `sha256`-hashed session cookies in `~/.groundly/remote.db` minted via auto-rotating single-use `#fragment` QR tokens (`segno`).

**Tech Stack:** Python ≥3.11, FastAPI / Pydantic v2 / Starlette / uvicorn, SQLite WAL (`sqlite3`), `segno>=1.6` (pure-Python QR), Svelte 5.59+ / TypeScript / Tailwind v4 / Vite+ (`vp`), `openapi-typescript`, `openapi-fetch`, `@tanstack/svelte-query` v6, `sv-router`, pytest, Vitest (`vp test`).

**Spec:** [`docs/superpowers/specs/2026-11-25-tailscale-remote-spa-design.md`](../specs/2026-11-25-tailscale-remote-spa-design.md)

## Global Constraints

- **Never bind wildcard interfaces (`0.0.0.0` or `::`) and keep `proxy_headers=False`:** Bind only `127.0.0.1` and the explicit `100.64.0.0/10` Tailscale IPv4; default (`web.tailscale = false`, no `--tailscale`) binds `127.0.0.1` only.
- **`/mcp` and `/api/remote*` are strictly loopback-only:** Even a paired Tailscale client with a valid `groundly_session` cookie receives `404` on `/mcp`, `/mcp/*`, `/api/remote`, and `/api/remote/*`.
- **Zero pre-auth information leaks:** Unpaired Tailscale requests never call `_open_subject()` (known and unknown `/s/{subject}` HTML routes both return `200` `_html()`), `GET /api/auth` returns `paired_devices: 0` on Tailscale, and `/api/subjects*`, `/api/runs*`, `/s/*/materials/*` return `401 {"error": "unpaired", ...}`.
- **Session storage safety:** `~/.groundly/remote.db` (`groundly_home() / "remote.db"`) is created with `0600` permissions, SQLite WAL + `busy_timeout = 5000`, stores only `sha256` digests of raw 32-byte tokens, and lives outside `subjects/` so `groundly export` never touches it.
- **Strict `_CSP`, `AUTHORED_SOURCE` & `SHIPPED_BUNDLE` compliance (`tests/cli/test_cli_serve.py`):** `_CSP` stays unchanged (`img-src 'self'`, `script-src 'self'`, `connect-src 'self'`); zero `{@html`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `" onclick="`, `.endsWith(".pdf")`, `"verified"`, `http://`, or `https://` in `web/src/`; `crypto.randomUUID()` remains present in `AUTHORED_SOURCE` and `SHIPPED_BUNDLE`.
- **Interchange pin & layering guards:** Never touch `docling==`, `sentence-transformers==`, `FlagEmbedding==`, `rapidocr==`, or `mlx==` lines in `pyproject.toml` (`.claude/hooks/guard-pins.sh`); preserve `lint-imports` (`core` ← `web`/`cli`) and `test_importing_web_app_pulls_in_no_ml_or_retrieval`.
- **Runtime OpenAPI stays disabled (`404`):** `docs_url=None, redoc_url=None, openapi_url=None`; all new `/api/*` routes register unconditionally in `create_app()` so `uv run python -m groundly.web.openapi --check` is deterministic without Tailscale installed.
- **Feature branch only:** Commit on `feat/tailscale-remote-spa`, never on `main`.

## Review Focus

1. **Mixed-case MagicDNS `Host` vs lowercased browser `Origin` (`Host: MacBook.Tail1234.ts.net:8765` + `Origin: http://macbook.tail1234.ts.net:8765`):** `_SocketPerimeterMiddleware` and `_require_page_json` must accept mixed-case `Host` and match `Origin` case-insensitively on scheme+host so `POST /api/pair`, `POST .../attempts`, and `POST .../runs` succeed instead of returning `403`. *(Pinned in Task 3 `tests/web/test_remote.py`.)*
2. **Paired Tailscale client probing `/mcp` or `/api/remote*` with a valid `groundly_session` cookie:** Must still return `404` before reaching FastMCP or loopback device-management handlers. *(Pinned in Task 3 `tests/web/test_remote.py`.)*
3. **Unpaired Tailscale client probing subject existence (`GET /s/PDSS` vs `GET /s/NOPE`) or PDFs (`GET /s/PDSS/materials/1`):** Both HTML paths must return identical `200` `_html()` without calling `_open_subject()`, while `/s/PDSS/materials/1` returns `401 {"error": "unpaired"}`. *(Pinned in Task 3 `tests/web/test_remote.py`.)*
4. **Already-paired phone scanning a fresh QR code (`/pair#<new-token>` while holding an active `groundly_session` cookie):** `POST /api/pair` must atomically delete the old cookie's row in `remote.db` when inserting the new session (no duplicate device rows), and `authStore.bootstrap()` must scrub `#<new-token>` via `history.replaceState` before/during exchange even when previously paired. *(Pinned in Task 2 & Task 3 `tests/web/test_remote.py` and Task 5 `web/src/lib/__tests__/auth.test.ts`.)*
5. **Plain-HTTP Tailscale context (`crypto.randomUUID === undefined`) and mobile screen-lock SSE drop:** `generateAttemptToken()` must fall back to `crypto.getRandomValues` producing a valid `[A-Za-z0-9-]{8,64}` token without throwing, and `visibilitychange` (`visible`) / `online` after `streamErrors > MAX_STREAM_RETRIES` must reset `streamErrors = 0` and call `runStore.attach()`. *(Pinned in Task 6 `web/src/lib/__tests__/test-session.test.ts` and `web/src/lib/__tests__/run.test.ts`.)*

---

## File Structure

- **Config, Paths & Discovery:**
  - Modify `pyproject.toml`, `uv.lock` — add pure-Python `segno>=1.6`.
  - Modify `groundly/core/paths.py` — `remote_db_path()`.
  - Modify `groundly/core/config.py` & `tests/core/test_config.py` — `WebSettings` (`[web]`, `tailscale: bool = False`).
  - Create `groundly/web/tailscale.py` & `tests/web/test_tailscale.py` — `TailscaleInfo`, `discover_tailscale()`.
- **Remote Store, Pairing & ASGI Perimeter:**
  - Create `groundly/web/remote.py` & `tests/web/test_remote.py` — `RemoteStore`, `PairingManager`, `RemoteRuntime`, `qr_matrix`, `render_terminal_qr`, `_SocketPerimeterMiddleware`.
  - Modify `groundly/web/schemas.py`, `groundly/web/app.py`, `web/openapi.json`, `tests/web/test_openapi.py` — `/pair`, `/api/auth`, `/api/pair`, `/api/remote*`, case-insensitive `Origin` comparison.
- **CLI Server Lifecycle:**
  - Modify `groundly/cli/serve.py` & `tests/cli/test_cli_serve.py` — `--tailscale/--no-tailscale`, dual-socket bind, offline fallback, terminal QR + pairing notifications.
- **Frontend Auth, Device UI & Mobile Resilience (`web/`):**
  - Regenerate `web/src/lib/api/schema.d.ts`.
  - Modify `web/src/lib/api/client.ts`, `web/src/lib/api/queries.ts`, `web/src/lib/router.ts`, `web/src/App.svelte`.
  - Create `web/src/lib/auth.svelte.ts`, `web/src/lib/views/PairView.svelte`, `web/src/lib/components/QrCode.svelte`, `web/src/lib/components/RemoteDialog.svelte`.
  - Modify `web/src/lib/views/test-session.ts`, `web/src/lib/views/TestView.svelte`, `web/src/lib/run.svelte.ts`, `web/src/lib/components/SourceItem.svelte`.
  - Create/update frontend tests in `web/src/lib/__tests__/auth.test.ts`, `web/src/lib/components/__tests__/remote.test.ts`, `web/src/lib/views/__tests__/test-session.test.ts`, `web/src/lib/__tests__/run.test.ts`, `web/src/lib/components/__tests__/source-item.test.ts`, `web/src/lib/views/__tests__/views.test.ts`.
- **Bundle & Documentation:**
  - Rebuild `groundly/web/static/page.js` and `groundly/web/static/page.css`.
  - Update `docs/groundly-spec.md` (Decision 52), `docs/infrastructure/security.md`, `docs/tech-stack/tech-stack.md`, `.claude/rules/architecture.md`.

---

### Task 1: `[web]` Settings, `remote_db_path`, `segno` Dependency & Tailscale Discovery

**Files:**
- Modify: `pyproject.toml:29-31`, `uv.lock`
- Modify: `groundly/core/paths.py:13-33`
- Modify: `groundly/core/config.py:21-36,115-142`
- Create: `groundly/web/tailscale.py`
- Modify: `tests/core/test_config.py:22-66`
- Create: `tests/web/test_tailscale.py`

**Interfaces:**
- Produces:
  - `groundly.core.paths.remote_db_path() -> Path`
  - `groundly.core.config.WebSettings(BaseModel)` (`tailscale: bool = False`), `Settings.web: WebSettings = WebSettings()`
  - `groundly.web.tailscale.TailscaleInfo(ipv4: str, dns_name: str | None)` with `.preferred_host -> str` and `.allowed_hosts -> frozenset[str]`
  - `groundly.web.tailscale.discover_tailscale() -> TailscaleInfo | None`

- [ ] **Step 1: Write the failing config, path, and Tailscale discovery tests**

Add to `tests/core/test_config.py`:

```python
from groundly.core.paths import remote_db_path


def test_web_tailscale_default_and_round_trip(home):
    assert load_settings().web.tailscale is False
    assert remote_db_path() == home / "remote.db"
    set_key("web.tailscale", "true")
    assert load_settings().web.tailscale is True
    assert "[web]" in config_path().read_text()
    set_key("retrieval.top_k", "12")
    assert load_settings().web.tailscale is True


def test_unknown_section_lists_web(home):
    with pytest.raises(ConfigKeyError) as exc:
        set_key("nope.field", "x")
    assert "web" in str(exc.value)
```

Create `tests/web/test_tailscale.py`:

```python
import json
import subprocess

from groundly.web.tailscale import TailscaleInfo, discover_tailscale


def _status(
    *,
    state: str = "Running",
    ips: list[str] | None = None,
    dns_name: str = "MacBook.Tail1234.ts.net.",
):
    return json.dumps(
        {
            "BackendState": state,
            "TailscaleIPs": ips if ips is not None else ["100.101.102.103", "fd7a::1"],
            "Self": {"DNSName": dns_name, "TailscaleIPs": ["100.101.102.103"]},
        }
    )


def test_discover_parses_magicdns_and_cgnat(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/tailscale")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout=_status(), stderr=""),
    )
    info = discover_tailscale()
    assert info == TailscaleInfo(ipv4="100.101.102.103", dns_name="macbook.tail1234.ts.net")
    assert info.preferred_host == "macbook.tail1234.ts.net"
    assert info.allowed_hosts == frozenset({"100.101.102.103", "macbook.tail1234.ts.net"})


def test_discover_handles_disabled_magicdns_and_invalid_dns(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/tailscale")
    for bad_dns in ("", "not-a-ts-domain", "evil..ts.net.", "bad host.ts.net."):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, d=bad_dns, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout=_status(dns_name=d), stderr=""
            ),
        )
        info = discover_tailscale()
        assert info == TailscaleInfo(ipv4="100.101.102.103", dns_name=None)
        assert info.preferred_host == "100.101.102.103"


def test_discover_returns_none_when_stopped_non_cgnat_or_timed_out(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/tailscale")
    for payload in (_status(state="Stopped"), _status(ips=["192.168.1.10", "::1"])):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, p=payload, **kw: subprocess.CompletedProcess(a[0], 0, stdout=p, stderr=""),
        )
        assert discover_tailscale() is None

    def fail(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 2.0)

    monkeypatch.setattr(subprocess, "run", fail)
    assert discover_tailscale() is None


def test_discover_uses_macos_app_fallback(monkeypatch, tmp_path):
    fake_bin = tmp_path / "Tailscale"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("groundly.web.tailscale._MACOS_TAILSCALE_APP", fake_bin)
    seen_cmd: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen_cmd.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=_status(dns_name=""), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert discover_tailscale() == TailscaleInfo(ipv4="100.101.102.103", dns_name=None)
    assert seen_cmd == [[str(fake_bin), "status", "--json"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_config.py tests/web/test_tailscale.py -v`
Expected: FAIL (`ImportError: cannot import name 'remote_db_path'` and `ModuleNotFoundError: No module named 'groundly.web.tailscale'`).

- [ ] **Step 3: Add `segno`, `remote_db_path()`, `WebSettings`, and `groundly/web/tailscale.py`**

1. In `pyproject.toml`, append `"segno>=1.6",` directly after `"fastapi>=0.165.0",` (do NOT edit or include the `mlx==` line above it so `.claude/hooks/guard-pins.sh` permits the edit cleanly), then run `uv lock && uv sync --extra dev`.
2. In `groundly/core/paths.py`, add `remote_db_path() -> Path` returning `groundly_home() / "remote.db"`.
3. In `groundly/core/config.py`, add `WebSettings(BaseModel)` with `tailscale: bool = False`, add `web: WebSettings = WebSettings()` on `Settings`, and emit `[web]\ntailscale = ...` in `render_config_toml`.
4. Create `groundly/web/tailscale.py` defining `_MACOS_TAILSCALE_APP`, `_CGNAT = ipaddress.ip_network("100.64.0.0/10")`, `_TS_DNS_RE` (DNS labels ending in `.ts.net`), `TailscaleInfo`, and `discover_tailscale() -> TailscaleInfo | None` running `[binary, "status", "--json"]` with `stdin=subprocess.DEVNULL`, `timeout=2.0`, `check=False`.

- [ ] **Step 4: Run tests and static checks to verify pass**

Run: `uv run pytest tests/core/test_config.py tests/web/test_tailscale.py -v && uv run lint-imports && uv run pyrefly check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock groundly/core/paths.py groundly/core/config.py groundly/web/tailscale.py tests/core/test_config.py tests/web/test_tailscale.py
git commit -m "feat(web): add web.tailscale config, remote_db_path, segno, and tailscale discovery"
```

---

### Task 2: `RemoteStore` (`~/.groundly/remote.db`), `PairingManager` & QR Helpers

**Files:**
- Create: `groundly/web/remote.py`
- Create: `tests/web/test_remote.py`

**Interfaces:**
- Consumes: `groundly.core.paths.remote_db_path`, `groundly.web.tailscale.TailscaleInfo`
- Produces in `groundly.web.remote`:
  - `SESSION_COOKIE = "groundly_session"`, `SESSION_TTL_S = 30 * 86400`, `LAST_SEEN_THROTTLE_S = 60`, `MAX_PAIR_FAILURES = 5`, `PAIR_WINDOW_S = 60.0`
  - `PairAuthError(Exception)` (`status_code: int`, `error: str`, `detail: str`)
  - `parse_device_label(user_agent: str) -> str`
  - `RemoteStore(db_path: Path | None = None)` with `.create_session(raw_token: str, user_agent: str, peer_ip: str, *, replace_raw_token: str | None = None) -> dict[str, Any]`, `.authenticate(raw_token: str) -> bool`, `.list_devices() -> list[dict[str, Any]]`, `.count_devices() -> int`, `.revoke_device(device_id: int) -> bool`, `.revoke_all() -> int`
  - `PairingManager(ts_info: TailscaleInfo, port: int)` with `.pairing_url -> str`, `.rotate() -> str`, `.consume_and_rotate(candidate: str, peer_ip: str) -> str`
  - `qr_matrix(url: str) -> list[list[bool]]`, `render_terminal_qr(url: str) -> str`
  - `RemoteRuntime(ts_info: TailscaleInfo, port: int, store: RemoteStore | None = None, on_paired: Callable[[str, str, str], None] | None = None)` with `.pairing`, `.store`, `.on_paired`

- [ ] **Step 1: Write the failing unit tests in `tests/web/test_remote.py`**

```python
import hashlib
import os
import stat
import sqlite3

import pytest

from groundly.web.remote import (
    MAX_PAIR_FAILURES,
    PAIR_WINDOW_S,
    SESSION_TTL_S,
    PairAuthError,
    PairingManager,
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_remote.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'groundly.web.remote'`).

- [ ] **Step 3: Implement `RemoteStore`, `PairingManager`, `qr_matrix`, `render_terminal_qr`, and `RemoteRuntime` in `groundly/web/remote.py`**

Key requirements:
- `RemoteStore._connect()`: ensure parent exists; if DB file does not exist create it with `os.open(path, os.O_RDWR | os.O_CREAT, 0o600)` + `os.chmod(path, 0o600)` (also enforce `0o600` on existing file if group/other bits are set); execute `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `PRAGMA foreign_keys=ON`; check `PRAGMA user_version` (raise `RuntimeError` if `> 1`; create `sessions` table and set `PRAGMA user_version = 1` when `0`).
- `RemoteStore` uses `threading.Lock()` around multi-statement writes/reads so concurrent FastAPI threads share safely.
- `qr_matrix(url)` uses `segno.make(url, error="l")` and iterates `qr.matrix` into `list[list[bool]]`. `render_terminal_qr(url)` captures `qr.terminal(out=buf, compact=True, border=2)`.
- `parse_device_label(ua)` checks OS (`iPad`, `iPhone`, `Android`, `Macintosh`/`Mac OS`, `Windows`, `Linux`, fallback `Device`) and browser (`Edg/`/`EdgiOS` → `Edge`, `FxiOS`/`Firefox` → `Firefox`, `CriOS`/`Chrome` → `Chrome`, `Safari` → `Safari`, fallback `Browser`), never letting raw untrusted UA bytes into DB labels.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/web/test_remote.py -v && uv run lint-imports && uv run pyrefly check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groundly/web/remote.py tests/web/test_remote.py
git commit -m "feat(web): add RemoteStore session db, PairingManager, and QR helpers"
```

---

### Task 3: `_SocketPerimeterMiddleware`, Pairing/Device Routes & OpenAPI Contract

**Files:**
- Modify: `groundly/web/remote.py` (add `_SocketPerimeterMiddleware`)
- Modify: `groundly/web/schemas.py`
- Modify: `groundly/web/app.py`
- Modify: `web/openapi.json`
- Modify: `tests/web/test_remote.py`
- Modify: `tests/web/test_openapi.py`

**Interfaces:**
- Consumes: `RemoteRuntime`, `RemoteStore`, `HOSTS`, `remote_db_path`
- Produces:
  - `_SocketPerimeterMiddleware(app: ASGIApp, *, loopback_hosts: Sequence[str], html_shell: Callable[[], Response], remote: RemoteRuntime | None = None)` in `groundly/web/remote.py`
  - Schemas in `groundly/web/schemas.py`: `AuthStatusResponse`, `PairDeviceRequest`, `PairDeviceResponse`, `PairedDevice`, `RemoteInfoResponse`, `RevokeDeviceResponse`, `RevokeAllResponse`
  - `create_app(mcp_app: ASGIApp | None = None, mcp_url: str | None = None, remote: RemoteRuntime | None = None) -> FastAPI` in `groundly/web/app.py`
  - Routes: `GET /pair` (`include_in_schema=False`), `GET /api/auth` (`getAuthStatus`), `POST /api/pair` (`pairDevice`), `GET /api/remote` (`getRemoteInfo`), `POST /api/remote/devices/{id:int}/revoke` (`revokeRemoteDevice`), `POST /api/remote/revoke-all` (`revokeAllRemoteDevices`)
  - Updated `web/openapi.json` (`14` `/api/*` paths total)

- [ ] **Step 1: Write the failing ASGI perimeter & route tests in `tests/web/test_remote.py` and `tests/web/test_openapi.py`**

Update `tests/web/test_openapi.py` line 20 from `assert len(paths) == 9` to `assert len(paths) == 14` and assert `"/api/auth"`, `"/api/pair"`, `"/api/remote"`, `"/api/remote/devices/{id}/revoke"`, `"/api/remote/revoke-all"` are present.

Append HTTP integration tests to `tests/web/test_remote.py` using an ASGI helper `_socket_wrapper(app, server_ip, client_ip)` that sets `scope["server"] = (server_ip, 8765)` and `scope["client"] = (client_ip, 51234)` around `create_app(mcp_app, remote=runtime)`:
- Loopback client (`127.0.0.1`) hits `/api/auth` (`access="loopback", paired=True, tailscale_enabled=True, paired_devices=0`), `/api/subjects`, `/api/remote`, and `/mcp` without a cookie; hits `POST /api/pair` and gets `404`.
- Pre-auth Tailscale client (`100.101.102.103`):
  - `Host: localhost:8765` or `127.0.0.1:8765` returns `400`.
  - `GET /s/PDSS` and `GET /s/NOPE` both return `200` with identical HTML shell and `Content-Security-Policy` (no subject-existence oracle).
  - `GET /api/auth` returns `{"access": "tailscale", "paired": False, "tailscale_enabled": True, "paired_devices": 0}`.
  - `GET /api/subjects`, `GET /api/runs`, `GET /api/runs/stream`, `GET /s/PDSS/materials/1`, and `POST /api/subjects/PDSS/attempts` return `401 {"error": "unpaired", ...}`.
  - `GET /api/remote` and `POST /mcp` return `404`.
- Mixed-case MagicDNS `Host: MacBook.Tail1234.ts.net:8765` + lowercased `Origin: http://macbook.tail1234.ts.net:8765` on `POST /api/pair` succeeds (`200`), sets `HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000` cookie, records device in `remote.db`, invokes `on_paired`, and rotates QR token.
- Paired Tailscale client with valid cookie:
  - Still receives `404` on `/mcp`, `/mcp/anything`, `/api/remote`, `/api/remote/revoke-all`.
  - Can read `/api/subjects`, `/api/subjects/PDSS/tree`, `/s/PDSS/materials/1`, submit a test attempt (`POST /api/subjects/PDSS/attempts` with mixed-case `Host` + lowercased `Origin` -> `201`), and subscribe to `/api/runs/stream`.
  - Re-pairing from the same phone with the rotated token replaces its previous session row (still 1 row for phone).
- Phone + tablet concurrent workflow: tablet pairs with the rotated token → `GET /api/remote` on loopback lists both devices (`count == 2`); `POST /api/remote/devices/{tablet_id}/revoke` immediately yields `401 unpaired` for tablet while phone continues getting `200`; `POST /api/remote/revoke-all` invalidates phone too and rotates QR token.
- Loopback `remote=None` with pre-existing `remote.db`: `GET /api/auth` reports `paired_devices == 2` and `GET /api/remote` lists them with `active=False, pairing_url=None, qr_matrix=None`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_remote.py tests/web/test_openapi.py -v`
Expected: FAIL (`AttributeError`/`TypeError` on missing middleware, schemas, and `remote` param).

- [ ] **Step 3: Implement `_SocketPerimeterMiddleware`, schemas, route handlers, and export `web/openapi.json`**

1. In `groundly/web/remote.py`, implement `_SocketPerimeterMiddleware`:
   - Inspect `scope["type"] == "http"` (non-HTTP scopes pass straight through).
   - Determine `is_tailscale = remote is not None and server_ip == remote.ts_info.ipv4`.
   - Validate `Host` header (strip optional `:port`, lowercase): against `remote.ts_info.allowed_hosts` if `is_tailscale`, else against `{h.lower() for h in loopback_hosts}` (`400 PlainTextResponse("Invalid host header")` on mismatch — matching Starlette `TrustedHostMiddleware`).
   - Store `scope.setdefault("state", {})["access"] = "tailscale" if is_tailscale else "loopback"`.
   - On loopback: set `scope["state"]["paired"] = True` and forward to `self.app`.
   - On Tailscale:
     - If `path == "/mcp"` or `path.startswith("/mcp/")`: return `Response("not found", status_code=404)`.
     - If `path == "/api/remote"` or `path.startswith("/api/remote/")`: return `JSONResponse({"error": "not_found", "detail": "remote management is local only"}, status_code=404)`.
     - Parse `groundly_session` cookie (via `http.cookies.SimpleCookie` or `Request(scope).cookies`) and validate once via `remote.store.authenticate(cookie)` if present; store `scope["state"]["paired"] = paired`.
     - Allow `GET / HEAD` `/static/*`, `/api/auth`, and `POST /api/pair` without cookie.
     - For `GET / HEAD` HTML shell paths (`path in {"/", "/pair"}` or `_SUBJECT_SHELL_RE` matching `/s/[^/]+`, `/s/[^/]+/test`, `/s/[^/]+/attempts/\d+`): if `not paired`, return `html_shell()` (`200` with `_CSP`, never calling `_open_subject()`); if `paired`, forward to `self.app`.
     - Otherwise, if `not paired`: return `JSONResponse({"error": "unpaired", "detail": "scan the pairing QR code on your computer"}, status_code=401)`.
2. In `groundly/web/schemas.py`, add the strict Pydantic models (`PairDeviceRequest` with `ConfigDict(strict=True, extra="forbid")`, `AuthStatusResponse`, `PairDeviceResponse`, `PairedDevice`, `RemoteInfoResponse`, `RevokeDeviceResponse`, `RevokeAllResponse`).
3. In `groundly/web/app.py`:
   - Update `create_app(mcp_app: ASGIApp | None = None, mcp_url: str | None = None, remote: RemoteRuntime | None = None) -> FastAPI` to pass `Middleware(_SocketPerimeterMiddleware, loopback_hosts=HOSTS, html_shell=_html, remote=remote)` instead of `TrustedHostMiddleware`.
   - In `_require_page_json`, compare `origin.lower() != f"{request.url.scheme}://{request.headers.get('host', '')}".lower()` (handling `origin is None` safely).
   - Register `GET /pair` (`include_in_schema=False`), `GET /api/auth`, `POST /api/pair`, `GET /api/remote`, `POST /api/remote/devices/{id:int}/revoke`, and `POST /api/remote/revoke-all` (`_require_page_json` on POSTs, `StopRunRequest.model_validate` for `{}` revocation POSTs, checking `remote_db_path().exists()` before instantiating `RemoteStore` on `GET /api/auth` / `GET /api/remote` when `remote is None` so fresh loopback sessions never create an unused `remote.db` file).
4. Run `uv run python -m groundly.web.openapi` to regenerate `web/openapi.json`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/web/ -v && uv run lint-imports && uv run pyrefly check`
Expected: PASS across `test_app.py`, `test_runs.py`, `test_remote.py`, and `test_openapi.py`.

- [ ] **Step 5: Commit**

```bash
git add groundly/web/remote.py groundly/web/schemas.py groundly/web/app.py web/openapi.json tests/web/test_remote.py tests/web/test_openapi.py
git commit -m "feat(web): add socket-scoped perimeter middleware, pairing/device routes, and OpenAPI schema"
```

---

### Task 4: CLI `--tailscale/--no-tailscale`, Dual-Socket Bind & Offline Fallback

**Files:**
- Modify: `groundly/cli/serve.py:1-108`
- Modify: `tests/cli/test_cli_serve.py:36-119`

**Interfaces:**
- Consumes: `load_settings`, `discover_tailscale`, `RemoteRuntime`, `render_terminal_qr`, `create_app(..., remote=...)`
- Produces: `serve(port: _PORT = 8765, tailscale: _TAILSCALE = None) -> None` and `start(port: _PORT = 8765, tailscale: _TAILSCALE = None) -> None`

- [ ] **Step 1: Write the failing CLI tests in `tests/cli/test_cli_serve.py`**

Update `_patch` helper's `fake_run(self, sockets=None)` so it records `"sockets": sockets` alongside `"host"`/`"port"`, then add tests covering:
- `groundly serve --tailscale` and `groundly start --tailscale`: bind sockets for `("127.0.0.1", port)` and `("100.101.102.103", port)`, attach `RemoteRuntime` to `create_app`, print `Tailscale:`, `Pair URL:` containing `/pair#`, and terminal QR lines, while `_open_browser` still receives only `http://groundly.localhost:8765/` and `mcp_url` stays `http://127.0.0.1:8765/mcp` (and `test_start_binds_loopback_opens_page_and_prints_both_urls` stays green).
- Explicit `--tailscale` when `discover_tailscale()` returns `None` exits with code 1 and prints `--no-tailscale` hint; `runs == []` and `opened == []`.
- `web.tailscale = true` in `config.toml` when `discover_tailscale()` returns `None` prints warning (`127.0.0.1 only`) and serves single loopback socket cleanly; `--no-tailscale` overrides `web.tailscale = true` without calling `_fail` or binding a second socket.
- Second socket bind failure (`OSError` on Tailscale IP after `127.0.0.1` succeeded) closes the first socket cleanly and exits 1 naming the cause without a traceback.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_cli_serve.py -v`
Expected: FAIL (`No such option: --tailscale`).

- [ ] **Step 3: Implement `--tailscale/--no-tailscale`, dual-socket pre-binding, and QR printing in `groundly/cli/serve.py`**

- Resolve `want_ts = load_settings().web.tailscale if tailscale is None else tailscale`.
- Call `discover_tailscale()` only when `want_ts` is `True`; handle explicit `--tailscale` fail vs config warning fallback.
- Bind `("127.0.0.1", port)` (and `(ts_info.ipv4, port)` when `ts_info` is active) using helper `_bind_socket(host: str, port: int) -> socket.socket` (`SO_REUSEADDR`, `.bind()`, `.listen(128)`), closing already-bound sockets on any bind error via `try ... except` before `_fail(...)`.
- Create `RemoteRuntime(ts_info, port=port, on_paired=...)` where `on_paired(label, peer_ip, next_url)` prints `Paired {_safe(label)} ({peer_ip}) — next pair link: {next_url}` via `console.print(..., highlight=False, soft_wrap=True)`.
- Pass `sockets=sockets` to `_PageServer(config).run(sockets=sockets)` and close `sockets` in `finally:` after exit (`host` remains `"127.0.0.1"` in `uvicorn.Config` for consistency with existing config attributes).

- [ ] **Step 4: Run CLI tests and Python quality gates**

Run: `uv run pytest tests/cli/test_cli_serve.py -v && uv run ruff check . && uv run lint-imports && uv run pyrefly check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add groundly/cli/serve.py tests/cli/test_cli_serve.py
git commit -m "feat(cli): add --tailscale/--no-tailscale dual-socket serving and QR output"
```

---

### Task 5: Frontend Generated Types, `authStore`, `#fragment` `PairView` & Loopback `RemoteDialog`

**Files:**
- Modify: `web/src/lib/api/schema.d.ts` (via `pnpm run gen:api`)
- Modify: `web/src/lib/api/client.ts`
- Modify: `web/src/lib/api/queries.ts`
- Modify: `web/src/lib/router.ts`
- Modify: `web/src/lib/run.svelte.ts` (add `disconnect(): void`)
- Modify: `web/src/App.svelte`
- Create: `web/src/lib/auth.svelte.ts`
- Create: `web/src/lib/components/QrCode.svelte`
- Create: `web/src/lib/components/RemoteDialog.svelte`
- Create: `web/src/lib/views/PairView.svelte`
- Create: `web/src/lib/__tests__/auth.test.ts`
- Create: `web/src/lib/components/__tests__/remote.test.ts`
- Modify: `web/src/lib/views/__tests__/views.test.ts` (mock `/api/auth` + `authStore.resetForTests()` in `App shell` block)

**Interfaces:**
- Consumes: `web/openapi.json` (`getAuthStatus`, `pairDevice`, `getRemoteInfo`, `revokeRemoteDevice`, `revokeAllRemoteDevices`)
- Produces:
  - `client.ts`: type exports `AuthStatusResponse`, `RemoteInfoResponse`, `PairedDevice`, plus `setOnUnpaired(handler: (() => void) | null): void` wired into `apiClient.use({ onResponse })`
  - `queries.ts`: `queryKeys.remote()` (`["remote"] as const`)
  - `auth.svelte.ts`: `createAuthStore(qc?: QueryClient, onUnpaired?: () => void)` and singleton `authStore` (`ready`, `access`, `paired`, `tailscaleEnabled`, `pairedDevices`, `pairing`, `error`, `bootstrap()`, `handleUnpaired()`, `resetForTests()`)
  - `QrCode.svelte`: `<QrCode matrix={boolean[][]} />`
  - `RemoteDialog.svelte`: `<RemoteDialog />` header button + popover
  - `PairView.svelte`: `<PairView />` unpaired / pairing status card

- [ ] **Step 1: Regenerate `web/src/lib/api/schema.d.ts` and write failing auth & component tests**

Run: `cd web && pnpm run gen:api`

Create `web/src/lib/__tests__/auth.test.ts` testing:
- `bootstrap()` on loopback resolves `access="loopback"`, `paired=true`, `tailscaleEnabled`, `pairedDevices`.
- `bootstrap()` on unpaired Tailscale resolves `access="tailscale"`, `paired=false` without calling `/api/subjects`.
- `bootstrap()` when `location.pathname === "/pair"` and `location.hash === "#tok123"` calls `history.replaceState` immediately to scrub `#tok123` to `"/"`, sends `POST /api/pair` with `{ token: "tok123" }`, and resolves `paired=true` (also works when re-scanning `/pair#<new>` from an already-paired session).
- Burned/invalid token (`401 {"error": "invalid_pairing_token", "detail": "..."}`) resolves `paired=false` and surfaces `detail` in `authStore.error`.
- `setOnUnpaired` middleware invokes `handleUnpaired()` when a non-`/api/pair` call returns `401`, clearing `queryClient`, closing SSE, and setting `paired=false`, while `401` from `/api/pair` does not loop into `handleUnpaired`.

Create `web/src/lib/components/__tests__/remote.test.ts` testing:
- `QrCode.svelte` renders `<svg role="img">` containing one `<path d=...>` encoding active modules with quiet zone.
- `RemoteDialog.svelte` toggles open, displays `"iPhone · Safari"` from `GET /api/remote`, and sends `POST /api/remote/devices/{id}/revoke` and `POST /api/remote/revoke-all` on button clicks.

- [ ] **Step 2: Run frontend tests to verify they fail**

Run: `cd web && pnpm exec vp test run src/lib/__tests__/auth.test.ts src/lib/components/__tests__/remote.test.ts`
Expected: FAIL (`Cannot find module '../auth.svelte'` / `'../QrCode.svelte'`).

- [ ] **Step 3: Implement `client.ts`, `queries.ts`, `auth.svelte.ts`, `router.ts`, `QrCode.svelte`, `RemoteDialog.svelte`, `PairView.svelte`, `App.svelte`, and update `views.test.ts`**

- In `client.ts`: export `AuthStatusResponse`, `RemoteInfoResponse`, `PairedDevice`; add `setOnUnpaired(handler: (() => void) | null): void` wired into `apiClient.use({ onResponse })` checking `response.status === 401` and `!requestPath.endsWith("/api/pair")` (parsing via `new URL(request.url, location.origin).pathname` when `location` is defined, else `request.url` — zero `"http://"` literal strings anywhere in `web/src/`).
- In `run.svelte.ts`: expose `disconnect(): void` (calls `closeStream()` without removing window/document listeners) alongside `destroy(): void`.
- In `auth.svelte.ts`: start singleton `authStore` with `{ ready = false, access = "loopback", paired = true }` (`paired = true` keeps standalone `runStore` tests unaffected while `App.svelte` gates `<Router />` on `authStore.ready`), wire `createAuthStore(queryClient, () => runStore.disconnect())` lazily (avoiding top-level ESM circular initialization), and register `setOnUnpaired(() => authStore.handleUnpaired())`. On `handleUnpaired()`: set `ready = true`, `access = "tailscale"`, `paired = false`, `error = "This device is no longer paired — scan the current QR code to reconnect."`, call `onUnpaired?.()`, and `qc.clear()`. `resetForTests()` restores defaults (`ready = false, access = "loopback", paired = true, ...`).
- In `router.ts`: add `"/pair"` route mapping cleanly (e.g. `beforeLoad` on `"/pair"` navigates to `"/"` whenever the paired router is mounted).
- In `App.svelte`: trigger `void authStore.bootstrap()` once on mount; render header (`Brand`, breadcrumb if `authStore.paired`, `RemoteDialog` when `authStore.access === "loopback" && (authStore.tailscaleEnabled || authStore.pairedDevices > 0)`, `Theme` selector); inside `<main>`, gate `<RunStrip />` and `<Router />` behind `authStore.ready`: show neutral loading text while bootstrapping, `<PairView />` when `!authStore.paired`, and `<RunStrip /><Router />` when `authStore.paired`.
- In `web/src/lib/views/__tests__/views.test.ts`: reset `authStore.resetForTests()` in `App shell` `beforeEach`/`afterEach` and return `jsonResponse({ access: "loopback", paired: true, tailscale_enabled: false, paired_devices: 0 })` for `/api/auth`.

- [ ] **Step 4: Run web lint, check, and tests**

Run: `cd web && pnpm run fmt && pnpm exec vp lint && pnpm exec svelte-check --tsconfig ./tsconfig.json && pnpm exec vp test run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/
git commit -m "feat(web): add Tailscale pairing flow, authStore, QrCode, and loopback Devices dialog"
```

---

### Task 6: Mobile SPA Resilience (`generateAttemptToken`, SSE Wake-Up Resync & `SourceItem` Passage Toggle)

**Files:**
- Modify: `web/src/lib/views/test-session.ts`
- Modify: `web/src/lib/views/TestView.svelte:18,153-157`
- Modify: `web/src/lib/run.svelte.ts:244-389`
- Modify: `web/src/lib/components/SourceItem.svelte:1-38`
- Modify: `web/src/lib/views/__tests__/test-session.test.ts`
- Modify: `web/src/lib/__tests__/run.test.ts`
- Create: `web/src/lib/components/__tests__/source-item.test.ts`

**Interfaces:**
- Consumes: `authStore` (to skip wake-up resync when `!authStore.paired`), `queryKeys.allSubjects()`
- Produces:
  - `generateAttemptToken(): string` in `web/src/lib/views/test-session.ts`
  - Wake-up `visibilitychange` + `online` listeners in `createRunStore` cleaned up in `.destroy()`
  - Expandable `Passage` toggle in `web/src/lib/components/SourceItem.svelte`

- [ ] **Step 1: Write the failing mobile resilience tests**

1. In `web/src/lib/views/__tests__/test-session.test.ts`: test `generateAttemptToken()` delegates to `crypto.randomUUID()` when defined, and when `crypto.randomUUID` is stubbed `undefined` falls back to `crypto.getRandomValues` producing a 32-char lowercase hex string matching `/^[A-Za-z0-9-]{8,64}$/`.
2. In `web/src/lib/__tests__/run.test.ts`: test that after SSE errors exhaust `MAX_STREAM_RETRIES`, dispatching `document` `"visibilitychange"` (with `visibilityState = "visible"`) or `window` `"online"` resets retry state, calls `GET /api/runs`, re-opens `/api/runs/stream`, and stops firing once `store.destroy()` is called.
3. Create `web/src/lib/components/__tests__/source-item.test.ts`: test `SourceItem` when `src.pdf === true` with non-empty `src.text` renders both `<a target="_blank" ...>` and a `Passage` button (`aria-expanded="false"` initially, toggling to `"true"` and rendering `src.text` inside `<pre>` as plain text without HTML injection), and omits the toggle when `src.text` is blank.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && pnpm exec vp test run src/lib/views/__tests__/test-session.test.ts src/lib/__tests__/run.test.ts src/lib/components/__tests__/source-item.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement `generateAttemptToken`, SSE wake-up listeners, and `SourceItem` passage disclosure**

- In `test-session.ts`: add `generateAttemptToken()` keeping `crypto.randomUUID()` verbatim so `AUTHORED_SOURCE` and `SHIPPED_BUNDLE` assertions keep passing; call it from `TestView.svelte`.
- In `run.svelte.ts`: attach `onWake()` to `document` (`visibilitychange`, gated on `document.visibilityState === "visible"`) and `window` (`online`) when globals exist. Inside `onWake()`: check `if (!authStore.paired) return;` lazily (avoiding top-level ESM circular access); reset `streamErrors = 0`; if `message?.startsWith("Lost ")` clear `message = null`; call `void attach()` and `void qc.invalidateQueries({ queryKey: queryKeys.allSubjects() })`. Remove both listeners in `destroy()`.
- In `SourceItem.svelte`: keep `src.pdf === true` branch (`<a {href} target="_blank" rel="noopener">`); add local `let expanded = $state(false)` and a `Passage` toggle button (`aria-expanded={expanded}`) beside the link when `src.text.trim() !== ""` revealing `<pre>{src.text}</pre>`.

- [ ] **Step 4: Run web lint, type-check, and tests**

Run: `cd web && pnpm run fmt && pnpm exec vp lint && pnpm exec svelte-check --tsconfig ./tsconfig.json && pnpm exec vp test run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/
git commit -m "feat(web): add plain-HTTP token fallback, wake-up SSE resync, and PDF passage toggle"
```

---

### Task 7: Production Bundle Build, Full Verification, Graphify & Decision 52 Docs

**Files:**
- Modify: `groundly/web/static/page.js`, `groundly/web/static/page.css`
- Modify: `docs/groundly-spec.md`
- Modify: `docs/infrastructure/security.md`
- Modify: `docs/tech-stack/tech-stack.md`
- Modify: `.claude/rules/architecture.md`
- Update: `graphify-out/` (if `graphify` CLI is installed)

- [ ] **Step 1: Build and verify the web workspace end to end**

Run:
```bash
cd web && pnpm run gen:api && pnpm run fmt && pnpm exec vp lint && pnpm exec vp fmt --check && pnpm exec svelte-check --tsconfig ./tsconfig.json && pnpm exec vp test run && pnpm run build
```
Expected: zero diffs on `src/lib/api/schema.d.ts`, all web checks and Vitest tests pass, `groundly/web/static/page.js` and `page.css` updated.

- [ ] **Step 2: Record Decision 52 and update architecture/security/tech-stack docs**

1. **`docs/groundly-spec.md`:** Add Decision 52 (amending Decisions 41, 44, 45, 50, 51) in §7 documenting opt-in `[web] tailscale = false` + `--tailscale/--no-tailscale`, dual-socket bind (`127.0.0.1` + `100.64.0.0/10`), `_SocketPerimeterMiddleware`, `/mcp` loopback-only isolation, `~/.groundly/remote.db` (`0600`, `sha256` session cookies), single-use `#fragment` QR pairing (`segno`), and mobile SPA resilience; update §4/§8 references where applicable.
2. **`docs/infrastructure/security.md`:** Update §4 (*Local servers*) and §5 (*Privacy*) with the opt-in Tailscale perimeter controls, why `0.0.0.0` and `proxy_headers` stay off, `remote.db` (`0600`, never exported), rate limiting, and pre-auth no-oracle HTML shell.
3. **`docs/tech-stack/tech-stack.md` & `.claude/rules/architecture.md`:** Document `segno`, `[web]` in `config.toml`, and `remote.db` / dual-socket perimeter under Storage & concurrency / Frameworks.

- [ ] **Step 3: Run full Python verification suite (including bundle hygiene and no-ML import checks)**

Run:
```bash
uv run ruff check .
uv run ruff format --check .
uv run lint-imports
uv run pyrefly check
uv run pytest
```
Expected: PASS across every test including `tests/cli/test_cli_serve.py` (`AUTHORED_SOURCE` + `SHIPPED_BUNDLE`), `tests/web/test_app.py::test_importing_web_app_pulls_in_no_ml_or_retrieval`, `tests/web/test_openapi.py`, and `tests/web/test_remote.py`.

- [ ] **Step 4: Refresh knowledge graph (`graphify`) if available**

Check `command -v graphify >/dev/null 2>&1 && graphify update .` (per `CLAUDE.md` — AST-only, no API cost).

- [ ] **Step 5: Commit final bundle, docs, and graph updates**

```bash
git add groundly/web/static/ docs/groundly-spec.md docs/infrastructure/security.md docs/tech-stack/tech-stack.md .claude/rules/architecture.md
git commit -m "build(web): ship Tailscale remote SPA bundle, security updates, and decision 52"
```
(If `graphify update .` modified `graphify-out/`, inspect `git status` and include or leave per repo convention — verify clean `git status` apart from pre-existing unrelated worktree files.)
