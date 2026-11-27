# Web Agent Switcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let students launch mapping, question writing, and grading from the page with a visible, remembered provider, model, and effort.

**Architecture:** Extend the existing page runner and typed API. Keep one runner lifecycle across providers, store the user's selection in the browser, and use the existing MCP gates for writes. A private map session scopes tool access to one subject and carries the first-map precondition into the tree write transaction.

**Tech Stack:** Python, FastAPI, FastMCP, pytest, Svelte 5, Bits UI, TypeScript, offline OpenAPI, Vite+.

**Spec:** [Web agent switcher and headless topic mapping](../specs/2026-11-27-web-agent-switcher-design.md)

## Global Constraints

- Groundly holds no key, constructs no model client and opens no direct model connection. Run the student's local CLI with fixed argv and no shell. Preserve the same-origin, JSON, request-size, and paired-device checks.
- macOS and Linux. Discover on the serving process's `PATH`; the provider must also have a verified integration. Resolve again on launch. The browser sends IDs and nullable overrides. Missing CLI or metadata never selects a different provider automatically.
- One active web run per serving process, with the current Stop, 20-minute timeout, and SSE reconnect behavior. Keep the run's selection immutable and all provider sessions ephemeral.
- Retain the public MCP tool names, the topic and question gates, and the page's read-only role over `store.db`. The map run's MCP scope is one subject and four tools. Its first-map precondition is atomic with the tree write.
- Browser-local preferences are shared across actions and private to an origin. Persist only an accepted launch. Use one keyboard-operable popover, a 200–240 ms provider expansion, and immediate reduced-motion transitions.
- Work on `codex/web-agent-switcher`. Commit implementation tasks after their checks and review. Run `graphify update .` after code changes.

## Review Focus

1. An external client commits a map during topic retrieval. The page run cannot overwrite it. Task 2 exercises two store handles.
2. A CLI is replaced with an incompatible build or removed between discovery and launch. Re-detection rejects the old choice before spawn. Task 1 exercises both states.
3. A broken catalog lookup and a confirmed removal look different. Task 3 preserves an explicit selection through a transient failure and requires a replacement on confirmed removal.
4. A stale stored effort or two rapid clicks cannot launch a substituted, duplicate, or incorrectly attributed run. Task 3 tests the pending action and submission guard.
5. A model reports success without a stored tree, or a user stops after a tree commits. Task 4 tests the copy and destination against persisted state.

## File map

| Area | Files and responsibilities |
| --- | --- |
| Web backend | `groundly/web/runs.py` keeps lifecycle, prompt, and progress. New `groundly/web/run_providers.py` owns discovery, selection validation, and host launch configuration. `groundly/web/schemas.py` and `app.py` define and serve the requests, capabilities, and snapshots. |
| Map gate and transport | `groundly/core/store.py` checks first-map atomically. `groundly/agents/topics.py` returns gate results. `groundly/mcp/server.py` carries the condition. New `groundly/mcp/page_map.py` scopes stdio sessions, registered by `groundly/cli/mcp.py`. Keep forwarding in `groundly/mcp/forward.py`. |
| Browser state and controls | `web/src/lib/run.svelte.ts` owns run state and API calls. New `web/src/lib/run-selection.svelte.ts` owns the picker and remembered configuration. New `web/src/lib/components/RunAction.svelte` renders the action and switcher, shared by `SubjectsView.svelte`, `TreeView.svelte`, and `ResultView.svelte`. `RunStrip.svelte` shows the launched configuration. |
| Build and tests | `web/openapi.json`, `web/src/lib/api/schema.d.ts`, and `groundly/web/static/page.{js,css}` are generated. Extend the existing runner, MCP, store, remote, and view tests. |

### Task 1: Discover providers and launch with a validated configuration

**Files:** Create `groundly/web/run_providers.py`. Modify `groundly/web/{app,runs,schemas}.py` and the generated REST contract. Test in `tests/web/{conftest,test_runs,test_openapi,test_remote}.py`.

**Interfaces:**

