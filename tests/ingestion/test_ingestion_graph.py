"""groundly/ingestion/graph.py: the graphrag batch builder. `build_index` is always
monkeypatched here — no test ever runs a real graphrag pipeline or hits a real model
(matches the discipline around complete/classify/extractors/embedders elsewhere)."""

import logging
from pathlib import Path

import pytest

from groundly.core.config import set_key
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.store import SQLiteSubjectStore
from groundly.core.subject import Subject, init_subject
from groundly.ingestion.extract import ChunkData
from groundly.ingestion.graph import (
    GraphBuildError,
    build_graph,
    corpus_hash,
    current_extraction_fingerprint,
    graph_is_stale,
)
from groundly.llm.graphrag_adapter import extraction_entity_types

_EXTRACT_ERR = "graphrag.index.operations.extract_graph.graph_extractor"
_COMMUNITY_ERR = "graphrag.index.operations.summarize_communities.community_reports_extractor"


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture(autouse=True)
def stub_probe(monkeypatch):
    """build_graph probes the provider before running the pipeline — a real extraction
    prompt plus a structured-output capability call. These tests point at an unreachable
    fake provider, so stub it; probe-specific tests override this with their own fake.

    **kwargs absorbs response_format=CommunityReportResponse (the second call). Without
    this fixture the probe reaches the network, which is how `http://x` connection errors
    show up in tests that look unrelated."""
    from groundly.llm.chat import ChatResult

    monkeypatch.setattr(
        "groundly.llm.chat.complete",
        lambda call_class, messages, **kwargs: ChatResult(
            text="ok", tokens=1, cost_usd=None, model="stub"
        ),
    )


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


def _add_chunks(store, n: int) -> None:
    """n indexed materials, one chunk each — the failure gates work on chunk counts."""
    for i in range(n):
        _add_material(store, f"f{i}.pdf", f"{i:064d}")


def _write_entities(subj) -> None:
    """A real graphrag run leaves entities.parquet behind, and build_graph refuses to
    stamp the manifest without it, so success-path fakes must produce it too."""
    import pandas as pd

    graph_dir = subj.root_dir / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"title": ["Mutex"], "type": ["concept"]}).to_parquet(
        graph_dir / "entities.parquet"
    )


