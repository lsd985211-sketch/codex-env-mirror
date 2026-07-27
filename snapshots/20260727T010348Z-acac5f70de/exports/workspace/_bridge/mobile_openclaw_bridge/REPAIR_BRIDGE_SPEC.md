# Repair Bridge Command Specification

Status: draft
Created: 2026-06-26
Scope: OpenClaw Weixin bridge maintenance command design

## Purpose

`repair` is the preferred mobile-facing entry point for controlled bridge
repair. `/repair_bridge` remains a compatibility alias for older instructions.
It must classify the real fault first, run only the smallest safe repair for
that class, verify the result, and report unresolved risk clearly.

The command is not a generic "fix everything" button. It must not hide state
loss, duplicate Weixin replies, kill unrelated processes, switch routes, or
rewrite queue history just to make a dashboard look clean.

Normal status acknowledgements and terminal failure receipts are different:
status acknowledgements describe live progress and remain subject to the usual
phone-status gate, while a terminal failure receipt is a one-shot visible
closing notice emitted only after the task has fail-closed.

## Command Surface

```text
repair
repair status
repair safe
repair deep
repair last
repair active
repair cdp
repair backlog
repair supplement
repair plugins
repair tools
```

Default mode is `safe`, so the normal phone command is just `repair`.
Compatibility form `/repair_bridge <mode>` must resolve to the same modes, but
new prompts and operator docs should use the short `repair` form.

Implementation phase 2 wires `last`, `active`, `cdp`, `backlog`,
`supplement`, `plugins`, and `tools` to bounded scoped executors. These modes
must stay narrower than full safe repair: they collect only scope-relevant
evidence and run only the smallest already-safe action for that scope. They must
not call broad maintenance repair as a shortcut, because that can accidentally
start unrelated workers, repair unrelated config, or handle reply leases outside
the requested scope. Send-capable, destructive, route-switching,
process-killing, package-install, and active-task mutation actions remain
blocked or plan-only unless separately confirmed.

`status` is read-only. `safe` may apply low-risk local repairs. `deep` must
return a confirmation-gated plan, not execute high-risk actions by default.

## Pipeline

Every mode follows the same pipeline:

```text
observe -> classify -> constrain -> repair -> verify -> report
```

- `observe`: collect the authoritative state from maintenance, task rows,
  events, Codex thread state, app-server/CDP probes, plugin/tool probes, and
  reply-delivery evidence.
- `classify`: assign one or more fault classes with confidence and evidence.
- `constrain`: derive the allowed actions and forbidden actions from the class.
- `repair`: run only the minimal allowed action set.
- `verify`: rerun targeted probes and compare before/after state.
- `report`: reply with what changed, what did not change, remaining risk, and
  the next required confirmation if any.

## Controlled Iteration Finalization

Any broad change to bridge delivery, maintenance repair, resource acquisition,
GUI automation, configuration repair, or agent-interaction behavior must run
the controlled iteration gate before the final operator report:

```powershell
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance iteration
```

The output is proposal-only. It may identify skill, project-knowledge,
tool-registry, or CLI automation follow-up work, but it must not be treated as
permission to write files, mutate queue state, send Weixin replies, or apply
repair actions. The final report for broad system work must show a concise
proposal summary and any validation failures. If the gate is unavailable, the
report must say so explicitly and avoid promoting new long-term rules.

## Authoritative State Sources

Use these sources before making a repair decision:

- `maintenance summary --deep`: route/account/backlog and health overview.
- `maintenance doctor`: structured issues and evidence.
- `maintenance repair`: dry-run plan and existing safe repair boundaries.
- `stuck-tasks`: active `queued_for_codex`, `sent_to_codex`, and processing
  rows.
- `mobile_events`: durable delivery, recovery, result, and reply events.
- Codex thread readback: actual turn materialization, interrupted turns,
  visible protocol markers, and final results.
- Mobile MCP: supplement wait/ack state.
- `tool-registry-health`: MCP, plugins, app-server, CDP, GUI, and tool drift.

Do not treat one source as final when it is known to lag. Reconcile task state,
thread state, result state, and reply state together.

## Fault Classes And Actions

