"""Contract tests for the quiz service: mastery, the draw, grading and results. Questions are
written straight to the store (the gate is tested on its own), and nothing here loads a model."""

import json

import pytest

from groundly.agents import quiz
from groundly.agents.quiz import QuizError
from groundly.core.paths import subject_dir
from groundly.core.progress import AnswerWrite, ProgressStore
from groundly.core.store import QuestionWrite, SubjectStore, TopicWrite


def _choice(stem, kind="single"):
    if kind == "single":
        options = [("right", True), ("wrong", False), ("also wrong", False)]
    else:
        options = [("a", True), ("b", True), ("c", False)]
    return QuestionWrite(kind, stem, None, None, options, [1])


def _open(stem):
    return QuestionWrite("open", stem, None, "The reference.", [], [1])


class Bank:
    """A parent with two leaves; each leaf holds four choice questions and one open."""

    def __init__(self, subject):
        root = subject_dir(subject)
        self.store = SubjectStore(root / "store.db")
        self.progress = ProgressStore(root / "progress.db")
        self.store.replace_tree(
            [
                TopicWrite(
                    None,
                    "Deadlocks",
                    "d",
                    [1],
                    [TopicWrite(None, "Conditions", "c", [1]), TopicWrite(None, "Cures", "u", [3])],
                )
            ]
        )
        parent = self.store.tree()[0]
        self.parent = parent["id"]
        self.leaves = [s["id"] for s in parent["subtopics"]]
        self.ids: dict[int, list[int]] = {}
        for leaf in self.leaves:
            self.ids[leaf] = self.store.add_questions(
                leaf,
                [
                    _choice(f"single {leaf} 1"),
                    _choice(f"single {leaf} 2"),
                    _choice(f"multiple {leaf} 3", "multiple"),
                    _choice(f"single {leaf} 4"),
                    _open(f"open {leaf}"),
                ],
            )
        self.questions = {q["id"]: q for q in self.store.questions()}
        self._tokens = 0

    def leaf(self, i=0):
        return self.leaves[i]

    def key(self, qid):
        return [o["id"] for o in self.questions[qid]["options"] if o["correct"]]

    def wrong(self, qid):
        return [next(o["id"] for o in self.questions[qid]["options"] if not o["correct"])]

    def record(self, results, leaf=None):
        """Write one attempt of graded answers directly: [(question_id, correct)]."""
        self._tokens += 1
        self.progress.add_attempt(
            f"seed-{self._tokens:04d}",
            None,
            [
                AnswerWrite(qid, self.questions[qid]["topic_id"], "single", [], c)
                for qid, c in results
            ],
        )

    def submit(self, answers, topic_id=None, token=None):
        self._tokens += 1
        return quiz.submit_attempt(
            self.store, self.progress, token or f"tok-{self._tokens:04d}", topic_id, answers
        )


@pytest.fixture
def bank(retrievable_subject):
    return Bank(retrievable_subject)


# --- mastery ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("graded", "correct_in_last_ten", "state"),
    [
        (0, 0, "not_started"),
        (9, 9, "in_progress"),
        (10, 8, "in_progress"),
        (10, 9, "mastered"),
        (12, 9, "mastered"),
    ],
)
def test_leaf_mastery_at_its_boundaries(bank, graded, correct_in_last_ten, state):
    qid = bank.ids[bank.leaf()][0]
    older = graded - min(graded, 10)
    recent = min(graded, 10)
    results = [(qid, True)] * older
    results += [(qid, True)] * correct_in_last_ten + [(qid, False)] * (recent - correct_in_last_ten)
    if results:
        bank.record(results)
    entry = quiz.mastery(bank.store, bank.progress)[bank.leaf()]
    assert entry["state"] == state
    assert entry["answered"] == graded
    assert entry["recent"] == recent
    assert entry["recent_correct"] == correct_in_last_ten
    assert entry["questions"] == 5
    assert entry["subtopics"] is None and entry["subtopics_mastered"] is None


def test_only_the_last_ten_answers_count(bank):
    qid = bank.ids[bank.leaf()][0]
    bank.record([(qid, False)] * 10)
    bank.record([(qid, True)] * 10)
    assert quiz.mastery(bank.store, bank.progress)[bank.leaf()]["state"] == "mastered"


