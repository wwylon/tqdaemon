# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses [Semantic Versioning](https://semver.org/).

### Fixed

- `tq archive` no longer archives a finished task that an unfinished task depends on (the dependent would be blocked forever as "not found"); output reports `kept_as_dependency`.
- `tq archive` / archive age now uses `finished_at`; `updated_at` is rewritten by every sync, so `--older-than` matched nothing.
- Scheduler tick could take minutes: YAML cache cleanup resolved every cached path once per project.

## [Unreleased]

### Added

- **GPU leases** for interactive GPU work: `tq gpu run --vram 3 -- python demo.py` waits until the scheduler has the VRAM free, runs the command and releases the lease; `tq lease acquire|release|list` and `/api/leases` for scripts. Leases count like a running task's declared `gpu_vram_gib` and end on release, TTL or when the holder process dies; `tq capacity` lists them.
- **Opt-in Ollama unload** (`ollama_unload_url`): a GPU task blocked on VRAM asks resident Ollama models to unload (`keep_alive: 0`).
- **`tq hint show` / `tq hint reset`** explain and clear a task's RAM hint; the Why? panel and `tq why` show where it comes from. Outlier peaks (more than 3x the next one) no longer set the hint, and a declared `ram_gib` beats sibling/group history.
- **`require_token_local`** makes loopback clients present the API token too; **`cpu_charge_measured`** charges running tasks their measured cores when admitting new ones.
- **`tq archive --task-glob PATTERN`** (and an id-pattern field in the Archive dialog); a bulk archive now runs as a background job (`GET /api/jobs/<id>`) so it cannot time out.
- `web.bat` starts the daemon (if needed) and opens the dashboard; a scheduler-tick watchdog dumps all thread stacks if a tick stalls.
- **`tq drain on|off|status`** (config `admission_drain`): finish what runs, start nothing new, also for remote workers; the web UI shows a **Drain mode** banner. **`tq stop-daemon --keep-runs`** leaves adoptable runs running. While the daemon shuts down the web UI now shows a **shutting down** banner (tasks handed over to the next daemon, or being stopped) instead of going dark.
- `docs/agents/queue-rules.md`: when to enqueue, how to wait, what not to do.
- **`tq project rename <old> <new>`** (`--dry-run`, `--no-alias`; web: project page **Rename…**; `POST /api/project/rename/<old>`) renames a project id with its history: tasks, runs, usage, limits/colour/freeze settings, scoped tokens and workers, `depends_on` references in other projects, the `projects.yaml` key, the log folder and the archive file move in one step, with backups and an undo (rename back). The old name stays an alias (`tq project alias list|rm`) so old YAMLs, CLI keys, MCP and API calls keep working. Admin only; refused while a task of the project runs. Schema 37 (`project_aliases`).

### Security

- A write-scoped API token can no longer relocate or release a project (admin only). A non-ASCII token header is a clean 401 instead of a server error.
- Worker auto-upgrade only installs a wheel the master signed (HMAC with the shared token) and only from a plain path on the master; `tq export` blanks the stored `auth_token` unless `--with-token`.
- API tokens in the query string log a one-time deprecation warning. Ruff security rules (S104/S105/S202/S324/S606/S608/S310/S603/S607) are enforced.

### Changed

- GPU-looking keys under `resources:` (`gpu`, `vram`, ...) are rejected instead of silently ignored; other unknown keys warn.
- A running task's declared VRAM is held as `max(live, declared)`, not live + declared (Windows hides per-process VRAM), so two declared-5 GiB tasks no longer share a 6 GiB card.
- Schema version 36 (new `gpu_leases` table).

### Fixed

- Docs page: "Could not load this guide" for every guide when the sidebar had grouped guides.
- `tq retry --force` is parsed (the ownership hint told you to pass it, but it was treated as a task id).
- `tq remove` no longer reports "currently running" when the task was already gone.
- A task's own last RAM peak can no longer block it for hours when it declares `ram_gib`.

### Added (earlier, unreleased)

