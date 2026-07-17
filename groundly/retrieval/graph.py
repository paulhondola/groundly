"""Arm 2 (graph backend): MS graphrag local/global search. Named stub at P3 — the
real per-subject batch indexer and local/global search land in P5
(docs/architecture/retrieval.md). Subclassing BaseRetriever now keeps the "four
arms, one interface" gate structural even before the implementation exists."""

from llama_index.core.callbacks import CallbackManager
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle


class GraphNotBuiltError(Exception):
    """Raised by graph arms until P5 ships graphrag indexing + local/global search."""

    def __init__(self, message: str = "graph search arrives in P5") -> None:
        super().__init__(f"graph not built for this subject — {message}")


class _GraphRetrieverStub(BaseRetriever):
    def __init__(self, subject: str) -> None:
        super().__init__(callback_manager=CallbackManager([]))
        self.subject = subject

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        raise GraphNotBuiltError()


class GraphLocalRetriever(_GraphRetrieverStub):
    """Entity-anchored local search — multi-hop queries. Stub until P5."""


class GraphGlobalRetriever(_GraphRetrieverStub):
    """Community-summary global search — synthesis queries. Stub until P5."""
