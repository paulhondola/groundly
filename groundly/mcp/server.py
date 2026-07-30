"""The MCP tool surface: `list_subjects`, `search`, `ask`, `drill_down`, `overview`,
`get_page`, plus a citation resource template — thin wrappers over the same functions
`groundly` CLI verbs call (docs/superpowers/specs/2026-07-18-mcp-skeleton-design.md).
No heavy imports at module top: service imports live inside tool/resource bodies so
host spawn -> handshake is fast and bge-m3/torch load lazily on first `search`/`ask`
(.claude/rules/architecture.md).
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from pydantic import BaseModel

mcp = FastMCP("groundly")


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
    names for search/ask/get_page."""
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
                # partial parquet on disk that must never be reported as a graph
                # (same gate as retrieval/graph.py's _require_graph).
                "graph_built": (subj.root_dir / "graph").exists()
                and subj.load_manifest().graphrag.corpus_hash is not None,
            }
        )
    return result


@mcp.tool
def search(subject: str, query: str, k: int | None = None) -> list[dict]:
    """Raw ranked retrieval: the top-k chunks for `query` from `subject`'s materials
    (hybrid dense + sparse + BM25, reranked). Omit `k` to use the configured default
    (`retrieval.context_k`); pass it only to override. No LLM call, no provider needed —
    you compose the answer yourself from the returned chunks; grounding is not enforced
    here (use `ask` when you need an enforced, cited answer)."""
    from groundly.llm.embeddings import ModelDownloadError
    from groundly.retrieval.vector import search as search_fn

    _subject_or_error(subject, ToolError)
    try:
        nodes = search_fn(subject, query, k=k)
    except ModelDownloadError as exc:
        raise ToolError(str(exc)) from exc
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
    from groundly.llm.chat import ChatUnreachableError
    from groundly.llm.config import ProviderNotConfiguredError
    from groundly.llm.embeddings import ModelDownloadError

    _subject_or_error(subject, ToolError)
    try:
        result = ask_fn(subject, query)
    except ProviderNotConfiguredError as exc:
        raise ToolError(
            f"ask needs a configured chat provider; search works without one — {exc}"
        ) from exc
    except NoCitationsError as exc:
        raise ToolError(str(exc)) from exc
    except (ModelDownloadError, ChatUnreachableError) as exc:
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
def drill_down(subject: str, entity: str) -> dict:
    """Entity-anchored deep dive: multi-hop graph search anchored on one specific
    `entity`, producing a cited answer drawn from `subject`'s knowledge graph rather
    than plain vector retrieval. Use this instead of `ask`/`search` when the question
    is about how one entity connects to others (multi-hop), not a single fact.
    Requires the subject's graph to be built — check `graph_built` via `list_subjects`
    first, and run `groundly index --graph` if it's false. Needs a configured chat
    provider, same as `ask`."""
    from groundly.agents.citations import NoCitationsError
    from groundly.agents.study_modes import drill_down as drill_down_fn
    from groundly.llm.chat import ChatUnreachableError
    from groundly.llm.config import ProviderNotConfiguredError
    from groundly.llm.embeddings import ModelDownloadError
    from groundly.retrieval.graph import GraphNotBuiltError

    _subject_or_error(subject, ToolError)
    try:
        result = drill_down_fn(subject, entity)
    except GraphNotBuiltError as exc:
        raise ToolError(str(exc)) from exc
    except ProviderNotConfiguredError as exc:
        raise ToolError(
            f"drill_down needs a configured chat provider; search works without one — {exc}"
        ) from exc
    except NoCitationsError as exc:
        raise ToolError(str(exc)) from exc
    except (ModelDownloadError, ChatUnreachableError) as exc:
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
def overview(subject: str, topic: str) -> dict:
    """Course-wide synthesis: community-summary global search over `subject`'s
    knowledge graph, producing a cited answer about `topic` plus the graph communities
    consulted to build it. Use this instead of `ask`/`drill_down` when the question is
    broad or thematic (e.g. "what does this course cover about X") rather than anchored
    on one entity. Requires the subject's graph to be built — check `graph_built` via
    `list_subjects` first, and run `groundly index --graph` if it's false. Needs a
    configured chat provider, same as `ask`."""
    from groundly.agents.citations import NoCitationsError
    from groundly.agents.study_modes import overview as overview_fn
    from groundly.llm.chat import ChatUnreachableError
    from groundly.llm.config import ProviderNotConfiguredError
    from groundly.llm.embeddings import ModelDownloadError
    from groundly.retrieval.graph import GraphNotBuiltError

    _subject_or_error(subject, ToolError)
    try:
        result = overview_fn(subject, topic)
    except GraphNotBuiltError as exc:
        raise ToolError(str(exc)) from exc
    except ProviderNotConfiguredError as exc:
        raise ToolError(
            f"overview needs a configured chat provider; search works without one — {exc}"
        ) from exc
    except NoCitationsError as exc:
        raise ToolError(str(exc)) from exc
    except (ModelDownloadError, ChatUnreachableError) as exc:
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
        "communities": result.communities,
    }


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
    `submit_cards`/`generate_deck` write into and `export_deck` reads from."""
    from groundly.core.store import SubjectStore

    subj = _subject_or_error(subject, ToolError)
    rows = SubjectStore(subj.store_db_path).list_decks()
    return [{"deck": r["name"], "cards": r["card_count"]} for r in rows]


@mcp.tool
def generate_deck(
    subject: str, topic: str, deck: str, count: int = 20, confirm: bool = False
) -> dict:
    """Generate a verified flashcard deck about `topic` from `subject`'s materials,
    server-side (needs a configured [providers.generation]; use `submit_cards` to
    build decks yourself without one). Two-phase: with confirm=false (the default)
    nothing runs — you get a token/cost estimate to relay to the student. Call again
    with confirm=true to start the background job, then poll `get_job` with the
    returned job_id for the batch report. Cards are machine-verified before storage;
    unverifiable ones are regenerated up to twice, then dropped (reported in the
    batch report, never stored)."""
    from groundly.agents.decks import MAX_COUNT, estimate_generation, generate_deck_job
    from groundly.agents.jobs import start_job
    from groundly.llm.config import ProviderNotConfiguredError, require_provider

    _subject_or_error(subject, ToolError)
    count = max(1, min(count, MAX_COUNT))
    if not confirm:
        return estimate_generation(count)
    try:
        require_provider("generation")  # fail at submit time, not buried in the job
    except ProviderNotConfiguredError as exc:
        raise ToolError(
            f"generate_deck needs a configured generation provider; submit_cards "
            f"works without one — {exc}"
        ) from exc
    job = start_job(subject, lambda: generate_deck_job(subject, topic, deck, count))
    return {"job_id": job.id, "status": job.status}


@mcp.tool
def get_job(job_id: str) -> dict:
    """Status of a generate_deck job: 'queued'/'running' (poll again), 'done' (the
    `report` field holds the batch report: accepted count, dropped cards with
    machine-readable reasons, tokens, cost), or 'failed' (`error` names the cause)."""
    from groundly.agents.jobs import get_job as get_job_fn

    job = get_job_fn(job_id)
    if job is None:
        raise ToolError(
            "unknown or expired job id — jobs do not survive a server restart; cards "
            "already verified are stored, check list_decks"
        )
    return {"job_id": job.id, "status": job.status, "report": job.report, "error": job.error}


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
    way to open what a search/ask citation points to. Never returns raw file bytes or
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
    summaries. Empirically (see docs/superpowers/specs/2026-07-18-mcp-skeleton-design.md),
    FastMCP does not split the `#page=N` citation fragment out as a separate handler
    argument: it arrives concatenated onto `filename` (e.g. "lec.pdf#page=2"), so we
    parse it back out here and narrow to just that page when present; `get_page` is
    the precise tool either way and is what the gate demo uses."""
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
