# Architecture invariants

Source of truth: `docs/architecture/overview.md`. Violating these is a bug even if tests pass.

## Module boundaries (`groundly/`)

Layers: clients (`cli/`, `mcp/`) → services (`agents/`, `retrieval/`, `ingestion/`) → foundations (`llm/`, `core/`).

- Dependencies point one way; **nothing imports the client layer** (`tests/test_layering.py`).
- `agents` may call `retrieval` (the verifier re-retrieves); `retrieval` never calls `agents`.
- `ingestion` writes the stores; it never serves queries.

## LLM provider boundary (hard rule)

- LLM clients are constructed **only** in `groundly/llm/` — OpenAI-compatible `base_url` + model + key from `~/.groundly/config.toml`. Never hardcode a provider; cloud keys and LM Studio are the same code path. litellm is the sanctioned client library inside `llm/`, constructed nowhere else.
- **One call class, `extraction`**, used by `groundly index --graph` and nothing else. Every call passes through `llm/` and records tokens + cost into the traces table.
- **Zero-key operation is first-class**: index, `search`, `get_page`, `submit_cards`, `export_deck` and import/export must never require a provider.
- **Groundly runs no answer-generation loop.** The host composes answers from `search` results (decision 33); adding a server-side generation path is a decision change, not a feature.
- Embeddings: `bge-m3`, pinned incl. hf_revision — the pin is the interchange compatibility contract. Changing it = full re-index migration + manifest bump, never a tweak.

## Storage & concurrency

- SQLite **WAL + busy_timeout on every connection** (one-shot CLI and host-spawned MCP share store.db); schema via `PRAGMA user_version`, no migration framework.
- **Lazy model loading** — never load bge-m3/reranker at MCP spawn; load on first use.
- `groundly serve` binds **127.0.0.1 only**.
- `progress.db` is written by the graph build alone; `search` is read-only.

## Frameworks

Exactly two, one owner each: MS graphrag (the graph build) and FastMCP (the tool surface). litellm is a provider client used inside `llm/`, not a third framework. Exact pins for graphrag/docling/sentence-transformers/FlagEmbedding/rapidocr/litellm are guarded by `.claude/hooks/guard-pins.sh`; upgrades are deliberate events recorded in docs + manifest.
