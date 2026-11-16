"""tq archive: export old finished tasks to JSONL, move their YAML aside, drop the DB rows."""

from __future__ import annotations

import json
from pathlib import Path

from tqdaemon.archive import archive_tasks
from tqdaemon.config import Project
from tqdaemon.db import open_db


def _setup(tmp_path: Path):
    root = tmp_path / "proj"
    qdir = root / "experiments" / "operator_queue"
    qdir.mkdir(parents=True)
    conn = open_db(tmp_path / "home" / "queue.db")
    (tmp_path / "home").mkdir(exist_ok=True)
    rows = [
        ("old_done", "completed", "2026-01-01T00:00:00"),
        ("old_fail", "failed", "2026-01-01T00:00:00"),
        ("new_done", "completed", "2026-10-20T00:00:00"),
        ("old_run", "running", "2026-01-01T00:00:00"),
    ]
    for tid, status, upd in rows:
        f = qdir / f"{tid}.yaml"
        f.write_text(f"task_id: {tid}\ncommand: x\n", encoding="utf-8")
        conn.execute(
            "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at, yaml_source) "
            "VALUES ('p', ?, 'x', ?, ?, ?, ?)",
            (tid, status, upd, upd, str(f)),
        )
        conn.execute(
            "INSERT INTO runs (project, task_id, started_at, status) VALUES ('p', ?, ?, ?)", (tid, upd, status)
        )
    conn.commit()
    return conn, Project(name="p", root=root, queue_dir=qdir), qdir


