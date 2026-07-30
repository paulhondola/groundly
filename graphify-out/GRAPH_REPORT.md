# Graph Report - .  (2026-07-28)

## Corpus Check
- 141 files · ~101,514 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1699 nodes · 3929 edges · 126 communities (91 shown, 35 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 337 edges (avg confidence: 0.69)
- Token cost: 140,861 input · 0 output

## Community Hubs (Navigation)
- CLI Subjects & Ingestion Results
- Config & LLM Call Classes
- MCP Server Entrypoint
- Store.db Connection Layer
- Agent Router & Ask Arm
- Adaptive Retrieval Arm
- P1/P5 Review Findings
- Extraction Worker (Docling/OCR)
- Vector Retrieval Arm
- GraphRAG Build Config
- MCP Server Tools
- bge-m3 Graph Embedding Registration
- Study Progress & Drill-down
- Subject Workspace & Pipeline
- Graph Build Cost Estimate
- Ask Pipeline & Citations
- Graph Staleness Detection
- Bundle Export/Import CLI
- Manifest Integrity Checks
- Bundle Export Test Fixtures
- Verifier & Deck Building
- Prompt Assembly
- Ingestion Test Fixtures
- Deck Generation Job
- Debug Logging (Stderr Only)
- Pipeline Indexing Tests
- GraphRAG Preflight Probe
- CLI App Shared Helpers
- Anki Deck Export
- Chat Completion Client (litellm)
- Embeddings & Model Install
- Store Connection Tests
- Extraction Failure Types
- Graph Prompt Token Budgeting
- Embeddings Download Errors
- Graph Cost Pricing Lookup
- Bgem3GraphEmbedding Class
- GraphRAG Progress Callbacks
- Ingestion Format Tables
- GraphRAG Completion Model Config
- GraphRAG Provider Decisions (Spec)
- Bundle Zip-Slip Validation
- Shared bge-m3 Embedder Singleton
- CLI Model Management Tests
- Cost Model & Zero-Key Principle
- Generation Job Registry
- Subject Filesystem Paths
- bge-m3 Embedder Encoding
- Reranker (bge)
- Grounding Invariant (README/Overview)
- Ingestion Chat Stubs
- P6 Verified Cards Design
- CLI Subjects Index/Init
- GraphRAG Metrics Trace Build
- Import Trust Boundary & Traces
- Interchange Format Decision
- Extraction Error Bookkeeping
- Security Reviewer Agent Checks
- Verifier Gate Invariant
- MCP Serve & Distribution
- CLI Cost Display
- GraphRAG Chat Unreachable Errors
- Performance Specialist Agent
- Course-Tuned Entity Extraction
- Debug Logging Design Spec
- CLI Ask Verb
- Embeddings Default Extractor
- Graph Not Built Error
- Spec Guardian Module Layering
- bge-m3 Embeddings Decision
- CLI App Version/Callback
- CLI Decks Export Verb
- Adversarial Reviewer Agent
- Provider Boundary & fp32 Weights
- Retrieval Arms Architecture
- Storage & Concurrency Rules
- JSON Schema Capability Findings
- Arm-Aware Routing & Study Formats
- Verifier & Coding Challenges (Spec)
- GraphRAG Broken-Prompt Safety
- Hostile Image/PDF Risk
- Import Zip-Bomb Mitigation
- CLI Serve Verb
- CLI MCP Verb Tests
- Three Frameworks Rule
- CI/Publish Workflows
- Version Pin Guard Hook
- OCR & Operational Settings Decisions
- Local Extraction Latency Finding
- Cost Visibility via Traces
- FastAPI/FastMCP Stack Choice
- Package Init (groundly)
- Cost Estimate Convention
- Docs-as-Source-of-Truth Convention
- Product Surfaces Convention
- Python Conventions
- Study Memory Concept
- Runtime Modes & Concurrency
- Trust Layers (Prompt Assembly)
- UC-03 Source Management
- UC-12 Graph Study Formats
- UC-14 Mastery & Study Memory
- Extraction Call Class
- Release Process (PyPI)
- Offline Model-Fetch Finding
- Serve HTTP Redirect Finding
- Image Ingestion Review Finding
- Local Model Load Finding
- groundly init Command Spec
- groundly models Command Spec
- No-LangGraph Decision
- Docling + Bundled RapidOCR
- Python >=3.11 via uv
- SQLite WAL + sqlite-vec + FTS5
- Static HTML Dashboard
- Local traces Observability
- typer + rich CLI
- pyproject.toml Metadata

## God Nodes (most connected - your core abstractions)
1. `SubjectStore` - 138 edges
2. `Subject` - 104 edges
3. `subject_dir()` - 100 edges
4. `build_graph()` - 63 edges
5. `_add_material()` - 44 edges
6. `mcp()` - 41 edges
7. `connect_progress()` - 38 edges
8. `stub_chat()` - 37 edges
9. `VectorRetriever` - 36 edges
10. `set_key()` - 34 edges

## Surprising Connections (you probably didn't know these)
- `Subprocess Runner Check` --semantically_similar_to--> `Exam Verifier (Identity of Generation)`  [INFERRED] [semantically similar]
  .claude/agents/security-reviewer.md → docs/architecture/agents.md
- `Finding 2: Preflight Probe Cannot Detect Empty Content` --references--> `_probe_call()`  [EXTRACTED]
  docs/superpowers/reviews/2026-07-27-local-json-schema-capability.md → groundly/ingestion/graph.py
- `bge-m3 Embeddings Pin` --semantically_similar_to--> `manifest.json Interchange Contract`  [INFERRED] [semantically similar]
  .claude/rules/architecture.md → docs/architecture/data-model.md
- `Connecting MCP Hosts Guide` --semantically_similar_to--> `serve Binds 127.0.0.1 Only`  [INFERRED] [semantically similar]
  docs/guides/mcp-hosts.md → .claude/rules/architecture.md
- `Cross-Cutting Rules` --semantically_similar_to--> `Grounding Invariant`  [INFERRED] [semantically similar]
  docs/architecture/overview.md → .claude/rules/grounding-and-privacy.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Trust-Layered Prompt Assembly Model** — _claude_rules_grounding_and_privacy_trust_layers, docs_architecture_agents_trust_layers, docs_groundly_spec_trust_layers [INFERRED 0.90]
- **Verifier Gate Invariant Across Docs and Review Agents** — _claude_agents_spec_guardian_verifier_gate_check, _claude_rules_grounding_and_privacy_verification_gate, docs_architecture_agents_exam_verifier [INFERRED 0.90]
- **P1 Ingestion Pipeline Spec + Two Review Rounds** — docs_superpowers_specs_2026_07_16_p1_ingestion_pipeline_pipeline_module, docs_superpowers_reviews_2026_07_16_p1_ingestion_pipeline_review_f1_integrity_error, docs_superpowers_reviews_2026_07_16_p1_ingestion_pipeline_round2_review_f1_remove_collision [INFERRED 0.80]
- **MCP Tool Surface Growing Across P4/P5/P6** — docs_superpowers_specs_2026_07_18_mcp_skeleton_design_tools_surface, docs_superpowers_specs_2026_07_24_p5_graphrag_arm_design_study_modes_tools, docs_superpowers_specs_2026_07_25_p6_verified_cards_design_thick_door [INFERRED 0.80]
- **Course-Tuned Extraction Prompt: Design, Artifact, and A/B Measurement** — docs_superpowers_specs_2026_07_26_lean_extraction_prompt_design_bundled_prompt, groundly_prompts_extract_graph_prompt, docs_superpowers_reviews_2026_07_26_lean_prompt_ab_arm_b_course_tuned_prompt [EXTRACTED 0.90]
- **Agent Layer: Ask Pipeline + Exam Verifier under Trust Layers** — docs_groundly_spec_ask_pipeline, docs_groundly_spec_exam_verifier, docs_groundly_spec_trust_layers [EXTRACTED 0.90]
- **Graph Build Cost & Context-Budget Decision Chain (21→22→23)** — docs_groundly_spec_decision_21_graph_prompt_budgets, docs_groundly_spec_decision_22_course_tuned_entity_extraction, docs_groundly_spec_decision_23_graph_cost_estimate_range [EXTRACTED 0.90]
- **Silent Graph-Build Failure Detection: Preflight Probe, Failure Gate, and Its Blind Spot** — docs_guides_graphrag_provider_preflight_probe, docs_groundly_spec_decision_21_graph_prompt_budgets, docs_superpowers_reviews_2026_07_27_local_json_schema_capability_finding2_probe_blind_spot [INFERRED 0.85]

## Communities (126 total, 35 thin omitted)

### Community 0 - "CLI Subjects & Ingestion Results"
Cohesion: 0.05
Nodes (68): subject_dir(), FileResult, Pipeline result types — the canonical home for status constants, `FileResult`, a, `groundly export-deck`: the CLI wrapper over core/anki.py's export_deck., _seed_deck(), test_export_deck_writes_apkg(), home(), _invoke_graph_build() (+60 more)

### Community 1 - "Config & LLM Call Classes"
Cohesion: 0.06
Nodes (73): Context, config(), callback, Embedding model management verbs, plus the still-stubbed config verbs (small; no, Show the config file path and effective values per call class (keys masked)., _coerce(), config_path(), ConfigKeyError (+65 more)

### Community 2 - "MCP Server Entrypoint"
Cohesion: 0.06
Nodes (54): mcp(), command, Serve the groundly MCP tools (list_subjects/search/ask/get_page) over stdio., _configure_chat(), _FakeGraphGlobalRetriever, _FakeGraphLocalRetriever, _NearEmbedder, _PassthroughReranker (+46 more)

### Community 3 - "Store.db Connection Layer"
Cohesion: 0.06
Nodes (37): Connection, A subject's store.db: connection lifecycle + all reads/writes for materials,, sha256 -> status, for hash-skip (indexed) and retry (extraction_failed)., Match by exact filename or sha256 prefix (the disambiguator)., One transaction. FTS syncs via the chunks_ad trigger; sparse_terms via FK, Exact KNN over the dense channel (sqlite-vec brute force). Chunk ids         nea, Learned-sparse channel: sum of weight * query_weight per chunk, best-first., FTS5 BM25 channel. Each term is individually double-quoted before joining (+29 more)

### Community 4 - "Agent Router & Ask Arm"
Cohesion: 0.09
Nodes (42): ask(), classify(), Query router — arm 3's brain and the cost gate (docs/architecture/retrieval.md)., ChatFn, Protocol, _configure_chat(), _FakeGraphGlobalRetriever, _FakeGraphLocalRetriever (+34 more)

### Community 5 - "Adaptive Retrieval Arm"
Cohesion: 0.06
Nodes (39): DataFrame, AdaptiveRetriever, BaseRetriever, NodeWithScore, QueryBundle, Arm 4 (adaptive agentic): retrieve -> self-grade -> escalate/rewrite, bounded at, _GraphArtifacts, GraphGlobalRetriever (+31 more)

### Community 6 - "P1/P5 Review Findings"
Cohesion: 0.05
Nodes (44): F1: Concurrent Index IntegrityError Crash, F5: extraction_failed Not Terminal (Infinite Retry), F6: No Test for Page Attribution / Heading Paths, F9: Corpus-Hash Graph Offer Missing, Carried-Over Findings From Round 1, F1: remove Deletes Wrong Material's File, F4: Pipeline Test Suite Excluded From CI, Finding 1: Boxed list[float] 8x Overhead (+36 more)

### Community 7 - "Extraction Worker (Docling/OCR)"
Cohesion: 0.08
Nodes (42): _bge_m3_tokenizer(), _extract_docling(), _extract_plain_text(), _first_frame(), main(), _model_step(), Path, Extraction worker — runs as `python -m groundly.ingestion.extract_worker <in> <o (+34 more)

### Community 8 - "Vector Retrieval Arm"
Cohesion: 0.09
Nodes (31): BaseRetriever, NodeWithScore, QueryBundle, Arm 1 (vector baseline): dense + learned-sparse + BM25, fused by reciprocal rank, The raw retrieval path: query -> ranked chunks, no LLM call, no provider     nee, Reciprocal rank fusion over already-ranked (best-first) id lists. Pure     funct, dense + sparse + BM25 -> RRF -> optional cross-encoder rerank -> top context_k., rrf() (+23 more)

### Community 9 - "GraphRAG Build Config"
Cohesion: 0.09
Nodes (37): GraphRagConfig, _build_config(), build_graph(), current_extraction_fingerprint(), _extraction_prompt(), GraphBuildError, GraphBuildResult, _probe_call() (+29 more)

### Community 10 - "MCP Server Tools"
Cohesion: 0.10
Nodes (35): ask(), CardIn, _citation_uri(), document(), drill_down(), export_deck(), generate_deck(), get_job() (+27 more)

### Community 11 - "bge-m3 Graph Embedding Registration"
Cohesion: 0.07
Nodes (32): Path, Register Bgem3GraphEmbedding under the `bge_m3` strategy name. Idempotent:     t, Yield `(path, text)` for the entity-extraction prompt the build will send., register_bge_m3_embedding(), resolve_extraction_prompt(), home(), fixture, parametrize (+24 more)

### Community 12 - "Study Progress & Drill-down"
Cohesion: 0.12
Nodes (27): drill_down(), overview(), connect_progress(), create_progress(), Connection, Path, Open progress.db, creating it (and the traces/verifications tables) if missing., One row per verifier verdict, from either door — the rejection-rate-by-source (+19 more)

### Community 13 - "Subject Workspace & Pipeline"
Cohesion: 0.14
Nodes (24): Path, Represents a Groundly subject workspace with its directories, database files, an, Subject, IngestionPipeline, Orchestrates indexing of documents: extraction, embedding, and storage., stub_extractor(), Fast pipeline-logic tests using classes and interfaces directly, avoiding heavy, Another process recording the same failing content between our hash check and (+16 more)

### Community 14 - "Graph Build Cost Estimate"
Cohesion: 0.11
Nodes (32): estimate_cost(), Rough heuristic graph-build cost estimate: no tokenizer, no LLM call. Uses     `, home(), _priced(), fixture, litellm 1.86.2 prices mistral/mistral-small-latest at $0.06/$0.18 per Mtok; the, An entry with no output price would produce an upper bound identical to the, An extraction provider with both manual prices set. Both are required for the (+24 more)

### Community 15 - "Ask Pipeline & Citations"
Cohesion: 0.14
Nodes (21): AskResult, The ask pipeline — the one shared function exposed identically as `groundly ask`, Citation, NoCitationsError, Exception, Citation resolution shared by every agent call site that turns a model's cited r, Every cited chunk id in the model's response was hallucinated (not among the, resolve_citations() (+13 more)

### Community 16 - "Graph Staleness Detection"
Cohesion: 0.14
Nodes (29): corpus_hash(), graph_is_stale(), sha256 over the subject's indexed materials' sha256s, sorted — stable across, Why the recorded graph no longer describes this subject, or None if it still doe, _add_material(), groundly/ingestion/graph.py: the graphrag batch builder. `build_index` is always, Guards the exact formula: sha256("\\n".join(sorted(sha256s))) — insertion     or, Stamp the manifest the way a successful build_graph does — both the corpus hash (+21 more)

### Community 17 - "Bundle Export/Import CLI"
Cohesion: 0.11
Nodes (28): export(), import_(), Argument, command, help, Option, Path, Zip a subject's manifest, store.db, materials and graph into a portable bundle. (+20 more)

### Community 18 - "Manifest Integrity Checks"
Cohesion: 0.13
Nodes (20): check_counts(), Opens via store.connect (runs the user_version refusal); compares manifest     c, Chunking, Counts, Graphrag, Manifest, Ocr, BaseModel (+12 more)

### Community 19 - "Bundle Export Test Fixtures"
Cohesion: 0.23
Nodes (26): init_subject(), Path, UC-30 export/import. Seeds store.db directly via SubjectStore.add_indexed, A materials/ file with no `materials` row (e.g. copied just before a transient, graph/cache/ (graphrag's own incremental-rebuild cache) and graph/logs/     (ope, Stamp a fake graph/ dir + manifest.graphrag for `name`, recorded against either, _rewrite_zip_manifest(), _row_counts() (+18 more)

### Community 20 - "Verifier & Deck Building"
Cohesion: 0.18
Nodes (20): CardOutcome, _parse_cards(), Deck building: the two doors through the one verifier gate (P6 slice 1 design do, Model reply -> card candidates. Tolerant of code fences/prose around the JSON, CardCandidate, The verifier gate (P6 slice 1 design doc): the single check both the thin (`subm, Fail-fast, cheapest check first. Returns None iff the card passes every     chec, Rejection (+12 more)

### Community 21 - "Prompt Assembly"
Cohesion: 0.16
Nodes (22): assemble(), assemble_cards(), assemble_cards_retry(), assemble_overview(), _escape(), NodeWithScore, Trust-layered prompt assembly for the ask pipeline (docs/architecture/agents.md), Regeneration round: only the rejected cards, each with its machine-readable (+14 more)

### Community 22 - "Ingestion Test Fixtures"
Cohesion: 0.11
Nodes (17): ChunkData, RuntimeError, course(), _fixed_console_width(), fixture, Shared fixtures for the ingestion pipeline/worker tests. Pytest auto-discovers f, Scripted `ChatFn`: replies pop in call order (last reply repeats once exhausted), An initialized subject with hand-built orthogonal dense vectors and distinct (+9 more)

### Community 23 - "Deck Generation Job"
Cohesion: 0.19
Nodes (21): estimate_generation(), generate_deck_job(), The thick door's job body (runs on a jobs.py thread): retrieve topic context, Verify every card and store the ones that pass into `deck`. Zero-key: the     ve, Constants-only cost heuristic — no retrieval, no model load, nothing spent., submit_cards(), AlignedEmbedder, _cards() (+13 more)

### Community 24 - "Debug Logging (Stderr Only)"
Cohesion: 0.12
Nodes (21): Debug logging: one stderr handler on the ROOT logger, never a log file. Log line, Attach one stderr handler to the ROOT logger; return True if logging is on     (, setup_logging(), fixture, groundly/core/logs.py: one stderr handler on the root logger, off by default., The load-bearing test: graphrag's `init_loggers` clears handlers on the     `gra, Logging state (handlers, per-logger levels, the module's idempotency flag)     i, A subprocess, because the regression is *ordering*, not the values.      litellm (+13 more)

### Community 25 - "Pipeline Indexing Tests"
Cohesion: 0.23
Nodes (22): index_paths(), connect(), stub_embedder(), slow, Pipeline tests that invoke the real extract worker (bge-m3 tokenizer download on, A same-content sibling after a transient embed failure must retry, not be     re, A tokenizer/model load failure in the worker is environmental, not a bad documen, The extract worker runs with cwd=tempdir; a relative CLI path must still     res (+14 more)

### Community 26 - "GraphRAG Preflight Probe"
Cohesion: 0.09
Nodes (23): _configure_extraction(), Regression: the probe must exercise the same prompt the build sends, with the, architecture.md: every LLM call records tokens + cost. The probe makes *two* rea, The probe is two real network calls and runs before graphrag's pipeline emits, graphrag's defaults want ~10k for community reports alone; on a small local mode, The probe runs outside build_graph's own wrapper, so anything it raises other th, A zero-row parquet is ~1.9 KB of schema, so a file-size check would wave it, graphrag swallows community-report failures under a *different* logger than (+15 more)

### Community 27 - "CLI App Shared Helpers"
Cohesion: 0.19
Nodes (14): _fail(), App objects and shared helpers for the Groundly CLI. Imports no verb modules (su, _store_checked(), _subject_checked(), ask/search verbs: the enforced grounded-answer pipeline and its raw retrieval ha, Deck export verb: `groundly export-deck SUBJECT DECK [--out PATH]` — the batch h, Groundly CLI — batch lifecycle verbs; the host agent is the interactive surface., `groundly mcp`: run the FastMCP tool surface over stdio for a host-spawned MCP c (+6 more)

### Community 28 - "Anki Deck Export"
Cohesion: 0.16
Nodes (18): _deck_id(), export_deck(), Path, Verified decks -> Anki .apkg via genanki (P6 slice 1; decision 6: Anki owns dail, Write `deck_name` as an .apkg. Default target: <subject>/exports/<deck>.apkg —, _source_line(), check_deck_name(), Deck names become file names (exports/<deck>.apkg) — the one host-controlled (+10 more)

### Community 29 - "Chat Completion Client (litellm)"
Cohesion: 0.24
Nodes (18): Reproduction Harness (probe_schema.py), complete(), Chat completion client: litellm.completion() against any OpenAI-compatible endpo, home(), fixture, groundly/llm/chat.py: litellm.completion() against any OpenAI-compatible endpoin, Both a manual price and a mapped model are available — manual formula wins., _response() (+10 more)

### Community 30 - "Embeddings & Model Install"
Cohesion: 0.15
Nodes (18): config_set(), install(), Argument, command, help, Option, Remove the bge-m3 embedding model from the local Hugging Face cache., Set a provider or settings value in ~/.groundly/config.toml. (+10 more)

### Community 31 - "Store Connection Tests"
Cohesion: 0.23
Nodes (17): connect(), create_store(), Path, db(), _add_material_with_chunk(), store.db v2: decks/questions/question_citations, the v1 -> v2 migration, and the, test_add_verified_card_bogus_chunk_id_rolls_back_everything(), test_add_verified_card_stores_question_and_citations() (+9 more)

### Community 32 - "Extraction Failure Types"
Cohesion: 0.16
Nodes (15): Extraction, ExtractionFailure, ModelUnavailable, Exception, Path, Parent side of extraction: spawn the worker, enforce a wall-clock timeout, map f, reason is user-facing and names the specific cause., The worker couldn't load its model (uncached + offline, HF rate-limit, missing (+7 more)

### Community 33 - "Graph Prompt Token Budgeting"
Cohesion: 0.12
Nodes (18): _max_output_tokens_per_call(), _preamble_tokens(), The extraction preamble, sent with every single chunk. Measured off the prompt, The room an extraction call has left to answer in, once its own prompt is in the, _bundled_prompt_text(), The saving only reaches the student if the confirmation gate quotes it. Pricing, estimate_cost feeds the cost line, not the build. It must degrade to a number, test_estimate_cost_falls_back_when_a_custom_prompt_is_unreadable() (+10 more)

### Community 34 - "Embeddings Download Errors"
Cohesion: 0.18
Nodes (12): ModelDownloadError, Exception, snapshot_download failed fetching bge-m3 (network/HF error); callers map this, _configure_chat(), _NearEmbedder, CLI: `ask` (enforced, cited) and `search` (raw, zero-key) verbs., test_ask_chat_unreachable_error_fails_cleanly(), test_ask_model_download_error_fails_cleanly() (+4 more)

### Community 35 - "Graph Cost Pricing Lookup"
Cohesion: 0.18
Nodes (16): BuildEstimate, extraction_prices(), _litellm_prices(), _litellm_version(), metered_usage(), MeteredUsage, ModelPrices, litellm's bundled price map, looked up by the bare model name from config.toml. (+8 more)

### Community 36 - "Bgem3GraphEmbedding Class"
Cohesion: 0.14
Nodes (9): Bgem3GraphEmbedding, graphrag's entity-description embedding store, delegated to the already-loaded, LLMEmbedding, ModelConfig, Records the texts it was asked to encode; returns deterministic dense vectors, StubEmbedder, test_embedding_async_delegates_the_same_way(), test_embedding_delegates_to_embedder_and_returns_dense_only() (+1 more)

### Community 37 - "GraphRAG Progress Callbacks"
Cohesion: 0.12
Nodes (7): BaseException, _ProgressCallbacks, Counts per-item LLM failures for one graphrag stage, which reach us no other way, Translates graphrag's workflow lifecycle into `on_event(description,     complet, _WorkflowErrorCounter, LogRecord, NoopWorkflowCallbacks

### Community 38 - "Ingestion Format Tables"
Cohesion: 0.21
Nodes (12): Format tables shared by the pipeline and the extraction worker. Stdlib-only: ext, _copy_to_materials(), _ignored(), _iter_files(), _load_ignore_patterns(), Path, The index pipeline (UC-01): hash-skip idempotent, per-file transactions, the run, Original files are the citation targets; they ship in exports. (+4 more)

### Community 39 - "GraphRAG Completion Model Config"
Cohesion: 0.15
Nodes (16): completion_model_config(), Build graphrag's ModelConfig from Groundly's `extraction` provider. Fails fast, Always on. graphrag fires extraction concurrently across the whole corpus and, _retry_config(), RetryConfig, graphrag swallows a 429 per text unit like any other failure, so without a retry, Unset means unthrottled — correct for a local runtime, which has no limits, and, Providers publish RPM and TPM independently; declaring one must not require (+8 more)

### Community 40 - "GraphRAG Provider Decisions (Spec)"
Cohesion: 0.14
Nodes (15): Decision 20: litellm Adopted as LLM Client, Decision 21: Graph Prompt Budgets from Context Window, Decision 23: Graph Cost Estimate Becomes a Range, MS graphrag, Hybrid Retrieval Strategy (Four Arms), litellm, Phase P5: GraphRAG, graph.context_window Budgeting (+7 more)

### Community 41 - "Bundle Zip-Slip Validation"
Cohesion: 0.17
Nodes (14): Zip-slip gate: reject, never sanitize. Also blocks anything outside the     expo, validate_entries(), parametrize, test_validate_entries_rejects_oversized_declared_total(), test_validate_entries_rejects_path_escapes_and_smuggled_entries(), test_validate_entries_rejects_symlink(), test_validate_entries_rejects_windows_style_paths(), _zip_with_entry() (+6 more)

### Community 42 - "Shared bge-m3 Embedder Singleton"
Cohesion: 0.14
Nodes (12): Process-level singleton: one resident bge-m3 model shared by every production, shared_embedder(), ExtractionPromptError, PromptBudgets, Exception, _rate_limit_config(), Translates Groundly's own provider config into graphrag's config primitives — th, Only when the provider's limits have been declared in config.toml. There is no (+4 more)

### Community 43 - "CLI Model Management Tests"
Cohesion: 0.14
Nodes (3): home(), fixture, CLI: model management verbs and the config verbs.

### Community 44 - "Cost Model & Zero-Key Principle"
Cohesion: 0.17
Nodes (13): Graph Build Cost (Single-Digit Dollars/Subject), Sharing Amortizes Cost, Zero-Key Operation Principle, Local Servers (127.0.0.1-only), Privacy Boundary Is a File (progress.db), llm/chat.py litellm.completion() Swap, Cost Precedence: Manual Price vs. litellm Auto-Cost, litellm as graphrag's Transitive Dependency (+5 more)

### Community 45 - "Generation Job Registry"
Cohesion: 0.28
Nodes (11): get_job(), Job, In-memory generation job registry (P6 slice 1 design doc): `generate_*` MCP tool, Register a job and run `fn` on a daemon thread behind the generation lock., start_job(), The in-memory job registry (P6 slice 1): session-scoped by design — durability l, test_failing_job_reports_error(), test_job_runs_to_done_with_report() (+3 more)

### Community 46 - "Subject Filesystem Paths"
Cohesion: 0.22
Nodes (11): groundly_home(), Path, Filesystem layout: ~/.groundly/ (GROUNDLY_HOME overrides), one dir per subject., Subject names become path components and MCP identifiers., validate_subject_name(), parametrize, test_discover_subjects_scans_manifests(), test_home_default() (+3 more)

### Community 47 - "bge-m3 Embedder Encoding"
Cohesion: 0.22
Nodes (8): BgeM3Embedder, Memory-bounded index path: yield (dense_row, sparse) per text, running the, test_bge_m3_load_wraps_construction_failure_in_model_download_error(), Real-model checks — excluded by default (pyproject addopts); run: pytest -m slow, encode_stream is the memory-bounded index path: it yields (dense, sparse) per, test_bge_m3_dense_and_sparse_contract(), test_bge_reranker_v2_m3_scores_relevant_pair_higher(), test_encode_stream_yields_one_numpy_vector_per_text_in_batches()

### Community 48 - "Reranker (bge)"
Cohesion: 0.22
Nodes (6): BgeReranker, Cross-encoder reranker for the vector arm's fused candidate pool. Lives in llm/, groundly/llm/rerank.py: lazy cross-encoder reranker. Fast tests only check lazin, test_bge_reranker_does_not_load_model_on_construction(), test_bge_reranker_exposes_compute_score(), test_bge_reranker_load_wraps_construction_failure_in_model_download_error()

### Community 49 - "Grounding Invariant (README/Overview)"
Cohesion: 0.21
Nodes (12): Grounding Check, Grounding Invariant, Groundly Project CLAUDE.md Overview, Ask Pipeline (Interactive Agent), Code Tutoring (Dropped, Pivot #2), Cross-Cutting Rules, One Package, Interchangeable Clients Shape, Connecting MCP Hosts Guide (+4 more)

### Community 50 - "Ingestion Chat Stubs"
Cohesion: 0.17
Nodes (12): ChatResult, ChatResultStub, home(), fixture, Criterion: a bad prompt path is a named cause, not a graphrag internal, and it, build_graph probes the provider before running the pipeline — a real extraction, The probe must pass the *same object* community_reports_extractor passes, so lit, store() (+4 more)

### Community 51 - "P6 Verified Cards Design"
Cohesion: 0.24
Nodes (11): Subprocess Execution (Verifier + Challenges), export_deck via genanki (.apkg), agents/jobs.py In-Memory Job Queue, store.db Schema v2 (decks/questions/question_citations), generate_deck Thick Door (Two-Phase Confirm), submit_cards Thin Door, agents/verifier.py verify_card Gate, genanki -> .apkg Flashcards (+3 more)

### Community 52 - "CLI Subjects Index/Init"
Cohesion: 0.27
Nodes (11): index(), init(), Argument, command, help, Option, Path, Create a subject: manifest.json, materials/, store.db, progress.db. (+3 more)

### Community 53 - "GraphRAG Metrics Trace Build"
Cohesion: 0.20
Nodes (11): _build_trace(), _build_with_metrics(), Drive graphrag's *real* metrics path: build the completion model from the config, The trace used to store the pre-build heuristic as if it were metered. graphrag, Cached responses are counted in graphrag's token totals but were never paid for,, A build that metered nothing is not a reason to fail one that otherwise     succ, graphrag registers metrics stores as singletons, so without a reset the second, test_build_graph_does_not_inherit_a_previous_builds_usage() (+3 more)

### Community 54 - "Import Trust Boundary & Traces"
Cohesion: 0.22
Nodes (10): Import Trust Boundary Check, /decision Skill Workflow, Observability Traces, Export / Import Process, Integrity Rules as Constraints, manifest.json Interchange Contract, progress.db (Never Exported), store.db (Exported) (+2 more)

### Community 55 - "Interchange Format Decision"
Cohesion: 0.24
Nodes (10): Ask Pipeline, Decision 7: Interchange Format, Groundly, Phase P2: Import/Export, UC-01 Index Materials, UC-02 Grounded Q&A, UC-30 Share Knowledge Bases, AC1: Export/Import Roundtrip (+2 more)

### Community 56 - "Extraction Error Bookkeeping"
Cohesion: 0.20
Nodes (10): _add_chunks(), The gates and the provenance fields share one write, so a graph that was refused, graphrag catches extraction errors per text unit and carries on, so they never, graphrag emits TWO ERROR records per failed text unit under the same package, n indexed materials, one chunk each — the failure gates work on chunk counts., The gate refuses to *record* the build but leaves partial parquet on disk so, test_one_failed_chunk_counts_once_not_twice(), test_refused_build_is_not_served_by_the_query_path() (+2 more)

### Community 57 - "Security Reviewer Agent Checks"
Cohesion: 0.22
Nodes (9): Security Reviewer Agent, Privacy / Export Boundary Check, Prompt Injection Check, Subprocess Runner Check, Trust Layers Check, Feature-Branch Workflow Convention, Privacy & Export Boundary, Trust Layers (Prompt Assembly) (+1 more)

### Community 58 - "Verifier Gate Invariant"
Cohesion: 0.22
Nodes (9): Verifier Gate Check, Verification Gate Invariant, Exam Verifier (Identity of Generation), Gap Analysis / Study Planning (Not an Agent), Thick Generation Path, Thin Generation Path, Latency Classes / Job Serialization, Request Flows (Latency Classes) (+1 more)

### Community 59 - "MCP Serve & Distribution"
Cohesion: 0.25
Nodes (9): groundly serve (Streamable HTTP, loopback-only), Honest Footprint Tradeoff, MCP Host Wiring (stdio), install.sh / uv tool install, F1: DNS-Rebinding / Origin Protection Off, F2: Smoke Test Never Exercises serve.py, Citation Resource Template groundly://subject/file#page=N, MCP Spawn-Speed / Lazy Loading Guard (+1 more)

### Community 60 - "CLI Cost Display"
Cohesion: 0.31
Nodes (8): _print_actual_spend(), _print_cost_estimate(), The spend gate (conventions.md: print cost estimates before spending the     stu, What the build actually cost, metered by graphrag's own usage aggregates rather, Two decimals reads as money; below a cent it reads as zero, which is worse than, _usd(), _maybe_build_graph(), Corpus state is final for this run — decide whether the graph needs a     (re)bu

### Community 61 - "GraphRAG Chat Unreachable Errors"
Cohesion: 0.22
Nodes (9): ChatUnreachableError, Exception, The configured chat provider could not be reached (network/HTTP error)., A provider can answer plain completions and still reject response_format — every, The reset runs *after* the probe: a misconfigured provider must not destroy a, test_a_failed_probe_leaves_the_existing_graph_intact(), test_probe_checks_structured_output_separately_and_says_so(), test_probe_failure_names_the_cause_and_never_starts_the_pipeline() (+1 more)

### Community 62 - "Performance Specialist Agent"
Cohesion: 0.25
Nodes (8): Performance Specialist Agent, Bounded Buffers Note, No Chunk-Level Streaming Issue, Concurrent Peaks Issue, Full-JSON Parse Issue, Ingestion to Embedding to Store Path, Vector Dtype Blow-up, Whole-Document Embed Issue

### Community 63 - "Course-Tuned Entity Extraction"
Cohesion: 0.25
Nodes (8): Decision 22: Course-Tuned Entity Extraction, The Privacy Boundary Is a File, progress.db, store.db, Bundled Course-Tuned Entity Extraction Prompt, AC3: Hostile Bundle + Privacy, Structural Privacy Test (progress.db never opened), Zip-Slip Validation Gate (validate_entries)

### Community 64 - "Debug Logging Design Spec"
Cohesion: 0.25
Nodes (8): Debug Logs Stderr-Only, Never a File, graphrag's Own Log File Exception, groundly/ingestion/graph.py build_graph, drill_down/overview Trace Bookkeeping (kind='ask'), Review Amendments (NullHandler, verbose removal, key-safe config), Graph Build Progress Bar + Verbose Callbacks, groundly/core/logs.py setup_logging, Never-Stdout Hard Constraint

### Community 65 - "CLI Ask Verb"
Cohesion: 0.36
Nodes (8): ask(), Argument, command, help, Option, Ask a grounded question: a cited answer, or the refusal — never model knowledge., Raw retrieval: top-k chunks with text + citations. No LLM call, works with no, search()

### Community 66 - "Embeddings Default Extractor"
Cohesion: 0.29
Nodes (5): _default_extractor(), Embedder, Protocol, OnEvent, SparseWeights

### Community 67 - "Graph Not Built Error"
Cohesion: 0.25
Nodes (4): GraphNotBuiltError, Exception, Raised by graph arms whenever `<subject>/graph/` doesn't exist yet., _NotBuiltRetriever

### Community 68 - "Spec Guardian Module Layering"
Cohesion: 0.38
Nodes (7): Spec Guardian Agent, Doc Drift Check, Module Layering Check, Module Boundaries Invariant, /implement-uc Skill Workflow, Working Rules, Module Dependency Rules

### Community 69 - "bge-m3 Embeddings Decision"
Cohesion: 0.29
Nodes (7): bge-m3 Embeddings, Decision 19: bge-m3 fp16 + Streamed Embedding, manifest.json (Interchange Contract), AC2: Pin Mismatch Re-embed, AC4: No Overwrite Without Confirmation, groundly import Command Flow, Embedding Pin Mismatch → Re-embed Path

### Community 70 - "CLI App Version/Callback"
Cohesion: 0.29
Nodes (7): main(), _print_version(), callback, help, Option, Groundly — local-first course knowledge bases for AI agents., is_eager

### Community 71 - "CLI Decks Export Verb"
Cohesion: 0.29
Nodes (7): export_deck(), Argument, command, help, Option, Path, Export a verified flashcard deck as an Anki .apkg (citations on card backs).

### Community 72 - "Adversarial Reviewer Agent"
Cohesion: 0.33
Nodes (6): Adversarial Reviewer Agent, Correctness Review Priority, Edge Cases Review Priority, Lying Tests Review Priority, Adversarial Review File Format, Silent Failures Review Priority

### Community 73 - "Provider Boundary & fp32 Weights"
Cohesion: 0.33
Nodes (6): fp32 Model Weights Residency, Provider Boundary Check, bge-m3 Embeddings Pin, LLM Provider Boundary (Hard Rule), Point Groundly at LM Studio Config, LM Studio Setup Guide

### Community 74 - "Retrieval Arms Architecture"
Cohesion: 0.47
Nodes (6): Arm 4: Adaptive Agentic Retrieval, Dual-Pipeline Confound, Fusion + Citation Rule, Arm 2: Pure GraphRAG, Arm 3: Query Router (Cost Gate), Arm 1: Vector Baseline

### Community 75 - "Storage & Concurrency Rules"
Cohesion: 0.40
Nodes (5): Local Servers Check, Storage & Concurrency Check, Lazy Model Loading Rule, serve Binds 127.0.0.1 Only, Storage & Concurrency Rules

### Community 76 - "JSON Schema Capability Findings"
Cohesion: 0.40
Nodes (5): JSON-Schema Structured Output Requirement, qwen/qwen3.5-9b Supports json_schema (Guide Claim), Finding 1: qwen3.5-9b Emits Answer into Reasoning Channel, Finding 8: Schema-Valid but Substantively Empty Report, The Four Tiers (T0 Accept / T1 Conform / T2 Fidelity / T3 Throughput)

### Community 77 - "Arm-Aware Routing & Study Formats"
Cohesion: 0.40
Nodes (5): ask.py Arm-Aware Routing + Degrade-to-Vector, drill_down / overview MCP Tools, Silent-Degradation Instrumentation Table, UC-12 Graph Study Formats, UC-14 Mastery & Study Memory

### Community 78 - "Verifier & Coding Challenges (Spec)"
Cohesion: 0.50
Nodes (4): Exam Verifier, UC-10 Verified Mock Tests, UC-11 Verified Flashcards to Anki, UC-13 Coding Challenges

### Community 79 - "GraphRAG Broken-Prompt Safety"
Cohesion: 0.50
Nodes (4): Same contract as a failed probe: nothing is destroyed until the configuration is, A real graphrag run leaves entities.parquet behind, and build_graph refuses to, test_a_broken_custom_prompt_leaves_the_existing_graph_intact(), _write_entities()

### Community 80 - "Hostile Image/PDF Risk"
Cohesion: 0.67
Nodes (3): Hostile PDF / OCR Rasterization Risk, Prompt Injection via Documents, F5: No Image-Dimension Cap on Hostile Images

### Community 81 - "Import Zip-Bomb Mitigation"
Cohesion: 0.67
Nodes (3): Import Trust Boundary, Imported Parquet Decompression Bomb (Residual Risk), Malicious Bundle Zip Bomb (Mitigated)

### Community 82 - "CLI Serve Verb"
Cohesion: 0.67
Nodes (3): command, Serve the groundly MCP tools over Streamable HTTP on 127.0.0.1., serve()

## Knowledge Gaps
- **64 isolated node(s):** `guard-pins.sh script`, `groundly`, `Correctness Review Priority`, `Edge Cases Review Priority`, `Lying Tests Review Priority` (+59 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **35 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Subject` connect `Subject Workspace & Pipeline` to `CLI Subjects & Ingestion Results`, `Config & LLM Call Classes`, `Agent Router & Ask Arm`, `Adaptive Retrieval Arm`, `Vector Retrieval Arm`, `GraphRAG Build Config`, `MCP Server Tools`, `Study Progress & Drill-down`, `Ask Pipeline & Citations`, `Graph Staleness Detection`, `Bundle Export/Import CLI`, `Manifest Integrity Checks`, `Bundle Export Test Fixtures`, `Verifier & Deck Building`, `Ingestion Test Fixtures`, `Deck Generation Job`, `Pipeline Indexing Tests`, `CLI App Shared Helpers`, `Anki Deck Export`, `GraphRAG Progress Callbacks`, `Ingestion Format Tables`, `Ingestion Chat Stubs`, `CLI Subjects Index/Init`, `Interchange Format Decision`, `Embeddings Default Extractor`, `Graph Not Built Error`?**
  _High betweenness centrality (0.161) - this node is a cross-community bridge._
- **Why does `SubjectStore` connect `Store.db Connection Layer` to `CLI Subjects & Ingestion Results`, `Agent Router & Ask Arm`, `Adaptive Retrieval Arm`, `Vector Retrieval Arm`, `GraphRAG Build Config`, `MCP Server Tools`, `Study Progress & Drill-down`, `Subject Workspace & Pipeline`, `Ask Pipeline & Citations`, `Graph Staleness Detection`, `Bundle Export/Import CLI`, `Bundle Export Test Fixtures`, `Verifier & Deck Building`, `Ingestion Test Fixtures`, `Deck Generation Job`, `Pipeline Indexing Tests`, `CLI App Shared Helpers`, `Anki Deck Export`, `Store Connection Tests`, `GraphRAG Progress Callbacks`, `Ingestion Format Tables`, `bge-m3 Embedder Encoding`, `Ingestion Chat Stubs`, `CLI Cost Display`, `Embeddings Default Extractor`, `Graph Not Built Error`?**
  _High betweenness centrality (0.153) - this node is a cross-community bridge._
- **Why does `export_subject()` connect `Interchange Format Decision` to `Manifest Integrity Checks`, `Subject Workspace & Pipeline`, `Store Connection Tests`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Are the 27 inferred relationships involving `SubjectStore` (e.g. with `AskResult` and `Citation`) actually correct?**
  _`SubjectStore` has 27 INFERRED edges - model-reasoned connections that need verification._
- **Are the 21 inferred relationships involving `Subject` (e.g. with `AskResult` and `CardOutcome`) actually correct?**
  _`Subject` has 21 INFERRED edges - model-reasoned connections that need verification._
- **What connects `guard-pins.sh script`, `groundly`, `Correctness Review Priority` to the rest of the system?**
  _64 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `CLI Subjects & Ingestion Results` be split into smaller, more focused modules?**
  _Cohesion score 0.054746835443037975 - nodes in this community are weakly interconnected._
