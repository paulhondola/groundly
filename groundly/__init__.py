"""Groundly: local-first course knowledge bases for AI agents.

The NullHandler keeps logging silent by default: with no handler in the chain,
`logging.lastResort` prints every WARNING+ record, including tracebacks the CLI wraps. It
sits on the package root to cover every `groundly.*` logger; propagation to
core/logs.py's root handler is untouched.

litellm reads two env vars at its own import, which graphrag triggers before any llm/
module runs, so they are set here, the only place early enough.
LITELLM_LOCAL_MODEL_COST_MAP stops a price-map fetch from GitHub (privacy rule);
LITELLM_LOG=ERROR silences import-time warnings about providers Groundly never uses.
setdefault leaves an explicit value in charge.
"""

import logging
import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_LOG", "ERROR")

logging.getLogger(__name__).addHandler(logging.NullHandler())
