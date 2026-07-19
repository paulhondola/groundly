"""groundly/retrieval/graph.py and adaptive.py: named stubs, real impl arrives P5
(graph) / eval-start (adaptive). All four arms must be BaseRetriever subclasses —
the "four arms, one interface" gate is structural, checked here even for stubs."""

import pytest
from llama_index.core.retrievers import BaseRetriever

from groundly.retrieval.adaptive import AdaptiveRetriever
from groundly.retrieval.graph import GraphGlobalRetriever, GraphLocalRetriever, GraphNotBuiltError
from groundly.retrieval.vector import VectorRetriever


@pytest.mark.parametrize(
    "cls", [VectorRetriever, GraphLocalRetriever, GraphGlobalRetriever, AdaptiveRetriever]
)
def test_all_four_arms_are_base_retriever_subclasses(cls):
    assert issubclass(cls, BaseRetriever)


@pytest.mark.parametrize("cls", [GraphLocalRetriever, GraphGlobalRetriever])
def test_graph_arms_raise_named_error_on_retrieve(cls):
    retriever = cls(subject="TEST")
    with pytest.raises(GraphNotBuiltError, match="graph search arrives in P5"):
        retriever.retrieve("what causes deadlocks?")


def test_adaptive_retriever_raises_stub_not_implemented():
    retriever = AdaptiveRetriever(subject="TEST")
    with pytest.raises(NotImplementedError):
        retriever.retrieve("what causes deadlocks?")
