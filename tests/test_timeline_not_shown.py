"""Timeline forecast lists queued tasks that get no bar, with the reason."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.test_operator_features import _mk_task
from tqdaemon import timeline_forecast as tf
from tqdaemon.db import open_db, set_config

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _ns(db) -> dict[str, dict]:
    data = tf.forecast_timeline(db, hours=4, now=NOW)
    return {x["task_id"]: x for x in data["not_shown"]}


def test_paused_and_frozen_projects_are_listed(tmp_path: Path) -> None:
    db = open_db(tmp_path / "q.db")
    _mk_task(db, "pp", "a")
    _mk_task(db, "ff", "b")
    _mk_task(db, "ok", "c")
    set_config(db, "project_paused_pp", 1)
    set_config(db, "project_frozen_ff", 1)
    ns = _ns(db)
    assert ns["a"]["reason"] == "paused"
    assert ns["b"]["reason"] == "frozen"
    assert "c" not in ns


def test_dead_and_unmet_dependencies(tmp_path: Path) -> None:
    db = open_db(tmp_path / "q.db")
    _mk_task(db, "p", "smoke", status="failed")
    _mk_task(db, "p", "train", depends_on=["smoke"])
    _mk_task(db, "p", "gate", depends_on=["train"])
    _mk_task(db, "p", "orphan", depends_on=["gone"])
    ns = _ns(db)
    assert ns["train"]["reason"] == "dead_dep" and "smoke [failed]" in ns["train"]["detail"]
    assert ns["gate"]["reason"] == "deps_unmet"
    assert ns["orphan"]["reason"] == "dead_dep" and "missing" in ns["orphan"]["detail"]


def test_nothing_hidden_gives_empty_list(tmp_path: Path) -> None:
    db = open_db(tmp_path / "q.db")
    _mk_task(db, "p", "x")
    assert tf.forecast_timeline(db, hours=4, now=NOW)["not_shown"] == []