def _write_communities(subj, n: int, reports: int) -> None:
    import pandas as pd

    graph_dir = subj.root_dir / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"community": list(range(n)), "level": [0] * n}).to_parquet(
        graph_dir / "communities.parquet"
    )
    if reports:
        pd.DataFrame({"community": list(range(reports)), "summary": ["s"] * reports}).to_parquet(
            graph_dir / "community_reports.parquet"
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


def _record_build(subj, store):
    """Stamp the manifest the way a successful build_graph does — both the corpus hash
    and the extraction fingerprint, in one write."""
    manifest = subj.load_manifest()
    manifest.graphrag.corpus_hash = corpus_hash(store)
    manifest.graphrag.extraction_fingerprint = current_extraction_fingerprint()
    subj.save_manifest(manifest)


def test_graph_is_stale_true_when_no_build_recorded_and_corpus_nonempty(subj, store):
    _add_material(store, "a.pdf", "a" * 64)
    assert graph_is_stale(subj, store) == "no graph has been recorded for this subject"


def test_graph_is_stale_false_when_hash_matches_and_dir_exists(subj, store):
    _add_material(store, "a.pdf", "a" * 64)
    (subj.root_dir / "graph").mkdir()
    _record_build(subj, store)
    assert graph_is_stale(subj, store) is None


def test_graph_is_stale_true_when_corpus_changed(subj, store):
    _add_material(store, "a.pdf", "a" * 64)
    (subj.root_dir / "graph").mkdir()
    _record_build(subj, store)

    _add_material(store, "b.pdf", "b" * 64)
    assert graph_is_stale(subj, store) == "the corpus changed since the last build"


def test_graph_is_stale_true_when_graph_dir_deleted_externally(subj, store):
    _add_material(store, "a.pdf", "a" * 64)
    _record_build(subj, store)  # a build was recorded...
    # ...but graph/ was never created (or got deleted) — defensive staleness
    assert not (subj.root_dir / "graph").exists()
    assert graph_is_stale(subj, store) == "the graph directory is missing"


def test_graph_is_stale_when_entity_types_change(subj, store, home):
    """The load-bearing case: the corpus is untouched, but the graph was built looking
    for different things. Without the fingerprint this returned None and every later
    query answered from a graph built under different framing."""
    _add_material(store, "a.pdf", "a" * 64)
    (subj.root_dir / "graph").mkdir()
    _record_build(subj, store)
    assert graph_is_stale(subj, store) is None

    set_key("graph.entity_types", "concept,algorithm")
    assert graph_is_stale(subj, store) == (
        "the extraction prompt or entity types changed since the last build"
    )


def test_graph_is_stale_when_the_extraction_prompt_changes(subj, store, home, tmp_path):
    _add_material(store, "a.pdf", "a" * 64)
    (subj.root_dir / "graph").mkdir()
    _record_build(subj, store)

    custom = tmp_path / "custom.txt"
    custom.write_text("Types: [{entity_types}]\nText: {input_text}\nOutput:")
    set_key("graph.extraction_prompt", str(custom))
    assert graph_is_stale(subj, store) == (
        "the extraction prompt or entity types changed since the last build"
    )


def test_reordering_entity_types_counts_as_a_change(subj, store, home):
    """The fingerprint hashes the joined types in order, not sorted: graphrag
    interpolates them in order, so a reorder genuinely changes the prompt sent."""
    _add_material(store, "a.pdf", "a" * 64)
    (subj.root_dir / "graph").mkdir()
    set_key("graph.entity_types", "concept,algorithm")
    _record_build(subj, store)

    set_key("graph.entity_types", "algorithm,concept")
    assert graph_is_stale(subj, store) is not None


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

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
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

    # The prompt graphrag is told to use is a real file it can read back — its `prompt`
    # field is a path, resolved at extraction time, not the text itself.
    assert Path(config.extract_graph.prompt).is_file()
    assert config.extract_graph.entity_types == extraction_entity_types()
    assert "{entity_types}" in config.extract_graph.resolved_prompts().extraction_prompt

    manifest = subj.load_manifest()
    assert manifest.graphrag.extraction_model == "gpt-4o-mini"
    assert manifest.graphrag.corpus_hash == corpus_hash(store)
    assert manifest.graphrag.version  # graphrag's installed package version
    assert manifest.graphrag.extraction_fingerprint == current_extraction_fingerprint()


def test_build_graph_traces_every_llm_call_it_makes(subj, store, home, monkeypatch):
    """architecture.md: every LLM call records tokens + cost. The probe makes *two* real
    billable calls (extraction prompt, then JSON-mode capability), so there must be a
    trace row for each — an earlier version discarded the second call's result, and the
    test that asserted a single probe row is what pinned that bug in place."""
    from groundly.core.store import connect_progress

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    build_graph(subj, store, estimated_tokens=123, estimated_cost_usd=0.0045)

    conn = connect_progress(subj.progress_db_path)
    try:
        rows = conn.execute("SELECT * FROM traces ORDER BY id").fetchall()
    finally:
        conn.close()
    assert [r["arm"] for r in rows] == ["graph-probe", "graph-probe", "graph-build"]
    assert all(r["outcome"] == "built" for r in rows)

    row = rows[-1]
    assert row["kind"] == "index"
    assert row["model"] == "gpt-4o-mini"
    assert row["tokens"] == 123
    assert row["cost_usd"] == 0.0045


def _build_with_metrics(subj, store, monkeypatch, **metrics):
    """Drive graphrag's *real* metrics path: build the completion model from the config
    build_graph handed to build_index, then feed its store the usage a run would.

    Deliberately not a fabricated file. graphrag_llm only ever *writes* its metrics from
    an `atexit` hook, so a test that stubbed the output would have passed against code
    that can never fire in production — which is exactly what happened to the first
    version of this feature."""
    from graphrag_llm.completion.completion_factory import create_completion

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        completion = create_completion(
            config.get_completion_model_config("default_completion_model")
        )
        completion.metrics_store.update_metrics(
            metrics={
                "prompt_tokens": 1000,
                "completion_tokens": 4000,
                "responses_with_tokens": 100,
                **metrics,
            }
        )
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)
    return build_graph(subj, store, estimated_tokens=123, estimated_cost_usd=0.0045)


