# stdio forwarding to `groundly start` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdio `groundly mcp` sends `search`, `submit_tree` and `submit_questions` to a running `groundly start` (found through a `start.json` runfile) and serves them locally when there is none.

**Architecture:** `start` announces itself in `groundly_home()/start.json` (pid, port, version). A new `ForwardMiddleware` in the stdio server reads it per call, connects with a FastMCP client to `http://127.0.0.1:<port>/mcp`, and returns the remote result; any failure before the call is sent falls back to the local tool, any failure after it is a tool error. `start`'s `ActivityMiddleware` skips requests marked `X-Groundly-Forwarded`, so the stdio server's card is the only one.

**Tech Stack:** Python 3.11+, FastMCP 4.1.6 (`fastmcp.Client`, `StreamableHttpTransport`, `Middleware`, `ToolResult`), uvicorn (tests only, in a thread), pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-stdio-forwarding-design.md`

## Global Constraints

- Forwarded tools exactly: `search`, `submit_tree`, `submit_questions`. Every other tool and every resource stays local.
- Runfile: `groundly_home() / "start.json"`, mode `0600`, JSON `{"pid": int, "port": int, "version": str}`; removed on exit only if it still names this pid.
- Liveness for the runfile: `os.kill(pid, 0)` succeeds; `PermissionError` and `ProcessLookupError` both mean not alive.
- Forward only when the runfile version and the server's `serverInfo` (`name == "groundly"`, `version`) equal this Groundly's `importlib.metadata.version("groundly")`.
- Forward header: `X-Groundly-Forwarded: 1` (lower-case constant `x-groundly-forwarded` for reading). Display only.
- No client-side read timeout on the forwarded call.
- A failure after the call was sent returns the tool error text `groundly start stopped during the call — retry it`; never a local retry.
- Windows (`os.name == "nt"`): `start` writes no runfile; the stdio server never forwards.
- `groundly mcp --http` registers no `ForwardMiddleware`.
- MCP tool surface, names, arguments, results and descriptions unchanged (`test_the_tool_surface_is_exactly_these_eleven_entries` stays green).
- Branch: commit the current `fix/activity-host-identity` work first, then cut `feat/stdio-forwarding` from it. Never commit to `main`.
- Gate: `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`, `uv run pyrefly check`, `uv run lint-imports`; then `graphify update .`.

## Review Focus

1. `start` killed with SIGKILL leaves `start.json`; the next stdio call must see a dead pid and run locally, with no connection attempt (Task 1 test `test_read_start_ignores_a_dead_pid`, Task 2 test `test_stale_runfile_runs_locally_without_connecting`).
2. A `start` from an older or newer install on the same port must not receive calls (Task 2 test `test_version_mismatch_runs_locally`).
3. A remote tool error (unknown subject, gate rejection raised as `ToolError`) must reach the host as an error with its message, not trigger a local retry (Task 2 test `test_remote_tool_error_is_returned_not_retried`).
4. `start` stopping mid-call must not double-write (Task 2 test `test_failure_after_send_is_an_error_not_a_local_retry`).
5. Two `start`s on different ports: the first one exiting must not delete the second's runfile (Task 1 test `test_remove_start_keeps_another_pids_runfile`).

---

### Task 0: Commit the base and branch

**Files:** none new.

- [ ] **Step 0:** Only with Paul's go-ahead to commit that branch's work.
- [ ] **Step 1:** Run the gate on `fix/activity-host-identity`; expected all green.
- [ ] **Step 2:** Commit the working tree there (activity host identity, server version, question-gate race fix; may be split into those three commits). Leave `docs/design/Groundly Course Study Guide Icon Board.png` untracked unless Paul says otherwise.
- [ ] **Step 3:** `git switch -c feat/stdio-forwarding`; commit the spec and this plan.

### Task 1: The runfile

**Files:**
- Create: `groundly/core/startfile.py`
- Modify: `groundly/cli/serve.py` (`start`, `_run`)
- Test: `tests/core/test_startfile.py`, `tests/cli/test_cli_serve.py`

**Interfaces:**
- Produces:
  - `startfile.path() -> Path` — `groundly_home() / "start.json"`.
  - `startfile.write(port: int) -> None` — writes `{"pid": os.getpid(), "port": port, "version": <package version>}` atomically (temp file + `os.replace`), mode `0600`. No-op on Windows.
  - `startfile.remove() -> None` — deletes the file only when it names `os.getpid()`; never raises.
  - `startfile.read() -> StartInfo | None` — `StartInfo` is a frozen dataclass `(pid: int, port: int, version: str)`. Returns None when: Windows, file missing, unreadable, not a JSON object with those three keys of those types, port outside 1–65535, or pid not alive.
  - `startfile.pid_alive(pid: int) -> bool` — strict rule from Global Constraints.

- [ ] **Step 1: Write the failing tests** in `tests/core/test_startfile.py` (each sets `GROUNDLY_HOME` to `tmp_path`):
  - `test_write_then_read_round_trips`: `write(8765)`; `read() == StartInfo(os.getpid(), 8765, version("groundly"))`; `stat(path()).st_mode & 0o777 == 0o600`.
  - `test_read_start_ignores_a_dead_pid`: write JSON with a pid from a reaped `subprocess.Popen(["true"])`; `read() is None`.
  - `test_pid_owned_by_another_account_is_not_alive`: monkeypatch `os.kill` to raise `PermissionError`; `pid_alive(1) is False`.
  - `test_read_rejects_malformed_files`: parametrize over `"not json"`, `"[]"`, `{"pid": "1", ...}`, `{"pid": os.getpid(), "port": 0, "version": "x"}`, missing `version`; each `read() is None`.
  - `test_remove_start_keeps_another_pids_runfile`: write a file naming a different live pid (`os.getppid()`); `remove()`; file still exists. Then `write(1)`; `remove()`; file gone.
  - `test_windows_writes_and_reads_nothing`: monkeypatch `startfile._WINDOWS = True`; `write(8765)` creates nothing; a hand-written valid file reads as None.
- [ ] **Step 2:** Run `uv run pytest tests/core/test_startfile.py -v`; expected FAIL (module missing).
- [ ] **Step 3:** Implement `groundly/core/startfile.py` per Interfaces. Read the version once with `importlib.metadata.version("groundly")`.
- [ ] **Step 4:** Run the same command; expected PASS.
- [ ] **Step 5: Write the failing CLI test** in `tests/cli/test_cli_serve.py`, reusing the file's `fake_run` pattern: `test_start_announces_itself_while_serving_and_cleans_up` — inside `fake_run`, assert `startfile.read()` names `os.getpid()` and the port passed (`--port 9123`); after the command returns, `startfile.path()` does not exist. Plus `test_serve_writes_no_runfile`: `groundly serve` leaves no `start.json` during or after `fake_run`.
- [ ] **Step 6:** Run `uv run pytest tests/cli/test_cli_serve.py -v`; expected the two new tests FAIL.
- [ ] **Step 7:** In `serve.py`, give `_run` a keyword `announce: bool = False`; when true, call `startfile.write(port)` after the sockets bind and `startfile.remove()` in the existing `finally`. `start` passes `announce=True`; `serve` does not. Register `atexit.register(startfile.remove)` too, for exits that skip the `finally`.
- [ ] **Step 8:** Run `uv run pytest tests/cli/test_cli_serve.py tests/core/test_startfile.py -v`; expected PASS.
- [ ] **Step 9:** Commit: `feat(start): announce a running start in start.json`.

### Task 2: `ForwardMiddleware`

**Files:**
- Create: `groundly/mcp/forward.py`
- Modify: `groundly/cli/mcp.py`
- Test: `tests/mcp/test_forward.py`, `tests/cli/test_cli_mcp.py`

**Interfaces:**
- Consumes: `startfile.read() -> StartInfo | None` (Task 1).
- Produces:
  - `FORWARDED_TOOLS = frozenset({"search", "submit_tree", "submit_questions"})`
  - `FORWARD_HEADER = "x-groundly-forwarded"`
  - `STOPPED_MESSAGE = "groundly start stopped during the call — retry it"`
  - `class ForwardMiddleware(Middleware)` with `async def on_call_tool(self, context, call_next)`.

Behaviour of `on_call_tool` (the algorithm, since tests can't pin the boundary alone):

```
if name not in FORWARDED_TOOLS: return await call_next(context)
info = startfile.read()
if info is None or info.version != VERSION: return await call_next(context)
client = Client(StreamableHttpTransport(f"http://127.0.0.1:{info.port}/mcp",
                headers={"X-Groundly-Forwarded": "1"}), timeout=None)
