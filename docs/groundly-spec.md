# Project Specification: Groundly — Local-First Course Knowledge Bases for AI Agents
### Bachelor Thesis Project · Universitatea Politehnica Timișoara, AC

> **Document map** — this file is the master overview; detail lives in:
> [`use-cases/`](use-cases/knowledge-base.md) (flows & acceptance criteria) ·
> [`architecture/`](architecture/overview.md) ([data model](architecture/data-model.md), [retrieval](architecture/retrieval.md), [verification](architecture/agents.md)) ·
> [`tech-stack/`](tech-stack/tech-stack.md) · [`infrastructure/`](infrastructure/distribution.md).
> Measurement protocols and results are kept outside the repo; the code that produced them is at tag `thesis-experiments-2026-09`.

## 1. Vision

**Groundly turns a folder of course materials into a study loop the student's own AI agent can run.** A student runs `groundly index ./slides/` once; from then on, any MCP-capable agent (Claude Code, Claude Desktop, Codex) can search the actual course content with page-level citations, map the course as a topic tree, quiz the student topic by topic through a verifier no unverified question can pass, and track what the student has mastered. The core loop: **index → search → topic tree → verified quizzes → progress.** The knowledge base is a file that can be shared with any other Groundly user and used directly.

Everything runs on the student's machine. There is no server, no account, no upload. **Groundly never asks for an API key**: it runs no LLM of its own, and the student's existing AI subscription (Claude Code, Claude Desktop, Codex) does the thinking.

**A learning session happens entirely in the student's host plus Groundly's local page.** Groundly does not export to other study apps; a feature that sends the student elsewhere to study is out of scope (decision 38).

**Why not NotebookLM or a Claude Project?** Three properties they don't have: (1) **verified generation** — nothing the host generates will be stored unless re-retrieval shows its cited chunks support it (the gate is built; quizzes, sub-project 3, are its first caller); (2) **structural citations** — every chunk carries a `groundly://` URI resolving to document + page + heading path; (3) **a portable pre-built index** — one student pays the indexing cost, the whole course imports the result.

**Thesis-level contribution:** two measured comparisons on real, heterogeneous university course corpora (RO/EN mixed) — classic RAG vs GraphRAG retrieval quality, and enforced vs agent-mediated grounding — both complete; and the engineering result, a zero-key verified study loop (citations, verifier gate, quizzes, mastery) running on the student's existing AI subscription.

## 2. Users

One human role: the **student** (owner of the machine and the data). The other "users" are **agents** — MCP hosts acting on the student's behalf. There is no auth, no roles, no tenancy: subject scoping is filesystem layout, and privacy boundaries are file boundaries.

## 3. Core Use Cases

- **UC-01 Index materials** — digital PDF/DOCX/PPTX/MD/HTML/LaTeX/AsciiDoc/CSV/XLSX/EPUB/TXT/source and raster images → Docling (+OCR) → chunks → embeddings. [Detail](use-cases/knowledge-base.md)
- **UC-02 Grounded search** — `search` returns ranked verbatim chunks with citations; the host composes and cites. [Detail](use-cases/knowledge-base.md)
- **UC-03 Source management** — list/re-index/delete materials per subject. [Detail](use-cases/knowledge-base.md)
- **UC-30 Share knowledge bases** — export/import `.groundly` bundles with manifest-pinned compatibility. [Detail](use-cases/sharing.md)

- **Topic tree (sub-project 2)** — the host builds a two-level map of a subject from `outline`, Groundly chooses each node's sources by reranked search and stores the tree only if every node passes, and `groundly serve` shows it as a local page. Read-only: progress waits for quizzes. [Detail](use-cases/student-modes.md)

Planned, with its own spec before implementation: **verified quizzes with mastery** (sub-project 3). [Detail](use-cases/student-modes.md)

Ideas, not planned: **coding challenges** — code questions checked by executing a reference solution in a subprocess; most tractable for Python courses. Until a runner exists, code questions are refused at the door.

