"""R1 + C3 (gpu-admit-hold): declared VRAM stays held for running local tasks as max(live, declared)."""

import json
import sqlite3
from types import SimpleNamespace

from tqdaemon import scheduler


class _Budget:
    def __init__(self, gpu_live: float) -> None:
        self.gpu_live = gpu_live
        self.vram: list[float] = []

    def reserve(self, cpu: float, vram: float, ram: float) -> None:
        self.vram.append(vram)

    @property
    def reserved_total(self) -> float:
        """What the budget would count: live host VRAM + everything reserved on top."""
        return self.gpu_live + sum(self.vram)


def _run(*, elapsed: float = 600.0, vram: float = 0.0):
    return SimpleNamespace(elapsed_sec=lambda: elapsed, current_ram_gib=1.0, current_vram_gib=vram)


def _seed(
    tasks: list[tuple[float, float]],
    *,
    gpu_live: float,
    settle: float = 0.0,
    elapsed: float = 600.0,
    leased: float = 0.0,
) -> _Budget:
    """``tasks`` = [(declared_vram, measured_vram)] of running tasks."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE runs (run_id INTEGER, project TEXT, task_id TEXT, status TEXT, peak_ram_gib REAL, peak_vram_gib REAL)"
    )
    conn.execute("CREATE TABLE tasks (project TEXT, task_id TEXT, yaml_source TEXT, grp TEXT)")
    rows, active = [], {}
    for i, (declared, measured) in enumerate(tasks):
        rows.append(
            {"project": "p", "task_id": f"t{i}", "resources": json.dumps({"gpu_vram_gib": declared, "ram_gib": 2.0})}
        )
        active[("p", f"t{i}")] = _run(elapsed=elapsed, vram=measured)
    budget = _Budget(gpu_live)
    scheduler._seed_budget_from_running(
        conn,
        rows,
        active,
        budget,
        scheduler._OccupancyState(),
        {},
        default_ram=1.0,
        predict_runs=5,
        settle_window=settle,
        est_cache={},
        leased_vram_gib=leased,
    )
    return budget


def test_declared_vram_held_with_zero_settle_window() -> None:
    assert _seed([(5.0, 0.5)], gpu_live=0.5).reserved_total == 5.0


def test_wddm_unmeasured_vram_counts_declared_once_not_on_top_of_live() -> None:
    # C3: nvidia-smi gives no per-process VRAM (N/A); host used 2.6 GiB, tasks declare 5.5 + 3.0.
    assert _seed([(5.5, 0.0), (3.0, 0.0)], gpu_live=2.6).reserved_total == 8.5


def test_no_extra_hold_once_live_reaches_declared() -> None:
    assert _seed([(5.0, 5.2)], gpu_live=5.4).reserved_total == 5.4


def test_hold_survives_past_settle_window() -> None:
    assert _seed([(5.0, 0.0)], gpu_live=1.0, settle=180.0, elapsed=600.0).reserved_total == 5.0
