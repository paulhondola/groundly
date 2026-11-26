# Conventions

## Docs are the source of truth

- Decisions live in `docs/groundly-spec.md` §4/§7 with satellite docs; a changed decision updates the docs in the same change set (`/decision`).
- "Done" for a feature = the acceptance criteria in `docs/use-cases/` pass.
- Measurement protocols and results are kept outside the repo; the retired experiments' code is at tag `thesis-experiments-2026-09`.

## Python & Web

- Python ≥3.11; typer CLI; FastAPI + Pydantic v2 for the local page API (`groundly/web/`, runtime `/docs`/`/redoc`/`/openapi.json` disabled, strict validation after `_require_page_json`); type hints on public functions.
- Frontend (`web/`): Svelte 5 + TypeScript + Tailwind CSS v4 compiled via Vite+ (`vp build`) into committed `groundly/web/static/page.{js,css}` (`web/` excluded from wheel; zero Node at install or `pytest` time). Untrusted Layer-3 strings enter the DOM strictly as text nodes (zero `{@html}` or HTML-inserting sinks).
- SQLite schema versioned via `PRAGMA user_version` (checked on open; refuse newer-than-known). Integrity rules as constraints where SQLite allows (unique hashes, FKs), not app code.
- pytest for tests (no service containers — SQLite files + stub embedders; no Node invoked during `pytest`); ruff for lint + format; pyrefly (default preset) for types; `vp lint` / `vp fmt` / `vp test` / `svelte-check` for `web/`.
- **Comments and docstrings state intent and invariants.** History belongs in git and in `docs/`: no "used to", no decision narration, no measurement tables in code.

## Product surfaces

- **A learning session happens entirely in the student's host plus Groundly's local page** (decision 38). Groundly exports to no other study app; a feature that sends the student elsewhere to study is out of scope. The topic-tree page (`groundly serve`) is that local page: it shows the map and is where the student takes tests (decision 44), and it links to a stored PDF or shows chunk text, never to another app. Its **Write questions** and **Grade now** buttons start the student's own Claude Code headless for those two chores (decision 46); conversation (explaining, mapping) stays in the host chat, and the page is never a chat client.
- MCP tools are the product surface: tool descriptions are UX — write them for the host model. **A retrieval tool's description leads with when to call it, not with how it works**, and says why this course's own material beats what the model already knows; the server's `instructions` carry that norm once for the whole surface.
- **The server's `instructions` state the norm; tool descriptions say which tool. Never rank one tool above another server-wide.** Measured (decision 31): instructions ending "`ask` returns an enforced, cited answer; `search` returns raw chunks for you to compose from" took a `search`-only host to **4/48** questions retrieved; deleting that one clause took it to **29/48** (Fisher p=8.3e-08). A tool preferred server-wide is invisible to whoever allowlists a subset later.
- The surface is pinned by `tests/mcp/test_mcp_server.py::test_the_tool_surface_is_exactly_these_eleven_entries` — adding or removing a tool is a deliberate change, not a side effect.
- Citations are `groundly://<subject>/<file>#page=N` URIs: `get_page(uri)` opens the cited page, and the URI read as an MCP resource returns the whole document (a fragment is not part of a resource's identity).
- CLI verbs are batch lifecycle only (index/list/title/remove/drop/search/import/export/config/models/skill/mcp/serve/start); `serve` opens the topic-tree and test page on loopback, `start` serves that page plus the MCP tools at `/mcp` from one process, and `skill` installs the packaged skills that map a course, write questions and grade (decision 47); `-y/--yes` is the one way to skip a prompt; anything conversational belongs to the host agent. No TUI. Export is also a page action (decision 53): the page downloads the same bundle `groundly export` writes.
- A subject's name is its ID: strict, stable, used in commands, paths and citations. Its `title` is a label for people and models, shown beside the name and never accepted in its place (decision 42).
- Long operations report per-file/per-item progress.
- User-facing failure messages name the cause specifically ("no readable text — OCR found nothing to extract"), never generic errors.

## Workflow

- Commit finished, reviewed work on a feature branch — **never on `main`** (merges to `main` go through Paul).
- Review diffs with `spec-guardian` (invariants) and `security-reviewer` (threat model) before phase gates.
