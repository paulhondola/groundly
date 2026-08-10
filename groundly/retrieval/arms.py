"""The arm table: which retrieval arms exist, what each one is, and how to run exactly
one of them (docs/architecture/retrieval.md, "the four arms").

The comparison between the arms *is* the thesis contribution, so the arms are data here
rather than control flow. Three parallel string collections used to encode it —
`ARMS`, `PRODUCT_ARMS`, `UNRANKED_ARMS`, each hand-maintained in `agents/ask.py`
alongside an if/elif dispatch — which meant re-admitting or retiring an arm touched four
places that could disagree, in the one project whose whole subject is arms disagreeing.

**This lives in `retrieval/`, not `agents/`.** `groundly eval` is retrieval-only: it
scores candidates without paying for generation. While the dispatch lived next to
`ask()`, importing it pulled `llm/chat`, `agents/prompts` and `agents/citations` into a
harness that calls none of them.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore

from groundly.core.store import SubjectStore
from groundly.retrieval.graph import GraphGlobalRetriever, GraphNotBuiltError
from groundly.retrieval.vector import RERANK_POOL, HybridLocalRetriever, VectorRetriever

logger = logging.getLogger(__name__)

# The one arm a missing graph degrades to, and the eval's baseline. Named once because
# it is also a table key.
VECTOR = "vector"


@dataclass(frozen=True)
class ArmContext:
    """Everything an arm needs to be constructed. One shape for every arm, so the table
    below holds builders rather than four different call signatures."""

    subject: str
    store: SubjectStore
    rerank: bool
    embedder: object | None
    reranker: object | None


@dataclass(frozen=True)
class Arm:
    """One retrieval arm as a fact rather than a branch.

    `build=None` means *declared but not implemented* — arm 4 is in the architecture
    doc and has an interface-gate stub, and saying so here is more honest than leaving
    the table silently three-long.
    """

    name: str
    build: Callable[[ArmContext], BaseRetriever] | None
    # False for arms whose returned order carries no relevance signal — rank metrics
    # (MRR) must not be computed over them. See `graph-global` below.
    ranked: bool = True
    # Whether a *user question* may reach this arm, as opposed to `groundly eval`.
    product: bool = False
    # Whether a missing graph degrades this arm to the baseline instead of failing.
    needs_graph: bool = False


def _build_vector(ctx: ArmContext) -> BaseRetriever:
    # `RERANK_POOL`, not `context_k`: every arm returns its FULL candidate list and the
    # consumer applies the cap (see `retrieve_for_arm`). 20 is this arm's honest
    # ceiling — past it the fused order was never seen by the cross-encoder.
    return VectorRetriever(
        ctx.store,
        embedder=ctx.embedder,
        reranker=ctx.reranker,
        rerank=ctx.rerank,
        context_k=RERANK_POOL,
    )


def _build_hybrid_local(ctx: ArmContext) -> BaseRetriever:
    return HybridLocalRetriever(
        ctx.store,
        ctx.subject,
        embedder=ctx.embedder,
        reranker=ctx.reranker,
        rerank=ctx.rerank,
        context_k=RERANK_POOL,
    )


def _build_graph_global(ctx: ArmContext) -> BaseRetriever:
    return GraphGlobalRetriever(ctx.subject)


# In the order docs/architecture/retrieval.md numbers them.
ARM_TABLE: dict[str, Arm] = {
    VECTOR: Arm(name=VECTOR, build=_build_vector, product=True),
    "hybrid-local": Arm(name="hybrid-local", build=_build_hybrid_local, needs_graph=True),
    # `ranked=False`: global search resolves its citations through a set and emits
    # `sorted(chunk_ids)` — ascending SQLite rowid, i.e. the order chunks happened to be
    # indexed in. An MRR over that reports corpus layout, not retrieval quality.
    # Order-insensitive metrics (hit rate, recall, leakage) stay valid. This is a
    # property of the arm and it disappears once global search ranks its output.
    "graph-global": Arm(
        name="graph-global", build=_build_graph_global, ranked=False, needs_graph=True
    ),
    # Arm 4. `retrieval/adaptive.py` holds the interface-gate stub; there is nothing to
    # dispatch to yet, so `--arms adaptive` is refused up front by `ARMS` below rather
    # than swallowed once per question by the eval's error tolerance.
    "adaptive": Arm(name="adaptive", build=None),
}

# Derived views — one source, so re-admitting an arm is one line in the table above.
ARMS = tuple(name for name, arm in ARM_TABLE.items() if arm.build is not None)
"""What `groundly eval` may score: every implemented arm."""

PRODUCT_ARMS = tuple(name for name, arm in ARM_TABLE.items() if arm.product)
"""What a user question may reach. `ARMS` and this being different is the whole content
of decision 28 — retiring an arm from the product is not the same as deleting it."""

UNRANKED_ARMS = frozenset(name for name, arm in ARM_TABLE.items() if not arm.ranked)
"""Arms whose returned order carries no relevance signal; MRR is withheld for these."""


def validate_arms(names: Sequence[str]) -> None:
    """Refuse a batch of arm names before any work starts, naming which mistake it was.

    Shared by `groundly eval`'s argument parsing and `eval.runner.run`, because a check
    in only one of them is a check that does not happen: the CLI screens first, so a
    runner-only check is unreachable from the product surface, and a CLI-only check
    leaves the library entry point unguarded.

    Two messages, not one. "unknown arm" sends someone hunting a typo they did not make
    when the real answer is that arm 4 has no implementation yet — it is in `ARM_TABLE`
    and in docs/architecture/retrieval.md, just not dispatchable.
    """
    unknown = [n for n in names if n not in ARM_TABLE]
    if unknown:
        raise ValueError(
            f"unknown retrieval arm(s): {', '.join(unknown)} — expected from: {', '.join(ARMS)}"
        )
    unimplemented = [n for n in names if ARM_TABLE[n].build is None]
    if unimplemented:
        raise ValueError(
            f"retrieval arm(s) {', '.join(unimplemented)} are declared but not implemented "
            f"— they exist in the architecture doc and as an interface stub only. "
            f"Scoreable arms: {', '.join(ARMS)}"
        )


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
    built — that degradation is what the trace's `arm` column records, and what makes
    `eval/runner.ArmDegradedError` able to refuse a run that would report baseline
    numbers under a graph arm's name.

    **Returns each arm's full candidate list, not its top `context_k`.** Applying the cap
    is the consumer's job (`ask()` does it). That split is what lets the eval score every
    k from a single sweep instead of one full re-run per k — which matters because the
    published comparison put an 8-chunk arm against a 33-chunk arm and called the
    difference a result.

    No arm calls `chat` here — that is why the eval harness can score retrieval without
    the generation step. Note the asymmetry: `vector` and `hybrid-local` need no provider
    at all, while `graph-global` reaches the `extraction` provider inside graphrag's own
    map-reduce (retrieval/graph.py's known gap — untraced, unmetered), running tens of
    calls per query rather than one.

    An unknown arm raises rather than silently falling through to vector — a typo in
    `groundly eval --arms` must not quietly score the baseline three times.
    """
    spec = ARM_TABLE.get(arm)
    if spec is None:
        raise ValueError(f"unknown retrieval arm {arm!r} — expected one of {', '.join(ARMS)}")
    if spec.build is None:
        # Distinct from "unknown": the arm is real, documented and named in ARM_TABLE —
        # it just has no implementation to dispatch to. Saying "unknown arm 'adaptive'"
        # would send someone looking for a typo they did not make.
        raise ValueError(
            f"retrieval arm {arm!r} is declared but not implemented — it exists in the "
            f"architecture doc and as an interface stub only. Scoreable arms: {', '.join(ARMS)}"
        )

    ctx = ArmContext(
        subject=subject, store=store, rerank=rerank, embedder=embedder, reranker=reranker
    )
    retriever = spec.build(ctx)
    try:
        nodes = retriever.retrieve(query)
    except GraphNotBuiltError:
        # Only for arms that declared the dependency. Anything else raising this is a
        # bug in that arm, not a missing build, and must not be absorbed as a fallback.
        if not spec.needs_graph:
            raise
        logger.info(
            "arm %s needs a graph and none is built for %s — degrading to vector-only",
            arm,
            subject,
        )
        fallback = _build_vector(ctx)
        return fallback.retrieve(query), fallback.path, VECTOR
    return nodes, retriever.path, arm
