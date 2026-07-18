# Adversarial review: `groundly serve` (MCP over Streamable HTTP) (2026-07-18)

Verdict: MERGE WITH FIXES

Scope reviewed: `groundly/cli/serve.py`, `groundly/cli/__init__.py`,
`tests/test_mcp_server.py` (HTTP smoke test), `docs/guides/mcp-hosts.md`,
`docs/superpowers/specs/2026-07-18-mcp-skeleton-design.md`. fastmcp 3.4.4 in
`.venv` verified directly. 22/22 tests in `tests/test_mcp_server.py` pass;
ruff clean; lazy-import guard test still passes.

## Findings

### F1 — DNS-rebinding / cross-origin protection is left OFF, so "loopback = safe" no longer holds [severity: medium]
- Where: `groundly/cli/serve.py:17` — `mcp_server.run(transport="http", host="127.0.0.1", port=port)` passes no `host_origin_protection`.
- Failure scenario: fastmcp 3.4.4's `settings.http_host_origin_protection`
  defaults to **False** (verified: `settings.py:323`). `run_http_async`
  resolves the value straight from that setting, so the served app validates
  neither `Host` nor `Origin`. A student running `groundly serve` who then
  visits an attacker page can be hit by a classic local-MCP DNS-rebinding
  attack: attacker serves the initial page on port 8000, rebinds its hostname
  to 127.0.0.1, then same-origin JS `POST`s to `http://<attacker-host>:8000/mcp`
  (same scheme+host+port ⇒ no CORS preflight) and reads the response — full
  course-material exfiltration via `search`/`get_page`, plus provider token
  spend via `ask`.
- Evidence (ran): against `mcp.http_app()` with the default settings the code
  uses, a `POST /mcp` carrying `Host: evil.example.com` + `Origin: http://evil.example.com`
  returns **200** with a valid MCP `initialize` result. The same request against
  `mcp.http_app(host_origin_protection="auto")` returns **421 Misdirected
  Request**. FastMCP ships `"auto"` specifically to "protect localhost-bound
  servers"; the code opts out by omission.
- Why it matters here: `docs/infrastructure/security.md §4` and
  `.claude/rules/architecture.md` treat loopback binding as *the* security
  control ("no-auth is acceptable exactly and only on loopback"). DNS rebinding
  is the standard hole in that exact model, and the one-line fix
  (`host_origin_protection="auto"`, or an explicit `allowed_hosts=["127.0.0.1", "localhost"]`)
  restores the assumption the invariant relies on. Binding to 127.0.0.1 alone
  does not deliver what the invariant promises.

### F2 — The smoke test never exercises `serve.py` [severity: low]
- Where: `tests/test_mcp_server.py:315` `test_http_transport_serves_the_same_tools`.
- Failure scenario: the test re-implements the uvicorn wiring by hand
  (`uvicorn.Config(mcp.http_app(), ...)`) and never calls `serve()` nor
  `mcp_server.run(transport="http", ...)`. So the actual production line —
  the `run()` call with its transport string and kwargs — has zero coverage.
  A typo like `transport="htttp"` or a wrong kwarg name would keep all 22
  tests green. The test proves the FastMCP *instance* can serve HTTP, not that
  the `serve` command does. It also means the test's app is built with the same
  default (protection off) as production but doesn't assert anything about it,
  so F1 slipped through a "green" suite.
- Evidence (read): the test body constructs its own server; `serve.py` is
  imported by nothing in the test module.
- Note: mitigating factor — `serve.py` is 3 lines and structurally identical to
  the already-tested `cli/mcp.py`. The test's readiness handling is otherwise
  sound: ephemeral `port=0` (no port race), polls `server.started` (no
  sleep-and-hope), `daemon=True` + `join(timeout=5)` (no session-leaking thread).

### F3 — Docs recommend the trailing-slash URL, which 307-redirects on every request [severity: low]
- Where: `docs/guides/mcp-hosts.md` — all snippets use `http://127.0.0.1:8000/mcp/`.
- Failure scenario: the app mounts the route at `/mcp` (verified:
  `app.state.path == "/mcp"`). `POST /mcp` → 200; `POST /mcp/` → **307**
  redirect to `/mcp`. fastmcp's own `Client` and httpx follow the 307 and
  preserve method+body (the test passes), so this is not broken — but every
  request pays a redirect round-trip, and any host client that does not
  re-send the body on 307 will fail to initialize. The canonical, non-
  redirecting URL is `/mcp` (no trailing slash); the docs advertise the
  redirecting form.
- Evidence (ran): `POST /mcp` → 200, `POST /mcp/` → 307 against the served app.

## What I tried and could not break
- fastmcp 3.4.4 accepts `run(transport="http", host=..., port=...)` exactly as coded — kwargs flow through `run_async` → `run_http_async`; no signature mismatch.
- SQLite concurrency under multiple HTTP clients: every connection path (`store.connect`, `connect_progress`, `SQLiteSubjectStore.connect`) sets `journal_mode=WAL` + `busy_timeout=5000`; MCP tools are read-only, so no writer contention. Fine.
- Lazy loading: `serve.py` top-level imports are only `typer` + `groundly.cli.app`; service imports stay inside the tool bodies; `test_importing_server_never_pulls_in_heavy_ml_deps` passes.
- No `--host` flag is exposed, so the "refuse non-loopback host" invariant can't be violated via CLI (the gap is F1, not a bad host value).
- Test thread/socket lifecycle: ephemeral port + `server.started` poll + daemon thread + bounded join — no port race, no readiness race, no leaked listener across the session.

Verdict: MERGE WITH FIXES
Review file: /Users/paulhondola/Developer/groundly/docs/superpowers/reviews/2026-07-18-serve-http-review.md
