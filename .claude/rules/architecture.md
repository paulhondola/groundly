# Architecture invariants

Source of truth: `docs/architecture/overview.md`. Violating these is a bug even if tests pass.

## Module boundaries (`groundly/`)

Layers: clients (`cli/`, `mcp/`, `web/`) → services (`agents/`, `retrieval/`, `ingestion/`) → foundations (`llm/`, `core/`).

- Dependencies point one way; **nothing imports the client layer**.
- `agents` may call `retrieval` (as a tool) and the subprocess runner; `retrieval` never calls `agents`.
- `ingestion` writes the stores; it never serves queries.

## LLM provider boundary (hard rule)

- LLM clients are constructed **only** in `groundly/llm/` — OpenAI-compatible `base_url` + model + key, **per call class** (`chat`, `generation`, `extraction`, `router`) from `~/.groundly/config.toml`. Never hardcode a provider; cloud keys and LM Studio/Ollama are the same code path.
- Every LLM call passes through `llm/` and records tokens + cost into the traces table.
- **Zero-key operation is first-class**: index, `search`, thin `submit_*` generation must never require a provider.
- Embeddings: `bge-m3`, pinned incl. hf_revision — the pin is the interchange compatibility contract. Changing it = full re-index migration + manifest bump, never a tweak.

## Storage & concurrency

- SQLite **WAL + busy_timeout on every connection** (one-shot CLI and host-spawned MCP share store.db); schema via `PRAGMA user_version`, no migration framework.
- **Lazy model loading** — never load bge-m3/reranker at MCP spawn; load on first use.
- `groundly serve` binds **127.0.0.1 only**.
- Generation jobs are serialized when the provider is a local runtime; never block a request handler on an agent loop.

## Frameworks

Exactly three, one owner each: LlamaIndex (retrieval interface), MS graphrag (graph backend), FastMCP (tool surface). Agent loops are plain bounded async functions — no LangGraph. Exact pins for graphrag/llama-index/docling/sentence-transformers/FlagEmbedding set at P1; upgrades are deliberate events recorded in docs + manifest.
