"""Subject lifecycle verbs: init, index, list, remove."""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
from rich.table import Table

from groundly.cli.app import _fail, _subject_checked, _store_checked, app, console
from groundly.cli.cost_display import _print_actual_spend, _print_cost_estimate


@app.command()
def init(
    subject: Annotated[str, typer.Argument(help="Subject name; becomes ~/.groundly/<SUBJECT>/.")],
) -> None:
    """Create a subject: manifest.json, materials/, store.db, progress.db."""
    from groundly.core.subject import Subject

    try:
        subj = Subject(subject)
        created = subj.initialize()
    except ValueError as exc:
        _fail(str(exc))
    if created:
        console.print(f"Initialized [bold]{subject}[/bold] at {subj.root_dir}")
    else:
        console.print(f"[bold]{subject}[/bold] already initialized at {subj.root_dir}")


@app.command()
def index(
    subject: Annotated[str, typer.Argument(help="Subject to index into (must be initialized).")],
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to index.")],
    ocr_lang: Annotated[
        Optional[str],
        typer.Option(
            "--ocr-lang",
            help="OCR language for scanned PDFs (e.g. 'ro'); set once per subject, "
            "persisted in the manifest.",
        ),
    ] = None,
    graph: Annotated[
        bool,
        typer.Option(
            "--graph",
            help="Build the graphrag arm for this subject (first build only; once built, "
            "later index runs auto-rebuild on corpus changes without needing this flag again).",
        ),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug", help="Stream debug logs to stderr (also: GROUNDLY_LOG_LEVEL=DEBUG)."
        ),
    ] = False,
) -> None:
    """Index course materials: hash-skip idempotent, per-file progress, resumable."""
    from groundly.core.logs import setup_logging
    from groundly.core.subject import Subject
    from groundly.ingestion import pipeline
    from groundly.ingestion.results import Status

    try:
        debug_on = setup_logging(debug)
    except ValueError as exc:
        _fail(str(exc))

    try:
        subj = Subject(subject)
    except ValueError as exc:  # bad subject name — same cause pipeline would name
        _fail(str(exc))
    if subj.exists():
        manifest = subj.load_manifest()
        recorded = manifest.ocr.lang[0] if manifest.ocr.lang else None
        if ocr_lang and recorded and ocr_lang != recorded:
            # the recorded lang shaped every OCR'd chunk already stored — changing it
            # silently would mix corpora (decision 15). With nothing indexed yet there
            # is nothing to mix: allow the change (recovers from a mistyped lang, which
            # stores no rows — every extraction exits model-unavailable).
            if manifest.counts.materials > 0:
                _fail(
                    f"OCR language already set to {recorded!r} for this subject; changing it "
                    "requires re-indexing (remove and re-index scanned materials)"
                )
            manifest.ocr.lang = [ocr_lang]
            subj.save_manifest(manifest)
        elif ocr_lang and not recorded:
            manifest.ocr.lang = [ocr_lang]
            subj.save_manifest(manifest)
        elif not ocr_lang:
            ocr_lang = recorded

    labels = {
        Status.INDEXED: "[green]indexed[/green]",
        Status.SKIPPED_DUPLICATE: "[dim]skipped (already indexed)[/dim]",
        Status.SKIPPED_UNSUPPORTED: "[yellow]skipped[/yellow]",
        Status.SKIPPED_FAILED: "[yellow]skipped[/yellow]",
        Status.EXTRACTION_FAILED: "[red]failed[/red]",
        Status.ERROR: "[red]error[/red]",
    }

    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        disable=debug_on,
    ) as progress:
        # Named phase and a real total from the first frame. `total=None` renders as
        # `0/?`, and the stretch before `on_discovered` fires is a directory walk with
        # `.groundlyignore` pruning — on a large tree that is the slowest silent part of
        # the run, showing an unknown file count exactly when the user wants one.
        task = progress.add_task("discovering files…", total=1)

        def on_discovered(total: int) -> None:
            progress.update(task, description="indexing…", total=total)

        def on_event(path: Path, stage: str) -> None:
            if stage in ("extracting", "embedding"):
                progress.update(task, description=f"{path.name}: {stage}…")
            elif stage in set(Status):
                progress.advance(task)

        try:
            results = pipeline.index_paths(
                subject, paths, on_event=on_event, on_discovered=on_discovered, ocr_lang=ocr_lang
            )
        except (RuntimeError, ValueError) as exc:
            _fail(str(exc))

    for r in results:
        # filenames and parser errors are document-influenced — never live markup
        detail = f" — {escape(r.detail)}" if r.detail else ""
        chunks = f" ({r.chunks} chunks)" if r.status == Status.INDEXED else ""
        console.print(f"  {escape(r.path.name)}: {labels[r.status]}{chunks}{detail}")

    indexed = sum(r.status == Status.INDEXED for r in results)
    failed = sum(r.status in (Status.EXTRACTION_FAILED, Status.ERROR) for r in results)
    console.print(f"{indexed} indexed, {len(results) - indexed - failed} skipped, {failed} failed")

    _maybe_build_graph(subj, graph=graph, yes=yes, debug=debug_on)

    if failed:
        raise typer.Exit(code=1)


