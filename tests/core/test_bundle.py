"""UC-30 export/import. Seeds store.db directly via SubjectStore.add_indexed +
sync_counts (no pipeline); real SQLite; StubEmbedder injected for the re-embed path."""

import json
import stat
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.core import bundle, store
from groundly.core.manifest import EMBEDDING_DIM, HF_REVISION, Manifest, sync_counts
from groundly.core.paths import groundly_home, subject_dir
from groundly.core.store import SubjectStore, TopicWrite
from groundly.core.subject import Subject
from groundly.ingestion.extract import ChunkData
from tests.conftest import init_subject

runner = CliRunner()


def _use_home(monkeypatch, path):
    path.mkdir(exist_ok=True)
    monkeypatch.setenv("GROUNDLY_HOME", str(path))
    return path


def _seed(name, filename="lec.pdf", content=b"lecture bytes", sha256="a" * 64, n_chunks=2):
    sdir = subject_dir(name)
    (sdir / "materials" / filename).write_bytes(content)
    chunks = [
        ChunkData(f"deadlock needs mutual exclusion {i}", "Intro > Motivation", i + 1, 10)
        for i in range(n_chunks)
    ]
    dense = [[0.1] * EMBEDDING_DIM for _ in chunks]
    sparse = [{1: 0.5} for _ in chunks]
    SubjectStore(sdir / "store.db").add_indexed(filename, sha256, 3, chunks, zip(dense, sparse))
    conn = store.connect(sdir / "store.db")
    try:
        sync_counts(conn, sdir / "manifest.json")
    finally:
        conn.close()


def _row_counts(store_db_path):
    conn = store.connect(store_db_path)
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("chunks", "vectors", "sparse_terms")
        }
    finally:
        conn.close()


def _zip_with_entry(path, entry_name, content=b"x", symlink=False):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", Manifest.new("PDSS").model_dump_json())
        if symlink:
            info = zipfile.ZipInfo(entry_name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
        else:
            zf.writestr(entry_name, content)
    return path


def _rewrite_zip_manifest(bundle_path, **embedding_overrides):
    with zipfile.ZipFile(bundle_path) as zf:
        contents = {name: zf.read(name) for name in zf.namelist()}
    manifest = json.loads(contents["manifest.json"])
    manifest["embedding"].update(embedding_overrides)
    contents["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in contents.items():
            zf.writestr(name, data)


# --- AC1: export on machine A, import on machine B -------------------------------


def test_export_import_roundtrip_preserves_data_and_resets_progress(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    (subject_dir("PDSS") / "progress.db").write_bytes(b"private study state")
    original_material = (subject_dir("PDSS") / "materials" / "lec.pdf").read_bytes()
    counts_a = _row_counts(subject_dir("PDSS") / "store.db")

    bundle_path = tmp_path / "PDSS.groundly"
    result = runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])
    assert result.exit_code == 0, result.output
    assert "everything indexed in this subject, including its topic map" in " ".join(
        result.output.split()
    )

    _use_home(monkeypatch, tmp_path / "b")
    result = runner.invoke(app, ["import", str(bundle_path)])
    assert result.exit_code == 0, result.output

    subj_b = Subject("PDSS")
    assert subj_b.exists()
    assert (subj_b.materials_dir / "lec.pdf").read_bytes() == original_material
    assert _row_counts(subj_b.store_db_path) == counts_a

    # fresh empty progress.db: never derived from the bundle's own (garbage) state
    assert subj_b.progress_db_path.read_bytes() != b"private study state"
    import sqlite3

    conn = sqlite3.connect(subj_b.progress_db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
    finally:
        conn.close()


def test_export_excludes_orphan_material_with_no_store_row(tmp_path, monkeypatch):
    """A materials/ file with no `materials` row (e.g. copied just before a failed embed)
    must NOT ship in the bundle: export ships what store.db knows is indexed, not whatever
    files sit in materials/."""
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")  # indexes lec.pdf (has a materials row)
    orphan = subject_dir("PDSS") / "materials" / "leaked-secret.pdf"
    orphan.write_bytes(b"un-indexed original that failed to embed")

    out_path = tmp_path / "PDSS.groundly"
    bundle.export_subject(Subject("PDSS"), out_path)

    names = zipfile.ZipFile(out_path).namelist()
    assert "materials/lec.pdf" in names  # indexed material still ships
    assert "materials/leaked-secret.pdf" not in names  # orphan does not leak


def test_export_never_ships_a_leftover_graph_dir(tmp_path, monkeypatch):
    """A subject indexed by a version that built graphs still has graph/ on disk; it is
    not part of the bundle."""
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    graph_dir = subject_dir("PDSS") / "graph"
    graph_dir.mkdir()
    (graph_dir / "entities.parquet").write_bytes(b"parquet data")

    out_path = tmp_path / "PDSS.groundly"
    bundle.export_subject(Subject("PDSS"), out_path)

    with zipfile.ZipFile(out_path) as zf:
        assert not any(n.startswith("graph/") for n in zf.namelist())


# --- AC2: embedding pin mismatch triggers re-embed --------------------------------


def test_import_pin_mismatch_confirm_yes_reembeds(tmp_path, monkeypatch, stub_embedder):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])
    _rewrite_zip_manifest(bundle_path, hf_revision="stale-revision")

    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", stub_embedder)
    result = runner.invoke(app, ["import", str(bundle_path), "--as", "PDSS_B"], input="y\n")
    assert result.exit_code == 0, result.output

    subj = Subject("PDSS_B")
    assert subj.load_manifest().embedding.hf_revision == HF_REVISION
    assert _row_counts(subj.store_db_path)["vectors"] == 2


