"""CLI: the `search` verb (raw retrieval, zero-key)."""

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()


def test_search_works_with_no_config(retrievable_subject, monkeypatch, stub_embedder):
    """UC-02: no config.toml anywhere in this test's GROUNDLY_HOME."""
    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", stub_embedder)
    result = runner.invoke(app, ["search", retrievable_subject, "deadlock", "--no-rerank"])
    assert result.exit_code == 0, result.output
    assert "lec.pdf" in result.output


def test_search_no_rerank_plumbs_through(retrievable_subject, monkeypatch):
    captured = {}

    def fake_search(subject, query, *, k=8, rerank=True, **_):
        captured["rerank"] = rerank
        return []

    monkeypatch.setattr("groundly.retrieval.vector.search", fake_search)
    result = runner.invoke(app, ["search", retrievable_subject, "deadlock", "--no-rerank"])
    assert result.exit_code == 0, result.output
    assert captured["rerank"] is False


def test_search_model_download_error_fails_cleanly(retrievable_subject, monkeypatch):
    from groundly.llm.embeddings import ModelDownloadError

    def fake_search(*a, **k):
        raise ModelDownloadError("failed to load bge-m3: boom")

    monkeypatch.setattr("groundly.retrieval.vector.search", fake_search)
    result = runner.invoke(app, ["search", retrievable_subject, "deadlock"])
    assert result.exit_code == 1
    assert "failed to load bge-m3" in result.output


def test_search_uninitialized_subject_fails_with_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    result = runner.invoke(app, ["search", "NOPE", "q"])
    assert result.exit_code == 1
    assert "groundly index NOPE" in result.output


def _add_other(subject):
    from groundly.core.manifest import EMBEDDING_DIM
    from groundly.core.paths import subject_dir
    from groundly.core.store import SubjectStore
    from groundly.ingestion.extract import ChunkData

    SubjectStore(subject_dir(subject) / "store.db").add_indexed(
        "other.pdf",
        "b" * 64,
        1,
        [ChunkData("deadlock deadlock deadlock", "Other", 1, 10)],
        iter([([0.1] * EMBEDDING_DIM, {1: 0.5})]),
    )


def test_search_material_flag_scopes(retrievable_subject, monkeypatch, stub_embedder):
    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", stub_embedder)
    _add_other(retrievable_subject)
    result = runner.invoke(
        app, ["search", retrievable_subject, "deadlock", "--no-rerank", "-m", "lec.pdf"]
    )
    assert result.exit_code == 0, result.output
    assert "lec.pdf" in result.output and "other.pdf" not in result.output


def test_search_unknown_material_fails_naming_it(retrievable_subject):
    result = runner.invoke(app, ["search", retrievable_subject, "deadlock", "-m", "nope.pdf"])
    assert result.exit_code == 1
    assert "nope.pdf" in result.output and f"groundly list {retrievable_subject}" in result.output


def test_search_material_ignores_failed_row_with_same_name(
    retrievable_subject, monkeypatch, stub_embedder
):
    from groundly.core.paths import subject_dir
    from groundly.core.store import SubjectStore

    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", stub_embedder)
    SubjectStore(subject_dir(retrievable_subject) / "store.db").add_extraction_failed(
        "lec.pdf", "c" * 64, "err"
    )
    result = runner.invoke(
        app, ["search", retrievable_subject, "deadlock", "--no-rerank", "-m", "lec.pdf"]
    )
    assert result.exit_code == 0, result.output
    assert "ambiguous" not in result.output


def test_search_prints_hostile_chunk_fields_inert(retrievable_subject, monkeypatch):
    from groundly.retrieval.hits import Hit

    evil = "\x1b]52;c;cHduZWQ=\x07[/nosuch]\x9b31m"
    hit = Hit(
        chunk_id=1,
        text=f"first {evil}\nsecond",
        score=1.0,
        filename=f"a{evil}.pdf",
        page=1,
        heading_path=evil,
    )
    monkeypatch.setattr("groundly.retrieval.vector.search", lambda *a, **k: [hit])

    result = runner.invoke(app, ["search", retrievable_subject, "q"])

    assert result.exit_code == 0, result.output
    for char in ("\x1b", "\x07", "\x9b"):
        assert char not in result.output
    assert "[/nosuch]" in result.output
    assert "first" in result.output and "\nsecond" in result.output  # chunk lines survive


def _search_one(monkeypatch, subject, **fields):
    from groundly.retrieval.hits import Hit

    hit = Hit(
        **{
            "chunk_id": 1,
            "text": "body",
            "score": 1.0,
            "filename": "lec.pdf",
            "page": 1,
            "heading_path": None,
            **fields,
        }
    )
    monkeypatch.setattr("groundly.retrieval.vector.search", lambda *a, **k: [hit])
    return runner.invoke(app, ["search", subject, "q"])


def test_search_prints_a_hostile_page_inert(retrievable_subject, monkeypatch):
    result = _search_one(
        monkeypatch, retrievable_subject, page="\x1b]52;c;cHduZWQ=[link=https://evil.example]x"
    )

    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output
    assert "[link=https://evil.example]" in result.output


def test_search_never_forms_a_tag_across_fields(retrievable_subject, monkeypatch):
    result = _search_one(
        monkeypatch, retrievable_subject, filename="[red", page=None, heading_path="secret]"
    )

    assert result.exit_code == 0, result.output
    assert "[red" in result.output and "secret]" in result.output


def test_search_never_forms_a_tag_across_lines(retrievable_subject, monkeypatch):
    crash = _search_one(monkeypatch, retrievable_subject, text="[/x\ny]")
    swallowed = _search_one(monkeypatch, retrievable_subject, text="A = [a,\nb] follows")

    assert crash.exit_code == 0, crash.output
    assert "[a," in swallowed.output and "b] follows" in swallowed.output
