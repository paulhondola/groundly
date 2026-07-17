"""Arm 1 (vector baseline): dense + learned-sparse + BM25, fused by reciprocal rank
fusion, reranked by a cross-encoder (default ON). The `BaseRetriever` interface is
the "four arms, one interface" gate — every arm returns `NodeWithScore` with the same
metadata shape (docs/architecture/retrieval.md).

`search()` is the zero-key shared function CLI `search` (and later the MCP tool)
call directly; it never requires a provider and always logs a `kind='search'` trace.
"""

import time

from llama_index.core.callbacks import CallbackManager
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

from groundly.core.store import SQLiteSubjectStore

CHANNEL_K = 50  # candidates pulled per channel before fusion
RRF_K = 60  # standard reciprocal-rank-fusion constant
RERANK_POOL = 20  # fused candidates handed to the cross-encoder
CONTEXT_K = 8  # final chunks assembled into the prompt


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion over already-ranked (best-first) id lists. Pure
    function: no I/O, easy to unit-test independent of any store."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


class VectorRetriever(BaseRetriever):
    """dense + sparse + BM25 -> RRF -> optional cross-encoder rerank -> top context_k.

    `embedder`/`reranker` default to the real (lazy-loaded) bge-m3 / bge-reranker-v2-m3
    models; tests inject stubs. `self.path` records which stages ran, for trace logging.
    """

    def __init__(
        self,
        store: SQLiteSubjectStore,
        embedder=None,
        reranker=None,
        rerank: bool = True,
        channel_k: int = CHANNEL_K,
        rerank_pool: int = RERANK_POOL,
        context_k: int = CONTEXT_K,
    ) -> None:
        super().__init__(callback_manager=CallbackManager([]))
        self.store = store
        self.rerank = rerank
        self.channel_k = channel_k
        self.rerank_pool = rerank_pool
        self.context_k = context_k
        self._embedder = embedder
        self._reranker = reranker
        self.path: list[str] = []

    @property
    def embedder(self):
        if self._embedder is None:
            from groundly.llm.embeddings import BgeM3Embedder

            self._embedder = BgeM3Embedder()
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            from groundly.llm.rerank import BgeReranker

            self._reranker = BgeReranker()
        return self._reranker

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        query = query_bundle.query_str
        dense, sparse = self.embedder.encode([query])  # one pass feeds both channels

        dense_ids = self.store.dense_search(dense[0], self.channel_k)
        sparse_ids = self.store.sparse_search(sparse[0], self.channel_k)
        bm25_ids = self.store.bm25_search(query, self.channel_k)
        path = ["dense", "sparse", "bm25", "rrf"]

        fused = rrf([dense_ids, sparse_ids, bm25_ids])[: self.rerank_pool]
        if not fused:
            self.path = path
            return []

        fused_ids = [doc_id for doc_id, _ in fused]
        fused_scores = dict(fused)
        details = {row["chunk_id"]: row for row in self.store.chunk_details(fused_ids)}

        if self.rerank:
            path.append("rerank")
            pairs = [(query, details[cid]["text"]) for cid in fused_ids if cid in details]
            scores = self.reranker.compute_score(pairs)
            ranked = sorted(zip(fused_ids, scores), key=lambda cs: cs[1], reverse=True)
        else:
            ranked = [(cid, fused_scores[cid]) for cid in fused_ids]

        self.path = path
        nodes = []
        for chunk_id, score in ranked[: self.context_k]:
            row = details.get(chunk_id)
            if row is None:  # removed between fusion and detail lookup — skip, don't crash
                continue
            node = TextNode(
                text=row["text"],
                id_=str(chunk_id),
                metadata={
                    "chunk_id": chunk_id,
                    "filename": row["filename"],
                    "page": row["page"],
                    "heading_path": row["heading_path"],
                },
            )
            nodes.append(NodeWithScore(node=node, score=float(score)))
        return nodes


def search(
    subject: str,
    query: str,
    *,
    k: int = CONTEXT_K,
    rerank: bool = True,
    embedder=None,
    reranker=None,
) -> list[NodeWithScore]:
    """The raw retrieval path: query -> ranked chunks, no LLM call, no provider
    needed. Shared by `groundly search` and the MCP `search` tool (P4)."""
    from groundly.core.store import connect_progress, record_trace
    from groundly.core.subject import Subject

    subj = Subject(subject)
    store = SQLiteSubjectStore(subj.store_db_path)
    retriever = VectorRetriever(
        store, embedder=embedder, reranker=reranker, rerank=rerank, context_k=k
    )
    start = time.monotonic()
    nodes = retriever.retrieve(query)
    latency_ms = int((time.monotonic() - start) * 1000)

    conn = connect_progress(subj.progress_db_path)
    try:
        record_trace(
            conn,
            kind="search",
            query=query,
            arm="vector",
            path=retriever.path,
            chunk_ids=[n.node.metadata["chunk_id"] for n in nodes],
            outcome="results",
            latency_ms=latency_ms,
        )
    finally:
        conn.close()
    return nodes
