"""Rename a project id with its history (``tq project rename OLD NEW``).

A project id is the key in ``projects.yaml`` and the ``<project>/<task_id>`` prefix of every task. Unlike
``project_relocate`` (folder moved, id kept) this changes the id itself: every DB row, config key, log folder
and cross-project reference moves to the new name in one step, and an alias keeps the old name resolving.
Reuses the relocate helpers (backup, running check, path-prefix variants).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from tqdaemon._time import _now
from tqdaemon.config import load_projects, logs_dir, registry_path
from tqdaemon.project_limits import _PROJECT_LIMIT_FIELDS
from tqdaemon.project_relocate import backup_queue_db, count_running, status_histogram

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# (table, column) pairs holding a plain project name. ``UPDATE OR IGNORE`` + delete keeps a PK clash on
# an aggregate table (usage buckets) from aborting the rename; ``tasks``/``runs`` clashes are refused up front.
_PLAIN_TABLES = (
    ("tasks", "project"),
    ("runs", "project"),
    ("api_usage", "project"),
    ("operator_audit", "project"),
    ("project_usage", "project"),
    ("usage_snapshot", "project"),
    ("alert_rules", "project"),
    ("submit_keys", "project"),
)
# JSON-list columns that may contain the name; (table, column).
_JSON_LIST_COLUMNS = (("workers", "projects"), ("api_tokens", "projects"))
# Columns holding "<project>/<task_id>" references (JSON list or bare string) in ANY project's tasks.
_REF_LIST_COLUMNS = ("depends_on", "depends_on_any")
_REF_STR_COLUMNS = ("spawned_by",)


class RenameError(ValueError):
    """The rename was refused; the message is user-facing and nothing was changed."""


def _config_key_moves(conn: sqlite3.Connection, old: str, new: str) -> list[tuple[str, str]]:
    """``[(old_key, new_key)]`` for config rows that embed the project name."""
    keys = {f"project_color_{old}": f"project_color_{new}"}
    keys[f"project_frozen_{old}"] = f"project_frozen_{new}"
    keys[f"project_paused_{old}"] = f"project_paused_{new}"
    for field in _PROJECT_LIMIT_FIELDS:
        keys[f"project_limit_{old}_{field}"] = f"project_limit_{new}_{field}"
    marks = ",".join("?" * len(keys))
    rows = conn.execute(f"SELECT key FROM config WHERE key IN ({marks})", list(keys)).fetchall()  # noqa: S608
    return [(r["key"], keys[r["key"]]) for r in rows]


def _count(conn: sqlite3.Connection, table: str, column: str, name: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (name,)).fetchone()  # noqa: S608
    except sqlite3.OperationalError:  # table missing on an old DB
        return 0
    return int(row[0])


def _ref_rows(conn: sqlite3.Connection, old: str) -> list[sqlite3.Row]:
    like = f"%{old}/%"
    cols = " OR ".join(f"{c} LIKE ?" for c in (*_REF_LIST_COLUMNS, *_REF_STR_COLUMNS))
    n = len(_REF_LIST_COLUMNS) + len(_REF_STR_COLUMNS)
    return conn.execute(
        f"SELECT project, task_id, {', '.join((*_REF_LIST_COLUMNS, *_REF_STR_COLUMNS))} FROM tasks WHERE {cols}",  # noqa: S608
        [like] * n,
    ).fetchall()


def _rewrite_ref(value: str, old: str, new: str) -> str:
    """``old/x`` -> ``new/x`` for one reference; other values unchanged."""
    return f"{new}/{value[len(old) + 1 :]}" if value.startswith(old + "/") else value


def _stale_yaml_mentions(home: Path, old: str, exclude: str) -> list[str]:
    """Queue YAMLs of OTHER projects that still reference ``old/<task>`` (the alias keeps them working)."""
    out: list[str] = []
    pat = re.compile(rf"(?<![\w.-]){re.escape(old)}/[\w.-]+")
    for name, proj in load_projects(home).items():
        if name == exclude or not proj.queue_dir.is_dir():
            continue
        for path in sorted(proj.queue_dir.glob("*.y*ml")):
            try:
                if pat.search(path.read_text(encoding="utf-8", errors="replace")):
                    out.append(str(path))
            except OSError:
                continue
        if len(out) >= 50:
            break
    return out


def validate_names(home: Path, old: str, new: str) -> None:
    if old == new:
        raise RenameError("old and new names are the same")
    if not NAME_RE.match(new):
        raise RenameError(
            f"invalid project name {new!r}: use letters, digits, '_', '.', '-' (must start with a letter or digit)"
        )
    projects = load_projects(home)
    if old not in projects:
        raise RenameError(f"project {old!r} is not registered in projects.yaml")
    if new in projects:
        raise RenameError(f"a project named {new!r} is already registered")


def plan_rename(home: Path, conn: sqlite3.Connection, old: str, new: str) -> dict[str, Any]:
    """Read-only preview: what a rename would touch, and why it would be refused. Raises ``RenameError``."""
    validate_names(home, old, new)
    running = count_running(conn, old)
    if running:
        raise RenameError(f"{running} task(s) running in {old}; stop them first")
    for table in ("tasks", "runs"):
        n = _count(conn, table, "project", new)
        if n:
            raise RenameError(f"{n} {table} row(s) already exist under {new!r}; choose another name")
    counts = {f"{t}.{c}": _count(conn, t, c, old) for t, c in _PLAIN_TABLES}
    counts = {k: v for k, v in counts.items() if v}
    refs = _ref_rows(conn, old)
    logs = logs_dir(home, old)
    new_logs = logs_dir(home, new)
    if logs.is_dir() and new_logs.exists() and any(new_logs.iterdir()):
        raise RenameError(f"log folder {new_logs} already exists and is not empty")
    arch_old = home / "archive" / f"{old}.jsonl"
    arch_new = home / "archive" / f"{new}.jsonl"
    if arch_old.is_file() and arch_new.exists():
        raise RenameError(f"archive file {arch_new} already exists")
    return {
        "ok": True,
        "old": old,
        "new": new,
        "status": status_histogram(conn, old),
        "rows": counts,
        "reference_rows": len(refs),
        "config_keys": [a for a, _ in _config_key_moves(conn, old, new)],
        "log_folder": str(logs) if logs.is_dir() else None,
        "archive_file": str(arch_old) if arch_old.is_file() else None,
        "stale_yaml_mentions": _stale_yaml_mentions(home, old, exclude=old),
    }


def _rename_registry_key(home: Path, old: str, new: str) -> Path:
    """Rename the ``  <old>:`` key line in projects.yaml (CRLF/comments kept). Returns the backup path."""
    path = registry_path(home)
    text = path.read_bytes().decode("utf-8")  # bytes: read_text() would fold CRLF to LF
    pat = re.compile(rf"^(  ){re.escape(old)}(:[ \t]*\r?)$", re.MULTILINE)
    if len(pat.findall(text)) != 1:
        raise RenameError(f"could not find exactly one '  {old}:' entry in projects.yaml")
    backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    shutil.copy2(path, backup)
    path.write_bytes(pat.sub(lambda m: f"{m.group(1)}{new}{m.group(2)}", text, count=1).encode("utf-8"))
    return backup


def _rewrite_json_list(raw: Any, old: str, new: str) -> str | None:
    """New JSON text for a list column containing ``old``; None when nothing changes."""
    try:
        items = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return None
    if not isinstance(items, list) or old not in items:
        return None
    return json.dumps([new if x == old else x for x in items])


def _forms(path: Path) -> list[str]:
    """The same path spelled with native, forward and back slashes (parallel lists for old/new)."""
    raw = str(path)
    return [raw, raw.replace("\\", "/"), raw.replace("/", "\\")]


def _rewrite_db(conn: sqlite3.Connection, old: str, new: str, *, old_logs: Path, new_logs: Path) -> dict[str, int]:
    """Every DB rewrite, inside the caller's transaction."""
    done: dict[str, int] = {}
    for table, column in _PLAIN_TABLES:
        try:
            cur = conn.execute(f"UPDATE OR IGNORE {table} SET {column} = ? WHERE {column} = ?", (new, old))  # noqa: S608
            conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (old,))  # noqa: S608 - PK clashes only
        except sqlite3.OperationalError:
            continue
        if cur.rowcount:
            done[f"{table}.{column}"] = cur.rowcount
    for table, column in _JSON_LIST_COLUMNS:
        try:
            rows = conn.execute(
                f"SELECT rowid AS rid, {column} FROM {table} WHERE {column} LIKE ?",  # noqa: S608
                (f"%{old}%",),
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        n = 0
        for r in rows:
            fresh = _rewrite_json_list(r[column], old, new)
            if fresh is not None:
                conn.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (fresh, r["rid"]))  # noqa: S608
                n += 1
        if n:
            done[f"{table}.{column}"] = n
    n_ref = 0
    for r in _ref_rows(conn, old):  # references live in any project's rows (the renamed ones included)
        sets: dict[str, str] = {}
        for col in _REF_LIST_COLUMNS:
            try:
                items = json.loads(r[col] or "[]")
            except ValueError:
                continue
            if isinstance(items, list):
                fresh = [_rewrite_ref(x, old, new) if isinstance(x, str) else x for x in items]
                if fresh != items:
                    sets[col] = json.dumps(fresh)
        for col in _REF_STR_COLUMNS:
            val = r[col]
            if isinstance(val, str) and val.startswith(old + "/"):
                sets[col] = _rewrite_ref(val, old, new)
        if sets:
            assigns = ", ".join(f"{c} = ?" for c in sets)
            conn.execute(
                f"UPDATE tasks SET {assigns} WHERE project = ? AND task_id = ?",  # noqa: S608
                [*sets.values(), r["project"], r["task_id"]],
            )
            n_ref += 1
    if n_ref:
        done["task_references"] = n_ref
    moved = _config_key_moves(conn, old, new)
    for old_key, new_key in moved:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (old_key,)).fetchone()
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (new_key, row["value"]))
        conn.execute("DELETE FROM config WHERE key = ?", (old_key,))
    if moved:
        done["config_keys"] = len(moved)
    if old_logs.is_dir():
        n_logs = 0
        for variant_old, variant_new in zip(_forms(old_logs), _forms(new_logs), strict=True):
            # substr comparison, not LIKE: '_' and '%' in a path would act as wildcards
            cur = conn.execute(
                "UPDATE runs SET log_path = ? || substr(log_path, ?) WHERE substr(log_path, 1, ?) = ?",
                (variant_new, len(variant_old) + 1, len(variant_old), variant_old),
            )
            n_logs += max(cur.rowcount, 0)
        if n_logs:
            done["runs.log_path"] = n_logs
    return done


