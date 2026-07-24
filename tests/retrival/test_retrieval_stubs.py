"""groundly/retrieval/adaptive.py: named stub, real impl arrives at eval-start.
Also the "four arms, one interface" structural gate — all four arms must be
BaseRetriever subclasses (the graph arms' real behavior is exercised in
tests/retrival/test_retrieval_graph.py, not here)."""

import pytest
from llama_index.core.retrievers import BaseRetriever

from groundly.retrieval.adaptive import AdaptiveRetriever
from groundly.retrieval.graph import GraphGlobalRetriever, GraphLocalRetriever
from groundly.retrieval.vector import VectorRetriever


@pytest.mark.parametrize(
    "cls", [VectorRetriever, GraphLocalRetriever, GraphGlobalRetriever, AdaptiveRetriever]
)
def test_all_four_arms_are_base_retriever_subclasses(cls):
    assert issubclass(cls, BaseRetriever)


def test_adaptive_retriever_raises_stub_not_implemented():
    retriever = AdaptiveRetriever(subject="TEST")
    with pytest.raises(NotImplementedError):
        retriever.retrieve("what causes deadlocks?")
