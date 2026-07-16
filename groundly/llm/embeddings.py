"""bge-m3 embedding: dense (1024-d, normalized) + learned sparse from one forward pass.

Lives in llm/ because embedding clients are constructed only here (overview.md module
rules); it is local and key-free, so no cost metering applies. Lazy-loaded — never at
import/spawn time (.claude/rules/architecture.md). The model is
resolved at the pinned hf_revision via snapshot_download, which is the interchange
compatibility contract: same pin ⇒ shared vectors transfer as-is.
"""

import sys
from typing import Protocol

from groundly.core.manifest import EMBEDDING_MODEL, HF_REVISION

SparseWeights = dict[int, float]


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[SparseWeights]]: ...


class BgeM3Embedder:
    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is None:
            from pathlib import Path

            from huggingface_hub import snapshot_download

            try:
                # a snapshot can exist with only tokenizer files (the extraction
                # worker caches those) — "cached" means the weights are present
                local = snapshot_download(
                    EMBEDDING_MODEL, revision=HF_REVISION, local_files_only=True
                )
                if not any(
                    (Path(local) / name).exists()
                    for name in ("model.safetensors", "pytorch_model.bin")
                ):
                    raise FileNotFoundError("model weights not cached")
            except Exception:
                print(
                    f"downloading {EMBEDDING_MODEL} (one-time, ~2.3 GB) …",
                    file=sys.stderr,
                )
                local = snapshot_download(EMBEDDING_MODEL, revision=HF_REVISION)

            from FlagEmbedding import BGEM3FlagModel

            self._model = BGEM3FlagModel(local, use_fp16=False)
        return self._model

    def encode(self, texts: list[str]) -> tuple[list[list[float]], list[SparseWeights]]:
        out = self._load().encode(texts, return_dense=True, return_sparse=True)
        dense = [vec.tolist() for vec in out["dense_vecs"]]
        sparse = [
            {int(token_id): float(weight) for token_id, weight in weights.items()}
            for weights in out["lexical_weights"]
        ]
        return dense, sparse
