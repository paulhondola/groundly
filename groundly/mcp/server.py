"""The MCP tool surface (P4 v1): `list_subjects`, `search`, `ask`, `get_page`, plus a
citation resource template — thin wrappers over the same functions `groundly` CLI
verbs call (docs/superpowers/specs/2026-07-18-mcp-skeleton-design.md). No heavy
imports at module top: service imports live inside tool/resource bodies so host
spawn -> handshake is fast and bge-m3/torch load lazily on first `search`/`ask`
(.claude/rules/architecture.md).
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError

mcp = FastMCP("groundly")


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
    names for search/ask/get_page."""
    from groundly.core.paths import discover_subjects
    from groundly.core.store import SQLiteSubjectStore
    from groundly.core.subject import Subject

    result = []
    for name in discover_subjects():
        subj = Subject(name)
        rows = SQLiteSubjectStore(subj.store_db_path).list_materials()
        indexed = [r for r in rows if r["status"] == "indexed"]
        result.append(
            {
                "subject": name,
                "materials": len(indexed),
                "pages": sum(r["pages"] or 0 for r in indexed),
                "chunks": sum(r["chunk_count"] for r in rows),
                "graph_built": (subj.root_dir / "graph").exists(),
            }
        )
    return result


@mcp.tool
def search(subject: str, query: str, k: int = 8) -> list[dict]:
    """Raw ranked retrieval: the top-k chunks for `query` from `subject`'s materials
    (hybrid dense + sparse + BM25, reranked). No LLM call, no provider needed — you
    compose the answer yourself from the returned chunks; grounding is not enforced
    here (use `ask` when you need an enforced, cited answer)."""
    from groundly.retrieval.vector import search as search_fn

    _subject_or_error(subject, ToolError)
    nodes = search_fn(subject, query, k=k)
    results = []
    for n in nodes:
        m = n.node.metadata
        results.append(
            {
                "chunk_id": m["chunk_id"],
                "text": n.node.get_content(),
                "score": float(n.score),
                "filename": m["filename"],
                "page": m["page"],
                "heading_path": m["heading_path"],
                "uri": _citation_uri(subject, m["filename"], m["page"]),
            }
        )
    return results


@mcp.tool
def ask(subject: str, query: str) -> dict:
    """Enforced grounded answer: retrieves relevant chunks from `subject`'s materials,
    generates an answer that must cite them, and refuses ("not covered by the course
    materials") rather than fall back to model knowledge when nothing supports an
    answer. Needs a configured chat provider — `search` does not."""
    from groundly.agents.ask import NoCitationsError
    from groundly.agents.ask import ask as ask_fn
    from groundly.llm.config import ProviderNotConfiguredError

    _subject_or_error(subject, ToolError)
    try:
        result = ask_fn(subject, query)
    except ProviderNotConfiguredError as exc:
        raise ToolError(
            f"ask needs a configured chat provider; search works without one — {exc}"
        ) from exc
    except NoCitationsError as exc:
        raise ToolError(str(exc)) from exc

    return {
        "answer": result.answer,
        "citations": [
            {
                "chunk_id": c.chunk_id,
                "filename": c.filename,
                "page": c.page,
                "heading_path": c.heading_path,
                "uri": _citation_uri(subject, c.filename, c.page),
            }
            for c in result.citations
        ],
    }


@mcp.tool
def get_page(subject: str, filename: str, page: int) -> list[dict]:
    """Verbatim chunk text for one page of one material, in chunk order — the precise
    way to open what a search/ask citation points to. Never returns raw file bytes or
    a summary; empty list if the page/filename has no indexed chunks."""
    from groundly.core.store import SQLiteSubjectStore

    subj = _subject_or_error(subject, ToolError)
    rows = SQLiteSubjectStore(subj.store_db_path).page_chunks(filename, page)
    return [
        {"chunk_id": r["chunk_id"], "text": r["text"], "heading_path": r["heading_path"]}
        for r in rows
    ]


@mcp.resource("groundly://{subject}/{filename}")
def document(subject: str, filename: str) -> dict[str, list[dict]]:
    """A material's verbatim chunks grouped by page — never raw file bytes, never
    summaries. Empirically (see docs/superpowers/specs/2026-07-18-mcp-skeleton-design.md),
    FastMCP does not split the `#page=N` citation fragment out as a separate handler
    argument: it arrives concatenated onto `filename` (e.g. "lec.pdf#page=2"), so we
    parse it back out here and narrow to just that page when present; `get_page` is
    the precise tool either way and is what the gate demo uses."""
    from groundly.core.store import SQLiteSubjectStore

    page: int | None = None
    if "#page=" in filename:
        filename, _, frag = filename.partition("#page=")
        page = int(frag) if frag.isdigit() else None

    subj = _subject_or_error(subject, ResourceError)
    store = SQLiteSubjectStore(subj.store_db_path)

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