- `RunSelection` in `schemas.py` has `provider: Literal["claude", "codex"]`, `model: str | None`, and `effort: str | None`. Start requests carry a `selection`, and snapshots expose `selection: RunSelection`. Requests use strict validation and forbid extra fields. During the API/front-end transition, missing `selection` in a write/grade request means Claude with both overrides null. The browser sends it explicitly after Task 3.
- `ProviderInfo` has `id`, `label`, `models: list[ModelInfo] | None`, and `default_model: str | None`. A `ModelInfo` has `id`, `label`, and `efforts: list[str]`. Null catalog means lookup unavailable. An empty list means only defaults are offered. Set `default_model` only when the integration can establish that model for this isolated launch. If null, a null model accepts only a null effort. `GET /api/runs` keeps `available`, `reason`, `target`, and `run` and adds `providers: list[ProviderInfo]`.
- `discover_providers() -> list[ProviderInfo]` and `resolve_provider(selection: RunSelection) -> ResolvedProvider` live in `run_providers.py`; the resolved object carries the server-only executable and validated selection. A lookup or validation failure maps to `provider_unavailable`, `catalog_unavailable`, or `invalid_selection`. Keep the existing platform and no-host reason when no CLI is available.
- `Runner.start(kind, subject, topic_id, prompt, *, selection: RunSelection)` owns the run lock, starts the resolved provider, and copies the selection into the snapshot.

- [ ] **Write failing discovery and API tests.** With fake executables, assert `[p["id"] for p in response["providers"]] == ["claude"]` when only the Claude integration is enabled, even when a Codex binary and active Codex MCP client exist. Remove the resolved executable or replace it with an incompatible build before POST and assert `503` and zero spawns. Verify `400` and zero spawns for unknown provider IDs, paths as model IDs, and unmatched explicit effort, including explicit effort when the default model is unknown. Assert an unpaired request cannot read inventory or launch, and an Origin mismatch still starts nothing.
- [ ] **Run the backend tests.** Run `uv run pytest tests/web/test_runs.py tests/web/test_remote.py tests/web/test_openapi.py -q`. Expect the provider metadata and selection tests to fail first.
- [ ] **Implement Claude discovery and launch.** Use `shutil.which`, a bounded CLI version/capability check off the async event loop, and cache capability results by executable identity and version. A replaced executable invalidates the cache. Use the documented `sonnet`, `opus`, and `haiku` aliases when the CLI accepts model overrides, and expose a conservative tested effort matrix for each alias. Omit both flags in a default run, and append only validated `--model` and `--effort` values for explicit selections. Preserve existing init checks, packaged skills, no-shell invocation, process group, and output bounds. An unknown Claude capability can retain the previously supported default flow if the existing isolation contract still passes.
- [ ] **Pin the launch behavior.** Extend the fake CLI tests to assert default argv lacks both flags, explicit argv passes the requested pair, and a missing CLI/stale pair cannot reuse a previous path or fall back. Assert snapshots contain the launched configuration after a reload. Cover sign-in errors and process startup failure with provider-specific human-readable messages and a clean runner slot.
- [ ] **Regenerate and verify.** Run `uv run python -m groundly.web.openapi` at the root and `pnpm run gen:api` in `web/`. Rerun the backend tests and the web type check. Commit as `feat(web): discover and configure local agent providers`.

### Task 2: Add a subject-scoped first-map run

**Files:** Modify `groundly/core/store.py:796`, `groundly/agents/topics.py:191`, `groundly/mcp/server.py:236`, `groundly/cli/mcp.py`, `groundly/web/{app,runs,schemas}.py` and the generated contract. Create `groundly/mcp/page_map.py`. Test in `tests/{core/test_store,agents/test_topics,mcp/test_mcp_server,mcp/test_forward,web/test_runs}.py`.

**Interfaces:**

