"""Tests on the local page: drawing a test from the question bank, grading a submitted
attempt, and the mastery each topic has reached.

Answer keys and reference answers never leave this module in a drawn test; grading reads the
keys from store.db at submit time. Loads no model: everything here is SQL and set arithmetic.
"""

from typing import Any, TypeGuard

from groundly.core.progress import AnswerWrite, ProgressStore, ReviewError
from groundly.core.store import SubjectStore

MASTERY_WINDOW = 10
MASTERY_CORRECT = 9
TEST_LENGTHS = (5, 10, 20)
MAX_TEST_QUESTIONS = max(TEST_LENGTHS)
MAX_OPEN_ANSWER_CHARS = 4000
MAX_COMMENT_CHARS = 1000
MAX_REVIEW_BATCH = 20  # awaiting answers handed to the host per call, oldest first

_REMOVED = "a question in this test was removed — start a new test"


class QuizError(Exception):
    """A draw or an attempt that cannot be served; raised before anything is written."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


def _topics(tree: list[dict]) -> tuple[dict[int, list[int]], dict[int, str]]:
    """(parent id -> subtopic ids, id -> title), in tree order."""
    children = {node["id"]: [s["id"] for s in node["subtopics"]] for node in tree}
    titles = {node["id"]: node["title"] for node in tree}
    titles.update({s["id"]: s["title"] for node in tree for s in node["subtopics"]})
    return children, titles


def _leaves_in_order(tree: list[dict]) -> list[int]:
    return [s["id"] for node in tree for s in node["subtopics"]] + [
        node["id"] for node in tree if not node["subtopics"]
    ]


def mastery(
    store: SubjectStore, progress: ProgressStore, tree: list[dict] | None = None
) -> dict[int, dict]:
    """Every topic's state. A leaf is mastered at MASTERY_CORRECT or more correct of its last
    MASTERY_WINDOW graded answers, with at least that many; a parent when all its subtopics are.
    Answers awaiting review do not count; answers to removed questions do. Pass the `tree`
    the caller shows, so the states are keyed by exactly its nodes."""
    if tree is None:
        tree = store.tree(with_sources=False)
    children, _ = _topics(tree)
    bank = store.question_ids_by_topic()
    graded: dict[int, list[bool]] = {}
    for a in progress.answers():
        if a["correct"] is not None:
            graded.setdefault(a["topic_id"], []).append(a["correct"])

    out: dict[int, dict] = {}
    for leaf in _leaves_in_order(tree):
        history = graded.get(leaf, [])
        recent = history[-MASTERY_WINDOW:]
        right = sum(recent)
        if not history:
            state = "not_started"
        elif len(recent) == MASTERY_WINDOW and right >= MASTERY_CORRECT:
            state = "mastered"
        else:
            state = "in_progress"
        out[leaf] = {
            "questions": len(bank.get(leaf, [])),
            "answered": len(history),
            "recent": len(recent),
            "recent_correct": right,
            "state": state,
            "subtopics": None,
            "subtopics_mastered": None,
        }
    for parent, subs in children.items():
        if not subs:
            continue
        entries = [out[s] for s in subs]
        mastered = sum(e["state"] == "mastered" for e in entries)
        if mastered == len(entries):
            state = "mastered"
        elif any(e["state"] != "not_started" for e in entries):
            state = "in_progress"
        else:
            state = "not_started"
        out[parent] = {
            "questions": sum(e["questions"] for e in entries),
            "answered": sum(e["answered"] for e in entries),
            "recent": sum(e["recent"] for e in entries),
            "recent_correct": sum(e["recent_correct"] for e in entries),
            "state": state,
            "subtopics": len(entries),
            "subtopics_mastered": mastered,
        }
    return out


def subject_card(store: SubjectStore, progress: ProgressStore | None) -> dict:
    """What a subject's card on the page shows: top-level topics, leaves (subtopics plus
    topics with none), leaves mastered by the same rule as the topic map, and the newest
    test's time. Empty for a subject with no map; `progress=None` (unreadable) costs only
    the progress fields."""
    tree = store.tree(with_sources=False)
    if not tree:
        return {}
    leaves = _leaves_in_order(tree)
    card = {"topics": len(tree), "leaves": len(leaves), "leaves_mastered": 0}
    if progress is None:
        return card
    states = mastery(store, progress, tree)
    card["leaves_mastered"] = sum(states[leaf]["state"] == "mastered" for leaf in leaves)
    attempts = progress.attempts()
    if attempts:
        card["last_test_at"] = attempts[0]["submitted_at"]
    return card


def scope_leaves(store: SubjectStore, topic_id: int | None) -> list[int]:
    """The leaves a scope covers: a topic's subtopics, or the topic itself; every leaf for None."""
    tree = store.tree(with_sources=False)
    children, titles = _topics(tree)
    if topic_id is None:
        return _leaves_in_order(tree)
    if topic_id not in titles:
        raise QuizError("unknown_topic_id", f"no topic has id {topic_id} in this subject")
    return children.get(topic_id) or [topic_id]


