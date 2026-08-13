"""Query router — one cheap `router` call-class completion labelling a query
factoid / multi-hop / global (docs/architecture/retrieval.md).

**No runtime caller.** `ask()` stopped classifying at decision 28 and did not resume at
29, which made every arm selectable again: arm selection there is explicit (`arm=`,
`--arm`), so a classifier would spend a provider round-trip guessing at something the
caller already stated. This module is retained as a *measured quantity* — `groundly
eval` calls `classify()` directly to report router accuracy, which is the number that
justified taking it off the path (apd, `gemma-4-12b-qat`: 47.9% against 45.8% for always
answering "multi-hop", with 30 of 48 questions routed to `graph-global`).

Unconfigured or unreachable both degrade to no label rather than raising — kept, because
the eval must be able to run this on a machine with no router provider configured."""

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
