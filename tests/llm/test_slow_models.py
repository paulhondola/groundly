"""Real-model checks — excluded by default (pyproject addopts); run: pytest -m slow.
First run downloads bge-m3 (~2.3 GB) to the HF cache."""

import subprocess
import sys

import pytest

from groundly.llm.embeddings import BgeM3Embedder

pytestmark = pytest.mark.slow


def test_bge_m3_dense_and_sparse_contract():
    from groundly.core.manifest import EMBEDDING_DIM
    from groundly.llm.embeddings import BgeM3Embedder

    dense, sparse = BgeM3Embedder().encode(["mutual exclusion in distributed systems"])
    assert len(dense) == len(sparse) == 1
    assert len(dense[0]) == EMBEDDING_DIM
    norm_sq = sum(x * x for x in dense[0])
    assert abs(norm_sq - 1.0) < 1e-3  # manifest contract: normalized
    assert sparse[0], "learned sparse weights must be non-empty"
    assert all(isinstance(t, int) and w > 0 for t, w in sparse[0].items())


def test_encode_stream_yields_one_numpy_vector_per_text_in_batches():
    """encode_stream is the memory-bounded index path: it yields (dense, sparse) per
    text, dense as a numpy row rather than list[float], running the model on batch_size
    texts at a time so peak never scales with the whole document."""
    import numpy as np

    from groundly.core.manifest import EMBEDDING_DIM
    from groundly.llm.embeddings import BgeM3Embedder

    texts = ["mutual exclusion", "deadlock", "semaphore", "monitor", "spinlock"]
    out = list(BgeM3Embedder().encode_stream(texts, batch_size=2))

    assert len(out) == len(texts)
    vec, weights = out[0]
    assert isinstance(vec, np.ndarray) and vec.shape == (EMBEDDING_DIM,)
    assert abs(float((vec * vec).sum()) - 1.0) < 1e-3  # normalized, same contract as encode
    assert weights and all(isinstance(t, int) and w > 0 for t, w in weights.items())


def test_bge_reranker_v2_m3_scores_relevant_pair_higher():
    from groundly.llm.rerank import BgeReranker

    scores = BgeReranker().compute_score(
        [
            ("what causes a deadlock?", "A deadlock needs mutual exclusion and circular wait."),
            ("what causes a deadlock?", "Semaphores coordinate producer-consumer queues."),
        ]
    )
    assert scores[0] > scores[1]


def test_cross_lingual_romanian_query_matches_english_deadlock_chunk(tmp_path, monkeypatch):
    """UC-02 criterion 2: a Romanian question over English-only slides retrieves the
    relevant chunk via the dense channel (docs/architecture/retrieval.md cross-lingual
    caveat — only the dense channel, not sparse/BM25, matches across languages)."""
    from groundly.core.paths import subject_dir
    from groundly.core.store import SubjectStore
    from groundly.ingestion.extract import ChunkData
    from groundly.llm.embeddings import BgeM3Embedder
    from groundly.retrieval.vector import VectorRetriever
    from tests.conftest import init_subject

    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    init_subject("PDA")

    embedder = BgeM3Embedder()
    texts = [
        "A deadlock requires mutual exclusion, hold and wait, no preemption, and circular wait.",
        "Semaphores coordinate access between producer and consumer threads.",
    ]
    dense, sparse = embedder.encode(texts)
    chunks = [ChunkData(t, None, i + 1, 10) for i, t in enumerate(texts)]
    SubjectStore(subject_dir("PDA") / "store.db").add_indexed(
        "slides.pdf", "a" * 64, 2, chunks, zip(dense, sparse)
    )

    store = SubjectStore(subject_dir("PDA") / "store.db")
    retriever = VectorRetriever(store, embedder=embedder, rerank=False)
    hits = retriever.retrieve("ce condiții sunt necesare pentru un deadlock?")
    assert hits
    assert "mutual exclusion" in hits[0].text


needs_mlx = pytest.mark.skipif(
    not __import__("groundly.llm.mlx_xlmr").llm.mlx_xlmr.mlx_available(),
    reason="MLX needs Apple Silicon",
)

