"""manifest.json — the interchange contract (docs/architecture/data-model.md).

The embedding pin (model + hf_revision + dim + dtype + normalization) decides whether
shared vectors transfer as-is; changing it is a full re-index migration, never a tweak.
"""

import re
import sqlite3
from importlib.metadata import version as _package_version
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, field_validator

FORMAT_VERSION = 1
EMBEDDING_MODEL = "BAAI/bge-m3"
HF_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"  # pinned 2026-07-16 (P1 start)
EMBEDDING_DIM = 1024
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP = 0  # HybridChunker is structure-aware (split+merge); it has no token overlap
TITLE_MAX_CHARS = 120
# Unicode's Default_Ignorable_Code_Point plus the blank braille cell. str.isprintable accepts
# many of these, and a run of them can carry text a host model reads and a student never sees.
_INVISIBLE = re.compile(
    "[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e"
    "\u2060-\u206f\u2800\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\U000e0000-\U000e0fff]"
)


def validate_title(title: str) -> str:
    """A subject's display label, stripped. It reaches terminals, the page and the host model
    from bundles a stranger may have written, so it stays one short line of visible text."""
    title = title.strip()
    if len(title) > TITLE_MAX_CHARS:
        raise ValueError(f"title is {len(title)} characters; the limit is {TITLE_MAX_CHARS}")
    if not title.isprintable() or _INVISIBLE.search(title):
        raise ValueError("title contains control or invisible characters")
    return title


_OCR_LANG = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")


def validate_ocr_lang(lang: str) -> str:
    """An OCR language name. From a bundle it reaches terminals and the extraction worker's
    argv, so it stays one bare token: no option prefix, console markup or control bytes."""
    if not _OCR_LANG.fullmatch(lang):
        raise ValueError(
            f"invalid OCR language {lang!r} — use up to 32 letters, digits, '-' or '_', "
            "starting with a letter"
        )
    return lang


class Embedding(BaseModel):
    model: str = EMBEDDING_MODEL
    hf_revision: str = HF_REVISION
    dim: int = EMBEDDING_DIM
    dtype: str = "float16"
    normalized: bool = True


class Chunking(BaseModel):
    strategy: str = "docling-hybrid"
    max_tokens: int = CHUNK_MAX_TOKENS
    overlap: int = CHUNK_OVERLAP


class Ocr(BaseModel):
    engine: str = "rapidocr-onnxruntime"
    # [] = bundled default model set
    lang: list[Annotated[str, AfterValidator(validate_ocr_lang)]] = Field(default_factory=list)


class Counts(BaseModel):
    materials: int = 0
    chunks: int = 0


class Manifest(BaseModel):
    format_version: int = FORMAT_VERSION
    subject: str
    title: str = ""  # display only: commands, paths and citations always use `subject`
    embedding: Embedding = Embedding()
    chunking: Chunking = Chunking()
    ocr: Ocr = Ocr()
    counts: Counts = Counts()
    tool_version: str = ""

    _title_is_valid = field_validator("title")(validate_title)

    @classmethod
    def new(cls, subject: str) -> "Manifest":
        return cls(subject=subject, tool_version=_package_version("groundly"))

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        # write-then-rename: a Ctrl-C or concurrent save never leaves torn JSON behind
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


def sync_counts(conn: sqlite3.Connection, manifest_path: Path) -> None:
    """Keep manifest counts in sync after every mutation (UC-03 acceptance)."""
    manifest = Manifest.load(manifest_path)
    manifest.counts.materials = conn.execute(
        "SELECT COUNT(*) FROM materials WHERE status = 'indexed'"
    ).fetchone()[0]
    manifest.counts.chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    manifest.save(manifest_path)
