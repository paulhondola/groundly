"""groundly/ingestion/graph.py: the graphrag batch builder. `build_index` is always
monkeypatched here — no test ever runs a real graphrag pipeline or hits a real model
(matches the discipline around complete/classify/extractors/embedders elsewhere)."""

import pytest

from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.store import SQLiteSubjectStore
from groundly.core.subject import Subject, init_subject
from groundly.ingestion.extract import ChunkData
from groundly.ingestion.graph import GraphBuildError, build_graph, corpus_hash, graph_is_stale


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def _add_material(store: SQLiteSubjectStore, filename: str, sha256: str, status: str = "indexed"):
    if status == "indexed":
        chunks = [ChunkData("some chunk text", "Intro", 1, 5)]
        dense = [[0.1] * EMBEDDING_DIM]
        sparse = [{1: 0.5}]
        store.add_indexed(filename, sha256, 1, chunks, zip(dense, sparse))
    else:
        store.add_extraction_failed(filename, sha256, "boom")


@pytest.fixture
def subj():
    init_subject("TEST")
    return Subject("TEST")


@pytest.fixture
def store(subj):
    return SQLiteSubjectStore(subj.store_db_path)


def _configure_extraction(home, model="gpt-4o-mini"):
    (home / "config.toml").write_text(
        f'[providers.extraction]\nbase_url = "http://x"\nmodel = "{model}"\napi_key = "sk-secret"\n'
    )


# --- corpus_hash ---------------------------------------------------------------------


def test_corpus_hash_stable_for_same_materials(store):
    _add_material(store, "a.pdf", "a" * 64)
    _add_material(store, "b.pdf", "b" * 64)
    assert corpus_hash(store) == corpus_hash(store)


def test_corpus_hash_matches_sorted_sha256_formula(store):
    """Guards the exact formula: sha256("\\n".join(sorted(sha256s))) — insertion
    order into materials must not affect the resulting hash."""
    import hashlib

    _add_material(store, "z_first_inserted.pdf", "b" * 64)
    _add_material(store, "a_second_inserted.pdf", "a" * 64)
    expected = hashlib.sha256("\n".join(sorted(["b" * 64, "a" * 64])).encode()).hexdigest()
    assert corpus_hash(store) == expected


def test_corpus_hash_changes_when_material_added(store):
    _add_material(store, "a.pdf", "a" * 64)
    before = corpus_hash(store)
    _add_material(store, "b.pdf", "b" * 64)
    after = corpus_hash(store)
    assert before != after


def test_corpus_hash_ignores_extraction_failed_materials(store):
    _add_material(store, "a.pdf", "a" * 64)
    before = corpus_hash(store)
    _add_material(store, "bad.pdf", "b" * 64, status="extraction_failed")
    after = corpus_hash(store)
    assert before == after


# --- graph_is_stale --------------------------------------------------------------------


def test_graph_is_stale_true_when_no_build_recorded_and_corpus_nonempty(subj, store):
    _add_material(store, "a.pdf", "a" * 64)
    assert graph_is_stale(subj, store) is True  # corpus_hash None != real hash


def test_graph_is_stale_false_when_hash_matches_and_dir_exists(subj, store):
    _add_material(store, "a.pdf", "a" * 64)
    (subj.root_dir / "graph").mkdir()
    manifest = subj.load_manifest()
    manifest.graphrag.corpus_hash = corpus_hash(store)
    subj.save_manifest(manifest)
    assert graph_is_stale(subj, store) is False


def test_graph_is_stale_true_when_corpus_changed(subj, store):
    _add_material(store, "a.pdf", "a" * 64)
    (subj.root_dir / "graph").mkdir()
    manifest = subj.load_manifest()
    manifest.graphrag.corpus_hash = corpus_hash(store)
    subj.save_manifest(manifest)

    _add_material(store, "b.pdf", "b" * 64)
    assert graph_is_stale(subj, store) is True


def test_graph_is_stale_true_when_graph_dir_deleted_externally(subj, store):
    _add_material(store, "a.pdf", "a" * 64)
    manifest = subj.load_manifest()
    manifest.graphrag.corpus_hash = corpus_hash(store)  # a build was recorded...
    subj.save_manifest(manifest)
    # ...but graph/ was never created (or got deleted) — defensive staleness
    assert not (subj.root_dir / "graph").exists()
    assert graph_is_stale(subj, store) is True


# --- build_graph -----------------------------------------------------------------------


def test_build_graph_fails_fast_without_extraction_provider(subj, store):
    from groundly.core.config import ProviderNotConfiguredError

    _add_material(store, "a.pdf", "a" * 64)
    with pytest.raises(ProviderNotConfiguredError):
        build_graph(subj, store)


def test_build_graph_feeds_input_documents_and_records_manifest(subj, store, home, monkeypatch):
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    captured = {}

    async def fake_build_index(config, input_documents=None):
        captured["config"] = config
        captured["input_documents"] = input_documents
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    build_graph(subj, store)

    df = captured["input_documents"]
    rows = store.all_chunks()
    assert list(df["id"]) == [str(r["chunk_id"]) for r in rows]
    assert list(df["text"]) == [r["text"] for r in rows]
    assert list(df["title"]) == [f"{r['filename']}#p{r['page']}" for r in rows]

    config = captured["config"]
    assert config.chunking.size == 4096
    assert config.chunking.overlap == 0
    assert config.embedding_models["default_embedding_model"].type == "bge_m3"
    assert config.completion_models["default_completion_model"].model == "gpt-4o-mini"
    assert str(subj.root_dir / "graph" / "lancedb") == config.vector_store.db_uri
    assert config.vector_store.vector_size == EMBEDDING_DIM

    manifest = subj.load_manifest()
    assert manifest.graphrag.extraction_model == "gpt-4o-mini"
    assert manifest.graphrag.corpus_hash == corpus_hash(store)
    assert manifest.graphrag.version  # graphrag's installed package version


def test_build_graph_records_one_index_trace_row_on_success(subj, store, home, monkeypatch):
    from groundly.core.store import connect_progress

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def fake_build_index(config, input_documents=None):
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    build_graph(subj, store, estimated_tokens=123, estimated_cost_usd=0.0045)

    conn = connect_progress(subj.progress_db_path)
    try:
        rows = conn.execute("SELECT * FROM traces").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "index"
    assert row["outcome"] == "built"
    assert row["arm"] == "graph-build"
    assert row["model"] == "gpt-4o-mini"
    assert row["tokens"] == 123
    assert row["cost_usd"] == 0.0045


def test_build_graph_wraps_failure_in_graph_build_error(subj, store, home, monkeypatch):
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def failing_build_index(config, input_documents=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("groundly.ingestion.graph.build_index", failing_build_index)

    with pytest.raises(GraphBuildError):
        build_graph(subj, store)

    # manifest must be untouched on failure
    manifest = subj.load_manifest()
    assert manifest.graphrag.corpus_hash is None
