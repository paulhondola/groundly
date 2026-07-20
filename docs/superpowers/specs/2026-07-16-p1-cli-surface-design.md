# P1 CLI surface — verbs & flags design

Status: approved 2026-07-16. Source decisions: [groundly-spec.md](../../groundly-spec.md) §4/§8,
[use-cases/knowledge-base.md](../../use-cases/knowledge-base.md) (UC-01, UC-03),
[.claude/rules/conventions.md](../../../.claude/rules/conventions.md).

## Context

P1 delivers `groundly init/index`: the indexing pipeline behind a typer CLI. This design fixes
the command surface — verbs, arguments, flags, output, errors — before implementation, so later
phases add verbs without reshaping P1's grammar.

Scope: **P1 verbs + UC-03 + config** → `init`, `index`, `list`, `remove`, `config` (show + set).
No stubs for later-phase verbs (`import/export/ask/mcp/serve` arrive in their own phases).

## Command surface

### Global

- `groundly --version` — print version, exit. The only global flag.
- Home dir: `~/.groundly/`, overridden by `GROUNDLY_HOME` env var only — **no `--home` flag**;
  one override mechanism.
- Exit codes: 0 success, 1 error, 2 usage (typer default). Failure messages name the specific
  cause, e.g. "scanned PDF — not supported".

### `groundly init <SUBJECT>`

Creates `~/.groundly/<SUBJECT>/` with `manifest.json` (format version + model pins),
`materials/`, `store.db` (schema via `PRAGMA user_version`, WAL + busy_timeout), `progress.db`.
Also creates `~/.groundly/config.toml` on first run if absent.

- **Flags: none.**
- Subject name validated as a safe directory name (alnum, `-`, `_`; it becomes a path component
  and MCP identifier).
- Already-initialized subject → friendly message, exit 0 (idempotent).

### `groundly index <SUBJECT> <PATHS...>`

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
- Subject not initialized → error telling the user to run `groundly init <SUBJECT>` (UC-01
  precondition; no auto-init).

### `groundly list [SUBJECT]`

UC-03. No argument → table of subjects (name, materials, chunks). With subject → materials
table: filename, status (`indexed` / `extraction_failed` / …), pages, chunks. Same data the MCP
`list_subjects` tool will expose in P4.

- **Flags: none.** (No `--json`; agents get MCP, not CLI scraping.)

### `groundly remove <SUBJECT> [MATERIAL]`

UC-03. `MATERIAL` = filename as shown by `list`. Deletes chunks/vectors/sparse/FTS rows and the
file under `materials/` in one transaction; manifest counts re-synced. Prints the
graph-staleness note when a graph exists (harmless no-op until P5).

- `MATERIAL` omitted → removes the **whole subject directory** (materials, index, progress,
  notes). Runs before any store.db access, so a damaged subject is still removable.
- `--yes` / `-y` — skip the confirmation prompt (destructive op; only P1 flag besides config's
  arguments).
- Ambiguous/unknown material name → error listing candidates.

### `groundly config` / `groundly config set <KEY> <VALUE>`

- Bare `config`: prints the config file path + effective values per call class (`chat`,
  `generation`, `extraction`, `router`), **keys masked** to last 3 chars, then the operational
  settings (`ingestion`/`llm`/`retrieval`, decision 18) with their effective values.
- `config set <key> <value>`: dotted keys — providers `chat.model`, `chat.base_url`, `chat.key`;
  settings `ingestion.timeout_seconds`, `ingestion.max_file_size_mb`, `llm.timeout_seconds`,
  `retrieval.context_k`, `retrieval.rerank`, … The first segment routes to a call-class or a
  settings section; unknown section/field or an unparseable value → error naming valid keys
  (typo + type protection, coerced per the Pydantic field). The write regenerates the whole
  documented template from the effective config (`tomllib` is read-only) — no dependency, and
  the commented template survives every `set`.
