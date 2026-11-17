# Page Chores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add **Write questions** and **Grade now** buttons to the `groundly serve`/`start` page. Each button starts one isolated, headless run of the student's own Claude Code (`claude -p`) on the skill's write or grade flow, and the page shows the run's steps and summary.

**Architecture:**
- A new `groundly/web/runs.py` holds the pure pieces and one `Runner`:
  - the pure pieces: availability, prompt, argv, MCP config, init check and stream-to-steps;
  - the `Runner` spawns at most one `claude` process, reads its `stream-json` on a thread and keeps that run's state in memory.
- `web/app.py` gets three routes. The two POST routes share a guard extracted from test Submit.
- `page.js` adds the buttons, a status strip and polling.
- Docs record decision 46.

**Tech Stack:** Python 3.11+, Starlette, `subprocess`/`threading` (stdlib), vanilla JS, pytest with a fake `claude` script.

**Spec:** `docs/superpowers/specs/2026-11-17-page-chores-design.md`. Read it first; this plan argues from it.

## Global Constraints

- **The run's argv is exactly the spec's flag set:**
  - `-p <prompt>`, `--strict-mcp-config`, `--mcp-config <json>`
  - `--setting-sources ""`, `--tools ""`
  - `--allowedTools <flow list>`, `--permission-mode dontAsk`
  - `--append-system-prompt-file <packaged SKILL.md>`
  - `--output-format stream-json`, `--verbose`, `--no-session-persistence`
  - No `--model`. Exec'd as a list, never through a shell.
- **The run's process settings:**
  - cwd: `groundly_home() / ".host-run"`, one fixed directory;
  - stdin: `subprocess.DEVNULL`;
  - the environment is inherited unchanged.
- **Allowed tools:**
  - write: `mcp__groundly__list_subjects`, `mcp__groundly__progress`, `mcp__groundly__search`, `mcp__groundly__get_page`, `mcp__groundly__submit_questions`;
  - grade: `mcp__groundly__review_answers`, `mcp__groundly__get_page`.
- **Values:** `TARGET_QUESTIONS = 10`, `RUN_TIMEOUT_S = 20 * 60`, `MAX_STEPS = 50`, `MAX_SUMMARY_CHARS = 2000`, poll interval 2 s.
- **Platforms:** macOS and Linux. On `sys.platform == "win32"`, availability is `unsupported_platform`.
- **Reasons, with status codes:**
  - `run_in_progress` 409, `no_host` 503, `unsupported_platform` 503
  - `unknown_subject` 404, `unknown_topic` 404
  - `nothing_to_write` 400, `nothing_to_grade` 400, `invalid_body` 400
  - run-level failures, not HTTP: `isolation_failed`, `groundly_not_connected`, `host_error` (an error `result` from `claude`), `host_exit` (an exit with no `result`), `timeout`
- **Fixed rules from the project:**
  - The page writes no rows to `store.db` and loads no model.
  - Every API string reaches the DOM through `textContent`.
  - No layer-3 text (titles, summaries) goes into the prompt.
  - Groundly reads none of Claude Code's settings.
- **Tests never call a model.** They put a fake `claude` on PATH.
- **Commits go on branch `page-chores`.** Never commit on `main`. End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **`claude` dies without a `result` event** (crash, killed, OOM). The run must end `failed` with the exit code and the tail of stderr, never stay `running`. Pinned in Task 2.
2. **The student is not logged in to Claude Code.** `claude` prints a `result` with `is_error: true` ("Please run /login"). The run must end `failed` showing that text. Pinned in Task 2.
3. **Junk on stdout** (a non-JSON warning line, or one line of several MB). Those lines are skipped, the run still finishes, and memory stays bounded. Pinned in Task 2.
4. **The page process stops mid-run** (Ctrl-C on `serve`). The `claude` process and its stdio `groundly mcp` child must be gone, with no orphan using plan usage. Pinned in Task 2 (`shutdown`) and Task 3 (lifespan).
5. **Groundly's MCP server fails to connect inside the run** (a broken install, or `/mcp` not reachable). The init check ends the run `failed` with `groundly_not_connected`, before any turn, separately from `isolation_failed`. Pinned in Task 1.

---

### Task 1: The pure pieces of a run, and `python -m groundly`

**Files:**
- Create: `groundly/__main__.py`
- Create: `groundly/web/runs.py`
- Test: `tests/web/test_runs.py`

