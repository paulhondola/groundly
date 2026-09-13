"""The one shape retrieval returns: a chunk plus the citation fields every consumer
reads. `mcp/server.py`'s `search` payload, `cli/search.py` and `agents/verifier.py` all
read these names, so they are defined once here rather than by convention at each
constructor."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    text: str
    filename: str
    page: int | None
    heading_path: str | None
    score: float


def hit_from_row(row, score: float) -> Hit:
    """One `chunk_details`/`all_chunks` row plus the retriever's own score — a
    cross-encoder score or a fused RRF weight; nothing downstream compares them."""
    return Hit(
        chunk_id=row["chunk_id"],
        text=row["text"],
        filename=row["filename"],
        page=row["page"],
        heading_path=row["heading_path"],
        score=float(score),
    )
