# Conventions

## Docs are the source of truth

- Decisions live in `docs/groundly-spec.md` §4/§7 with satellite docs; a changed decision updates the docs in the same change set (`/decision`).
- "Done" for a feature = the acceptance criteria in `docs/use-cases/` pass.

## Python

- Python ≥3.11; typer CLI; FastAPI only inside `groundly serve`; Pydantic v2 + pydantic-settings; type hints on public functions.
- SQLite schema versioned via `PRAGMA user_version` (checked on open; refuse newer-than-known). Integrity rules as constraints where SQLite allows (unique hashes, FKs), not app code.
- pytest for tests (no service containers — SQLite files + stub providers); ruff for lint + format.
- Pin `graphrag`, `llama-index`, `docling`, `sentence-transformers`, `FlagEmbedding` exactly at P1 start; record pins in thesis + export manifest.

## Product surfaces

- MCP tools are the product surface: tool descriptions are UX — write them for the host model. **A retrieval tool's description leads with when to call it, not with how it works**, and says why this course's own material beats what the model already knows; the server's `instructions` carry that norm once for the whole surface. Measured (decision 30/31): under descriptions that opened on retrieval mechanics and named no occasion to call `search`, an undirected host retrieved on 17% of questions and **0 of 17 factoids** — the same host told to search retrieved every time. Citations double as MCP resources (`groundly://<subject>/<file>#page=N`).
- Tool descriptions are measured, not just written: `eval-grounding`'s `host-product` condition runs the real surface, and every results file records the descriptions verbatim under one hash — a reworded tool is a changed experiment.
- CLI verbs are batch lifecycle only (index/import/export/config/ask); anything conversational belongs to the host agent. No TUI.
- Long operations print cost estimates before spending the student's tokens and report per-file/per-item progress.
- User-facing failure messages name the cause specifically ("no readable text — OCR found nothing to extract"), never generic errors.

## Workflow

- Commit finished, reviewed work on a feature branch — **never on `main`** (merges to `main` go through Paul).
- Review diffs with `spec-guardian` (invariants) and `security-reviewer` (threat model) before phase gates.
