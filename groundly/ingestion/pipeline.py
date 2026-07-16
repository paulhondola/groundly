"""The index pipeline (UC-01): hash-skip idempotent, per-file transactions, the run
continues past failures. Ingestion writes the stores; it never serves queries."""

import hashlib
import shutil
import sqlite3
from pathlib import Path

import sqlite_vec

from groundly.core import store
from groundly.core.manifest import sync_counts
from groundly.core.paths import subject_dir
from groundly.ingestion.extract import ExtractionFailure, ModelUnavailable, extract
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


def index_paths(
    subject: str,
    paths: list[Path],
    embedder: Embedder | None = None,
    on_event: OnEvent | None = None,
) -> list[FileResult]:
    sdir = subject_dir(subject)
    manifest_path = sdir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"subject '{subject}' is not initialized — run: groundly init {subject}")

    emit = on_event or (lambda path, stage: None)
    embedder = embedder or BgeM3Embedder()
    results: list[FileResult] = []
    conn = store.connect(sdir / "store.db")
    try:
        known = store.hash_status(conn)
        for path in _iter_files(paths):
            emit(path, "queued")
            if path.is_symlink():  # a hostile symlink would index (and later export)
                results.append(
                    FileResult(path, Status.SKIPPED_UNSUPPORTED, "symlink — not followed")
                )
                emit(path, Status.SKIPPED_UNSUPPORTED)
                continue
            if not path.exists():
                results.append(FileResult(path, Status.ERROR, "file not found"))
                emit(path, Status.ERROR)
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                results.append(
                    FileResult(
                        path, Status.SKIPPED_UNSUPPORTED, f"unsupported type {path.suffix!r}"
                    )
                )
                emit(path, Status.SKIPPED_UNSUPPORTED)
                continue

            sha = _sha256(path)
            if known.get(sha) == Status.INDEXED:
                results.append(FileResult(path, Status.SKIPPED_DUPLICATE, "already indexed"))
                emit(path, Status.SKIPPED_DUPLICATE)
                continue
            if known.get(sha) == Status.EXTRACTION_FAILED:
                # terminal per UC-01: don't re-extract every run; `remove` it to retry
                error = store.find_materials(conn, sha)[0]["error"]
                results.append(
                    FileResult(
                        path, Status.SKIPPED_FAILED, f"failed previously: {error} — remove to retry"
                    )
                )
                emit(path, Status.SKIPPED_FAILED)
                continue

            result = _index_one(conn, path, sha, sdir, embedder, emit)
            results.append(result)
            if result.status in (Status.INDEXED, Status.EXTRACTION_FAILED):
                known[sha] = result.status  # a same-content sibling this run is a skip
            sync_counts(conn, manifest_path)
    finally:
        conn.close()
    return results


def _index_one(
    conn, path: Path, sha: str, sdir: Path, embedder: Embedder, emit: OnEvent
) -> FileResult:
    emit(path, "extracting")
    try:
        extraction = extract(path)
    except ModelUnavailable as exc:  # transient (offline/uncached model): no row, next run retries
        emit(path, Status.ERROR)
        return FileResult(path, Status.ERROR, f"extractor unavailable: {exc}")
    except ExtractionFailure as failure:
        try:
            with conn:
                conn.execute(
                    "INSERT INTO materials (filename, sha256, status, error) "
                    "VALUES (?, ?, 'extraction_failed', ?)",
                    (path.name, sha, str(failure)),
                )
        except sqlite3.IntegrityError:
            pass  # sha256 UNIQUE lost a race: a concurrent run recorded this content first
        emit(path, Status.EXTRACTION_FAILED)
        return FileResult(path, Status.EXTRACTION_FAILED, str(failure))

    emit(path, "embedding")
    try:
        dense, sparse = embedder.encode([c.text for c in extraction.chunks])
    except Exception as exc:  # transient (model load/OOM): no row, next run retries
        emit(path, Status.ERROR)
        return FileResult(path, Status.ERROR, f"embedding failed: {exc}")

    try:
        stored_name = _copy_to_materials(path, sha, sdir / "materials")
    except OSError as exc:  # transient (disk full, permissions): no row, next run retries
        emit(path, Status.ERROR)
        return FileResult(path, Status.ERROR, f"copy to materials failed: {exc}")
    try:
        return _write_indexed(conn, path, sha, stored_name, extraction, dense, sparse, emit)
    except sqlite3.IntegrityError:
        # sha256 UNIQUE lost a race: a concurrent run (CLI + MCP share the store)
        # indexed the same content between our hash check and this write
        emit(path, Status.SKIPPED_DUPLICATE)
        return FileResult(path, Status.SKIPPED_DUPLICATE, "already indexed (concurrent run)")


def _write_indexed(
    conn, path: Path, sha: str, stored_name: str, extraction, dense, sparse, emit: OnEvent
) -> FileResult:
    with conn:  # one transaction per file: Ctrl-C loses at most the in-flight file
        cur = conn.execute(
            "INSERT INTO materials (filename, sha256, status, pages) VALUES (?, ?, 'indexed', ?)",
            (stored_name, sha, extraction.pages),
        )
        material_id = cur.lastrowid
        for chunk, vec, weights in zip(extraction.chunks, dense, sparse, strict=True):
            cid = conn.execute(
                "INSERT INTO chunks (material_id, page, heading_path, text, token_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (material_id, chunk.page, chunk.heading_path, chunk.text, chunk.token_count),
            ).lastrowid
            conn.execute(
                "INSERT INTO vectors (rowid, embedding) VALUES (?, ?)",
                (cid, sqlite_vec.serialize_float32(vec)),
            )
            conn.executemany(
                "INSERT INTO sparse_terms (token_id, chunk_id, weight) VALUES (?, ?, ?)",
                [(token_id, cid, weight) for token_id, weight in weights.items()],
            )
    emit(path, Status.INDEXED)
    return FileResult(path, Status.INDEXED, chunks=len(extraction.chunks))
