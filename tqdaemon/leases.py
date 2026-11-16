"""Ad-hoc GPU leases (T-208 / med R2+R4): interactive GPU work admitted by the scheduler, not racing it.

A lease is a VRAM reservation without a task: ``waiting`` until the budget has room, ``active`` while its
holder runs (released explicitly, on TTL expiry, or when the holder pid dies), then ``released`` / ``expired``.
Active leases count against the same VRAM budget as running tasks' declared ``gpu_vram_gib``.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from tqdaemon._time import _now

DEFAULT_TTL_SEC = 900
MAX_TTL_SEC = 24 * 3600


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return True  # no holder pid recorded: only the TTL ends the lease
    try:
        import psutil

        return psutil.pid_exists(int(pid))
    except Exception:  # noqa: BLE001 - liveness is best-effort; the TTL still bounds the lease
        return True


def create_lease(
    conn: sqlite3.Connection,
    *,
    vram_gib: float,
    ttl_sec: int = DEFAULT_TTL_SEC,
    priority: int = 0,
    holder: str = "",
    pid: int | None = None,
) -> int:
    if not (vram_gib > 0):
        raise ValueError("vram_gib must be > 0")
    ttl = max(1, min(int(ttl_sec), MAX_TTL_SEC))
    cur = conn.execute(
        "INSERT INTO gpu_leases (status, vram_gib, ttl_sec, priority, holder, pid, created_at) "
        "VALUES ('waiting', ?, ?, ?, ?, ?, ?)",
        (float(vram_gib), ttl, int(priority), holder[:200], pid, _now()),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def get_lease(conn: sqlite3.Connection, lease_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gpu_leases WHERE id = ?", (lease_id,)).fetchone()
    return dict(row) if row is not None else None


def list_leases(conn: sqlite3.Connection, *, active_only: bool = True) -> list[dict[str, Any]]:
    where = "WHERE status IN ('waiting','active')" if active_only else ""
    rows = conn.execute(f"SELECT * FROM gpu_leases {where} ORDER BY id DESC LIMIT 200").fetchall()  # noqa: S608
    return [dict(r) for r in rows]


def release_lease(conn: sqlite3.Connection, lease_id: int) -> bool:
    cur = conn.execute(
        "UPDATE gpu_leases SET status = 'released', released_at = ? WHERE id = ? AND status IN ('waiting','active')",
        (_now(), lease_id),
    )
    conn.commit()
    return cur.rowcount > 0


def active_vram(conn: sqlite3.Connection) -> float:
    row = conn.execute("SELECT COALESCE(SUM(vram_gib), 0) FROM gpu_leases WHERE status = 'active'").fetchone()
    return float(row[0] or 0.0)


def expire_stale(conn: sqlite3.Connection, *, now: datetime | None = None) -> int:
    """Expire active leases past their TTL or whose holder pid is gone; drop abandoned waiting ones."""
    now = now or datetime.now(UTC)
    n = 0
    rows = conn.execute(
        "SELECT id, status, expires_at, created_at, pid FROM gpu_leases WHERE status IN ('waiting','active')"
    ).fetchall()
    for row in rows:
        stale = False
        if (
            row["status"] == "active"
            and row["expires_at"]
            and datetime.fromisoformat(row["expires_at"]) <= now
            or not _pid_alive(row["pid"])
        ):
            stale = True
        elif row["status"] == "waiting":
            # A waiter nobody has polled for a day is abandoned.
            stale = datetime.fromisoformat(row["created_at"]) + timedelta(seconds=MAX_TTL_SEC) <= now
        if stale:
            conn.execute(
                "UPDATE gpu_leases SET status = 'expired', released_at = ? WHERE id = ?",
                (now.isoformat(), row["id"]),
            )
            n += 1
    if n:
        conn.commit()
    return n


def grant_waiting(conn: sqlite3.Connection, free_vram_gib: float, *, now: datetime | None = None) -> list[int]:
    """Activate waiting leases (priority desc, then FIFO) while they fit in ``free_vram_gib``."""
    now = now or datetime.now(UTC)
    granted: list[int] = []
    free = float(free_vram_gib)
    rows = conn.execute(
        "SELECT id, vram_gib, ttl_sec FROM gpu_leases WHERE status = 'waiting' ORDER BY priority DESC, id ASC"
    ).fetchall()
    for row in rows:
        need = float(row["vram_gib"])
        if need > free:
            break  # strict order: a small lease must not overtake the head of the line
        expires = (now + timedelta(seconds=int(row["ttl_sec"]))).isoformat()
        conn.execute(
            "UPDATE gpu_leases SET status = 'active', acquired_at = ?, expires_at = ? WHERE id = ?",
            (now.isoformat(), expires, row["id"]),
        )
        free -= need
        granted.append(int(row["id"]))
    if granted:
        conn.commit()
    return granted


def process_leases(
    conn: sqlite3.Connection, system_info: dict[str, Any], cfg: dict[str, Any], active_runs: dict
) -> None:
    """Scheduler step: expire stale leases, then grant waiting ones that fit next to running work."""
    from tqdaemon.resources import ResourceBudget
    from tqdaemon.rows import row_resources

    try:
        expire_stale(conn)
        if not conn.execute("SELECT 1 FROM gpu_leases WHERE status = 'waiting' LIMIT 1").fetchone():
            return
    except sqlite3.OperationalError:  # locked DB or table missing: lease processing must never stall admission
        return
    budget = ResourceBudget(system_info, cfg)
    declared = 0.0
    for run in active_runs.values():
        row = getattr(run, "task_row", None)
        if row is not None:
            declared += float(row_resources(row).get("gpu_vram_gib", 0.0) or 0.0)
    held = max(float(budget.gpu_live), declared + active_vram(conn))
    free = max(0.0, float(budget.gpu_limit) - held)
    grant_waiting(conn, free)


def wait_until_active(
    fetch: Callable[[int], dict[str, Any] | None], lease_id: int, *, timeout: float, interval: float = 1.0
) -> dict[str, Any] | None:
    """Poll ``fetch(lease_id)`` until the lease is active; None on timeout or a terminal state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lease = fetch(lease_id)
        if lease is None or lease.get("status") in ("released", "expired"):
            return None
        if lease.get("status") == "active":
            return lease
        time.sleep(interval)
    return None
