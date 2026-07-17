"""The index pipeline (UC-01): hash-skip idempotent, per-file transactions, the run
continues past failures. Ingestion writes the stores; it never serves queries."""

import hashlib
import shutil
import sqlite3
from pathlib import Path


from groundly.core.manifest import sync_counts
from groundly.core.subject import Subject
from groundly.core.store import SQLiteSubjectStore
from groundly.ingestion.extract import ExtractionFailure, ModelUnavailable, SubprocessExtractor
from groundly.ingestion.formats import SUPPORTED_SUFFIXES
from groundly.ingestion.results import FileResult, OnEvent, Status
from groundly.llm.embeddings import BgeM3Embedder, Embedder


def _iter_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.is_file()))
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
    ) -> None:
        self.subject = subject
        self.store = store or SQLiteSubjectStore(subject.store_db_path)
        self.extractor = extractor or SubprocessExtractor()
        self.embedder = embedder or BgeM3Embedder()
        self.on_event = on_event or (lambda path, stage: None)

    def run(self, paths: list[Path], ocr_lang: str | None = None) -> list[FileResult]:
        if not self.subject.exists():
            raise RuntimeError(
                f"subject '{self.subject.name}' is not initialized — run: groundly init {self.subject.name}"
            )

        results: list[FileResult] = []
        known = self.store.hash_status()
        for path in _iter_files(paths):
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
            dense, sparse = self.embedder.encode([c.text for c in extraction.chunks])
        except Exception as exc:  # transient (model load/OOM): no row, next run retries
            self.on_event(path, Status.ERROR)
            return FileResult(path, Status.ERROR, f"embedding failed: {exc}")

        try:
            stored_name = _copy_to_materials(path, sha, self.subject.materials_dir)
        except OSError as exc:  # transient (disk full, permissions): no row, next run retries
            self.on_event(path, Status.ERROR)
            return FileResult(path, Status.ERROR, f"copy to materials failed: {exc}")

        try:
            self.store.add_indexed(
                stored_name, sha, extraction.pages, extraction.chunks, dense, sparse
            )
            self.on_event(path, Status.INDEXED)
            return FileResult(path, Status.INDEXED, chunks=len(extraction.chunks))
        except sqlite3.IntegrityError:
            # sha256 UNIQUE lost a race: a concurrent run (CLI + MCP share the store)
            # indexed the same content between our hash check and this write
            self.on_event(path, Status.SKIPPED_DUPLICATE)
            return FileResult(path, Status.SKIPPED_DUPLICATE, "already indexed (concurrent run)")


def index_paths(
    subject: str,
    paths: list[Path],
    embedder: Embedder | None = None,
    on_event: OnEvent | None = None,
    ocr_lang: str | None = None,
) -> list[FileResult]:
    subj = Subject(subject)
    pipeline = IngestionPipeline(subject=subj, embedder=embedder, on_event=on_event)
    return pipeline.run(paths, ocr_lang=ocr_lang)
