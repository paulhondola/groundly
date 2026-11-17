# `groundly start` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One verb, `groundly start`, serves the page at `http://groundly.localhost:8765/` and the MCP tools at `/mcp` from one loopback process.

**Architecture:** The CLI composes: `start` builds FastMCP's Streamable HTTP app (`mcp.http_app(path="/mcp")`) and hands it to the page's `create_app`, which mounts it after the page routes (`Mount("", app=mcp_app)`) and adopts its lifespan. `serve` and `start` share one run helper (port probe, printed URLs, browser, uvicorn). Verified in a spike: page 200, `/mcp` initialize 200 on `groundly.localhost`, foreign Host 400, foreign Origin 403.

**Tech Stack:** Starlette, uvicorn, FastMCP 4.1.6, typer, pytest + `starlette.testclient`.

**Spec:** `docs/superpowers/specs/2026-11-17-groundly-start-design.md`

**Branch:** a new feature branch (`groundly-start`), never `main`.

## Global Constraints

- Bind `127.0.0.1` only; no `--host` flag.
- Accepted Host names, exactly: `127.0.0.1`, `localhost`, `groundly.localhost`. No wildcard, no user-chosen name.
- `start` default port **8765**; `--port` limited to 1–65535 like `serve`.
- FastMCP host/origin protection stays on (`host_origin_protection="auto"`).
- `serve` and `mcp --http` keep their behavior (only `serve`'s printed/opened URL changes).
- The page's routes load no model and write no rows to `store.db`; no new MCP tool (`test_the_tool_surface_is_exactly_these_ten_entries` unchanged).
- `uvicorn.run(..., proxy_headers=False)` stays.

## Review Focus

1. A machine whose resolver does not map `*.localhost` (some Linux setups) — the student still gets a working URL: the output also prints `http://127.0.0.1:<port>/`. Test in Task 2.
2. Submitting a test from the page opened at `groundly.localhost` — the same-Origin check passes for `http://groundly.localhost:8765`. Test in Task 1.
3. `/mcp` before the lifespan runs — the session manager must be started by the parent app; the initialize test uses a context-managed `TestClient`. Test in Task 1.
4. `/mcp` responses must not carry the page's CSP, and page responses must keep it. Test in Task 1.
5. A busy port under `start` gets the same plain message as `serve`, not a traceback. Test in Task 2.

---

### Task 1: Page app accepts `groundly.localhost` and can mount the MCP app

**Files:**
- Modify: `groundly/web/app.py` (`create_app`, new `HOSTS`)
- Test: `tests/web/test_app.py`

**Interfaces:**
- Produces: `HOSTS: list[str] = ["127.0.0.1", "localhost", "groundly.localhost"]` in `groundly.web.app`; `create_app(mcp_app: Starlette | None = None) -> Starlette`. With `mcp_app`, routes end with `Mount("", app=mcp_app)` and `lifespan=mcp_app.lifespan`; `TrustedHostMiddleware(allowed_hosts=HOSTS)` either way.

- [ ] **Step 1: Write the failing tests** in `tests/web/test_app.py`

```python
def test_groundly_localhost_host_header_is_accepted(client, seeded):
    r = client.get("/api/subjects", headers={"Host": "groundly.localhost:8765"})
    assert r.status_code == 200

def test_submit_accepts_the_groundly_localhost_origin(client, seeded):
    # same draw + body as test_submit_grades_and_the_result_shows_the_key_and_source
    # (tests/web/test_app.py:325), sent with these headers instead of ORIGIN
    h = {"Host": "groundly.localhost:8765", "Origin": "http://groundly.localhost:8765"}
    # assert the POST returns 201

@pytest.fixture
def start_client(seeded):
    from groundly.mcp.server import mcp
    from groundly.web.app import HOSTS
    mcp_app = mcp.http_app(path="/mcp", host_origin_protection="auto", allowed_hosts=HOSTS)
    with TestClient(create_app(mcp_app), base_url="http://groundly.localhost:8765") as c:
        yield c

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "t", "version": "1"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}

def test_start_app_serves_page_and_mcp(start_client):
    page = start_client.get("/")
    assert page.status_code == 200 and "content-security-policy" in page.headers
    r = start_client.post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers

def test_start_app_mcp_refuses_foreign_host_and_origin(start_client):
    assert start_client.post("/mcp", json=INIT,
        headers={**MCP_HEADERS, "Host": "evil.com"}).status_code == 400
    assert start_client.post("/mcp", json=INIT,
        headers={**MCP_HEADERS, "Origin": "http://evil.com"}).status_code == 403
```

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/web/test_app.py -k "groundly_localhost or start_app" -v`
Expected: FAIL (`groundly.localhost` rejected with 400; `create_app` takes no argument).

- [ ] **Step 3: Implement** `HOSTS` and `create_app(mcp_app=None)` in `groundly/web/app.py` per the Interfaces block. Update the module docstring: the page itself loads no model; under `start` the same process also serves `/mcp`.

- [ ] **Step 4: Run the web tests**
Run: `uv run pytest tests/web -v`
Expected: all PASS.

- [ ] **Step 5: Commit**
```bash
git add groundly/web/app.py tests/web/test_app.py
git commit -m "feat(web): accept groundly.localhost and mount an MCP app"
```

### Task 2: `groundly start` verb; `serve` opens the new name

**Files:**
- Modify: `groundly/cli/serve.py`
- Test: `tests/cli/test_cli_serve.py`

**Interfaces:**
- Consumes: `HOSTS`, `create_app(mcp_app)` from Task 1; `groundly.mcp.server.mcp`.
- Produces: `_run(asgi_app, port: int, lines: list[str]) -> None` (probe port, print `lines`, open `http://groundly.localhost:{port}/`, `uvicorn.run(..., host="127.0.0.1", proxy_headers=False, log_level="warning")`); verb `start(port: int = 8765)`.

- [ ] **Step 1: Write the failing tests** (reuse `_patch`)

```python
def test_start_binds_loopback_opens_page_and_prints_both_urls(monkeypatch):
    runs, opened = _patch(monkeypatch)
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0, result.output
    assert [(r["host"], r["port"]) for r in runs] == [("127.0.0.1", 8765)]
    assert runs[0]["proxy_headers"] is False
    assert opened == ["http://groundly.localhost:8765/"]
    assert "http://groundly.localhost:8765/mcp" in result.output
    assert "http://127.0.0.1:8765/" in result.output  # fallback when *.localhost won't resolve

def test_start_names_a_busy_port_and_the_flag(monkeypatch):
    ...  # same as test_serve_names_a_busy_port_and_the_flag with ["start", "--port", ...]
```
Update the existing `serve` tests: `opened == ["http://groundly.localhost:<port>/"]`, and output still contains `http://127.0.0.1:<port>/`.

- [ ] **Step 2: Run to verify they fail**
Run: `uv run pytest tests/cli/test_cli_serve.py -v`
Expected: FAIL (`No such command 'start'`; old URL opened).

- [ ] **Step 3: Implement** `_run` (moved out of `serve`'s body) and `start` in `groundly/cli/serve.py`. `start` builds `mcp.http_app(path="/mcp", host_origin_protection="auto", allowed_hosts=HOSTS)` with lazy imports inside the verb, as `serve` does. Printed lines for `start`:
```
Page:  http://groundly.localhost:{port}/   (or http://127.0.0.1:{port}/)
MCP:   http://groundly.localhost:{port}/mcp
Connect Claude Code: claude mcp add --transport http groundly http://groundly.localhost:{port}/mcp
```
`serve` prints its page line only. Update the module docstring.

- [ ] **Step 4: Run the full checks**
Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run lint-imports`
Expected: all green; tool-surface test unchanged.

- [ ] **Step 5: Smoke run**
Run `uv run groundly start --port 8799` in the background; `curl -s -o /dev/null -w '%{http_code}' http://groundly.localhost:8799/` → `200`; `curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.com' http://127.0.0.1:8799/` → `400`; stop it.

- [ ] **Step 6: Commit**
```bash
git add groundly/cli/serve.py tests/cli/test_cli_serve.py
git commit -m "feat(cli): groundly start serves the page and MCP on one address"
```

### Task 3: Record decision 45 in the docs

**Files (via `/decision`):** `docs/groundly-spec.md` (§4 interface row, §7 decision 45), `.claude/rules/conventions.md` (CLI verb list gains `start`), `.claude/rules/architecture.md` (`start` binds 127.0.0.1 only), `docs/infrastructure/security.md` (one process, both surfaces; `groundly.localhost` in the Host list), `docs/architecture/overview.md` (process table row), `docs/guides/mcp-hosts.md` (connect line), `docs/infrastructure/distribution.md` (multi-host line points at `start`), `README.md` if it names the verbs.

- [ ] **Step 1:** Run `/decision` with: "Decision 45 — `groundly start` runs the page and MCP over HTTP in one loopback process on 8765 (`/` and `/mcp`), reachable as `groundly.localhost`; one model load across sessions; `serve` and `mcp --http` unchanged; no port-free or `.local` name (root/proxy; Bonjour). Amends decision 40's 'two processes, accepted'."
- [ ] **Step 2:** Run `graphify update .`.
- [ ] **Step 3: Commit**
```bash
git add docs .claude/rules README.md graphify-out
git commit -m "docs: decision 45, groundly start"
```
