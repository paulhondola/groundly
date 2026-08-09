"""Retrieval metrics — pure functions over id lists, no I/O, no store, no provider.

Every function takes `retrieved` (best-first chunk ids, order matters) and `expected`
(the labelled answer chunks). Keeping them pure is what lets the whole scoring layer be
unit-tested without an index, a model, or a key.
"""

from dataclasses import dataclass, field, replace
from math import comb
from statistics import mean


def hit(retrieved: list[int], expected: set[int]) -> bool:
    """Did any labelled chunk make the retrieved set at all? The coarsest question, and
    the one the thesis's per-class table leads with."""
    return bool(expected) and bool(set(retrieved) & expected)


def recall(retrieved: list[int], expected: set[int]) -> float:
    """Fraction of labelled chunks retrieved. Distinguishes an arm that found one of
    four relevant pages from one that found all four — the difference multi-hop is
    supposed to show up in."""
    if not expected:
        return 0.0
    return len(set(retrieved) & expected) / len(expected)


def reciprocal_rank(retrieved: list[int], expected: set[int]) -> float:
    """1 / (1-based rank of the first labelled chunk), 0.0 if none. Rewards putting the
    answer at position 1 rather than position 8 — the context window is `context_k`, so
    rank inside it still matters to what the model actually reads first.

    Only meaningful when `retrieved` is ordered by relevance. Callers must skip it for
    arms in `retrieval.arms.UNRANKED_ARMS`; `Scored.score` does.
    """
    for i, cid in enumerate(retrieved, start=1):
        if cid in expected:
            return 1.0 / i
    return 0.0


def leakage(retrieved: list[int], source: set[int]) -> float:
    """Fraction of retrieved chunks that came from any exam/quiz file the gold questions
    were drawn from. Not an error — a measurement. The gold set forbids *labelling* those
    files (gold.py), but an arm can still retrieve them, and a high rate means the arm is
    matching question text rather than the material that answers it.

    Both sides are deduplicated: an arm that returns the same chunk twice must not have
    its leakage diluted by the repeat appearing only in the denominator.
    """
    unique = set(retrieved)
    if not unique:
        return 0.0
    return len(unique & source) / len(unique)


@dataclass
class Scored:
    """One question through one arm.

    `retrieved_n` is not decoration. Arms do not return comparable set sizes — measured
    on apd, the vector arm returns `context_k` (8) while graph-global returns 1,138 of
    the subject's 1,193 chunks, i.e. 95% of the corpus, for every question. Its recall
    of 1.00 is an artifact of returning nearly everything, not evidence of retrieval.
    Any hit-rate or recall comparison across arms is meaningless without this column
    beside it — and it is `retrieved_n`, not MRR, that carries that signal, because the
    same arm has no relevance order for a rank metric to read (`reciprocal_rank`).
    """

    question_id: str
    arm: str
    klass: str
    lang: str
    retrieved: list[int]
    retrieved_n: int
    hit: bool
    recall: float
    reciprocal_rank: float | None  # None = the arm returns no relevance order at all
    leakage: float
    # The labels this row was scored against, stored so a finished results file can be
    # re-scored at any k without the store that produced it. Re-deriving labels from a
    # live index is how the published truncation table had to be built, and a re-index
    # shifts every chunk id underneath it.
    expected: list[int] = field(default_factory=list)
    latency_ms: int | None = None
    error: str | None = None

    @classmethod
    def failed(cls, *, question, arm: str, error: str) -> "Scored":
        """A question the arm could not answer — a provider outage, a context overflow.
        Recorded rather than raised so one bad question cannot destroy a multi-hour run,
        and excluded from every quality metric so an outage never reads as poor
        retrieval (see `aggregate`)."""
        return cls(
            question_id=question.id,
            arm=arm,
            klass=question.klass,
            lang=question.lang,
            retrieved=[],
            retrieved_n=0,
            hit=False,
            recall=0.0,
            reciprocal_rank=None,
            leakage=0.0,
            error=error,
        )

    @classmethod
    def score(
        cls,
        *,
        question,
        arm: str,
        retrieved: list[int],
        expected: set[int],
        source: set[int],
        latency_ms: int | None = None,
        ranked: bool = True,
    ) -> "Scored":
        """`ranked=False` for an arm whose returned order carries no relevance signal
        (`retrieval.arms.UNRANKED_ARMS`). Rank-sensitive metrics are then left `None` rather
        than computed over an arbitrary order and published as if they meant something.
        The caller passes this in so metrics stays import-free of the service layer."""
        return cls(
            question_id=question.id,
            arm=arm,
            klass=question.klass,
            lang=question.lang,
            retrieved=retrieved,
            retrieved_n=len(retrieved),
            hit=hit(retrieved, expected),
            recall=recall(retrieved, expected),
            reciprocal_rank=reciprocal_rank(retrieved, expected) if ranked else None,
            leakage=leakage(retrieved, source),
            expected=sorted(expected),
            latency_ms=latency_ms,
        )

    def at(self, k: int, source: set[int]) -> "Scored":
        """This row re-scored against only its top `k` retrieved chunks.

        Arms do not return comparable set sizes, so a single-k table compares them at
        sizes they never shared — apd's published headline put vector's 8 chunks against
        hybrid-local's 33 and graph-global's 1,138 and read the differences as quality.
        Scoring every arm at the same k is the fix, and doing it here (rather than by
        re-running) is what makes it affordable: one sweep, every k.
        """
        top = self.retrieved[:k]
        want = set(self.expected)
        return replace(
            self,
            retrieved=top,
            retrieved_n=len(top),
            hit=hit(top, want),
            recall=recall(top, want),
            # Stays None for arms that never had a rank to truncate.
            reciprocal_rank=(None if self.reciprocal_rank is None else reciprocal_rank(top, want)),
            leakage=leakage(top, source),
        )