**Interfaces:**
- Produces (all in `groundly.web.runs`):
  - constants `TARGET_QUESTIONS`, `RUN_TIMEOUT_S`, `MAX_STEPS`, `MAX_SUMMARY_CHARS`, `WRITE_TOOLS: tuple[str, ...]`, `GRADE_TOOLS: tuple[str, ...]`
  - `class RunError(Exception)` with `reason: str`, `detail: str`, `status: int`, built as `RunError(reason, detail, status)`
  - `def availability() -> tuple[str | None, str | None]`: `(claude_path, None)`, or `(None, "no_host" | "unsupported_platform")`. It resolves `shutil.which("claude")` on every call, never at import.
  - `def mcp_config(mcp_url: str | None) -> dict`: `{"mcpServers": {"groundly": {"type": "http", "url": mcp_url}}}` when a URL is given, else `{"mcpServers": {"groundly": {"command": sys.executable, "args": ["-m", "groundly", "mcp"]}}}`
  - `def build_prompt(kind: str, subject: str, topic_id: int | None, big: bool) -> str`
  - `def build_argv(claude: str, kind: str, prompt: str, mcp_url: str | None) -> list[str]`
  - `def check_init(event: dict) -> tuple[str, str] | None`
  - `class Steps` with `feed(event: dict) -> list[str]`
  - `def summary_of(event: dict) -> tuple[str | None, tuple[str, str] | None]`: `(summary, None)` on success, `(None, ("host_error", text))` on error

- [ ] **Step 1: Write the failing tests** in `tests/web/test_runs.py`

```python
def test_python_dash_m_groundly_runs_the_cli():
    r = subprocess.run([sys.executable, "-m", "groundly", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "mcp" in r.stdout

def test_availability_names_why_no_run_can_start(monkeypatch):
    monkeypatch.setattr(runs.shutil, "which", lambda name: None)
    assert runs.availability() == (None, "no_host")
    monkeypatch.setattr(runs.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(runs.sys, "platform", "win32")
    assert runs.availability() == (None, "unsupported_platform")
    monkeypatch.setattr(runs.sys, "platform", "darwin")
    assert runs.availability() == ("/bin/claude", None)

def test_mcp_config_is_the_running_mcp_or_this_installations_stdio():
    assert runs.mcp_config("http://127.0.0.1:8765/mcp") == {
        "mcpServers": {"groundly": {"type": "http", "url": "http://127.0.0.1:8765/mcp"}}}
    assert runs.mcp_config(None) == {"mcpServers": {"groundly": {
        "command": sys.executable, "args": ["-m", "groundly", "mcp"]}}}

def test_write_argv_is_isolated_and_allows_only_the_write_tools():
    argv = runs.build_argv("/bin/claude", "write", "P", None)
    assert argv[:3] == ["/bin/claude", "-p", "P"]
    for flag, value in [("--setting-sources", ""), ("--tools", ""), ("--permission-mode", "dontAsk"),
                        ("--output-format", "stream-json")]:
        assert argv[argv.index(flag) + 1] == value
    for flag in ("--strict-mcp-config", "--verbose", "--no-session-persistence"):
        assert flag in argv
    assert "--model" not in argv
    allowed = argv[argv.index("--allowedTools") + 1].split(",")
    assert set(allowed) == set(runs.WRITE_TOOLS)
    assert not {"mcp__groundly__remove_questions", "mcp__groundly__submit_tree",
                "mcp__groundly__review_answers"} & set(allowed)
    skill = Path(argv[argv.index("--append-system-prompt-file") + 1])
    assert skill.name == "SKILL.md" and "## Grade a test" in skill.read_text()
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == runs.mcp_config(None)

def test_grade_argv_allows_only_the_grade_tools():
    argv = runs.build_argv("/bin/claude", "grade", "P", "http://127.0.0.1:8765/mcp")
    assert set(argv[argv.index("--allowedTools") + 1].split(",")) == set(runs.GRADE_TOOLS)

def test_prompts_carry_only_the_flow_the_subject_and_the_topic_id():
    leaf = runs.build_prompt("write", "apd", 27, big=False)
    big = runs.build_prompt("write", "apd", 3, big=True)
    grade = runs.build_prompt("grade", "apd", None, big=False)
    assert '"Write questions for a topic"' in leaf and "topic id 27" in leaf and "10 questions" in leaf
    assert "each subtopic of topic id 3" in big and "fewer than 10" in big
    assert '"Grade a test"' in grade and "`remaining` is 0" in grade
    for p in (leaf, big, grade):
        assert '"apd"' in p and "verified" in p  # the closing line forbids calling anything verified

INIT = {"type": "system", "subtype": "init",
        "tools": ["mcp__groundly__search", "mcp__groundly__progress"],
        "mcp_servers": [{"name": "groundly", "status": "connected"}]}

def test_init_check_passes_only_groundly():
    assert runs.check_init(INIT) is None
    assert runs.check_init({**INIT, "tools": INIT["tools"] + ["Bash", "Read"]}) == (
        "isolation_failed", "the run had tools other than Groundly's: Bash, Read")
    other = {**INIT, "mcp_servers": INIT["mcp_servers"] + [{"name": "github", "status": "connected"}]}
    assert runs.check_init(other) == ("isolation_failed", "the run had MCP servers other than Groundly: github")
    failed = {**INIT, "mcp_servers": [{"name": "groundly", "status": "failed"}]}
    assert runs.check_init(failed) == (
        "groundly_not_connected", "Groundly's MCP server did not connect (status: failed)")
```