def test_an_awaiting_answer_does_not_count(bank):
    qid = bank.ids[bank.leaf()][0]
    bank.record([(qid, True)] * 9 + [(qid, None)])
    entry = quiz.mastery(bank.store, bank.progress)[bank.leaf()]
    assert entry["answered"] == 9 and entry["state"] == "in_progress"


def test_a_parent_is_mastered_only_when_every_subtopic_is(bank):
    first, second = bank.leaves
    bank.record([(bank.ids[first][0], True)] * 10)
    m = quiz.mastery(bank.store, bank.progress)
    assert m[bank.parent]["state"] == "in_progress"
    assert m[bank.parent]["subtopics"] == 2 and m[bank.parent]["subtopics_mastered"] == 1
    assert m[bank.parent]["questions"] == 10 and m[bank.parent]["answered"] == 10
    bank.record([(bank.ids[second][0], True)] * 10)
    m = quiz.mastery(bank.store, bank.progress)
    assert m[bank.parent]["state"] == "mastered" and m[bank.parent]["subtopics_mastered"] == 2


def test_a_parent_with_no_answers_is_not_started(bank):
    assert quiz.mastery(bank.store, bank.progress)[bank.parent]["state"] == "not_started"


def test_answers_to_a_removed_question_keep_counting(bank):
    qid = bank.ids[bank.leaf()][0]
    bank.record([(qid, True)] * 10)
    bank.store.remove_questions([qid])
    entry = quiz.mastery(bank.store, bank.progress)[bank.leaf()]
    assert entry["state"] == "mastered" and entry["questions"] == 4


# --- the draw --------------------------------------------------------------------------


def test_a_drawn_test_carries_no_key_and_no_reference_answer(bank):
    drawn = quiz.draw(bank.store, bank.progress, None, 10)
    assert len(drawn) == 10
    text = json.dumps(drawn)
    assert "correct" not in text and "reference_answer" not in text
    assert set(drawn[0]) == {"id", "topic_id", "kind", "stem", "code", "options"}
    assert all(set(o) == {"id", "text"} for q in drawn for o in q["options"])


def test_the_draw_prefers_unanswered_then_last_wrong(bank):
    leaf = bank.leaf()
    q1, q2, q3, q4, q5 = bank.ids[leaf]
    bank.record([(q1, False), (q2, True), (q3, True), (q4, True)])
    bank.record([(q2, False)])
    drawn = [q["id"] for q in quiz.draw(bank.store, bank.progress, leaf, 5)]
    # q5 never answered; q1 and q2 last wrong (q1 less recently); then q3, q4 by recency.
    assert drawn == [q5, q1, q2, q3, q4]


def test_a_parent_scope_is_spread_across_its_subtopics(bank):
    drawn = quiz.draw(bank.store, bank.progress, bank.parent, 5)
    counts = [sum(q["topic_id"] == leaf for q in drawn) for leaf in bank.leaves]
    assert counts == [3, 2]


def test_the_draw_returns_what_the_bank_has_when_it_is_short(bank):
    assert len(quiz.draw(bank.store, bank.progress, None, 20)) == 10
    assert len(quiz.draw(bank.store, bank.progress, bank.leaf(), 10)) == 5


def test_a_length_outside_5_10_20_and_an_unknown_topic_are_refused(bank):
    with pytest.raises(QuizError) as err:
        quiz.draw(bank.store, bank.progress, None, 7)
    assert err.value.reason == "invalid_text"
    with pytest.raises(QuizError) as err:
        quiz.draw(bank.store, bank.progress, 9999, 5)
    assert err.value.reason == "unknown_topic_id"


# --- grading ---------------------------------------------------------------------------


def _multiple(bank):
    return bank.ids[bank.leaf()][2]


def test_a_multiple_answer_is_right_only_when_the_set_matches(bank):
    qid = _multiple(bank)
    key = bank.key(qid)
    every = [o["id"] for o in bank.questions[qid]["options"]]
    for selected, expected in ((key, True), (key[:1], False), (every, False)):
        attempt = bank.submit([{"question_id": qid, "selected": selected}])
        (answer,) = bank.progress.attempt(attempt)["answers"]
        assert answer["correct"] is expected


