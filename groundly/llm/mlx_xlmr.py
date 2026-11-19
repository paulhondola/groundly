"""bge-m3 and bge-reranker-v2-m3 on MLX (Apple Silicon): one hand-written XLM-R forward in f16.

Inputs are packed end to end instead of padded: positions restart per input and a
block-diagonal mask keeps each input's attention to itself. Importable without MLX:
`mlx` is imported inside functions only, so importing this never loads a framework.
"""

import functools
import threading
from pathlib import Path

import numpy as np

PACK_TOKENS = 1024  # tokens per packed forward pass
CACHE_LIMIT_BYTES = (
    1 << 30
)  # MLX's buffer cache is unbounded by default and grows until it thrashes
SPECIAL_IDS = {0, 1, 2, 3}  # XLM-R <s> <pad> </s> <unk>
CLS_ID, SEP_ID = 0, 2
EMBED_MAX_TOKENS = (
    512  # FlagEmbedding's default for queries and passages; every stored vector used it
)
RERANK_MAX_TOKENS = 512
RERANK_QUERY_MAX_TOKENS = RERANK_MAX_TOKENS * 3 // 4  # FlagReranker cuts the query first

# Every MLX forward and eval, embedder and reranker alike, runs under this one lock: they
# share MLX's default stream, so two overlapping tool calls must never evaluate at once.
_FORWARD_LOCK = threading.Lock()


@functools.cache
def mlx_available() -> bool:
    try:
        import mlx.core  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "use torch"
        return False
    return True


def pack_groups(lens: list[int], budget: int = PACK_TOKENS) -> list[list[int]]:
    """Consecutive indices grouped so each group's total length stays within `budget`;
    an input longer than the budget runs alone."""
    out: list[list[int]] = []
    total = 0
    for i, n in enumerate(lens):
        if out and total + n <= budget:
            out[-1].append(i)
            total += n
        else:
            out.append([i])
            total = n
    return out


def _starts(lens: list[int]) -> list[int]:
    return [int(s) for s in np.cumsum([0, *lens[:-1]])]


def _linear(w: dict, x, p: str):
    import mlx.core as mx

    return mx.addmm(w[p + ".bias"], x, w[p + ".weight"].T)


def forward(weights: dict, ids: list[int], lens: list[int], heads: int = 16):
    """[sum(lens), hidden] hidden states for inputs packed end to end."""
    import mlx.core as mx
    from mlx import nn

    w = weights
    n = len(ids)
    hidden = w["embeddings.word_embeddings.weight"].shape[1]
    head_dim = hidden // heads
    layers = sum(1 for k in w if k.endswith(".attention.self.query.weight"))

    def ln(x, p):
        return mx.fast.layer_norm(x, w[p + ".weight"], w[p + ".bias"], 1e-5)

    def split(x):
        return x.reshape(1, n, heads, head_dim).transpose(0, 2, 1, 3)

    pos = mx.array([p for length in lens for p in range(2, length + 2)])  # padding_idx + 1 on
    mask = None
    if len(lens) > 1:
        seg = mx.array([s for s, length in enumerate(lens) for _ in range(length)])
        mask = (seg[:, None] == seg[None, :])[None, None]
    x = ln(
        w["embeddings.word_embeddings.weight"][mx.array(ids)]
        + w["embeddings.position_embeddings.weight"][pos]
        + w["embeddings.token_type_embeddings.weight"][0],
        "embeddings.LayerNorm",
    )
    for layer in range(layers):
        p = f"encoder.layer.{layer}"
        q, k, v = (
            split(_linear(w, x, f"{p}.attention.self.{t}")) for t in ("query", "key", "value")
        )
        att = mx.fast.scaled_dot_product_attention(q, k, v, scale=head_dim**-0.5, mask=mask)
        att = att.transpose(0, 2, 1, 3).reshape(n, hidden)
        x = ln(
            _linear(w, att, f"{p}.attention.output.dense") + x, f"{p}.attention.output.LayerNorm"
        )
        h = nn.gelu(_linear(w, x, f"{p}.intermediate.dense"))
        x = ln(_linear(w, h, f"{p}.output.dense") + x, f"{p}.output.LayerNorm")
    return x


def _load(path: Path, prefix: str = "") -> dict:
    """f16 weights with `prefix` stripped; position-id buffers are skipped (positions are computed)."""
    import mlx.core as mx

    loaded = mx.load(str(path))
    assert isinstance(loaded, dict)  # a .safetensors file always loads as a dict
    w = {
        k.removeprefix(prefix): v.astype(mx.float16)
        for k, v in loaded.items()
        if not k.endswith("position_ids")
    }
    mx.eval(list(w.values()))
    return w


