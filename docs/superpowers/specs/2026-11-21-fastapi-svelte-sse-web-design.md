# FastAPI Web API, Svelte 5 UI Overhaul & Live Token-Level SSE Streaming — Design

Date: 2026-11-21 · Size: project · Records as decision 50 (via `/decision`) · Supersedes unimplemented [`2026-11-18-svelte-ts-sse-web-design.md`](2026-11-18-svelte-ts-sse-web-design.md).

## Intent

Groundly's local study page (`groundly serve` / `groundly start`) currently lives in a 719-line vanilla JavaScript file (`groundly/web/static/page.js`) and a 165-line CSS file (`groundly/web/static/page.css`), tracks headless Claude Code runs by polling `GET /api/runs` every 2 seconds, and uses raw Starlette routing with manual dictionary validation (`groundly/web/app.py`).

Three problems limit the current surface:
1. **Static, coarse UI and brittle DOM code:** Constructing nested views, slide-over panels, quiz state, and run feedback with raw `document.createElement` calls does not scale to a polished, reactive, mobile-friendly study interface with modern UI primitives and canvas/shader feedback.
2. **Stalled run feedback without true streaming:** Without `--include-partial-messages`, `claude -p` produces no stdout lines while thinking or generating long `submit_questions` payloads (15–30 seconds of silence), and 2-second polling over Starlette misses rapid transitions, provides no live token stream, and does not refresh counts or verdicts mid-run as MCP gates write to SQLite.
3. **Untyped HTTP contract:** Manual dict checks in `groundly/web/app.py` lack explicit Pydantic request/response schemas and offline OpenAPI generation needed as the API matures toward future remote/mobile clients.

This design migrates `groundly/web/app.py` to **FastAPI** with strict Pydantic v2 contracts, replaces `page.js`/`page.css` with a **Svelte 5 + TypeScript + Tailwind CSS v4 + `shadcn-svelte`** single-page application compiled via **Vite+ (`vp`)** into committed `groundly/web/static/page.{js,css}`, upgrades headless agent monitoring to **live token-level SSE streaming** (`claude -p --include-partial-messages` + async pub-sub `GET /api/runs/stream` with live mid-run gate refreshes), and delivers a full visual and UX overhaul across all four views (`Subjects`, `Topic Tree`, `Test`, `Result`) plus the elevated live `RunStrip`.

## Goals

1. **FastAPI + strict Pydantic v2 web API (`groundly/web/app.py`, `groundly/web/schemas.py`):**
   - Migrate `create_app()` from raw Starlette to `FastAPI` with `docs_url=None`, `redoc_url=None`, and `openapi_url=None` (zero runtime CDN assets; `create_app().openapi()` still generates the typed OpenAPI spec offline).
   - Define strict Pydantic v2 models (`ConfigDict(strict=True)` plus SQLite 64-bit `quiz.is_id` validation) for requests and responses across all JSON routes.
   - Preserve 100% of existing route paths, HTTP status codes, error payloads (`{"error", "detail"}`), pre-parse `Origin`/`Content-Type`/streaming 256 KiB guards, `405` enforcement, and `/mcp` routing under `groundly start`.
2. **Live token-level SSE streaming (`groundly/web/runs.py`, `GET /api/runs/stream`):**
   - Pass `--include-partial-messages` to `claude -p` alongside `--output-format stream-json --verbose`, and update `Runner._read`'s pre-init guard to fail closed if any `stream_event` arrives before `system/init`.
   - Track live `phase` (`"starting" | "thinking" | "reading" | "writing" | None`), `active_tool` (`str | None`), and rolling current-turn `thought` (`thinking_delta` / `text_delta`, capped at `MAX_STREAM_CHARS = 2000`; `input_json_delta` dropped), while keeping `Steps.feed` strictly derived from completed `tool_use` / `tool_result` blocks only.
   - Bridge `Runner._read` to SSE clients through async per-subscriber `asyncio.Queue`s (`loop.call_soon_threadsafe`, `DELTA_COALESCE_S = 0.04`, `QueueFull` snapshot resync, zero blocked threadpool workers, instant close on `Runner.shutdown()`).
   - Emit `event: snapshot` on connect followed by typed `phase`, `delta`, `step` (tagging gate-mutating writes with `refresh: true`), and `done` frames, closing as soon as a run settles.
3. **Svelte 5 + TypeScript + Vite+ SPA (`web/` -> `groundly/web/static/`):**
   - Build a decoupled client-side SPA using `vite-plus` (`vp dev` proxy in development; `vp build` emitting deterministic, single-file committed `groundly/web/static/page.js` and `groundly/web/static/page.css` so `uv tool install groundly` and `pytest` require zero Node.js tooling).
   - Use module-level Svelte 5 `$state` singletons (`router.svelte.ts`, `theme.svelte.ts`, `run.svelte.ts`) so active SSE streams and run UI survive client-side route transitions across all views.
4. **Full visual + UX overhaul across all 4 views & live `RunStrip`:**
   - Adopt `shadcn-svelte` (`bits-ui`), `lucide-svelte`, and `svelte-ai-elements`-inspired streaming/reasoning primitives, paired with `ThinkingOrb.svelte` (`thinking-orbs/engine`), `BorderBeam.svelte` (copy-owned CSS), and `LiquidMetalButton.svelte` (`@paper-design/shaders` with WebGL and reduced-motion fallbacks).
   - Support light/dark/system themes, mobile-responsive layouts, client-side topic search/filter, live mid-run count/verdict refreshes without closing an open drawer, quiz progress bar + keyboard shortcuts (`1`–`9`, arrow keys), and 100% behavioral parity with decisions 41, 42, 44, 45, 46, and 47.

## Non-goals

