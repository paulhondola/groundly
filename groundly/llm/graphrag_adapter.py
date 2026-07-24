"""Translates Groundly's own provider config into graphrag's config primitives — the
one place doing so (.claude/rules/architecture.md: LLM clients constructed only in
llm/, same interpretation already implied by embeddings.py/rerank.py). graphrag's
LiteLLM-based client speaks the same OpenAI-compatible base_url+model+key shape
Groundly already assumes everywhere else.
"""

from graphrag_llm.config import ModelConfig
from graphrag_llm.embedding.embedding import LLMEmbedding
from graphrag_llm.embedding.embedding_factory import register_embedding

from groundly.llm.config import load_provider, require_provider

BGE_M3_EMBEDDING_TYPE = "bge_m3"


_LOCAL_PLACEHOLDER_KEY = "not-needed"  # LM Studio/Ollama ignore the Authorization header


def completion_model_config() -> ModelConfig:
    """Build graphrag's ModelConfig from Groundly's `extraction` provider. Fails fast
    (via require_provider) — a *configured* provider is always required, but not
    necessarily a real API key: graphrag's own ModelConfig validator rejects an empty
    api_key outright (unlike Groundly's own llm/chat.py, which just omits the
    Authorization header when `cfg.api_key` is empty), so a local/keyless provider
    (LM Studio, Ollama) needs a truthy placeholder here to pass that validation — the
    placeholder is never checked by a local server, same as the empty-header path
    already works for every other call class."""
    cfg = require_provider("extraction")
    return ModelConfig(
        model_provider="openai",
        model=cfg.model,
        api_base=cfg.base_url,
        api_key=cfg.api_key or _LOCAL_PLACEHOLDER_KEY,
    )


class Bgem3GraphEmbedding(LLMEmbedding):
    """graphrag's entity-description embedding store, delegated to the already-loaded
    BgeM3Embedder — zero marginal cost, zero new provider config (graph build stays
    cheap beyond the one extraction cost). Dense vectors only: graphrag's embedding
    store only needs similarity, not our sparse channel.

    `embedder` mirrors VectorRetriever's pattern (retrieval/vector.py): None in
    production (lazily resolves to the process-wide `shared_embedder()` singleton on
    first use, same resident model VectorRetriever uses), or a stub injected by tests.
    """

    def __init__(
        self,
        *,
        model_id: str = "",
        model_config: ModelConfig | None = None,
        tokenizer=None,
        metrics_store=None,
        embedder=None,
        **kwargs,
    ) -> None:
        self._model_id = model_id
        self._tokenizer = tokenizer
        self._metrics_store = metrics_store
        self._embedder = embedder

    @property
    def embedder(self):
        if self._embedder is None:
            from groundly.llm.embeddings import shared_embedder

            self._embedder = shared_embedder()
        return self._embedder

    def embedding(self, /, **kwargs):
        from graphrag_llm.types import LLMEmbedding as EmbeddingItem
        from graphrag_llm.types import LLMEmbeddingResponse, LLMEmbeddingUsage

        texts = kwargs["input"]
        dense, _sparse = self.embedder.encode(texts)
        data = [
            EmbeddingItem(object="embedding", embedding=vec, index=i) for i, vec in enumerate(dense)
        ]
        return LLMEmbeddingResponse(
            object="list",
            data=data,
            model=self._model_id,
            usage=LLMEmbeddingUsage(prompt_tokens=0, total_tokens=0),
        )

    async def embedding_async(self, /, **kwargs):
        return self.embedding(**kwargs)

    @property
    def metrics_store(self):
        return self._metrics_store

    @property
    def tokenizer(self):
        return self._tokenizer


def register_bge_m3_embedding() -> None:
    """Register Bgem3GraphEmbedding under the `bge_m3` strategy name. Idempotent:
    the factory's register() is a plain dict assignment (graphrag_common/factory.py),
    so calling this more than once just re-assigns the same entry."""
    register_embedding(BGE_M3_EMBEDDING_TYPE, Bgem3GraphEmbedding)


def estimate_cost(total_chars: int) -> tuple[int, float | None]:
    """Rough heuristic graph-build cost estimate: no tokenizer, no LLM call. Uses
    `load_provider` (not `require_provider`) — this is an estimate, not the fail-fast
    build path, so an unconfigured/unpriced provider degrades to (tokens, None)."""
    tokens = total_chars // 4
    cfg = load_provider("extraction")
    if cfg is None or cfg.input_price_per_mtok is None:
        return tokens, None
    return tokens, tokens * cfg.input_price_per_mtok / 1_000_000
