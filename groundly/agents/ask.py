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

The three arms all still exist in `retrieve_for_arm` below: retiring them from the
product path is not the same as deleting them, and the eval harness is what keeps the
negative result reproducible from shipped code."""

import logging
import time
from dataclasses import dataclass

from llama_index.core.schema import NodeWithScore

from groundly.agents.citations import Citation, NoCitationsError, resolve_citations  # noqa: F401  re-exported: mcp/server.py + cli/ask.py import NoCitationsError from here
from groundly.agents.prompts import REFUSAL, assemble
from groundly.core.config import load_settings
from groundly.core.progress import connect_progress, record_trace
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.llm.chat import complete
from groundly.llm.config import require_provider
from groundly.retrieval.graph import GraphGlobalRetriever, GraphLocalRetriever, GraphNotBuiltError
from groundly.retrieval.vector import RERANK_POOL, VectorRetriever, rrf

logger = logging.getLogger(__name__)

ARMS = ("vector", "hybrid-local", "graph-global")

# Arms the *product* may select. `ARMS` is what the eval can score; this is what a user
# question can reach. Stated as data rather than left implicit in `ask()`'s body so
# "which arms are shipped" is a fact a test can assert, and so re-admitting an arm is a
# visible one-line change rather than a re-plumbing of the dispatch (decision 28).
PRODUCT_ARMS = ("vector",)

# Arms whose returned order carries NO relevance signal. `graph-global` resolves its
# citations through a set and emits `sorted(chunk_ids)` (retrieval/graph.py) — ascending
# SQLite rowid, i.e. the order chunks happened to be indexed in. Rank-sensitive metrics
# (MRR) must not be computed over it: they would report where a labelled chunk sits in
# rowid order and read as evidence about retrieval quality. Order-insensitive metrics
# (hit rate, recall) stay valid. This is a property of the arm, not of the eval, and it
# disappears once global search ranks its output.
UNRANKED_ARMS = frozenset({"graph-global"})


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

    **Returns each arm's full candidate list, not its top `context_k`.** Applying the cap
    is the consumer's job (`ask()` does it). That split is what lets the eval score every
    k from a single sweep instead of one full re-run per k — which matters because the
    published comparison put an 8-chunk arm against a 33-chunk arm and called the
    difference a result. The vector arm's honest ceiling is `RERANK_POOL` (20): beyond
    that the fused order was never seen by the cross-encoder, so a longer list would mix
    reranked and un-reranked positions and mean nothing.

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

    vector_retriever = VectorRetriever(
        store,
        embedder=embedder,
        reranker=reranker,
        rerank=rerank,
        context_k=RERANK_POOL,
    )

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
    progress_conn = connect_progress(subj.progress_db_path)

    # Always None from `ask`: the column still means "what the router said", and the
    # router no longer runs here. Kept on the result and the trace so a re-admitted
    # router needs no schema change (see the module docstring).
    router_label: str | None = None
    arm: str | None = None  # what actually ran — `retrieve_for_arm` reports degradation
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
        nodes, path, arm = retrieve_for_arm(
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
