"""CLI: grammar pinned by the P1 surface design; init/list/remove exercised for real
against a temp GROUNDLY_HOME. Heavy index logic is covered in test_pipeline.py; the
CLI index test stubs the pipeline entry point."""

import logging

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.core.paths import subject_dir
from groundly.ingestion import pipeline
from groundly.ingestion.results import FileResult, Status

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture(autouse=True)
def _reset_logging():
    """`--debug` tests attach a real handler to the process-global root logger —
    reset it after each test so it never leaks into an unrelated test."""
    import groundly.core.logs as logs_mod

    root = logging.getLogger()
    before_handlers = list(root.handlers)
    before_levels = {name: logging.getLogger(name).level for name in logs_mod._LOGGER_NAMES}
    yield
    for handler in list(root.handlers):
        if handler not in before_handlers:
            root.removeHandler(handler)
    for name, level in before_levels.items():
        logging.getLogger(name).setLevel(level)
    logs_mod._configured = False


def test_no_args_shows_help():
    assert "Usage" in runner.invoke(app, []).output


def test_init_creates_layout_and_is_idempotent(home):
    result = runner.invoke(app, ["init", "PDSS"])
    assert result.exit_code == 0, result.output
    sdir = home / "PDSS"
    for expected in ["manifest.json", "materials", "store.db", "progress.db"]:
        assert (sdir / expected).exists(), expected
    assert (home / "config.toml").exists()

    result = runner.invoke(app, ["init", "PDSS"])
    assert result.exit_code == 0
    assert "already initialized" in result.output


def test_init_rejects_bad_name():
    result = runner.invoke(app, ["init", "../evil"])
    assert result.exit_code == 1
    assert "invalid subject name" in result.output


def test_list_all_subjects():
    runner.invoke(app, ["init", "PDSS"])
    runner.invoke(app, ["init", "ML"])
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "PDSS" in result.output and "ML" in result.output


def test_list_unknown_subject_names_the_fix():
    result = runner.invoke(app, ["list", "NOPE"])
    assert result.exit_code == 1
    assert "groundly init NOPE" in result.output


def test_index_reports_results(monkeypatch, tmp_path):
    runner.invoke(app, ["init", "PDSS"])
    f = tmp_path / "lec.txt"
    f.write_text("content")

    def fake_index_paths(
        subject, paths, embedder=None, on_event=None, on_discovered=None, ocr_lang=None
    ):
        return [FileResult(f, Status.INDEXED, chunks=3)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)
    result = runner.invoke(app, ["index", "PDSS", str(f)])
    assert result.exit_code == 0, result.output
    assert "1 indexed" in result.output and "(3 chunks)" in result.output


def test_index_ocr_lang_set_reuse_mismatch(monkeypatch, tmp_path):
    """--ocr-lang: first use persists into the manifest; same value reuses; a
    different value is refused (re-index migration, decision 15)."""
    from groundly.core.manifest import Manifest

    runner.invoke(app, ["init", "PDSS"])
    f = tmp_path / "lec.txt"
    f.write_text("content")
    seen = []

    def fake_index_paths(
        subject, paths, embedder=None, on_event=None, on_discovered=None, ocr_lang=None
    ):
        seen.append(ocr_lang)
        return [FileResult(f, Status.INDEXED, chunks=1)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)
    manifest_path = subject_dir("PDSS") / "manifest.json"

    # set: persisted into manifest.json and passed to the pipeline
    assert runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "ro"]).exit_code == 0
    assert Manifest.load(manifest_path).ocr.lang == ["ro"]

    # reuse: same flag ok; no flag falls back to the recorded value
    assert runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "ro"]).exit_code == 0
    assert runner.invoke(app, ["index", "PDSS", str(f)]).exit_code == 0
    assert seen == ["ro", "ro", "ro"]

    # mismatch with indexed materials: refused, manifest untouched
    manifest = Manifest.load(manifest_path)
    manifest.counts.materials = 1
    manifest.save(manifest_path)
    result = runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "en"])
    assert result.exit_code == 1
    assert "already set to 'ro'" in result.output and "re-index" in result.output
    assert Manifest.load(manifest_path).ocr.lang == ["ro"]

    # mismatch with nothing indexed: allowed — recovers from a mistyped lang,
    # which stores no rows (every extraction exits model-unavailable)
    manifest = Manifest.load(manifest_path)
    manifest.counts.materials = 0
    manifest.save(manifest_path)
    assert runner.invoke(app, ["index", "PDSS", str(f), "--ocr-lang", "en"]).exit_code == 0
    assert Manifest.load(manifest_path).ocr.lang == ["en"]