_TOPICS = [
    ("excluderea mutuală", "mutual exclusion"),
    ("impasul (deadlock)", "deadlock"),
    ("semafoarele", "semaphores"),
    ("monitoarele", "monitors"),
    ("bariera de sincronizare", "the synchronization barrier"),
    ("problema producător-consumator", "the producer-consumer problem"),
    ("algoritmul lui Peterson", "Peterson's algorithm"),
    ("variabilele de condiție", "condition variables"),
]
_TEXTS = [
    t
    for ro, en in _TOPICS
    for t in (
        (
            f"Capitolul despre {ro}: firele de execuție accesează o resursă partajată, iar "
            f"secțiunea critică trebuie protejată pentru a evita condițiile de cursă."
        ),
        (
            f"This lecture covers {en}. Threads share memory, so every access to shared state "
            f"needs a protocol; we prove safety and liveness for {en} on slide 12."
        ),
        (
            f"{en.capitalize()} — exercise: implement it in C with pthreads and explain why "
            f"busy waiting wastes CPU cycles compared to blocking primitives."
        ),
        f"Întrebare de examen: definiți {ro} și dați un exemplu din sistemul de operare Linux.",
    )
][:31] + ["Sincronizare și concurență în sisteme distribuite. " * 1200]  # probes truncation at 512


def _mlx_embedder():
    from groundly.llm.convert import ensure_converted
    from groundly.llm.embeddings import ensure_downloaded
    from groundly.llm.mlx_xlmr import MlxBgeM3

    snapshot = ensure_downloaded()
    return MlxBgeM3(ensure_converted(snapshot), snapshot / "tokenizer.json")


def _top10(weights):
    return [t for t, _ in sorted(weights.items(), key=lambda kv: -kv[1])[:10]]


def _same_top10(a, b, tie=1e-3):
    """Top-10 token sets match, except where f16 noise swaps a near-tie across rank 10:
    a token in only one top-10 must sit within `tie` of the other side's 10th weight."""
    top_a, top_b = _top10(a), _top10(b)
    tenth_a, tenth_b = a[top_a[-1]], b[top_b[-1]]
    return all(abs(b.get(t, 0.0) - tenth_b) <= tie for t in set(top_a) - set(top_b)) and all(
        abs(a.get(t, 0.0) - tenth_a) <= tie for t in set(top_b) - set(top_a)
    )


@needs_mlx
def test_mlx_matches_torch_within_the_pin_tolerance():
    import numpy as np
    from FlagEmbedding import BGEM3FlagModel

    from groundly.llm.embeddings import ensure_downloaded

    assert len(_TEXTS) == 32
    mlx_out = _mlx_embedder().encode(_TEXTS)
    torch_out = BGEM3FlagModel(str(ensure_downloaded()), use_fp16=True).encode(
        _TEXTS, batch_size=1, return_dense=True, return_sparse=True
    )
    cosines = [float(np.dot(a, b)) for a, b in zip(mlx_out["dense_vecs"], torch_out["dense_vecs"])]
    assert min(cosines) >= 0.9997
    for m, t in zip(mlx_out["lexical_weights"], torch_out["lexical_weights"], strict=True):
        assert _same_top10(m, {int(k): float(v) for k, v in t.items()})


@needs_mlx
def test_mlx_reranker_keeps_flagreranker_order():
    from FlagEmbedding import FlagReranker

    from groundly.llm.embeddings import ensure_downloaded
    from groundly.llm.mlx_xlmr import MlxReranker
    from groundly.llm.rerank import RERANKER_HF_REVISION, RERANKER_MODEL

    snapshot = ensure_downloaded(RERANKER_MODEL, RERANKER_HF_REVISION)
    mlx_model = MlxReranker(snapshot)
    torch_model = FlagReranker(str(snapshot), use_fp16=False)
    passages = _TEXTS[:20]
    for query in ("ce este un deadlock?", "how does a semaphore work?", "Peterson's algorithm"):
        pairs = [(query, p) for p in passages]
        mine = mlx_model.compute_score(pairs)
        ref = torch_model.compute_score(pairs, batch_size=1)

        def top5(scores):
            return set(sorted(range(len(scores)), key=lambda i: -scores[i])[:5])

        assert top5(mine) == top5(ref)
        assert max(abs(a - b) for a, b in zip(mine, ref, strict=True)) <= 0.03


