"""Share a subject as a portable bundle: export / import (UC-30)."""

from pathlib import Path
from typing import Annotated, Optional

import typer

from groundly.cli.app import _fail, _subject_checked, app, console


@app.command()
def export(
    subject: Annotated[str, typer.Argument(help="Subject to export.")],
    out: Annotated[
        Optional[Path],
        typer.Option("-o", "--out", help="Output bundle path; default ./SUBJECT.groundly."),
    ] = None,
    no_materials: Annotated[
        bool,
        typer.Option("--no-materials", help="Exclude original files (chunk text still ships)."),
    ] = False,
) -> None:
    """Zip a subject's manifest, store.db, materials and graph into a portable bundle."""
    from groundly.core import bundle

    subj = _subject_checked(subject)
    out_path = out or Path(f"{subject}.groundly")

    with console.status("exporting…") as status:

        def on_file(name: str) -> None:
            status.update(f"packing {name}…")

        try:
            bundle.export_subject(
                subj, out_path, include_materials=not no_materials, on_file=on_file
            )
        except RuntimeError as exc:  # BundleError is a RuntimeError
            _fail(str(exc))

    console.print(f"exported [bold]{subject}[/bold] to {out_path}")
    console.print("this bundle contains everything indexed in this subject.")


@app.command("import")
def import_(
    bundle_path: Annotated[Path, typer.Argument(help="Bundle file (.groundly) to import.")],
    as_name: Annotated[
        Optional[str], typer.Option("--as", help="Import under a different subject name.")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing subject without confirmation.")
    ] = False,
) -> None:
    """Import a bundle produced by `groundly export`: validated, zip-slip-safe extraction,
    fresh empty progress database, embedding pin checked (re-embed offered on mismatch)."""
    import shutil
    import tempfile
    import zipfile

    from groundly.core import bundle, progress, store
    from groundly.core.manifest import Embedding, Graphrag
    from groundly.core.paths import groundly_home, validate_subject_name
    from groundly.core.subject import Subject
    from groundly.ingestion.graph import corpus_hash

    if not bundle_path.exists():
        _fail(f"{bundle_path} does not exist")

    try:
        with zipfile.ZipFile(bundle_path) as zf:
            manifest = bundle.read_manifest(zf)
    except (bundle.BundleError, zipfile.BadZipFile) as exc:
        _fail(f"{bundle_path} is not a valid bundle: {exc}")

    name = as_name or manifest.subject
    try:
        validate_subject_name(name)
    except ValueError as exc:
        _fail(str(exc))

    target = Subject(name)
    if target.exists() and not force:
        typer.confirm(
            f"subject '{name}' exists — replace it? (its progress and notes are deleted)",
            abort=True,
        )

    imports_dir = groundly_home() / ".imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=imports_dir))

    def _drop_graph(graph_dir: Path, manifest, reason: str):
        """The imported graph/ can't be trusted (reason) — drop it and reset the
        manifest's graphrag block so the subject's manifest correctly reflects "no
        graph"; the student can rebuild with `groundly index --graph` if they want one."""
        shutil.rmtree(graph_dir, ignore_errors=True)
        manifest.graphrag = Graphrag()
        console.print(f"[dim]{reason} — dropped the imported graph/[/dim]")
        return manifest

    try:
        with console.status("importing…") as status:

            def on_file(fname: str) -> None:
                status.update(f"extracting {fname}…")

            manifest = bundle.extract_bundle(bundle_path, tmp_dir, on_file=on_file)
            bundle.check_counts(tmp_dir / "store.db", manifest)

            graph_dir = tmp_dir / "graph"
            if graph_dir.exists():
                actual_hash = corpus_hash(store.SQLiteSubjectStore(tmp_dir / "store.db"))
                if manifest.graphrag.corpus_hash != actual_hash:
                    manifest = _drop_graph(
                        graph_dir,
                        manifest,
                        "bundle's graph/ doesn't match its store.db contents",
                    )

            if not bundle.pin_matches(manifest):
                typer.confirm(
                    "embedding pin mismatch — re-embed locally now? (free, takes a few minutes)",
                    abort=True,
                )
                from groundly.llm.embeddings import shared_embedder

                def on_step(done: int, total: int) -> None:
                    status.update(f"re-embedding {done}/{total} chunks…")

                bundle.re_embed(tmp_dir / "store.db", shared_embedder(), on_step=on_step)
                manifest.embedding = Embedding()
                if graph_dir.exists():
                    manifest = _drop_graph(
                        graph_dir,
                        manifest,
                        "embedding pin changed and the graph was re-embedded",
                    )

        manifest.subject = name
        manifest.save(tmp_dir / "manifest.json")
        progress.create_progress(tmp_dir / "progress.db")
        (tmp_dir / "materials").mkdir(exist_ok=True)
    except typer.Abort:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except (RuntimeError, ValueError) as exc:  # BundleError is a RuntimeError
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _fail(str(exc))

    try:
        if target.exists():
            shutil.rmtree(target.root_dir)
        tmp_dir.rename(target.root_dir)
    except OSError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _fail(
            f"a leftover directory already exists at {target.root_dir} "
            "(no manifest.json, so it wasn't replaced) — remove it and re-run the import"
        )
    console.print(f"imported [bold]{name}[/bold] to {target.root_dir}")