def test_import_pin_mismatch_confirm_no_installs_nothing(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])
    _rewrite_zip_manifest(bundle_path, hf_revision="stale-revision")

    result = runner.invoke(app, ["import", str(bundle_path), "--as", "PDSS_C"], input="n\n")
    assert result.exit_code != 0
    assert not Subject("PDSS_C").exists()
    assert not any((groundly_home() / ".imports").glob("*"))


# --- Topic tree: import is the trust boundary ----------------------------------------


def _seed_tree(name, n_chunks=2):
    _seed(name, n_chunks=n_chunks)
    sub = [TopicWrite(None, "Conditions", "The four", [2])]
    SubjectStore(subject_dir(name) / "store.db").replace_tree(
        [TopicWrite(None, "Deadlock", "Why it happens", [1], sub)]
    )


def _tamper(name, *statements):
    """Raw SQL with foreign keys off (sqlite3's default), as a hostile bundle author has."""
    import sqlite3

    conn = sqlite3.connect(subject_dir(name) / "store.db")
    try:
        with conn:
            for sql in statements:
                conn.execute(sql)
    finally:
        conn.close()


def _export_then_import(tmp_path, monkeypatch, name="PDSS"):
    bundle_path = tmp_path / f"{name}.groundly"
    assert runner.invoke(app, ["export", name, "-o", str(bundle_path)]).exit_code == 0
    _use_home(monkeypatch, tmp_path / "b")
    return runner.invoke(app, ["import", str(bundle_path)])


def _assert_refused(result, *phrases):
    assert result.exit_code != 0
    text = " ".join(result.output.split())
    for phrase in phrases:
        assert phrase in text
    assert not Subject("PDSS").exists()
    assert not any((groundly_home() / ".imports").glob("*"))


