"""UC-30 export/import. Seeds store.db directly via SubjectStore.add_indexed +
sync_counts (no pipeline); real SQLite; StubEmbedder injected for the re-embed path."""

import json
import stat
import zipfile
from pathlib import Path

import pytest
import sqlite_vec
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
    from groundly.core.progress import ProgressStore

    progress_b = ProgressStore(subj_b.progress_db_path)
    assert progress_b.attempts() == [] and progress_b.answers() == []


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
    conn = store.connect(subj.store_db_path)
    try:
        assert (
            conn.execute(
                "SELECT count(*) FROM vectors v JOIN chunks c ON c.id = v.rowid "
                "WHERE v.material_id IS NOT c.material_id"
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


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
    # The anti-joins stand behind the schema comparison, which refuses such a store first.
    with pytest.raises(bundle.BundleError, match=f"{phrase}.*no matching"):
        bundle.check_tree(subject_dir("PDSS") / "store.db")

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "a changed table", "groundly does not ship")


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
    assert "groundly does not ship" in result.output


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

    _assert_refused(result, "a changed table 'topic_citations'")


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


def test_title_survives_export_import_as_and_count_syncs(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    assert runner.invoke(app, ["title", "PDSS", "Parallel Systems"]).exit_code == 0
    conn = SubjectStore(subject_dir("PDSS") / "store.db").connect()
    try:
        sync_counts(conn, Subject("PDSS").manifest_path)
    finally:
        conn.close()
    assert Subject("PDSS").load_manifest().title == "Parallel Systems"

    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])
    result = runner.invoke(app, ["import", str(bundle_path), "--as", "PDSS2"])
    assert result.exit_code == 0, result.output
    imported = Subject("PDSS2").load_manifest()
    assert (imported.subject, imported.title) == ("PDSS2", "Parallel Systems")


@pytest.mark.parametrize(
    "title", ["x" * 121, "two\nlines", "rtl\N{RIGHT-TO-LEFT OVERRIDE}override", "tag\U000e0100"]
)
def test_import_refuses_a_bad_title_and_installs_nothing(tmp_path, monkeypatch, title):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])
    with zipfile.ZipFile(bundle_path) as zf:
        contents = {name: zf.read(name) for name in zf.namelist()}
    manifest = json.loads(contents["manifest.json"])
    manifest["title"] = title
    contents["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(bundle_path, "w") as zf:
        for name, data in contents.items():
            zf.writestr(name, data)

    result = runner.invoke(app, ["import", str(bundle_path), "--as", "HOSTILE"])
    assert result.exit_code == 1
    assert "title" in result.output
    assert not Subject("HOSTILE").exists()


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


# --- hostile material rows and manifest fields ---------------------------------------


def _rewrite_zip(bundle_path, edit):
    with zipfile.ZipFile(bundle_path) as zf:
        contents = {name: zf.read(name) for name in zf.namelist()}
    edit(contents)
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in contents.items():
            zf.writestr(name, data)


def _seed_exportable(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")


def _export(tmp_path):
    bundle_path = tmp_path / "PDSS.groundly"
    assert runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)]).exit_code == 0
    return bundle_path


@pytest.mark.parametrize(
    "columns, values",
    [
        ("filename, sha256, status", "'../../victim', '{sha}', 'extraction_failed'"),
        ("filename, sha256, status", "'a/b.pdf', '{sha}', 'extraction_failed'"),
        ("filename, sha256, status", "'a\\b.pdf', '{sha}', 'extraction_failed'"),
        ("filename, sha256, status", "'..', '{sha}', 'extraction_failed'"),
        ("filename, sha256, status", "'', '{sha}', 'extraction_failed'"),
        ("filename, sha256, status", "'a' || char(0) || '.pdf', '{sha}', 'extraction_failed'"),
        ("filename, sha256, status", "x'00ff', '{sha}', 'extraction_failed'"),
        ("filename, sha256, status", "'a.pdf', '[/nosuch]', 'extraction_failed'"),
        ("filename, sha256, status", "'a.pdf', '{sha}', '[/nosuch]'"),
        ("filename, sha256, status, pages", "'a.pdf', '{sha}', 'extraction_failed', 'many'"),
        ("filename, sha256, status, error", "'a.pdf', '{sha}', 'extraction_failed', x'00ff'"),
    ],
)
def test_import_refuses_a_material_row_groundly_could_not_have_written(
    tmp_path, monkeypatch, columns, values
):
    _seed_exportable(tmp_path, monkeypatch)
    _tamper(
        "PDSS",
        "PRAGMA ignore_check_constraints = ON",
        f"INSERT INTO materials ({columns}) VALUES ({values.format(sha='b' * 64)})",
    )

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "material")
    assert "\x1b" not in result.output