Plus `Steps` and `summary_of` tests. Event shapes are as `claude -p --output-format stream-json --verbose` prints them:
- a tool call is an `assistant` message whose `content` holds `{"type": "tool_use", "id", "name", "input"}`;
- a tool's result is a `user` message whose `content` holds `{"type": "tool_result", "tool_use_id", "content", "is_error"?}`;
- `content` is a JSON string or a list of `{"type": "text", "text": ...}`, and the payload may be wrapped as `{"result": ...}`.

```python
def test_steps_come_from_tool_names_and_counts_never_model_text():
    s = runs.Steps()
    assert s.feed(_text("I will now write <script>")) == []
    assert s.feed(_use("t1", "mcp__groundly__search", {})) == ["Reading the course"]
    assert s.feed(_use("t2", "mcp__groundly__search", {})) == []  # consecutive repeats collapse
    assert s.feed(_use("t3", "mcp__groundly__submit_questions", {"questions": [1, 2, 3]})) == ["Submitting 3 questions"]
    assert s.feed(_result("t3", {"accepted": [{}, {}], "rejected": [{}]})) == ["2 stored, 1 rejected"]
    assert s.feed(_use("t4", "mcp__groundly__review_answers", {"reviews": [1, 2]})) == ["Recording 2 verdicts"]
    assert s.feed(_result("t4", {"recorded": [7, 8], "awaiting": [], "remaining": 0})) == ["2 verdicts recorded"]
    assert s.feed(_result("t9", "boom", is_error=True)) == ["A tool call failed"]

def test_summary_of_success_and_error():
    assert runs.summary_of({"type": "result", "subtype": "success", "is_error": False,
                            "result": "x" * 5000}) == ("x" * runs.MAX_SUMMARY_CHARS, None)
    assert runs.summary_of({"type": "result", "subtype": "success", "is_error": True,
                            "result": "Please run /login"}) == (None, ("host_error", "Please run /login"))
```

(`_text`, `_use` and `_result` are three small local builders for those event shapes.)

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `uv run pytest tests/web/test_runs.py -q`
Expected: FAIL (`No module named groundly.__main__` / `groundly.web.runs`)

- [ ] **Step 3: Implement**
  - `groundly/__main__.py` holds two lines: `from groundly.cli import app` and `app()`.
  - `groundly/web/runs.py` holds the interfaces above.
    - **The SKILL.md path:** `files("groundly").joinpath("skill/SKILL.md")`, made a real path with `str()`. The package is installed unzipped. Do not import `groundly.cli`, because nothing imports the client layer.
    - **`build_argv`** serialises `mcp_config(mcp_url)` with `json.dumps`, and joins the allowlist with `,` as one argument.
    - **`Steps` maps tool names to words:**
      - `search` → "Reading the course"
      - `get_page` → "Opening a cited page"
      - `progress` → "Checking question counts"
      - `list_subjects` → "Checking the subject"
      - `submit_questions` → "Submitting N questions", and its result → "A stored, R rejected"
      - `review_answers` without `reviews` → "Reading answers awaiting review"; with them → "Recording N verdicts"; its result → "N verdicts recorded"
      - an error result → "A tool call failed"
      - It drops a step equal to the previous one, and remembers each `tool_use_id`'s name to read its result.
    - **The prompts**, exact copy:

