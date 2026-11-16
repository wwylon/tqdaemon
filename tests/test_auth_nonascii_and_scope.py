"""S1/S2 (audit 2026-11b): non-ASCII tokens must be a clean no-match; write tokens are not admin."""

from tqdaemon import auth
from tqdaemon.operator_features import resolve_access_with_projects, scope_allows


def test_check_token_non_ascii_is_false_not_typeerror() -> None:
    assert auth.check_token({"auth_token": "secret"}, "sécret") is False
    assert auth.check_token({"auth_token": "sécret"}, "sécret") is True


def test_resolve_access_non_ascii_provided_is_unauthorized() -> None:
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert resolve_access_with_projects({"auth_token": "secret"}, conn, "é")[0] is None


def test_write_scope_cannot_relocate_or_release() -> None:
    assert not scope_allows("write", "POST", "/api/project/relocate/p")
    assert not scope_allows("write", "POST", "/api/project/release/p")
    assert scope_allows("admin", "POST", "/api/project/relocate/p")
    assert scope_allows("write", "POST", "/api/stop/p/t")


def test_require_token_local_disables_loopback_bypass() -> None:
    from tqdaemon.web.handler import local_bypass_allowed

    hdr = {"Host": "127.0.0.1:8765"}
    assert local_bypass_allowed({}, "127.0.0.1", hdr) is True
    assert local_bypass_allowed({"require_token_local": 1}, "127.0.0.1", hdr) is False
    assert local_bypass_allowed({}, "192.168.1.5", hdr) is False
