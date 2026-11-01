"""The topic gate: the host sends titles, summaries and structure, Groundly picks each
topic's sources by reranked search and stores nothing unless every node passes.

The gate shows a topic is in the course, not that its summary is true: a summary is never
a citation target and is never called verified. Sources are always chosen here, never by
the host."""

from groundly.core.store import SubjectStore, TopicWrite
from groundly.retrieval.vector import VectorRetriever

TOPIC_RELEVANCE_CUTOFF = 1.0  # minimum reranker score for a hit to count as a source
MAX_SOURCES = 5
MAX_SUMMARY_CHARS = 500
GATE_RERANK_POOL = 20  # fused candidates the gate's reranker scores per node


def outline(store: SubjectStore) -> dict:
    """What the host reads before writing a tree: each indexed material with its page
    headings, and the current tree with ids. No chunk ids and no chunk text."""
    headings: dict[str, list[str]] = {}
    for row in store.material_headings():
        label = (
            row["heading_path"] if row["page"] is None else f"p{row['page']}: {row['heading_path']}"
        )
        headings.setdefault(row["filename"], []).append(label)
    cited = store.cited_chunk_counts()
    return {
        "materials": [
            {
                "filename": m["filename"],
                "pages": m["pages"],
                "cited_chunks": cited.get(m["filename"], 0),
                "headings": headings.get(m["filename"], []),
            }
            for m in store.list_materials()
            if m["status"] == "indexed"
        ],
        "tree": _tree_view(store.tree()),
    }


def _tree_view(nodes: list[dict]) -> list[dict]:
    return [
        {
            "id": n["id"],
            "title": n["title"],
            "summary": n["summary"],
            "source_count": len(n["sources"]),
            "subtopics": _tree_view(n["subtopics"]),
        }
        for n in nodes
    ]


def _rejection(path, reason, detail, score=None, nearest=None) -> dict:
    return {"path": path, "reason": reason, "detail": detail, "score": score, "nearest": nearest}


def _structural_rejections(tree, known_ids: set[int]) -> list[dict]:
    """Every free check over the whole tree, so the slow search only runs on a tree that
    can be stored."""
    if not isinstance(tree, list) or not tree:
        return [_rejection([], "empty_tree", "the tree needs at least one big topic")]

    rejections: list[dict] = []
    seen_ids: set[int] = set()

    def check(node, path: list[str], level: int) -> None:
        if not isinstance(node, dict):
            rejections.append(_rejection(path, "invalid_text", "a topic must be an object"))
            return
        title, summary = node.get("title"), node.get("summary")
        if not isinstance(title, str) or not title.strip():
            rejections.append(_rejection(path, "invalid_text", "title must be non-empty text"))
        if not isinstance(summary, str) or not summary.strip():
            rejections.append(_rejection(path, "invalid_text", "summary must be non-empty text"))
        elif len(summary) > MAX_SUMMARY_CHARS:
            rejections.append(
                _rejection(
                    path,
                    "invalid_text",
                    f"summary is {len(summary)} characters; the limit is {MAX_SUMMARY_CHARS}",
                )
            )

        topic_id = node.get("id")
        if topic_id is not None:
            if type(topic_id) is not int or topic_id not in known_ids:
                rejections.append(
                    _rejection(
                        path,
                        "unknown_topic_id",
                        f"id {topic_id!r} is not in the current tree — omit id for a new topic",
                    )
                )
            elif topic_id in seen_ids:
                rejections.append(
                    _rejection(path, "duplicate_topic_id", f"id {topic_id} is used more than once")
                )
            else:
                seen_ids.add(topic_id)

        subtopics = node.get("subtopics") or []
        if not isinstance(subtopics, list):
            rejections.append(_rejection(path, "invalid_text", "subtopics must be a list"))
        elif level == 1 and subtopics:
            rejections.append(
                _rejection(path, "too_deep", "a subtopic cannot have subtopics of its own")
            )
        else:
            for i, sub in enumerate(subtopics):
                check(sub, [*path, _label(sub, i)], level + 1)

    for i, node in enumerate(tree):
        check(node, [_label(node, i)], 0)
    return rejections


def _label(node, index: int) -> str:
    title = node.get("title") if isinstance(node, dict) else None
    return title.strip() if isinstance(title, str) and title.strip() else f"#{index + 1}"


def submit_tree(store: SubjectStore, tree: list[dict], *, embedder=None, reranker=None) -> dict:
    """Store `tree` if every node passes, else store nothing and report every failing node.

    Structural checks run first and skip the search. Then each node is retrieved on its
    title and summary with the reranker always on (whatever `retrieval.rerank` says), and
    its sources are the hits scoring at least TOPIC_RELEVANCE_CUTOFF."""
    rejections = _structural_rejections(tree, store.topic_ids())
    if rejections:
        return {"accepted": False, "rejections": rejections}

    retriever = VectorRetriever(
        store,
        embedder=embedder,
        reranker=reranker,
        rerank=True,
        rerank_pool=GATE_RERANK_POOL,
        top_k=MAX_SOURCES,
    )

    def convert(nodes: list[dict], path: list[str]) -> list[TopicWrite]:
        out = []
        for node in nodes:
            title, summary = node["title"].strip(), node["summary"].strip()
            node_path = [*path, title]
            hits = retriever.retrieve(title + "\n" + summary)
            if not hits or hits[0].score < TOPIC_RELEVANCE_CUTOFF:
                top = hits[0] if hits else None
                rejections.append(
                    _rejection(
                        node_path,
                        "below_relevance_cutoff",
                        f"no material in this course matches closely enough (needs a score of "
                        f"{TOPIC_RELEVANCE_CUTOFF}) — reword using the course's own wording",
                        score=top.score if top else None,
                        nearest={
                            "filename": top.filename,
                            "page": top.page,
                            "heading_path": top.heading_path,
                        }
                        if top
                        else None,
                    )
                )
            # a failing big topic doesn't hide its subtopics' failures
            subtopics = convert(node.get("subtopics") or [], node_path)
            out.append(
                TopicWrite(
                    node.get("id"),
                    title,
                    summary,
                    [h.chunk_id for h in hits if h.score >= TOPIC_RELEVANCE_CUTOFF],
                    subtopics,
                )
            )
        return out

    writes = convert(tree, [])
    if rejections:
        return {"accepted": False, "rejections": rejections}
    store.replace_tree(writes)
    return {"accepted": True, "tree": _tree_view(store.tree())}
