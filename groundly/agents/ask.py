"""The ask pipeline — the one shared function exposed identically as `groundly ask`
and the MCP `ask` tool (docs/architecture/agents.md): router -> arm-aware retrieval ->
trust-layered prompt -> generation -> citation resolution -> cited answer or refusal.
Zero resolvable citations is an error, never a degraded answer
(.claude/rules/grounding-and-privacy.md); every outcome (including errors and the
no-key case never reaching this far) is traced.

Router label picks the retrieval arm: factoid/None -> vector only; multi-hop ->
graph local search fused with vector via RRF; global -> graph global search alone.
If the subject has no graph built, both graph arms degrade to vector-only rather
than failing `ask()` outright (`arm` in the trace reflects what actually ran, not
what the router asked for)."""

import logging
import time
from dataclasses import dataclass

from groundly.agents.citations import Citation, NoCitationsError, resolve_citations  # noqa: F401  re-exported: mcp/server.py + cli/ask.py import NoCitationsError from here
from groundly.agents.prompts import REFUSAL, assemble
from groundly.agents.router import classify
from groundly.core.progress import connect_progress, record_trace
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.llm.chat import complete
from groundly.llm.config import require_provider
from groundly.retrieval.graph import GraphGlobalRetriever, GraphLocalRetriever, GraphNotBuiltError
from groundly.retrieval.vector import VectorRetriever, rrf

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
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    store = SubjectStore(subj.store_db_path)
    progress_conn = connect_progress(subj.progress_db_path)

    router_label: str | None = None
    arm: str | None = None
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

        vector_retriever = VectorRetriever(
            store, embedder=embedder, reranker=reranker, rerank=rerank
        )

        if router_label == "multi-hop":
            try:
                graph_retriever = GraphLocalRetriever(subject)
                graph_nodes = graph_retriever.retrieve(query)
                vector_nodes = vector_retriever.retrieve(query)
                by_id = {n.node.metadata["chunk_id"]: n for n in graph_nodes + vector_nodes}
                fused = rrf(
                    [
                        [n.node.metadata["chunk_id"] for n in graph_nodes],
                        [n.node.metadata["chunk_id"] for n in vector_nodes],
                    ]
                )
                nodes = [by_id[cid] for cid, _ in fused if cid in by_id]
                path = graph_retriever.path + vector_retriever.path
                arm = "hybrid-local"
            except GraphNotBuiltError:
                logger.info(
                    "router picked multi-hop but no graph is built for %s — degrading to vector-only",
                    subject,
                )
                nodes = vector_retriever.retrieve(query)
                path = vector_retriever.path
                arm = "vector"
        elif router_label == "global":
            try:
                graph_retriever = GraphGlobalRetriever(subject)
                nodes = graph_retriever.retrieve(query)
                path = graph_retriever.path
                arm = "graph-global"
            except GraphNotBuiltError:
                logger.info(
                    "router picked global but no graph is built for %s — degrading to vector-only",
                    subject,
                )
                nodes = vector_retriever.retrieve(query)
                path = vector_retriever.path
                arm = "vector"
        else:  # factoid or no router label
            nodes = vector_retriever.retrieve(query)
            path = vector_retriever.path
            arm = "vector"

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

        citations = resolve_citations(result.text, chunk_ids, store)
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
            arm=arm,
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
