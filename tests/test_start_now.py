"""Operator "Start now" (force_run=2): first in line, ignores RAM held for other tasks, keeps real limits."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tqdaemon import scheduler
from tqdaemon.config import Project
from tqdaemon.db import get_task, open_db, start_now_task

SYSTEM = {"cpu_total_cores": 16, "ram_total_gib": 64.0, "gpu_vram_total_gib": 0.0, "gpus": []}
CFG = {
    "cpu_limit_pct": 100,
    "gpu_limit_pct": 100,
    "ram_limit_pct": 80,
    "ram_hard_limit_pct": 100,
    "reservation_max_ticks": 60,
    "admit_max_per_tick": 4,
}


def _setup(tmp_path, monkeypatch, *, live: float, small_ram: float = 4.0):
    conn = open_db(tmp_path / "q.db")
    now = datetime.now(UTC).isoformat()
    for tid, prio, ram in (("big", 90, 20.0), ("urgent", 10, small_ram)):
        conn.execute(
            "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at, enabled, priority, "
            "resources) VALUES ('p',?,'true','queued',?,?,1,?,?)",
            (tid, now, now, prio, json.dumps({"cpu_cores": 1, "ram_gib": ram})),
        )
    conn.commit()
    started: list = []
    monkeypatch.setattr(
        scheduler,
        "_start_local_run",
        lambda c, h, p, row, key, r, g: started.append(key) or SimpleNamespace(run_id=None),
    )
    monkeypatch.setattr("tqdaemon.resources.ram_used_gib", lambda: live)
    monkeypatch.setattr(scheduler, "ram_used_gib", lambda: live)
    projects = {"p": Project(name="p", root=tmp_path, queue_dir=tmp_path / "q")}
    return conn, projects, started


def _admit(conn, tmp_path, projects) -> None:
    scheduler._local_admit_queued(conn, tmp_path, projects, {}, SYSTEM, CFG, 30, False)


def test_hold_for_top_task_blocks_ordinary_start(tmp_path, monkeypatch) -> None:
    conn, projects, started = _setup(tmp_path, monkeypatch, live=40.0)
    _admit(conn, tmp_path, projects)
    assert started == []
    assert "hold" in (get_task(conn, "p", "urgent")["block_reason"] or "")


def test_start_now_ignores_hold_for_other_tasks(tmp_path, monkeypatch) -> None:
    conn, projects, started = _setup(tmp_path, monkeypatch, live=40.0)
    assert start_now_task(conn, "p", "urgent") is None
    assert get_task(conn, "p", "urgent")["force_run"] == 2
    _admit(conn, tmp_path, projects)
    assert started == [("p", "urgent")]


def test_start_now_still_needs_real_free_ram(tmp_path, monkeypatch) -> None:
    conn, projects, started = _setup(tmp_path, monkeypatch, live=49.0)
    start_now_task(conn, "p", "urgent")
    _admit(conn, tmp_path, projects)
    assert started == []
    assert "RAM" in (get_task(conn, "p", "urgent")["block_reason"] or "")


@pytest.mark.parametrize("window", [1])
def test_start_now_survives_candidate_window(tmp_path, monkeypatch, window) -> None:
    conn, projects, started = _setup(tmp_path, monkeypatch, live=10.0)
    start_now_task(conn, "p", "urgent")
    cfg = dict(CFG, admit_candidate_window=window)
    scheduler._local_admit_queued(conn, tmp_path, projects, {}, SYSTEM, cfg, 30, False)
    assert started and started[0] == ("p", "urgent")
