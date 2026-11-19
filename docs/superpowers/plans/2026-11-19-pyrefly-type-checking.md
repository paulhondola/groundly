# Type Checking with pyrefly Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `uv run pyrefly check` passes with zero errors on `groundly/` and runs in CI, pre-commit and the Claude Code edit hook.

**Architecture:** pyrefly 1.4.0 configured in `pyproject.toml` with the `default` preset. The 42 errors it reports today are fixed at their source, a layer at a time (cli, core, agents, web), with no behaviour change. Then the check is wired into the three places that already run ruff.

**Tech Stack:** pyrefly 1.4.0, uv, lefthook, GitHub Actions, Claude Code hooks.

**Spec:** `docs/superpowers/specs/2026-11-19-pyrefly-type-checking-design.md`

## Global Constraints

- `pyrefly==1.4.0`, exact pin, in the `dev` extra.
- `[tool.pyrefly]`: `project-includes = ["groundly"]`, `preset = "default"`, `python-version = "3.11"`. No `pyrefly.toml`.
- Zero errors. No baseline file, no blanket `# type: ignore`, no casts to `Any`. An ignore names its error code and its reason on the line, and is for a third-party stub only.
- Behaviour stays the same. A fix that changes behaviour is a bug fix: it gets its own test and is called out in the PR.
- Comments state intent and invariants, never history (`.claude/rules/conventions.md`).
- The edit hook runs `ruff check --fix`, which deletes an import nothing uses yet: change the code first, add the import last.
- Feature branch `pyrefly-types` only. Stage files by path: never add `docs/superpowers/specs/2026-11-18-rust-port-design.md` or `docs/superpowers/specs/2026-11-18-svelte-ts-sse-web-design.md`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- The edit hook on a `.py` file outside `groundly/` (tests, scripts) must not run pyrefly. Checking one file ignores `project-includes`, so it would block edits to files the gate does not cover. Checked in Task 6.
- The edit hook on a `groundly/` file with a type error exits 2 with the error on stderr. On a clean file it exits 0 and prints nothing. Checked in Task 6.
- A run that has warnings and no errors exits 0, so CI does not fail on pyrefly's 4 warnings. Checked in Task 5.
- `GET /api/subjects/<unknown>/attempts/1` still answers 404 `unknown_subject` once `_result` looks the subject up itself. New test in Task 5.
- A JSON body that is not an object, sent to start a run, still answers 400 `invalid_body`. Already covered by `tests/web/test_app.py:745`; rerun in Task 5.

---

### Task 1: Install and configure pyrefly

**Files:**
- Modify: `pyproject.toml` (the `dev` extra; a new `[tool.pyrefly]` table after `[tool.ruff]`)
- Modify: `uv.lock`
- Add: `docs/superpowers/specs/2026-11-19-pyrefly-type-checking-design.md`, `docs/superpowers/plans/2026-11-19-pyrefly-type-checking.md`

**Interfaces:**
- Produces: `uv run pyrefly check`, which every later task uses as its check.

- [ ] **Step 1: Add the dependency**

Run: `uv add --optional dev "pyrefly==1.4.0"`
Expected: the `dev` list in `pyproject.toml` includes `"pyrefly==1.4.0"`, and `grep -A1 'name = "pyrefly"' uv.lock` shows `version = "1.4.0"`.

- [ ] **Step 2: Add the config table**

```toml
[tool.pyrefly]
project-includes = ["groundly"]
preset = "default"
python-version = "3.11"
```

- [ ] **Step 3: Check that the config reproduces the 42**

