# FastAPI Web API, Svelte 5 UI Overhaul & Live Token-Level SSE Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `groundly/web/app.py` to FastAPI with strict Pydantic v2 contracts, upgrade `groundly/web/runs.py` to `--include-partial-messages` live token-level async pub-sub SSE (`GET /api/runs/stream`), replace vanilla `page.js`/`page.css` with a Vite+ Svelte 5 + TypeScript + Tailwind CSS v4 (`shadcn-svelte`, `thinking-orbs/engine`, `@paper-design/shaders`) SPA overhauling all four study views and the live `RunStrip`, and record decision 50.

**Architecture:**
- `groundly/web/runs.py` parses `--include-partial-messages` via `StreamTracker` (keeping `Steps` strictly tool-derived with a `refresh` flag on non-empty gate writes) and pushes coalesced frames to async per-subscriber queues owned by `Runner`.
- `groundly/web/app.py` exposes a runtime-locked `FastAPI` app (`docs_url=None`, strict `groundly/web/schemas.py`, pre-parse `_page_json` guard dependency, `Route("/mcp", mcp_app)`, `GET /api/runs/stream`) and serves committed single-file static assets (`groundly/web/static/page.{js,css}`).
- `web/` compiles a Svelte 5 SPA via Vite+ (`vite-plus` / `vp`) using module-level `$state` singletons (`router.svelte.ts`, `theme.svelte.ts`, `run.svelte.ts`), copy-owned `shadcn-svelte` primitives, and canvas/WebGL/CSS effects (`ThinkingOrb`, `BorderBeam`, `LiquidMetalButton`).

**Tech Stack:** Python ≥3.11, FastAPI (`fastapi`), Pydantic v2 (`ConfigDict(strict=True)`), Uvicorn, pytest (`TestClient`, `FakeClaude`), Vite+ (`vite-plus` / `vp`), Svelte 5, TypeScript, Tailwind CSS v4 (`@tailwindcss/vite`), `bits-ui`, `@lucide/svelte`, `thinking-orbs` (`thinking-orbs/engine`), `@paper-design/shaders`, Vitest (`vite-plus/test`), `svelte-check`.

**Spec:** [`docs/superpowers/specs/2026-11-21-fastapi-svelte-sse-web-design.md`](../specs/2026-11-21-fastapi-svelte-sse-web-design.md). Read it first; this plan argues from it.

## Global Constraints

- **Branch isolation before Task 1:** Never commit on `main` or `bench-suite`. At execution start, create a clean feature branch / git worktree off `main` (e.g., `web-fastapi-svelte-sse` via `superpowers:using-git-worktrees`) and copy the spec + plan documents onto it first.
- **Zero-Node Python wheel:** Add `fastapi` to `pyproject.toml`; wheel packages `groundly` only (`web/` excluded). `uv tool install groundly` and `pytest` must never invoke Node, npm, or `vp`.
- **Invariants preserved:** `test_importing_web_app_pulls_in_no_ml_or_retrieval` passes; `groundly/web/` never writes to `store.db`; `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` returns `404` on `/docs`, `/redoc`, `/openapi.json` at HTTP runtime while `create_app().openapi()` succeeds offline; `Route("/mcp", mcp_app)` mounts `/mcp` under `groundly start`; `405` is preserved on all read routes, `/api/runs/stream`, and non-POST writes.
- **Guard order & strict validation:** `_require_page_json` checks own `Origin` (`403`) → `Content-Type: application/json` (`415`) → streaming `MAX_BODY_BYTES = 256 * 1024` (`413`) → `json.loads` (`400`) **before** strict Pydantic v2 validation (`ConfigDict(strict=True)` + `quiz.is_id`, `400 {"error": "invalid_body", ...}`). Query/path `RequestValidationError` maps to `400 {"error": "invalid_text", ...}`.
- **Amended `_CSP` (HTML responses only):** `"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"`.
- **Constants:** `TARGET_QUESTIONS = 10`, `RUN_TIMEOUT_S = 20 * 60`, `MAX_STEPS = 50`, `MAX_SUMMARY_CHARS = 2000`, `MAX_STREAM_CHARS = 2000`, `DELTA_COALESCE_S = 0.04`, `SUBSCRIBER_QUEUE_MAX = 256`, `SSE_KEEPALIVE_S = 15.0`.
- **Layer-3 text-only & authored-source bans:** Never use `{@html`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `onclick=`, `.endsWith(".pdf")`, `"verified"`, `http://`, or `https://` anywhere in `groundly/web/page.html` or `web/src/**` (not even in comments).
- **Typography & diacritics:** Use `system-ui` + `ui-monospace` (full Romanian `ă â î ș ț` `latin-ext` coverage, zero external fonts).

## Review Focus

1. **Fast run settles before `EventSource("/api/runs/stream")` connects** — server emits the settled `event: snapshot` and closes immediately; `RunStore` closes `EventSource` without reconnecting, calls `onEnd()`, and shows outcome. Pinned in Task 3 (`tests/web/test_app.py`) and Task 4 (`web/src/lib/__tests__/run.test.ts`).
2. **Slow/backgrounded browser tab overflows `SUBSCRIBER_QUEUE_MAX = 256`** — `_enqueue` drains stale frames and replaces them with one `("snapshot", ...)` resync without blocking `Runner._read`. Pinned in Task 2 (`tests/web/test_runs.py`).
3. **Browser without WebGL context or with `prefers-reduced-motion: reduce`** — `LiquidMetalButton` catches WebGL errors and renders the styled fallback button; `ThinkingOrb` draws a static frame and `BorderBeam` disables motion. Pinned in Task 5 (`web/src/lib/components/__tests__/effects.test.ts`).
4. **Mid-run `refresh: true` fires while a topic drawer is open or while viewing another subject** — `TreeView` reloads counts silently while keeping `selectedTopicId` and focus intact, and `RunStore` ignores refreshes when another subject's view is open. Pinned in Task 4 (`run.test.ts`) and Task 6 (`tree-filter.test.ts`).
5. **Uvicorn restarts or exits while `EventSource` is open** — `RunStore`'s `onerror` closes `EventSource` (no infinite reconnect loop), probes `GET /api/runs` once, and reconciles or surfaces `"Lost the page server — reload the page when it is back."`. Pinned in Task 4 (`run.test.ts`).

## File Structure

