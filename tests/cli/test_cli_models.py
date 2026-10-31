"""CLI: model management verbs and the config verbs."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.core.manifest import EMBEDDING_MODEL
from groundly.llm import embeddings
from groundly.llm.rerank import RERANKER_MODEL

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_config_show_defaults_no_file(home):
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.output
    assert "config.toml" in result.output
    assert "top_k" in result.output  # settings shown


def test_config_set_then_show(home):
    assert runner.invoke(app, ["config", "set", "retrieval.top_k", "12"]).exit_code == 0
    result = runner.invoke(app, ["config"])
    assert "retrieval.top_k            = 12" in result.output


def test_config_set_unknown_key_rejected(home):
    result = runner.invoke(app, ["config", "set", "ingestion.nope", "x"])
    assert result.exit_code == 1
    assert "unknown field" in result.output


def test_models_install_cache_hit_skips_download(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda *a: Path("/fake/cached"))

    def must_not_download(*a, force=False):
        raise AssertionError("must not download on cache hit")

    monkeypatch.setattr(embeddings, "ensure_downloaded", must_not_download)
    result = runner.invoke(app, ["models", "install"])
    assert result.exit_code == 0, result.output
    assert "already cached" in result.output


def test_models_install_cache_miss_downloads_both_models(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda *a: None)
    calls = []

    def fake_ensure(*a, force=False):
        calls.append((a, force))
        return Path("/fake/downloaded")

    monkeypatch.setattr(embeddings, "ensure_downloaded", fake_ensure)
    result = runner.invoke(app, ["models", "install"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 2  # embedding + reranker
    assert all(force is False for _, force in calls)
    assert "ready" in result.output


def test_models_install_force_downloads_even_if_cached(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda *a: Path("/fake/cached"))
    calls = []

    def fake_ensure(*a, force=False):
        calls.append((a, force))
        return Path("/fake/cached")

    monkeypatch.setattr(embeddings, "ensure_downloaded", fake_ensure)
    result = runner.invoke(app, ["models", "install", "--force"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 2
    assert all(force is True for _, force in calls)


def test_models_install_download_failure_names_cause_not_traceback(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda *a: None)

    def fake_ensure(*a, force=False):
        raise embeddings.ModelDownloadError("failed to download BAAI/bge-m3: connection reset")

    monkeypatch.setattr(embeddings, "ensure_downloaded", fake_ensure)
    result = runner.invoke(app, ["models", "install"])
    assert result.exit_code == 1
    assert "failed to download" in result.output


def _cached(*models):
    """A cached_snapshot stub: only `models` are in the cache."""
    return lambda model=EMBEDDING_MODEL, revision=None: (
        Path(f"/fake/{model}") if model in models else None
    )


def _record_removals(monkeypatch):
    removed = []
    monkeypatch.setattr(embeddings, "remove_cached", lambda model: removed.append(model) or True)
    return removed


def test_models_bare_shows_cache_status(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", _cached(EMBEDDING_MODEL))
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert any(
        EMBEDDING_MODEL in line and "not cached" not in line and "cached" in line for line in lines
    )
    assert any(RERANKER_MODEL in line and "not cached" in line for line in lines)


def test_models_uninstall_not_cached_is_a_noop(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", _cached())
    removed = _record_removals(monkeypatch)
    result = runner.invoke(app, ["models", "uninstall"])
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.output
    assert removed == []


def test_models_uninstall_removes_both(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", _cached(EMBEDDING_MODEL, RERANKER_MODEL))
    removed = _record_removals(monkeypatch)
    result = runner.invoke(app, ["models", "uninstall", "--yes"])
    assert result.exit_code == 0, result.output
    assert removed == [EMBEDDING_MODEL, RERANKER_MODEL]


def test_models_uninstall_skips_the_one_not_cached(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", _cached(RERANKER_MODEL))
    removed = _record_removals(monkeypatch)
    result = runner.invoke(app, ["models", "uninstall", "--yes"])
    assert result.exit_code == 0, result.output
    assert removed == [RERANKER_MODEL]


def test_models_uninstall_aborts_without_confirmation(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", _cached(EMBEDDING_MODEL))
    removed = _record_removals(monkeypatch)
    result = runner.invoke(app, ["models", "uninstall"], input="n\n")
    assert result.exit_code != 0
    assert removed == []