def draw(store: SubjectStore, progress: ProgressStore, topic_id: int | None, n: int) -> list[dict]:
    """Up to `n` questions from the scope's leaves, taken round-robin starting with the leaves
    least recently practised (never practised first, ties in tree order), so successive tests
    of a wide scope reach every leaf. Within a topic: never answered, then last answered wrong,
    then least recently answered. The result carries no answer key and no reference answer."""
    if n not in TEST_LENGTHS:
        raise QuizError("invalid_text", f"a test is {', '.join(map(str, TEST_LENGTHS))} questions")
    leaves = scope_leaves(store, topic_id)
    last: dict[int, tuple[int, bool | None]] = {}
    practised: dict[int, int] = {}
    for index, a in enumerate(progress.answers()):
        last[a["question_id"]] = (index, a["correct"])
        practised[a["topic_id"]] = index
    leaves.sort(key=lambda leaf: practised.get(leaf, -1))  # stable: ties keep tree order

    def priority(q: dict) -> tuple[int, int, int]:
        if q["id"] not in last:
            return (0, 0, q["id"])
        index, correct = last[q["id"]]
        return (1 if correct is False else 2, index, q["id"])

    queues: dict[int, list[dict]] = {leaf: [] for leaf in leaves}
    for q in store.questions(topic_ids=leaves, with_sources=False):
        queues[q["topic_id"]].append(q)
    for queue in queues.values():
        queue.sort(key=priority, reverse=True)  # pop() takes from the end

    drawn: list[dict] = []
    while len(drawn) < n and any(queues.values()):
        for leaf in leaves:
            if queues[leaf] and len(drawn) < n:
                q = queues[leaf].pop()
                drawn.append(
                    {
                        "id": q["id"],
                        "topic_id": q["topic_id"],
                        "kind": q["kind"],
                        "stem": q["stem"],
                        "code": q["code"],
                        "options": [{"id": o["id"], "text": o["text"]} for o in q["options"]],
                    }
                )
    return drawn


def is_id(value: object) -> TypeGuard[int]:
    """An id SQLite can bind: an int (not a bool) within 64 bits."""
    return isinstance(value, int) and not isinstance(value, bool) and -(2**63) <= value < 2**63


def _grade(q: dict, answer: dict) -> AnswerWrite:
    if q["kind"] == "open":
        text = answer.get("text")
        if not isinstance(text, str) or "selected" in answer:
            raise QuizError("invalid_text", f"question {q['id']} takes a written answer")
        if len(text) > MAX_OPEN_ANSWER_CHARS:
            raise QuizError(
                "invalid_text", f"an answer is at most {MAX_OPEN_ANSWER_CHARS} characters"
            )
        return AnswerWrite(
            q["id"], q["topic_id"], "open", text, False if not text.strip() else None
        )
    selected = answer.get("selected")
    if not isinstance(selected, list) or not all(map(is_id, selected)) or "text" in answer:
        raise QuizError("invalid_text", f"question {q['id']} takes chosen option ids")
    chosen = set(selected)
    options = {o["id"] for o in q["options"]}
    if not chosen <= options:
        raise QuizError(
            "invalid_text", f"an option chosen for question {q['id']} is not one of its own"
        )
    if q["kind"] == "single" and len(chosen) > 1:
        raise QuizError("invalid_text", f"question {q['id']} takes one option")
    key = {o["id"] for o in q["options"] if o["correct"]}
    return AnswerWrite(
        q["id"], q["topic_id"], q["kind"], sorted(chosen), bool(chosen) and chosen == key
    )


