"""Export/import zip bundles (UC-30, docs/architecture/data-model.md).

Export zips a fixed allowlist (manifest.json, store.db, materials/**), never a
directory walk. The private study-state file is never opened, referenced or named here.
"""

import re
import stat
import zipfile
from collections.abc import Callable
from contextlib import closing
from pathlib import Path, PurePosixPath

import sqlite_vec
from pydantic import ValidationError

from groundly.core import store
from groundly.core.manifest import FORMAT_VERSION, Manifest, store_counts

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
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_UNSAFE_FILENAME_CHARS = re.compile(r"[/\\\x00]")  # backslash separates paths on Windows


class BundleError(RuntimeError):
    """Every failure names its specific cause; the CLI maps this to _fail."""


def material_files(subj) -> list[Path]:
    """The materials a bundle ships: only files store.db records as indexed. A materials/
    file with no row (copied before a failed embed) is an un-indexed original and must not
    leak (security.md §5)."""
    conn = store.connect(subj.store_db_path)
    try:
        indexed = {r["filename"] for r in conn.execute("SELECT filename FROM materials")}
    finally:
        conn.close()
    return [f for f in sorted(subj.materials_dir.rglob("*")) if f.is_file() and f.name in indexed]


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
    finally:
        conn.close()

    entries: list[tuple[Path, str]] = [
        (subj.manifest_path, "manifest.json"),
        (subj.store_db_path, "store.db"),
    ]
    if include_materials:
        for f in material_files(subj):
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


def check_counts(store_db_path: Path, manifest: Manifest) -> None:
    """Opens via store.connect (runs the user_version refusal); compares manifest
    counts to actual row counts."""
    with closing(store.connect(store_db_path)) as conn:
        found = store_counts(conn)
    if found != manifest.counts:
        raise BundleError("bundle is damaged — manifest counts do not match store.db contents")


def check_store_shape(store_db_path: Path) -> None:
    """Refuse a store holding what groundly could not have written: a view or trigger it does
    not ship, which would answer these checks with one thing and later reads with another, or
    a row outside a field's fixed shape. A filename is joined to the materials dir, so it must
    stay one path component. Free text (`error`, chunk text, heading paths, and any other
    character the filesystem allows in a filename) is made inert where it is shown. This is
    the first open, so an older store is upgraded here."""
    try:
        conn = store.connect(store_db_path)
    except store.SchemaUpgradeError as exc:
        reason = (
            "it lacks a table groundly writes"
            if "no such table" in str(exc)
            else "it holds tables groundly did not write"
        )
        raise BundleError(f"bundle store.db could not be upgraded — {reason}") from exc
    try:
        if store.programs(conn) != store.shipped_programs():
            raise BundleError("bundle store.db holds views or triggers groundly does not ship")
        problem = store.schema_problem(conn)
        if problem:
            raise BundleError(
                f"bundle store.db holds {problem}, which groundly does not ship"
                " — re-index the subject on the source machine and export it again"
            )
        for row in conn.execute("SELECT id, filename, sha256, status, pages, error FROM materials"):
            problem = _material_problem(row)
            if problem:
                raise BundleError(f"bundle material {row['id']!r} has {problem} — rejected")
        row = conn.execute(
            "SELECT id FROM chunks WHERE typeof(page) NOT IN ('integer', 'null') LIMIT 1"
        ).fetchone()
        if row:
            raise BundleError(f"bundle chunk {row['id']!r} has a page that is not a number")
        row = conn.execute(
            "SELECT v.rowid AS id FROM vectors v JOIN chunks c ON c.id = v.rowid "
            "WHERE v.material_id IS NOT c.material_id LIMIT 1"
        ).fetchone()
        if row:
            raise BundleError(
                f"bundle vector {row['id']!r} is filed under a different material than its "
                "chunk — rejected"
            )
    finally:
        conn.close()


def _material_problem(row) -> str | None:
    name = row["filename"]
    if not isinstance(name, str) or name in ("", ".", "..") or _UNSAFE_FILENAME_CHARS.search(name):
        return "an unsafe filename"
    if not isinstance(row["sha256"], str) or not _SHA256_HEX.fullmatch(row["sha256"]):
        return "a sha256 that is not 64 hex digits"
    if row["status"] not in ("indexed", "extraction_failed"):
        return "an unknown status"
    if not (row["pages"] is None or isinstance(row["pages"], int)):
        return "a page count that is not a number"
    if not (row["error"] is None or isinstance(row["error"], str)):
        return "an error that is not text"
    return None


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
                "on the source machine, re-index the subject, then re-export it"
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


