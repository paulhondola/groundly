"""The in-memory job registry (P6 slice 1): session-scoped by design — durability
lives in per-card commits, not here. One global lock serializes thick generation
jobs (stricter than the local-provider-only requirement; free for one student)."""

import threading
import time

from groundly.agents import jobs


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_job_runs_to_done_with_report():
    job = jobs.start_job("TEST", lambda: {"accepted": 3})
    assert _wait_for(lambda: job.status == "done")
    assert job.report == {"accepted": 3}
    assert job.error is None
    assert jobs.get_job(job.id) is job


def test_failing_job_reports_error():
    def boom():
        raise RuntimeError("no course material found for topic 'x'")

    job = jobs.start_job("TEST", boom)
    assert _wait_for(lambda: job.status == "failed")
    assert "no course material" in job.error
    assert job.report is None


def test_unknown_job_id_returns_none():
    assert jobs.get_job("nope") is None


def test_jobs_serialize_behind_the_global_lock():
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(timeout=5)
        return {"first": True}

    first = jobs.start_job("TEST", slow)
    assert started.wait(timeout=5)
    second = jobs.start_job("TEST", lambda: {"second": True})

    # while the first job holds the lock, the second must not run
    time.sleep(0.05)
    assert first.status == "running"
    assert second.status == "queued"

    release.set()
    assert _wait_for(lambda: first.status == "done" and second.status == "done")
