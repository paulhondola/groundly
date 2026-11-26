# Design Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the four API pieces the "Quiet dark + one orb" page design needs — Check answer, subject card fields, page-side export, and the shared activity log — with no UI change.

**Architecture:** Three changes land in `groundly/web/app.py` on top of `groundly/agents/quiz.py` and `groundly/core/bundle.py`. The activity log is a new foundation module `groundly/core/activity.py` (SQLite at `~/.groundly/activity.db`), written by a FastMCP middleware in `groundly/mcp/activity.py` and by `groundly index`, and read by `GET /api/activity`. Each task is one commit on `feat/design-endpoints` and carries its docs.

**Tech Stack:** Python ≥3.11, FastAPI + strict Pydantic v2, FastMCP 4.1.6 middleware, SQLite (WAL), pytest; `web/` only for the regenerated OpenAPI types.

**Spec:** `docs/superpowers/specs/2026-11-25-design-endpoints-design.md` (read it alongside this plan).

## Global Constraints

- Branch `feat/design-endpoints`; never commit to `main`. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The page writes no rows to `store.db`; its only `progress.db` write stays the submitted attempt.
- Export code never opens `progress.db`, `remote.db` or `activity.db`.
- `activity.db` holds subject names, tool names, counts, file names, host names only — never chunk text, summaries, questions or answers.
- A failed activity write never fails a tool call, a gate or an index run (catch, `log.warning`, continue).
- No response field, doc or description calls an answer key "verified".
- Every SQLite connection: WAL + busy_timeout; schema via `PRAGMA user_version`; private files mode `0600`, created on first write only.
- `activity.py` in `core/` imports nothing from `agents/`, `retrieval/`, `ingestion/` or client layers (`uv run lint-imports`).
- The MCP tool surface stays the eleven entries pinned by `tests/mcp/test_mcp_server.py::test_the_tool_surface_is_exactly_these_eleven_entries`.
- Comments state intent and invariants; deliberate shortcuts carry a `ponytail:` comment naming the ceiling (the spec lists them).
- Gate before every commit: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyrefly check`, `uv run lint-imports` (lefthook runs most of these).
- Python: `.venv/bin/python` / `uv run` only; no bare `python`.

## Review Focus

1. **A check on a question whose last cited chunk was removed** (question deleted by FK cascade) → 404 `unknown_question`, not 500. Test in Task 1.
2. **Two Groundly processes writing `activity.db` at once** (a stdio host and `groundly index`) → both rows land; no "database is locked" escapes. Test in Task 4.
3. **`GET /api/activity` when `activity.db` does not exist** → 200 with empty lists, and the file is still absent afterwards. Test in Task 4.
4. **Export of a subject whose manifest is corrupt** → 409 `manifest_invalid`, no temp file left behind. Test in Task 3.
5. **A tool call that raises** (e.g. `submit_tree` on an unknown subject) → a `tool_end` row is still written, so the orb does not stick on that tool. Test in Task 4.

---

### Task 1: Check answer route

**Files:**
- Modify: `groundly/agents/quiz.py` (add `check_answer` after `_grade`)
- Modify: `groundly/web/schemas.py` (add `CheckAnswerRequest`, `CheckAnswerResponse`)
- Modify: `groundly/web/app.py` (route after `_submit`; source `pdf` flagging)
- Test: `tests/agents/test_quiz.py`, `tests/web/test_app.py`
- Docs: `docs/groundly-spec.md` §7 (open decision 53 with its Check-answer clause), `docs/architecture/agents.md` (the check reveals a key before submit, writes nothing)

**Interfaces:**
- Produces: `quiz.check_answer(store: SubjectStore, question_id: int, selected: list[int]) -> dict` returning `{"correct": bool, "key": list[int], "sources": list[dict]}` (sources in `store.questions()` shape, without `pdf`). Raises `QuizError("unknown_question", _REMOVED)` for a missing id and `QuizError("invalid_text", …)` for an open question or a bad selection (via `_grade`).
- Produces: `POST /api/subjects/{subject}/questions/{id}/check`, `operation_id="checkAnswer"`.

- [ ] **Step 1: Write the failing quiz tests** in `tests/agents/test_quiz.py`

```python
def test_check_answer_agrees_with_submit_and_reveals_key_and_sources(...):
    result = quiz.check_answer(store, q["id"], key_ids)
    assert result["correct"] is True and result["key"] == key_ids
    assert result["sources"][0]["chunk_id"] == chunk_id and result["sources"][0]["text"]
    assert quiz.check_answer(store, q["id"], [wrong_id])["correct"] is False

