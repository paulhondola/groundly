"""In-memory generation job registry (P6 slice 1 design doc): `generate_*` MCP tools
return a job id immediately and a daemon thread runs the loop — never block a
request handler on an agent loop (.claude/rules/architecture.md). Jobs are
session-scoped: verified items commit to store.db per item, so a killed host
session loses at most this registry, never a verified card.

The downstream stack (chat, retrieval, store) is synchronous — a thread is the
plain bounded loop the docs ask for, identical on stdio and HTTP transports.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass

# ponytail: global lock serializes ALL thick jobs, not just local-provider ones —
# one line instead of a base_url heuristic; per-provider check if parallel cloud
# jobs ever matter.
_GEN_LOCK = threading.Lock()


@dataclass
class Job:
    id: str
    subject: str
    status: str  # 'queued' | 'running' | 'done' | 'failed'
    report: dict | None = None
    error: str | None = None


# ponytail: in-memory registry, lost on restart; move to progress.db if resumable
# jobs are ever needed.
_JOBS: dict[str, Job] = {}


def start_job(subject: str, fn: Callable[[], dict]) -> Job:
    """Register a job and run `fn` on a daemon thread behind the generation lock."""
    job = Job(id=uuid.uuid4().hex[:12], subject=subject, status="queued")
    _JOBS[job.id] = job

    def _run() -> None:
        with _GEN_LOCK:
            job.status = "running"
            try:
                job.report = fn()
                job.status = "done"
            except Exception as exc:  # the job surface: status + named error, no traceback
                job.error = str(exc)
                job.status = "failed"

    threading.Thread(target=_run, daemon=True, name=f"groundly-job-{job.id}").start()
    return job


def get_job(job_id: str) -> Job | None:
    return _JOBS.get(job_id)
