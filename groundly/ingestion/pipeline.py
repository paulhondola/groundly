"""The index pipeline (UC-01): hash-skip idempotent, per-file transactions, the run
continues past failures. Ingestion writes the stores; it never serves queries."""

import fnmatch
import hashlib
import os
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from groundly.core.config import load_settings
from groundly.core.manifest import sync_counts
from groundly.core.store import SubjectStore
from groundly.core.subject import Subject
from groundly.ingestion.extract import ExtractionFailure, ModelUnavailable, SubprocessExtractor
from groundly.ingestion.formats import skip_reason
from groundly.ingestion.results import FileResult, OnEvent, Status
from groundly.llm.embeddings import shared_embedder

DEFAULT_IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    "target",
    ".idea",
    ".vscode",
}


def _load_ignore_patterns(root: Path) -> list[str]:
    ignore_file = root / ".groundlyignore"
    if not ignore_file.is_file():
        return []
    lines = (line.strip() for line in ignore_file.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _ignored(rel_posix: str, name: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatch(rel_posix, pat) if "/" in pat else fnmatch.fnmatch(name, pat)
        for pat in patterns
    )


def _iter_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            patterns = _load_ignore_patterns(p)
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames.sort()
                filenames.sort()
                cur = Path(dirpath)
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in DEFAULT_IGNORED_DIRS
                    and not d.startswith(".")
                    and not _ignored((cur / d).relative_to(p).as_posix(), d, patterns)
                ]
                for name in filenames:
                    if name.startswith("."):
                        continue
                    rel = (cur / name).relative_to(p).as_posix()
                    if _ignored(rel, name, patterns):
                        continue
                    files.append(cur / name)
        else:
            files.append(p)
    return files