| Class | Typical evidence | Safe actions | Forbidden automatic actions |
| --- | --- | --- | --- |
| `result_boundary_drift` | Owned `mobile_result_begin/end` exists in thread, DB result empty, task not completed | Recover result by exact task id and owned boundary, then schedule normal reply flow | Guess result from unowned text; rewrite unrelated task result |
| `reply_delivery_backlog` | Result exists; `reply_pending`, `reply_retrying`, or expired `reply_sending` | Recover expired send lease; schedule due retry only when mode allows send | Resend already-accepted replies automatically; treat send acceptance as tentative visible delivery proof |
| `terminal_failure_receipt_missing` | Task is fail-closed after bounded recovery, but no visible closing notice was emitted | Emit one terminal failure receipt using the dedicated failure-close path; keep retry chain closed | Treat it as a normal status ack; reopen retry loops; suppress the final user-facing notice |
| `supplement_misrouting` | Supplement published but not MCP-acked; base owner active/inactive mismatch | If base still active, republish or expose pending batch; if base closed, promote as new owner/task | Ack and drop unprocessed content; pretend it was merged after base finished |
| `active_execution_stall` | Active task age high; thread idle/interrupted or tool process stale | If thread interrupted/idle with no result, send a bounded recovery prompt; if external process is progressing, observe; if stale, request confirmation | Mark failed/cancelled, move active to pending, or kill processes automatically |
| `app_server_turn_materialization_lag` | `turn/start` returned turn id, but `turns/list` could not read it during the window; later thread readback shows the delegation | Rehydrate queued task from durable turn/thread evidence; avoid duplicate dispatch | Redeliver while a matching task already exists in thread; force sent without evidence |
| `app_server_no_result_or_empty_spin` | App-server turn acked the mobile task but later completed/interrupted without owned result, or stayed in-progress with no text, result, or tool progress past the bounded threshold | Interrupt the old turn once, then submit one same-thread repair continuation that reuses the original result markers and forbids duplicate side effects; if continuation fails, fail closed for manual recovery | Send alternate prompts repeatedly; redeliver the original task as a new task; duplicate installs/downloads/GUI/Weixin sends |
| `cdp_route_failure` | CDP listener stale/missing; `/json/version` fails; visible route cannot send | Restart only the configured CDP path when allowed; verify live listener and `/json/version` | Switch primary route, launch plain non-admin Codex, or assume stale OS listener is live |
| `config_or_plugin_drift` | Known catalog MCP/plugin entries missing or drifted | Additive-only config repair with backup; never delete new entries | Roll back plugin list to an old baseline; delete unknown user config |
| `toolchain_acquisition_gap` | Tool expected but missing, installed but PATH-invisible, or install chain hanging | Probe PATH, registry, WinGet package directories, and known install paths; install one approved tool at a time | Batch-install large tools without timeout; mutate PATH just to pass a probe |
| `not_a_fault` | Active route is genuinely processing; long task has visible progress | Observe and report progress | Force repair to quiet the dashboard |

## Mode Contracts

### `repair status`

Read-only. Return the top classes, evidence, and suggested next mode.

Must not mutate files, DB rows, routes, replies, or processes.

### `repair safe`

May run only safe local repairs already allowed by maintenance policy:

- start missing worker scheduled task;
- start Gateway scheduled task when gateway port is down;
- recover expired `reply_sending` leases;
- clean stale dashboard live-state temp files;
- additive-only known MCP config repair with marked backup;
- additive-only plugin enablement repair with marked backup;
- rehydrate queued app-server turns when durable task/thread evidence proves
  delivery already materialized.

It must not send Weixin messages unless the user explicitly requested a
send-capable mode or flag.

### `repair deep`

Diagnostic and plan-only by default. It may propose high-risk actions, but each
action must include risk, exact target ids, backup/rollback plan, and required
confirmation.

### `repair last`

Scope to the latest task for the caller/account. Detect:

- result exists but was not written to DB;
- result written but reply not sent;
- reply accepted but phone visibility unknown;
- active task interrupted or still processing;
- app-server turn materialization lag.

Do not blindly resend. Prefer result recovery or reply-state repair before
send retry.

### `repair active`

Scope to active/queued tasks for the caller/account. Detect:

- `queued_for_codex` with durable started-turn evidence;
- `sent_to_codex` with missing volatile runtime;
- interrupted Codex turn;
- real long-running external process;
- route busy with ordered pending backlog.

Allowed repair is evidence collection plus worker-owned bounded recovery
observation. The mobile scoped executor must not directly interrupt turns,
submit continuation prompts, kill processes, mark tasks failed/cancelled, or
move active rows back to pending. Those actions stay in the worker recovery path
or require a narrower confirmed maintenance operation.

### `repair cdp`

Scope to visible desktop route. Verify live listener, `/json/version`, send
script, and startup contract. Repair only the configured route; do not switch
primary route.

### `repair backlog`

Scope to reply backlog and pending backlog. Reply retries require explicit
send permission. Pending backlog with active work should be classified before
repair. The scoped executor may recover expired `reply_sending` leases back to
retryable state, but it must not schedule or send Weixin replies without an
explicit send-capable mode or flag.

### `repair supplement`

Scope to supplement state. Preserve user content. Never ack an unprocessed
supplement merely to clear the queue. Evidence must come from pending task rows
and `bridge_supplement:*` runtime payloads, not from UI text alone.

### `repair plugins`

Scope to MCP/plugin config drift. Use additive-only repair, backup config, and
report that Codex Desktop restart may be required before live tools update.
This is the only specialized mode that may write Codex config under the safe
repair contract; it must call only MCP/plugin config repair helpers and must not
trigger unrelated safe repairs.

### `repair tools`

Scope to external tools. Probe before installing. Treat "installed but not in
PATH" as a separate state. Install one tool at a time only after approval.

