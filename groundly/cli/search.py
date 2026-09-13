"""`groundly search`: raw retrieval from the terminal — the same function the MCP
`search` tool calls. No LLM call, no provider needed."""

from typing import Annotated

import typer
from rich.markup import escape

from groundly.cli.app import _fail, _store_checked, _subject_checked, app, console


@app.command()
def search(
    subject: Annotated[str, typer.Argument(help="Subject to search.")],
    query: Annotated[str, typer.Argument(help="Search query.")],
    k: Annotated[
        int | None,
        typer.Option("-k", help="Number of chunks to return (default: retrieval.context_k)."),
    ] = None,
    rerank: Annotated[
        bool | None,
        typer.Option(
            "--rerank/--no-rerank", help="Cross-encoder rerank (default: retrieval.rerank)."
        ),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug", help="Stream debug logs to stderr (also: GROUNDLY_LOG_LEVEL=DEBUG)."
        ),
    ] = False,
) -> None:
    """Raw retrieval: top-k chunks with text + citations. No LLM call, works with no
    provider configured — the host composes its own answer (best-effort grounding)."""
    from groundly.core.logs import setup_logging
    from groundly.llm.embeddings import ModelDownloadError
    from groundly.retrieval.vector import search as search_fn

    try:
        setup_logging(debug)
    except ValueError as exc:
        _fail(str(exc))

    subj = _subject_checked(subject)
    _store_checked(subj)
    try:
        hits = search_fn(subject, query, k=k, rerank=rerank)
    except ModelDownloadError as exc:
        _fail(str(exc))
    if not hits:
        console.print("[dim]no results[/dim]")
        return
    for i, hit in enumerate(hits, start=1):
        loc = f" p.{hit.page}" if hit.page else ""
        heading = f" — {escape(hit.heading_path)}" if hit.heading_path else ""
        console.print(f"[bold]{i}.[/bold] {escape(hit.filename)}{loc}{heading}")
        console.print(escape(hit.text))
        console.print()
