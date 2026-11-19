# Type checking with pyrefly — design

Date: 2026-11-19. Size: a small change (one PR).

## Intent

Agents write most of Groundly. Today nothing checks types: CI runs ruff, import-linter and
pytest, and the edit hook runs ruff only. A type checker turns a whole class of mistakes
(None reaching an attribute access, a name unbound on one branch, a dict row with the wrong
shape) into an error at edit time instead of a test failure later or a runtime crash.

## Goals

- `pyrefly check` passes with zero errors on `groundly/` under pyrefly's `default` preset.
- It runs in three places: CI, the lefthook pre-commit hook, and the Claude Code edit hook.
- Every fix keeps behaviour. A fix that turns out to change behaviour is a bug fix, gets its
  own test, and is called out in the PR.

## Non-goals

- The `strict` preset (264 errors today). It comes later, a directory at a time.
- Type-checking `tests/`.
- Changing what any function does to satisfy the checker.

## Anti-goals

- Silencing instead of fixing: no baseline file, no blanket `# type: ignore`, no casts to
  `Any`. An ignore needs a reason on the line, and is a last resort (third-party stubs only).
- A gate that agents learn to work around because it is noisy or slow.

## Approaches

| Checker | Errors on `groundly/` | Speed | Trade-off |
|---|---|---|---|
| **pyrefly 1.4.0** (Meta, Rust) — chosen | basic 0 / default 42 / strict 264 | 1.3 s | Stable release, fast, presets give a ratchet path. Younger than mypy. |
| ty 0.0.97 (Astral) | 28 | 0.8 s warm | Same vendor as ruff and uv, but still beta: diagnostics and config change between releases. |
| mypy 2.4.1 | 27 / strict 171 | slower | The reference; per-edit runs are slow without its daemon. |
| basedpyright 1.40.2 | standard 50 / strict 1228 | Node | Strict mode is far noisier on this code; adds a Node toolchain. |

Paul picked pyrefly.

## Design

**Install.** `pyrefly==1.4.0` in the `dev` extra. Exact pin because a checker's diagnostics
change between releases, so an upgrade is a deliberate change with its fixes, not something
`uv lock` drifts into. (1.4.0 is the newest stable release older than two weeks.)

**Config.** A `[tool.pyrefly]` section in `pyproject.toml`: project includes `groundly`,
preset `default`, Python 3.11. No separate `pyrefly.toml`.

**Fixing the 42 errors**, grouped by cause:

- `cli/app.py:_fail` always raises but is typed `-> None`. Typing it `-> NoReturn` tells the
  checker the code after it is unreachable; that clears most of the 21 CLI errors (unbound
  names and `None` access after a failed lookup).
- Optional narrowing (`web/app.py`, `web/runs.py`, `core/bundle.py`, `core/progress.py`):
  where None is impossible by an invariant, state it (an early return or an `assert` that
  names the invariant); where None is possible, handle it.
- `cursor.lastrowid` is `int | None` (`core/store.py`, `core/progress.py`): it is set after
  an `INSERT`, so assert it once in a small helper used by each call site.
- Dict rows crossing modules (`agents/quiz.py`, `agents/verifier.py:151`): a `TypedDict` only
  where the checker needs the shape, not a schema for every row.
- Framework typing (`cli/serve.py` ASGI app, `web/app.py` Starlette lifespan and JSON body):
  correct the annotations.
- `core/store.py:524`: `serialize_float32` takes a list; the index path passes numpy rows,
  which work at runtime. Widen our annotation or convert explicitly, whichever keeps the
  zero-copy path.

A first read found narrowing gaps and unstated invariants, no confirmed runtime bugs. The
PR re-checks each site as it fixes it.

**Enforcement.**

- CI: a `Type check (pyrefly)` step after import-linter.
- lefthook pre-commit: a `pyrefly check` job beside `lint-imports`.
- Claude Code PostToolUse hook: after ruff, `pyrefly check` on the edited `.py` file; on
  errors it exits 2 so the agent sees them right after its edit (the edit is already made,
  so this is feedback, not a block). Per file, so a half-finished multi-file refactor does
  not report errors in files the agent has not reached yet; pre-commit and CI catch the rest.

**Docs.** `.claude/rules/conventions.md` (the Python tooling line) and
`docs/infrastructure/distribution.md` (the CI line) name pyrefly. Dev tooling is not in the
spec's decision register, so no `§7` entry.

## Left to the builder

- Whether a single site is better served by an assert, an early return or a TypedDict.
- Exact config key names (pyrefly's docs for 1.4.0).

## Later

Ratchet to `strict` one directory at a time, `agents/` and `core/` first (the gates and the
store), in separate PRs.
