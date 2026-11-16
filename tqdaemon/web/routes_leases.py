"""HTTP routes for ad-hoc GPU leases (T-208): acquire, poll, release, list."""

from __future__ import annotations

from typing import Any


class LeaseRoutes:
    """Mixin for ``Handler``; relies on ``_read_json``, ``_wconn``, ``_rconn``, ``_json`` and ``_audit``."""

    def _post_lease(self) -> None:
        """POST /api/leases — body ``{vram_gib, ttl_sec?, priority?, holder?, pid?}`` -> 202 ``{id}`` (waiting)."""
        from tqdaemon.leases import DEFAULT_TTL_SEC, create_lease

        data: Any = self._read_json()  # type: ignore[attr-defined]
        if data is None:
            return
        try:
            vram = float(data.get("vram_gib"))
            ttl = int(data.get("ttl_sec", DEFAULT_TTL_SEC))
            prio = int(data.get("priority", 0))
            pid = int(data["pid"]) if data.get("pid") is not None else None
        except (TypeError, ValueError):
            self._json(
                {"ok": False, "error": "vram_gib (number), ttl_sec/priority/pid (integers) required"}, status=400
            )  # type: ignore[attr-defined]
            return
        try:
            lease_id = create_lease(
                self._wconn(),  # type: ignore[attr-defined]
                vram_gib=vram,
                ttl_sec=ttl,
                priority=prio,
                holder=str(data.get("holder") or ""),
                pid=pid,
            )
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, status=400)  # type: ignore[attr-defined]
            return
        self._audit("gpu_lease", detail=f"lease {lease_id}: {vram:g} GiB ttl {ttl}s")  # type: ignore[attr-defined]
        self._json({"ok": True, "id": lease_id, "status": "waiting"}, status=202)  # type: ignore[attr-defined]

    def _post_lease_release(self, raw_id: str) -> None:
        from tqdaemon.leases import release_lease

        try:
            lease_id = int(raw_id)
        except ValueError:
            self._json({"ok": False, "error": "bad lease id"}, status=400)  # type: ignore[attr-defined]
            return
        ok = release_lease(self._wconn(), lease_id)  # type: ignore[attr-defined]
        if ok:
            self._audit("gpu_lease_release", detail=f"lease {lease_id}")  # type: ignore[attr-defined]
        self._json({"ok": ok, "id": lease_id}, status=200 if ok else 404)  # type: ignore[attr-defined]

    def _get_lease(self, raw_id: str) -> None:
        from tqdaemon.leases import get_lease

        try:
            lease = get_lease(self._rconn(), int(raw_id))  # type: ignore[attr-defined]
        except ValueError:
            lease = None
        if lease is None:
            self._json({"ok": False, "error": "unknown lease"}, status=404)  # type: ignore[attr-defined]
            return
        self._json({"ok": True, **lease})  # type: ignore[attr-defined]

    def _get_leases(self) -> None:
        from tqdaemon.leases import list_leases

        self._json({"ok": True, "leases": list_leases(self._rconn())})  # type: ignore[attr-defined]
