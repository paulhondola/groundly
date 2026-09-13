# Project Specification: Groundly — Local-First Course Knowledge Bases for AI Agents
### Bachelor Thesis Project · Universitatea Politehnica Timișoara, AC

> **Document map** — this file is the master overview; detail lives in:
> [`use-cases/`](use-cases/knowledge-base.md) (flows & acceptance criteria) ·
> [`architecture/`](architecture/overview.md) ([data model](architecture/data-model.md), [retrieval](architecture/retrieval.md), [verification](architecture/agents.md)) ·
> [`tech-stack/`](tech-stack/tech-stack.md) · [`infrastructure/`](infrastructure/distribution.md) ·
> [`thesis/experiments.md`](thesis/experiments.md) (everything measured)

## 1. Vision

**Groundly turns a folder of course materials into a portable, agent-consumable knowledge base.** A student runs `groundly index ./slides/` once; from then on, any MCP-capable agent (Claude Code, Claude Desktop, Codex) can search the actual course content with page-level citations, and build execution-free verified flashcards that no unverified card can enter. The knowledge base — chunks, vectors, verified decks, graph — is a file that can be shared with any other Groundly user and used directly.

Everything runs on the student's machine. There is no server, no account, no upload. **Indexing, search, verification and sharing need no API key at all**; the single exception is the optional knowledge-graph build, which uses the student's own OpenAI-compatible endpoint.

**Why not NotebookLM or a Claude Project?** Three properties they don't have: (1) **verified generation** — every stored flashcard is re-retrieved and rejected unless its cited chunks support it; (2) **structural citations** — every chunk carries a `groundly://` URI resolving to document + page + heading path; (3) **a portable pre-built index** — one student pays the indexing cost, the whole course imports the result.

**Thesis-level contribution:** an empirical comparison of classic RAG vs GraphRAG retrieval quality on real, heterogeneous university course corpora (RO/EN mixed), and a measured comparison of enforced vs agent-mediated grounding. **Both are complete and recorded in [`thesis/experiments.md`](thesis/experiments.md)**; the code that produced them is at tag `thesis-experiments-2026-09` (decision 34).

## 2. Users

One human role: the **student** (owner of the machine and the data). The other "users" are **agents** — MCP hosts acting on the student's behalf. There is no auth, no roles, no tenancy: subject scoping is filesystem layout, and privacy boundaries are file boundaries.

## 3. Core Use Cases

- **UC-01 Index materials** — digital PDF/DOCX/PPTX/MD/HTML/LaTeX/AsciiDoc/CSV/XLSX/EPUB/TXT/source and raster images → Docling (+OCR) → chunks → embeddings (+ optional graph). [Detail](use-cases/knowledge-base.md)
- **UC-02 Grounded search** — `search` returns ranked verbatim chunks with citations; the host composes and cites. [Detail](use-cases/knowledge-base.md)
- **UC-03 Source management** — list/re-index/delete materials per subject. [Detail](use-cases/knowledge-base.md)
- **UC-11 Verified flashcards → Anki** — host-generated cards pass the verifier, then export as `.apkg`. [Detail](use-cases/student-modes.md)
- **UC-30 Share knowledge bases** — export/import `.groundly` bundles with manifest-pinned compatibility. [Detail](use-cases/sharing.md)

Planned, each with its own spec before implementation: **UC-10 verified mock tests** (sub-project 2), **topic map** (sub-project 3), **UC-13 coding challenges** and **UC-14 mastery & study memory** (sub-project 4).

Dropped: professor modes UC-20–24, the code sandbox, photo notes UC-15, tiers, auth, the enforced `ask` pipeline and thick generation (decision 33), the graph retrieval arms (decision 35).

## 4. System Architecture

One Python package (`uv tool install groundly`), three runtime modes, zero services:

