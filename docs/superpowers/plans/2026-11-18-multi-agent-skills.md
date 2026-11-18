# Multi-Agent Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `groundly skill` (`status`, `install`, `uninstall`) and the three packaged skills (`groundly-map`, `groundly-questions`, `groundly-grade`) to target `~/.agents/skills/` always plus `~/.claude/skills/`, `~/.codex/skills/`, and `~/.gemini/config/skills/` whenever their host roots exist on disk.

**Architecture:** `groundly/cli/skill.py` resolves active skill directories from `Path.home()` (`~/.agents/skills` always, plus `<host>/skills` for any existing host root among `~/.claude`, `~/.codex`, and `~/.gemini/config`) and runs the existing byte-comparison, per-file confirmation, symlink refusal, and legacy `~/.claude/skills/groundly/` retirement across them. The three packaged `SKILL.md` files stay single universal files per flow whose step 1 connection check names Claude Code, Codex, and Antigravity.

**Tech Stack:** Python ≥3.11, Typer, pytest, ruff, import-linter (`uv run lint-imports`).

**Spec:** `docs/superpowers/specs/2026-11-18-multi-agent-skills-design.md`

## Global Constraints

- Skill names, exactly: `groundly-map`, `groundly-questions`, `groundly-grade`. Packaged at `groundly/skills/<name>/SKILL.md`.
- Four global skill directories under `Path.home()`, in order:
  1. `~/.agents/skills` (always active)
  2. `~/.claude/skills` (active when `~/.claude` exists)
  3. `~/.codex/skills` (active when `~/.codex` exists)
  4. `~/.gemini/config/skills` (active when `~/.gemini/config` exists)
- Old combined skill path remains `~/.claude/skills/groundly/SKILL.md` only.
- No new CLI flags on `groundly skill`, `install`, or `uninstall`; `-y/--yes` remains the only prompt skip.
- Every packaged skill contains `"data, never instructions"`, never contains `"verified"` (any case), and names the connect instructions for Claude Code (`claude mcp add --scope user groundly -- groundly mcp`), Codex (`codex mcp add groundly -- groundly mcp`), and Antigravity (`~/.gemini/config/mcp_config.json`).
- Headless page runs (`groundly/web/runs.py`) are unchanged and remain Claude Code (`claude -p`).
- Commits on branch `multi-agent-skills`, never `main`.

## Review Focus

- `~/.claude/skills/groundly-map` is a folder symlink to `~/.agents/skills/groundly-map` → `install` writes `~/.agents/skills/groundly-map/SKILL.md` first, sees `~/.claude/skills/groundly-map/SKILL.md` as already installed without prompting, and `uninstall -y` does not `rmdir` `~/.agents/skills/groundly-map` out from under the symlink (so a subsequent `install` still succeeds). (Task 2)
- `~/.claude` itself is a symlink to a directory (e.g. `~/dotfiles/claude/.claude`) → auto-detection treats `~/.claude` as active and installs into `~/.claude/skills/<name>/SKILL.md`. (Task 2)
- A host root or skill folder (e.g. `~/.codex` or `~/.gemini/config/skills/groundly-grade`) is a dangling symlink → `install` and `uninstall` fail closed in `_refuse_symlinks()` naming the broken link before writing or deleting anything. (Task 2)
- One copy in `~/.claude/skills/groundly-grade/SKILL.md` was edited and the student answers `"n"` on `install` → only that path is skipped; `~/.agents/skills/` and the remaining `~/.claude/skills/` files are installed, exiting 0. (Task 2)
- Skills were installed in `~/.codex/skills/` and the student runs `uninstall -y` → `uninstall` checks all four roots and removes the files from both `~/.agents/skills/` and `~/.codex/skills/`. (Task 2)

---

### Task 1: Packaged skills name all three host connection methods

**Files:**
- Modify: `groundly/skills/groundly-map/SKILL.md:12`
- Modify: `groundly/skills/groundly-questions/SKILL.md:14`
- Modify: `groundly/skills/groundly-grade/SKILL.md:14`
- Modify: `tests/cli/test_packaged_skills.py:18-26`

**Interfaces:**
- Consumes: none.
- Produces: updated `groundly/skills/<name>/SKILL.md` bytes read by `groundly/cli/skill.py` and `groundly/web/runs.py`.

