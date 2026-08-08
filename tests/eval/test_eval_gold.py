"""groundly/eval/gold.py: gold-set parsing, validation and label resolution. The
load-bearing test here is the source-file guard — a gold row that labels its own exam
file scores retrieving the question instead of the answer, which would silently inflate
every number the thesis reports."""

import json

import pytest

from groundly.eval.gold import Expected, GoldSetError, load, resolve

_ROW = {
    "id": "q1",
    "query": "what is a race condition?",
    "lang": "en",
    "class": "factoid",
    "expected": [{"file": "lec.pdf", "page": 7}],
    "source_file": "Examen.md",
}


def _write(tmp_path, *rows, raw=None):
    path = tmp_path / "gold.jsonl"
    path.write_text(raw if raw is not None else "\n".join(json.dumps(r) for r in rows))
    return path


class StubStore:
    """Only `all_chunks()` is used — the resolver builds both indexes from one pass."""

    def __init__(self, rows):
        self.rows = rows

    def all_chunks(self):
        return self.rows


def _chunk(chunk_id, filename, page):
    return {"chunk_id": chunk_id, "filename": filename, "page": page}


def test_load_parses_a_valid_row(tmp_path):
    questions = load(_write(tmp_path, _ROW))
    assert len(questions) == 1
    q = questions[0]
    assert q.id == "q1"
    assert q.klass == "factoid"
    assert q.expected == (Expected(file="lec.pdf", page=7),)
    assert q.source_file == "Examen.md"


def test_load_skips_comments_and_blank_lines(tmp_path):
    raw = f"# a comment\n\n{json.dumps(_ROW)}\n\n# trailing comment\n"
    assert len(load(_write(tmp_path, raw=raw))) == 1


def test_expected_may_omit_page_for_pageless_files(tmp_path):
    row = _ROW | {"expected": [{"file": "notes.md"}]}
    assert load(_write(tmp_path, row))[0].expected == (Expected(file="notes.md", page=None),)


def test_expected_pointing_at_its_own_source_file_is_rejected(tmp_path):
    """The contamination guard. A question lifted from Examen.md that labels Examen.md
    scores retrieving the question text, not the material that answers it."""
    row = _ROW | {"expected": [{"file": "Examen.md", "page": None}]}
    with pytest.raises(GoldSetError, match="a question source for this gold set"):
        load(_write(tmp_path, row))


def test_expected_pointing_at_another_rows_source_file_is_rejected(tmp_path):
    """The guard is corpus-wide, not per row. apd-006's question is verbatim in both
    Examen.md and Quiz 2, so a row sourced from one could otherwise legally label the
    other — still scoring retrieval of a question rather than of the answer."""
    from_examen = _ROW
    from_quiz = _ROW | {
        "id": "q2",
        "source_file": "Quiz 2.pdf",
        "expected": [{"file": "Examen.md"}],  # a DIFFERENT row's source
    }
    with pytest.raises(GoldSetError, match="q2: expected points at 'Examen.md'"):
        load(_write(tmp_path, from_examen, from_quiz))


def test_source_file_none_permits_any_expected(tmp_path):
    row = _ROW | {"source_file": None, "expected": [{"file": "Examen.md"}]}
    assert load(_write(tmp_path, row))[0].source_file is None


@pytest.mark.parametrize(
    "override, match",
    [
        ({"lang": "de"}, "lang 'de'"),
        ({"class": "trivia"}, "class 'trivia'"),
        ({"expected": []}, "expected is empty"),
        ({"expected": [{"page": 3}]}, "has no 'file'"),
    ],
)
def test_invalid_field_named_in_the_error(tmp_path, override, match):
    with pytest.raises(GoldSetError, match=match):
        load(_write(tmp_path, _ROW | override))


def test_missing_required_fields_are_all_named(tmp_path):
    with pytest.raises(GoldSetError, match="lang, class"):
        load(_write(tmp_path, {"id": "q1", "query": "?", "expected": [{"file": "a"}]}))


def test_duplicate_ids_rejected(tmp_path):
    with pytest.raises(GoldSetError, match="duplicate question id 'q1'"):
        load(_write(tmp_path, _ROW, _ROW))


def test_malformed_json_names_the_line(tmp_path):
    with pytest.raises(GoldSetError, match="line 2: not valid JSON"):
        load(_write(tmp_path, raw=json.dumps(_ROW) + "\n{not json}\n"))


def test_empty_file_rejected(tmp_path):
    with pytest.raises(GoldSetError, match="contains no questions"):
        load(_write(tmp_path, raw="# only a comment\n"))


def test_missing_file_named(tmp_path):
    with pytest.raises(GoldSetError, match="no gold set at"):
        load(tmp_path / "absent.jsonl")


def test_resolve_maps_labels_to_chunk_ids(tmp_path):
    questions = load(_write(tmp_path, _ROW))
    store = StubStore(
        [
            _chunk(1, "lec.pdf", 7),
            _chunk(2, "lec.pdf", 7),
            _chunk(3, "lec.pdf", 8),
            _chunk(9, "Examen.md", None),
        ]
    )
    expected, source, warnings, _base = resolve(questions, store)
    assert expected["q1"] == {1, 2}  # page 8 is a different label
    assert source["q1"] == {9}  # the gold set's question sources, for leakage
    assert warnings == []


def test_resolve_matches_whole_file_when_page_is_null(tmp_path):
    row = _ROW | {"expected": [{"file": "notes.md"}]}
    store = StubStore([_chunk(1, "notes.md", None), _chunk(2, "notes.md", None)])
    expected, _source, _warnings, _base = resolve(load(_write(tmp_path, row)), store)
    assert expected["q1"] == {1, 2}


def test_resolve_warns_but_does_not_crash_on_a_stale_label(tmp_path):
    """A partly-stale gold set should still score its good rows, with the bad ones named."""
    store = StubStore([_chunk(1, "other.pdf", 1), _chunk(9, "Examen.md", None)])
    expected, _source, warnings, _base = resolve(load(_write(tmp_path, _ROW)), store)
    assert expected["q1"] == set()
    assert warnings == ["q1: expected lec.pdf p.7 matches no chunk in this subject"]


def test_resolve_warns_when_a_question_source_is_not_in_the_index(tmp_path):
    """Measuring leakage against a file that is not indexed reports 0.0 and reads as a
    clean result — the one way a contamination metric can lie reassuringly."""
    store = StubStore([_chunk(1, "lec.pdf", 7)])  # no Examen.md
    _expected, source, warnings, _base = resolve(load(_write(tmp_path, _ROW)), store)
    assert source["q1"] == set()
    assert "source_file Examen.md matches no chunk in this subject" in warnings


def test_resolve_measures_leakage_against_every_question_source(tmp_path):
    """The source set is corpus-wide and identical for every question, including rows
    with `source_file: null` — retrieving an exam file is retrieving question text
    whoever's question it was."""
    rows = [
        _ROW,  # source_file: Examen.md
        _ROW | {"id": "q2", "source_file": "Quiz 2.pdf"},
        _ROW | {"id": "q3", "source_file": None},  # hand-written
    ]
    store = StubStore(
        [
            _chunk(1, "lec.pdf", 7),
            _chunk(9, "Examen.md", None),
            _chunk(10, "Quiz 2.pdf", 3),
        ]
    )
    _expected, source, _warnings, _base = resolve(load(_write(tmp_path, *rows)), store)
    assert source["q1"] == source["q2"] == source["q3"] == {9, 10}
