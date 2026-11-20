"""groundly/llm/mlx_xlmr.py: packing and the packed XLM-R forward, on a tiny random model."""

import subprocess
import sys

import numpy as np
import pytest

from groundly.llm.mlx_xlmr import forward, mlx_available, pack_groups

needs_mlx = pytest.mark.skipif(not mlx_available(), reason="MLX needs Apple Silicon")


def test_pack_groups_respects_the_budget_and_keeps_order():
    assert pack_groups([600, 400, 30, 2000, 10], budget=1024) == [[0, 1], [2], [3], [4]]
    assert pack_groups([], budget=1024) == []


def test_mlx_is_unavailable_when_metal_cannot_evaluate(monkeypatch):
    """A Metal that imports but cannot eval must fall back to torch, not fail every search."""
    pytest.importorskip("mlx.core")
    import mlx.core as mx

    def broken(*a, **k):
        raise RuntimeError("no Metal device")

    monkeypatch.setattr(mx, "eval", broken)
    mlx_available.cache_clear()
    try:
        assert mlx_available() is False
    finally:
        mlx_available.cache_clear()


def test_importing_the_llm_layer_loads_no_framework():
    """Spawn stays cheap: the embedder, reranker and MLX modules import neither MLX nor torch."""
    code = (
        "import sys, groundly.llm.embeddings, groundly.llm.rerank;"
        "bad = [m for m in ('mlx.core', 'torch', 'FlagEmbedding', 'sentence_transformers')"
        " if m in sys.modules]; print(bad); sys.exit(bool(bad))"
    )
    proc = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _tiny_weights(layers: int, hidden: int, vocab: int) -> dict:
    """A random XLM-R with bge-m3's key names."""
    import mlx.core as mx

    rng = np.random.default_rng(0)

    def t(*shape, scale=0.1):
        return mx.array((rng.standard_normal(shape) * scale).astype(np.float16))

    def ln(p):
        return {p + ".weight": mx.ones((hidden,), mx.float16), p + ".bias": t(hidden)}

    def lin(p, out, inp):
        return {p + ".weight": t(out, inp), p + ".bias": t(out)}

    w = {
        "embeddings.word_embeddings.weight": t(vocab, hidden),
        "embeddings.position_embeddings.weight": t(64, hidden),
        "embeddings.token_type_embeddings.weight": t(1, hidden),
        **ln("embeddings.LayerNorm"),
    }
    for i in range(layers):
        p = f"encoder.layer.{i}"
        for name in ("query", "key", "value"):
            w |= lin(f"{p}.attention.self.{name}", hidden, hidden)
        w |= lin(f"{p}.attention.output.dense", hidden, hidden)
        w |= ln(f"{p}.attention.output.LayerNorm")
        w |= lin(f"{p}.intermediate.dense", 4 * hidden, hidden)
        w |= lin(f"{p}.output.dense", hidden, 4 * hidden)
        w |= ln(f"{p}.output.LayerNorm")
    return w


@needs_mlx
def test_packed_forward_equals_one_input_per_pass():
    w = _tiny_weights(layers=2, hidden=32, vocab=50)
    a, b = [0, 5, 7, 2], [0, 9, 2]
    packed = np.array(forward(w, a + b, [4, 3], heads=2))
    alone = np.concatenate(
        [np.array(forward(w, a, [4], heads=2)), np.array(forward(w, b, [3], heads=2))]
    )
    assert packed.shape == (7, 32)
    assert np.allclose(packed, alone, atol=1e-2)