Run: `uv run pyrefly check --output-format min-text 2>&1 | tail -1`
Expected: `INFO 42 errors (4 warnings not shown, …)`. The same list as `uvx pyrefly@1.4.0 check --preset default groundly`. Anything about missing imports means pyrefly did not find the venv; fix that before going on.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock docs/superpowers/specs/2026-11-19-pyrefly-type-checking-design.md docs/superpowers/plans/2026-11-19-pyrefly-type-checking.md
git commit -m "build: add pyrefly 1.4.0 with the default preset"
```

### Task 2: Fix the CLI errors (20)

**Files:**
- Modify: `groundly/cli/app.py:56`
- Modify: `groundly/cli/serve.py:29`

**Interfaces:**
- Produces: `_fail(message: str) -> NoReturn`. The checker then infers `_subject_checked -> Subject`, `_manifest_checked -> Manifest` and `_store_checked -> SubjectStore`. No change to callers.

- [ ] **Step 1: Record the failing check**

Run: `uv run pyrefly check --output-format min-text 2>&1 | grep -c '^ERROR groundly/cli/'`
Expected: `20`

- [ ] **Step 2: Type `_fail` as `-> NoReturn`** (`from typing import NoReturn`). It always raises `typer.Exit`, so the code after a call to it never runs. That clears 19 errors (unbound names and None access after a failed lookup).

- [ ] **Step 3: Type `_run`'s first parameter as `asgi_app: Callable[..., Awaitable[None]]`** (`collections.abc`). uvicorn accepts any callable. Importing nothing from starlette keeps the CLI's import time as it is.

- [ ] **Step 4: Check**

Run: `uv run pyrefly check --output-format min-text 2>&1 | grep '^ERROR groundly/cli/'; uv run pytest tests/cli -q`
Expected: no pyrefly lines; pytest passes.

- [ ] **Step 5: Commit**

```bash
git add groundly/cli/app.py groundly/cli/serve.py
git commit -m "fix(types): _fail never returns; type the served ASGI app"
```

### Task 3: Fix the core errors (8)

**Files:**
- Modify: `groundly/core/store.py:515,524,592,790`
- Modify: `groundly/core/progress.py:171`
- Modify: `groundly/core/bundle.py:392`
- Modify: `groundly/core/config.py:66,100`

- [ ] **Step 1: Record the failing check**

Run: `uv run pyrefly check --output-format min-text 2>&1 | grep '^ERROR groundly/core/'`
Expected: 8 lines: bundle 394, config 82 and 101, progress 191, store 524, 530, 609, 795.

- [ ] **Step 2: Assert the inserted row ids.** sqlite3 types `cursor.lastrowid` as `int | None`, but it is always set right after an `INSERT`. Add `assert <id> is not None  # set by the INSERT above` after each of `material_id` (store 515), `question_id` (store 592), `topic_id` (store 790, the INSERT branch only) and `attempt_id` (progress 171). Inline, not the shared helper the spec suggested: `progress.py` does not import `store.py`, and one shared line is not worth adding that import.

- [ ] **Step 3: `bundle._text_problem`.** After the `if not trimmed` return, add `assert length is not None  # a text value always has a length`.

- [ ] **Step 4: `config.py`.** Annotate `_EXPECTED: dict[object, str]`; the key comes from `get_args` and is not statically a `type`. In `set_key`, after `model = Settings.model_fields[section].annotation`, add `assert isinstance(model, type) and issubclass(model, BaseModel)`, because every `Settings` field is a settings model.

- [ ] **Step 5: `store.py:524`.** `sqlite_vec.serialize_float32(vec),  # pyrefly: ignore[bad-argument-type] -- stub says list[float]; it unpacks any sequence, numpy rows included`. Converting to a list would copy the row the index path deliberately keeps as numpy. If pyrefly 1.4.0 spells the ignore differently, use its spelling, still with the code and the reason.

- [ ] **Step 6: Check**

Run: `uv run pyrefly check --output-format min-text 2>&1 | grep '^ERROR groundly/core/'; uv run pytest tests/core -q`
Expected: no pyrefly lines; pytest passes.

- [ ] **Step 7: Commit**

```bash
git add groundly/core/store.py groundly/core/progress.py groundly/core/bundle.py groundly/core/config.py
git commit -m "fix(types): state the core invariants pyrefly cannot see"
```