```python
_ENDING = (" Finish with at most three plain sentences for the student: what was stored or graded,"
           " and what was rejected. Do not tell them to run any command, and do not call any"
           " question, answer key or verdict verified.")
_WRITE_LEAF = 'Follow "Write questions for a topic" for subject "{subject}", topic id {topic_id}: bring that topic to {target} questions.'
_WRITE_BIG = ('Follow "Write questions for a topic" for subject "{subject}": for each subtopic of topic id'
              ' {topic_id} that has fewer than {target} questions (see `progress`), bring it to {target} questions.')
_GRADE = ('Follow "Grade a test" for subject "{subject}": review every written answer awaiting review,'
          ' calling `review_answers` until `remaining` is 0.')
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `uv run pytest tests/web/test_runs.py -q && uv run lint-imports`
Expected: all pass; contracts kept

- [ ] **Step 5: Commit**

```bash
git add groundly/__main__.py groundly/web/runs.py tests/web/test_runs.py
git commit -m "feat(web): isolated host-run argv, prompts and stream reading"
```

---

### Task 2: The `Runner`: one process, read on a thread, fail closed

**Files:**
- Modify: `groundly/web/runs.py`
- Test: `tests/web/test_runs.py` (add), plus a fake `claude` written by a fixture

**Interfaces:**
- Consumes: everything from Task 1.
- Produces:
  - `class Runner` with:
    - `__init__(self, mcp_url: str | None = None)`
    - attribute `mcp_url`
    - `start(self, kind: str, subject: str, topic_id: int | None, prompt: str) -> dict`, which raises `RunError` for `run_in_progress`, `no_host` or `unsupported_platform`
    - `stop(self) -> dict | None`
    - `view(self) -> dict | None`: the current or last run
    - `shutdown(self) -> None`
  - **The view dict:** `{"id": int, "kind", "subject", "topic_id", "status": "running"|"done"|"failed"|"stopped", "steps": list[str], "summary": str|None, "error": {"reason", "detail"}|None, "started_at": str}`

**The fake `claude` (a fixture).** It writes a script to `tmp_path/bin/claude` with shebang `#!{sys.executable}` and puts `tmp_path/bin` first on PATH. The script:
- appends its argv as one JSON line to `$FAKE_CLAUDE_ARGV`;
- prints each line of the file named by `$FAKE_CLAUDE_SCRIPT`, with a short sleep after any line equal to `"SLEEP"`;
- exits with `$FAKE_CLAUDE_EXIT` (default 0);
- writes `$FAKE_CLAUDE_STDERR` to stderr.

Each test writes its own script lines.

- [ ] **Step 1: Write the failing tests**
  - `test_a_run_reads_steps_and_summary_then_ends_done`: INIT, a `search` tool_use, then a success `result` "Stored 10." → `status == "done"`, `steps == ["Reading the course"]`, `summary == "Stored 10."`. The recorded argv's cwd is `GROUNDLY_HOME/.host-run`, which the script also writes to its argv line via `os.getcwd()`.
  - `test_a_second_start_while_running_raises_run_in_progress` (the script sleeps).
  - `test_bash_in_init_kills_the_run_before_any_turn`: INIT with `"Bash"`, then a tool_use line → `status == "failed"`, `error.reason == "isolation_failed"`, `steps == []`.
  - `test_groundly_not_connected_fails_the_run` (Review Focus 5).
  - `test_exit_without_result_fails_with_code_and_stderr_tail` (Review Focus 1): exit 3 and stderr "boom" → `error == {"reason": "host_exit", "detail": "claude exited with code 3: boom"}`.
  - `test_a_login_error_result_is_the_error_shown` (Review Focus 2).
  - `test_junk_and_oversized_lines_are_skipped` (Review Focus 3): a `not json` line plus a 3 MB line, then a normal success → `done`; `len(steps) <= MAX_STEPS` after 200 alternating tool_uses.
  - `test_stop_ends_running_as_stopped` and `test_timeout_fails_naming_the_timeout`: monkeypatch `RUN_TIMEOUT_S = 0.5` → `error.reason == "timeout"`, and `detail` names the limit in minutes or seconds.
  - `test_shutdown_kills_claude_and_its_children` (Review Focus 4): the script starts a `sleep 60` child and records its pid. After `shutdown()`, both pids are gone (`os.kill(pid, 0)` raises).
  - Each test waits with a helper that polls `runner.view()["status"] != "running"` for at most 10 s.

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `uv run pytest tests/web/test_runs.py -q -k "run or stop or timeout or shutdown or junk or login or exit or bash or groundly"`
Expected: FAIL (`Runner` not defined)

