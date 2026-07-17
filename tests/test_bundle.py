"""UC-30 export/import. Seeds store.db directly via SQLiteSubjectStore.add_indexed +
sync_counts (no pipeline); real SQLite; StubEmbedder injected for the re-embed path."""

import json
import stat
import zipfile

import pytest
from typer.testing import CliRunner

from groundly.cli import app
from groundly.core import bundle, store
from groundly.core.manifest import EMBEDDING_DIM, HF_REVISION, Manifest, sync_counts
from groundly.core.paths import groundly_home, subject_dir
from groundly.core.store import SQLiteSubjectStore
from groundly.core.subject import Subject, init_subject
from groundly.ingestion.extract import ChunkData

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
    SQLiteSubjectStore(sdir / "store.db").add_indexed(filename, sha256, 3, chunks, dense, sparse)
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
    assert "everything indexed in this subject" in result.output

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


# --- AC3: hostile bundle rejected + structural privacy ----------------------------


@pytest.mark.parametrize(
    "entry_name",
    ["../evil.txt", "materials/../../evil.txt", "/etc/passwd", "progress.db", "graph/../../evil"],
)
def test_validate_entries_rejects_path_escapes_and_smuggled_entries(tmp_path, entry_name):
    path = _zip_with_entry(tmp_path / "hostile.groundly", entry_name)
    with zipfile.ZipFile(path) as zf:
        with pytest.raises(bundle.BundleError) as exc:
            bundle.validate_entries(zf)
    assert entry_name in str(exc.value)


@pytest.mark.parametrize("entry_name", ["materials\\..\\evil.txt", "C:\\evil.txt"])
def test_validate_entries_rejects_windows_style_paths(tmp_path, entry_name):
    path = _zip_with_entry(tmp_path / "hostile.groundly", entry_name)
    with zipfile.ZipFile(path) as zf:
        with pytest.raises(bundle.BundleError, match="unsafe path"):
            bundle.validate_entries(zf)


def test_validate_entries_rejects_symlink(tmp_path):
    path = _zip_with_entry(tmp_path / "hostile.groundly", "materials/link.txt", symlink=True)
    with zipfile.ZipFile(path) as zf:
        with pytest.raises(bundle.BundleError, match="symlink"):
            bundle.validate_entries(zf)


def test_hostile_import_leaves_existing_subject_untouched(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS")
    progress_path = subject_dir("PDSS") / "progress.db"
    original_progress = progress_path.read_bytes()
    original_material = (subject_dir("PDSS") / "materials" / "lec.pdf").read_bytes()

    hostile = _zip_with_entry(tmp_path / "hostile.groundly", "../evil.txt")
    result = runner.invoke(app, ["import", str(hostile), "--as", "PDSS", "--force"])
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

    source = open(bundle.__file__).read().lower()
    assert "progress" not in source


# --- AC4: no silent overwrite ------------------------------------------------------


def test_import_collision_without_force_aborts_and_leaves_original(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS", filename="original.pdf")
    original = (subject_dir("PDSS") / "materials" / "original.pdf").read_bytes()
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])

    result = runner.invoke(app, ["import", str(bundle_path)], input="n\n")
    assert result.exit_code != 0
    assert (subject_dir("PDSS") / "materials" / "original.pdf").read_bytes() == original


def test_import_force_replaces_existing_subject(tmp_path, monkeypatch):
    _use_home(monkeypatch, tmp_path / "a")
    init_subject("PDSS")
    _seed("PDSS", filename="original.pdf")
    bundle_path = tmp_path / "PDSS.groundly"
    runner.invoke(app, ["export", "PDSS", "-o", str(bundle_path)])

    (subject_dir("PDSS") / "materials" / "extra.pdf").write_text("stray")  # mutate the live copy

    result = runner.invoke(app, ["import", str(bundle_path), "--force"])
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
    with zipfile.ZipFile(path) as zf:
        with pytest.raises(bundle.BundleError, match="missing manifest.json"):
            bundle.read_manifest(zf)


def test_read_manifest_rejects_newer_format_version(tmp_path):
    path = tmp_path / "bad.groundly"
    data = Manifest.new("PDSS").model_dump()
    data["format_version"] = 2
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(data))
    with zipfile.ZipFile(path) as zf:
        with pytest.raises(bundle.BundleError, match="newer groundly"):
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