- `SubjectStore.replace_tree(tree, *, if_unmapped: bool = False) -> None` raises `FirstMapConflict` if topics already exist and `if_unmapped` is true.
- `topics.submit_tree(..., if_unmapped: bool = False)` maps the conflict to `{"accepted": false, "rejections": [{"reason": "map_already_exists", ...}]}`. MCP's existing `submit_tree` takes the additive optional condition and keeps the same name, result schema, and default behavior.
- `PageMapMiddleware(subject: str)` lists and permits only `outline`, `get_page`, `search`, and `submit_tree`, rejects other subjects including page URIs, and forces `if_unmapped=true` on submissions. Expose no public resources or prompts in this private session. Keep the usual MCP server unchanged when no scope is supplied.
- `POST /api/subjects/{subject}/runs` accepts `kind: "map"` with `selection` and no topic ID. It returns `409 map_already_exists` for a mapped subject and `nothing_to_map` for an empty index. Existing unknown-subject and malformed-body errors still apply.

- [ ] **Write failing store and gate tests.** Begin a page-map submission against an empty tree, have another handle commit a tree before the first submission reaches the write transaction, and assert the page submission rejects with `map_already_exists`. Assert the winning topic IDs, titles, and citations are unchanged. Assert ordinary `submit_tree` without the condition still supports remapping.
- [ ] **Run the store and gate tests.** Run `uv run pytest tests/core/test_store.py tests/agents/test_topics.py -q`. Expect the first-map tests to fail.
- [ ] **Implement the first-map condition.** Check the existing topic IDs after `BEGIN IMMEDIATE` and before writes. Map the dedicated exception into the existing normal gate-rejection structure. Maintain the existing structural and relevance checks.
- [ ] **Write failing MCP scope and forwarding tests.** Assert the scoped session lists precisely the four tools, offers no resources or prompts, rejects another subject even with a crafted `get_page` URI, and refuses deletion or an unrelated write attempted by name. A supplied false or omitted condition still reaches both the local gate and the forwarded gate as true. When the forward fails after dispatch, assert it never retries the write locally.
- [ ] **Implement the private stdio scope.** Have the map integration launch `python -P -m groundly mcp` with a hidden `--page-map-subject` argument carrying the validated subject. Register the scope middleware before forwarding so only checked calls reach it and the forwarded `submit_tree` carries the forced condition. Reuse decision 54's forwarding to the live `start`; without it the same scoped session runs locally. Check both subject-bearing arguments and Groundly URI forms. Keep the tool-list and resource boundary consistent with call enforcement.
- [ ] **Write failing runner tests.** Assert the route rejects a mapped or empty index without spawning. Assert a valid map uses the scoped same-installation MCP command and the packaged `groundly-map` skill. Test the exact tool set, no topic ID, and a progress refresh for `submit_tree` with `accepted: true`. A rejection and a fabricated final success message both leave a run without a confirmed map.
- [ ] **Implement map startup and status.** Reuse the route's subject/store lookup and read-only preflight. Build a fixed first-map prompt with the subject ID and stop-on-conflict behavior. Attach the packaged skill with a page-mode preface that directs the run straight to `outline`, skips interactive setup, indexing, remapping and subagents, and preserves the map shape and gate instructions. Keep the request's arguments separate from retrieved course text. Add outline/submission progress, report a gate-accepted map through the snapshot, and keep the existing finish/error semantics. A map run that ends normally without its own accepted submission reports that outcome explicitly. Leave the accepted-write gate as the authority across Stop/disconnect races.
- [ ] **Regenerate, verify, and commit.** Regenerate OpenAPI and TS definitions as in Task 1. Run `uv run pytest tests/core/test_store.py tests/agents/test_topics.py tests/mcp tests/web -q`; confirm the public eleven-tool test stays green. Commit as `feat(web): create first topic maps through scoped MCP`.

### Task 3: Add the shared action and switcher

**Files:** Create `web/src/lib/run-selection.svelte.ts` and `web/src/lib/components/RunAction.svelte`. Modify `web/src/lib/run.svelte.ts`, `web/src/lib/components/RunStrip.svelte`, and `web/src/lib/views/{TreeView,ResultView}.svelte`. Test in `web/src/lib/__tests__/run-selection.test.ts`, `web/src/lib/__tests__/run.test.ts`, and `web/src/lib/views/__tests__/views.test.ts`.

