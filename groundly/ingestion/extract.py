"""Parent side of extraction: drive one warm worker per indexing run and map its replies
to specific causes.

Subprocess controls: argv exec (no shell), temp working directory, a wall-clock timeout
that restarts on every finished page, output size cap. A worker that crashes or times out is replaced before the next
file. Worker stderr goes to a file on disk, never an in-memory buffer, and only the tail
written during the current file is read."""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Self

from groundly.core.config import IngestionSettings
from groundly.ingestion.formats import IMAGE_SUFFIXES

STDERR_TAIL_BYTES = 4096
MAX_EXTRACTION_JSON_BYTES = 200 * 1024 * 1024  # far beyond any real textbook
REAP_SECONDS = 5  # a worker given EOF or found dead gets this long to exit before a kill


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


def _stderr_tail(path: Path, start: int = 0) -> str:
    """The last non-empty line written to `path` at or after byte `start`."""
    with open(path, "rb") as f:
        f.seek(max(start, path.stat().st_size - STDERR_TAIL_BYTES))
        lines = [ln for ln in f.read().decode(errors="replace").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _pump(stream: IO[str], replies: queue.Queue) -> None:
    """Every reply line onto `replies`, then None at EOF. A thread, because Windows has no
    select() on pipes."""
    with stream:
        for line in stream:
            replies.put(line)
    replies.put(None)


def _reap(proc: subprocess.Popen) -> int:
    """Wait for a worker that was told to stop or found dead; kill it if it lingers."""
    try:
        return proc.wait(timeout=REAP_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait()


class SubprocessExtractor:
    """Extracts document content in a worker subprocess (Docling, RapidOCR), started on first
    use and kept for the run. Call close(), or use it as a context manager."""

    def __init__(
        self, settings: IngestionSettings | None = None, argv: list[str] | None = None
    ) -> None:
        settings = settings or IngestionSettings()
        self.timeout = settings.timeout_seconds
        self.max_image_pixels = settings.max_image_pixels
        self.max_file_size_mb = settings.max_file_size_mb
        self.argv = argv or [sys.executable, "-m", "groundly.ingestion.extract_worker", "--serve"]
        self._proc: subprocess.Popen | None = None
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._replies: queue.Queue = queue.Queue()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _start(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        # image-pixel cap via env, read once at worker start
        env = {**os.environ, "GROUNDLY_MAX_IMAGE_PIXELS": str(int(self.max_image_pixels))}
        # Never let the worker turn on logging: this parent reads only the *last line* of
        # its stderr to name the failure cause, so a log line written after the cause
        # would silently replace it.
        env.pop("GROUNDLY_LOG_LEVEL", None)
        with open(tmp / "stderr.log", "wb") as stderr_file:
            self._proc = subprocess.Popen(
                self.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                cwd=tmp,
                env=env,
                text=True,
                bufsize=1,
            )
        self._replies = queue.Queue()
        threading.Thread(target=_pump, args=(self._proc.stdout, self._replies), daemon=True).start()

    def close(self) -> None:
        """End the worker (EOF, then a kill if it lingers) and remove its temp dir."""
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:  # a dead worker's pipe
                pass
            _reap(proc)
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def _exchange(self, in_path: str, ocr_lang: str | None) -> tuple[str, Path, int]:
        """Send one request; return the reply line, the worker's temp dir and the stderr
        offset the request started at. A dead or silent worker is closed before raising,
        so the next call starts a fresh one."""
        for attempt in range(2):
            if self._proc is not None and self._proc.poll() is not None:
                self.close()  # died while idle: it never saw this file
            if self._proc is None:
                self._start()
            proc, tmpdir = self._proc, self._tmp
            assert proc is not None and proc.stdin is not None and tmpdir is not None
            tmp = Path(tmpdir.name)
            out_json = tmp / "extraction.json"
            out_json.unlink(missing_ok=True)
            start = (tmp / "stderr.log").stat().st_size
            request = {"in": in_path, "out": str(out_json), "ocr_lang": ocr_lang}
            paged = False  # a page line proves the worker read this request
            try:
                proc.stdin.write(json.dumps(request) + "\n")
                proc.stdin.flush()
                # the wait restarts on every finished page: the limit is per page, not per file
                while (line := self._replies.get(timeout=self.timeout)) is not None:
                    if "page" not in json.loads(line):
                        break
                    paged = True
            except OSError:  # broken pipe: the worker is gone
                line = None
            except queue.Empty:
                proc.kill()
                self.close()
                raise ExtractionFailure(
                    f"extraction timed out — no page finished within {int(self.timeout)}s"
                ) from None
            if line is not None:
                return line, tmp, start
            rc = _reap(proc)
            tail = _stderr_tail(tmp / "stderr.log", start)
            self.close()
            # A death with nothing said since this request may have happened before the
            # worker read it: resend once to a fresh worker. A file that kills workers
            # silently still fails on the second try.
            if tail or attempt or paged:
                raise ExtractionFailure(f"parser failed: {tail or f'exit code {rc}'}")
        raise AssertionError("unreachable: the second attempt returns or raises")

    def extract(self, path: Path, ocr_lang: str | None = None) -> Extraction:
        if self.max_file_size_mb:  # None/0 = no limit
            size_mb = path.stat().st_size / (1024 * 1024)
            if size_mb > self.max_file_size_mb:
                raise ExtractionFailure(
                    f"file too large — {size_mb:.0f} MB exceeds the {self.max_file_size_mb:.0f} MB "
                    "limit (raise it with: groundly config set ingestion.max_file_size_mb <MB>)"
                )
        # resolved: the worker's cwd is its temp dir
        line, tmp, start = self._exchange(str(path.resolve()), ocr_lang)
        status = json.loads(line)["status"]
        tail = _stderr_tail(tmp / "stderr.log", start)
        if status == "model_unavailable":
            raise ModelUnavailable(tail or "extractor model unavailable")
        if status == "input_too_large":
            raise ExtractionFailure(tail or "image too large to process")
        if status == "no_text":
            if path.suffix.lower() == ".pdf" or path.suffix.lower() in IMAGE_SUFFIXES:
                raise ExtractionFailure("no readable text — OCR found nothing to extract")
            raise ExtractionFailure("no extractable text")
        if status != "ok":
            raise ExtractionFailure(f"parser failed: {tail or 'the worker reported no cause'}")

        out_json = tmp / "extraction.json"
        if out_json.stat().st_size > MAX_EXTRACTION_JSON_BYTES:
            raise ExtractionFailure("extraction output too large")
        data = json.loads(out_json.read_text())
        return Extraction(pages=data["pages"], chunks=[ChunkData(**c) for c in data["chunks"]])