## App-Server Materialization Lag Policy

This class was observed on backup1:

- `turn/start` returned a turn id.
- Post-dispatch readback could not find the turn within the bounded window.
- Worker reverted the task to pending/queued to avoid phantom active state.
- Later Codex thread readback showed the delegation content had actually
  materialized in the thread.

Repair policy:

1. Record provisional turn id, task id, thread id, batch id, and protocol
   markers when `turn/start` succeeds but readback is not yet visible.
2. During retry/recovery, inspect durable Codex thread readback before
   redelivery.
3. If the same task id and protocol markers are visible in the thread, rehydrate
   the task to observation state instead of dispatching it again.
4. If an owned result exists, recover the result and continue reply flow.
5. If the turn is still invisible after the grace window and no thread evidence
   exists, retry dispatch once per route cooldown.

This avoids both lost tasks and duplicate deliveries.

## App-Server No-Result Continuation Policy

This class covers backup-account turns that were delivered to Codex, emitted
`mobile_ack`, but then either ended without an owned `mobile_result` boundary or
sat in an empty in-progress state long enough to occupy the route.

Repair policy:

1. Poll for an existing owned result first, including historical attempts for
   the same task id/result code.
2. If the old turn is still in progress and shows real work such as an
   in-progress tool call, observe instead of interrupting.
3. If it is terminal-without-result or empty-spinning beyond the configured
   threshold, call `turn/interrupt` for the exact old thread/turn.
4. Only after interrupt confirmation, submit one repair continuation to the
   same thread. The continuation must reuse the original result code and tell
   Codex to inspect existing progress before acting.
5. The continuation prompt must forbid repeating irreversible side effects such
   as installs, downloads, file/GUI mutations, Weixin sends, purchases, or
   external posts unless prior execution is clearly absent.
6. If the continuation cannot be started, or a previous continuation already
   failed, do not send a different prompt. Mark the task as requiring manual
   recovery, release route occupancy, and preserve evidence.

This keeps the recovery intent aligned with the original task: complete the
same owned result when possible, but do not turn recovery into duplicate
execution.

## Invariants

- A task result belongs only to its exact task id and owned result markers.
- `mobile_ack` means received, not completed.
- `pushed_to_wecom` means send path accepted a request; it is not always phone
  visible proof.
- `failed` is not final if a later owned result exists.
- `queued_for_codex` with durable started-turn evidence is not a normal pending
  task.
- A completed base cannot retroactively merge an unconsumed supplement into its
  final answer.
- Repair output must not downgrade mobile tasks into status-only replies.

## Required Output Schema

Every run should produce a compact machine-readable object and a short Weixin
summary:

```json
{
  "ok": true,
  "mode": "safe",
  "run_id": "repair-...",
  "detected_classes": [
    {
      "class": "app_server_turn_materialization_lag",
      "confidence": "high",
      "task_ids": ["..."],
      "evidence": ["..."],
      "allowed_actions": ["queued_turn_rehydrate"],
      "forbidden_actions": ["duplicate_dispatch"]
    }
  ],
  "actions_taken": [],
  "actions_blocked": [],
  "verification": {
    "before": {},
    "after": {},
    "passed": true
  },
  "next_step": ""
}
```

## Validation Matrix

Before implementation is enabled from mobile, add or reuse checks for:

- owned result recovery from completed/interrupted thread;
- failed task with later owned result;
- reply pending retry without duplicate accepted replies;
- accepted-but-unconfirmed reply is not resent automatically;
- terminal failure close emits exactly one visible failure receipt;
- a recovered owned result does not also emit the failure-close receipt;
- supplement before final is consumed;
- supplement after final is promoted, not dropped;
- app-server turn materialization lag rehydrates instead of redelivering;
- app-server terminal-without-result starts exactly one continuation after
  interrupt and preserves original result markers;
- app-server acked empty-spin starts exactly one continuation after interrupt;
- continuation dispatch failure fails closed without alternate prompts;
- old-turn interrupt failure observes/defer instead of creating a second active
  turn;
- attachment/file/GUI/send tasks do not automatically repeat side effects
  through continuation;
- active app-server turns with real in-progress tools are observed, not
  interrupted;
- queued same-route ordering is preserved;
- active long external install is observed, not killed;
- plugin repair is additive-only;
- CDP stale OS listener is not treated as live;
- `safe` mode performs no Weixin send unless explicitly allowed.

## Implementation Order

1. Add read-only classifier command and JSON output.
2. Wire `repair status` to classifier only.
3. Wire `repair safe` to existing safe maintenance repair plus
   app-server queued-turn rehydrate.
4. Add `last`, `active`, and `backlog` scopes. (implemented as bounded scoped
   executors)
5. Add `cdp`, `supplement`, `plugins`, and `tools` scopes. (implemented as
   bounded scoped executors)
6. Add confirmation-gated `deep` plan mode.
7. Only after validation, expose send-capable reply retry behind explicit
   confirmation or admin-only flag.
