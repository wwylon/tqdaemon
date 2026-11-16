"""Tiny in-process background-job registry (audit 2026-11b C7 / feature 4).

Long admin operations (bulk archive) must not hold one HTTP request open past the client timeout.
``submit`` runs ``fn`` on a daemon thread and returns a job id; ``GET /api/jobs/<id>`` polls it.
State is in memory only: a daemon restart forgets jobs (the operation itself is idempotent/batched).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

MAX_JOBS = 50
_lock = threading.Lock()
_jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()


def submit(kind: str, fn: Callable[[], dict[str, Any]]) -> str:
    """Run ``fn`` in the background; its returned dict becomes the job ``result``."""
    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {"id": job_id, "kind": kind, "state": "running", "started": time.time()}
    with _lock:
        _jobs[job_id] = job
        while len(_jobs) > MAX_JOBS:
            _jobs.popitem(last=False)

    def _run() -> None:
        try:
            result = fn()
            update = {"state": "done", "result": result}
        except Exception as exc:  # noqa: BLE001 - a job failure is reported to the poller, never raised
            update = {"state": "error", "error": f"{type(exc).__name__}: {exc}"}
        with _lock:
            job.update(update)
            job["finished"] = time.time()

    threading.Thread(target=_run, name=f"tq-job-{kind}", daemon=True).start()
    return job_id


def get(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None
