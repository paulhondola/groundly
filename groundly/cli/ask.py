"""ask/search verbs: the enforced grounded-answer pipeline and its raw retrieval
half — P3's testable face of the one shared `ask`/`search` functions (P4 exposes
the same functions as MCP tools)."""

from typing import Annotated

import typer
from rich.markup import escape

from groundly.cli.app import _fail, _store_checked, _subject_checked, app, console

# A literal, not `retrieval.arms.VECTOR`: a signature default is evaluated at import
# time, and importing the arm table costs ~6.4s of graphrag and pandas that every
# `groundly --help` would pay for. `cli/eval.py`'s `_DEFAULT_ARMS` is a literal for the
# same reason. `test_the_cli_default_arm_matches_the_table` pins this against ARM_TABLE
# so the shortcut cannot drift, and `test_the_arm_help_text_names_every_askable_arm`
# does the same for the prose list in the `--arm` help below.
_DEFAULT_ARM = "vector"


@app.command()
def ask(
    subject: Annotated[str, typer.Argument(help="Subject to ask.")],
    query: Annotated[
        str, typer.Argument(help="Question to answer, grounded in the subject's materials.")
    ],
    arm: Annotated[
        str,
        typer.Option(
            "--arm",
            help="Retrieval arm: 'vector' (default, zero-key) or 'hybrid-local', which "
            "needs a built graph and is also provider-free per query. Singular, unlike "
            "`groundly eval --arms`.",
        ),
    ] = _DEFAULT_ARM,
    no_rerank: Annotated[
        bool, typer.Option("--no-rerank", help="Skip the cross-encoder rerank step.")
    ] = False,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug", help="Stream debug logs to stderr (also: GROUNDLY_LOG_LEVEL=DEBUG)."
        ),
    ] = False,
) -> None:
    """Ask a grounded question: a cited answer, or the refusal — never model knowledge."""
    from groundly.agents.ask import NoCitationsError
    from groundly.agents.ask import ask as ask_fn
    from groundly.core.logs import setup_logging
    from groundly.llm.chat import ChatUnreachableError
    from groundly.llm.config import ProviderNotConfiguredError
    from groundly.llm.embeddings import ModelDownloadError
    from groundly.retrieval.arms import validate_arms
    from groundly.retrieval.graph import GraphNotBuiltError

    try:
        setup_logging(debug)
    except ValueError as exc:
        _fail(str(exc))

    # Screened here as well as inside `ask()`: same reason `groundly eval` screens before
    # `eval.runner.run`. It also keeps ValueError out of the except tuple below, where it
    # would dress an unrelated bug up as a clean CLI failure.
    try:
        validate_arms([arm], ranked_only=True)
    except ValueError as exc:
        _fail(str(exc))

    subj = _subject_checked(subject)
    _store_checked(subj)
    try:
        result = ask_fn(subject, query, arm=arm, rerank=not no_rerank)
    except (
        ProviderNotConfiguredError,
        NoCitationsError,
        ModelDownloadError,
        ChatUnreachableError,
        GraphNotBuiltError,
    ) as exc:
        _fail(str(exc))

    console.print(escape(result.answer))
    if result.citations:
        console.print("\nSources:")
        for i, c in enumerate(result.citations, start=1):
            loc = f" p.{c.page}" if c.page else ""
            heading = f" — {escape(c.heading_path)}" if c.heading_path else ""
            console.print(f"  {i}. {escape(c.filename)}{loc}{heading}")
    # No `router=` here: `ask()` no longer classifies, so the field would print `—`
    # forever (decision 28, unchanged by 29 — `--arm` states the arm outright).
    # `groundly eval` is where router behaviour is reported now.
    console.print(f"[dim]citations={len(result.citations)}[/dim]")


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
        nodes = search_fn(subject, query, k=k, rerank=rerank)
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