def test_import_keeps_free_text_in_a_material_error(tmp_path, monkeypatch):
    """Parser output is free text: it is escaped when shown, and a bundle is not refused for it."""
    _seed_exportable(tmp_path, monkeypatch)
    _tamper(
        "PDSS",
        "INSERT INTO materials (filename, sha256, status, error) VALUES "
        f"('a.pdf', '{'b' * 64}', 'extraction_failed', 'x' || char(27) || '[31m [/nosuch]')",
    )

    result = _export_then_import(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert Subject("PDSS").exists()


@pytest.mark.parametrize("name", ["notes\tfinal.pdf", "a\nb.pdf", "bell\x07.pdf", "e\x1b[31m.pdf"])
def test_import_keeps_a_filename_the_filesystem_allows(tmp_path, monkeypatch, name):
    """Indexing stores a file's own name, so these are real exports. Only a name that could
    leave the materials dir is refused; printing it is made inert where it is shown."""
    _seed_exportable(tmp_path, monkeypatch)
    SubjectStore(subject_dir("PDSS") / "store.db").add_extraction_failed(name, "b" * 64, "no text")

    result = _export_then_import(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert "\x1b" not in runner.invoke(app, ["list", "PDSS"]).output


def test_import_refuses_a_page_that_is_not_a_number(tmp_path, monkeypatch):
    _seed_exportable(tmp_path, monkeypatch)
    _tamper("PDSS", "UPDATE chunks SET page = 'p' || char(27) || '[31m' WHERE id = 1")

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "chunk", "page")
    assert "\x1b" not in result.output


@pytest.mark.parametrize(
    "statements",
    [
        pytest.param(
            [
                "ALTER TABLE materials RENAME TO m0",
                "CREATE VIEW materials AS SELECT * FROM m0",
            ],
            id="materials-as-a-view",
        ),
        pytest.param(["CREATE VIEW extra AS SELECT 1"], id="extra-view"),
        pytest.param(
            ["CREATE TRIGGER extra AFTER DELETE ON topics BEGIN SELECT 1; END"],
            id="extra-trigger",
        ),
        pytest.param(
            [
                "DROP TRIGGER chunks_ad",
                (
                    "CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN "
                    "INSERT INTO materials (filename, sha256, status) "
                    "VALUES ('../../victim', 'x', 'indexed'); END"
                ),
            ],
            id="shipped-trigger-name-with-another-body",
        ),
    ],
)
def test_import_refuses_views_and_triggers_groundly_does_not_ship(
    tmp_path, monkeypatch, statements
):
    """A view answers the row checks with one thing and later reads with another; a trigger
    rewrites rows after the checks have passed."""
    _seed_exportable(tmp_path, monkeypatch)
    _tamper("PDSS", *statements)

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "views or triggers")


def test_import_refuses_a_view_before_any_check_reads_through_it(tmp_path, monkeypatch):
    """A view runs when it is read, and one can be written never to finish. This one only
    changes the row count, so the counts check would name the wrong cause if it ran first."""
    _seed_exportable(tmp_path, monkeypatch)
    _seed("PDSS", filename="other.pdf", sha256="b" * 64)
    _tamper(
        "PDSS",
        "ALTER TABLE materials RENAME TO m0",
        "CREATE VIEW materials AS SELECT * FROM m0 WHERE 0",
    )

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "views or triggers")


def test_export_status_shows_a_filename_as_plain_text(tmp_path, monkeypatch):
    """The status line is transient, so it never reaches captured output. A plain string would
    be parsed for markup and emoji codes; `Text` is shown as it is."""
    from rich.status import Status
    from rich.text import Text

    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS", filename=":warning:.pdf")
    seen = []
    real_update = Status.update

    def recording_update(self, status=None, **kwargs):
        seen.append(status)
        real_update(self, status, **kwargs)

    monkeypatch.setattr(Status, "update", recording_update)

    result = runner.invoke(app, ["export", "PDSS", "-o", str(tmp_path / "out.groundly")])

    assert result.exit_code == 0, result.output
    assert any(isinstance(s, Text) and ":warning:.pdf" in s.plain for s in seen)