- [ ] **Step 3: Implement `Runner` in `groundly/web/runs.py`**
  - **One `threading.Lock`** guards the run state.
  - **`start`:**
    - checks `availability()`;
    - makes `groundly_home() / ".host-run"`;
    - spawns `subprocess.Popen(build_argv(...), cwd=run_dir, stdin=DEVNULL, stdout=PIPE, stderr=<run_dir/"stderr.log" opened "wb">, start_new_session=True)`;
    - starts a daemon reader thread and a `threading.Timer(RUN_TIMEOUT_S, ...)`.
  - **The reader:**
    - reads `proc.stdout.readline(1_000_000)` and skips any line `json.loads` rejects;
    - on the first `system/init` event, applies `check_init`; a fault kills the process group and records the error;
    - otherwise feeds `Steps` (the list is capped to the last `MAX_STEPS`), and on a `result` event applies `summary_of`;
    - after EOF, waits for exit. If there was no result and the run was not stopped or killed, the error is `host_exit` with the code and the last 500 bytes of `stderr.log`.
  - **Killing** is `os.killpg(proc.pid, SIGTERM)`, `wait(5)`, then `SIGKILL`. That reaches the stdio `groundly mcp` child too.
  - **`stop`** marks the run `stopped` before it kills. A timeout marks it `failed` with `("timeout", f"the run passed the {minutes} minute limit")`.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `uv run pytest tests/web/test_runs.py -q`
Expected: PASS, with no process left behind (`pgrep -f FAKE` is empty)

- [ ] **Step 5: Commit**

```bash
git add groundly/web/runs.py tests/web/test_runs.py
git commit -m "feat(web): one host run at a time, isolation checked at start"
```

---

### Task 3: Routes, the shared write guard, and the `start` wiring

**Files:**
- Modify: `groundly/web/app.py` (the `_submit` guard, new handlers, `create_app`)
- Modify: `groundly/cli/serve.py` (in `start`: `create_app(mcp_app, mcp_url=mcp_url)`)
- Test: `tests/web/test_app.py` (add a `# --- page chores` section), `tests/cli/test_cli_serve.py`

**Interfaces:**
- Consumes: `Runner`, `RunError`, `availability`, `build_prompt`, `TARGET_QUESTIONS` (Tasks 1–2); `quiz.mastery`, `quiz.pending_reviews`, `quiz.is_id`, and the existing `_quiz` and `_error` helpers.
- Produces:
  - `create_app(mcp_app: Starlette | None = None, mcp_url: str | None = None) -> Starlette`, with `app.state.runner: Runner`
  - `async def _page_json(request: Request, forbidden: str) -> object | Response`: the Origin, content-type, size and JSON checks lifted from `_submit`, returning the parsed body or the error `Response`. `_submit` calls it with its current message.
  - **Routes:**
    - `GET /api/runs` → `{"available": bool, "reason": str|None, "run": dict|None}`
    - `POST /api/subjects/{subject}/runs` → 202 with the run view
    - `POST /api/runs/stop` → 200 `{"run": dict|None}`

