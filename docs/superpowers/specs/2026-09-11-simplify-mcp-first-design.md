# Simplify: MCP-first, zero-key (sub-project 1 — the cut)

**Status:** approved 2026-09-11 (Paul) · branch `simplify-mcp-first` from `main` @ 3f4cda8 · baseline 715 passed, 24 slow deselected.

**Done =** every item under [Verification](#verification) holds.

## Why

A YAGNI review found ~54% of `groundly/` (~6.1k of 11.4k lines), ~300 of 715 tests and the
whole `graphrag` dependency tree serving experiments whose results are already banked in
`docs/thesis/`:

- **Graph retrieval arms:** `hybrid-local` never beats `vector` on MRR at k ≥ 5 on either
  subject; 0 of 10 McNemar tests below p = 0.18. `graph-global` returns 88–96% of the
  corpus in a fixed order. The router scores 47.9% against 45.8% for a constant.
- **Enforced `ask`:** a host told to search draws with it on answer support; `ask` alone
  refused 28–47% of questions on `gpt-oss-120b`, and it is the only reason a student
  must configure a provider.
- **Thick generation:** reachable only over MCP, where the host is already an LLM that
  generates and calls `submit_cards` through the same verifier.

## Decisions

| # | Decision | Consequence |
|---|---|---|
| 1 | Research results are banked | Tag `thesis-experiments-2026-09` on 3f4cda8; delete the eval harnesses and gold sets from `main` |
| 2 | MCP-first: the host generates, Groundly verifies | Thick path (`generate_deck`, jobs, `[providers.generation]`) removed; thin `submit_*` is the only door |
| 3 | Zero-key: drop `ask` | MCP `ask` + `groundly ask` + prompts/citations/tracing/probe + `config check` removed |
| 4 | GraphRAG stays as a **build-time topic-map builder only** | Query-time graph search removed; the build, cost estimate and `export-graph` stay for sub-project 3 |
| 5 | LlamaIndex dropped | One arm left, so the shared-interface reason is gone; a plain `Hit` dataclass replaces `NodeWithScore` |
| 6 | Full prose diet, docs and code | Register → one-liners; experiment write-ups → `docs/thesis/experiments.md`; code keeps intent + invariants only |

**Why the graph survives (decision 4), measured 2026-09-11 in a throwaway spike.** Topic
maps with k matched to the 31/32 level-0 communities, random baseline preserving topic
sizes. Gold-question purity (all of a question's gold chunks in one topic): apd graph
**51%** vs k-means over stored bge-m3 vectors 32% (random 6% / 2%); passc 37% vs 34%
(tie). On cross-lecture gold questions neither beats the other (apd n=14, passc n=22).
Community titles are human-readable; k-means labels from headings are not; k-means
reshuffles across seeds (ARI 0.52–0.62). Paul chose the graph's grouping and labels over
the free alternative, accepting an extraction key for the build, $0.15–0.49 per build and
graphrag's 56 transitive packages.

## Sub-project map

| # | Sub-project | Depends on |
|---|---|---|
| **1** | **The cut (this spec)** | — |
| 2 | UC-10 thin quizzes: `submit_questions` → verifier (rewrites the thick-door spec on `p6-uc10-mock-tests`) | 1 |
| 3 | Topic map: build → persisted level-0 themes, `topics` MCP tool (report text + cited chunks, zero LLM per query), `export-graph` shows themes | 1 |
| 4 | Mastery: Anki note tags, AnkiConnect read (FSRS retrievability, review log), `submit_quiz_result`, evidence per question in `progress.db`, rollup per theme and per material, coverage gaps | 2, 3 |

## What survives

**MCP surface — exactly 7:** `list_subjects`, `search`, `get_page`, the `groundly://`
resource, `submit_cards`, `list_decks`, `export_deck`. `SERVER_INSTRUCTIONS` text is
unchanged (it states the norm and ranks no tool); descriptions drop references to removed
tools.

**CLI:** `init`, `index [--graph]`, `list`, `remove`, `search`, `export`, `import`,
`export-deck`, `export-graph`, `mcp`, `serve`, `config` / `config set`,
`models install|uninstall`.

| Layer | Stays | Goes |
|---|---|---|
| clients | `cli/`, `mcp/` | `eval/`, `web/` |
| agents | `verifier.py`, `decks.py` (`submit_cards`) | `ask`, `citations`, `prompts`, `tracing`, `probe`, `router`, `study_modes`, `jobs` |
| retrieval | `vector.py` (plain `VectorRetriever`), `hits.py` | `arms`, `graph`, `adaptive`, `nodes`, `HybridLocalRetriever` |
| ingestion | all, incl. `graph.py` (build) | — |
| llm | `embeddings`, `rerank`, `config`, `graphrag_adapter`, `graph_cost`, `chat` (extraction probe) | — |
| core | all | — |

**Config:** `CALL_CLASSES = ("extraction",)`, used only by `index --graph`;
`graph.report_call_class` removed. Everything except the graph build is zero-key.

**`progress.db`:** file and tables unchanged, no migration. Only writer: the graph build's
spend trace. `search` traces and verification rows stop.

**Interchange: no change.** No manifest field (LlamaIndex was never pinned there), no
`format_version` or `user_version` bump; old bundles' `generation_source='server'` cards
stay valid.

**Dependencies removed:** `llama-index`, `fastapi`, `uvicorn`, the `eval` extra.

**Size:** ~11.4k → ~6.4k lines before the prose diet; ~715 → ~480 tests.

## Commits

Each commit leaves the non-slow suite green and ruff clean.

- **C0 — tag.** Annotated `thesis-experiments-2026-09` on 3f4cda8. Pushed only on Paul's
  confirmation.
- **C1 — thick generation out.** First because `agents/prompts.py` is shared with `ask`.
  Delete `agents/jobs.py`; `estimate_generation`, `_parse_cards`, `generate_deck_job` from
  `decks.py`; MCP `generate_deck`/`get_job`; card prompts; the `generation` call class.
  Tests: delete `test_jobs.py`; trim `test_decks.py`, `test_mcp_server.py`,
  `test_agents_prompts.py`.
- **C2a — answer paths and research surface out.** Delete `eval/`, `cli/eval.py`,
  `cli/eval_grounding.py`, `agents/{ask,citations,prompts,tracing,probe,router,study_modes}.py`,
  `retrieval/{arms,graph,adaptive}.py`, `HybridLocalRetriever`, `groundly ask`,
  `config check`, MCP `ask`/`drill_down`/`overview`, `evals/*/gold.jsonl`. Stop `search`
  traces and verification rows, and delete the `progress.py` helpers that leaves without
  callers (`read_traces`, `max_trace_id`, `record_verification`). `CALL_CLASSES` → `("extraction",)`, drop
  `report_call_class`. `git mv cli/ask.py cli/search.py`. Tests: delete `tests/eval/`,
  `test_cli_eval*.py`, `test_agents_{ask,router,study_modes,prompts}.py`,
  `test_citations.py`, `test_probe.py`, `test_arms.py`, `test_retrieval_graph.py`,
  `test_retrieval_stubs.py`; trim `test_cli_ask.py` (keep `search`), `test_mcp_server.py`,
  `test_config.py`, `test_progress.py`, `test_cli_models.py`, `test_retrieval_vector.py`;
  rewrite `test_layering.py` to keep only "nothing imports the client layer".
- **C2b — one provider.** `CALL_CLASSES` → `("extraction",)` and `graph.report_call_class`
  removed, which also strips report routing from `ingestion/graph.py` (plan fields, probe
  target, second completion model, manifest write), `llm/graphrag_adapter.py`,
  `llm/graph_cost.py` and `cli/cost_display.py`; `complete()`'s eval-only `model=`
  override goes with it. Old `config.toml` files keep loading, retired keys ignored.
- **C3 — LlamaIndex → `Hit`.** `nodes.py` → `hits.py`; plain `VectorRetriever`; callers in
  `mcp/server.py`, `cli/search.py`, `agents/verifier.py`, `agents/decks.py` read `Hit`
  fields.
- **C4 — dependencies.** Change `.claude/hooks/guard-pins.sh` from "any `==`" to a named list
  of interchange pins (graphrag, docling, sentence-transformers, FlagEmbedding, rapidocr,
  litellm), then remove `llama-index`, `fastapi`, `uvicorn` and the `eval` extra; re-lock.
- **C5 — docs, rules, tooling.** Record the decisions through `/decision`. Spec register →
  one-liners; decisions 27–32 and `retrieval.md`'s results → `docs/thesis/experiments.md`.
  Architecture docs describe the current system only. UC-02 → `search` only; UC-12 removed
  with a pointer to sub-project 3; UC-10/13/14 marked against the sub-project map; README
  claims only what exists. `graphrag-provider.md` loses query-side content;
  `tech-stack.md` loses the chat-model floor. `.claude/rules/*` updated; `research-specialist`
  agent deleted (lives at the tag); other agents and skills trimmed. Old
  `docs/superpowers/specs|reviews` stay as dated history.
- **C6 — prose diet.** Every surviving module keeps docstrings for intent and invariants;
  history, measurement tables and "used to" narration go. Comments-only diff: same tests,
  same ruff result.

## Interfaces

```python
@dataclass(frozen=True)
class Hit:
    chunk_id: int
    text: str
    filename: str
    page: int | None
    heading_path: str | None
    score: float

class VectorRetriever:
    def retrieve(self, query: str) -> list[Hit]: ...

def search(subject, query, *, k=None, rerank=None, embedder=None, reranker=None) -> list[Hit]: ...
```

The MCP `search` payload (`chunk_id`, `text`, `score`, `filename`, `page`, `heading_path`,
`uri`) and the `groundly search` output are unchanged — a host must not notice C3.

## Errors

- MCP `_maps_service_errors` is deleted with the tools it wrapped; `search` and
  `submit_cards` keep their own `ModelDownloadError`/`ValueError` → `ToolError` mapping.
- Surviving: `ModelDownloadError` (search, submit_cards, models); `ProviderNotConfiguredError`,
  `ChatUnreachableError`, `GraphBuildError` (graph build only). `GraphNotBuiltError` and
  `NoCitationsError` go with their modules.
- **Old `config.toml` files keep loading.** Retired `[providers.chat|generation|router|judge]`
  sections and `graph.report_call_class` are ignored with a debug log, never rejected.

## Verification

1. Every commit: `pytest` (non-slow) green; `ruff check` and `ruff format --check` clean.
2. After C4's re-lock, the suite passes with `llama-index`, `fastapi`, `uvicorn` uninstalled.
3. `test_layering.py`: nothing imports the client layer.
4. A test pins the MCP surface to exactly the 7 entries.
5. `test_config_with_retired_sections_still_loads`.
6. Zero-key tests hold: with no `config.toml`, `init`, `index`, `search`, `submit_cards`,
   `export_deck` work.
7. Slow suite (`-m slow`) passes once at the end.
8. Manual: `groundly mcp` in Claude Code on apd — `list_subjects` → `search` → `get_page` →
   `submit_cards` → `export_deck`.
9. `graphify update .`; `spec-guardian` and `security-reviewer` report no blocking finding.

## Risks

- **Hidden coupling** (verb registration, conftest fixtures, lazy imports): every commit
  green, plus a grep for each deleted module name before committing.
- **The Edit hook runs `ruff --fix`** and strips imports that look unused mid-edit: change
  bodies first, add imports last.
- **`p6-uc10-mock-tests`** still specs the thick door; sub-project 2 rewrites it. Untouched here.
