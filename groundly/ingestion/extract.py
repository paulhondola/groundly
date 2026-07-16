"""Parent side of extraction: spawn the worker, enforce a wall-clock timeout,
map failures to specific user-facing causes (conventions: never generic errors).

security.md §3 controls: argv exec (no shell), temp working directory, wall-clock
timeout, output size cap — the worker's stdout is discarded, stderr goes to a file
on disk (never an in-memory buffer) and only its tail is ever read."""

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from groundly.ingestion import extract_worker

EXTRACT_TIMEOUT_SECONDS = 300
STDERR_TAIL_BYTES = 4096
MAX_EXTRACTION_JSON_BYTES = 200 * 1024 * 1024  # far beyond any real textbook


@dataclass
class ChunkData:
    text: str
    heading_path: str | None
    page: int | None
    token_count: int


@dataclass
class Extraction:
    pages: int | None
    chunks: list[ChunkData]


class ExtractionFailure(Exception):
    """reason is user-facing and names the specific cause."""


class ModelUnavailable(Exception):
    """The worker couldn't load its model (uncached + offline, HF rate-limit, missing
    dep). Transient, not the document's fault — the pipeline retries, records no row."""


def _stderr_tail(path: Path) -> str:
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - STDERR_TAIL_BYTES))
        lines = [ln for ln in f.read().decode(errors="replace").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def extract(path: Path, timeout: float = EXTRACT_TIMEOUT_SECONDS) -> Extraction:
    with tempfile.TemporaryDirectory() as tmp:
        out_json = Path(tmp) / "extraction.json"
        stderr_path = Path(tmp) / "stderr.log"
        with open(stderr_path, "wb") as stderr_file:
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "groundly.ingestion.extract_worker",
                        str(
                            path.resolve()
                        ),  # worker runs with cwd=tmp; relative paths must survive
                        str(out_json),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                    cwd=tmp,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                raise ExtractionFailure(f"extraction timed out after {int(timeout)}s") from None

        if proc.returncode == extract_worker.EXIT_MODEL_UNAVAILABLE:
            raise ModelUnavailable(_stderr_tail(stderr_path) or "extractor model unavailable")
        if proc.returncode == extract_worker.EXIT_NO_TEXT:
            if path.suffix.lower() == ".pdf":
                raise ExtractionFailure("scanned PDF — not supported")
            raise ExtractionFailure("no extractable text")
        if proc.returncode != 0:
            tail = _stderr_tail(stderr_path) or f"exit code {proc.returncode}"
            raise ExtractionFailure(f"parser failed: {tail}")

        if out_json.stat().st_size > MAX_EXTRACTION_JSON_BYTES:
            raise ExtractionFailure("extraction output too large")
        data = json.loads(out_json.read_text())
        return Extraction(pages=data["pages"], chunks=[ChunkData(**c) for c in data["chunks"]])