### Task 4: Fix the agents errors (6)

**Files:**
- Modify: `groundly/agents/quiz.py:159,206-208,241,365-378`
- Modify: `groundly/agents/verifier.py:148`

**Interfaces:**
- Produces: `is_id(value: object) -> TypeGuard[int]` (`typing.TypeGuard`). Same body. Callers in `web/app.py` gain the narrowing for free.

- [ ] **Step 1: Record the failing check**

Run: `uv run pyrefly check --output-format min-text 2>&1 | grep '^ERROR groundly/agents/'`
Expected: 6 lines: quiz 211, 263, 389, 390, 393; verifier 151.

- [ ] **Step 2: Make `is_id` return `TypeGuard[int]`.** That clears quiz 389, 390 and 393, where `if not is_id(answer_id): raise` already guards.

- [ ] **Step 3: Build `submit_attempt`'s ids in a loop**, so each one is narrowed. Same message, same order of checks:

```python
ids: list[int] = []
for a in answers:
    question_id = a.get("question_id") if isinstance(a, dict) else None
    if not is_id(question_id):
        raise QuizError("invalid_text", "every answer names its question id")
    ids.append(question_id)
```

- [ ] **Step 4: Annotate the list `attempt_result` builds** (`answers = []` at line 241) as `list[dict[str, Any]]`. It is a JSON payload with mixed values.

- [ ] **Step 5: `verifier._retrieval_text`.** For an open question, `assert q.reference_answer is not None`. The structural check at `verifier.py:122` requires a reference answer before any re-retrieval.

- [ ] **Step 6: Check**

Run: `uv run pyrefly check --output-format min-text 2>&1 | grep '^ERROR groundly/agents/'; uv run pytest tests/agents -q`
Expected: no pyrefly lines; pytest passes.

- [ ] **Step 7: Commit**

```bash
git add groundly/agents/quiz.py groundly/agents/verifier.py
git commit -m "fix(types): is_id narrows to int; type the attempt payload"
```

### Task 5: Fix the web errors (8)

**Files:**
- Modify: `groundly/web/app.py:55,252-266,286-287,339`
- Modify: `groundly/web/runs.py:290,329,419`
- Test: `tests/web/test_app.py`

**Interfaces:**
- Produces: `_read(subject: Subject, reader: Callable[[SubjectStore], T]) -> T | JSONResponse`, with `T = TypeVar("T")`. The two existing callers keep their `isinstance(result, Response)` checks.

- [ ] **Step 1: Write the test for the route about to change**

```python
def test_an_attempt_of_an_unknown_subject_is_404(client, seeded):
    r = client.get("/api/subjects/NOPE/attempts/1")
    assert (r.status_code, r.json()["error"]) == (404, "unknown_subject")
```

- [ ] **Step 2: Run it.** It passes today, because `_quiz` answers the 404. It pins that behaviour before `_result` changes.

Run: `uv run pytest tests/web/test_app.py::test_an_attempt_of_an_unknown_subject_is_404 -q`
Expected: PASS

- [ ] **Step 3: Record the failing check**

Run: `uv run pyrefly check --output-format min-text 2>&1 | grep '^ERROR groundly/web/'`
Expected: 8 lines: app 133, 262, 287, 295, 339; runs 306, 343, 419.

- [ ] **Step 4: `web/app.py`.**
  - Make `_read` generic as in Interfaces (clears 133).
  - In `_result`, open the subject before `def read`. If it is None, return the same `_error("unknown_subject", f"unknown subject {name!r}", 404)` that `_quiz` returns. The closure then captures a `Subject` (clears 262).
  - In `_start_run`, make the guard `if not isinstance(body, dict) or not (kind == "grade" or …)`, so `body` is a dict from there on, closure included (clears 287 and 295).
  - In `lifespan`, use `mcp_app.router.lifespan_context(app)`. FastMCP's `StarletteWithLifespan.lifespan` returns exactly that (`fastmcp/server/http.py:381`), and plain `Starlette` types it (clears 339).