def check_answer(store: SubjectStore, question_id: int, selected: list[int]) -> dict:
    """One choice answer graded before its test is submitted: whether it is right, the key,
    and the question's cited sources. Grades through `_grade`, so a check and a submit of
    the same choice agree; writes nothing, so the answer counts only once submitted."""
    found = store.questions(ids=[question_id]) if is_id(question_id) else []
    if not found:
        raise QuizError("unknown_question", _REMOVED)
    (q,) = found
    graded = _grade(q, {"selected": selected})
    return {
        "correct": bool(graded.correct),
        "key": [o["id"] for o in q["options"] if o["correct"]],
        "sources": q["sources"],
    }


def submit_attempt(
    store: SubjectStore,
    progress: ProgressStore,
    token: str,
    topic_id: int | None,
    answers: list[dict],
) -> int:
    """Grade the choice answers against store.db and save the attempt in one transaction.
    Every answer is checked before anything is written; the same token twice is one attempt.
    `topic_id` is the scope the test was drawn for, kept as a label: a re-map may have removed
    it since the draw, and that must not cost the student their answers."""
    if not answers or len(answers) > MAX_TEST_QUESTIONS:
        raise QuizError("too_large", f"a test has 1 to {MAX_TEST_QUESTIONS} answers")
    ids: list[int] = []
    for a in answers:
        question_id = a.get("question_id") if isinstance(a, dict) else None
        if not is_id(question_id):
            raise QuizError("invalid_text", "every answer names its question id")
        ids.append(question_id)
    if len(set(ids)) != len(ids):
        raise QuizError("invalid_text", "a question is answered twice")
    questions = {q["id"]: q for q in store.questions(ids=ids, with_sources=False)}
    if len(questions) != len(ids):
        raise QuizError("unknown_question", _REMOVED)
    graded = [_grade(questions[a["question_id"]], a) for a in answers]
    return progress.add_attempt(token, topic_id, graded)


def _state(answer: dict, removed: bool) -> str:
    if answer["correct"] is None:
        return "unreviewable" if removed else "awaiting"
    return "correct" if answer["correct"] else "wrong"


def _score(states: list[str]) -> dict:
    correct = states.count("correct")
    return {
        "correct": correct,
        "graded": correct + states.count("wrong"),
        "awaiting": states.count("awaiting"),
    }


def attempt_result(store: SubjectStore, progress: ProgressStore, attempt_id: int) -> dict | None:
    """One attempt as the result screen shows it: each answer with its key, the student's
    choice, the reference answer and sources, plus the mastery of every topic it touched."""
    attempt = progress.attempt(attempt_id)
    if attempt is None:
        return None
    ids = [a["question_id"] for a in attempt["answers"]]
    questions = {q["id"]: q for q in store.questions(ids=ids)}
    answers: list[dict[str, Any]] = []
    for a in attempt["answers"]:
        q = questions.get(a["question_id"])
        chosen = set(a["response"]) if isinstance(a["response"], list) else set()
        answers.append(
            {
                "answer_id": a["id"],
                "question_id": a["question_id"],
                "kind": a["kind"],
                "state": _state(a, q is None),
                "removed": q is None,
                "stem": q["stem"] if q else None,
                "code": q["code"] if q else None,
                "options": [
                    {**o, "selected": o["id"] in chosen} for o in (q["options"] if q else [])
                ],
                "text": a["response"] if a["kind"] == "open" else None,
                "reference_answer": q["reference_answer"] if q else None,
                "comment": a["comment"],
                "sources": q["sources"] if q else [],
            }
        )
    score = _score([a["state"] for a in answers])
    tree = store.tree(with_sources=False)
    _, titles = _topics(tree)
    states = mastery(store, progress, tree)
    topics = []
    for tid in dict.fromkeys(a["topic_id"] for a in attempt["answers"]):
        if tid in titles:
            topics.append({"id": tid, "title": titles[tid], **states[tid]})
    return {
        "id": attempt["id"],
        "submitted_at": attempt["submitted_at"],
        "scope_topic_id": attempt["scope_topic_id"],
        "score": score,
        "provisional": score["awaiting"] > 0,
        "answers": answers,
        "topics": topics,
    }


