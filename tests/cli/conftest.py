"""Fixtures shared by every CLI verb test."""

import logging

import pytest


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