- Requiring Node.js, `npm`, or `vp` for end users installing or running Groundly via `uv`, or during `pytest`.
- Adding chat, a free-text prompt box, or new headless run flows (decision 46 remains strictly two chores: `write` and `grade`).
- Adding remote network binding, authentication, TLS, or CORS headers (loopback `127.0.0.1` + fixed `HOSTS` stay unchanged; remote phone access and authentication belong to a separate future sub-project).
- Persisting run history, intermediate thoughts, or partial stream tokens to `store.db` or `progress.db`.
- Using SvelteKit, server-side rendering (SSR), or a separate production frontend server process.

## Anti-goals (a failure even if it works)

- **Raw HTML rendering of Layer-3 data:** Using `{@html ...}`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, or `eval(` anywhere in `web/src/` or `groundly/web/page.html`. Every string returned by the API (titles, summaries, filenames, chunk text, question stems/options/code, review comments, deterministic `steps`, live `thought`, final `summary`, and `error`) is untrusted Layer-3 content and must enter the DOM solely as text nodes.
- **Weakening `script-src`, external-origin blocking, CORS, or `_page_json`:** Adding `'unsafe-inline'` or `'unsafe-eval'` to `script-src`, enabling FastAPI `/docs`/`/redoc` CDN links at runtime, adding external font/asset origins or CORS headers, or letting FastAPI parse bodies before enforcing own `Origin` (`403`), `Content-Type: application/json` (`415`), and the streaming 256 KiB cap (`413`).
- **Mixing model text into `steps`:** `Steps.feed` must never inspect `stream_event` deltas or model text blocks; prompt-injected course content must never be able to forge a deterministic tool-outcome step.
- **Leaking `stream_event` before `system/init`:** `check_init` must fail closed if any `stream_event` arrives before tool and MCP server isolation are verified.
- **Holding Uvicorn shutdown open or blocking worker threads:** `/api/runs/stream` must use async queues (no blocking `run_in_threadpool` waits) and exit immediately when `Runner.shutdown()` fires on `SIGINT`, `SIGTERM`, or `SIGHUP`.

## Constraints

- **Zero-Node Python wheel (`pyproject.toml`):** `packages = ["groundly"]` packages `groundly/web/static/page.js`, `groundly/web/static/page.css`, and `groundly/web/static/claude-code.svg`. Top-level `web/` is excluded from the wheel.
- **No ML/retrieval import in `groundly.web.app`:** `test_importing_web_app_pulls_in_no_ml_or_retrieval` must continue to pass.
- **No `store.db` writes from `groundly/web/`:** The web server opens `store.db` read-only (`PRAGMA data_version` unchanged across reads, quiz draws/submissions, and run triggers); only submitted test attempts write to `progress.db` via `agents/quiz.py`.
- **`405 Method Not Allowed` preservation:** All read routes and non-POST writes (including `/api/runs/stream`, and all page/API routes when `/mcp` is mounted under `groundly start`) continue returning `405` for unsupported HTTP methods.
- **Strict Pydantic v2 parity:** Request schemas enforce `ConfigDict(strict=True)` and SQLite 64-bit signed integer bounds (`quiz.is_id`), preserving exact `400`/`403`/`413`/`415` error codes and `{"error", "detail"}` payloads.
- **Amended `_CSP` on HTML responses only:**
  `default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`
- **Romanian + English glyph coverage:** All corpora include Romanian diacritics (`ă â î ș ț`). Typography uses `system-ui` and `ui-monospace` (zero asset weight, native `latin-ext` glyph coverage, zero external font fetches).

## Approaches considered

| Approach | Shape | Trade-off |
|---|---|---|
| **A — FastAPI + Svelte 5 / Vite+ static SPA + Hybrid async pub-sub token-level SSE (chosen)** | `FastAPI` (`docs_url=None`) serves strict Pydantic API routes + async queue `GET /api/runs/stream` + committed single-file Svelte 5 SPA built by Vite+ (`vp`); `--include-partial-messages` streams live `phase`/`delta`/`step`/`done` frames with `refresh: true` on gate writes | Fine-grained runes update streaming DOM nodes without VDOM diffing, zero React StrictMode canvas/EventSource churn, typed Pydantic/OpenAPI backend ready for future clients, zero blocked worker threads, and zero Node at user install/test time |
| **B — React 19 + `shadcn/ui` + Libraries.dev (`metal-fx`, `border-beam`, `thinking-orbs`)** | Native React runtime + full Libraries.dev catalog | Zero-wrapper access to `metal-fx` cursor-bend and `bot-avatars`, but coarser subtree re-renders at 30–60 chunks/s, imperative `useRef` mirrors for canvas/WebGL/EventSource, and ~200 KB+ runtime; Svelte 5 covers all needed effects (`thinking-orbs/engine`, `@paper-design/shaders`, CSS `BorderBeam`, `shadcn-svelte`, `svelte-ai-elements`) |
| **C — Keep raw Starlette (`groundly/web/app.py` unchanged)** | Minimal diff on `app.py`, manual dict validation | Zero new Python dependency, but defers typed Pydantic route schemas and OpenAPI generation needed for future remote/mobile clients |
| **D — Turn-level snapshot SSE via `threading.Condition` + `run_in_threadpool` (Nov 18 draft)** | No `--include-partial-messages`; emits full snapshot on every turn change | Stalls 15–30 s while a model thinks or composes `submit_questions`, resends full JSON per tick, and holds a threadpool thread per open tab |
| **E — Always-on persistent SSE / Separate production Node frontend server** | Persistent idle `EventSource` or separate Node port with CORS | Idle persistent connections risk the browser's 6-connection HTTP/1.1 per-origin cap; a separate prod frontend breaks zero-Node installs and forces CORS against the CSRF model |

## Design

### 1. Frontend Architecture, Vite+ Toolchain & CSP

