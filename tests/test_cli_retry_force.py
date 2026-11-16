"""tq retry --force reaches the server as ?force=1 (omnicard3 bug report)."""

from pathlib import Path

from tqdaemon import _cli_task, cli


def test_retry_force_flag_sets_query(monkeypatch, tmp_path: Path) -> None:
    from tqdaemon import master_client

    seen: dict = {}

    def fake_via_master(home, method, path, body=None, **kw):
        seen["path"], seen["body"] = path, body
        return {"ok": True, "requeued": ["t"]}

    class _Db:
        def close(self) -> None: ...

    monkeypatch.setattr(master_client, "via_master", fake_via_master)
    import tqdaemon.db as dbm

    monkeypatch.setattr(dbm, "open_db", lambda p: _Db())
    monkeypatch.setattr(dbm, "get_task", lambda *a, **k: None)
    monkeypatch.setattr(dbm, "collect_downstream_task_ids", lambda *a, **k: ["d"])
    monkeypatch.setattr(_cli_task, "_print_retry_wait_hint", lambda *a, **k: None)
    monkeypatch.setattr(_cli_task, "_print_retry_report", lambda *a, **k: None)
    assert cli._cmd_retry(tmp_path, ["p/t", "--downstream", "--force"]) == 0
    assert seen["path"].endswith("/retry?force=1")
    assert seen["body"] == {"downstream": True}
    assert cli._cmd_retry(tmp_path, ["p/t"]) == 0
    assert seen["path"].endswith("/retry")
