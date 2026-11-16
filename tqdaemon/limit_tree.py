"""One limits tree: all (global) > sets > projects, the same cap fields on every node.

Each cap is a total over everything running under the node: the root is the global config,
a set is its subtree (``set_caps``), a project is itself. An empty value means "no cap of its
own"; ancestors still apply, and ``effective`` reports the tightest one and where it comes from.
``min_tasks`` is a per-project guarantee (a set value is the default for its members).

Storage is unchanged: global config keys, ``set_limit_*`` and ``project_limit_*`` rows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from tqdaemon.rows import row_resources

CAP_FIELDS = ("max_tasks", "max_cpu_cores", "max_ram_gib", "max_gpu_vram_gib", "max_gpu_hours_day")
ALL_FIELDS = (*CAP_FIELDS, "min_tasks")
# Root node fields → global config keys (no global GPU-hours/day or min_tasks).
ROOT_KEYS = {
    "max_tasks": "max_running_tasks",
    "max_cpu_cores": "max_cpu_cores",
    "max_ram_gib": "max_ram_gib",
    "max_gpu_vram_gib": "gpu_vram_limit_gib",
}
ROOT_ID = "all"


def node_id(kind: str, path: str = "") -> str:
    return ROOT_ID if kind == "root" else f"{kind}:{path}"


def _running_usage(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Per project: running task count and declared cpu/ram/vram of running tasks."""
    out: dict[str, dict[str, float]] = {}
    for r in conn.execute("SELECT project, resources FROM tasks WHERE status = 'running'").fetchall():
        u = out.setdefault(str(r["project"]), {"running": 0, "cpu": 0.0, "ram": 0.0, "vram": 0.0})
        u["running"] += 1
        res = row_resources(r)
        u["cpu"] += float(res.get("cpu_cores") or 0)
        u["ram"] += float(res.get("ram_gib") or 0)
        u["vram"] += float(res.get("gpu_vram_gib") or 0)
    return out


