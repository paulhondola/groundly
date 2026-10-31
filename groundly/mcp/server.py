"""The MCP tool surface plus a citation resource template: thin wrappers over the functions
the `groundly` CLI verbs call. Service imports live inside tool bodies, so host spawn is
fast and bge-m3/torch load on first use.
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError

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
back to answering from memory (decision 31)."""

mcp = FastMCP("groundly", instructions=SERVER_INSTRUCTIONS)


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
    """List every initialized subject with its material/page/chunk counts. Call this
    first to discover valid subject names for search/get_page."""
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
    default (`retrieval.top_k`); pass it only to override. No LLM call, no provider
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


def _parse_citation_uri(uri: str) -> tuple[str, str, int | None]:
    """Inverse of `_citation_uri`: (subject, filename, page or None). Anything `search`
    could not have produced is a ToolError; the filename only ever reaches SQL."""
    base, has_frag, frag = uri.partition("#")
    subject, _, filename = base.removeprefix("groundly://").partition("/")
    page = frag.removeprefix("page=")
    well_formed = base.startswith("groundly://") and subject and filename
    if not well_formed or (
        has_frag and not (frag.startswith("page=") and page.isascii() and page.isdigit())
    ):
        raise ToolError(
            f"not a groundly citation uri: {uri!r} — pass the `uri` field of a search result"
        )
    return subject, filename, int(page) if has_frag else None


@mcp.tool
def get_page(uri: str) -> list[dict]:
    """Open what a search citation points to: pass a result's `uri` verbatim (e.g.
    `groundly://<subject>/<file>#page=N`) to read every chunk on that page, in order —
    use it when a hit needs its surrounding context before you answer. A uri without
    `#page` (plain-text materials) returns the whole material. To read a neighbouring
    page, change the `N` in `#page=N`; an empty list means you are past the end.
    Verbatim chunk text only, never file bytes or summaries."""
    from groundly.core.store import SubjectStore

    subject, filename, page = _parse_citation_uri(uri)
    store = SubjectStore(_subject_or_error(subject, ToolError).store_db_path)
    rows = store.material_chunks(filename) if page is None else store.page_chunks(filename, page)
    return [
        {
            "chunk_id": r["chunk_id"],
            "page": r["page"],
            "text": r["text"],
            "heading_path": r["heading_path"],
        }
        for r in rows
    ]


@mcp.resource("groundly://{subject}/{filename}")
def document(subject: str, filename: str) -> dict[str, list[dict]]:
    """A material's verbatim chunks grouped by page — never raw file bytes, never
    summaries. FastMCP drops a citation's `#page=N` before matching (a fragment is not
    part of a resource's identity), so this is always the whole document; `get_page`
    is the tool that opens one page."""
    from groundly.core.store import SubjectStore

    store = SubjectStore(_subject_or_error(subject, ResourceError).store_db_path)
    pages: dict[str, list[dict]] = {}
    for r in store.material_chunks(filename):
        pages.setdefault(str(r["page"]), []).append(
            {"chunk_id": r["chunk_id"], "text": r["text"], "heading_path": r["heading_path"]}
        )
    return pages
