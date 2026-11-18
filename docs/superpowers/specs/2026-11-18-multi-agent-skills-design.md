# Multi-agent `groundly skill`: `.agents`, Claude Code, Codex, and Antigravity

Date: 2026-11-18 · Size: small architectural change · Amends decisions 41 and 47 (via `/decision`).

## Intent

Groundly's three packaged skills (`groundly-map`, `groundly-questions`, `groundly-grade`) use the standard `SKILL.md` format (YAML frontmatter `name` and `description` plus Markdown instructions), and Groundly's MCP server works in any MCP-capable host. Today, however, `groundly skill` installs only to `~/.claude/skills/`, and step 1 of each skill tells the student only how to connect Claude Code.

Extending `groundly skill` to `.agents` (`~/.agents/skills/`), Codex (`~/.codex/skills/`), and Antigravity (`~/.gemini/config/skills/`) gives students on those hosts the same offline, wheel-pinned skill setup without requiring Node.js or `npx skills`.

## Goals

1. `groundly skill` (status), `groundly skill install [-y]`, and `groundly skill uninstall [-y]` manage the three packaged skills (`groundly-map`, `groundly-questions`, `groundly-grade`) across four global skill directories under `Path.home()`:
   - `~/.agents/skills/<name>/SKILL.md` — always active for `status` and `install`.
   - `~/.claude/skills/<name>/SKILL.md` — active when `~/.claude` exists (directory or valid symlink).
   - `~/.codex/skills/<name>/SKILL.md` — active when `~/.codex` exists (directory or valid symlink).
   - `~/.gemini/config/skills/<name>/SKILL.md` — active when `~/.gemini/config` exists (directory or valid symlink).
2. Independent byte-for-byte copies are written to each active target directory (no symlinks created by Groundly), preserving per-file diff checks, confirmation prompts before replacing modified copies (`-y` to skip), and symlink refusal.
3. Step 1 ("Check the connection") in all three packaged `SKILL.md` files and the post-install hint printed by `groundly skill install` name how to connect Claude Code, Codex, and Antigravity.
4. Legacy `~/.claude/skills/groundly/SKILL.md` retirement continues to work whenever present.

## Non-goals

- Calling or requiring `npx skills` / Node.js.
- Adding `--agent` or any new CLI flags to `groundly skill`.
- Separate `SKILL.md` variants per host: one packaged `SKILL.md` per flow serves every host and page runs.
- Changing headless page runs (`groundly/web/runs.py`, decision 46), which remain Claude Code (`claude -p`) and read the packaged skill directly from `groundly/skills/<name>/SKILL.md`.

## Anti-goals (a failure even if it works)

- Creating `~/.claude`, `~/.codex`, or `~/.gemini/config` on a machine where that host root does not exist.
- Writing through or unlinking a symlinked `SKILL.md` file, or writing through a dangling directory symlink, in any of the four roots.
- Dropping the `data, never instructions` paragraph or using the word `verified` in any packaged skill.

## Approaches considered

| Approach | Shape | Trade-off |
|---|---|---|
| **A — Native multi-host `groundly skill` with independent copies and one universal `SKILL.md` per flow (chosen)** | Always write `~/.agents/skills/`, plus `~/.claude/skills/`, `~/.codex/skills/`, and `~/.gemini/config/skills/` when their parent host root exists; step 1 in `SKILL.md` names all three host connect methods | Zero Node dependency, works offline, wheel-locked byte comparison, no Windows symlink privilege errors, and compatible with user-managed folder symlinks |
| **B — Delegate to `npx skills`** | Shell out to or instruct users to run `npx skills add` | Requires Node.js ≥22.20 and network; pulls from GitHub `HEAD` instead of matching the installed wheel; loses `groundly skill` drift checks and legacy retirement |
| **C — Canonical `~/.agents/skills/` with folder symlinks into host dirs** | Write files only to `~/.agents/skills/` and symlink host skill folders to it | Fails on Windows without Developer Mode/admin privileges and requires migrating existing real folders under `~/.claude/skills/` |