| File | Responsibility |
|---|---|
| `groundly/web/runs.py` | `--include-partial-messages`, pre-init `stream_event` guard, `StreamTracker`, `Steps` refresh flag, `Runner` async subscriber queues + coalescing |
| `groundly/web/schemas.py` | Strict Pydantic v2 request, response, and run snapshot models |
| `groundly/web/app.py` | Runtime-locked FastAPI app, `_CSP`, `_require_page_json`, `RequestValidationError` handler, routes + `GET /api/runs/stream` |
| `web/package.json`, `tsconfig.json`, `vite.config.ts`, `index.html` | Vite+ toolchain, `@tailwindcss/vite`, `@sveltejs/vite-plugin-svelte`, dev proxy, single-bundle build to `groundly/web/static/page.{js,css}` |
| `web/src/app.css` | Tailwind v4 theme tokens (`.dark` + light), `@keyframes` for `BorderBeam`, reduced-motion rules |
| `web/src/lib/{types,api}.ts`, `{router,theme,run}.svelte.ts` | Typed API/SSE union, History router, light/dark/system theme, persistent SSE `RunStore` |
| `web/src/lib/components/ui/*` | Copy-owned `shadcn-svelte` primitives (`Button`, `Badge`, `Card`, `Input`, `Select`, `utils`) |
| `web/src/lib/components/effects/*` | `orb-state.ts`, `ThinkingOrb.svelte`, `BorderBeam.svelte`, `LiquidMetalButton.svelte` |
| `web/src/lib/components/{Brand,MasteryBadge,SourceItem,RunStrip}.svelte` | Shared header, progress badge, citation renderer, live streaming run panel |
| `web/src/lib/views/*` | `tree-filter.ts`, `test-session.ts`, `SubjectsView`, `TreeView`, `TestView`, `ResultView` |
| `web/src/{App.svelte,main.ts}` | Persistent shell + route outlet |
| `groundly/web/static/page.{js,css}` | Committed single-bundle Vite+ outputs |
| `tests/web/{conftest,test_runs,test_app}.py`, `tests/cli/test_cli_serve.py` | Backend partial-streaming, async SSE, FastAPI contract, and split `AUTHORED_SOURCE`/`SHIPPED_BUNDLE` tests |
| `docs/{groundly-spec.md,tech-stack/tech-stack.md,architecture/overview.md,infrastructure/security.md}`, `.claude/rules/{architecture,conventions}.md` | Decision 50 and satellite documentation updates |

---

### Task 1: Live partial streaming, pre-init `stream_event` guard, `Steps` refresh flag & `StreamTracker`

**Files:**
- Modify: `groundly/web/runs.py`
- Modify: `tests/web/conftest.py`
- Modify: `tests/web/test_runs.py`

**Interfaces:**
- Produces in `groundly.web.runs`:
  - Constants: `MAX_STREAM_CHARS = 2000`, `DELTA_COALESCE_S = 0.04`, `SUBSCRIBER_QUEUE_MAX = 256`, `SSE_KEEPALIVE_S = 15.0`
  - `Steps.feed(event: dict) -> tuple[list[str], bool]` (returns `(steps, refresh)`; `refresh=True` iff `submit_questions` `accepted` count ≥ 1 or `review_answers` `recorded` count ≥ 1)
  - `@dataclass(frozen=True) class PhaseEvent`: `phase: str | None`, `active_tool: str | None`
  - `@dataclass(frozen=True) class DeltaEvent`: `channel: str`, `text: str`, `reset: bool = False`
  - `class StreamTracker`: `phase: str | None`, `active_tool: str | None`, `thought: str`, `thought_channel: str | None`, `on_init() -> PhaseEvent`, `feed(event: dict) -> list[PhaseEvent | DeltaEvent]`, `reset_settled() -> None`
  - `build_argv` includes `"--include-partial-messages"`
  - `Runner._read` rejects `"stream_event"` before `system/init` with `isolation_failed`
  - `Runner` run dict includes `phase` (`"starting"`), `active_tool` (`None`), `thought` (`""`), `thought_channel` (`None`), cleared to `phase=None, active_tool=None, thought="", thought_channel=None` on settlement
- Produces in `tests/web/conftest.py`: `stream_start(block_type, *, name=None)`, `stream_delta(delta_type, value)`, `tool_result(tool_id, payload, *, is_error=False)`

- [ ] **Step 1: Add stream helpers to `tests/web/conftest.py` and write failing tests in `tests/web/test_runs.py`**

Update existing `Steps.feed(...)` assertions in `tests/web/test_runs.py` to unpack `(steps, refresh)` (`assert steps == [...]` and `refresh is False` for non-mutating steps), and add the new tests:

```python
# tests/web/conftest.py helpers:
def stream_start(block_type: str, *, name: str | None = None) -> dict: ...
def stream_delta(delta_type: str, value: str) -> dict: ...
def tool_result(tool_id: str, payload, *, is_error: bool = False) -> dict: ...
```

