"""App objects and shared helpers for the Groundly CLI. Imports no verb modules
(subjects/models import from here) — avoids circularity."""

from importlib.metadata import version as _package_version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Groundly — local course knowledge bases for AI agents.",
)
config_app = typer.Typer()
app.add_typer(config_app, name="config")

models_app = typer.Typer(no_args_is_help=True)
app.add_typer(models_app, name="models")


console = Console()


def _fail(message: str) -> None:
    console.print(f"[red]error:[/red] {escape(message)}")
    raise typer.Exit(code=1)


def _subject_dir_checked(subject: str) -> Path:
    from groundly.core.paths import subject_dir

    try:
        sdir = subject_dir(subject)
    except ValueError as exc:
        _fail(str(exc))
    if not (sdir / "manifest.json").exists():
        _fail(f"subject '{subject}' is not initialized — run: groundly init {subject}")
    return sdir


def _connect_checked(sdir: Path):
    from groundly.core import store

    try:
        return store.connect(sdir / "store.db")
    except RuntimeError as exc:  # missing store.db / newer schema — named cause, no traceback
        _fail(str(exc))


def _not_implemented(verb: str) -> None:
    typer.echo(f"groundly {verb}: not implemented yet — arrives in a later phase")
    raise typer.Exit(code=1)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(_package_version("groundly"))
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
    """Groundly — local-first course knowledge bases for AI agents."""
