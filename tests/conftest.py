"""Shared fixtures for the ingestion pipeline/worker tests. Pytest auto-discovers
fixtures defined here for every test module in this directory — test modules
request them by parameter name, they never import from this file."""

import pytest

from groundly.core import store
from groundly.core.manifest import EMBEDDING_DIM
from groundly.core.paths import subject_dir
from groundly.core.subject import init_subject


class StubEmbedder:
    def __init__(self, fail_on: str | None = None):
        self.encoded: list[str] = []
        self.fail_on = fail_on

    def encode(self, texts):
        for t in texts:
            if self.fail_on and self.fail_on in t:
                raise RuntimeError("stub embedder failure")
        self.encoded.extend(texts)
        return [[0.1] * EMBEDDING_DIM for _ in texts], [{1: 0.5} for _ in texts]


@pytest.fixture
def stub_embedder():
    return StubEmbedder


@pytest.fixture
def subject(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    init_subject("TEST")
    return "TEST"


@pytest.fixture
def course(tmp_path):
    d = tmp_path / "course"
    d.mkdir()
    (d / "notes.txt").write_text("Deadlock needs mutual exclusion and circular wait to occur.")
    (d / "readme.md").write_text("# Deadlock\n\n## Conditions\n\nFour conditions must hold.\n")
    return d


@pytest.fixture
def connect():
    def _connect(subject):
        return store.connect(subject_dir(subject) / "store.db")

    return _connect


@pytest.fixture
def stub_extraction():
    def _stub_extraction():
        from groundly.ingestion.extract import ChunkData, Extraction

        return Extraction(pages=None, chunks=[ChunkData("stub text", None, None, 2)])

    return _stub_extraction


class StubExtractor:
    def __init__(self, extraction):
        self.extraction = extraction
        self.seen_paths = []
        self.seen_langs = []

    def extract(self, path, ocr_lang=None):
        self.seen_paths.append(path)
        self.seen_langs.append(ocr_lang)
        return self.extraction


@pytest.fixture
def stub_extractor(stub_extraction):
    def _stub_extractor(extraction=None):
        ext = extraction or stub_extraction()
        return StubExtractor(ext)

    return _stub_extractor
