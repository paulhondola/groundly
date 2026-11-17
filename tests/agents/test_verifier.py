"""Contract tests for the question gate: `submit_questions` stores a question only when it
is well formed and re-retrieving its own text finds a chunk it cites. Each item is judged on
its own, and every rejection carries a reason from REJECTION_REASONS."""

import pytest

from groundly.agents import verifier as verifier_mod
from groundly.agents.verifier import REJECTION_REASONS, submit_questions
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import subject_dir
from groundly.core.store import SubjectStore, TopicWrite


class AlignedEmbedder:
    """Returns one fixed (dense, sparse) pair for every text — aim it at a specific
    chunk of the retrievable_subject fixture (chunk 1: dense [1,0,...], sparse {1:...};
    chunk 2: dense [0,1,...], sparse {3:...})."""

    def __init__(self, dense: list[float], sparse: dict[int, float]):
        self.dense = dense
        self.sparse = sparse

    def encode(self, texts):
        return [self.dense for _ in texts], [self.sparse for _ in texts]


def _chunk1_embedder():
    return AlignedEmbedder([1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2), {1: 0.9})


def _chunk2_embedder():
    return AlignedEmbedder([0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2), {3: 0.9})


def _store(subject_name: str) -> SubjectStore:
    return SubjectStore(subject_dir(subject_name) / "store.db")


@pytest.fixture
def gate(retrievable_subject):
    """The store with a parent, its one subtopic (the leaf questions go on) and a lone topic."""
    store = _store(retrievable_subject)
    store.replace_tree(
        [
            TopicWrite(None, "Deadlocks", "d", [1], [TopicWrite(None, "Conditions", "c", [1])]),
            TopicWrite(None, "Sync", "s", [2]),
        ]
    )
    tree = store.tree()
    return store, tree[0]["subtopics"][0]["id"], tree[0]["id"]


SINGLE = {
    "kind": "single",
    "stem": "What does a deadlock need?",
    "options": [{"text": "mutual exclusion", "correct": True}, {"text": "a GPU", "correct": False}],
    "chunk_ids": [1],
}
MULTIPLE = {
    "kind": "multiple",
    "stem": "Which are deadlock conditions?",
    "options": [
        {"text": "mutual exclusion", "correct": True},
        {"text": "circular wait", "correct": True},
        {"text": "a GPU", "correct": False},
    ],
    "chunk_ids": [1, 3],
}
OPEN = {
    "kind": "open",
    "stem": "Why does a deadlock occur?",
    "reference_answer": "Threads hold resources while waiting for each other.",
    "chunk_ids": [1],
}
CODE_READING = {**SINGLE, "stem": "What is wrong here?", "code": "lock(a); lock(b);"}


def _submit(store, leaf, questions, embedder=None):
    return submit_questions(store, leaf, questions, embedder=embedder or _chunk1_embedder())