def _maybe_build_graph(subj, *, graph: bool, yes: bool, debug: bool = False) -> None:
    """Corpus state is final for this run — decide whether the graph needs a
    (re)build. Auto-rebuilds a stale graph unconditionally; a first build only
    happens opt-in via --graph. Zero-key `index` behavior is unchanged when
    neither applies."""
    from groundly.core.store import SubjectStore
    from groundly.ingestion.graph import GraphBuildError, build_graph, graph_is_stale
    from groundly.llm.config import ProviderNotConfiguredError
    from groundly.llm.graph_cost import estimate_cost
    from groundly.llm.graphrag_adapter import ExtractionPromptError

    store_obj = SubjectStore(subj.store_db_path)
    # The manifest, not the directory: a refused or Ctrl-C'd build deliberately leaves
    # graph/ behind so the retry keeps graphrag's paid-for cache (decision 21), and
    # reading that as "there is a graph here" turned every later plain `groundly index`
    # into a prompt to *rebuild* a graph that was never recorded — the opt-in this
    # function documents, bypassed. Same gate as mcp/server.py and _require_graph.
    recorded = subj.load_manifest().graphrag.corpus_hash is not None

    # graph_is_stale resolves the configured extraction prompt, so a broken custom path
    # surfaces here — named, and before any LLM call.
    try:
        reason = graph_is_stale(subj, store_obj) if recorded else None
    except (GraphBuildError, ExtractionPromptError) as exc:
        _fail(str(exc))

    if reason:
        # Printed rather than folded into the confirmation text: `--yes` skips the
        # prompt, and a rebuild that silently re-spends the student's tokens should
        # still say what triggered it.
        console.print(f"[yellow]The graph is stale[/yellow] — {reason}.")
        prompt = "Rebuild it now?"
    elif not recorded and graph:
        prompt = "Build the graphrag arm for this subject now?"
    else:
        return

    chunks = store_obj.all_chunks()
    total_chars = sum(len(row["text"]) for row in chunks)
    est = estimate_cost(total_chars, len(chunks))
    _print_cost_estimate(est)

    if not yes:
        typer.confirm(prompt, abort=True)

    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        disable=debug,
    ) as progress:
        # A real total from the first frame: `total=None` renders as `0/?`, which reads as
        # a broken bar rather than an unknown one. build_graph replaces both the
        # description and the total as soon as it knows them.
        task = progress.add_task("preparing…", total=1)

        def on_event(description: str, completed: int, total: int) -> None:
            progress.update(task, description=description, completed=completed, total=total)

        try:
            result = build_graph(
                subj,
                store_obj,
                estimated_tokens=est.input_tokens,
                estimated_cost_usd=est.low_usd,
                on_event=on_event,
            )
        except (GraphBuildError, ProviderNotConfiguredError) as exc:
            _fail(str(exc))
    notes = []
    if result.failed:
        notes.append(f"{result.failed} of {result.chunks} chunks failed extraction")
    if result.reports_failed:
        notes.append(f"{result.reports_failed} community summaries failed")
    dropped = f" ([yellow]{', '.join(notes)}[/yellow])" if notes else ""
    console.print(f"Graph built for [bold]{subj.name}[/bold]{dropped}")
    _print_actual_spend(result)


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

    from groundly.core.paths import discover_subjects
    from groundly.core.subject import Subject

    if subject is None:
        table = Table("subject", "materials", "chunks")
        for name in discover_subjects():
            try:
                subj = Subject(name)
                manifest = subj.load_manifest()
            except ValidationError:
                # one damaged subject must not take down the whole listing
                console.print(f"[red]Warning:[/red] {name}: manifest.json is corrupt — skipping")
                continue
            table.add_row(name, str(manifest.counts.materials), str(manifest.counts.chunks))
        console.print(table)
        return

    subj = _subject_checked(subject)
    store_obj = _store_checked(subj)
    try:
        table = Table("material", "status", "pages", "chunks", "detail")
        for row in store_obj.list_materials():
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


@app.command()
def remove(
    subject: Annotated[str, typer.Argument(help="Subject the material belongs to.")],
    material: Annotated[
        Optional[str],
        typer.Argument(
            help="Material filename (or sha256 prefix) as shown by `groundly list`; "
            "omit to remove the whole subject."
        ),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove a material and all its indexed data, or a whole subject if no material given."""
    import sqlite3

    from groundly.core.manifest import sync_counts

    subj = _subject_checked(subject)

    if material is None:
        import shutil

        if not yes:
            typer.confirm(
                f"Remove subject {subject} and ALL its data (materials, index, progress, notes)?",
                abort=True,
            )
        shutil.rmtree(subj.root_dir)
        console.print(f"Removed subject [bold]{subject}[/bold]")
        return

    store_obj = _store_checked(subj)
    try:
        matches = store_obj.find_materials(material)
        if not matches:
            _fail(f"no material {material!r} in {subject} — see: groundly list {subject}")
        if len(matches) > 1:
            candidates = ", ".join(f"{m['filename']} ({m['sha256'][:8]})" for m in matches)
            _fail(f"{material!r} is ambiguous — candidates: {candidates}; use a sha256 prefix")
        target = matches[0]
        if not yes:
            typer.confirm(
                f"Remove {target['filename']} and all its indexed data from {subject}?",
                abort=True,
            )
        store_obj.remove_material(target["id"])
        conn = store_obj.connect()
        try:
            sync_counts(conn, subj.manifest_path)
        finally:
            conn.close()

        if target["status"] == "indexed":
            # failed rows never got a copy in materials/, and their original filename
            # (no collision suffix) can shadow a different indexed material's file
            stored = subj.materials_dir / target["filename"]
            if stored.exists():
                stored.unlink()
        console.print(f"Removed [bold]{escape(target['filename'])}[/bold] from {subject}")
        # Recorded, not just present: leftover artifacts from a failed build are not a
        # graph, and promising a rebuild that no index run will trigger is the same
        # confident-but-wrong message the gates in ingestion/graph.py exist to prevent.
        if subj.load_manifest().graphrag.corpus_hash is not None:
            console.print(
                "[dim]Note: the graph is now stale — it rebuilds on the next"
                " corpus-hash-triggered index run[/dim]"
            )
    except sqlite3.OperationalError as exc:
        _fail(f"store.db is corrupt or incomplete: {exc}")
