"""Debug logging: one stderr handler on the ROOT logger, never a log file. Log
lines can carry query text and chunk ids — layer-4 data — so keeping logs
ephemeral on stderr means there is no new artifact for export code to reason
about (.claude/rules/grounding-and-privacy.md).

Named `logs.py`, not `logging.py`, to avoid confusion with the stdlib module.

Why the root logger and not `graphrag` directly: graphrag's own `init_loggers`
clears handlers on the `graphrag`/`graphrag_llm` loggers before attaching its
own, but never sets `propagate = False` — a handler on the root logger still
receives every record they emit. Root's own level stays at its default
(WARNING); only the loggers we name get `setLevel`, so third-party libraries
(httpx, litellm) never get their DEBUG records created in the first place.
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
