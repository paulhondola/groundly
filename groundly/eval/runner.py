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

from groundly.agents.ask import retrieve_for_arm
from groundly.eval import gold as gold_mod
from groundly.eval.metrics import Scored, by_slice

logger = logging.getLogger(__name__)


class ArmDegradedError(RuntimeError):
    """A graph arm ran as vector because no graph is built. Fatal for an eval: the run
    would report graph numbers that are really baseline numbers, which is worse than no
    numbers at all."""


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
) -> dict:
    """Score `arms` over the gold set. Returns the results document (also what gets
    written to disk). `on_question(question, arm)` is a progress callback."""
    questions = gold_mod.load(gold_path)
    expected, source, warnings = gold_mod.resolve(questions, store)

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
                nodes, _path, arm_actual = retrieve_for_arm(
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
            except Exception as exc:
                # One question must not destroy the run. A 48-question two-graph-arm
                # sweep is ~4.4 hours against a local provider; losing all of it to a
                # single context overflow is how the first real run ended. Errors are
                # recorded, excluded from quality metrics, and reported loudly.
                logger.warning("%s on arm %s failed: %s", question.id, arm, exc)
                scored.append(Scored.failed(question=question, arm=arm, error=str(exc)))
                continue
            latency_ms = int((time.monotonic() - start) * 1000)
            if arm_actual != arm:
                # Unlike a per-question error this is a configuration fact — it will hold
                # for every remaining question, so there is nothing to salvage by going on.
                raise ArmDegradedError(
                    f"arm {arm!r} degraded to {arm_actual!r} — no graph is built for "
                    f"'{subject}'. Build it first: groundly index {subject} <paths> --graph"
                )
            scored.append(
                Scored.score(
                    question=question,
                    arm=arm,
                    retrieved=[n.node.metadata["chunk_id"] for n in nodes],
                    expected=expected[question.id],
                    source=source[question.id],
                    latency_ms=latency_ms,
                )
            )

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
