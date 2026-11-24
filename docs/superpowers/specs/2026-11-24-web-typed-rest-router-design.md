# Pure-Svelte Typed REST Client, Query Cache & Router Upgrade — Design

Date: 2026-11-24 · Size: project · Records as decision 51 (amending decision 50, via `/decision`) · Sub-project 1 of 2 (Sub-project 2: laptop-hosted remote toggle + auth).

## Intent

Decision 50 migrated `groundly/web/` to FastAPI with strict Pydantic v2 schemas and replaced the vanilla frontend with a Vite+ / Svelte 5 SPA compiled into committed `groundly/web/static/page.{js,css}`. Looking ahead to a growing REST surface and a future **single-user, laptop-hosted remote mode** (outbound tunnel / Tailscale toggle + QR pairing serving both a browser/PWA and a future mobile app against the same FastAPI API), three structural weaknesses remain in `web/`:

1. **Manual API contract duplication:** `web/src/lib/types.ts` (176 lines) hand-mirrors `groundly/web/schemas.py`, and `web/src/lib/api.ts` builds string-concatenated URLs over raw `fetch` with no middleware hook for auth headers, `401` handling, or request cancellation.
2. **Repeated per-view fetch & refresh plumbing:** All four views (`SubjectsView`, `TreeView`, `TestView`, `ResultView`) hand-roll `$effect` + `loadSeq` race guards, duplicate `/tree` fetches on navigation (`TreeView` → `TestView` → `ResultView`), and register imperative `runStore.setHooks({ onRefresh, onEnd })` callbacks for mid-run SSE updates.
3. **Ad-hoc client routing:** `web/src/lib/router.svelte.ts` (~160 lines) manually splits pathnames, parses query strings, and intercepts clicks — lacking typed route params, reactive search params, and a clean `beforeLoad` guard slot for future remote pairing/auth.

