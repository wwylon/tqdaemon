"""``tq lease`` and ``tq gpu run`` (T-208): ad-hoc GPU leases admitted by the scheduler."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tqdaemon.flagparse import FlagError, parse_flags

LEASE_USAGE = (
    "Usage: tq lease acquire --vram GiB [--ttl SEC] [--priority N] [--wait SEC] | tq lease release <id> | tq lease list"
)
GPU_RUN_USAGE = "Usage: tq gpu run --vram GiB [--ttl SEC] [--priority N] [--wait SEC] -- <command> [args...]"


def _master(home: Path, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any] | None:
    from tqdaemon.master_client import via_master

    return via_master(home, method, path, body=body, timeout=15.0)


def _request_lease(home: Path, opts: dict[str, object]) -> int | None:
    body = {
        "vram_gib": float(str(opts["vram"])),
        "ttl_sec": int(str(opts["ttl"] or 900)),
        "priority": int(str(opts["priority"] or 0)),
        "holder": os.environ.get("TQ_OPERATOR", "") or f"pid {os.getpid()}",
        "pid": os.getpid(),
    }
    resp = _master(home, "POST", "/api/leases", body)
    if resp is None:
        print("daemon unreachable: GPU leases need a running tq daemon", file=sys.stderr)
        return None
    if not resp.get("ok"):
        print(f"lease refused: {resp.get('error', resp)}", file=sys.stderr)
        return None
    return int(resp["id"])


def _wait_active(home: Path, lease_id: int, wait_sec: float) -> bool:
    deadline = time.monotonic() + wait_sec
    announced = False
    while time.monotonic() < deadline:
        lease = _master(home, "GET", f"/api/leases/{lease_id}")
        if lease is None:
            print("daemon stopped answering while waiting for the lease", file=sys.stderr)
            return False
        status = lease.get("status")
        if status == "active":
            return True
        if status in ("released", "expired"):
            print(f"lease {lease_id} ended while waiting ({status})", file=sys.stderr)
            return False
        if not announced:
            print(f"lease {lease_id}: waiting for {lease.get('vram_gib')} GiB of VRAM ...", file=sys.stderr, flush=True)
            announced = True
        time.sleep(1.0)
    print(f"lease {lease_id}: not granted within {wait_sec:.0f}s", file=sys.stderr)
    return False


def _parse(rest: list[str], usage: str) -> tuple[dict[str, object], list[str]] | None:
    try:
        opts, pos = parse_flags(rest, values=["vram", "ttl", "priority", "wait"])
    except FlagError as exc:
        print(f"Error: {exc}\n{usage}")
        return None
    try:
        if not opts["vram"] or float(str(opts["vram"])) <= 0:
            raise ValueError
        for k in ("ttl", "priority", "wait"):
            if opts[k] is not None:
                float(str(opts[k]))
    except ValueError:
        print(usage)
        return None
    return opts, pos


def cmd_lease(home: Path, rest: list[str]) -> int:
    if not rest:
        print(LEASE_USAGE)
        return 1
    sub, args = rest[0], rest[1:]
    if sub == "list":
        resp = _master(home, "GET", "/api/leases")
        if resp is None:
            print("daemon unreachable", file=sys.stderr)
            return 1
        leases = resp.get("leases") or []
        if not leases:
            print("No active or waiting GPU leases.")
            return 0
        print(f"{'ID':>4s}  {'STATUS':<8s}  {'VRAM':>6s}  {'PRIO':>4s}  HOLDER")
        for lease in leases:
            print(
                f"{lease['id']:>4d}  {lease['status']:<8s}  {lease['vram_gib']:>6.1f}  {lease['priority']:>4d}  {lease['holder']}"
            )
        return 0
    if sub == "release":
        if len(args) != 1 or not args[0].isdigit():
            print(LEASE_USAGE)
            return 1
        resp = _master(home, "POST", f"/api/leases/{args[0]}/release")
        if resp is None or not resp.get("ok"):
            print(f"lease {args[0]} not found or already ended", file=sys.stderr)
            return 1
        print(f"lease {args[0]} released")
        return 0
    if sub == "acquire":
        parsed = _parse(args, LEASE_USAGE)
        if parsed is None:
            return 1
        opts, _ = parsed
        lease_id = _request_lease(home, opts)
        if lease_id is None:
            return 1
        if not _wait_active(home, lease_id, float(str(opts["wait"] or 3600))):
            _master(home, "POST", f"/api/leases/{lease_id}/release")
            return 1
        print(lease_id)  # stdout is just the id so scripts can `ID=$(tq lease acquire ...)`
        return 0
    print(LEASE_USAGE)
    return 1


def cmd_gpu(home: Path, rest: list[str]) -> int:
    if not rest or rest[0] != "run":
        print(GPU_RUN_USAGE)
        return 1
    args = rest[1:]
    if "--" not in args:
        print(GPU_RUN_USAGE)
        return 1
    cut = args.index("--")
    flags, command = args[:cut], args[cut + 1 :]
    parsed = _parse(flags, GPU_RUN_USAGE)
    if parsed is None or not command:
        if parsed is not None:
            print(GPU_RUN_USAGE)
        return 1
    opts, _ = parsed
    lease_id = _request_lease(home, opts)
    if lease_id is None:
        return 1
    try:
        if not _wait_active(home, lease_id, float(str(opts["wait"] or 3600))):
            return 1
        env = dict(os.environ, TQ_GPU_LEASE_ID=str(lease_id))
        return subprocess.run(command, env=env).returncode  # noqa: S603 - the user's own command
    finally:
        _master(home, "POST", f"/api/leases/{lease_id}/release")
