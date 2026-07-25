"""Groundly — local-first course knowledge bases for AI agents.

The NullHandler below is the stdlib library convention, and load-bearing here:
with no handler anywhere in the chain, `logging.lastResort` emits every WARNING+
record to stderr on its own. That would break the promise that with no `--debug`
and no GROUNDLY_LOG_LEVEL Groundly emits nothing via logging, and would surface
the raw tracebacks the CLI deliberately wraps. Attached to the package root so
it covers every `groundly.*` logger regardless of import order; propagation is
untouched, so core/logs.py's root handler still receives everything when logging
is on.
"""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())