def test_index_uninitialized_subject_fails_with_fix(tmp_path):
    f = tmp_path / "lec.txt"
    f.write_text("content")
    result = runner.invoke(app, ["index", "NOPE", str(f)])
    assert result.exit_code == 1
    assert "groundly init NOPE" in result.output


@pytest.mark.parametrize("args", [["list", "../evil"], ["remove", "../evil", "x.pdf", "-y"]])
def test_bad_subject_name_fails_cleanly_not_traceback(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "invalid subject name" in result.output


def test_remove_unknown_material():
    runner.invoke(app, ["init", "PDSS"])
    result = runner.invoke(app, ["remove", "PDSS", "ghost.pdf", "-y"])
    assert result.exit_code == 1
    assert "no material" in result.output


def test_remove_deletes_rows_and_file(home):
    from groundly.core import store

    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    (sdir / "materials" / "lec.txt").write_text("x")
    conn = store.connect(sdir / "store.db")
    with conn:
        conn.execute(
            "INSERT INTO materials (filename, sha256, status) VALUES ('lec.txt', ?, 'indexed')",
            ("c" * 64,),
        )
    conn.close()

    result = runner.invoke(app, ["remove", "PDSS", "lec.txt", "--yes"])
    assert result.exit_code == 0, result.output
    assert not (sdir / "materials" / "lec.txt").exists()
    conn = store.connect(sdir / "store.db")
    assert conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0] == 0
    conn.close()


def test_list_missing_store_db_names_cause_not_traceback():
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()
    result = runner.invoke(app, ["list", "PDSS"])
    assert result.exit_code == 1
    assert "store.db is missing" in result.output
    assert not (sdir / "store.db").exists()  # regression: connect() created an empty db


def test_list_empty_store_db_names_cause_not_traceback():
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()
    (sdir / "store.db").touch()  # exists but schema-less (e.g. interrupted init)
    result = runner.invoke(app, ["list", "PDSS"])
    assert result.exit_code == 1
    assert "corrupt or incomplete" in result.output


def test_list_all_skips_corrupt_manifest_with_warning():
    runner.invoke(app, ["init", "PDSS"])
    runner.invoke(app, ["init", "ML"])
    (subject_dir("PDSS") / "manifest.json").write_text("{ truncated")
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0  # one damaged subject must not take down the listing
    assert "ML" in result.output
    assert "PDSS" in result.output and "corrupt" in result.output


def test_remove_failed_row_keeps_indexed_siblings_file(home):
    """A failed row records the original filename with no collision suffix — removing
    it must not delete a same-named indexed material's stored file (citation target)."""
    from groundly.core import store

    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    (sdir / "materials" / "lec.pdf").write_text("indexed copy")
    conn = store.connect(sdir / "store.db")
    with conn:
        conn.execute(
            "INSERT INTO materials (filename, sha256, status) VALUES ('lec.pdf', ?, 'indexed')",
            ("a" * 64,),
        )
        conn.execute(
            "INSERT INTO materials (filename, sha256, status, error) "
            "VALUES ('lec.pdf', ?, 'extraction_failed', 'no readable text — OCR found nothing to extract')",
            ("b" * 64,),
        )
    conn.close()

    result = runner.invoke(app, ["remove", "PDSS", "b" * 8, "--yes"])
    assert result.exit_code == 0, result.output
    assert (sdir / "materials" / "lec.pdf").exists()  # indexed material's file survives
    conn = store.connect(sdir / "store.db")
    rows = conn.execute("SELECT status FROM materials").fetchall()
    assert [r["status"] for r in rows] == ["indexed"]
    conn.close()


def test_remove_whole_subject_deletes_directory():
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    (sdir / "store.db").unlink()  # even a damaged subject must be removable
    result = runner.invoke(app, ["remove", "PDSS", "--yes"])
    assert result.exit_code == 0, result.output
    assert not sdir.exists()


def test_remove_whole_subject_aborts_without_confirmation():
    runner.invoke(app, ["init", "PDSS"])
    result = runner.invoke(app, ["remove", "PDSS"], input="n\n")
    assert result.exit_code != 0
    assert subject_dir("PDSS").exists()