def test_export_import_round_trip_keeps_the_tree_and_no_progress(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    (subject_dir("PDSS") / "progress.db").write_bytes(b"private study state")
    tree_a = SubjectStore(subject_dir("PDSS") / "store.db").tree()

    result = _export_then_import(tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output

    subj_b = Subject("PDSS")
    assert SubjectStore(subj_b.store_db_path).tree() == tree_a
    assert b"private study state" not in subj_b.progress_db_path.read_bytes()


def test_import_refuses_citations_that_do_not_resolve(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    _tamper("PDSS", "INSERT INTO topic_citations (topic_id, chunk_id, rank) VALUES (1, 999, 5)")

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "'topic_citations'", "no matching 'chunks'")


def test_import_refuses_a_three_level_tree(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    _tamper(
        "PDSS", "INSERT INTO topics (parent_id, position, title, summary) VALUES (2, 0, 'x', 'y')"
    )

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "more than two levels")


def test_import_refuses_a_store_without_topic_tables(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    _tamper("PDSS", "DROP TABLE topic_citations", "DROP TABLE topics")

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "no topic tree tables", "re-index")


def test_import_refuses_more_than_max_tree_nodes(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    _tamper(
        "PDSS",
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 1000) "
        "INSERT INTO topics (parent_id, position, title, summary) SELECT NULL, i, 'x', 'y' FROM n",
    )

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "1002 topics", "limit is 1000")


def test_import_refuses_a_topic_with_more_than_max_sources(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS", n_chunks=6)
    _tamper(
        "PDSS",
        *(
            f"INSERT INTO topic_citations (topic_id, chunk_id, rank) VALUES (1, {i}, {i})"
            for i in range(2, 7)
        ),
    )

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "topic 1 cites 6 sources", "limit is 5")


@pytest.mark.parametrize(
    ("statement", "phrases"),
    [
        (f"UPDATE topics SET title = '{'t' * 201}' WHERE id = 1", ("title", "201 characters")),
        (f"UPDATE topics SET summary = '{'s' * 501}' WHERE id = 1", ("summary", "501 characters")),
        ("UPDATE topics SET title = x'4142' WHERE id = 1", ("title", "not text")),
        ("UPDATE topics SET summary = x'4142' WHERE id = 1", ("summary", "not text")),
    ],
)
def test_import_refuses_titles_and_summaries_over_the_limit_or_not_text(
    tmp_path, monkeypatch, statement, phrases
):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    _tamper("PDSS", statement)

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, *phrases)


def test_import_refuses_a_tree_whose_cited_text_is_too_large(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    monkeypatch.setattr(store, "MAX_TREE_SOURCE_BYTES", 20)

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "cite 66 bytes of chunk text", "limit is 20")


_NO_FK_TOPICS = (
    "DROP TABLE topic_citations",
    "DROP TABLE topics",
    "CREATE TABLE topics (id INTEGER PRIMARY KEY, parent_id INTEGER, position INTEGER, title TEXT, summary TEXT)",
    "CREATE TABLE topic_citations (topic_id INTEGER, chunk_id INTEGER, rank INTEGER)",
    "INSERT INTO topics VALUES (1, NULL, 0, 'a', 'b')",
)


@pytest.mark.parametrize(
    ("extra", "phrase"),
    [
        ("INSERT INTO topic_citations VALUES (777, 1, 0)", "topic_citations.topic_id"),
        ("INSERT INTO topic_citations VALUES (1, 12345, 0)", "topic_citations.chunk_id"),
        ("INSERT INTO topics VALUES (2, 999, 1, 'c', 'd')", "topics.parent_id"),
    ],
)
def test_import_refuses_dangling_references_in_a_store_that_declares_no_keys(
    tmp_path, monkeypatch, extra, phrase
):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    _tamper("PDSS", *_NO_FK_TOPICS, extra)

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, phrase, "no matching")


def test_import_names_hostile_table_names_without_printing_them_raw(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    evil = "x\x1b]52;c;cHduZWQ=\x07"
    _tamper(
        "PDSS",
        f'CREATE TABLE "{evil}" (a INTEGER REFERENCES topics(id))',
        f'INSERT INTO "{evil}" VALUES (42)',
    )

    result = _export_then_import(tmp_path, monkeypatch)

    assert result.exit_code != 0
    assert "\x1b" not in result.output
    assert "dangling reference" in result.output


def test_import_names_a_hostile_schema_and_removes_the_temp_dir(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    _tamper(
        "PDSS",
        "DROP TABLE topic_citations",
        "CREATE TABLE topic_citations (topic_id INTEGER REFERENCES topics(title),"
        " chunk_id INTEGER, rank INTEGER)",
        "INSERT INTO topic_citations VALUES (1, 1, 0)",
    )

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "foreign key mismatch")


# --- Bundles from before the graph was dropped -------------------------------------


def test_import_of_a_legacy_bundle_ignores_its_graph(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])
    with zipfile.ZipFile(bundle_path) as zf:
        contents = {name: zf.read(name) for name in zf.namelist()}
    manifest = json.loads(contents["manifest.json"])
    manifest["graphrag"] = {"version": "3.1.0", "corpus_hash": "abc"}
    contents["manifest.json"] = json.dumps(manifest).encode()
    contents["graph/entities.parquet"] = b"parquet data"
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in contents.items():
            zf.writestr(name, data)

    _use_home(monkeypatch, tmp_path / "b")
    result = runner.invoke(app, ["import", str(bundle_path)])
    assert result.exit_code == 0, result.output

    subj = Subject("PDSS")
    assert not (subj.root_dir / "graph").exists()
    assert "graphrag" not in json.loads(subj.manifest_path.read_text())