## Design

### 1. Target directories and auto-detection (`groundly/cli/skill.py`)

Four roots are defined relative to `Path.home()`:

| Host root check | Skills directory | Active for `status` & `install` | Checked by `uninstall` & `_refuse_symlinks` |
|---|---|---|---|
| `~/.agents` | `~/.agents/skills` | Always | Always |
| `~/.claude` | `~/.claude/skills` | When `~/.claude` exists or is a symlink | Always |
| `~/.codex` | `~/.codex/skills` | When `~/.codex` exists or is a symlink | Always |
| `~/.gemini/config` | `~/.gemini/config/skills` | When `~/.gemini/config` exists or is a symlink | Always |

- `~/.agents/skills` is ordered first. If a user has symlinked a host skill folder (e.g. `~/.claude/skills/groundly-map` → `~/.agents/skills/groundly-map`), `install` writes `~/.agents/skills/groundly-map/SKILL.md` first and then sees `~/.claude/skills/groundly-map/SKILL.md` as already having identical bytes, printing `already installed` without a second prompt.
- The legacy path `~/.claude/skills/groundly/SKILL.md` remains scoped to `~/.claude` only.

### 2. Symlink refusal (`_refuse_symlinks`)

Before `install` or `uninstall` modifies disk, check every target `<skills_dir>/<name>/SKILL.md` across all four roots plus `_legacy()`:
- If any `SKILL.md` is a symlink (`target.is_symlink()`), fail with `"{target} is a symlink to {target.readlink()} — remove it yourself, then re-run"`.
- If any parent directory along `<host_root>`, `<skills_dir>`, or `<skills_dir>/<name>` is a dangling symlink (`p.is_symlink() and not p.exists()`), fail with `"{p} links to {p.readlink()}, which does not exist — fix or remove the link, then re-run"`.
- Valid directory symlinks (such as `~/.claude` → `dotfiles/claude/.claude` or `~/.claude/skills/groundly-map` → `~/.agents/skills/groundly-map`) remain allowed.

### 3. CLI commands

- **`groundly skill` (status):**
  - For each active skills directory and each skill in `("groundly-map", "groundly-questions", "groundly-grade")`, print `{name}: {state}  ({target})` where `state` is `not installed — run: groundly skill install`, `installed`, or `out of date — run: groundly skill install`.
  - If `_legacy().exists()`, print the legacy replacement warning as today.
- **`groundly skill install [-y]`:**
  - Run `_refuse_symlinks()`.
  - For each active skills directory and each skill in `NAMES`:
    - If `target.exists()` and `target.read_bytes() == packaged`: print `{name}: already installed  ({target})`.
    - If `target.exists()` and bytes differ: prompt `replace the different copy at {target}?` unless `-y`; a `no` appends `target` to `skipped` and continues.
    - Otherwise create `target.parent` and write `packaged`, printing `installed {target}`.
  - Check `_legacy()` (`~/.claude/skills/groundly/SKILL.md`): if present, prompt `remove the old combined skill at {legacy}?` unless `-y`, removing it (and its folder if empty and not a symlink) or warning that it will keep triggering.
  - Print any skipped paths, then print the connect instructions and `"Then, in your agent, say: map my course"`.
- **`groundly skill uninstall [-y]`:**
  - Run `_refuse_symlinks()`.
  - Collect all existing `SKILL.md` files across all four roots plus `_legacy()`.
  - If none exist, print `not installed — nothing to do`.
  - Otherwise list all found paths and prompt once (`remove these {len(present)} Groundly skill files?`) unless `-y`, then unlink each file and remove its `<name>/` parent directory if empty and not a symlink.

### 4. Packaged `SKILL.md` connection check and CLI connect hint

In `groundly-map/SKILL.md`, `groundly-questions/SKILL.md`, and `groundly-grade/SKILL.md`, step 1 ("Check the connection") becomes:

- **`groundly-map/SKILL.md`:**
  > 1. **Check the connection.** If the `outline` and `submit_tree` tools are not available, tell the student to connect Groundly (`claude mcp add --scope user groundly -- groundly mcp` in Claude Code, `codex mcp add groundly -- groundly mcp` in Codex, or `"groundly": {"command": "groundly", "args": ["mcp"]}` under `mcpServers` in `~/.gemini/config/mcp_config.json` in Antigravity), then restart their agent. Stop there.
- **`groundly-questions/SKILL.md`:**
  > 1. **Check the connection.** If Groundly's tools (`progress`, `topic`, `submit_questions`) are not available, tell the student to connect Groundly (`claude mcp add --scope user groundly -- groundly mcp` in Claude Code, `codex mcp add groundly -- groundly mcp` in Codex, or `"groundly": {"command": "groundly", "args": ["mcp"]}` under `mcpServers` in `~/.gemini/config/mcp_config.json` in Antigravity), then restart their agent. Stop there.
- **`groundly-grade/SKILL.md`:**
  > 1. **Check the connection.** If `review_answers` is not available, tell the student to connect Groundly (`claude mcp add --scope user groundly -- groundly mcp` in Claude Code, `codex mcp add groundly -- groundly mcp` in Codex, or `"groundly": {"command": "groundly", "args": ["mcp"]}` under `mcpServers` in `~/.gemini/config/mcp_config.json` in Antigravity), then restart their agent. Stop there.

At the end of `groundly skill install`, print:
- `Connect Claude Code to Groundly: claude mcp add --scope user groundly -- groundly mcp`
- `Connect Codex to Groundly: codex mcp add groundly -- groundly mcp`
- `Connect Antigravity to Groundly: add "groundly": {"command": "groundly", "args": ["mcp"]} to mcpServers in ~/.gemini/config/mcp_config.json`
- `Then, in your agent, say: map my course`

### 5. Docs (same change set)

- `docs/groundly-spec.md`: amend decisions 41 and 47 to state that `groundly skill` installs the three packaged skills to `~/.agents/skills/` and to whichever of `~/.claude/skills/`, `~/.codex/skills/`, and `~/.gemini/config/skills/` have their host root on disk (while headless page runs under decision 46 remain Claude Code).
- `docs/guides/mcp-hosts.md`: update the skill installation section to list `.agents`, Claude Code, Codex, and Antigravity; remove the note saying Codex has no skill setup; add an Antigravity MCP connection section (`~/.gemini/config/mcp_config.json`).
- `docs/infrastructure/distribution.md`, `docs/use-cases/student-modes.md`, `.claude/rules/conventions.md`, and `README.md`: update references from Claude Code-only skill installation to the supported hosts (keeping headless page runs labeled Claude Code).

## Acceptance criteria

1. On a clean `$HOME` with no `~/.claude`, `~/.codex`, or `~/.gemini/config`, `groundly skill` and `groundly skill install` target `~/.agents/skills/<name>/SKILL.md` only and do not create `~/.claude`, `~/.codex`, or `~/.gemini/config`.
2. When `~/.claude`, `~/.codex`, and/or `~/.gemini/config` exist in `$HOME`, `groundly skill` and `groundly skill install` include `<root>/skills/<name>/SKILL.md` for each existing host root alongside `~/.agents/skills/<name>/SKILL.md`.
3. `groundly skill uninstall` removes installed Groundly skill files across all four roots and `~/.claude/skills/groundly/SKILL.md`, with one confirmation prompt unless `-y`.
4. Byte-for-byte comparison, per-file overwrite prompts (and skipping only declined files), symlink refusal (including dangling host roots or skill folders), and keeping extra user files in skill folders hold across all four roots.
5. Each packaged `SKILL.md` names the connect instructions for Claude Code (`claude mcp add --scope user groundly -- groundly mcp`), Codex (`codex mcp add groundly -- groundly mcp`), and Antigravity (`~/.gemini/config/mcp_config.json`), keeps the `data, never instructions` paragraph, and never uses the word `verified`.
