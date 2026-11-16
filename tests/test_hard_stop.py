"""Operator hard stop (stop_requested=2): kill the process tree now, no cooperative grace."""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from tqdaemon import errors as tqerr
from tqdaemon.daemon import STATUS_INTERRUPTED, decide_terminal_status
from tqdaemon.db import get_task, open_db, request_stop
from tqdaemon.runner import ActiveRun


def _task(conn, status: str = "running") -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at) VALUES ('p','t',?,?,?,?)",
        (sys.executable, status, now, now),
    )
    conn.commit()


def test_hard_request_is_never_downgraded(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "q.db")
    _task(conn)
    request_stop(conn, "p", "t", hard=True)
    assert get_task(conn, "p", "t")["stop_requested"] == 2
    request_stop(conn, "p", "t")
    assert get_task(conn, "p", "t")["stop_requested"] == 2


def test_soft_request_is_one(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "q.db")
    _task(conn)
    request_stop(conn, "p", "t")
    assert get_task(conn, "p", "t")["stop_requested"] == 1


def test_killed_run_is_interrupted_with_code() -> None:
    status, err, by = decide_terminal_status(
        1, "killed", reader_crashed=False, stop_requested=True, negative_exit=False
    )
    assert status == STATUS_INTERRUPTED
    assert err == tqerr.STOP_KILLED
    assert by


def test_kill_now_skips_graceful_wait(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "q.db")
    _task(conn)
    run = ActiveRun(conn=conn, task_row=get_task(conn, "p", "t"), log_dir=tmp_path / "logs", cwd=tmp_path)
    # A child that ignores SIGTERM; a cooperative stop would wait out the 60 s grace.
    code = "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nwhile True: time.sleep(0.1)"
    run.proc = subprocess.Popen([sys.executable, "-c", code])
    run.graceful_stop_sec = 60
    t0 = time.monotonic()
    run.kill_now()
    run.proc.wait(timeout=20)
    assert time.monotonic() - t0 < 15
    assert run.stop_reason == "killed"
