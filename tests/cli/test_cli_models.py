"""CLI: model management verbs and the config verbs."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.llm import embeddings

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
    assert "(not configured)" in result.output  # no providers
    assert "context_k" in result.output  # settings shown


def test_config_set_provider_shows_masked_key(home):
    assert runner.invoke(app, ["config", "set", "chat.base_url", "http://x"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "chat.model", "qwen"]).exit_code == 0
    assert runner.invoke(app, ["config", "set", "chat.key", "sk-secret"]).exit_code == 0
    result = runner.invoke(app, ["config"])
    assert "model=qwen" in result.output
    assert "***ret" in result.output  # last 3 of sk-secret, masked
    assert "sk-secret" not in result.output


def test_config_set_unknown_key_rejected(home):
    result = runner.invoke(app, ["config", "set", "chat.nope", "x"])
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


def test_models_uninstall_not_cached_is_a_noop(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: None)

    def must_not_remove():
        raise AssertionError("must not remove when nothing is cached")

    monkeypatch.setattr(embeddings, "remove_cached", must_not_remove)
    result = runner.invoke(app, ["models", "uninstall"])
    assert result.exit_code == 0, result.output
    assert "not cached" in result.output


def test_models_uninstall_removes_with_confirmation(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: Path("/fake/cached"))
    calls = {"n": 0}

    def fake_remove():
        calls["n"] += 1
        return True

    monkeypatch.setattr(embeddings, "remove_cached", fake_remove)
    result = runner.invoke(app, ["models", "uninstall", "--yes"])
    assert result.exit_code == 0, result.output
    assert calls["n"] == 1
    assert "removed" in result.output


def test_models_uninstall_aborts_without_confirmation(monkeypatch):
    monkeypatch.setattr(embeddings, "cached_snapshot", lambda: Path("/fake/cached"))

    def must_not_remove():
        raise AssertionError("must not remove without confirmation")

    monkeypatch.setattr(embeddings, "remove_cached", must_not_remove)
    result = runner.invoke(app, ["models", "uninstall"], input="n\n")
    assert result.exit_code != 0
