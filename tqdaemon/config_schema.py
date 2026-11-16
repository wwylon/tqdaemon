"""Settings dialog schema: section + one-line help for every ``VALID_CONFIG_KEYS`` entry.

``GET /api/config/schema`` serves this so the web Settings tabs render from one table; a key
missing here falls into "advanced" (and ``tests/test_config_schema.py`` fails, so add it).
Limit caps (max_running_tasks, max_cpu_cores, max_ram_gib, gpu_vram_limit_gib) live on the
root node of the limits tree, not in a settings section.
"""

from __future__ import annotations

from typing import Any

SECTIONS = ("scheduling", "memory", "stopping", "notifications", "server", "retention", "advanced")

# key -> (section, help)
SCHEMA: dict[str, tuple[str, str]] = {
    # --- limits tree root (edited in the Limits tab) ---
    "max_running_tasks": ("limits", "Max tasks running at once across all projects (0 = no cap)."),
    "max_cpu_cores": ("limits", "Max declared CPU cores of running tasks in total (0 = no cap)."),
    "max_ram_gib": ("limits", "Max declared RAM of running tasks in total, GiB (0 = no cap)."),
    "gpu_vram_limit_gib": ("limits", "Max declared VRAM of running tasks in total, GiB (0 = no cap)."),
    # --- scheduling ---
    "cpu_charge_measured": (
        "scheduling",
        "Charge running tasks max(declared cpu_cores, measured cores) when admitting CPU tasks.",
    ),
    "require_token_local": (
        "server",
        "Require the API token from loopback clients too (default: local processes skip it).",
    ),
    "ollama_unload_url": (
        "scheduling",
        "Ollama base URL (e.g. http://127.0.0.1:11434): unload its resident models when a GPU task is blocked on VRAM (empty = off).",
    ),
    "admission_drain": (
        "scheduling",
        "Drain mode: running tasks finish, nothing new starts (also for remote workers). `tq drain on|off`.",
    ),
    "gpu_first": ("scheduling", "Start equal-priority GPU tasks before CPU tasks while the GPU is idle."),
    "reservation_max_ticks": ("scheduling", "Ticks RAM/VRAM is held for the top waiting task (0 = no holds)."),
    "admit_max_per_tick": ("scheduling", "Max tasks started per scheduler tick (0 = no cap)."),
    "admit_candidate_window": ("scheduling", "How many queued tasks are considered per tick."),
    "admit_settle_sec": ("scheduling", "Pause between starts in one tick so memory readings settle."),
    "settle_window_sec": ("scheduling", "Seconds a new task's estimate is held until its usage is measured."),
    "max_concurrent_unknown": ("scheduling", "Max running tasks with no RAM estimate yet (0 = no cap)."),
    "default_task_ram_gib": ("scheduling", "RAM assumed for a task with no declaration or history, GiB."),
    "resource_predict_runs": ("scheduling", "Past runs used to predict a task's RAM/VRAM."),
    "max_queued_tasks_per_project": ("scheduling", "Max queued tasks per project; extra YAMLs are skipped."),
    "fairshare_halflife_days": ("scheduling", "Half-life of past usage in fair-share ordering, days."),
    "fairshare_weight": ("scheduling", "How much fair share moves priority (0 = off, 1 = strong)."),
    "scavenger_preempt_cooldown_sec": ("scheduling", "Min seconds between scavenger preemptions."),
    "admission_shadow_mode": ("scheduling", "Log what resource gates would block but admit anyway (testing)."),
    "max_concurrent_tasks": ("scheduling", "Legacy cap overlapping max_running_tasks; keep 0."),
    "placement_mode": ("scheduling", "Remote worker placement: binpack or first_fit."),
    "placement_age_sec": ("scheduling", "Queue age after which placement prefers any free worker, s."),
    "placement_shadow_mode": ("scheduling", "Log best-fit placement decisions without applying them."),
    "placement_prefer_last_worker": ("scheduling", "Prefer the worker that ran the task last."),
    "submit_key_ttl_sec": ("scheduling", "How long idempotent submit keys are remembered, s."),
    # --- memory & safety ---
    "cpu_limit_pct": ("memory", "Soft CPU limit, % of cores; above it no new task starts."),
    "cpu_measure_ema_sec": ("memory", "Smoothing window for live CPU load, s."),
    "ram_limit_pct": ("memory", "Soft RAM limit, % of installed; new tasks must fit under it."),
    "ram_hard_limit_pct": ("memory", "Hard RAM limit, %; above it running tasks are stopped."),
    "gpu_limit_pct": ("memory", "Soft VRAM limit, % of each GPU."),
    "vram_hard_limit_pct": ("memory", "Hard VRAM limit, %; above it GPU tasks are stopped (0 = off)."),
    "limit_decrease_policy": ("memory", "When a cap is lowered below what runs: ignore, evict or block."),
    "stop_max_per_tick": ("memory", "Max tasks stopped per tick under memory pressure."),
    "recovery_cooldown_sec": ("memory", "No new starts for this long after a pressure stop, s."),
    "ram_settle_sec": ("memory", "Age after which RSS growth counts as a leak, s."),
    "ram_leak_warn_gib_per_hour": ("memory", "Warn when a task's RAM grows faster than this, GiB/h."),
    "gpu_util_warn_pct": ("memory", "Warn when a GPU task's utilisation stays below this, %."),
    "hung_task_k": ("memory", "Flag a task as hung after k× its usual run time (0 = off)."),
    # --- stopping & restart ---
    "graceful_stop_sec": ("stopping", "Grace for a task to checkpoint before it is killed, s."),
    "suspend_grace_sec": ("stopping", "Grace for a cooperative suspend before stopping, s."),
    "frozen_threshold_sec": ("stopping", "No output for this long marks a task frozen, s."),
    "frozen_action": ("stopping", "What to do with a frozen task: log, stop or restart."),
    "adopt_on_restart": ("stopping", "Tasks keep running across tq restart (new tasks only)."),
    "prevent_sleep": ("stopping", "Keep the machine awake while tasks run."),
    "clock_jump_threshold_sec": ("stopping", "Clock jump that counts as sleep/resume, s (0 = auto)."),
    "clock_jump_max_requeue": ("stopping", "Max tasks re-queued after one clock jump."),
    "clock_jump_requeue_window_sec": ("stopping", "Window for counting clock-jump re-queues, s."),
    # --- notifications ---
    "notify_url": ("notifications", "Webhook URL called when a task finishes."),
    "notify_default": ("notifications", "Write every finished task to notify.jsonl."),
    "notify_on_attention": ("notifications", "Notify when a task needs attention."),
    "notify_allow_remote": ("notifications", "Allow webhook URLs outside localhost."),
    "toast_notifications": ("notifications", "Desktop toasts for finished tasks."),
    # --- server ---
    "scheduler_interval_sec": ("server", "Seconds between scheduler ticks."),
    "heartbeat_sec": ("server", "Worker heartbeat interval, s."),
    "web_port": ("server", "Web UI / API port (restart to apply)."),
    "bind_host": ("server", "Address the web server listens on (restart to apply)."),
    "auth_token": ("server", "API token required for writes (empty = none)."),
    "service_probe_background": ("server", "Probe required services in the background."),
    "usage_tracking": ("server", "Count agent/API usage (see docs/security.md)."),
    # --- retention ---
    "retention_enabled": ("retention", "Prune old logs and runs."),
    "retention_log_days": ("retention", "Delete finished task logs older than this, days."),
    "retention_run_days": ("retention", "Delete run history older than this, days."),
    "retention_projects": ("retention", "Comma-separated projects retention applies to (empty = all)."),
    "retention_log_max_bytes": ("retention", "Truncate finished logs to this many bytes (0 = off)."),
    "retention_log_gzip_days": ("retention", "Gzip finished logs older than this, days (0 = off)."),
    "retention_usage_days": ("retention", "Keep API usage counters this many days."),
    "retention_audit_days": ("retention", "Keep the audit log this many days."),
}


def config_schema() -> dict[str, Any]:
    """``GET /api/config/schema``: ``{sections, keys: [{key, section, help, type, min, max, enum, default}]}``."""
    from tqdaemon.config import _ENUM_CONFIG, VALID_CONFIG_KEYS
    from tqdaemon.migrations import _DEFAULT_CONFIG

    keys = []
    for key, (typ, lo, hi) in VALID_CONFIG_KEYS.items():
        section, help_ = SCHEMA.get(key, ("advanced", ""))
        enum = sorted(_ENUM_CONFIG[key]) if key in _ENUM_CONFIG else None
        boolean = typ is int and lo == 0 and hi == 1
        keys.append(
            {
                "key": key,
                "section": section,
                "help": help_,
                "type": "bool" if boolean else ("enum" if enum else typ.__name__),
                "min": lo if typ is not str else None,
                "max": hi if typ is not str else None,
                "enum": enum,
                "default": _DEFAULT_CONFIG.get(key),
                "secret": key == "auth_token",
            }
        )
    return {"sections": list(SECTIONS), "keys": keys}
