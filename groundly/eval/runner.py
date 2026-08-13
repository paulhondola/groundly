"""The eval loop: every gold question through every requested arm, scored offline.

Retrieval-only by design at this slice — no `chat` call, no generation. The vector arm
needs no provider at all; the graph arms need `extraction`, because graphrag synthesises
inside its own search call (groundly/eval/__init__.py). Generation metrics (citation
accuracy, faithfulness, cost) come from full `ask()` runs and land in the next slice;
they read the traces table, which retrieval-only runs deliberately do not write to.
"""

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from groundly.core.subject import Subject
from groundly.eval import gold as gold_mod
from groundly.eval.metrics import Scored, by_slice, mcnemar, sweep, unranked_arms
from groundly.retrieval.arms import ARM_TABLE, UNRANKED_ARMS, retrieve_for_arm, validate_arms
from groundly.retrieval.graph import GraphNotBuiltError

logger = logging.getLogger(__name__)

# Cutoffs the arm comparison is reported at. Ends at RERANK_POOL because that is the
# vector arm's honest ceiling — past it the fused order was never seen by the
# cross-encoder, so a longer list mixes reranked and un-reranked positions.
DEFAULT_AT_K = (1, 5, 8, 10, 20)

# Every arm is tested against this one. It is the zero-key, zero-build arm, so "does the
# graph earn its cost" is the only question the comparison is really asking.
BASELINE_ARM = "vector"


# Exceptions that mean *this code is broken*, never "the provider had a bad minute".
# Per-question error tolerance exists for outages and context overflows; letting it
# absorb a KeyError in the node-metadata contract or a TypeError in a citation join
# would report a broken arm as a flaky one — and TypeError is exactly how the
# text-unit-collision bug in retrieval/graph.py presented before it was fixed.
_BUG_ERRORS = (AttributeError, ImportError, IndexError, KeyError, NameError, TypeError)


