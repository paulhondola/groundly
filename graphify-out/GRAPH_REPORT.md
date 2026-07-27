# Graph Report - .  (2026-07-27)

## Corpus Check
- 135 files · ~96,886 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1661 nodes · 3880 edges · 124 communities (99 shown, 25 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 337 edges (avg confidence: 0.7)
- Token cost: 368,518 input · 0 output

## Community Hubs (Navigation)
- GraphRAG Build Pipeline
- CLI Model & Config Verbs
- MCP Server & Retriever Tests
- Verified Deck Generation
- Generation Job Registry
- Extraction Worker Subprocess
- Graph Build Cost Estimate
- Query Router & Chat Client
- Subject Export/Import
- Subject Workspace Model
- Vector Baseline Retrieval
- Adaptive Agentic Retrieval
- Graph Build & Probe
- Store Retrieval Channels
- Ingestion Test Fixtures
- Trust-Layered Prompt Assembly
- GraphRAG Provider Adapter
- Graph Study Modes
- SQLite Store Tests
- Subject Init & Bundle Tests
- Ask Pipeline Arm Tests
- Slow Pipeline Integration Tests
- Extraction Subprocess Management
- Anki Deck Export
- Debug Logging System
- Extraction Prompt Budgeting
- Citation Resolution
- Deck/Question Store Schema
- GraphRAG Rate Limit Config
- Graph Staleness Fingerprint
- Project Conventions Overview
- CLI Ask Command Tests
- GraphRAG Workflow Error Counter
- Ingestion Pipeline Formats
- Cross-Encoder Reranker
- GraphRAG Extraction Probe
- Ask/Search CLI Verbs
- Model Install CLI
- Subject Filesystem Layout
- GraphRAG Builder Probe Tests
- BGE-M3 Embedder
- Cost & Privacy Decisions
- GraphRAG A/B Prompt Decisions
- GraphRAG Config & Metrics
- GraphRAG Embedding Adapter
- CLI Model Tests
- CLI App Shell
- Export-Deck CLI Verb
- Manifest Schema Models
- Manifest Sync
- Security Reviewer Checklist
- P1 Ingestion Review Findings
- Graph-Not-Built Error Handling
- Performance Review Findings
- Verifier Gate & Trust Layers
- Logging Design Review
- P6 Verified Cards Design
- P1 Correctness Findings
- Subject Init/List CLI
- Corpus Hash
- Extraction Prompt Errors
- Prompt Budget Scaling
- Chunk Gate & Provenance
- GraphRAG Metrics Trace
- MCP Serve & Host Wiring
- Subject Lifecycle Cost Print
- Interchange & Trust Boundary
- Zip-Slip Import Validation
- Chat Provider Error Handling
- Memory-Bounded Embed Encode
- Fake Graph Retriever Stubs
- Spec Guardian Checklist
- Grounding & Storage Rules
- LM Studio & Cost Range
- Service Tier Compatibility
- Retrieval Arms Overview
- Embedding Performance Fixes
- Subject Layout Tests
- Chunk Metadata Resolution
- Ask-Site Retriever Stubs
- Adversarial Reviewer Checklist
- Bundle Import Trust Boundary
- Shared Embedder Singleton
- Packaging Tests
- MCP HTTP Serve
- OCR Decisions
- Graph Context Window Decisions
- Retrieval Test Fixture
- Probe Failure Safety
- Hostile Document Risks
- Logging Test Reset
- CI/CD Workflows
- Pin Guard Script
- Study Memory Decision
- Bundled Extraction Prompt
- Cost Visibility Decision
- Server Frameworks Decision
- Package Init NullHandler
- Cost Convention
- Product Surfaces Convention
- Python Conventions
- Runtime Modes & Concurrency
- Release Process
- Offline Fetch Finding
- Trailing-Slash Redirect Finding
- ImageFormatOption Finding
- Init Command Surface
- Models Install Command
- Async Agent Loops Decision
- Docling RapidOCR Decision
- Python 3.11 via uv
- SQLite Storage Decision
- Static HTML Dashboard
- Traces Table Observability
- Typer+Rich CLI Decision
- Groundly Root Package

## God Nodes (most connected - your core abstractions)
1. `SQLiteSubjectStore` - 138 edges
2. `subject_dir()` - 100 edges
3. `Subject` - 98 edges
4. `build_graph()` - 62 edges
5. `_add_material()` - 44 edges
6. `mcp()` - 41 edges
7. `connect_progress()` - 39 edges
8. `stub_chat()` - 37 edges
9. `VectorRetriever` - 36 edges
10. `set_key()` - 34 edges

## Surprising Connections (you probably didn't know these)
- `Subprocess Runner Check` --semantically_similar_to--> `Exam Verifier (Identity of Generation)`  [INFERRED] [semantically similar]
  .claude/agents/security-reviewer.md → docs/architecture/agents.md
- `Verified Generation Feature` --semantically_similar_to--> `Exam Verifier (Identity of Generation)`  [INFERRED] [semantically similar]
  README.md → docs/architecture/agents.md
- `Point Groundly at LM Studio Config` --semantically_similar_to--> `LLM Provider Boundary (Hard Rule)`  [INFERRED] [semantically similar]
  docs/guides/lm-studio.md → .claude/rules/architecture.md
- `bge-m3 Embeddings Pin` --semantically_similar_to--> `manifest.json Interchange Contract`  [INFERRED] [semantically similar]
  .claude/rules/architecture.md → docs/architecture/data-model.md
- `Cross-Cutting Rules` --semantically_similar_to--> `Grounding Invariant`  [INFERRED] [semantically similar]
  docs/architecture/overview.md → .claude/rules/grounding-and-privacy.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Trust-Layered Prompt Assembly Model** — _claude_rules_grounding_and_privacy_trust_layers, docs_architecture_agents_trust_layers, docs_groundly_spec_trust_layers [INFERRED 0.90]
- **Verifier Gate Invariant Across Docs and Review Agents** — _claude_agents_spec_guardian_verifier_gate_check, _claude_rules_grounding_and_privacy_verification_gate, docs_architecture_agents_exam_verifier, docs_groundly_spec_verifier_gate_decision [INFERRED 0.90]
- **Cost-Estimate-Before-Spending Pattern** — _claude_rules_conventions_cost_estimate_transparency, docs_guides_graphrag_provider_cost_range, docs_groundly_spec_decision_23_cost_range, docs_architecture_agents_latency_classes [INFERRED 0.85]
- **P1 Ingestion Pipeline Spec + Two Review Rounds** — docs_superpowers_specs_2026_07_16_p1_ingestion_pipeline_pipeline_module, docs_superpowers_reviews_2026_07_16_p1_ingestion_pipeline_review_f1_integrity_error, docs_superpowers_reviews_2026_07_16_p1_ingestion_pipeline_round2_review_f1_remove_collision [INFERRED 0.80]
- **MCP Tool Surface Growing Across P4/P5/P6** — docs_superpowers_specs_2026_07_18_mcp_skeleton_design_tools_surface, docs_superpowers_specs_2026_07_24_p5_graphrag_arm_design_study_modes_tools, docs_superpowers_specs_2026_07_25_p6_verified_cards_design_thick_door [INFERRED 0.80]
- **Course-Tuned Extraction Prompt: Design, Artifact, and A/B Measurement** — docs_superpowers_specs_2026_07_26_lean_extraction_prompt_design_bundled_prompt, groundly_prompts_extract_graph_prompt, docs_superpowers_reviews_2026_07_26_lean_prompt_ab_arm_b_course_tuned_prompt [EXTRACTED 0.90]

## Communities (124 total, 25 thin omitted)

### Community 0 - "GraphRAG Build Pipeline"
Cohesion: 0.05
Nodes (74): Graphrag, subject_dir(), GraphBuildError, Exception, Drop the previous build's outputs, and the manifest's claim to a graph, before a, Wraps any graphrag indexing failure — no raw traceback ever surfaces., _reset_graph_artifacts(), FileResult (+66 more)

### Community 1 - "CLI Model & Config Verbs"
Cohesion: 0.06
Nodes (72): Context, config(), callback, Embedding model management verbs, plus the still-stubbed config verbs (small; no, Show the config file path and effective values per call class (keys masked)., _coerce(), config_path(), ConfigKeyError (+64 more)

### Community 2 - "MCP Server & Retriever Tests"
Cohesion: 0.06
Nodes (54): mcp(), command, Serve the groundly MCP tools (list_subjects/search/ask/get_page) over stdio., _configure_chat(), _FakeGraphGlobalRetriever, _FakeGraphLocalRetriever, _NearEmbedder, _PassthroughReranker (+46 more)

### Community 3 - "Verified Deck Generation"
Cohesion: 0.09
Nodes (44): CardOutcome, estimate_generation(), generate_deck_job(), _parse_cards(), Deck building: the two doors through the one verifier gate (P6 slice 1 design do, Model reply -> card candidates. Tolerant of code fences/prose around the JSON, The thick door's job body (runs on a jobs.py thread): retrieve topic context, Verify every card and store the ones that pass into `deck`. Zero-key: the     ve (+36 more)

### Community 4 - "Generation Job Registry"
Cohesion: 0.07
Nodes (46): get_job(), Job, In-memory generation job registry (P6 slice 1 design doc): `generate_*` MCP tool, Register a job and run `fn` on a daemon thread behind the generation lock., start_job(), ask(), CardIn, _citation_uri() (+38 more)

### Community 5 - "Extraction Worker Subprocess"
Cohesion: 0.08
Nodes (42): _bge_m3_tokenizer(), _extract_docling(), _extract_plain_text(), _first_frame(), main(), _model_step(), Path, Extraction worker — runs as `python -m groundly.ingestion.extract_worker <in> <o (+34 more)

### Community 6 - "Graph Build Cost Estimate"
Cohesion: 0.09
Nodes (36): estimate_cost(), Rough heuristic graph-build cost estimate: no tokenizer, no LLM call. Uses     `, home(), _priced(), fixture, groundly/llm/graphrag_adapter.py: the one place translating Groundly's provider, An extraction provider with both manual prices set. Both are required for the, A half-set override would produce a range whose upper bound silently omits     o (+28 more)

### Community 7 - "Query Router & Chat Client"
Cohesion: 0.12
Nodes (30): classify(), Query router — arm 3's brain and the cost gate (docs/architecture/retrieval.md)., ChatFn, complete(), Protocol, Chat completion client: litellm.completion() against any OpenAI-compatible endpo, _configure_router(), home() (+22 more)

### Community 8 - "Subject Export/Import"
Cohesion: 0.10
Nodes (32): export(), import_(), Argument, command, help, Option, Path, Share a subject as a portable bundle: export / import (UC-30). (+24 more)

### Community 9 - "Subject Workspace Model"
Cohesion: 0.14
Nodes (24): Path, Represents a Groundly subject workspace with its directories, database files, an, Subject, IngestionPipeline, Orchestrates indexing of documents: extraction, embedding, and storage., stub_extractor(), Fast pipeline-logic tests using classes and interfaces directly, avoiding heavy, Another process recording the same failing content between our hash check and (+16 more)

### Community 10 - "Vector Baseline Retrieval"
Cohesion: 0.12
Nodes (26): BaseRetriever, NodeWithScore, QueryBundle, Arm 1 (vector baseline): dense + learned-sparse + BM25, fused by reciprocal rank, The raw retrieval path: query -> ranked chunks, no LLM call, no provider     nee, Reciprocal rank fusion over already-ranked (best-first) id lists. Pure     funct, dense + sparse + BM25 -> RRF -> optional cross-encoder rerank -> top context_k., rrf() (+18 more)

### Community 11 - "Adaptive Agentic Retrieval"
Cohesion: 0.11
Nodes (25): DataFrame, AdaptiveRetriever, BaseRetriever, NodeWithScore, QueryBundle, Arm 4 (adaptive agentic): retrieve -> self-grade -> escalate/rewrite, bounded at, GraphGlobalRetriever, GraphLocalRetriever (+17 more)

### Community 12 - "Graph Build & Probe"
Cohesion: 0.10
Nodes (30): build_graph(), GraphBuildResult, What the build actually managed to index. `failed` is chunks graphrag dropped, Batch build graphrag's graph for a subject (parquet artifacts + a LanceDB     ve, _configure_extraction(), Regression: the probe must exercise the same prompt the build sends, with the, architecture.md: every LLM call records tokens + cost. The probe makes *two* rea, `verbose=True` would raise graphrag's loggers to DEBUG, and graphrag logs     `s (+22 more)

### Community 13 - "Store Retrieval Channels"
Cohesion: 0.09
Nodes (12): sha256 -> status, for hash-skip (indexed) and retry (extraction_failed)., Match by exact filename or sha256 prefix (the disambiguator)., One transaction. FTS syncs via the chunks_ad trigger; sparse_terms via FK, Exact KNN over the dense channel (sqlite-vec brute force). Chunk ids         nea, Learned-sparse channel: sum of weight * query_weight per chunk, best-first., FTS5 BM25 channel. Each term is individually double-quoted before joining, Resolve chunk ids to citation targets: document + page + heading path., Every chunk in the subject, resolved to its citation target — feeds the (+4 more)

### Community 14 - "Ingestion Test Fixtures"
Cohesion: 0.10
Nodes (19): ChunkData, RuntimeError, course(), _fixed_console_width(), fixture, Shared fixtures for the ingestion pipeline/worker tests. Pytest auto-discovers f, Scripted `ChatFn`: replies pop in call order (last reply repeats once exhausted), An initialized subject with hand-built orthogonal dense vectors and distinct (+11 more)

### Community 15 - "Trust-Layered Prompt Assembly"
Cohesion: 0.16
Nodes (22): assemble(), assemble_cards(), assemble_cards_retry(), assemble_overview(), _escape(), NodeWithScore, Trust-layered prompt assembly for the ask pipeline (docs/architecture/agents.md), Regeneration round: only the rejected cards, each with its machine-readable (+14 more)

### Community 16 - "GraphRAG Provider Adapter"
Cohesion: 0.10
Nodes (24): BuildEstimate, extraction_fingerprint(), extraction_prices(), _litellm_prices(), _litellm_version(), metered_usage(), MeteredUsage, ModelPrices (+16 more)

### Community 17 - "Graph Study Modes"
Cohesion: 0.19
Nodes (22): AskResult, drill_down(), overview(), OverviewResult, Graph-only study modes (UC-12): `drill_down` (entity-anchored local search) and, connect_progress(), Open progress.db, creating it (and the traces/verifications tables) if missing., record_trace() (+14 more)

### Community 18 - "SQLite Store Tests"
Cohesion: 0.15
Nodes (21): A subject's store.db: connection lifecycle + all reads/writes for materials,, SQLiteSubjectStore, _add_material(), add_indexed consumes a one-shot (dense, sparse) generator aligned with chunks,, test_add_indexed_streams_vectors_from_lazy_iterable(), test_all_chunks_empty_store_returns_empty(), test_all_chunks_returns_every_chunk_joined_with_material(), test_bm25_search_finds_repeated_term_first() (+13 more)

### Community 19 - "Subject Init & Bundle Tests"
Cohesion: 0.24
Nodes (23): init_subject(), Create ~/.groundly/<name>/ (manifest, materials/, store.db, progress.db).      R, UC-30 export/import. Seeds store.db directly via SQLiteSubjectStore.add_indexed, A materials/ file with no `materials` row (e.g. copied just before a transient, graph/cache/ (graphrag's own incremental-rebuild cache) and graph/logs/     (ope, Stamp a fake graph/ dir + manifest.graphrag for `name`, recorded against either, _rewrite_zip_manifest(), _row_counts() (+15 more)

### Community 20 - "Ask Pipeline Arm Tests"
Cohesion: 0.32
Nodes (22): ask(), _configure_chat(), _near_embedder(), _no_vector_retrieval(), groundly/agents/ask.py: router -> retrieval -> assemble -> chat -> citation reso, Fails the test loudly if the vector arm is ever asked to retrieve — used to, The single highest-value log line in the debug-logging design: today the     rou, test_ask_empty_store_refuses_without_llm_call() (+14 more)

### Community 21 - "Slow Pipeline Integration Tests"
Cohesion: 0.23
Nodes (22): index_paths(), connect(), stub_embedder(), slow, Pipeline tests that invoke the real extract worker (bge-m3 tokenizer download on, A same-content sibling after a transient embed failure must retry, not be     re, A tokenizer/model load failure in the worker is environmental, not a bad documen, The extract worker runs with cwd=tempdir; a relative CLI path must still     res (+14 more)

### Community 22 - "Extraction Subprocess Management"
Cohesion: 0.14
Nodes (17): Extraction, ExtractionFailure, ModelUnavailable, Exception, Path, Parent side of extraction: spawn the worker, enforce a wall-clock timeout, map f, reason is user-facing and names the specific cause., The worker couldn't load its model (uncached + offline, HF rate-limit, missing (+9 more)

### Community 23 - "Anki Deck Export"
Cohesion: 0.16
Nodes (18): _deck_id(), export_deck(), Path, Verified decks -> Anki .apkg via genanki (P6 slice 1; decision 6: Anki owns dail, Write `deck_name` as an .apkg. Default target: <subject>/exports/<deck>.apkg —, _source_line(), check_deck_name(), Deck names become file names (exports/<deck>.apkg) — the one host-controlled (+10 more)

### Community 24 - "Debug Logging System"
Cohesion: 0.15
Nodes (18): Debug logging: one stderr handler on the ROOT logger, never a log file. Log line, Attach one stderr handler to the ROOT logger; return True if logging is on     (, setup_logging(), groundly/core/logs.py: one stderr handler on the root logger, off by default., The load-bearing test: graphrag's `init_loggers` clears handlers on the     `gra, A subprocess, because the regression is *ordering*, not the values.      litellm, `logging.lastResort` is a WARNING-level stderr handler that fires whenever no, The NullHandler must not swallow anything once logging is enabled — it     suppr (+10 more)

### Community 25 - "Extraction Prompt Budgeting"
Cohesion: 0.11
Nodes (20): _bundled_prompt_text(), _max_output_tokens_per_call(), _preamble_tokens(), The extraction preamble, sent with every single chunk. Measured off the prompt, The room an extraction call has left to answer in, once its own prompt is in the, The saving only reaches the student if the confirmation gate quotes it. Pricing, estimate_cost feeds the cost line, not the build. It must degrade to a number, graph_extractor._process_document formats with exactly these two keys. A prompt (+12 more)

### Community 26 - "Citation Resolution"
Cohesion: 0.22
Nodes (14): The ask pipeline — the one shared function exposed identically as `groundly ask`, Citation, NoCitationsError, Exception, Citation resolution shared by every agent call site that turns a model's cited r, Every cited chunk id in the model's response was hallucinated (not among the, resolve_citations(), _FakeStore (+6 more)

### Community 27 - "Deck/Question Store Schema"
Cohesion: 0.23
Nodes (17): connect(), create_store(), Path, db(), _add_material_with_chunk(), store.db v2: decks/questions/question_citations, the v1 -> v2 migration, and the, test_add_verified_card_bogus_chunk_id_rolls_back_everything(), test_add_verified_card_stores_question_and_citations() (+9 more)

### Community 28 - "GraphRAG Rate Limit Config"
Cohesion: 0.12
Nodes (19): completion_model_config(), _rate_limit_config(), Build graphrag's ModelConfig from Groundly's `extraction` provider. Fails fast, Always on. graphrag fires extraction concurrently across the whole corpus and, Only when the provider's limits have been declared in config.toml. There is no, _retry_config(), RateLimitConfig, RetryConfig (+11 more)

### Community 29 - "Graph Staleness Fingerprint"
Cohesion: 0.16
Nodes (18): current_extraction_fingerprint(), graph_is_stale(), The fingerprint a build started right now would record. Raises     ExtractionPro, Why the recorded graph no longer describes this subject, or None if it still doe, extraction_entity_types(), `graph.entity_types`, split and stripped. Stored comma-separated (see     core/c, Stamp the manifest the way a successful build_graph does — both the corpus hash, The load-bearing case: the corpus is untouched, but the graph was built looking (+10 more)

### Community 30 - "Project Conventions Overview"
Cohesion: 0.15
Nodes (17): Exactly Three Frameworks Rule, litellm as Shared Provider Client, Docs Are Source of Truth Convention, /decision Skill Workflow, /implement-uc Skill Workflow, Groundly Project CLAUDE.md Overview, Working Rules, Ask Pipeline (Interactive Agent) (+9 more)

### Community 31 - "CLI Ask Command Tests"
Cohesion: 0.18
Nodes (12): ModelDownloadError, Exception, snapshot_download failed fetching bge-m3 (network/HF error); callers map this, _configure_chat(), _NearEmbedder, CLI: `ask` (enforced, cited) and `search` (raw, zero-key) verbs., test_ask_chat_unreachable_error_fails_cleanly(), test_ask_model_download_error_fails_cleanly() (+4 more)

### Community 32 - "GraphRAG Workflow Error Counter"
Cohesion: 0.12
Nodes (7): BaseException, _ProgressCallbacks, Counts per-item LLM failures for one graphrag stage, which reach us no other way, Translates graphrag's workflow lifecycle into `on_event(description,     complet, _WorkflowErrorCounter, LogRecord, NoopWorkflowCallbacks

### Community 33 - "Ingestion Pipeline Formats"
Cohesion: 0.21
Nodes (12): Format tables shared by the pipeline and the extraction worker. Stdlib-only: ext, _copy_to_materials(), _ignored(), _iter_files(), _load_ignore_patterns(), Path, The index pipeline (UC-01): hash-skip idempotent, per-file transactions, the run, Original files are the citation targets; they ship in exports. (+4 more)

### Community 34 - "Cross-Encoder Reranker"
Cohesion: 0.17
Nodes (8): BgeReranker, Protocol, Cross-encoder reranker for the vector arm's fused candidate pool. Lives in llm/, Reranker, groundly/llm/rerank.py: lazy cross-encoder reranker. Fast tests only check lazin, test_bge_reranker_does_not_load_model_on_construction(), test_bge_reranker_load_wraps_construction_failure_in_model_download_error(), test_bge_reranker_satisfies_the_reranker_protocol()

### Community 35 - "GraphRAG Extraction Probe"
Cohesion: 0.15
Nodes (13): GraphRagConfig, _probe_call(), _probe_extraction(), Send one real extraction prompt before committing to the whole corpus.      Enti, Run one probe call, trace it either way, and wrap any failure as a named     Gra, _GraphArtifacts, _GraphRetrieverBase, _load_artifacts() (+5 more)

### Community 36 - "Ask/Search CLI Verbs"
Cohesion: 0.27
Nodes (14): _fail(), _store_checked(), _subject_checked(), ask(), Argument, command, help, Option (+6 more)

### Community 37 - "Model Install CLI"
Cohesion: 0.19
Nodes (15): config_set(), install(), Argument, command, help, Option, Remove the bge-m3 embedding model from the local Hugging Face cache., Set a provider or settings value in ~/.groundly/config.toml. (+7 more)

### Community 38 - "Subject Filesystem Layout"
Cohesion: 0.20
Nodes (13): discover_subjects(), groundly_home(), Path, Filesystem layout: ~/.groundly/ (GROUNDLY_HOME overrides), one dir per subject., Subject names become path components and MCP identifiers., validate_subject_name(), test_import_onto_leftover_dir_without_manifest_fails_cleanly(), parametrize (+5 more)

### Community 39 - "GraphRAG Builder Probe Tests"
Cohesion: 0.20
Nodes (13): ChatResult, ChatResultStub, home(), fixture, groundly/ingestion/graph.py: the graphrag batch builder. `build_index` is always, Criterion: a bad prompt path is a named cause, not a graphrag internal, and it, build_graph probes the provider before running the pipeline — a real extraction, The probe must pass the *same object* community_reports_extractor passes, so lit (+5 more)

### Community 40 - "BGE-M3 Embedder"
Cohesion: 0.17
Nodes (11): BgeM3Embedder, bge-m3 embedding: dense (1024-d, normalized) + learned sparse from one forward p, Delete bge-m3 from the local Hugging Face cache. Returns True if anything was re, remove_cached(), groundly/llm/embeddings.py: lazy bge-m3 embedder. Construction-failure wrapping, test_bge_m3_load_wraps_construction_failure_in_model_download_error(), Real-model checks — excluded by default (pyproject addopts); run: pytest -m slow, encode_stream is the memory-bounded index path: it yields (dense, sparse) per (+3 more)

### Community 41 - "Cost & Privacy Decisions"
Cohesion: 0.15
Nodes (14): Graph Build Cost (Single-Digit Dollars/Subject), Sharing Amortizes Cost, Zero-Key Operation Principle, Privacy Boundary Is a File (progress.db), llm/chat.py litellm.completion() Swap, Cost Precedence: Manual Price vs. litellm Auto-Cost, litellm as graphrag's Transitive Dependency, LITELLM_LOCAL_MODEL_COST_MAP Privacy Hazard (+6 more)

### Community 42 - "GraphRAG A/B Prompt Decisions"
Cohesion: 0.15
Nodes (14): Arm A: graphrag Default Prompt (Baseline), Arm B: Bundled Course-Tuned Prompt, A/B Pass Conditions (Criteria 2-4), document_id == chunk_id Citation Mapping, Global Search Citation Resolution (Open Risk), --graph Flag + Corpus-Hash Auto-Rebuild Trigger, P5 Scope: Local + Global GraphRAG Search, Acceptance Criteria (Cost Floor, Quality Floor) (+6 more)

### Community 43 - "GraphRAG Config & Metrics"
Cohesion: 0.14
Nodes (12): _build_config(), _extraction_prompt(), Path, `resolve_extraction_prompt` with its named error mapped onto this module's, so t, graphrag's config, rooted entirely under <subject>/graph/ — nothing touches, graphrag_llm's own in-memory metrics store, plus a handle on the instance., _ReadableMetricsStore, MemoryMetricsStore (+4 more)

### Community 44 - "GraphRAG Embedding Adapter"
Cohesion: 0.18
Nodes (8): Bgem3GraphEmbedding, graphrag's entity-description embedding store, delegated to the already-loaded, LLMEmbedding, Records the texts it was asked to encode; returns deterministic dense vectors, StubEmbedder, test_embedding_async_delegates_the_same_way(), test_embedding_delegates_to_embedder_and_returns_dense_only(), test_metrics_store_and_tokenizer_are_passthrough()

### Community 45 - "CLI Model Tests"
Cohesion: 0.14
Nodes (3): home(), fixture, CLI: model management verbs and the config verbs.

### Community 46 - "CLI App Shell"
Cohesion: 0.17
Nodes (9): main(), _print_version(), callback, help, Option, App objects and shared helpers for the Groundly CLI. Imports no verb modules (su, Groundly — local-first course knowledge bases for AI agents., is_eager (+1 more)

### Community 47 - "Export-Deck CLI Verb"
Cohesion: 0.15
Nodes (10): export_deck(), Argument, command, help, Option, Path, Deck export verb: `groundly export-deck SUBJECT DECK [--out PATH]` — the batch h, Export a verified flashcard deck as an Anki .apkg (citations on card backs). (+2 more)

### Community 48 - "Manifest Schema Models"
Cohesion: 0.22
Nodes (9): Chunking, Counts, Ocr, BaseModel, manifest.json — the interchange contract (docs/architecture/data-model.md).  The, create_progress(), store.db / progress.db access. Schema versioned via PRAGMA user_version — no mig, Subject lifecycle: create the on-disk layout that everything else assumes. (+1 more)

### Community 49 - "Manifest Sync"
Cohesion: 0.23
Nodes (9): Manifest, Connection, Path, Keep manifest counts in sync after every mutation (UC-03 acceptance)., sync_counts(), groundly/core/manifest.py: manifest.json field round-trips (docs/architecture/da, test_graphrag_corpus_hash_defaults_to_none(), test_manifest_round_trips_corpus_hash() (+1 more)

### Community 50 - "Security Reviewer Checklist"
Cohesion: 0.17
Nodes (12): Security Reviewer Agent, Local Servers Check, Privacy / Export Boundary Check, Prompt Injection Check, Subprocess Runner Check, Trust Layers Check, serve Binds 127.0.0.1 Only, Feature-Branch Workflow Convention (+4 more)

### Community 51 - "P1 Ingestion Review Findings"
Cohesion: 0.18
Nodes (12): F6: No Test for Page Attribution / Heading Paths, F9: Corpus-Hash Graph Offer Missing, Carried-Over Findings From Round 1, F4: Pipeline Test Suite Excluded From CI, F1: Multi-Frame TIFF/WEBP Breaks Page-1 Attribution, groundly config Command Surface, groundly index Command Surface, groundly remove Command Surface (+4 more)

### Community 52 - "Graph-Not-Built Error Handling"
Cohesion: 0.17
Nodes (6): GraphNotBuiltError, Exception, Raised by graph arms whenever `<subject>/graph/` doesn't exist yet., _NotBuiltRetriever, Stubs either graph retriever to simulate a subject with no graph built yet., _NotBuiltRetriever

### Community 53 - "Performance Review Findings"
Cohesion: 0.20
Nodes (11): Performance Specialist Agent, Bounded Buffers Note, No Chunk-Level Streaming Issue, Concurrent Peaks Issue, fp32 Model Weights Residency, Full-JSON Parse Issue, Ingestion to Embedding to Store Path, Vector Dtype Blow-up (+3 more)

### Community 54 - "Verifier Gate & Trust Layers"
Cohesion: 0.20
Nodes (11): Verifier Gate Check, Verification Gate Invariant, Exam Verifier (Identity of Generation), Gap Analysis / Study Planning (Not an Agent), Thick Generation Path, Thin Generation Path, Latency Classes / Job Serialization, Request Flows (Latency Classes) (+3 more)

### Community 55 - "Logging Design Review"
Cohesion: 0.18
Nodes (11): Debug Logs Stderr-Only, Never a File, graphrag's Own Log File Exception, ask.py Arm-Aware Routing + Degrade-to-Vector, groundly/ingestion/graph.py build_graph, drill_down / overview MCP Tools, drill_down/overview Trace Bookkeeping (kind='ask'), Review Amendments (NullHandler, verbose removal, key-safe config), Graph Build Progress Bar + Verbose Callbacks (+3 more)

### Community 56 - "P6 Verified Cards Design"
Cohesion: 0.24
Nodes (11): Subprocess Execution (Verifier + Challenges), export_deck via genanki (.apkg), agents/jobs.py In-Memory Job Queue, store.db Schema v2 (decks/questions/question_citations), generate_deck Thick Door (Two-Phase Confirm), submit_cards Thin Door, agents/verifier.py verify_card Gate, genanki -> .apkg Flashcards (+3 more)

### Community 57 - "P1 Correctness Findings"
Cohesion: 0.18
Nodes (11): F1: Concurrent Index IntegrityError Crash, F5: extraction_failed Not Terminal (Infinite Retry), F1: remove Deletes Wrong Material's File, groundly/ingestion/embed.py BgeM3Embedder, groundly/ingestion/extract_worker.py, groundly/core/manifest.py Constants, groundly/ingestion/pipeline.py index_paths, groundly/core/store.py Schema (P1) (+3 more)

### Community 58 - "Subject Init/List CLI"
Cohesion: 0.25
Nodes (11): index(), init(), list_(), Argument, command, help, Option, Path (+3 more)

### Community 59 - "Corpus Hash"
Cohesion: 0.27
Nodes (11): corpus_hash(), sha256 over the subject's indexed materials' sha256s, sorted — stable across, _add_material(), Guards the exact formula: sha256("\\n".join(sorted(sha256s))) — insertion     or, graphrag writes into an existing graph/ without clearing it, so before the reset, test_corpus_hash_changes_when_material_added(), test_corpus_hash_ignores_extraction_failed_materials(), test_corpus_hash_matches_sorted_sha256_formula() (+3 more)

### Community 60 - "Extraction Prompt Errors"
Cohesion: 0.20
Nodes (11): ExtractionPromptError, Exception, Path, A configured `graph.extraction_prompt` that cannot be used — missing, unreadable, Yield `(path, text)` for the entity-extraction prompt the build will send., resolve_extraction_prompt(), _validate_prompt(), The silent-failure case: graphrag does not substitute these, so .format() raises (+3 more)

### Community 61 - "Prompt Budget Scaling"
Cohesion: 0.20
Nodes (11): prompt_budgets(), PromptBudgets, graphrag's per-stage prompt sizing, scaled to the extraction model's context., Scale graphrag's stage budgets to the model actually configured.      graphrag's, parametrize, Every stage's input + output reserve has to fit the model's actual context —, This only ever scales down: a big context reproduces stock graphrag., The gleaning round replays prompt + chunk + the model's whole first answer, (+3 more)

### Community 62 - "Chunk Gate & Provenance"
Cohesion: 0.18
Nodes (11): _add_chunks(), The gates and the provenance fields share one write, so a graph that was refused, graphrag catches extraction errors per text unit and carries on, so they never, graphrag emits TWO ERROR records per failed text unit under the same package, n indexed materials, one chunk each — the failure gates work on chunk counts., The gate refuses to *record* the build but leaves partial parquet on disk so, test_a_few_swallowed_failures_complete_the_build_and_are_reported(), test_one_failed_chunk_counts_once_not_twice() (+3 more)

### Community 63 - "GraphRAG Metrics Trace"
Cohesion: 0.20
Nodes (11): _build_trace(), _build_with_metrics(), Drive graphrag's *real* metrics path: build the completion model from the config, The trace used to store the pre-build heuristic as if it were metered. graphrag, Cached responses are counted in graphrag's token totals but were never paid for,, A build that metered nothing is not a reason to fail one that otherwise     succ, graphrag registers metrics stores as singletons, so without a reset the second, test_build_graph_does_not_inherit_a_previous_builds_usage() (+3 more)

### Community 64 - "MCP Serve & Host Wiring"
Cohesion: 0.22
Nodes (10): groundly serve (Streamable HTTP, loopback-only), Honest Footprint Tradeoff, MCP Host Wiring (stdio), install.sh / uv tool install, Local Servers (127.0.0.1-only), F1: DNS-Rebinding / Origin Protection Off, F2: Smoke Test Never Exercises serve.py, Citation Resource Template groundly://subject/file#page=N (+2 more)

### Community 65 - "Subject Lifecycle Cost Print"
Cohesion: 0.29
Nodes (9): _maybe_build_graph(), _print_actual_spend(), _print_cost_estimate(), Subject lifecycle verbs: init, index, list, remove., Corpus state is final for this run — decide whether the graph needs a     (re)bu, Two decimals reads as money; below a cent it reads as zero, which is worse than, The spend gate (conventions.md: print cost estimates before spending the     stu, What the build actually cost, metered by graphrag's own usage aggregates rather (+1 more)

### Community 66 - "Interchange & Trust Boundary"
Cohesion: 0.25
Nodes (9): Import Trust Boundary Check, Observability Traces, Export / Import Process, Integrity Rules as Constraints, manifest.json Interchange Contract, progress.db (Never Exported), store.db (Exported), Evaluation Protocol (+1 more)

### Community 67 - "Zip-Slip Import Validation"
Cohesion: 0.33
Nodes (9): Zip-slip gate: reject, never sanitize. Also blocks anything outside the     expo, validate_entries(), parametrize, test_hostile_import_leaves_existing_subject_untouched(), test_validate_entries_rejects_oversized_declared_total(), test_validate_entries_rejects_path_escapes_and_smuggled_entries(), test_validate_entries_rejects_symlink(), test_validate_entries_rejects_windows_style_paths() (+1 more)

### Community 68 - "Chat Provider Error Handling"
Cohesion: 0.22
Nodes (9): ChatUnreachableError, Exception, The configured chat provider could not be reached (network/HTTP error)., A provider can answer plain completions and still reject response_format — every, The reset runs *after* the probe: a misconfigured provider must not destroy a, test_a_failed_probe_leaves_the_existing_graph_intact(), test_probe_checks_structured_output_separately_and_says_so(), test_probe_failure_names_the_cause_and_never_starts_the_pipeline() (+1 more)

### Community 69 - "Memory-Bounded Embed Encode"
Cohesion: 0.31
Nodes (4): Embedder, Protocol, Memory-bounded index path: yield (dense_row, sparse) per text, running the, SparseWeights

### Community 70 - "Fake Graph Retriever Stubs"
Cohesion: 0.22
Nodes (5): _FakeGraphGlobalRetriever, _FakeGraphLocalRetriever, _graph_node(), Stubs `GraphLocalRetriever` at ask.py's import site — always returns chunk 2,, Stubs `GraphGlobalRetriever` at ask.py's import site.

### Community 71 - "Spec Guardian Checklist"
Cohesion: 0.29
Nodes (8): Spec Guardian Agent, Doc Drift Check, Module Layering Check, Provider Boundary Check, LLM Provider Boundary (Hard Rule), Module Boundaries Invariant, Module Dependency Rules, Decision 18: Operational Settings Configurable

### Community 72 - "Grounding & Storage Rules"
Cohesion: 0.25
Nodes (8): Grounding Check, Storage & Concurrency Check, Lazy Model Loading Rule, Storage & Concurrency Rules, Grounding Invariant, Cross-Cutting Rules, Non-Functional Requirements (Section 6), Enforced Citations Feature

### Community 73 - "LM Studio & Cost Range"
Cohesion: 0.29
Nodes (8): Decision 23: Graph Cost Estimate Becomes a Range, Graph Build Cost Range Estimate, Extraction Needs Mid-Tier Cloud Model, Point Groundly at LM Studio Config, LM Studio Setup Guide, Cross-Host Progress Feature, Groundly Pitch, Verified Generation Feature

### Community 74 - "Service Tier Compatibility"
Cohesion: 0.25
Nodes (8): allow_nonstandard_service_tier(), Widen `graphrag_llm.LLMCompletionResponse.service_tier` from OpenAI's literal, graphrag_llm types service_tier with OpenAI's exact literal set and builds its, Called on every build and every graph query, so it must be cheap to repeat., Called on every build *and every graph query*. `str | None` builds a fresh     t, test_allow_nonstandard_service_tier_accepts_groqs_value(), test_allow_nonstandard_service_tier_is_idempotent(), test_allow_nonstandard_service_tier_rebuilds_the_model_only_once()

### Community 75 - "Retrieval Arms Overview"
Cohesion: 0.38
Nodes (7): Arm 4: Adaptive Agentic Retrieval, Dual-Pipeline Confound, Fusion + Citation Rule, Arm 2: Pure GraphRAG, Arm 3: Query Router (Cost Gate), Arm 1: Vector Baseline, Hybrid Retrieval Strategy (Section 5)

### Community 76 - "Embedding Performance Fixes"
Cohesion: 0.29
Nodes (7): Finding 1: Boxed list[float] 8x Overhead, Finding 2: fp32 Resident Model Floor / fp16 Fix, Findings 3+4: Unbounded Per-Document Encode/Buffer, Resolution: Streaming Encode + fp16 (Flat 5GB Peak), Bgem3GraphEmbedding LLMEmbedding Adapter, bge-m3 Local Embeddings (Pinned incl. hf_revision), bge-reranker-v2-m3 (Default ON)

### Community 77 - "Subject Layout Tests"
Cohesion: 0.29
Nodes (4): Create subject layout (~/.groundly/<name>/).          Returns True if created, F, test_check_counts_refuses_newer_schema(), test_check_counts_rejects_mismatched_rows(), test_read_manifest_rejects_newer_format_version()

### Community 78 - "Chunk Metadata Resolution"
Cohesion: 0.48
Nodes (4): _nodes_from_chunk_ids(), NodeWithScore, QueryBundle, Resolve chunk ids to the shared metadata contract, in the given order (best

### Community 79 - "Ask-Site Retriever Stubs"
Cohesion: 0.29
Nodes (3): _FakeGraphGlobalRetriever, _FakeGraphLocalRetriever, _graph_node()

### Community 80 - "Adversarial Reviewer Checklist"
Cohesion: 0.33
Nodes (6): Adversarial Reviewer Agent, Correctness Review Priority, Edge Cases Review Priority, Lying Tests Review Priority, Adversarial Review File Format, Silent Failures Review Priority

### Community 81 - "Bundle Import Trust Boundary"
Cohesion: 0.33
Nodes (6): Import Trust Boundary, Imported Parquet Decompression Bomb (Residual Risk), Malicious Bundle Zip Bomb (Mitigated), groundly/core/bundle.py (Export/Import), groundly/cli/sharing.py (export/import verbs), test_bundle.py Acceptance-Criteria Test Plan

### Community 82 - "Shared Embedder Singleton"
Cohesion: 0.33
Nodes (4): Process-level singleton: one resident bge-m3 model shared by every production, shared_embedder(), One resident bge-m3 model shared by every default production call site, not a, test_shared_embedder_is_a_process_singleton_used_by_vector_retriever_default()

### Community 83 - "Packaging Tests"
Cohesion: 0.33
Nodes (5): slow, Packaging assumptions that only break once installed.  `groundly/prompts/extract, The runtime lookup graphrag_adapter uses. Runs everywhere (no wheel build), so a, test_bundled_extraction_prompt_ships_in_the_wheel(), test_bundled_prompt_resolves_as_a_package_resource()

### Community 84 - "MCP HTTP Serve"
Cohesion: 0.40
Nodes (4): command, `groundly serve`: run the FastMCP tool surface over Streamable HTTP for hosts th, Serve the groundly MCP tools over Streamable HTTP on 127.0.0.1., serve()

### Community 85 - "OCR Decisions"
Cohesion: 0.67
Nodes (4): Decision 14: Pivot #3 Reversed - OCR Enabled, Decision 15: Per-Subject OCR Language, Decision 17: Standalone Image Ingestion, Decision 5: No OCR (Later Reversed)

### Community 86 - "Graph Context Window Decisions"
Cohesion: 0.50
Nodes (4): Decision 21: Graph Prompt Budgets from Context Window, graph.context_window Config, JSON-Schema Structured Output Requirement, Rate Limit Configuration

### Community 87 - "Retrieval Test Fixture"
Cohesion: 0.50
Nodes (4): _add_chunk(), fixture, ranked(), Three chunks with orthogonal dense vectors and distinct sparse weights, so     r

### Community 88 - "Probe Failure Safety"
Cohesion: 0.50
Nodes (4): Same contract as a failed probe: nothing is destroyed until the configuration is, A real graphrag run leaves entities.parquet behind, and build_graph refuses to, test_a_broken_custom_prompt_leaves_the_existing_graph_intact(), _write_entities()

### Community 89 - "Hostile Document Risks"
Cohesion: 0.67
Nodes (3): Hostile PDF / OCR Rasterization Risk, Prompt Injection via Documents, F5: No Image-Dimension Cap on Hostile Images

### Community 90 - "Logging Test Reset"
Cohesion: 0.67
Nodes (3): fixture, Logging state (handlers, per-logger levels, the module's idempotency flag)     i, _reset_logging()

## Knowledge Gaps
- **51 isolated node(s):** `guard-pins.sh script`, `groundly`, `Correctness Review Priority`, `Edge Cases Review Priority`, `Lying Tests Review Priority` (+46 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SQLiteSubjectStore` connect `SQLite Store Tests` to `GraphRAG Build Pipeline`, `Verified Deck Generation`, `Generation Job Registry`, `Subject Export/Import`, `Subject Workspace Model`, `Vector Baseline Retrieval`, `Adaptive Agentic Retrieval`, `Graph Build & Probe`, `Store Retrieval Channels`, `Ingestion Test Fixtures`, `Graph Study Modes`, `Subject Init & Bundle Tests`, `Ask Pipeline Arm Tests`, `Slow Pipeline Integration Tests`, `Extraction Subprocess Management`, `Anki Deck Export`, `Citation Resolution`, `Deck/Question Store Schema`, `Graph Staleness Fingerprint`, `GraphRAG Workflow Error Counter`, `Ingestion Pipeline Formats`, `GraphRAG Extraction Probe`, `Ask/Search CLI Verbs`, `GraphRAG Builder Probe Tests`, `BGE-M3 Embedder`, `CLI App Shell`, `Manifest Schema Models`, `Graph-Not-Built Error Handling`, `Corpus Hash`, `Subject Lifecycle Cost Print`, `Chunk Metadata Resolution`, `Shared Embedder Singleton`?**
  _High betweenness centrality (0.158) - this node is a cross-community bridge._
- **Why does `Subject` connect `Subject Workspace Model` to `GraphRAG Build Pipeline`, `CLI Model & Config Verbs`, `Verified Deck Generation`, `Generation Job Registry`, `Subject Export/Import`, `Vector Baseline Retrieval`, `Adaptive Agentic Retrieval`, `Graph Build & Probe`, `Graph Study Modes`, `Subject Init & Bundle Tests`, `Ask Pipeline Arm Tests`, `Slow Pipeline Integration Tests`, `Extraction Subprocess Management`, `Anki Deck Export`, `Citation Resolution`, `Graph Staleness Fingerprint`, `GraphRAG Workflow Error Counter`, `Ingestion Pipeline Formats`, `GraphRAG Extraction Probe`, `Ask/Search CLI Verbs`, `GraphRAG Builder Probe Tests`, `GraphRAG Config & Metrics`, `CLI App Shell`, `Manifest Schema Models`, `Manifest Sync`, `Graph-Not-Built Error Handling`, `Subject Init/List CLI`, `Subject Lifecycle Cost Print`, `Subject Layout Tests`?**
  _High betweenness centrality (0.090) - this node is a cross-community bridge._
- **Why does `subject_dir()` connect `GraphRAG Build Pipeline` to `MCP Server & Retriever Tests`, `Verified Deck Generation`, `Zip-Slip Import Validation`, `Subject Filesystem Layout`, `Subject Export/Import`, `Subject Workspace Model`, `BGE-M3 Embedder`, `Adaptive Agentic Retrieval`, `Vector Baseline Retrieval`, `Ingestion Test Fixtures`, `Manifest Schema Models`, `Graph Study Modes`, `Subject Init & Bundle Tests`, `Ask Pipeline Arm Tests`, `Slow Pipeline Integration Tests`, `Anki Deck Export`, `CLI Ask Command Tests`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Are the 27 inferred relationships involving `SQLiteSubjectStore` (e.g. with `AskResult` and `Citation`) actually correct?**
  _`SQLiteSubjectStore` has 27 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `Subject` (e.g. with `AskResult` and `CardOutcome`) actually correct?**
  _`Subject` has 18 INFERRED edges - model-reasoned connections that need verification._
- **What connects `guard-pins.sh script`, `groundly`, `Correctness Review Priority` to the rest of the system?**
  _51 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `GraphRAG Build Pipeline` be split into smaller, more focused modules?**
  _Cohesion score 0.05034199726402189 - nodes in this community are weakly interconnected._