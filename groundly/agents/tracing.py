"""The trace row for one ask-shaped call, as a context manager.

`ask()`, `drill_down()` and `overview()` are three different pipelines that produce one
identical piece of bookkeeping: ten fields, a timer, and a `traces` row that must be
written on *every* exit path — answered, refused, or raised. Written inline that was
~70 lines per function, ~85% of it the same, and the duplication was load-bearing in the
wrong direction: the rule is that **every outcome is traced**
(.claude/rules/grounding-and-privacy.md), so a fourth pipeline copying the block and
dropping one `finally` is a silently untraced path rather than a visible bug.

What is deliberately *not* here: retrieval, prompt assembly, the `chat` call, and
citation resolution. Those differ across the three on five axes, and a runner taking
five callables reads worse than three short explicit functions. This owns the trace and
nothing else.
"""

import sqlite3
import time
from types import TracebackType

from groundly.agents.citations import Citation
from groundly.agents.prompts import REFUSAL
from groundly.core.progress import connect_progress, record_trace
from groundly.core.subject import Subject


class TracedAnswer:
    """Opens progress.db, times the call, and writes exactly one `traces` row on exit.

    Fields the caller fills in as it learns them (`arm`, `path`, `chunk_ids`,
    `router_label`) are plain attributes; the ones with a rule attached go through
    `record_usage`/`refuse`/`answered`, so "refused" can never be recorded without the
    refusal text and "answered" can never be recorded without citations.

    `outcome` starts at `"error"` on purpose. Any exit that does not explicitly claim a
    better outcome is an error, which is the safe direction: an untraced failure and a
    failure traced as a success are both worse than a success traced as a failure.
    """

    def __init__(self, subj: Subject, *, kind: str, query: str, arm: str | None = None) -> None:
        self._subj = subj
        self._kind = kind
        self._query = query
        self.arm = arm
        self.router_label: str | None = None
        self.path: list[str] = []
        self.chunk_ids: list[int] = []

        self._outcome = "error"
        self._answer: str | None = None
        self._citations: list[Citation] = []
        self._model: str | None = None
        self._tokens: int | None = None
        self._cost_usd: float | None = None
        self._error: str | None = None
        self._conn: sqlite3.Connection | None = None
        self._start = 0.0

    def __enter__(self) -> "TracedAnswer":
        self._conn = connect_progress(self._subj.progress_db_path)
        self._start = time.monotonic()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc is not None:
            self._outcome = "error"
            # `str()` only for `Exception`, reproducing the `except Exception` this
            # replaced. `__exit__` also sees `BaseException`, so without the guard a
            # KeyboardInterrupt mid-`ask` writes `error = ""` (that is what
            # `str(KeyboardInterrupt())` is) where the old code left NULL — turning
            # "the student interrupted it" into a zero-length error message in the
            # traces table the thesis reads. `outcome` is "error" either way, exactly
            # as before, because that is its initial value.
            if isinstance(exc, Exception):
                self._error = str(exc)
        try:
            record_trace(
                self._conn,
                kind=self._kind,
                query=self._query,
                router_label=self.router_label,
                arm=self.arm,
                # Empty -> NULL rather than "[]": a call that never retrieved and one
                # that retrieved nothing are different facts about the run.
                path=self.path or None,
                chunk_ids=self.chunk_ids or None,
                outcome=self._outcome,
                answer=self._answer,
                citations=[c.__dict__ for c in self._citations] or None,
                model=self._model,
                tokens=self._tokens,
                cost_usd=self._cost_usd,
                latency_ms=int((time.monotonic() - self._start) * 1000),
                error=self._error,
            )
        finally:
            self._conn.close()
        return False  # never suppress — the caller's exception is the caller's to handle

    def record_usage(self, result) -> None:
        """Model, tokens and cost from a `llm.chat.complete` result. Separate from
        `answered` because a refusal costs tokens too, and dropping them there would
        under-report spend in exactly the case the student is most likely to repeat."""
        self._model = result.model
        self._tokens = result.tokens
        self._cost_usd = result.cost_usd

    def refuse(self) -> str:
        """Record the refusal and hand back the one sentence it is allowed to be. The
        text comes from here rather than the caller so a refusal cannot be traced under
        one wording and returned under another."""
        self._outcome = "refused"
        self._answer = REFUSAL
        return REFUSAL

    def answered(self, answer: str, citations: list[Citation]) -> None:
        """Record both together, so a caller cannot trace an answer and forget its
        citations.

        This does **not** enforce that `citations` is non-empty, and an earlier version
        of this docstring implied it did. `resolve_citations` raises `NoCitationsError`
        when the model cited nothing resolvable, which is the guard that matters — but
        its return comprehension drops ids whose chunk vanished between retrieval and
        the detail lookup, so it can still hand back `[]`. That path records
        `outcome="answered"` with no citations, which the grounding rule forbids. It
        predates this class and is not something a refactor should change silently.
        """
        self._outcome = "answered"
        self._answer = answer
        self._citations = citations