def _record_alias(conn: sqlite3.Connection, old: str, new: str) -> None:
    conn.execute("UPDATE project_aliases SET new = ? WHERE new = ?", (new, old))  # chain: A->B, B->C => A->C
    conn.execute(
        "INSERT OR REPLACE INTO project_aliases (old, new, created_at) VALUES (?, ?, ?)",
        (old, new, _now()),
    )


def resolve_project(conn: sqlite3.Connection, name: str) -> str:
    """The current name for ``name`` (itself unless an alias from a rename points elsewhere)."""
    try:
        row = conn.execute("SELECT new FROM project_aliases WHERE old = ?", (name,)).fetchone()
    except sqlite3.OperationalError:  # table missing on an old DB
        return name
    return str(row["new"]) if row is not None else name


def resolve_project_in_home(home: Path | None, name: str) -> str:
    """``resolve_project`` for callers without a connection (CLI keys, MCP args): read-only, never raises."""
    if not name:
        return name
    try:
        from tqdaemon.config import db_path, state_home
        from tqdaemon.db import open_ro

        path = db_path(home or state_home())
        if not path.is_file():
            return name
        conn = open_ro(path, timeout=1.0)
        try:
            return resolve_project(conn, name)
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return name


def alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    """``{old: new}`` for every recorded rename (empty when none)."""
    return {a["old"]: a["new"] for a in list_aliases(conn)}


