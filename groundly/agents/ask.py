"""The ask pipeline — the one shared function exposed identically as `groundly ask`
and the MCP `ask` tool (docs/architecture/agents.md): router -> vector retrieval ->
trust-layered prompt -> generation -> citation resolution -> cited answer or refusal.
Zero resolvable citations is an error, never a degraded answer
(.claude/rules/grounding-and-privacy.md); every outcome (including errors and the
no-key case never reaching this far) is traced."""

import re
import time
from dataclasses import dataclass

from groundly.agents.prompts import REFUSAL, assemble
from groundly.agents.router import classify
from groundly.core.store import SQLiteSubjectStore, connect_progress, record_trace
from groundly.core.subject import Subject
from groundly.llm.chat import complete
from groundly.llm.config import require_provider
from groundly.retrieval.vector import VectorRetriever

_CITATION_RE = re.compile(r"\[chunk (\d+)\]")


@dataclass
class Citation:
    chunk_id: int
    filename: str
    page: int | None
    heading_path: str | None


@dataclass
class AskResult:
    answer: str
    citations: list[Citation]
    router_label: str | None


class NoCitationsError(Exception):
    """Every cited chunk id in the model's response was hallucinated (not among the
    retrieved set) — zero resolvable citations is an error, never a degraded answer."""


def ask(
    subject: str,
    query: str,
    *,
    rerank: bool = True,
    embedder=None,
    reranker=None,
) -> AskResult:
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    store = SQLiteSubjectStore(subj.store_db_path)
    progress_conn = connect_progress(subj.progress_db_path)

    router_label: str | None = None
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
        router_label = classify(query, complete)

        retriever = VectorRetriever(store, embedder=embedder, reranker=reranker, rerank=rerank)
        nodes = retriever.retrieve(query)
        path = retriever.path
        chunk_ids = [n.node.metadata["chunk_id"] for n in nodes]

        if not nodes:
            outcome = "refused"
            answer = REFUSAL
            return AskResult(answer=REFUSAL, citations=[], router_label=router_label)

        messages = assemble(query, nodes)
        result = complete("chat", messages)
        model, tokens, cost_usd = result.model, result.tokens, result.cost_usd

        if REFUSAL in result.text:
            outcome = "refused"
            answer = REFUSAL
            return AskResult(answer=REFUSAL, citations=[], router_label=router_label)

        cited_ids = {int(m) for m in _CITATION_RE.findall(result.text)}
        resolvable_ids = [cid for cid in chunk_ids if cid in cited_ids]  # hallucinated ids dropped
        if not resolvable_ids:
            raise NoCitationsError(
                "the model's response cited no chunk ids that resolve to retrieved chunks"
            )

        details = {row["chunk_id"]: row for row in store.chunk_details(resolvable_ids)}
        citations = [
            Citation(
                chunk_id=cid,
                filename=details[cid]["filename"],
                page=details[cid]["page"],
                heading_path=details[cid]["heading_path"],
            )
            for cid in resolvable_ids
            if cid in details
        ]
        outcome = "answered"
        answer = result.text
        return AskResult(answer=answer, citations=citations, router_label=router_label)
    except Exception as exc:
        outcome = "error"
        error = str(exc)
        raise
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        record_trace(
            progress_conn,
            kind="ask",
            query=query,
            router_label=router_label,
            arm="vector",
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