def test_an_unanswered_question_is_recorded_as_wrong(bank):
    qid = bank.ids[bank.leaf()][0]
    attempt = bank.submit([{"question_id": qid, "selected": []}])
    assert bank.progress.attempt(attempt)["answers"][0]["correct"] is False


def test_a_blank_open_answer_is_wrong_not_awaiting(bank):
    qid = bank.ids[bank.leaf()][4]
    attempt = bank.submit([{"question_id": qid, "text": "   \n"}])
    assert bank.progress.attempt(attempt)["answers"][0]["correct"] is False


def test_an_open_answer_is_saved_awaiting_and_the_result_is_provisional(bank):
    single, open_q = bank.ids[bank.leaf()][0], bank.ids[bank.leaf()][4]
    attempt = bank.submit(
        [
            {"question_id": single, "selected": bank.key(single)},
            {"question_id": open_q, "text": "Because threads wait."},
        ],
        topic_id=bank.leaf(),
    )
    stored = bank.progress.attempt(attempt)
    assert stored["scope_topic_id"] == bank.leaf()
    assert [a["correct"] for a in stored["answers"]] == [True, None]
    assert stored["answers"][1]["response"] == "Because threads wait."
    result = quiz.attempt_result(bank.store, bank.progress, attempt)
    assert result["score"] == {"correct": 1, "graded": 1, "awaiting": 1}
    assert result["provisional"] is True
    assert result["answers"][1]["state"] == "awaiting"


def _bad(bank, case):
    single = bank.ids[bank.leaf()][0]
    other = bank.ids[bank.leaf(1)][0]
    every = [q for ids in bank.ids.values() for q in ids]
    pick = {"question_id": single, "selected": bank.key(single)}
    return (
        {
            "twenty_one": [{"question_id": q, "selected": []} for q in (every * 3)[:21]],
            "repeated_question": [pick, pick],
            "unknown_question": [{"question_id": 9999, "selected": []}],
            "option_of_another_question": [{"question_id": single, "selected": bank.key(other)}],
            "two_selected_on_a_single": [
                {
                    "question_id": single,
                    "selected": [o["id"] for o in bank.questions[single]["options"][:2]],
                }
            ],
            "open_text_4001": [{"question_id": bank.ids[bank.leaf()][4], "text": "x" * 4001}],
        }[case]
        if isinstance(case, str)
        else case
    )


@pytest.mark.parametrize(
    ("answers", "reason"),
    [
        ([], "too_large"),
        ("twenty_one", "too_large"),
        ("repeated_question", "invalid_text"),
        ("unknown_question", "unknown_question"),
        ("option_of_another_question", "invalid_text"),
        ("two_selected_on_a_single", "invalid_text"),
        ("open_text_4001", "invalid_text"),
    ],
)
def test_a_bad_attempt_is_refused_and_nothing_is_written(bank, answers, reason):
    with pytest.raises(QuizError) as err:
        bank.submit(_bad(bank, answers))
    assert err.value.reason == reason
    assert bank.progress.attempts() == [] and bank.progress.answers() == []


def test_the_removed_question_refusal_names_the_cause(bank):
    qid = bank.ids[bank.leaf()][0]
    bank.store.remove_questions([qid])
    with pytest.raises(QuizError) as err:
        bank.submit([{"question_id": qid, "selected": []}])
    assert err.value.reason == "unknown_question" and "removed" in err.value.detail


def test_the_same_token_twice_records_one_attempt(bank):
    qid = bank.ids[bank.leaf()][0]
    answers = [{"question_id": qid, "selected": bank.key(qid)}]
    first = bank.submit(answers, token="same-token")
    assert bank.submit(answers, token="same-token") == first
    assert len(bank.progress.attempts()) == 1


# --- results ---------------------------------------------------------------------------


