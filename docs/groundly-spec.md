# Project Specification: Groundly — Local-First Course Knowledge Bases for AI Agents
### Bachelor Thesis Project · Universitatea Politehnica Timișoara, AC

> **Document map** — this file is the master overview; detail lives in:
> [`use-cases/`](use-cases/knowledge-base.md) (flows & acceptance criteria) ·
> [`architecture/`](architecture/overview.md) ([data model](architecture/data-model.md), [retrieval](architecture/retrieval.md), [agents](architecture/agents.md)) ·
> [`tech-stack/`](tech-stack/tech-stack.md) (incl. the LLM provider boundary) ·
> [`infrastructure/`](infrastructure/distribution.md) ([security](infrastructure/security.md), [cost model](infrastructure/cost-model.md))

## 1. Vision

**Groundly turns a folder of course materials into a portable, agent-consumable knowledge base.** A student runs `groundly index ./slides/` once; from then on, any MCP-capable agent (Claude Code, Claude Desktop, Codex) can answer questions grounded in the actual course content with page-level citations, generate execution-verified tests and flashcards, quiz weak areas, and track mastery. The knowledge base — vectors, graph, verified decks — is a file that can be shared with any other Groundly user and used directly.

Everything runs on the student's machine. There is no server, no account, no upload. The only external dependency is an optional OpenAI-compatible LLM endpoint (the student's own cloud key, or LM Studio/Ollama).

**Why not NotebookLM or a Claude Project?** Four properties they don't have: (1) **verified generation** — every generated question passes a server-side verifier, including subprocess *execution* of code answers; (2) **page-level citations** enforced structurally, with "not covered by the course materials" instead of hallucination; (3) **a portable pre-built index** — one student pays the indexing cost, the whole course imports the result; (4) **cross-host progress** — mastery and study memory persist across whatever agent the student talks to.

