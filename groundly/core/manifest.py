"""manifest.json — the interchange contract (docs/architecture/data-model.md).

The embedding pin (model + hf_revision + dim + normalization) decides whether shared
vectors transfer as-is; changing it is a full re-index migration, never a tweak.
"""

import sqlite3
from importlib.metadata import version as _package_version
from pathlib import Path

from pydantic import BaseModel

FORMAT_VERSION = 1
EMBEDDING_MODEL = "BAAI/bge-m3"
HF_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"  # pinned 2026-07-16 (P1 start)
EMBEDDING_DIM = 1024
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP = 0  # HybridChunker is structure-aware (split+merge); it has no token overlap


class Embedding(BaseModel):
    model: str = EMBEDDING_MODEL
    hf_revision: str = HF_REVISION
    dim: int = EMBEDDING_DIM
    dtype: str = "float32"
    normalized: bool = True


class Graphrag(BaseModel):
    version: str | None = None
    extraction_model: str | None = None


class Chunking(BaseModel):
    strategy: str = "docling-hybrid"
    max_tokens: int = CHUNK_MAX_TOKENS
    overlap: int = CHUNK_OVERLAP


class Counts(BaseModel):
    materials: int = 0
    chunks: int = 0


class Manifest(BaseModel):
    format_version: int = FORMAT_VERSION
    subject: str
    embedding: Embedding = Embedding()
    graphrag: Graphrag = Graphrag()
    chunking: Chunking = Chunking()
    counts: Counts = Counts()
    tool_version: str = ""

    @classmethod
    def new(cls, subject: str) -> "Manifest":
        return cls(subject=subject, tool_version=_package_version("groundly"))

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        return cls.model_validate_json(path.read_text())

    def save(self, path: Path) -> None:
        # write-then-rename: a Ctrl-C or concurrent save never leaves torn JSON behind
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(self.model_dump_json(indent=2) + "\n")
        tmp.replace(path)


def sync_counts(conn: sqlite3.Connection, manifest_path: Path) -> None:
    """Keep manifest counts in sync after every mutation (UC-03 acceptance)."""
    manifest = Manifest.load(manifest_path)
    manifest.counts.materials = conn.execute(
        "SELECT COUNT(*) FROM materials WHERE status = 'indexed'"
    ).fetchone()[0]
    manifest.counts.chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    manifest.save(manifest_path)
