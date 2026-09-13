# Conventions

## Docs are the source of truth

- Decisions live in `docs/groundly-spec.md` §4/§7 with satellite docs; a changed decision updates the docs in the same change set (`/decision`).
- "Done" for a feature = the acceptance criteria in `docs/use-cases/` pass.
- Measurements live in `docs/thesis/`; the retired experiments' code is at tag `thesis-experiments-2026-09`.

## Python

- Python ≥3.11; typer CLI; Pydantic v2 + pydantic-settings; type hints on public functions.
- SQLite schema versioned via `PRAGMA user_version` (checked on open; refuse newer-than-known). Integrity rules as constraints where SQLite allows (unique hashes, FKs), not app code.
- pytest for tests (no service containers — SQLite files + stub providers); ruff for lint + format.
- **Comments and docstrings state intent and invariants.** History belongs in git and in `docs/`: no "used to", no decision narration, no measurement tables in code.

## Product surfaces

- MCP tools are the product surface: tool descriptions are UX — write them for the host model. **A retrieval tool's description leads with when to call it, not with how it works**, and says why this course's own material beats what the model already knows; the server's `instructions` carry that norm once for the whole surface.
- **The server's `instructions` state the norm; tool descriptions say which tool. Never rank one tool above another server-wide.** Measured (decision 31): instructions ending "`ask` returns an enforced, cited answer; `search` returns raw chunks for you to compose from" took a `search`-only host to **4/48** questions retrieved; deleting that one clause took it to **29/48** (Fisher p=8.3e-08). A tool preferred server-wide is invisible to whoever allowlists a subset later.
- The surface is pinned by `tests/mcp/test_mcp_server.py::test_the_tool_surface_is_exactly_these_seven_entries` — adding or removing a tool is a deliberate change, not a side effect.
- Citations double as MCP resources (`groundly://<subject>/<file>#page=N`).
- CLI verbs are batch lifecycle only (init/index/list/remove/search/import/export/export-deck/export-graph/config/models/mcp/serve); anything conversational belongs to the host agent. No TUI.
- Long operations print cost estimates before spending the student's tokens and report per-file/per-item progress.
- User-facing failure messages name the cause specifically ("no readable text — OCR found nothing to extract"), never generic errors.

## Workflow

- Commit finished, reviewed work on a feature branch — **never on `main`** (merges to `main` go through Paul).
- Review diffs with `spec-guardian` (invariants) and `security-reviewer` (threat model) before phase gates.