def test_rejection_reasons_are_the_canonical_eleven():
    assert REJECTION_REASONS == (
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


def test_a_good_question_of_each_kind_is_stored(gate):
    store, leaf, _ = gate
    result = _submit(store, leaf, [SINGLE, MULTIPLE, OPEN, CODE_READING])
    assert result["rejected"] == []
    assert [a["index"] for a in result["accepted"]] == [0, 1, 2, 3]
    stored = store.questions(ids=[a["id"] for a in result["accepted"]])
    assert [q["kind"] for q in stored] == ["single", "multiple", "open", "single"]
    assert stored[3]["code"] == "lock(a); lock(b);"
    assert stored[2]["reference_answer"].startswith("Threads hold")
    assert [o["correct"] for o in stored[1]["options"]] == [True, True, False]


def test_text_is_stored_stripped(gate):
    store, leaf, _ = gate
    result = _submit(store, leaf, [{**SINGLE, "stem": "  What does a deadlock need?  "}])
    assert store.questions(ids=[result["accepted"][0]["id"]])[0]["stem"] == (
        "What does a deadlock need?"
    )


def _opt(*pairs):
    return [{"text": t, "correct": c} for t, c in pairs]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"kind": "code"}, "code_questions_unsupported"),
        ({"kind": "essay"}, "unknown_kind"),
        ({"stem": " "}, "invalid_text"),
        ({"stem": 7}, "invalid_text"),
        ({"stem": "x" * 501}, "invalid_text"),
        ({"code": "x" * 2001}, "invalid_text"),
        ({"options": _opt(("a" * 301, True), ("b", False))}, "invalid_text"),
        ({"options": "a, b"}, "invalid_text"),
        ({"options": _opt(("a", True))}, "wrong_answer_key"),
        ({"options": _opt(*[(str(i), i == 0) for i in range(7)])}, "wrong_answer_key"),
        ({"options": _opt(("a", True), ("b", True))}, "wrong_answer_key"),
        ({"options": _opt(("a", False), ("b", False))}, "wrong_answer_key"),
        (
            {"options": [{"text": "a", "correct": "yes"}, {"text": "b", "correct": False}]},
            "invalid_text",
        ),
        ({"options": _opt(("A ", True), ("a", False))}, "duplicate_option"),
        ({"chunk_ids": []}, "not_answerable_from_chunks"),
        ({"chunk_ids": [1, 2, 3, 4, 5, 6]}, "invalid_text"),
        ({"chunk_ids": ["1"]}, "invalid_text"),
        ({"chunk_ids": [999]}, "not_answerable_from_chunks"),
    ],
)
def test_structural_faults_are_rejected_and_nothing_is_stored(gate, change, reason):
    store, leaf, _ = gate
    result = _submit(store, leaf, [{**SINGLE, **change}])
    assert result["accepted"] == []
    assert [(r["index"], r["reason"]) for r in result["rejected"]] == [(0, reason)]
    assert result["rejected"][0]["detail"]
    assert store.question_counts() == {}


def test_a_question_that_is_not_an_object_is_invalid_text(gate):
    store, leaf, _ = gate
    assert _submit(store, leaf, ["a question"])["rejected"][0]["reason"] == "invalid_text"


@pytest.mark.parametrize(
    "options",
    [
        _opt(("a", True), ("b", False), ("c", False)),  # one key
        _opt(("a", True), ("b", True), ("c", True)),  # no wrong option
        _opt(("a", True), ("b", True)),  # too few
    ],
)
def test_multiple_needs_two_keys_and_one_wrong_option(gate, options):
    store, leaf, _ = gate
    result = _submit(store, leaf, [{**MULTIPLE, "options": options}])
    assert result["rejected"][0]["reason"] == "wrong_answer_key"


@pytest.mark.parametrize(
    "change",
    [{"reference_answer": " "}, {"reference_answer": "x" * 1001}, {"options": SINGLE["options"]}],
)
def test_open_needs_a_reference_answer_and_no_options(gate, change):
    store, leaf, _ = gate
    assert _submit(store, leaf, [{**OPEN, **change}])["rejected"][0]["reason"] == "invalid_text"


def test_unresolvable_chunk_ids_are_named_in_the_detail(gate):
    store, leaf, _ = gate
    detail = _submit(store, leaf, [{**SINGLE, "chunk_ids": [1, 999]}])["rejected"][0]["detail"]
    assert "999" in detail
    assert "[1," not in detail and "[1]" not in detail  # the resolvable id is not blamed


def test_a_question_that_does_not_retrieve_its_citation_is_rejected(gate, monkeypatch):
    # Shrink the pool to one slot and aim the query at chunk 2 while citing chunk 1.
    monkeypatch.setattr(verifier_mod, "VERIFY_TOP_K", 1)
    store, leaf, _ = gate
    result = _submit(store, leaf, [SINGLE], embedder=_chunk2_embedder())
    assert result["rejected"][0]["reason"] == "not_answerable_from_chunks"
    assert "re-retriev" in result["rejected"][0]["detail"]
    assert store.question_counts() == {}


class _Recording(AlignedEmbedder):
    def __init__(self):
        super().__init__([1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2), {1: 0.9})
        self.texts = []

    def encode(self, texts):
        self.texts.extend(texts)
        return super().encode(texts)


