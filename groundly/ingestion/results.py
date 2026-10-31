"""Pipeline result types — the canonical home for status constants, `FileResult`,
and the `OnEvent` callback alias, shared by the pipeline itself and its callers
(CLI, tests)."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    """FileResult.status values — a str subclass, so it compares equal to and is
    stored in store.db as the plain strings below without any conversion."""

    INDEXED = "indexed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    SKIPPED_UNSUPPORTED = "skipped_unsupported"
    SKIPPED_FAILED = "skipped_failed"  # failed on an earlier run; `remove` it to retry
    EXTRACTION_FAILED = "extraction_failed"  # terminal, recorded in store.db
    ERROR = "error"  # transient (e.g. embedder crash), no row recorded


OnEvent = Callable[[Path, str], None]


@dataclass
class FileResult:
    path: Path
    status: Status
    detail: str | None = None
    chunks: int = 0
