"""The shared context contract every arm returns.

"Four arms, one interface" (docs/architecture/retrieval.md) is only fair if the arms
agree on the *shape* of what they return, not merely on the base class. Citation
resolution (agents/citations.py), prompt assembly (agents/prompts.py) and the eval's
scoring all read these four metadata keys by name, and none of them may special-case
which arm produced a node.

That contract used to be two identical `TextNode(...)` literals — one in vector.py, one
in graph.py — so it was a convention rather than a thing. It is one function now.
"""

from llama_index.core.schema import NodeWithScore, TextNode

# What every consumer downstream reads off `node.metadata`. Named so a test can assert
# the contract instead of trusting two constructors to stay in step.
METADATA_KEYS = ("chunk_id", "filename", "page", "heading_path")


def node_from_row(row, score: float) -> NodeWithScore:
    """One `chunk_details`/`all_chunks` row -> the node shape every arm returns.

    `row` is an `sqlite3.Row` carrying chunk_id/text/filename/page/heading_path. `score`
    is the arm's own: a cross-encoder score, a fused RRF weight, or a reciprocal rank —
    they are not comparable across arms and nothing downstream compares them.
    """
    node = TextNode(
        text=row["text"],
        id_=str(row["chunk_id"]),
        metadata={
            "chunk_id": row["chunk_id"],
            "filename": row["filename"],
            "page": row["page"],
            "heading_path": row["heading_path"],
        },
    )
    return NodeWithScore(node=node, score=float(score))


def chunk_ids(nodes: list[NodeWithScore]) -> list[int]:
    """The retrieved chunk ids, in the arm's own order. Written out at a dozen call
    sites before this existed; the list comprehension is not the point, the fact that
    every caller reaches for the same metadata key is."""
    return [n.node.metadata["chunk_id"] for n in nodes]