```
groundly/
└── web/
    ├── app.py                     # FastAPI app factory, CSP, page_json guard, /api/runs/stream
    ├── schemas.py                 # Strict Pydantic v2 request/response & SSE payload models
    ├── runs.py                    # Runner, StreamTracker, Steps, async subscriber queues
    ├── page.html                  # Minimal shell loading /static/page.css and /static/page.js
    └── static/                    # Committed build outputs (packaged in wheel)
        ├── claude-code.svg
        ├── page.css
        └── page.js
web/                               # Frontend source workspace (dev/build only; excluded from wheel)
├── package.json                   # Svelte 5, TS, Tailwind v4, shadcn-svelte, bits-ui,
│                                  # lucide-svelte, thinking-orbs, @paper-design/shaders, vite-plus
├── tsconfig.json
├── vite.config.ts                 # vp dev proxy + vp build -> ../groundly/web/static/page.{js,css}
└── src/
    ├── main.ts                    # Mounts App.svelte onto #app
    ├── app.css                    # Tailwind v4 + shadcn-svelte light/dark CSS tokens + border-beam CSS
    ├── App.svelte                 # Sticky shell, breadcrumbs, theme toggle, persistent RunStrip, view switch
    └── lib/
        ├── types.ts               # Typed API responses & discriminated SSE event union
        ├── api.ts                 # getJSON, postJSON, api(), pageURL(), testQuery()
        ├── router.svelte.ts       # Module-level $state History API router (4 routes)
        ├── theme.svelte.ts        # light | dark | system ($state + localStorage + matchMedia)
        ├── run.svelte.ts          # Module-level RunStore ($state + on-demand EventSource)
        ├── components/
        │   ├── ui/                # Copy-owned shadcn-svelte primitives (Button, Badge, Card, Input, Select, Dialog)
        │   ├── effects/
        │   │   ├── ThinkingOrb.svelte       # 2D <canvas> wrapper over thinking-orbs/engine
        │   │   ├── BorderBeam.svelte        # Pure-CSS animated border beam for active RunStrip
        │   │   └── LiquidMetalButton.svelte # @paper-design/shaders button + WebGL/motion fallback
        │   ├── Brand.svelte       # Groundly logo (/static/claude-code.svg) + navigation header
        │   ├── MasteryBadge.svelte# Topic progress / mastery pill + progress bar
        │   ├── RunStrip.svelte    # Live orb, phase/tool badge, BorderBeam, live text stream, steps, Stop, outcome
        │   └── SourceItem.svelte  # Deduplicated PDF link (src.pdf === true) or focusable <pre tabindex="0">
        └── views/
            ├── SubjectsView.svelte
            ├── TreeView.svelte
            ├── TestView.svelte
            └── ResultView.svelte
```

- **Vite+ configuration (`web/vite.config.ts`):**
  - **Build:** `outDir: "../groundly/web/static"`, `emptyOutDir: false` (preserves `claude-code.svg`), `cssCodeSplit: false`, Rollup `entryFileNames: "page.js"`, `assetFileNames: "page.[ext]"`, single self-contained bundle (`inlineDynamicImports: true`, `format: "iife"`) so `<script src="/static/page.js">` loads without extra chunk fetches or path assumptions.
  - **Dev server (`vp dev`):** Proxies `/api/*` and `/s/*/materials/*` to `http://127.0.0.1:8765`, rewriting the forwarded `Origin` and `Host` headers to `http://127.0.0.1:8765` / `127.0.0.1:8765` in the dev proxy so `_page_json` and `TrustedHostMiddleware` work cleanly during local HMR without adding CORS headers to Groundly.
- **CSP audit & rationale:**
  - Auditing `thinking-orbs` (`0.3.2`), `bits-ui` (`2.21.0`), and `@paper-design/shaders` (`0.0.82`) confirms zero runtime dependencies and zero external network URLs in runtime code, **but** `bits-ui` floating positioning and `@paper-design/shaders` (`shader-mount.js`) inject inline styles / a `<style>` element at runtime.
  - `_CSP` in `groundly/web/app.py` therefore amends `style-src` to `'self' 'unsafe-inline'` and adds `font-src 'self'`, while keeping `default-src 'none'`, `script-src 'self'`, `img-src 'self'`, `connect-src 'self'`, `base-uri 'none'`, `form-action 'none'`, and `frame-ancestors 'none'` strict.

### 2. FastAPI Backend & Typed Schemas (`groundly/web/app.py`, `groundly/web/schemas.py`)