**Thesis-level contribution:** (a) an empirical comparison of classic RAG vs GraphRAG retrieval quality on real, heterogeneous university course corpora (RO/EN mixed), stratified by query class, across four retrieval arms; (b) a measured comparison of **enforced vs agent-mediated grounding** (the `ask` tool's pipeline vs host-composed answers over `search`); (c) a portable knowledge-base interchange format with pinned-model compatibility semantics.

## 2. Users

One human role: the **student** (owner of the machine and the data). The other "users" are **agents** — MCP hosts acting on the student's behalf. There is no auth, no roles, no tenancy: subject scoping is filesystem layout, and privacy boundaries are file boundaries (see §4).

## 3. Core Use Cases

- **UC-01 Index materials** — digital PDF/DOCX/PPTX/MD/HTML/LaTeX/AsciiDoc/CSV/XLSX/EPUB/TXT/source → Docling → chunks → embeddings (+ optional graph). [Detail](use-cases/knowledge-base.md)
- **UC-02 Grounded Q&A** — `ask` (enforced pipeline, cited-or-refusal) and `search` (raw cited chunks for host agents). [Detail](use-cases/knowledge-base.md)
- **UC-03 Source management** — list/re-index/delete materials per subject. [Detail](use-cases/knowledge-base.md)
- **UC-10 Verified mock tests** — generate→verify→regenerate; code questions execution-verified. [Detail](use-cases/student-modes.md)
- **UC-11 Verified flashcards → Anki** — generated + verified decks exported as `.apkg`. [Detail](use-cases/student-modes.md)
- **UC-12 Graph study formats** — topic overviews and drill-downs from community summaries. [Detail](use-cases/student-modes.md)
- **UC-13 Coding challenges** — generated from course content, reference solutions execution-verified. [Detail](use-cases/student-modes.md)
- **UC-14 Mastery & study memory** — per-community mastery from quiz results; cross-session continuity tools. [Detail](use-cases/student-modes.md)
- **UC-30 Share knowledge bases** — export/import `.groundly` bundles with manifest-pinned compatibility. [Detail](use-cases/sharing.md)

Dropped from the v1 spec: professor modes UC-20–24, the code sandbox, photo notes UC-15, OCR, tiers, auth.

## 4. System Architecture

One Python package (`uv tool install groundly`), three runtime modes, zero services:

```
  Claude Code / Codex / Desktop        terminal (student)         browser
        │ MCP (stdio, host-spawned)         │ one-shot verbs           │
        ▼                                   ▼                          ▼
  ┌──────────────────────── client layer ─────────────────────────────────┐
  │ mcp/ FastMCP tools     cli/ typer verbs      web/ mastery dashboard   │
  │ stdio: `groundly mcp`  (index/import/export/ (static page, served     │
  │ HTTP: `groundly serve`  ask/config)           by `serve`)             │
  └──────────────┬───────────────┬──────────────────────┬─────────────────┘
                 ▼               ▼                      ▼
  ┌──────────────────────── service layer ────────────────────────────────┐
  │ agents/    ask pipeline (trust layers → gen → citation check)         │
  │            exam verifier gate (thick generate_* + thin submit_*)      │
  │ retrieval/ four arms · router · fusion · rerank · citation resolution │
  │ ingestion/ docling subprocess → chunk → embed; graphrag batch         │
  └──────────────┬─────────────────────────────────────────────────────────┘
                 ▼
  ┌────────── foundations ─────────────┐    ┌── external (optional) ───────┐
  │ llm/  provider per call class +    │──▶ │ OpenAI-compatible endpoint   │
  │       cost metering into traces    │    │ (cloud key / LM Studio /     │
  │ core/ stores (SQLite WAL), manifest│    │  Ollama)                     │
  └──────────────┬─────────────────────┘    └──────────────────────────────┘
                 ▼                           bge-m3 + reranker in-process,
  ~/.groundly/<SUBJECT>/                     lazy-loaded
```

### Storage (`~/.groundly/`, global; `GROUNDLY_HOME` overrides)

```
~/.groundly/
  config.toml          # provider config per call class, preferences
  <SUBJECT>/           # e.g. PDSS/ — one dir per subject
    manifest.json      # format + model pins (the interchange contract)
    materials/         # original digital files (citation targets)
    store.db           # SQLite: chunks, vectors (sqlite-vec), sparse terms,
                       #         FTS5, verified decks/questions  → EXPORTED
    progress.db        # quiz history, study notes, traces       → NEVER exported
    graph/             # MS graphrag parquet artifacts
```

The **privacy boundary is a file**: `store.db` travels, `progress.db` (your queries, results, notes) never does. Subject isolation is directory isolation — no query *can* cross subjects.

### Component decisions

| Component | Decision | Decisive reason |
|---|---|---|
| Distribution | Python package via `uv` | Docling/LlamaIndex/graphrag/RAGAS ecosystem is Python-only |
| Interface | CLI (typer) + MCP server (FastMCP, stdio + HTTP); **no TUI** | The host agent is the interactive surface; residual tasks are batch verbs |
| Storage | SQLite (WAL) + sqlite-vec + FTS5, files on disk | Zero services; export = zip; exact KNN at subject scale |
| Extraction | Docling, **digital documents only — no OCR** | Professor decision (pivot #3); scanned PDFs fail cleanly |
| Embeddings | `bge-m3` local, pinned incl. hf_revision; dense + learned sparse | Quality-first (Paul); RO/EN cross-lingual; the pin makes shared vectors compatible |
| Rerank | `bge-reranker-v2-m3`, **default ON** | Quality over performance (Paul); `--no-rerank` for weak hardware |
| Graph | MS `graphrag` per-subject batch → parquet | Canonical GraphRAG; naturally file-based |
| Retrieval orchestration | LlamaIndex `Retriever` interface | One interface across four evaluation arms |
| Agent loops | **Plain async functions** (LangGraph dropped) | Post-pivot roster is a pipeline + two bounded loops |
| LLM access | OpenAI-compatible `base_url` + key per call class (`chat`, `generation`, `extraction`, `router`) | One code path for cloud keys and LM Studio/Ollama; no subscription-OAuth piggybacking |
| Flashcard delivery | `.apkg` export via genanki | Anki owns daily review; Groundly owns verified generation |
| Dashboard | One static HTML page served by `groundly serve` | React toolchain for one page was unjustifiable |

## 5. Hybrid Retrieval Strategy

Four arms behind one LlamaIndex `Retriever` interface — see [`architecture/retrieval.md`](architecture/retrieval.md):

1. **Vector baseline** — three channels: bge-m3 dense (exact KNN via sqlite-vec) + bge-m3 learned sparse + FTS5/BM25, fused with RRF, cross-encoder reranked.
2. **GraphRAG** — graphrag local search (entity-anchored, multi-hop) and global search (community summaries, synthesis).
3. **Static hybrid** — router (cheap LLM call) dispatches per query class; fusion + rerank. Production arm. The router is also the **cost gate** for token-hungry global search.
4. **Adaptive agentic** — retrieve → self-grade → escalate/rewrite, hard-bounded at 2 iterations. **Evaluation arm only, never the product default.**

**Evaluation plan (thesis core):** gold Q/A set per pilot subject from past exams, stratified factoid / multi-hop / global-synthesis, RO and EN, cross-lingual queries as their own slice. Metrics per arm × class: retrieval hit rate, RAGAS groundedness/faithfulness, citation accuracy, router accuracy, cost, latency. Plus the **grounding-fidelity experiment**: the same gold questions answered via the enforced `ask` pipeline vs host-composed over `search`. GraphRAG is timeboxed; "the graph didn't help" is a valid, publishable finding. **Gold-set construction starts before implementation** — it needs the professor, not code.

## 5b. Agent Layer

Governing rule: agents only where the system must decide, iterate, or use tools mid-task — everything else is a pipeline. See [`architecture/agents.md`](architecture/agents.md). Roster of two:

1. **Ask pipeline** (interactive): router → retrieval → trust-layered prompt → generation → citation resolution → cited answer or "not covered". Exposed as the MCP `ask` tool and the `groundly ask` CLI verb — the same function is the product tool and the evaluation instrument.
2. **Exam verifier** (the identity of generation): every question entering `store.db` passes verification — answerable from cited chunks (re-retrieval), answer key correct, distractors wrong, code executed in a subprocess with timeout. Generators are pluggable: **thick** (`generate_*`, Groundly's provider key) or **thin** (`submit_*`, the host agent generates, same verifier gates). Rejections carry machine-readable reasons so hosts regenerate conversationally.

**Trust layers** (prompt assembly, low never overrides high): 1 immutable system rules · 2 subject profile (user-editable, shippable in exports; trusted content never trusted authority, size-capped, cannot disable grounding) · 3 task params · 4 retrieved content + imported KB content + user input — **data, never instructions**, delimited and inert.

## 6. Non-Functional Requirements

- **Grounding**: every answer/question cites chunk ids resolving to document + page; zero resolvable citations = error; insufficient context = "not covered". No model-knowledge fallback on any path.
- **Privacy**: nothing leaves the machine except LLM calls to the student's own configured provider. Exports contain the whole KB (documented plainly in the export UX) but never `progress.db`.
- **Security**: import is the trust boundary (zip-slip protection; imported content is layer-4); subprocess runner (timeout + tempdir); `serve` binds 127.0.0.1 only. See [`infrastructure/security.md`](infrastructure/security.md).
- **Languages**: RO and EN; retrieval must be cross-lingual (RO question over EN slides — dense channel carries this; lexical channels are same-language).
- **Concurrency**: SQLite WAL + busy_timeout from day one (one-shot CLI and host-spawned MCP share `store.db`); lazy model loading (no 2.2GB load at MCP spawn); generation jobs serialized when the provider is a local runtime.

## 7. Resolved Decisions

One-line register:

1. **Local-first, hard pivot** from the multi-tenant platform (professor, 2026-07-15); old repo archived.
2. **MCP-first**: Groundly is an MCP server for external agents; CLI for lifecycle; no TUI (professor).
3. **Embedded storage**: SQLite WAL + sqlite-vec + FTS5 + parquet under `~/.groundly/`; no Postgres/Redis/Celery/Docker.
4. **bge-m3 local, pinned (incl. hf_revision)**, dense + learned sparse; reranker default ON; ColBERT rejected (storage). Quality over performance (Paul).
5. **No OCR** — digital documents only; vision fallback and photo notes dropped (professor).
6. **Verifier-gate generation**: server-side verifier mandatory; generators pluggable (thick/thin). Flashcards delivered as Anki `.apkg`.
7. **Interchange format**: export = subject dir minus `progress.db`; manifest pins embedding/graphrag/chunking; no merge in v1; import creates fresh `progress.db`.
8. **Frameworks**: LlamaIndex + MS graphrag + FastMCP, one owner each; **LangGraph and LangSmith dropped** — traces live in a local table.
9. **Providers**: OpenAI-compatible per call class; no subscription-OAuth piggybacking (ToS-fragile).
10. **Study memory**: `recent_activity` daily rollups + `remember` notes + `continue-studying` MCP prompt; no server-side LLM summarization.
11. **Pilot subjects: two** (Parallel & Distributed Algorithms; an ML course) — carried over from v1. Professor available for gold-set spot-checks.
12. **Timeline**: defense June/July 2027.
13. **Expanded ingest formats** (2026-07-16): all docling-native text formats (HTML, LaTeX, AsciiDoc, CSV, XLSX, EPUB) plus a wider plain-text set (rst, json, yaml, toml, sh, sql, cs, rb, kt, swift); OCR and `.ipynb` still excluded.

## 8. Phasing (roadmap v2)

| Phase | Deliverable | Verify by |
|---|---|---|
| P1 | `groundly init/index`: Docling (subprocess, digital-only) → HybridChunker → bge-m3 dense+sparse → sqlite-vec/FTS5; resumable (hash-skip); WAL | Index a real course incl. one scanned PDF (fails cleanly) + one Ctrl-C resume; page attribution correct |
| P2 | Import/export: zip + manifest validation + re-embed path | Export on machine A, import on machine B, citations open the right page |
| P3 | Grounded core + `groundly ask`: four arms, trust layers, citations, refusal, traces | Gold-set eval starts; "not covered" path proven |
| P4 | MCP v1: `list_subjects`/`search`/`ask`/`get_page` over stdio + HTTP; citation resources | Demo inside Claude Code: search, ask, open a cited page |
| P5 | GraphRAG: batch build (cost estimate, skippable) + `drill_down`/`overview` tools | Timeboxed; graph arms measured on the gold set |
| P6 | Study toolset: verifier gate (thick + thin), `export_deck` (.apkg), adaptive quiz, mastery report, study memory | Verified deck imports into Anki; rejection reasons round-trip through a host agent |
| P7 | Mastery dashboard (static page) | Per-community mastery renders from progress.db |

**P1–P4 = the professor's product** (indexed KB, interchange, agent-consumable). P5–P7 are differentiators and natural cut-lines. Gold-set collection runs in parallel from P0. Evaluation work starts when P3 lands, not after P7.