# --- AC3: hostile bundle rejected + structural privacy ----------------------------


@pytest.mark.parametrize(
    "entry_name",
    ["../evil.txt", "materials/../../evil.txt", "/etc/passwd", "progress.db", "graph/../../evil"],
)
def test_validate_entries_rejects_path_escapes_and_smuggled_entries(tmp_path, entry_name):
    path = _zip_with_entry(tmp_path / "hostile.groundly", entry_name)
    with zipfile.ZipFile(path) as zf, pytest.raises(bundle.BundleError) as exc:
        bundle.validate_entries(zf)
    assert entry_name in str(exc.value)


@pytest.mark.parametrize("entry_name", ["materials\\..\\evil.txt", "C:\\evil.txt"])
def test_validate_entries_rejects_windows_style_paths(tmp_path, entry_name):
    path = _zip_with_entry(tmp_path / "hostile.groundly", entry_name)
    with zipfile.ZipFile(path) as zf, pytest.raises(bundle.BundleError, match="unsafe path"):
        bundle.validate_entries(zf)


def test_validate_entries_rejects_symlink(tmp_path):
    path = _zip_with_entry(tmp_path / "hostile.groundly", "materials/link.txt", symlink=True)
    with zipfile.ZipFile(path) as zf, pytest.raises(bundle.BundleError, match="symlink"):
        bundle.validate_entries(zf)


def test_validate_entries_rejects_oversized_declared_total(tmp_path):
    path = _zip_with_entry(tmp_path / "hostile.groundly", "materials/lec.pdf")
    with zipfile.ZipFile(path) as zf:
        # zipfile enforces each entry's *declared* file_size on read (truncates + CRC-fails
        # on mismatch), so admission checks against declared size are trustworthy without
        # decompressing anything. Bump the declared size on the live ZipInfo objects to
        # simulate a bundle claiming to be huge, without writing a huge file.
        for info in zf.infolist():
            info.file_size = bundle._BUNDLE_MAX_BYTES + 1
        with pytest.raises(bundle.BundleError, match="uncompressed"):
            bundle.validate_entries(zf)