def canonical_dep_refs(refs: tuple[str, ...], aliases: dict[str, str]) -> tuple[str, ...]:
    """Rewrite ``old/<task>`` dependency refs to the project's current name; bare ids are untouched."""
    out = []
    for ref in refs:
        head, sep, tail = str(ref).partition("/")
        out.append(f"{aliases[head]}/{tail}" if sep and head in aliases else str(ref))
    return tuple(out)


def list_aliases(conn: sqlite3.Connection) -> list[dict[str, str]]:
    try:
        rows = conn.execute("SELECT old, new, created_at FROM project_aliases ORDER BY old").fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"old": r["old"], "new": r["new"], "created_at": r["created_at"]} for r in rows]


def remove_alias(conn: sqlite3.Connection, old: str) -> bool:
    cur = conn.execute("DELETE FROM project_aliases WHERE old = ?", (old,))
    conn.commit()
    return cur.rowcount > 0


def rename_project(home: Path, conn: sqlite3.Connection, old: str, new: str, *, alias: bool = True) -> dict[str, Any]:
    """Apply the rename. Raises ``RenameError`` (nothing changed) or the underlying error after a rollback.

    The caller (daemon callback / CLI) is responsible for reloading the registry afterwards.
    """
    from tqdaemon.yaml_sync import _SYNC_LOCK

    summary = plan_rename(home, conn, old, new)
    old_logs, new_logs = logs_dir(home, old), logs_dir(home, new)
    with _SYNC_LOCK:  # a periodic sync between the yaml edit and the DB update would create NEW/... duplicates
        db_backup = backup_queue_db(home)
        yaml_backup = _rename_registry_key(home, old, new)
        try:
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            done = _rewrite_db(conn, old, new, old_logs=old_logs, new_logs=new_logs)
            if alias:
                _record_alias(conn, old, new)
            conn.execute(
                "INSERT INTO operator_audit (ts, actor, action, project, target, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), "cli", "project_rename", new, old, json.dumps(done)),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            shutil.copy2(yaml_backup, registry_path(home))  # undo the registry edit
            raise
        moved_files: list[str] = []
        try:
            if old_logs.is_dir():
                if new_logs.exists():
                    new_logs.rmdir()  # plan_rename only allows an empty folder here
                old_logs.rename(new_logs)
                moved_files.append(f"{old_logs} -> {new_logs}")
            arch_old = home / "archive" / f"{old}.jsonl"
            if arch_old.is_file():
                arch_old.rename(home / "archive" / f"{new}.jsonl")
                moved_files.append(f"{arch_old.name} -> {new}.jsonl")
        except OSError as exc:
            summary["warnings"] = [f"DB and projects.yaml renamed, but moving files failed: {exc}"]
    summary.update(
        {
            "dry_run": False,
            "applied": done,
            "alias": alias,
            "backup": str(db_backup),
            "projects_yaml_backup": str(yaml_backup),
            "moved": moved_files,
            "after": status_histogram(conn, new),
        }
    )
    return summary


def checklist(old: str, new: str) -> list[str]:
    """What a rename cannot do for you."""
    return [
        f"other repos' docs / YAML that say '{old}/<task>' (the alias keeps depends_on working; update them when convenient)",
        f"inbox/{old} -> rename it or leave a junction to inbox/{new}",
        f"<portfolio-root>\\ports.yaml / devkit registries that list the project as '{old}'",
        f"remote workers: run `tq worker sync` so they pick up the new name '{new}'",
    ]