@needs_mlx
def test_mlx_reranker_truncates_a_long_query_like_flagreranker():
    """A query over 384 tokens is cut first, then only the passage is cut to fit 512."""
    from FlagEmbedding import FlagReranker
    from FlagEmbedding.utils.tokenizer_compat import prepare_for_model_compat

    from groundly.llm.embeddings import ensure_downloaded
    from groundly.llm.mlx_xlmr import MlxReranker, rerank_ids
    from groundly.llm.rerank import RERANKER_HF_REVISION, RERANKER_MODEL

    snapshot = ensure_downloaded(RERANKER_MODEL, RERANKER_HF_REVISION)
    mlx_model = MlxReranker(snapshot)
    torch_model = FlagReranker(str(snapshot), use_fp16=False)
    query = "De ce apare un deadlock când procesele așteaptă resurse? " * 60
    passages = _TEXTS[:20]
    hf = torch_model.tokenizer
    for p in passages:
        q_ids = hf(query, add_special_tokens=False, max_length=384, truncation=True)["input_ids"]
        p_ids = hf(p, add_special_tokens=False, max_length=512, truncation=True)["input_ids"]
        expected = prepare_for_model_compat(
            hf, q_ids, p_ids, truncation="only_second", max_length=512, padding=False
        )["input_ids"]
        assert rerank_ids(mlx_model._tok, query, p) == expected
    assert len(q_ids) == 384

    pairs = [(query, p) for p in passages]
    mine = mlx_model.compute_score(pairs)
    ref = torch_model.compute_score(pairs, batch_size=1)
    assert (
        sorted(range(20), key=lambda i: -mine[i])[:5]
        == sorted(range(20), key=lambda i: -ref[i])[:5]
    )
    # f16 against torch's f32 over full 512-token inputs: the ids match exactly, the
    # scores drift a little more than on short pairs
    assert max(abs(a - b) for a, b in zip(mine, ref, strict=True)) <= 0.05


@needs_mlx
def test_mlx_edge_inputs():
    import numpy as np

    model = _mlx_embedder()
    out = model.encode([])
    assert out["dense_vecs"].shape == (0, 1024) and out["lexical_weights"] == []
    out = model.encode(["", "x " * 20_000])
    assert out["dense_vecs"].shape == (2, 1024) and np.isfinite(out["dense_vecs"]).all()
    assert len(out["lexical_weights"]) == 2


@needs_mlx
def test_mlx_ignores_trailing_whitespace_like_flagembedding():
    """FlagEmbedding's tokenizer adds no token for trailing whitespace; 12 apd chunks end in it."""
    import numpy as np

    model = _mlx_embedder()
    dense = model.encode(["a | - |\n", "a | - |", "abc ", "abc"])["dense_vecs"]
    assert np.allclose(dense[0], dense[1]) and np.allclose(dense[2], dense[3])


@needs_mlx
def test_concurrent_encodes_and_reranks_equal_sequential():
    """Overlapping tool calls: embeds and reranks running at once give the sequential results."""
    from concurrent.futures import ThreadPoolExecutor

    import numpy as np

    from groundly.llm.embeddings import BgeM3Embedder
    from groundly.llm.rerank import BgeReranker

    m, r = BgeM3Embedder(), BgeReranker()
    texts = ["mutex", "semafor", "deadlock", "barieră"]
    pairs = [("ce este un deadlock?", t) for t in _TEXTS[:8]]
    expected_dense = m.encode(texts)[0]
    expected_scores = r.compute_score(pairs)

    def job(i):
        return ("embed", m.encode(texts)[0]) if i % 2 else ("rerank", r.compute_score(pairs))

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(job, range(12)))
    for kind, out in results:
        if kind == "embed":
            assert np.allclose(out, expected_dense, atol=1e-6)
        else:
            assert np.allclose(out, expected_scores, atol=1e-6)


@needs_mlx
def test_search_path_never_imports_torch():
    """Search, the topic gate and import re-embedding all reach the models through these two
    singletons, so one probe covers them. A subprocess: never pop torch from sys.modules."""
    probe = (
        "import sys; from groundly.llm.embeddings import shared_embedder;"
        "from groundly.llm.rerank import shared_reranker;"
        "shared_embedder().encode(['x']); list(shared_embedder().encode_stream(['y']));"
        "shared_reranker().compute_score([('a', 'b')]);"
        "print(','.join(m for m in ('torch', 'FlagEmbedding', 'sentence_transformers') if m in sys.modules))"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == ""


@needs_mlx
def test_idle_unload_returns_mlx_memory():
    import mlx.core as mx

    m = BgeM3Embedder()
    m.encode(["warm"])
    assert mx.get_active_memory() > 500 * 2**20
    m.unload()
    assert mx.get_active_memory() < 50 * 2**20 and mx.get_cache_memory() == 0
