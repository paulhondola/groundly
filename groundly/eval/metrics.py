"""Retrieval metrics — pure functions over id lists, no I/O, no store, no provider.

Every function takes `retrieved` (best-first chunk ids, order matters) and `expected`
(the labelled answer chunks). Keeping them pure is what lets the whole scoring layer be
unit-tested without an index, a model, or a key.
"""

from dataclasses import dataclass, field
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
    rank inside it still matters to what the model actually reads first."""
    for i, cid in enumerate(retrieved, start=1):
        if cid in expected:
            return 1.0 / i
    return 0.0


def leakage(retrieved: list[int], source: set[int]) -> float:
    """Fraction of retrieved chunks that came from the question's own source (exam/quiz)
    file. Not an error — a measurement. The gold set forbids *labelling* the source file
    (gold.py), but an arm can still retrieve it, and a high rate means the arm is
    matching the question text rather than the material that answers it."""
    if not retrieved:
        return 0.0
    return len(set(retrieved) & source) / len(retrieved)


@dataclass
class Scored:
    """One question through one arm.

    `retrieved_n` is not decoration. Arms do not return comparable set sizes — measured
    on apd, the vector arm returns `context_k` (8) while graph-global returns 1,138 of
    the subject's 1,193 chunks, i.e. 95% of the corpus, for every question. Its recall
    of 1.00 is an artifact of returning nearly everything, not evidence of retrieval.
    Any hit-rate or recall comparison across arms is meaningless without this column
    beside it.
    """

    question_id: str
    arm: str
    klass: str
    lang: str
    retrieved: list[int]
    retrieved_n: int
    hit: bool
    recall: float
    reciprocal_rank: float
    leakage: float
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
            reciprocal_rank=0.0,
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
    ) -> "Scored":
        return cls(
            question_id=question.id,
            arm=arm,
            klass=question.klass,
            lang=question.lang,
            retrieved=retrieved,
            retrieved_n=len(retrieved),
            hit=hit(retrieved, expected),
            recall=recall(retrieved, expected),
            reciprocal_rank=reciprocal_rank(retrieved, expected),
            leakage=leakage(retrieved, source),
            latency_ms=latency_ms,
        )


@dataclass
class Aggregate:
    n: int  # questions scored (errored ones excluded)
    errors: int
    hit_rate: float
    recall: float
    mrr: float
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
    return Aggregate(
        n=len(ok),
        errors=len(scored) - len(ok),
        hit_rate=mean(1.0 if s.hit else 0.0 for s in ok) if ok else 0.0,
        recall=mean(s.recall for s in ok) if ok else 0.0,
        mrr=mean(s.reciprocal_rank for s in ok) if ok else 0.0,
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