def run(
    subject: str,
    gold_path: Path,
    store,
    *,
    arms: list[str],
    rerank: bool = True,
    embedder=None,
    reranker=None,
    on_question=None,
    at_k: tuple[int, ...] = DEFAULT_AT_K,
) -> dict:
    """Score `arms` over the gold set. Returns the results document (also what gets
    written to disk). `on_question(question, arm)` is a progress callback."""
    # Validate every arm before the first question. A typo'd arm raises ValueError from
    # `retrieve_for_arm`, and the per-question handler below would file that as an
    # "error" — producing a results file that looks like a provider outage instead of
    # refusing to run. Fail here, before any model loads.
    validate_arms(arms)

    # Same shape, same reason, for the graph the graph arms need. A missing graph is a
    # configuration fact: it holds for every remaining question, so there is nothing to
    # salvage by starting. Preflighting also keeps it out of reach of the per-question
    # handler below, which would otherwise file 48 identical failures and write a
    # results file full of zeroes.
    if any(ARM_TABLE[a].needs_graph for a in arms) and not Subject(subject).graph_is_built():
        # The exception already prefixes "graph not built for this subject —", so this
        # says only which arms need one and how to get it.
        raise GraphNotBuiltError(
            f"{', '.join(a for a in arms if ARM_TABLE[a].needs_graph)} cannot be scored "
            f"without it. Build it first: groundly index {subject} <paths> --graph"
        )

    questions = gold_mod.load(gold_path)
    expected, source, warnings, base_rate = gold_mod.resolve(questions, store)

    scored: list[Scored] = []
    interrupted = False
    for question in questions:
        if interrupted:
            break
        for arm in arms:
            if on_question is not None:
                on_question(question, arm)
            start = time.monotonic()
            try:
                nodes, _path = retrieve_for_arm(
                    subject,
                    question.query,
                    arm,
                    store=store,
                    rerank=rerank,
                    embedder=embedder,
                    reranker=reranker,
                )
            except KeyboardInterrupt:
                # Hours of graph queries are worth keeping. Results are still written,
                # but flagged `partial` — a half-run silently indistinguishable from a
                # full one is the one outcome worse than losing it.
                logger.warning("interrupted at %s on arm %s", question.id, arm)
                interrupted = True
                break
            except _BUG_ERRORS:
                raise
            except Exception as exc:
                # One question must not destroy the run. A 48-question two-graph-arm
                # sweep is ~4.4 hours against a local provider; losing all of it to a
                # single context overflow is how the first real run ended. Errors are
                # recorded, excluded from quality metrics, and reported loudly — but
                # only for the failures that are genuinely the provider's (`_BUG_ERRORS`
                # above re-raise, so a broken arm crashes instead of scoring as flaky).
                logger.warning("%s on arm %s failed: %s", question.id, arm, exc)
                scored.append(Scored.failed(question=question, arm=arm, error=str(exc)))
                continue
            latency_ms = int((time.monotonic() - start) * 1000)
            scored.append(
                Scored.score(
                    question=question,
                    arm=arm,
                    retrieved=[n.node.metadata["chunk_id"] for n in nodes],
                    expected=expected[question.id],
                    source=source[question.id],
                    latency_ms=latency_ms,
                    ranked=arm not in UNRANKED_ARMS,
                )
            )

    source_chunks = next(iter(source.values()), set())
    baseline = [s for s in scored if s.arm == BASELINE_ARM]
    return {
        "subject": subject,
        "gold": str(gold_path),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "arms": arms,
        "rerank": rerank,
        "questions": len(questions),
        "partial": interrupted,
        "errors": sum(1 for s in scored if s.error is not None),
        "warnings": warnings,
        # Share of the corpus that is question-source material — the denominator that
        # makes the leakage column readable (gold.resolve).
        "leakage_base_rate": base_rate,
        # Latency is only comparable across arms when one arm ran: a resident local model
        # slows every other arm on the machine (measured 5.4x on the vector arm), so a
        # mixed sweep's per-arm timings say more about contention than about retrieval.
        "latency_comparable": len(arms) == 1,
        "at_k": [asdict(a) for a in sweep(scored, list(at_k), source_chunks, "arm")],
        # Named, not silently absent: an arm missing from the matched table is a claim
        # about that arm (it has no relevance order), not a gap in the run.
        "at_k_excluded_arms": unranked_arms(scored),
        # Significance is computed at each *matched* cutoff, never at the arms' natural
        # set sizes. Testing "hit at 42 chunks" against "hit at 20 chunks" would inherit
        # exactly the set-size confound the at_k table exists to remove, and would hand a
        # p-value to a comparison that was never fair — measured on apd, the unmatched
        # rows read as a tie (p = 1.000) while every matched cutoff favours the baseline.
        "significance": [
            {
                "arm": arm,
                "baseline": BASELINE_ARM,
                "k": k,
                **dict(
                    zip(
                        ("arm_only", "baseline_only", "p"),
                        mcnemar(
                            [s.at(k, source_chunks) for s in scored if s.arm == arm],
                            [s.at(k, source_chunks) for s in baseline],
                        ),
                    )
                ),
            }
            for k in at_k
            for arm in arms
            # Unranked arms are excluded for the same reason they are absent from the
            # at_k table: truncating an order that carries no relevance signal produces
            # a comparison about ingestion order.
            if arm != BASELINE_ARM and baseline and arm not in unranked_arms(scored)
        ],
        "by_arm": [asdict(a) for a in by_slice(scored, "arm")],
        "by_arm_class": [asdict(a) for a in by_slice(scored, "arm", "klass")],
        "by_arm_lang": [asdict(a) for a in by_slice(scored, "arm", "lang")],
        "rows": [asdict(s) for s in scored],
    }


def write_results(results: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = results["ts"].replace(":", "").replace("-", "")
    path = out_dir / f"results-{stamp}.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return path
