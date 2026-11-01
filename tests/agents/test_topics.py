"""Contract tests for the topic gate: `submit_tree` stores a tree only when every node is
well-formed and a reranked search over the course finds it, and the sources are always
chosen by that search, never by the host. `outline` is the host's read-only view."""

import json

import pytest

from groundly.agents.topics import (
    GATE_RERANK_POOL,
    MAX_SOURCES,
    MAX_SUMMARY_CHARS,
    TOPIC_RELEVANCE_CUTOFF,
    outline,
    submit_tree,
)
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import subject_dir
from groundly.core.store import SubjectStore, TopicWrite
from groundly.core.subject import Subject
from groundly.ingestion.extract import ChunkData

ON_TOPIC = "Deadlocks"
OFF_TOPIC = "French Revolution"


class _Embedder:
    """Aims every query at chunk 1 of the retrievable_subject fixture."""

    def encode(self, texts):
        return [[1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2) for _ in texts], [{1: 0.9} for _ in texts]


class _FnReranker:
    """Scores each (query, text) pair with `fn`, so a test can give chunks different scores."""

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def compute_score(self, pairs):
        self.calls += 1
        return [self.fn(q, t) for q, t in pairs]


def _by_query(q, _text):
    return -5.0 if OFF_TOPIC in q else 3.0


class _Forbidden:
    def encode(self, texts):
        raise AssertionError("the embedder must not run")

    def compute_score(self, pairs):
        raise AssertionError("the reranker must not run")


@pytest.fixture
def store(retrievable_subject):
    return SubjectStore(subject_dir(retrievable_subject) / "store.db")


def _node(title=ON_TOPIC, summary="Why threads wait on each other forever", **extra):
    return {"title": title, "summary": summary, **extra}


def _submit(store, tree, reranker=None):
    return submit_tree(
        store, tree, embedder=_Embedder(), reranker=reranker or _FnReranker(_by_query)
    )


def _reasons(result):
    assert result["accepted"] is False
    return [r["reason"] for r in result["rejections"]]


def test_constants():
    assert (TOPIC_RELEVANCE_CUTOFF, MAX_SOURCES, MAX_SUMMARY_CHARS, GATE_RERANK_POOL) == (
        1.0,
        5,
        500,
        20,
    )


def test_accepted_tree_stores_up_to_five_sources_chosen_by_search(store):
    result = _submit(store, [_node(subtopics=[_node("Detection", "Finding the cycle")])])
    assert result["accepted"] is True
    (big,) = store.tree()
    (sub,) = big["subtopics"]
    assert len(big["sources"]) == 3  # all the course has, within the cap of 5
    assert {s["filename"] for s in big["sources"]} == {"lec.pdf"}
    assert len(sub["sources"]) == 3
    assert result["tree"][0]["id"] == big["id"]
    assert result["tree"][0]["source_count"] == 3
    assert result["tree"][0]["subtopics"][0]["title"] == "Detection"


def test_sources_are_capped_at_max_sources(store):
    chunks = [ChunkData(f"more deadlock text {i}", "Extra", i, 5) for i in range(1, 7)]
    vectors = [([0.5, 0.5] + [0.0] * (EMBEDDING_DIM - 2), {1: 0.2})] * 6
    store.add_indexed("extra.pdf", "c" * 64, 6, chunks, vectors)
    assert _submit(store, [_node()])["accepted"] is True
    assert len(store.tree()[0]["sources"]) == MAX_SOURCES


def test_host_cannot_supply_citations(store):
    result = _submit(store, [_node(chunk_ids=[999], sources=[{"chunk_id": 999}])])
    assert result["accepted"] is True
    assert 999 not in {s["chunk_id"] for s in store.tree()[0]["sources"]}


def test_score_exactly_at_cutoff_passes_and_below_is_rejected(store):
    at = _submit(store, [_node()], _FnReranker(lambda q, t: TOPIC_RELEVANCE_CUTOFF))
    assert at["accepted"] is True
    store.replace_tree([])
    below = _submit(store, [_node()], _FnReranker(lambda q, t: 0.99))
    assert _reasons(below) == ["below_relevance_cutoff"]
    assert store.tree() == []


def test_only_hits_at_or_above_cutoff_become_sources(store):
    def fn(_query, text):
        if text.startswith("deadlock needs"):
            return 4.0
        return 0.5 if text.startswith("semaphores") else 1.0

    assert _submit(store, [_node()], _FnReranker(fn))["accepted"] is True
    texts = [s["text"] for s in store.tree()[0]["sources"]]
    assert texts[0].startswith("deadlock needs")  # best first
    assert len(texts) == 2
    assert not any(t.startswith("semaphores") for t in texts)


def test_off_topic_node_rejects_whole_tree_with_score_and_nearest(store):
    result = _submit(store, [_node(), _node(OFF_TOPIC, "Revolution in 1789")])
    assert _reasons(result) == ["below_relevance_cutoff"]
    (rej,) = result["rejections"]
    assert rej["path"] == [OFF_TOPIC]
    assert rej["score"] == -5.0
    assert rej["nearest"]["filename"] == "lec.pdf"
    assert {"page", "heading_path"} <= rej["nearest"].keys()
    assert store.tree() == []


