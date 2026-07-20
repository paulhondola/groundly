"""bge-m3 embedding: dense (1024-d, normalized) + learned sparse from one forward pass.

Lives in llm/ because embedding clients are constructed only here (overview.md module
rules); it is local and key-free, so no cost metering applies. Lazy-loaded — never at
import/spawn time (.claude/rules/architecture.md). The model is
resolved at the pinned hf_revision via snapshot_download, which is the interchange
compatibility contract: same pin ⇒ shared vectors transfer as-is.
"""

import os
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol

from groundly.core.manifest import EMBEDDING_MODEL, HF_REVISION

# suppress transformers' advisory warnings (e.g. the fast-tokenizer pad() notice).
# Must be the env var, not logging.setLevel("transformers"): transformers resets its
# root logger level on first (lazy) import, clobbering any level set here at import
# time; the env var is read per call, so ordering can't break it.
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

SparseWeights = dict[int, float]


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[SparseWeights]]: ...
    def encode_stream(
        self, texts: list[str], batch_size: int = ...
    ) -> Iterator[tuple[Sequence[float], SparseWeights]]: ...


class ModelDownloadError(Exception):
    """snapshot_download failed fetching bge-m3 (network/HF error); callers map this
    to a named cause, never a raw traceback."""


def cached_snapshot(model: str = EMBEDDING_MODEL, revision: str = HF_REVISION) -> Path | None:
    """Return the local snapshot dir if `model`'s weights are already cached, else None.

    Defaults to bge-m3; rerank.py passes the reranker's own (model, revision) pin to
    reuse this same cache-check logic."""
    from huggingface_hub import snapshot_download

    try:
        # a snapshot can exist with only tokenizer files (the extraction
        # worker caches those) — "cached" means the weights are present
        local = snapshot_download(model, revision=revision, local_files_only=True)
    except Exception:
        return None
    if not any(
        (Path(local) / name).exists() for name in ("model.safetensors", "pytorch_model.bin")
    ):
        return None
    return Path(local)


def ensure_downloaded(
    model: str = EMBEDDING_MODEL, revision: str = HF_REVISION, force: bool = False
) -> Path:
    """Ensure `model`'s weights are present in the local HF cache; return the snapshot dir.

    force=True skips the cache-hit fast path and always re-fetches — HF's own cache
    dedupes unchanged files, so this re-verifies rather than wiping and refetching.
    """
    from huggingface_hub import snapshot_download

    if not force:
        cached = cached_snapshot(model, revision)
        if cached is not None:
            return cached

    print(f"downloading {model} (one-time) …", file=sys.stderr)
    try:
        local = snapshot_download(model, revision=revision)
    except Exception as exc:
        raise ModelDownloadError(f"failed to download {model}: {exc}") from exc
    return Path(local)


def remove_cached() -> bool:
    """Delete bge-m3 from the local Hugging Face cache. Returns True if anything was removed."""
    import shutil

    from huggingface_hub import scan_cache_dir

    removed = False
    for repo in scan_cache_dir().repos:
        if repo.repo_id == EMBEDDING_MODEL:
            shutil.rmtree(repo.repo_path, ignore_errors=True)
            removed = True
    return removed


class BgeM3Embedder:
    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is None:
            local = ensure_downloaded()
            try:
                from FlagEmbedding import BGEM3FlagModel

                self._model = BGEM3FlagModel(str(local), use_fp16=True)
            except Exception as exc:
                raise ModelDownloadError(f"failed to load {EMBEDDING_MODEL}: {exc}") from exc
        return self._model

    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[SparseWeights]]:
        out = self._load().encode(texts, return_dense=True, return_sparse=True)
        dense = [vec.tolist() for vec in out["dense_vecs"]]
        sparse = [
            {int(token_id): float(weight) for token_id, weight in weights.items()}
            for weights in out["lexical_weights"]
        ]
        return dense, sparse

    def encode_stream(
        self, texts: list[str], batch_size: int = 64
    ) -> Iterator[tuple[Sequence[float], SparseWeights]]:
        """Memory-bounded index path: yield (dense_row, sparse) per text, running the
        model on batch_size texts at a time. FlagEmbedding pre-tokenizes *and*
        accumulates dense+sparse for whatever list it is handed, so encoding a whole
        document at once makes peak RAM scale with the document; slicing here caps it to
        one batch. Dense rows stay as numpy fp32 — never boxed into list[float] (8x, and
        sqlite_vec.serialize_float32 takes the numpy row directly)."""
        model = self._load()
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            out = model.encode(batch, batch_size=batch_size, return_dense=True, return_sparse=True)
            for vec, weights in zip(out["dense_vecs"], out["lexical_weights"], strict=True):
                yield vec, {int(token_id): float(weight) for token_id, weight in weights.items()}