```python
# tests/web/test_runs.py additions:
def test_build_argv_enables_partial_messages():
    argv = runs.build_argv("/bin/claude", "write", "P", None)
    assert "--include-partial-messages" in argv


def test_stream_tracker_tracks_partial_deltas_and_drops_tool_json():
    tracker = runs.StreamTracker()
    assert tracker.on_init() == runs.PhaseEvent("thinking", None)
    assert tracker.feed(stream_start("thinking")) == [
        runs.PhaseEvent("thinking", None),
        runs.DeltaEvent("thinking", "", reset=True),
    ]
    tracker.feed(stream_delta("thinking_delta", "Checking "))
    tracker.feed(stream_delta("thinking_delta", "slides.\nNext line."))
    assert tracker.thought == "Checking slides.\nNext line."
    assert tracker.thought_channel == "thinking"
    # Drops input_json_delta and flips phase immediately on tool_use start
    assert tracker.feed(stream_start("tool_use", name="mcp__groundly__submit_questions")) == [
        runs.PhaseEvent("writing", "Drafting questions")
    ]
    assert tracker.feed(stream_delta("input_json_delta", '{"questions": [')) == []
    assert tracker.thought == "Checking slides.\nNext line."
    # review_answers starts as reading, upgrades to writing on completed assistant if reviews present
    assert tracker.feed(stream_start("tool_use", name="mcp__groundly__review_answers")) == [
        runs.PhaseEvent("reading", "Reviewing answers")
    ]
    assert tracker.feed(use("r1", "mcp__groundly__review_answers", {"reviews": [1]})) == [
        runs.PhaseEvent("writing", "Reviewing answers")
    ]


def test_stream_tracker_caps_thought_at_max_stream_chars_and_falls_back_on_assistant():
    tracker = runs.StreamTracker()
    tracker.on_init()
    tracker.feed(stream_start("text"))
    tracker.feed(stream_delta("text_delta", "a" * (runs.MAX_STREAM_CHARS + 50)))
    assert len(tracker.thought) == runs.MAX_STREAM_CHARS
    assert tracker.thought == "a" * runs.MAX_STREAM_CHARS
    # Non-partial assistant fallback updates thought when no stream_event ran for the turn
    tracker2 = runs.StreamTracker()
    tracker2.on_init()
    tracker2.feed(
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "Plan"}]}}
    )
    assert tracker2.thought == "Plan" and tracker2.thought_channel == "thinking"


def test_steps_refresh_flag_triggers_only_on_nonempty_gate_writes():
    s = runs.Steps()
    assert s.feed(stream_delta("text_delta", "10 stored")) == ([], False)
    assert s.feed(use("q0", "mcp__groundly__submit_questions", {"questions": []})) == (
        ["Submitting 0 questions"],
        False,
    )
    assert s.feed(tool_result("q0", {"accepted": [], "rejected": [{}]})) == (
        ["0 stored, 1 rejected"],
        False,
    )
    assert s.feed(use("q1", "mcp__groundly__submit_questions", {"questions": [1]})) == (
        ["Submitting 1 questions"],
        False,
    )
    assert s.feed(tool_result("q1", {"accepted": [{}], "rejected": []})) == (
        ["1 stored, 0 rejected"],
        True,
    )
    assert s.feed(use("r0", "mcp__groundly__review_answers", {})) == (
        ["Reading answers awaiting review"],
        False,
    )
    assert s.feed(tool_result("r0", {"awaiting": [], "remaining": 0})) == ([], False)
    assert s.feed(use("r1", "mcp__groundly__review_answers", {"reviews": [1]})) == (
        ["Recording 1 verdicts"],
        False,
    )
    assert s.feed(tool_result("r1", {"recorded": [1], "remaining": 0})) == (
        ["1 verdicts recorded"],
        True,
    )


def test_stream_event_before_init_fails_closed(fake_claude):
    fake_claude.script([stream_delta("text_delta", "hello"), FAKE_INIT, success()])
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    run = wait_until_ended(runner.view)
    assert run["status"] == "failed" and run["error"]["reason"] == "isolation_failed"
    assert run["phase"] is None and run["thought"] == ""


def test_run_tracks_and_clears_phase_and_thought_on_settlement(fake_claude):
    fake_claude.script(
        [
            FAKE_INIT,
            stream_start("thinking"),
            stream_delta("thinking_delta", "Thinking..."),
            use("t1", "mcp__groundly__search"),
            success("Stored 10."),
        ]
    )
    runner = runs.Runner()
    started = runner.start("write", "PDSS", 7, "the prompt")
    assert (started["phase"], started["active_tool"], started["thought"]) == ("starting", None, "")
    done = wait_until_ended(runner.view)
    assert (
        done["status"],
        done["phase"],
        done["active_tool"],
        done["thought"],
        done["thought_channel"],
    ) == ("done", None, None, "", None)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/web/test_runs.py -v`
Expected: FAIL (`AttributeError: module 'groundly.web.runs' has no attribute 'StreamTracker'` / missing `--include-partial-messages`).

- [ ] **Step 3: Implement `StreamTracker`, `Steps` refresh flag, `--include-partial-messages`, and pre-init guard in `groundly/web/runs.py`**

- Add `MAX_STREAM_CHARS = 2000`, `DELTA_COALESCE_S = 0.04`, `SUBSCRIBER_QUEUE_MAX = 256`, `SSE_KEEPALIVE_S = 15.0`.
- Append `"--include-partial-messages"` after `"--verbose"` in `build_argv`.
- Update `Steps.feed(event) -> tuple[list[str], bool]`: set `refresh = True` only when `tool_result` resolves a `submit_questions` call with `_count(data.get("accepted")) > 0` or a `review_answers` call with `_count(data.get("recorded")) > 0`.
- Implement `PhaseEvent`, `DeltaEvent`, and `StreamTracker` per spec §3 (`stream_event` → `content_block_start` / `thinking_delta` / `text_delta` / drop `input_json_delta`; non-partial `assistant` fallback; `user` `tool_result` → `PhaseEvent("thinking", None)`; `review_answers` starting `"reading"` then upgrading to `"writing"` if `input.reviews` non-empty). Keep whitespace/newlines verbatim in `thought`, tail-capping `thought[-MAX_STREAM_CHARS:]`.
- Initialize `run` with `phase="starting"`, `active_tool=None`, `thought=""`, `thought_channel=None`.
- In `Runner._read`: treat `"stream_event"` as a disallowed pre-init event (`not is_init and event.get("type") not in ("stream_event", "assistant", "user", "result")` skips only other system events); feed both `Steps` and `StreamTracker` under `self._lock`; clear `phase=None, active_tool=None, thought="", thought_channel=None` on every terminal state (`result`, `_end`, and exit).

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/web/test_runs.py -v`
Expected: PASS (all `test_runs.py` tests pass).

- [ ] **Step 5: Commit**

```bash
git add groundly/web/runs.py tests/web/conftest.py tests/web/test_runs.py
git commit -m "feat(web): stream partial messages with StreamTracker and tool-only refresh"
```

---

### Task 2: Async per-subscriber queues, 40 ms delta coalescing & shutdown wakeup in `Runner`

**Files:**
- Modify: `groundly/web/runs.py`
- Modify: `tests/web/test_runs.py`

**Interfaces:**
- Consumes: Task 1's `StreamTracker`, `Steps.feed`, `DELTA_COALESCE_S`, `SUBSCRIBER_QUEUE_MAX`
- Produces on `Runner`:
  - `subscribe(self, loop: asyncio.AbstractEventLoop) -> tuple[dict | None, asyncio.Queue[tuple[str, dict | None] | None] | None]`
  - `unsubscribe(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[tuple[str, dict | None] | None]) -> None`
  - `_publish(self, event_name: str, data: dict | None, *, snapshot_fallback: dict | None = None) -> None` (defaults `snapshot_fallback` to `self._snapshot()` if omitted when emitting from `Runner`)

- [ ] **Step 1: Write failing async pub-sub tests in `tests/web/test_runs.py`**

Include tests covering fan-out, causal coalescing, backpressure overflow (Review Focus #2), and shutdown wakeup:

```python
async def test_runner_subscribe_fans_out_coalesced_deltas_before_phase_and_step(fake_claude):
    fake_claude.script(
        [
            FAKE_INIT,
            stream_start("thinking"),
            stream_delta("thinking_delta", "One "),
            stream_delta("thinking_delta", "two"),
            use("t1", "mcp__groundly__search"),
            tool_result("t1", {"hits": []}),
            success("Done."),
        ]
    )
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    snap, q1 = runner.subscribe(loop)
    _, q2 = runner.subscribe(loop)
    assert snap is not None and q1 is not None and q2 is not None
    # Drain both queues until None sentinel; verify causal order and done snapshot
    events1 = []
    while (item := await asyncio.wait_for(q1.get(), timeout=5.0)) is not None:
        events1.append(item)
    events2 = []
    while (item := await asyncio.wait_for(q2.get(), timeout=5.0)) is not None:
        events2.append(item)
    assert [name for name, _ in events1] == [name for name, _ in events2]
    names = [name for name, _ in events1]
    assert "delta" in names and "step" in names and names[-1] == "done"
    # Merged/coalesced delta arrives strictly before the tool_use phase transition
    assert names.index("delta") < names.index("step")


