# Pure-Svelte Typed REST Client, Query Cache & Router Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `web/` to an OpenAPI-typed REST client (`openapi-typescript` + `openapi-fetch`), `@tanstack/svelte-query` v6 reactive caching with SSE-driven invalidation, and code-based `sv-router` routing while preserving Groundly's zero-Node single-file bundle and strict security invariants.

**Architecture:** FastAPI exports an offline `/api/*`-only OpenAPI 3.1 schema (`web/openapi.json`) pinned by a Python pytest drift test, and `openapi-typescript` generates compile-time `web/src/lib/api/schema.d.ts` pinned by the Node-only `web` CI job. `web/src/lib/api/client.ts` wraps `openapi-fetch` with `ApiError` normalization and a single auth-ready middleware slot, `web/src/lib/api/queries.ts` configures TanStack Query v6 for `RunStore` SSE invalidation and view queries, and `web/src/lib/router.ts` uses code-based `sv-router` with thin route wrappers so views remain prop-driven and independently testable.

**Tech Stack:** FastAPI / Pydantic v2 (`groundly/web/`), Svelte 5.59+ / TypeScript / Vite+ (`vp`), `openapi-typescript`, `openapi-fetch`, `@tanstack/svelte-query` v6, `sv-router`, Vitest (`vp test`) + jsdom, pytest.

**Spec:** [`docs/superpowers/specs/2026-11-24-web-typed-rest-router-design.md`](../specs/2026-11-24-web-typed-rest-router-design.md)

## Global Constraints

- **Zero-Node Python wheel & pytest:** `vp build` compiles to exactly `groundly/web/static/page.js` (IIFE) and `groundly/web/static/page.css`; `web/` is excluded from the wheel; `pytest` never invokes Node.js and `web` CI never invokes Python.
- **Runtime OpenAPI disabled:** `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` stays `None`; `/docs`, `/redoc`, and `/openapi.json` return `404` over HTTP.
- **Strict `_CSP` & bundle URL hygiene:** `script-src 'self'` stays unchanged (no `'unsafe-inline'` or `'unsafe-eval'`), `connect-src 'self'`, `frame-ancestors 'none'`; zero runtime `http://`/`https://` URLs in `AUTHORED_SOURCE` or `SHIPPED_BUNDLE` (after existing W3C/Svelte/Tailwind comment stripping).
- **Zero XSS sinks & Layer-3 text-only rendering:** No `{@html`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `" onclick="`, `.endsWith(".pdf")`, or `"verified"` in `web/src/`.
- **No ML/retrieval import in `groundly.web`:** `test_importing_web_app_pulls_in_no_ml_or_retrieval` must keep passing; `lint-imports` layering contract holds.
- **Preserve runtime API & security behavior:** Identical `/api/*` paths, status codes, `{"error", "detail"}` payloads, `_require_page_json` pre-parse guards (`403`/`415`/`413`/`400`), `405` enforcement, and `quiz._grade` error codes (`"invalid_text"`, `"unknown_question"`).
- **Feature branch workflow:** Never commit to `main`; create a feature branch (e.g. `feat/web-typed-rest-router`) before the first task commit.

## Review Focus

1. **Malformed percent-encoding or non-positive attempt IDs in URLs (`/s/%E0%A4%A`, `/s/PDSS/attempts/0`, `/s/PDSS/attempts/-5`, `/s/PDSS/attempts/abc`):** Must never throw `URIError` or fire `GET /api/.../attempts/NaN`; route wrappers render `NotFoundView` cleanly. *(Pinned in Task 3 `router.test.ts`.)*
2. **Non-JSON gateway/tunnel error body (`502 Bad Gateway` plain text/HTML):** `unwrap()` must throw `ApiError("request failed (502)", 502, null, null)` instead of crashing on JSON parsing, and `QueryClient` must retry `502`/`503` up to 2 times while never retrying 4xx errors. *(Pinned in Task 2 `client.test.ts`.)*
3. **Rapid navigation from Subject A to Subject B while Subject A's tree is cached:** `TreeView` must not flash Subject A's tree or keep Subject A's open topic drawer when `subject` changes to Subject B. *(Pinned in Task 5 `views.test.ts`.)*
4. **SSE `step` (`refresh: true`) arriving mid-run while a topic drawer is open or a test is in progress:** `queryClient.invalidateQueries` must refresh `TreeView` question counts in place with the open drawer preserved, and must never trigger a fresh `GET /api/subjects/{subject}/test` redraw in `TestView`. *(Pinned in Task 4 `run.test.ts`, Task 5 `views.test.ts`, Task 6 `views.test.ts`.)*
5. **Third-party runtime dependencies embedding dev/docs `http://`/`https://` URLs into `page.js`:** Bundling `openapi-fetch`, `@tanstack/svelte-query`, and `sv-router` must leave zero external URLs in `groundly/web/static/page.js`. *(Pinned in Task 7 `test_cli_serve.py`.)*

---

## File Structure

- **Backend OpenAPI (`groundly/web/`):**
  - Modify `groundly/web/schemas.py` — add `ErrorResponse`, `SubmittedChoiceAnswer`, `SubmittedOpenAnswer`, and `SubmitAttemptOpenAPIBody` (schema-only; runtime `SubmitAttemptRequest` unchanged).
  - Modify `groundly/web/app.py` — `include_in_schema=False` on HTML/material routes, explicit `operation_id` and `ErrorResponse` `responses` on all `/api/*` routes.
  - Create `groundly/web/openapi.py` — `export_openapi()`, `format_openapi()`, CLI writer/`--check`.
  - Create `web/openapi.json` — committed OpenAPI 3.1 schema.
  - Create `tests/web/test_openapi.py` — schema drift, `/api/*`-only paths, runtime `404` check.
