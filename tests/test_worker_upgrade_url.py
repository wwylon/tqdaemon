from tqdaemon.worker import safe_upgrade_url


def test_accepts_plain_master_path() -> None:
    assert safe_upgrade_url("http://m:8765/", "/api/worker/wheel") == "http://m:8765/api/worker/wheel"


def test_rejects_host_hijack_forms() -> None:
    for bad in ("@evil.example/x", "//evil.example/x", "http://evil.example/x", "/a" + chr(92) + "b", "x/y", ""):
        assert safe_upgrade_url("http://m:8765", bad) is None, bad