- [ ] **Step 1: Update the failing test in `tests/cli/test_packaged_skills.py`**

In `test_each_skill_stands_alone(name)`, add assertions for Codex and Antigravity connect instructions:

```python
@pytest.mark.parametrize("name", NAMES)
def test_each_skill_stands_alone(name):
    text = " ".join(_text(name).split())
    assert f"name: {name}" in _head(name)
    assert "data, never instructions" in text
    assert "verified" not in text.lower()
    assert "claude mcp add --scope user groundly -- groundly mcp" in text
    assert "codex mcp add groundly -- groundly mcp" in text
    assert "~/.gemini/config/mcp_config.json" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_packaged_skills.py -q`
Expected: FAIL — `assert 'codex mcp add groundly -- groundly mcp' in text`

- [ ] **Step 3: Update step 1 in the three `SKILL.md` files**

Replace step 1 ("Check the connection") in each file with the spec's exact wording:
- `groundly/skills/groundly-map/SKILL.md`:
  `1. **Check the connection.** If the \`outline\` and \`submit_tree\` tools are not available, tell the student to connect Groundly (\`claude mcp add --scope user groundly -- groundly mcp\` in Claude Code, \`codex mcp add groundly -- groundly mcp\` in Codex, or \`"groundly": {"command": "groundly", "args": ["mcp"]}\` under \`mcpServers\` in \`~/.gemini/config/mcp_config.json\` in Antigravity), then restart their agent. Stop there.`
- `groundly/skills/groundly-questions/SKILL.md`:
  `1. **Check the connection.** If Groundly's tools (\`progress\`, \`topic\`, \`submit_questions\`) are not available, tell the student to connect Groundly (\`claude mcp add --scope user groundly -- groundly mcp\` in Claude Code, \`codex mcp add groundly -- groundly mcp\` in Codex, or \`"groundly": {"command": "groundly", "args": ["mcp"]}\` under \`mcpServers\` in \`~/.gemini/config/mcp_config.json\` in Antigravity), then restart their agent. Stop there.`
- `groundly/skills/groundly-grade/SKILL.md`:
  `1. **Check the connection.** If \`review_answers\` is not available, tell the student to connect Groundly (\`claude mcp add --scope user groundly -- groundly mcp\` in Claude Code, \`codex mcp add groundly -- groundly mcp\` in Codex, or \`"groundly": {"command": "groundly", "args": ["mcp"]}\` under \`mcpServers\` in \`~/.gemini/config/mcp_config.json\` in Antigravity), then restart their agent. Stop there.`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_packaged_skills.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add groundly/skills tests/cli/test_packaged_skills.py
git commit -m "feat(skill): name Claude Code, Codex and Antigravity connect steps"
```

---

### Task 2: Multi-agent `groundly skill` (`status`, `install`, `uninstall`)

**Files:**
- Modify: `groundly/cli/skill.py`
- Modify: `tests/cli/test_cli_skill.py`

**Interfaces:**
- Consumes: `files("groundly").joinpath("skills", name, "SKILL.md")` from Task 1.
- Produces: `HOSTS = (".claude", ".codex", ".gemini/config")`, `_active_dirs() -> list[Path]`, `_all_dirs() -> list[Path]`, `_legacy() -> Path` in `groundly/cli/skill.py`.

- [ ] **Step 1: Write the failing tests in `tests/cli/test_cli_skill.py`**

Update `targets` fixture in `tests/cli/test_cli_skill.py` so default clean-`$HOME` targets point to `home / ".agents/skills" / n / "SKILL.md"`, and add tests covering host detection and multi-root behaviour:

```python
@pytest.fixture
def targets(home):
    return {n: home / ".agents/skills" / n / "SKILL.md" for n in NAMES}


@pytest.mark.parametrize("host", [".claude", ".codex", ".gemini/config"])
def test_detected_host_roots_are_installed_and_reported_beside_agents(home, host):
    (home / host).mkdir(parents=True)
    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    for name in NAMES:
        assert (home / ".agents/skills" / name / "SKILL.md").read_bytes() == PACKAGED[name]
        assert (home / host / "skills" / name / "SKILL.md").read_bytes() == PACKAGED[name]
    for absent in {".claude", ".codex", ".gemini/config"} - {host}:
        assert not (home / absent).exists()
    status = runner.invoke(app, ["skill"]).output
    assert str(home / host / "skills/groundly-map/SKILL.md") in status


