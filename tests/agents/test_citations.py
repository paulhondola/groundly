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