def test_hostile_import_leaves_existing_subject_untouched(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    progress_path = subject_dir("PDSS") / "progress.db"
    original_progress = progress_path.read_bytes()
    original_material = (subject_dir("PDSS") / "materials" / "lec.pdf").read_bytes()

    hostile = _zip_with_entry(tmp_path / "hostile.groundly", "../evil.txt")
    result = runner.invoke(app, ["import", str(hostile), "--as", "PDSS", "--yes"])
    assert result.exit_code != 0
    assert "../evil.txt" in result.output
    assert progress_path.read_bytes() == original_progress
    assert (subject_dir("PDSS") / "materials" / "lec.pdf").read_bytes() == original_material


def test_export_never_reads_progress_db_and_bundle_module_never_names_it(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    (subject_dir("PDSS") / "progress.db").write_bytes(b"\xff" * 4096)  # garbage-fill

    out_path = tmp_path / "PDSS.groundly"
    subj = Subject("PDSS")
    bundle.export_subject(subj, out_path)  # must not raise despite the corrupt file

    with zipfile.ZipFile(out_path) as zf:
        names = zf.namelist()
    assert not any(n.endswith("progress.db") for n in names)

    source = Path(bundle.__file__).read_text().lower()
    assert "progress" not in source


# --- AC4: no silent overwrite ------------------------------------------------------


def test_import_collision_without_yes_aborts_and_leaves_original(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS", filename="original.pdf")
    original = (subject_dir("PDSS") / "materials" / "original.pdf").read_bytes()
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])

    result = runner.invoke(app, ["import", str(bundle_path)], input="n\n")
    assert result.exit_code != 0
    assert (subject_dir("PDSS") / "materials" / "original.pdf").read_bytes() == original


def test_import_yes_replaces_existing_subject(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS", filename="original.pdf")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])

    (subject_dir("PDSS") / "materials" / "extra.pdf").write_text("stray")  # mutate the live copy

    result = runner.invoke(app, ["import", str(bundle_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert not (subject_dir("PDSS") / "materials" / "extra.pdf").exists()
    assert (subject_dir("PDSS") / "materials" / "original.pdf").exists()


def test_import_as_creates_second_subject_alongside_original(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])

    result = runner.invoke(app, ["import", str(bundle_path), "--as", "PDSS2"])
    assert result.exit_code == 0, result.output
    assert Subject("PDSS").exists()
    assert Subject("PDSS2").exists()


def test_import_onto_leftover_dir_without_manifest_fails_cleanly(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])

    leftover = subject_dir("PDSS_PARTIAL")
    leftover.mkdir(parents=True)
    (leftover / "stray.txt").write_text("interrupted init")

    result = runner.invoke(app, ["import", str(bundle_path), "--as", "PDSS_PARTIAL"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "leftover directory" in result.output
    assert leftover.name in result.output
    assert not any((groundly_home() / ".imports").iterdir())


# --- Extras --------------------------------------------------------------------


def test_check_counts_refuses_newer_schema(tmp_path):
    import sqlite3

    path = tmp_path / "store.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="newer than this groundly"):
        bundle.check_counts(path, Manifest.new("PDSS"))


def test_check_counts_rejects_mismatched_rows(tmp_path):
    path = tmp_path / "store.db"
    store.create_store(path)
    manifest = Manifest.new("PDSS")
    manifest.counts.chunks = 5
    with pytest.raises(bundle.BundleError, match="damaged"):
        bundle.check_counts(path, manifest)


def test_read_manifest_missing_names_cause(tmp_path):
    path = tmp_path / "bad.groundly"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("store.db", b"")
    with (
        zipfile.ZipFile(path) as zf,
        pytest.raises(bundle.BundleError, match="missing manifest.json"),
    ):
        bundle.read_manifest(zf)


def test_read_manifest_rejects_oversized_manifest(tmp_path):
    path = tmp_path / "bad.groundly"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", b" " * (1024 * 1024 + 1))
    with zipfile.ZipFile(path) as zf, pytest.raises(bundle.BundleError, match="too large"):
        bundle.read_manifest(zf)


def test_read_manifest_rejects_newer_format_version(tmp_path):
    path = tmp_path / "bad.groundly"
    data = Manifest.new("PDSS").model_dump()
    data["format_version"] = 2
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(data))
    with zipfile.ZipFile(path) as zf, pytest.raises(bundle.BundleError, match="newer groundly"):
        bundle.read_manifest(zf)


def test_export_no_materials_excludes_originals(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    result = runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path), "--no-materials"])
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(bundle_path) as zf:
        assert not any(n.startswith("materials/") for n in zf.namelist())

    result = runner.invoke(app, ["import", str(bundle_path), "--as", "PDSS2"])
    assert result.exit_code == 0, result.output
    subj2 = Subject("PDSS2")
    assert subj2.materials_dir.exists()
    assert list(subj2.materials_dir.iterdir()) == []


def test_import_yes_skips_pin_mismatch_prompt(tmp_path, monkeypatch, stub_embedder):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])
    _rewrite_zip_manifest(bundle_path, hf_revision="stale-revision")

    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", stub_embedder)
    result = runner.invoke(app, ["import", str(bundle_path), "--as", "PDSS_B", "--yes"])
    assert result.exit_code == 0, result.output
    assert Subject("PDSS_B").load_manifest().embedding.hf_revision == HF_REVISION


def test_import_replace_prompt_names_progress_not_notes(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])
    result = runner.invoke(app, ["import", str(bundle_path)], input="n\n")
    assert "progress" in result.output
    assert "notes" not in result.output


def test_import_force_flag_is_gone(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    bundle_path = tmp_path / "PDSS.groundly"
    bundle_path.write_bytes(b"")
    assert runner.invoke(app, ["import", str(bundle_path), "--force"]).exit_code == 2