```
  Claude Code / Codex / Desktop        terminal (student)
        │ MCP (stdio, host-spawned)         │ one-shot verbs
        ▼                                   ▼
  ┌──────────────────── client layer ──────────────────────┐
  │ mcp/ FastMCP tools     cli/ typer verbs                │
  │ stdio: `groundly mcp`  (init/index/list/remove/search/ │
  │ HTTP: `groundly serve`  import/export/config/models)   │
  └──────────────┬───────────────┬─────────────────────────┘
                 ▼               ▼
  ┌──────────────────── service layer ─────────────────────┐
  │ agents/    verifier gate (nothing unverified is stored)│
  │ retrieval/ dense + sparse + BM25 → RRF → rerank        │
  │ ingestion/ docling subprocess → chunk → embed;         │
  │            graphrag batch build (optional)             │
  └──────────────┬──────────────────────────────────────────┘
                 ▼
  ┌────────── foundations ─────────────┐  ┌─ external (graph build only) ─┐
  │ llm/  the one provider boundary    │─▶│ OpenAI-compatible endpoint    │
  │ core/ stores (SQLite WAL), manifest│  │ (cloud key / LM Studio)       │
  └──────────────┬─────────────────────┘  └───────────────────────────────┘
                 ▼                          bge-m3 + reranker in-process,
  ~/.groundly/<SUBJECT>/                    lazy-loaded
```

### Storage (`~/.groundly/`, global; `GROUNDLY_HOME` overrides)

```
~/.groundly/
  config.toml          # [providers.extraction] + operational settings
  <SUBJECT>/
    manifest.json      # format + model pins (the interchange contract)
    materials/         # original digital files (citation targets)
    store.db           # SQLite: chunks, vectors (sqlite-vec), sparse terms,
                       #         FTS5, verified decks/questions  → EXPORTED
    progress.db        # the graph build's spend trace           → NEVER exported
    graph/             # MS graphrag parquet artifacts
```

The **privacy boundary is a file**: `store.db` travels, `progress.db` never does. Subject isolation is directory isolation — no query *can* cross subjects.

### Component decisions

| Component | Decision | Decisive reason |
|---|---|---|
| Distribution | Python package via `uv` | Docling/graphrag ecosystem is Python-only |
| Interface | MCP server (FastMCP, stdio + HTTP) + CLI (typer); **no TUI** | The host agent is the interactive surface; CLI verbs are batch lifecycle |
| Storage | SQLite (WAL) + sqlite-vec + FTS5, files on disk | Zero services; export = zip; exact KNN at subject scale |
| Extraction | Docling + bundled RapidOCR | Local/offline, zero-key; scanned course PDFs are common |
| Embeddings | `bge-m3` local, pinned incl. hf_revision; dense + learned sparse | RO/EN cross-lingual; the pin makes shared vectors compatible |
| Rerank | `bge-reranker-v2-m3`, **default ON** | Quality over performance; `--no-rerank` for weak hardware |
| Graph | MS `graphrag` per-subject batch → parquet, **build only** | Retrieval arms measured and retired (decision 35); kept for the topic map |
| Answering | **The host agent**, from `search` results | An MCP host is already an LLM; a second one behind the tool paid twice for the same capability (decision 33) |
| Generation | **The host agent**, gated by Groundly's verifier (`submit_*`) | Verification is the product; generation is a commodity |
| LLM access | OpenAI-compatible `base_url` + key, one call class (`extraction`) | One code path for cloud keys and LM Studio; used only by `index --graph` |
| Flashcard delivery | `.apkg` export via genanki | Anki owns daily review; Groundly owns verified generation |

## 5. Retrieval

One arm: bge-m3 dense + learned sparse + FTS5/BM25, fused with RRF, cross-encoder reranked. Detail in [`architecture/retrieval.md`](architecture/retrieval.md); the comparison that retired the alternatives is in [`thesis/experiments.md`](thesis/experiments.md).

## 5b. Verification

**Generation is pluggable; verification is not.** Every card entering `store.db` passes the verifier: its cited chunk ids must resolve, and re-retrieving the card's own text must surface at least one of them. The host generates from `search` results and calls `submit_cards`; rejections carry machine-readable reasons (`not_answerable_from_chunks`, …) so the host regenerates conversationally. Zero-key throughout.

