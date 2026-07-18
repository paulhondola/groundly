"""ask/search verbs: the enforced grounded-answer pipeline and its raw retrieval
half — P3's testable face of the one shared `ask`/`search` functions (P4 exposes
the same functions as MCP tools)."""

from typing import Annotated

import typer
from rich.markup import escape

from groundly.cli.app import _fail, _store_checked, _subject_checked, app, console


@app.command()
def ask(
    subject: Annotated[str, typer.Argument(help="Subject to ask.")],
    query: Annotated[
        str, typer.Argument(help="Question to answer, grounded in the subject's materials.")
    ],
    no_rerank: Annotated[
        bool, typer.Option("--no-rerank", help="Skip the cross-encoder rerank step.")
    ] = False,
) -> None:
    """Ask a grounded question: a cited answer, or the refusal — never model knowledge."""
    from groundly.agents.ask import NoCitationsError
    from groundly.agents.ask import ask as ask_fn
    from groundly.llm.chat import ChatUnreachableError
    from groundly.llm.config import ProviderNotConfiguredError
    from groundly.llm.embeddings import ModelDownloadError

    subj = _subject_checked(subject)
    _store_checked(subj)
    try:
        result = ask_fn(subject, query, rerank=not no_rerank)
    except (
        ProviderNotConfiguredError,
        NoCitationsError,
        ModelDownloadError,
        ChatUnreachableError,
    ) as exc:
        _fail(str(exc))

    console.print(escape(result.answer))
    if result.citations:
        console.print("\nSources:")
        for i, c in enumerate(result.citations, start=1):
            loc = f" p.{c.page}" if c.page else ""
            heading = f" — {escape(c.heading_path)}" if c.heading_path else ""
            console.print(f"  {i}. {escape(c.filename)}{loc}{heading}")
    console.print(
        f"[dim]router={result.router_label or '—'} citations={len(result.citations)}[/dim]"
    )


@app.command()
def search(
    subject: Annotated[str, typer.Argument(help="Subject to search.")],
    query: Annotated[str, typer.Argument(help="Search query.")],
    k: Annotated[int, typer.Option("-k", help="Number of chunks to return.")] = 8,
    no_rerank: Annotated[
        bool, typer.Option("--no-rerank", help="Skip the cross-encoder rerank step.")
    ] = False,
) -> None:
    """Raw retrieval: top-k chunks with text + citations. No LLM call, works with no
    provider configured — the host composes its own answer (best-effort grounding)."""
    from groundly.llm.embeddings import ModelDownloadError
    from groundly.retrieval.vector import search as search_fn

    subj = _subject_checked(subject)
    _store_checked(subj)
    try:
        nodes = search_fn(subject, query, k=k, rerank=not no_rerank)
    except ModelDownloadError as exc:
        _fail(str(exc))
    if not nodes:
        console.print("[dim]no results[/dim]")
        return
    for i, n in enumerate(nodes, start=1):
        m = n.node.metadata
        loc = f" p.{m['page']}" if m["page"] else ""
        heading = f" — {escape(m['heading_path'])}" if m["heading_path"] else ""
        console.print(f"[bold]{i}.[/bold] {escape(m['filename'])}{loc}{heading}")
        console.print(escape(n.node.get_content()))
        console.print()
