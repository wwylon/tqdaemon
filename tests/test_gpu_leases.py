"""T-208: ad-hoc GPU leases — queueing order, TTL/holder expiry, budget accounting, HTTP + CLI."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tqdaemon import leases
from tqdaemon.db import open_db


def _conn(tmp_path: Path):
    return open_db(tmp_path / "q.db")


def test_grant_in_priority_then_fifo_order_and_no_overtaking(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    a = leases.create_lease(conn, vram_gib=3.0)
    b = leases.create_lease(conn, vram_gib=4.0)  # does not fit after a
    c = leases.create_lease(conn, vram_gib=1.0)  # would fit, but must not overtake b
    assert leases.grant_waiting(conn, 5.0) == [a]
    assert leases.get_lease(conn, b)["status"] == "waiting"
    assert leases.get_lease(conn, c)["status"] == "waiting"
    assert leases.active_vram(conn) == 3.0
    hi = leases.create_lease(conn, vram_gib=2.0, priority=5)
    assert leases.grant_waiting(conn, 2.0) == [hi]  # priority beats the older waiter


def test_two_leases_that_fit_run_together_and_release_frees_room(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    a = leases.create_lease(conn, vram_gib=2.0)
    b = leases.create_lease(conn, vram_gib=3.0)
    assert leases.grant_waiting(conn, 6.0) == [a, b]
    assert leases.release_lease(conn, a) is True
    assert leases.release_lease(conn, a) is False  # already ended
    assert leases.active_vram(conn) == 3.0


def test_ttl_expiry_and_dead_holder_expire_active_leases(tmp_path: Path, monkeypatch) -> None:
    conn = _conn(tmp_path)
    ttl = leases.create_lease(conn, vram_gib=1.0, ttl_sec=60)
    dead = leases.create_lease(conn, vram_gib=1.0, pid=999999)
    live = leases.create_lease(conn, vram_gib=1.0, pid=os.getpid())
    leases.grant_waiting(conn, 10.0)
    monkeypatch.setattr(leases, "_pid_alive", lambda pid: pid != 999999)
    later = datetime.now(UTC) + timedelta(seconds=120)
    leases.expire_stale(conn, now=later)
    assert leases.get_lease(conn, ttl)["status"] == "expired"
    assert leases.get_lease(conn, dead)["status"] == "expired"
    assert leases.get_lease(conn, live)["status"] == "active"  # alive holder, TTL (900s) not reached


def test_live_holder_keeps_lease_until_ttl(tmp_path: Path, monkeypatch) -> None:
    conn = _conn(tmp_path)
    live = leases.create_lease(conn, vram_gib=1.0, ttl_sec=600, pid=os.getpid())
    leases.grant_waiting(conn, 10.0)
    monkeypatch.setattr(leases, "_pid_alive", lambda pid: True)
    assert leases.expire_stale(conn, now=datetime.now(UTC) + timedelta(seconds=30)) == 0
    assert leases.get_lease(conn, live)["status"] == "active"


def test_invalid_lease_is_rejected(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        leases.create_lease(_conn(tmp_path), vram_gib=0)


def test_active_leases_count_against_running_task_budget() -> None:
    """A lease holds VRAM like a declared task: only the shortfall over the live reading is reserved."""
    from tests.test_seed_declared_vram_hold import _seed

    budget = _seed([], gpu_live=1.0, leased=4.0)
    assert budget.reserved_total == 4.0


def test_http_roundtrip_and_scope(tmp_path: Path) -> None:
    from tests.helpers.master_worker import http_request, make_handler_env

    H, db, _ = make_handler_env(tmp_path / "h", projects={})
    code, data = http_request(H, "POST", "/api/leases", {"vram_gib": 2.5, "ttl_sec": 120, "holder": "pytest"})
    assert code == 202 and data["status"] == "waiting"
    lease_id = data["id"]
    code, one = http_request(H, "GET", f"/api/leases/{lease_id}")
    assert code == 200 and one["vram_gib"] == 2.5 and one["holder"] == "pytest"
    code, many = http_request(H, "GET", "/api/leases")
    assert code == 200 and [x["id"] for x in many["leases"]] == [lease_id]
    assert http_request(H, "POST", "/api/leases", {"vram_gib": "x"})[0] == 400
    assert http_request(H, "POST", "/api/leases", {"vram_gib": 0})[0] == 400
    assert http_request(H, "POST", f"/api/leases/{lease_id}/release")[0] == 200
    assert http_request(H, "POST", f"/api/leases/{lease_id}/release")[0] == 404
    assert http_request(H, "GET", "/api/leases/9999")[0] == 404
    db.close()


def test_write_tokens_may_use_leases_read_tokens_may_not() -> None:
    from tqdaemon.operator_features import scope_allows

    assert scope_allows("write", "POST", "/api/leases")
    assert scope_allows("write", "POST", "/api/leases/3/release")
    assert not scope_allows("read", "POST", "/api/leases")
    assert scope_allows("read", "GET", "/api/leases")


def test_cli_gpu_run_acquires_runs_and_releases(monkeypatch, tmp_path: Path) -> None:
    from tqdaemon import _cli_lease

    calls: list[tuple[str, str]] = []
    lease = {"status": "waiting"}

    def fake_master(home, method, path, body=None):
        calls.append((method, path))
        if (method, path) == ("POST", "/api/leases"):
            return {"ok": True, "id": 7}
        if method == "GET":
            lease["status"] = "active"
            return {"ok": True, "status": "active", "vram_gib": 2.0}
        return {"ok": True}

    monkeypatch.setattr(_cli_lease, "_master", fake_master)
    rc = _cli_lease.cmd_gpu(tmp_path, ["run", "--vram", "2", "--", "python", "-c", "import sys; sys.exit(3)"])
    assert rc == 3  # the command's exit code is passed through
    assert calls[0] == ("POST", "/api/leases")
    assert calls[-1] == ("POST", "/api/leases/7/release")  # released even though the command failed


def test_cli_usage_errors(tmp_path: Path) -> None:
    from tqdaemon import _cli_lease

    assert _cli_lease.cmd_lease(tmp_path, []) == 1
    assert _cli_lease.cmd_lease(tmp_path, ["acquire"]) == 1  # --vram missing
    assert _cli_lease.cmd_gpu(tmp_path, ["run", "--vram", "2"]) == 1  # no command
