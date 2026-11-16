"""`tq drain` (no new starts) and the shutdown stage the web UI shows."""

from __future__ import annotations

from pathlib import Path

from tqdaemon import adopt
from tqdaemon import daemon_state as lifecycle


def test_lifecycle_reports_shutdown_and_drain() -> None:
    lifecycle.reset()
    assert lifecycle.lifecycle_info({}) == {
        "shutting_down": False,
        "shutdown_since": None,
        "keep_runs": False,
        "draining": False,
    }
    assert lifecycle.lifecycle_info({"admission_drain": 1})["draining"] is True
    lifecycle.begin_shutdown(keep_runs=True)
    info = lifecycle.lifecycle_info({})
    assert info["shutting_down"] and info["keep_runs"] and info["shutdown_since"]
    lifecycle.reset()


def test_peek_keep_runs_does_not_consume_the_marker(tmp_path: Path) -> None:
    assert adopt.peek_keep_runs(tmp_path) is False
    adopt.request_keep_runs(tmp_path)
    assert adopt.peek_keep_runs(tmp_path) is True and adopt.peek_keep_runs(tmp_path) is True
    assert adopt.consume_keep_runs(tmp_path) is True and adopt.peek_keep_runs(tmp_path) is False


def test_status_payload_carries_lifecycle(tmp_path: Path) -> None:
    from tests.helpers.master_worker import http_request, make_handler_env

    lifecycle.reset()
    H, db, _ = make_handler_env(tmp_path, projects={})
    code, st = http_request(H, "GET", "/api/status")
    assert code == 200 and st["lifecycle"]["shutting_down"] is False and st["lifecycle"]["draining"] is False
    lifecycle.begin_shutdown(keep_runs=False)
    code, st = http_request(H, "GET", "/api/status")
    assert st["lifecycle"]["shutting_down"] is True and st["lifecycle"]["keep_runs"] is False
    lifecycle.reset()
    db.close()


def test_drain_blocks_local_admission_with_a_reason(tmp_path: Path, monkeypatch) -> None:
    import tqdaemon.scheduler as scheduler
    from tqdaemon import daemon

    seen: list[dict] = []
    monkeypatch.setattr(scheduler, "_local_admit_queued", lambda *a, **k: seen.append(k))
    monkeypatch.setattr("tqdaemon.leases.process_leases", lambda *a, **k: None)

    def run(cfg):
        return daemon._run_local_admission(
            object(), None, {}, {}, {"cpu_total_cores": 8}, cfg, 3.0, False,
            now=100.0, admit_frozen_until=0.0, cooldown_logged=False, reservation_state={}, cpu_ema=0.0,
        )  # fmt: skip

    run({"cpu_limit_pct": 50, "admission_drain": 1})
    assert seen[-1]["admit_frozen"] is True and "drained" in seen[-1]["admit_frozen_reason"]
    run({"cpu_limit_pct": 50, "admission_drain": 0})
    assert seen[-1]["admit_frozen"] is False


def test_worker_claims_get_no_task_while_drained(tmp_path: Path) -> None:
    from tests.helpers.master_worker import http_request, make_handler_env, seed_worker
    from tqdaemon.db import set_config

    H, db, _ = make_handler_env(tmp_path, projects={})
    seed_worker(db, "w1")
    set_config(db, "admission_drain", 1)
    code, out = http_request(H, "GET", "/api/worker/next-task?worker_id=w1")
    assert code == 200 and out["task"] is None and out.get("drain") is True
    db.close()


def test_cli_drain_toggles_config_and_stop_daemon_keep_runs(tmp_path: Path, monkeypatch, capsys) -> None:
    from tqdaemon import _cli_admin
    from tqdaemon.db import get_config, open_db

    home = tmp_path / "h"
    home.mkdir()
    open_db(home / "queue.db").close()
    monkeypatch.setattr(_cli_admin, "_running_runs", lambda *a, **k: ["p/a", "p/b"])
    assert _cli_admin.cmd_drain(home, "on") == 0
    assert "drain ON" in capsys.readouterr().out
    conn = open_db(home / "queue.db")
    assert get_config(conn)["admission_drain"] == 1
    conn.close()
    assert _cli_admin.cmd_drain(home, "off") == 0 and "drain off" in capsys.readouterr().out

    sent: list = []
    monkeypatch.setattr(_cli_admin, "_daemon_running", lambda h: 4242)
    monkeypatch.setattr("os.kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr("sys.platform", "linux")
    seen_skip: list = []
    monkeypatch.setattr(
        _cli_admin, "_refuse_if_running", lambda h, v, skip_adoptable=False: seen_skip.append(skip_adoptable) or False
    )
    assert _cli_admin.cmd_stop_daemon(home, keep_runs=True) == 0
    assert seen_skip == [True] and adopt.peek_keep_runs(home) is True and sent