def test_check_answer_writes_nothing_to_progress(...):
    # progress.answers() and progress.attempts() unchanged after a check

def test_check_answer_refuses_open_and_foreign_options_and_unknown_ids(...):
    # open question -> QuizError reason "invalid_text"
    # an option id from another question -> "invalid_text"
    # two options on a single question -> "invalid_text"
    # id 999999 -> "unknown_question"
```

Reuse the fixtures `test_quiz.py` already uses to build a bank; a multiple-answer question needs `["a","b"]` correct and the full set to grade `True`.

- [ ] **Step 2: Run** `uv run pytest tests/agents/test_quiz.py -k check_answer -v` → FAIL (`check_answer` not defined).
- [ ] **Step 3: Implement `check_answer`** — load with `store.questions(ids=[question_id])` (sources included); empty → `unknown_question`; `_grade(q, {"selected": selected})` for the score (so an open question raises its `invalid_text`); key = ids of options with `correct`.
- [ ] **Step 4: Run** the Step 2 command → PASS.
- [ ] **Step 5: Write the failing web tests** in `tests/web/test_app.py`, reusing `_bank`, `_key`, `ORIGIN`:

```python
def test_check_returns_key_and_pdf_flagged_sources_and_writes_nothing(client, seeded):
    _, (q, _, open_q) = _bank(seeded)
    r = client.post(f"/api/subjects/PDSS/questions/{q['id']}/check",
                    json={"selected": _key(q)}, headers=ORIGIN)
    assert r.status_code == 200
    body = r.json()
    assert body["correct"] is True and body["key"] == _key(q)
    assert body["sources"][0]["filename"] == "extra.pdf" and body["sources"][0]["pdf"] is False
    assert "verified" not in r.text
    assert client.get("/api/subjects/PDSS/attempts").json() == []

def test_check_errors(client, seeded):
    # open question -> 400 invalid_text; unknown id -> 404 unknown_question;
    # unknown subject -> 404 unknown_subject; body {"selected": "x"} -> 400 invalid_body

@pytest.mark.parametrize("headers,status", [({}, 403), ({"Origin": "http://evil"}, 403)])
def test_check_from_elsewhere_is_refused(client, seeded, headers, status): ...

def test_check_on_a_question_removed_with_its_chunk_is_404(client, seeded):  # Review Focus 1
    # remove the material whose chunk q cites (SubjectStore.remove_material or _tamper DELETE),
    # then check -> 404 unknown_question
```

`extra.pdf` is indexed but not written to `materials/`, so `pdf` is `False`; add one assertion with a stored PDF source (`lec.pdf` from `seeded`) being `True` if `_bank` is extended, otherwise leave it to the existing tree test.

- [ ] **Step 6: Run** `uv run pytest tests/web/test_app.py -k check -v` → FAIL (404 route missing).
- [ ] **Step 7: Implement the route** in `create_app`, beside `_submit`:
  - `CheckAnswerRequest(BaseModel)`: `selected: list[SqliteId]`, `model_config` strict like `SubmitAttemptRequest`. `CheckAnswerResponse`: `correct: bool`, `key: list[int]`, `sources: list[SourceItem]`.
  - Dependency `_require_page_json("an answer is checked from this page only")`; validation failure → `_error("invalid_body", "expected {selected}")`.
  - Run through `_quiz` in the thread pool; then set each source's `pdf` with `_stored_pdf(opened, src["filename"]) is not None` (as `_result` does).
  - Map `QuizError` reason `unknown_question` to 404 for this route only (wrap the call; `_quiz` keeps its 400 default).
  - `responses=_protected_responses(400, 403, 404, 415)`, `openapi_extra=_json_body_schema(CheckAnswerRequest)`.
- [ ] **Step 8: Run** the Step 6 command, then the full gate → PASS.
- [ ] **Step 9: Docs** — add decision 53 to `docs/groundly-spec.md` §7 (Paul, 2026-11-25, amending 44 and 52; design link to the spec) with its first clause: the check route reveals one choice question's key and cited sources before submit, writes nothing, and the page's lock on a checked answer is client-side. Add one sentence to `docs/architecture/agents.md` where the page's test flow is described.
- [ ] **Step 10: Commit**

```bash
git add groundly/agents/quiz.py groundly/web/schemas.py groundly/web/app.py tests/agents/test_quiz.py tests/web/test_app.py docs/groundly-spec.md docs/architecture/agents.md
git commit -m "feat(web): check one answer before the test is submitted"
```

---

### Task 2: Subject card fields

**Files:**
- Modify: `groundly/agents/quiz.py` (add `subject_card`)
- Modify: `groundly/web/schemas.py` (`SubjectSummary` gains four optional fields)
- Modify: `groundly/web/app.py` (`_subjects`)
- Test: `tests/agents/test_quiz.py`, `tests/web/test_app.py`
- Docs: decision 53 clause in `docs/groundly-spec.md`

**Interfaces:**
- Consumes: `quiz.mastery`, `ProgressStore.attempts()` (newest first, as `attempt_history` uses it).
- Produces: `quiz.subject_card(store: SubjectStore, progress: ProgressStore | None) -> dict` returning `{}` for a subject with no tree, else `{"topics": int, "leaves": int, "leaves_mastered": int}` plus `"last_test_at": str` when an attempt exists. `progress=None` means unreadable progress: `leaves_mastered = 0`, no `last_test_at`.
- Produces: `SubjectSummary.topics | leaves | leaves_mastered: int | None = None`, `last_test_at: str | None = None` (the route already uses `response_model_exclude_none=True`).

- [ ] **Step 1: Write the failing quiz test**

```python
def test_subject_card_counts_leaves_and_last_test(...):
    # tree: topic A with subtopics A1, A2; topic B with none -> topics 2, leaves 3
    # answer A1 correctly MASTERY_WINDOW times in one attempt -> leaves_mastered 1
    card = quiz.subject_card(store, progress)
    assert card == {"topics": 2, "leaves": 3, "leaves_mastered": 1,
                    "last_test_at": progress.attempts()[0]["submitted_at"]}
    assert quiz.subject_card(empty_store, progress) == {}
    assert quiz.subject_card(store, None) == {"topics": 2, "leaves": 3, "leaves_mastered": 0}
