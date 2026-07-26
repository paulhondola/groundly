"""Query router — arm 3's brain and the cost gate (docs/architecture/retrieval.md).
One cheap `router` call-class completion labels the query; unconfigured or
unreachable both degrade to no label, never blocking `ask`. At P3 the label is
logged only — every non-vector label still degrades to the vector arm (P5 wires
graph routing)."""

import logging

from groundly.llm.chat import ChatFn, ChatUnreachableError
from groundly.llm.config import load_provider

logger = logging.getLogger(__name__)

_LABELS = {"factoid", "multi-hop", "global"}

_PROMPT = (
    "Classify the following question as exactly one word: factoid, multi-hop, or "
    "global. Reply with that one word and nothing else.\n\nQuestion: {query}"
)


def classify(query: str, chat: ChatFn) -> str | None:
    if load_provider("router") is None:
        logger.debug("no router provider configured — skipping classification")
        return None
    try:
        result = chat("router", [{"role": "user", "content": _PROMPT.format(query=query)}])
    except ChatUnreachableError:
        logger.info("router unreachable — degrading to no label")
        return None
    label = result.text.strip().lower()
    if label not in _LABELS:
        # truncated: a runaway reply would otherwise dump in full, and under
        # `groundly mcp` these lines land in the host's own log files
        logger.info("router returned unexpected reply %r — coercing to factoid", label[:80])
        return "factoid"
    return label
