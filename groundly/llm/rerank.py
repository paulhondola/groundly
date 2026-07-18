"""Cross-encoder reranker for the vector arm's fused candidate pool. Lives in llm/
alongside embeddings.py (model clients constructed only here); local and key-free,
lazy-loaded — never at import/spawn time (.claude/rules/architecture.md). Pinned at
the resolved hf_revision, same interchange-compatibility contract as bge-m3."""

from typing import Protocol

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_HF_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"  # pinned 2026-07-18 (P3 start)


class Reranker(Protocol):
    def compute_score(self, pairs: list[tuple[str, str]]) -> list[float]: ...


class BgeReranker:
    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is None:
            from groundly.llm.embeddings import ModelDownloadError, ensure_downloaded

            local = ensure_downloaded(RERANKER_MODEL, RERANKER_HF_REVISION)
            try:
                from FlagEmbedding import FlagReranker

                self._model = FlagReranker(str(local), use_fp16=False)
            except Exception as exc:
                raise ModelDownloadError(f"failed to load {RERANKER_MODEL}: {exc}") from exc
        return self._model

    def compute_score(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores = self._load().compute_score(pairs)
        if isinstance(scores, float):
            return [scores]
        return list(scores)
