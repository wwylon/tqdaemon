"""Daemon lifecycle as the UI sees it: running, draining (no new starts), or in its shutdown stage.

``begin_shutdown`` is called from the signal handler; ``lifecycle_info`` feeds ``/api/status`` so the web
interface can say "shutting down" instead of just going dark, and whether running tasks will be handed over
(``tq restart`` / ``tq stop-daemon --keep-runs``) or stopped.
"""

from __future__ import annotations

import time
from typing import Any

_state: dict[str, Any] = {"since": None, "keep_runs": False}


def begin_shutdown(*, keep_runs: bool) -> None:
    if _state["since"] is None:
        _state["since"] = time.time()
    _state["keep_runs"] = bool(keep_runs)


def reset() -> None:
    """Test seam."""
    _state["since"] = None
    _state["keep_runs"] = False


def lifecycle_info(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    since = _state["since"]
    return {
        "shutting_down": since is not None,
        "shutdown_since": since,
        "keep_runs": bool(_state["keep_runs"]),
        "draining": bool(int((cfg or {}).get("admission_drain", 0) or 0)),
    }
