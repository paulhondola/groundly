"""groundly/eval/attribution.py: what an answer says it drew on, in every syntax either
path of the grounding-fidelity experiment produces.

The load-bearing tests here are the ones proving the extractor is not biased toward the
enforced path. `ask` is mandated to emit `[chunk N]`; a host cites filenames, pages and
`groundly://` uris in prose. If this module only found the first, path B would score ~0
on attribution by construction and the experiment would measure its own regex."""

from groundly.eval.attribution import ChunkIndex, extract, resolvable, strip

_CHUNKS = [
    {"chunk_id": 1, "filename": "Curs 3.pdf", "page": 4},
    {"chunk_id": 2, "filename": "Curs 3.pdf", "page": 4},
    {"chunk_id": 3, "filename": "Curs 3.pdf", "page": 9},
    {"chunk_id": 4, "filename": "notes.md", "page": None},
    {"chunk_id": 5, "filename": "Quiz 2", "page": 1},
]


def _index():
    return ChunkIndex(_CHUNKS)


def _kinds(attributions):
    return [(a.kind, sorted(a.resolved)) for a in attributions]


def test_chunk_marker_is_found_and_resolved():
    found = extract("Deadlocks require mutual exclusion [chunk 3].", _index())
    assert _kinds(found) == [("chunk", [3])]


def test_chunk_marker_for_an_id_outside_the_subject_resolves_to_nothing():
    """A hallucinated id is a *present but unresolvable* attribution, not an absent one —
    the three layers count those differently and the distinction is the finding."""
    found = extract("Deadlocks need a cycle [chunk 999].", _index())
    assert len(found) == 1
    assert found[0].kind == "chunk"
    assert found[0].resolved == frozenset()


def test_uri_with_a_space_in_the_filename_survives():
    """`groundly://apd/Curs 3.pdf#page=4` is not a parseable uri — a `\\S+` regex stops at
    the space and loses the extension and the page. This is why filenames are scanned as
    known literals rather than matched by shape."""
    found = extract("See groundly://apd/Curs 3.pdf#page=4 for the proof.", _index())
    assert _kinds(found) == [("uri", [1, 2])]


def test_prose_filename_and_page_resolve_to_that_page_only():
    found = extract("As shown in Curs 3.pdf p. 9, the bound is tight.", _index())
    assert _kinds(found) == [("prose", [3])]


def test_romanian_page_word_is_recognised():
    """9 of apd's 48 gold questions are Romanian, so an RO answer cites in Romanian."""
    found = extract("Vezi Curs 3.pdf pagina 9 pentru demonstratie.", _index())
    assert _kinds(found) == [("prose", [3])]


def test_bare_filename_is_a_whole_file_attribution():
    """Correct rather than lenient: notes.md has no pages at all, so page-level
    attribution is impossible for it and demanding one would score it unresolvable."""
    found = extract("The definition is in notes.md.", _index())
    assert _kinds(found) == [("prose", [4])]


def test_page_that_does_not_exist_is_present_but_unresolvable():
    found = extract("See Curs 3.pdf p. 77.", _index())
    assert len(found) == 1
    assert found[0].resolved == frozenset()


def test_a_bare_number_near_no_filename_is_not_an_attribution():
    """The page regex runs only immediately after a known filename. A free scan would
    turn 'takes 4 steps' into a citation of page 4."""
    assert extract("The algorithm takes 4 steps and page 9 of the standard agrees.", _index()) == []


def test_unknown_filename_is_not_invented():
    assert extract("According to Lecture 12.pdf p. 3, this holds.", _index()) == []


def test_repeated_attribution_counts_once():
    """An answer citing the same page four times has made one attribution. Counting the
    repeats would let a repetitive answer bank its resolvability rate four times."""
    text = "Curs 3.pdf p. 4 says X. Curs 3.pdf p. 4 also says Y. And [chunk 1]. [chunk 1]."
    assert len(extract(text, _index())) == 2


def test_case_insensitive_but_not_fuzzy():
    """A host retypes what the tool handed it and may not preserve case. Anything looser
    would credit the host for citing a file it did not name."""
    assert _kinds(extract("see CURS 3.PDF p. 9", _index())) == [("prose", [3])]
    assert extract("see Curs3.pdf p. 9", _index()) == []


def test_uri_and_a_later_prose_mention_are_classified_separately():
    """The uri test is anchored to the characters immediately before the filename. A
    'is there a uri nearby' search would relabel the prose mention and misreport which
    syntax the host actually reaches for."""
    text = "See groundly://apd/Curs 3.pdf#page=4 and also Curs 3.pdf p. 9."
    assert _kinds(extract(text, _index())) == [("uri", [1, 2]), ("prose", [3])]


def test_resolvable_intersects_rather_than_contains():
    """A (filename, page) attribution resolves to every chunk on that page while the
    author saw only some. Demanding containment would score the host down for the
    granularity of its citation syntax rather than for its accuracy."""
    found = extract("See Curs 3.pdf p. 4.", _index())
    assert resolvable(found, seen={2}) == found
    assert resolvable(found, seen={3}) == []


def test_strip_removes_markers_and_tidies_what_it_orphans():
    """Blinding is partial by design, but the *gap* must not itself be the tell."""
    assert (
        strip(
            "Mutual exclusion is required [chunk 3].",
            _index_extract("Mutual exclusion is required [chunk 3]."),
        )
        == "Mutual exclusion is required."
    )
    text = "As Curs 3.pdf p. 9 shows, the bound is tight."
    assert "Curs 3.pdf" not in strip(text, _index_extract(text))


def _index_extract(text):
    return extract(text, _index())
