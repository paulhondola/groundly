"""bge-m3 embedding: dense (1024-d, normalized) + learned sparse from one forward pass.

Local and key-free, lazy-loaded (never at MCP spawn), resolved at the pinned hf_revision.
The pin is the interchange contract: the same pin means shared vectors transfer as-is.
"""

import gc
import os
import sys
import threading
import time
import weakref
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from groundly.core.manifest import EMBEDDING_MODEL, HF_REVISION

# Silence transformers' advisory warnings. The env var, not logging.setLevel: transformers
# resets its logger level on first (lazy) import, while the env var is read per call.
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")

SparseWeights = dict[int, float]


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
    except Exception:  # noqa: BLE001 - any hub failure means "not cached"
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


def remove_cached(model: str = EMBEDDING_MODEL) -> None:
    """Delete `model` from the local Hugging Face cache."""
    import shutil

    from huggingface_hub import scan_cache_dir

    for repo in scan_cache_dir().repos:
        if repo.repo_id == model:
            shutil.rmtree(repo.repo_path, ignore_errors=True)


IDLE_UNLOAD_SECONDS = 600  # an unused model gives its memory back after this long

# One lock for every LazyModel, so device cleanup never overlaps a load or the start of a
# use of any model. Reentrant: a finalized `encode_stream` generator ends its use under it.
_lock = threading.RLock()
_active = 0  # uses in progress across every LazyModel


def _expire(ref: "weakref.ref[LazyModel]") -> None:
    """Timer callback. Holds only a weak reference, so a dropped model is freed at once
    instead of living on until its timer fires."""
    model = ref()
    if model is not None:
        model._expire()


class LazyModel:
    """A model loaded on first use and dropped `idle_seconds` after its last use ends, so
    a long-lived process (`groundly start`, a host's stdio `groundly mcp`) does not hold
    GBs of weights between study sessions. Subclasses implement `_build()` and reach the
    model only through `with self._use() as model:`."""

    idle_seconds: float = IDLE_UNLOAD_SECONDS

    def __init__(self) -> None:
        self._model = None
        self._users = 0
        self._last_end = 0.0
        self._timer: threading.Timer | None = None

    def _build(self):
        raise NotImplementedError

    def _arm(self, delay: float) -> None:
        """Start the one idle timer; the caller holds `_lock`."""
        self._timer = threading.Timer(delay, _expire, (weakref.ref(self),))
        self._timer.daemon = True  # never keeps a one-shot CLI process alive
        self._timer.start()

    @contextmanager
    def _use(self):
        """Hold the model for one use. The idle window starts when the last use ends, and
        unload() never drops a model in use: freeing weights after the cache was emptied
        would strand them in it."""
        global _active
        with _lock:
            if self._model is None:
                self._model = self._build()
            self._users += 1
            _active += 1
            model = self._model
        try:
            yield model
        finally:
            with _lock:
                self._users -= 1
                _active -= 1
                self._last_end = time.monotonic()
                if self._timer is None:  # one timer per model, not one thread per use
                    self._arm(self.idle_seconds)

    def _expire(self) -> None:
        """Unload if the model sat unused for the whole window, else wait out the rest."""
        with _lock:
            if self._timer is not threading.current_thread():
                return  # a stale timer: the current one decides
            self._timer = None
            if self._model is None:
                return
            if self._users:
                self._arm(self.idle_seconds)
                return
            left = self._last_end + self.idle_seconds - time.monotonic()
            if left > 0:
                self._arm(left)
                return
            self.unload()

    def unload(self) -> None:
        """Drop the weights now and return the freed device memory to the system."""
        with _lock:
            if self._model is None or self._users:
                return  # in use: the end of that use re-arms the timer
            self._model = None
            if _active:
                return  # another model is mid-use; its own unload empties the cache
            gc.collect()
            torch = sys.modules.get("torch")  # a loaded model means torch is imported
            if torch is not None:
                # the caching allocators keep freed blocks until asked; this is what
                # actually shrinks the process
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()


# Inputs per forward pass: texts when embedding, query-passage pairs when reranking. A batch is
# padded to its longest input, and on Apple's MPS that padding costs more than batching saves:
# one input per pass is the fastest there.
# ponytail: one value for every device; CUDA likely prefers larger batches — make it
# per-device once one is measured.
FORWARD_BATCH = 1


class BgeM3Embedder(LazyModel):
    def _build(self):
        local = ensure_downloaded()
        try:
            from FlagEmbedding import BGEM3FlagModel

            return BGEM3FlagModel(str(local), use_fp16=True)
        except Exception as exc:
            raise ModelDownloadError(f"failed to load {EMBEDDING_MODEL}: {exc}") from exc

    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[SparseWeights]]:
        with self._use() as model:
            out = model.encode(
                texts, batch_size=FORWARD_BATCH, return_dense=True, return_sparse=True
            )
        dense = [vec.tolist() for vec in out["dense_vecs"]]
        sparse = [
            {int(token_id): float(weight) for token_id, weight in weights.items()}
            for weights in out["lexical_weights"]
        ]
        return dense, sparse

    def encode_stream(
        self, texts: list[str], batch_size: int = 64
    ) -> Iterator[tuple[Sequence[float], SparseWeights]]:
        """Memory-bounded index path: yield (dense_row, sparse) per text, encoding batch_size
        texts at a time. FlagEmbedding accumulates results for whatever list it is handed,
        so slicing caps peak RAM at one batch. Within a slice the model runs FORWARD_BATCH
        texts per pass. Dense rows stay numpy fp32, never boxed into list[float];
        sqlite_vec.serialize_float32 takes them directly."""
        with self._use() as model:
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                out = model.encode(
                    batch, batch_size=FORWARD_BATCH, return_dense=True, return_sparse=True
                )
                for vec, weights in zip(out["dense_vecs"], out["lexical_weights"], strict=True):
                    yield vec, {int(tid): float(weight) for tid, weight in weights.items()}


_shared: BgeM3Embedder | None = None


def shared_embedder() -> BgeM3Embedder:
    """Process-wide singleton, so one resident bge-m3 serves every production call site
    (retrieval, re-embedding) instead of each loading a GB-scale copy. Tests
    inject stubs via `embedder=`."""
    global _shared
    if _shared is None:
        _shared = BgeM3Embedder()
    return _shared
