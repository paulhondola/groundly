"""Graph-only study modes (UC-12): `drill_down` (entity-anchored local search) and
`overview` (community-synthesis global search), each mirroring `ask()`'s "fail before
any model load -> retrieve -> assemble -> generate -> refusal-check -> resolve
citations -> trace" sequence (docs/architecture/agents.md). Unlike `ask()`'s
router-driven degrade-to-vector, a missing graph here is an availability
precondition (UC-12) — `GraphNotBuiltError` is left to propagate uncaught, not
degraded to the vector arm."""

import time
from dataclasses import dataclass

from groundly.agents.ask import AskResult
from groundly.agents.citations import Citation, resolve_citations
from groundly.agents.prompts import REFUSAL, assemble, assemble_overview
from groundly.core.progress import connect_progress, record_trace
from groundly.core.store import SQLiteSubjectStore
from groundly.core.subject import Subject
from groundly.llm.chat import complete
from groundly.llm.config import require_provider
from groundly.retrieval.graph import GraphGlobalRetriever, GraphLocalRetriever


@dataclass
class OverviewResult(AskResult):
    communities: list[dict]


def drill_down(subject: str, entity: str) -> AskResult:
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    store = SQLiteSubjectStore(subj.store_db_path)
    progress_conn = connect_progress(subj.progress_db_path)

    path: list[str] = []
    chunk_ids: list[int] = []
    outcome = "error"
    answer: str | None = None
    citations: list[Citation] = []
    model: str | None = None
    tokens: int | None = None
    cost_usd: float | None = None
    error: str | None = None
    start = time.monotonic()

    try:
        retriever = GraphLocalRetriever(subject)  # GraphNotBuiltError propagates uncaught
        nodes = retriever.retrieve(entity)
        path = retriever.path
        chunk_ids = [n.node.metadata["chunk_id"] for n in nodes]

        if not nodes:
            outcome = "refused"
            answer = REFUSAL
            return AskResult(answer=REFUSAL, citations=[], router_label=None)

        messages = assemble(entity, nodes)
        result = complete("chat", messages)
        model, tokens, cost_usd = result.model, result.tokens, result.cost_usd

        if REFUSAL in result.text:
            outcome = "refused"
            answer = REFUSAL
            return AskResult(answer=REFUSAL, citations=[], router_label=None)

        citations = resolve_citations(result.text, chunk_ids, store)
        outcome = "answered"
        answer = result.text
        return AskResult(answer=answer, citations=citations, router_label=None)
    except Exception as exc:
        outcome = "error"
        error = str(exc)
        raise
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        record_trace(
            progress_conn,
            kind="ask",
            query=entity,
            router_label=None,
            arm="drill_down",
            path=path or None,
            chunk_ids=chunk_ids or None,
            outcome=outcome,
            answer=answer,
            citations=[c.__dict__ for c in citations] if citations else None,
            model=model,
            tokens=tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            error=error,
        )
        progress_conn.close()


def overview(subject: str, topic: str) -> OverviewResult:
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    store = SQLiteSubjectStore(subj.store_db_path)
    progress_conn = connect_progress(subj.progress_db_path)

    path: list[str] = []
    chunk_ids: list[int] = []
    communities: list[dict] = []
    outcome = "error"
    answer: str | None = None
    citations: list[Citation] = []
    model: str | None = None
    tokens: int | None = None
    cost_usd: float | None = None
    error: str | None = None
    start = time.monotonic()

    try:
        retriever = GraphGlobalRetriever(subject)  # GraphNotBuiltError propagates uncaught
        nodes = retriever.retrieve(topic)
        path = retriever.path
        communities = retriever.communities
        chunk_ids = [n.node.metadata["chunk_id"] for n in nodes]

        if not nodes:
            outcome = "refused"
            answer = REFUSAL
            return OverviewResult(
                answer=REFUSAL, citations=[], router_label=None, communities=communities
            )

        messages = assemble_overview(topic, communities, nodes)
        result = complete("chat", messages)
        model, tokens, cost_usd = result.model, result.tokens, result.cost_usd

        if REFUSAL in result.text:
            outcome = "refused"
            answer = REFUSAL
            return OverviewResult(
                answer=REFUSAL, citations=[], router_label=None, communities=communities
            )

        citations = resolve_citations(result.text, chunk_ids, store)
        outcome = "answered"
        answer = result.text
        return OverviewResult(
            answer=answer, citations=citations, router_label=None, communities=communities
        )
    except Exception as exc:
        outcome = "error"
        error = str(exc)
        raise
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        record_trace(
            progress_conn,
            kind="ask",
            query=topic,
            router_label=None,
            arm="overview",
            path=path or None,
            chunk_ids=chunk_ids or None,
            outcome=outcome,
            answer=answer,
            citations=[c.__dict__ for c in citations] if citations else None,
            model=model,
            tokens=tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            error=error,
        )
        progress_conn.close()