def test_archives_only_old_completed_by_default(tmp_path: Path) -> None:
    conn, proj, qdir = _setup(tmp_path)
    home = tmp_path / "home"
    r = archive_tasks(conn, {"p": proj}, home=home, older_than_days=30, now="2026-10-24T00:00:00")
    assert r["archived"] == 1 and r["tasks"] == [{"project": "p", "task_id": "old_done"}]
    left = {x[0] for x in conn.execute("SELECT task_id FROM tasks")}
    assert left == {"old_fail", "new_done", "old_run"}
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE task_id='old_done'").fetchone()[0] == 0
    assert not (qdir / "old_done.yaml").exists() and (qdir / "archive" / "old_done.yaml").exists()
    lines = (home / "archive" / "p.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    assert rec["task"]["task_id"] == "old_done" and len(rec["runs"]) == 1


def test_dry_run_and_status_override(tmp_path: Path) -> None:
    conn, proj, qdir = _setup(tmp_path)
    home = tmp_path / "home"
    r = archive_tasks(
        conn,
        {"p": proj},
        home=home,
        older_than_days=30,
        statuses=("completed", "failed"),
        dry_run=True,
        now="2026-10-24T00:00:00",
    )
    assert r["archived"] == 2 and r["dry_run"] is True
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 4
    assert (qdir / "old_done.yaml").exists() and not (home / "archive").exists()


def test_sync_does_not_resurrect_archived(tmp_path: Path) -> None:
    from tqdaemon.yaml_sync import _sync_project, clear_yaml_mtime_cache

    clear_yaml_mtime_cache()
    conn, proj, qdir = _setup(tmp_path)
    archive_tasks(conn, {"p": proj}, home=tmp_path / "home", older_than_days=30, now="2026-10-24T00:00:00")
    _sync_project(conn, proj, force=True)
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE task_id='old_done'").fetchone()[0] == 0


def test_api_archive_route_and_validation(tmp_path: Path) -> None:
    from tests.helpers.master_worker import http_request, make_handler_env

    conn, proj, qdir = _setup(tmp_path)
    conn.close()
    H, db, _ = make_handler_env(tmp_path / "h", projects={"p": proj})
    for tid, st in (("a", "completed"), ("b", "failed")):
        db.execute(
            "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at) "
            "VALUES ('p', ?, 'x', ?, '2020-01-01T00:00:00', '2020-01-01T00:00:00')",
            (tid, st),
        )
    db.commit()
    code, data = http_request(H, "POST", "/api/cli/archive", {"dry_run": True})
    assert code == 200 and data["archived"] == 1 and data["dry_run"] is True
    code, data = http_request(H, "POST", "/api/cli/archive", {"statuses": ["completed", "failed"]})
    assert code == 200 and data["archived"] == 2
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE project='p'").fetchone()[0] == 0
    assert http_request(H, "POST", "/api/cli/archive", {"projects": ["nope"]})[0] == 404
    assert http_request(H, "POST", "/api/cli/archive", {"older_than_days": "x"})[0] == 400
    db.close()


def test_api_archive_async_job_is_polled(tmp_path: Path) -> None:
    """C7: ``async: true`` answers 202 {job} at once; GET /api/jobs/<id> reports the result."""
    import time

    from tests.helpers.master_worker import http_request, make_handler_env

    conn, proj, _ = _setup(tmp_path)
    conn.close()
    H, db, _ = make_handler_env(tmp_path / "h", projects={"p": proj})
    db.execute(
        "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at) "
        "VALUES ('p', 'a', 'x', 'completed', '2020-01-01T00:00:00', '2020-01-01T00:00:00')"
    )
    db.commit()
    code, data = http_request(H, "POST", "/api/cli/archive", {"async": True})
    assert code == 202 and data["job"]
    job = {}
    for _ in range(100):
        code, job = http_request(H, "GET", f"/api/jobs/{data['job']}")
        assert code == 200
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert job["state"] == "done" and job["result"]["archived"] == 1
    assert http_request(H, "GET", "/api/jobs/nope")[0] == 404
    db.close()


def test_age_uses_finished_at_not_sync_touched_updated_at(tmp_path: Path) -> None:
    """Sync rewrites updated_at every pass; an old finished task must still be archivable."""
    conn, proj, _ = _setup(tmp_path)
    conn.execute("UPDATE tasks SET updated_at='2026-10-23T23:00:00'")  # as if synced just now
    conn.execute("UPDATE tasks SET finished_at='2026-01-01T00:00:00' WHERE task_id='old_done'")
    conn.execute("UPDATE tasks SET finished_at='2026-10-20T00:00:00' WHERE task_id='new_done'")
    conn.commit()
    r = archive_tasks(
        conn, {"p": proj}, home=tmp_path / "home", older_than_days=30, now="2026-10-24T00:00:00", dry_run=True
    )
    assert r["tasks"] == [{"project": "p", "task_id": "old_done"}]


def test_archive_task_with_last_run_id_fk(tmp_path: Path) -> None:
    """tasks.last_run_id -> runs(run_id): deleting runs before the task row violated the FK."""
    conn, proj, qdir = _setup(tmp_path)
    rid = conn.execute("SELECT run_id FROM runs WHERE task_id='old_done'").fetchone()[0]
    conn.execute("UPDATE tasks SET last_run_id=? WHERE task_id='old_done'", (rid,))
    conn.commit()
    r = archive_tasks(conn, {"p": proj}, home=tmp_path / "home", older_than_days=30, now="2026-10-24T00:00:00")
    assert r["archived"] == 1
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE task_id='old_done'").fetchone()[0] == 0
    assert (qdir / "archive" / "old_done.yaml").exists()


def test_archive_keeps_dependency_of_unfinished_task(tmp_path: Path) -> None:
    conn, proj, qdir = _setup(tmp_path)
    conn.execute(
        "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at, depends_on) "
        "VALUES ('p', 'next', 'x', 'queued', '2026-10-23T00:00:00', '2026-10-23T00:00:00', '[\"old_done\"]')"
    )
    conn.execute(
        "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at, depends_on) "
        "VALUES ('q', 'other', 'x', 'queued', '2026-10-23T00:00:00', '2026-10-23T00:00:00', '[\"p/old_fail\"]')"
    )
    conn.commit()
    r = archive_tasks(
        conn,
        {"p": proj},
        home=tmp_path / "home",
        older_than_days=30,
        statuses=("completed", "failed"),
        now="2026-10-24T00:00:00",
    )
    assert r["archived"] == 0 and r["kept_as_dependency"] == 2
    assert (qdir / "old_done.yaml").exists()


def test_task_glob_limits_archive_to_matching_task_ids(tmp_path: Path) -> None:
    conn, proj, _ = _setup(tmp_path)
    r = archive_tasks(
        conn,
        {"p": proj},
        home=tmp_path / "home",
        older_than_days=30,
        statuses=("completed", "failed"),
        dry_run=True,
        now="2026-10-24T00:00:00",
        task_glob="old_*",
    )
    assert {t["task_id"] for t in r["tasks"]} <= {"old_done", "old_fail"}
    assert r["archived"] >= 1
    none = archive_tasks(
        conn,
        {"p": proj},
        home=tmp_path / "home",
        older_than_days=30,
        dry_run=True,
        now="2026-10-24T00:00:00",
        task_glob="zzz*,qqq*",
    )
    assert none["archived"] == 0
