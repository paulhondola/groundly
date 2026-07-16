# Tech Stack

Expands [`groundly-spec.md`](../groundly-spec.md) §4. Every row is a **decision** with its decisive reason, not a menu. Alternatives appear only as live migration paths.

| Layer | Choice | Decisive reason | Documented alternative |
|---|---|---|---|
| Language / distribution | Python ≥3.11, installed via **uv** (`uv tool install groundly`) | Docling + LlamaIndex + graphrag + RAGAS coexist only in Python; uv makes `curl \| bash` honest | — |
| CLI | **typer + rich** | Batch verbs with progress output; the host agent is the interactive surface (no TUI) | — |
| MCP surface | **FastMCP** | stdio + streamable HTTP from one tool set; tools, resources, prompts | — |
| Storage | **SQLite (WAL) + sqlite-vec + FTS5**, files on disk | Zero services; export = zip; exact KNN at 5k–50k chunks/subject | LanceDB/IVF if a corpus ever outgrows brute force |
| Document extraction | **Docling, no OCR extras** — digital documents only | Professor decision (pivot #3); layout/table/reading-order on digital PDFs; HybridChunker for structure-aware chunks | none — scanned files fail cleanly |
| Embeddings | **`bge-m3` local** (FlagEmbedding — sentence-transformers' SparseEncoder does not expose bge-m3's learned-sparse head), pinned incl. **hf_revision**; dense + learned sparse from one forward pass | Quality over performance (Paul); RO/EN cross-lingual; the pin is the interchange compatibility contract. Changing it = full re-index migration, never a tweak | ColBERT vectors rejected (~100× storage) |
| Rerank | **`bge-reranker-v2-m3`**, default ON | Quality-first; same model family as the embedder | `--no-rerank` for weak hardware |
| Graph engine | **Microsoft `graphrag`** (batch, per subject, parquet on disk) | Canonical GraphRAG — Leiden + community summaries + local/global search | timeboxed; vector-only operation is first-class |
| Retrieval orchestration | **LlamaIndex** | One `Retriever` interface across all four evaluation arms — the comparison's fairness depends on it | — |
| Agent loops | **Plain async functions** | Post-pivot roster = one pipeline + two bounded loops; a graph framework wraps nothing (LangGraph dropped) | — |
| Flashcards | **genanki → .apkg** | Anki owns daily review; Groundly owns verified generation | — |
| Observability | **Local `traces` table** in progress.db | Offline, private, shippable eval artifact (LangSmith dropped — cloud tracing inside a local-first tool) | — |
| Dashboard | One **static HTML page** (theme as CSS variables in `groundly/web/static/theme.css`) | A React toolchain for one page was unjustifiable (final review) | — |
| Web serving | FastAPI + uvicorn, only inside `groundly serve` (loopback-only) | FastMCP mounts into it; dashboard rides along | — |

## LLM provider boundary

**Decision: one OpenAI-compatible boundary, per call class, bring-your-own provider.**

All LLM clients are constructed in **one module** (`groundly/llm/`) from `~/.groundly/config.toml`:

```toml
[providers.chat]        # ask pipeline generation
base_url = "..."        # https://api.openai.com/v1 | http://localhost:1234/v1 | ...
model    = "..."
api_key  = "..."

[providers.generation]  # exam/deck generation (thick path)
[providers.extraction]  # graphrag entity extraction — mid-tier cloud model rule
[providers.router]      # cheap classifier
```

Rules that make this real:

1. **No provider SDK usage outside `groundly/llm/`.** LlamaIndex and graphrag accept OpenAI-compatible configs — only that form is used.
2. **Per call class**, so "cheap router, strong verifier" is config, not refactoring. Local runtimes (LM Studio, Ollama) and cloud keys are the same code path — different `base_url`.
3. **Every call passes through `llm/` and records tokens + cost into traces.** Visibility, not budget enforcement — it's the student's own key.
4. **No subscription-OAuth piggybacking** (Claude Pro token reuse etc.) — ToS-fragile; opencode's history proves it.
5. **Zero-key operation is first-class:** indexing, vector retrieval, `search`, and the thin `submit_*` generation path all work with no provider configured. Only `ask`, thick generation, and graph builds need a key.
6. **Evaluation runs use one recorded provider config** — results from ad-hoc local models would be invalid; the config is part of the experimental record.

## Version pinning policy

Pin **exact** versions of `graphrag`, `llama-index`, `docling`, `sentence-transformers`, and `FlagEmbedding` (and bge-m3's hf_revision) at P1 start; record them in the thesis and in every export manifest. Pinned 2026-07-16: `docling==2.113.0`, `llama-index==0.14.23`, `graphrag==3.1.0`, `sentence-transformers==5.6.0`, `FlagEmbedding==1.3.5`, bge-m3 hf_revision `5617a9f61b028005a4858fdac845db406aefb181`. The graphrag and embedding pins are **interchange compatibility contracts**, not just hygiene — an import built with different pins is a different experimental condition. Upgrades are deliberate events.
