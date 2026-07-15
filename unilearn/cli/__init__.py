"""CLI entry point. Verbs land phase by phase (pivot.md roadmap v2):
P1 init/index · P2 import/export · P3 ask · P4 mcp/serve · P6 export-deck."""

import typer

app = typer.Typer(no_args_is_help=True, help="UniLearn — local course knowledge bases for AI agents.")


@app.command()
def version() -> None:
    """Print version."""
    from importlib.metadata import version as v

    typer.echo(v("unilearn"))
