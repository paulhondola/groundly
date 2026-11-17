"""progress.db: the student's private test attempts and answers, never exported."""

import sqlite3

import pytest

from groundly.core import progress
from groundly.core.progress import AnswerWrite, ProgressStore, ReviewError


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "progress.db"
    progress.create_progress(path)
    return ProgressStore(path)


def _answers():
    return [
        AnswerWrite(question_id=7, topic_id=2, kind="single", response=[31], correct=True),
        AnswerWrite(question_id=8, topic_id=2, kind="multiple", response=[40, 41], correct=False),
        AnswerWrite(question_id=9, topic_id=3, kind="open", response="Because.", correct=None),
    ]


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_create_progress_makes_the_tables_at_version_2(tmp_path):
    path = tmp_path / "progress.db"
    progress.create_progress(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == progress.PROGRESS_USER_VERSION
    finally:
        conn.close()
    assert {"attempts", "answers"} <= _tables(path)


def test_an_empty_version_1_file_is_upgraded_on_open(tmp_path):
    path = tmp_path / "progress.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 1")
    conn.close()

    assert ProgressStore(path).attempts() == []
    assert {"attempts", "answers"} <= _tables(path)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_a_newer_progress_file_is_refused(tmp_path):
    path = tmp_path / "progress.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer than this groundly"):
        ProgressStore(path).attempts()


def test_a_missing_progress_file_names_the_cause(tmp_path):
    with pytest.raises(RuntimeError, match="progress.db is missing"):
        ProgressStore(tmp_path / "progress.db").attempts()


def test_add_attempt_round_trips_choice_and_open_answers(db):
    attempt_id = db.add_attempt("token-0001", 2, _answers())

    (row,) = db.attempts()
    assert (row["id"], row["scope_topic_id"], row["question_count"]) == (attempt_id, 2, 3)
    assert row["submitted_at"]
    got = db.attempt(attempt_id)
    assert got["id"] == attempt_id
    assert [
        (a["question_id"], a["topic_id"], a["kind"], a["response"], a["correct"])
        for a in got["answers"]
    ] == [
        (7, 2, "single", [31], True),
        (8, 2, "multiple", [40, 41], False),
        (9, 3, "open", "Because.", None),
    ]
    assert all(a["attempt_id"] == attempt_id for a in got["answers"])
    assert all(a["comment"] is None and a["reviewed_at"] is None for a in got["answers"])


def test_a_whole_course_attempt_has_no_scope(db):
    db.add_attempt("token-0001", None, _answers())
    assert db.attempts()[0]["scope_topic_id"] is None


def test_attempts_are_listed_newest_first_and_answers_oldest_first(db):
    first = db.add_attempt("token-0001", 2, _answers()[:1])
    second = db.add_attempt("token-0002", 3, _answers()[2:])
    assert [a["id"] for a in db.attempts()] == [second, first]
    assert [a["question_id"] for a in db.answers()] == [7, 9]


def test_an_unknown_attempt_is_none(db):
    assert db.attempt(404) is None


def test_the_same_token_returns_the_first_attempt_and_writes_nothing(db):
    first = db.add_attempt("token-0001", 2, _answers())
    again = db.add_attempt("token-0001", 3, _answers()[:1])
    assert again == first
    assert len(db.attempts()) == 1 and len(db.answers()) == 3


def _open_answer_id(db):
    return next(a["id"] for a in db.answers() if a["kind"] == "open")


def test_record_reviews_sets_verdict_comment_and_time(db):
    db.add_attempt("token-0001", 2, _answers())
    open_id = _open_answer_id(db)
    db.record_reviews([(open_id, True, "Names the serial fraction.")])
    (reviewed,) = [a for a in db.answers() if a["id"] == open_id]
    assert reviewed["correct"] is True
    assert reviewed["comment"] == "Names the serial fraction."
    assert reviewed["reviewed_at"]


def test_one_bad_review_records_none(db):
    db.add_attempt("token-0001", 2, _answers())
    open_id = _open_answer_id(db)
    with pytest.raises(ReviewError) as raised:
        db.record_reviews([(open_id, True, "ok"), (9999, True, "x")])
    assert (raised.value.reason, raised.value.answer_id) == ("unknown_answer_id", 9999)
    assert next(a for a in db.answers() if a["id"] == open_id)["correct"] is None


def test_a_reviewed_answer_cannot_be_reviewed_again(db):
    db.add_attempt("token-0001", 2, _answers())
    open_id = _open_answer_id(db)
    db.record_reviews([(open_id, False, "Misses the point.")])
    with pytest.raises(ReviewError) as raised:
        db.record_reviews([(open_id, True, "Changed my mind.")])
    assert (raised.value.reason, raised.value.answer_id) == ("already_reviewed", open_id)


def test_a_choice_answer_cannot_be_reviewed(db):
    db.add_attempt("token-0001", 2, _answers())
    choice_id = next(a["id"] for a in db.answers() if a["kind"] == "single")
    with pytest.raises(ReviewError) as raised:
        db.record_reviews([(choice_id, False, "x")])
    assert raised.value.reason == "unknown_answer_id"


def test_every_connection_is_wal_with_a_busy_timeout(db):
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()