def test_the_result_shows_keys_sources_and_topic_mastery(bank):
    qid = bank.ids[bank.leaf()][0]
    wrong = bank.wrong(qid)
    attempt = bank.submit([{"question_id": qid, "selected": wrong}], topic_id=bank.leaf())
    result = quiz.attempt_result(bank.store, bank.progress, attempt)
    assert result["id"] == attempt and result["scope_topic_id"] == bank.leaf()
    assert result["submitted_at"]
    assert result["score"] == {"correct": 0, "graded": 1, "awaiting": 0}
    assert result["provisional"] is False
    (answer,) = result["answers"]
    assert answer["state"] == "wrong" and answer["removed"] is False
    assert answer["question_id"] == qid and answer["kind"] == "single"
    assert answer["stem"] == bank.questions[qid]["stem"]
    assert [o["correct"] for o in answer["options"]] == [True, False, False]
    assert [o["selected"] for o in answer["options"]] == [False, True, False]
    assert answer["sources"][0]["filename"] == "lec.pdf" and answer["sources"][0]["page"] == 1
    (topic,) = result["topics"]
    assert topic["id"] == bank.leaf() and topic["title"] == "Conditions"
    assert topic["state"] == "in_progress" and topic["answered"] == 1


def test_an_unknown_attempt_has_no_result(bank):
    assert quiz.attempt_result(bank.store, bank.progress, 404) is None


def test_a_question_removed_after_the_attempt_shows_as_removed(bank):
    qid = bank.ids[bank.leaf()][0]
    attempt = bank.submit([{"question_id": qid, "selected": bank.key(qid)}])
    bank.store.remove_questions([qid])
    result = quiz.attempt_result(bank.store, bank.progress, attempt)
    (answer,) = result["answers"]
    assert answer["removed"] is True and answer["state"] == "correct"
    assert answer["stem"] is None and answer["options"] == [] and answer["sources"] == []
    assert result["score"] == {"correct": 1, "graded": 1, "awaiting": 0}


def test_an_awaiting_answer_whose_question_is_removed_is_unreviewable(bank):
    qid = bank.ids[bank.leaf()][4]
    attempt = bank.submit([{"question_id": qid, "text": "An answer."}])
    bank.store.remove_questions([qid])
    result = quiz.attempt_result(bank.store, bank.progress, attempt)
    assert result["answers"][0]["state"] == "unreviewable"
    assert result["score"] == {"correct": 0, "graded": 0, "awaiting": 0}
    assert result["provisional"] is False


def test_the_history_lists_attempts_newest_first(bank):
    qid = bank.ids[bank.leaf()][0]
    first = bank.submit([{"question_id": qid, "selected": bank.key(qid)}], topic_id=bank.leaf())
    second = bank.submit([{"question_id": qid, "selected": []}])
    history = quiz.attempt_history(bank.store, bank.progress)
    assert [h["id"] for h in history] == [second, first]
    assert history[1]["scope_title"] == "Conditions" and history[0]["scope_title"] is None
    assert history[1]["score"] == {"correct": 1, "graded": 1, "awaiting": 0}
    assert history[0]["provisional"] is False


