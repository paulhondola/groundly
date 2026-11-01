"""Export/import zip bundles (UC-30, docs/architecture/data-model.md).

Export zips a fixed allowlist (manifest.json, store.db, materials/**), never a
directory walk. The private study-state file is never opened, referenced or named here.
"""

import stat
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import sqlite_vec
from pydantic import ValidationError

from groundly.core import store
from groundly.core.manifest import FORMAT_VERSION, Embedding, Manifest

OnFile = Callable[[str], None]
OnStep = Callable[[int, int], None]  # re_embed callback: (done, total) chunks re-embedded so far

_ALLOWED_TOP = {"manifest.json", "store.db"}
# Bundles from before the graph was dropped still carry graph/: accepted by the allowlist
# (so they import), never extracted.
_IGNORED_PREFIX = "graph/"
_ALLOWED_PREFIXES = ("materials/", _IGNORED_PREFIX)
_REEMBED_BATCH = 32
_MANIFEST_MAX_BYTES = 1024 * 1024  # 1 MB; a real manifest is ~600 bytes
_BUNDLE_MAX_BYTES = 20 * 1024**3  # 20 GiB declared uncompressed total


class BundleError(RuntimeError):
    """Every failure names its specific cause; the CLI maps this to _fail."""


def export_subject(
    subj,
    out_path: Path,
    include_materials: bool = True,
    on_file: OnFile | None = None,
) -> None:
    """Zip subj's allowlisted files to out_path."""
    conn = store.connect(subj.store_db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # flush WAL sidecars before zipping
        indexed = {r["filename"] for r in conn.execute("SELECT filename FROM materials")}
    finally:
        conn.close()

    entries: list[tuple[Path, str]] = [
        (subj.manifest_path, "manifest.json"),
        (subj.store_db_path, "store.db"),
    ]
    if include_materials:
        # Ship only files store.db records as indexed: a materials/ file with no row (copied
        # before a failed embed) is an un-indexed original and must not leak (security.md §5).
        for f in sorted(subj.materials_dir.rglob("*")):
            if f.is_file() and f.name in indexed:
                entries.append((f, f"materials/{f.relative_to(subj.materials_dir).as_posix()}"))
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in entries:
            zf.write(src, arcname)
            if on_file:
                on_file(arcname)


def read_manifest(zf: zipfile.ZipFile) -> Manifest:
    """Validate and return the bundle's manifest. Nothing else is read."""
    try:
        info = zf.getinfo("manifest.json")
    except KeyError:
        raise BundleError("bundle is missing manifest.json — not a groundly bundle") from None
    if info.file_size > _MANIFEST_MAX_BYTES:
        raise BundleError(
            f"bundle manifest.json declares {info.file_size} bytes — too large for a "
            "groundly manifest, rejected"
        )
    raw = zf.read("manifest.json")
    try:
        manifest = Manifest.model_validate_json(raw)
    except ValidationError as exc:
        raise BundleError(f"manifest.json is invalid: {exc}") from exc
    if manifest.format_version > FORMAT_VERSION:
        raise BundleError(
            f"bundle format_version {manifest.format_version} was created by a newer "
            "groundly — upgrade"
        )
    if manifest.counts.materials < 0 or manifest.counts.chunks < 0:
        raise BundleError("manifest.json has negative counts — bundle is damaged")
    return manifest


def validate_entries(zf: zipfile.ZipFile) -> None:
    """Zip-slip gate: reject, never sanitize. Also blocks anything outside the
    export allowlist, so a smuggled non-allowlisted file cannot be extracted."""
    total_size = 0
    for info in zf.infolist():
        name = info.filename
        posix = PurePosixPath(name)
        if posix.is_absolute() or "\\" in name or (len(name) > 1 and name[1] == ":"):
            raise BundleError(f"bundle entry {name!r} is an unsafe path — rejected")
        if ".." in posix.parts:
            raise BundleError(f"bundle entry {name!r} escapes the bundle root — rejected")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise BundleError(f"bundle entry {name!r} is a symlink — rejected")
        if name not in _ALLOWED_TOP and not name.startswith(_ALLOWED_PREFIXES):
            raise BundleError(f"bundle entry {name!r} is outside the export allowlist — rejected")
        total_size += info.file_size
    if total_size > _BUNDLE_MAX_BYTES:
        raise BundleError(
            f"bundle declares {total_size} bytes uncompressed — over the "
            f"{_BUNDLE_MAX_BYTES} byte cap, rejected"
        )


def extract_bundle(bundle_path: Path, dest_dir: Path, on_file: OnFile | None = None) -> Manifest:
    """Validate manifest + every entry before anything touches disk, then extract."""
    with zipfile.ZipFile(bundle_path) as zf:
        manifest = read_manifest(zf)
        validate_entries(zf)
        for info in zf.infolist():
            if info.filename.startswith(_IGNORED_PREFIX):
                continue
            zf.extract(info, dest_dir)
            if on_file:
                on_file(info.filename)
    return manifest


def pin_matches(manifest: Manifest) -> bool:
    return manifest.embedding == Embedding()


def check_counts(store_db_path: Path, manifest: Manifest) -> None:
    """Opens via store.connect (runs the user_version refusal); compares manifest
    counts to actual row counts."""
    conn = store.connect(store_db_path)
    try:
        materials = conn.execute(
            "SELECT COUNT(*) FROM materials WHERE status = 'indexed'"
        ).fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()
    if materials != manifest.counts.materials or chunks != manifest.counts.chunks:
        raise BundleError("bundle is damaged — manifest counts do not match store.db contents")


def check_tree(store_db_path: Path) -> None:
    """Refuse a store whose topic tree is absent, damaged or too costly to load. Foreign
    keys are enforced on write, so a violation here means the file was edited outside
    groundly; the anti-joins repeat them because a hostile file declares only the keys it
    likes, and the limits repeat the gate's because an import never passes through it."""
    conn = store.connect(store_db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"topics", "topic_citations"} <= tables:
            raise BundleError(
                "bundle has no topic tree tables — it was exported by an older groundly; "
                "re-export it from the source machine"
            )
        dangling = conn.execute("PRAGMA foreign_key_check").fetchall()
        if dangling:
            found = ", ".join(
                f"{r['table']!r} row {r['rowid']} has no matching {r['parent']!r}"
                for r in dangling[:5]
            )
            raise BundleError(f"bundle is damaged — {len(dangling)} dangling reference(s): {found}")
        for table, column, parent in _TREE_REFERENCES:
            if conn.execute(
                f"SELECT 1 FROM {table} x LEFT JOIN {parent} p ON p.id = x.{column} "
                f"WHERE x.{column} IS NOT NULL AND p.id IS NULL LIMIT 1"
            ).fetchone():
                raise BundleError(
                    f"bundle is damaged — {table}.{column} has a value with no matching {parent}"
                )
        if conn.execute(
            "SELECT 1 FROM topics t JOIN topics p ON p.id = t.parent_id "
            "WHERE p.parent_id IS NOT NULL LIMIT 1"
        ).fetchone():
            raise BundleError("bundle is damaged — its topic tree is nested more than two levels")
        _check_tree_cost(conn)
    finally:
        conn.close()


# (column checked, table its value must exist in)
_TREE_REFERENCES = [
    ("topic_citations", "topic_id", "topics"),
    ("topic_citations", "chunk_id", "chunks"),
    ("topics", "parent_id", "topics"),
]


def _check_tree_cost(conn) -> None:
    nodes = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    if nodes > store.MAX_TREE_NODES:
        raise BundleError(
            f"bundle has {nodes} topics; the limit is {store.MAX_TREE_NODES} — rejected"
        )
    row = conn.execute(
        "SELECT topic_id, COUNT(*) FROM topic_citations GROUP BY topic_id "
        "ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    if row and row[1] > store.MAX_SOURCES:
        raise BundleError(
            f"bundle topic {row[0]} cites {row[1]} sources; the limit is {store.MAX_SOURCES}"
            " — rejected"
        )
    for column, limit in (("title", store.MAX_TITLE_CHARS), ("summary", store.MAX_SUMMARY_CHARS)):
        row = conn.execute(
            f"SELECT id, typeof({column}), LENGTH({column}) FROM topics "
            f"WHERE typeof({column}) != 'text' OR LENGTH({column}) > ? LIMIT 1",
            (limit,),
        ).fetchone()
        if row and row[1] != "text":
            raise BundleError(f"bundle topic {row[0]} has a {column} that is not text — rejected")
        if row:
            raise BundleError(
                f"bundle topic {row[0]} has a {column} of {row[2]} characters; "
                f"the limit is {limit} — rejected"
            )
    cited = conn.execute(
        "SELECT SUM(LENGTH(c.text)) FROM topic_citations tc JOIN chunks c ON c.id = tc.chunk_id"
    ).fetchone()[0]
    if cited and cited > store.MAX_TREE_SOURCE_BYTES:
        raise BundleError(
            f"bundle topics cite {cited} bytes of chunk text; the limit is "
            f"{store.MAX_TREE_SOURCE_BYTES} — rejected"
        )


def re_embed(store_db_path: Path, embedder, on_step: OnStep | None = None) -> None:
    """One transaction: drop vectors + sparse_terms, batch chunk text through embedder,
    reinsert at rowid = chunk id. Chunk text and FTS are untouched."""
    conn = store.connect(store_db_path)
    try:
        with conn:
            conn.execute("DELETE FROM vectors")
            conn.execute("DELETE FROM sparse_terms")
            rows = conn.execute("SELECT id, text FROM chunks ORDER BY id").fetchall()
            total = len(rows)
            for i in range(0, total, _REEMBED_BATCH):
                batch = rows[i : i + _REEMBED_BATCH]
                dense, sparse = embedder.encode([r["text"] for r in batch])
                for row, vec, weights in zip(batch, dense, sparse, strict=True):
                    conn.execute(
                        "INSERT INTO vectors (rowid, embedding) VALUES (?, ?)",
                        (row["id"], sqlite_vec.serialize_float32(vec)),
                    )
                    conn.executemany(
                        "INSERT INTO sparse_terms (token_id, chunk_id, weight) VALUES (?, ?, ?)",
                        [(token_id, row["id"], weight) for token_id, weight in weights.items()],
                    )
                if on_step:
                    on_step(min(i + _REEMBED_BATCH, total), total)
    finally:
        conn.close()
