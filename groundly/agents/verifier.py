"""The question gate: the single check every host-written question passes before it lands
in store.db. Nothing unchecked enters the question bank.

It shows a question is well formed and that re-retrieving its own text finds a chunk it
cites. It does not show the answer key is right: that needs a model Groundly does not have,
so nothing here or downstream may call a key verified.

Each question is judged on its own, cheapest check first; structural faults never load the
embedder."""

from groundly.core.store import (
    MAX_CODE_CHARS,
    MAX_OPTION_CHARS,
    MAX_QUESTION_CITATIONS,
    MAX_QUESTIONS_PER_TOPIC,
    MAX_REFERENCE_CHARS,
    MAX_STEM_CHARS,
    QUESTION_SHAPES,
    QuestionConflictError,
    QuestionWrite,
    SubjectStore,
    text_key,
)
from groundly.retrieval.vector import VectorRetriever

REJECTION_REASONS = (
    "unknown_topic_id",
    "not_a_leaf_topic",
    "too_large",
    "bank_full",
    "unknown_kind",
    "code_questions_unsupported",
    "invalid_text",
    "wrong_answer_key",
    "duplicate_option",
    "duplicate_question",
    "not_answerable_from_chunks",
)

MAX_QUESTIONS_PER_CALL = 50
VERIFY_TOP_K = 20  # re-retrieval pool checked for membership of a cited chunk id


_BANK_DETAIL = {
    "duplicate_question": "this topic already has a question with the same stem",
    "bank_full": f"this topic already holds {MAX_QUESTIONS_PER_TOPIC} questions, the limit",
}


