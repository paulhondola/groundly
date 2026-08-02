"""Graph export verb: `groundly export-graph SUBJECT [--out PATH] [--level 0|1]` — writes
one self-contained HTML file visualizing the subject's graphrag knowledge graph (entity
nodes coloured by Leiden community, force-directed, sidebar with community reports and
citations). The generator is `groundly/core/graph_html.py`."""

from pathlib import Path
from typing import Annotated, Optional

import typer

from groundly.cli.app import _fail, _subject_checked, app, console


@app.command("export-graph")
def export_graph(
    subject: Annotated[str, typer.Argument(help="Subject whose graph to visualize.")],
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Output HTML path (default: ./<SUBJECT>-graph.html)."),
    ] = None,
    level: Annotated[
        Optional[int],
        typer.Option("--level", "-l", help="Community level to render (0 or 1)."),
    ] = None,
) -> None:
    """Export the subject's knowledge graph as a self-contained HTML visualization."""
    from groundly.core.graph_html import GraphHtmlError, export_graph_html

    _subject_checked(subject)
    target = out if out is not None else Path.cwd() / f"{subject}-graph.html"
    try:
        result = export_graph_html(subject, target, level=level)
    except GraphHtmlError as exc:
        _fail(str(exc))
    console.print(
        f"Exported graph to {result.path} "
        f"({result.nodes} nodes, {result.edges} edges, {result.communities} communities)"
    )
    # The page is meant to be shared — into a thesis, at a supervisor — so say what travels
    # with it, the way `groundly export` names a bundle's contents (cli/sharing.py). The
    # file-level privacy boundary holds (progress.db is never read, so no query history or
    # quiz results), but source filenames and heading paths are still course-shaped
    # metadata a student should know they are handing over.
    console.print(
        "[dim]Contains entity names, descriptions, community summaries and source "
        "filenames/pages. No quiz history or query traces.[/dim]"
    )
    if result.aggregated:
        console.print(
            "[yellow]Note:[/yellow] the graph exceeded the node cap — rendered the "
            "community meta-graph instead of the entity graph."
        )
