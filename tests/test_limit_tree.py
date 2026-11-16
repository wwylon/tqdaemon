"""One limits tree: all > sets > projects; effective = tightest ancestor cap; node writes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tqdaemon.config import VALID_CONFIG_KEYS, Project
from tqdaemon.config_schema import SCHEMA, config_schema
from tqdaemon.db import get_config, get_project_limits, open_db
from tqdaemon.limit_tree import build_limit_tree, write_limit_node
from tqdaemon.project_limits import get_set_limits


def _projects(tmp_path: Path) -> dict:
    def p(name, set_path):
        return Project(name=name, root=tmp_path, queue_dir=tmp_path / name, set_path=set_path)

    return {"b1": p("b1", "bots/bridge"), "b2": p("b2", "bots"), "solo": p("solo", None)}


def _conn(tmp_path: Path):
    conn = open_db(tmp_path / "q.db")
    now = datetime.now(UTC).isoformat()
    for tid, proj in (("r1", "b1"), ("r2", "b1"), ("r3", "solo")):
        conn.execute(
            "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at, resources) "
            "VALUES (?,?,'x','running',?,?,'{\"cpu_cores\": 2, \"ram_gib\": 3}')",
            (proj, tid, now, now),
        )
    conn.commit()
    return conn


def test_tree_shape_usage_and_effective(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    projs = _projects(tmp_path)
    assert write_limit_node(conn, "root", "", {"max_tasks": 8}) is None
    assert write_limit_node(conn, "set", "bots", {"max_tasks": 4, "min_tasks": 1}) is None
    assert write_limit_node(conn, "set", "bots/bridge", {"max_tasks": 2}) is None
    tree = build_limit_tree(conn, projs)
    n = tree["nodes"]
    assert set(n) == {"all", "set:bots", "set:bots/bridge", "project:b1", "project:b2", "project:solo"}
    assert n["project:b1"]["parent"] == "set:bots/bridge" and n["project:solo"]["parent"] == "all"
    assert n["set:bots"]["usage"]["running"] == 2 and n["all"]["usage"]["running"] == 3
    assert n["set:bots"]["usage"]["cpu"] == 4 and n["set:bots"]["usage"]["ram"] == 6
    # b1: no own cap -> tightest ancestor (bots/bridge=2), not bots (4) or all (8).
    assert n["project:b1"]["effective"]["max_tasks"] == {"value": 2, "from": "set:bots/bridge"}
    assert n["project:b2"]["effective"]["max_tasks"] == {"value": 4, "from": "set:bots"}
    assert n["project:solo"]["effective"]["max_tasks"] == {"value": 8, "from": "all"}
    # min_tasks: per-project default from the nearest set.
    assert n["project:b1"]["effective"]["min_tasks"] == {"value": 1, "from": "set:bots"}


def test_writes_route_to_existing_storage(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    assert write_limit_node(conn, "root", "", {"max_tasks": 5, "max_ram_gib": 40}) is None
    cfg = get_config(conn)
    assert cfg["max_running_tasks"] == 5 and cfg["max_ram_gib"] == 40
    assert write_limit_node(conn, "project", "b1", {"max_tasks": 3}) is None
    assert get_project_limits(conn, "b1")["max_tasks"] == 3
    assert write_limit_node(conn, "project", "b1", {"max_tasks": ""}) is None  # clear
    assert get_project_limits(conn, "b1")["max_tasks"] == 0
    assert write_limit_node(conn, "set", "bots", {"max_gpu_hours_day": 2.5}) is None
    assert get_set_limits(conn, "bots")["max_gpu_hours_day"] == 2.5


@pytest.mark.parametrize(
    ("kind", "path", "fields", "msg"),
    [
        ("root", "", {"min_tasks": 1}, "not settable"),
        ("root", "", {"max_tasks": 9999}, "between"),
        ("project", "b1", {"max_tasks": 1, "min_tasks": 2}, "min_tasks cannot exceed"),
        ("project", "b1", {"max_tasks": -1}, ">= 0"),
        ("project", "b1", {"max_tasks": "x"}, "not a number"),
        ("set", "", {"max_tasks": 1}, "path is required"),
        ("weird", "a", {"max_tasks": 1}, "unknown node kind"),
    ],
)
def test_write_validation(tmp_path: Path, kind, path, fields, msg) -> None:
    conn = _conn(tmp_path)
    err = write_limit_node(conn, kind, path, fields)
    assert err and msg in err


def test_schema_covers_every_config_key() -> None:
    assert set(SCHEMA) == set(VALID_CONFIG_KEYS)
    keys = {k["key"]: k for k in config_schema()["keys"]}
    assert keys["gpu_first"]["type"] == "bool"
    assert keys["frozen_action"]["type"] == "enum" and "restart" in keys["frozen_action"]["enum"]
    assert keys["auth_token"]["secret"] is True
