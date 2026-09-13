# Tech Stack

Expands [`groundly-spec.md`](../groundly-spec.md) §4. Every row is a **decision** with its decisive reason, not a menu.

| Layer | Choice | Decisive reason | Documented alternative |
|---|---|---|---|
| Language / distribution | Python ≥3.11, installed via **uv** (`uv tool install groundly`) | Docling + graphrag coexist only in Python; uv makes `curl \| bash` honest | — |
| CLI | **typer + rich** | Batch verbs with progress output; the host agent is the interactive surface (no TUI) | — |
| MCP surface | **FastMCP** | stdio + streamable HTTP from one tool set; tools and resources | — |
| Storage | **SQLite (WAL) + sqlite-vec + FTS5**, files on disk | Zero services; export = zip; exact KNN at 5k–50k chunks/subject | LanceDB/IVF if a corpus ever outgrows brute force |
| Document extraction | **Docling with bundled RapidOCR** | Local/offline, zero-key; layout/table/reading order plus OCR for scanned PDFs and raster images; HybridChunker for structure-aware chunks | none — documents with no readable text fail cleanly |
| Embeddings | **`bge-m3` local** (FlagEmbedding), pinned incl. **hf_revision**; dense + learned sparse from one forward pass | RO/EN cross-lingual; the pin is the interchange compatibility contract | ColBERT vectors rejected (~100× storage) |
| Rerank | **`bge-reranker-v2-m3`**, default ON | Quality-first; same model family as the embedder | `--no-rerank` for weak hardware |
| Retrieval | **A plain `VectorRetriever` returning `Hit`** | One arm left, so an orchestration framework wrapped a SQLite row (decision 36) | — |
| Graph engine | **Microsoft `graphrag`** (batch, per subject, parquet on disk) — **build only** | Leiden communities + summaries are the topic map's input; the retrieval arms were measured and retired (decision 35) | vector-only subjects are first-class |
| Answer generation | **The host agent** | It is already an LLM; enforced server-side answering was measured and removed (decision 33) | — |
| Flashcards | **genanki → .apkg** | Anki owns daily review; Groundly owns verified generation | — |
| Observability | **Local `traces` table** in progress.db | Offline, private (LangSmith dropped — cloud tracing inside a local-first tool) | — |
| Graph visualization | `groundly export-graph` → one self-contained HTML file; **vis-network 9.1.6 vendored** | No CDN is permitted (privacy rule), and the page must open offline years later | 9.1.6, SHA-384 pinned in `assets/VENDORED.md` |
| HTTP transport | `groundly serve`, loopback-only | fastmcp's own Streamable HTTP app (uvicorn arrives with `mcp`) | — |

## LLM provider boundary

**One OpenAI-compatible boundary, one call class, bring-your-own provider.** The only operation that calls a provider is `groundly index --graph`.

```toml
[providers.extraction]  # graphrag entity extraction + community reports
base_url = "..."        # https://api.openai.com/v1 | http://localhost:1234/v1 | ...
model    = "..."
api_key  = "..."
```

The same file carries operational settings (`[ingestion]`/`[llm]`/`[retrieval]`/`[graph]`) whose defaults equal the shipped constants. Config **parsing** lives in `groundly/core/config.py` (a foundation); `groundly/llm/` still constructs every client.

Rules that make this real:

1. **No provider SDK usage outside `groundly/llm/`.** graphrag accepts OpenAI-compatible configs — only that form is used. `groundly/llm/chat.py` talks to providers via `litellm.completion()` (pinned `litellm==1.86.2`, decision 20).
2. **Every call passes through `llm/` and records tokens + cost into traces.**
3. **No subscription-OAuth piggybacking** — ToS-fragile.
4. **Zero-key operation is first-class:** indexing, search, verification, Anki export and import/export all work with no provider configured.
5. **The extraction model must accept JSON-schema structured output** — community reports request it, and the build's preflight probe refuses a model that cannot, naming it. Configs written before 2026-09 may carry retired sections (`chat`, `generation`, `router`, `judge`) and `graph.report_call_class`; they are ignored, never rejected.

## Version pinning policy

Pin **exact** versions of `graphrag`, `docling`, `sentence-transformers`, `FlagEmbedding`, `rapidocr` and `litellm` (and bge-m3's hf_revision); record them in the thesis and in every export manifest. Pinned 2026-07-16: `docling==2.113.0`, `graphrag==3.1.0`, `sentence-transformers==5.6.0`, `FlagEmbedding==1.3.5`, `rapidocr==3.9.1`, `litellm==1.86.2`, bge-m3 hf_revision `5617a9f61b028005a4858fdac845db406aefb181`. The graphrag and embedding pins are **interchange compatibility contracts**; upgrades are deliberate events, guarded by `.claude/hooks/guard-pins.sh`.
