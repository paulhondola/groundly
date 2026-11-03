"""App objects and shared helpers for the Groundly CLI. Imports no verb modules
(subjects/models import from here) — avoids circularity."""

import io
import re
import sys
from importlib.metadata import version as _package_version
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

models_app = typer.Typer()
app.add_typer(models_app, name="models")

skill_app = typer.Typer()
app.add_typer(skill_app, name="skill")


# A terminal that cannot encode a character shows "?" rather than crashing the verb. This changes
# one stream's error handler: stored text is untouched, and MCP stdio writes UTF-8 bytes to the fd.
if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(errors="replace")

console = Console(emoji=False)  # `:warning:` in a filename is text

# C0 (but tab), DEL, C1, Unicode line separators and bidi overrides: what a terminal would act on
# or reorder. Rich strips only a few of these, so ESC survives both `escape` and `Text`.
_UNSAFE = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


def _visible(value: object) -> str:
    """A value from a store, a bundle or the filesystem, as text a terminal only displays. For
    text printed outside Rich; console output goes through `_safe`."""
    return _UNSAFE.sub("\ufffd", str(value))


def _safe(value: object, *, multiline: bool = False) -> str:
    """Such a value as console markup that prints literally, one line unless `multiline`. Escape
    the finished string once, and never build a line from two results: `escape` leaves an
    unclosed `[` alone, and a tag can close on a later line or in a later value."""
    if multiline:
        return escape("\n".join(_visible(line) for line in str(value).splitlines()))
    return escape(_visible(value))


def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {_safe(message, multiline=True)}")
    raise typer.Exit(code=1)


def _subject_checked(subject: str):
    from groundly.core.subject import Subject

    try:
        subj = Subject(subject)
    except ValueError as exc:
        _fail(str(exc))
    if not subj.exists():
        _fail(f"Subject '{subject}' is not initialized — run: groundly index {subject} <paths>")
    return subj


def _manifest_checked(subj):
    try:
        return subj.load_manifest()
    except (OSError, ValueError):  # pydantic's ValidationError is a ValueError
        _fail(
            f"manifest.json of '{subj.name}' is corrupt or unreadable, so nothing was changed — "
            f"re-import the subject, or run: groundly drop {subj.name}"
        )


def _store_checked(subj):
    from groundly.core.store import SubjectStore

    try:
        store_obj = SubjectStore(subj.store_db_path)
        conn = store_obj.connect()
        conn.close()
        return store_obj
    except RuntimeError as exc:  # missing store.db / newer schema — named cause, no traceback
        _fail(str(exc))


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