def _build_trace(subj):
    from groundly.core.store import connect_progress

    conn = connect_progress(subj.progress_db_path)
    try:
        return conn.execute(
            "SELECT * FROM traces WHERE arm = 'graph-build' ORDER BY id"
        ).fetchall()[-1]
    finally:
        conn.close()


def test_build_graph_traces_metered_usage_not_the_estimate(subj, store, home, monkeypatch):
    """The trace used to store the pre-build heuristic as if it were metered. graphrag
    swallows its own LLM calls, but graphrag_llm aggregates their usage in a store this
    process can read — so the number recorded is what was spent."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
        'api_key = "sk-secret"\ninput_price_per_mtok = 1.0\noutput_price_per_mtok = 2.0\n'
    )
    _add_material(store, "a.pdf", "a" * 64)

    result = _build_with_metrics(subj, store, monkeypatch)

    assert (result.prompt_tokens, result.completion_tokens) == (1000, 4000)
    assert result.cost_usd == pytest.approx(1000 * 1e-06 + 4000 * 2e-06)

    row = _build_trace(subj)
    assert row["tokens"] == 5000  # not the 123 estimate
    assert row["cost_usd"] == pytest.approx(result.cost_usd)


def test_build_graph_metered_cost_excludes_cache_hits(subj, store, home, monkeypatch):
    """Cached responses are counted in graphrag's token totals but were never paid for,
    and decision 21 deliberately keeps `cache/` across a failed rebuild — so retrying
    against a warm cache is the normal path, not an edge case. Tokens stay as metered;
    only the cost is scaled to the responses that actually reached the provider."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
        'api_key = "sk-secret"\ninput_price_per_mtok = 1.0\noutput_price_per_mtok = 2.0\n'
    )
    _add_material(store, "a.pdf", "a" * 64)

    result = _build_with_metrics(subj, store, monkeypatch, cached_responses=100)

    assert (result.prompt_tokens, result.completion_tokens) == (1000, 4000)
    assert result.cost_usd == 0.0


def test_build_graph_falls_back_to_the_estimate_without_metrics(subj, store, home, monkeypatch):
    """A build that metered nothing is not a reason to fail one that otherwise
    succeeded — it just means there is no metered number to report, and reporting
    "0 tokens, $0.00" would read as a fact rather than as an absence."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)
    result = build_graph(subj, store, estimated_tokens=123, estimated_cost_usd=0.0045)

    assert result.prompt_tokens is None and result.cost_usd is None
    row = _build_trace(subj)
    assert row["tokens"] == 123
    assert row["cost_usd"] == 0.0045


def test_build_graph_does_not_inherit_a_previous_builds_usage(subj, store, home, monkeypatch):
    """graphrag registers metrics stores as singletons, so without a reset the second
    build in a process would report the first one's tokens on top of its own."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    first = _build_with_metrics(subj, store, monkeypatch)
    assert first.prompt_tokens == 1000

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)
    second = build_graph(subj, store, estimated_tokens=123, estimated_cost_usd=0.0045)

    assert second.prompt_tokens is None  # not 1000 carried over
    assert _build_trace(subj)["tokens"] == 123


def test_build_graph_wraps_failure_in_graph_build_error(subj, store, home, monkeypatch):
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def failing_build_index(config, input_documents=None, callbacks=None, verbose=False):
        raise RuntimeError("boom")

    monkeypatch.setattr("groundly.ingestion.graph.build_index", failing_build_index)

    with pytest.raises(GraphBuildError):
        build_graph(subj, store)

    # manifest must be untouched on failure
    manifest = subj.load_manifest()
    assert manifest.graphrag.corpus_hash is None