def test_quiz_loads_no_model(bank, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a model was loaded")

    monkeypatch.setattr("groundly.llm.embeddings.shared_embedder", boom)
    monkeypatch.setattr("groundly.llm.rerank.shared_reranker", boom)
    drawn = quiz.draw(bank.store, bank.progress, None, 5)
    attempt = bank.submit(
        [{"question_id": q["id"], "selected": []} for q in drawn if q["kind"] != "open"]
    )
    assert quiz.attempt_result(bank.store, bank.progress, attempt)


# --- host review of open answers -------------------------------------------------------


def _open_attempt(bank, text="Threads wait on each other.", leaf=0):
    qid = bank.ids[bank.leaf(leaf)][4]
    attempt = bank.submit([{"question_id": qid, "text": text}])
    (pending,) = [
        p
        for p in quiz.pending_reviews(bank.store, bank.progress)["awaiting"]
        if p["attempt_id"] == attempt
    ]
    return attempt, pending["answer_id"], qid


def test_pending_reviews_carry_the_stem_reference_answer_and_sources(bank):
    attempt, answer_id, qid = _open_attempt(bank)
    (p,) = quiz.pending_reviews(bank.store, bank.progress)["awaiting"]
    assert p == {
        "answer_id": answer_id,
        "attempt_id": attempt,
        "question_id": qid,
        "stem": bank.questions[qid]["stem"],
        "reference_answer": "The reference.",
        "answer": "Threads wait on each other.",
        "sources": [
            {
                "filename": "lec.pdf",
                "page": 1,
                "heading_path": "Intro > Deadlocks",
                "text": "deadlock needs mutual exclusion to occur",
            }
        ],
    }


def test_a_verdict_makes_the_score_final_and_counts_toward_mastery(bank):
    attempt, answer_id, _ = _open_attempt(bank)
    assert quiz.attempt_result(bank.store, bank.progress, attempt)["provisional"] is True
    assert quiz.record_reviews(
        bank.store, bank.progress, [{"answer_id": answer_id, "correct": True, "comment": "Yes."}]
    ) == [answer_id]
    result = quiz.attempt_result(bank.store, bank.progress, attempt)
    assert result["provisional"] is False
    assert result["score"] == {"correct": 1, "graded": 1, "awaiting": 0}
    assert result["answers"][0]["state"] == "correct"
    assert quiz.mastery(bank.store, bank.progress)[bank.leaf()]["answered"] == 1
    assert quiz.pending_reviews(bank.store, bank.progress)["awaiting"] == []


def test_the_comment_is_returned_on_the_result_unchanged(bank):
    attempt, answer_id, _ = _open_attempt(bank)
    quiz.record_reviews(
        bank.store,
        bank.progress,
        [{"answer_id": answer_id, "correct": False, "comment": "<b>x</b>"}],
    )
    answer = quiz.attempt_result(bank.store, bank.progress, attempt)["answers"][0]
    assert answer["comment"] == "<b>x</b>" and answer["state"] == "wrong"


def test_a_second_verdict_and_an_unknown_id_are_refused(bank):
    _, answer_id, _ = _open_attempt(bank)
    review = {"answer_id": answer_id, "correct": True, "comment": "ok"}
    quiz.record_reviews(bank.store, bank.progress, [review])
    with pytest.raises(QuizError) as err:
        quiz.record_reviews(bank.store, bank.progress, [review])
    assert err.value.reason == "already_reviewed" and str(answer_id) in err.value.detail
    with pytest.raises(QuizError) as err:
        quiz.record_reviews(bank.store, bank.progress, [{**review, "answer_id": 9999}])
    assert err.value.reason == "unknown_answer_id"


def test_one_bad_verdict_records_none(bank):
    _, first, _ = _open_attempt(bank)
    with pytest.raises(QuizError):
        quiz.record_reviews(
            bank.store,
            bank.progress,
            [
                {"answer_id": first, "correct": True, "comment": "ok"},
                {"answer_id": 9999, "correct": True, "comment": "ok"},
            ],
        )
    assert len(quiz.pending_reviews(bank.store, bank.progress)["awaiting"]) == 1


@pytest.mark.parametrize(
    "review",
    [
        {"answer_id": 1, "correct": "true", "comment": "ok"},
        {"answer_id": 1, "correct": True, "comment": "x" * 1001},
        {"answer_id": 1, "correct": True, "comment": None},
    ],
)
def test_a_malformed_verdict_is_invalid_text(bank, review):
    _open_attempt(bank)
    with pytest.raises(QuizError) as err:
        quiz.record_reviews(bank.store, bank.progress, [review])
    assert err.value.reason == "invalid_text"
    assert len(quiz.pending_reviews(bank.store, bank.progress)["awaiting"]) == 1


def test_an_empty_or_repeated_review_list_is_invalid_text(bank):
    _, answer_id, _ = _open_attempt(bank)
    review = {"answer_id": answer_id, "correct": True, "comment": "ok"}
    for reviews in ([], [review, review]):
        with pytest.raises(QuizError) as err:
            quiz.record_reviews(bank.store, bank.progress, reviews)
        assert err.value.reason == "invalid_text"


def test_an_unreviewable_answer_is_not_listed_and_cannot_be_reviewed(bank):
    _, answer_id, qid = _open_attempt(bank)
    bank.store.remove_questions([qid])
    assert quiz.pending_reviews(bank.store, bank.progress)["awaiting"] == []
    with pytest.raises(QuizError) as err:
        quiz.record_reviews(
            bank.store, bank.progress, [{"answer_id": answer_id, "correct": True, "comment": "ok"}]
        )
    assert err.value.reason == "unknown_answer_id"


# --- final review ----------------------------------------------------------------------


def test_a_wide_scope_reaches_every_subtopic_over_successive_tests(retrievable_subject):
    """More leaves than a test is long: each test starts with the leaves least practised."""
    root = subject_dir(retrievable_subject)
    store = SubjectStore(root / "store.db")
    progress = ProgressStore(root / "progress.db")
    store.replace_tree(
        [
            TopicWrite(
                None, "Big", "b", [1], [TopicWrite(None, f"Leaf {i}", "l", [1]) for i in range(8)]
            )
        ]
    )
    leaves = [s["id"] for s in store.tree()[0]["subtopics"]]
    for leaf in leaves:
        store.add_questions(leaf, [_choice(f"q {leaf} {i}") for i in range(3)])
    seen: set[int] = set()
    for i in range(2):
        drawn = quiz.draw(store, progress, None, 5)
        seen |= {q["topic_id"] for q in drawn}
        quiz.submit_attempt(
            store,
            progress,
            f"tok-wide-{i}",
            None,
            [{"question_id": q["id"], "selected": []} for q in drawn],
        )
    assert seen == set(leaves)


def test_the_history_does_not_load_question_sources(bank, monkeypatch):
    bank.submit([{"question_id": bank.ids[bank.leaf()][0], "selected": []}])

    def unfiltered(*, ids=None, topic_ids=None):
        assert ids is not None or topic_ids is not None, "loaded every question with its sources"
        return original(ids=ids, topic_ids=topic_ids)

    original = bank.store.questions
    monkeypatch.setattr(bank.store, "questions", unfiltered)
    assert len(quiz.attempt_history(bank.store, bank.progress)) == 1


def test_an_attempt_keeps_a_scope_a_re_map_removed(bank):
    qid = bank.ids[bank.leaf()][0]
    attempt = bank.submit([{"question_id": qid, "selected": bank.key(qid)}], topic_id=9999)
    (entry,) = quiz.attempt_history(bank.store, bank.progress)
    assert entry["id"] == attempt and entry["scope_title"] == "Removed topic"


def test_answers_awaiting_review_come_in_batches_oldest_first(bank, monkeypatch):
    monkeypatch.setattr(quiz, "MAX_REVIEW_BATCH", 1)
    first, second = bank.ids[bank.leaf(0)][4], bank.ids[bank.leaf(1)][4]
    bank.submit([{"question_id": first, "text": "a"}])
    bank.submit([{"question_id": second, "text": "b"}])
    pending = quiz.pending_reviews(bank.store, bank.progress)
    assert [p["question_id"] for p in pending["awaiting"]] == [first]
    assert pending["remaining"] == 1


# --- check one answer --------------------------------------------------------------------


def test_check_answer_agrees_with_submit_and_reveals_key_and_sources(bank):
    qid = bank.ids[bank.leaf()][0]
    right = quiz.check_answer(bank.store, qid, bank.key(qid))
    assert right["correct"] is True and right["key"] == bank.key(qid)
    assert right["sources"][0]["chunk_id"] == 1 and right["sources"][0]["text"]
    assert quiz.check_answer(bank.store, qid, bank.wrong(qid))["correct"] is False
    multiple = _multiple(bank)
    assert quiz.check_answer(bank.store, multiple, bank.key(multiple))["correct"] is True
    assert quiz.check_answer(bank.store, multiple, bank.key(multiple)[:1])["correct"] is False


def test_check_answer_writes_nothing_to_progress(bank):
    qid = bank.ids[bank.leaf()][0]
    quiz.check_answer(bank.store, qid, bank.key(qid))
    assert bank.progress.answers() == [] and bank.progress.attempts() == []


def test_check_answer_refuses_open_foreign_options_and_unknown_ids(bank):
    single, *_, open_q = bank.ids[bank.leaf()]
    other = bank.ids[bank.leaf()][1]
    every = [o["id"] for o in bank.questions[single]["options"]]
    for qid, selected, reason in (
        (open_q, [], "invalid_text"),
        (single, bank.key(other), "invalid_text"),
        (single, every[:2], "invalid_text"),
        (999_999, [], "unknown_question"),
    ):
        with pytest.raises(QuizError) as err:
            quiz.check_answer(bank.store, qid, selected)
        assert err.value.reason == reason