- **Timeline** lists queued tasks that get no bar, with the reason (paused/frozen project, dead dependency, unmet dependency, after the window); `GET /api/timeline` returns `not_shown`.
- **Archive… button** on the project page: preview how many finished tasks older than N days (chosen statuses) would go, then archive them (same as `tq archive`: JSONL export, YAML moved to `archive/`, rows dropped).
- **Settings dialog** replaces "Resource limits": tabs (Limits, Scheduling, Memory & safety, Stopping & restart, Notifications, Server, Retention, All settings), every change staged with an unsaved-changes count and applied by one Save. Every config key now has a UI (the "All settings" tab is generated from the server's key list, `GET /api/config/schema`).
- **One limits tree** (Settings → Limits, `tq limits`, `GET /api/limits/tree`, `POST /api/limits/node`): all > sets > projects, the same caps on every node, each the total of what runs under it; the form shows the effective ancestor cap and live usage, and can create, delete and re-parent sets. Project page has a **⚖ Limits…** shortcut — per-project max tasks is now one click away.
- **Hard stop**: `tq stop <task> --now`, drawer **Kill now**, project "Kill all now", `?hard=1` on the stop API, MCP `stop_task hard=true` — kills the process tree immediately instead of waiting for a checkpoint (`tq.stop.killed`); works on remote workers too.

- **Start now** (task drawer button, `tq start-now <project/task_id>`, `POST /api/start-now/<project>/<task>`): a task you start by hand goes first in line and ignores RAM/VRAM the scheduler is holding for other waiting tasks. Unlike Force it never overcommits: it still needs real free memory and respects dependencies, locks and limits.

## [0.9.0] - 2026-11-12

### Changed

- **Project set limits are subtree totals.** `bots: max_tasks=4` now means at most 4 running tasks across every project under `bots/` (also CPU, RAM, VRAM and GPU-hours/day). Previously each member inherited the value as its own cap. Only `min_tasks` is still inherited per project. The root ("all") is the global config. Over-cap sets have their newest local runs stopped, as with per-project caps.
- `tq set rename` moves the set's limits (and those of its descendants) along with the projects.

### Added

- **GPU memory by program** (Resources → hardware): every process holding VRAM per GPU — tqdaemon tasks marked, other programs highlighted, unattributed memory shown — with a hint to close them. On Windows, where the driver reports no per-process memory, the values come from the "GPU Process Memory" performance counters (the Task Manager source).
- **GPU first** (`gpu_first`, default on): equal-priority GPU tasks start before CPU tasks while the GPU is idle.
- Queue page: project chips are coloured by the project's worst live state (red failed, orange interrupted, blue running, yellow waiting, green completed); group and tag chips are hidden while "all" projects are shown.
- Header project picker lists the set tree (all > bots > bridge2 …); set chips show running/cap; Config → project sets can add and remove tree nodes.
- Explicit set nodes: `tq set node add|rm <path>` and `POST /api/project-sets/node`, so a node (e.g. `bots`) can exist and carry limits before any project is assigned. `GET /api/project-sets` returns per-node `limits`, `running` and `explicit`.

### Removed

- The transitional `taskqueue` import shim (private checkout only). Use `tqdaemon`.

### Fixed

- Stopping a task on Windows no longer sends Ctrl+C to every process on the daemon's console (it could interrupt a foreground `tq` or test run); a graceful Ctrl+Break now goes only to a child that leads its own process group.
- Daemon shutdown no longer marks a task it killed as completed (a task trapping SIGTERM and exiting 0 used to release its dependents).
- An older tqdaemon refuses to open a database with a newer schema instead of deleting the settings it does not know.
- `/api/yield-master` now actually hands the lease to a standby; HTTP-triggered relocate/sync no longer share the scheduler's DB connection; the project registry is never briefly empty during reloads.
- Log streaming no longer re-sends lines when the log grows during a read, and recovers after truncation.
- Admission tolerates `null` resource fields; remote wall-clock caps use monotonic time; project limit changes apply immediately.

## [0.8.0] - 2026-11-11

### Changed

- **Renamed the project from `taskqueue` to `tqdaemon`** (PyPI distribution `tqdaemon`; `taskqueue`, `tq` and `task-queue` were taken). The `tq` command is unchanged; the Python package is now `tqdaemon`.
- New names: `TQDAEMON_HOME` (env), `~/.tqdaemon` (state home), `.tqdaemon/project.yaml` (per-project config), MCP server name `tqdaemon`.
- Migration: the old env var `TASKQUEUE_HOME`, an existing `~/.taskqueue` home, `.taskqueue/project.yaml` and runs started under the old wrapper module are still recognised. To migrate by hand: stop the daemon, rename `~/.taskqueue` to `~/.tqdaemon`, reinstall (`pip install -U tqdaemon`), `tq start`.

### Added

- Automated demo GIF (`scripts/record_demo.py`), issue/PR templates and Dependabot config; PyPI publishing wired into the house publish flow.

## [0.7.6] - 2026-11-08

### Added

- Slim stranger-facing README (EN/PL/ZH/AR) with hero image, agent CTA, and transparency note (no product phone-home).
- Marketing GitHub Pages tree under `site/` (landing, sitemap, robots); CLI encyclopedia moved to `docs/operators/cli.md`.
- Root `llms.txt` and expanded package keywords for discoverability.

### Fixed

- Deep-audit reliability work for remote `stopped` claim paths, MCP mutate helpers, scheduler admit hot path, and web UI SSE/log races (F-244–F-258).

## [0.7.5] - 2026-11-01

### Added

- Queue hygiene report: `tq hygiene` / `tq report --hygiene`, MCP `report` with `kind=hygiene`, and `GET /api/hygiene` — churn rates, multi-use vs flaky classification, and honest archive proposals (`agent_prompt`; never auto-archives).
- `tq archive --max-completed-runs N` so archive proposals can skip multi-use tasks (hygiene uses `1`).
- Tasks keep `completed_run_count` / `total_run_count` (schema 33); `tq list` reads them instead of scanning all runs.
- History tab pages with a fixed offset (Prev/Next) and mounts log/history expand rows only when opened.
- Help topic `queue-hygiene` and operator/docs notes distinguishing hygiene from retention prune and agent usage summaries.
- House `publish.manifest.yaml` and dual-tree export readiness for the public GitHub mirror.

### Changed

- Unscoped queue `sort=deps` / `sort=order` require a project filter when more than 2000 tasks match.
- `/api/deps` applies project, suite, and `project/task_id` focus in SQL before building the graph; unscoped graphs cap the SELECT at 500 nodes.
- `GET /api/tasks/<project>/<task_id>/runs` is paginated (`limit` default 50, max 200; `offset`).
- Unscoped `GET /api/task-stats` returns at most 500 tasks (prefer `project=`).
- History anomaly badges are computed in a batched pass per page instead of one query per completed run.

## [0.7.4] - 2026-10-26

### Added

- Initial public changelog baseline matching `pyproject.toml` version 0.7.4.