try:
    await client.__aenter__()                       # connect + discover
except Exception: return await call_next(context)   # nothing was sent: local
try:
    si = client.server_info
    if si is None or si.name != "groundly" or si.version != VERSION:
        return await call_next(context)
    try:
        r = await client.call_tool_mcp(name, arguments)
    except Exception as exc:
        raise ToolError(STOPPED_MESSAGE) from exc
    return ToolResult(content=r.content, structured_content=r.structuredContent, is_error=r.isError)
finally:
    close the client, swallowing errors
```

The builder checks whether `call_tool_mcp(timeout=None)` plus `Client(timeout=None)` leaves any read timeout in the transport's httpx client; if one remains, pass an `httpx_client_factory` that sets `timeout=None` for reads.

Test harness for this task: a fake `start` is a fresh `FastMCP("groundly", version=VERSION)` with stub tools `search(subject, query, k=None)` returning `[{"from": "start"}]`, `submit_tree(subject, tree)` returning `{"accepted": True, "from": "start"}` and `submit_questions(...)` raising `ToolError("unknown subject 'x'")`, served by uvicorn from `http_app(path="/mcp")` on `127.0.0.1:0` in a daemon thread (fixture yields the port and stops the server). The local side is another `FastMCP("groundly")` with the same stub names returning `"from": "local"`, plus `ForwardMiddleware()`, driven by an in-memory `fastmcp.Client(local_server)`. Tests write `start.json` with `startfile.write(port)` (current pid, so alive).

- [ ] **Step 1: Write the failing tests** in `tests/mcp/test_forward.py`:
  - `test_model_tools_go_to_start_when_it_runs`: `search` result data `== [{"from": "start"}]`; `submit_tree` structured content has `"from": "start"`.
  - `test_other_tools_stay_local`: a stub `outline` on both servers; result says `"local"` even with a live runfile.
  - `test_no_runfile_runs_locally`: no `start.json`; `search` says `"local"`.
  - `test_stale_runfile_runs_locally_without_connecting`: runfile naming a reaped pid and a port nothing listens on; `search` says `"local"`; monkeypatch `Client.__aenter__` to fail the test if called.
  - `test_version_mismatch_runs_locally`: fake start built with `version="0.0.0"`, runfile carrying the real version; `search` says `"local"`. And a runfile carrying `"0.0.0"` against a correct server; `"local"`.
  - `test_refused_connection_runs_locally`: runfile with a live pid and a closed port; `"local"`.
  - `test_remote_tool_error_is_returned_not_retried`: `submit_questions` via `call_tool(..., raise_on_error=False)` gives `is_error` true, text containing `unknown subject 'x'`; the local stub's call counter stays 0.
  - `test_failure_after_send_is_an_error_not_a_local_retry`: monkeypatch `Client.call_tool_mcp` to raise `httpx.ReadError`; result `is_error`, text `== STOPPED_MESSAGE`; local counter 0.
  - `test_forward_marks_its_requests`: the fake start records `get_http_request().headers.get(FORWARD_HEADER)` in its `search` stub; it equals `"1"`.
  - `test_windows_never_forwards`: monkeypatch `startfile._WINDOWS = True`; `"local"`.
- [ ] **Step 2:** Run `uv run pytest tests/mcp/test_forward.py -v`; expected FAIL (module missing).
- [ ] **Step 3:** Implement `groundly/mcp/forward.py` per the algorithm above. Module docstring states the invariant: nothing is retried locally once sent.
- [ ] **Step 4:** Run the same command; expected PASS.
- [ ] **Step 5: Write the failing CLI tests** in `tests/cli/test_cli_mcp.py`: `test_stdio_mcp_forwards_model_tools` — after `groundly mcp` (stdio, `FastMCP.run` patched), `mcp.middleware` holds an `ActivityMiddleware` followed by a `ForwardMiddleware` (order matters: activity outside). `test_http_mcp_does_not_forward` — with `--http`, no `ForwardMiddleware`.
- [ ] **Step 6:** Run them; expected FAIL.
- [ ] **Step 7:** In `groundly/cli/mcp.py`, after the `ActivityMiddleware` line, `if not http: mcp_server.add_middleware(ForwardMiddleware())`.
- [ ] **Step 8:** Run `uv run pytest tests/cli/test_cli_mcp.py tests/mcp -v`; expected PASS.
- [ ] **Step 9:** Commit: `feat(mcp): stdio forwards model tools to a running start`.

### Task 3: Activity — one card per forwarded call

**Files:**
- Modify: `groundly/mcp/activity.py`
- Test: `tests/mcp/test_mcp_server.py`

**Interfaces:**
- Consumes: `FORWARD_HEADER` from `groundly.mcp.forward` (Task 2).

- [ ] **Step 1: Write the failing test** `test_activity_skips_forwarded_requests` in `tests/mcp/test_mcp_server.py`, using the existing `_http_tool_call(headers)` helper: with `ActivityMiddleware("mcp_http")` registered, a `list_subjects` call (no model needed) carrying `{"X-Groundly-Forwarded": "1"}` writes no row to `activity.db`; the same call without the header writes `tool_call` and `tool_end`.
- [ ] **Step 2:** Run it; expected FAIL.
- [ ] **Step 3:** In `ActivityMiddleware`, add `_forwarded(context) -> bool` (HTTP source, request present, header present). Each `on_*` hook returns `await call_next(context)` without logging when it is true. A comment: display only, any local client can send it.
- [ ] **Step 4:** Run `uv run pytest tests/mcp -v`; expected PASS.
- [ ] **Step 5: Docs, via `/decision`** (decision 54, amending 45), in the same commit:
  - `docs/groundly-spec.md`: decision 54 entry (design link, the forwarded three, runfile, fallback, no local retry after send, Windows never forwards); interface row mentions it.
  - `docs/architecture/overview.md`: stdio forwards model tools to a running `start`.
  - `docs/architecture/data-model.md`: `start.json` in the layout, never exported.
  - `docs/infrastructure/security.md`: replace "stdio MCP has no network surface at all" with the outbound loopback forward to the port the student's own `start` announced in a `0600` runfile; `X-Groundly-Forwarded` display only; the stale-runfile residual case; Windows never forwards.
  - `.claude/rules/architecture.md`: storage and concurrency section names the forward and the runfile.
- [ ] **Step 6:** Run the full gate (Global Constraints); expected all green. Run `graphify update .`.
- [ ] **Step 7:** Commit: `feat(activity): forwarded calls show once; decision 54`.

### Task 4: End-to-end check by hand

- [ ] **Step 1:** `groundly start` in one terminal; `cat ~/.groundly/start.json` shows its pid and 8765.
- [ ] **Step 2:** In a Claude Code session on stdio, run a `search` on `bia`. Expected: the page shows one card (stdio) for the call; `ps` shows the stdio `groundly mcp` without growing to ~2 GB, and `start` holding the model.
- [ ] **Step 3:** Stop `start`; the runfile is gone; the next `search` answers locally (the stdio process loads the model).
- [ ] **Step 4:** `kill -9` a running `start`; the next `search` answers locally with no error.
- [ ] **Step 5:** Ask `spec-guardian` and `security-reviewer` to review the branch diff; address findings.
