"""`tq project rename OLD NEW`: every place a project id lives moves with it; refusals change nothing."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tqdaemon import project_rename
from tqdaemon.db import open_db
from tqdaemon.project_rename import RenameError, plan_rename, rename_project, resolve_project

NOW = "2026-11-16T00:00:00+00:00"


def _task(conn, project: str, task_id: str, *, status: str = "completed", depends=None, spawned_by=None) -> None:
    conn.execute(
        "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at, depends_on, spawned_by) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (project, task_id, "x", status, NOW, NOW, json.dumps(depends or []), spawned_by),
    )


def _home(tmp_path: Path):
    """A state home with projects ``old`` and ``other`` (CRLF registry, as on the real machine)."""
    home = tmp_path / "home"
    home.mkdir()
    for name in ("old", "other"):
        q = tmp_path / name / "experiments" / "operator_queue"
        q.mkdir(parents=True)
    (home / "projects.yaml").write_bytes(
        (
            "projects:\r\n"
            "  old:\r\n"
            f"    root: {tmp_path / 'old'}\r\n"
            "    queue_dir: experiments/operator_queue\r\n"
            "\r\n"
            "  other:  # keep this comment\r\n"
            f"    root: {tmp_path / 'other'}\r\n"
            "    queue_dir: experiments/operator_queue\r\n"
        ).encode()
    )
    conn = open_db(home / "queue.db")
    _task(conn, "old", "a")
    _task(conn, "old", "b", depends=["a"])
    _task(conn, "other", "c", depends=["old/a", "other/zzz"], spawned_by="old/b")
    log_dir = home / "logs" / "old"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "a_1.log"
    log_path.write_text("hello", encoding="utf-8")
    conn.execute(
        "INSERT INTO runs (project, task_id, status, started_at, log_path) VALUES ('old','a','completed',?,?)",
        (NOW, str(log_path)),
    )
    conn.execute("INSERT INTO project_usage (project, gpu_seconds, cpu_seconds, updated_at) VALUES ('old', 1, 2, 3)")
    conn.execute("INSERT INTO alert_rules (name, kind, threshold, project) VALUES ('r','k',1,'old')")
    for key, val in (
        ("project_color_old", '"#112233"'),
        ("project_frozen_old", "1"),
        ("project_limit_old_max_tasks", "3"),
        ("project_color_other", '"#445566"'),
    ):
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, val))
    conn.execute(
        "INSERT INTO api_tokens (name, token_hash, prefix, scope, created_at, projects) VALUES ('t','h','p','read',?,?)",
        (NOW, json.dumps(["old", "other"])),
    )
    (home / "archive").mkdir()
    (home / "archive" / "old.jsonl").write_text("{}\n", encoding="utf-8")
    conn.commit()
    return home, conn, tmp_path


def _snapshot(home: Path, conn: sqlite3.Connection) -> tuple:
    return (
        (home / "projects.yaml").read_bytes(),
        [tuple(r) for r in conn.execute("SELECT project, task_id FROM tasks ORDER BY 1, 2").fetchall()],
        sorted(p.name for p in (home / "logs").iterdir()),
    )


def test_dry_run_reports_and_changes_nothing(tmp_path: Path) -> None:
    home, conn, _ = _home(tmp_path)
    before = _snapshot(home, conn)
    plan = plan_rename(home, conn, "old", "new")
    assert plan["rows"]["tasks.project"] == 2 and plan["rows"]["runs.project"] == 1
    assert plan["reference_rows"] == 1 and "project_color_old" in plan["config_keys"]
    assert plan["log_folder"] and plan["archive_file"]
    assert _snapshot(home, conn) == before


def test_rename_moves_every_reference_and_keeps_history(tmp_path: Path) -> None:
    home, conn, _ = _home(tmp_path)
    out = rename_project(home, conn, "old", "new")
    assert out["applied"]["tasks.project"] == 2
    q = lambda sql: conn.execute(sql).fetchall()  # noqa: E731
    assert [tuple(r) for r in q("SELECT project, task_id FROM tasks ORDER BY 1,2")] == [
        ("new", "a"),
        ("new", "b"),
        ("other", "c"),
    ]
    assert q("SELECT COUNT(*) FROM runs WHERE project='new'")[0][0] == 1
    c = conn.execute("SELECT depends_on, spawned_by FROM tasks WHERE task_id='c'").fetchone()
    assert json.loads(c["depends_on"]) == ["new/a", "other/zzz"] and c["spawned_by"] == "new/b"
    keys = {r["key"] for r in q("SELECT key FROM config WHERE key LIKE 'project_%'")}
    assert {"project_color_new", "project_frozen_new", "project_limit_new_max_tasks", "project_color_other"} <= keys
    assert not any(k.endswith("_old") or "_old_" in k for k in keys)
    assert conn.execute("SELECT value FROM config WHERE key='project_limit_new_max_tasks'").fetchone()[0] == "3"
    assert json.loads(q("SELECT projects FROM api_tokens")[0][0]) == ["new", "other"]
    assert q("SELECT project FROM project_usage")[0][0] == "new"
    assert q("SELECT project FROM alert_rules")[0][0] == "new"
    # files: registry key (CRLF + comment kept), logs folder + run path, archive
    reg = (home / "projects.yaml").read_bytes().decode()
    assert "  new:\r\n" in reg and "  old:" not in reg and "  other:  # keep this comment\r\n" in reg
    assert (home / "logs" / "new" / "a_1.log").is_file() and not (home / "logs" / "old").exists()
    assert str(home / "logs" / "new" / "a_1.log") == conn.execute("SELECT log_path FROM runs").fetchone()[0]
    assert (home / "archive" / "new.jsonl").is_file() and not (home / "archive" / "old.jsonl").exists()
    # alias + audit + backups
    assert resolve_project(conn, "old") == "new" and resolve_project(conn, "other") == "other"
    audit = conn.execute("SELECT project, target FROM operator_audit WHERE action='project_rename'").fetchone()
    assert tuple(audit) == ("new", "old")
    assert Path(out["backup"]).is_file() and Path(out["projects_yaml_backup"]).is_file()


def test_reverse_rename_restores_the_original_names(tmp_path: Path) -> None:
    home, conn, _ = _home(tmp_path)
    rename_project(home, conn, "old", "new")
    rename_project(home, conn, "new", "old")
    assert {r[0] for r in conn.execute("SELECT DISTINCT project FROM tasks")} == {"old", "other"}
    assert "  old:\r\n" in (home / "projects.yaml").read_bytes().decode()
    assert (home / "logs" / "old" / "a_1.log").is_file()
    assert json.loads(conn.execute("SELECT depends_on FROM tasks WHERE task_id='c'").fetchone()[0]) == [
        "old/a",
        "other/zzz",
    ]


def test_alias_chains_when_renamed_again(tmp_path: Path) -> None:
    home, conn, _ = _home(tmp_path)
    rename_project(home, conn, "old", "mid")
    rename_project(home, conn, "mid", "last")
    assert resolve_project(conn, "old") == "last" and resolve_project(conn, "mid") == "last"
    assert project_rename.remove_alias(conn, "old") and resolve_project(conn, "old") == "old"


@pytest.mark.parametrize(
    ("new", "needle"),
    [("old", "same"), ("bad name!", "invalid"), ("other", "already registered"), ("-x", "invalid")],
)
def test_refusals_change_nothing(tmp_path: Path, new: str, needle: str) -> None:
    home, conn, _ = _home(tmp_path)
    before = _snapshot(home, conn)
    with pytest.raises(RenameError, match=needle):
        rename_project(home, conn, "old", new)
    assert _snapshot(home, conn) == before


def test_running_task_unknown_project_and_existing_rows_are_refused(tmp_path: Path) -> None:
    home, conn, _ = _home(tmp_path)
    with pytest.raises(RenameError, match="not registered"):
        rename_project(home, conn, "ghost", "new")
    _task(conn, "new", "left_over")  # history under the target name
    conn.commit()
    with pytest.raises(RenameError, match="already exist"):
        rename_project(home, conn, "old", "new")
    conn.execute("DELETE FROM tasks WHERE project='new'")
    conn.execute("UPDATE tasks SET status='running' WHERE project='old' AND task_id='a'")
    conn.commit()
    with pytest.raises(RenameError, match="running"):
        rename_project(home, conn, "old", "new")


def test_db_failure_rolls_back_and_restores_the_registry(tmp_path: Path, monkeypatch) -> None:
    home, conn, _ = _home(tmp_path)
    before = _snapshot(home, conn)

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(project_rename, "_record_alias", boom)
    with pytest.raises(sqlite3.OperationalError):
        rename_project(home, conn, "old", "new")
    assert _snapshot(home, conn) == before  # DB rolled back, registry restored, logs untouched


def test_stale_yaml_mentions_in_other_projects_are_listed(tmp_path: Path) -> None:
    home, conn, root = _home(tmp_path)
    (root / "other" / "experiments" / "operator_queue" / "t.yaml").write_text(
        "task_id: t\ncommand: x\ndepends_on: [old/a]\n", encoding="utf-8"
    )
    plan = plan_rename(home, conn, "old", "new")
    assert [Path(p).name for p in plan["stale_yaml_mentions"]] == ["t.yaml"]


def _handler(home: Path, conn, cb=None):
    from tqdaemon.web.handler import make_handler

    return make_handler(conn, {}, {}, lambda: {}, home / "queue.db", "127.0.0.1", rename_project=cb)


def test_rename_route_passes_arguments_and_maps_errors(tmp_path: Path) -> None:
    from tests.helpers.master_worker import http_request

    home, conn, _ = _home(tmp_path)
    seen: list[tuple] = []

    def cb(old, new, dry_run, alias):
        seen.append((old, new, dry_run, alias))
        if new == "ghost":
            return {"ok": False, "error": "project 'x' is not registered in projects.yaml"}
        if new == "busy":
            return {"ok": False, "error": "1 task(s) running in old; stop them first"}
        return {"ok": True, "old": old, "new": new}

    H = _handler(home, conn, cb)
    assert (
        http_request(H, "POST", "/api/project/rename/old", {"new": "fresh", "dry_run": True, "alias": False})[0] == 200
    )
    assert seen[-1] == ("old", "fresh", True, False)
    assert http_request(H, "POST", "/api/project/rename/old", {"new": "ghost"})[0] == 404
    assert http_request(H, "POST", "/api/project/rename/old", {"new": "busy"})[0] == 409
    assert http_request(H, "POST", "/api/project/rename/old", {})[0] == 400
    code, _ = http_request(_handler(home, conn), "POST", "/api/project/rename/old", {"new": "n"})
    assert code == 503  # no daemon callback wired


def test_rename_is_admin_only() -> None:
    from tqdaemon.operator_features import scope_allows

    assert scope_allows("admin", "POST", "/api/project/rename/old")
    assert not scope_allows("write", "POST", "/api/project/rename/old")


def test_old_name_still_resolves_in_task_urls_and_cli_keys(tmp_path: Path, monkeypatch) -> None:
    from tests.helpers.master_worker import http_request
    from tqdaemon._cli_helpers import _split_key

    home, conn, _ = _home(tmp_path)
    rename_project(home, conn, "old", "new")
    monkeypatch.setenv("TQDAEMON_HOME", str(home))
    assert _split_key("old/a") == ("new", "a") and _split_key("new/a") == ("new", "a")
    assert _split_key("other/c") == ("other", "c")
    H = _handler(home, conn)
    code_new, _ = http_request(H, "GET", "/api/tasks/new/a")
    code_old, _ = http_request(H, "GET", "/api/tasks/old/a")
    assert code_new == code_old and code_new != 404


def test_yaml_depending_on_the_old_name_syncs_to_the_new_one(tmp_path: Path) -> None:
    from tqdaemon.config import load_projects
    from tqdaemon.yaml_sync import _sync_project, clear_yaml_mtime_cache

    home, conn, root = _home(tmp_path)
    rename_project(home, conn, "old", "new")
    other = load_projects(home)["other"]
    (root / "other" / "experiments" / "operator_queue" / "d.yaml").write_text(
        "task_id: d\ncommand: python\ndepends_on: [old/a]\n", encoding="utf-8"
    )
    clear_yaml_mtime_cache()
    _sync_project(conn, other, home=home, force=True)
    row = conn.execute("SELECT depends_on FROM tasks WHERE project='other' AND task_id='d'").fetchone()
    assert json.loads(row["depends_on"]) == ["new/a"]


def test_cli_offline_dry_run_and_apply(tmp_path: Path, monkeypatch, capsys) -> None:
    from tqdaemon import _cli_project

    home, conn, _ = _home(tmp_path)
    conn.close()
    monkeypatch.setenv("TQDAEMON_HOME", str(home))
    monkeypatch.setattr("tqdaemon._cli_helpers.master_mutation", lambda *a, **k: (None, "offline"))
    assert _cli_project.cmd_project_rename(home, "old", "new", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Would rename project old -> new" in out and "tasks.project: 2" in out
    assert "  old:" in (home / "projects.yaml").read_bytes().decode()  # dry run wrote nothing
    assert _cli_project.cmd_project_rename(home, "old", "new") == 0
    out = capsys.readouterr().out
    assert "Renamed project old -> new" in out and "undo: tq project rename new old" in out
    assert _cli_project.cmd_project_rename(home, "old", "other") == 1  # old is gone now
    assert _cli_project.cmd_project_alias(home, ["list"]) == 0
    assert "old -> new" in capsys.readouterr().out
    assert _cli_project.cmd_project_alias(home, ["rm", "old"]) == 0