def test_import_refuses_a_materials_table_missing_a_column(tmp_path, monkeypatch):
    _seed_exportable(tmp_path, monkeypatch)
    _tamper("PDSS", "ALTER TABLE materials DROP COLUMN error")

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "a changed table 'materials'")


@pytest.mark.parametrize("lang", ["\x1b]52;c;cHduZWQ=\x07", "[/nosuch]"])
def test_import_refuses_a_hostile_ocr_lang_without_printing_it_raw(tmp_path, monkeypatch, lang):
    _seed_exportable(tmp_path, monkeypatch)
    bundle_path = _export(tmp_path)

    def edit(contents):
        manifest = json.loads(contents["manifest.json"])
        manifest["ocr"]["lang"] = [lang]
        contents["manifest.json"] = json.dumps(manifest).encode()

    _rewrite_zip(bundle_path, edit)
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # a named refusal, not a traceback
    assert "not a valid bundle" in " ".join(result.output.split())
    assert "\x1b" not in result.output and "\x07" not in result.output
    assert not Subject("PDSS").exists()


def test_import_survives_markup_in_a_bundle_entry_name(tmp_path, monkeypatch):
    """The name reaches the status line, which parses a plain string as console markup."""
    _seed_exportable(tmp_path, monkeypatch)
    bundle_path = _export(tmp_path)
    _rewrite_zip(bundle_path, lambda contents: contents.update({"materials/[/nosuch].pdf": b"x"}))
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    assert result.exit_code == 0, result.output
    assert Subject("PDSS").exists()


def test_export_survives_markup_in_a_material_path(tmp_path, monkeypatch):
    """A nested entry an earlier import extracted: its stored path reads as a closing tag."""
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS", filename="nosuch].pdf")
    materials = subject_dir("PDSS") / "materials"
    (materials / "[").mkdir()
    (materials / "nosuch].pdf").rename(materials / "[" / "nosuch].pdf")
    bundle_path = tmp_path / "out.groundly"

    result = runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])

    assert result.exit_code == 0, result.output


# --- Question bank: import repeats the gate's structure checks -----------------------


def _seed_bank(name="PDSS"):
    """The tree from _seed_tree (leaf 'Conditions' is topic 2, citing chunk 2) with one
    single-answer question on the leaf."""
    _seed_tree(name)
    SubjectStore(subject_dir(name) / "store.db").add_questions(
        2,
        [
            store.QuestionWrite(
                "single",
                "What does a deadlock need?",
                None,
                None,
                [("mutual exclusion", True), ("a GPU", False)],
                [2],
            )
        ],
    )


def _edit_bundled_store(bundle_path, scratch, *statements):
    """Rewrite the store.db inside a bundle with raw SQL, foreign keys off: a bundle
    `groundly export` could not have produced, since export opens and upgrades the store."""
    import sqlite3

    db = scratch / "edited-store.db"

    def edit(contents):
        db.write_bytes(contents["store.db"])
        conn = sqlite3.connect(db)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        try:
            conn.executescript(";".join(statements))
        finally:
            conn.close()
        contents["store.db"] = db.read_bytes()

    _rewrite_zip(bundle_path, edit)


def _shipped_trigger_sql():
    (sql,) = [
        sql for kind, name, sql in store.shipped_programs() if name == "question_citations_ad"
    ]
    return sql


_VECTORS_TO_V2 = (
    "CREATE TEMP TABLE old AS SELECT rowid AS id, embedding FROM vectors",
    "DROP TABLE vectors",
    f"CREATE VIRTUAL TABLE vectors USING vec0(embedding float[{EMBEDDING_DIM}])",
    "INSERT INTO vectors (rowid, embedding) SELECT id, embedding FROM temp.old",
)

_DOWNGRADE = (
    *_VECTORS_TO_V2,
    "DROP TRIGGER question_citations_ad",
    "DROP TABLE question_citations",
    "DROP TABLE question_options",
    "DROP TABLE questions",
    "PRAGMA user_version = 1",
)