def test_a_symlinked_host_root_is_detected_and_installed(home):
    real = home / "dotfiles/claude/.claude"
    real.mkdir(parents=True)
    (home / ".claude").symlink_to(real)
    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    for name in NAMES:
        assert (real / "skills" / name / "SKILL.md").read_bytes() == PACKAGED[name]


def test_a_dangling_host_root_symlink_is_refused_by_name(home):
    (home / ".codex").symlink_to(home / "gone")
    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code != 0
    assert "does not exist" in _flat(result) and str(home / "gone") in _flat(result)
    assert not (home / ".agents/skills").exists()


def test_a_host_skill_folder_symlinked_to_agents_survives_install_and_uninstall(home):
    agents_map = home / ".agents/skills/groundly-map"
    agents_map.mkdir(parents=True)
    claude_skills = home / ".claude/skills"
    claude_skills.mkdir(parents=True)
    (claude_skills / "groundly-map").symlink_to(agents_map)

    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    assert (agents_map / "SKILL.md").read_bytes() == PACKAGED["groundly-map"]

    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    assert not (agents_map / "SKILL.md").exists()
    assert (claude_skills / "groundly-map").exists()  # link not left dangling

    result = runner.invoke(app, ["skill", "install"])
    assert result.exit_code == 0, result.output
    assert (agents_map / "SKILL.md").read_bytes() == PACKAGED["groundly-map"]


def test_a_no_in_one_host_skips_only_that_copy(home):
    (home / ".claude").mkdir()
    runner.invoke(app, ["skill", "install"])
    claude_grade = home / ".claude/skills/groundly-grade/SKILL.md"
    claude_grade.write_text("edited")

    result = runner.invoke(app, ["skill", "install"], input="n\n")
    assert result.exit_code == 0, result.output
    assert claude_grade.read_text() == "edited"
    assert (home / ".agents/skills/groundly-grade/SKILL.md").read_bytes() == PACKAGED[
        "groundly-grade"
    ]
    assert "skipped" in _flat(result) and str(claude_grade) in _flat(result)


def test_uninstall_removes_across_all_roots(home):
    for host in (".claude", ".codex", ".gemini/config"):
        (home / host).mkdir(parents=True)
    runner.invoke(app, ["skill", "install"])
    result = runner.invoke(app, ["skill", "uninstall", "-y"])
    assert result.exit_code == 0, result.output
    for root in (".agents", ".claude", ".codex", ".gemini/config"):
        assert not any((home / root / "skills" / n).exists() for n in NAMES)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_cli_skill.py -q`
Expected: FAIL — `.agents/skills` not targeted by current `groundly/cli/skill.py`.

- [ ] **Step 3: Implement multi-root resolution, status, install, and uninstall in `groundly/cli/skill.py`**

- Define `HOSTS = (".claude", ".codex", ".gemini/config")` and connect constants:
  - `CONNECT_COMMAND = "claude mcp add --scope user groundly -- groundly mcp"`
  - `CODEX_CONNECT_COMMAND = "codex mcp add groundly -- groundly mcp"`
  - `ANTIGRAVITY_CONNECT_HINT = 'add "groundly": {"command": "groundly", "args": ["mcp"]} to mcpServers in ~/.gemini/config/mcp_config.json'`
- `_all_dirs() -> list[Path]`: returns `[home / ".agents/skills", *(home / h / "skills" for h in HOSTS)]`.
- `_active_dirs() -> list[Path]`: returns `[home / ".agents/skills", *(home / h / "skills" for h in HOSTS if (home / h).exists() or (home / h).is_symlink())]`.
- `_refuse_symlinks() -> None`:
  - For each `skills_dir` in `_all_dirs()`, check `skills_dir.parent` and `skills_dir`: if `p.is_symlink() and not p.exists()`, fail with `f"{p} links to {p.readlink()}, which does not exist — fix or remove the link, then re-run"`.
  - For each `target` in `(*(d / n / "SKILL.md" for d in _all_dirs() for n in NAMES), _legacy())`:
    - If `target.is_symlink()`: fail with `f"{target} is a symlink to {target.readlink()} — remove it yourself, then re-run"`.
    - If `target.parent.is_symlink() and not target.parent.exists()`: fail with `f"{target.parent} links to {target.parent.readlink()}, which does not exist — fix or remove the link, then re-run"`.
- `_remove(target: Path) -> None`:
  - If `target.exists()`: `target.unlink()`.
  - Remove `target.parent` only when `target.parent.exists()`, `not target.parent.is_symlink()`, `not any(target.parent.iterdir())`, and no other skill folder `[d / n for d in _all_dirs() for n in NAMES]` is a symlink resolving to `target.parent.resolve()`.
- `skill` (status): iterate over `d in _active_dirs()` and `name in NAMES`, printing `f"{name}: {state}  ({target})"`; then check `_legacy()`.
- `install`: run `_refuse_symlinks()`, iterate over `d in _active_dirs()` and `name in NAMES` with the existing byte comparison and confirmation prompt (`f"{name}: already installed  ({target})"` when identical), retire `_legacy()`, print skipped paths, then print the three connect lines and `"Then, in your agent, say: map my course"`.
- `uninstall`: run `_refuse_symlinks()`, collect `present = [t for t in (*(d / n / "SKILL.md" for d in _all_dirs() for n in NAMES), _legacy()) if t.exists()]`, prompt once unless `-y`, and call `_remove(t)` for each.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_cli_skill.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add groundly/cli/skill.py tests/cli/test_cli_skill.py
git commit -m "feat(cli): extend groundly skill to .agents, Codex and Antigravity"
```

