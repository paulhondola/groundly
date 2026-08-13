"""The arm table: which retrieval arms exist, what each one is, and how to run exactly
one of them (docs/architecture/retrieval.md, "the four arms").

The comparison between the arms *is* the thesis contribution, so the arms are data here
rather than control flow. Parallel string collections used to encode it — each
hand-maintained in `agents/ask.py` alongside an if/elif dispatch — which meant
re-admitting or retiring an arm touched four places that could disagree, in the one
project whose whole subject is arms disagreeing.

**Every implemented arm is selectable, and no arm silently becomes another one.** There
is no longer a product/research split in this table: `ask()` takes `arm=`, `groundly
eval --arms` takes the same names, and a graph arm on a subject with no graph raises
rather than degrading to the baseline. The one restriction left is mechanical rather
than editorial — `ask` truncates to `context_k`, so it refuses arms whose order carries
no relevance signal (`ranked=False`); see `validate_arms`.

**This lives in `retrieval/`, not `agents/`.** `groundly eval` is retrieval-only: it
scores candidates without paying for generation. While the dispatch lived next to
`ask()`, importing it pulled `llm/chat`, `agents/prompts` and `agents/citations` into a
harness that calls none of them.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore

from groundly.core.store import SubjectStore
from groundly.retrieval.graph import GraphGlobalRetriever
from groundly.retrieval.vector import RERANK_POOL, HybridLocalRetriever, VectorRetriever

# The eval's baseline and `ask`'s default. Named once because it is also a table key.
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
    # (MRR) must not be computed over them, and `ask` must not truncate them to
    # `context_k`. See `graph-global` below.
    #
    # **The safe direction is opt-out, and it used to be opt-in.** Under decision 28 a
    # new arm was unreachable from a user question until someone set `product=True`;
    # now a new arm is askable unless someone sets `ranked=False`. That is right for
    # `ranked` — most retrievers do rank, and claiming otherwise by default would
    # withhold MRR from arms that earned it — but it means adding an arm carelessly
    # makes it askable. `test_every_ranked_arm_is_askable_and_the_unranked_one_is_not`
    # pins the askable set for exactly this reason.
    ranked: bool = True
    # Whether this arm needs a built graph. The *preflight* predicate: callers refuse
    # up front rather than paying for a run that cannot work. It is no longer a
    # fallback trigger — nothing degrades to the baseline any more.
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
    VECTOR: Arm(name=VECTOR, build=_build_vector),
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
"""Every implemented arm — what `groundly eval` may score, and (minus the unranked ones)
what `ask` may be pointed at."""

UNRANKED_ARMS = frozenset(name for name, arm in ARM_TABLE.items() if not arm.ranked)
"""Arms whose returned order carries no relevance signal; MRR is withheld for these."""


def validate_arms(names: Sequence[str], *, ranked_only: bool = False) -> None:
    """Refuse a batch of arm names before any work starts, naming which mistake it was.

    Shared by every surface that takes arm names — `groundly eval`'s argument parsing
    and `eval.runner.run`, `groundly ask`'s and `agents.ask.ask` — because a check in
    only one of a pair is a check that does not happen: the CLI screens first, so a
    library-only check is unreachable from the product surface, and a CLI-only check
    leaves the library entry point unguarded.

    Three messages, not one. "unknown arm" sends someone hunting a typo they did not
    make when the real answer is that arm 4 has no implementation yet — it is in
    `ARM_TABLE` and in docs/architecture/retrieval.md, just not dispatchable.

    `ranked_only` is what `ask` passes. It is a mechanical restriction, not a revived
    product/research split: `ask` truncates to `context_k`, and truncating a list that
    carries no relevance order returns whichever chunks sort first rather than the best
    ones. The eval never passes it — scoring an unranked arm on order-insensitive
    metrics is exactly what `UNRANKED_ARMS` exists to make safe.
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
    unranked = [n for n in names if n in UNRANKED_ARMS] if ranked_only else []
    if unranked:
        raise ValueError(
            f"retrieval arm(s) {', '.join(unranked)} return no relevance order, so an "
            f"answer cannot be grounded in their top results — truncating to context_k "
            f"would take whichever chunks sort first, the same ones for every question. "
            f"Score them with `groundly eval --arms {','.join(unranked)}` instead. "
            f"Askable arms: {', '.join(n for n in ARMS if n not in UNRANKED_ARMS)}"
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
) -> tuple[list[NodeWithScore], list[str]]:
    """Run exactly one retrieval arm. Returns (nodes, path) — the arm that ran is always
    the arm that was asked for.

    **A graph arm on a subject with no graph raises `GraphNotBuiltError`.** It used to
    degrade to vector and report which arm actually ran, and both callers wanted that
    gone: the eval treated the degradation as fatal anyway, and someone who typed
    `--arm hybrid-local` does not want the baseline's numbers under that name. Callers
    preflight with `ARM_TABLE[arm].needs_graph` and `Subject.graph_is_built()` so the
    refusal lands before anything is started or paid for; this is the backstop.

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
    return retriever.retrieve(query), retriever.path