```

- [ ] **Step 2: Run** `uv run pytest tests/agents/test_quiz.py -k subject_card -v` → FAIL.
- [ ] **Step 3: Implement `subject_card`** — `tree = store.tree(with_sources=False)`; leaves via `_leaves_in_order(tree)`; mastered from `mastery(store, progress, tree)` counting leaf entries with `state == "mastered"`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Write the failing web tests**

```python
def test_subjects_carry_card_fields_once_mapped_and_tested(client, seeded):
    leaf, (q, *_) = _bank(seeded)
    _post(client, _attempt(q, _key(q), topic_id=leaf))
    (entry,) = client.get("/api/subjects").json()
    assert entry["topics"] == 1 and entry["leaves"] == 1 and entry["leaves_mastered"] == 0
    assert entry["last_test_at"]

def test_subjects_omit_card_fields_without_a_tree_or_tests(client, home):
    Subject("BARE").initialize()
    (entry,) = client.get("/api/subjects").json()
    assert set(entry) == {"subject", "title", "has_tree"}

def test_an_unreadable_progress_db_costs_only_the_progress_fields(client, seeded):
    # delete PDSS progress.db -> listing still 200, leaves_mastered 0, no last_test_at,
    # and progress.db is NOT recreated by the listing
```

- [ ] **Step 6: Run** `uv run pytest tests/web/test_app.py -k subjects -v` → FAIL.
- [ ] **Step 7: Implement in `_subjects`** — inside the existing `try`, after `has_tree`: open `ProgressStore(subject.progress_db_path)` and call `subject_card`; if the progress read raises `(sqlite3.DatabaseError, RuntimeError)`, call again with `progress=None`. Merge the card into `entry`. Add the `ponytail:` comment from the spec (no cache; per-mtime cache at hundreds of subjects).
- [ ] **Step 8: Run** Step 6, the existing `test_api_lists_subjects_with_has_tree` and the full gate → PASS.
- [ ] **Step 9: Docs** — decision 53 clause: `GET /api/subjects` carries `topics`, `leaves`, `leaves_mastered`, `last_test_at`, computed by the same mastery rule as the topic map.
- [ ] **Step 10: Commit** `feat(web): subject card counts and last test time`.

---

### Task 3: Export routes

**Files:**
- Modify: `groundly/web/schemas.py` (`ExportInfoResponse`)
- Modify: `groundly/web/app.py` (two routes)
- Test: `tests/web/test_app.py`, `tests/web/test_tailscale.py` (paired vs unpaired)
- Docs: decision 53 clause; `docs/infrastructure/security.md` (export from the page, reachable by paired devices; export never reads private files); `.claude/rules/conventions.md` (export is also a page action); `.claude/rules/grounding-and-privacy.md` (privacy boundary line unchanged in substance, names the page export)

**Interfaces:**
- Consumes: `groundly.core.bundle.export_subject(subject: Subject, out: Path, *, include_materials: bool, on_file=None)`; `Subject.load_manifest()` (raises on a bad manifest — check the exception types it raises in `groundly/core/subject.py`/`manifest.py` and catch exactly those).
- Produces: `GET /api/subjects/{subject}/export/info` (`operation_id="exportInfo"`) → `ExportInfoResponse{filename: str, store_bytes: int, materials_bytes: int}`; `GET /api/subjects/{subject}/export?materials=true|false` (`operation_id="exportSubject"`, default `true`) → `FileResponse`, `media_type="application/zip"`, `filename=f"{subject}.groundly"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_export_info_reports_sizes(client, seeded):
    info = client.get("/api/subjects/PDSS/export/info").json()
    assert info["filename"] == "PDSS.groundly"
    assert info["store_bytes"] == (subject_dir("PDSS") / "store.db").stat().st_size
    assert info["materials_bytes"] == len(PDF_BYTES) + len("<script>alert(1)</script>")

