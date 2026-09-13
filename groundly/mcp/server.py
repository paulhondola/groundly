"""The MCP tool surface plus a citation resource template: thin wrappers over the functions
the `groundly` CLI verbs call. Service imports live inside tool bodies, so host spawn is
fast and bge-m3/torch load on first use.
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from pydantic import BaseModel

SERVER_INSTRUCTIONS = """Groundly serves local, indexed course knowledge bases. Each \
`subject` is one university course: its slides, notes and past exams.

When the student asks about a subject listed by `list_subjects`, retrieve before you \
answer. Do not answer from your own knowledge of the topic — a course's definitions, \
notation and emphasis are what the student is graded on, and general knowledge is not a \
substitute for them. Whichever groundly tools you have been given are enough to do this. \
Cite what you use: every chunk carries a `groundly://` uri that resolves to a document \
and page."""
"""The MCP `initialize` instructions: the retrieval norm, stated once for the whole server.
Never rank one tool above another here: a host pointed at a tool it was not given falls
back to answering from memory (decision 31; basis in docs/thesis/experiments.md)."""

mcp = FastMCP("groundly", instructions=SERVER_INSTRUCTIONS)


class CardIn(BaseModel):
    """One flashcard candidate for `submit_cards`."""

    front: str
    back: str
    chunk_ids: list[int]  # chunk_id values from `search` results this card is based on


def _citation_uri(subject: str, filename: str, page: int | None) -> str:
    base = f"groundly://{subject}/{filename}"
    return base if page is None else f"{base}#page={page}"


def _subject_or_error(subject: str, error_cls: type[Exception]):
    """Load `subject`'s Subject handle, or raise `error_cls` naming the subject and
    pointing to `list_subjects` — the one unknown-subject error shape shared by
    every tool and the resource template."""
    from groundly.core.subject import Subject

    try:
        subj = Subject(subject)
    except ValueError as exc:
        raise error_cls(str(exc)) from exc
    if not subj.exists():
        raise error_cls(f"unknown subject {subject!r} — call list_subjects for valid names")
    return subj


@mcp.tool
def list_subjects() -> list[dict]:
    """List every initialized subject with its material/page/chunk counts and whether
    its knowledge graph has been built. Call this first to discover valid subject
    names for search/get_page."""
    from groundly.core.paths import discover_subjects
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    result = []
    for name in discover_subjects():
        subj = Subject(name)
        rows = SubjectStore(subj.store_db_path).list_materials()
        indexed = [r for r in rows if r["status"] == "indexed"]
        result.append(
            {
                "subject": name,
                "materials": len(indexed),
                "pages": sum(r["pages"] or 0 for r in indexed),
                "chunks": sum(r["chunk_count"] for r in rows),
                # Manifest, not directory: a refused or interrupted build leaves
                # partial parquet on disk that must never be reported as a graph.
                "graph_built": subj.graph_is_built(),
            }
        )
    return result


@mcp.tool
def search(subject: str, query: str, k: int | None = None) -> list[dict]:
    """Search this course's own materials — the slides, notes and past exams the student
    is examined on. Use it for ANY question about `subject`, including ones you already
    know the general answer to: a course's definitions, notation, emphasis and worked
    examples are what the student is graded on, and they differ from the textbook
    treatment. Returns ranked verbatim chunks, each with filename, page and a
    `groundly://` uri you can cite or open with `get_page`. Omit `k` to use the configured
    default (`retrieval.context_k`); pass it only to override. No LLM call, no provider
    needed — you compose the answer from what comes back, so cite the chunks you used."""
    from groundly.llm.embeddings import ModelDownloadError
    from groundly.retrieval.vector import search as search_fn

    _subject_or_error(subject, ToolError)
    try:
        hits = search_fn(subject, query, k=k)
    except ModelDownloadError as exc:
        raise ToolError(str(exc)) from exc
    return [
        {
            "chunk_id": h.chunk_id,
            "text": h.text,
            "score": h.score,
            "filename": h.filename,
            "page": h.page,
            "heading_path": h.heading_path,
            "uri": _citation_uri(subject, h.filename, h.page),
        }
        for h in hits
    ]


