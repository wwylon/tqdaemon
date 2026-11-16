"""C6: remove_task must not claim 'running' when the row was already removed concurrently."""

import sqlite3

from tqdaemon import _db_runs


def test_vanished_row_is_success_not_running(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tasks (project TEXT, task_id TEXT, status TEXT)")
    conn.execute("CREATE TABLE runs (project TEXT, task_id TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('p','t','completed')")
    calls = {"n": 0}
    real = _db_runs.get_task_status

    def racing(c, p, t):  # first read sees the row; then another writer deletes it
        calls["n"] += 1
        row = real(c, p, t)
        if calls["n"] == 1:
            c.execute("DELETE FROM tasks")
        return row

    monkeypatch.setattr(_db_runs, "get_task_status", racing)
    assert _db_runs.remove_task(conn, "p", "t") is None


def test_running_task_still_refused() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tasks (project TEXT, task_id TEXT, status TEXT)")
    conn.execute("CREATE TABLE runs (project TEXT, task_id TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('p','t','running')")
    assert "running" in (_db_runs.remove_task(conn, "p", "t") or "")
