"""``tq archive``: take old finished tasks out of the live queue without losing them.

Each archived task (row + its runs) is appended to ``<home>/archive/<project>.jsonl``, its YAML
moves to ``<queue_dir>/archive/`` (a subdirectory, so YAML sync never globs it), and the DB rows
are deleted. Nothing is archived while running, and the YAML move happens before the DB delete so
a later sync cannot resurrect or delete-by-absence anything inconsistently.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tqdaemon.rows import has_col

DEFAULT_STATUSES = ("completed",)
_NEVER = frozenset({"running", "queued", "suspended"})


def archive_tasks(
    conn,
    projects: dict[str, Any],
    *,
    home: Path,
    older_than_days: float = 30.0,
    statuses: tuple[str, ...] = DEFAULT_STATUSES,
    only_projects: list[str] | None = None,
    dry_run: bool = False,
    now: str | None = None,
    max_completed_runs: int | None = None,
    task_glob: str | None = None,
) -> dict[str, Any]:
    """Archive tasks in ``statuses`` that finished more than ``older_than_days`` ago.

    Age is ``finished_at`` (falls back to ``updated_at`` for rows that never finished): YAML
    sync rewrites ``updated_at`` on every pass, so it cannot tell old tasks from new ones.

    ``max_completed_runs`` (optional): skip tasks with ``completed_run_count`` greater than
    this value (keeps multi-use / often re-run tasks). ``None`` = no limit (backward compatible).

    ``task_glob`` (optional): only tasks whose ``task_id`` matches this ``fnmatch`` pattern (comma-separated
    patterns are alternatives), e.g. ``localize_ui_messages__*``.
    """
    statuses = tuple(s for s in statuses if s not in _NEVER)
    if not statuses:
        return {"archived": 0, "tasks": [], "dry_run": dry_run, "max_completed_runs": max_completed_runs}
    ref = datetime.fromisoformat(now) if now else datetime.now(UTC).replace(tzinfo=None)
    cutoff = (ref - timedelta(days=older_than_days)).isoformat()
    marks = ",".join("?" * len(statuses))
    sql = f"SELECT * FROM tasks WHERE status IN ({marks}) AND COALESCE(finished_at, updated_at) < ?"  # noqa: S608
    params: list[Any] = [*statuses, cutoff]
    if only_projects:
        sql += f" AND project IN ({','.join('?' * len(only_projects))})"
        params += only_projects
    if max_completed_runs is not None:
        sql += " AND COALESCE(completed_run_count, 0) <= ?"
        params.append(int(max_completed_runs))
    rows = conn.execute(sql + " ORDER BY project, task_id", params).fetchall()
    if task_glob:
        import fnmatch

        pats = [p.strip() for p in task_glob.split(",") if p.strip()]
        rows = [r for r in rows if any(fnmatch.fnmatchcase(str(r["task_id"]), p) for p in pats)]
    needed = _needed_dependencies(conn)
    kept = 0
    done: list[dict[str, str]] = []
    pending: list[tuple[str | None, Any]] = []  # YAMLs to move once their rows are committed

    def _flush() -> None:
        conn.commit()
        for src_, proj_obj in pending:
            _move_yaml(src_, proj_obj)
        pending.clear()

    for row in rows:
        proj, tid = row["project"], row["task_id"]
        src = row["yaml_source"] if has_col(row, "yaml_source") else None
        if not _movable(src):
            continue  # shares a YAML with other tasks: sync would resurrect it
        if (proj, tid) in needed:
            kept += 1  # an unfinished task depends on it; a missing dependency blocks forever
            continue
        done.append({"project": proj, "task_id": tid})
        if dry_run:
            continue
        runs = [dict(r) for r in conn.execute("SELECT * FROM runs WHERE project = ? AND task_id = ?", (proj, tid))]
        out = home / "archive"
        out.mkdir(parents=True, exist_ok=True)
        with open(out / f"{proj}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"task": dict(row), "runs": runs}, default=str) + "\n")
        # Task row first: tasks.last_run_id references runs(run_id).
        conn.execute("DELETE FROM tasks WHERE project = ? AND task_id = ?", (proj, tid))
        for r in runs:
            conn.execute("DELETE FROM resource_history WHERE run_id = ?", (r["run_id"],))
        conn.execute("DELETE FROM runs WHERE project = ? AND task_id = ?", (proj, tid))
        pending.append((src, projects.get(proj)))
        if len(pending) >= 200:  # one write transaction per batch, not per task
            _flush()
    if not dry_run:
        _flush()
    return {
        "archived": len(done),
        "tasks": done,
        "dry_run": dry_run,
        "max_completed_runs": max_completed_runs,
        "kept_as_dependency": kept,
    }


def _needed_dependencies(conn) -> set[tuple[str, str]]:
    """(project, task_id) named in depends_on / depends_on_any of any task that has not finished."""
    from tqdaemon.deprefs import resolve_dep_ref
    from tqdaemon.jsonutil import parse_json_list

    out: set[tuple[str, str]] = set()
    for r in conn.execute(
        "SELECT project, depends_on, depends_on_any FROM tasks "
        "WHERE status IN ('queued','running','suspended','interrupted') "
        "AND (COALESCE(depends_on,'') NOT IN ('','[]','null') OR COALESCE(depends_on_any,'') NOT IN ('','[]','null'))"
    ).fetchall():
        for ref in [*parse_json_list(r["depends_on"]), *parse_json_list(r["depends_on_any"])]:
            dp, did, _ = resolve_dep_ref(str(r["project"]), str(ref))
            out.add((dp, did))
    return out


def _movable(src: str | None) -> bool:
    """True when the task's YAML is absent/untracked or holds only this task."""
    if not src or not Path(src).is_file():
        return True
    try:
        from tqdaemon.taskspec import parse_task_yaml_many

        return len(parse_task_yaml_many(Path(src))) <= 1
    except Exception:  # noqa: BLE001 - an unparseable file holds no other live task
        return True  # unparseable file holds no other live task


def _move_yaml(src: str | None, project: Any) -> None:
    """Move the task's YAML to ``<queue_dir>/archive/``."""
    if not src or project is None or not Path(src).is_file():
        return
    dest_dir = Path(project.queue_dir) / "archive"
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(src, str(dest_dir / Path(src).name))
