# Tech Stack

Expands [`groundly-spec.md`](../groundly-spec.md) §4. Every row is a **decision** with its decisive reason, not a menu.

| Layer | Choice | Decisive reason | Documented alternative |
|---|---|---|---|
| Language / distribution | Python ≥3.11, installed via **uv** (`uv tool install groundly`) | Docling is Python-only; uv provisions Python itself | — |
| CLI | **typer + rich** | Batch verbs with progress output; the host agent is the interactive surface (no TUI) | — |
| MCP surface | **FastMCP** | stdio + streamable HTTP from one tool set; tools and resources | — |
| Storage | **SQLite (WAL) + sqlite-vec + FTS5**, files on disk | Zero services; export = zip; exact KNN at 5k–50k chunks/subject | LanceDB/IVF if a corpus ever outgrows brute force |
| Document extraction | **Docling with bundled RapidOCR**, one warm worker process per `index` run; PDFs and standalone images read page by page and chunked per page, the timeout per page (decision 49) | Local/offline, zero-key; layout/table/reading order plus OCR for scanned PDFs and raster images; HybridChunker for structure-aware chunks; the worker keeps per-file crash and timeout isolation (decision 48) | none — documents with no readable text fail cleanly |
| Embeddings | **`bge-m3` local**, pinned incl. **hf_revision**; dense + learned sparse from one forward pass; engine per platform (below) | RO/EN cross-lingual; the pin is the interchange compatibility contract | ColBERT vectors rejected (~100× storage) |
| Rerank | **`bge-reranker-v2-m3`**, default ON | Quality-first; same model family as the embedder | `--no-rerank` for weak hardware |
| Retrieval | **A plain `VectorRetriever` returning `Hit`** | One arm left, so an orchestration framework wrapped a SQLite row (decision 36) | — |
| Answer generation | **The host agent** | It is already an LLM; enforced server-side answering was measured and removed (decision 33) | — |
| HTTP transport | `groundly mcp --http` (loopback-only) or `groundly start` (`/mcp` stays loopback-only even when opt-in Tailscale SPA pairing is enabled) | fastmcp's own Streamable HTTP app; `_SocketPerimeterMiddleware` returns `404` for `/mcp` on Tailscale sockets (decision 52) | — |
| Topic-tree page | **FastAPI + uvicorn** (`groundly serve` / `groundly start`, loopback-only by default with opt-in Tailscale `100.64.0.0/10` dual-socket pairing via **`segno`** + `~/.groundly/remote.db`) + **Svelte 5 / TypeScript / Tailwind v4** SPA (`openapi-typescript` + `openapi-fetch`, `@tanstack/svelte-query` v6, `sv-router`, `shadcn-svelte`, `thinking-orbs/engine`, `@paper-design/shaders`) compiled via **Vite+** (`vp`) to committed `groundly/web/static/page.{js,css}` | Strict Pydantic v2 contracts (`docs_url=None`, offline `web/openapi.json` export), typed REST client, reactive query cache invalidated by async pub-sub SSE (`/api/runs/stream`), code-based pure-Svelte routing, fine-grained reactive UI without VDOM overhead, and pure-Python `segno` QR matrix/terminal rendering + socket-scoped per-device `sha256` cookie sessions; Vite+ compiles deterministic single-file static assets (`web/` excluded from wheel, zero Node at install or `pytest` time, zero external fonts). FastMCP stays the sole tool-surface framework (decisions 50, 51, 52) | SvelteKit (`adapter-static` adds hashed multi-file churn + inline bootstrap script clashing with `script-src 'self'` CSP, and server features cannot run without Node) |

## Engine per platform

The embedder and the reranker run on one of two engines, chosen at run time by whether `mlx` imports (decision 48):

| Platform | Engine | Precision |
|---|---|---|
| macOS 14+ on arm64 | **MLX**: one hand-written XLM-R forward (`groundly/llm/mlx_xlmr.py`) shared by both models, inputs packed up to 1,024 tokens per pass, truncated as FlagEmbedding truncates | f16 |
| everywhere else, or wherever `mlx` fails to import | **FlagEmbedding on torch** (MPS, CUDA or CPU), one input per pass | bge-m3 fp16 on a GPU, the reranker fp32 |

The engine is not part of the embedding pin. An engine must stay within the tolerance MLX measured against torch on all 1,197 apd chunks and the 50 workload search pools: dense cosine ≥ 0.9997; sparse top-10 identical up to f16 near-ties (1,196 of 1,197 within 1e-3 of the 10th weight, the last at 1.1e-3); rerank top-5 identical as a set, with adjacent near-ties allowed to swap (48 of 50 in identical order); reranker scores within 0.026 on search pools and 0.034 on full 512-token inputs. bge-m3's pinned weights ship as torch pickles; MLX reads them after a one-time conversion to f16 safetensors in a child process, so the serving process never imports torch. torch stays installed: docling's layout model and TableFormer need it in the extraction worker.

## No LLM client

**Groundly constructs no LLM client and holds no key** (decision 37). The student's AI subscription (Claude Code, Claude Desktop, Codex) is the only model in the loop, and it reaches Groundly through MCP. `groundly/llm/` holds only the local bge-m3 embedder and reranker. The page may start the student's own Claude Code for two chores (decision 46); that is the student's binary on the student's login, not a client of Groundly's, and Groundly passes it no key and no model.

1. **Index, search, verification and import/export need nothing but the local models.**
2. **No subscription-OAuth piggybacking** — ToS-fragile, and unnecessary: the host already holds the session.
3. **`config.toml` carries operational settings only** (`[ingestion]`, `[retrieval]`, and `[web]`). Files written before decision 37 may still carry `[providers.*]`, `[llm]` and `[graph]` sections; they are ignored, never rejected, and the next `config set` drops them.

## Version pinning policy

Pin **exact** versions of `docling`, `sentence-transformers`, `FlagEmbedding`, `rapidocr`, `mlx` and `pypdfium2` (and bge-m3's hf_revision); record them in the thesis and in every export manifest. Pinned 2026-10-31 (decision 39): `docling==2.115.0`, `sentence-transformers==6.2.0`, `FlagEmbedding==1.4.2`, `rapidocr==3.10.0`, with `transformers` 5.x; `mlx==0.32.4` on macOS 14+ arm64 only (decision 48, platform marker `platform_release >= '23'`); `pypdfium2==5.14.0` (decision 49, the version docling 2.115 resolves; shapes the text of routed pages); bge-m3 hf_revision `5617a9f61b028005a4858fdac845db406aefb181` (unchanged since 2026-07-16). **docling stays below 2.116**: its layout-driven OCR reads each layout region separately and reorders the words of a line ("Mutual Exclusion Principle" → "Exclusion Principle Mutual"); swapping the layout model or enabling heading hierarchy does not fix it, and `OcrMode.FULL_PAGE` does at the cost of OCRing digital PDFs over their text layer. Revisit when upstream fixes it; `tests/ingestion/test_extract_worker.py::test_title_only_image_salvages_heading_text` (`-m slow`) is the check. A pin upgrade runs the slow suite. The embedding pin is the **interchange compatibility contract**; upgrades are deliberate events, guarded by `.claude/hooks/guard-pins.sh` for every pin above except `pypdfium2`, which is pinned exact but not yet in the hook (an open call).
