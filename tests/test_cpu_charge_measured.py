"""cpu_charge_measured (omnicard3 request): charge max(declared, measured cores)."""

import json
import sqlite3
from types import SimpleNamespace

from tqdaemon import scheduler


class _Budget:
    gpu_live = 0.0

    def __init__(self) -> None:
        self.cpu: list[float] = []

    def reserve(self, cpu: float, vram: float, ram: float) -> None:
        self.cpu.append(cpu)


def _charged(monkeypatch, *, enabled: bool, cpu_pct: float, declared: float = 1.0) -> float:
    monkeypatch.setattr(scheduler, "_host_cpu_count", lambda: 16)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE runs (run_id INTEGER, project TEXT, task_id TEXT, status TEXT, peak_ram_gib REAL, peak_vram_gib REAL)"
    )
    conn.execute("CREATE TABLE tasks (project TEXT, task_id TEXT, yaml_source TEXT, grp TEXT)")
    row = {"project": "p", "task_id": "t", "resources": json.dumps({"cpu_cores": declared, "ram_gib": 1.0})}
    run = SimpleNamespace(elapsed_sec=lambda: 600.0, current_ram_gib=1.0, current_vram_gib=0.0, current_cpu_pct=cpu_pct)
    budget = _Budget()
    scheduler._seed_budget_from_running(
        conn,
        [row],
        {("p", "t"): run},
        budget,
        scheduler._OccupancyState(),
        {},
        default_ram=1.0,
        predict_runs=5,
        settle_window=180.0,
        est_cache={},
        charge_measured_cpu=enabled,
    )
    return budget.cpu[0]


def test_off_by_default_charges_declared(monkeypatch) -> None:
    assert _charged(monkeypatch, enabled=False, cpu_pct=100.0) == 1.0


def test_on_charges_measured_cores(monkeypatch) -> None:
    assert _charged(monkeypatch, enabled=True, cpu_pct=100.0) == 16.0


def test_on_keeps_declared_when_measured_is_lower(monkeypatch) -> None:
    assert _charged(monkeypatch, enabled=True, cpu_pct=5.0, declared=4.0) == 4.0


def test_prefers_smoothed_ema_over_instant_sample(monkeypatch) -> None:
    monkeypatch.setattr(scheduler, "_host_cpu_count", lambda: 16)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE runs (run_id INTEGER, project TEXT, task_id TEXT, status TEXT, peak_ram_gib REAL, peak_vram_gib REAL)"
    )
    conn.execute("CREATE TABLE tasks (project TEXT, task_id TEXT, yaml_source TEXT, grp TEXT)")
    row = {"project": "p", "task_id": "t", "resources": json.dumps({"cpu_cores": 1, "ram_gib": 1.0})}
    run = SimpleNamespace(
        elapsed_sec=lambda: 600.0, current_ram_gib=1.0, current_vram_gib=0.0, current_cpu_pct=100.0, cpu_pct_ema=25.0
    )
    budget = _Budget()
    scheduler._seed_budget_from_running(
        conn,
        [row],
        {("p", "t"): run},
        budget,
        scheduler._OccupancyState(),
        {},
        default_ram=1.0,
        predict_runs=5,
        settle_window=180.0,
        est_cache={},
        charge_measured_cpu=True,
    )
    assert budget.cpu[0] == 4.0  # 25% of 16 cores, not the 100% burst
