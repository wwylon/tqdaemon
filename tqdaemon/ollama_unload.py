"""T-209: optional Ollama unload when a GPU task is blocked on VRAM by resident models.

Off unless ``ollama_unload_url`` is set (e.g. ``http://127.0.0.1:11434``). When a GPU candidate cannot be
admitted for lack of VRAM and the candidate does not itself use Ollama, every model currently loaded there is
asked to unload (``keep_alive: 0``). Rate limited so a persistently blocked task does not hammer the API.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

_log = logging.getLogger(__name__)
MIN_INTERVAL_SEC = 60.0
_last_unload: dict[str, float] = {}


def _loaded_models(base_url: str, *, allow_remote: bool, timeout: float) -> list[str]:
    from tqdaemon.netutil import probe_get

    status, body = probe_get(base_url.rstrip("/") + "/api/ps", allow_remote=allow_remote, timeout=timeout)
    if status >= 400:
        return []
    return [str(m.get("name") or m.get("model")) for m in (json.loads(body).get("models") or []) if isinstance(m, dict)]


def maybe_unload(
    base_url: str,
    *,
    allow_remote: bool = False,
    timeout: float = 3.0,
    now: float | None = None,
    min_interval: float = MIN_INTERVAL_SEC,
) -> list[str]:
    """Unload all resident models at ``base_url``; returns the names asked to unload ([] when skipped)."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return []
    t = time.monotonic() if now is None else now
    if t - _last_unload.get(base, float("-inf")) < min_interval:
        return []
    _last_unload[base] = t
    from tqdaemon.netutil import probe_post_json

    unloaded: list[str] = []
    try:
        for name in _loaded_models(base, allow_remote=allow_remote, timeout=timeout):
            status, _ = probe_post_json(
                base + "/api/generate",
                {"model": name, "keep_alive": 0},
                allow_remote=allow_remote,
                timeout=timeout,
            )
            if status < 400:
                unloaded.append(name)
    except Exception as exc:  # noqa: BLE001 - an unreachable Ollama must never affect admission
        _log.debug("ollama unload failed: %s", exc)
    return unloaded


def candidate_uses_ollama(requires_services_json: str | None) -> bool:
    """True when the task declares any service entry that looks like Ollama (never unload under it)."""
    from tqdaemon.jsonutil import json_list

    try:
        services: list[Any] = json_list(requires_services_json)
    except (TypeError, ValueError):
        return False
    return any("ollama" in json.dumps(s).lower() for s in services or [])