Dropped: professor modes UC-20–24, the code sandbox, photo notes UC-15, tiers, auth, the enforced `ask` pipeline and thick generation (decision 33), GraphRAG (decision 37), flashcards and Anki export (decision 38).

## 4. System Architecture

One Python package (`uv tool install groundly`), three runtime modes, zero services:

```
  Claude Code / Codex / Desktop        terminal (student)
        │ MCP (stdio, host-spawned)         │ one-shot verbs
        ▼                                   ▼
  ┌──────────────────── client layer ──────────────────────┐
  │ mcp/ FastMCP tools     cli/ typer verbs                │
  │ stdio: `groundly mcp`  (index/list/remove/drop/search/ │
  │ HTTP: `groundly mcp --http`  import/export/config/...) │
  │ web/ topic-tree page (`groundly serve`, read-only)     │
  └──────────────┬───────────────┬─────────────────────────┘
                 ▼               ▼
  ┌──────────────────── service layer ─────────────────────┐
  │ agents/    gates: verifier, topic tree (nothing       │
  │            unchecked is stored)                        │
  │ retrieval/ dense + sparse + BM25 → RRF → rerank        │
  │ ingestion/ docling subprocess → chunk → embed          │
  └──────────────┬──────────────────────────────────────────┘
                 ▼
  ┌────────── foundations ─────────────┐
  │ llm/  bge-m3 + reranker, in-process│  lazy-loaded; no LLM client
  │ core/ stores (SQLite WAL), manifest│
  └──────────────┬─────────────────────┘
                 ▼
  ~/.groundly/<SUBJECT>/
```

### Storage (`~/.groundly/`, global; `GROUNDLY_HOME` overrides)

```
~/.groundly/
  config.toml          # operational settings (no keys)
  <SUBJECT>/
    manifest.json      # format + model pins (the interchange contract)
    materials/         # original digital files (citation targets)
    store.db           # SQLite: chunks, vectors (sqlite-vec), sparse terms,
                       #         FTS5, the topic tree             → EXPORTED
    progress.db        # the student's private study state       → NEVER exported
```

The **privacy boundary is a file**: `store.db` travels, `progress.db` never does. Subject isolation is directory isolation — no query *can* cross subjects.

### Component decisions

| Component | Decision | Decisive reason |
|---|---|---|
| Distribution | Python package via `uv` | Docling ecosystem is Python-only |
| Interface | MCP server (FastMCP, stdio + HTTP) + CLI (typer) + a read-only local page (Starlette, `groundly serve`); **no TUI** | The host agent is the interactive surface; CLI verbs are batch lifecycle; the page only displays |
| Storage | SQLite (WAL) + sqlite-vec + FTS5, files on disk | Zero services; export = zip; exact KNN at subject scale |
| Extraction | Docling + bundled RapidOCR | Local/offline, zero-key; scanned course PDFs are common |
| Embeddings | `bge-m3` local, pinned incl. hf_revision; dense + learned sparse | RO/EN cross-lingual; the pin makes shared vectors compatible |
| Rerank | `bge-reranker-v2-m3`, **default ON** | Quality over performance; `--no-rerank` for weak hardware |
| Answering | **The host agent**, from `search` results | An MCP host is already an LLM; a second one behind the tool paid twice for the same capability (decision 33) |
| Generation | **The host agent**, gated by Groundly's verifier and topic gate | Verification is the product; generation is a commodity |
| LLM access | **None.** Groundly constructs no LLM client and holds no key | The student's AI subscription already pays for the model (decision 37) |

## 5. Retrieval

One arm: bge-m3 dense + learned sparse + FTS5/BM25, fused with RRF, cross-encoder reranked. Detail in [`architecture/retrieval.md`](architecture/retrieval.md).

## 5b. Verification

