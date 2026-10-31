# Retrieval

Expands [`groundly-spec.md`](../groundly-spec.md) §5. One retriever: the vector baseline.
The graph arms, the router and the adaptive arm were measured and retired; protocols and
results are kept outside the repo, and the code is at tag `thesis-experiments-2026-09`.

## The pipeline

`query → dense + sparse + BM25 → RRF → cross-encoder rerank → top context_k hits`

`search` (MCP tool and `groundly search`) returns those hits verbatim, each carrying
`chunk_id`, `filename`, `page`, `heading_path` and a `groundly://` URI. There is no
server-side answer generation: the host agent composes and cites.

## Channels

1. **Dense** — bge-m3 (pinned incl. hf_revision), 1024-d, sqlite-vec brute force = exact
   KNN. At 5k–50k chunks per subject the exact scan costs milliseconds.
2. **Learned sparse** — bge-m3's sparse lexical weights from the same forward pass,
   stored as an inverted table. Handles Romanian morphology far better than raw
   tokenization. (bge-m3's ColBERT vectors are rejected: ~100× storage in a portable
   bundle.)
3. **BM25** — SQLite FTS5 over chunk text; free, exact-phrase capable.

Fusion is three-way reciprocal rank fusion, ties broken by how many channels found the
id. Rerank is `bge-reranker-v2-m3` over the fused top-20, default ON (`--no-rerank` for
weak hardware).

**Cross-lingual caveat:** only the dense channel matches a Romanian question against
English slides; sparse and BM25 are same-language.

Chunking is Docling's HybridChunker — section-aligned, with the heading path
("Lecture 4 › Deadlocks › Prevention") prepended before embedding and stored for
citation display.

## Citations

Retrieval returns chunk ids; every id resolves to document + page + heading path.
Only verbatim chunks are citation targets; anything that summarizes has no page.

## Knobs

`retrieval.context_k` (default 8) and `retrieval.rerank` (default true) in
`~/.groundly/config.toml`.