@pytest.mark.parametrize(
    "args",
    [
        ["init"],  # subject required
        ["index", "PDSS"],  # paths required
        ["config", "set", "chat.model"],  # value required
        ["ask", "PDSS"],  # query required
    ],
)
def test_bad_usage_is_usage_error(args):
    assert runner.invoke(app, args).exit_code == 2


# --- index --graph / --yes -----------------------------------------------------------
# `pipeline.index_paths` is stubbed (as above) — these tests only exercise the graph
# build trigger/confirm/skip logic that runs after it, so `build_graph` itself is also
# stubbed (real graphrag never runs in tests). The fake mimics build_graph's real
# side effects (create graph/, stamp manifest.graphrag) closely enough that the
# staleness/no-rebuild logic on a second `index` run is exercised for real.


def _seed_material(sdir, filename, sha256, text="some chunk text"):
    from groundly.core import store

    conn = store.connect(sdir / "store.db")
    with conn:
        cur = conn.execute(
            "INSERT INTO materials (filename, sha256, status) VALUES (?, ?, 'indexed')",
            (filename, sha256),
        )
        conn.execute(
            "INSERT INTO chunks (material_id, page, heading_path, text, token_count) "
            "VALUES (?, 1, 'h', ?, 3)",
            (cur.lastrowid, text),
        )
    conn.close()


def _stub_index_paths(monkeypatch, f):
    def fake_index_paths(
        subject, paths, embedder=None, on_event=None, on_discovered=None, ocr_lang=None
    ):
        return [FileResult(f, Status.INDEXED, chunks=1)]

    monkeypatch.setattr(pipeline, "index_paths", fake_index_paths)


def _stub_build_graph(
    monkeypatch,
    failed: int = 0,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_usd: float | None = None,
):
    """Records each call and mimics build_graph's real success side effects (graph/
    directory + manifest.graphrag stamped with the current corpus hash) so a later
    `index` run's staleness check behaves like it would against a real build.
    `failed` sets the dropped-chunk count the CLI reports; the token/cost arguments
    stand in for graphrag's metered usage aggregates, which default to unavailable."""
    from groundly.core.manifest import Graphrag
    from groundly.ingestion import graph as ingestion_graph

    calls = []

    def fake_build_graph(
        subj,
        store_obj,
        *,
        estimated_tokens=0,
        estimated_cost_usd=None,
        on_event=None,
    ):
        calls.append(subj.name)
        (subj.root_dir / "graph").mkdir(exist_ok=True)
        manifest = subj.load_manifest()
        manifest.graphrag = Graphrag(
            version="3.1.0",
            extraction_model="m",
            corpus_hash=ingestion_graph.corpus_hash(store_obj),
            # Real build_graph records this in the same write; without it the next
            # `index` would see a framing change and re-offer a rebuild.
            extraction_fingerprint=ingestion_graph.current_extraction_fingerprint(),
        )
        subj.save_manifest(manifest)
        return ingestion_graph.GraphBuildResult(
            chunks=1,
            failed=failed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
        )

    monkeypatch.setattr(ingestion_graph, "build_graph", fake_build_graph)
    return calls


def test_index_graph_flag_triggers_first_build(monkeypatch, home, tmp_path):
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])
    assert result.exit_code == 0, result.output
    assert calls == ["PDSS"]
    assert (sdir / "graph").exists()
    assert "Graph built" in result.output
    # `0/?` reads as a broken bar, not an unknown one — and the longest silent stretch of
    # a real build (the two probe calls) happens before graphrag reports any total.
    assert "?" not in result.output


def _write_extraction_provider(home, model: str = "m", *, priced: bool = True) -> None:
    body = f'[providers.extraction]\nbase_url = "http://x"\nmodel = "{model}"\n'
    if priced:
        body += "input_price_per_mtok = 5.0\noutput_price_per_mtok = 10.0\n"
    (home / "config.toml").write_text(body)


def _invoke_graph_build(monkeypatch, home, tmp_path, **stub_kwargs):
    runner.invoke(app, ["init", "PDSS"])
    _seed_material(subject_dir("PDSS"), "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    _stub_build_graph(monkeypatch, **stub_kwargs)
    return runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])