async def test_subscriber_queue_overflow_emits_snapshot_resync_without_blocking(
    fake_claude, monkeypatch
):
    # Review Focus #2: small maxsize forces QueueFull -> drains stale frames, emits ("snapshot", ...)
    monkeypatch.setattr(runs, "SUBSCRIBER_QUEUE_MAX", 2)
    fake_claude.script([FAKE_INIT, "SLEEP"])
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    _, q = runner.subscribe(loop)
    assert q is not None
    for i in range(10):
        runner._publish(
            "delta",
            {"id": 1, "channel": "text", "text": str(i), "reset": False},
            snapshot_fallback={"id": 1, "i": i},
        )
    await asyncio.sleep(0.05)
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert any(name == "snapshot" for name, _ in items)
    runner.shutdown()


async def test_shutdown_pushes_sentinel_and_wakes_subscribers(fake_claude):
    fake_claude.script([FAKE_INIT, "SLEEP"])
    loop = asyncio.get_running_loop()
    runner = runs.Runner()
    runner.start("write", "PDSS", 7, "the prompt")
    _, q = runner.subscribe(loop)
    assert q is not None
    runner.shutdown()
    items = []
    while (item := await asyncio.wait_for(q.get(), timeout=2.0)) is not None:
        items.append(item)
    assert items[-1][0] == "done" and items[-1][1]["status"] == "stopped"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/web/test_runs.py -k "subscribe or overflow or shutdown_pushes" -v`
Expected: FAIL (`AttributeError: 'Runner' object has no attribute 'subscribe'`).

- [ ] **Step 3: Implement `subscribe`, `unsubscribe`, `_publish`, `_enqueue`, and delta coalescer in `groundly/web/runs.py`**

- Maintain `self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[tuple[str, dict | None] | None]]] = set()`.
- `subscribe(loop)`: under `self._lock`, snapshot `self._run`. If `snapshot is not None and snapshot["status"] == "running"`, create `q = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)`, add `(loop, q)` to `self._subscribers`, and return `(snapshot, q)`; else return `(snapshot, None)`.
- `unsubscribe(loop, queue)`: under `self._lock`, `self._subscribers.discard((loop, queue))`.
- `_publish(event_name, data, *, snapshot_fallback=None)` and `_enqueue(queue, event_name, data, snapshot_fallback)`:
  - Schedule via `loop.call_soon_threadsafe`; catch `RuntimeError` if loop is closed and remove subscriber.
  - Inside `_enqueue`: if `event_name == "done"` or `data is None`, drop non-terminal queued items if full so terminal `done` + `None` sentinel always land; for intermediate events on `asyncio.QueueFull`, drain queue and `put_nowait(("snapshot", snapshot_fallback))`.
- In `Runner._read`, maintain a per-channel pending delta buffer flushed every `DELTA_COALESCE_S = 0.04` seconds or immediately before publishing any `phase`, `step`, or `done` frame (guaranteeing causal order).
- Ensure `_end` and `Runner.shutdown()` publish `("done", snapshot)` then `None` sentinel to all subscribers (removing them from `_subscribers` after terminal push).

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/web/test_runs.py -v`
Expected: PASS (all tests in `test_runs.py` pass).

- [ ] **Step 5: Commit**

```bash
git add groundly/web/runs.py tests/web/test_runs.py
git commit -m "feat(web): add async subscriber queues and coalesced delta pub-sub to Runner"
```

---

### Task 3: FastAPI migration, strict Pydantic v2 schemas & `GET /api/runs/stream`

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Create: `groundly/web/schemas.py`
- Modify: `groundly/web/app.py`
- Modify: `tests/web/test_app.py`

**Interfaces:**
- Consumes: Task 2's `Runner.subscribe`, `Runner.unsubscribe`, `SSE_KEEPALIVE_S`, `quiz.is_id`
- Produces:
  - `groundly/web/schemas.py`: `ErrorResponse`, `SubjectSummary`, `SourceItem`, `TopicProgress`, `TopicNode`, `TreeResponse`, `QuestionOption`, `DrawnQuestion`, `DrawTestResponse`, `SubmitAttemptRequest`, `SubmitAttemptResponse`, `AttemptScore`, `AttemptSummary`, `AttemptResultResponse`, `RunErrorDetail`, `RunSnapshot`, `RunsInfoResponse`, `StartRunRequest`, `StopRunRequest`, `StopRunResponse` (using `ConfigDict(strict=True)` on request models)
  - `groundly/web/app.py`: `create_app(mcp_app: ASGIApp | None = None, mcp_url: str | None = None) -> FastAPI` with `GET /api/runs/stream` returning `StreamingResponse(..., media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})`

- [ ] **Step 1: Write failing FastAPI & SSE tests in `tests/web/test_app.py` (and deterministic `"WAIT:<path>"` support in `tests/web/conftest.py`)**