def test_the_question_bank_survives_export_and_import(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    (subject_dir("PDSS") / "progress.db").write_bytes(b"private study state")
    bank_a = SubjectStore(subject_dir("PDSS") / "store.db").questions()

    result = _export_then_import(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert SubjectStore(Subject("PDSS").store_db_path).questions() == bank_a
    with zipfile.ZipFile(tmp_path / "PDSS.groundly") as zf:
        assert "progress.db" not in zf.namelist()


def test_the_export_statement_names_the_question_bank(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    result = runner.invoke(app, ["export", "PDSS", "-o", str(tmp_path / "PDSS.groundly")])
    assert "including its topic map and question bank." in " ".join(result.output.split())


@pytest.mark.parametrize(
    ("statement", "phrase"),
    [
        ("UPDATE question_options SET correct = 0", "exactly one correct option"),
        ("UPDATE questions SET kind = 'open'", "an open question"),
        ("UPDATE questions SET topic_id = 1", "topic that has subtopics"),
        ("UPDATE questions SET stem = zeroblob(10)", "stem that is not text"),
        ("UPDATE questions SET stem = printf('%.*c', 501, 'x')", "stem of 501 characters"),
        ("UPDATE question_options SET text = printf('%.*c', 301, 'x')", "option of 301 characters"),
        ("UPDATE questions SET code = printf('%.*c', 2001, 'x')", "code of 2001 characters"),
        ("UPDATE questions SET reference_answer = 'r'", "reference answer"),
        ("UPDATE questions SET stem = char(9, 10)", "empty stem"),
        ("UPDATE question_options SET text = char(10)", "empty option"),
        ("UPDATE questions SET code = char(10, 9)", "empty code"),
    ],
)
def test_import_refuses_a_malformed_question_bank(tmp_path, monkeypatch, statement, phrase):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    _tamper("PDSS", statement)

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "bundle question 1", phrase)


def test_import_refuses_a_question_citing_a_chunk_that_does_not_exist(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    _tamper("PDSS", "INSERT INTO question_citations (question_id, chunk_id) VALUES (1, 99999)")

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "'question_citations'", "no matching 'chunks'")


def test_import_refuses_a_question_without_citations(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    bundle_path = _export(tmp_path)
    # The trigger would delete the question with its last citation: drop it first.
    _edit_bundled_store(
        bundle_path,
        tmp_path,
        "DROP TRIGGER question_citations_ad",
        "DELETE FROM question_citations",
    )
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    _assert_refused(result, "views or triggers groundly does not ship")


def test_import_refuses_a_question_citing_more_than_five_chunks(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS", n_chunks=6)
    SubjectStore(subject_dir("PDSS") / "store.db").add_questions(
        2,
        [store.QuestionWrite("single", "Q?", None, None, [("a", True), ("b", False)], [2])],
    )
    _tamper(
        "PDSS",
        *(
            f"INSERT INTO question_citations (question_id, chunk_id) VALUES (1, {i})"
            for i in (1, 3, 4, 5, 6)
        ),
    )

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "bundle question 1 cites 6 chunks", "limit is 5")


def test_import_refuses_a_topic_holding_more_than_the_question_limit(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    monkeypatch.setattr(store, "MAX_QUESTIONS_PER_TOPIC", 0)

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "bundle topic 2 holds 1 questions", "limit is 0")


def test_a_version_1_bundle_is_upgraded_on_import(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    tree_a = SubjectStore(subject_dir("PDSS") / "store.db").tree()
    bundle_path = _export(tmp_path)
    _edit_bundled_store(bundle_path, tmp_path, *_DOWNGRADE)
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    assert result.exit_code == 0, result.output
    imported = SubjectStore(Subject("PDSS").store_db_path)
    assert imported.tree() == tree_a and imported.questions() == []
    conn = imported.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.STORE_USER_VERSION
    finally:
        conn.close()


def _misfiled_vectors(store_db_path) -> int:
    conn = store.connect(store_db_path)
    try:
        return conn.execute(
            "SELECT count(*) FROM vectors v JOIN chunks c ON c.id = v.rowid "
            "WHERE v.material_id IS NOT c.material_id"
        ).fetchone()[0]
    finally:
        conn.close()


def test_a_version_2_bundle_is_upgraded_on_import(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    counts_a = _row_counts(subject_dir("PDSS") / "store.db")
    bundle_path = _export(tmp_path)
    _edit_bundled_store(bundle_path, tmp_path, *_VECTORS_TO_V2, "PRAGMA user_version = 2")
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    assert result.exit_code == 0, result.output
    path = Subject("PDSS").store_db_path
    assert _row_counts(path) == counts_a and _misfiled_vectors(path) == 0
    conn = store.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.STORE_USER_VERSION
    finally:
        conn.close()


def test_a_version_2_bundle_without_a_vectors_table_is_refused(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    bundle_path = _export(tmp_path)
    _edit_bundled_store(bundle_path, tmp_path, "DROP TABLE vectors", "PRAGMA user_version = 2")
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    _assert_refused(result, "could not be upgraded", "lacks a table groundly writes")


def test_a_bundle_with_a_vector_filed_under_another_material_is_refused(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    _seed("PDSS", filename="other.pdf", content=b"other bytes", sha256="b" * 64)
    bundle_path = _export(tmp_path)
    _edit_bundled_store(bundle_path, tmp_path, "UPDATE vectors SET material_id = 2 WHERE rowid = 1")
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    _assert_refused(result, "bundle vector 1", "filed under a different material than its chunk")


def test_a_version_1_bundle_with_its_own_questions_table_is_refused(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_tree("PDSS")
    bundle_path = _export(tmp_path)
    _edit_bundled_store(bundle_path, tmp_path, *_DOWNGRADE, "CREATE TABLE questions (x)")
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    _assert_refused(result, "could not be upgraded", "tables groundly did not write")
    assert "Traceback" not in result.output


def test_import_refuses_duplicate_options_in_a_question(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    _tamper("PDSS", "UPDATE question_options SET text = ' Mutual Exclusion' WHERE position = 1")

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "bundle question 1 has two options with the same text")


def test_import_refuses_duplicate_stems_in_a_topic(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    SubjectStore(subject_dir("PDSS") / "store.db").add_questions(
        2, [store.QuestionWrite("single", "Other?", None, None, [("a", True), ("b", False)], [2])]
    )
    _tamper("PDSS", "UPDATE questions SET stem = 'WHAT does a deadlock need? ' WHERE id = 2")

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "bundle topic 2 has two questions with the same stem")


@pytest.mark.parametrize(
    "statements",
    [
        # questions without AUTOINCREMENT: a removed question's id could be reused
        (
            "DROP TRIGGER question_citations_ad",
            "ALTER TABLE questions RENAME TO q_old",
            (
                "CREATE TABLE questions (id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL, "
                "kind TEXT NOT NULL, stem TEXT NOT NULL, code TEXT, reference_answer TEXT)"
            ),
            "INSERT INTO questions SELECT * FROM q_old",
            "DROP TABLE q_old",
            _shipped_trigger_sql(),
        ),
        # an index groundly does not ship
        ("CREATE INDEX extra ON questions(stem)",),
    ],
)
def test_import_refuses_question_tables_declared_differently(tmp_path, monkeypatch, statements):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    bundle_path = _export(tmp_path)
    _edit_bundled_store(bundle_path, tmp_path, *statements)
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    _assert_refused(result, "groundly does not ship")


@pytest.mark.parametrize(
    "statements",
    [
        # a generated column that would make the first length check allocate gigabytes
        (
            "DROP TRIGGER question_citations_ad",
            "ALTER TABLE questions RENAME TO q_old",
            (
                "CREATE TABLE questions (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER, "
                "kind TEXT, stem TEXT GENERATED ALWAYS AS (printf('%.*c', 600, 'x')) VIRTUAL, "
                "code TEXT, reference_answer TEXT)"
            ),
            "DROP TABLE q_old",
            _shipped_trigger_sql(),
        ),
        # a dropped index that turns the bank check quadratic
        ("DROP INDEX idx_question_options_question",),
        # a table groundly never writes
        ("CREATE TABLE extra (x)",),
    ],
)
def test_import_refuses_tables_and_indexes_groundly_does_not_ship(
    tmp_path, monkeypatch, statements
):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    bundle_path = _export(tmp_path)
    _edit_bundled_store(bundle_path, tmp_path, *statements)
    _use_home(monkeypatch, tmp_path / "b")

    result = runner.invoke(app, ["import", str(bundle_path)])

    _assert_refused(result, "groundly does not ship")


def test_a_locked_version_1_store_names_the_lock_not_a_foreign_table(tmp_path):
    import sqlite3

    path = tmp_path / "store.db"
    store.create_store(path)
    raw = sqlite3.connect(path)
    raw.enable_load_extension(True)
    sqlite_vec.load(raw)
    raw.executescript(";".join(_DOWNGRADE))
    raw.close()
    holder = sqlite3.connect(path, timeout=0)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked") as raised:
            store.connect(path)
        assert not isinstance(raised.value, store.SchemaUpgradeError)
    finally:
        holder.rollback()
        holder.close()


def test_import_refuses_a_bank_whose_cited_text_is_too_large(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed_bank()
    monkeypatch.setattr(store, "MAX_BANK_SOURCE_BYTES", 10)

    result = _export_then_import(tmp_path, monkeypatch)

    _assert_refused(result, "bundle questions cite", "limit is 10")