# --- callbacks / results-error check (debug-logging design) ---------------------------


def test_build_graph_passes_callbacks_and_never_enables_graphrag_verbose(
    subj, store, home, monkeypatch
):
    """`verbose=True` would raise graphrag's loggers to DEBUG, and graphrag logs
    `str(output.result)` at DEBUG — a sample DataFrame including the text column,
    i.e. verbatim course material onto stderr and into graph/logs/. It must stay
    at graphrag's own default."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    captured = {}

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        captured["callbacks"] = callbacks
        captured["verbose"] = verbose
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    build_graph(subj, store)

    assert captured["verbose"] is False
    assert captured["callbacks"] is not None
    assert len(captured["callbacks"]) == 1


def test_build_graph_adapter_translates_lifecycle_into_on_event(subj, store, home, monkeypatch):
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    events = []

    def on_event(description, completed, total):
        events.append((description, completed, total))

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        adapter = callbacks[0]
        adapter.pipeline_start(["wf1", "wf2"])
        adapter.workflow_start("wf1", None)
        adapter.workflow_end("wf1", None)
        adapter.workflow_start("wf2", None)
        adapter.workflow_end("wf2", None)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    build_graph(subj, store, on_event=on_event)

    assert ("starting…", 0, 2) == events[0]
    assert ("wf1", 0, 2) in events  # workflow_start: description set, nothing completed yet
    assert ("wf1", 1, 2) in events  # workflow_end: advances completed
    assert ("wf2", 2, 2) in events


def test_build_graph_raises_on_workflow_error_and_leaves_manifest_untouched(
    subj, store, home, monkeypatch
):
    from graphrag.index.typing.pipeline_run_result import PipelineRunResult

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        return [
            PipelineRunResult(workflow="extract_graph", result=None, state={}, error=None),
            PipelineRunResult(
                workflow="community_reports", result=None, state={}, error=RuntimeError("boom")
            ),
        ]

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    with pytest.raises(GraphBuildError, match="community_reports"):
        build_graph(subj, store)

    manifest = subj.load_manifest()
    assert manifest.graphrag.corpus_hash is None

    # the other half of the invariant: no *build* trace for a build that failed.
    # (The probe's own rows are expected — those were real LLM calls that succeeded.)
    from groundly.core.store import connect_progress

    conn = connect_progress(subj.progress_db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM traces WHERE arm = 'graph-build'").fetchone()
    finally:
        conn.close()
    assert rows[0] == 0


# --- context sizing ---------------------------------------------------------------------


def test_build_config_scales_graphrag_budgets_to_the_configured_context_window(
    subj, store, home, monkeypatch
):
    """graphrag's defaults want ~10k for community reports alone; on a small local model
    every call 400s with 'Context size has been exceeded'."""
    _configure_extraction(home)
    (home / "config.toml").write_text(
        (home / "config.toml").read_text() + "\n[graph]\ncontext_window = 4096\n"
    )
    _add_material(store, "a.pdf", "a" * 64)

    captured = {}

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        captured["config"] = config
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)
    build_graph(subj, store)

    cfg = captured["config"]
    assert cfg.extract_graph.max_gleanings == 0  # no conversation replay on a small window
    assert cfg.community_reports.max_input_length == 2048
    assert cfg.community_reports.max_length == 1024
    assert cfg.summarize_descriptions.max_input_tokens == 2048
    # every stage's input + output reserve has to fit what the model actually has
    assert cfg.community_reports.max_input_length + cfg.community_reports.max_length <= 4096
    assert (
        cfg.summarize_descriptions.max_input_tokens + cfg.summarize_descriptions.max_length <= 4096
    )


# --- the preflight probe ----------------------------------------------------------------


def test_probe_failure_names_the_cause_and_never_starts_the_pipeline(
    subj, store, home, monkeypatch
):
    from groundly.llm.chat import ChatUnreachableError

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    monkeypatch.setattr(
        "groundly.llm.chat.complete",
        lambda *a, **k: (_ for _ in ()).throw(
            ChatUnreachableError("Context size has been exceeded")
        ),
    )

    started = []

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        started.append(True)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    with pytest.raises(GraphBuildError, match="Context size has been exceeded"):
        build_graph(subj, store)

    assert started == []  # the whole point: fail in seconds, not hours
    assert subj.load_manifest().graphrag.corpus_hash is None


def test_probe_checks_structured_output_separately_and_says_so(subj, store, home, monkeypatch):
    """A provider can answer plain completions and still reject response_format — every
    DeepSeek model does. The message must name structured output, not context size: an
    earlier version reused the extraction-prompt wording and misdirected a real user to
    check their context window."""
    from groundly.llm.chat import ChatResult, ChatUnreachableError

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    calls = []

    def fake_complete(call_class, messages, *, response_format=None):
        calls.append(response_format)
        if response_format is not None:
            raise ChatUnreachableError("This response_format type is unavailable now")
        return ChatResult(text="ok", tokens=1, cost_usd=None, model="stub")

    monkeypatch.setattr("groundly.llm.chat.complete", fake_complete)

    with pytest.raises(GraphBuildError, match="structured-output"):
        build_graph(subj, store)

    # Plain completion first, then the capability check.
    assert [c is None for c in calls] == [True, False]


def test_probe_sends_graphrags_own_response_model(subj, store, home, monkeypatch):
    """The probe must pass the *same object* community_reports_extractor passes, so litellm
    derives the same wire request for both and the probe cannot test a shape the build never
    sends. A hand-written `{"type": "json_object"}` stood here and was wrong in both
    directions at once (2026-07-26): DeepSeek accepts json_object and refuses the json_schema
    litellm derives from the model class, so a doomed build ran a full extraction pass; LM
    Studio refuses json_object and requires json_schema, so a local model that builds graphs
    fine would have been refused before starting.

    Asserting on the class itself, not on a dict shaped like it, is the point — a copy of
    the shape is exactly what drifts when graphrag changes its response model."""
    from graphrag.index.operations.summarize_communities.community_reports_extractor import (
        CommunityReportResponse,
    )

    from groundly.llm.chat import ChatResult

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    formats = []

    def fake_complete(call_class, messages, *, response_format=None):
        formats.append(response_format)
        return ChatResult(text="ok", tokens=1, cost_usd=None, model="stub")

    monkeypatch.setattr("groundly.llm.chat.complete", fake_complete)

    # The build itself still fails (the fake provider is unreachable) — only the probe's
    # two calls are under test here.
    with pytest.raises(GraphBuildError):
        build_graph(subj, store)

    assert formats[1] is CommunityReportResponse


def test_probe_contains_unexpected_exceptions_as_named_errors(subj, store, home, monkeypatch):
    """The probe runs outside build_graph's own wrapper, so anything it raises other than
    ChatUnreachableError would reach the CLI as a raw traceback past its
    `except (GraphBuildError, ProviderNotConfiguredError)`."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    monkeypatch.setattr(
        "groundly.llm.chat.complete",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("unexpected response shape")),
    )

    with pytest.raises(GraphBuildError):  # not KeyError
        build_graph(subj, store)


