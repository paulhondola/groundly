Ingestion pipeline (P1 core) — implementation plan

Context

The CLI skeleton (branch cli-setup) is merged to main; all verbs are stubs. This branch — ingestion-pipeline, created from origin/main (local main is stale) — implements P1 for real: init, index, list, remove backed by the actual stores and the Docling→chunk→embed pipeline. config stays a stub (separate small change, needed by P3). Contract: UC-01/UC-03 acceptance criteria in docs/use-cases/knowledge-base.md; design constraints from docs/architecture/data-model.md, docs/tech-stack/tech-stack.md, .claude/rules/architecture.md.

Decisions made with Paul during planning:
- Embedder = FlagEmbedding (BGEM3FlagModel): dense + learned sparse from one encode call. The tech-stack doc row currently says sentence-transformers → update it in this change set (conventions: doc changes travel with the decision).
- Branch scope: ingestion only — no config implementation.

Setup

1. git checkout main && git pull && git checkout -b ingestion-pipeline.
2. Version pinning (P1-start policy, tech-stack.md §"Version pinning policy"): in pyproject.toml, pin exact docling==2.113.0, llama-index==0.14.23, graphrag==3.1.0, sentence-transformers==5.6.0 (versions from current uv.lock), add FlagEmbedding (pin the version uv resolves). Resolve bge-m3's current hf_revision sha from Hugging Face and hardcode it as a constant (below). Keep dev tools under [project.optional-dependencies].dev — CI uses uv sync --extra dev; don't restructure.

Modules (layering: cli → ingestion → core; ingestion writes stores, never serves queries)

unilearn/core/paths.py

- unilearn_home() -> Path — $UNILEARN_HOME or ~/.unilearn.
- validate_subject_name(name) — alnum/-/_ only (path component + MCP id); specific error otherwise.
- subject_dir(name), discover_subjects() -> list[str] — scan */manifest.json (no registry DB, data-model.md).

unilearn/core/manifest.py

- Constants: FORMAT_VERSION=1, EMBEDDING_MODEL="BAAI/bge-m3", HF_REVISION="<pinned sha>", DIM=1024, CHUNK_MAX_TOKENS=512, CHUNK_OVERLAP=64.
- Pydantic model mirroring the manifest.json contract in data-model.md; load/save; sync_counts(store) after every mutation (UC-03 AC).

unilearn/core/store.py

- connect(path) — every connection: WAL, busy_timeout=5000, foreign_keys=ON, sqlite-vec extension loaded, PRAGMA user_version checked (refuse newer than known).
- create_store(path) — DDL at user_version=1:
  - materials(id, filename, sha256 UNIQUE, status CHECK IN ('indexed','extraction_failed'), pages, error, indexed_at)
  - chunks(id, material_id FK ON DELETE CASCADE, page, heading_path, text, token_count)
  - vectors = vec0(embedding float[1024]), rowid = chunk id (no FK support in vec0 → explicit delete)
  - sparse_terms(token_id, chunk_id FK ON DELETE CASCADE, weight) + index on token_id
  - chunks_fts = FTS5 external-content on chunks(text) + the standard ai/ad/au sync triggers
- create_progress(path) — valid SQLite file, user_version=1, no tables (nothing writes it in P1; it never travels, so schema can grow locally in P3 without interchange impact).
- Queries for the CLI: list_materials(conn), indexed_hashes(conn), remove_material(conn, ...) (one transaction: cascades cover chunks/sparse/FTS; vectors deleted by chunk rowids explicitly), failed-row replace for retries.

unilearn/ingestion/extract_worker.py (runs as python -m unilearn.ingestion.extract_worker <in-file> <out-json>)

- Docling convert + HybridChunker (bge-m3 tokenizer, max 512, overlap per constants); heading path from chunk meta; page from doc-item provenance.
- Writes JSON: {pages: N, chunk

unilearn/ingestion/embed.py

- Embedder protocol: encode(texts) -> (dense: list[list[float]], sparse: list[dict[int, float]]).
- BgeM3Embedder — lazy singleton (never load at import; rules/architecture.md), BGEM3FlagModel(EMBEDDING_MODEL, revision=HF_REVISION), return_dense=True, return_sparse=True. First-run note about the one-time ~2.3 GB model download (conventions: long ops announce cost).

unilearn/ingestion/pipeline.py

- index_paths(subject, paths, embedder=None, on_event=...) -> list[FileResult]:
  a. Expand dirs recursively; extension allowlist pdf docx pptx txt md py c cpp h java js ts (unsupported → reported as skipped, per spec).
  b. sha-256 per file; hash already indexed → skip/duplicate (A3). Hash present as extraction_failed → delete the failed row and retry (transient-crash recovery).
  c. Per file: copy into materials/ (name collision with different content → suffix with short hash) → extract → embed → one transaction writing materials/chunks/vectors/sparse rows (+FTS via triggers) → commit → sync_counts. Failure → terminal extraction_failed row + delete the materials/ copy; run continues. Ctrl-C loses at most the in-flight file (A4).
  d. on_event callback drives the CLI's queued → extracting → embedding → indexed per-file progress.

unilearn/cli/__init__.py

- Replace the four stubs: init (create dirs + manifest + both DBs + config.toml if absent; idempotent), index (rich per-file progress from on_event), list (subjects table / materials table), remove (confirm unless --yes; ambiguous filename → error listing candidates with sha prefixes; accept a sha prefix as the identifier too). Grammar unchanged — tests in tests/test_cli.py keep passing except stub-exit assertions, which move to real behavior.

Doc updates (same change set)

- docs/tech-stack/tech-stack.md embeddings row: sentence-transformers → FlagEmbedding (one forward pass, dense+sparse); note in the pinning section that FlagEmbedding is pinned too. Mirror the one cell in docs/unilearn-spec.md §4 if it names the library.
- Record the four exact pins + hf_revision where the pinning policy says (pyproject + core/manifest.py constants).

Tests (pytest; SQLite tmp files + stub providers — no model downloads in CI)

- tests/test_paths.py — home override, name validation, discovery.
- tests/test_store.py — WAL/user_version/FK cascade on; newer user_version refused; remove leaves zero rows in chunks/sparse/FTS/vectors.
- tests/test_pipeline.py — with StubEmbedder (deterministic vectors) + tiny .md/.txt fixtures (Docling handles these without layout models): index → rows land with heading paths; re-run → all skipped; add one file → exactly one embedded; embedder raising on file 2 → file 1 committed, re-run completes; unsupported extension skipped.
- tests/test_cli.py — update stub assertions to real behavior (init idempotent, list output, remove --yes).
- @pytest.mark.slow (excluded by default via pyproject markers/addopts): real bge-m3 encode shape/normalization check; real PDF extraction. Run locally, not CI.

Verification (UC-01/UC-03 acceptance, end-to-end)

1. uv sync --extra dev && uv run pytest — green.
2. uv run unilearn init PDSS → tree exists; rerun no-op.
3. uv run unilearn index PDSS <real lecture PDF> → progress, indexed; unilearn list PDSS shows pages/chunks; spot-check a chunk's page + heading path against the PDF (sqlite3 store.db).
4. Scanned PDF → extraction_failed, "scanned PDF — not supported"; run continues.
5. Re-run on unchanged folder → all skips; Ctrl-C mid-index → re-run resumes cleanly.
6. uv run unilearn remove PDSS <file> -y → list empty; no rows in any channel; manifest counts synced.
7. uv run ruff check . && uv run ruff format --check . (CI parity). Changes stay in the working tree — Paul commits.
