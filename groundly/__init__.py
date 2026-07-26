"""Groundly — local-first course knowledge bases for AI agents.

The NullHandler below is the stdlib library convention, and load-bearing here:
with no handler anywhere in the chain, `logging.lastResort` emits every WARNING+
record to stderr on its own. That would break the promise that with no `--debug`
and no GROUNDLY_LOG_LEVEL Groundly emits nothing via logging, and would surface
the raw tracebacks the CLI deliberately wraps. Attached to the package root so
it covers every `groundly.*` logger regardless of import order; propagation is
untouched, so core/logs.py's root handler still receives everything when logging
is on.

Two env vars for litellm live here rather than in `llm/`, because litellm reads both
at *its* import and the modules that need them import litellm transitively before
their own module body runs: `ingestion/graph.py` and `retrieval/graph.py` do
`from graphrag.api... import ...` at the top, which pulls graphrag -> graphrag_llm ->
litellm well before their `from groundly.llm.graphrag_adapter import ...` line. A
setdefault in `llm/` was therefore a no-op on exactly the paths that matter (verified:
litellm's warnings still appeared). Python runs this package init before any
`groundly.*` submodule, so it is the only placement that actually holds.

- LITELLM_LOCAL_MODEL_COST_MAP: unset, litellm's __init__ fetches its price map from
  GitHub, which the privacy rule forbids (.claude/rules/grounding-and-privacy.md).
- LITELLM_LOG: litellm sets its handler level from this at import and defaults to
  DEBUG, so it warns on every run that botocore is absent and Bedrock/SageMaker
  event-stream decoding is unavailable — providers Groundly never uses. ERROR keeps
  real failures, which also reach us as exceptions. Both use setdefault, so an
  explicit LITELLM_LOG=DEBUG still works for debugging litellm itself.
"""

import logging
import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_LOG", "ERROR")

logging.getLogger(__name__).addHandler(logging.NullHandler())