- **Frontend API & Query layer (`web/src/lib/api/`):**
  - Create `web/src/lib/api/schema.d.ts` — generated by `openapi-typescript` + formatted by `vp fmt`.
  - Create `web/src/lib/api/client.ts` — `apiClient`, `configureClient`, `ApiError`, `unwrap`, schema type aliases, `pageURL`, `materialURL`, `testQuery`.
  - Create `web/src/lib/api/queries.ts` — `queryKeys`, `shouldRetryQuery`, `createAppQueryClient`, `queryClient`.
  - Create `web/src/lib/api/sse.ts` — `SSEPhaseEvent`, `SSEDeltaEvent`, `SSEStepEvent`, `EventSourceLike`, `createEventStream`, `setEventStreamFactory`.
  - Create `web/src/lib/api/__tests__/client.test.ts`.
- **Frontend Routing (`web/src/lib/`):**
  - Create `web/src/lib/router.ts` — `sv-router` setup (`p`, `navigate`, `isActive`, `route`), `decodeParam`, `parseTestSearch`, `parsePositiveInt`, `setBeforeLoadGuard`, `shouldSkipSpaLink`.
  - Create `web/src/lib/routes/SubjectTreeRoute.svelte`, `SubjectTestRoute.svelte`, `AttemptResultRoute.svelte` — thin route wrappers validating params/search and rendering `NotFoundView` on invalid params.
  - Create `web/src/lib/views/NotFoundView.svelte`.
  - Replace `web/src/lib/__tests__/router.test.ts`.
- **Frontend State, Views & Shell:**
  - Modify `web/src/lib/run.svelte.ts` and `web/src/lib/__tests__/run.test.ts`.
  - Update type/URL imports in `RunStrip.svelte`, `MasteryBadge.svelte`, `SourceItem.svelte`, `ThinkingOrb.svelte`, `orb-state.ts`, `test-session.ts`, `tree-filter.ts`, and their tests.
  - Modify `SubjectsView.svelte`, `TreeView.svelte`, `TestView.svelte`, `ResultView.svelte`, `App.svelte`, and `views.test.ts` (with `QueryHarness.svelte` test helper).
  - Delete `web/src/lib/api.ts`, `web/src/lib/types.ts`, `web/src/lib/router.svelte.ts`.
- **Build, CI & Docs:**
  - Rebuild `groundly/web/static/page.js` and `groundly/web/static/page.css`.
  - Update `tests/cli/test_cli_serve.py`, `.github/workflows/ci.yml`, `docs/groundly-spec.md`, `docs/tech-stack/tech-stack.md`, `.claude/rules/architecture.md`.

---

### Task 1: FastAPI OpenAPI Schema Tightening & Offline Exporter

**Files:**
- Modify: `groundly/web/schemas.py`
- Modify: `groundly/web/app.py`
- Create: `groundly/web/openapi.py`
- Create: `web/openapi.json`
- Create: `tests/web/test_openapi.py`

**Interfaces:**
- Consumes: `groundly.web.app.create_app`, `groundly.web.schemas.*`
- Produces:
  - `groundly.web.schemas.ErrorResponse` (`error: str`, `detail: str | None = None`)
  - `groundly.web.schemas.SubmittedChoiceAnswer` (`question_id: SqliteId`, `selected: list[SqliteId]`)
  - `groundly.web.schemas.SubmittedOpenAnswer` (`question_id: SqliteId`, `text: str`)
  - `groundly.web.schemas.SubmitAttemptOpenAPIBody` (`token: str`, `topic_id: SqliteId | None = None`, `answers: list[SubmittedChoiceAnswer | SubmittedOpenAnswer]`)
  - `groundly.web.openapi.export_openapi() -> dict[str, Any]`
  - `groundly.web.openapi.format_openapi(spec: dict[str, Any]) -> str`
  - Committed `web/openapi.json` with `/api/*`-only paths and explicit `operationId`s (`listSubjects`, `getSubjectTree`, `drawTest`, `submitAttempt`, `listAttempts`, `getAttemptResult`, `getRuns`, `streamRuns`, `startRun`, `stopRun`).

- [ ] **Step 1: Ensure work starts on a feature branch (never `main`)**

Run: `git branch --show-current`
If on `main`, create a feature branch: `git checkout -b feat/web-typed-rest-router` (do not stage unrelated dirty files).

- [ ] **Step 2: Write the failing OpenAPI contract tests in `tests/web/test_openapi.py`**

```python
from pathlib import Path
from fastapi.testclient import TestClient
from groundly.web.app import create_app
from groundly.web.openapi import export_openapi, format_openapi

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "web" / "openapi.json"


def test_committed_openapi_json_matches_fastapi_schema():
    expected = format_openapi(export_openapi())
    assert OPENAPI_PATH.exists(), "run: uv run python -m groundly.web.openapi"
    assert OPENAPI_PATH.read_text(encoding="utf-8") == expected


def test_openapi_schema_contains_only_api_paths_and_typed_errors():
    spec = export_openapi()
    paths = spec["paths"]
    assert len(paths) == 9
    assert all(path.startswith("/api/") for path in paths)
    assert "ErrorResponse" in spec["components"]["schemas"]
    submit_schema = paths["/api/subjects/{subject}/attempts"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert "SubmittedChoiceAnswer" in str(submit_schema) or "selected" in str(submit_schema)
    assert "SubmittedOpenAnswer" in str(submit_schema) or "text" in str(submit_schema)


def test_runtime_openapi_and_docs_remain_disabled():
    client = TestClient(create_app(), base_url="http://127.0.0.1:8765")
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/web/test_openapi.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'groundly.web.openapi'`).