def test_probe_records_a_trace_row_on_failure(subj, store, home, monkeypatch):
    from groundly.core.store import connect_progress
    from groundly.llm.chat import ChatUnreachableError

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)
    monkeypatch.setattr(
        "groundly.llm.chat.complete",
        lambda *a, **k: (_ for _ in ()).throw(ChatUnreachableError("nope")),
    )

    with pytest.raises(GraphBuildError):
        build_graph(subj, store)

    conn = connect_progress(subj.progress_db_path)
    try:
        rows = conn.execute("SELECT * FROM traces WHERE arm = 'graph-probe'").fetchall()
    finally:
        conn.close()
    assert [r["outcome"] for r in rows] == ["error"]
    assert "nope" in rows[0]["error"]


def test_build_graph_refuses_an_empty_corpus(subj, store, home):
    _configure_extraction(home)
    with pytest.raises(GraphBuildError, match="nothing indexed yet"):
        build_graph(subj, store)


# --- swallowed extraction failures ------------------------------------------------------


def test_swallowed_extraction_failures_above_threshold_refuse_to_stamp_manifest(
    subj, store, home, monkeypatch
):
    """graphrag catches extraction errors per text unit and carries on, so they never
    reach PipelineRunResult.error — without counting them a graph missing most of the
    corpus would be stamped as current and never rebuilt."""
    _configure_extraction(home)
    _add_chunks(store, 20)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        for _ in range(5):  # 25% of 20 chunks, well over the 5% threshold
            logging.getLogger(_EXTRACT_ERR).error(
                "error extracting graph", exc_info=RuntimeError("Context size exceeded")
            )
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    with pytest.raises(GraphBuildError, match="5 of 20 chunks"):
        build_graph(subj, store)

    assert subj.load_manifest().graphrag.corpus_hash is None