_QUESTION_TABLES = {"questions", "question_options", "question_citations"}

# (column checked, table its value must exist in)
_QUESTION_REFERENCES = [
    ("questions", "topic_id", "topics"),
    ("question_options", "question_id", "questions"),
    ("question_citations", "question_id", "questions"),
    ("question_citations", "chunk_id", "chunks"),
]


def check_questions(store_db_path: Path) -> None:
    """Refuse a question bank the question gate would not have stored. An import never passes
    through the gate, so its structure checks and limits are repeated here; grounding is not,
    since import loads no model. Runs after `check_tree`, so the tree is already sound."""
    conn = store.connect(store_db_path)
    # The gate's own notion of blank: SQL TRIM strips spaces only, Python strips all whitespace.
    conn.create_function(
        "stripped_len",
        1,
        lambda v: len(v.strip()) if isinstance(v, str) else None,
        deterministic=True,
    )
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not _QUESTION_TABLES <= tables:
            raise BundleError("bundle has no question bank tables — rejected")
        for table, column, parent in _QUESTION_REFERENCES:
            if conn.execute(
                f"SELECT 1 FROM {table} x LEFT JOIN {parent} p ON p.id = x.{column} "
                f"WHERE p.id IS NULL LIMIT 1"
            ).fetchone():
                raise BundleError(
                    f"bundle is damaged — {table}.{column} has a value with no matching {parent}"
                )
        _check_question_counts(conn)
        _check_bank_cost(conn)
        for row in conn.execute(
            """
            SELECT q.id, q.kind,
                   typeof(q.stem) AS stem_type, stripped_len(q.stem) AS stem_trim,
                   LENGTH(q.stem) AS stem_len,
                   typeof(q.code) AS code_type, LENGTH(q.code) AS code_len,
                   stripped_len(q.code) AS code_trim,
                   typeof(q.reference_answer) AS ref_type, stripped_len(q.reference_answer) AS ref_trim,
                   LENGTH(q.reference_answer) AS ref_len,
                   EXISTS (SELECT 1 FROM topics t WHERE t.parent_id = q.topic_id) AS on_parent,
                   (SELECT COUNT(*) FROM question_citations c WHERE c.question_id = q.id) AS cites,
                   (SELECT COUNT(*) FROM question_options o WHERE o.question_id = q.id) AS options,
                   (SELECT COUNT(*) FROM question_options o
                     WHERE o.question_id = q.id AND o.correct = 1) AS correct,
                   (SELECT COUNT(*) FROM question_options o
                     WHERE o.question_id = q.id AND NOT (typeof(o.correct) = 'integer'
                       AND o.correct IN (0, 1))) AS bad_flags,
                   (SELECT COUNT(*) FROM question_options o
                     WHERE o.question_id = q.id AND typeof(o.text) != 'text') AS untyped_options,
                   (SELECT MAX(LENGTH(o.text)) FROM question_options o
                     WHERE o.question_id = q.id) AS longest_option,
                   (SELECT MIN(stripped_len(o.text)) FROM question_options o
                     WHERE o.question_id = q.id) AS shortest_option
            FROM questions q ORDER BY q.id
            """
        ):
            problem = _question_problem(row)
            if problem:
                raise BundleError(f"bundle question {row['id']} {problem} — rejected")
        _check_duplicates(conn)
    finally:
        conn.close()


def _check_duplicates(conn) -> None:
    seen: set[tuple[int, str]] = set()
    for r in conn.execute("SELECT question_id, text FROM question_options ORDER BY id"):
        key = (r[0], store.text_key(r[1]))
        if key in seen:
            raise BundleError(
                f"bundle question {r[0]} has two options with the same text — rejected"
            )
        seen.add(key)
    seen.clear()
    for r in conn.execute("SELECT topic_id, stem FROM questions ORDER BY id"):
        key = (r[0], store.text_key(r[1]))
        if key in seen:
            raise BundleError(
                f"bundle topic {r[0]} has two questions with the same stem — rejected"
            )
        seen.add(key)


def _check_bank_cost(conn) -> None:
    """A whole-course draw loads every cited chunk's text, as `tree()` does for the map."""
    cited = conn.execute(
        "SELECT SUM(LENGTH(c.text)) FROM question_citations qc JOIN chunks c ON c.id = qc.chunk_id"
    ).fetchone()[0]
    if cited and cited > store.MAX_BANK_SOURCE_BYTES:
        raise BundleError(
            f"bundle questions cite {cited} bytes of chunk text; the limit is "
            f"{store.MAX_BANK_SOURCE_BYTES} — rejected"
        )


