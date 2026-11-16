"""`tq worker <sub>` flag parsing after the flagparse migration keeps the old behaviour."""

from pathlib import Path

from tqdaemon import cli


def test_sync_accepts_worker_id_before_or_after_flags(monkeypatch, tmp_path: Path) -> None:
    seen = []
    monkeypatch.setattr(cli, "cmd_worker_sync", lambda home, wid, **k: seen.append((wid, k)) or 0)
    monkeypatch.delenv("TQ_AUTH_TOKEN", raising=False)
    assert cli._cmd_worker(tmp_path, ["sync", "w1", "--master", "http://m"]) == 0
    assert cli._cmd_worker(tmp_path, ["sync", "--master", "http://m", "--token", "t", "w2", "--ignored"]) == 0
    assert seen[0] == ("w1", {"master_url": "http://m", "auth_token": None})
    assert seen[1] == ("w2", {"master_url": "http://m", "auth_token": "t"})
    assert cli._cmd_worker(tmp_path, ["sync", "--master", "http://m"]) == 1  # worker id required


def test_start_falls_back_to_usage_without_master_and_config(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.delenv("TQ_AUTH_TOKEN", raising=False)
    assert cli._cmd_worker(tmp_path, ["--worker-id", "w"]) == 1
    assert "Usage: tq worker --master" in capsys.readouterr().out


def test_start_and_config_master_pass_parsed_values(monkeypatch, tmp_path: Path) -> None:
    started, cfg = [], []
    monkeypatch.setattr(cli, "cmd_worker_start", lambda home, m, w, auth_token=None: started.append((m, w, auth_token)))
    from tqdaemon import _cli_worker

    monkeypatch.setattr(_cli_worker, "cmd_worker_config_master", lambda home, **k: cfg.append(k) or 0)
    monkeypatch.delenv("TQ_AUTH_TOKEN", raising=False)
    assert cli._cmd_worker(tmp_path, ["--master", "http://m", "--worker-id", "w", "--token", "t"]) == 0
    assert started == [("http://m", "w", "t")]
    assert cli._cmd_worker(tmp_path, ["config-master", "--host", "10.0.0.5", "--port", "9000", "--remote-only"]) == 0
    assert cfg == [{"host": "10.0.0.5", "out": None, "remote_only": True, "port": 9000}]
    assert cli._cmd_worker(tmp_path, ["config-master"]) == 1