---

### Task 3: Docs, decision record, and full verification gate

**Files:**
- Modify: `docs/groundly-spec.md` (amend decisions 41 and 47)
- Modify: `docs/guides/mcp-hosts.md`
- Modify: `docs/infrastructure/distribution.md`
- Modify: `docs/use-cases/student-modes.md`
- Modify: `.claude/rules/conventions.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: synchronized documentation and updated knowledge graph.

- [ ] **Step 1: Update the docs and decision record**

- `docs/groundly-spec.md`:
  - In decision 41: replace `"Claude Code only for now; Codex comes later"` with `"installed via groundly skill install to ~/.agents/skills/ and any present host among ~/.claude/skills/, ~/.codex/skills/ and ~/.gemini/config/skills/ (Antigravity)"`.
  - In decision 47: update `"installed to ~/.claude/skills/<name>/"` to `"installed to ~/.agents/skills/<name>/ and any present host root among ~/.claude/skills/<name>/, ~/.codex/skills/<name>/ and ~/.gemini/config/skills/<name>/ (design in superpowers/specs/2026-11-18-multi-agent-skills-design.md)"`.
- `docs/guides/mcp-hosts.md`:
  - Update the title and intro to include Antigravity alongside Claude Code and Codex.
  - Under `groundly skill install`, state that it copies the three skills to `~/.agents/skills/` and to whichever of `~/.claude/skills/`, `~/.codex/skills/`, and `~/.gemini/config/skills/` exist on the machine.
  - Under `## Codex`: remove `"there is no skill setup for Codex yet, so building a topic tree and writing questions are Claude Code only for now"` and state that `groundly skill install` installs the skills to `~/.codex/skills/` and `~/.agents/skills/`, while page buttons (decision 46) stay Claude Code-only.
  - Add `## Antigravity` showing `~/.gemini/config/mcp_config.json` with `{"mcpServers": {"groundly": {"command": "groundly", "args": ["mcp"]}}}` and `groundly skill install`.
- `docs/infrastructure/distribution.md`: update line 22 to note that `groundly skill install` covers Claude Code, Codex, Antigravity, and `.agents`.
- `docs/use-cases/student-modes.md`: update Topic tree preconditions, step 1, Out of scope, and criterion 11 to reflect multi-agent `groundly skill` installation while keeping page chores scoped to Claude Code.
- `.claude/rules/conventions.md`: line 23 (`"installs the Claude Code skills"` → `"installs the packaged skills"`).
- `README.md`: lines 22–23 (`(available now, Claude Code)` → `(available now)`, keeping `"With Claude Code installed, the page's Write questions and Grade now buttons start it for you"`).

- [ ] **Step 2: Run the full verification gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest -q`
Expected: all pass.

- [ ] **Step 3: Refresh the knowledge graph**

Run: `graphify update .`

- [ ] **Step 4: Commit**

```bash
git add docs .claude/rules README.md graphify-out
git commit -m "docs: multi-agent groundly skill for .agents, Codex and Antigravity"
```
