"""The one shape retrieval returns: a chunk plus the citation fields every consumer
reads. `mcp/server.py`'s `search` payload, `cli/search.py` and `agents/verifier.py` all
read these names, so they are defined once here rather than by convention at each
constructor."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Hit:
    """`score` is the retriever's own: a cross-encoder score or a fused RRF weight. The two
    scales differ, so compare scores only between hits from one retrieval mode: the topic
    gate does, against its cutoff, and only with reranking on."""

    chunk_id: int
    text: str
    filename: str
    page: int | None
    heading_path: str | None
    score: float
