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


class StubChat:
    """Scripted `ChatFn`: replies pop in call order (last reply repeats once exhausted),
    every call is recorded as (call_class, messages) for assertion. StubEmbedder returns
    identical vectors for every text, which can't exercise ranking — this stub can't
    either, but reruns of the *same* stub_chat instance across router+generation calls
    let a test script both classification and the final answer in one object."""

    def __init__(
        self,
        replies="not covered by the course materials",
        *,
        model="stub-model",
        tokens=10,
        cost_usd=None,
    ):
        self.replies = [replies] if isinstance(replies, str) else list(replies)
        self.calls: list[tuple[str, list[dict]]] = []
        self.model = model
        self.tokens = tokens
        self.cost_usd = cost_usd

    def __call__(self, call_class, messages, *, transport=None):
        from groundly.llm.chat import ChatResult

        self.calls.append((call_class, messages))
        i = min(len(self.calls) - 1, len(self.replies) - 1)
        return ChatResult(
            text=self.replies[i], tokens=self.tokens, cost_usd=self.cost_usd, model=self.model
        )


@pytest.fixture
def stub_chat():
    return StubChat


@pytest.fixture
def retrievable_subject(monkeypatch, tmp_path):
    """An initialized subject with hand-built orthogonal dense vectors and distinct
    sparse weights across 3 chunks, so retrieval ranking is actually exercised
    (StubEmbedder above returns identical vectors for every text — useless for this)."""
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    init_subject("TEST")

    from groundly.core.store import SQLiteSubjectStore
    from groundly.ingestion.extract import ChunkData

    chunks = [
        ChunkData("deadlock needs mutual exclusion to occur", "Intro > Deadlocks", 1, 10),
        ChunkData("semaphores and mutexes for synchronization", "Intro > Sync", 2, 10),
        ChunkData("deadlock deadlock deadlock circular wait condition", "Intro > Deadlocks", 3, 10),
    ]
    dense = [
        [1.0, 0.0] + [0.0] * (EMBEDDING_DIM - 2),
        [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2),
        [0.9, 0.1] + [0.0] * (EMBEDDING_DIM - 2),
    ]
    sparse = [{1: 0.9, 2: 0.1}, {3: 0.9}, {1: 0.4}]
    SQLiteSubjectStore(subject_dir("TEST") / "store.db").add_indexed(
        "lec.pdf", "a" * 64, 3, chunks, dense, sparse
    )
    return "TEST"
