# Architecture invariants

Source of truth: `docs/architecture/overview.md`. Violating these is a bug even if tests pass.

## Module boundaries (`groundly/`)

Layers: clients (`cli/`, `mcp/`) → services (`agents/`, `retrieval/`, `ingestion/`) → foundations (`llm/`, `core/`).

- Dependencies point one way; **nothing imports the client layer** — enforced by the import-linter contract in `pyproject.toml` (`uv run lint-imports`); a new subpackage must be placed in a layer there.
- `agents` may call `retrieval` (the verifier re-retrieves); `retrieval` never calls `agents`.
- `ingestion` writes the stores; it never serves queries.

## No LLM, no key (hard rule)

- **Groundly constructs no LLM client and holds no key** (decision 37). `groundly/llm/` holds only the local bge-m3 embedder and reranker. Adding a provider, an API-key setting or any network model call is a decision change, not a feature.
- **Zero-key is total**: every verb and tool works with nothing configured; `config.toml` has no providers.
- **Groundly runs no answer-generation loop.** The host composes answers from `search` results (decision 33); adding a server-side generation path is a decision change, not a feature.
- Embeddings: `bge-m3`, pinned incl. hf_revision — the pin is the interchange compatibility contract. Changing it = full re-index migration + manifest bump, never a tweak.

## Storage & concurrency

- SQLite **WAL + busy_timeout on every connection** (one-shot CLI and host-spawned MCP share store.db); schema via `PRAGMA user_version`, no migration framework.
- **Lazy model loading** — never load bge-m3/reranker at MCP spawn; load on first use.
- `groundly serve` binds **127.0.0.1 only**.
- `progress.db` is the student's private study state (no tables yet); `search` is read-only.

## Frameworks

Exactly one: FastMCP (the tool surface). Exact pins for docling/sentence-transformers/FlagEmbedding/rapidocr are guarded by `.claude/hooks/guard-pins.sh`; upgrades are deliberate events recorded in docs + manifest.