@dataclass
class Aggregate:
    n: int  # questions scored (errored ones excluded)
    errors: int
    hit_rate: float
    recall: float
    mrr: float | None  # None = no ranked question in this slice; see Scored.score
    leakage: float
    median_retrieved_n: int
    median_latency_ms: int | None = None
    slice: dict[str, str] = field(default_factory=dict)


def _median(values: list[int]) -> int:
    return int(sorted(values)[len(values) // 2]) if values else 0


def aggregate(scored: list[Scored], **slice_: str) -> Aggregate:
    """Quality metrics average over questions that actually ran. An errored question is
    counted in `errors`, never as a miss — a provider outage must not read as an arm
    retrieving badly, which is the one way this table could lie in the arm's favour or
    against it without anyone noticing."""
    ok = [s for s in scored if s.error is None]
    latencies = [s.latency_ms for s in ok if s.latency_ms is not None]
    # Averaged only over rows that actually carry a rank. An unranked arm contributes
    # nothing rather than a zero, so its MRR column reads "—" instead of "very bad".
    ranks = [s.reciprocal_rank for s in ok if s.reciprocal_rank is not None]
    return Aggregate(
        n=len(ok),
        errors=len(scored) - len(ok),
        hit_rate=mean(1.0 if s.hit else 0.0 for s in ok) if ok else 0.0,
        recall=mean(s.recall for s in ok) if ok else 0.0,
        mrr=mean(ranks) if ranks else None,
        leakage=mean(s.leakage for s in ok) if ok else 0.0,
        median_retrieved_n=_median([s.retrieved_n for s in ok]),
        median_latency_ms=_median(latencies) if latencies else None,
        slice=slice_,
    )


def by_slice(scored: list[Scored], *keys: str) -> list[Aggregate]:
    """Group and aggregate on any combination of `arm`/`klass`/`lang`. `by_slice(rows,
    "arm")` is the headline table; `by_slice(rows, "arm", "klass")` is the one that
    answers whether graph arms earn their cost on multi-hop."""
    groups: dict[tuple, list[Scored]] = {}
    for s in scored:
        groups.setdefault(tuple(getattr(s, k) for k in keys), []).append(s)
    return [
        aggregate(rows, **dict(zip(keys, values)))
        for values, rows in sorted(groups.items(), key=lambda kv: kv[0])
    ]


def is_ranked(s: "Scored") -> bool:
    """Whether this row's `retrieved` order carries a relevance signal. `reciprocal_rank`
    is None exactly for arms in `retrieval.arms.UNRANKED_ARMS` (errored rows carry `error`)."""
    return s.reciprocal_rank is not None or s.error is not None


def sweep(scored: list[Scored], ks: list[int], source: set[int], *keys: str) -> list[Aggregate]:
    """`by_slice` repeated at each cutoff, with `k` added to every slice label.

    This is the table the arm comparison should lead with. One retrieval sweep produces
    all of it, because `Scored` keeps the full candidate list and its labels — so
    choosing a different k later costs nothing, where re-running the graph arms costs
    hours. Errored rows pass through untouched and stay excluded by `aggregate`.

    **Unranked arms are omitted entirely**, for the same reason they report `mrr = None`:
    `graph-global` emits `sorted(chunk_ids)`, so its "top 8" is the eight lowest rowids in
    the corpus — a number that looks like precision@8 and measures ingestion order. A
    cutoff is only meaningful over an order that means something. Callers report the
    omission (`unranked_arms`) rather than leaving a silent hole in the table.
    """
    rankable = [s for s in scored if is_ranked(s)]
    out: list[Aggregate] = []
    for k in ks:
        rows = [s if s.error is not None else s.at(k, source) for s in rankable]
        for agg in by_slice(rows, *keys):
            agg.slice["k"] = str(k)
            out.append(agg)
    return out


def unranked_arms(scored: list[Scored]) -> list[str]:
    """Arms excluded from the `@k` table because they return no relevance order."""
    return sorted({s.arm for s in scored if not is_ranked(s)})


def mcnemar(a: list[Scored], b: list[Scored]) -> tuple[int, int, float]:
    """Exact two-sided McNemar test on paired hit outcomes. Returns (a_only, b_only, p).

    Arms are scored on the *same* questions, so the comparison is paired and the only
    informative rows are the disagreements: questions arm A hits and B misses, and vice
    versa. Under the null the split is a fair coin, so p comes straight from the binomial
    — no scipy, no normal approximation (which is invalid at these counts anyway).

    This exists because the published verdict was "hybrid-local hits 1 question vector
    misses and misses 4 it finds — net -3". Five discordant pairs give p = 0.375: a
    result indistinguishable from a tie. At n = 48 this harness cannot resolve a
    difference much smaller than 10 questions, and saying so is a stronger claim than
    reporting the -3.

    Rows with errors are dropped from both sides — an outage is not a miss.
    """
    a_hits = {s.question_id: s.hit for s in a if s.error is None}
    b_hits = {s.question_id: s.hit for s in b if s.error is None}
    shared = a_hits.keys() & b_hits.keys()

    a_only = sum(1 for q in shared if a_hits[q] and not b_hits[q])
    b_only = sum(1 for q in shared if b_hits[q] and not a_hits[q])

    n = a_only + b_only
    if n == 0:
        return 0, 0, 1.0
    tail = sum(comb(n, i) for i in range(min(a_only, b_only) + 1))
    return a_only, b_only, min(1.0, 2.0 * tail / 2**n)
