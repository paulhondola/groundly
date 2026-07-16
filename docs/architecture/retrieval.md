# Retrieval Architecture (Hybrid RAG + GraphRAG)

Expands [`groundly-spec.md`](../groundly-spec.md) §5. This layer is the thesis's scientific core — every design choice must remain measurable and reproducible. Directive on file: **quality over performance** (Paul, 2026-07-15).

## The four arms (evaluation frame)

| Arm | Path | Exists to answer |
|---|---|---|
| 1. Vector baseline | dense + sparse + BM25 → RRF → rerank | Strong baseline; NotebookLM-class behavior |
| 2. Pure GraphRAG | MS `graphrag` local/global search | Does graph structure help multi-hop/global queries? |
| 3. Static hybrid | router → backend(s) → fusion → rerank | Does cheap routing capture most of the gain? **Production arm.** |
| 4. Adaptive agentic | retrieve → self-grade → escalate/rewrite (≤2 iterations) | Does self-evaluation beat static routing, at what cost? **Eval only.** |

The common LlamaIndex `Retriever` interface is what makes the comparison fair — same query in, same context format out, per-arm logging (arm, path, chunk ids, tokens, latency, cost) into the local `traces` table in `progress.db`.

## Components

### Vector baseline (arm 1; the hybrid's workhorse)

Three channels, all derived at index time, fused at query time:

1. **Dense**: bge-m3 (pinned incl. hf_revision), 1024-d, sqlite-vec **brute-force = exact KNN**. This is an accuracy *upgrade* over the archived design's approximate HNSW — at 5k–50k chunks/subject the exact scan costs milliseconds. LanceDB/IVF is the named escape hatch if a corpus ever explodes.
2. **Learned sparse**: bge-m3's sparse lexical weights — same forward pass as dense, stored as an inverted table. Handles Romanian morphology far better than raw tokenization. (bge-m3's ColBERT vectors are **rejected**: ~100× storage would bloat the portable bundle.)
3. **BM25**: SQLite FTS5 over chunk text — free, exact-phrase capable.

Fusion: three-way reciprocal rank fusion. Rerank: `bge-reranker-v2-m3` cross-encoder over the fused top-k, **default ON** (`--no-rerank` for weak hardware); the eval measures its contribution.

**Cross-lingual caveat (stated, not hidden):** only the dense channel matches a Romanian question against English slides; sparse and BM25 are same-language. The eval reports cross-lingual queries as their own slice so the lexical channels aren't misjudged on queries they cannot serve.

Chunking: **Docling HybridChunker** — section-aligned chunks with the heading path ("Lecture 4 › Deadlocks › Prevention") prepended before embedding and stored for citation display; fixed-size windows only for unstructured text.

### Graph backend (arm 2)

MS `graphrag` as a **per-subject batch indexer**: entity/relation extraction → Leiden communities → hierarchical summaries, artifacts as parquet in `graph/`. Rebuild trigger = corpus-hash check inside `groundly index`. Local search (entity-anchored) for multi-hop; global search (community summaries) for synthesis.

- **Extraction cost lands on the student** — mid-tier cloud model rule stands (a bad graph silently invalidates the comparison; never a small local model). `groundly index` shows the estimated cost before building; graph build is skippable — the vector baseline works with zero API key.
- Mitigation is the sharing feature: the graph is the most expensive *and* most portable artifact (no embedding coupling) — one student builds, the course imports.
- **Global search is the cost hazard**: map-reduce over community summaries can mean dozens of LLM calls per query. It fires only via the router (arm 3) or explicitly (`overview` tool) — never as a default path.

### Query router (arm 3's brain — and the cost gate)

One cheap LLM call (router call class) labels the query: `factoid` → vector; `multi-hop` → graph local (+ vector); `global` → graph global. Ambiguous → both non-global backends. Router decisions are logged; router accuracy is itself a measured quantity. The router's second job is economic: nothing reaches token-hungry global search unclassified.

### Fusion + citation rule

When both backends fire: RRF first, cross-encoder rerank after. Context assembly pairs community summaries (breadth) with verbatim chunks (grounding). **Citations always resolve to verbatim chunks — a community summary is never a citation target** (it has no page).

### Adaptive retrieval (arm 4)

Vector first → LLM self-grades sufficiency → escalate to graph or rewrite the query — hard bound of 2 iterations, then answer with what exists or refuse. A plain bounded async loop (no framework). Eval arm only; a self-grading call on every query is exactly the latency/cost hazard the product path avoids.

## The dual-pipeline confound (honest accounting)

MS `graphrag` runs its own chunking/extraction — the two backends do not share one ingestion pipeline, so an observed difference could partly stem from pipeline differences. Mitigation: align chunk size/overlap and the extraction model where configurable; document the residual difference in the methods section. An examined confound is a methods section; a hidden one is a rejected thesis.

## Evaluation protocol

- **Gold set** per pilot subject from past exams, stratified by query class (factoid / multi-hop / global synthesis), RO and EN, **cross-lingual queries as a separate slice**. Professor spot-checks. Collection starts before implementation — it needs the professor, not code.
- **Metrics per arm × class:** retrieval hit rate, RAGAS groundedness/faithfulness, citation accuracy, router accuracy, cost, latency — all from the traces table, all offline.
- **Grounding-fidelity experiment:** the same gold questions answered (a) through the enforced `ask` pipeline and (b) host-composed from raw `search` results — compared on faithfulness + citation accuracy. Measures enforced vs agent-mediated grounding, the design's biggest real-world tension.
- **Reproducibility:** a frozen `~/.groundly/<SUBJECT>/` directory is the experimental artifact — hashable, shippable with the thesis; all four arms re-runnable anywhere.
- Expected result shape: per-class deltas ("hybrid matches the baseline on factoids at ~equal cost; improves multi-hop by X% at Y% cost"). GraphRAG is timeboxed; a negative result is a finding, not a failure.
