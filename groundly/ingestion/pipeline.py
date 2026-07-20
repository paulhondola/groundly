"""The index pipeline (UC-01): hash-skip idempotent, per-file transactions, the run
continues past failures. Ingestion writes the stores; it never serves queries."""

import fnmatch
import hashlib
import os
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path


from groundly.core.manifest import sync_counts
from groundly.core.subject import Subject
from groundly.core.store import SQLiteSubjectStore
from groundly.ingestion.extract import ExtractionFailure, ModelUnavailable, SubprocessExtractor
from groundly.ingestion.formats import SUPPORTED_SUFFIXES
from groundly.ingestion.results import FileResult, OnEvent, Status
from groundly.llm.embeddings import BgeM3Embedder, Embedder


def _default_extractor() -> SubprocessExtractor:
    from groundly.core.config import load_settings

    s = load_settings().ingestion
    return SubprocessExtractor(
        timeout=s.timeout_seconds,
        max_image_pixels=s.max_image_pixels,
        max_file_size_mb=s.max_file_size_mb,
    )


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
    patterns = []
    for line in ignore_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


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


class IngestionPipeline:
    """Orchestrates indexing of documents: extraction, embedding, and storage."""

    def __init__(
        self,
        subject: Subject,
        store: SQLiteSubjectStore | None = None,
        extractor: SubprocessExtractor | None = None,
        embedder: Embedder | None = None,
        on_event: OnEvent | None = None,
        on_discovered: Callable[[int], None] | None = None,
    ) -> None:
        self.subject = subject
        self.store = store or SQLiteSubjectStore(subject.store_db_path)
        self.extractor = extractor or _default_extractor()
        self.embedder = embedder or BgeM3Embedder()
        self.on_event = on_event or (lambda path, stage: None)
        self.on_discovered = on_discovered or (lambda total: None)

    def run(self, paths: list[Path], ocr_lang: str | None = None) -> list[FileResult]:
        if not self.subject.exists():
            raise RuntimeError(
                f"subject '{self.subject.name}' is not initialized — run: groundly init {self.subject.name}"
            )

        results: list[FileResult] = []
        known = self.store.hash_status()
        files = _iter_files(paths)
        self.on_discovered(len(files))
        for path in files:
            self.on_event(path, "queued")
            if path.is_symlink():  # a hostile symlink would index (and later export)
                results.append(
                    FileResult(path, Status.SKIPPED_UNSUPPORTED, "symlink — not followed")
                )
                self.on_event(path, Status.SKIPPED_UNSUPPORTED)
                continue
            if not path.exists():
                results.append(FileResult(path, Status.ERROR, "file not found"))
                self.on_event(path, Status.ERROR)
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                results.append(
                    FileResult(
                        path, Status.SKIPPED_UNSUPPORTED, f"unsupported type {path.suffix!r}"
                    )
                )
                self.on_event(path, Status.SKIPPED_UNSUPPORTED)
                continue

            sha = _sha256(path)
            if known.get(sha) == Status.INDEXED:
                results.append(FileResult(path, Status.SKIPPED_DUPLICATE, "already indexed"))
                self.on_event(path, Status.SKIPPED_DUPLICATE)
                continue
            if known.get(sha) == Status.EXTRACTION_FAILED:
                # terminal per UC-01: don't re-extract every run; `remove` it to retry
                materials = self.store.find_materials(sha)
                error = materials[0]["error"] if materials else "unknown error"
                results.append(
                    FileResult(
                        path, Status.SKIPPED_FAILED, f"failed previously: {error} — remove to retry"
                    )
                )
                self.on_event(path, Status.SKIPPED_FAILED)
                continue

            result = self._index_one(path, sha, ocr_lang)
            results.append(result)
            if result.status in (Status.INDEXED, Status.EXTRACTION_FAILED):
                known[sha] = result.status  # a same-content sibling this run is a skip
            conn = self.store.connect()
            try:
                sync_counts(conn, self.subject.manifest_path)
            finally:
                conn.close()
        return results

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
        except Exception as exc:  # transient embed (model load/OOM): no row, next run retries
            self.on_event(path, Status.ERROR)
            return FileResult(path, Status.ERROR, f"embedding failed: {exc}")
        self.on_event(path, Status.INDEXED)
        return FileResult(path, Status.INDEXED, chunks=len(extraction.chunks))


def index_paths(
    subject: str,
    paths: list[Path],
    embedder: Embedder | None = None,
    on_event: OnEvent | None = None,
    on_discovered: Callable[[int], None] | None = None,
    ocr_lang: str | None = None,
) -> list[FileResult]:
    subj = Subject(subject)
    pipeline = IngestionPipeline(
        subject=subj, embedder=embedder, on_event=on_event, on_discovered=on_discovered
    )
    return pipeline.run(paths, ocr_lang=ocr_lang)
