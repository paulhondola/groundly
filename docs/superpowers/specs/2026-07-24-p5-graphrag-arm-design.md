# P5 — GraphRAG Retrieval Arm (local + global search)

## Context

P1–P4 are shipped (indexing, import/export, grounded `ask`, MCP v1) plus an
unplanned performance-improvements pass. Of the four retrieval arms the thesis
compares, only Arm 1 (vector baseline) actually retrieves anything: Arm 2
(GraphRAG) and Arm 4 (adaptive) are literal stub classes that raise
`NotImplementedError`/`GraphNotBuiltError`, and the query router
(`groundly/agents/router.py`) labels queries but nothing consumes the label —
every query still runs the vector arm regardless of label. `graphrag==3.1.0`
is already pinned as a dependency, the `extraction` provider call-class is
already reserved for it in `groundly/core/config.py`, and the CLI already has
forward-looking messaging ("the graph is now stale — rebuilds on next
corpus-hash-triggered index run") anticipating this exact phase. This is P5
per `docs/groundly-spec.md` §8, confirmed as the next build target.

Arm 4 (adaptive) stays out of scope — it's eval-only and not needed for P5.

## Confirmed design decisions

1. **Scope**: build local search (multi-hop, entity-anchored) and global
   search (community-summary synthesis) together, plus router wiring and the
   two new MCP tools (`drill_down`, `overview`) — full P5 in one pass.
2. **Cost estimate**: rough heuristic, no tokenizer, no LLM call. Estimate
   tokens as `total_chunk_chars // 4`, price using the *existing*
   `ProviderConfig.input_price_per_mtok` field (already defined in
   `groundly/core/config.py`, already used for post-hoc chat cost tracing in
   `groundly/llm/chat.py`) applied to the `extraction` provider. If unpriced,
   print "no cost estimate available" but still require confirmation.
3. **Trigger**: opt-in `--graph` flag on `groundly index` for the first build;
   once `<subject>/graph/` exists, every subsequent `index` run checks a
   corpus hash (sha256 of sorted material sha256s) and auto-rebuilds if the
   corpus changed — matching the stale-graph message already coded at
   `groundly/cli/subjects.py:224-228`. `--yes/-y` skips the confirm prompt,
   mirroring `remove`'s existing pattern (`subjects.py:175, 187-191`).
4. **Chunk↔citation mapping**: feed graphrag pre-chunked text via
   `graphrag.api.index.build_index(config, input_documents=<DataFrame>)`
   (verified against the installed package — `build_index` takes this kwarg
   and bypasses graphrag's own file-based chunking entirely). Each Groundly
   chunk becomes one input "document" with `id=str(chunk_id)`; setting
   `chunking.size` generously above `CHUNK_MAX_TOKENS=512` (proposed: `4096`,
   an internal-only constant, never in the manifest) guarantees graphrag
   produces exactly one text unit per document — confirmed
   `text_units.parquet`'s `document_id` column survives to the final
   artifact, so `document_id == chunk_id` directly, no sidecar mapping table
   needed.
5. **Graph embeddings**: local search requires an entity-description
   embedding store graphrag builds itself at index time. Write a custom
   `LLMEmbedding` adapter (graphrag's own plug point, verified via
   `graphrag_llm.embedding.embedding_factory.register_embedding`) that
   delegates to the already-loaded `BgeM3Embedder` — zero marginal cost, zero
   new provider config, consistent with "graph build stays cheap beyond the
   one extraction cost." This is real adapter code against a non-trivial ABC
   (`graphrag_llm.embedding.embedding.LLMEmbedding` — constructor needs
   `model_config`, `tokenizer`, `metrics_store`, `rate_limiter`, `retrier`,
   `cache`), flagged as the fiddliest single piece of new code in this plan.
6. **Trace bookkeeping**: `drill_down`/`overview` reuse `kind='ask'` in the
   traces table (no schema change) and record the distinguishing fact in
   `arm` (`"drill_down"` / `"overview"` / `"hybrid-local"` / `"graph-global"`)
   — `arm` already exists for exactly this purpose.

## Open risk (not blocking, flagged for implementation time)

Global search's context is **community reports**, which have no page and can
never be citation targets directly (citation rule: summaries are breadth,
never a citation source). Resolving global search's citations means joining
the community reports graphrag's map-reduce actually used back to their
member entities' contributing text units (chunk ids) — the exact parquet
column path for this join needs a short spike against a real build (the
`apd` subject) before it's locked in; this is the single highest-uncertainty
piece of the plan. Everything else was verified directly against the
installed `graphrag==3.1.0` source.

## Implementation order

1. **`groundly/core/manifest.py`** — add `Graphrag.corpus_hash: str | None`.
   Additive, no `format_version` bump (matches decisions 15/18 precedent);
   zero interchange-compatibility effect since `bundle.py`'s `pin_matches`
   only compares the `Embedding` block.

2. **`groundly/core/store.py`** — add `SubjectStore.all_chunks()`
   (id, text, filename, page, heading_path) to feed the batch builder.
   Corpus hash itself is computed from `list_materials()`'s existing sha256
   column, no new method needed for that.

3. **`groundly/llm/graphrag_adapter.py`** (new) — the one place translating
   Groundly's `ProviderConfig` into graphrag's config primitives (keeps "LLM
   clients constructed only in `llm/`" true in spirit, same interpretation
   already implied by `llm/embeddings.py`/`llm/rerank.py`):
   - `completion_model_config()` → graphrag `ModelConfig` from
     `require_provider("extraction")`.
   - `Bgem3GraphEmbedding` — the `LLMEmbedding` adapter from decision 5,
     registered once via `register_embedding`.
   - `estimate_cost(total_chars) -> tuple[int, float | None]` — the heuristic
     from decision 2.

4. **`groundly/ingestion/graph.py`** (new) — the batch builder (ingestion
   writes stores, never serves queries — matches the architecture
   invariant):
   - `corpus_hash(store)`, `graph_is_stale(subj, store)`.
   - `build_graph(subj, store)` — `require_provider("extraction")` fail-fast,
     build the `input_documents` DataFrame from `all_chunks()`, construct
     `GraphRagConfig` rooted at `<subject>/graph/` (parquet output, LanceDB
     vector store at `graph/lancedb/` — confirmed shipped transitively with
     `graphrag`, no new dependency, already inside `bundle.py`'s export
     allowlist), `await build_index(...)`, then write
     `manifest.graphrag = Graphrag(version=..., extraction_model=...,
     corpus_hash=...)`. Wrap failures in a new `GraphBuildError` (matches the
     codebase's no-raw-traceback pattern).
   - Cost-estimate confirmation prompt (`typer.confirm`) stays in
     `cli/subjects.py`, not here — ingestion never does interactive I/O in
     this codebase (mirrors `pipeline.index_paths`' callback-only design).

5. **`groundly/cli/subjects.py`** — `index()` gets `--graph` and `--yes/-y`.
   After the existing per-file loop: if `graph/` exists and is stale, rebuild
   automatically; elif `--graph` and no `graph/` yet, build for the first
   time; else no graph action. Default zero-key path is untouched.

6. **`groundly/retrieval/graph.py`** — replace both stub classes with real
   `GraphLocalRetriever`/`GraphGlobalRetriever` (`GraphNotBuiltError` and its
   message stay, now genuinely conditional on `graph/` missing):
   - Local: `graphrag.api.query.local_search(...)`, map `text_units`'
     `document_id` straight to `chunk_id`, resolve via `store.chunk_details`
     — same `NodeWithScore` metadata shape (`chunk_id`/`filename`/`page`/
     `heading_path`) as `VectorRetriever`, so nothing downstream needs to
     special-case the graph arm.
   - Global: `graphrag.api.query.global_search(...)`, resolve per the open
     risk above; record which communities contributed (`self.path`) so
     `overview`'s "names its constituent communities" acceptance criterion
     (UC-12) is satisfiable.

7. **`groundly/agents/citations.py`** (new) — factor `ask.py`'s inline
   citation regex/hallucination-filter/lookup (currently `ask.py:20, 91-108`)
   into `resolve_citations(text, retrieved_chunk_ids, store)`, with
   `Citation`/`NoCitationsError` moving alongside it. No behavior change;
   `ask.py` becomes a thin caller.

8. **`groundly/agents/ask.py`** — arm-aware routing: `factoid`/`None` → vector
   only (current behavior, unchanged); `multi-hop` → `GraphLocalRetriever` +
   `VectorRetriever` fused via the *existing* `rrf()` from `vector.py`;
   `global` → `GraphGlobalRetriever` alone. If a graph retriever raises
   `GraphNotBuiltError`, degrade to vector-only rather than failing `ask()` —
   preserves "graph build stays skippable" for subjects without one. `arm` in
   the trace reflects what actually ran, not what the router asked for.

9. **`groundly/agents/prompts.py`** — no change needed for `ask()`'s fused
   path (graph nodes already share the vector arm's metadata shape). Add
   `assemble_overview(query, communities, nodes)` for `overview()`'s
   community-grouped layout, reusing existing constants — don't parameterize
   `assemble()` itself for a need only one caller has.

10. **`groundly/agents/study_modes.py`** (new) — `drill_down(subject, entity)`
    and `overview(subject, topic)`, each: require `graph/` to exist (raise
    `GraphNotBuiltError`, no vector degrade — this is an availability
    precondition per UC-12, not a routing decision) → retrieve via the
    matching graph retriever → `complete("chat", ...)` → refusal check →
    `resolve_citations` → trace (`kind='ask'`, `arm='drill_down'`/`'overview'`).
    `overview`'s result additionally carries `communities: list[dict]`.
    Once three call sites share the "assemble → complete → refusal-check →
    resolve_citations → trace" sequence (`ask`, `drill_down`, `overview`),
    extract it into one shared helper — don't pre-abstract before the third
    caller exists.

11. **`groundly/mcp/server.py`** — add `drill_down`/`overview` tools,
    following the exact `ask` tool's wrapper shape (`_subject_or_error`,
    lazy imports, `ToolError` mapping for `GraphNotBuiltError`/
    `ProviderNotConfiguredError`/`NoCitationsError`/`ChatUnreachableError`/
    `ModelDownloadError`). `overview`'s dict return includes `communities`.
    `list_subjects`'s existing `graph_built` field already covers the
    precondition these tools need surfaced — no change there.

## Tests

- `tests/ingestion/test_ingestion_graph.py` (new): `corpus_hash` stability/
  change-detection; `estimate_cost` priced vs. unpriced; `build_graph` with
  `build_index` monkeypatched to a fake recording its `input_documents` arg
  (chunk_id↔id mapping asserted, never a real graphrag run).
- `tests/retrival/test_retrieval_graph.py` (replaces the graph half of
  `test_retrieval_stubs.py`): stub `local_search`/`global_search`, assert both
  retrievers produce the shared `NodeWithScore` metadata contract; keep the
  `GraphNotBuiltError`-when-missing regression test alive against the real
  classes.
- `tests/agents/test_agents_ask.py` additions: router-label → arm-selection
  matrix (factoid/multi-hop/global), degrade-to-vector when graph isn't
  built — graph retrievers stubbed, never real graphrag.
- `tests/agents/test_agents_study_modes.py` (new): `drill_down`/`overview`
  happy path, `GraphNotBuiltError` precondition, citation reuse — same
  `stub_chat` fixture style as existing ask tests.
- `tests/core/test_manifest.py` additions: `corpus_hash` round-trip; old
  manifests without the field still parse.
- `tests/cli/test_cli_subjects.py` additions: `--graph` triggers a stubbed
  build; unchanged corpus doesn't rebuild; changed corpus auto-rebuilds
  without `--graph`; `--yes` skips confirmation — mirrors existing
  `remove --yes` test structure.
- `tests/mcp/test_mcp_server.py` additions: `drill_down`/`overview` tool
  calls, `graph_built: false` → `ToolError`, matching existing `ask`/`search`
  error-mapping tests.

No test touches a real graphrag pipeline or real cloud model — every
graphrag call site (`build_index`, `local_search`, `global_search`) is
monkeypatched at its import location, matching the existing discipline
around `complete`/`classify`/extractors/embedders.

## Verification (real corpus)

The pilot subject **`apd`** (Parallel & Distributed Algorithms, 188
materials / 1194 chunks) is already indexed locally. Its `config.toml` only
has `[providers.chat]` configured, pointing at a local LM Studio model — the
decision register explicitly forbids a small local model for graphrag
extraction (would invalidate the thesis's graph-vs-vector comparison).
**Before the real end-to-end run, the user needs to configure
`[providers.extraction]` with a real mid-tier cloud provider themselves** —
implementation does not do that (requires the user's API key).

Once configured:
1. `groundly index apd --graph` — should print the cost estimate over
   `apd`'s corpus, prompt, then build; inspect `~/.groundly/apd/graph/` for
   parquet artifacts + `lancedb/`, and `manifest.json` for populated
   `graphrag.*` fields.
2. `groundly index apd` again with no changes — confirm no rebuild fires.
3. Remove + re-add one material — confirm the existing "graph is now stale"
   message fires and the next `index` auto-rebuilds.
4. `groundly ask apd "<a multi-hop question>"` — confirm the trace's `arm`
   shows graph involvement (also needs `[providers.router]` configured, or
   test router-independent by calling `drill_down`/`overview` directly).
5. Call `drill_down`/`overview` via MCP against `apd`; confirm `overview`
   names its constituent communities and every citation resolves to a real
   chunk (UC-12's literal acceptance criterion) by spot-checking against
   `apd`'s actual course content.

## Critical files

- `groundly/retrieval/graph.py`
- `groundly/ingestion/graph.py` (new)
- `groundly/llm/graphrag_adapter.py` (new)
- `groundly/agents/ask.py`
- `groundly/agents/citations.py` (new)
- `groundly/agents/study_modes.py` (new)
- `groundly/core/manifest.py`
- `groundly/core/store.py`
- `groundly/cli/subjects.py`
- `groundly/mcp/server.py`