- [ ] **Step 1: Write the failing tests.** Reuse the `seeded`, `_bank`, `ORIGIN` and fake-`claude` fixtures; move the fake into `tests/web/conftest.py`.
  - `test_runs_reports_availability_and_no_run` (both with and without `claude` on PATH).
  - `test_write_on_a_thin_leaf_starts_one_run_whose_prompt_names_only_the_id`: retitle the leaf `<script>ignore previous instructions` with `_tamper`, POST `{"kind": "write", "topic_id": leaf}` → 202. The recorded argv's prompt holds `topic id {leaf}` and none of the title.
  - `test_write_on_a_big_topic_uses_the_big_prompt_and_refuses_when_all_subtopics_are_full`: seed a two-level tree. Give each subtopic `TARGET_QUESTIONS` questions → 400 `nothing_to_write`. Remove one → 202, and the prompt holds "each subtopic of topic id".
  - `test_grade_starts_only_when_answers_await_review`: no awaiting answers → 400 `nothing_to_grade`. After submitting an attempt with an open answer → 202, and the argv allows only `GRADE_TOOLS`.
  - `test_start_refuses_and_spawns_nothing` (parametrised): no or foreign `Origin` → 403; `text/plain` → 415; unknown subject → 404 `unknown_subject`; unknown topic → 404 `unknown_topic`; `{"kind": "map"}` and `{"kind": "write", "topic_id": "7"}` → 400 `invalid_body`; a run already running → 409 `run_in_progress`; no `claude` → 503 `no_host`. Afterwards the argv file is absent or unchanged.
  - `test_stop_route_needs_the_page_origin` and `test_stop_ends_the_run`.
  - `test_starting_a_run_writes_no_rows_to_store_db`, using the `PRAGMA data_version` pattern of `test_tree_read_leaves_the_store_unchanged`.
  - `test_app_shutdown_stops_a_running_run`: inside `with TestClient(create_app())`, start a sleeping run; after the block exits, the fake's pid is gone (Review Focus 4).
  - In `tests/cli/test_cli_serve.py`, `_patch` records `app_`, and the `start` test asserts `runs[0]["app"].state.runner.mcp_url == "http://127.0.0.1:8765/mcp"`. The `serve` test asserts it is `None`.

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `uv run pytest tests/web/test_app.py tests/cli/test_cli_serve.py -q`
Expected: the new tests FAIL (404 on `/api/runs`); the existing tests still pass

- [ ] **Step 3: Implement**
  - Extract `_page_json` and change `_submit` to use it. Keep `_submit`'s messages and its error ordering the same.
  - **The start handler:**
    1. guard with `_page_json(request, "a run is started from this page only")`;
    2. check the body is `{"kind": "write", "topic_id": <quiz.is_id>}` or `{"kind": "grade"}`, else `invalid_body`;
    3. inside `_quiz`, read `store.tree(with_sources=False)`. For write, find the node (`unknown_topic` if missing), set `big = bool(node["subtopics"])` and use `quiz.mastery(store, progress, tree)` to require a thin leaf in scope, else `nothing_to_write`. For grade, require `quiz.pending_reviews(store, progress)["awaiting"]`, else `nothing_to_grade`;
    4. call `runner.start(kind, subject, topic_id, build_prompt(...))` and map `RunError` to `_error(e.reason, e.detail, e.status)`.
  - **The lifespan** is an `asynccontextmanager` that enters `mcp_app.lifespan(app)` when given and always calls `runner.shutdown()` on exit.
  - **The module docstring** says that the page can now also start a host run, and that it still writes no rows to `store.db`.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `uv run pytest tests/web tests/cli -q && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add groundly/web/app.py groundly/cli/serve.py tests/web tests/cli/test_cli_serve.py
git commit -m "feat(web): routes to start, watch and stop a host run"
```

---

### Task 4: The page: buttons, the run strip, polling

**Files:**
- Modify: `groundly/web/static/page.js`
- Modify: `groundly/web/static/page.css`
- Test: `tests/cli/test_cli_serve.py` (static text checks, same style as its existing `PAGE` checks), plus a manual browser check

**Interfaces:**
- Consumes: the three routes from Task 3 and the run view dict from Task 2.
- Produces:
  - `async function runInfo()` → the `GET /api/runs` JSON
  - `function runStrip(subject, onEnd)` → a node that polls every 2000 ms while `run.status === "running"`, shows the steps, a **Stop** button and then the summary or the error, and calls `onEnd()` once when the run ends
  - `async function startRun(subject, body, onEnd)`