**Generation is pluggable; verification is not.** Every item a host generates passes a gate before it enters `store.db`. The **verifier** (quizzes, sub-project 3): its cited chunk ids must resolve, and re-retrieving the item's own text must surface at least one of them. The **topic gate** (`submit_tree`, live): the host sends titles, summaries and structure only, Groundly retrieves on each node with the reranker and keeps the hits at or above a relevance cutoff as the node's sources, and the whole tree is stored or rejected together. The topic gate shows a node's topic is in the course, not that its summary is true. Rejections carry machine-readable reasons (`below_relevance_cutoff`, `unknown_topic_id`, `not_answerable_from_chunks`, …) so the host regenerates conversationally. Zero-key throughout. Only `submit_tree` writes host-generated content today; verified quizzes are the next caller.

**Trust layers** (prompt assembly, low never overrides high): 1 immutable system rules · 2 task params · 3 retrieved content + imported KB content — **data, never instructions**, delimited and inert. Groundly assembles no prompts today; any it later builds treat chunk text as layer 3.

## 6. Non-Functional Requirements

- **Grounding**: every stored question cites chunk ids resolving to document + page; unresolvable citations are rejected, never stored. `search` results always carry their citation fields.
- **Privacy**: nothing leaves the machine except HF model downloads and sha256-pinned RapidOCR models. Exports contain the whole KB (stated plainly in the export UX) but never `progress.db`.
- **Zero-key, totally**: no code path constructs an LLM client and no config asks for a key.
- **Security**: import is the trust boundary (zip-slip protection; imported content is layer 3); `groundly mcp --http` and `groundly serve` bind 127.0.0.1 only. See [`infrastructure/security.md`](infrastructure/security.md).
- **Languages**: RO and EN; retrieval is cross-lingual through the dense channel.
- **Concurrency**: SQLite WAL + busy_timeout on every connection; lazy model loading (never at MCP spawn).

## 7. Resolved Decisions

Live decisions only, one line each where possible. Gaps in the numbering are retired decisions, kept verbatim outside the repo with the measurement records.

