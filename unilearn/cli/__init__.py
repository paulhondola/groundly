"""UniLearn CLI — batch lifecycle verbs; the host agent is the interactive surface.

Command surface per docs/superpowers/specs/2026-07-16-p1-cli-surface-design.md.
Later phases add verbs: P2 import/export · P3 ask · P4 mcp/serve · P6 export-deck.
"""

from importlib.metadata import version as _package_version
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="UniLearn — local course knowledge bases for AI agents.",
)
config_app = typer.Typer()
app.add_typer(config_app, name="config")

console = Console()


def _fail(message: str) -> None:
    console.print(f"[red]error:[/red] {escape(message)}")
    raise typer.Exit(code=1)


def _subject_dir_checked(subject: str) -> Path:
    from unilearn.core.paths import subject_dir

    try:
        sdir = subject_dir(subject)
    except ValueError as exc:
        _fail(str(exc))
    if not (sdir / "manifest.json").exists():
        _fail(f"subject '{subject}' is not initialized — run: unilearn init {subject}")
    return sdir


def _connect_checked(sdir: Path):
    from unilearn.core import store

    try:
        return store.connect(sdir / "store.db")
    except RuntimeError as exc:  # missing store.db / newer schema — named cause, no traceback
        _fail(str(exc))


def _not_implemented(verb: str) -> None:
    typer.echo(f"unilearn {verb}: not implemented yet — arrives in a later phase")
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
    from unilearn.core.subject import init_subject

    try:
        sdir, created = init_subject(subject)
    except ValueError as exc:
        _fail(str(exc))
    if created:
        console.print(f"initialized [bold]{subject}[/bold] at {sdir}")
    else:
        console.print(f"[bold]{subject}[/bold] already initialized at {sdir}")


@app.command()
def index(
    subject: Annotated[str, typer.Argument(help="Subject to index into (must be initialized).")],
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to index.")],
) -> None:
    """Index course materials: hash-skip idempotent, per-file progress, resumable."""
    from unilearn.ingestion import pipeline

    labels = {
        pipeline.INDEXED: "[green]indexed[/green]",
        pipeline.SKIPPED_DUPLICATE: "[dim]skipped (already indexed)[/dim]",
        pipeline.SKIPPED_UNSUPPORTED: "[yellow]skipped[/yellow]",
        pipeline.SKIPPED_FAILED: "[yellow]skipped[/yellow]",
        pipeline.EXTRACTION_FAILED: "[red]failed[/red]",
        pipeline.ERROR: "[red]error[/red]",
    }

    with console.status("indexing…") as status:

        def on_event(path: Path, stage: str) -> None:
            if stage in ("extracting", "embedding"):
                status.update(f"{path.name}: {stage}…")

        try:
            results = pipeline.index_paths(subject, paths, on_event=on_event)
        except (RuntimeError, ValueError) as exc:
            _fail(str(exc))

    for r in results:
        # filenames and parser errors are document-influenced — never live markup
        detail = f" — {escape(r.detail)}" if r.detail else ""
        chunks = f" ({r.chunks} chunks)" if r.status == pipeline.INDEXED else ""
        console.print(f"  {escape(r.path.name)}: {labels[r.status]}{chunks}{detail}")

    indexed = sum(r.status == pipeline.INDEXED for r in results)
    failed = sum(r.status in (pipeline.EXTRACTION_FAILED, pipeline.ERROR) for r in results)
    console.print(f"{indexed} indexed, {len(results) - indexed - failed} skipped, {failed} failed")
    if failed:
        raise typer.Exit(code=1)


@app.command(name="list")
def list_(
    subject: Annotated[
        Optional[str],
        typer.Argument(help="Subject to inspect; omit to list all subjects."),
    ] = None,
) -> None:
    """List subjects, or one subject's materials with status, pages, chunks."""
    import sqlite3

    from pydantic import ValidationError

    from unilearn.core import store
    from unilearn.core.manifest import Manifest
    from unilearn.core.paths import discover_subjects, subject_dir

    if subject is None:
        table = Table("subject", "materials", "chunks")
        for name in discover_subjects():
            try:
                manifest = Manifest.load(subject_dir(name) / "manifest.json")
            except ValidationError:
                # one damaged subject must not take down the whole listing
                console.print(f"[red]warning:[/red] {name}: manifest.json is corrupt — skipping")
                continue
            table.add_row(name, str(manifest.counts.materials), str(manifest.counts.chunks))
        console.print(table)
        return

    sdir = _subject_dir_checked(subject)
    conn = _connect_checked(sdir)
    try:
        table = Table("material", "status", "pages", "chunks", "detail")
        for row in store.list_materials(conn):
            table.add_row(
                escape(row["filename"]),
                row["status"],
                str(row["pages"] or "—"),
                str(row["chunk_count"]),
                escape(row["error"] or ""),
            )
        console.print(table)
    except sqlite3.OperationalError as exc:
        _fail(f"store.db is corrupt or incomplete: {exc}")
    finally:
        conn.close()


@app.command()
def remove(
    subject: Annotated[str, typer.Argument(help="Subject the material belongs to.")],
    material: Annotated[
        Optional[str],
        typer.Argument(
            help="Material filename (or sha256 prefix) as shown by `unilearn list`; "
            "omit to remove the whole subject."
        ),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove a material and all its indexed data, or a whole subject if no material given."""
    import sqlite3

    from unilearn.core import store
    from unilearn.core.manifest import sync_counts

    sdir = _subject_dir_checked(subject)

    if material is None:
        import shutil

        if not yes:
            typer.confirm(
                f"remove subject {subject} and ALL its data"
                " (materials, index, progress, notes)?",
                abort=True,
            )
        shutil.rmtree(sdir)
        console.print(f"removed subject [bold]{subject}[/bold]")
        return

    conn = _connect_checked(sdir)
    try:
        matches = store.find_materials(conn, material)
        if not matches:
            _fail(f"no material {material!r} in {subject} — see: unilearn list {subject}")
        if len(matches) > 1:
            candidates = ", ".join(f"{m['filename']} ({m['sha256'][:8]})" for m in matches)
            _fail(f"{material!r} is ambiguous — candidates: {candidates}; use a sha256 prefix")
        target = matches[0]
        if not yes:
            typer.confirm(
                f"remove {target['filename']} and all its indexed data from {subject}?",
                abort=True,
            )
        store.remove_material(conn, target["id"])
        sync_counts(conn, sdir / "manifest.json")
        if target["status"] == "indexed":
            # failed rows never got a copy in materials/, and their original filename
            # (no collision suffix) can shadow a different indexed material's file
            stored = sdir / "materials" / target["filename"]
            if stored.exists():
                stored.unlink()
        console.print(f"removed [bold]{escape(target['filename'])}[/bold] from {subject}")
        if (sdir / "graph").exists():
            console.print(
                "[dim]note: the graph is now stale — it rebuilds on the next"
                " corpus-hash-triggered index run[/dim]"
            )
    except sqlite3.OperationalError as exc:
        _fail(f"store.db is corrupt or incomplete: {exc}")
    finally:
        conn.close()


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