def _tokenizer(path: Path, max_tokens: int | None):
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(path))
    tok.no_padding()
    if max_tokens is None:
        tok.no_truncation()
    else:
        tok.enable_truncation(max_tokens)
    return tok


def rerank_ids(tok, query: str, passage: str) -> list[int]:
    """FlagReranker's pair input: the query cut to RERANK_QUERY_MAX_TOKENS, then
    `<s> q </s></s> p </s>` with only the passage cut to fit RERANK_MAX_TOKENS."""
    q = tok.encode(query, add_special_tokens=False).ids[:RERANK_QUERY_MAX_TOKENS]
    p = tok.encode(passage, add_special_tokens=False).ids[: RERANK_MAX_TOKENS - 4 - len(q)]
    return [CLS_ID, *q, SEP_ID, SEP_ID, *p, SEP_ID]


class MlxBgeM3:
    """Duck-types FlagEmbedding's `BGEM3FlagModel.encode`; packing replaces `batch_size`."""

    def __init__(self, model_dir: Path, tokenizer_json: Path) -> None:
        import mlx.core as mx

        mx.set_cache_limit(CACHE_LIMIT_BYTES)
        self._w = _load(model_dir / "model.safetensors")
        head = _load(model_dir / "sparse_linear.safetensors")
        self._sparse_w, self._sparse_b = head["weight"], head["bias"]
        self._tok = _tokenizer(tokenizer_json, EMBED_MAX_TOKENS)

    def encode(
        self, texts, batch_size: int = 1, return_dense: bool = True, return_sparse: bool = True
    ):
        import mlx.core as mx
        from mlx import nn

        # rstrip: transformers' bge-m3 tokenizer (what FlagEmbedding runs) emits no token for
        # trailing whitespace, where the raw tokenizer.json emits "▁" before </s>
        ids = [e.ids for e in self._tok.encode_batch([t.rstrip() for t in texts])] if texts else []
        dense = np.zeros(
            (len(ids), self._w["embeddings.word_embeddings.weight"].shape[1]), np.float32
        )
        sparse: list[dict[int, float]] = []
        for group in pack_groups([len(i) for i in ids]):
            lens = [len(ids[i]) for i in group]
            starts = _starts(lens)
            with _FORWARD_LOCK:
                x = forward(self._w, [t for i in group for t in ids[i]], lens)
                cls = x[mx.array(starts)].astype(mx.float32)
                sp = nn.relu(mx.addmm(self._sparse_b, x, self._sparse_w.T)).astype(mx.float32)
                mx.eval(cls, sp)
                cls_np, sp_np = np.array(cls), np.array(sp)[:, 0]
            for k, i in enumerate(group):
                dense[i] = cls_np[k] / np.linalg.norm(cls_np[k])
                weights: dict[int, float] = {}
                for tid, wt in zip(
                    ids[i], sp_np[starts[k] : starts[k] + lens[k]].tolist(), strict=True
                ):
                    if tid in SPECIAL_IDS or wt <= 0:
                        continue
                    weights[tid] = max(weights.get(tid, 0.0), wt)
                sparse.append(weights)
        return {"dense_vecs": dense, "lexical_weights": sparse}


class MlxReranker:
    """Duck-types FlagEmbedding's `FlagReranker.compute_score`: raw logits, one per pair."""

    def __init__(self, model_dir: Path) -> None:
        import mlx.core as mx

        mx.set_cache_limit(CACHE_LIMIT_BYTES)
        self._w = _load(model_dir / "model.safetensors", "roberta.")
        self._tok = _tokenizer(model_dir / "tokenizer.json", None)

    def compute_score(self, pairs, batch_size: int = 1) -> list[float]:
        import mlx.core as mx

        ids = [rerank_ids(self._tok, q, p) for q, p in pairs]
        scores: list[float] = []
        for group in pack_groups([len(i) for i in ids]):
            lens = [len(ids[i]) for i in group]
            with _FORWARD_LOCK:
                x = forward(self._w, [t for i in group for t in ids[i]], lens)
                cls = x[mx.array(_starts(lens))]
                h = mx.tanh(_linear(self._w, cls, "classifier.dense"))
                s = _linear(self._w, h, "classifier.out_proj").astype(mx.float32)
                mx.eval(s)
                scores.extend(np.array(s)[:, 0].tolist())
        return scores