def attempt_history(store: SubjectStore, progress: ProgressStore) -> list[dict]:
    """Every attempt, newest first, with its score and the title of its scope."""
    existing = store.question_ids()
    states: dict[int, list[str]] = {}
    for a in progress.answers():
        states.setdefault(a["attempt_id"], []).append(_state(a, a["question_id"] not in existing))
    _, titles = _topics(store.tree(with_sources=False))
    out = []
    for attempt in progress.attempts():
        score = _score(states.get(attempt["id"], []))
        out.append(
            {
                "id": attempt["id"],
                "submitted_at": attempt["submitted_at"],
                "scope_topic_id": attempt["scope_topic_id"],
                "scope_title": None
                if attempt["scope_topic_id"] is None
                else titles.get(attempt["scope_topic_id"], "Removed topic"),
                "score": score,
                "provisional": score["awaiting"] > 0,
            }
        )
    return out


def progress_report(store: SubjectStore, progress: ProgressStore) -> list[dict]:
    """The topic tree with each node's mastery entry; `subtopics` holds the child nodes."""
    tree = store.tree(with_sources=False)
    states = mastery(store, progress, tree)

    def node(n: dict) -> dict:
        return {
            "id": n["id"],
            "title": n["title"],
            **states[n["id"]],
            "subtopics": [node(s) for s in n["subtopics"]],
        }

    return [node(n) for n in tree]


def _awaiting(store: SubjectStore, progress: ProgressStore) -> list[dict]:
    """Open answers awaiting the host's verdict whose question still exists, oldest first."""
    existing = store.question_ids()
    return [
        a
        for a in progress.answers()
        if a["kind"] == "open" and a["correct"] is None and a["question_id"] in existing
    ]


def pending_reviews(store: SubjectStore, progress: ProgressStore) -> dict:
    """The oldest MAX_REVIEW_BATCH answers awaiting review, each with what judging it needs
    (the stem, the reference answer and the cited chunks), and how many more remain."""
    awaiting = _awaiting(store, progress)
    batch = awaiting[:MAX_REVIEW_BATCH]
    questions = {q["id"]: q for q in store.questions(ids=[a["question_id"] for a in batch])}
    return {
        "awaiting": [
            {
                "answer_id": a["id"],
                "attempt_id": a["attempt_id"],
                "question_id": a["question_id"],
                "stem": q["stem"],
                "reference_answer": q["reference_answer"],
                "answer": a["response"],
                "sources": [
                    {k: src[k] for k in ("filename", "page", "heading_path", "text")}
                    for src in q["sources"]
                ],
            }
            for a in batch
            if (q := questions.get(a["question_id"])) is not None
        ],
        "remaining": len(awaiting) - len(batch),
    }


def record_reviews(store: SubjectStore, progress: ProgressStore, reviews: list[dict]) -> list[int]:
    """Record the host's verdicts on open answers, all or none; returns the recorded ids. An
    answer whose question was removed cannot be reviewed, and a verdict cannot be changed."""
    if not reviews:
        raise QuizError("invalid_text", "give at least one verdict")
    rows = []
    for r in reviews:
        answer_id = r.get("answer_id") if isinstance(r, dict) else None
        if not is_id(answer_id):
            raise QuizError("invalid_text", "every verdict names its answer_id")
        if not isinstance(r.get("correct"), bool):
            raise QuizError("invalid_text", f"answer {answer_id}: correct is true or false")
        comment = r.get("comment")
        if not isinstance(comment, str) or len(comment) > MAX_COMMENT_CHARS:
            raise QuizError(
                "invalid_text",
                f"answer {answer_id}: a comment is text of at most {MAX_COMMENT_CHARS} characters",
            )
        rows.append((answer_id, r["correct"], comment))
    ids = [row[0] for row in rows]
    if len(set(ids)) != len(ids):
        raise QuizError("invalid_text", "an answer has two verdicts")
    # An awaiting answer whose question is gone is not pending, so it is unknown to review;
    # the store itself refuses ids that are unknown, not open, or already reviewed.
    pending = {a["id"] for a in _awaiting(store, progress)}
    awaiting = {a["id"] for a in progress.answers() if a["correct"] is None}
    try:
        for answer_id in ids:
            if answer_id in awaiting and answer_id not in pending:
                raise ReviewError("unknown_answer_id", answer_id)
        progress.record_reviews(rows)
    except ReviewError as e:
        raise QuizError(e.reason, f"answer {e.answer_id}") from e
    return ids