- P1 needs zero keys (embeddings are local) — `config` exists so provider setup is ready before
  `ask` lands; settings all default to the former constants, so zero-config is unchanged.

### `groundly models install [--force]`

Eagerly downloads bge-m3 into the local Hugging Face cache, instead of relying on the implicit
first-`index` download. Useful before going offline or before wiring up an MCP host, so the first
real tool call doesn't pay the download cost.

- Cache hit (weights already present): prints "already cached — nothing to do" and exits 0
  without calling `snapshot_download` again.
- Cache miss: prints the same "(one-time, ~2.3 GB)" lead-in `index` already shows on a lazy
  download, then fetches. `snapshot_download` writes its own progress to stderr.
- `--force`: skips the cache-hit fast path and always re-fetches; HF's own cache dedupes
  unchanged files, so this re-verifies rather than wiping and refetching.
- Download failure (network/HF error) → named cause via `_fail()`, never a raw traceback.
- Zero-key: doesn't touch `config.toml` or any LLM provider — foundation-layer download only.
  Scoped to bge-m3 only; the reranker (`bge-reranker-v2-m3`) has no code yet (`retrieval/` is an
  empty stub) so it isn't covered — `models` is its own sub-app so adding it later is one more
  sub-command, not a restructuring.

### `groundly models uninstall [--yes]`

Removes bge-m3 from the local Hugging Face cache (all cached revisions, via
`huggingface_hub.scan_cache_dir()` — not just the pinned revision's snapshot). Frees the ~2.3 GB
the model occupies when a student wants to reclaim disk space; re-running `index` or `models
install` re-downloads it.

- Not cached: prints "is not cached — nothing to do" and exits 0.
- Cached: confirmation prompt (destructive, same `--yes`/`-y` pattern as `remove`), then deletes
  and prints "removed ... from the cache".

## Module layout

Client layer thin, logic in services (architecture invariants):

- `groundly/cli/__init__.py` — typer `app`, the six verb groups (`init`, `index`, `list`,
  `remove`, `config`, `models`), rich output. No business logic.
- `groundly/core/paths.py` — home resolution (`GROUNDLY_HOME`), subject dir layout,
  subject-name validation.
- `groundly/core/store.py` — SQLite open helper (WAL + busy_timeout on every connection,
  `PRAGMA user_version` check), P1 schema (materials, chunks, vectors, sparse, FTS5), manifest
  read/write.
- `groundly/core/config.py` — Pydantic models for `config.toml`: providers per call class +
  operational settings (decision 18); `load_provider`/`load_settings`/`set_key`/`render_config_toml`/
  `mask_key`. `groundly/llm/config.py` re-exports the provider surface (boundary preserved).
- `groundly/llm/embeddings.py` — owns bge-m3 cache/download logic (`cached_snapshot`,
  `ensure_downloaded`, `remove_cached`); `BgeM3Embedder._load()` (lazy) and `groundly models
  install`/`uninstall` (eager) all call it.
- `groundly/ingestion/` — the index pipeline (hash-skip, Docling subprocess, chunker, embedder,
  per-file transaction). The pipeline is the bulk of P1 and gets its own detailed implementation
  plan; the CLI wires `index` to its entry point.

## Acceptance (traceable to UC-01/UC-03)

- `groundly init PDSS` → dir tree exists, `store.db` opens with WAL + expected `user_version`;
  re-run is a no-op.
- A real digital lecture PDF round-trips to `indexed` with correct page/chunk counts; chunks
  carry page + heading path.
- Scanned PDF → `extraction_failed`, "scanned PDF — not supported"; every file reaches a
  terminal state.
- Re-run on unchanged folder → zero re-embedding; adding one file embeds exactly one file.
- Ctrl-C mid-index → re-run resumes; store not corrupted (WAL).
- `groundly remove` leaves no retrievable rows in any channel; manifest counts synced.
- `groundly config set chat.model x` visible in `config` output, key masked; unknown key
  rejected.
