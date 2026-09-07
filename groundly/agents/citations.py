"""Citation resolution shared by every agent call site that turns a model's cited
response into grounded citations (ask.py, study_modes.py): regex-extract cited chunk
ids, drop hallucinated ones (not among the retrieved set), resolve survivors to
file/page/heading via the store. Zero resolvable citations is an error, never a
degraded answer (.claude/rules/grounding-and-privacy.md)."""

import logging
import re
from dataclasses import dataclass

from groundly.core.store import SubjectStore

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[chunk (\d+)\]")


@dataclass
class Citation:
    chunk_id: int
    filename: str
    page: int | None
    heading_path: str | None


class NoCitationsError(Exception):
    """Every cited chunk id in the model's response was hallucinated (not among the
    retrieved set) — zero resolvable citations is an error, never a degraded answer."""


def cited_ids(text: str) -> set[int]:
    """Every chunk id the model claimed, resolvable or not.

    Shared with `agents/probe.py`, which has no store to resolve against and only needs
    to know whether the model emitted the marker it was given."""
    return {int(m) for m in _CITATION_RE.findall(text)}


def resolve_citations(
    text: str, retrieved_chunk_ids: list[int], store: SubjectStore
) -> list[Citation]:
    claimed_ids = cited_ids(text)
    resolvable_ids = [
        cid for cid in retrieved_chunk_ids if cid in claimed_ids
    ]  # hallucinated ids dropped
    hallucinated_ids = claimed_ids - set(retrieved_chunk_ids)
    if hallucinated_ids:
        logger.info(
            "dropped %d hallucinated citation id(s): %s",
            len(hallucinated_ids),
            sorted(hallucinated_ids),
        )
    if not resolvable_ids:
        raise NoCitationsError(
            "the model's response cited no chunk ids that resolve to retrieved chunks. "
            "Run `groundly config check` to test whether your chat model satisfies the "
            "citation mandate."
        )

    details = {row["chunk_id"]: row for row in store.chunk_details(resolvable_ids)}
    return [
        Citation(
            chunk_id=cid,
            filename=details[cid]["filename"],
            page=details[cid]["page"],
            heading_path=details[cid]["heading_path"],
        )
        for cid in resolvable_ids
        if cid in details
    ]