- [ ] **Step 4: Add OpenAPI schema models in `groundly/web/schemas.py`**

Add `ErrorResponse(BaseModel)` (`error: str`, `detail: str | None = None`), `SubmittedChoiceAnswer(BaseModel)` (`question_id: SqliteId`, `selected: list[SqliteId]`), `SubmittedOpenAnswer(BaseModel)` (`question_id: SqliteId`, `text: str`), and `SubmitAttemptOpenAPIBody(BaseModel)` (`token: str`, `topic_id: SqliteId | None = None`, `answers: list[SubmittedChoiceAnswer | SubmittedOpenAnswer]`). Keep runtime `SubmitAttemptRequest` completely unchanged so `quiz._grade` continues returning `"invalid_text"` / `"unknown_question"`.

- [ ] **Step 5: Tighten OpenAPI annotations in `groundly/web/app.py`**

- Add `include_in_schema=False` on `_root`, `_subject_page`, `_test_page`, `_attempt_page`, and `_material`.
- Define `_ERROR_RESPONSES` helper mapping applicable error status codes (`400`, `403`, `404`, `409`, `413`, `415`, `500`, `503`) to `{"model": ErrorResponse}` and attach `responses=...` + explicit `operation_id=...` (`listSubjects`, `getSubjectTree`, `drawTest`, `submitAttempt`, `listAttempts`, `getAttemptResult`, `getRuns`, `streamRuns`, `startRun`, `stopRun`) on the `/api/*` routes.
- Point `POST /api/subjects/{subject}/attempts`'s `openapi_extra=_json_body_schema(SubmitAttemptOpenAPIBody)` at `SubmitAttemptOpenAPIBody` while keeping runtime validation on `SubmitAttemptRequest.model_validate(body)`.
- Ensure `_json_body_schema` inlines or resolves `$defs` cleanly so OpenAPI 3.1 has no dangling `#/$defs/...` references (either use `ref_template="#/components/schemas/{model}"` with a custom `app.openapi` hook merging `$defs` into `components.schemas`, OR register `ErrorResponse`/`SubmittedChoiceAnswer`/`SubmittedOpenAnswer` cleanly in `components.schemas` — verify `openapi-typescript` parses `web/openapi.json` with zero broken `$ref`s!).

- [ ] **Step 6: Create `groundly/web/openapi.py` and generate `web/openapi.json`**

Implement `export_openapi() -> dict[str, Any]`, `format_openapi(spec: dict[str, Any]) -> str` (`json.dumps(spec, indent=2, sort_keys=True) + "\n"`), and a `__main__` entrypoint writing `web/openapi.json` (with `--check` flag exiting 1 on diff).

Run: `uv run python -m groundly.web.openapi`

- [ ] **Step 7: Run Python tests, layering, and type-checking**

Run: `uv run pytest tests/web/ -v && uv run lint-imports && uv run pyrefly check`
Expected: PASS (all existing `test_app.py`, `test_runs.py`, and new `test_openapi.py` pass; zero import-linter or pyrefly errors).

- [ ] **Step 8: Commit Task 1**

```bash
git add groundly/web/schemas.py groundly/web/app.py groundly/web/openapi.py web/openapi.json tests/web/test_openapi.py
git commit -m "feat(web): export offline /api/* OpenAPI schema with drift check"
```

---

### Task 2: Install Pure-Svelte Dependencies, Generate `schema.d.ts`, and Implement `api/client.ts`, `api/queries.ts`, `api/sse.ts`

**Files:**
- Modify: `web/package.json`
- Modify: `web/pnpm-lock.yaml`
- Create: `web/src/lib/api/schema.d.ts` (generated)
- Create: `web/src/lib/api/client.ts`
- Create: `web/src/lib/api/queries.ts`
- Create: `web/src/lib/api/sse.ts`
- Create: `web/src/lib/api/__tests__/client.test.ts`

**Interfaces:**
- Consumes: `web/openapi.json` (from Task 1)
- Produces:
  - `web/src/lib/api/schema.d.ts` (`paths`, `components`)
  - `web/src/lib/api/client.ts`:
    - `ApiError` (`status: number`, `code: string | null`, `detail: string | null`)
    - `ClientConfig` (`baseUrl?: string`, `getAuthHeaders?: () => Record<string, string> | Promise<Record<string, string>>`, `onUnauthorized?: (response: Response) => void`)
    - `apiClient` (`openapi-fetch` client for `paths`)
    - `configureClient(config: ClientConfig): void` and `resetClientConfig(): void`
    - `unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T`
    - `pageURL(subject: string, rest?: string): string`, `materialURL(subject: string, materialId: number, page?: number | null): string`, `testQuery(topicId: number | null, n: number): string`
    - Schema-derived type aliases: `SubjectSummary`, `SourceItem`, `TopicProgress`, `MasteryState`, `TopicNode`, `TreeResponse`, `QuestionKind`, `QuestionOption`, `DrawnQuestion`, `DrawTestResponse`, `SubmittedChoiceAnswer`, `SubmittedOpenAnswer`, `SubmittedAnswer`, `SubmitAttemptResponse`, `AttemptScore`, `AttemptSummary`, `AttemptOption`, `AnswerState`, `AttemptAnswer`, `AttemptTopicSummary`, `AttemptResult`, `RunPhase`, `RunKind`, `RunStatus`, `ThoughtChannel`, `RunErrorDetail`, `RunSnapshot`, `RunsInfo`, `StartRunBody`, `StopRunResponse`, `ErrorResponse`.
  - `web/src/lib/api/queries.ts`:
    - `queryKeys = { subjects, subject, tree, attempts, attempt }`
    - `shouldRetryQuery(failureCount: number, error: unknown): boolean`
    - `createAppQueryClient(): QueryClient`
    - `queryClient: QueryClient`
  - `web/src/lib/api/sse.ts`:
    - `SSEPhaseEvent`, `SSEDeltaEvent`, `SSEStepEvent`, `EventSourceLike`
    - `createEventStream(url: string): EventSourceLike | null`
    - `setEventStreamFactory(factory: ((url: string) => EventSourceLike | null) | null): void`