def test_a_few_swallowed_failures_complete_the_build_and_are_reported(
    subj, store, home, monkeypatch
):
    _configure_extraction(home)
    _add_chunks(store, 100)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        logging.getLogger(_EXTRACT_ERR).error("error extracting graph")
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    result = build_graph(subj, store)  # 1% — a transient blip shouldn't bin the build

    assert (result.chunks, result.failed) == (100, 1)
    assert subj.load_manifest().graphrag.corpus_hash == corpus_hash(store)


def test_one_failed_chunk_counts_once_not_twice(subj, store, home, monkeypatch):
    """graphrag emits TWO ERROR records per failed text unit under the same package
    logger — graph_extractor's `logger.exception` and extract_graph's `on_error` lambda.
    Counting both halves the effective threshold and can report more failures than there
    are chunks (verified on a real run: 252 records from each logger)."""
    _configure_extraction(home)
    _add_chunks(store, 100)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        # exactly what graphrag does for ONE failed chunk
        logging.getLogger(_EXTRACT_ERR).exception("error extracting graph")
        logging.getLogger("graphrag.index.operations.extract_graph.extract_graph").error(
            "Entity Extraction Error"
        )
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    assert build_graph(subj, store).failed == 1


# --- artifact backstops -----------------------------------------------------------------


def test_build_without_entities_parquet_refuses(subj, store, home, monkeypatch):
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        return []  # deliberately writes no parquet

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    with pytest.raises(GraphBuildError, match="no entities"):
        build_graph(subj, store)
    assert subj.load_manifest().graphrag.corpus_hash is None


def test_zero_row_entities_parquet_refuses(subj, store, home, monkeypatch):
    """A zero-row parquet is ~1.9 KB of schema, so a file-size check would wave it
    through. Reachable when a model returns unparseable output that never raises: the
    error counter sees nothing and the graph is empty."""
    import pandas as pd

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        g = subj.root_dir / "graph"
        g.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"title": [], "type": []}).to_parquet(g / "entities.parquet")
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    with pytest.raises(GraphBuildError, match="no entities"):
        build_graph(subj, store)


def test_refused_build_is_not_served_by_the_query_path(subj, store, home, monkeypatch):
    """The gate refuses to *record* the build but leaves partial parquet on disk so
    graphrag's LLM cache survives the retry. Retrieval must therefore gate on the
    manifest, not the directory — otherwise a graph missing most of the corpus is still
    answered from, which is the grounding violation the gate exists to prevent."""
    from groundly.retrieval.graph import GraphLocalRetriever, GraphNotBuiltError

    _configure_extraction(home)
    _add_chunks(store, 20)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)  # partial artifacts, as a real failed run would leave
        for _ in range(5):
            logging.getLogger(_EXTRACT_ERR).error("error extracting graph")
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    with pytest.raises(GraphBuildError):
        build_graph(subj, store)

    assert (subj.root_dir / "graph" / "entities.parquet").exists()  # left for the retry
    with pytest.raises(GraphNotBuiltError):
        GraphLocalRetriever(subject=subj.name).retrieve("anything")


