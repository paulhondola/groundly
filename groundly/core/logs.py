"""Debug logging: one stderr handler on the root logger, never a log file. Log lines can
carry query text and chunk ids (layer-3 data), so no file exists for export code to
reason about.

The root logger, because graphrag's `init_loggers` clears handlers on its own loggers but
leaves propagation on. Root stays at WARNING; only the named loggers get `setLevel`, so
third-party DEBUG records are never created.
"""

import logging
import os
import sys

_LOGGER_NAMES = ("groundly", "graphrag", "graphrag_llm")

_configured = False


def setup_logging(debug: bool = False) -> bool:
    """Attach one stderr handler to the ROOT logger; return True if logging is on
    (callers use that to disable live progress displays)."""
    global _configured

    if debug:
        level = logging.DEBUG
    else:
        env = os.environ.get("GROUNDLY_LOG_LEVEL")
        if not env:
            return False
        mapping = logging.getLevelNamesMapping()
        if env.upper() not in mapping:
            valid = ", ".join(sorted(mapping))
            raise ValueError(f"invalid GROUNDLY_LOG_LEVEL {env!r} — valid names: {valid}")
        level = mapping[env.upper()]

    for name in _LOGGER_NAMES:
        logging.getLogger(name).setLevel(level)

    if not _configured:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        _configured = True

    return True
