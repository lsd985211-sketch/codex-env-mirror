# mail Maintenance Surfaces

Authority: maintenance contracts for this system. Load this shard only
after the maintenance registry selects the system. The compact index at
`../maintenance_surface_map.md` is navigation only and does not duplicate
the contracts below.

| Surface | Owns | Does Not Own | Usual Entry |
| --- | --- | --- | --- |
| `persistent_task_kernel.py` / `persistent_task_kernel_tests.py` | Sidecar durable task control state: idempotent enqueue, exact-task or priority claim, lease/ack/execution transitions, approval pause, bounded retry, dead-letter, expired-lease classification, and signature-fenced durable terminal settlement. The unified scheduler invokes only guarded expired-lease recovery every five minutes; leased/acked tasks return to `queued`, while interrupted execution becomes `recovery_required` until the original terminal evidence is consumed. | Executing owner commands, claiming or running tasks from the scheduler, sending mail, creating Codex threads, replacing owner business state, auto-starting a worker, or accepting terminal evidence whose operation/input/execution signatures do not match the task payload. | `snapshot`, `doctor`, `repair-plan`, `validate`, `metrics`, `behavior-eval`, `recover-expired --apply --confirm RECOVER-EXPIRED-TASKS`; validate with `python _bridge\persistent_task_kernel_tests.py`, `python _bridge\business_environment_durable_executor_tests.py`, and `python _bridge\shared\codex_scheduler_runner.py task-drift` |
| `shared/email_state_index.py` | Derived email query index and stable-ID reconciliation across task/run/content/outbox/inbound/RFC Message-ID/SMTP receipt identities | Sending, deleting, resending, or direct business-state repair through SQLite | `email_scheduler.py state-index refresh --apply`, `state-index repair-plan`, `state-query --table reconciliation`, `validate` |