class _Reject(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason, self.detail = reason, detail


def _text(value, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Reject("invalid_text", f"{name} must be non-empty text")
    value = value.strip()
    if len(value) > limit:
        raise _Reject("invalid_text", f"{name} is {len(value)} characters; the limit is {limit}")
    return value


def _options(kind: str, raw) -> list[tuple[str, bool]]:
    if not isinstance(raw, list):
        raise _Reject("invalid_text", 'options must be a list of {"text", "correct"} objects')
    options: list[tuple[str, bool]] = []
    for i, option in enumerate(raw):
        if not isinstance(option, dict) or type(option.get("correct")) is not bool:
            raise _Reject(
                "invalid_text", f'option {i + 1} must be {{"text": ..., "correct": true or false}}'
            )
        options.append(
            (_text(option.get("text"), f"option {i + 1}", MAX_OPTION_CHARS), option["correct"])
        )
    fewest, most, fewest_correct = QUESTION_SHAPES[kind]
    if not fewest <= len(options) <= most:
        raise _Reject(
            "wrong_answer_key",
            f"a {kind} question needs {fewest} to {most} options; this one has {len(options)}",
        )
    correct = sum(1 for _, is_correct in options if is_correct)
    if kind == "single" and correct != 1:
        raise _Reject(
            "wrong_answer_key",
            f"a single question needs exactly one correct option; this one has {correct}",
        )
    if kind == "multiple" and (correct < fewest_correct or correct == len(options)):
        raise _Reject(
            "wrong_answer_key",
            "a multiple question needs at least two correct options and at least one wrong "
            f"one; this one has {correct} correct of {len(options)}",
        )
    if len({text_key(text) for text, _ in options}) != len(options):
        raise _Reject("duplicate_option", "two options have the same text")
    return options


def _structure(question) -> QuestionWrite:
    """Every check that needs no store and no model."""
    if not isinstance(question, dict):
        raise _Reject("invalid_text", "a question must be an object")
    kind = question.get("kind")
    if kind == "code":
        raise _Reject(
            "code_questions_unsupported",
            "questions answered by writing code are not supported — ask about a code block "
            'with a single or multiple question and a "code" field instead',
        )
    if kind not in ("single", "multiple", "open"):
        raise _Reject("unknown_kind", f"kind {kind!r} is not one of single, multiple, open")

    stem = _text(question.get("stem"), "stem", MAX_STEM_CHARS)
    code = question.get("code")
    if code is not None:
        # Leading spaces are the code's own indentation: keep them, trim only blank lines.
        _text(code, "code", MAX_CODE_CHARS)
        code = code.strip("\r\n")
        if len(code) > MAX_CODE_CHARS:  # the stored value is what import checks
            raise _Reject(
                "invalid_text", f"code is {len(code)} characters; the limit is {MAX_CODE_CHARS}"
            )

    if kind == "open":
        if question.get("options"):
            raise _Reject("invalid_text", "an open question has no options")
        if code is not None:
            raise _Reject("invalid_text", "only a single or multiple question may carry code")
        reference = _text(question.get("reference_answer"), "reference_answer", MAX_REFERENCE_CHARS)
        options: list[tuple[str, bool]] = []
    else:
        reference = None
        options = _options(kind, question.get("options"))

    chunk_ids = question.get("chunk_ids")
    if not isinstance(chunk_ids, list) or any(type(c) is not int for c in chunk_ids):
        raise _Reject("invalid_text", "chunk_ids must be a list of chunk id numbers")
    if not chunk_ids:
        raise _Reject(
            "not_answerable_from_chunks",
            "no chunk_ids given — cite the chunk_id of every supporting chunk returned by search",
        )
    chunk_ids = list(dict.fromkeys(chunk_ids))
    if len(chunk_ids) > MAX_QUESTION_CITATIONS:
        raise _Reject(
            "invalid_text",
            f"{len(chunk_ids)} chunk_ids cited; the limit is {MAX_QUESTION_CITATIONS} — cite "
            "only the chunks the question is drawn from",
        )
    return QuestionWrite(kind, stem, code, reference, options, chunk_ids)


def _retrieval_text(q: QuestionWrite) -> str:
    """What the question claims the course says: the stem, its code and the right answer."""
    if q.kind == "open":
        assert q.reference_answer is not None  # the structural check requires it first
        answer = [q.reference_answer]
    else:
        answer = [t for t, correct in q.options if correct]
    return "\n".join([q.stem, *([q.code] if q.code else []), *answer])


def _whole_call(reason: str, detail: str) -> dict:
    return {"accepted": [], "rejected": [{"index": None, "reason": reason, "detail": detail}]}


def submit_questions(
    store: SubjectStore, topic_id: int, questions: list[dict], *, embedder=None
) -> dict:
    """Store each question that passes and report each that does not, by its position in
    `questions`: `{"accepted": [{"index", "id"}], "rejected": [{"index", "reason", "detail"}]}`.
    A fault in the call itself (the topic, the batch size) has index None and stores nothing."""
    if type(topic_id) is not int or topic_id not in store.topic_ids():
        return _whole_call(
            "unknown_topic_id", f"topic {topic_id!r} is not in this subject's tree — see outline"
        )
    if topic_id not in store.leaf_topic_ids():
        return _whole_call(
            "not_a_leaf_topic",
            f"topic {topic_id} has subtopics — put each question on the subtopic it belongs to",
        )
    if not isinstance(questions, list) or not questions:
        return _whole_call("invalid_text", "questions must be a non-empty list")
    if len(questions) > MAX_QUESTIONS_PER_CALL:
        return _whole_call(
            "too_large",
            f"{len(questions)} questions in one call; the limit is {MAX_QUESTIONS_PER_CALL}",
        )

    stems = {text_key(s) for s in store.question_stems(topic_id)}
    stored = len(stems)
    retriever = VectorRetriever(store, embedder=embedder, rerank=False, top_k=VERIFY_TOP_K)
    passed: list[tuple[int, QuestionWrite]] = []
    rejected: list[dict] = []

    for index, raw in enumerate(questions):
        try:
            question = _structure(raw)
            if text_key(question.stem) in stems:
                raise _Reject("duplicate_question", _BANK_DETAIL["duplicate_question"])
            if stored + len(passed) >= MAX_QUESTIONS_PER_TOPIC:
                raise _Reject("bank_full", _BANK_DETAIL["bank_full"])
            resolved = {row["chunk_id"] for row in store.chunk_details(question.chunk_ids)}
            missing = [cid for cid in question.chunk_ids if cid not in resolved]
            if missing:
                raise _Reject(
                    "not_answerable_from_chunks",
                    f"chunk_ids {missing} do not resolve to any chunk in this subject — "
                    "cite only chunk_id values returned by search",
                )
            retrieved = {h.chunk_id for h in retriever.retrieve(_retrieval_text(question))}
            if not (set(question.chunk_ids) & retrieved):
                raise _Reject(
                    "not_answerable_from_chunks",
                    f"re-retrieving the question's own text did not surface any cited chunk in "
                    f"the top {VERIFY_TOP_K} results — it does not look answerable from its "
                    "own citations",
                )
        except _Reject as fault:
            rejected.append({"index": index, "reason": fault.reason, "detail": fault.detail})
            continue
        stems.add(text_key(question.stem))
        passed.append((index, question))

    # The checks above ran before the slow re-retrieval; the write repeats the bank's under
    # its lock, and a question another session has since made a duplicate or pushed past the
    # limit is rejected here, the rest written. Each retry drops one, so this ends.
    while True:
        try:
            ids = store.add_questions(topic_id, [q for _, q in passed]) if passed else []
            break
        except QuestionConflictError as conflict:
            index, _ = passed.pop(conflict.index)
            reason = conflict.reason
            rejected.append({"index": index, "reason": reason, "detail": _BANK_DETAIL[reason]})
    return {
        "accepted": [{"index": index, "id": qid} for (index, _), qid in zip(passed, ids)],
        "rejected": sorted(rejected, key=lambda r: r["index"]),
    }
