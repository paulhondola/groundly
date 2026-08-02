# Graph Report - .  (2026-08-01)

## Corpus Check
- 142 files · ~112,779 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1709 nodes · 3977 edges · 128 communities (100 shown, 28 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 324 edges (avg confidence: 0.69)
- Token cost: 194,068 input · 0 output

## Community Hubs (Navigation)
- Grounded Ask & Citations
- Graphrag Build Artifacts & Manifest
- Ask Agent & Query Router
- MCP Server & Tests
- Extraction Worker Subprocess
- GraphRAG Adapter Model Config
- Graph Build Cost Estimation
- MCP Server Tool Handlers
- Export/Import Bundle CLI
- Vector Retrieval Arm (RRF)
- Subject Workspace & Ingestion Pipeline
- Config Settings Loader
- GraphRAG Batch Builder Tests
- SubjectStore Reads/Writes
- P1 Ingestion Pipeline Review Findings
- SubjectStore Unit Tests
- Bundle Export/Import Test Fixtures
- litellm Adoption & Debug Logging Decisions
- Verified Deck Generation & Verifier Gate
- Ingestion Test Fixtures
- GraphRAG Retriever Base & Service Tier
- Graph Staleness & Corpus Hash
- Config Model & Validation
- Deck Generation Job & Cost Estimate
- Debug Logging Setup
- Slow Pipeline Integration Tests
- GraphRAG Chat Provider Probe
- bge-m3 Embedder Singleton
- CLI App Shell & Shared Helpers
- Anki Deck Export
- store.db Schema & Migrations
- Manifest Schema Model
- CLI Model Install/Config Verbs
- Extraction Failure Handling
- Graph Build Token Pricing
- GraphRAG Pipeline Callbacks
- GraphRAG Build Config & Prompt
- LLM Provider Config Loader
- GraphRAG Entity Embedding Adapter
- progress.db Traces & Verification
- Model Download Error Handling
- GraphRAG Local/Global Retrievers
- Ingestion Format Tables
- Filesystem Layout & Subject Paths
- Project CLAUDE.md & Agent Overview
- CLI Config Display Verbs
- CLI Model/Config Tests
- Retrieval Arms & Router Spec
- In-Memory Job Registry
- Cross-Encoder Reranker
- Adaptive Retrieval Arm
- CLI Ask/Search Tests
- Spec: Fusion, Citations & MCP Tools
- Decision 24: Reasoning Effort & Local Extraction Floor
- Extraction Prompt Budget & Preamble Pricing
- CLI Subject Init/Index Verbs
- GraphRAG Custom Prompt Resolution
- GraphRAG Chunk Failure Gates
- GraphRAG Metrics Trace Tests
- Performance Specialist Findings
- Spec Guardian Agent Rules
- Decision 22: Bundled Course-Tuned Extraction Prompt
- bge-m3 GraphRAG Embedding Registration
- Decision 20: litellm & Architecture Layers
- CLI Cost Display & Spend Gate
- GraphRAG Chat Unreachable Probe
- GraphRAG Prompt Budget Scaling
- Security Reviewer Agent Rules
- Verifier Gate & Generation Paths
- Decision 14-19: OCR & Embedding Pins
- Decision 21: Graph Context Window Budgets
- CLI Ask/Search Verbs
- Zip-Slip Validation Tests
- Embedder Protocol & Defaults
- P2 Import/Export Design
- Local JSON-Schema Capability Findings
- CLI App Entry Point
- CLI Deck Export Verb
- Adversarial Reviewer Agent Rules
- Privacy & Export Boundary Rules
- Bundle Manifest Count Checks
- GraphRAG Query Config Fixtures
- CLI Deck Export Tests
- Local Server & Storage Rules
- Extraction Fingerprint & Entity Types
- GraphRAG Broken Prompt Tests
- MCP Server Test Fixtures
- Hostile Document Security Findings
- Import Trust Boundary Risks
- groundly serve Verb
- GraphRAG Rate Limit Config
- GraphRAG Retry Config
- CLI MCP Verb Test
- Three Frameworks Rule
- CI/Publish Workflows
- Pin Guard Script
- Decision 23: Metered Cost Range
- MCP Host Wiring Docs
- Distribution Footprint Tradeoff
- Package Init (NullHandler)
- serve HTTP Rebinding Test
- Convention: Cost Estimates Before Spend
- Convention: Product Surfaces
- Convention: Python Conventions
- Study Memory Note
- Runtime Modes & Concurrency
- Arm 4 Adaptive (Eval-Only) Note
- Decision 18: Operational Settings
- UC-03 Source Management
- UC-10 Verified Mock Tests
- UC-11 Verified Flashcards
- UC-13 Coding Challenges
- UC-14 Mastery & Study Memory
- Release Process
- P1 Review: Offline Model-Fetch Finding
- serve Review: Trailing-Slash Redirect
- Image Ingestion Review Finding
- groundly init Command Surface
- groundly models Command
- pyproject.toml

## God Nodes (most connected - your core abstractions)
1. `SubjectStore` - 138 edges
2. `Subject` - 103 edges
3. `subject_dir()` - 100 edges
4. `build_graph()` - 67 edges
5. `_add_material()` - 48 edges
6. `mcp()` - 41 edges
7. `connect_progress()` - 38 edges
8. `set_key()` - 37 edges
9. `stub_chat()` - 37 edges
10. `VectorRetriever` - 36 edges

## Surprising Connections (you probably didn't know these)
- `Subprocess Runner Check` --semantically_similar_to--> `Exam Verifier (Identity of Generation)`  [INFERRED] [semantically similar]
  .claude/agents/security-reviewer.md → docs/architecture/agents.md
- `Verified Generation Feature` --semantically_similar_to--> `Exam Verifier (Identity of Generation)`  [INFERRED] [semantically similar]
  README.md → docs/architecture/agents.md
- `Connecting MCP Hosts Guide` --semantically_similar_to--> `serve Binds 127.0.0.1 Only`  [INFERRED] [semantically similar]
  docs/guides/mcp-hosts.md → .claude/rules/architecture.md
- `Verification Gate Invariant` --semantically_similar_to--> `Exam Verifier (Identity of Generation)`  [INFERRED] [semantically similar]
  .claude/rules/grounding-and-privacy.md → docs/architecture/agents.md
- `Privacy & Export Boundary` --semantically_similar_to--> `progress.db (never exported)`  [INFERRED] [semantically similar]
  .claude/rules/grounding-and-privacy.md → docs/architecture/data-model.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Trust-Layered Prompt Assembly Model** — _claude_rules_grounding_and_privacy_trust_layers, docs_architecture_agents_trust_layers [INFERRED 0.90]
- **Verifier Gate Invariant Across Docs and Review Agents** — _claude_agents_spec_guardian_verifier_gate_check, _claude_rules_grounding_and_privacy_verification_gate, docs_architecture_agents_exam_verifier [INFERRED 0.90]
- **P1 Ingestion Pipeline Spec + Two Review Rounds** — docs_superpowers_specs_2026_07_16_p1_ingestion_pipeline_pipeline_module, docs_superpowers_reviews_2026_07_16_p1_ingestion_pipeline_review_f1_integrity_error, docs_superpowers_reviews_2026_07_16_p1_ingestion_pipeline_round2_review_f1_remove_collision [INFERRED 0.80]
- **The four retrieval arms behind one LlamaIndex Retriever interface** — docs_architecture_retrieval_vector_baseline, docs_architecture_retrieval_pure_graphrag, docs_architecture_retrieval_static_hybrid, docs_architecture_retrieval_adaptive_agentic [EXTRACTED 1.00]
- **Empirical findings that established the 12B-reasoning-off local extraction floor** — docs_superpowers_reviews_2026_07_27_local_json_schema_capability_finding1_qwen_channel, docs_superpowers_reviews_2026_07_30_local_extraction_feasibility_finding1_reasoning_tax, docs_superpowers_reviews_2026_07_30_local_extraction_feasibility_finding2_smaller_model_fails, docs_groundly_spec_decision24_reasoning_configurable_report_call_class, docs_guides_graphrag_provider_measured_floor_gemma [INFERRED 0.85]
- **Components implementing the import trust boundary and interchange contract** — docs_architecture_data_model_import, docs_superpowers_specs_2026_07_17_p2_import_export_extract_bundle, docs_superpowers_specs_2026_07_17_p2_import_export_pin_matches, docs_groundly_spec_uc30_share_knowledge_bases [INFERRED 0.85]

## Communities (128 total, 28 thin omitted)

### Community 0 - "Grounded Ask & Citations"
Cohesion: 0.05
Nodes (73): AskResult, The ask pipeline — the one shared function exposed identically as `groundly ask`, Citation, NoCitationsError, Exception, Citation resolution shared by every agent call site that turns a model's cited r, Every cited chunk id in the model's response was hallucinated (not among the, resolve_citations() (+65 more)

### Community 1 - "Graphrag Build Artifacts & Manifest"
Cohesion: 0.06
Nodes (71): Graphrag, subject_dir(), GraphBuildError, Exception, Drop the previous build's outputs, and the manifest's claim to a graph, before a, Wraps any graphrag indexing failure — no raw traceback ever surfaces., _reset_graph_artifacts(), FileResult (+63 more)

### Community 2 - "Ask Agent & Query Router"
Cohesion: 0.09
Nodes (42): ask(), classify(), Query router — arm 3's brain and the cost gate (docs/architecture/retrieval.md)., ChatFn, Protocol, _configure_chat(), _FakeGraphGlobalRetriever, _FakeGraphLocalRetriever (+34 more)

### Community 3 - "MCP Server & Tests"
Cohesion: 0.09
Nodes (41): mcp(), command, Serve the groundly MCP tools (list_subjects/search/ask/get_page) over stdio., _configure_chat(), groundly/mcp/server.py: the FastMCP tool surface (list_subjects/search/ask/ get_, The zero-key proof: host generates, groundly verifies+stores — no [providers], test_ask_chat_unreachable_error_raises_tool_error(), test_ask_hallucinated_citation_raises_tool_error() (+33 more)

### Community 4 - "Extraction Worker Subprocess"
Cohesion: 0.08
Nodes (42): _bge_m3_tokenizer(), _extract_docling(), _extract_plain_text(), _first_frame(), main(), _model_step(), Path, Extraction worker — runs as `python -m groundly.ingestion.extract_worker <in> <o (+34 more)

### Community 5 - "GraphRAG Adapter Model Config"
Cohesion: 0.07
Nodes (38): completion_model_config(), extraction_fingerprint(), sha256 over exactly what the build sends: the prompt text and the entity-type, Build graphrag's ModelConfig from one of Groundly's provider sections     (`call, home(), fixture, groundly/llm/graphrag_adapter.py: the one place translating Groundly's provider, The gleaning round replays prompt + chunk + the model's whole first answer, (+30 more)

### Community 6 - "Graph Build Cost Estimation"
Cohesion: 0.09
Nodes (36): estimate_cost(), Rough heuristic graph-build cost estimate: no tokenizer, no LLM call. Uses     `, home(), _priced(), fixture, Derived from the room a call has left to answer in, not fitted to one provider's, Reachable via graph.extraction_prompt: a custom preamble larger than the window, litellm 1.86.2 prices mistral/mistral-small-latest at $0.06/$0.18 per Mtok; the (+28 more)

### Community 7 - "MCP Server Tool Handlers"
Cohesion: 0.10
Nodes (35): ask(), CardIn, _citation_uri(), document(), drill_down(), export_deck(), generate_deck(), get_job() (+27 more)

### Community 8 - "Export/Import Bundle CLI"
Cohesion: 0.09
Nodes (33): export(), import_(), Argument, command, help, Option, Path, Zip a subject's manifest, store.db, materials and graph into a portable bundle. (+25 more)

### Community 9 - "Vector Retrieval Arm (RRF)"
Cohesion: 0.11
Nodes (26): BaseRetriever, NodeWithScore, QueryBundle, Arm 1 (vector baseline): dense + learned-sparse + BM25, fused by reciprocal rank, The raw retrieval path: query -> ranked chunks, no LLM call, no provider     nee, Reciprocal rank fusion over already-ranked (best-first) id lists. Pure     funct, dense + sparse + BM25 -> RRF -> optional cross-encoder rerank -> top context_k., rrf() (+18 more)

### Community 10 - "Subject Workspace & Ingestion Pipeline"
Cohesion: 0.15
Nodes (24): Path, Represents a Groundly subject workspace with its directories, database files, an, Subject, IngestionPipeline, Orchestrates indexing of documents: extraction, embedding, and storage., stub_extractor(), Fast pipeline-logic tests using classes and interfaces directly, avoiding heavy, Another process recording the same failing content between our hash check and (+16 more)

### Community 11 - "Config Settings Loader"
Cohesion: 0.12
Nodes (30): load_settings(), Set one dotted key (`chat.model`, `chat.key`, `ingestion.timeout_seconds`, ...),, set_key(), home(), fixture, groundly/core/config.py: settings + the config-set writer (providers covered by, `_coerce` checks the field's annotation only; whole-model validators fire later,, Rate limits are provider/tier properties, so they live on the provider section (+22 more)

### Community 12 - "GraphRAG Batch Builder Tests"
Cohesion: 0.10
Nodes (30): build_graph(), Batch build graphrag's graph for a subject (parquet artifacts + a LanceDB     ve, _configure_extraction(), A zero-row parquet is ~1.9 KB of schema, so a file-size check would wave it, graphrag swallows community-report failures under a *different* logger than, cache/ is graphrag's paid-for LLM responses and logs/ is how a failure gets, Regression: the probe must exercise the same prompt the build sends, with the, architecture.md: every LLM call records tokens + cost. The probe makes *two* rea (+22 more)

### Community 13 - "SubjectStore Reads/Writes"
Cohesion: 0.08
Nodes (13): Connection, sha256 -> status, for hash-skip (indexed) and retry (extraction_failed)., Match by exact filename or sha256 prefix (the disambiguator)., One transaction. FTS syncs via the chunks_ad trigger; sparse_terms via FK, Exact KNN over the dense channel (sqlite-vec brute force). Chunk ids         nea, Learned-sparse channel: sum of weight * query_weight per chunk, best-first., FTS5 BM25 channel. Each term is individually double-quoted before joining, Resolve chunk ids to citation targets: document + page + heading path. (+5 more)

### Community 14 - "P1 Ingestion Pipeline Review Findings"
Cohesion: 0.08
Nodes (27): F1: Concurrent Index IntegrityError Crash, F5: extraction_failed Not Terminal (Infinite Retry), F6: No Test for Page Attribution / Heading Paths, F9: Corpus-Hash Graph Offer Missing, Carried-Over Findings From Round 1, F1: remove Deletes Wrong Material's File, F4: Pipeline Test Suite Excluded From CI, Finding 1: Boxed list[float] 8x Overhead (+19 more)

### Community 15 - "SubjectStore Unit Tests"
Cohesion: 0.13
Nodes (25): A subject's store.db: connection lifecycle + all reads/writes for materials,, SubjectStore, _add_chunk(), _add_material(), fixture, ranked(), Three chunks with orthogonal dense vectors and distinct sparse weights, so     r, add_indexed consumes a one-shot (dense, sparse) generator aligned with chunks, (+17 more)

### Community 16 - "Bundle Export/Import Test Fixtures"
Cohesion: 0.23
Nodes (26): init_subject(), Path, UC-30 export/import. Seeds store.db directly via SubjectStore.add_indexed + sync, A materials/ file with no `materials` row (e.g. copied just before a transient, graph/cache/ (graphrag's own incremental-rebuild cache) and graph/logs/     (ope, Stamp a fake graph/ dir + manifest.graphrag for `name`, recorded against either, _rewrite_zip_manifest(), _row_counts() (+18 more)

### Community 17 - "litellm Adoption & Debug Logging Decisions"
Cohesion: 0.09
Nodes (26): Debug Logs Stderr-Only, Never a File, graphrag's Own Log File Exception, Local Servers (127.0.0.1-only), Privacy Boundary Is a File (progress.db), Subprocess Execution (Verifier + Challenges), llm/chat.py litellm.completion() Swap, Cost Precedence: Manual Price vs. litellm Auto-Cost, litellm as graphrag's Transitive Dependency (+18 more)

### Community 18 - "Verified Deck Generation & Verifier Gate"
Cohesion: 0.18
Nodes (20): CardOutcome, _parse_cards(), Deck building: the two doors through the one verifier gate (P6 slice 1 design do, Model reply -> card candidates. Tolerant of code fences/prose around the JSON, CardCandidate, The verifier gate (P6 slice 1 design doc): the single check both the thin (`subm, Fail-fast, cheapest check first. Returns None iff the card passes every     chec, Rejection (+12 more)

### Community 19 - "Ingestion Test Fixtures"
Cohesion: 0.11
Nodes (17): ChunkData, RuntimeError, course(), _fixed_console_width(), fixture, Shared fixtures for the ingestion pipeline/worker tests. Pytest auto-discovers f, Scripted `ChatFn`: replies pop in call order (last reply repeats once exhausted), An initialized subject with hand-built orthogonal dense vectors and distinct (+9 more)

### Community 20 - "GraphRAG Retriever Base & Service Tier"
Cohesion: 0.10
Nodes (20): allow_nonstandard_service_tier(), Widen `graphrag_llm.LLMCompletionResponse.service_tier` from OpenAI's literal, _GraphArtifacts, GraphNotBuiltError, _GraphRetrieverBase, _load_artifacts(), BaseRetriever, Exception (+12 more)

### Community 21 - "Graph Staleness & Corpus Hash"
Cohesion: 0.15
Nodes (24): corpus_hash(), graph_is_stale(), sha256 over the subject's indexed materials' sha256s, sorted — stable across, Why the recorded graph no longer describes this subject, or None if it still doe, _add_material(), graphrag writes into an existing graph/ without clearing it, so before the reset, Guards the exact formula: sha256("\\n".join(sorted(sha256s))) — insertion     or, Stamp the manifest the way a successful build_graph does — both the corpus hash (+16 more)

### Community 22 - "Config Model & Validation"
Cohesion: 0.14
Nodes (19): field_validator, _coerce(), ConfigKeyError, GraphSettings, IngestionSettings, LlmSettings, BaseModel, Groundly config: ~/.groundly/config.toml — the one place that reads and writes i (+11 more)

### Community 23 - "Deck Generation Job & Cost Estimate"
Cohesion: 0.19
Nodes (21): estimate_generation(), generate_deck_job(), The thick door's job body (runs on a jobs.py thread): retrieve topic context, Verify every card and store the ones that pass into `deck`. Zero-key: the     ve, Constants-only cost heuristic — no retrieval, no model load, nothing spent., submit_cards(), AlignedEmbedder, _cards() (+13 more)

### Community 24 - "Debug Logging Setup"
Cohesion: 0.12
Nodes (21): Debug logging: one stderr handler on the ROOT logger, never a log file. Log line, Attach one stderr handler to the ROOT logger; return True if logging is on     (, setup_logging(), fixture, groundly/core/logs.py: one stderr handler on the root logger, off by default., The load-bearing test: graphrag's `init_loggers` clears handlers on the     `gra, Logging state (handlers, per-logger levels, the module's idempotency flag)     i, A subprocess, because the regression is *ordering*, not the values.      litellm (+13 more)

### Community 25 - "Slow Pipeline Integration Tests"
Cohesion: 0.23
Nodes (22): index_paths(), connect(), stub_embedder(), slow, Pipeline tests that invoke the real extract worker (bge-m3 tokenizer download on, A same-content sibling after a transient embed failure must retry, not be     re, A tokenizer/model load failure in the worker is environmental, not a bad documen, The extract worker runs with cwd=tempdir; a relative CLI path must still     res (+14 more)

### Community 26 - "GraphRAG Chat Provider Probe"
Cohesion: 0.13
Nodes (21): ChatResult, ChatResultStub, _configure_extraction_and_chat(), home(), fixture, groundly/ingestion/graph.py: the graphrag batch builder. `build_index` is always, Criterion: a bad prompt path is a named cause, not a graphrag internal, and it, build_graph probes the provider before running the pipeline — a real extraction (+13 more)

### Community 27 - "bge-m3 Embedder Singleton"
Cohesion: 0.12
Nodes (15): BgeM3Embedder, Memory-bounded index path: yield (dense_row, sparse) per text, running the, Process-level singleton: one resident bge-m3 model shared by every production, shared_embedder(), groundly/llm/embeddings.py: lazy bge-m3 embedder. Construction-failure wrapping, One resident bge-m3 model shared by every default production call site, not a, test_bge_m3_load_wraps_construction_failure_in_model_download_error(), test_shared_embedder_is_a_process_singleton_used_by_vector_retriever_default() (+7 more)

### Community 28 - "CLI App Shell & Shared Helpers"
Cohesion: 0.19
Nodes (14): _fail(), App objects and shared helpers for the Groundly CLI. Imports no verb modules (su, _store_checked(), _subject_checked(), ask/search verbs: the enforced grounded-answer pipeline and its raw retrieval ha, Deck export verb: `groundly export-deck SUBJECT DECK [--out PATH]` — the batch h, Groundly CLI — batch lifecycle verbs; the host agent is the interactive surface., `groundly mcp`: run the FastMCP tool surface over stdio for a host-spawned MCP c (+6 more)

### Community 29 - "Anki Deck Export"
Cohesion: 0.16
Nodes (18): _deck_id(), export_deck(), Path, Verified decks -> Anki .apkg via genanki (P6 slice 1; decision 6: Anki owns dail, Write `deck_name` as an .apkg. Default target: <subject>/exports/<deck>.apkg —, _source_line(), check_deck_name(), Deck names become file names (exports/<deck>.apkg) — the one host-controlled (+10 more)

### Community 30 - "store.db Schema & Migrations"
Cohesion: 0.21
Nodes (18): connect(), create_store(), Path, store.db access — the file that travels on export. Schema versioned via PRAGMA u, db(), _add_material_with_chunk(), store.db v2: decks/questions/question_citations, the v1 -> v2 migration, and the, test_add_verified_card_bogus_chunk_id_rolls_back_everything() (+10 more)

### Community 31 - "Manifest Schema Model"
Cohesion: 0.16
Nodes (14): Chunking, Counts, Manifest, Ocr, BaseModel, Connection, Path, manifest.json — the interchange contract (docs/architecture/data-model.md).  The (+6 more)

### Community 32 - "CLI Model Install/Config Verbs"
Cohesion: 0.15
Nodes (18): config_set(), install(), Argument, command, help, Option, Remove the bge-m3 embedding model from the local Hugging Face cache., Set a provider or settings value in ~/.groundly/config.toml. (+10 more)

### Community 33 - "Extraction Failure Handling"
Cohesion: 0.16
Nodes (15): Extraction, ExtractionFailure, ModelUnavailable, Exception, Path, Parent side of extraction: spawn the worker, enforce a wall-clock timeout, map f, reason is user-facing and names the specific cause., The worker couldn't load its model (uncached + offline, HF rate-limit, missing (+7 more)

### Community 34 - "Graph Build Token Pricing"
Cohesion: 0.17
Nodes (18): BuildEstimate, extraction_prices(), _litellm_prices(), _litellm_version(), metered_usage(), MeteredUsage, ModelPrices, _prices_for_model() (+10 more)

### Community 35 - "GraphRAG Pipeline Callbacks"
Cohesion: 0.11
Nodes (9): BaseException, _ProgressCallbacks, Counts per-item LLM failures for one graphrag stage, which reach us no other way, Translates graphrag's workflow lifecycle into `on_event(description,     complet, Refuse a build that reported success while producing an unusable graph.     grap, _verify_build_output(), _WorkflowErrorCounter, LogRecord (+1 more)

### Community 36 - "GraphRAG Build Config & Prompt"
Cohesion: 0.13
Nodes (17): GraphRagConfig, _build_config(), _extraction_prompt(), GraphBuildResult, _probe_call(), _probe_extraction(), Path, The graphrag batch builder — ingestion writes stores, never serves queries (.cla (+9 more)

### Community 37 - "LLM Provider Config Loader"
Cohesion: 0.20
Nodes (15): load_provider(), ProviderConfig, require_provider(), Provider config lives in groundly.core.config now (parsed by a foundation both l, test_provider_rate_limits_default_to_unset(), home(), fixture, groundly/llm/config.py: provider config reading from ~/.groundly/config.toml. (+7 more)

### Community 38 - "GraphRAG Entity Embedding Adapter"
Cohesion: 0.14
Nodes (11): ProviderNotConfiguredError, Exception, Raised by require_provider when a call class has no usable config section., Bgem3GraphEmbedding, graphrag's entity-description embedding store, delegated to the already-loaded, LLMEmbedding, Records the texts it was asked to encode; returns deterministic dense vectors, StubEmbedder (+3 more)

### Community 39 - "progress.db Traces & Verification"
Cohesion: 0.21
Nodes (15): connect_progress(), create_progress(), Connection, Path, progress.db access — traces, verification outcomes, and the study history built, Open progress.db, creating it (and the traces/verifications tables) if missing., One row per verifier verdict, from either door — the rejection-rate-by-source, record_trace() (+7 more)

### Community 40 - "Model Download Error Handling"
Cohesion: 0.12
Nodes (11): ModelDownloadError, Exception, snapshot_download failed fetching bge-m3 (network/HF error); callers map this, test_search_model_download_error_fails_cleanly(), _FakeGraphGlobalRetriever, _FakeGraphLocalRetriever, _NearEmbedder, _PassthroughReranker (+3 more)

### Community 41 - "GraphRAG Local/Global Retrievers"
Cohesion: 0.26
Nodes (15): DataFrame, GraphGlobalRetriever, GraphLocalRetriever, Entity-anchored local search — multi-hop queries., Community-summary global search — synthesis queries. Citations resolve via     t, _empty_frame(), parametrize, groundly/retrieval/graph.py: real GraphLocalRetriever/GraphGlobalRetriever imple (+7 more)

### Community 42 - "Ingestion Format Tables"
Cohesion: 0.21
Nodes (12): Format tables shared by the pipeline and the extraction worker. Stdlib-only: ext, _copy_to_materials(), _ignored(), _iter_files(), _load_ignore_patterns(), Path, The index pipeline (UC-01): hash-skip idempotent, per-file transactions, the run, Original files are the citation targets; they ship in exports. (+4 more)

### Community 43 - "Filesystem Layout & Subject Paths"
Cohesion: 0.19
Nodes (12): groundly_home(), Path, Filesystem layout: ~/.groundly/ (GROUNDLY_HOME overrides), one dir per subject., Subject names become path components and MCP identifiers., validate_subject_name(), Subject lifecycle: create the on-disk layout that everything else assumes., parametrize, test_discover_subjects_scans_manifests() (+4 more)

### Community 44 - "Project CLAUDE.md & Agent Overview"
Cohesion: 0.18
Nodes (14): Grounding Check, Grounding Invariant, Groundly Project CLAUDE.md Overview, Ask Pipeline (Interactive Agent), Code Tutoring (Dropped, Pivot #2), Cross-Cutting Rules, One Package, Interchangeable Clients Shape, Connecting MCP Hosts Guide (+6 more)

### Community 45 - "CLI Config Display Verbs"
Cohesion: 0.19
Nodes (13): Context, config(), callback, Embedding model management verbs, plus the still-stubbed config verbs (small; no, Show the config file path and effective values per call class (keys masked)., config_path(), _load_raw(), mask_key() (+5 more)

### Community 46 - "CLI Model/Config Tests"
Cohesion: 0.14
Nodes (3): home(), fixture, CLI: model management verbs and the config verbs.

### Community 47 - "Retrieval Arms & Router Spec"
Cohesion: 0.17
Nodes (13): Dual-pipeline confound (graphrag's own chunking vs shared pipeline), Arm 2: Pure GraphRAG (local/global search), Query router (arm 3's brain and cost gate), Arm 3: Static hybrid (router + fusion + rerank, production arm), UC-02 Grounded Q&A (ask/search), UC-12 Graph study formats (drill_down/overview), Per-operation cost table (student-side), ask MCP tool (+5 more)

### Community 48 - "In-Memory Job Registry"
Cohesion: 0.28
Nodes (11): get_job(), Job, In-memory generation job registry (P6 slice 1 design doc): `generate_*` MCP tool, Register a job and run `fn` on a daemon thread behind the generation lock., start_job(), The in-memory job registry (P6 slice 1): session-scoped by design — durability l, test_failing_job_reports_error(), test_job_runs_to_done_with_report() (+3 more)

### Community 49 - "Cross-Encoder Reranker"
Cohesion: 0.22
Nodes (6): BgeReranker, Cross-encoder reranker for the vector arm's fused candidate pool. Lives in llm/, groundly/llm/rerank.py: lazy cross-encoder reranker. Fast tests only check lazin, test_bge_reranker_does_not_load_model_on_construction(), test_bge_reranker_exposes_compute_score(), test_bge_reranker_load_wraps_construction_failure_in_model_download_error()

### Community 50 - "Adaptive Retrieval Arm"
Cohesion: 0.18
Nodes (9): AdaptiveRetriever, BaseRetriever, NodeWithScore, QueryBundle, Arm 4 (adaptive agentic): retrieve -> self-grade -> escalate/rewrite, bounded at, parametrize, groundly/retrieval/adaptive.py: named stub, real impl arrives at eval-start. Als, test_adaptive_retriever_raises_stub_not_implemented() (+1 more)

### Community 51 - "CLI Ask/Search Tests"
Cohesion: 0.22
Nodes (8): _configure_chat(), _NearEmbedder, CLI: `ask` (enforced, cited) and `search` (raw, zero-key) verbs., test_ask_chat_unreachable_error_fails_cleanly(), test_ask_model_download_error_fails_cleanly(), test_ask_no_rerank_plumbs_through(), test_ask_prints_answer_and_sources(), test_ask_refusal_exits_zero()

### Community 52 - "Spec: Fusion, Citations & MCP Tools"
Cohesion: 0.20
Nodes (12): Docs Are Source of Truth Convention, Integrity rules as constraints, not app code, store.db (exported knowledge base), Fusion + citation rule (citations resolve to verbatim chunks only), Reciprocal rank fusion (three-way RRF), Arm 1: Vector baseline (dense+sparse+BM25+RRF+rerank), Groundly vision (portable agent-consumable course KB), groundly:// citation resource template (+4 more)

### Community 53 - "Decision 24: Reasoning Effort & Local Extraction Floor"
Cohesion: 0.21
Nodes (12): Decision 24: Reasoning is a configurable cost; report_call_class routes community reports, Measured local floor: gemma4:12b, reasoning off, context_window 12288, providers.*.reasoning_effort config knob, graph.report_call_class knob (route community reports to a different provider), LM Studio provider setup guide, Local-runtime note (model-class floor for extraction), Finding 3: reasoning tokens charged against the context window, Finding 4: lms load -c honored on GGUF, ignored on MLX (+4 more)

### Community 54 - "Extraction Prompt Budget & Preamble Pricing"
Cohesion: 0.20
Nodes (12): _max_output_tokens_per_call(), _preamble_tokens(), The extraction preamble, sent with every single chunk. Measured off the prompt, The room an extraction call has left to answer in, once its own prompt is in the, _bundled_prompt_text(), The saving only reaches the student if the confirmation gate quotes it. Pricing, estimate_cost feeds the cost line, not the build. It must degrade to a number, test_estimate_cost_falls_back_when_a_custom_prompt_is_unreadable() (+4 more)

### Community 55 - "CLI Subject Init/Index Verbs"
Cohesion: 0.27
Nodes (11): index(), init(), Argument, command, help, Option, Path, Create a subject: manifest.json, materials/, store.db, progress.db. (+3 more)

### Community 56 - "GraphRAG Custom Prompt Resolution"
Cohesion: 0.24
Nodes (10): ExtractionPromptError, Exception, Path, Translates Groundly's own provider config into graphrag's config primitives — th, A configured `graph.extraction_prompt` that cannot be used — missing, unreadable, Yield `(path, text)` for the entity-extraction prompt the build will send., resolve_extraction_prompt(), _validate_prompt() (+2 more)

### Community 57 - "GraphRAG Chunk Failure Gates"
Cohesion: 0.18
Nodes (11): _add_chunks(), The gate refuses to *record* the build but leaves partial parquet on disk so, The gates and the provenance fields share one write, so a graph that was refused, n indexed materials, one chunk each — the failure gates work on chunk counts., graphrag catches extraction errors per text unit and carries on, so they never, graphrag emits TWO ERROR records per failed text unit under the same package, test_a_few_swallowed_failures_complete_the_build_and_are_reported(), test_one_failed_chunk_counts_once_not_twice() (+3 more)

### Community 58 - "GraphRAG Metrics Trace Tests"
Cohesion: 0.20
Nodes (11): _build_trace(), _build_with_metrics(), Drive graphrag's *real* metrics path: build the completion model from the config, The trace used to store the pre-build heuristic as if it were metered. graphrag, Cached responses are counted in graphrag's token totals but were never paid for,, A build that metered nothing is not a reason to fail one that otherwise     succ, graphrag registers metrics stores as singletons, so without a reset the second, test_build_graph_does_not_inherit_a_previous_builds_usage() (+3 more)

### Community 59 - "Performance Specialist Findings"
Cohesion: 0.20
Nodes (10): Performance Specialist Agent, Bounded Buffers Note, No Chunk-Level Streaming Issue, Concurrent Peaks Issue, fp32 Model Weights Residency, Full-JSON Parse Issue, Ingestion to Embedding to Store Path, Vector Dtype Blow-up (+2 more)

### Community 60 - "Spec Guardian Agent Rules"
Cohesion: 0.27
Nodes (10): Spec Guardian Agent, Doc Drift Check, Module Layering Check, Provider Boundary Check, LLM Provider Boundary (Hard Rule), Module Boundaries Invariant, /decision Skill Workflow, /implement-uc Skill Workflow (+2 more)

### Community 61 - "Decision 22: Bundled Course-Tuned Extraction Prompt"
Cohesion: 0.24
Nodes (10): Decision 22: Course-tuned entity extraction, bundled and fingerprinted, Bundled course-tuned entity-extraction prompt (guide usage), Arm A: graphrag Default Prompt (Baseline), Arm B: Bundled Course-Tuned Prompt, A/B Pass Conditions (Criteria 2-4), Acceptance criteria (entity count floor, type distribution shift), Bundled course-tuned extraction prompt design (696 tokens), Course-tuned entity types (concept/algorithm/data_structure/theorem/technique/tool/metric/person) (+2 more)

### Community 62 - "bge-m3 GraphRAG Embedding Registration"
Cohesion: 0.29
Nodes (7): Register Bgem3GraphEmbedding under the `bge_m3` strategy name. Idempotent:     t, register_bge_m3_embedding(), _nodes_from_chunk_ids(), NodeWithScore, QueryBundle, Resolve chunk ids to the shared metadata contract, in the given order (best, test_register_bge_m3_embedding_is_idempotent()

### Community 63 - "Decision 20: litellm & Architecture Layers"
Cohesion: 0.22
Nodes (9): Decision 20: litellm adopted as the LLM client inside llm/, System architecture (client/service/foundation layers), Cost model principles (zero-key first-class, visibility not enforcement), F1: DNS-rebinding / cross-origin protection left off, F2: smoke test never exercises serve.py, Finding 5: Groundly had no config passthrough to disable reasoning, Bgem3GraphEmbedding — LLMEmbedding adapter delegating to BgeM3Embedder, llm/graphrag_adapter.py (ProviderConfig → graphrag config) (+1 more)

### Community 64 - "CLI Cost Display & Spend Gate"
Cohesion: 0.31
Nodes (8): _print_actual_spend(), _print_cost_estimate(), The spend gate (conventions.md: print cost estimates before spending the     stu, What the build actually cost, metered by graphrag's own usage aggregates rather, Two decimals reads as money; below a cent it reads as zero, which is worse than, _usd(), _maybe_build_graph(), Corpus state is final for this run — decide whether the graph needs a     (re)bu

### Community 65 - "GraphRAG Chat Unreachable Probe"
Cohesion: 0.22
Nodes (9): ChatUnreachableError, Exception, The configured chat provider could not be reached (network/HTTP error)., The reset runs *after* the probe: a misconfigured provider must not destroy a, A provider can answer plain completions and still reject response_format — every, test_a_failed_probe_leaves_the_existing_graph_intact(), test_probe_checks_structured_output_separately_and_says_so(), test_probe_failure_names_the_cause_and_never_starts_the_pipeline() (+1 more)

### Community 66 - "GraphRAG Prompt Budget Scaling"
Cohesion: 0.25
Nodes (9): prompt_budgets(), PromptBudgets, graphrag's per-stage prompt sizing, scaled to the extraction model's context., Scale graphrag's stage budgets to the model actually configured.      graphrag's, parametrize, Every stage's input + output reserve has to fit the model's actual context —, This only ever scales down: a big context reproduces stock graphrag., test_prompt_budgets_always_fit_the_window() (+1 more)

### Community 67 - "Security Reviewer Agent Rules"
Cohesion: 0.25
Nodes (8): Security Reviewer Agent, Import Trust Boundary Check, Prompt Injection Check, Subprocess Runner Check, Trust Layers Check, Feature-Branch Workflow Convention, Trust Layers (Prompt Assembly), Prompt Assembly Trust Layers

### Community 68 - "Verifier Gate & Generation Paths"
Cohesion: 0.25
Nodes (8): Verifier Gate Check, Verification Gate Invariant, Exam Verifier (Identity of Generation), Gap Analysis / Study Planning (Not an Agent), Thick Generation Path, Thin Generation Path, Latency Classes / Job Serialization, Request Flows (Latency Classes)

### Community 69 - "Decision 14-19: OCR & Embedding Pins"
Cohesion: 0.29
Nodes (8): Import (validate manifest, zip-slip-safe extraction), manifest.json interchange contract, Decision 14: Pivot #3 reversed — OCR enabled via bundled RapidOCR, Decision 15: Per-subject OCR language (--ocr-lang), Decision 17: Standalone image ingestion via IMAGE→OCR pipeline, Decision 19: bge-m3 fp16 inference + streamed per-document embedding, Tech stack decision table, Version pinning policy (exact pins as interchange compatibility contracts)

### Community 70 - "Decision 21: Graph Context Window Budgets"
Cohesion: 0.32
Nodes (8): Decision 21: Graph prompt budgets derived from graph.context_window, Phasing roadmap P1-P7, UC-01 Index materials, graph.context_window knob and prompt_budgets() derivation, 12288 is the local sweet spot (fits report, one extraction call), Finding 3: graph.context_window documented two incompatible ways, Finding 8: context_window=16384 turns on gleanings, doubling extraction calls, ingestion/graph.py batch builder (build_graph)

### Community 71 - "CLI Ask/Search Verbs"
Cohesion: 0.36
Nodes (8): ask(), Argument, command, help, Option, Ask a grounded question: a cited answer, or the refusal — never model knowledge., Raw retrieval: top-k chunks with text + citations. No LLM call, works with no, search()

### Community 72 - "Zip-Slip Validation Tests"
Cohesion: 0.39
Nodes (8): Zip-slip gate: reject, never sanitize. Also blocks anything outside the     expo, validate_entries(), parametrize, test_validate_entries_rejects_oversized_declared_total(), test_validate_entries_rejects_path_escapes_and_smuggled_entries(), test_validate_entries_rejects_symlink(), test_validate_entries_rejects_windows_style_paths(), _zip_with_entry()

### Community 73 - "Embedder Protocol & Defaults"
Cohesion: 0.29
Nodes (5): _default_extractor(), Embedder, Protocol, OnEvent, SparseWeights

### Community 74 - "P2 Import/Export Design"
Cohesion: 0.33
Nodes (7): Export (zip subject dir minus progress.db), UC-30 Share knowledge bases (.groundly bundles), core/bundle.py design (export_subject/import flow), export_subject() — allowlist zip, WAL checkpoint, extract_bundle()/validate_entries() zip-slip gate, pin_matches()/re_embed() — embedding pin check and re-embed path, cli/sharing.py design (groundly export/import verbs)

### Community 75 - "Local JSON-Schema Capability Findings"
Cohesion: 0.29
Nodes (7): Community reports need JSON-schema structured output, Preflight probe (one real extraction prompt before the corpus run), Finding 1: qwen3.5-9b emits answer into reasoning_content, not content, Finding 2: preflight probe cannot detect empty-content success, Finding 8: schema-valid response with substantively empty findings, Finding 9: Ollama's default context (4096) is the failing configuration, The four tiers: T0 Accept, T1 Conform, T2 Fidelity, T3 Throughput

### Community 76 - "CLI App Entry Point"
Cohesion: 0.29
Nodes (7): main(), _print_version(), callback, help, Option, Groundly — local-first course knowledge bases for AI agents., is_eager

### Community 77 - "CLI Deck Export Verb"
Cohesion: 0.29
Nodes (7): export_deck(), Argument, command, help, Option, Path, Export a verified flashcard deck as an Anki .apkg (citations on card backs).

### Community 78 - "Adversarial Reviewer Agent Rules"
Cohesion: 0.33
Nodes (6): Adversarial Reviewer Agent, Correctness Review Priority, Edge Cases Review Priority, Lying Tests Review Priority, Adversarial Review File Format, Silent Failures Review Priority

### Community 79 - "Privacy & Export Boundary Rules"
Cohesion: 0.33
Nodes (6): Privacy / Export Boundary Check, Privacy & Export Boundary, Observability Traces, Privacy boundary is a file (progress.db never exported), progress.db (never exported), Evaluation protocol (gold set, metrics, grounding-fidelity experiment)

### Community 80 - "Bundle Manifest Count Checks"
Cohesion: 0.40
Nodes (5): check_counts(), Opens via store.connect (runs the user_version refusal); compares manifest     c, test_check_counts_refuses_newer_schema(), test_check_counts_rejects_mismatched_rows(), test_read_manifest_rejects_newer_format_version()

### Community 81 - "GraphRAG Query Config Fixtures"
Cohesion: 0.33
Nodes (4): ModelConfig, fixture, The graph query config always builds a completion model config (local/global, stub_completion_model_config()

### Community 82 - "CLI Deck Export Tests"
Cohesion: 0.40
Nodes (3): `groundly export-deck`: the CLI wrapper over core/anki.py's export_deck., _seed_deck(), test_export_deck_writes_apkg()

### Community 83 - "Local Server & Storage Rules"
Cohesion: 0.40
Nodes (5): Local Servers Check, Storage & Concurrency Check, Lazy Model Loading Rule, serve Binds 127.0.0.1 Only, Storage & Concurrency Rules

### Community 84 - "Extraction Fingerprint & Entity Types"
Cohesion: 0.50
Nodes (5): current_extraction_fingerprint(), The fingerprint a build started right now would record. Raises     ExtractionPro, extraction_entity_types(), `graph.entity_types`, split and stripped. Stored comma-separated (see     core/c, test_build_graph_feeds_input_documents_and_records_manifest()

### Community 85 - "GraphRAG Broken Prompt Tests"
Cohesion: 0.50
Nodes (4): Same contract as a failed probe: nothing is destroyed until the configuration is, A real graphrag run leaves entities.parquet behind, and build_graph refuses to, test_a_broken_custom_prompt_leaves_the_existing_graph_intact(), _write_entities()

### Community 86 - "MCP Server Test Fixtures"
Cohesion: 0.50
Nodes (4): fixture, GROUNDLY_HOME with no subjects at all — for list_subjects-empty and     unknown-, _stub_models(), subject_free_home()

### Community 87 - "Hostile Document Security Findings"
Cohesion: 0.67
Nodes (3): Hostile PDF / OCR Rasterization Risk, Prompt Injection via Documents, F5: No Image-Dimension Cap on Hostile Images

### Community 88 - "Import Trust Boundary Risks"
Cohesion: 0.67
Nodes (3): Import Trust Boundary, Imported Parquet Decompression Bomb (Residual Risk), Malicious Bundle Zip Bomb (Mitigated)

### Community 89 - "groundly serve Verb"
Cohesion: 0.67
Nodes (3): command, Serve the groundly MCP tools over Streamable HTTP on 127.0.0.1., serve()

### Community 90 - "GraphRAG Rate Limit Config"
Cohesion: 0.67
Nodes (3): _rate_limit_config(), Only when the provider's limits have been declared in config.toml. There is no, RateLimitConfig

### Community 91 - "GraphRAG Retry Config"
Cohesion: 0.67
Nodes (3): Always on. graphrag fires extraction concurrently across the whole corpus and, _retry_config(), RetryConfig

## Knowledge Gaps
- **63 isolated node(s):** `guard-pins.sh script`, `groundly`, `Correctness Review Priority`, `Edge Cases Review Priority`, `Lying Tests Review Priority` (+58 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **28 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SubjectStore` connect `SubjectStore Unit Tests` to `Grounded Ask & Citations`, `Graphrag Build Artifacts & Manifest`, `Ask Agent & Query Router`, `MCP Server Tool Handlers`, `Export/Import Bundle CLI`, `Vector Retrieval Arm (RRF)`, `Subject Workspace & Ingestion Pipeline`, `GraphRAG Batch Builder Tests`, `SubjectStore Reads/Writes`, `Bundle Export/Import Test Fixtures`, `Verified Deck Generation & Verifier Gate`, `Ingestion Test Fixtures`, `GraphRAG Retriever Base & Service Tier`, `Graph Staleness & Corpus Hash`, `Deck Generation Job & Cost Estimate`, `Slow Pipeline Integration Tests`, `GraphRAG Chat Provider Probe`, `bge-m3 Embedder Singleton`, `CLI App Shell & Shared Helpers`, `Anki Deck Export`, `store.db Schema & Migrations`, `GraphRAG Pipeline Callbacks`, `GraphRAG Build Config & Prompt`, `GraphRAG Local/Global Retrievers`, `Ingestion Format Tables`, `bge-m3 GraphRAG Embedding Registration`, `CLI Cost Display & Spend Gate`, `Embedder Protocol & Defaults`, `CLI Deck Export Tests`?**
  _High betweenness centrality (0.149) - this node is a cross-community bridge._
- **Why does `Subject` connect `Subject Workspace & Ingestion Pipeline` to `Grounded Ask & Citations`, `Graphrag Build Artifacts & Manifest`, `Ask Agent & Query Router`, `MCP Server Tool Handlers`, `Export/Import Bundle CLI`, `Vector Retrieval Arm (RRF)`, `GraphRAG Batch Builder Tests`, `Bundle Export/Import Test Fixtures`, `Verified Deck Generation & Verifier Gate`, `Ingestion Test Fixtures`, `GraphRAG Retriever Base & Service Tier`, `Graph Staleness & Corpus Hash`, `Config Model & Validation`, `Deck Generation Job & Cost Estimate`, `Slow Pipeline Integration Tests`, `GraphRAG Chat Provider Probe`, `CLI App Shell & Shared Helpers`, `Anki Deck Export`, `Manifest Schema Model`, `GraphRAG Pipeline Callbacks`, `GraphRAG Build Config & Prompt`, `GraphRAG Local/Global Retrievers`, `Ingestion Format Tables`, `Filesystem Layout & Subject Paths`, `CLI Subject Init/Index Verbs`, `Embedder Protocol & Defaults`?**
  _High betweenness centrality (0.108) - this node is a cross-community bridge._
- **Why does `estimate_cost()` connect `Graph Build Cost Estimation` to `CLI Cost Display & Spend Gate`, `Graph Build Token Pricing`, `LLM Provider Config Loader`, `Config Settings Loader`, `Extraction Prompt Budget & Preamble Pricing`, `CLI App Shell & Shared Helpers`?**
  _High betweenness centrality (0.043) - this node is a cross-community bridge._
- **Are the 27 inferred relationships involving `SubjectStore` (e.g. with `AskResult` and `Citation`) actually correct?**
  _`SubjectStore` has 27 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `Subject` (e.g. with `AskResult` and `CardOutcome`) actually correct?**
  _`Subject` has 21 INFERRED edges - model-reasoned connections that need verification._
- **What connects `guard-pins.sh script`, `groundly`, `Correctness Review Priority` to the rest of the system?**
  _63 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Grounded Ask & Citations` be split into smaller, more focused modules?**
  _Cohesion score 0.05207835642618251 - nodes in this community are weakly interconnected._