def test_index_graph_cost_estimate_is_a_range_with_its_assumptions_named(
    monkeypatch, home, tmp_path
):
    """conventions.md: print a cost estimate before spending the student's tokens. The
    previous line was a bare dollar figure that priced *input tokens for the extraction
    pass only* and said so nowhere, presenting a build as costing a fraction of what it
    did. Every part of this is load-bearing: the range, the exclusion, the source."""
    _write_extraction_provider(home)
    result = _invoke_graph_build(monkeypatch, home, tmp_path)

    assert result.exit_code == 0, result.output
    assert "input tokens" in result.output and "output" in result.output
    assert " to $" in result.output  # a range, not a point
    assert "extraction pass only" in result.output
    assert "community reports" in result.output
    assert "prices: config.toml" in result.output


def test_index_graph_cost_estimate_names_the_litellm_price_map_and_its_version(
    monkeypatch, home, tmp_path
):
    """A price the student cannot attribute is a price they cannot sanity-check — and
    litellm's map is bundled, so it can be months behind the provider's real rates."""
    _write_extraction_provider(home, "gpt-4o-mini", priced=False)
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07}},
    )
    result = _invoke_graph_build(monkeypatch, home, tmp_path)

    assert "litellm " in result.output and "gpt-4o-mini" in result.output
    assert "may be out of date" in result.output


def test_index_graph_warns_about_a_moving_alias(monkeypatch, home, tmp_path):
    """Drift is certain for an unpinned alias, not merely possible: litellm prices
    mistral-small-latest at $0.06/$0.18 per Mtok while the alias resolves today to
    Mistral Small 4 at $0.15/$0.60. Warn, never block — confirmation still gates it."""
    _write_extraction_provider(home, "mistral-small-latest")
    result = _invoke_graph_build(monkeypatch, home, tmp_path)

    assert "moving alias" in result.output
    assert "mistral-small-latest" in result.output
    assert result.exit_code == 0, result.output


def test_index_graph_does_not_warn_for_a_pinned_model(monkeypatch, home, tmp_path):
    _write_extraction_provider(home, "mistral-small-2603")
    assert "moving alias" not in _invoke_graph_build(monkeypatch, home, tmp_path).output


def test_index_graph_unpriced_provider_still_confirms(monkeypatch, home, tmp_path):
    """No price is not a reason to skip the gate — the token counts and the confirmation
    both still appear, and the message names both fields that would fix it."""
    _write_extraction_provider(home, priced=False)
    result = _invoke_graph_build(monkeypatch, home, tmp_path)

    assert "no cost estimate available" in result.output
    assert "input_price_per_mtok" in result.output and "output_price_per_mtok" in result.output
    assert "input tokens" in result.output
    assert "Graph built" in result.output


def test_index_graph_reports_metered_spend_after_the_build(monkeypatch, home, tmp_path):
    """Closes the loop: the student saw an estimated range before, and what was actually
    metered after. Absent when build_graph couldn't read graphrag's usage aggregates."""
    _write_extraction_provider(home)
    result = _invoke_graph_build(
        monkeypatch, home, tmp_path, prompt_tokens=1234, completion_tokens=5678, cost_usd=0.42
    )

    assert "metered: 1,234 in / 5,678 out" in result.output
    assert "$0.42" in result.output


def test_index_graph_omits_metered_spend_when_unavailable(monkeypatch, home, tmp_path):
    _write_extraction_provider(home)
    assert "metered:" not in _invoke_graph_build(monkeypatch, home, tmp_path).output


def _leave_failed_build_artifacts(sdir) -> None:
    """The state a refused or Ctrl-C'd build leaves behind: `graph/` on disk (decision 21
    keeps cache/ and logs/ so a retry doesn't re-buy the LLM responses) and
    `manifest.graphrag` reset, i.e. no recorded corpus_hash."""
    (sdir / "graph" / "cache").mkdir(parents=True)
    (sdir / "graph" / "logs").mkdir(parents=True)


def test_index_after_a_failed_build_does_not_offer_a_graph_without_the_flag(
    monkeypatch, home, tmp_path
):
    """A first build is opt-in via --graph. A failed one leaves `graph/` on disk with no
    recorded corpus_hash, and keying off the *directory* read that as a stale graph — so
    every later plain `groundly index` prompted to "rebuild" a graph that never existed.
    The rest of the tree already gates on the manifest (mcp/server.py, _require_graph)."""
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)
    _leave_failed_build_artifacts(sdir)

    result = runner.invoke(app, ["index", "PDSS", str(f)])

    assert result.exit_code == 0, result.output
    assert calls == []
    assert "stale" not in result.output
    assert "Rebuild" not in result.output