**Trust layers** (prompt assembly, low never overrides high): 1 immutable system rules · 2 task params · 3 retrieved content + imported KB content — **data, never instructions**, delimited and inert. Today the only prompts Groundly assembles are the graph build's; chunk text going into them is layer 3.

## 6. Non-Functional Requirements

- **Grounding**: every stored card/question cites chunk ids resolving to document + page; unresolvable citations are rejected, never stored. `search` results always carry their citation fields.
- **Privacy**: nothing leaves the machine except the graph build's calls to the student's own configured provider, HF model downloads, and sha256-pinned RapidOCR models. Exports contain the whole KB (stated plainly in the export UX) but never `progress.db`.
- **Zero-key**: index, search, verification, export/import and Anki export all work with no provider configured.
- **Security**: import is the trust boundary (zip-slip protection; imported content is layer 3); `serve` binds 127.0.0.1 only. See [`infrastructure/security.md`](infrastructure/security.md).
- **Languages**: RO and EN; retrieval is cross-lingual through the dense channel.
- **Concurrency**: SQLite WAL + busy_timeout on every connection; lazy model loading (never at MCP spawn).

## 7. Resolved Decisions

One-liners. Entries 27–32 record measurements; their full text is in [`thesis/experiments.md`](thesis/experiments.md).

