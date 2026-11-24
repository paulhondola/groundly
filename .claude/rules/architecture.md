# Architecture invariants

Source of truth: `docs/architecture/overview.md`. Violating these is a bug even if tests pass.

## Module boundaries (`groundly/`)

Layers: clients (`cli/`, `mcp/`, `web/`) → services (`agents/`, `retrieval/`, `ingestion/`) → foundations (`llm/`, `core/`).

- Dependencies point one way; **nothing imports the client layer** except `groundly/__main__.py` (`python -m groundly`, a page run's stdio Groundly), its own top layer — enforced by the import-linter contract in `pyproject.toml` (`uv run lint-imports`); a new subpackage must be placed in a layer there.
- `agents` may call `retrieval` (the verifier re-retrieves); `retrieval` never calls `agents`.
- Three writers touch `store.db`: `ingestion` (materials, chunks, vectors), the topic gate in `agents/topics.py` (the tree, reached from `submit_tree`) and the question gate in `agents/verifier.py` (the question bank, written through the gate from `submit_questions`; `remove_questions` takes questions out). `ingestion` never serves queries. `web/` writes no rows to `store.db`; its one write is a submitted test attempt to `progress.db`, made through `agents/quiz.py` (decision 44). A page run it starts (decision 46) writes only through the MCP gates, in its own process. Any opener of a version-1 `store.db` or `progress.db`, the page and read-only tools included, adds that file's empty tables once (the in-place upgrade below).

## No LLM, no key (hard rule)

- **Groundly constructs no LLM client and holds no key** (decision 37). `groundly/llm/` holds only the local bge-m3 embedder and reranker. Adding a provider, an API-key setting or any network model call is a decision change, not a feature.
- **Zero-key is total**: every verb and tool works with nothing configured; `config.toml` has no providers.
- **Groundly runs no answer-generation loop.** The host composes answers from `search` results (decision 33); adding a server-side generation path is a decision change, not a feature.
- **The page may start the student's own host, isolated** (decisions 46, 50): `web/runs.py` spawns `claude -p` with `--include-partial-messages` for the write and grade flows only, from an argv list with no shell, with `--strict-mcp-config` (Groundly only), `--setting-sources ""`, `--tools ""`, the flow's `--allowedTools`, `--permission-mode dontAsk` and the flow's packaged skill (`groundly-questions` or `groundly-grade`, decision 47); the init event is checked fail-closed (rejecting pre-init `stream_event` too), and `GET /api/runs/stream` streams coalesced `phase`/`delta`/`step`/`done` SSE frames over async per-subscriber queues while keeping `Steps` strictly tool-derived. It passes no key and no `--model`, and the prompt carries the subject name and a topic id only, never layer-3 text. That is the student's binary on the student's login, not a Groundly client; widening the tools, adding a flow or passing course text in is a decision change.
- Embeddings: `bge-m3`, pinned incl. hf_revision — the pin is the interchange compatibility contract. Changing it = full re-index migration + manifest bump, never a tweak.
- The engine is not part of the pin (decision 48): MLX on Apple Silicon (macOS 14+), FlagEmbedding/torch elsewhere. Any engine must stay within the recorded tolerance: dense cosine ≥ 0.9997; sparse top-10 identical up to f16 near-ties (worst 1.1e-3); rerank top-5 identical as a set, adjacent near-ties may swap; max score diff 0.026. torch stays installed for docling's extraction worker.
- **On Apple Silicon, torch, FlagEmbedding and sentence-transformers are never imported by long-running processes** (`start`, `mcp`, `serve`), nor by the indexing parent; bge-m3's weights are converted to safetensors in a child process.
- **One warm extraction worker per indexing run, isolation per file** (decision 48): a crash (`parser failed: …`), a timeout (`extraction timed out after Ns`), an oversized file (`file too large — …`) or a no-text file (`no readable text — OCR found nothing to extract`) each ends that file `extraction_failed` with that message, and the next file indexes (a fresh worker after a crash or timeout). The worker never outlives the run.

## Storage & concurrency

- SQLite **WAL + busy_timeout on every connection** (one-shot CLI and host-spawned MCP share store.db); schema via `PRAGMA user_version`, no migration framework. The one in-place upgrade (version 1 to 2, decision 44) only adds empty tables.
- **Lazy model loading** — never load bge-m3/reranker at MCP spawn; load on first use. Each is one shared instance per process, dropped (and the device cache emptied) after `IDLE_UNLOAD_SECONDS` without a use, so a long-lived `start` or host-spawned process gives the memory back.
- `groundly mcp --http`, `groundly serve` and `groundly start` (page + `/mcp` in one process) bind **127.0.0.1 only**; `serve` and `start` accept exactly the Host names `127.0.0.1`, `localhost`, `groundly.localhost` (`mcp --http` keeps FastMCP's defaults); the page checks the Host header, loads no model and writes no rows to `store.db`. Its write routes (a test attempt; starting and stopping a run) also require the page's own `Origin` and a JSON body.
- `progress.db` is the student's private study state (test attempts and answers); `search` and the topic tree write nothing to it.

## Frameworks

Exactly one for the tool surface: FastMCP. FastAPI (`docs_url=None`, strict Pydantic v2 after `_require_page_json`), Starlette, and uvicorn serve the topic-tree page and SSE stream (`groundly/web/static/page.{js,css}` compiled from `web/` via Vite+ / Svelte 5, zero Node at install/runtime — decision 50); they are not a second tool-surface framework. Exact pins for docling/sentence-transformers/FlagEmbedding/rapidocr/mlx are guarded by `.claude/hooks/guard-pins.sh`; upgrades are deliberate events recorded in docs + manifest.