**Interfaces:**

- `createRunSelectionStore(storage)` exposes the current `RunSelection | null`, accepts an inventory update, resolves `selectProvider(id)`, `selectModel(id | null)`, and `selectEffort(id | null)`, and persists via `rememberLaunch(selection)`. Use one versioned `groundly-run-selection` browser storage key.
- `runStore.start(subject, body): Promise<RunSnapshot | null>` returns the accepted snapshot or null after reporting the error. Consumers only persist `snapshot.selection`. Return null immediately while starting or busy.
- `RunAction` takes `subject`, `kind`, optional `topicId`, and `label`. It reads both stores, owns this action's local popover/pending state, and captures the valid selection before starting.

- [ ] **Write failing state and view tests.** Assert the unconfigured primary button opens setup with zero POSTs, then **Run** sends one POST. With persisted Claude settings, a repeat click posts the identical pair. Switching back to Claude restores its previous pair. A blocked storage API still allows a run. A null catalog keeps the explicit stored pair, shows a temporary unavailable message, and offers a refresh or explicitly confirmed default launch. A confirmed missing model requires a valid replacement and explicit Run. Assert a rejected start keeps stored values, rapid double click posts once, and a provider change during a run leaves the strip's original label.
- [ ] **Run the web tests.** From `web/`, run `pnpm exec vp test run src/lib/__tests__/run-selection.test.ts src/lib/__tests__/run.test.ts src/lib/views/__tests__/views.test.ts`. Expect the new controls and state cases to fail.
- [ ] **Implement state and actions.** Follow the theme store's defensive browser-storage pattern. Keep staged edits separate from the last accepted launch. A model change resets an unsupported draft effort to Default. Invalidated saved pairs require explicit repair. Preserve the full configuration across actions. Replace the write and grade button implementations while retaining their existing eligibility and fallback content.
- [ ] **Implement and exercise the popover.** Reuse Bits UI focus and dismissal behavior. Keep one popover with an expanding provider row, a scrollable model list and a compact effort list. Show Default options and selection indicators. Animate provider expansion in 200–240 ms and honor `prefers-reduced-motion`. Check Tab, arrow keys within options, Escape, focus return, wrapping at 375 px, and immediate chip activation. Make zero- and one-provider states operable.
- [ ] **Run the checks and commit.** Run `pnpm test`, `pnpm run check`, and `pnpm run build` in `web/`. Commit as `feat(web): remember and expose page-run configuration`.

### Task 4: Put Create topic map on the subject card

**Files:** Modify `web/src/lib/views/SubjectsView.svelte`, `web/src/lib/components/RunStrip.svelte`, and `web/src/lib/run.svelte.ts`. Test in `web/src/lib/__tests__/run.test.ts` and `web/src/lib/views/__tests__/views.test.ts`.

**Interfaces:** Use `RunAction` with `kind="map"` and `label="Create topic map"`. Use the run snapshot and subject queries to display progress and a persisted-map destination with `pageURL(subject)`.

- [ ] **Write failing view and store tests.** An indexed course with an empty tree shows both the action and **Copy prompt**. A course without an index or a course already mapped has no first-map action. While a page map is in flight, show its progress and disable second starts. An external run still displays mapping activity. A successful map moves the card and exposes **Open topic map** in the run result. Host success with zero committed topics leaves the card retryable. Stop after a commit, a concurrent winner, and reload all show the map once the subject response confirms it.
- [ ] **Run the focused web tests.** Run `pnpm exec vp test run src/lib/__tests__/run.test.ts src/lib/views/__tests__/views.test.ts`. Expect the mapping cases to fail.
- [ ] **Wire the card.** Attach the page's existing runner state, keep the manual fallback, and invalidate the subject list and subject tree on accepted map writes and settlement. Use refreshed storage state for the link and card placement. If Stop or an external map lands during a refresh, use the existing activity reconciliation so the next settled state triggers a fresh read. Show a specific error and **Copy prompt** when the host cannot handle the outline.
- [ ] **Verify and commit.** Run `pnpm test`, `pnpm run check`, and `pnpm run build`. Exercise the subject, write, and result views with a fake CLI over a local browser, including the narrow and reduced-motion cases. Commit as `feat(web): launch topic mapping from the subject list`.