Add `"WAIT:<path>"` support to `tests/web/conftest.py` (`FakeClaude` script waits up to 5s for `<path>` to appear on disk before continuing subsequent lines, eliminating any fast-exit race during live stream tests). Update `test_page_routes_serve_html_under_a_self_only_csp` to assert the amended `_CSP` (`script-src 'self'` without `'unsafe-inline'` in the `script-src` segment, `style-src 'self' 'unsafe-inline'`, `font-src 'self'`, `default-src 'none'`, `frame-ancestors 'none'`). Add tests for FastAPI lockdown and `GET /api/runs/stream` (including Review Focus #1):

```python
def test_openapi_and_docs_are_disabled_at_runtime_but_work_offline(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    schema = create_app().openapi()
    assert "/api/runs/stream" in schema["paths"]
    assert "/api/subjects/{subject}/runs" in schema["paths"]


def test_runs_stream_closes_immediately_when_idle_or_already_settled(client, seeded, fake_claude):
    # Review Focus #1: idle or already settled run emits event: snapshot then closes
    r = client.get("/api/runs/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    assert r.text == "event: snapshot\ndata: null\n\n"
    leaf = _leaf(client)
    _runs_post(client, {"kind": "write", "topic_id": leaf})
    wait_until_ended(_run_view(client))
    r2 = client.get("/api/runs/stream")
    assert "event: snapshot\n" in r2.text and '"status": "done"' in r2.text


def test_runs_stream_emits_phase_delta_step_and_done(client, seeded, fake_claude, tmp_path):
    gate = tmp_path / "go"
    fake_claude.script(
        [
            FAKE_INIT,
            f"WAIT:{gate}",
            stream_start("thinking"),
            stream_delta("thinking_delta", "Reading notes"),
            use("t1", "mcp__groundly__submit_questions", {"questions": [1]}),
            tool_result("t1", {"accepted": [{}], "rejected": []}),
            success("Stored 1."),
        ]
    )
    leaf = _leaf(client)
    assert _runs_post(client, {"kind": "write", "topic_id": leaf}).status_code == 202
    chunks = []
    with client.stream("GET", "/api/runs/stream") as resp:
        for chunk in resp.iter_text():
            chunks.append(chunk)
            if not gate.exists() and "event: snapshot" in "".join(chunks):
                gate.write_text("1")
    body = "".join(chunks)
    assert "event: snapshot\n" in body
    assert "event: phase\n" in body
    assert "event: delta\n" in body
    assert "event: step\n" in body and '"refresh": true' in body
    assert "event: done\n" in body


def test_runs_stream_refuses_writes_with_405(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/runs/stream").status_code == 405
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/web/test_app.py -k "openapi or runs_stream or self_only_csp" -v`
Expected: FAIL (`404` on `/api/runs/stream`, `_CSP` mismatch, `/openapi.json` missing or `create_app()` not `FastAPI`).

- [ ] **Step 3: Add `fastapi`, create `groundly/web/schemas.py`, and migrate `groundly/web/app.py` to FastAPI + SSE**

1. Add dependency cleanly: `uv add fastapi`.
2. Create `groundly/web/schemas.py` with strict Pydantic v2 models (`ConfigDict(strict=True)` on `SubmitAttemptRequest`, `StartRunRequest`, `StopRunRequest`, validating `quiz.is_id` on integers so `"1"`, `True`, and `2**70` fail validation).
3. Update `groundly/web/app.py`:
   - Update `_CSP` to `"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"`.
   - Define `_PageJsonError(Exception)` + exception handler returning `_error(exc.error, exc.detail, exc.status_code)` and `RequestValidationError` exception handler returning `_error("invalid_text", ...)` (`400`).
   - Implement `_require_page_json(forbidden: str)` dependency running `Origin` (`403`) → `Content-Type` (`415`) → streaming `MAX_BODY_BYTES` (`413`) → `json.loads` (`400`) before validating strict Pydantic models (`400 {"error": "invalid_body", ...}` with exact existing detail strings).
   - Construct `FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan, middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=HOSTS)])`, attach existing routes with typed `response_model=...` where returning JSON, add `@app.get("/api/runs/stream")` yielding `snapshot` → `phase`/`delta`/`step`/`done` + `: keep-alive` (closing immediately if `snap is None or snap["status"] != "running"`, and unsubscribing in `finally`), and append `Route("/mcp", mcp_app)` when `mcp_app` is passed. (Use explicit `methods=["GET"]` on page/JSON/PDF/stream routes and ensure wrong HTTP methods return `405`.)

- [ ] **Step 4: Verify Python backend checks**

Run: `uv run pytest tests/web/ -v && uv run lint-imports && uv run pyrefly check`
Expected: PASS (every test in `tests/web/` passes, `import-linter` contract passes, `pyrefly` passes with 0 errors).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock groundly/web/schemas.py groundly/web/app.py tests/web/test_app.py
git commit -m "feat(web): migrate web app to FastAPI with strict schemas and async SSE stream"
```

---

### Task 4: Vite+ Svelte 5 workspace scaffold & reactive client core (`web/`)

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/index.html`, `web/src/app.css`
- Create: `web/src/lib/types.ts`, `web/src/lib/api.ts`, `web/src/lib/router.svelte.ts`, `web/src/lib/theme.svelte.ts`, `web/src/lib/run.svelte.ts`
- Create: `web/src/lib/__tests__/router.test.ts`, `web/src/lib/__tests__/run.test.ts`

**Interfaces:**
- Produces in `web/src/lib/types.ts`: TS types matching `groundly/web/schemas.py` (`RunPhase`, `RunKind`, `RunStatus`, `ThoughtChannel`, `RunSnapshot`, `RunsInfo`, `SubjectSummary`, `TopicNode`, `TreeResponse`, `DrawnQuestion`, `AttemptSummary`, `AttemptResult`, `SSEPhaseEvent`, `SSEDeltaEvent`, `SSEStepEvent`)
- Produces in `web/src/lib/api.ts`: `getJSON<T>(url, init?)`, `postJSON<T>(url, body)`, `api(subject, rest)`, `pageURL(subject, rest?)`, `testQuery(topicId, n)` (containing exact `"/api/runs"` and `'"/runs"'` strings required by `test_cli_serve.py`)
- Produces in `web/src/lib/router.svelte.ts`: `parseRoute(pathname, search)`, `shouldInterceptLink(anchor, event)`, `router` (`route`, `navigate(url)`, `interceptClick(e)`)
- Produces in `web/src/lib/theme.svelte.ts`: `theme` (`mode: "system" | "light" | "dark"`, `resolved: "light" | "dark"`, `setMode(m)`)
- Produces in `web/src/lib/run.svelte.ts`: `createRunStore()` & singleton `runStore` (`available`, `target`, `run`, `watched`, `starting`, `message`, `busy`, `dismissed`, `attach()`, `start(subject, body)`, `stop()`, `dismiss()`, `setHooks({ subject, onRefresh, onEnd })`)

- [ ] **Step 1: Initialize `web/` workspace config and dependencies**

Create `web/package.json` (`private: true`, scripts `dev: "vp dev"`, `build: "vp build"`, `check: "vp check && svelte-check --tsconfig ./tsconfig.json"`, `test: "vp test run"`), `web/tsconfig.json`, `web/vite.config.ts` (`defineConfig` from `"vite-plus"`, `svelte()`, `tailwindcss()`, `test: { environment: "jsdom" }`, dev proxy `/api` and `/s/:subject/materials` → `http://127.0.0.1:8765` rewriting `Origin` + `Host`, build `outDir: "../groundly/web/static"`, `emptyOutDir: false`, `cssCodeSplit: false`, Rollup IIFE output `page.js` / `page.css`), `web/index.html` (dev shell), and `web/src/app.css` (Tailwind v4 `@import "tailwindcss";` + `@custom-variant dark (&:where(.dark, .dark *));` + light/dark CSS tokens + pure-CSS `.border-beam` animation obeying `prefers-reduced-motion`). Install packages inside `web/` via `pnpm install` (or `npm install`) for `svelte`, `@sveltejs/vite-plugin-svelte`, `tailwindcss`, `@tailwindcss/vite`, `bits-ui`, `@lucide/svelte`, `thinking-orbs`, `@paper-design/shaders`, `clsx`, `tailwind-merge`, `tailwind-variants`, `vite-plus`, `svelte-check`, `typescript`, `jsdom`.

- [ ] **Step 2: Write failing unit tests in `web/src/lib/__tests__/router.test.ts` and `web/src/lib/__tests__/run.test.ts`**

Test router parsing + link interception + `RunStore` covering Review Focus #1, #4, and #5:

```ts
// router.test.ts:
// - parseRoute parses "/", "/s/PDSS", "/s/PDSS/test?topic=7&n=10", "/s/PDSS/attempts/42", and unknown path -> { kind: "not_found" }
// - shouldInterceptLink returns false for target="_blank", modifier clicks, external origins, and "/s/PDSS/materials/1"
```

```ts
// run.test.ts (mock global fetch + EventSource, importing describe/it/expect/vi from "vite-plus/test"):
// 1. Snapshot -> phase -> delta (reset + append + tail cap at 2000 chars) -> step (refresh: true triggers onRefresh only when subject matches; Review Focus #4) -> done (closes EventSource, calls onEnd, sets outcome)
// 2. Fast run already settled in initial `snapshot` closes EventSource immediately and triggers onEnd (Review Focus #1)
// 3. `start()` receiving 409 attaches to active run via GET /api/runs
// 4. `onerror` closes EventSource, probes GET /api/runs once, and shows "Lost the page server — reload the page when it is back." on network failure (Review Focus #5)
```

- [ ] **Step 3: Run unit tests to verify failure**

Run: `cd web && vp test run`
Expected: FAIL (missing `router.svelte.ts` / `run.svelte.ts`).

- [ ] **Step 4: Implement `types.ts`, `api.ts`, `router.svelte.ts`, `theme.svelte.ts`, and `run.svelte.ts`**

Ensure zero `http://`/`https://` literals anywhere under `web/src/`. In `run.svelte.ts`, handle subject-scoped `onRefresh` filtering (only fire `onRefresh()` when `currentRun.subject === hookSubject`) and close `EventSource` cleanly inside `snapshot` (when settled or `null`), `done`, and `onerror`.

- [ ] **Step 5: Run tests and type/lint checks**

Run: `cd web && vp test run && vp check && npx svelte-check --tsconfig ./tsconfig.json`
Expected: PASS (0 test failures, 0 type/Svelte errors).

- [ ] **Step 6: Commit**

```bash
git add web/
git commit -m "feat(web): scaffold Vite+ Svelte 5 workspace and reactive SSE RunStore"
```

---

### Task 5: `shadcn-svelte` primitives, visual effects (`ThinkingOrb`, `BorderBeam`, `LiquidMetalButton`) & `RunStrip`

**Files:**
- Create: `web/src/lib/components/ui/` (`utils.ts`, `Button.svelte`, `Badge.svelte`, `Card.svelte`, `Input.svelte`, `Select.svelte`)
- Create: `web/src/lib/components/effects/` (`orb-state.ts`, `ThinkingOrb.svelte`, `BorderBeam.svelte`, `LiquidMetalButton.svelte`)
- Create: `web/src/lib/components/` (`Brand.svelte`, `MasteryBadge.svelte`, `SourceItem.svelte`, `RunStrip.svelte`)
- Create: `web/src/lib/components/__tests__/effects.test.ts`

**Interfaces:**
- Consumes: Task 4's `types.ts`, `theme`, `runStore`
- Produces:
  - `orb-state.ts`: `orbStateFor(phase: RunPhase, kind: RunKind): OrbState`, `phaseLabelFor(phase: RunPhase, kind: RunKind, activeTool?: string | null): string`
  - `ThinkingOrb.svelte`: props `{ phase: RunPhase, kind: RunKind, size?: 20 | 32 | 64 }`
  - `BorderBeam.svelte`: props `{ active?: boolean }`
  - `LiquidMetalButton.svelte`: props `{ label: string, disabled?: boolean, onclick: () => void, class?: string }`
  - `RunStrip.svelte`: global live run card reading `runStore` (plain-text `{run.thought}`, deterministic `<ol class="steps">` capped to last 6, **Stop** button, `<p class="outcome" role="status">`)
  - `SourceItem.svelte`: props `{ subject: string, src: TopicSource }` (links when `src.pdf === true`, else focusable `<pre tabindex="0" aria-label="Text from {label}">`)
  - `MasteryBadge.svelte`: props `{ progress: TopicProgress | null }` + `progressLine(p)` helper

- [ ] **Step 1: Write failing component/effect tests in `web/src/lib/components/__tests__/effects.test.ts`**

Test `orbStateFor`, `phaseLabelFor`, `LiquidMetalButton` WebGL-unavailable / reduced-motion fallback (Review Focus #3), and `RunStrip` strict text rendering of XSS payloads (`<script>x</script>`) using `mount` + `flushSync` from `"svelte"` under jsdom:

```ts
// effects.test.ts:
// 1. orbStateFor maps starting->connecting, reading->searching, writing+write->composing, writing+grade->solving, thinking->working
// 2. LiquidMetalButton renders an accessible button and falls back cleanly without throwing when canvas getContext('webgl2')/('webgl') returns null or matchMedia("(prefers-reduced-motion: reduce)") matches (Review Focus #3)
// 3. RunStrip renders run.thought, run.steps, and run.summary strictly as text nodes (no <script> element created) and announces outcome with role="status"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd web && vp test run src/lib/components/__tests__/effects.test.ts`
Expected: FAIL (missing modules).

- [ ] **Step 3: Implement `ui/` primitives, effects, `Brand`, `MasteryBadge`, `SourceItem`, and `RunStrip`**

- In `ThinkingOrb.svelte`: import `MODE_FRAMES`, `paintFrame`, `resolvePreset` from `"thinking-orbs/engine"`, animate via `requestAnimationFrame` respecting `document.visibilityState`, `IntersectionObserver`, and `prefers-reduced-motion: reduce` (paints static `t = 0.6`), syncing ink colors from `theme.resolved`.
- In `LiquidMetalButton.svelte`: mount `ShaderMount` with `liquidMetalFragmentShader` from `"@paper-design/shaders"` inside `try/catch`; skip WebGL completely when `prefers-reduced-motion: reduce` or WebGL unavailable; always `dispose()` on cleanup.
- In `SourceItem.svelte`: keep exact `"src.pdf === true"` check (never `.endsWith(".pdf")`), `target="_blank" rel="noopener"`, and focusable `<pre tabindex="0" aria-label="Text from {label}">`.
- In `RunStrip.svelte`: wrap active state in `<BorderBeam active={working} />`, display `<ThinkingOrb>`, phase/active-tool badge, **Stop** button, scrollable plain-text live `{run.thought}` stream box, last 6 `run.steps`, `<p class="outcome" role="status">`, and Dismiss button once settled.

- [ ] **Step 4: Run tests and type checks**

Run: `cd web && vp test run && vp check && npx svelte-check --tsconfig ./tsconfig.json`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/components/
git commit -m "feat(web): add shadcn-svelte primitives, ThinkingOrb, LiquidMetalButton, BorderBeam, and RunStrip"
```

---

### Task 6: `SubjectsView` & `TreeView` overhaul

**Files:**
- Create: `web/src/lib/views/tree-filter.ts`
- Create: `web/src/lib/views/SubjectsView.svelte`
- Create: `web/src/lib/views/TreeView.svelte`
- Create: `web/src/lib/views/__tests__/tree-filter.test.ts`

**Interfaces:**
- Consumes: Task 4's `api`, `runStore`, `router` and Task 5's `Card`, `Badge`, `Input`, `Select`, `MasteryBadge`, `SourceItem`, `LiquidMetalButton`
- Produces:
  - `tree-filter.ts`: `filterTree(tree: TopicNode[], query: string): TopicNode[]`, `findTopicById(tree: TopicNode[], id: number | null): TopicNode | null`, `writeButtonInfo(available: boolean, target: number, node: TopicNode): { label: string } | null`
  - `SubjectsView.svelte`, `TreeView.svelte` (accepting `{ subject }: { subject: string }`)

- [ ] **Step 1: Write failing tests in `web/src/lib/views/__tests__/tree-filter.test.ts`**

Cover topic search/filter logic, `writeButtonInfo` threshold logic, and live reload preservation (Review Focus #4):

```ts
// tree-filter.test.ts:
// 1. filterTree returns full tree for blank query; keeps parent when only a subtopic matches; matches case-insensitively across title/summary
// 2. findTopicById locates updated TopicNode across silent mid-run reloads (Review Focus #4: drawer stays open with updated question counts)
// 3. writeButtonInfo returns "Write questions for its subtopics" for parent with thin leaf, "Write questions" for thin leaf, null when all leaves >= target or !available
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd web && vp test run src/lib/views/__tests__/tree-filter.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement `tree-filter.ts`, `SubjectsView.svelte`, and `TreeView.svelte`**

Preserve all view contracts verbatim:
- `SubjectsView`: card grid showing `s.title || s.subject`, strict ID alongside `title`, status badges (`"topic map ready"`, `"no map yet"`, or error), empty state (`groundly index`).
- `TreeView`:
  - Subject title + strict `subject` ID; `"Test the whole course"` primary CTA; search input filtering `tree`.
  - Two-level spine (`role="list"`) and subtopic chips (`role="list"`, `aria-label="Subtopics of {topic.title}"`), `"sources removed"` dashed flag, `MasteryBadge`.
  - Slide-over drawer (`<aside class="panel" aria-label="Topic details">`): desktop sticky / mobile bottom sheet (`max-width: 700px`), moves focus to `<h2>` (`tabIndex = -1`) on open, restores focus to topic button on `Escape` or `Close`. On silent mid-run `onRefresh` (registered via `runStore.setHooks`), preserves `selectedTopicId` and updates node data in place without stealing focus or closing the drawer.
  - Test chooser (`5`, `10`, `20` disabling above question count), `<LiquidMetalButton>` Write CTA, exact fallback `"Say to your agent: "` + `<code>{"Write questions on \"" + title + "\" for " + subject + "."}</code>` (matching `test_cli_serve.py` contract), deduplicated PDF links (`src.pdf === true`) vs `<pre tabindex="0">`, and past tests list.

- [ ] **Step 4: Run tests and type checks**

Run: `cd web && vp test run && vp check && npx svelte-check --tsconfig ./tsconfig.json`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/views/
git commit -m "feat(web): overhaul SubjectsView and TreeView with live mid-run refreshes"
```

---

### Task 7: `TestView`, `ResultView`, `App.svelte` & `main.ts`

**Files:**
- Create: `web/src/lib/views/test-session.ts`
- Create: `web/src/lib/views/TestView.svelte`
- Create: `web/src/lib/views/ResultView.svelte`
- Create: `web/src/App.svelte`
- Create: `web/src/main.ts`
- Create: `web/src/lib/views/__tests__/test-session.test.ts`

**Interfaces:**
- Consumes: Tasks 4–6
- Produces:
  - `test-session.ts`: `storageKey(subject, topicId)`, `shuffled(items)`, `isAnswered(q, answer)`, `loadTest(key)`, `saveTest(key, test)`, `dropTest(key)`, `createTestPersister(key)` exposing `markDead()` and `save(test)` (while `TestView.svelte` also keeps the exact closure helper `function store() { if (!dead) saveTest(key, test); }` pinned by `test_cli_serve.py`)
  - `TestView.svelte`, `ResultView.svelte`, `App.svelte`, `main.ts`

- [ ] **Step 1: Write failing tests in `web/src/lib/views/__tests__/test-session.test.ts`**

Test `sessionStorage` contract, `dead` guard on `"unknown_question"`, and keyboard shortcut bounds:

```ts
// test-session.test.ts:
// 1. storageKey builds "groundly-test:<subject>:<topicId|all>"
// 2. createTestPersister drops storage on markDead() and never saves again on subsequent store() calls
// 3. saveTest serializes plain snapshot objects cleanly without throwing when storage throws
// 4. TestView shortcut handler ignores number/arrow keys when target is TEXTAREA, INPUT, or SELECT
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd web && vp test run src/lib/views/__tests__/test-session.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement `test-session.ts`, `TestView.svelte`, `ResultView.svelte`, `App.svelte`, and `main.ts`**

- `TestView.svelte`:
  - Uses `"groundly-test:" + subject + ":" + (topicId == null ? "all" : topicId)`, `crypto.randomUUID()`, `shuffled(q.options)` on initial draw, `$state.snapshot(test)` inside `function store() { if (!dead) saveTest(key, test); }`, and sets `dead = true; dropTest(key);` on `"unknown_question"` refusal.
  - Adds progress bar, question pills (`aria-current="step"`), keyboard shortcuts (`1`–`9`, `ArrowLeft`/`ArrowRight` ignored inside form inputs/textareas), crisp text-only `<pre class="code">`, and `role="alertdialog"` confirmation for unanswered questions.
- `ResultView.svelte`:
  - Renders score summary (`"{correct} / {graded} correct"`), provisional banner with `<LiquidMetalButton>` **Grade now** when `result.provisional && runStore.available`, per-answer verdict cards (`state-{a.state}`) with collapsible cited sources (`SourceItem`), `"grade my test"` fallback on awaiting open cards when `!canGrade`, live mid-run verdict reload via `runStore.setHooks({ subject, onRefresh, onEnd })`, and per-topic progress list.
- `App.svelte` & `main.ts`:
  - Top bar with `Brand`, breadcrumbs, light/dark/system theme selector, persistent `RunStrip`, click interceptor (`router.interceptClick`), and route switch (`SubjectsView`, `TreeView`, `TestView`, `ResultView`, and clean not-found card). Mount onto `#app` in `main.ts`.

- [ ] **Step 4: Run tests and type checks**

Run: `cd web && vp test run && vp check && npx svelte-check --tsconfig ./tsconfig.json`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/
git commit -m "feat(web): implement TestView, ResultView, and App shell"
```

---

### Task 8: Production static bundle build & `tests/cli/test_cli_serve.py` split

**Files:**
- Generated: `groundly/web/static/page.js`, `groundly/web/static/page.css`
- Modify: `tests/cli/test_cli_serve.py`

**Interfaces:**
- Consumes: Tasks 4–7 (`cd web && vp build`)
- Produces: Single-file compiled `groundly/web/static/page.js` and `groundly/web/static/page.css` (`claude-code.svg` untouched); updated `tests/cli/test_cli_serve.py` checking `AUTHORED_SOURCE` and `SHIPPED_BUNDLE` separately without invoking Node during `pytest`.

- [ ] **Step 1: Build the production bundle and inspect outputs**

Run: `cd web && vp build && ls -la ../groundly/web/static/`
Expected: `page.js`, `page.css`, and unchanged `claude-code.svg` present (no split JS chunks).

- [ ] **Step 2: Update `tests/cli/test_cli_serve.py` with `AUTHORED_SOURCE` + `SHIPPED_BUNDLE` checks**

Replace the single `PAGE` check with:
- `AUTHORED_SOURCE`: concatenation of `groundly/web/page.html` and every runtime `*.ts`, `*.svelte`, `*.css` under `web/src/` (excluding `**/__tests__/**`). Bans `"innerHTML"`, `"outerHTML"`, `"insertAdjacentHTML"`, `"document.write"`, `"eval("`, `"{@html"`, `"http://"`, `"https://"`, `" onclick="`, `'.endsWith(".pdf")'`, and `"verified"` (case-insensitive). Pins `"src.pdf === true"`, `"sessionStorage"`, `"crypto.randomUUID()"`, `'"unknown_question"'`, `"function store() { if (!dead) saveTest(key, test); }"`, `'"Write questions on \\"" + title + "\\" for "'`, `"/api/runs"`, `'"/runs"'`, `"/api/runs/stream"`, `"/api/runs/stop"`, `"Write questions for its subtopics"`, `"Grade now"`, `"Say to your agent"`, and `"grade my test"`.
- `SHIPPED_BUNDLE`: `groundly/web/page.html` + `groundly/web/static/page.js` + `groundly/web/static/page.css`. Asserts `page.js` and `page.css` are non-empty, `"verified"` not in lowercase bundle, no `http://` or `https://` after stripping W3C XML namespaces (`http://www.w3.org/2000/svg`, `http://www.w3.org/1999/xhtml`, `http://www.w3.org/1998/Math/MathML`), and presence of `"/api/runs/stream"`, `"/api/runs/stop"`, `"unknown_question"`, `"sessionStorage"`, `"randomUUID"`, `"Write questions for its subtopics"`, `"Grade now"`, `"grade my test"`.

- [ ] **Step 3: Run CLI & web tests**

Run: `uv run pytest tests/cli/test_cli_serve.py tests/web/ -v`
Expected: PASS (0 failures, zero Node invocations during `pytest`).

- [ ] **Step 4: Commit**

```bash
git add groundly/web/static/page.js groundly/web/static/page.css tests/cli/test_cli_serve.py
git commit -m "build(web): commit Svelte 5 bundle and split authored/shipped serve tests"
```

---

### Task 9: Decision 50, satellite docs & full-suite verification

**Files:**
- Modify: `docs/groundly-spec.md` (§4 Topic-tree page row, §7 decision 50)
- Modify: `docs/tech-stack/tech-stack.md`
- Modify: `docs/architecture/overview.md`
- Modify: `docs/infrastructure/security.md`
- Modify: `.claude/rules/architecture.md`
- Modify: `.claude/rules/conventions.md`

**Interfaces:**
- Records decision 50 consistently across master spec, satellite docs, and `.claude/rules/`.

- [ ] **Step 1: Record Decision 50 in `docs/groundly-spec.md` and update satellite docs**

Update all six files in sync per spec §"Docs and decision (same change set)":
- FastAPI (`docs_url=None`, `redoc_url=None`, `openapi_url=None`, strict Pydantic after `_page_json`, `Route("/mcp", mcp_app)`, amending decision 41's Starlette framing while FastMCP stays the sole tool-surface framework).
- Vite+ / Svelte 5 / TypeScript / Tailwind v4 (`shadcn-svelte`, `thinking-orbs/engine`, `@paper-design/shaders`) SPA compiling into committed `groundly/web/static/page.{js,css}` (zero Node runtime).
- `--include-partial-messages` + pre-init `stream_event` guard + `MAX_STREAM_CHARS = 2000` rolling `thought` separate from tool-only `steps` + async pub-sub `GET /api/runs/stream` with `refresh: true` mid-run gate refreshes.
- Amended `_CSP`: `style-src 'self' 'unsafe-inline'` + `font-src 'self'` while `script-src 'self'`, `default-src 'none'`, and zero `{@html}` stay intact.

- [ ] **Step 2: Run complete frontend, Python, and graph verification**

Run all checks after final edits:
```bash
(cd web && vp check && npx svelte-check --tsconfig ./tsconfig.json && vp test run && vp build)
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
uv run lint-imports
uv run pytest -q
```
If `graphify` CLI is installed (`command -v graphify`), run `graphify update .` per `CLAUDE.md`. Also confirm `git status --short` shows clean bundle/doc state (no stale edits, `claude-code.svg` intact).

- [ ] **Step 3: Commit**

```bash
git add docs/groundly-spec.md docs/tech-stack/tech-stack.md docs/architecture/overview.md docs/infrastructure/security.md .claude/rules/architecture.md .claude/rules/conventions.md
git commit -m "docs: record decision 50 for FastAPI web API, Svelte 5 UI, and live SSE"
```
