"""Scheduler-tick watchdog (audit 2026-11b, Wave 0.1).

The daemon once answered ``tq doctor`` with "pid live but http failed" while the host was overloaded
(nvidia-smi probes timing out for minutes). A stalled tick leaves no trace in daemon.log, so this thread
logs a stack dump of every thread once per stall — evidence for the next diagnosis, never a restart.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
import time
from collections.abc import Callable

_log = logging.getLogger(__name__)


def _default_dump(stalled_sec: float) -> None:
    print(
        f"[queue] WATCHDOG: scheduler tick has not finished for {stalled_sec:.0f}s; dumping all threads",
        file=sys.stderr,
        flush=True,
    )
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)


class TickWatchdog:
    def __init__(
        self,
        *,
        stall_sec: float = 120.0,
        check_sec: float = 15.0,
        dump: Callable[[float], None] = _default_dump,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stall_sec = stall_sec
        self.check_sec = check_sec
        self._dump = dump
        self._clock = clock
        self._last = clock()
        self._dumped = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        """Called by the tick loop each iteration; re-arms the one-shot dump."""
        self._last = self._clock()
        self._dumped = False

    def check(self) -> bool:
        """Dump once when no beat arrived for ``stall_sec``. Returns True when it dumped."""
        stalled = self._clock() - self._last
        if stalled >= self.stall_sec and not self._dumped:
            self._dumped = True
            try:
                self._dump(stalled)
            except Exception:  # noqa: BLE001 - diagnostics must never take the daemon down
                _log.debug("watchdog dump failed", exc_info=True)
            return True
        return False

    def start(self) -> None:
        if self._thread is not None:
            return

        def _loop() -> None:
            while not self._stop.wait(self.check_sec):
                self.check()

        self._thread = threading.Thread(target=_loop, name="tq-tick-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
