"""Groundly: local-first course knowledge bases for AI agents.

The NullHandler keeps logging silent by default: with no handler in the chain,
`logging.lastResort` prints every WARNING+ record, including tracebacks the CLI wraps. It
sits on the package root to cover every `groundly.*` logger; propagation to
core/logs.py's root handler is untouched.
"""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())