### Task 5: Qualify the Codex integration

**Files:** Use `groundly/web/run_providers.py` and `groundly/web/runs.py`, plus `tests/web/{conftest,test_runs}.py`. If a verified integration needs a separate protocol implementation, keep it in `groundly/web/codex_run.py`.

**Interfaces:** Use the Task 1 provider inventory and resolved launch boundary. Reuse the Task 2 scoped map transport. Emit the existing run snapshot and SSE events.

- [ ] **Verify the tool boundary before implementing support in discovery.** Start with the locally observed Codex CLI 0.172.0. Evaluate fixed isolated configuration and a pre-turn inventory/error check, using a harmless disposable fixture with a project instruction file and a fake unrelated MCP server. Check exclusion of global and project hooks, extra tools and servers, filesystem/shell tools, and subagents. Check that tool calls cannot occur before the boundary is established and that ephemeral mode keeps the run out of saved sessions. Verify this independently of the student's API key or actual provider traffic. Use the [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) and local CLI help to verify each claimed control. `--ignore-user-config` alone leaves other configuration layers.
- [ ] **Choose the verified runtime.** Prefer the simpler CLI path if it can enforce and establish the boundary before any tool can run. Evaluate local App Server only if it exposes an enforceable pre-turn boundary the CLI lacks. If neither passes, record the exact gap and keep Codex out of discovery. Continue to final verification with Claude Code.
- [ ] **Write failing tests for the qualified path.** When Codex qualifies, a fake protocol fixture must expose both providers, run map/write/grade with the selected options, report the exact supported model/effort pairs, and reject an unexpected tool/server before use. Assert supervisor-owned Stop, timeout, typed errors, and a single active run across providers.
- [ ] **Implement and verify the qualified path.** Run the selected runtime locally with existing account login. Use the documented [App Server model list](https://learn.chatgpt.com/docs/app-server#list-models-modellist) with a bounded lifecycle, and only expose explicit model and effort choices confirmed compatible with that runtime. Normalize events into the existing run stream and surface provider errors. Run the failing tests before implementation and rerun `uv run pytest tests/web/test_runs.py -q` afterward. Commit as `feat(web): enable isolated Codex page runs` only after isolation is demonstrated.

### Task 6: Record the decision and verify the release

**Files:** `docs/groundly-spec.md`, `docs/use-cases/student-modes.md`, `docs/architecture/{overview,agents,data-model}.md`, `docs/infrastructure/security.md`, `docs/tech-stack/tech-stack.md`, and `.claude/rules/{architecture,conventions,grounding-and-privacy}.md`. Check README and other current product docs for affected claims.

- [ ] **Amend the docs.** Use the repository's decision workflow to amend 46 and 50 with the actual shipped providers, scoped first-map transport, careful model/effort handling, and browser preference boundary. Add the new acceptance cases and keep decision 37 unchanged. If the Codex qualification failed, document the check that failed and why discovery excludes it.
- [ ] **Run the full gates.** At the root run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyrefly check`, `uv run lint-imports`, and `uv run python -m groundly.web.openapi --check`. In `web/`, run `pnpm run gen:api`, `pnpm run check`, `pnpm test`, and `pnpm run build`. Confirm generated artifacts are committed and reproducible. Run `graphify update .` for the code changes.
- [ ] **Smoke each enabled provider.** On a disposable indexed course under the student's logged-in CLI, create the first map, write a thin topic's questions, take a test, and grade a written answer. Repeat a run with the remembered selection after reload. Record the CLI versions, the supported platforms tested, and whether both `serve` and `start` routes pass.
- [ ] **Review and commit.** Run the repo's spec-guardian and security-reviewer checks with particular attention to CLI startup, the read/write boundary, pairing, the first-map race, and preserved question-citation guarantees. Resolve material findings and commit the release docs and remaining verification changes.
