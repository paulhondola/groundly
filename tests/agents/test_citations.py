"""groundly/agents/citations.py: regex-extract cited chunk ids, drop hallucinated
ones, resolve survivors via the store (UC-02's citation-resolution logic, factored
out of ask.py in P5 so study_modes.py can reuse it unmodified)."""

import pytest

from groundly.agents.citations import NoCitationsError, resolve_citations


class _FakeStore:
    def __init__(self, rows):
        self._rows = {row["chunk_id"]: row for row in rows}

    def chunk_details(self, chunk_ids):
        return [self._rows[cid] for cid in chunk_ids if cid in self._rows]


def _row(chunk_id, filename="lec.pdf", page=1, heading_path=None):
    return {"chunk_id": chunk_id, "filename": filename, "page": page, "heading_path": heading_path}


def test_resolve_citations_returns_cited_chunks_in_retrieved_order():
    store = _FakeStore([_row(1), _row(2, filename="notes.pdf", page=3)])
    citations = resolve_citations(
        "Deadlocks need mutual exclusion [chunk 2] and circular wait [chunk 1].",
        retrieved_chunk_ids=[1, 2],
        store=store,
    )
    assert [c.chunk_id for c in citations] == [1, 2]  # retrieved order, not citation order
    assert citations[1].filename == "notes.pdf"
    assert citations[1].page == 3


def test_resolve_citations_drops_hallucinated_ids_not_among_retrieved():
    store = _FakeStore([_row(1)])
    citations = resolve_citations(
        "See [chunk 1] and also [chunk 999].", retrieved_chunk_ids=[1], store=store
    )
    assert [c.chunk_id for c in citations] == [1]


def test_resolve_citations_all_hallucinated_raises_no_citations_error():
    store = _FakeStore([_row(1)])
    with pytest.raises(NoCitationsError):
        resolve_citations("See [chunk 999].", retrieved_chunk_ids=[1], store=store)


def test_resolve_citations_no_markers_raises_no_citations_error():
    store = _FakeStore([_row(1)])
    with pytest.raises(NoCitationsError):
        resolve_citations("No citations here.", retrieved_chunk_ids=[1], store=store)


def test_cited_ids_extracts_every_marker():
    """Shared with the compliance probe (agents/probe.py), which has no store to resolve
    against and only needs to know which ids the model claimed."""
    from groundly.agents.citations import cited_ids

    assert cited_ids("a [chunk 1] b [chunk 42] c [chunk 1].") == {1, 42}


def test_cited_ids_of_an_uncited_answer_is_empty():
    from groundly.agents.citations import cited_ids

    assert cited_ids("Deadlocks require mutual exclusion.") == set()


def test_no_citations_error_points_at_the_compliance_probe():
    """The message reaches the student verbatim through both surfaces (cli/ask.py and
    mcp/server.py render `str(exc)`), and a refusal that does not name the probe reads
    as a broken product rather than an unsuitable model."""
    store = _FakeStore([_row(1)])

    with pytest.raises(NoCitationsError) as exc:
        resolve_citations("Deadlocks require mutual exclusion.", [1], store)

    assert "groundly config check" in str(exc.value)
