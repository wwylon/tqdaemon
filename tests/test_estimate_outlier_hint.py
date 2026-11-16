"""Bad peak-RAM samples must not dominate admission hints (bridge2-htn report)."""

from tqdaemon import estimate


def test_p95_drops_single_wild_outlier() -> None:
    assert estimate._p95([0.9, 0.8, 0.7, 19.2]) == 0.9


def test_p95_keeps_real_growth_and_small_samples() -> None:
    assert estimate._p95([19.2]) == 19.2
    assert estimate._p95([0.9, 19.2]) == 19.2
    assert estimate._p95([4.0, 5.0, 6.0]) == 6.0


def test_declared_beats_sibling_proxy(monkeypatch) -> None:
    monkeypatch.setattr(
        estimate,
        "predict_task_resources",
        lambda *a, **k: {"ram_gib": 19.2, "vram_gib": 0.0, "source": estimate.SOURCE_SIBLING},
    )
    out = estimate.estimate_task_resources(None, "p", "t", {"ram_gib": 2})
    assert out["ram_gib"] == 2.0
    assert out["source"] == estimate.SOURCE_DECLARED


def test_own_history_still_raises_declared(monkeypatch) -> None:
    monkeypatch.setattr(
        estimate,
        "predict_task_resources",
        lambda *a, **k: {"ram_gib": 8.0, "vram_gib": 0.0, "source": estimate.SOURCE_HISTORY},
    )
    out = estimate.estimate_task_resources(None, "p", "t", {"ram_gib": 2})
    assert out["ram_gib"] == 8.0


def test_hint_reset_clears_peaks(tmp_path, monkeypatch) -> None:
    from tqdaemon import _cli_task
    from tqdaemon.db import open_db

    db = tmp_path / "q.db"
    conn = open_db(db)
    conn.execute(
        "INSERT INTO runs (project, task_id, status, started_at, peak_ram_gib, peak_vram_gib) VALUES ('p','t','completed','2026-11-14T00:00:00+00:00',19.2,1.0)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(_cli_task, "_db", lambda home: db)
    assert _cli_task.cmd_hint_reset(tmp_path, "p/t") == 0
    conn = open_db(db)
    row = conn.execute("SELECT peak_ram_gib, peak_vram_gib FROM runs").fetchone()
    conn.close()
    assert row["peak_ram_gib"] is None and row["peak_vram_gib"] is None
    assert _cli_task.cmd_hint_reset(tmp_path, "p/missing") == 1


def test_capped_last_peak_limits_wild_own_sample_when_declared() -> None:
    from tqdaemon.scheduler import capped_last_peak

    assert capped_last_peak(19.2, 2.0) == 6.0  # max(2*2, 2+4)
    assert capped_last_peak(5.0, 2.0) == 5.0  # under the cap: untouched
    assert capped_last_peak(19.2, 0.0) == 19.2  # undeclared: raw peak


def test_hint_show_reports_dropped_outlier(tmp_path, monkeypatch, capsys) -> None:
    from tqdaemon import _cli_task
    from tqdaemon.db import open_db

    db = tmp_path / "q.db"
    conn = open_db(db)
    for i, peak in enumerate([0.9, 0.8, 0.7, 19.2]):
        conn.execute(
            "INSERT INTO runs (project, task_id, status, started_at, peak_ram_gib) VALUES (?,?,?,?,?)",
            ("p", "t", "completed", f"2026-11-14T00:00:0{i}+00:00", peak),
        )
    conn.execute(
        "INSERT INTO tasks (project, task_id, status, command, resources, enqueued_at, updated_at) VALUES ('p','t','completed','x','{}','2026-11-14T00:00:00+00:00','2026-11-14T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(_cli_task, "_db", lambda home: db)
    assert _cli_task.cmd_hint_show(tmp_path, "p/t") == 0
    out = capsys.readouterr().out
    assert "ignored outlier: 19.20" in out
    assert "hint 0.90 GiB" in out or "hint 1.00 GiB" in out


def test_why_includes_ram_hint_for_queued_task(tmp_path) -> None:
    from tqdaemon.db import open_db
    from tqdaemon.why import explain_task, format_explanation

    conn = open_db(tmp_path / "q.db")
    conn.execute(
        "INSERT INTO tasks (project, task_id, status, command, resources, enqueued_at, updated_at) "
        "VALUES ('p','t','queued','x','{}','2026-11-14T00:00:00+00:00','2026-11-14T00:00:00+00:00')"
    )
    conn.commit()
    info = explain_task(conn, "p", "t")
    conn.close()
    assert info["ram_hint"]["source"] in {"default", "history", "declared"}
    assert "tq hint reset p/t" in format_explanation(info)