- [ ] **Step 1: Add dependencies and `gen:api` script in `web/package.json`**

Install runtime dependencies `openapi-fetch`, `@tanstack/svelte-query` (v6+), `sv-router` and devDependency `openapi-typescript` via `pnpm --dir web add ...` / `pnpm --dir web add -D openapi-typescript`.
Add script in `web/package.json`:
`"gen:api": "openapi-typescript ./openapi.json -o ./src/lib/api/schema.d.ts && vp fmt --write ./src/lib/api/schema.d.ts"`

- [ ] **Step 2: Generate `web/src/lib/api/schema.d.ts`**

Run: `pnpm --dir web run gen:api`
Expected: `web/src/lib/api/schema.d.ts` generated cleanly with zero unresolved `$ref` errors and formatted cleanly by `vp fmt`.

- [ ] **Step 3: Write the failing unit tests in `web/src/lib/api/__tests__/client.test.ts`**

Test cases (including Review Focus #2):
- `unwrap` returns `data` on `200` OK responses.
- `unwrap` throws `ApiError(detail, status, code, detail)` on JSON `ErrorResponse` (`400` `unknown_question`, `404` `unknown_subject`).
- `unwrap` throws `ApiError("request failed (502)", 502, null, null)` when the server/tunnel returns a non-JSON plain-text/HTML `502 Bad Gateway` body (Review Focus #2).
- `configureClient({ getAuthHeaders, onUnauthorized })` injects headers on outbound requests via `apiClient.GET("/api/subjects")` and invokes `onUnauthorized` on `401` responses (and not on `200`/`400`/`500`).
- `shouldRetryQuery` returns `false` for `ApiError` with status `400`, `401`, `403`, `404`, `409`, `413`, `415`, `422`, and returns `true` for `failureCount < 2` (and `false` at `failureCount >= 2`) on `503`, `502`, and generic `TypeError` network failures.
- `queryKeys.tree("PDSS")` starts with `queryKeys.subject("PDSS")` (`["subject", "PDSS"]`) so prefix invalidation covers `tree`, `attempts`, and `attempt`.

- [ ] **Step 4: Run test to verify it fails**

Run: `pnpm --dir web exec vp test run src/lib/api/__tests__/client.test.ts`
Expected: FAIL (modules `../client` / `../queries` not found).

- [ ] **Step 5: Implement `web/src/lib/api/client.ts`, `web/src/lib/api/queries.ts`, and `web/src/lib/api/sse.ts`**

- `client.ts`: create `apiClient = createClient<paths>({ baseUrl: "", credentials: "same-origin" })`, attach one middleware reading module-level `ClientConfig` (updating `request` URL when `baseUrl` is configured, merging `getAuthHeaders()`, calling `onUnauthorized(response)` on `401`), implement `ApiError`, `unwrap`, URL helpers (`pageURL`, `materialURL`, `testQuery`), and export all domain type aliases mapped from `components["schemas"]`. (Avoid any literal `http://` or `https://` strings in authored source!)
- `queries.ts`: implement `queryKeys`, `shouldRetryQuery`, `createAppQueryClient` (`staleTime: 10_000`, `retry: shouldRetryQuery`, `refetchOnWindowFocus: true`), and singleton `queryClient`.
- `sse.ts`: export `SSEPhaseEvent`, `SSEDeltaEvent`, `SSEStepEvent`, `EventSourceLike`, `createEventStream` (using `globalThis.EventSource` by default, or custom factory when set via `setEventStreamFactory`).

- [ ] **Step 6: Run tests and type-check**

Run: `pnpm --dir web exec vp test run src/lib/api/__tests__/client.test.ts && pnpm --dir web exec svelte-check --tsconfig ./tsconfig.json`
Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add web/package.json web/pnpm-lock.yaml web/src/lib/api/
git commit -m "feat(web): add OpenAPI-typed client, TanStack Query v6 config, and SSE transport"
```

---

### Task 3: Code-Based `sv-router` Setup, Thin Route Wrappers & `NotFoundView`

**Files:**
- Create: `web/src/lib/router.ts`
- Create: `web/src/lib/views/NotFoundView.svelte`
- Create: `web/src/lib/routes/SubjectTreeRoute.svelte`
- Create: `web/src/lib/routes/SubjectTestRoute.svelte`
- Create: `web/src/lib/routes/AttemptResultRoute.svelte`
- Modify: `web/src/lib/__tests__/router.test.ts`

**Interfaces:**
- Consumes: `sv-router`, `SubjectsView`, `TreeView`, `TestView`, `ResultView`
- Produces:
  - `web/src/lib/router.ts`:
    - `p`, `navigate`, `isActive`, `route` (from `sv-router` `createRouter`)
    - `BeforeLoadContext`, `BeforeLoadGuard`, `setBeforeLoadGuard(guard: BeforeLoadGuard | null): void`
    - `decodeParam(segment: string | undefined): string | null`
    - `parsePositiveInt(raw: string | undefined | null): number | null`
    - `parseTestSearch(params: URLSearchParams): { topicId: number | null; n: number }`
    - `isMaterialPath(pathname: string): boolean`
  - `web/src/lib/views/NotFoundView.svelte` (optional `pathname?: string` prop defaulting to `route.pathname`)

- [ ] **Step 1: Write the failing router tests in `web/src/lib/__tests__/router.test.ts`**

Keep existing coverage while targeting the new `web/src/lib/router.ts` API (including Review Focus #1):
- `decodeParam` decodes `"PDSS"` → `"PDSS"`, `"CS%20101"` → `"CS 101"`, returns `null` for `""`, `undefined`, and malformed percent-encoding `"%E0%A4%A"` (never throws `URIError`).
- `parsePositiveInt` accepts `"1"`, `"42"`, rejects `"0"`, `"-5"`, `"abc"`, `"1.5"`, `"9007199254740993"` (non-safe int), `""`, `null` → `null`.
- `parseTestSearch` parses `?topic=7&n=10` → `{ topicId: 7, n: 10 }`, `?n=20` → `{ topicId: null, n: 20 }`, invalid/missing `n` → `10`, invalid `topic=abc` → `topicId: null`.
- `isMaterialPath("/s/PDSS/materials/1")` returns `true`; `isMaterialPath("/s/PDSS")` returns `false`.
- `setBeforeLoadGuard` runs before route transitions on `navigate("/s/PDSS")` and cancels navigation when the guard returns `false` (and resets cleanly via `setBeforeLoadGuard(null)`).
- Navigating to `/s/PDSS` updates `route.pathname` and `route.params.subject`, and `navigate("/s/:subject", { params: { subject: "CS 101" } })` round-trips through `decodeParam(route.params.subject)` as `"CS 101"` without double-encoding.

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --dir web exec vp test run src/lib/__tests__/router.test.ts`
Expected: FAIL (`../router` not found).

- [ ] **Step 3: Create `NotFoundView.svelte`, the three thin route wrappers, and `web/src/lib/router.ts`**

- `NotFoundView.svelte`: renders the existing "Page not found" card with `pathname` and `<Button href="/" size="sm">All subjects</Button>`.
- `SubjectTreeRoute.svelte`: reads `subject = $derived(decodeParam(route.params.subject))`; renders `<TreeView {subject} />` when non-null, else `<NotFoundView />`.
- `SubjectTestRoute.svelte`: reads `subject = $derived(decodeParam(route.params.subject))` and `parsed = $derived(parseTestSearch(searchParams))`; renders `<TestView {subject} topicId={parsed.topicId} n={parsed.n} />` when `subject` is non-null, else `<NotFoundView />`.
- `AttemptResultRoute.svelte`: reads `subject = $derived(decodeParam(route.params.subject))` and `attemptId = $derived(parsePositiveInt(route.params.id))`; renders `<ResultView {subject} {attemptId} />` when both are non-null, else `<NotFoundView />`.
- `router.ts`: defines `createRouter({ '/': ..., '/s/:subject': ..., '/s/:subject/test': ..., '/s/:subject/attempts/:id': ..., '*': NotFoundView })` with shared `hooks.beforeLoad` delegating to the registered `BeforeLoadGuard` (and refusing to match `/s/:subject/materials/:id` as an SPA transition by checking `isMaterialPath`).

- [ ] **Step 4: Run router tests and type-check**

Run: `pnpm --dir web exec vp test run src/lib/__tests__/router.test.ts && pnpm --dir web exec svelte-check --tsconfig ./tsconfig.json`
Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add web/src/lib/router.ts web/src/lib/views/NotFoundView.svelte web/src/lib/routes/ web/src/lib/__tests__/router.test.ts
git commit -m "feat(web): add code-based sv-router with typed route wrappers and beforeLoad guard"
```

---

### Task 4: Migrate `RunStore` & Shared Components to `apiClient`, `createEventStream`, and `QueryClient` Invalidation

**Files:**
- Modify: `web/src/lib/run.svelte.ts`
- Modify: `web/src/lib/__tests__/run.test.ts`
- Modify: `web/src/lib/components/RunStrip.svelte`
- Modify: `web/src/lib/components/MasteryBadge.svelte`
- Modify: `web/src/lib/components/SourceItem.svelte`
- Modify: `web/src/lib/components/effects/ThinkingOrb.svelte`
- Modify: `web/src/lib/components/effects/orb-state.ts`
- Modify: `web/src/lib/components/__tests__/effects.test.ts`
- Modify: `web/src/lib/views/test-session.ts`
- Modify: `web/src/lib/views/tree-filter.ts`
- Modify: `web/src/lib/views/__tests__/test-session.test.ts`
- Modify: `web/src/lib/views/__tests__/tree-filter.test.ts`

**Interfaces:**
- Consumes: `apiClient`, `unwrap`, `ApiError`, `materialURL`, schema types (`web/src/lib/api/client.ts`), `queryClient`, `queryKeys` (`web/src/lib/api/queries.ts`), `createEventStream`, SSE types (`web/src/lib/api/sse.ts`)
- Produces:
  - `createRunStore(qc?: QueryClient)` (invalidates `queryKeys.subject(subject)` on SSE `step` with `refresh: true` and on run settlement, and invalidates `["subject"]` on server restart drop; retains `setHooks` temporarily until Task 6 finishes migrating the last views so every intermediate commit passes `svelte-check` and `views.test.ts`)
  - `runStore` (singleton wired to app `queryClient`)

- [ ] **Step 1: Update `web/src/lib/__tests__/run.test.ts` to specify `QueryClient` invalidation and `apiClient` calls**

Update tests in `run.test.ts` (including Review Focus #4):
- Inject a fresh `QueryClient` into `createRunStore(qc)` and spy on `qc.invalidateQueries`.
- Assert `step` event with `{ id, step, refresh: true }` calls `qc.invalidateQueries({ queryKey: ["subject", "PDSS"] })`, whereas `refresh: false` does not invalidate.
- Assert `done` event calls `qc.invalidateQueries({ queryKey: ["subject", "PDSS"] })` once (never twice for the same settled run ID).
- Assert `snapshot: null` while watching a running job calls `qc.invalidateQueries({ queryKey: ["subject"] })` and shows the restart message.
- Keep existing assertions for `attach()` SSE race guard, `MAX_STREAM_CHARS` delta cap, `MAX_STREAM_RETRIES` cap, `start()`, and `stop()`.

- [ ] **Step 2: Run `run.test.ts` to verify it fails against the old `RunStore`**

Run: `pnpm --dir web exec vp test run src/lib/__tests__/run.test.ts`
Expected: FAIL (`createRunStore` does not accept `QueryClient` / does not call `invalidateQueries`).

- [ ] **Step 3: Refactor `web/src/lib/run.svelte.ts` and update shared component/helper imports**

- In `run.svelte.ts`:
  - Replace `getJSON`/`postJSON` with `unwrap(await apiClient.GET("/api/runs"))`, `unwrap(await apiClient.POST("/api/subjects/{subject}/runs", { params: { path: { subject } }, body }))`, and `unwrap(await apiClient.POST("/api/runs/stop", { body: {} }))`.
  - Replace `new EventSource(RUNS_STREAM_API)` with `createEventStream("/api/runs/stream")`.
  - Accept optional `qc: QueryClient = queryClient` in `createRunStore(qc)` and trigger `void qc.invalidateQueries(...)` on `refresh: true`, `applySettled`, and server restart drop. Keep `setHooks` working until Task 6 finishes migrating `ResultView` so intermediate commits stay green.
- Update type/URL imports across `RunStrip.svelte`, `MasteryBadge.svelte`, `SourceItem.svelte` (using `materialURL` while keeping `src.pdf === true`), `ThinkingOrb.svelte`, `orb-state.ts`, `effects.test.ts`, `test-session.ts`, `tree-filter.ts`, `test-session.test.ts`, and `tree-filter.test.ts` to import from `api/client` instead of legacy `types.ts` / `api.ts`.

- [ ] **Step 4: Run updated frontend tests and type-check**

Run: `pnpm --dir web exec vp test run src/lib/__tests__/run.test.ts src/lib/components/__tests__/effects.test.ts src/lib/views/__tests__/test-session.test.ts src/lib/views/__tests__/tree-filter.test.ts && pnpm --dir web exec svelte-check --tsconfig ./tsconfig.json`
Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add web/src/lib/run.svelte.ts web/src/lib/__tests__/run.test.ts web/src/lib/components/ web/src/lib/views/test-session.ts web/src/lib/views/tree-filter.ts web/src/lib/views/__tests__/test-session.test.ts web/src/lib/views/__tests__/tree-filter.test.ts
git commit -m "feat(web): migrate RunStore to openapi-fetch and QueryClient invalidation"
```

---

### Task 5: Migrate `SubjectsView` and `TreeView` to TanStack Query v6

**Files:**
- Create: `web/src/lib/views/__tests__/QueryHarness.svelte`
- Modify: `web/src/lib/views/SubjectsView.svelte`
- Modify: `web/src/lib/views/TreeView.svelte`
- Modify: `web/src/lib/views/__tests__/views.test.ts`

**Interfaces:**
- Consumes: `createQuery`, `QueryClientProvider` (`@tanstack/svelte-query`), `apiClient`, `unwrap`, `pageURL`, `testQuery` (`web/src/lib/api/client.ts`), `queryKeys` (`web/src/lib/api/queries.ts`), `runStore` (`web/src/lib/run.svelte.ts`)
- Produces:
  - `SubjectsView.svelte` (zero props; `createQuery` on `queryKeys.subjects()`)
  - `TreeView.svelte` (`subject: string` prop; `createQuery` on `queryKeys.tree(subject)` and `queryKeys.attempts(subject)`)
  - `QueryHarness.svelte` (`client: QueryClient`, `children: Snippet` — reusable test provider wrapper)

- [ ] **Step 1: Add `QueryHarness.svelte` and add failing `SubjectsView` / `TreeView` query tests to `views.test.ts`**

Add tests in `web/src/lib/views/__tests__/views.test.ts` (including Review Focus #3 and #4):
- `SubjectsView` renders subjects from `GET /api/subjects` and displays an alert on failure.
- `TreeView` mid-run SSE invalidation (`qc.invalidateQueries({ queryKey: queryKeys.subject("PDSS") })`) updates the open topic drawer's question count from `2` to `10` in place without closing the drawer (Review Focus #4).
- Switching `TreeView`'s `subject` prop from `"PDSS"` (cached) to `"PASSC"` (pending fetch) immediately clears the open topic drawer and does NOT show `"PDSS"`'s topic titles while `"PASSC"` loads (Review Focus #3).

- [ ] **Step 2: Run `views.test.ts` to verify new tests fail**

Run: `pnpm --dir web exec vp test run src/lib/views/__tests__/views.test.ts`
Expected: FAIL.

- [ ] **Step 3: Migrate `SubjectsView.svelte` and `TreeView.svelte` to `createQuery`**

- `SubjectsView.svelte`: replace manual `$state`/`$effect` fetch with `createQuery(() => ({ queryKey: queryKeys.subjects(), queryFn: async ({ signal }) => unwrap(await apiClient.GET("/api/subjects", { signal })) }))`. Keep `document.title = "Groundly"` and `void runStore.attach()` in an `$effect`.
- `TreeView.svelte`:
  - Replace `loadData`/`loadSeq`/`runStore.setHooks` with two `createQuery` calls:
    - `treeQuery` on `queryKeys.tree(subject)` calling `apiClient.GET("/api/subjects/{subject}/tree", { params: { path: { subject } }, signal })`.
    - `attemptsQuery` on `queryKeys.attempts(subject)` calling `apiClient.GET("/api/subjects/{subject}/attempts", { params: { path: { subject } }, signal })` (optional — render empty history if it errors without blocking the tree).
  - Derive `tree = $derived(treeQuery.data?.tree ?? [])`, `title = $derived(treeQuery.data?.title ?? "")`, `attempts = $derived(attemptsQuery.data ?? [])`.
  - Keep a tiny `$effect` watching `subject` to reset `selectedTopicId = null`, `query = ""`, update `document.title`, and call `void runStore.attach()`.

- [ ] **Step 4: Run `views.test.ts` and `svelte-check`**

Run: `pnpm --dir web exec vp test run src/lib/views/__tests__/views.test.ts && pnpm --dir web exec svelte-check --tsconfig ./tsconfig.json`
Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add web/src/lib/views/SubjectsView.svelte web/src/lib/views/TreeView.svelte web/src/lib/views/__tests__/QueryHarness.svelte web/src/lib/views/__tests__/views.test.ts
git commit -m "feat(web): migrate SubjectsView and TreeView to TanStack Query v6"
```

---

### Task 6: Migrate `TestView`, `ResultView`, and `App.svelte` Shell + Remove Legacy Modules

**Files:**
- Modify: `web/src/lib/views/TestView.svelte`
- Modify: `web/src/lib/views/ResultView.svelte`
- Modify: `web/src/lib/run.svelte.ts` (remove transitional `setHooks` / `RunHooks` now that all views use `QueryClient` invalidation)
- Modify: `web/src/App.svelte`
- Modify: `web/src/lib/views/__tests__/views.test.ts`
- Delete: `web/src/lib/api.ts`
- Delete: `web/src/lib/types.ts`
- Delete: `web/src/lib/router.svelte.ts`

**Interfaces:**
- Consumes: `QueryClientProvider`, `createQuery`, `createMutation`, `useQueryClient` (`@tanstack/svelte-query`), `Router`, `route`, `navigate`, `decodeParam` (`web/src/lib/router.ts`), `apiClient`, `unwrap`, `ApiError`, `pageURL`, `testQuery` (`web/src/lib/api/client.ts`), `queryClient`, `queryKeys` (`web/src/lib/api/queries.ts`)
- Produces: Complete SPA shell and all 4 views migrated with zero references to deleted `api.ts`, `types.ts`, or `router.svelte.ts`.

- [ ] **Step 1: Add failing `TestView` & `ResultView` tests to `views.test.ts`**

Update `TestView` and `ResultView` tests in `views.test.ts` to mount inside `QueryHarness.svelte`, and add two explicit assertions:
- `TestView`: calling `qc.invalidateQueries({ queryKey: queryKeys.subject("PDSS") })` while a test is in progress refreshes the `tree` title query without calling `GET /api/subjects/PDSS/test` a second time (Review Focus #4).
- `TestView`: preserves the existing `unknown_question` dead-session assertion (`persister.markDead()` + `sessionStorage` never re-saved).
- `ResultView`: calling `qc.invalidateQueries({ queryKey: queryKeys.subject("PDSS") })` refreshes awaiting open-answer verdicts in place.

- [ ] **Step 2: Run `views.test.ts` to verify new tests fail**

Run: `pnpm --dir web exec vp test run src/lib/views/__tests__/views.test.ts`
Expected: FAIL.

- [ ] **Step 3: Migrate `TestView.svelte`, `ResultView.svelte`, and `App.svelte`, then delete legacy files**

- `TestView.svelte`:
  - `treeQuery = createQuery(() => ({ queryKey: queryKeys.tree(subject), queryFn: ... }))` for `courseTitle` and `scopeTitle` (warm from cache when navigating from `TreeView`).
  - Keep `sessionStorage` draft loader in `$effect`, replacing raw `getJSON` with imperative `unwrap(await apiClient.GET("/api/subjects/{subject}/test", { params: { path: { subject }, query: { ...(topicId != null ? { topic: topicId } : {}), n } }, signal: controller.signal }))` and `controller.abort()` on cleanup.
  - Submit via `createMutation` calling `unwrap(await apiClient.POST("/api/subjects/{subject}/attempts", { params: { path: { subject } }, body }))`:
    - On success: `persister?.drop()`, `void qc.invalidateQueries({ queryKey: queryKeys.subject(subject) })`, and `void navigate("/s/:subject/attempts/:id", { params: { subject, id: String(res.id) } })` (using the encoding behavior pinned in Task 3's `router.test.ts`).
    - On error: if `err instanceof ApiError && err.code === "unknown_question"`, call `persister?.markDead()` and drop stored draft.
- `ResultView.svelte`:
  - Replace `loadResult`/`loadSeq`/`runStore.setHooks` with `attemptQuery` (`queryKeys.attempt(subject, attemptId)`) and `treeQuery` (`queryKeys.tree(subject)`). Keep `void runStore.attach()` in `$effect`.
- `run.svelte.ts`:
  - Delete transitional `RunHooks` and `setHooks` now that no view calls them.
- `App.svelte`:
  - Mount `<QueryClientProvider client={queryClient}>` wrapping header (breadcrumb derived from `route.pathname` + `decodeParam(route.params.subject)`), `<RunStrip />`, and `<Router />` from `sv-router`. Remove manual `onclick={(e) => router.interceptClick(e)}` and `{#if route.kind ...}` switch.
- Delete `web/src/lib/api.ts`, `web/src/lib/types.ts`, and `web/src/lib/router.svelte.ts`.

- [ ] **Step 4: Run full frontend lint, format, type-check, and unit tests**

Run: `pnpm --dir web exec vp lint && pnpm --dir web exec vp fmt --check && pnpm --dir web exec svelte-check --tsconfig ./tsconfig.json && pnpm --dir web exec vp test run`
Expected: PASS with zero errors or unused legacy imports.

- [ ] **Step 5: Commit Task 6**

```bash
git add -A web/src/
git commit -m "feat(web): migrate TestView, ResultView, and App shell to Query v6 and sv-router"
```

---

### Task 7: Rebuild Bundle, Verify Security Hygiene, Update CI & Docs (Decision 51)

**Files:**
- Modify: `groundly/web/static/page.js` (rebuilt)
- Modify: `groundly/web/static/page.css` (rebuilt)
- Modify: `tests/cli/test_cli_serve.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/groundly-spec.md`
- Modify: `docs/tech-stack/tech-stack.md`
- Modify: `.claude/rules/architecture.md`

**Interfaces:**
- Consumes: All tasks 1–6
- Produces: Rebuilt deterministic `page.{js,css}` passing all security/bundle invariants, updated CI workflow with OpenAPI type-drift check, and updated project documentation recording Decision 51.

- [ ] **Step 1: Rebuild the production bundle**

Run: `pnpm --dir web run gen:api && pnpm --dir web run build`
Expected: `groundly/web/static/page.js` and `groundly/web/static/page.css` built cleanly with zero warnings/errors.

- [ ] **Step 2: Run `test_cli_serve.py` to check `AUTHORED_SOURCE` and `SHIPPED_BUNDLE` invariants (Review Focus #5)**

Run: `uv run pytest tests/cli/test_cli_serve.py -v`
- Update `test_page_starts_runs_through_the_api_only` in `tests/cli/test_cli_serve.py` where `'"/runs"'` is now the full OpenAPI path `"/api/subjects/{subject}/runs"`.
- If `test_shipped_bundle_is_built_clean_and_self_contained` flags any dev-only URL from `@tanstack/svelte-query`, `openapi-fetch`, or `sv-router`, verify `NODE_ENV=production` / Vite `define` strips dev-only branches rather than weakening the test.

- [ ] **Step 3: Update `.github/workflows/ci.yml` with the Node-only `gen:api` drift check**

In the `web` job (before `svelte-check`), add:
```yaml
      - name: Generated API types check (up to date)
        run: |
          pnpm run gen:api
          git diff --exit-code src/lib/api/schema.d.ts
```

- [ ] **Step 4: Update docs for Decision 51**

- `docs/groundly-spec.md`: Add Decision 51 (amending Decision 50) recording the offline OpenAPI contract pipeline (`groundly/web/openapi.py` → `web/openapi.json` → `openapi-typescript`), `openapi-fetch` + auth-ready middleware, `@tanstack/svelte-query` v6 with SSE invalidation, `sv-router`, and the decision to stay pure Svelte 5 instead of SvelteKit for laptop-hosted zero-Node distribution.
- `docs/tech-stack/tech-stack.md`: Update the Topic-tree page row to include `openapi-typescript`/`openapi-fetch`, `@tanstack/svelte-query` v6, and `sv-router`.
- `.claude/rules/architecture.md`: Update the Frameworks paragraph to reflect Decision 51 and offline OpenAPI export.

- [ ] **Step 5: Run the complete verification suite (Python + Web)**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pyrefly check && uv run pytest
```
and
```bash
pnpm --dir web run gen:api && git diff --exit-code web/src/lib/api/schema.d.ts && pnpm --dir web exec vp lint && pnpm --dir web exec vp fmt --check && pnpm --dir web exec svelte-check --tsconfig ./tsconfig.json && pnpm --dir web exec vp test run && pnpm --dir web run build && git diff --exit-code groundly/web/static
```
Expected: ALL checks PASS with zero errors and clean `git diff --exit-code`.

- [ ] **Step 6: Commit Task 7**

```bash
git add groundly/web/static/page.js groundly/web/static/page.css tests/cli/test_cli_serve.py .github/workflows/ci.yml docs/groundly-spec.md docs/tech-stack/tech-stack.md .claude/rules/architecture.md
git commit -m "build(web): ship typed OpenAPI/Query/sv-router bundle, CI check, and decision 51"
```
