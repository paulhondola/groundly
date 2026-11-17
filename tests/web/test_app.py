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
        assert "script-src 'self'; style-src 'self'; img-src 'self'" in csp
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
        {"subject": "EMPTY", "title": "", "has_tree": False},
        {"subject": "PDSS", "title": "", "has_tree": True},
    ]


def _set_title(name, title):
    subject = Subject(name)
    manifest = subject.load_manifest()
    manifest.title = title
    subject.save_manifest(manifest)


def test_api_reports_the_title_in_the_list_and_the_tree(client, seeded):
    _set_title("PDSS", "Parallel Systems")
    (entry,) = client.get("/api/subjects").json()
    assert entry["title"] == "Parallel Systems"
    assert client.get("/api/subjects/PDSS/tree").json()["title"] == "Parallel Systems"


def test_corrupt_manifest_loses_the_title_not_the_listing(client, seeded):
    Subject("PDSS").manifest_path.write_text("{not json")
    (entry,) = client.get("/api/subjects").json()
    assert entry == {"subject": "PDSS", "title": "", "has_tree": True}
    assert client.get("/api/subjects/PDSS/tree").json()["title"] == ""


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


# --- tests on the page -------------------------------------------------------------------

ORIGIN = {"Origin": "http://127.0.0.1"}


def _bank(seeded):
    """Two single-answer questions and one open on the Locks leaf; returns their stored rows."""
    s = SubjectStore(subject_dir("PDSS") / "store.db")
    leaf = s.tree()[0]["id"]
    _, chunk = _index("PDSS", "extra.pdf", "c" * 64)
    options = [("mutual exclusion", True), ("a GPU", False)]
    s.add_questions(
        leaf,
        [
            store.QuestionWrite("single", "What does a lock give?", None, None, options, [chunk]),
            store.QuestionWrite("single", "<script>x</script>", None, None, options, [chunk]),
            store.QuestionWrite("open", "Why lock?", None, "To keep data consistent.", [], [chunk]),
        ],
    )
    return leaf, s.questions()


def _key(q):
    return [o["id"] for o in q["options"] if o["correct"]]


def _post(client, body, headers=ORIGIN):
    return client.post("/api/subjects/PDSS/attempts", json=body, headers=headers)


def _attempt(q, selected, token="token-0001", topic_id=None):
    return {
        "token": token,
        "topic_id": topic_id,
        "answers": [{"question_id": q["id"], "selected": selected}],
    }


def test_a_drawn_test_holds_no_key(client, seeded):
    leaf, _ = _bank(seeded)
    r = client.get(f"/api/subjects/PDSS/test?topic={leaf}&n=5")
    assert r.status_code == 200
    assert len(r.json()["questions"]) == 3
    assert "correct" not in r.text and "reference_answer" not in r.text
    assert len(client.get("/api/subjects/PDSS/test?n=5").json()["questions"]) == 3


def test_a_bad_draw_is_a_400_with_its_reason(client, seeded):
    _bank(seeded)
    r = client.get("/api/subjects/PDSS/test?n=7")
    assert r.status_code == 400 and r.json()["error"] == "invalid_text"
    assert client.get("/api/subjects/PDSS/test?topic=999&n=5").json()["error"] == "unknown_topic_id"
    assert client.get("/api/subjects/PDSS/test?n=five").status_code == 400


def test_submit_grades_and_the_result_shows_the_key_and_source(client, seeded):
    leaf, (q, *_) = _bank(seeded)
    r = _post(client, _attempt(q, _key(q), topic_id=leaf))
    assert r.status_code == 201
    result = client.get(f"/api/subjects/PDSS/attempts/{r.json()['id']}").json()
    assert result["score"] == {"correct": 1, "graded": 1, "awaiting": 0}
    (answer,) = result["answers"]
    assert answer["state"] == "correct"
    assert [o["correct"] for o in answer["options"]] == [True, False]
    assert answer["sources"][0]["filename"] == "extra.pdf"


def test_tree_nodes_carry_bank_size_and_mastery(client, seeded):
    _, (q, *_) = _bank(seeded)
    _post(client, _attempt(q, _key(q)))
    (node,) = client.get("/api/subjects/PDSS/tree").json()["tree"]
    assert node["progress"]["questions"] == 3
    assert node["progress"]["answered"] == 1
    assert node["progress"]["state"] == "in_progress"


@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 403), ({"Origin": "http://evil.example"}, 403), ({"Origin": "null"}, 403)],
)
def test_a_submit_from_elsewhere_is_refused_and_writes_nothing(client, seeded, headers, status):
    _, (q, *_) = _bank(seeded)
    assert _post(client, _attempt(q, _key(q)), headers=headers).status_code == status
    assert client.get("/api/subjects/PDSS/attempts").json() == []


