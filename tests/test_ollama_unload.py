"""T-209: opt-in Ollama unload for GPU tasks blocked on VRAM."""

import json

from tqdaemon import netutil, ollama_unload


def _stub(monkeypatch, posts: list, models: list[str]) -> None:
    ollama_unload._last_unload.clear()
    monkeypatch.setattr(
        netutil, "probe_get", lambda url, **k: (200, json.dumps({"models": [{"name": m} for m in models]}).encode())
    )
    monkeypatch.setattr(
        netutil, "probe_post_json", lambda url, payload, **k: posts.append((url, payload)) or (200, b"")
    )


def test_unloads_every_resident_model_with_keep_alive_zero(monkeypatch) -> None:
    posts: list = []
    _stub(monkeypatch, posts, ["aya-expanse:8b", "nomic-embed"])
    assert ollama_unload.maybe_unload("http://127.0.0.1:11434/") == ["aya-expanse:8b", "nomic-embed"]
    assert posts[0] == ("http://127.0.0.1:11434/api/generate", {"model": "aya-expanse:8b", "keep_alive": 0})


def test_rate_limited_and_off_when_url_empty(monkeypatch) -> None:
    posts: list = []
    _stub(monkeypatch, posts, ["m"])
    assert ollama_unload.maybe_unload("", now=0.0) == []
    assert ollama_unload.maybe_unload("http://127.0.0.1:11434", now=100.0) == ["m"]
    assert ollama_unload.maybe_unload("http://127.0.0.1:11434", now=120.0) == []  # within 60 s
    assert ollama_unload.maybe_unload("http://127.0.0.1:11434", now=200.0) == ["m"]


def test_unreachable_ollama_is_swallowed(monkeypatch) -> None:
    ollama_unload._last_unload.clear()

    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(netutil, "probe_get", boom)
    assert ollama_unload.maybe_unload("http://127.0.0.1:11434") == []


def test_candidate_using_ollama_is_detected() -> None:
    assert ollama_unload.candidate_uses_ollama('[{"name": "ollama", "health_url": "http://127.0.0.1:11434"}]')
    assert not ollama_unload.candidate_uses_ollama("[]")
    assert not ollama_unload.candidate_uses_ollama(None)


def test_remote_ollama_blocked_without_allow_remote(monkeypatch) -> None:
    ollama_unload._last_unload.clear()
    assert ollama_unload.maybe_unload("http://192.168.1.5:11434") == []  # SSRF guard: nothing fetched or posted


def _blocked_gpu_task(tmp_path, cfg_extra: dict):
    """A queued 5 GiB GPU task on a 6 GiB card with 3 GiB already in use (resident models)."""
    from datetime import UTC, datetime

    from tqdaemon.config import Project
    from tqdaemon.db import get_task, open_db
    from tqdaemon.scheduler import _local_admit_queued

    conn = open_db(tmp_path / "q.db")
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO tasks (project, task_id, command, status, enqueued_at, updated_at, enabled, resources) "
        "VALUES ('p','g1','true','queued',?,?,1,?)",
        (now, now, json.dumps({"gpu_vram_gib": 5.0, "ram_gib": 1.0})),
    )
    conn.commit()
    system = {
        "cpu_total_cores": 8,
        "ram_total_gib": 64.0,
        "gpu_vram_total_gib": 6.0,
        "gpus": [{"index": 0, "name": "g", "vram_gib": 6.0, "vram_used_gib": 3.0}],
    }
    cfg = {"cpu_limit_pct": 100, "gpu_limit_pct": 100, "ram_limit_pct": 80, "ram_hard_limit_pct": 95, **cfg_extra}
    projects = {"p": Project(name="p", root=tmp_path, queue_dir=tmp_path / "q")}
    _local_admit_queued(conn, tmp_path, projects, {}, system, cfg, 30, False)
    return get_task(conn, "p", "g1")


def test_gpu_blocked_task_triggers_unload_only_when_configured(monkeypatch, tmp_path) -> None:
    asked: list[str] = []
    monkeypatch.setattr(ollama_unload, "maybe_unload", lambda url, **k: asked.append(url) or ["aya"])
    (tmp_path / "off").mkdir()
    row = _blocked_gpu_task(tmp_path / "off", {})
    assert row["status"] == "queued" and asked == []  # default: never touches Ollama
    (tmp_path / "on").mkdir()
    row = _blocked_gpu_task(tmp_path / "on", {"ollama_unload_url": "http://127.0.0.1:11434"})
    assert row["status"] == "queued" and asked == ["http://127.0.0.1:11434"]
