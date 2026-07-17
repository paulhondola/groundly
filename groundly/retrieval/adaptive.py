"""Arm 4 (adaptive agentic): retrieve -> self-grade -> escalate/rewrite, bounded at
2 iterations. Eval-only arm; arrives with the eval harness, not needed for the
product path at P3 (docs/architecture/retrieval.md). Named stub for now, kept as a
`BaseRetriever` subclass so the four-arm interface gate holds structurally."""

from llama_index.core.callbacks import CallbackManager
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle


class AdaptiveRetriever(BaseRetriever):
    def __init__(self, subject: str) -> None:
        super().__init__(callback_manager=CallbackManager([]))
        self.subject = subject

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        raise NotImplementedError("adaptive retrieval arrives with the eval harness")