def _check_question_counts(conn) -> None:
    row = conn.execute(
        "SELECT topic_id, COUNT(*) FROM questions GROUP BY topic_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    if row and row[1] > store.MAX_QUESTIONS_PER_TOPIC:
        raise BundleError(
            f"bundle topic {row[0]} holds {row[1]} questions; the limit is "
            f"{store.MAX_QUESTIONS_PER_TOPIC} — rejected"
        )


def _text_problem(name: str, kind: str, length: int | None, trimmed: int | None, limit: int):
    if kind != "text":
        return f"has a {name} that is not text"
    if not trimmed:
        return f"has an empty {name}"
    assert length is not None  # a text value always has a length
    if length > limit:
        return f"has a {name} of {length} characters; the limit is {limit}"
    return None


def _question_problem(row) -> str | None:
    kind = row["kind"]
    if kind not in ("single", "multiple", "open"):
        return "has an unknown kind"
    if row["on_parent"]:
        return "is on a topic that has subtopics"
    problem = _text_problem(
        "stem", row["stem_type"], row["stem_len"], row["stem_trim"], store.MAX_STEM_CHARS
    )
    if problem:
        return problem
    if row["code_type"] != "null":
        if kind == "open":
            return "is an open question with code"
        if row["code_type"] != "text":
            return "has code that is not text"
        if not row["code_trim"]:
            return "has empty code"
        if row["code_len"] > store.MAX_CODE_CHARS:
            return f"has code of {row['code_len']} characters; the limit is {store.MAX_CODE_CHARS}"
    if not row["cites"]:
        return "cites no chunk"
    if row["cites"] > store.MAX_QUESTION_CITATIONS:
        return f"cites {row['cites']} chunks; the limit is {store.MAX_QUESTION_CITATIONS}"
    if kind == "open":
        if row["options"]:
            return "is an open question with options"
        return _text_problem(
            "reference answer",
            row["ref_type"],
            row["ref_len"],
            row["ref_trim"],
            store.MAX_REFERENCE_CHARS,
        )
    if row["ref_type"] != "null":
        return f"is a {kind} question with a reference answer"
    if row["bad_flags"] or row["untyped_options"]:
        return "has an option that is not text with a true or false flag"
    if not row["shortest_option"]:
        return "has an empty option"
    if row["longest_option"] > store.MAX_OPTION_CHARS:
        return (
            f"has an option of {row['longest_option']} characters; the limit is "
            f"{store.MAX_OPTION_CHARS}"
        )
    fewest, most, fewest_correct = store.QUESTION_SHAPES[kind]
    if not fewest <= row["options"] <= most:
        return f"has {row['options']} options; a {kind} question needs {fewest} to {most}"
    if kind == "single" and row["correct"] != 1:
        return f"has {row['correct']} correct options; a single question needs exactly one correct option"
    if kind == "multiple" and not (fewest_correct <= row["correct"] < row["options"]):
        return (
            f"has {row['correct']} correct of {row['options']} options; a multiple question "
            "needs at least two correct and one wrong"
        )
    return None


def re_embed(store_db_path: Path, embedder, on_step: OnStep | None = None) -> None:
    """One transaction: drop vectors + sparse_terms, batch chunk text through embedder,
    reinsert at rowid = chunk id. Chunk text and FTS are untouched."""
    conn = store.connect(store_db_path)
    try:
        with conn:
            conn.execute("DELETE FROM vectors")
            conn.execute("DELETE FROM sparse_terms")
            rows = conn.execute("SELECT id, material_id, text FROM chunks ORDER BY id").fetchall()
            total = len(rows)
            for i in range(0, total, _REEMBED_BATCH):
                batch = rows[i : i + _REEMBED_BATCH]
                dense, sparse = embedder.encode([r["text"] for r in batch])
                for row, vec, weights in zip(batch, dense, sparse, strict=True):
                    conn.execute(
                        "INSERT INTO vectors (rowid, embedding, material_id) VALUES (?, ?, ?)",
                        (row["id"], sqlite_vec.serialize_float32(vec), row["material_id"]),
                    )
                    conn.executemany(
                        "INSERT INTO sparse_terms (token_id, chunk_id, weight) VALUES (?, ?, ?)",
                        [(token_id, row["id"], weight) for token_id, weight in weights.items()],
                    )
                if on_step:
                    on_step(min(i + _REEMBED_BATCH, total), total)
    finally:
        conn.close()
