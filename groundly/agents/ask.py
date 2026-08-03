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

from llama_index.core.schema import NodeWithScore

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

ARMS = ("vector", "hybrid-local", "graph-global")

# Router vocabulary -> arm. The router speaks query classes, the retrieval layer
# speaks arms; keeping the two vocabularies separate is what lets the eval force an
# arm without inventing a fake router label (docs/architecture/retrieval.md).
_LABEL_TO_ARM = {"multi-hop": "hybrid-local", "global": "graph-global"}


@dataclass
class AskResult:
    answer: str
    citations: list[Citation]
    router_label: str | None


def retrieve_for_arm(
    subject: str,
    query: str,
    arm: str,
    *,
    store: SubjectStore,
    rerank: bool = True,
    embedder=None,
    reranker=None,
) -> tuple[list[NodeWithScore], list[str], str]:
    """Run exactly one retrieval arm. Returns (nodes, path, arm_actual); `arm_actual`
    differs from `arm` only when a graph arm degraded to vector because no graph is
    built — that degradation is what the trace's `arm` column records.

    No arm calls `chat` here — that is why the eval harness can score retrieval without
    the generation step, and why this dispatch lives in its own function rather than
    inline in `ask()`. Note the asymmetry: `vector` needs no provider at all, while the
    graph arms reach the `extraction` provider inside graphrag's own search call
    (retrieval/graph.py's known gap — untraced, unmetered). Their costs differ by an
    order of magnitude: `local_search` is ~1 call, `global_search` is map-reduce over
    community summaries and runs tens of calls per query.

    An unknown arm raises rather than silently falling through to vector — a typo in
    `groundly eval --arms` must not quietly score the baseline three times.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown retrieval arm {arm!r} — expected one of {', '.join(ARMS)}")

    vector_retriever = VectorRetriever(store, embedder=embedder, reranker=reranker, rerank=rerank)

    if arm == "hybrid-local":
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
            return nodes, graph_retriever.path + vector_retriever.path, "hybrid-local"
        except GraphNotBuiltError:
            logger.info(
                "arm %s needs a graph and none is built for %s — degrading to vector-only",
                arm,
                subject,
            )
    elif arm == "graph-global":
        try:
            graph_retriever = GraphGlobalRetriever(subject)
            return graph_retriever.retrieve(query), graph_retriever.path, "graph-global"
        except GraphNotBuiltError:
            logger.info(
                "arm %s needs a graph and none is built for %s — degrading to vector-only",
                arm,
                subject,
            )

    nodes = vector_retriever.retrieve(query)
    return nodes, vector_retriever.path, "vector"


def ask(
    subject: str,
    query: str,
    *,
    rerank: bool = True,
    embedder=None,
    reranker=None,
    arm: str | None = None,
) -> AskResult:
    """`arm=None` is the product path: classify, then dispatch on the label. Passing an
    explicit arm skips the router entirely — the eval harness needs the same question
    through every arm, which a router label cannot express (router accuracy is measured
    separately by calling `classify()` directly)."""
    require_provider("chat")  # fail before any model load; nothing started, nothing to trace

    subj = Subject(subject)
    store = SubjectStore(subj.store_db_path)
    progress_conn = connect_progress(subj.progress_db_path)

    router_label: str | None = None
    requested_arm = arm  # what the caller asked for; `arm` below is what actually ran
    arm = None
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
        if requested_arm is None:
            router_label = classify(query, complete)
            requested_arm = _LABEL_TO_ARM.get(router_label, "vector")

        nodes, path, arm = retrieve_for_arm(
            subject,
            query,
            requested_arm,
            store=store,
            rerank=rerank,
            embedder=embedder,
            reranker=reranker,
        )

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