def test_every_failing_node_is_reported_at_once(store):
    tree = [
        _node(),
        _node(OFF_TOPIC, "Revolution in 1789", subtopics=[_node("Reign of Terror", OFF_TOPIC)]),
    ]
    result = _submit(store, tree)
    assert _reasons(result) == ["below_relevance_cutoff"] * 2
    assert [r["path"] for r in result["rejections"]] == [
        [OFF_TOPIC],
        [OFF_TOPIC, "Reign of Terror"],
    ]
    assert store.tree() == []


def test_subject_with_no_chunks_rejects_with_null_nearest(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    Subject("EMPTY").initialize()
    empty = SubjectStore(subject_dir("EMPTY") / "store.db")
    result = _submit(empty, [_node()])
    assert _reasons(result) == ["below_relevance_cutoff"]
    assert result["rejections"][0]["score"] is None
    assert result["rejections"][0]["nearest"] is None
    assert empty.tree() == []


@pytest.mark.parametrize("tree", [[], None])
def test_empty_tree_rejected(store, tree):
    assert _reasons(_submit(store, tree)) == ["empty_tree"]


def test_blank_title_summary_over_500_and_non_string_are_invalid_text(store):
    assert _submit(store, [_node(summary="x" * MAX_SUMMARY_CHARS)])["accepted"] is True
    store.replace_tree([])
    bad = [
        _node(title="  "),
        _node(summary=""),
        _node(summary="x" * (MAX_SUMMARY_CHARS + 1)),
        _node(title=7),
        _node(summary=None),
        "not a node",
    ]
    for node in bad:
        result = _submit(store, [node])
        assert _reasons(result) == ["invalid_text"], node
        assert store.tree() == []


def test_unknown_and_duplicate_ids_rejected(store):
    _submit(store, [_node(), _node("Semaphores", "Mutexes and semaphores")])
    first, second = store.tree()
    before = store.tree()

    unknown = _submit(store, [_node(id=first["id"] + 100)])
    assert _reasons(unknown) == ["unknown_topic_id"]
    assert _reasons(_submit(store, [_node(id="1")])) == ["unknown_topic_id"]

    dup = _submit(
        store,
        [
            _node(id=first["id"]),
            _node("Semaphores", id=second["id"], subtopics=[_node("Again", "x", id=first["id"])]),
        ],
    )
    assert _reasons(dup) == ["duplicate_topic_id"]
    assert dup["rejections"][0]["path"] == ["Semaphores", "Again"]
    assert store.tree() == before


def test_known_ids_are_kept(store):
    _submit(store, [_node()])
    (old,) = store.tree()
    result = _submit(store, [_node(id=old["id"], summary="Reworded")])
    assert result["accepted"] is True
    (new,) = store.tree()
    assert (new["id"], new["summary"]) == (old["id"], "Reworded")


def test_third_level_rejected_as_too_deep(store):
    tree = [_node(subtopics=[_node("Detection", "Finding the cycle", subtopics=[_node("Deeper")])])]
    result = _submit(store, tree)
    assert _reasons(result) == ["too_deep"]
    assert result["rejections"][0]["path"] == [ON_TOPIC, "Detection"]
    assert store.tree() == []


def test_structural_failures_never_touch_the_models(store):
    forbidden = _Forbidden()
    result = submit_tree(store, [_node(), _node(id=999)], embedder=forbidden, reranker=forbidden)
    assert _reasons(result) == ["unknown_topic_id"]


def test_reranker_runs_even_when_config_disables_it(store):
    from groundly.core.config import set_key

    set_key("retrieval.rerank", "false")
    reranker = _FnReranker(_by_query)
    result = _submit(store, [_node(OFF_TOPIC, "Revolution in 1789")], reranker)
    assert reranker.calls == 1
    assert result["rejections"][0]["score"] == -5.0


def test_big_topic_without_subtopics_is_accepted(store):
    assert _submit(store, [_node()])["accepted"] is True
    assert store.tree()[0]["subtopics"] == []


def test_outline_lists_headings_and_current_tree_without_chunk_ids_or_text(store):
    store.add_extraction_failed("broken.pdf", "b" * 64, "no readable text")
    store.replace_tree(
        [TopicWrite(None, "Deadlocks", "Why", [1, 3], [TopicWrite(None, "Cycle", "c", [])])]
    )
    result = outline(store)
    assert result["materials"] == [
        {
            "filename": "lec.pdf",
            "pages": 3,
            "cited_chunks": 2,
            "headings": ["p1: Intro > Deadlocks", "p2: Intro > Sync", "p3: Intro > Deadlocks"],
        }
    ]
    (big,) = result["tree"]
    assert (big["title"], big["summary"], big["source_count"]) == ("Deadlocks", "Why", 2)
    assert big["subtopics"][0]["title"] == "Cycle"
    assert big["subtopics"][0]["source_count"] == 0
    dumped = json.dumps(result)
    assert "chunk_id" not in dumped
    assert "text" not in dumped
    assert "deadlock needs" not in dumped


def test_outline_heading_without_a_page_is_the_text_alone(store):
    with store._open() as conn, conn:
        conn.execute("UPDATE chunks SET page = NULL WHERE id = 2")
    (material,) = outline(store)["materials"]
    assert "Intro > Sync" in material["headings"]
