"""Graph-only study modes (UC-12): `drill_down` (entity-anchored local search) and
`overview` (community-synthesis global search), each mirroring `ask()`'s "fail before
any model load -> retrieve -> assemble -> generate -> refusal-check -> resolve
citations -> trace" sequence (docs/architecture/agents.md). Unlike `ask()`'s
router-driven degrade-to-vector, a missing graph here is an availability
precondition (UC-12) — `GraphNotBuiltError` is left to propagate uncaught, not
degraded to the vector arm.

The trace bookkeeping the three share lives in `agents/tracing.py`; what is left in
each function below is the part that actually differs.
"""

from dataclasses import dataclass

from groundly.agents.ask import AskResult
from groundly.agents.citations import resolve_citations
from groundly.agents.prompts import REFUSAL, assemble, assemble_overview
from groundly.agents.tracing import TracedAnswer
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.llm.chat import complete
from groundly.llm.config import require_provider
from groundly.retrieval.graph import GraphGlobalRetriever, GraphLocalRetriever
from groundly.retrieval.nodes import chunk_ids as chunk_ids_of


@dataclass
class OverviewResult(AskResult):
    communities: list[dict]


def drill_down(subject: str, entity: str) -> AskResult:
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    store = SubjectStore(subj.store_db_path)

    with TracedAnswer(subj, kind="ask", query=entity, arm="drill_down") as trace:
        retriever = GraphLocalRetriever(subject)  # GraphNotBuiltError propagates uncaught
        nodes = retriever.retrieve(entity)
        trace.path = retriever.path
        trace.chunk_ids = chunk_ids_of(nodes)

        if not nodes:
            return AskResult(answer=trace.refuse(), citations=[], router_label=None)

        result = complete("chat", assemble(entity, nodes))
        trace.record_usage(result)

        if REFUSAL in result.text:
            return AskResult(answer=trace.refuse(), citations=[], router_label=None)

        citations = resolve_citations(result.text, trace.chunk_ids, store)
        trace.answered(result.text, citations)
        return AskResult(answer=result.text, citations=citations, router_label=None)


def overview(subject: str, topic: str) -> OverviewResult:
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    store = SubjectStore(subj.store_db_path)

    with TracedAnswer(subj, kind="ask", query=topic, arm="overview") as trace:
        retriever = GraphGlobalRetriever(subject)  # GraphNotBuiltError propagates uncaught
        nodes = retriever.retrieve(topic)
        trace.path = retriever.path
        trace.chunk_ids = chunk_ids_of(nodes)
        # Not a trace field, but part of the result on every path including refusal —
        # "an overview answer names its constituent communities" (UC-12) is still true
        # of an overview that has nothing to say.
        communities = retriever.communities

        if not nodes:
            return OverviewResult(
                answer=trace.refuse(), citations=[], router_label=None, communities=communities
            )

        result = complete("chat", assemble_overview(topic, communities, nodes))
        trace.record_usage(result)

        if REFUSAL in result.text:
            return OverviewResult(
                answer=trace.refuse(), citations=[], router_label=None, communities=communities
            )

        citations = resolve_citations(result.text, trace.chunk_ids, store)
        trace.answered(result.text, citations)
        return OverviewResult(
            answer=result.text,
            citations=citations,
            router_label=None,
            communities=communities,
        )
