"""Cross-encoder reranker for the retriever's fused candidate pool. Lives in llm/
alongside embeddings.py (model clients constructed only here); local and key-free,
lazy-loaded — never at import/spawn time (.claude/rules/architecture.md). Pinned at
the resolved hf_revision, same interchange-compatibility contract as bge-m3."""

import threading

from groundly.llm.embeddings import (
    FORWARD_BATCH,
    LazyModel,
    ModelDownloadError,
    ensure_downloaded,
)

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_HF_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"  # pinned 2026-07-18 (P3 start)


class BgeReranker(LazyModel):
    def __init__(self) -> None:
        super().__init__()
        # FlagReranker re-sets the shared tokenizer's truncation on every batch, so two
        # concurrent calls (FastMCP runs tools in a threadpool) would cut each other's text
        self._scoring = threading.Lock()

    def _build(self):
        local = ensure_downloaded(RERANKER_MODEL, RERANKER_HF_REVISION)
        try:
            from FlagEmbedding import FlagReranker

            return FlagReranker(str(local), use_fp16=False)
        except Exception as exc:
            raise ModelDownloadError(f"failed to load {RERANKER_MODEL}: {exc}") from exc

    def compute_score(self, pairs: list[tuple[str, str]]) -> list[float]:
        with self._use() as model, self._scoring:
            scores = model.compute_score(pairs, batch_size=FORWARD_BATCH)
        if isinstance(scores, float):
            return [scores]
        return list(scores)


_shared: BgeReranker | None = None


def shared_reranker() -> BgeReranker:
    """Process-wide singleton, like `shared_embedder()`: one resident cross-encoder serves
    every reranked call instead of a reload per `search`. Tests inject stubs via
    `reranker=`."""
    global _shared
    if _shared is None:
        _shared = BgeReranker()
    return _shared
