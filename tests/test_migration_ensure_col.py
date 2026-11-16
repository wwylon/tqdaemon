"""D3: table-driven ALTER ... ADD COLUMN migrations restore columns missing from an older DB."""

import sqlite3
from pathlib import Path

from tqdaemon.migrations import _ensure_col, _open_db_impl


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_ensure_col_adds_once_and_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (a INTEGER)")
    cols = _cols(conn, "t")
    _ensure_col(conn, cols, "t", "b", "TEXT NOT NULL DEFAULT 'x'")
    _ensure_col(conn, _cols(conn, "t"), "t", "b", "TEXT NOT NULL DEFAULT 'x'")  # no second ALTER
    assert _cols(conn, "t") == {"a", "b"}


def test_open_db_restores_dropped_columns(tmp_path: Path) -> None:
    db = tmp_path / "q.db"
    conn = _open_db_impl(db)
    wanted_runs = {"peak_ram_gib", "peak_cpu_pct", "stop_reason", "estimated_ram_gib"}
    wanted_tasks = {"force_run", "bundle_hash", "block_reason"}
    for c in wanted_runs:
        conn.execute(f"ALTER TABLE runs DROP COLUMN {c}")
    for c in wanted_tasks:
        conn.execute(f"ALTER TABLE tasks DROP COLUMN {c}")
    conn.commit()
    assert not (wanted_runs & _cols(conn, "runs"))
    conn.close()
    conn = _open_db_impl(db)  # schema_version is current: force the full path
    conn.close()
    from tqdaemon import migrations

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("DELETE FROM config WHERE key='schema_version'")
    conn.commit()
    conn.close()
    conn = migrations._open_db_impl(db)
    assert wanted_runs <= _cols(conn, "runs")
    assert wanted_tasks <= _cols(conn, "tasks")
    conn.close()