def test_a_form_encoded_submit_is_refused(client, seeded):
    _bank(seeded)
    r = client.post("/api/subjects/PDSS/attempts", data={"token": "token-0001"}, headers=ORIGIN)
    assert r.status_code == 415
    assert client.get("/api/subjects/PDSS/attempts").json() == []


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"token": "short", "topic_id": None, "answers": []},
        {"token": "has space in it", "topic_id": None, "answers": []},
        {"token": "x" * 65, "topic_id": None, "answers": []},
        {"token": "token-0001", "topic_id": "1", "answers": []},
        {"token": "token-0001", "topic_id": True, "answers": []},
        {"token": "token-0001", "topic_id": None, "answers": "all"},
        {"token": "token-0001", "topic_id": None},
    ],
)
def test_a_malformed_body_or_token_is_a_400(client, seeded, body):
    _bank(seeded)
    r = _post(client, body)
    assert r.status_code == 400 and r.json()["error"]
    assert client.get("/api/subjects/PDSS/attempts").json() == []


def test_a_body_that_is_not_json_is_a_400(client, seeded):
    r = client.post(
        "/api/subjects/PDSS/attempts",
        content=b"{not json",
        headers={**ORIGIN, "Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_a_removed_question_refusal_reaches_the_page_with_its_cause(client, seeded):
    _, (q, *_) = _bank(seeded)
    SubjectStore(subject_dir("PDSS") / "store.db").remove_questions([q["id"]])
    r = _post(client, _attempt(q, []))
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_question" and "removed" in r.json()["detail"]


def test_a_busy_database_is_a_503_naming_it(client, seeded, monkeypatch):
    import sqlite3

    from groundly.agents import quiz

    _, (q, *_) = _bank(seeded)

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(quiz, "submit_attempt", locked)
    r = _post(client, _attempt(q, _key(q)))
    assert r.status_code == 503 and "database is busy" in r.json()["detail"]


def test_the_same_token_twice_returns_the_same_attempt(client, seeded):
    _, (q, *_) = _bank(seeded)
    first = _post(client, _attempt(q, _key(q))).json()["id"]
    assert _post(client, _attempt(q, _key(q))).json()["id"] == first
    assert len(client.get("/api/subjects/PDSS/attempts").json()) == 1


def test_history_lists_attempts_newest_first(client, seeded):
    leaf, (q, *_) = _bank(seeded)
    first = _post(client, _attempt(q, _key(q), topic_id=leaf)).json()["id"]
    second = _post(client, _attempt(q, [], token="token-0002")).json()["id"]
    history = client.get("/api/subjects/PDSS/attempts").json()
    assert [h["id"] for h in history] == [second, first]
    assert history[1]["scope_title"] == "Locks"


def test_an_unknown_attempt_is_404(client, seeded):
    assert client.get("/api/subjects/PDSS/attempts/404").status_code == 404
    assert client.get("/api/subjects/NOPE/attempts").status_code == 404


def test_test_and_result_pages_serve_the_shell_under_the_csp(client, seeded):
    for path in ("/s/PDSS/test", "/s/PDSS/attempts/1"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert "/static/page.js" in r.text
    assert client.get("/s/NOPE/test").status_code == 404


def test_the_write_route_takes_only_post(client, seeded):
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)("/api/subjects/PDSS/attempts").status_code == 405
    for path in ("/api/subjects/PDSS/test", "/api/subjects/PDSS/attempts/1", "/s/PDSS/test"):
        assert client.post(path, headers=ORIGIN).status_code == 405, path


def test_the_page_writes_no_rows_to_store_db(client, seeded):
    leaf, _questions = _bank(seeded)
    held = store.connect(subject_dir("PDSS") / "store.db")
    try:
        before = held.execute("PRAGMA data_version").fetchone()[0]
        drawn = client.get(f"/api/subjects/PDSS/test?topic={leaf}&n=5").json()["questions"]
        answers = [
            {"question_id": d["id"], "text": "Because."}
            if d["kind"] == "open"
            else {"question_id": d["id"], "selected": [d["options"][0]["id"]]}
            for d in drawn
        ]
        attempt = _post(client, {"token": "token-0001", "topic_id": leaf, "answers": answers})
        assert attempt.status_code == 201
        client.get(f"/api/subjects/PDSS/attempts/{attempt.json()['id']}")
        client.get("/api/subjects/PDSS/attempts")
        client.get("/api/subjects/PDSS/tree")
        after = held.execute("PRAGMA data_version").fetchone()[0]
    finally:
        held.close()
    assert after == before


def test_serving_a_test_loads_no_model(client, seeded, monkeypatch):
    from groundly.llm import embeddings

    def boom(*_a, **_k):
        raise AssertionError("a model was loaded")

    monkeypatch.setattr(embeddings, "shared_embedder", boom)
    _, (q, *_) = _bank(seeded)
    assert client.get("/api/subjects/PDSS/test?n=5").status_code == 200
    assert _post(client, _attempt(q, _key(q))).status_code == 201


def test_a_version_1_store_still_shows_its_map(client, seeded):
    """A subject indexed before the question tables: opening it adds them in place."""
    _tamper(
        "PDSS",
        "DROP TRIGGER question_citations_ad",
        "DROP TABLE question_citations",
        "DROP TABLE question_options",
        "DROP TABLE questions",
        "PRAGMA user_version = 1",
    )
    (node,) = client.get("/api/subjects/PDSS/tree").json()["tree"]
    assert node["title"] == "Locks" and node["progress"]["state"] == "not_started"


def test_result_sources_say_whether_their_pdf_opens(client, seeded):
    _, (q, *_) = _bank(seeded)
    attempt = _post(client, _attempt(q, _key(q))).json()["id"]
    (answer,) = client.get(f"/api/subjects/PDSS/attempts/{attempt}").json()["answers"]
    assert answer["sources"][0]["pdf"] is False  # extra.pdf is indexed but its file is not stored
    (Subject("PDSS").materials_dir / "extra.pdf").write_bytes(PDF_BYTES)
    (answer,) = client.get(f"/api/subjects/PDSS/attempts/{attempt}").json()["answers"]
    assert answer["sources"][0]["pdf"] is True


# --- slice 2 review --------------------------------------------------------------------


def test_a_deeply_nested_body_is_a_400_not_a_crash(client, seeded):
    r = client.post(
        "/api/subjects/PDSS/attempts",
        content=b"[" * 200_000,
        headers={**ORIGIN, "Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_an_id_too_large_for_sqlite_is_refused_not_a_crash(client, seeded):
    _bank(seeded)
    body = {
        "token": "token-0001",
        "topic_id": None,
        "answers": [{"question_id": 2**70, "selected": []}],
    }
    assert _post(client, body).status_code == 400
    assert _post(client, {**body, "topic_id": 2**70}).status_code == 400
    assert client.get(f"/api/subjects/PDSS/attempts/{2**70}").status_code == 404
    assert client.get(f"/api/subjects/PDSS/test?topic={2**70}&n=5").status_code == 400


def test_an_oversized_body_is_refused_before_it_is_read(client, seeded):
    r = client.post(
        "/api/subjects/PDSS/attempts",
        content=b'{"token": "' + b"x" * 300_000 + b'"}',
        headers={**ORIGIN, "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_a_body_without_topic_id_is_a_whole_course_attempt(client, seeded):
    _, (q, *_) = _bank(seeded)
    body = _attempt(q, _key(q))
    del body["topic_id"]
    assert _post(client, body).status_code == 201


def test_the_map_still_shows_when_progress_db_is_unreadable(client, seeded):
    (subject_dir("PDSS") / "progress.db").unlink()
    r = client.get("/api/subjects/PDSS/tree")
    assert r.status_code == 200 and r.json()["tree"][0]["progress"] is None


def test_groundly_localhost_host_header_is_accepted(client, seeded):
    r = client.get("/api/subjects", headers={"Host": "groundly.localhost:8765"})
    assert r.status_code == 200


def test_submit_accepts_the_groundly_localhost_origin(client, seeded):
    _, (q, *_) = _bank(seeded)
    headers = {"Host": "groundly.localhost:8765", "Origin": "http://groundly.localhost:8765"}
    assert _post(client, _attempt(q, _key(q)), headers=headers).status_code == 201


# `groundly start`: the page with the MCP app mounted after it, as the CLI composes them.
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def start_client(seeded):
    from groundly.mcp.server import mcp
    from groundly.web.app import HOSTS

    mcp_app = mcp.http_app(path="/mcp", host_origin_protection="auto", allowed_hosts=HOSTS)
    with TestClient(create_app(mcp_app), base_url="http://groundly.localhost:8765") as c:
        yield c


def test_start_app_serves_page_and_mcp(start_client):
    page = start_client.get("/")
    assert page.status_code == 200 and "content-security-policy" in page.headers
    r = start_client.post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers


def test_start_app_mcp_refuses_foreign_host_and_origin(start_client):
    foreign_host = {**MCP_HEADERS, "Host": "evil.com"}
    foreign_origin = {**MCP_HEADERS, "Origin": "http://evil.com"}
    assert start_client.post("/mcp", json=INIT, headers=foreign_host).status_code == 400
    assert start_client.post("/mcp", json=INIT, headers=foreign_origin).status_code == 403