@pytest.mark.parametrize("materials,expect_pdf", [("true", True), ("false", False)])
def test_export_downloads_a_bundle_without_progress(client, seeded, materials, expect_pdf, tmp_path):
    r = client.get(f"/api/subjects/PDSS/export?materials={materials}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="PDSS.groundly"' in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith("store.db") for n in names)
    assert not any("progress" in n or "activity" in n or "remote" in n for n in names)
    assert any(n.endswith("lec.pdf") for n in names) is expect_pdf

def test_export_leaves_no_temp_file(client, seeded, monkeypatch, tmp_path):
    # monkeypatch tempfile.tempdir to tmp_path / "t"; after the response the dir is empty

def test_export_of_a_corrupt_manifest_is_409_and_leaves_nothing(client, seeded, ...):  # Review Focus 4
    # write garbage to the subject's manifest -> 409 manifest_invalid; temp dir empty

def test_export_unknown_subject_is_404(client, seeded): ...
```

In `tests/web/test_tailscale.py`, following its existing paired/unpaired helpers: a paired session gets 200 from both export routes; an unpaired request does not.

- [ ] **Step 2: Run** `uv run pytest tests/web -k export -v` → FAIL.
- [ ] **Step 3: Implement** — both routes `def` (thread pool). `export`: `_open_subject` → 404; `load_manifest()` failure → `_error("manifest_invalid", str(e), 409)`; build into `tempfile.mkstemp(suffix=".groundly")`, delete it in `finally` if building raised, else return `FileResponse(path, background=BackgroundTask(path.unlink, missing_ok=True))`. `info`: on-disk sizes of `store_db_path` and every file under `materials_dir`. Add the spec's `ponytail:` comment (whole bundle built before the first byte). No Origin/JSON dependency; no Tailscale special case (the middleware already gates unlisted routes on the paired session).
- [ ] **Step 4: Run** Step 2 and the full gate → PASS.
- [ ] **Step 5: Docs** as listed in **Files**.
- [ ] **Step 6: Commit** `feat(web): export a subject's bundle from the page`.

---

### Task 4: Activity log, its writers and `GET /api/activity`

**Files:**
- Create: `groundly/core/activity.py` (store: write, prune, read model)
- Modify: `groundly/core/paths.py` (`activity_db_path() -> Path`, beside `remote_db_path`)
- Create: `groundly/mcp/activity.py` (`ActivityMiddleware`)
- Modify: `groundly/cli/mcp.py` (stdio → `"mcp_stdio"`, `--http` → `"mcp_http"`; `atexit` `session_end` for stdio)
- Modify: `groundly/cli/serve.py` (`start` → `"mcp_http"`)
- Modify: `groundly/cli/subjects.py` (`index` hooks `on_discovered`/`on_event` and the results)
- Modify: `groundly/web/schemas.py` (`ActivityResponse`, `ActivityHost`, `ActivityIndexing`, `ActivityRecent`), `groundly/web/app.py` (route)
- Test: `tests/core/test_activity.py` (create), `tests/mcp/test_mcp_server.py`, `tests/cli/` (index test file that already drives `groundly index`), `tests/web/test_app.py`
- Docs: decision 53 clause; spec storage section; `docs/architecture/overview.md`, `docs/architecture/data-model.md`, `docs/infrastructure/security.md`; `.claude/rules/architecture.md` (writers/reader of `activity.db`), `.claude/rules/grounding-and-privacy.md` (`activity.db` never exported); memory index line is Paul's call, not the builder's
- Generated: `web/openapi.json`, `web/src/lib/api/schema.d.ts`

**Interfaces:**
- Produces (`groundly/core/activity.py`):
  - `SOURCES = ("mcp_stdio", "mcp_http", "index")`, `ACTIVITY_USER_VERSION = 1`, `RETENTION_SECONDS = 7 * 86400`, `HTTP_LIVE_SECONDS = 600`, `LINGER_SECONDS = 5`, `RECENT_LIMIT = 20`.
  - `record(source: str, event: str, *, subject: str | None = None, client: str | None = None, session: str | None = None, detail: dict | None = None, path: Path | None = None, now: float | None = None) -> None` — best-effort (never raises); prunes rows older than `RETENTION_SECONDS` when `event` is `session_start` or `index_start`.
  - `snapshot(*, path: Path | None = None, now: float | None = None, pid_alive: Callable[[int], bool] | None = None, topic_title: Callable[[str, int], str | None] | None = None) -> dict` — the `GET /api/activity` body; a missing file → `{"hosts": [], "indexing": [], "recent": []}` without creating it.
  - `TOOL_ORB: dict[str, str]` — the spec's tool → orb table.
- Produces (`groundly/mcp/activity.py`): `class ActivityMiddleware(Middleware)` with `__init__(self, source: str)`; `on_initialize` → `session_start` (client = clientInfo name); `on_call_tool` → `tool_call` before, `tool_end` in `finally`, and after a successful result: `submit_tree` with `accepted: true` → `tree_accepted {topics, subtopics}` (counted from the submitted `tree` argument); `submit_questions` with a non-empty `accepted` → `questions_accepted {topic_id, count}`; `review_answers` whose result has `recorded` → `answers_reviewed {count}`.
- Produces: `GET /api/activity` (`operation_id="getActivity"`) → `ActivityResponse` exactly as the spec's read model (`last_done_at`, no server `done`).

- [ ] **Step 1: Write the failing store tests** in `tests/core/test_activity.py` (all with an explicit `path` under `tmp_path` and an explicit `now`):

```python
def test_record_creates_a_private_wal_file_on_first_write(tmp_path): ...  # mode 0600, user_version 1, journal_mode wal
def test_snapshot_of_a_missing_file_is_empty_and_creates_nothing(tmp_path): ...  # Review Focus 3
def test_record_never_raises(tmp_path): ...  # path is a directory -> no exception
def test_stdio_host_is_live_while_its_pid_is(tmp_path): ...  # pid_alive=lambda p: p == 111
def test_http_host_expires_after_ten_minutes(tmp_path): ...  # last event now-601 -> gone
def test_session_end_removes_the_host(tmp_path): ...
def test_orb_holds_an_unfinished_tool_then_lingers_then_listens(tmp_path):
    # tool_call submit_tree at t=0, no end -> "mapping" at t=60
    # tool_end at t=60 -> "mapping" at t=64, "listening" at t=66
def test_last_done_at_is_the_newest_accepted_event(tmp_path): ...
def test_indexing_lists_live_unfinished_runs_with_progress(tmp_path):
    # index_start {files: 3}, index_file n=2 of 3 file "b.pdf" -> done 2, total 3, current_file "b.pdf"
    # index_done -> gone; dead pid -> gone
def test_recent_texts_newest_first_and_capped(tmp_path):
    # tree_accepted -> "Built topic map"; questions_accepted count 10 with topic_title -> "Wrote 10 questions · Locks"
    # answers_reviewed 3 -> "Graded 3 answers"; index_done indexed 4 -> "Indexed 4 files"; 25 events -> 20
def test_old_rows_are_pruned_on_session_start(tmp_path): ...
def test_two_processes_write_at_once(tmp_path): ...  # Review Focus 2: multiprocessing, 2×200 records, 400 rows
def test_windows_liveness_falls_back_to_recency(tmp_path, monkeypatch): ...  # monkeypatch os.name / sys.platform as activity.py checks it
```

- [ ] **Step 2: Run** `uv run pytest tests/core/test_activity.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `groundly/core/activity.py`** with the spec's `events` table and index. Connection setup mirrors `groundly/core/progress.py` (`_configure`: WAL, busy_timeout; refuse a newer `user_version`). `path` defaults to `paths.activity_db_path()`. Default `pid_alive`: on POSIX `os.kill(pid, 0)` (`ProcessLookupError` → False, `PermissionError` → True); on Windows `None` → the recency rule. Orb and liveness rules exactly as the spec's "Read model". Add the spec's `ponytail:` pid-reuse comment.
- [ ] **Step 4: Run** Step 2 → PASS.
- [ ] **Step 5: Write the failing MCP tests** in `tests/mcp/test_mcp_server.py`, using its existing in-memory FastMCP client with `ActivityMiddleware("mcp_stdio")` added and `GROUNDLY_HOME` under `tmp_path`:

```python
def test_tool_calls_are_logged_start_and_end(...): ...  # search -> tool_call + tool_end rows, subject set, no query text stored
def test_a_raising_tool_still_logs_its_end(...): ...  # Review Focus 5: submit_tree on unknown subject
def test_accepted_submissions_are_logged_with_counts(...): ...  # submit_tree accepted -> tree_accepted {topics, subtopics}
def test_a_broken_activity_db_never_fails_a_tool(...): ...  # activity path is a directory -> tool result unchanged
def test_no_course_text_reaches_the_log(...): ...  # after search/submit_questions, no row's detail contains the stem or chunk text
```

- [ ] **Step 6: Run** `uv run pytest tests/mcp -k activity -v` → FAIL.
- [ ] **Step 7: Implement `ActivityMiddleware`** — client name from the initialize request's `clientInfo.name`; `session` = `context.fastmcp_context.session_id` for `mcp_http`, `None` for stdio. Register it in `groundly/cli/mcp.py` before `run()` (`"mcp_stdio"` or `"mcp_http"`) and in `serve.start` (`"mcp_http"`); do not register it at import of `groundly.mcp.server` (tests and `serve` without MCP must not log). stdio: `atexit.register(activity.record, "mcp_stdio", "session_end")`.
- [ ] **Step 8: Run** Step 6 and `tests/mcp` in full (eleven-tool surface unchanged) → PASS.
- [ ] **Step 9: Write the failing index test** in the CLI test file that already runs `groundly index` via `CliRunner`: after indexing two files, `activity.db` holds `index_start {files: 2}`, two `index_file` rows (`n`, `of`, `file`, `status`), and `index_done {indexed, failed}`; a run that fails with `RuntimeError` writes `index_failed {reason}`.
- [ ] **Step 10: Implement** in `groundly/cli/subjects.py::index`: `index_start` inside `on_discovered`; `index_file` in `on_event` when `stage in set(Status)` (counter `n`); `index_done` from `results` after `index_paths` returns; `index_failed` in the existing `except` before `_fail`.
- [ ] **Step 11: Run** the index test → PASS.
- [ ] **Step 12: Write the failing route tests** in `tests/web/test_app.py`:

```python
def test_activity_route_reads_the_log(
    client, home
): ...  # record a stdio session_start for os.getpid() + questions_accepted with topic_id -> hosts[0]["orb"] == "listening", recent text uses the topic title
def test_activity_route_without_a_log_is_empty_and_creates_none(
    client, home
): ...  # Review Focus 3 at the route
```

Paired/unpaired Tailscale access to `/api/activity` in `tests/web/test_tailscale.py`, as in Task 3.

- [ ] **Step 13: Implement the route** — `def _activity()` (thread pool), `snapshot(topic_title=…)` where the lookup opens the subject's `SubjectStore` read-only and returns the node's title or `None`; `responses=_protected_responses()`.
- [ ] **Step 14: Run** Step 12 and the full gate → PASS.
- [ ] **Step 15: Regenerate the API types** — `uv run python -m groundly.web.openapi` (check the module's `__main__`/docstring for the exact output flag) → `web/openapi.json`; then `cd web && pnpm gen:api && pnpm check` → no errors. `git diff web/openapi.json` shows the four new operations and the `SubjectSummary` fields.
- [ ] **Step 16: Docs** as listed in **Files**; decision 53 complete.
- [ ] **Step 17: Commit** `feat: shared activity log for hosts, indexing and the page orb` including the generated files.

---

### After Task 4

- [ ] Run `spec-guardian` and `security-reviewer` on `git diff main...feat/design-endpoints`; fix what they confirm.
- [ ] Run `graphify update .` (project rule after code changes).
- [ ] Ask Paul before pushing or opening a PR.