# --- community reports: the second swallowed-failure stage ------------------------------


def test_zero_community_reports_refuses_and_names_json_mode(subj, store, home, monkeypatch):
    """graphrag swallows community-report failures under a *different* logger than
    extraction's. Every report failing leaves an empty frame, which graphrag then merges
    -> KeyError 'community' after a fully successful extraction pass."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        _write_communities(subj, n=22, reports=0)
        for _ in range(22):
            logging.getLogger(_COMMUNITY_ERR).error("This response_format type is unavailable now")
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    with pytest.raises(GraphBuildError, match="22 community summaries"):
        build_graph(subj, store)
    assert subj.load_manifest().graphrag.corpus_hash is None


def test_partial_community_report_failures_are_counted_and_reported(subj, store, home, monkeypatch):
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        _write_communities(subj, n=22, reports=20)
        logging.getLogger(_COMMUNITY_ERR).error("boom")
        logging.getLogger(_COMMUNITY_ERR).error("boom")
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    result = build_graph(subj, store)
    assert result.reports_failed == 2
    assert subj.load_manifest().graphrag.corpus_hash == corpus_hash(store)


# --- rebuilds inherit nothing but the cache ----------------------------------------------


def test_stale_artifacts_cannot_satisfy_the_gates(subj, store, home, monkeypatch):
    """graphrag writes into an existing graph/ without clearing it, so before the reset a
    second build that produced nothing and logged no failures inherited build 1's
    entities.parquet, passed every gate, and was stamped as current for the NEW corpus."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def ok(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", ok)
    build_graph(subj, store)
    assert subj.load_manifest().graphrag.corpus_hash == corpus_hash(store)

    _add_material(store, "b.pdf", "b" * 64)  # corpus changes

    async def writes_nothing(config, input_documents=None, callbacks=None, verbose=False):
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", writes_nothing)

    with pytest.raises(GraphBuildError, match="no entities"):
        build_graph(subj, store)

    # and the manifest no longer claims a graph, so the query path says "not built"
    # instead of hunting for parquet that was just deleted
    assert subj.load_manifest().graphrag.corpus_hash is None