def _clean(vals: Mapping[str, Any] | None, fields: tuple[str, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    for f in fields:
        try:
            v = float((vals or {}).get(f) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v:
            out[f] = v
    return out


def build_limit_tree(conn: sqlite3.Connection, projects: Mapping[str, Any] | None) -> dict[str, Any]:
    """``GET /api/limits/tree`` payload: ``{root, nodes: {id: node}}``."""
    from tqdaemon._db_runs import gpu_hours_today_by_project
    from tqdaemon.db import get_config
    from tqdaemon.project_limits import (
        explicit_set_nodes,
        get_all_project_limits,
        get_all_set_limits,
        set_path_prefixes,
        set_paths_from_projects,
    )
    from tqdaemon.project_sets import flat_nodes

    projs = dict(projects or {})
    cfg = get_config(conn)
    explicit = explicit_set_nodes(conn)
    flat = flat_nodes(projs, explicit)  # type: ignore[arg-type]
    set_lim = get_all_set_limits(conn)
    proj_lim = get_all_project_limits(conn)
    paths = set_paths_from_projects(projs)
    use = _running_usage(conn)
    gpu_h = gpu_hours_today_by_project(conn)

    def usage(members: list[str]) -> dict[str, float]:
        tot = {"running": 0.0, "cpu": 0.0, "ram": 0.0, "vram": 0.0, "gpu_hours_today": 0.0}
        for m in members:
            u = use.get(m) or {}
            for k in ("running", "cpu", "ram", "vram"):
                tot[k] += float(u.get(k, 0) or 0)
            tot["gpu_hours_today"] += float(gpu_h.get(m, 0.0) or 0.0)
        return {k: round(v, 3) for k, v in tot.items()}

    root_own = _clean({f: cfg.get(k) for f, k in ROOT_KEYS.items()}, CAP_FIELDS)
    nodes: dict[str, dict[str, Any]] = {
        ROOT_ID: {
            "id": ROOT_ID,
            "kind": "root",
            "path": "",
            "label": "all",
            "parent": None,
            "own": root_own,
            "fields": list(ROOT_KEYS),
            "usage": usage(sorted(projs)),
            "children": [],
        }
    }
    for path in sorted(flat):
        parent = "/".join(path.split("/")[:-1])
        nodes[node_id("set", path)] = {
            "id": node_id("set", path),
            "kind": "set",
            "path": path,
            "label": path.split("/")[-1],
            "parent": node_id("set", parent) if parent else ROOT_ID,
            "own": _clean(set_lim.get(path), ALL_FIELDS),
            "fields": list(ALL_FIELDS),
            "usage": usage(list(flat[path]["subtree_projects"])),
            "explicit": path in explicit,
            "children": [],
        }
    for name in sorted(projs):
        sp = paths.get(name)
        nodes[node_id("project", name)] = {
            "id": node_id("project", name),
            "kind": "project",
            "path": name,
            "label": name,
            "parent": node_id("set", sp) if sp and node_id("set", sp) in nodes else ROOT_ID,
            "own": _clean(proj_lim.get(name), ALL_FIELDS),
            "fields": list(ALL_FIELDS),
            "usage": usage([name]),
            "children": [],
        }
    for nid, node in nodes.items():
        if node["parent"]:
            nodes[node["parent"]]["children"].append(nid)

    # Effective = tightest non-zero cap on the node or any ancestor; min_tasks = own or nearest set.
    for node in nodes.values():
        chain: list[dict[str, Any]] = []
        cur: dict[str, Any] | None = node
        while cur is not None:
            chain.append(cur)
            cur = nodes.get(cur["parent"]) if cur["parent"] else None
        eff: dict[str, dict[str, Any]] = {}
        for f in CAP_FIELDS:
            best: tuple[float, str] | None = None
            for n in chain:
                v = n["own"].get(f)
                if v and (best is None or v < best[0]):
                    best = (v, n["id"])
            if best:
                eff[f] = {"value": best[0], "from": best[1]}
        if node["kind"] == "project":
            mt = node["own"].get("min_tasks")
            src = node["id"]
            if not mt:
                for prefix in set_path_prefixes(paths.get(node["path"])):
                    v = (set_lim.get(prefix) or {}).get("min_tasks")
                    if v:
                        mt, src = float(v), node_id("set", prefix)
                        break
            if mt:
                eff["min_tasks"] = {"value": mt, "from": src}
        node["effective"] = eff
    return {"root": ROOT_ID, "nodes": nodes}


def write_limit_node(conn: sqlite3.Connection, kind: str, path: str, fields: Mapping[str, Any]) -> str | None:
    """Set cap fields on one node (0 / empty clears). Returns an error message or None."""
    from tqdaemon.config import validate_config
    from tqdaemon.db import get_project_limits, set_config, set_project_limit
    from tqdaemon.project_limits import set_set_limit

    allowed = tuple(ROOT_KEYS) if kind == "root" else ALL_FIELDS
    vals: dict[str, float] = {}
    for f, raw in (fields or {}).items():
        if f not in allowed:
            return f"field {f!r} is not settable on a {kind} node"
        try:
            v = float(raw) if raw not in (None, "") else 0.0
        except (TypeError, ValueError):
            return f"{f}: not a number: {raw!r}"
        if v < 0:
            return f"{f}: must be >= 0"
        vals[f] = v
    if kind == "root":
        for f, v in vals.items():
            key = ROOT_KEYS[f]
            norm: float | int = int(v) if key in ("max_running_tasks", "max_cpu_cores") else v
            err = validate_config(key, norm)
            if err:
                return err
        for f, v in vals.items():
            key = ROOT_KEYS[f]
            set_config(conn, key, int(v) if key in ("max_running_tasks", "max_cpu_cores") else v)
        return None
    if not path:
        return "path is required"
    if kind == "project":
        cur = dict(get_project_limits(conn, path) or {})
        cur.update(vals)
        if cur.get("min_tasks") and cur.get("max_tasks") and cur["min_tasks"] > cur["max_tasks"]:
            return "min_tasks cannot exceed max_tasks"
        for f, v in vals.items():
            set_project_limit(conn, path, f, int(v) if f in ("max_tasks", "min_tasks") else v, commit=False)
        conn.commit()
        return None
    if kind == "set":
        for f, v in vals.items():
            set_set_limit(conn, path, f, int(v) if f in ("max_tasks", "min_tasks") else v, commit=False)
        conn.commit()
        return None
    return f"unknown node kind {kind!r}"