def _sha256(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _copy_to_materials(src: Path, sha256: str, materials_dir: Path) -> str:
    """Original files are the citation targets; they ship in exports."""
    dest = materials_dir / src.name
    if dest.exists():
        if dest.samefile(src):  # orphan already in materials/ — or a hard link to it
            return dest.name
        if _sha256(dest) != sha256:
            dest = materials_dir / f"{src.stem}-{sha256[:8]}{src.suffix}"
    shutil.copy2(src, dest)
    return dest.name


def _sibling_result(path: Path, primary: FileResult) -> FileResult:
    """Result for a duplicate-content file deferred behind its first occurrence in a run."""
    if primary.status in (Status.INDEXED, Status.SKIPPED_DUPLICATE):
        return FileResult(path, Status.SKIPPED_DUPLICATE, "already indexed")
    if primary.status == Status.EXTRACTION_FAILED:
        error = primary.detail or "unknown error"
        return FileResult(
            path,
            Status.SKIPPED_FAILED,
            f"failed previously: {error} — re-run with --retry-failed",
        )
    return FileResult(path, primary.status, primary.detail)


class IngestionPipeline:
    """Orchestrates indexing of documents: extraction, embedding, and storage."""

    def __init__(
        self,
        subject: Subject,
        extractor: SubprocessExtractor | None = None,
        embedder=None,
        on_event: OnEvent | None = None,
        on_discovered: Callable[[int], None] | None = None,
    ) -> None:
        self.subject = subject
        self.store = SubjectStore(subject.store_db_path)
        self._owns_extractor = extractor is None  # an injected extractor is the caller's to close
        self.extractor = extractor or SubprocessExtractor(load_settings().ingestion)
        self.embedder = embedder or shared_embedder()
        self.on_event = on_event or (lambda path, stage: None)
        self.on_discovered = on_discovered or (lambda total: None)

    def run(
        self, paths: list[Path], ocr_lang: str | None = None, retry_failed: bool = False
    ) -> list[FileResult]:
        if not self.subject.exists():
            raise RuntimeError(
                f"subject '{self.subject.name}' is not initialized — run: groundly index {self.subject.name} <paths>"
            )

        try:
            return self._run(paths, ocr_lang, retry_failed)
        finally:
            if self._owns_extractor:
                self.extractor.close()

    def _run(self, paths: list[Path], ocr_lang: str | None, retry_failed: bool) -> list[FileResult]:
        known = self.store.hash_status()
        files = _iter_files(paths)
        results: dict[int, FileResult] = {}
        work: list[tuple[int, Path, str]] = []
        siblings: dict[str, list[tuple[int, Path]]] = {}
        planned: set[str] = set()

        # Pre-classify before reporting discovery so the progress total and terminal events
        # count only remaining work; keep existing DB rows untouched until actually retried.
        for idx, path in enumerate(files):
            if path.is_symlink():  # a hostile symlink would index (and later export)
                results[idx] = FileResult(
                    path, Status.SKIPPED_UNSUPPORTED, "symlink — not followed"
                )
                continue
            if not path.exists():
                results[idx] = FileResult(path, Status.ERROR, "file not found")
                continue
            if reason := skip_reason(path):
                results[idx] = FileResult(path, Status.SKIPPED_UNSUPPORTED, reason)
                continue

            sha = _sha256(path)
            if known.get(sha) == Status.INDEXED:
                results[idx] = FileResult(path, Status.SKIPPED_DUPLICATE, "already indexed")
                continue
            if known.get(sha) == Status.EXTRACTION_FAILED and not retry_failed:
                # terminal per UC-01 (a slow OCR timeout must not repeat every run) unless
                # the student asks
                materials = self.store.find_materials(sha)
                error = materials[0]["error"] if materials else "unknown error"
                results[idx] = FileResult(
                    path,
                    Status.SKIPPED_FAILED,
                    f"failed previously: {error} — re-run with --retry-failed",
                )
                continue
            if (
                sha in planned
            ):  # extract each content once; same-content siblings follow its outcome
                siblings.setdefault(sha, []).append((idx, path))
                continue
            planned.add(sha)
            work.append((idx, path, sha))

        self.on_discovered(len(work))
        for idx, path, sha in work:
            self.on_event(path, "queued")
            if known.get(sha) == Status.EXTRACTION_FAILED:
                # the old row goes first because sha256 is UNIQUE; delete only right before retry
                # so an interrupted run preserves untouched failure rows
                for row in self.store.find_materials(sha):
                    self.store.remove_material(row["id"])
                del known[sha]

            result = self._index_one(path, sha, ocr_lang)
            results[idx] = result
            if result.status in (Status.INDEXED, Status.EXTRACTION_FAILED):
                known[sha] = result.status
            for sib_idx, sib_path in siblings.get(sha, ()):
                results[sib_idx] = _sibling_result(sib_path, result)
            with closing(self.store.connect()) as conn:
                sync_counts(conn, self.subject.manifest_path)

        return [results[i] for i in range(len(files))]

    def _index_one(self, path: Path, sha: str, ocr_lang: str | None) -> FileResult:
        self.on_event(path, "extracting")
        try:
            extraction = self.extractor.extract(path, ocr_lang=ocr_lang)
        except (
            ModelUnavailable
        ) as exc:  # transient (offline/uncached model): no row, next run retries
            self.on_event(path, Status.ERROR)
            return FileResult(path, Status.ERROR, f"extractor unavailable: {exc}")
        except ExtractionFailure as failure:
            try:
                self.store.add_extraction_failed(path.name, sha, str(failure))
            except sqlite3.IntegrityError:
                pass  # sha256 UNIQUE lost a race: a concurrent run recorded this content first
            self.on_event(path, Status.EXTRACTION_FAILED)
            return FileResult(path, Status.EXTRACTION_FAILED, str(failure))

        self.on_event(path, "embedding")
        try:
            stored_name = _copy_to_materials(path, sha, self.subject.materials_dir)
        except OSError as exc:  # transient (disk full, permissions): no row, next run retries
            self.on_event(path, Status.ERROR)
            return FileResult(path, Status.ERROR, f"copy to materials failed: {exc}")

        # Vectors stream lazily into the single per-file transaction (encode_stream runs
        # the model batch-by-batch), so peak RAM never holds the whole document's
        # embeddings. Embedding failure rolls the transaction back — no row, next run
        # retries; the already-copied material is reused by _copy_to_materials on retry.
        try:
            self.store.add_indexed(
                stored_name,
                sha,
                extraction.pages,
                extraction.chunks,
                self.embedder.encode_stream([c.text for c in extraction.chunks]),
            )
        except sqlite3.IntegrityError:
            # sha256 UNIQUE lost a race: a concurrent run (CLI + MCP share the store)
            # indexed the same content between our hash check and this write
            self.on_event(path, Status.SKIPPED_DUPLICATE)
            return FileResult(path, Status.SKIPPED_DUPLICATE, "already indexed (concurrent run)")
        except Exception as exc:  # noqa: BLE001 - transient embed (load/OOM): no row, retried
            self.on_event(path, Status.ERROR)
            return FileResult(path, Status.ERROR, f"embedding failed: {exc}")
        self.on_event(path, Status.INDEXED)
        return FileResult(path, Status.INDEXED, chunks=len(extraction.chunks))


def index_paths(
    subject: str,
    paths: list[Path],
    embedder=None,
    on_event: OnEvent | None = None,
    on_discovered: Callable[[int], None] | None = None,
    ocr_lang: str | None = None,
    retry_failed: bool = False,
) -> list[FileResult]:
    subj = Subject(subject)
    pipeline = IngestionPipeline(
        subject=subj, embedder=embedder, on_event=on_event, on_discovered=on_discovered
    )
    return pipeline.run(paths, ocr_lang=ocr_lang, retry_failed=retry_failed)