def test_index_after_a_failed_build_offers_a_first_build_with_the_flag(monkeypatch, home, tmp_path):
    """--graph still works in that state, and calls it what it is: a first build, not a
    rebuild of something that was never recorded."""
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)
    _leave_failed_build_artifacts(sdir)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])

    assert result.exit_code == 0, result.output
    assert calls == ["PDSS"]
    assert "stale" not in result.output


def test_remove_only_calls_the_graph_stale_when_one_was_recorded(monkeypatch, home, tmp_path):
    """Same predicate, same wrong message: `graph/` left by a failed build made `remove`
    promise a rebuild that no later index run would trigger."""
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    _leave_failed_build_artifacts(sdir)

    result = runner.invoke(app, ["remove", "PDSS", "a.pdf", "--yes"])

    assert result.exit_code == 0, result.output
    assert "stale" not in result.output


def test_index_without_graph_flag_or_existing_graph_does_nothing(monkeypatch, home, tmp_path):
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)

    result = runner.invoke(app, ["index", "PDSS", str(f)])
    assert result.exit_code == 0, result.output
    assert calls == []
    assert not (sdir / "graph").exists()


def test_index_unchanged_corpus_does_not_rebuild(monkeypatch, home, tmp_path):
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)

    runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])
    assert calls == ["PDSS"]

    result = runner.invoke(app, ["index", "PDSS", str(f)])  # no --graph, no --yes needed
    assert result.exit_code == 0, result.output
    assert calls == ["PDSS"]  # no second build


def test_index_corpus_change_auto_rebuilds_without_graph_flag(monkeypatch, home, tmp_path):
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)

    runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])
    assert calls == ["PDSS"]

    _seed_material(sdir, "b.pdf", "b" * 64)  # corpus changed after the first build
    result = runner.invoke(app, ["index", "PDSS", str(f), "--yes"])  # no --graph needed
    assert result.exit_code == 0, result.output
    assert calls == ["PDSS", "PDSS"]  # auto-rebuilt without needing --graph again


def test_index_corpus_change_prompts_with_stale_message_without_yes(monkeypatch, home, tmp_path):
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)

    runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])
    assert calls == ["PDSS"]

    _seed_material(sdir, "b.pdf", "b" * 64)
    result = runner.invoke(app, ["index", "PDSS", str(f)], input="y\n")
    assert result.exit_code == 0, result.output
    assert calls == ["PDSS", "PDSS"]
    assert "stale" in result.output.lower()


def test_index_graph_declining_confirmation_aborts_without_building(monkeypatch, home, tmp_path):
    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--graph"], input="n\n")
    assert result.exit_code != 0
    assert calls == []
    assert not (sdir / "graph").exists()


def test_index_stale_graph_rebuild_fails_cleanly_without_extraction_provider(
    monkeypatch, home, tmp_path
):
    """build_graph's require_provider("extraction") call runs before its own
    try/except (fail fast, by design) — a ProviderNotConfiguredError on the
    auto-rebuild-a-stale-graph path (no --graph flag needed) must still be caught
    and named, not propagate raw past _maybe_build_graph."""
    from groundly.core.manifest import Graphrag
    from groundly.core.subject import Subject

    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)

    (sdir / "graph").mkdir()
    subj = Subject("PDSS")
    manifest = subj.load_manifest()
    manifest.graphrag = Graphrag(version="3.1.0", extraction_model="m", corpus_hash="stale-hash")
    subj.save_manifest(manifest)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--yes"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "[providers.extraction]" in result.output


def test_index_graph_build_error_fails_cleanly(monkeypatch, home, tmp_path):
    from groundly.ingestion import graph as ingestion_graph
    from groundly.ingestion.graph import GraphBuildError

    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)

    def failing_build_graph(
        subj,
        store_obj,
        *,
        estimated_tokens=0,
        estimated_cost_usd=None,
        on_event=None,
        verbose=False,
    ):
        raise GraphBuildError("boom")

    monkeypatch.setattr(ingestion_graph, "build_graph", failing_build_graph)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])
    assert result.exit_code == 1
    assert "boom" in result.output
    assert not (sdir / "graph").exists()


# --- --debug (debug-logging design) ---------------------------------------------------


