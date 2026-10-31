# Tech Stack

Expands [`groundly-spec.md`](../groundly-spec.md) §4. Every row is a **decision** with its decisive reason, not a menu.

| Layer | Choice | Decisive reason | Documented alternative |
|---|---|---|---|
| Language / distribution | Python ≥3.11, installed via **uv** (`uv tool install groundly`) | Docling is Python-only; uv provisions Python itself | — |
| CLI | **typer + rich** | Batch verbs with progress output; the host agent is the interactive surface (no TUI) | — |
| MCP surface | **FastMCP** | stdio + streamable HTTP from one tool set; tools and resources | — |
| Storage | **SQLite (WAL) + sqlite-vec + FTS5**, files on disk | Zero services; export = zip; exact KNN at 5k–50k chunks/subject | LanceDB/IVF if a corpus ever outgrows brute force |
| Document extraction | **Docling with bundled RapidOCR** | Local/offline, zero-key; layout/table/reading order plus OCR for scanned PDFs and raster images; HybridChunker for structure-aware chunks | none — documents with no readable text fail cleanly |
| Embeddings | **`bge-m3` local** (FlagEmbedding), pinned incl. **hf_revision**; dense + learned sparse from one forward pass | RO/EN cross-lingual; the pin is the interchange compatibility contract | ColBERT vectors rejected (~100× storage) |
| Rerank | **`bge-reranker-v2-m3`**, default ON | Quality-first; same model family as the embedder | `--no-rerank` for weak hardware |
| Retrieval | **A plain `VectorRetriever` returning `Hit`** | One arm left, so an orchestration framework wrapped a SQLite row (decision 36) | — |
| Answer generation | **The host agent** | It is already an LLM; enforced server-side answering was measured and removed (decision 33) | — |
| HTTP transport | `groundly mcp --http`, loopback-only | fastmcp's own Streamable HTTP app (uvicorn arrives with `mcp`) | — |

## No LLM client

**Groundly constructs no LLM client and holds no key** (decision 37). The student's AI subscription (Claude Code, Claude Desktop, Codex) is the only model in the loop, and it reaches Groundly through MCP. `groundly/llm/` holds only the local bge-m3 embedder and reranker.

1. **Index, search, verification and import/export need nothing but the local models.**
2. **No subscription-OAuth piggybacking** — ToS-fragile, and unnecessary: the host already holds the session.
3. **`config.toml` carries operational settings only** (`[ingestion]`/`[retrieval]`). Files written before decision 37 may still carry `[providers.*]`, `[llm]` and `[graph]` sections; they are ignored, never rejected, and the next `config set` drops them.

## Version pinning policy

Pin **exact** versions of `docling`, `sentence-transformers`, `FlagEmbedding` and `rapidocr` (and bge-m3's hf_revision); record them in the thesis and in every export manifest. Pinned 2026-10-31 (decision 39): `docling==2.115.0`, `sentence-transformers==6.2.0`, `FlagEmbedding==1.4.2`, `rapidocr==3.10.0`, with `transformers` 5.x; bge-m3 hf_revision `5617a9f61b028005a4858fdac845db406aefb181` (unchanged since 2026-07-16). **docling stays below 2.116**: its layout-driven OCR reads each layout region separately and reorders the words of a line ("Mutual Exclusion Principle" → "Exclusion Principle Mutual"); swapping the layout model or enabling heading hierarchy does not fix it, and `OcrMode.FULL_PAGE` does at the cost of OCRing digital PDFs over their text layer. Revisit when upstream fixes it; `tests/ingestion/test_extract_worker.py::test_title_only_image_salvages_heading_text` (`-m slow`) is the check. A pin upgrade runs the slow suite. The embedding pin is the **interchange compatibility contract**; upgrades are deliberate events, guarded by `.claude/hooks/guard-pins.sh`.
