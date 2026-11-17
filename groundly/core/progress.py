"""progress.db: the student's private study state — the tests they submitted and every answer.

**Never exported**: the privacy boundary is a file, so core/bundle.py imports nothing from
here. Question and topic ids are plain integers, never foreign keys into store.db, so
history survives a question or topic that is later removed.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

PROGRESS_USER_VERSION = 2

_SCHEMA = """
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    scope_topic_id INTEGER,
    question_count INTEGER NOT NULL
);

CREATE TABLE answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    topic_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    response TEXT NOT NULL,
    correct INTEGER CHECK (correct IN (0, 1)),
    comment TEXT,
    reviewed_at TEXT
);
CREATE INDEX idx_answers_attempt ON answers(attempt_id);
CREATE INDEX idx_answers_topic ON answers(topic_id);
"""


@dataclass
class AnswerWrite:
    """One graded answer. `response` is the chosen option ids, or the open answer's text;
    `correct` is None while an open answer awaits review."""

    question_id: int
    topic_id: int
    kind: str
    response: list[int] | str
    correct: bool | None


class ReviewError(Exception):
    """A verdict that cannot be recorded; raised before anything is written."""

    def __init__(self, reason: str, answer_id: int) -> None:
        super().__init__(f"{reason}: answer {answer_id}")
        self.reason, self.answer_id = reason, answer_id


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")


def _create_tables(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute(f"PRAGMA user_version = {PROGRESS_USER_VERSION}")


def create_progress(path: Path) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        _configure(conn)
        conn.execute("BEGIN IMMEDIATE")
        _create_tables(conn)
        conn.execute("COMMIT")
    finally:
        conn.close()


def connect(path: Path) -> sqlite3.Connection:
    """Open progress.db, giving a version-1 file (created empty) its tables first. The
    version is read again under the write lock: another process may have upgraded it."""
    if not path.exists():
        raise RuntimeError(f"{path.name} is missing from {path.parent} — the subject is damaged")
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    _configure(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > PROGRESS_USER_VERSION:
        conn.close()
        raise RuntimeError(
            f"{path.name} has schema version {version}, newer than this groundly "
            f"understands (max {PROGRESS_USER_VERSION}) — upgrade groundly"
        )
    if version < PROGRESS_USER_VERSION:
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute("PRAGMA user_version").fetchone()[0] < PROGRESS_USER_VERSION:
                    _create_tables(conn)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        except BaseException:
            conn.close()  # a lock timeout on BEGIN must not leak the connection either
            raise
    return conn


def _answer(row: sqlite3.Row) -> dict:
    out = dict(row)
    out.pop("position", None)
    out["response"] = json.loads(out["response"])
    out["correct"] = None if out["correct"] is None else bool(out["correct"])
    return out


_ANSWER_COLUMNS = (
    "id, attempt_id, position, question_id, topic_id, kind, response, correct, comment, reviewed_at"
)


class ProgressStore:
    """A subject's progress.db: submitted attempts, their answers, and host verdicts."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One transaction holding the write lock from its first statement."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def add_attempt(
        self, token: str, scope_topic_id: int | None, answers: list[AnswerWrite]
    ) -> int:
        """Save one attempt and its answers in one transaction. A token already stored
        means the same Submit arrived twice: return that attempt and write nothing."""
        with self._write() as conn:
            row = conn.execute("SELECT id FROM attempts WHERE token = ?", (token,)).fetchone()
            if row:
                return row["id"]
            attempt_id = conn.execute(
                "INSERT INTO attempts (token, scope_topic_id, question_count) VALUES (?, ?, ?)",
                (token, scope_topic_id, len(answers)),
            ).lastrowid
            conn.executemany(
                "INSERT INTO answers (attempt_id, position, question_id, topic_id, kind, "
                "response, correct) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        attempt_id,
                        position,
                        a.question_id,
                        a.topic_id,
                        a.kind,
                        json.dumps(a.response),
                        None if a.correct is None else int(a.correct),
                    )
                    for position, a in enumerate(answers)
                ],
            )
            return attempt_id

    def attempts(self) -> list[dict]:
        """Every attempt, newest first."""
        with self._read() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id, submitted_at, scope_topic_id, question_count FROM attempts "
                    "ORDER BY id DESC"
                )
            ]

    def attempt(self, attempt_id: int) -> dict | None:
        """One attempt with its answers in the order they were shown, or None."""
        with self._read() as conn:
            row = conn.execute(
                "SELECT id, submitted_at, scope_topic_id, question_count FROM attempts "
                "WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            answers = conn.execute(
                f"SELECT {_ANSWER_COLUMNS} FROM answers WHERE attempt_id = ? ORDER BY position",
                (attempt_id,),
            ).fetchall()
        return {**dict(row), "answers": [_answer(a) for a in answers]}

    def answers(self) -> list[dict]:
        """Every answer, oldest first."""
        with self._read() as conn:
            return [
                _answer(r)
                for r in conn.execute(
                    f"SELECT {_ANSWER_COLUMNS} FROM answers ORDER BY attempt_id, position"
                )
            ]

    def record_reviews(self, reviews: list[tuple[int, bool, str]]) -> None:
        """Record host verdicts on open answers awaiting review, all or none. Every id is
        checked before anything is written."""
        with self._write() as conn:
            for answer_id, _, _ in reviews:
                row = conn.execute(
                    "SELECT kind, correct FROM answers WHERE id = ?", (answer_id,)
                ).fetchone()
                if row is None or row["kind"] != "open":
                    raise ReviewError("unknown_answer_id", answer_id)
                if row["correct"] is not None:
                    raise ReviewError("already_reviewed", answer_id)
            conn.executemany(
                "UPDATE answers SET correct = ?, comment = ?, reviewed_at = datetime('now') "
                "WHERE id = ?",
                [(int(correct), comment, answer_id) for answer_id, correct, comment in reviews],
            )