- **App factory (`create_app`):**
  - Constructs `FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan, middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=HOSTS)])`.
  - Runtime `/docs`, `/redoc`, and `/openapi.json` return `404` (keeping Swagger's external `cdn.jsdelivr.net` scripts/styles completely absent at runtime), while `create_app().openapi()` still returns the full OpenAPI JSON schema offline in dev scripts.
  - Under `groundly start`, appends `Route("/mcp", mcp_app)` after the explicit FastAPI routes (matching the current Starlette `Route("/mcp", mcp_app)` rule so wrong HTTP methods on page/API routes return `405`, never `404`).
- **Route map (unchanged URLs + 1 new SSE route):**

| Method & Path | Handler | Response / Status |
|---|---|---|
| `GET /`, `/s/{subject}`, `/s/{subject}/test`, `/s/{subject}/attempts/{id:int}` | HTML shell | `200` / `404` (`_CSP` header, `page.html`) |
| `GET /static/*` | `StaticFiles` | `page.js`, `page.css`, `claude-code.svg` |
| `GET /s/{subject}/materials/{id:int}` | PDF file | `200 application/pdf` (`nosniff`, no `_CSP`) or `404` |
| `GET /api/subjects` | `_subjects` | `list[SubjectSummary]` |
| `GET /api/subjects/{subject}/tree` | `_tree` | `TreeResponse` |
| `GET /api/subjects/{subject}/test` | `_draw` | `DrawTestResponse` (no answer keys) |
| `POST /api/subjects/{subject}/attempts` | `_submit` | `201 SubmitAttemptResponse` |
| `GET /api/subjects/{subject}/attempts` | `_history` | `list[AttemptSummary]` |
| `GET /api/subjects/{subject}/attempts/{id:int}` | `_result` | `AttemptResultResponse` |
| `GET /api/runs` | `_runs` | `RunsInfoResponse` (`available`, `reason`, `target`, `run`) |
| `GET /api/runs/stream` | `_stream_runs` | `200 text/event-stream` (`405` on `POST`/`PUT`/`PATCH`/`DELETE`) |
| `POST /api/subjects/{subject}/runs` | `_start_run` | `202 RunSnapshot` |
| `POST /api/runs/stop` | `_stop_run` | `200 StopRunResponse` (`{"run": RunSnapshot \| None}`) |

- **Strict validation & security guard order (`page_json` dependency):**
  - Instead of letting FastAPI buffer and coerce request bodies automatically, write routes inject `body = Depends(_require_page_json(forbidden_msg))` which executes strictly in this order:
    1. Check `Origin == f"{request.url.scheme}://{request.headers.get('host')}"` → `403 {"error": "forbidden", "detail": ...}`
    2. Check media type == `"application/json"` → `415 {"error": "unsupported", "detail": "the body is JSON"}`
    3. Stream body with `MAX_BODY_BYTES = 256 * 1024` cap → `413 {"error": "too_large", "detail": "the body is larger than a test can be"}`
    4. Decode via `json.loads` catching `(ValueError, RecursionError)` → `400 {"error": "invalid_body", "detail": "the body is not valid JSON"}`
  - Handlers then validate via strict Pydantic v2 models (`ConfigDict(strict=True)`):
    - `SubmitAttemptRequest`: `token` matching `[A-Za-z0-9-]{8,64}`, `topic_id: int | None = None` validated with `quiz.is_id`, `answers: list[dict]`. Failures map to `400 {"error": "invalid_body", "detail": "expected {token, topic_id, answers}"}`.
    - `StartRunRequest`: discriminated union or validator accepting strictly `{"kind": "grade"}` or `{"kind": "write", "topic_id": int}` where `quiz.is_id(topic_id)` holds (rejecting `"1"`, `True`, `2**70`). Failures map to `400 {"error": "invalid_body", "detail": 'expected {kind: "write", topic_id} or {kind: "grade"}'}`.
    - `StopRunRequest`: `dict` (empty JSON object allowed; non-dict fails `400`).
  - A custom `@app.exception_handler(RequestValidationError)` maps query/path validation errors (such as `?n=five` on `/api/subjects/{subject}/test`) to `400 {"error": "invalid_text", "detail": ...}` rather than FastAPI's default `422`.
  - Path `id` on `/api/subjects/{subject}/attempts/{id:int}` uses Starlette's `:int` converter and checks `quiz.is_id(attempt_id)` inside `_result` so `2**70` still returns `404 {"error": "unknown_attempt", "detail": "no such attempt"}`.

### 3. Live Partial Streaming & Async Pub-Sub SSE (`groundly/web/runs.py` & `GET /api/runs/stream`)

#### Subprocess Flags & Pre-Init Isolation
- `build_argv` adds `"--include-partial-messages"` after `"--verbose"` so `claude -p` emits incremental `stream_event` JSON lines while still producing completed `assistant`, `user`, and `result` lines.
- `Runner._read` extends the pre-init fail-closed check: if `"stream_event"` arrives alongside `assistant`, `user`, or `result` before a passing `system/init` event, the runner immediately fails with `isolation_failed` and kills the process group.

#### Run Snapshot Shape (In-Memory Only)
```python
MAX_STEPS = 50
MAX_SUMMARY_CHARS = 2000
MAX_STREAM_CHARS = 2000
DELTA_COALESCE_S = 0.04
SUBSCRIBER_QUEUE_MAX = 256
SSE_KEEPALIVE_S = 15.0

run = {
    "id": self._next_id,
    "kind": kind,              # "write" | "grade"
    "subject": subject,
    "topic_id": topic_id,      # int | None
    "status": "running",       # "running" | "done" | "failed" | "stopped"
    "phase": "starting",       # "starting" | "thinking" | "reading" | "writing" | None
    "active_tool": None,       # str | None (plain label for tool currently generating/running)
    "thought": "",             # str (rolling current-turn plain text, tail-capped at MAX_STREAM_CHARS)
    "thought_channel": None,   # "thinking" | "text" | None
    "steps": [],               # list[str] from Steps.feed only (up to MAX_STEPS)
    "summary": None,           # str | None (up to MAX_SUMMARY_CHARS)
    "error": None,             # {"reason": str, "detail": str} | None
    "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
}
```

#### Tool-Step Isolation (`Steps`) vs. Live Stream Parsing (`StreamTracker`)
- **`Steps` (strictly tool-derived):** Continues reading *only* completed `assistant` (`tool_use`) and `user` (`tool_result`) messages — never `stream_event` and never model text. `Steps.feed(event)` returns `(new_steps: list[str], refresh: bool)`, setting `refresh = True` whenever a `submit_questions` result has `accepted` count ≥ 1 or a `review_answers` result has `recorded` count ≥ 1.
- **`StreamTracker` (live phase & rolling thought):** Maps incoming events to zero or more internal UI events:
  1. `system/init` passes `check_init`: sets `phase = "thinking"`, `active_tool = None`.
  2. `stream_event` (`inner = event.get("event") or {}`):
     - `content_block_start`:
       - `content_block.type in ("thinking", "text")`: switches `phase = "thinking"`, `active_tool = None`, resets current-turn `thought = ""`, sets `thought_channel = block_type`, emits `reset=True` delta marker.
       - `content_block.type == "tool_use"`: strips `"mcp__groundly__"` from `content_block.get("name")`:
         - `"submit_questions"` → `phase = "writing"`, `active_tool = "Drafting questions"`
         - `"review_answers"` → starts as `phase = "reading"`, `active_tool = "Reviewing answers"` (because tool input JSON has not arrived yet at `content_block_start`, and upgrades to `phase = "writing"` when the completed `assistant` event carries non-empty `input.reviews`)
         - `"search"`, `"get_page"`, `"topic"`, `"list_subjects"`, `"progress"` → `phase = "reading"`, `active_tool = _USE_WORDS.get(name)`
     - `content_block_delta`:
       - `delta.type == "thinking_delta"` (`delta.thinking`) or `"text_delta"` (`delta.text`): appends raw text (without normalizing whitespace mid-stream so spacing/newlines stream naturally), truncating from the left to keep `len(run["thought"]) <= MAX_STREAM_CHARS`, and marks `saw_stream_for_turn = True`.
       - `delta.type == "input_json_delta"`: **ignored / dropped** (raw tool JSON args never leave the backend stream; `phase` and `active_tool` already communicate what the agent is writing).
  3. Completed `assistant` event (handles both live runs and `FakeClaude` test scripts that emit `assistant` without `stream_event`):
     - Always feeds `Steps`.
     - If `saw_stream_for_turn` is `False`, inspects `message.content` blocks (`thinking`, `text`, `tool_use`) as a fallback to update `phase`, `active_tool`, and `thought` (capped at `MAX_STREAM_CHARS`). Resets `saw_stream_for_turn = False` once handled.
     - For `"review_answers"`, inspects `input.reviews` to refine `phase` (`"writing"` when `reviews` is non-empty, else `"reading"`).
  4. Completed `user` event with any `tool_result`:
     - Feeds `Steps` (`refresh = True` when gate-mutating writes succeeded).
     - Transitions `phase = "thinking"`, `active_tool = None`.
  5. Settlement (`result`, `_end`, or exit):
     - Sets `phase = None`, `active_tool = None`, `thought = ""`, `thought_channel = None`, sets `status` (`"done"`, `"failed"`, or `"stopped"`) and `summary` / `error`.

#### Async Subscriber Queues & SSE Wire Format (`GET /api/runs/stream`)
- `Runner` maintains `self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[tuple[str, dict | None] | None]]] = set()`.
- `Runner.subscribe(loop: asyncio.AbstractEventLoop) -> tuple[dict | None, asyncio.Queue]`:
  - Under `self._lock`, takes an atomic `snapshot = self._snapshot()`, and **only if** `snapshot is not None and snapshot["status"] == "running"` registers `(loop, queue)` (`maxsize=SUBSCRIBER_QUEUE_MAX`). Otherwise no queue needs subscribing.
- `_publish(event_name: str, payload: dict | None)`:
  - Iterates `list(self._subscribers)` and calls `loop.call_soon_threadsafe(self._enqueue, loop, queue, event_name, payload)`.
  - `_enqueue` drops closed loops cleanly; if `queue.full()`, drains queued items and puts `("snapshot", payload_snapshot)` (or if still full, forces resync on next flush) so `Runner._read` never blocks or allocates unboundedly.
- **40 ms delta coalescing:** `Runner._read` buffers rapid `delta` chunks per channel and flushes them to subscribers every `DELTA_COALESCE_S = 0.04` seconds, or immediately before any `phase`, `step`, or `done` event so phase/step ordering stays strictly causal.
- **`Runner.shutdown()` & `_end()`:** Pushes `None` sentinel to all active subscriber queues on settlement / shutdown (after pushing `("done", snapshot)`), then kills the `claude` process group. Active `/api/runs/stream` handlers exit immediately on receiving `None`.
- **`GET /api/runs/stream` response headers:** `media_type="text/event-stream"`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
- **Wire events emitted by `GET /api/runs/stream`:**
  1. Immediately on connect: `event: snapshot\ndata: <RunSnapshot JSON | null>\n\n`. If `snapshot is None` or `snapshot["status"] != "running"`, closes the stream immediately after this frame (cleanly resolves the fast-run race where a run finishes between `POST .../runs` and opening `EventSource`).
  2. While subscribed, waits with `asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_S)`:
     - On `TimeoutError`: emits `: keep-alive\n\n` (if `await request.is_disconnected()`, unsubscribes and exits).
     - On `(event_name, data)`:
       - `event: phase\ndata: {"id": int, "phase": str | null, "active_tool": str | null}\n\n`
       - `event: delta\ndata: {"id": int, "channel": "thinking" | "text", "text": str, "reset": bool}\n\n`
       - `event: step\ndata: {"id": int, "step": str, "refresh": bool}\n\n`
       - `event: done\ndata: <RunSnapshot JSON>\n\n` → breaks and closes stream.
       - `None` sentinel → breaks and closes stream cleanly.

### 4. Client State, Visual Effects & `RunStrip`

- **`router.svelte.ts`:** Module-level `$state` pathname + search parser supporting `/`, `/s/{subject}`, `/s/{subject}/test`, `/s/{subject}/attempts/{id}` (with a clean not-found fallback linking back to `/` for unmatched client paths). Intercepts same-origin `<a>` clicks (skipping `target="_blank"`, `download`, modifier clicks, and `/s/*/materials/*` PDF links) with `history.pushState` and listens to `popstate`.
- **`theme.svelte.ts`:** Manages `"system" | "light" | "dark"` persisted in `localStorage` (`"groundly-theme"`), applies `.dark` on `document.documentElement`, and reacts to `window.matchMedia("(prefers-color-scheme: dark)")` so Tailwind tokens, `ThinkingOrb`, and `@paper-design/shaders` stay aligned.
- **`run.svelte.ts` (`RunStore`, module-level `$state` singleton):**
  - Holds `available`, `reason`, `target`, `run`, `watched`, `starting`, `message`, `dismissed`, plus callbacks (`onRefresh`, `onEnd`).
  - `attach()` fetches `GET /api/runs`; if `info.run?.status === "running"`, sets `watched = info.run.id` and opens `EventSource("/api/runs/stream")`.
  - `start(subject, body)` posts to `/api/subjects/{subject}/runs` (`202`), sets `watched = run.id`, and opens `EventSource("/api/runs/stream")`. On `409` (`run_in_progress`), sets the error notice and calls `attach()` so the active run and **Stop** button render immediately.
  - Listeners on `EventSource`:
    - `snapshot`: replaces `run`; if `watched` matches and `run === null` or `run.status !== "running"`, closes `EventSource` and triggers `onEnd` + outcome status message.
    - `phase`: updates `run.phase` and `run.active_tool`.
    - `delta`: if `reset`, replaces `run.thought = text`; else appends `text` and slices tail to `2000` chars; updates `run.thought_channel`.
    - `step`: appends `step` to `run.steps` (keeping last 50); if `refresh === true`, invokes `onRefresh()` so tree counts or result verdicts update immediately while the run continues.
    - `done`: updates `run` with final `RunSnapshot`, closes `EventSource`, invokes `onEnd()`, and updates `<p class="outcome" role="status">`.
    - `onerror`: closes `EventSource`, probes `GET /api/runs` once (prevents infinite browser reconnect loops if uvicorn restarted), and either reconciles the settled run or announces `"Lost the page server — reload the page when it is back."`.
- **Visual effect components:**
  - **`ThinkingOrb.svelte`:** Wraps `thinking-orbs/engine` (`MODE_FRAMES`, `paintFrame`, `resolvePreset`) on a `<canvas role="img" aria-label={label}>`. Maps `(phase, kind)` → `"connecting"` (`starting`), `"searching"` (`reading`), `"composing"` (`writing` + `write`), `"solving"` (`writing` + `grade`), `"working"` (`thinking`). Pauses animation when offscreen (`IntersectionObserver`) or hidden (`document.visibilityState`), and paints a static frame when `prefers-reduced-motion: reduce` matches.
  - **`BorderBeam.svelte`:** Pure-CSS animated conic/gradient border highlight in `app.css` around the active `RunStrip` card while `status === "running"`, disabled under `prefers-reduced-motion: reduce`.
  - **`LiquidMetalButton.svelte`:** Accessible `<button type="button">` layering a `@paper-design/shaders` `ShaderMount` (`liquidMetalFragmentShader`) beneath crisp foreground text/icon for primary agent actions (**Write questions**, **Write questions for its subtopics**, **Grade now**). Cleanly disposes `ShaderMount` on unmount and falls back to a styled Tailwind button when WebGL initialization fails or `prefers-reduced-motion: reduce` is active.
- **`RunStrip.svelte`:** Persistent elevated card docked in `App.svelte`. Shows `<ThinkingOrb>`, heading (`"Writing questions · <subject>"` / `"Grading written answers · <subject>"`), phase/active-tool badge, **Stop** button, live auto-scrolling streaming box showing `{run.thought}` as plain text with a subtle `Thinking` vs `Response` label, the last 6 deterministic `steps` in `<ol class="steps">`, and `<p class="outcome" role="status">` (`run.summary || "Done."`, `"Stopped. What was already stored stays."`, or `"Failed: " + error.detail`). Dismissible once settled.

### 5. 4-View Overhaul & Preserved Behavioral Contracts

All views use `shadcn-svelte` primitives, responsive Tailwind layouts, Svelte transitions, and strict text-only rendering:

- **`App.svelte`:** Sticky header with Groundly mark (`/static/claude-code.svg`), contextual breadcrumbs, theme switcher, persistent `RunStrip`, and route outlet.
- **`SubjectsView.svelte` (`/`):** Clean grid of subject cards (`Card` + `Badge` showing `"Topic map ready"`, `"No map yet"`, or error string, showing strict `subject` ID alongside `title` when `title` is set), plus empty-state guidance (`groundly index`).
- **`TreeView.svelte` (`/s/{subject}`):**
  - Header showing `title || subject` (plus strict `subject` ID underneath whenever `title` is set), `"Test the whole course"` CTA when any topic holds questions, and a client-side topic search/filter input (filtering big topics and matching subtopic chips without hiding structure awkwardly).
  - Two-level spine (`role="list"`) and subtopic chips (`role="list"`, `aria-label="Subtopics of {topic.title}"`), mastery progress bars/badges (`MasteryBadge.svelte`), dashed border + `"sources removed"` flag when `node.sources.length === 0`.
  - **Slide-over detail drawer (`<aside class="panel" aria-label="Topic details">`):** Sticky panel on desktop, bottom sheet on mobile (`<= 700px`). Opening moves focus to the panel `<h2>` (`tabIndex = -1`), and `Escape` or `Close` returns focus to the triggering topic button. Preserves open `selectedTopicId` across mid-run `refresh` fetches so live question counts increment in place while the drawer stays open.
  - Test length `<select>` (`5`, `10`, `20`, disabling lengths above available count just as today), `<LiquidMetalButton>` **Write questions** / **Write questions for its subtopics** when `host.available` and any subtopic in scope has `< host.target` questions (or fallback `"Say to your agent: "` + `<code>Write questions on "{title}" for {subject}.</code>` when count is 0 and unavailable), deduplicated PDF links (`src.pdf === true`, `target="_blank"`, `rel="noopener"`) vs focusable `<pre tabindex="0" aria-label="Text from {label}">`, and past tests list.
- **`TestView.svelte` (`/s/{subject}/test`):**
  - Distraction-free test stage with top progress bar, question navigation pills (`aria-current="step"`, answered state badge), keyboard shortcuts (`1`–`9` to select options when focus is not inside `<textarea>`, `←`/`→` for Back/Next), and crisp `<pre class="code">` blocks rendered strictly as text.
  - Preserves exact `sessionStorage` contract (`"groundly-test:" + subject + ":" + (topicId == null ? "all" : topicId)`), `crypto.randomUUID()` token, shuffled options on initial draw, `$state.snapshot()` before JSON serialization, `role="alertdialog"` confirmation when unanswered questions remain, and `dead = true` + `dropTest(key)` on `"unknown_question"` refusal (`function store() { if (!dead) saveTest(key, test); }`).
- **`ResultView.svelte` (`/s/{subject}/attempts/{id}`):**
  - Prominent score summary card (`"{correct} / {graded} correct"`), provisional banner with `<LiquidMetalButton>` **Grade now** when `result.provisional && host.available`, color-coded verdict cards (`state-{a.state}`) with collapsible cited sources (`SourceItem`), fallback `"Awaiting review — say "` + `<code>grade my test</code>` + `" to your agent."` on awaiting open cards when `!canGrade`, live mid-run verdict refresh as `review_answers` records verdicts, and per-topic mastery summary list.

### 6. Verification & Testing Strategy

- **`tests/web/test_runs.py` (no Node, `FakeClaude` subprocesses):**
  - Assert `--include-partial-messages` in `build_argv`.
  - Assert `stream_event` before `system/init` fails closed (`isolation_failed`).
  - Test `StreamTracker` over partial `thinking_delta`, `text_delta`, `content_block_start(tool_use)` (`reading`/`writing`), `input_json_delta` (dropped), `MAX_STREAM_CHARS = 2000` tail capping, turn reset on new content block, fallback non-partial `assistant` events, and clearing on settlement.
  - Assert `Steps.feed` continues ignoring model text and deltas, and sets `refresh=True` only when `submit_questions` stores ≥ 1 question or `review_answers` records ≥ 1 verdict.
  - Test async `Runner.subscribe` fan-out, delta coalescing, `QueueFull` snapshot resync, and immediate `None` sentinel on `Runner.shutdown()`.
- **`tests/web/test_app.py` (FastAPI `TestClient`, no Node):**
  - Run all existing HTTP security, `Host` header, `Origin` check (`403`), `Content-Type` (`415`), streaming 256 KiB cap (`413`), strict integer / `2**70` (`400`/`404`), `PRAGMA data_version` read-only `store.db`, `405` preservation, and `/mcp` mount tests against the FastAPI app.
  - Assert `/docs`, `/redoc`, and `/openapi.json` return `404` at runtime while `create_app().openapi()` succeeds offline.
  - Assert updated `_CSP` (`script-src 'self'` without `'unsafe-inline'` in the `script-src` directive, `style-src 'self' 'unsafe-inline'`, `font-src 'self'`, `default-src 'none'`).
  - Test `GET /api/runs/stream`: initial `snapshot`, live `phase`/`delta`/`step`/`done` sequence with `FakeClaude`, fast run already settled before stream opens (emits `snapshot` and closes), `405` on `POST`/`PUT`/`PATCH`/`DELETE`, and clean stream exit on `Runner.shutdown()`.
- **`tests/cli/test_cli_serve.py` (no Node required):**
  - Split static assertions into `AUTHORED_SOURCE` and `SHIPPED_BUNDLE` because Svelte 5's compiled runtime uses `innerHTML` internally on static compiled component templates and embeds standard W3C XML namespace literals (`http://www.w3.org/2000/svg`), whereas authored source must never touch HTML sinks or external URLs:
    - **`AUTHORED_SOURCE`** (`groundly/web/page.html` + all `*.ts`, `*.svelte`, `*.css` under `web/src/`): bans `"innerHTML"`, `"outerHTML"`, `"insertAdjacentHTML"`, `"document.write"`, `"eval("`, `"{@html"`, `"http://"`, `"https://"`, `" onclick="`, `'.endsWith(".pdf")'`, and `"verified"` (case-insensitive); pins `"src.pdf === true"`, `"sessionStorage"`, `"crypto.randomUUID()"`, `'"unknown_question"'`, `"function store() { if (!dead) saveTest(key, test); }"`, `'"Write questions on \\"" + title + "\\" for "'`, `"/api/runs"`, `'"/runs"'`, `"/api/runs/stream"`, `"/api/runs/stop"`, `"Write questions for its subtopics"`, `"Grade now"`, `"Say to your agent"`, and `"grade my test"`.
    - **`SHIPPED_BUNDLE`** (`groundly/web/page.html` + `groundly/web/static/page.js` + `groundly/web/static/page.css`): asserts non-empty `page.js`/`page.css`, bans `"verified"` (case-insensitive) and non-W3C `http://`/`https://` strings (after stripping standard W3C XML namespaces `http://www.w3.org/2000/svg`, `http://www.w3.org/1999/xhtml`, `http://www.w3.org/1998/Math/MathML`), and checks key API/UI markers (`"/api/runs/stream"`, `"/api/runs/stop"`, `"unknown_question"`, `"sessionStorage"`, `"randomUUID"`, `"Grade now"`, `"grade my test"`) so a stale bundle fails `pytest`.
- **Frontend verification (`web/`, Vite+ toolchain):**
  - `vp check` / `svelte-check` (TypeScript + Svelte 5 diagnostics + lint/format).
  - `vp test` (Vitest unit tests for `RunStore` SSE state machine, delta capping, `409` attach, `router.svelte.ts`, and `TestView` `dead = true` guard).
  - `vp build` producing fresh `groundly/web/static/page.js` and `groundly/web/static/page.css`, followed by `uv run pytest`, `uv run ruff check`, `uv run pyrefly check`, and `uv run lint-imports`.

## Docs and decision (same change set)

- **Decision 50** in `docs/groundly-spec.md` §4 (Topic-tree page row) and §7 (Decision register): records migrating `groundly/web/app.py` to FastAPI (amending decision 41's raw-Starlette framing while keeping FastMCP as the sole tool-surface framework), moving the study UI to a Vite+ / Svelte 5 / TypeScript / Tailwind v4 (`shadcn-svelte`, `thinking-orbs/engine`, `@paper-design/shaders`) SPA compiling to committed static assets, upgrading headless page runs to `--include-partial-messages` live token-level SSE (`GET /api/runs/stream`) with live mid-run gate refreshes, and amending `_CSP` `style-src` to `'self' 'unsafe-inline'` + `font-src 'self'`.
- **Satellite docs:**
  - `docs/tech-stack/tech-stack.md`: updates the Topic-tree page row to FastAPI + uvicorn + Svelte 5 / TypeScript / Tailwind v4 compiled via Vite+ into `groundly/web/static/page.{js,css}` (zero Node at install/runtime).
  - `docs/architecture/overview.md`: updates `web/` description, runtime modes table, and `groundly/` tree comment for FastAPI, Svelte SPA, and SSE streaming (`/api/runs/stream`).
  - `docs/infrastructure/security.md`: updates §4 (Local servers / Page runs) for FastAPI (`/docs`/`/redoc`/`/openapi.json` disabled, strict Pydantic after `_page_json`), `GET /api/runs/stream`, pre-init rejection of `stream_event`, `MAX_STREAM_CHARS = 2000` capping of live `thought` separate from tool-only `steps`, and the `_CSP` `style-src 'self' 'unsafe-inline'` + `font-src 'self'` update with text-node-only guarantees.
  - `.claude/rules/architecture.md` & `.claude/rules/conventions.md`: update references from Starlette-only/single-HTML-file wording to FastAPI + committed Svelte 5 SPA + SSE stream while keeping FastMCP as the sole MCP tool-surface framework.

## Security (threat model check)

- **Amended `style-src 'self' 'unsafe-inline'` + `font-src 'self'`:** Required by `bits-ui` positioning and `@paper-design/shaders`. Because `script-src 'self'` (zero `'unsafe-inline'`/`'unsafe-eval'`), `default-src 'none'`, `img-src 'self'`, `connect-src 'self'`, and text-only Svelte rendering (`{@html}` and DOM HTML sinks banned by tests) all remain intact, neither arbitrary script execution nor external CSS exfiltration (`url(https://...)`) is possible.
- **Untrusted Layer-3 streaming text (`thought`):** Model `thinking_delta` and `text_delta` streams may echo injected PDF or imported bundle text. Capping `thought` at 2000 characters, dropping `input_json_delta` completely, isolating `Steps.feed` to tool names and gate counts only, and rendering `{run.thought}` strictly as Svelte text nodes prevents XSS and prevents forged step lines.
- **Pre-init `stream_event` guard:** Failing closed on any `stream_event` before `system/init` preserves decision 46's isolation guarantee even with `--include-partial-messages` enabled.
- **FastAPI security parity:** Disabling `/docs`, `/redoc`, and `/openapi.json` prevents external CDN requests; enforcing `_page_json` (`Origin` -> `Content-Type` -> streaming 256 KiB cap -> strict Pydantic) prevents cross-origin run triggers and unbounded body buffering.
- **SSE surface exposure:** `GET /api/runs/stream` binds strictly to `127.0.0.1`, enforces `TrustedHostMiddleware` (`HOSTS`), returns zero CORS headers, accepts `GET` only (`405` on writes), starts no process, and caps per-subscriber queue memory.

## Acceptance criteria

1. `groundly/web/app.py` runs on `FastAPI` with `docs_url=None`, `redoc_url=None`, and `openapi_url=None` (each returning `404` at HTTP runtime while `create_app().openapi()` succeeds offline), `test_importing_web_app_pulls_in_no_ml_or_retrieval` passes, and every existing test in `tests/web/test_app.py` (except the intentional `_CSP` `style-src` / `font-src` update) passes with identical `400`/`403`/`404`/`405`/`413`/`415` behavior.
2. `build_argv` passes `--include-partial-messages`, and `Runner._read` fails closed with `isolation_failed` if a `stream_event` arrives before `system/init`.
3. `StreamTracker` emits live `phase` (`"starting"` → `"thinking"` ↔ `"reading"` / `"writing"` → `None`), `active_tool`, and tail-capped `thought` (`MAX_STREAM_CHARS = 2000`) from `thinking_delta` and `text_delta` events (dropping `input_json_delta`), falls back cleanly on non-partial `assistant` events, and clears `thought` on settlement while `Steps.feed` continues reading only completed `tool_use` / `tool_result` blocks and sets `refresh=True` only on non-empty gate writes.
4. `GET /api/runs/stream` returns `text/event-stream`, immediately emits `event: snapshot`, streams `phase`/`delta`/`step`/`done` frames via async subscriber queues (without blocking AnyIO threadpool workers), closes immediately on settled or idle snapshots, closes immediately when `Runner.shutdown()` runs, and returns `405` on `POST`/`PUT`/`PATCH`/`DELETE`.
5. Running `vp check`, `vp test`, and `vp build` in `web/` passes type checking, Svelte diagnostics, and unit tests, and outputs self-contained `groundly/web/static/page.js` and `groundly/web/static/page.css` without touching `claude-code.svg`.
6. `RunStrip.svelte` stays persistent across client-side routes, wraps active runs in `BorderBeam.svelte`, animates `ThinkingOrb.svelte` (`thinking-orbs/engine`) mapped to `(phase, kind)` (honoring `prefers-reduced-motion` and tab/intersection visibility), streams live `thought` strictly as text, renders the last 6 deterministic `steps`, supports **Stop**, and announces the settled outcome in `role="status"`.
7. `LiquidMetalButton.svelte` renders **Write questions**, **Write questions for its subtopics**, and **Grade now** with `@paper-design/shaders` and degrades cleanly to a styled accessible button when WebGL is unavailable or `prefers-reduced-motion: reduce` is set.
8. Live mid-run refreshes (`refresh: true`) update topic question counts in `TreeView` (keeping any open drawer open and focused) and verdict cards in `ResultView` while a run is still in progress.
9. All 4 views (`SubjectsView`, `TreeView`, `TestView`, `ResultView`) preserve every behavioral and accessibility contract (theme toggle, mobile sheet, Escape focus return, `sessionStorage` + `$state.snapshot()`, `randomUUID`, `"unknown_question"` `dead = true` guard, `src.pdf === true`, fallback agent phrases, keyboard shortcuts, and Romanian `latin-ext` rendering).
10. `uv run pytest`, `uv run ruff check`, `uv run pyrefly check`, and `uv run lint-imports` all pass with zero Node.js execution during `pytest`, and decision 50 + all satellite docs are updated in the same implementation change set.