**Why pure Svelte 5 (no SvelteKit):** Under the laptop-hosted model (zero Node.js runtime on the student's machine, single Python package via `uv tool install groundly`), SvelteKit cannot run its server features (`+page.server.ts`, `hooks.server.ts`, form actions, BFF proxying). Running SvelteKit in `adapter-static` SPA mode adds multi-file hashed `_app/immutable/` git churn and an inline bootstrap script that clashes with Groundly's strict `script-src 'self'` CSP — without solving FastAPI REST typing or client caching. Adopting **`openapi-typescript` + `openapi-fetch`**, **`@tanstack/svelte-query` v6**, and **`sv-router`** in the existing Vite+ Svelte 5 workspace solves routing, typed REST contracts, caching, and remote-auth readiness directly while preserving the single-file zero-Node bundle.

## Goals

1. **Offline OpenAPI contract pipeline (`groundly/web/` → `web/openapi.json` → `web/src/lib/api/schema.d.ts`):**
   - Exclude non-API HTML and PDF material routes (`/`, `/s/{subject}`, `/s/{subject}/test`, `/s/{subject}/attempts/{id}`, `/s/{subject}/materials/{id}`) from the FastAPI OpenAPI schema (`include_in_schema=False`) so `create_app().openapi()` describes only the `/api/*` REST surface.
   - Add a shared `ErrorResponse` Pydantic model (`error: str`, `detail: str | None = None`) and declare error response schemas on API routes so error payloads are typed in OpenAPI.
   - Add a deterministic Python exporter (`groundly/web/openapi.py`, runnable via `uv run python -m groundly.web.openapi`) that writes `web/openapi.json` (`sort_keys=True`, 2-space indent, trailing newline).
   - Generate `web/src/lib/api/schema.d.ts` via `openapi-typescript` (compile-time only, 0 bytes runtime) and delete hand-mirrored API response/request types from `web/src/lib/types.ts`.
2. **Typed REST client & auth-ready middleware (`web/src/lib/api/client.ts`):**
   - Replace `getJSON`/`postJSON` and string-concatenated API URL helpers with `openapi-fetch` typed against `paths` from `schema.d.ts`.
   - Normalize non-OK and error-body responses through a single `unwrap()` helper throwing `ApiError(message, status, code, detail)`.
   - Register one central `openapi-fetch` middleware with pluggable `configureClient({ baseUrl, getAuthHeaders, onUnauthorized })` hooks (defaulting to same-origin `baseUrl: ""`, `credentials: "same-origin"`, no auth headers, and no-op `onUnauthorized`).
3. **Reactive query cache & SSE-driven invalidation (`web/src/lib/api/queries.ts`, `web/src/lib/run.svelte.ts`):**
   - Adopt `@tanstack/svelte-query` v6 (native Svelte 5 runes thunk API) with a centralized `queryKeys` factory and `QueryClient` provided in `App.svelte`.
   - Migrate `SubjectsView`, `TreeView`, `ResultView`, and the `tree` lookup in `TestView` to `createQuery`, and `TestView` attempt submission to `createMutation`.
   - Keep `GET /api/subjects/{subject}/test` (randomized test draw) as an imperative typed `apiClient.GET` call with `AbortController` inside `TestView`'s `sessionStorage`-backed initializer so focus/reconnect never redraws an active test.
   - Replace `RunStore`'s per-view `setHooks({ onRefresh, onEnd })` with `queryClient.invalidateQueries({ queryKey: queryKeys.subject(subject) })` on SSE `step` (`refresh: true`) and `done` events, keeping open topic drawers intact while stale data stays visible during background refetch.
   - Route `RunStore`'s SSE connection through a thin `createEventStream(url)` transport helper so Sub-project 2 can add cookie or `fetch`-based Bearer SSE without modifying `RunStore` state transitions.
4. **Type-safe pure-Svelte routing (`web/src/lib/router.ts`):**
   - Replace `web/src/lib/router.svelte.ts` with code-based `sv-router` routes (`/`, `/s/:subject`, `/s/:subject/test`, `/s/:subject/attempts/:id`, `*`) and reactive search-param handling for `topic` and `n`.
   - Provide a single router-level `beforeLoad` guard slot (no-op in Sub-project 1) ready for Sub-project 2's remote `/pair` auth redirect.
   - Ensure stored PDF links (`/s/:subject/materials/:id`) remain non-SPA browser navigations (`target="_blank"`) never intercepted by the router.
5. **Decoupled CI drift checks:**
   - Python-only `server` CI checks that `create_app().openapi()` equals committed `web/openapi.json` (zero Python/Node coupling).
   - Node-only `web` CI checks that `pnpm run gen:api` produces no diff on `web/src/lib/api/schema.d.ts` and `pnpm run build` produces no diff on `groundly/web/static/`.

## Non-goals

- Implementing remote tunnel/Tailscale control, QR pairing endpoints, session cookies, Bearer token verification, or CORS on FastAPI (all deferred to Sub-project 2).
- Migrating to SvelteKit, adding SSR, or introducing a Node.js runtime process on the student's laptop.
- Redesigning the visual layout, components, theme system, or `RunStrip` UI.
- Changing any existing `/api/*` URL paths, HTTP status codes, JSON payload shapes, or pre-parse `_require_page_json` security rules.
- Persisting client query caches across full page reloads (e.g., IndexedDB persistence).

## Anti-goals (a failure even if it works)

- **Enabling runtime OpenAPI endpoints:** `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` must remain unchanged so `/docs`, `/redoc`, and `/openapi.json` continue returning `404` at HTTP runtime.
- **Weakening `_CSP` or shipping external URLs in `page.js`:** `script-src 'self'` must not gain `'unsafe-inline'` or `'unsafe-eval'`, and the compiled `groundly/web/static/page.js` bundle must contain zero runtime `http://` or `https://` URLs.
- **Auto-redrawing an in-progress test:** `GET /api/subjects/{subject}/test` must never be placed in an auto-refetching `createQuery` that overwrites or redraws a student's active `sessionStorage` test on tab focus or reconnect.
- **Breaking `unknown_question` dead-session handling:** A failed test submission returning `error: "unknown_question"` must still call `persister.markDead()` and drop the stale `sessionStorage` entry so it is never re-saved.
- **Introducing Node.js into `pytest` or Python into the `web` CI job:** Both jobs and local `uv run pytest` must remain strictly independent.
- **Breaking zero-ML imports in `groundly.web`:** `groundly/web/openapi.py` and `groundly/web/app.py` must never import ML, retrieval, or ingestion modules (`test_importing_web_app_pulls_in_no_ml_or_retrieval` must keep passing).

## Constraints

- **Single-file committed bundle:** `vp build` continues producing exactly `groundly/web/static/page.js` (IIFE) and `groundly/web/static/page.css`, with `web/` excluded from the Python wheel.
- **No XSS sinks:** All `AUTHORED_SOURCE` checks in `tests/cli/test_cli_serve.py` (`{@html`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `" onclick="`, `http://`, `https://`, `.endsWith(".pdf")`, `"verified"`) stay enforced.
- **Layering contract (`pyproject.toml` / `lint-imports`):** `groundly/web/openapi.py` lives inside the `groundly.web` client subpackage and is imported by nothing below the client layer.
- **Formatter stability:** `pnpm run gen:api` formats `web/src/lib/api/schema.d.ts` with `vp fmt --write` immediately after generation so `lefthook` and CI `vp fmt --check` never disagree with `openapi-typescript`.

---

## Architecture & Module Design

### 1. Backend OpenAPI Surface (`groundly/web/`)

Changes to Python code are intentionally narrow and preserve 100% HTTP runtime behavior:

- **`groundly/web/schemas.py`:**
  - Add `ErrorResponse(BaseModel)` with `error: str` and `detail: str | None = None`.
  - Keep all existing strict request/response Pydantic v2 models unchanged in wire shape and runtime validation (`ConfigDict(strict=True)`, `SqliteId`), so `quiz._grade` continues owning every answer-level `"invalid_text"` and `"unknown_question"` error code.
  - Define OpenAPI-facing `SubmittedChoiceAnswer` (`question_id: SqliteId`, `selected: list[SqliteId]`) and `SubmittedOpenAnswer` (`question_id: SqliteId`, `text: str`) models and reference their union in `POST /api/subjects/{subject}/attempts`'s `openapi_extra` schema (leaving `SubmitAttemptRequest.answers: list[dict[str, Any]]` unchanged at runtime) so `openapi-typescript` generates exact answer payloads without changing runtime error codes.
- **`groundly/web/app.py`:**
  - Add `include_in_schema=False` to `_root` (`/`), `_subject_page` (`/s/{subject}`), `_test_page` (`/s/{subject}/test`), `_attempt_page` (`/s/{subject}/attempts/{id:int}`), and `_material` (`/s/{subject}/materials/{id:int}`).
  - Declare common error responses (`400`, `403`, `404`, `409`, `413`, `415`, `500`, `503` as applicable per route) referencing `ErrorResponse` so OpenAPI documents the error contract.
  - Give every `/api/*` route an explicit `operation_id` (e.g. `listSubjects`, `getSubjectTree`, `drawTest`, `submitAttempt`, `listAttempts`, `getAttemptResult`, `getRuns`, `streamRuns`, `startRun`, `stopRun`) so generated types and future mobile SDKs have stable, clean operation names rather than `_tree_api_subjects__subject__tree_get`.
- **`groundly/web/openapi.py`:**
  - Small module providing `export_openapi() -> dict[str, Any]` (calls `create_app().openapi()`) and `format_openapi(spec: dict[str, Any]) -> str` (`json.dumps(spec, indent=2, sort_keys=True) + "\n"`).
  - Runnable as `uv run python -m groundly.web.openapi` to write `web/openapi.json` (or `--check` to exit non-zero on drift).

### 2. Frontend Directory Layout (`web/src/lib/`)

```text
web/
  openapi.json                  # Committed FastAPI OpenAPI 3.1 schema (single source of truth)
  src/
    App.svelte                  # QueryClientProvider + Header/Breadcrumb + RunStrip + <Router />
    lib/
      api/
        schema.d.ts             # Generated by openapi-typescript (never hand-edited)
        client.ts               # openapi-fetch client, ApiError, unwrap(), auth middleware, URL helpers
        queries.ts              # QueryClient factory, retry predicate, queryKeys
        sse.ts                  # createEventStream(url) transport wrapper + SSE frame payload types
      router.ts                 # Code-based sv-router setup, route params/search helpers, beforeLoad guard slot
      run.svelte.ts             # RunStore using apiClient + createEventStream + queryClient invalidation
      theme.svelte.ts           # Unchanged
      components/               # Unchanged UI primitives & effects (Brand, RunStrip, MasteryBadge, SourceItem, ...)
      views/
        SubjectsView.svelte     # createQuery(['subjects'])
        TreeView.svelte         # createQuery(['subject', subject, 'tree']) + createQuery(['subject', subject, 'attempts'])
        TestView.svelte         # createQuery(tree) + imperative apiClient.GET(test) + createMutation(submitAttempt)
        ResultView.svelte       # createQuery(attempt) + createQuery(tree)
        NotFoundView.svelte     # Extracted from App.svelte for sv-router '*' fallback
        test-session.ts         # Unchanged sessionStorage persistence + dead-session guard
        tree-filter.ts          # Unchanged pure helpers (updated type imports to schema aliases)
```

Deleted files:
- `web/src/lib/api.ts` (superseded by `web/src/lib/api/client.ts`)
- `web/src/lib/types.ts` (superseded by `schema.d.ts` type aliases in `web/src/lib/api/client.ts` + SSE types in `web/src/lib/api/sse.ts`)
- `web/src/lib/router.svelte.ts` (superseded by `web/src/lib/router.ts`)

### 3. API Client, Middleware & Error Normalization (`web/src/lib/api/client.ts`)

- Export convenient domain type aliases derived directly from `components["schemas"]` in `schema.d.ts` (e.g., `SubjectSummary`, `TreeResponse`, `TopicNode`, `TopicProgress`, `SourceItem`, `DrawTestResponse`, `DrawnQuestion`, `AttemptSummary`, `AttemptResult`, `RunSnapshot`, `RunsInfo`, `StopRunResponse`, `ErrorResponse`) so view components keep terse, readable imports with zero manual type duplication.
- Instantiate `apiClient = createClient<paths>({ baseUrl: "", credentials: "same-origin" })`.
- Register a single middleware object controlled by `configureClient(opts: ClientConfig)`:
  - `baseUrl?: string` — overrides request origin when non-empty (defaults to `""` for same-origin).
  - `getAuthHeaders?: () => Record<string, string> | Promise<Record<string, string>>` — merges headers into `request.headers` in `onRequest` (no-op by default).
  - `onUnauthorized?: (response: Response) => void` — invoked in `onResponse` when `response.status === 401` (no-op by default).
- Provide `unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T`:
  - If `result.response.ok && result.data !== undefined`, return `result.data`.
  - Otherwise extract `error` (string code) and `detail` (string message) from `result.error` when it is an object, fall back to `` `request failed (${result.response.status})` ``, and throw `new ApiError(message, result.response.status, code, detail)`.
- Provide small page/material URL helpers (`pageURL(subject, rest)`, `materialURL(subject, materialId, page)`, `testQuery(topicId, n)`) so navigation links and inline PDF links stay encoded consistently in one place.

### 4. Query Cache & Invalidation (`web/src/lib/api/queries.ts`)

- **Query key hierarchy:**
  - `queryKeys.subjects()` → `["subjects"] as const`
  - `queryKeys.subject(subject)` → `["subject", subject] as const` (prefix key used for invalidation!)
  - `queryKeys.tree(subject)` → `["subject", subject, "tree"] as const`
  - `queryKeys.attempts(subject)` → `["subject", subject, "attempts"] as const`
  - `queryKeys.attempt(subject, id)` → `["subject", subject, "attempt", id] as const`
- **`QueryClient` defaults (`createAppQueryClient()`):**
  - `staleTime: 10_000` (10 s — eliminates duplicate `/tree` refetches during rapid Tree → Test → Result navigation while still keeping data fresh).
  - `refetchOnWindowFocus: true` for standard queries (great when switching back to a phone browser tab), disabled where irrelevant.
  - `retry(failureCount, error)`: return `false` if `error instanceof ApiError && error.status >= 400 && error.status < 500`; otherwise retry up to `failureCount < 2` (covers transient `503` SQLite `busy` and mobile/tunnel network blips).
  - Do **not** use `keepPreviousData` across different subject/attempt keys (so navigating from subject A to subject B shows a clean loading state rather than flashing subject A). For background invalidation on the *same* key during an active SSE run, TanStack Query keeps existing `data` rendered automatically while `isFetching` is true — keeping open topic drawers intact with zero flicker.

### 5. Routing (`web/src/lib/router.ts`)

- Configure `sv-router` in code-based mode with exact routes:
  - `'/'`: `SubjectsView`
  - `'/s/:subject'`: `TreeView`
  - `'/s/:subject/test'`: `TestView`
  - `'/s/:subject/attempts/:id'`: `ResultView`
  - `'*'`: `NotFoundView`
- Export `setBeforeLoadGuard(guard)` wired into `sv-router`'s route hook (default no-op). Sub-project 2 plugs its remote session check here to redirect unauthenticated remote browsers to `/pair`.
- Search-param helper `parseTestSearch(searchParams)` mirrors existing validation (`topic`: positive integer or `null`, `n`: integer clamped/defaulted to `10`).
- Attempt ID helper `parsePositiveInt(raw)` validates `:id` in `ResultView` and renders `NotFoundView` if malformed (matching existing `router.svelte.ts` behavior).
- `App.svelte` derives the header breadcrumb cleanly from `route.pathname` and `route.params` (decoding `:subject` safely once) and wraps `<Router />` inside `<QueryClientProvider client={queryClient}>`.
- Verify `sv-router` link interception leaves external links, modifier clicks, `target="_blank"` PDF links (`/s/:subject/materials/:id`), and `download` links untouched.

### 6. `RunStore` & SSE Integration (`web/src/lib/run.svelte.ts`, `web/src/lib/api/sse.ts`)

- `sse.ts` defines the SSE event payload types (`SSEPhaseEvent`, `SSEDeltaEvent`, `SSEStepEvent`) and `createEventStream(url: string): EventSourceLike` (defaulting to `new EventSource(url)`), plus a setter for tests or Sub-project 2's custom stream transport.
- `RunStore` accepts a `QueryClient` (defaulting to the app singleton `queryClient`, injectable in tests) and removes `setHooks(hooks)` entirely:
  - `attach()`, `start()`, `stop()`, and `reconcileAfterStreamError()` call `apiClient.GET("/api/runs")`, `apiClient.POST("/api/subjects/{subject}/runs", ...)`, and `apiClient.POST("/api/runs/stop", ...)` through `unwrap()`.
  - When an SSE `step` event arrives with `data.refresh === true`, call `void queryClient.invalidateQueries({ queryKey: queryKeys.subject(run.subject) })`.
  - When a run settles (`done` event or settled snapshot), call `void queryClient.invalidateQueries({ queryKey: queryKeys.subject(settled.subject) })`.
  - When a watched run disappears after a server restart (`snapshot` is `null` while `watched !== null`), invalidate all subject queries (`queryClient.invalidateQueries({ queryKey: ["subject"] })`).
- Views no longer pass `onRefresh`/`onEnd` callbacks; mounting a view only calls `void runStore.attach()` (or `App.svelte` attaches once on mount and reconnects cleanly).

### 7. View-by-View Data Flow

- **`SubjectsView.svelte`:** Uses `createQuery(() => ({ queryKey: queryKeys.subjects(), queryFn: ... }))`. Eliminates manual `cancelled`, `loading`, `error`, and `$effect` fetch boilerplate.
- **`TreeView.svelte`:** Uses two `createQuery` calls (`tree` and `attempts`). Keeps `selectedTopicId`, `query` (search filter), and drawer focus management in local `$state`. When `tree` updates via SSE invalidation, `selectedTopic` (`$derived(findTopicById(tree, selectedTopicId))`) updates live without closing the drawer.
- **`TestView.svelte`:**
  - Uses `createQuery` for `tree` (warms instantly from cache when navigating from `TreeView`) to resolve `courseTitle` and `scopeTitle`.
  - Keeps its `sessionStorage` draft initializer in `$effect`: if a valid stored draft exists, restore it immediately without hitting `/test`; otherwise call `apiClient.GET("/api/subjects/{subject}/test", { params: { path: { subject }, query: ... }, signal })` with an `AbortController` cleaned up on `$effect` teardown.
  - Uses `createMutation` for `POST /api/subjects/{subject}/attempts`:
    - On success: clears `sessionStorage`, calls `queryClient.invalidateQueries({ queryKey: queryKeys.subject(subject) })`, and navigates via `sv-router` to `/s/:subject/attempts/:id`.
    - On error: if `err instanceof ApiError && err.code === "unknown_question"`, calls `persister.markDead()`, drops the stored draft, and displays the reload prompt; otherwise displays `err.message`.
- **`ResultView.svelte`:** Uses `createQuery` for `attempt` (`['subject', subject, 'attempt', id]`) and `tree` (`['subject', subject, 'tree']`). When a `grade` run finishes or records verdicts mid-run, `RunStore`'s invalidation refreshes both `attempt` verdicts and `tree` mastery automatically.

---

## Security & Bundle Hygiene Verification

Before shipping the upgrade, implementation must verify:

1. **Zero external URLs in compiled `page.js`:** After adding `openapi-fetch`, `@tanstack/svelte-query`, and `sv-router`, run `pnpm run build` and `uv run pytest tests/cli/test_cli_serve.py::test_shipped_bundle_is_built_clean_and_self_contained`. If any dependency embeds a dev-only `http://`/`https://` warning URL, confirm Vite production bundling strips it (e.g., via `process.env.NODE_ENV === "production"` / dead-code elimination). Never relax the test to allow runtime external URLs.
2. **Strict `_CSP` untouched:** Confirm `groundly/web/app.py`'s `_CSP` header (`script-src 'self'`, `connect-src 'self'`, `frame-ancestors 'none'`) requires zero changes.
3. **Auth-readiness does not weaken loopback security:** Default `credentials: "same-origin"` and empty `baseUrl: ""` preserve identical same-origin `Origin` headers on `POST` requests; `_require_page_json` and `TrustedHostMiddleware` remain unchanged.

---

## Testing & CI Strategy

### Python Tests (`uv run pytest`)
- **New `tests/web/test_openapi.py`:**
  - Asserts `export_openapi()` equals the JSON parsed from `web/openapi.json` (and `format_openapi(export_openapi())` matches the file byte-for-byte).
  - Asserts every key in `spec["paths"]` starts with `/api/`.
  - Asserts `GET /openapi.json`, `GET /docs`, and `GET /redoc` still return `404` on `TestClient(create_app())`.
- **Existing tests (`tests/web/test_app.py`, `tests/cli/test_cli_serve.py`):**
  - Must pass with zero regressions on status codes, strict validation, `405` enforcement, CSP, `AUTHORED_SOURCE`, and `SHIPPED_BUNDLE`.
  - Update `tests/cli/test_cli_serve.py::test_page_starts_runs_through_the_api_only` to assert `"/api/subjects/{subject}/runs"` instead of the split literal `'"/runs"'`.

### Frontend Tests (`pnpm test` / `vp test run` in jsdom)
- **`web/src/lib/api/__tests__/client.test.ts`:**
  - `unwrap()` maps JSON `ErrorResponse` and non-JSON error responses to `ApiError(message, status, code, detail)`.
  - Middleware injects headers from `getAuthHeaders` and triggers `onUnauthorized` on `401` (and not on `200`/`400`/`500`).
  - `createAppQueryClient()` retry predicate skips retries on `400`/`401`/`403`/`404`/`409`/`422` and retries transient `503` / network failures up to 2 times.
- **`web/src/lib/__tests__/router.test.ts`:**
  - Route matching and parameter decoding for `/`, `/s/:subject`, `/s/:subject/test?topic=3&n=5`, `/s/:subject/attempts/42`, invalid attempt IDs (`0`, `-1`, `abc`), and unknown paths (`*`).
  - `beforeLoad` guard hook invocation and redirect behavior.
  - Non-interception of `/s/:subject/materials/:id` links.
- **`web/src/lib/__tests__/run.test.ts`:**
  - Updated to inject a test `QueryClient` and `createEventStream` stub; asserts `invalidateQueries` is called with `["subject", subject]` on SSE `step` (`refresh: true`) and `done`, and not called when `refresh: false`.
  - Preserves existing SSE race-guard, delta capping, and retry-cap tests.
- Existing `theme`, `effects`, `tree-filter`, and `test-session` tests stay green.

### Scripts, Lefthook & CI
- **`web/package.json`:** Add `"gen:api": "openapi-typescript ./openapi.json -o ./src/lib/api/schema.d.ts && vp fmt --write ./src/lib/api/schema.d.ts"`.
- **`.github/workflows/ci.yml`:**
  - `server` job (Python-only): unchanged commands; `uv run pytest` automatically enforces `web/openapi.json` freshness via `tests/web/test_openapi.py`.
  - `web` job (Node-only): add a step before `svelte-check`:
    ```yaml
    - name: Generated API types check (up to date)
      run: |
        pnpm run gen:api
        git diff --exit-code src/lib/api/schema.d.ts
    ```
- **`lefthook.yml`:** Existing `web-fix+format` and `web-svelte-check` hooks stay compatible because `gen:api` formats `schema.d.ts` with `vp fmt`.

---

## Documentation Updates

In the same implementation change set (per `.claude/rules/conventions.md`):
- **`docs/groundly-spec.md`:** Record Decision 51 (amending Decision 50) documenting the offline OpenAPI contract pipeline, `openapi-typescript` + `openapi-fetch`, `@tanstack/svelte-query` v6, and `sv-router`, plus the rationale for staying pure Svelte 5 rather than SvelteKit.
- **`docs/tech-stack/tech-stack.md`:** Update the Topic-tree page row to include the typed OpenAPI client, TanStack Query v6, and `sv-router`.
- **`.claude/rules/architecture.md`:** Update the Frameworks note if needed to stay aligned with Decision 51.