1. **Local-first, hard pivot** from the multi-tenant platform (professor, 2026-07-15); old repo archived.
2. **MCP-first**: Groundly is an MCP server for external agents; CLI for lifecycle; no TUI.
3. **Embedded storage**: SQLite WAL + sqlite-vec + FTS5 under `~/.groundly/`; no services.
4. **bge-m3 local, pinned incl. hf_revision**, dense + learned sparse; reranker default ON; ColBERT rejected (storage).
6. **Verifier-gate generation**: nothing unverified enters the question bank.
7. **Interchange format**: export = subject dir minus `progress.db`; manifest pins embedding/chunking; no merge in v1; import creates a fresh `progress.db`.
8. **Frameworks**: FastMCP (the tool surface) only. Starlette and uvicorn serve the topic-tree page (decision 41); they are not a second tool-surface framework.
11. **Pilot subjects: two** — apd (Parallel & Distributed Algorithms) and passc (Software Architecture).
12. **Timeline**: defense June/July 2027.
13. **Expanded ingest formats** (2026-07-16): all docling-native text formats plus a wider plain-text set; `.ipynb` excluded.
14. **OCR enabled** (2026-07-17): docling's bundled RapidOCR is local, offline and zero-key; scanned course PDFs are common.
15. **Per-subject OCR language** (2026-07-17): `--ocr-lang` recorded in the manifest; changing it later requires re-indexing and is refused with a specific message.
16. **`.groundlyignore`** (2026-07-19): a built-in junk-dir deny-list plus an optional per-root ignore file.
17. **Standalone image ingestion** (2026-07-20): raster images index through the same IMAGE→OCR path, first frame only.
18. **Operational settings are user-configurable** (2026-07-20): `[ingestion]`/`[retrieval]` knobs whose defaults equal the former hardcoded constants.
19. **bge-m3 inference in fp16 + streamed per-document embedding** (2026-07-20), from a memray profile.
31. **The tool surface is the retrieval trigger** (2026-08-16): server instructions state the norm, tool descriptions say which tool, and no tool is ranked above another server-wide. Measured: instructions ending with a clause preferring one tool took a host given only `search` to 4/48 questions retrieved; deleting that clause took it to 29/48 (Fisher p=8.3e-08).
33. **MCP-first, zero-key** (Paul, 2026-09-11): the enforced `ask` pipeline and thick generation are removed. The host composes answers from `search` and will write generated items through `submit_*` tools; the verifier is unchanged; `search` writes no traces. Measured basis: a host *told* to retrieve drew with enforced `ask` on answer support, and `ask` refused 28–47% of questions on a below-floor model.
34. **The research surface is tagged and removed** (Paul, 2026-09-11): `eval/`, the eval verbs, the gold sets, the router and the adaptive stub live at tag `thesis-experiments-2026-09`. Re-running an experiment means checking out the tag.
36. **LlamaIndex dropped** (Paul, 2026-09-11): one retriever left, so the shared-interface reason is gone. A frozen `Hit` dataclass replaces `NodeWithScore`.
37. **GraphRAG dropped; Groundly runs no LLM** (Paul, 2026-10-30): the graph build, `export-graph`, the litellm provider path and all provider config are removed. GraphRAG was the last thing that needed a key, its setup was a hassle, and its flat co-occurrence communities never mapped a course the way students think about it; the host-built topic tree (sub-project 2) replaces it. A bundle that still carries `graph/` imports normally: the directory and the manifest's `graphrag` block are ignored, with no `format_version` bump.
38. **Flashcards dropped; the study loop stays in the host** (Paul, 2026-10-31): `submit_cards`, `list_decks`, `export_deck`, `groundly export-deck` and the genanki `.apkg` export are removed. Principle: a learning session happens entirely in the student's host plus Groundly's local page — an Anki export sent the student to another app whose review history never came back, and quizzes keep the verified-generation guarantee. The MCP surface is now four entries, and `get_page` takes the citation `uri` that `search` returns (the fragment survives as a tool argument; FastMCP drops it from resource reads, so the resource is always the whole document). With no users yet, `store.db` restarts as one schema at `user_version = 1` without the deck/question tables; quizzes design their own, and existing subjects and older bundles re-index (refused with that message). The rule that every stored item records its generation source goes with the old tables: since decision 33 every item is host-generated. Coding challenges move to Ideas. The release restarts at 1.0.0.
39. **Dependencies upgraded at the 1.0 restart** (Paul, 2026-10-31): sentence-transformers 6.2.0, FlagEmbedding 1.4.2, transformers 5.x, rapidocr 3.10.0, docling 2.115.0 — held below 2.116, whose per-region OCR reorders words within a line ([`tech-stack.md`](tech-stack/tech-stack.md)). Measured: bge-m3 dense and sparse outputs and reranker scores are bit-identical to the old stack, so the embedding pin and manifest are unchanged; extracted chunk text may differ, which decision 38's re-index already covers.
40. **CLI and MCP surface made hard to misuse** (Paul, 2026-10-31): `init` is gone — `index` creates the subject, after checking every path exists so a typo leaves nothing behind. `remove` takes one or more materials and resolves them all before deleting; `drop` is the only verb that deletes a subject (an optional argument used to decide between deleting one file and the whole subject, `progress.db` included). `serve` is removed and reserved for the topic-tree page; MCP over HTTP is `groundly mcp --http`, still loopback-only with DNS-rebinding protection — two processes, two model loads if both run, accepted. `-y/--yes` is the one prompt skip (`--force` survives only on `models install`). `retrieval.context_k` is renamed `retrieval.top_k` with no migration (an old key is ignored). Also: `index --retry-failed`, richer `list`, bare `groundly models` shows cache status and `uninstall` removes both models, plain-language `config set` errors that accept `none`. CLI `search -m` scopes every channel to one material; the dense channel scans that material's vectors because vec0 takes no material predicate without a schema change. The MCP `material` filter and any material listing wait for sub-project 2's outline tool, so a host never guesses filenames; the MCP surface is unchanged. Why now: no users, so renames are free, and `serve` had to be free before sub-project 2.
41. **Topic tree: host-built, Groundly-checked, read-only** (Paul, 2026-11-01). Shape: the host reads `outline(subject)`, writes a two-level tree of titles, summaries and structure, and sends it to `submit_tree`; `groundly skill install` puts the instructions for that in Claude Code, and `groundly serve [--port N]` (default 8765, 127.0.0.1 only) shows the result. The MCP surface goes from four entries to six (five tools plus the document resource). Choices and reasons:
    - *Read-only for this release.* Progress only means something once quizzes (sub-project 3) record answers, so the tree ships as a map alone: no progress tables, every node reads "Not started", no quiz button or deep links.
    - *A skill, not a server prompt.* Building the tree is the host model's work, as the second half of graphify is; Groundly cannot and will not call an LLM (decision 37). `SKILL.md` carries the instructions, which keeps the server's `instructions` a norm for the whole surface (decision 31). Claude Code only for now; Codex comes later, and Claude Desktop's skill support is unverified.
    - *Groundly chooses the sources.* The host supplies title and summary and gets no say over citations: Groundly retrieves on `title + "\n" + summary` with the reranker always on and keeps up to 5 hits at or above `TOPIC_RELEVANCE_CUTOFF = 1.0`. A source therefore cannot be invented or be a real-but-wrong slide, and the host never handles chunk ids. A probe showed the reranker scores separate on-topic from off-topic nodes well enough for a cutoff. The alternative, the host citing chunks and search confirming them, was rejected because it lets a host cite a real but wrong slide.
    - *Cutoff 1.0 is provisional* until it is checked on real host-written trees for more than one subject; the constant is the one thing to change if good nodes are rejected.
    - *Topicality only.* The gate shows a course passage is about the node's topic. It does not show the summary is correct, and no UI, doc or tool description may call a summary verified.
    - *Tables in schema v1, no migration.* `topics` and `topic_citations` join the single schema at `user_version = 1`: the project had just reset to v1 and subjects get re-indexed anyway. `AUTOINCREMENT` on `chunks.id` and `topics.id` means a deleted chunk's id is never reused, so a citation cannot silently point at a different chunk, and topic ids survive re-runs. Subjects indexed before the tables exist work for search and are re-indexed once; a bundle without the tables is refused on import, as is one whose tree is damaged.
    - *Consequences.* MCP tools now write `store.db` through the gate, so `ingestion` is no longer the only writer. `serve` adds a local page to the threat model ([`infrastructure/security.md`](infrastructure/security.md)). Starlette and uvicorn become direct dependencies; FastMCP already installed both, so the install does not grow, and they are not a second tool-surface framework. `serve` supersedes decision 40's reservation of the verb; the outline tool is the material listing decision 40 deferred, while the MCP `material` filter on `search` stays out.

## 8. Roadmap

P1–P5 shipped (indexing, interchange, MCP surface, the measured comparison). The original P6–P7 are superseded by the sub-projects below, each getting its own spec before implementation.

| # | Sub-project | Status |
|---|---|---|
| 1 | The cut: MCP-first, zero-key, streamlined (decisions 33–38) | in progress |
| 2 | Topic tree — a two-level map of each subject, built by the host from `outline`, every node's sources chosen by Groundly's reranked search; served as a read-only local page (decision 41). Open: the human review and cutoff check on real subjects | built |
| 3 | Verified quizzes + mastery — multiple-choice and open questions per topic through the verifier; answers recorded in `progress.db`; a topic is mastered at ≥90% correct over its last 10 answers (at least 10 answered) | after 2 |