- [ ] **Step 5: `web/runs.py`.**
  - In `Runner.start`, after `if reason: raise …`, add `assert claude is not None  # availability() gives a path whenever it gives no reason`.
  - Before `return snapshot`, add `assert snapshot is not None  # self._run was set just above`.
  - In `_live`, return `run is not None and proc is not None and (run["status"] == "running" or proc.poll() is None)`. `_run` and `_proc` are set together (`runs.py:328`), so this is the same truth table, stated.

- [ ] **Step 6: Check the whole package and the exit code**

Run: `uv run pyrefly check; echo "exit=$?"; uv run pytest tests/web -q`
Expected: `INFO 0 errors (4 warnings not shown…)`, then `exit=0`. pytest passes, including the new test and the `["grade"]` case at `test_app.py:745`.

- [ ] **Step 7: Commit**

```bash
git add groundly/web/app.py groundly/web/runs.py tests/web/test_app.py
git commit -m "fix(types): narrow the web routes and run lifecycle"
```

### Task 6: Enforce it, and name it in the docs

**Files:**
- Modify: `.github/workflows/ci.yml`, `.github/workflows/publish.yml` (after the `Layering (import-linter)` step)
- Modify: `lefthook.yml` (a new pre-commit job)
- Modify: `.claude/settings.json` (the PostToolUse command)
- Modify: `.claude/rules/conventions.md:13`, `docs/infrastructure/distribution.md:35`

- [ ] **Step 1: CI.** Add to both workflows, after the `Layering (import-linter)` step:

```yaml
      - name: Type check (pyrefly)
        run: uv run pyrefly check
```

- [ ] **Step 2: lefthook.** Add a job next to `lint-imports`: `name: pyrefly`, `glob: "*.py"`, `run: uv run pyrefly check`.

- [ ] **Step 3: Edit hook.** Append to the PostToolUse command, after the ruff step:

```sh
; case "$f" in "$CLAUDE_PROJECT_DIR"/groundly/*) uv run pyrefly check --summary=none --output-format min-text "$f" >&2 || exit 2 ;; esac
```

- [ ] **Step 4: Check the hook's three cases.** Pipe a JSON payload into the command, with `CLAUDE_PROJECT_DIR=$PWD`:
  - `tests/conftest.py`: `exit=0`, no pyrefly output.
  - `groundly/cli/app.py`: `exit=0`, no output.
  - A scratch `groundly/_typecheck_probe.py` containing `x: int = "a"`: `exit=2`, with the `bad-assignment` error on stderr. Delete the probe afterwards.

- [ ] **Step 5: Docs.**
  - `conventions.md:13` ends "…; ruff for lint + format; pyrefly (default preset) for types."
  - `distribution.md:35` reads "CI (GitHub Actions): ruff + pyrefly + pytest on …".

- [ ] **Step 6: Commit.** The pre-commit run now includes the pyrefly job, which proves it works.

```bash
git add .github/workflows/ci.yml .github/workflows/publish.yml lefthook.yml .claude/settings.json .claude/rules/conventions.md docs/infrastructure/distribution.md
git commit -m "ci: type-check with pyrefly in CI, pre-commit and the edit hook"
```

### Task 7: Verify the branch

- [ ] **Step 1: Run every CI gate locally**

Run: `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pyrefly check && uv run pytest -q`
Expected: every step passes.

- [ ] **Step 2: Refresh the graph.** Run `graphify update .`

- [ ] **Step 3: Review.** Run the `spec-guardian` agent on `git diff main...pyrefly-types`. The branch touches the page's routes and the run lifecycle. Fix what it confirms.

- [ ] **Step 4: Hand over.** List any fix that changed behaviour (expected: none) and wait for Paul's go before pushing or opening the PR.
