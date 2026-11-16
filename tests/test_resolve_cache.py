"""The YAML-cache cleanup must not resolve every foreign directory once per project per sync pass."""

from pathlib import Path

from tqdaemon import yaml_sync


def test_resolved_is_cached_across_calls_and_projects(monkeypatch, tmp_path: Path) -> None:
    yaml_sync._RESOLVE_CACHE.clear()
    calls: list[str] = []
    real = Path.resolve

    def counting(self, *a, **k):
        calls.append(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "resolve", counting)
    d = tmp_path / "q"
    d.mkdir()
    qres = Path(yaml_sync._resolved(str(d)))
    for _ in range(30):  # 30 projects each cleaning the same foreign cache keys
        memo: dict[str, bool] = {}
        for i in range(50):
            yaml_sync._in_queue_dir(str(d / f"t{i}.yaml"), str(tmp_path / "other"), qres, memo)
    assert calls.count(str(d)) == 1  # one resolve for 1500 checks


def test_ttl_expiry_resolves_again(monkeypatch, tmp_path: Path) -> None:
    yaml_sync._RESOLVE_CACHE.clear()
    first = yaml_sync._resolved(str(tmp_path))
    monkeypatch.setattr(yaml_sync, "_RESOLVE_TTL_SEC", -1.0)
    assert yaml_sync._resolved(str(tmp_path)) == first