def test_index_debug_disables_indexing_progress_bar(monkeypatch, home, tmp_path):
    import rich.progress

    disable_kwargs = []
    original_init = rich.progress.Progress.__init__

    def spy_init(self, *args, **kwargs):
        disable_kwargs.append(kwargs.get("disable"))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(rich.progress.Progress, "__init__", spy_init)

    runner.invoke(app, ["init", "PDSS"])
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--debug"])
    assert result.exit_code == 0, result.output
    assert disable_kwargs == [True]


def test_index_graph_debug_disables_both_progress_bars(monkeypatch, home, tmp_path):
    import rich.progress

    disable_kwargs = []
    original_init = rich.progress.Progress.__init__

    def spy_init(self, *args, **kwargs):
        disable_kwargs.append(kwargs.get("disable"))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(rich.progress.Progress, "__init__", spy_init)

    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    _stub_build_graph(monkeypatch)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes", "--debug"])
    assert result.exit_code == 0, result.output
    assert disable_kwargs == [True, True]  # indexing bar, then graph bar


def test_index_graph_debug_streams_logs_to_stderr(monkeypatch, home, tmp_path):
    from groundly.core.manifest import Graphrag
    from groundly.ingestion import graph as ingestion_graph

    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)

    def fake_build_graph(
        subj,
        store_obj,
        *,
        estimated_tokens=0,
        estimated_cost_usd=None,
        on_event=None,
    ):
        logging.getLogger("groundly.ingestion.graph").debug("building graph for %s", subj.name)
        (subj.root_dir / "graph").mkdir(exist_ok=True)
        manifest = subj.load_manifest()
        manifest.graphrag = Graphrag(
            version="3.1.0",
            extraction_model="m",
            corpus_hash=ingestion_graph.corpus_hash(store_obj),
            # Real build_graph records this in the same write; without it the next
            # `index` would see a framing change and re-offer a rebuild.
            extraction_fingerprint=ingestion_graph.current_extraction_fingerprint(),
        )
        subj.save_manifest(manifest)
        return ingestion_graph.GraphBuildResult(chunks=1, failed=0)

    monkeypatch.setattr(ingestion_graph, "build_graph", fake_build_graph)

    result = runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes", "--debug"])
    assert result.exit_code == 0, result.output
    assert "building graph for PDSS" in result.stderr
    assert "DEBUG groundly.ingestion.graph" in result.stderr


def test_index_debug_invalid_log_level_fails_cleanly(monkeypatch, home, tmp_path):
    monkeypatch.setenv("GROUNDLY_LOG_LEVEL", "BOGUS")
    runner.invoke(app, ["init", "PDSS"])
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)

    result = runner.invoke(app, ["index", "PDSS", str(f)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "BOGUS" in result.output


def test_index_names_a_framing_change_rather_than_blaming_the_corpus(monkeypatch, home, tmp_path):
    """The corpus is untouched — only graph.entity_types changed. Saying "the corpus
    changed" here would be confidently wrong, which is the failure mode the staleness
    reason exists to prevent."""
    from groundly.core.config import set_key

    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    calls = _stub_build_graph(monkeypatch)

    runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])
    assert calls == ["PDSS"]

    set_key("graph.entity_types", "concept,proof")
    result = runner.invoke(app, ["index", "PDSS", str(f), "--yes"])

    assert result.exit_code == 0, result.output
    assert "extraction prompt or entity types changed" in result.output
    assert "corpus changed" not in result.output
    assert calls == ["PDSS", "PDSS"]  # rebuilt under the new framing


def test_index_names_a_broken_custom_prompt_without_a_traceback(monkeypatch, home, tmp_path):
    """graph_is_stale resolves the configured prompt, so a bad path surfaces on the
    staleness check — before the cost estimate, and before any LLM call."""
    from groundly.core.config import set_key

    runner.invoke(app, ["init", "PDSS"])
    sdir = subject_dir("PDSS")
    _seed_material(sdir, "a.pdf", "a" * 64)
    f = tmp_path / "lec.txt"
    f.write_text("content")
    _stub_index_paths(monkeypatch, f)
    _stub_build_graph(monkeypatch)

    runner.invoke(app, ["index", "PDSS", str(f), "--graph", "--yes"])
    set_key("graph.extraction_prompt", str(tmp_path / "gone.txt"))

    result = runner.invoke(app, ["index", "PDSS", str(f), "--yes"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "gone.txt" in result.output
    assert "unset it to use the bundled course-tuned prompt" in result.output