1. **Local-first, hard pivot** from the multi-tenant platform (professor, 2026-07-15); old repo archived.
2. **MCP-first**: Groundly is an MCP server for external agents; CLI for lifecycle; no TUI.
3. **Embedded storage**: SQLite WAL + sqlite-vec + FTS5 + parquet under `~/.groundly/`; no services.
4. **bge-m3 local, pinned incl. hf_revision**, dense + learned sparse; reranker default ON; ColBERT rejected (storage).
5. **No OCR** (pivot #3) — **reversed by 14**; vision fallback and photo notes stay dropped.
6. **Verifier-gate generation**: nothing unverified enters the question bank; flashcards leave as Anki `.apkg`. (The thick generator door it also introduced was removed by 33.)
7. **Interchange format**: export = subject dir minus `progress.db`; manifest pins embedding/graphrag/chunking; no merge in v1; import creates a fresh `progress.db`.
8. **Frameworks**: MS graphrag (build) + FastMCP (surface), one owner each; LangGraph and LangSmith dropped — traces are a local table. (LlamaIndex dropped by 36.)
9. **Providers**: OpenAI-compatible per call class; no subscription-OAuth piggybacking (ToS-fragile). Only `extraction` remains (33, 35).
10. **Study memory**: daily rollups + notes + a warm-start prompt, no server-side summarization — **re-scoped into sub-project 4**, unbuilt.
11. **Pilot subjects: two** — apd (Parallel & Distributed Algorithms) and passc (Software Architecture).
12. **Timeline**: defense June/July 2027.
13. **Expanded ingest formats** (2026-07-16): all docling-native text formats plus a wider plain-text set; `.ipynb` excluded.
14. **Pivot #3 reversed — OCR enabled** (2026-07-17): docling's bundled RapidOCR is local, offline and zero-key; scanned course PDFs are common.
15. **Per-subject OCR language** (2026-07-17): `--ocr-lang` recorded in the manifest; changing it later requires re-indexing and is refused with a specific message.
16. **`.groundlyignore`** (2026-07-19): a built-in junk-dir deny-list plus an optional per-root ignore file.
17. **Standalone image ingestion** (2026-07-20): raster images index through the same IMAGE→OCR path, first frame only.
18. **Operational settings are user-configurable** (2026-07-20): `[ingestion]`/`[llm]`/`[retrieval]` knobs whose defaults equal the former hardcoded constants.
19. **bge-m3 inference in fp16 + streamed per-document embedding** (2026-07-20), from a memray profile.
20. **litellm adopted as the LLM client inside `llm/`** (2026-07-24) — a provider client, not a fourth framework.
21. **Graph prompt budgets derive from one `graph.context_window`** (2026-07-25).
22. **Course-tuned entity extraction, bundled and fingerprinted** (2026-07-26): Groundly ships its own `extract_graph` prompt and entity types; the fingerprint triggers rebuilds.
23. **The graph-build cost estimate is a range, and the build's spend is metered** (2026-07-27).
24. **Reasoning effort is a configurable provider passthrough** (2026-07-31); its second half — community reports on a different provider — was removed by 35.
25. **A local graph build serializes** (2026-08-01): `concurrent_requests=1` whenever a provider is loopback.
26. **The knowledge graph gets a picture** (2026-08-02): `groundly export-graph` writes one self-contained HTML file with a vendored renderer, no CDN.
27. **The eval harness is a fourth client** (2026-08-03) — removed from `main` by 34.
28. **The vector arm wins the measured comparison** (2026-08-08): the graph arms lost at every cutoff the product uses.
29. **Every implemented arm is selectable** (2026-08-11) — superseded by 34/35.
30. **The grounding-fidelity experiment** (2026-08-13/16): enforced `ask` against a real MCP host, two subjects, two models.
31. **The tool surface is the retrieval trigger** (2026-08-16): server instructions state the norm, tool descriptions say which tool, and no tool is ranked above another server-wide. **Still binding.**
32. **`[providers.chat]` gets a documented floor and a probe** (2026-09-07) — removed by 33 along with `ask`.
33. **MCP-first, zero-key** (Paul, 2026-09-11): the enforced `ask` pipeline (MCP + CLI, prompts/citations/tracing, `config check`) and thick generation (`generate_*`, the job registry, `[providers.generation]`) are removed. The host composes answers from `search` and writes cards through `submit_*`; the verifier is unchanged. `search` no longer writes traces and verdicts are no longer logged, so `progress.db` holds only the graph build's spend row. Measured basis: a host *told* to retrieve draws with enforced `ask` on answer support, `ask` refused 28–47% of questions on a below-floor model, and it was the only reason a student needed a key.
34. **The research surface is tagged and removed** (Paul, 2026-09-11): `eval/`, `groundly eval`, `groundly eval-grounding`, the gold sets, the router and the adaptive stub live at tag `thesis-experiments-2026-09`; results and protocols in [`thesis/experiments.md`](thesis/experiments.md). Re-running an experiment means checking out the tag.
35. **GraphRAG is a build-time topic-map builder only** (Paul, 2026-09-11): query-time graph search — local, global, hybrid fusion, `drill_down`, `overview` — is removed; the build, its cost estimate and `export-graph` stay as the input to sub-project 3. One provider per build: `graph.report_call_class` is gone and the extraction model must support JSON-schema structured output, which the existing probe already checks. Measured basis for keeping the build: on apd, level-0 communities kept 51% of gold questions' chunks in one topic against 32% for k-means over the stored embeddings (passc 37% vs 34%), with readable labels.
36. **LlamaIndex dropped** (Paul, 2026-09-11): one retriever left, so the shared-interface reason is gone. A frozen `Hit` dataclass replaces `NodeWithScore`; the MCP `search` payload is unchanged.

## 8. Roadmap

P1–P5 shipped (indexing, interchange, MCP surface, graph build, the measured comparison). P6–P7 as originally phased are superseded by the sub-projects below, each getting its own spec before implementation.

| # | Sub-project | Status |
|---|---|---|
| 1 | The cut: MCP-first, zero-key (this spec's decisions 33–36) | in progress |
| 2 | UC-10 verified mock tests — `submit_questions` through the same verifier | next |
| 3 | Topic map — graph build → persisted level-0 themes, a `topics` tool, themes in `export-graph` | after 2 |
| 4 | Mastery — Anki note tags + AnkiConnect review history, `submit_quiz_result`, rollup per theme and material, coverage gaps (absorbs UC-13/UC-14) | after 2 and 3 |