def test_re_retrieval_uses_the_stem_code_and_correct_answers_only(gate):
    store, leaf, _ = gate
    embedder = _Recording()
    _submit(store, leaf, [CODE_READING, OPEN], embedder=embedder)
    assert embedder.texts == [
        "What is wrong here?\nlock(a); lock(b);\nmutual exclusion",
        "Why does a deadlock occur?\nThreads hold resources while waiting for each other.",
    ]


def test_a_mixed_batch_stores_the_good_and_reports_the_bad_by_index(gate):
    store, leaf, _ = gate
    result = _submit(store, leaf, [SINGLE, {**MULTIPLE, "kind": "essay"}, OPEN])
    assert [a["index"] for a in result["accepted"]] == [0, 2]
    assert [(r["index"], r["reason"]) for r in result["rejected"]] == [(1, "unknown_kind")]
    assert store.question_counts() == {leaf: 2}


def test_a_stem_already_stored_or_repeated_in_the_batch_is_a_duplicate(gate):
    store, leaf, _ = gate
    again = {**SINGLE, "stem": "  what does a DEADLOCK need? "}
    first = _submit(store, leaf, [SINGLE, again])
    assert [(r["index"], r["reason"]) for r in first["rejected"]] == [(1, "duplicate_question")]
    second = _submit(store, leaf, [again])
    assert second["rejected"][0]["reason"] == "duplicate_question"
    assert store.question_counts() == {leaf: 1}


@pytest.mark.parametrize(
    ("topic", "reason"),
    [("parent", "not_a_leaf_topic"), (9999, "unknown_topic_id"), ("7", "unknown_topic_id")],
)
def test_a_parent_topic_and_an_unknown_topic_refuse_the_whole_call(gate, topic, reason):
    store, _, parent = gate
    result = _submit(store, parent if topic == "parent" else topic, [SINGLE])
    assert result == {
        "accepted": [],
        "rejected": [{"index": None, "reason": reason, "detail": result["rejected"][0]["detail"]}],
    }
    assert store.question_counts() == {}


@pytest.mark.parametrize("questions", [[], "a question", None])
def test_questions_must_be_a_non_empty_list(gate, questions):
    store, leaf, _ = gate
    result = _submit(store, leaf, questions)
    assert [(r["index"], r["reason"]) for r in result["rejected"]] == [(None, "invalid_text")]


def test_more_than_fifty_questions_refuses_the_whole_call(gate):
    store, leaf, _ = gate
    batch = [{**SINGLE, "stem": f"Question {i}?"} for i in range(51)]
    result = _submit(store, leaf, batch)
    assert [(r["index"], r["reason"]) for r in result["rejected"]] == [(None, "too_large")]
    assert store.question_counts() == {}


def test_items_past_the_topic_limit_are_bank_full(gate, monkeypatch):
    monkeypatch.setattr(verifier_mod, "MAX_QUESTIONS_PER_TOPIC", 1)
    store, leaf, _ = gate
    result = _submit(store, leaf, [SINGLE, MULTIPLE])
    assert [a["index"] for a in result["accepted"]] == [0]
    assert [(r["index"], r["reason"]) for r in result["rejected"]] == [(1, "bank_full")]


class _NoModel:
    def encode(self, texts):
        raise AssertionError("a structural rejection must not load the embedder")


def test_structural_rejections_never_touch_the_embedder(gate):
    store, leaf, _ = gate
    result = _submit(
        store,
        leaf,
        [{**SINGLE, "kind": "essay"}, {**SINGLE, "chunk_ids": [999]}],
        embedder=_NoModel(),
    )
    assert len(result["rejected"]) == 2


def test_a_code_block_keeps_its_indentation(gate):
    store, leaf, _ = gate
    code = "    if x:\n        y()\n"
    result = _submit(store, leaf, [{**CODE_READING, "code": code}])
    (stored,) = store.questions(ids=[result["accepted"][0]["id"]])
    assert stored["code"] == "    if x:\n        y()"


def test_code_is_capped_at_the_length_it_is_stored_with():
    """Indentation is kept, so it counts: import refuses stored code over the limit."""
    with pytest.raises(verifier_mod._Reject) as err:
        verifier_mod._structure({**CODE_READING, "code": "\t" * 30 + "x" * 1990})
    assert err.value.reason == "invalid_text" and "2020 characters" in err.value.detail
