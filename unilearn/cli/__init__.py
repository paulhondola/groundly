from importlib.metadata import version as _package_version
from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="UniLearn — local course knowledge bases for AI agents.",
)
config_app = typer.Typer()
app.add_typer(config_app, name="config")


def _not_implemented(verb: str) -> None:
    typer.echo(f"unilearn {verb}: not implemented yet — CLI skeleton (P1 in progress)")
    raise typer.Exit(code=1)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(_package_version("unilearn"))
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Print version and exit."
        ),
    ] = False,
) -> None:
    """UniLearn — local-first course knowledge bases for AI agents."""


@app.command()
def init(
    subject: Annotated[str, typer.Argument(help="Subject name; becomes ~/.unilearn/<SUBJECT>/.")],
) -> None:
    """Create a subject: manifest.json, materials/, store.db, progress.db."""
    _not_implemented("init")


@app.command()
def index(
    subject: Annotated[str, typer.Argument(help="Subject to index into (must be initialized).")],
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to index.")],
) -> None:
    """Index course materials: hash-skip idempotent, per-file progress, resumable."""
    _not_implemented("index")


@app.command(name="list")
def list_(
    subject: Annotated[
        Optional[str],
        typer.Argument(help="Subject to inspect; omit to list all subjects."),
    ] = None,
) -> None:
    """List subjects, or one subject's materials with status, pages, chunks."""
    _not_implemented("list")


@app.command()
def remove(
    subject: Annotated[str, typer.Argument(help="Subject the material belongs to.")],
    material: Annotated[str, typer.Argument(help="Material filename as shown by `unilearn list`.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove a material and all its indexed data (chunks, vectors, sparse, FTS)."""
    _not_implemented("remove")


@config_app.callback(invoke_without_command=True)
def config(ctx: typer.Context) -> None:
    """Show the config file path and effective values per call class (keys masked)."""
    if ctx.invoked_subcommand is None:
        _not_implemented("config")


@config_app.command(name="set")
def config_set(
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. chat.model or chat.base_url.")],
    value: Annotated[str, typer.Argument(help="Value to set.")],
) -> None:
    """Set a provider config value in ~/.unilearn/config.toml."""
    _not_implemented("config set")
