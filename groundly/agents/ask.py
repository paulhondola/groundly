"""The ask pipeline — the one shared function exposed identically as `groundly ask`
and the MCP `ask` tool (docs/architecture/agents.md): retrieval -> trust-layered
prompt -> generation -> citation resolution -> cited answer or refusal. Zero
resolvable citations is an error, never a degraded answer
(.claude/rules/grounding-and-privacy.md); every outcome is traced **except the two
refusals that happen before the pipeline starts** — the no-key case and, since decision
29, a graph arm on a subject with no graph. Both are preflighted above `TracedAnswer`
on purpose: nothing ran, nothing was paid for, so a row recording it would be a record
of an attempt rather than of an answer. **`drill_down`/`overview` differ**: they raise
the same `GraphNotBuiltError` from *inside* their trace (agents/study_modes.py), so
they write an error row where this writes none. That divergence is known, not
incidental — those two reach the retriever directly and have no arm to preflight.

**The arm is chosen explicitly and defaults to `vector`.** There is no router: measured
on apd, `classify()` routed 30 of 48 questions to `graph-global` and scored 47.9%
against 45.8% for a constant classifier, so it bought a provider round-trip per `ask`
and spent it badly. It stays off this path on those merits, not because there is only
one arm to pick — extending the arm comparison past retrieval to citation accuracy,
faithfulness and cost per answer needs this pipeline runnable on each arm.

`router_label` stays on `AskResult` and in the trace schema, always `None` from here.
It keeps meaning "what the router said" rather than being repurposed — `agents/router.py`
is still measured by `groundly eval`, just never on the way to an answer.

**`graph-global` is not askable**, and that restriction is mechanical rather than a
verdict. It emits `sorted(chunk_ids)` — ascending rowid, no relevance order — so the
`context_k` truncation below would hand the model whichever chunks sort first, the same
ones for every question. `groundly eval --arms graph-global` scores it on the
order-insensitive metrics that remain honest for it (`UNRANKED_ARMS`)."""

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
from groundly.retrieval.arms import ARM_TABLE, VECTOR, retrieve_for_arm, validate_arms
from groundly.retrieval.graph import GraphNotBuiltError
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
    arm: str = VECTOR,
    rerank: bool = True,
    embedder=None,
    reranker=None,
) -> AskResult:
    """Answer `query` from `subject`'s materials through `arm`, defaulting to the arm
    that won the measured comparison at every cutoff the product uses.

    Raises `ValueError` for an arm that is unknown, unimplemented or unranked, and
    `GraphNotBuiltError` for a graph arm on a subject with no graph — never a quiet
    fall back to the baseline, which would put another arm's numbers under this one's
    name."""
    validate_arms([arm], ranked_only=True)
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    # Both refusals land before `TracedAnswer` opens, so a run that cannot work leaves no
    # trace row and loads no model — the same bargain `require_provider` makes above.
    # Provider first: configuring one is a config edit, building a graph is an hour and
    # real money, and nobody should be sent to spend that on a run that still cannot
    # generate an answer.
    if ARM_TABLE[arm].needs_graph and not subj.graph_is_built():
        # Names the arm, because the default one works on this subject and "no graph"
        # alone does not explain why *this* invocation is the one that failed.
        raise GraphNotBuiltError(
            f"the {arm!r} arm needs one. Build it with `groundly index {subject} <paths> "
            f"--graph`, or ask through {VECTOR!r}, which needs no graph."
        )

    store = SubjectStore(subj.store_db_path)

    # `router_label` is left at None throughout: the column still means "what the router
    # said", and the router no longer runs here. Kept on the result and the trace so a
    # re-admitted router needs no schema change (see the module docstring).
    with TracedAnswer(subj, kind="ask", query=query) as trace:
        trace.arm = arm
        nodes, trace.path = retrieve_for_arm(
            subject,
            query,
            arm,
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
