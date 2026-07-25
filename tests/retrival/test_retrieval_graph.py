"""groundly/retrieval/graph.py: real GraphLocalRetriever/GraphGlobalRetriever
implementations. graphrag's query API (`local_search`/`global_search`) is always
monkeypatched at the module's import site — no test touches a real graphrag
pipeline or a real cloud model."""

import pandas as pd
import pytest
from graphrag_llm.config import ModelConfig
from llama_index.core.retrievers import BaseRetriever

import groundly.retrieval.graph as graph_module
from groundly.core.paths import subject_dir
from groundly.retrieval.graph import GraphGlobalRetriever, GraphLocalRetriever, GraphNotBuiltError


@pytest.fixture(autouse=True)
def stub_completion_model_config(monkeypatch):
    """The graph query config always builds a completion model config (local/global
    search both do their own synthesis LLM call) — stub it so tests never need a
    real `extraction` provider configured."""
    monkeypatch.setattr(
        graph_module,
        "completion_model_config",
        lambda: ModelConfig(model_provider="openai", model="stub-model", api_key="test-key"),
    )


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in columns})


def _write_graph_artifacts(
    subject: str,
    *,
    entities: pd.DataFrame,
    communities: pd.DataFrame,
    community_reports: pd.DataFrame,
    text_units: pd.DataFrame,
    relationships: pd.DataFrame,
) -> None:
    graph_dir = subject_dir(subject) / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    entities.to_parquet(graph_dir / "entities.parquet")
    communities.to_parquet(graph_dir / "communities.parquet")
    community_reports.to_parquet(graph_dir / "community_reports.parquet")
    text_units.to_parquet(graph_dir / "text_units.parquet")
    relationships.to_parquet(graph_dir / "relationships.parquet")


# --- structural gate + precondition ---------------------------------------------------


@pytest.mark.parametrize("cls", [GraphLocalRetriever, GraphGlobalRetriever])
def test_graph_arms_are_base_retriever_subclasses(cls):
    assert issubclass(cls, BaseRetriever)


@pytest.mark.parametrize("cls", [GraphLocalRetriever, GraphGlobalRetriever])
def test_graph_not_built_raises_when_graph_dir_missing(cls, subject):
    retriever = cls(subject=subject)
    with pytest.raises(GraphNotBuiltError, match="graph not built for this subject"):
        retriever.retrieve("what causes deadlocks?")


# --- GraphLocalRetriever ---------------------------------------------------------------


def test_graph_local_retriever_resolves_text_units_to_chunks(monkeypatch, retrievable_subject):
    text_units = pd.DataFrame({"id": ["tu-0", "tu-1"], "document_id": ["1", "2"]})
    _write_graph_artifacts(
        retrievable_subject,
        entities=_empty_frame(["id", "title"]),
        communities=_empty_frame(["community", "level", "entity_ids"]),
        community_reports=_empty_frame(["id", "title"]),
        text_units=text_units,
        relationships=_empty_frame(["id"]),
    )

    async def fake_local_search(**kwargs):
        # "id" here is graphrag's own text-unit short_id: a positional index into
        # the text_units DataFrame we passed in, not the chunk_id.
        sources = pd.DataFrame({"id": ["0"], "text": ["irrelevant prompt text"]})
        return "answer", {"sources": sources}

    monkeypatch.setattr(graph_module, "local_search", fake_local_search)

    retriever = GraphLocalRetriever(subject=retrievable_subject)
    nodes = retriever.retrieve("what causes deadlocks?")

    assert len(nodes) == 1
    node = nodes[0].node
    assert node.metadata["chunk_id"] == 1
    assert node.metadata["filename"] == "lec.pdf"
    assert node.metadata["page"] == 1
    assert node.metadata["heading_path"] == "Intro > Deadlocks"
    assert "mutual exclusion" in node.get_content()
    assert retriever.path == ["graphrag-local", "entity-search", "text-unit-resolve"]


def test_graph_local_retriever_empty_sources_returns_no_nodes(monkeypatch, retrievable_subject):
    _write_graph_artifacts(
        retrievable_subject,
        entities=_empty_frame(["id", "title"]),
        communities=_empty_frame(["community", "level", "entity_ids"]),
        community_reports=_empty_frame(["id", "title"]),
        text_units=_empty_frame(["id", "document_id"]),
        relationships=_empty_frame(["id"]),
    )

    async def fake_local_search(**kwargs):
        return "not covered by the course materials", {"sources": pd.DataFrame()}

    monkeypatch.setattr(graph_module, "local_search", fake_local_search)

    retriever = GraphLocalRetriever(subject=retrievable_subject)
    assert retriever.retrieve("anything") == []


# --- GraphGlobalRetriever ---------------------------------------------------------------


def test_graph_global_retriever_resolves_communities_to_chunks(monkeypatch, retrievable_subject):
    entities = pd.DataFrame(
        {
            "id": ["e1", "e2"],
            "title": ["Entity One", "Entity Two"],
            "human_readable_id": [0, 1],
            "type": ["concept", "concept"],
            "description": ["d1", "d2"],
            "degree": [1, 1],
            "description_embedding": [None, None],
            "text_unit_ids": [["tu-0"], ["tu-1"]],
        }
    )
    communities = pd.DataFrame(
        {
            "community": [0, 0],
            "level": [0, 0],
            "entity_ids": [["e1"], ["e2"]],
        }
    )
    text_units = pd.DataFrame({"id": ["tu-0", "tu-1"], "document_id": ["1", "2"]})

    _write_graph_artifacts(
        retrievable_subject,
        entities=entities,
        communities=communities,
        community_reports=_empty_frame(["id", "title"]),
        text_units=text_units,
        relationships=_empty_frame(["id"]),
    )

    async def fake_global_search(**kwargs):
        # community "id" is graphrag's community_reports.short_id == the community id.
        reports = pd.DataFrame({"id": ["0"], "title": ["Deadlocks Overview"]})
        return "answer", {"reports": reports}

    monkeypatch.setattr(graph_module, "global_search", fake_global_search)

    retriever = GraphGlobalRetriever(subject=retrievable_subject)
    nodes = retriever.retrieve("summarize the course")

    ids = sorted(n.node.metadata["chunk_id"] for n in nodes)
    assert ids == [1, 2]
    assert retriever.communities == [{"id": "0", "title": "Deadlocks Overview"}]
    assert retriever.path == [
        "graphrag-global",
        "community-search",
        "entity-resolve",
        "text-unit-resolve",
    ]


def test_graph_global_retriever_empty_reports_returns_no_nodes(monkeypatch, retrievable_subject):
    _write_graph_artifacts(
        retrievable_subject,
        entities=_empty_frame(["id", "title"]),
        communities=_empty_frame(["community", "level", "entity_ids"]),
        community_reports=_empty_frame(["id", "title"]),
        text_units=_empty_frame(["id", "document_id"]),
        relationships=_empty_frame(["id"]),
    )

    async def fake_global_search(**kwargs):
        return "not covered by the course materials", {"reports": pd.DataFrame()}

    monkeypatch.setattr(graph_module, "global_search", fake_global_search)

    retriever = GraphGlobalRetriever(subject=retrievable_subject)
    assert retriever.retrieve("anything") == []
    assert retriever.communities == []
