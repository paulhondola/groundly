"""Tests on the local page: drawing a test from the question bank, grading a submitted
attempt, and the mastery each topic has reached.

Answer keys and reference answers never leave this module in a drawn test; grading reads the
keys from store.db at submit time. Loads no model: everything here is SQL and set arithmetic.
"""

from groundly.core.progress import AnswerWrite, ProgressStore
from groundly.core.store import SubjectStore

MASTERY_WINDOW = 10
MASTERY_CORRECT = 9
TEST_LENGTHS = (5, 10, 20)
MAX_TEST_QUESTIONS = max(TEST_LENGTHS)
MAX_OPEN_ANSWER_CHARS = 4000

_REMOVED = "a question in this test was removed — start a new test"


class QuizError(Exception):
    """A draw or an attempt that cannot be served; raised before anything is written."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


def _topics(store: SubjectStore) -> tuple[list[dict], dict[int, list[int]], dict[int, str]]:
    """(top-level nodes, parent id -> subtopic ids, id -> title), in tree order."""
    tree = store.tree()
    children = {node["id"]: [s["id"] for s in node["subtopics"]] for node in tree}
    titles = {node["id"]: node["title"] for node in tree}
    titles.update({s["id"]: s["title"] for node in tree for s in node["subtopics"]})
    return tree, children, titles


def _leaves_in_order(tree: list[dict]) -> list[int]:
    return [s["id"] for node in tree for s in node["subtopics"]] + [
        node["id"] for node in tree if not node["subtopics"]
    ]


def mastery(store: SubjectStore, progress: ProgressStore) -> dict[int, dict]:
    """Every topic's state. A leaf is mastered at MASTERY_CORRECT or more correct of its last
    MASTERY_WINDOW graded answers, with at least that many; a parent when all its subtopics are.
    Answers awaiting review do not count; answers to removed questions do."""
    tree, children, _ = _topics(store)
    counts = store.question_counts()
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
            "questions": counts.get(leaf, 0),
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


def _scope_leaves(store: SubjectStore, topic_id: int | None) -> list[int]:
    tree, children, titles = _topics(store)
    if topic_id is None:
        return _leaves_in_order(tree)
    if topic_id not in titles:
        raise QuizError("unknown_topic_id", f"no topic has id {topic_id} in this subject")
    return children.get(topic_id) or [topic_id]


def draw(store: SubjectStore, progress: ProgressStore, topic_id: int | None, n: int) -> list[dict]:
    """Up to `n` questions from the scope's leaves, taken round-robin in tree order. Within a
    topic: never answered, then last answered wrong, then least recently answered. The result
    carries no answer key and no reference answer."""
    if n not in TEST_LENGTHS:
        raise QuizError("invalid_text", f"a test is {', '.join(map(str, TEST_LENGTHS))} questions")
    leaves = _scope_leaves(store, topic_id)
    last: dict[int, tuple[int, bool | None]] = {}
    for index, a in enumerate(progress.answers()):
        last[a["question_id"]] = (index, a["correct"])

    def priority(q: dict) -> tuple[int, int, int]:
        if q["id"] not in last:
            return (0, 0, q["id"])
        index, correct = last[q["id"]]
        return (1 if correct is False else 2, index, q["id"])

    queues: dict[int, list[dict]] = {leaf: [] for leaf in leaves}
    for q in store.questions(topic_ids=leaves):
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


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
    if not isinstance(selected, list) or not all(map(_is_int, selected)) or "text" in answer:
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


def submit_attempt(
    store: SubjectStore,
    progress: ProgressStore,
    token: str,
    topic_id: int | None,
    answers: list[dict],
) -> int:
    """Grade the choice answers against store.db and save the attempt in one transaction.
    Every answer is checked before anything is written; the same token twice is one attempt."""
    if not answers or len(answers) > MAX_TEST_QUESTIONS:
        raise QuizError("too_large", f"a test has 1 to {MAX_TEST_QUESTIONS} answers")
    ids = [a.get("question_id") if isinstance(a, dict) else None for a in answers]
    if not all(map(_is_int, ids)):
        raise QuizError("invalid_text", "every answer names its question id")
    if len(set(ids)) != len(ids):
        raise QuizError("invalid_text", "a question is answered twice")
    if topic_id is not None:
        _scope_leaves(store, topic_id)
    questions = {q["id"]: q for q in store.questions(ids=ids)}
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
    answers = []
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
    _, _, titles = _topics(store)
    states = mastery(store, progress)
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
    existing = {q["id"] for q in store.questions()}
    states: dict[int, list[str]] = {}
    for a in progress.answers():
        states.setdefault(a["attempt_id"], []).append(_state(a, a["question_id"] not in existing))
    _, _, titles = _topics(store)
    out = []
    for attempt in progress.attempts():
        score = _score(states.get(attempt["id"], []))
        out.append(
            {
                "id": attempt["id"],
                "submitted_at": attempt["submitted_at"],
                "scope_topic_id": attempt["scope_topic_id"],
                "scope_title": titles.get(attempt["scope_topic_id"]),
                "score": score,
                "provisional": score["awaiting"] > 0,
            }
        )
    return out
