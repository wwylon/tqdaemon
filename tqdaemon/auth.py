"""Bearer / X-TQ-Token auth helpers."""

from __future__ import annotations

import hmac
from typing import Any


def secure_equals(a: str, b: str) -> bool:
    """Constant-time string compare that never raises (non-ASCII ``str`` breaks compare_digest)."""
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def token_required(cfg: dict[str, Any]) -> bool:
    tok = cfg.get("auth_token") or ""
    return bool(str(tok).strip())


def configured_token(cfg: dict[str, Any]) -> str:
    return str(cfg.get("auth_token") or "").strip()


def extract_request_token(headers: dict[str, str], query_token: str | None = None) -> str | None:
    """Pull token from Authorization Bearer, X-TQ-Token, or query."""
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    xt = headers.get("X-TQ-Token") or headers.get("x-tq-token")
    if xt:
        return xt.strip()
    if query_token:
        return query_token.strip()
    return None


def client_auth_headers(
    token: str | None = None,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Outbound client headers: Bearer + X-TQ-Token when a token is set."""
    h: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if extra:
        h.update(extra)
    tok = (token or "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
        h["X-TQ-Token"] = tok
    return h


def check_token(cfg: dict[str, Any], provided: str | None) -> bool:
    """True if auth not required or provided matches."""
    expected = configured_token(cfg)
    if not expected:
        return True
    if not provided:
        return False
    return secure_equals(provided, expected)


def sign_upgrade(token: str, version: str, sha256: str) -> str:
    """S6: HMAC over (version, wheel sha256) keyed by the shared auth token.

    A worker only auto-upgrades to a wheel the master vouches for with the token, so someone who can
    answer ``GET /api/worker/version`` on the wire (plain HTTP LAN) but does not hold the token cannot
    push code to workers.
    """
    msg = f"tqdaemon-upgrade:{version}:{sha256}".encode()
    return hmac.new(token.encode("utf-8"), msg, "sha256").hexdigest()


def verify_upgrade(token: str, version: str, sha256: str, sig: object) -> bool:
    if not token or not isinstance(sig, str) or not sig:
        return False
    return secure_equals(sign_upgrade(token, version, sha256), sig)


def finalize_token_matches(stored: Any, given: Any) -> bool:
    """Constant-time check of a per-claim finalize token; a missing token on either side never matches."""
    if not stored or not given:
        return False
    return hmac.compare_digest(str(stored).encode(), str(given).encode())
