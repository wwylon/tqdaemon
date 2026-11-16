# Queue rules for agents

Short discipline for any agent (or script) that drives a tqdaemon queue. Specs live next to this file:
[task standards](../task_standards.md) · [MCP tools](../mcp.md) · [worker setup](../WORKER_SETUP.md) · [errors](../errors.md).

## First calls in a session

1. **Create tasks** with MCP `submit_task` / `submit_matrix`, or write a YAML into the project's
   `experiments/operator_queue/` and run `tq sync`. MCP `enqueue_task` / `retry_task` only re-queue
   *existing* terminal tasks; they cannot create one.
2. Set **`TQ_OPERATOR`** so `started_by` and `my_tasks` work.
3. Run **`tq doctor`** (or MCP `queue_status`) before leaving a long run unattended: it confirms the state
   home (`~/.tqdaemon`) and the UI port (`8765`).
4. **After `tq sync` / `tq add` / `tq retry`, start a waiter in the same turn:**
   `tq wait <project>/<task_id> --timeout 3600` as a background command (in Claude Code, `run_in_background`
   re-invokes the session when it exits). Exit `0` = completed, `1` = failed or stopped (run `tq diagnose`),
   `2` = timeout (start it again). MCP `wait_for_task` is the same thing. Never poll with sleep loops;
   `notify_url` is only an external webhook.
5. Prefer `dispatch: local` unless you mean remote. Set `resources.ram_gib` near the expected peak RSS and
   cite the **`#run_id`** when you talk about a run.
6. For live CPU / RAM / VRAM numbers use MCP `progress_snapshot` / `diagnose` or `tq metrics <project>/<task_id>`.
7. For remote workers set `execution_semantics` (or `idempotent`) and prefer `git_patch` / `bundle` workspaces
   over a shared `project_root`.

## Long jobs go in the queue

Anything expected to take more than about two minutes belongs in a YAML under `experiments/operator_queue/`,
followed by `tq sync` (and `tq validate` before leaving it overnight). With `enabled: true` the daemon admits
it; there is no separate "start" step. Leave `enabled: false` (or use `tq freeze` / `tq pause`) only when a
person must gate admission. Do not launch such jobs as background shells inside an agent session: a background
`tq wait` is fine because it only waits. Queued jobs are restartable, logged and kept out of the session context.

Every YAML should have: `resume_args` for restartable tools, `wall_clock_cap_sec` for anything that can hang,
explicit paths for resource directories, the `(XX.X%)` progress contract, and one small **smoke** tier green
before a long run. Check it with `tq validate` and `tq list`.

## Pipelines

Chain tasks with `depends_on` (see [task standards](../task_standards.md), *Dependency Management*). To re-run a
chain use `tq retry <project>/<root> --downstream`; for a whole set use `tq suite retry <suite>` or
`tq group retry <project> <group>`.

## Do not

- Invent YAML keys or worker APIs: use [task standards](../task_standards.md) and the README.
- Treat `dispatch: remote` as a way to park work when no worker is online: it falls back to local.
- Mass-`tq retry` without looking at why the tasks failed (`tq diagnose`, `tq why`).
- `tq unfreeze` / `tq resume` a project, or `tq unhold` a running task, unless its owner asked: a freeze, pause
  or hold is a deliberate decision, not a fault.
- Run GPU work outside the queue on a shared card. Declare `resources.gpu_vram_gib`, check `tq capacity` first,
  and let the scheduler arbitrate.