def test_rebuild_preserves_the_llm_cache_and_logs(subj, store, home, monkeypatch):
    """cache/ is graphrag's paid-for LLM responses and logs/ is how a failure gets
    diagnosed — a retry that binned either would be expensive and blind."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    graph_dir = subj.root_dir / "graph"
    (graph_dir / "cache").mkdir(parents=True)
    (graph_dir / "cache" / "entry.json").write_text("cached response")
    (graph_dir / "logs").mkdir()
    (graph_dir / "logs" / "indexing-engine.log").write_text("previous run")
    (graph_dir / "text_units.parquet").write_text("stale derived output")
    (graph_dir / "lancedb").mkdir()

    async def ok(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", ok)
    build_graph(subj, store)

    assert (graph_dir / "cache" / "entry.json").read_text() == "cached response"
    assert (graph_dir / "logs" / "indexing-engine.log").read_text() == "previous run"
    assert not (graph_dir / "text_units.parquet").exists()  # derived output, cleared
    assert not (graph_dir / "lancedb").exists()


def test_a_failed_probe_leaves_the_existing_graph_intact(subj, store, home, monkeypatch):
    """The reset runs *after* the probe: a misconfigured provider must not destroy a
    graph that still works."""
    from groundly.llm.chat import ChatUnreachableError

    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    async def ok(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", ok)
    build_graph(subj, store)
    good_hash = subj.load_manifest().graphrag.corpus_hash

    monkeypatch.setattr(
        "groundly.llm.chat.complete",
        lambda *a, **k: (_ for _ in ()).throw(ChatUnreachableError("provider down")),
    )
    with pytest.raises(GraphBuildError):
        build_graph(subj, store)

    assert (subj.root_dir / "graph" / "entities.parquet").exists()
    assert subj.load_manifest().graphrag.corpus_hash == good_hash  # still usable


def test_reset_is_safe_on_a_first_build_with_no_graph_dir(subj, store, home, monkeypatch):
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)
    assert not (subj.root_dir / "graph").exists()

    async def ok(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", ok)
    assert build_graph(subj, store).chunks == 1


# --- the extraction prompt on the build path (decision 22) ------------------------------


def test_a_broken_custom_prompt_fails_before_any_llm_call(subj, store, home, monkeypatch, tmp_path):
    """Criterion: a bad prompt path is a named cause, not a graphrag internal, and it
    costs nothing — the probe is the first billable call and must never be reached."""
    _configure_extraction(home)
    set_key("graph.extraction_prompt", str(tmp_path / "nope.txt"))
    _add_material(store, "a.pdf", "a" * 64)

    calls = []
    monkeypatch.setattr(
        "groundly.llm.chat.complete", lambda *a, **k: calls.append(a) or ChatResultStub()
    )

    async def never(*a, **k):
        raise AssertionError("the pipeline must not start")

    monkeypatch.setattr("groundly.ingestion.graph.build_index", never)

    with pytest.raises(GraphBuildError, match="nope.txt"):
        build_graph(subj, store)
    assert calls == []


class ChatResultStub:
    model = "stub"
    tokens = 1
    cost_usd = None


def test_a_broken_custom_prompt_leaves_the_existing_graph_intact(
    subj, store, home, monkeypatch, tmp_path
):
    """Same contract as a failed probe: nothing is destroyed until the configuration is
    known to work (_reset_graph_artifacts runs after resolution, not before)."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)
    _write_entities(subj)
    survivor = subj.root_dir / "graph" / "entities.parquet"
    assert survivor.exists()

    set_key("graph.extraction_prompt", str(tmp_path / "nope.txt"))
    with pytest.raises(GraphBuildError):
        build_graph(subj, store)
    assert survivor.exists()


def test_refused_build_records_neither_hash_nor_fingerprint(subj, store, home, monkeypatch):
    """The gates and the provenance fields share one write, so a graph that was refused
    can never be reported as current under *either* dimension."""
    _configure_extraction(home)
    _add_chunks(store, 10)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        logging.getLogger(_EXTRACT_ERR).error("boom")  # every chunk fails
        for _ in range(9):
            logging.getLogger(_EXTRACT_ERR).error("boom")
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)

    with pytest.raises(GraphBuildError):
        build_graph(subj, store)

    manifest = subj.load_manifest()
    assert manifest.graphrag.corpus_hash is None
    assert manifest.graphrag.extraction_fingerprint is None


def test_probe_sends_the_bundled_prompt_formatted_as_graphrag_formats_it(
    subj, store, home, monkeypatch
):
    """Regression: the probe must exercise the same prompt the build sends, with the
    same substitutions. It used to pass `tuple_delimiter` &c, which graphrag does not —
    making the probe laxer than the build it exists to predict."""
    _configure_extraction(home)
    _add_material(store, "a.pdf", "a" * 64)

    prompts = []

    def fake_complete(call_class, messages, **kwargs):
        prompts.append(messages[0]["content"])
        return ChatResultStub()

    monkeypatch.setattr("groundly.llm.chat.complete", fake_complete)

    async def fake_build_index(config, input_documents=None, callbacks=None, verbose=False):
        _write_entities(subj)
        return []

    monkeypatch.setattr("groundly.ingestion.graph.build_index", fake_build_index)
    build_graph(subj, store)

    extraction_prompt = prompts[0]
    assert "PETERSON'S ALGORITHM" in extraction_prompt  # the bundled worked example
    assert "some chunk text" in extraction_prompt  # the real chunk, substituted
    assert "concept" in extraction_prompt  # the entity types, substituted
    assert "{" not in extraction_prompt.split("-Real Data-")[-1]  # nothing left unsubstituted
