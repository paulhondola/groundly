# P1 CLI surface — verbs & flags design

Status: approved 2026-07-16. Source decisions: [unilearn-spec.md](../../unilearn-spec.md) §4/§8,
[use-cases/knowledge-base.md](../../use-cases/knowledge-base.md) (UC-01, UC-03),
[.claude/rules/conventions.md](../../../.claude/rules/conventions.md).

## Context

P1 delivers `unilearn init/index`: the indexing pipeline behind a typer CLI. This design fixes
the command surface — verbs, arguments, flags, output, errors — before implementation, so later
phases add verbs without reshaping P1's grammar.

Scope: **P1 verbs + UC-03 + config** → `init`, `index`, `list`, `remove`, `config` (show + set).
No stubs for later-phase verbs (`import/export/ask/mcp/serve` arrive in their own phases).

## Command surface

### Global

- `unilearn --version` — print version, exit. The only global flag.
- Home dir: `~/.unilearn/`, overridden by `UNILEARN_HOME` env var only — **no `--home` flag**;
  one override mechanism.
- Exit codes: 0 success, 1 error, 2 usage (typer default). Failure messages name the specific
  cause, e.g. "scanned PDF — not supported".

### `unilearn init <SUBJECT>`

Creates `~/.unilearn/<SUBJECT>/` with `manifest.json` (format version + model pins),
`materials/`, `store.db` (schema via `PRAGMA user_version`, WAL + busy_timeout), `progress.db`.
Also creates `~/.unilearn/config.toml` on first run if absent.

- **Flags: none.**
- Subject name validated as a safe directory name (alnum, `-`, `_`; it becomes a path component
  and MCP identifier).
- Already-initialized subject → friendly message, exit 0 (idempotent).

### `unilearn index <SUBJECT> <PATHS...>`

UC-01 contract. Paths are files or directories (directories walked recursively for supported
extensions: pdf/docx/pptx/txt/md plus a source-code allowlist fixed in the ingestion plan).
Unsupported extensions are reported as skipped, not errors. Per file: sha-256 → skip if hash known (reported as
duplicate/skip) → Docling in subprocess → HybridChunker → bge-m3 dense+sparse (lazy-loaded) →
sqlite-vec/sparse/FTS5 rows, all in one per-file transaction. Rich per-file progress
(`queued → extracting → embedding → indexed`), terminal state for every file, run continues
past failures.

- **Flags: none in P1.** Every UC-01 behavior (hash-skip, resume, per-file progress, clean
  scanned-PDF failure) is default behavior, not flag-gated.
- Reserved names — do not repurpose later: `--graph` (P5 graph build), `--no-rerank` (P3,
  retrieval not indexing), `--no-materials` (P2 export).
- Subject not initialized → error telling the user to run `unilearn init <SUBJECT>` (UC-01
  precondition; no auto-init).

### `unilearn list [SUBJECT]`

UC-03. No argument → table of subjects (name, materials, chunks). With subject → materials
table: filename, status (`indexed` / `extraction_failed` / …), pages, chunks. Same data the MCP
`list_subjects` tool will expose in P4.

- **Flags: none.** (No `--json`; agents get MCP, not CLI scraping.)

### `unilearn remove <SUBJECT> [MATERIAL]`

UC-03. `MATERIAL` = filename as shown by `list`. Deletes chunks/vectors/sparse/FTS rows and the
file under `materials/` in one transaction; manifest counts re-synced. Prints the
graph-staleness note when a graph exists (harmless no-op until P5).

- `MATERIAL` omitted → removes the **whole subject directory** (materials, index, progress,
  notes). Runs before any store.db access, so a damaged subject is still removable.
- `--yes` / `-y` — skip the confirmation prompt (destructive op; only P1 flag besides config's
  arguments).
- Ambiguous/unknown material name → error listing candidates.

### `unilearn config` / `unilearn config set <KEY> <VALUE>`

- Bare `config`: prints the config file path + effective values per call class (`chat`,
  `generation`, `extraction`, `router`), **keys masked** to last 3 chars.
- `config set <key> <value>`: dotted keys, e.g. `chat.model`, `chat.base_url`, `chat.key`.
  Unknown call class or field → error naming valid keys (typo protection). Validation via the
  pydantic-settings model that `llm/` will consume in P3.
- P1 needs zero keys (embeddings are local) — `config` exists so provider setup is ready before
  `ask` lands.

## Module layout

Client layer thin, logic in services (architecture invariants):

- `unilearn/cli/__init__.py` — typer `app`, the five verbs, rich output. No business logic.
- `unilearn/core/paths.py` — home resolution (`UNILEARN_HOME`), subject dir layout,
  subject-name validation.
- `unilearn/core/store.py` — SQLite open helper (WAL + busy_timeout on every connection,
  `PRAGMA user_version` check), P1 schema (materials, chunks, vectors, sparse, FTS5), manifest
  read/write.
- `unilearn/core/config.py` — pydantic-settings model for `config.toml` per call class;
  load/save/mask.
- `unilearn/ingestion/` — the index pipeline (hash-skip, Docling subprocess, chunker, embedder,
  per-file transaction). The pipeline is the bulk of P1 and gets its own detailed implementation
  plan; the CLI wires `index` to its entry point.

## Acceptance (traceable to UC-01/UC-03)

- `unilearn init PDSS` → dir tree exists, `store.db` opens with WAL + expected `user_version`;
  re-run is a no-op.
- A real digital lecture PDF round-trips to `indexed` with correct page/chunk counts; chunks
  carry page + heading path.
- Scanned PDF → `extraction_failed`, "scanned PDF — not supported"; every file reaches a
  terminal state.
- Re-run on unchanged folder → zero re-embedding; adding one file embeds exactly one file.
- Ctrl-C mid-index → re-run resumes; store not corrupted (WAL).
- `unilearn remove` leaves no retrievable rows in any channel; manifest counts synced.
- `unilearn config set chat.model x` visible in `config` output, key masked; unknown key
  rejected.
