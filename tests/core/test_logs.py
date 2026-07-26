"""groundly/core/logs.py: one stderr handler on the root logger, off by default."""

import logging
import sys

import pytest

from groundly.core import logs


@pytest.fixture(autouse=True)
def _reset_logging(monkeypatch):
    """Logging state (handlers, per-logger levels, the module's idempotency flag)
    is process-global — reset it around every test so tests never leak into
    each other."""
    monkeypatch.setattr(logs, "_configured", False)
    root = logging.getLogger()
    before_handlers = list(root.handlers)
    before_levels = {name: logging.getLogger(name).level for name in (*logs._LOGGER_NAMES, "httpx")}
    yield
    for handler in list(root.handlers):
        if handler not in before_handlers:
            root.removeHandler(handler)
    for name, level in before_levels.items():
        logging.getLogger(name).setLevel(level)


def test_default_is_off_no_handler_no_records(monkeypatch, capsys):
    monkeypatch.delenv("GROUNDLY_LOG_LEVEL", raising=False)
    root = logging.getLogger()
    before = list(root.handlers)

    assert logs.setup_logging() is False
    assert root.handlers == before

    logging.getLogger("groundly").debug("should never appear")
    assert capsys.readouterr().err == ""


def test_default_is_off_for_warning_and_error_too(monkeypatch, capsys):
    """`logging.lastResort` is a WARNING-level stderr handler that fires whenever no
    handler is found in the chain — so without the NullHandler attached in
    groundly/__init__.py, a single logger.error() would print a raw traceback to
    stderr on the default no-logging path. Pins that: off means off at every level."""
    monkeypatch.delenv("GROUNDLY_LOG_LEVEL", raising=False)
    assert logs.setup_logging() is False

    log = logging.getLogger("groundly.ingestion.graph")
    log.warning("no warning either")
    try:
        raise RuntimeError("workflow blew up")
    except RuntimeError as exc:
        log.error("graphrag pipeline error: %s", exc, exc_info=exc)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_error_records_still_reach_the_handler_when_logging_is_on(monkeypatch, capsys):
    """The NullHandler must not swallow anything once logging is enabled — it
    suppresses lastResort only, never propagation to the root handler."""
    monkeypatch.delenv("GROUNDLY_LOG_LEVEL", raising=False)
    logs.setup_logging(debug=True)

    logging.getLogger("groundly.ingestion.graph").error("pipeline error: %s", "boom")
    assert "pipeline error: boom" in capsys.readouterr().err


def test_debug_flag_and_env_level_both_attach(monkeypatch):
    monkeypatch.setenv("GROUNDLY_LOG_LEVEL", "INFO")
    assert logs.setup_logging() is True
    assert logging.getLogger("groundly").level == logging.INFO

    monkeypatch.delenv("GROUNDLY_LOG_LEVEL", raising=False)
    monkeypatch.setattr(logs, "_configured", False)
    assert logs.setup_logging(debug=True) is True
    assert logging.getLogger("groundly").level == logging.DEBUG


def test_debug_flag_wins_over_env(monkeypatch):
    monkeypatch.setenv("GROUNDLY_LOG_LEVEL", "INFO")
    assert logs.setup_logging(debug=True) is True
    assert logging.getLogger("groundly").level == logging.DEBUG


def test_invalid_env_level_raises_naming_valid_names(monkeypatch):
    monkeypatch.setenv("GROUNDLY_LOG_LEVEL", "BOGUS")
    with pytest.raises(ValueError) as exc:
        logs.setup_logging()
    assert "BOGUS" in str(exc.value)
    assert "DEBUG" in str(exc.value)


def test_handler_stream_is_stderr(monkeypatch):
    monkeypatch.delenv("GROUNDLY_LOG_LEVEL", raising=False)
    logs.setup_logging(debug=True)
    handler = logging.getLogger().handlers[-1]
    assert handler.stream is sys.stderr


def test_two_calls_attach_one_handler(monkeypatch):
    monkeypatch.delenv("GROUNDLY_LOG_LEVEL", raising=False)
    root = logging.getLogger()
    before_count = len(root.handlers)
    logs.setup_logging(debug=True)
    logs.setup_logging(debug=True)
    assert len(root.handlers) - before_count == 1


def test_propagation_survives_graphrag_handler_clear_and_suppresses_httpx(monkeypatch, capsys):
    """The load-bearing test: graphrag's `init_loggers` clears handlers on the
    `graphrag` logger before attaching its own — a root handler must still
    receive its records. `httpx` (never given a level) must stay silent: its
    effective level inherits root's own WARNING, so the DEBUG record is never
    even created."""
    monkeypatch.delenv("GROUNDLY_LOG_LEVEL", raising=False)
    logs.setup_logging(debug=True)

    logging.getLogger("graphrag").handlers.clear()  # simulate init_loggers's clear

    logging.getLogger("graphrag.index.run").debug("graph workflow started")
    logging.getLogger("httpx").debug("http debug noise")

    captured = capsys.readouterr()
    assert "graph workflow started" in captured.err
    assert "http debug noise" not in captured.err


def test_litellm_env_defaults_land_before_litellm_is_imported():
    """A subprocess, because the regression is *ordering*, not the values.

    litellm reads LITELLM_LOCAL_MODEL_COST_MAP and LITELLM_LOG at its own import. The
    graph modules do `from graphrag.api... import ...` at the top, which pulls
    graphrag -> graphrag_llm -> litellm before their own body runs — so a setdefault in
    llm/ was a no-op on exactly those paths, and litellm both warned about absent
    botocore and (for the cost map) would fetch its price map from GitHub, which the
    privacy rule forbids. Only groundly/__init__.py runs early enough.

    Asserting the env values in-process would pass even after a regression, since the
    var still ends up set — just too late to matter."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k not in ("LITELLM_LOG",)}

    proc = subprocess.run(
        [sys.executable, "-c", "import groundly.ingestion.graph"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert proc.returncode == 0, proc.stderr
    assert "could not pre-load" not in proc.stderr  # botocore/Bedrock noise
    assert "LiteLLM:WARNING" not in proc.stderr
    assert proc.stdout == ""  # nothing may reach stdout — `groundly mcp` speaks it
