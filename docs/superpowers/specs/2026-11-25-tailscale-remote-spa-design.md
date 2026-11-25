# Tailscale Remote SPA Access & Per-Device Pairing — Design

Date: 2026-11-25 · Size: project · Records as decision 52 (amending decisions 41, 44, 45, 50, 51, via `/decision`) · Sub-project 2 of 2 (following decision 51's typed REST client, TanStack Query v6 & `sv-router`).

## Intent

Groundly's topic-tree and test SPA (`groundly serve` and `groundly start`) currently binds strictly to `127.0.0.1` with fixed `HOSTS = ["127.0.0.1", "localhost", "groundly.localhost"]` and zero authentication ("no-auth is acceptable exactly and only on loopback", [`docs/infrastructure/security.md`](../../infrastructure/security.md)). Students want to open and interact with the exact same SPA from a **phone or tablet over Tailscale** — reviewing topic maps and cited course passages on the couch or commute, sitting and submitting quizzes to `progress.db`, and triggering or watching headless `claude -p` runs (`Write questions` / `Grade now`) over live SSE — while keeping laptop loopback zero-friction and keeping `/mcp` strictly unreachable off-box.

Direct Tailscale interface binding (`100.64.0.0/10`) paired with a socket-scoped ASGI perimeter gate, per-device SQLite sessions (`~/.groundly/remote.db`), and strict single-use `#fragment` QR pairing tokens achieves full SPA parity across laptop, phone, and tablet with:
- Zero Tailscale admin-console prerequisites (no `tailscale serve` daemon state or tailnet HTTPS cert provisioning required; WireGuard encrypts traffic end to end).
- Kernel-level separation (`getsockname()` via ASGI `scope["server"]`) between the local `127.0.0.1` listener (no-auth, `/mcp` enabled, device management enabled) and the `<tailscale-ipv4>` listener (cookie-gated, `/mcp` and device management hard-`404`d).
- Independent revocable sessions for multiple devices (e.g., phone + tablet) without weakening `_CSP` or adding any HTML-inserting sink or external URL to `page.js`.

## Goals

1. **Opt-in configuration & CLI flag (`groundly/core/config.py`, `groundly/cli/serve.py`):**
   - Add `WebSettings(tailscale: bool = False)` under `Settings.web` (`[web]` in `~/.groundly/config.toml`), configurable via `groundly config set web.tailscale true`.
   - Add `--tailscale / --no-tailscale` (`default=None`) to both `groundly serve` and `groundly start`, overriding `config.toml` when passed explicitly.
   - When `web.tailscale = true` comes from `config.toml` and Tailscale is disconnected or absent, emit a clear yellow warning and fall back cleanly to `127.0.0.1`-only so offline local study never breaks; when `--tailscale` is passed explicitly on the CLI and Tailscale is unavailable, fail fast with a plain-language error.
2. **Direct dual-socket Tailscale listener (`groundly/web/tailscale.py`, `groundly/cli/serve.py`):**
   - Discover the host's active `100.64.0.0/10` Tailscale IPv4 and optional `.ts.net` MagicDNS name via `tailscale status --json` (argv exec, `stdin=DEVNULL`, 2 s timeout, checking `PATH` plus macOS `/Applications/Tailscale.app/Contents/MacOS/Tailscale`).
   - Pre-bind two explicit `SO_REUSEADDR` TCP sockets — `("127.0.0.1", port)` and `(ts.ipv4, port)` — and pass both to a single `uvicorn.Server.run(sockets=[loop_sock, ts_sock])` sharing one FastAPI `app`, one `Runner` SSE pub-sub instance, and (under `start`) one `/mcp` lifespan, with `proxy_headers=False` unchanged.
   - Print a compact Unicode half-block terminal QR code (`segno`) + pairing URL on startup, while still opening only `http://groundly.localhost:{port}/` in the host browser.
3. **Socket-scoped ASGI perimeter gate (`groundly/web/remote.py`):**
   - Classify every HTTP request by kernel `scope["server"][0]` (the listening socket's own IP):
     - **Loopback (`127.0.0.1`):** Enforce `HOSTS` (`127.0.0.1`, `localhost`, `groundly.localhost`); require no cookie; allow `/mcp` (under `start`) and loopback-only `/api/remote*` management routes.
     - **Tailscale (`ts.ipv4`):** Enforce Tailscale `Host` (`{ts.ipv4}` plus `ts.dns_name` when present; reject `localhost`/`127.0.0.1`); return `404` on `/mcp` (and `/mcp/*`) and `/api/remote*` before routing; serve unauthenticated HTML shell navigations (`/`, `/pair`, `/s/{subject}`, `/s/{subject}/test`, `/s/{subject}/attempts/{id}`) directly as `200` `_html()` without calling `_open_subject()` (preventing pre-auth subject-name probing); allow `/static/*`, `GET /api/auth`, and `POST /api/pair`; require a valid `groundly_session` cookie on every other route (`401 {"error": "unpaired", "detail": "..."}`).
4. **Per-device SQLite sessions & single-use auto-rotating QR pairing (`~/.groundly/remote.db`):**
   - Persist paired device sessions in `~/.groundly/remote.db` (`0600`, SQLite WAL + `busy_timeout`, `PRAGMA user_version = 1`), storing `sha256(cookie_token)` (never raw tokens), sanitized User-Agent device label (e.g., `"iPhone · Safari"`, `"iPad · Safari"`), Tailscale `peer_ip`, `created_at`, throttled `last_seen_at`, and 30-day `expires_at`.
   - Mint an in-memory single-use pairing token (`secrets.token_urlsafe(24)`) formatted as `http://{preferred_host}:{port}/pair#{token}` (`#fragment` keeps secrets out of HTTP GET URLs and Referers).
   - On `POST /api/pair`, enforce `_require_page_json` + per-IP failure rate limiting + `hmac.compare_digest`, atomically burn the active token, supersede any prior session cookie presented by the same browser, insert the new session row, mint the next pairing token in memory, emit a 1-line terminal notice with the next pairing link, and set `HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000`.
   - Expose loopback-only `GET /api/remote` (returning active status, pairing URL, boolean `qr_matrix` for zero-`{@html}` SVG rendering, and paired devices), `POST /api/remote/devices/{id}/revoke`, and `POST /api/remote/revoke-all`.
5. **Svelte 5 SPA pairing, device management & mobile resilience (`web/src/`):**
   - Bootstrap `GET /api/auth` in `App.svelte`: render `PairView.svelte` on unpaired Tailscale sessions (auto-exchanging `#<token>` and scrubbing `location.hash` via `history.replaceState`), render a loopback-only `RemoteDialog.svelte` **Devices** popover in the header (showing live-rotating `<svg>` QR + per-device / all-device revoke), and catch `401` (`error: "unpaired"`) globally in `openapi-fetch` to drop revoked devices cleanly back to `PairView`.
   - Fix three mobile browser sharp edges: non-HTTPS `crypto.getRandomValues` token fallback alongside `crypto.randomUUID()` in `TestView.svelte`, `visibilitychange`/`online` SSE auto-resync in `run.svelte.ts`, and an inline expandable `Passage` chunk-text toggle alongside PDF links in `SourceItem.svelte`.

## Non-goals

- Public internet exposure, outbound relay tunnels (Cloudflare/ngrok), or multi-user accounts/permissions.
- Exposing `/mcp` over Tailscale (MCP hosts run locally on the computer or connect over stdio; page-started `claude -p` runs execute on the host and reach `http://127.0.0.1:{port}/mcp` over loopback).
- Managing or mutating `tailscale serve` / `tailscale funnel` daemon state.
- Syncing *unsubmitted* in-progress test drafts across devices (drafts remain per-device in `sessionStorage` until submitted to `progress.db`).
- Server-side PDF page rasterization for mobile previews (`src.text` is already delivered in `SourceItem` payloads).

## Anti-goals (a failure even if it works)

- **Binding `0.0.0.0` or `::`:** Groundly must never bind wildcard interfaces (which would expose the server on untrusted campus/cafe Wi-Fi LANs). Only `127.0.0.1` and the explicit `100.64.0.0/10` Tailscale interface IP may be bound.
- **Enabling `proxy_headers=True` or trusting `X-Forwarded-*`:** Origin checks must continue using socket ground truth (`request.url.scheme` and validated `Host`).
- **Exposing `/mcp` or `/api/remote*` on the Tailscale socket:** Even a paired phone with a valid `groundly_session` cookie must receive `404` on `/mcp` and `/api/remote*`.
- **Leaking subject names or existence before pairing:** Unauthenticated requests on the Tailscale socket must never reveal whether `/s/{subject}` exists (`200` vs `404`) or return `/api/subjects` / `/api/runs` payloads.
- **Storing raw session tokens on disk or inside `subjects/`:** `~/.groundly/remote.db` stores only `sha256` digests at `groundly_home() / "remote.db"` (`0600`), never inside a subject folder where `groundly export` could read it.
- **Weakening `_CSP` or `AUTHORED_SOURCE` / `SHIPPED_BUNDLE` checks:** No `{@html}`, `innerHTML`, `data:` image URLs (`img-src 'self'` stays strict), runtime `http://`/`https://` literals in `web/src/`, or `"verified"` strings anywhere in `web/src/` or compiled `groundly/web/static/page.{js,css}`.

## Constraints

- **Default remains strictly loopback-only:** Fresh installs and existing configs without `web.tailscale = true` or `--tailscale` bind only `127.0.0.1` and create no `remote.db` until remote endpoints or Tailscale mode touch it.
- **Offline OpenAPI determinism:** All new `/api/*` routes are registered unconditionally in `create_app()` so `uv run python -m groundly.web.openapi --check` is deterministic on CI machines without Tailscale installed.
- **Zero ML/retrieval imports in `groundly.web`:** `test_importing_web_app_pulls_in_no_ml_or_retrieval` continues passing; `segno` is pure Python with zero transitive dependencies.
- **Layering contract (`pyproject.toml` / `lint-imports`):** `remote_db_path()` lives in `groundly/core/paths.py`; `tailscale.py` and `remote.py` live in `groundly/web/` (client layer).

---

## Architecture & Module Design

### 1. Settings & Paths (`groundly/core/config.py`, `groundly/core/paths.py`)

- **`groundly/core/paths.py`:**
  - Add `remote_db_path() -> Path` returning `groundly_home() / "remote.db"`.
- **`groundly/core/config.py`:**
  - Add `WebSettings`:
    ```python
    class WebSettings(BaseModel):
        tailscale: bool = False  # bind Tailscale IP + QR pairing on serve/start
    ```
  - Add `web: WebSettings = WebSettings()` to `Settings`.
  - Append `[web]` section to `render_config_toml(settings)`:
    ```toml
    [web]
    tailscale = false   # also serve the page on your Tailscale IP (paired via QR code)
    ```
  - `groundly config` automatically displays `web.tailscale` and `groundly config set web.tailscale true` validates via existing `_EXPECTED[bool]`.

### 2. Tailscale Discovery (`groundly/web/tailscale.py`)

Focused module with zero FastAPI coupling:

```python
@dataclass(frozen=True)
class TailscaleInfo:
    ipv4: str
    dns_name: str | None

    @property
    def preferred_host(self) -> str:
        return self.dns_name or self.ipv4

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({self.ipv4, *((self.dns_name,) if self.dns_name else ())})
```

- Binary lookup: `shutil.which("tailscale")`, falling back on `sys.platform == "darwin"` to `/Applications/Tailscale.app/Contents/MacOS/Tailscale` if executable.
- Runs `subprocess.run([binary, "status", "--json"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2.0, check=False)` (argv list, no shell).
- Validation rules:
  - Non-zero exit, timeout, `OSError`, invalid JSON, or `data.get("BackendState") != "Running"` returns `None`.
  - Finds the first address in `data.get("TailscaleIPs") or Self.get("TailscaleIPs")` that parses as an `ipaddress.IPv4Address` inside `100.64.0.0/10` (`_CGNAT = ipaddress.ip_network("100.64.0.0/10")`). If none exists, returns `None`.
  - Reads `Self.DNSName` (string), strips one trailing `.`, lowercases, and accepts it only if it matches `^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$` and ends with `.ts.net`; otherwise sets `dns_name = None` (covers tailnets with MagicDNS disabled).

### 3. CLI Startup, Dual-Socket Bind & Terminal QR (`groundly/cli/serve.py`)

- Add CLI option to both `serve` and `start`:
  ```python
  _TAILSCALE = Annotated[
      bool | None,
      typer.Option(
          "--tailscale/--no-tailscale",
          help="Also serve the page over Tailscale with QR device pairing (overrides web.tailscale).",
      ),
  ]
  ```
- Resolution flow before creating the app:
  1. `want_ts = load_settings().web.tailscale if tailscale is None else tailscale`.
  2. If `want_ts` is `False`: bind single socket `("127.0.0.1", port)` as today (`remote_runtime = None`).
  3. If `want_ts` is `True`: call `discover_tailscale()`:
     - If `None` and `tailscale is True` (explicit CLI flag): `_fail("Tailscale is not connected — start Tailscale or pass --no-tailscale")`.
     - If `None` and `tailscale is None` (`config.toml`): print `[yellow]Warning:[/yellow] Tailscale is not connected — serving on 127.0.0.1 only` and continue with `("127.0.0.1", port)` only.
     - If `TailscaleInfo` is returned: build `RemoteRuntime(ts_info, port=port, on_paired=_print_next_pair_line)` and pre-bind **both** sockets (`("127.0.0.1", port)` and `(ts_info.ipv4, port)`) with `SO_REUSEADDR` and `.listen()`, closing any already-opened socket in `try/except` if the second bind fails (`_fail(...)` naming the address and port).
- Pass `sockets=[...]` into `_PageServer(config).run(sockets=sockets)` (closing the Python socket wrappers in a `finally:` block after `run()` exits).
- Terminal output when Tailscale is active:
  - Prints standard `Page:` (and `MCP:` / `Connect Claude Code:` under `start`).
  - Prints `Tailscale: http://{ts.preferred_host}:{port}/  (paired devices)`
  - Prints `Pair URL:  http://{ts.preferred_host}:{port}/pair#{initial_token}` (`soft_wrap=True` so it never line-wraps mid-token) followed by `render_terminal_qr(pair_url)` using `segno.make(pair_url, error="l").terminal(compact=True, border=2)`.
  - When a device consumes the active token via `POST /api/pair`, `on_paired(label, peer_ip, next_url)` prints one concise line via `console.print`:
    `Paired {label} ({peer_ip}) — next pair link: {next_url}` (using `_safe()` on `label`).

### 4. Session Store, Pairing Coordinator & Socket Perimeter (`groundly/web/remote.py`)

#### 4.1 `RemoteStore` (`~/.groundly/remote.db`)
- File creation: ensures parent dir exists, opens file with `os.open(path, os.O_RDWR | os.O_CREAT, 0o600)` and `os.chmod(path, 0o600)` before `sqlite3.connect(path)` (`WAL`, `busy_timeout = 5000`, `foreign_keys = ON`). Refuses `PRAGMA user_version > 1`.
- Schema (`user_version = 1`):
  ```sql
  CREATE TABLE IF NOT EXISTS sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      token_hash BLOB NOT NULL UNIQUE,
      label TEXT NOT NULL,
      peer_ip TEXT NOT NULL,
      created_at TEXT NOT NULL,
      last_seen_at INTEGER NOT NULL,
      expires_at INTEGER NOT NULL
  );
  ```
- Operations:
  - `create_session(raw_token: str, user_agent: str, peer_ip: str, replace_raw_token: str | None = None) -> dict`: within one transaction, deletes expired rows (`expires_at <= now`), deletes `sha256(replace_raw_token)` if provided (so re-scanning on an already-paired phone replaces its row rather than duplicating it), inserts the new row with `expires_at = now + 30 * 86400`, and returns the device metadata.
  - `authenticate(raw_token: str) -> bool`: looks up `token_hash = sha256(raw_token.encode("utf-8")).digest()` with `expires_at > now`. If found and `now - last_seen_at >= 60`, updates `last_seen_at = now`. Returns `True`/`False`.
  - `list_devices() -> list[dict]`: prunes expired rows and returns active devices ordered by `last_seen_at DESC, id DESC` (`id`, `label`, `peer_ip`, `created_at`, `last_seen_at`).
  - `revoke_device(device_id: int) -> bool`: deletes `WHERE id = ?`, returns `rowcount > 0`.
  - `revoke_all() -> int`: deletes all rows, returns `rowcount`.
- `User-Agent` label derivation (`parse_device_label(ua: str) -> str`):
  - Deterministic, regex-free substring classification producing clean labels (`"iPad · Safari"`, `"iPhone · Safari"`, `"Android · Chrome"`, `"Android · Firefox"`, `"Mac · Safari"`, `"Device · Browser"`), stripped of control characters and capped at 64 characters — never storing raw arbitrary User-Agent strings directly as UI labels.

#### 4.2 `PairingManager` & QR Matrix
- Thread-safe (`threading.Lock`) coordinator holding:
  - `active_token: str = secrets.token_urlsafe(24)`
  - Sliding-window failure tracker per `peer_ip`: `dict[str, deque[float]]` allowing at most `MAX_PAIR_FAILURES = 5` failed attempts within `PAIR_WINDOW_S = 60.0` (`429 {"error": "rate_limited", "detail": "too many failed pairing attempts — wait a minute and try again"}`).
- `pairing_url`: `f"http://{ts.preferred_host}:{port}/pair#{active_token}"`.
- `qr_matrix(url: str) -> list[list[bool]]`: builds `qr = segno.make(url, error="l")` and converts `qr.matrix` rows to `[[bool(cell) for cell in row] for row in qr.matrix]`.
- `consume_and_rotate(candidate: str, peer_ip: str) -> str`:
  - Checks rate limit for `peer_ip` first.
  - Compares `candidate` against `active_token` with `hmac.compare_digest`. On mismatch (or empty string), records failure timestamp for `peer_ip` and raises `PairAuthError(401, "invalid_pairing_token", "pairing link is invalid or was already used — scan the current QR code")`.
  - On match, immediately replaces `active_token = secrets.token_urlsafe(24)` and returns the new `pairing_url`.

#### 4.3 `_SocketPerimeterMiddleware` (ASGI)
Wraps the FastAPI app (replacing global `TrustedHostMiddleware`) so every HTTP request is classified by `server_ip = (scope.get("server") or ("127.0.0.1", None))[0]`:

| Check / Surface | Loopback Socket (`127.0.0.1`) | Tailscale Socket (`ts.ipv4`) |
|---|---|---|
| Allowed `Host` (port-stripped, lowercased) | `{"127.0.0.1", "localhost", "groundly.localhost"}`; `400 PlainTextResponse("Invalid host header")` otherwise | `ts.allowed_hosts` (`{ts.ipv4}` + optional `ts.dns_name`); `400 PlainTextResponse("Invalid host header")` otherwise |
| `/mcp` and `/mcp/*` | Allowed (FastMCP's own Host/Origin guard also runs) | Immediate `404` `Response("not found", 404)` |
| `/api/remote` and `/api/remote/*` | Allowed | Immediate `404` `JSONResponse({"error": "not_found"}, 404)` |
| `POST /api/pair` | `404` `JSONResponse({"error": "not_available", "detail": "pairing is only used on Tailscale"}, 404)` | Allowed unauthenticated (guarded by `_require_page_json` + rate limit) |
| `GET /static/*` & `GET /api/auth` | Allowed | Allowed unauthenticated |
| `GET` HTML shell routes (`/`, `/pair`, `/s/{subject}`, `/s/{subject}/test`, `/s/{subject}/attempts/{id}`) | Routed normally | If unpaired: return `200` `_html()` directly from middleware (zero `_open_subject()` disk check); if paired: routed normally |
| All other routes (`/api/subjects*`, `/api/runs*`, `/s/{subject}/materials/{id}`, unsupported paths/methods) | Routed normally | Require valid `groundly_session` cookie in `RemoteStore`; otherwise `401` `JSONResponse({"error": "unpaired", "detail": "scan the pairing QR code on your computer"}, 401)` |

- Also sets `scope["state"]["access"] = "tailscale" | "loopback"` and `scope["state"]["paired"] = bool` so route handlers read already-validated request context without re-querying SQLite.
- In `_require_page_json` (`groundly/web/app.py`), normalize both `Origin` scheme+host and `f"{request.url.scheme}://{request.headers.get('host', '')}"` case-insensitively on the host portion (`origin.lower() == expected.lower()`) while keeping `proxy_headers=False`.

### 5. FastAPI Routes & OpenAPI Schemas (`groundly/web/schemas.py`, `groundly/web/app.py`)

New strict Pydantic v2 models in `groundly/web/schemas.py`:
- `AuthStatusResponse`: `access: Literal["loopback", "tailscale"]`, `paired: bool`, `tailscale_enabled: bool`, `paired_devices: int` (`0` on the Tailscale socket so device counts never leak off-box; active session count on loopback when `remote_db_path()` exists, else `0`).
- `PairDeviceRequest`: `model_config = ConfigDict(strict=True, extra="forbid")`, `token: str` (`min_length=16, max_length=128`).
- `PairDeviceResponse`: `paired: Literal[True]`.
- `PairedDevice`: `id: int`, `label: str`, `peer_ip: str`, `created_at: str`, `last_seen_at: int`.
- `RemoteInfoResponse`: `active: bool`, `host: str | None`, `pairing_url: str | None`, `qr_matrix: list[list[bool]] | None`, `devices: list[PairedDevice]`.
- `EmptyObjectRequest` (reusing `StopRunRequest`'s `{}` strict shape) for `POST /api/remote/devices/{id}/revoke` and `POST /api/remote/revoke-all`.
- `RevokeDeviceResponse`: `revoked: bool`.
- `RevokeAllResponse`: `revoked: int`.

New routes in `groundly/web/app.py` (`create_app(mcp_app=None, mcp_url=None, remote: RemoteRuntime | None = None)`):
- `GET /pair` (`include_in_schema=False`) → `_html()`.
- `GET /api/auth` (`operation_id="getAuthStatus"`, `response_model=AuthStatusResponse`).
- `POST /api/pair` (`operation_id="pairDevice"`, `response_model=PairDeviceResponse`, `responses=_error_responses(400, 401, 403, 404, 413, 415, 429)`, `_require_page_json`): consumes token, records session in `RemoteStore` (passing any existing `groundly_session` cookie to `replace_raw_token`), sets `groundly_session` cookie (`httponly=True, samesite="strict", path="/", max_age=30 * 86400`), triggers `on_paired`, returns `{"paired": True}`.
- `GET /api/remote` (`operation_id="getRemoteInfo"`, `response_model=RemoteInfoResponse`): returns active Tailscale pairing URL + `qr_matrix` (when `remote is not None`) plus `devices` from `RemoteStore` (returning `[]` without creating `remote.db` if `remote is None` and `remote_db_path()` does not yet exist).
- `POST /api/remote/devices/{id:int}/revoke` (`operation_id="revokeRemoteDevice"`, `response_model=RevokeDeviceResponse`, `responses=_error_responses(400, 403, 404, 413, 415)`, `_require_page_json`).
- `POST /api/remote/revoke-all` (`operation_id="revokeAllRemoteDevices"`, `response_model=RevokeAllResponse`, `responses=_error_responses(400, 403, 413, 415)`, `_require_page_json`): revokes all rows in `RemoteStore` and rotates `PairingManager` token if active.

### 6. Svelte 5 SPA Changes (`web/src/`)

```text
web/src/
  App.svelte                        # Auth bootstrap gate + RemoteDialog trigger in header + <Router />
  lib/
    auth.svelte.ts                  # Reactive auth store (bootstrap, pair, handleUnpaired)
    api/
      client.ts                     # Adds 401 "unpaired" onResponse hook -> authStore.handleUnpaired()
      queries.ts                    # Adds queryKeys.remote()
      schema.d.ts                   # Regenerated from web/openapi.json
    components/
      QrCode.svelte                 # Pure <svg><path d=.../></svg> renderer from boolean qr_matrix
      RemoteDialog.svelte           # Loopback header "Devices" popover (QR, copy URL, device list, revoke)
      SourceItem.svelte             # Adds inline expandable "Passage" toggle when src.pdf === true
    router.ts                       # Adds "/pair" route redirecting paired/loopback browsers to "/"
    run.svelte.ts                   # Adds visibilitychange + online auto-resync of SSE / run state
    views/
      PairView.svelte               # Tailscale pairing screen (auto-exchanges #token, scrubs hash, shows errors)
      TestView.svelte               # Plain-HTTP fallback token generator alongside crypto.randomUUID()
```

Key frontend invariants & flows:
1. **Auth bootstrap & `#fragment` pairing (`auth.svelte.ts`, `PairView.svelte`, `App.svelte`):**
   - On startup, before `<Router />` mounts (preventing premature `/api/subjects` or `/api/runs` 401s), `authStore.bootstrap()` checks:
     - If `window.location.pathname === "/pair"` and `window.location.hash.length > 1`, extract `rawToken = decodeURIComponent(window.location.hash.slice(1))` and **immediately** scrub the URL via `window.history.replaceState(window.history.state, "", "/")` so the one-time secret never stays in browser history or the URL bar.
     - Fetch `GET /api/auth`. If `access === "tailscale"` and `rawToken` was present, call `POST /api/pair` (`{ token: rawToken }`):
       - On `200`: mark `paired = true` and mount `<Router />` at `/`.
       - On `401`/`429`: mark `paired = false`, store the server's `detail` message, and render `PairView.svelte` showing why pairing failed and how to scan a fresh QR code.
     - If `access === "tailscale"` and `paired === false` (no token): render `PairView.svelte` with instructions to scan the QR code shown in the terminal or under **Devices** on the host computer.
2. **Global `401 unpaired` handling (`client.ts`):**
   - Register a lightweight `openapi-fetch` middleware `onResponse`: when `response.status === 401` on any `/api/*` request other than `/api/pair`, call `onUnpairedCallback?.()`, which closes `runStore`'s SSE stream, clears `queryClient`, and sets `authStore.paired = false` so a revoked phone/tablet immediately transitions to `PairView`.
3. **Desktop `Devices` Popover (`RemoteDialog.svelte` + `QrCode.svelte`):**
   - Shown in `App.svelte`'s header (`next to Theme`) whenever `authStore.access === "loopback" && (authStore.tailscaleEnabled || authStore.pairedDevices > 0)`.
   - Uses `createQuery` on `GET /api/remote` (`enabled: open`, `refetchInterval: open ? 3000 : false`). While open, scanning the QR on a phone causes `"iPhone · Safari"` to appear in the device list within ≤3 s and automatically updates `QrCode.svelte` to the freshly rotated token for pairing a tablet next.
   - `QrCode.svelte` builds one SVG `<path>` string (`M${x},${y}h1v1h-1z` per `true` module with a 2-module quiet zone) inside `<svg viewBox=... role="img" aria-label="Tailscale pairing QR code">` — zero `{@html}`, zero `data:` URIs, crisp at any DPR.
   - Includes per-device **Revoke** (`POST /api/remote/devices/{id}/revoke`) and **Revoke all** (`POST /api/remote/revoke-all`) mutations invalidating `queryKeys.remote()`.
4. **Mobile resilience (`TestView.svelte`, `run.svelte.ts`, `SourceItem.svelte`):**
   - **Token generation on plain HTTP (`test-session.ts` / `TestView.svelte`):** Add `generateAttemptToken()` returning `typeof crypto.randomUUID === "function" ? crypto.randomUUID() : randomHexToken()` (32 hex chars from `crypto.getRandomValues(new Uint8Array(16))`, matching `_TOKEN = re.compile(r"[A-Za-z0-9-]{8,64}")`). Preserves `crypto.randomUUID()` in `AUTHORED_SOURCE` and `SHIPPED_BUNDLE` while working in non-HTTPS contexts (`http://100.x.y.z:8765`) on iOS Safari and Android Chrome.
   - **Wake-up SSE resync (`run.svelte.ts`):** Attach `document.addEventListener("visibilitychange", ...)` (when `visibilityState === "visible"`) and `window.addEventListener("online", ...)` inside `createRunStore` (gated on browser globals and skipped when `authStore` is unpaired): resets `streamErrors = 0`, clears transient connection-lost `message` banners if a run was active, calls `void attach()`, and invalidates active queries so unlocking a phone mid-run or post-run catches up automatically.
   - **Inline PDF passage toggle (`SourceItem.svelte`):** When `src.pdf === true`, keep the existing `<a href={href} target="_blank" rel="noopener">` link and, when `src.text.trim().length > 0`, render a compact `Passage` toggle button (`aria-expanded`) beside it that reveals/hides the same `<pre>` text block used for non-PDF sources.
5. **Static bundle compliance:**
   - Author zero `"http://"`, `"https://"`, `"verified"`, `{@html`, or `innerHTML` tokens anywhere in `web/src/`. Confirm `segno` stays Python-only (`pyproject.toml`).

---

## Testing & CI Strategy

### Python Tests (`uv run pytest`)
- **`tests/core/test_config.py`:**
  - `web.tailscale` defaults to `False`, round-trips through `set_key("web.tailscale", "true")`, renders `[web]` in `config.toml`, and rejects non-booleans with `ConfigKeyError`.
- **`tests/web/test_tailscale.py`:**
  - Parses `tailscale status --json` with MagicDNS enabled (`dns_name` stripped of trailing dot), MagicDNS disabled (`dns_name=None`), non-`Running` `BackendState` (`None`), missing CGNAT IPv4 (`None`), malformed DNS names ignored, timeout / missing binary (`None`), and macOS `/Applications/Tailscale.app/...` fallback.
- **`tests/web/test_remote.py`:**
  - Dual-socket perimeter simulation via ASGI `scope["server"] = ("127.0.0.1", 8765)` vs `("100.101.102.103", 8765)`:
    - Loopback accepts `127.0.0.1` / `localhost` / `groundly.localhost`, rejects `100.101.102.103`, serves `/api/subjects`, `/mcp`, and `/api/remote` without cookies, and returns `404` on `POST /api/pair`.
    - Tailscale accepts `100.101.102.103:8765` and `macbook.tail1234.ts.net:8765` (including mixed-case `Origin`/`Host`), rejects `localhost` (`400`), returns `404` on `/mcp` and `/api/remote*` **even with a valid session cookie**, returns `200` HTML on `/s/unknown` and `/s/known` identically when unpaired (no subject-existence oracle), and returns `401 {"error": "unpaired"}` on `/api/subjects`, `/api/runs`, `/api/runs/stream`, and `/s/{subject}/materials/{id}` when unpaired.
  - Pairing & multi-device sessions:
    - Phone pairs with token `T1` → gets `HttpOnly; SameSite=Strict` cookie; replaying `T1` immediately fails with `401 invalid_pairing_token`; `GET /api/remote` on loopback now shows `"iPhone · Safari"` and rotated token `T2`; tablet pairs with `T2` → both phone and tablet access `/api/subjects`, submit attempts, and subscribe concurrently to `GET /api/runs/stream`.
    - Re-pairing from the already-paired phone supersedes its old session row rather than creating duplicates.
    - Revoking the tablet (`POST /api/remote/devices/{tablet_id}/revoke`) immediately makes the tablet get `401 unpaired` while the phone stays authenticated (`200`).
    - `POST /api/remote/revoke-all` invalidates both devices and rotates the active pairing token.
    - 6th bad `POST /api/pair` attempt within 60 s from the same peer IP returns `429 rate_limited`.
    - `~/.groundly/remote.db` is created with `0600` mode (`stat.S_IMODE(os.stat(path).st_mode) == 0o600`), stores `sha256` digests rather than raw cookie strings, and is untouched by `groundly export`.
- **`tests/cli/test_cli_serve.py`:**
  - `--tailscale` binds both `127.0.0.1` and `100.x.y.z`, prints pairing URL + QR, and opens only `groundly.localhost` in the host browser.
  - `--tailscale` fails with exit code 1 when `discover_tailscale()` returns `None`.
  - `web.tailscale = true` in `config.toml` warns and falls back to `127.0.0.1` when `discover_tailscale()` returns `None`, and `--no-tailscale` overrides `web.tailscale = true`.
  - All existing `AUTHORED_SOURCE` and `SHIPPED_BUNDLE` checks continue passing.
- **`tests/web/test_openapi.py`:**
  - Verifies `web/openapi.json` matches `create_app().openapi()` with the new `/api/auth`, `/api/pair`, and `/api/remote*` operations.

### Frontend Tests (`pnpm test` / `vp test run`)
- **`web/src/lib/__tests__/auth.test.ts`:**
  - Loopback bootstrap mounts app immediately; Tailscale unpaired renders `PairView`; `/pair#<token>` strips `location.hash` before/during `POST /api/pair` and transitions to paired; burned token renders error message in `PairView`; `401 unpaired` response drops active session back to `PairView` and closes SSE.
- **`web/src/lib/components/__tests__/remote.test.ts`:**
  - `QrCode.svelte` renders `<svg>` `<path>` for `qr_matrix`; `RemoteDialog.svelte` renders paired devices and triggers per-device / revoke-all mutations.
- **`web/src/lib/components/__tests__/source-item.test.ts`:**
  - `SourceItem.svelte` renders PDF link + `Passage` toggle when `src.pdf === true`, expanding/collapsing `src.text` as plain text.
- **`web/src/lib/__tests__/test-session.test.ts` & `run.test.ts`:**
  - `generateAttemptToken()` falls back cleanly to `crypto.getRandomValues` when `crypto.randomUUID` is `undefined`.
  - `visibilitychange` (`visible`) and `online` reset `streamErrors` and trigger `runStore.attach()` resync.

---

## Documentation Updates

In the same implementation change set (per `.claude/rules/conventions.md`):
- **`docs/groundly-spec.md`:** Record Decision 52 (amending Decisions 41, 44, 45, 50, 51) covering opt-in Tailscale dual-socket binding, socket-scoped loopback `/mcp` isolation, `~/.groundly/remote.db` per-device sessions, single-use `#fragment` QR pairing, and mobile SPA resilience.
- **`docs/infrastructure/security.md`:** Update §4 (*Local servers*) and §5 (*Privacy*) to document the opt-in Tailscale interface perimeter, why `0.0.0.0` and `proxy_headers` remain forbidden, per-device `sha256` session cookies in `remote.db` (`0600`, never exported), single-use QR rate-limiting, pre-auth no-oracle HTML shell, and `/mcp` staying loopback-only.
- **`docs/tech-stack/tech-stack.md` & `.claude/rules/architecture.md`:** Add `segno` (pure-Python QR matrix/terminal encoder) and update the storage/concurrency bullet for opt-in Tailscale socket isolation + `remote.db`.