@mcp.tool
def submit_cards(subject: str, deck: str, cards: list[CardIn]) -> dict:
    """Verify flashcards you generated and store the ones that pass into `deck`
    (created if new). Generate cards from `search` results and set each card's
    `chunk_ids` to the chunk_id values of the chunks it is actually based on — the
    verifier re-retrieves every card and rejects any whose cited chunks don't
    support it. No LLM provider needed. Returns accepted cards (with their stored
    question_id) and, per rejected card, a machine-readable `reason` plus a `detail`
    explaining what to fix — usually: re-search, and cite chunks that genuinely
    support the card. Fix and resubmit only the rejected ones."""
    from groundly.agents.decks import MAX_COUNT, submit_cards as submit_cards_fn
    from groundly.agents.verifier import CardCandidate
    from groundly.llm.embeddings import ModelDownloadError

    _subject_or_error(subject, ToolError)
    if len(cards) > MAX_COUNT:
        raise ToolError(
            f"submit_cards accepts at most {MAX_COUNT} cards per call — split the batch"
        )
    candidates = [CardCandidate(front=c.front, back=c.back, chunk_ids=c.chunk_ids) for c in cards]
    try:
        outcomes = submit_cards_fn(subject, deck, candidates, generation_source="host")
    except (ValueError, ModelDownloadError) as exc:  # ValueError: invalid deck name
        raise ToolError(str(exc)) from exc
    return {
        "deck": deck,
        "accepted": [
            {"index": o.index, "question_id": o.question_id} for o in outcomes if o.accepted
        ],
        "rejected": [
            {"index": o.index, "reason": o.rejection.reason, "detail": o.rejection.detail}
            for o in outcomes
            if not o.accepted
        ],
    }


@mcp.tool
def list_decks(subject: str) -> list[dict]:
    """List `subject`'s flashcard decks with their card counts — deck names are what
    `submit_cards` writes into and `export_deck` reads from."""
    from groundly.core.store import SubjectStore

    subj = _subject_or_error(subject, ToolError)
    rows = SubjectStore(subj.store_db_path).list_decks()
    return [{"deck": r["name"], "cards": r["card_count"]} for r in rows]


@mcp.tool
def export_deck(subject: str, deck: str) -> dict:
    """Export a verified flashcard deck as an Anki .apkg file (citations on the card
    backs) and return its absolute path for the student to import into Anki. The file
    is written under the subject's exports/ directory; use `list_decks` to see which
    decks exist."""
    from groundly.core.anki import export_deck as export_deck_fn

    _subject_or_error(subject, ToolError)
    try:
        path = export_deck_fn(subject, deck)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"path": str(path)}


@mcp.tool
def get_page(subject: str, filename: str, page: int) -> list[dict]:
    """Verbatim chunk text for one page of one material, in chunk order — the precise
    way to open what a search citation points to. Never returns raw file bytes or
    a summary; empty list if the page/filename has no indexed chunks."""
    from groundly.core.store import SubjectStore

    subj = _subject_or_error(subject, ToolError)
    rows = SubjectStore(subj.store_db_path).page_chunks(filename, page)
    return [
        {"chunk_id": r["chunk_id"], "text": r["text"], "heading_path": r["heading_path"]}
        for r in rows
    ]


@mcp.resource("groundly://{subject}/{filename}")
def document(subject: str, filename: str) -> dict[str, list[dict]]:
    """A material's verbatim chunks grouped by page — never raw file bytes, never
    summaries. Empirically, FastMCP does not split the `#page=N` citation fragment out
    as a separate handler argument: it arrives concatenated onto `filename` (e.g.
    "lec.pdf#page=2"), so we parse it back out here and narrow to just that page when
    present; `get_page` is the precise tool either way."""
    from groundly.core.store import SubjectStore

    page: int | None = None
    if "#page=" in filename:
        filename, _, frag = filename.partition("#page=")
        page = int(frag) if frag.isdigit() else None

    subj = _subject_or_error(subject, ResourceError)
    store = SubjectStore(subj.store_db_path)

    if page is not None:
        pages = {page: store.page_chunks(filename, page)}
    else:
        conn = store.connect()
        try:
            rows = conn.execute(
                """
                SELECT c.id AS chunk_id, c.page, c.heading_path, c.text
                FROM chunks c JOIN materials m ON m.id = c.material_id
                WHERE m.filename = ?
                ORDER BY c.page, c.id
                """,
                (filename,),
            ).fetchall()
        finally:
            conn.close()
        pages = {}
        for row in rows:
            pages.setdefault(row["page"], []).append(row)

    return {
        str(p): [
            {"chunk_id": r["chunk_id"], "text": r["text"], "heading_path": r["heading_path"]}
            for r in rows
        ]
        for p, rows in pages.items()
    }
