"""The page server is a read-only loopback surface over content a stranger's bundle may have
written, so these tests pin what it refuses as much as what it serves."""

import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from groundly.core import store
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import subject_dir
from groundly.core.store import SubjectStore, TopicWrite
from groundly.core.subject import Subject
from groundly.ingestion.extract import ChunkData
from groundly.web.app import create_app

PDF_BYTES = b"%PDF-1.4 stored lecture"


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def client(home):
    return TestClient(create_app(), base_url="http://127.0.0.1")


def _index(name, filename, sha, *, status_pages=3):
    """Index a one-chunk material and return (material_id, chunk_id)."""
    s = SubjectStore(subject_dir(name) / "store.db")
    chunks = [ChunkData(f"text of {filename}", "Intro > Locks", 17, 5)]
    material_id = s.add_indexed(
        filename, sha, status_pages, chunks, zip([[0.1] * EMBEDDING_DIM], [{1: 0.5}])
    )
    conn = s.connect()
    try:
        chunk_id = conn.execute(
            "SELECT id FROM chunks WHERE material_id = ?", (material_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    return material_id, chunk_id


@pytest.fixture
def seeded(home):
    """PDSS with a stored pdf and html material and a one-node tree citing both."""
    Subject("PDSS").initialize()
    materials = Subject("PDSS").materials_dir
    (materials / "lec.pdf").write_bytes(PDF_BYTES)
    (materials / "lec.html").write_text("<script>alert(1)</script>")
    pdf_id, pdf_chunk = _index("PDSS", "lec.pdf", "a" * 64)
    html_id, html_chunk = _index("PDSS", "lec.html", "b" * 64)
    SubjectStore(subject_dir("PDSS") / "store.db").replace_tree(
        [TopicWrite(None, "Locks", "Mutual exclusion", [pdf_chunk, html_chunk])]
    )
    return {"pdf": pdf_id, "html": html_id}


def _tamper(name, *statements):
    """Raw SQL as a hostile bundle author has it; a statement may be a (sql, params) pair."""
    conn = store.connect(subject_dir(name) / "store.db")
    try:
        with conn:
            for sql in statements:
                conn.execute(*((sql,) if isinstance(sql, str) else sql))
    finally:
        conn.close()


def test_page_routes_serve_html_under_a_self_only_csp(client, seeded):
    for path in ("/", "/s/PDSS"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'; style-src 'self'" in csp
        assert "unsafe-inline" not in csp and "nonce" not in csp
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        assert "/static/page.js" in r.text and "/static/page.css" in r.text


def test_page_assets_are_served_with_their_own_types(client, seeded):
    css, js = client.get("/static/page.css"), client.get("/static/page.js")
    assert css.status_code == js.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "javascript" in js.headers["content-type"]
    assert client.get("/static/../app.py").status_code == 404


def test_foreign_host_header_is_rejected(client, seeded):
    for path in ("/", "/api/subjects"):
        assert client.get(path, headers={"Host": "evil.example"}).status_code == 400


def test_localhost_host_header_is_accepted(client, seeded):
    assert client.get("/api/subjects", headers={"Host": "localhost:8765"}).status_code == 200


def test_api_lists_subjects_with_has_tree(client, seeded):
    Subject("EMPTY").initialize()
    assert client.get("/api/subjects").json() == [
        {"subject": "EMPTY", "has_tree": False},
        {"subject": "PDSS", "has_tree": True},
    ]


def test_tree_api_returns_tree_with_sources(client, seeded):
    body = client.get("/api/subjects/PDSS/tree").json()
    (node,) = body["tree"]
    assert node["title"] == "Locks"
    assert [(s["filename"], s["page"], s["material_id"]) for s in node["sources"]] == [
        ("lec.pdf", 17, seeded["pdf"]),
        ("lec.html", 17, seeded["html"]),
    ]


def test_tree_flags_which_pdf_sources_can_be_opened(client, seeded):
    (node,) = client.get("/api/subjects/PDSS/tree").json()["tree"]
    assert [(s["filename"], s["pdf"]) for s in node["sources"]] == [
        ("lec.pdf", True),
        ("lec.html", False),
    ]
    (Subject("PDSS").materials_dir / "lec.pdf").unlink()  # e.g. exported with --no-materials
    (node,) = client.get("/api/subjects/PDSS/tree").json()["tree"]
    assert node["sources"][0]["pdf"] is False


def test_pdf_route_404_when_filename_is_not_text(client, seeded):
    _tamper("PDSS", ("UPDATE materials SET filename = ? WHERE id = ?", (b"x.pdf", seeded["pdf"])))
    assert client.get(f"/s/PDSS/materials/{seeded['pdf']}").status_code == 404


def test_unknown_or_invalid_subject_is_404(client, seeded):
    for name in ("nope", "..%2Fx", "a.b"):
        for path in (f"/s/{name}", f"/api/subjects/{name}/tree", f"/s/{name}/materials/1"):
            assert client.get(path).status_code == 404, path


def test_unknown_subject_page_names_the_subject_without_reflecting_it(client, seeded):
    name = "%3Cimg%20src=x%3E"
    page = client.get(f"/s/{name}")
    assert page.status_code == 404
    assert "<img src=x>" not in page.text and "Content-Security-Policy" in page.headers
    assert (
        client.get(f"/api/subjects/{name}/tree").json()["error"] == "unknown subject '<img src=x>'"
    )


def test_store_without_topic_tables_reports_the_cause(client, seeded):
    _tamper("PDSS", "DROP TABLE topic_citations", "DROP TABLE topics")
    (entry,) = client.get("/api/subjects").json()
    assert entry["subject"] == "PDSS" and entry["has_tree"] is False
    assert "no such table" in entry["error"]
    r = client.get("/api/subjects/PDSS/tree")
    assert r.status_code == 500
    assert "no such table" in r.json()["error"]


def test_pdf_route_serves_stored_file_with_nosniff(client, seeded):
    r = client.get(f"/s/PDSS/materials/{seeded['pdf']}")
    assert r.status_code == 200
    assert r.content == PDF_BYTES
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in r.headers


def test_pdf_route_refuses_non_pdf_material(client, seeded):
    assert client.get(f"/s/PDSS/materials/{seeded['html']}").status_code == 404


def test_pdf_route_refuses_unknown_and_unindexed_materials(client, seeded):
    _tamper("PDSS", f"UPDATE materials SET status = 'extraction_failed' WHERE id = {seeded['pdf']}")
    assert client.get(f"/s/PDSS/materials/{seeded['pdf']}").status_code == 404
    assert client.get("/s/PDSS/materials/999").status_code == 404


@pytest.mark.parametrize("filename", ["../../secret.pdf", "/etc/secret.pdf", "x\0.pdf"])
def test_pdf_route_refuses_path_escape_in_filename(client, seeded, home, filename):
    (home / "secret.pdf").write_bytes(b"%PDF-secret")
    _tamper("PDSS", ("UPDATE materials SET filename = ? WHERE id = ?", (filename, seeded["pdf"])))
    assert client.get(f"/s/PDSS/materials/{seeded['pdf']}").status_code == 404


def test_pdf_route_refuses_symlink_out_of_materials(client, seeded, home):
    (home / "secret.pdf").write_bytes(b"%PDF-secret")
    link = Subject("PDSS").materials_dir / "lec.pdf"
    link.unlink()
    link.symlink_to(home / "secret.pdf")
    assert client.get(f"/s/PDSS/materials/{seeded['pdf']}").status_code == 404


def test_pdf_route_404_when_file_missing_from_disk(client, seeded):
    (Subject("PDSS").materials_dir / "lec.pdf").unlink()
    assert client.get(f"/s/PDSS/materials/{seeded['pdf']}").status_code == 404


def test_every_route_refuses_writes(client, seeded):
    for path in (
        "/",
        "/s/PDSS",
        "/api/subjects",
        "/api/subjects/PDSS/tree",
        f"/s/PDSS/materials/{seeded['pdf']}",
    ):
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(path).status_code == 405, (method, path)


def test_importing_web_app_pulls_in_no_ml_or_retrieval():
    """A subprocess: see tests/mcp/test_mcp_server.py for why not sys.modules surgery."""
    probe = (
        "import sys, groundly.web.app;"
        "print(','.join(m for m in ('sentence_transformers', 'torch', 'FlagEmbedding',"
        " 'groundly.retrieval') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"loaded by the page server: {result.stdout.strip()}"


def test_tree_read_leaves_the_store_unchanged(client, seeded):
    held = store.connect(subject_dir("PDSS") / "store.db")
    try:
        before = (
            held.execute("PRAGMA data_version").fetchone()[0],
            client.get("/api/subjects/PDSS/tree").json(),
        )
        client.get("/api/subjects")
        client.get(f"/s/PDSS/materials/{seeded['pdf']}")
        after = (
            held.execute("PRAGMA data_version").fetchone()[0],
            client.get("/api/subjects/PDSS/tree").json(),
        )
    finally:
        held.close()
    assert after == before
