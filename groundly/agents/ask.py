"""The ask pipeline — the one shared function exposed identically as `groundly ask`
and the MCP `ask` tool (docs/architecture/agents.md): retrieval -> trust-layered
prompt -> generation -> citation resolution -> cited answer or refusal. Zero
resolvable citations is an error, never a degraded answer
(.claude/rules/grounding-and-privacy.md); every outcome (including errors and the
no-key case never reaching this far) is traced.

**The product path is the vector arm, and only the vector arm** (decision 28). The
router used to pick between three arms here; measured on apd it routed 30 of 48
questions to `graph-global` — an arm that returns the same 1,138 chunks (95% of the
corpus) for every question — while `vector` beat `hybrid-local` at every matched
cutoff on every metric. With one selectable arm a classifier has nothing to select,
so the `classify()` call is gone and with it one provider round-trip per `ask`.

`router_label` stays on `AskResult` and in the trace schema, always `None` from here.
It keeps meaning "what the router said" rather than being repurposed — `agents/router.py`
is still measured by `groundly eval`, just never on the way to an answer.

The arms themselves all still exist, in `retrieval/arms.py`: retiring them from the
product path is not the same as deleting them, and the eval harness is what keeps the
negative result reproducible from shipped code."""

import logging
from dataclasses import dataclass

from groundly.agents.citations import Citation, NoCitationsError, resolve_citations  # noqa: F401  re-exported: mcp/server.py + cli/ask.py import NoCitationsError from here
from groundly.agents.prompts import REFUSAL, assemble
from groundly.agents.tracing import TracedAnswer
from groundly.core.config import load_settings
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.llm.chat import complete
from groundly.llm.config import require_provider
from groundly.retrieval.arms import retrieve_for_arm
from groundly.retrieval.nodes import chunk_ids as chunk_ids_of

logger = logging.getLogger(__name__)


@dataclass
class AskResult:
    answer: str
    citations: list[Citation]
    router_label: str | None


def ask(
    subject: str,
    query: str,
    *,
    rerank: bool = True,
    embedder=None,
    reranker=None,
) -> AskResult:
    """Answer `query` from `subject`'s materials through the vector arm, the only arm
    the product selects (`PRODUCT_ARMS`).

    **There is deliberately no `arm=` parameter.** While one existed, a caller could
    still route a user question into a retired graph arm, which would make the
    retirement nominal rather than real — and nothing in production ever passed it,
    because the eval calls `retrieve_for_arm` directly (decision 28)."""
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    store = SubjectStore(subj.store_db_path)

    # `router_label` is left at None throughout: the column still means "what the router
    # said", and the router no longer runs here. Kept on the result and the trace so a
    # re-admitted router needs no schema change (see the module docstring).
    with TracedAnswer(subj, kind="ask", query=query) as trace:
        nodes, trace.path, trace.arm = retrieve_for_arm(
            subject,
            query,
            "vector",
            store=store,
            rerank=rerank,
            embedder=embedder,
            reranker=reranker,
        )

        # `retrieve_for_arm` returns each arm's full candidate list — that is what lets
        # the eval score every k from one run. Applying `context_k` is the *consumer's*
        # job, and this is the consumer. Before this, hybrid-local assembled a median of
        # 33 chunks against context_k=8 and graph-global assembled 1,138.
        # Truncating here rather than after `chunk_ids` keeps citation resolution honest:
        # a chunk the model never saw must not be resolvable.
        nodes = nodes[: load_settings().retrieval.context_k]
        trace.chunk_ids = chunk_ids_of(nodes)

        if not nodes:
            return AskResult(answer=trace.refuse(), citations=[], router_label=None)

        result = complete("chat", assemble(query, nodes))
        trace.record_usage(result)

        if REFUSAL in result.text:
            return AskResult(answer=trace.refuse(), citations=[], router_label=None)

        citations = resolve_citations(result.text, trace.chunk_ids, store)
        trace.answered(result.text, citations)
        return AskResult(answer=result.text, citations=citations, router_label=None)