- [ ] **Step 1: Write the failing static checks**
  - `test_page_starts_runs_through_the_api_only`: `PAGE` contains `/api/runs` and `"/runs"`. It never contains `innerHTML`, `insertAdjacentHTML` or `eval(` (keep any existing check of that kind).
  - `test_page_keeps_the_phrases_as_the_fallback`: `PAGE` still contains `Say to your agent` and `grade my test`.

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/cli/test_cli_serve.py -q -k "runs or fallback"`
Expected: FAIL

- [ ] **Step 3: Implement**
  - **`renderPanel`:**
    - When `available`, the button shows if:
      - on a leaf, `node.progress.questions < 10`;
      - on a big topic, any `sub.progress.questions < 10`.
    - Labels: on a leaf, **Write questions** below the test chooser (or in place of the "Say to your agent" note when there are no questions); on a big topic, **Write questions for its subtopics**.
    - When not available, the note stays as today.
  - **`showResult`:** when `result.provisional` and `available`, a **Grade now** button goes beside the provisional line, and each awaiting card shows only "Awaiting review". When not available, the `grade my test` phrase stays.
  - **While running:** the strip goes at the top of the panel or result, and every run button is `disabled`.
  - **When the run ends:** `onEnd` re-runs `showTree(subject)` or `showResult(...)`, and the strip keeps showing the summary of the run this tab watched.
  - **Re-attaching:** on view load, if `run.status === "running"`, show the strip, so a reload re-attaches.
  - **Errors:** a 4xx or 5xx from start shows the response's `detail` in the strip.
  - **All text from the run goes in with `el()` and `textContent`.** CSS covers the strip, the step list and the disabled state, and fits at phone width.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/cli/test_cli_serve.py -q`
Expected: PASS.

Then do a browser check without a model:
1. Put Task 2's fake `claude` on PATH, scripted with INIT, two `search` uses, a `submit_questions` use and result, and a success summary.
2. Run `groundly serve --port 8799` against a seeded throwaway `GROUNDLY_HOME`.
3. Check:
   - the leaf panel shows **Write questions**;
   - a click shows the steps, then the summary, then the panel re-renders;
   - **Stop** works on a sleeping script;
   - a summary containing `<b>x</b>` shows as text;
   - at 375 px wide the strip wraps with no horizontal scroll;
   - with `claude` removed from PATH, the phrases come back.

- [ ] **Step 5: Commit**

```bash
git add groundly/web/static/page.js groundly/web/static/page.css tests/cli/test_cli_serve.py
git commit -m "feat(web): Write questions and Grade now buttons with a run strip"
```

---

### Task 5: Decision 46 and the docs

**Files:**
- Modify:
  - `docs/groundly-spec.md` (§1, §4 Interface and Answering rows, §7 decision 46)
  - `docs/architecture/overview.md`
  - `docs/infrastructure/security.md`
  - `docs/use-cases/student-modes.md`
  - `docs/guides/mcp-hosts.md`, `README.md`
  - `.claude/rules/architecture.md`, `.claude/rules/conventions.md`
- Test: `uv run pytest -q` (any doc-text tests)

- [ ] **Step 1: Run the project's `decision` skill.** Record decision 46 as the spec states it: the page may start the student's own host headless for the write and grade flows, isolated, with no key, no client, no `--model`, macOS and Linux only. It updates the spec rows, the overview and the two rules files listed above.
- [ ] **Step 2: In `student-modes.md`,** add the page-chores flow, its alternative paths (no `claude`, Windows, a run in progress, the isolation check failing, not logged in) and the spec's 13 acceptance criteria.
- [ ] **Step 3: In `security.md`,** add the new capability, its guards, the injection limits and the residual risk, as in the spec's Security section.
- [ ] **Step 4: In `mcp-hosts.md` and `README.md`:** the buttons, the `ANTHROPIC_MODEL` escape hatch, and that each click uses plan usage.
- [ ] **Step 5: Verify.** `uv run pytest -q` passes, and `grep -rn "decision 46" docs .claude/rules` finds the spec, the overview and the rules.
- [ ] **Step 6: Commit**

```bash
git add docs .claude/rules README.md
git commit -m "docs: decision 46, the page starts the student's host for two chores"
```

---

### Task 6: Gates: full suite, reviewers, the graph, the human check

- [ ] **Step 1:** `uv run pytest -q && uv run ruff check && uv run ruff format --check && uv run lint-imports`. All green.
- [ ] **Step 2:** Run the `spec-guardian` and `security-reviewer` agents over `git diff main...page-chores`. Fix their blocking findings with a test each, then commit `fix: page chores review findings`.
- [ ] **Step 3:** `graphify update .`
- [ ] **Step 4 (Paul, criterion 13):**
  1. Re-index apd first if you want unflattened code. Re-indexing replaces chunks, and questions citing only those chunks are deleted.
  2. Run `groundly start`.
  3. On one big topic, click **Write questions for its subtopics**.
  4. Take a test with written answers, then click **Grade now**.
  5. Check that the verdicts appear with nothing typed into a chat.
  6. Record the outcome in `student-modes.md`.
