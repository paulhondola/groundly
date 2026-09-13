"""The vector retriever: bge-m3 dense + learned sparse + BM25, fused by reciprocal rank
fusion, then an optional cross-encoder rerank. `search()` is the zero-key shared function
behind `groundly search` and the MCP `search` tool."""

import logging

from groundly.core.store import SubjectStore
from groundly.retrieval.hits import Hit, hit_from_row

logger = logging.getLogger(__name__)

CHANNEL_K = 50  # candidates pulled per channel before fusion
RRF_K = 60  # standard reciprocal-rank-fusion constant
RERANK_POOL = 20  # fused candidates handed to the cross-encoder
CONTEXT_K = 8  # default number of hits returned


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion over already-ranked (best-first) id lists. Pure function:
    no I/O, testable without a store.

    Ties break by *how many rankings contributed*, then by id. An id at rank i in one
    list scores exactly the same as a different id at rank i in another, and a stable
    sort would then hand rank 1 to whichever list was passed first; agreement across
    channels is the honest tie-break, and the id keeps the order deterministic."""
    scores: dict[int, float] = {}
    votes: dict[int, int] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
            votes[doc_id] = votes.get(doc_id, 0) + 1
    return sorted(scores.items(), key=lambda kv: (kv[1], votes[kv[0]], -kv[0]), reverse=True)


class VectorRetriever:
    """dense + sparse + BM25 -> RRF -> optional cross-encoder rerank -> top context_k.

    `embedder`/`reranker` default to the lazily loaded bge-m3 / bge-reranker-v2-m3
    models; tests inject stubs."""

    def __init__(
        self,
        store: SubjectStore,
        embedder=None,
        reranker=None,
        rerank: bool | None = None,
        channel_k: int = CHANNEL_K,
        rerank_pool: int = RERANK_POOL,
        context_k: int | None = None,
    ) -> None:
        # rerank/context_k default from config (retrieval.*); explicit args override.
        if rerank is None or context_k is None:
            from groundly.core.config import load_settings

            settings = load_settings().retrieval
            rerank = settings.rerank if rerank is None else rerank
            context_k = settings.context_k if context_k is None else context_k
        self.store = store
        self.rerank = rerank
        self.channel_k = channel_k
        self.rerank_pool = rerank_pool
        self.context_k = context_k
        self._embedder = embedder
        self._reranker = reranker

    @property
    def embedder(self):
        if self._embedder is None:
            from groundly.llm.embeddings import shared_embedder

            self._embedder = shared_embedder()
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is None:
            from groundly.llm.rerank import BgeReranker

            self._reranker = BgeReranker()
        return self._reranker

    def retrieve(self, query: str) -> list[Hit]:
        dense, sparse = self.embedder.encode([query])  # one pass feeds both channels

        dense_ids = self.store.dense_search(dense[0], self.channel_k)
        sparse_ids = self.store.sparse_search(sparse[0], self.channel_k)
        bm25_ids = self.store.bm25_search(query, self.channel_k)
        stages = ["dense", "sparse", "bm25", "rrf"]
        logger.debug(
            "channel hits: dense=%d sparse=%d bm25=%d",
            len(dense_ids),
            len(sparse_ids),
            len(bm25_ids),
        )

        fused = rrf([dense_ids, sparse_ids, bm25_ids])[: self.rerank_pool]
        logger.debug("fused pool size=%d rerank=%s", len(fused), self.rerank)
        if not fused:
            return []

        fused_ids = [doc_id for doc_id, _ in fused]
        fused_scores = dict(fused)
        details = {row["chunk_id"]: row for row in self.store.chunk_details(fused_ids)}

        if self.rerank:
            stages.append("rerank")
            pairs = [(query, details[cid]["text"]) for cid in fused_ids if cid in details]
            scores = self.reranker.compute_score(pairs)
            ranked = sorted(zip(fused_ids, scores), key=lambda cs: cs[1], reverse=True)
        else:
            ranked = [(cid, fused_scores[cid]) for cid in fused_ids]

        hits = []
        for chunk_id, score in ranked[: self.context_k]:
            row = details.get(chunk_id)
            if row is None:  # removed between fusion and detail lookup — skip, don't crash
                logger.debug("chunk %s vanished between fusion and detail lookup", chunk_id)
                continue
            hits.append(hit_from_row(row, score))
        logger.debug("stages=%s top=%s", stages, [(h.chunk_id, h.score) for h in hits])
        return hits


def search(
    subject: str,
    query: str,
    *,
    k: int | None = None,
    rerank: bool | None = None,
    embedder=None,
    reranker=None,
) -> list[Hit]:
    """The raw retrieval path: query -> ranked hits, no LLM call, no provider needed,
    nothing written. Shared by `groundly search` and the MCP `search` tool."""
    from groundly.core.subject import Subject

    store = SubjectStore(Subject(subject).store_db_path)
    retriever = VectorRetriever(
        store, embedder=embedder, reranker=reranker, rerank=rerank, context_k=k
    )
    return retriever.retrieve(query)
