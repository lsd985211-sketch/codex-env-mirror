# OpenClaw Weixin Mobile Bridge

This is the OpenClaw Weixin adapter for the local mobile bridge.

## Current Scope

- Reuses `_bridge/mobile_wecom_bridge/mobile_queue.py` for SQLite queue,
  allowlist, dedupe, risk classification, cooldown, confirmation, and state
  handling.
- Ingests full OpenClaw Weixin message text into a separate queue database
  through the `openclaw-mobile-queue` plugin.
- Defaults to shadow mode. It records mobile messages and can safely simulate
  dispatch without waking Codex.
- Supports a verified Codex Desktop CDP delivery mode for controlled real
  dispatch into the active Codex thread.

## Why Shadow Mode

Shadow mode is the default safety posture. Real delivery uses Codex Desktop's
local CDP endpoint and should only be enabled when Codex Desktop is already
running with the expected thread open.

## Files

- `mobile_openclaw_cli.py`: local CLI for explicit enqueue, log metadata ingest,
  task listing, health checks, and worker polling.
- `health_checks.py`: low-noise bridge diagnostics used by `stability-check`.
  Keep new health probes here instead of growing the CLI.
- `..\file_toolkit\`: read-only attachment analysis layer used for local file
  previews. It keeps document/image/archive parsing out of the queue worker.
- `config.example.json`: template config.
- `config.local.json`: local config, if present.
- `mobile_openclaw_bridge.db`: local queue database, created on first use.
- `start-worker-hidden.ps1`: starts the polling worker in the background.
- `run-openclaw-gateway-loop.ps1`: supervises the local OpenClaw Gateway and
  restarts it if port `127.0.0.1:18789` stops listening.
- `start-openclaw-gateway-hidden.ps1`: starts the Gateway supervisor in the
  background.

## Commands

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\mobile_openclaw_bridge
python .\mobile_openclaw_cli.py health
python .\mobile_openclaw_cli.py enqueue "test from phone" --user "o9cq80_7_t7OGRYescsBdqz_4YrI@im.wechat"
python .\mobile_openclaw_cli.py ingest-log
python .\mobile_openclaw_cli.py list
python .\mobile_openclaw_cli.py worker-once
python .\mobile_openclaw_cli.py worker-loop --interval 10
python .\mobile_openclaw_cli.py stability-check
python .\mobile_openclaw_cli.py maintenance summary
python .\mobile_openclaw_cli.py maintenance inspect
python .\mobile_openclaw_cli.py maintenance doctor
python .\mobile_openclaw_cli.py maintenance repair
python .\mobile_openclaw_cli.py maintenance repair --apply
python .\mobile_openclaw_cli.py stuck-tasks
python .\mobile_openclaw_cli.py mode status
python .\mobile_openclaw_cli.py mode shadow
python .\mobile_openclaw_cli.py mode real
python .\mobile_openclaw_cli.py mode pause
python .\mobile_openclaw_cli.py mode resume
python .\mobile_openclaw_cli.py control stop
python .\mobile_openclaw_cli.py control resume
python .\mobile_openclaw_cli.py stop-status
python .\mobile_openclaw_cli.py scan-stop-log
python .\mobile_openclaw_cli.py confirm-latest --secret "<secret>"
python .\mobile_openclaw_cli.py set-secret-hash --secret "<secret>"
python .\mobile_openclaw_cli.py reply <task_id> --text "reply text"
python .\mobile_openclaw_cli.py reply <task_id> --text "reply text" --send
python .\mobile_openclaw_cli.py thread-route list
python .\mobile_openclaw_cli.py thread-route get --user "<external_user>"
python .\mobile_openclaw_cli.py thread-route set --user "<external_user>" --thread "<thread_id_or_name>"
python .\mobile_dashboard.py --host 127.0.0.1 --port 18808
python .\dashboard_smoke.py
.\open-dashboard.ps1
.\start-worker-hidden.ps1
.\install-worker-task.ps1 -StartNow
.\start-openclaw-gateway-hidden.ps1
.\install-openclaw-gateway-task.ps1 -StartNow
```

## Maintenance Doctor

Use `maintenance` before ad-hoc bridge repairs. It keeps inspection and repair
route/account scoped, so one bad account or thread does not hide the rest of
the system state.

```powershell
python .\mobile_openclaw_cli.py maintenance summary
python .\mobile_openclaw_cli.py maintenance inspect
python .\mobile_openclaw_cli.py maintenance doctor
python .\mobile_openclaw_cli.py maintenance repair
python .\mobile_openclaw_cli.py maintenance repair --apply
```

`summary` is the first-look operator view. It prints the bridge layer state,
queue totals, top account backlogs, top route backlogs, safe repairs currently
available, and manual-only boundaries. Use it before opening the larger JSON
reports.

`inspect` is a structured snapshot: task counts by account/status, active
routes, pending routes, reply backlog, worker/gateway processes, scheduled
tasks, key ports, SQLite file health, latest worker stderr, and dashboard
live-state freshness.

`doctor` classifies the snapshot into findings with evidence, severity, and
safe or manual next steps. Current classes include gateway down, worker down,
worker scheduled task disabled, global PAUSE/STOP_REQUEST active, database
unhealthy, database size high, CDP unavailable, pending backlog, old active
tasks, route active-plus-pending, reply delivery backlog, stale dashboard live
state, and repeated CDP probe failures.

`repair` defaults to dry-run. `repair --apply` only performs low-risk repairs:
start the worker scheduled task when absent, start the Gateway scheduled task
when port `18789` is down, recover expired `reply_sending` leases back to
`reply_pending`, and remove stale dashboard live-state temp files. It does not
delete tasks, fail tasks, move active `sent_to_codex` tasks back to pending,
change account bindings, alter Codex config, or switch delivery routes.
It also does not compact or rewrite the SQLite database automatically; DB
cleanup or `VACUUM` requires a deliberate backup and separate confirmation.

Retrying `reply_pending` can send old messages to Weixin users, so it is behind
an explicit extra flag:

```powershell
python .\mobile_openclaw_cli.py maintenance repair --apply --include-reply-send
```

Use that only after reviewing `maintenance doctor` output.

Maintenance safety table:

| Class | Examples | Default behavior |
| --- | --- | --- |
| Safe automatic repair | start missing worker task, start Gateway task when port is down, clean dashboard live-state temp files, recover expired `reply_sending` leases | Allowed by `maintenance repair --apply` |
| Explicit send repair | retry or schedule `reply_pending` Weixin replies | Requires `--include-reply-send` |
| Manual review only | clearing `STOP_REQUEST` or PAUSE, enabling a disabled worker task, CDP/app-server recovery, DB cleanup or `VACUUM`, old active result review, account binding/config/MCP baseline changes | Never automatic |
| Forbidden automatic recovery | delete tasks, mark active tasks failed/cancelled, move `sent_to_codex` back to `pending`, switch routes globally | Do not do from maintenance repair |

The maintenance layer must stay diagnostic and conservative. If one account or
route is broken, it should explain that route and keep the rest visible instead
of applying global state changes.

## High-Frequency Runbooks

Use these bounded operator paths for the most common bridge-maintenance tasks.
They are intentionally diagnostic-first and keep repair, validation, and
promotion concerns separated.

### 1. Reply Delivery Backlog

Use this when `maintenance summary` or `maintenance doctor` reports
`reply_delivery_backlog`.

1. Confirm the backlog is real and not mixed with active queue work:

```powershell
python .\mobile_openclaw_cli.py maintenance summary
python .\mobile_openclaw_cli.py maintenance doctor
```

2. Read the account and sample task evidence from `doctor`. Distinguish:
   - historical `push_failed`
   - `reply_pending` or `reply_retrying`
   - accepted-but-phone-visibility-unconfirmed historical rows

3. Inspect the dry-run repair plan before applying anything:

```powershell
python .\mobile_openclaw_cli.py maintenance repair
```

4. Only if the repair plan matches the observed backlog, apply the low-risk
   local repair:

```powershell
python .\mobile_openclaw_cli.py maintenance repair --apply
```

5. Only add `--include-reply-send` after deliberate review, because it can send
   old replies to Weixin users:

```powershell
python .\mobile_openclaw_cli.py maintenance repair --apply --include-reply-send
```

Boundaries:

- Do not treat `push_failed` and `phone_visible_not_confirmed` as the same
  class.
- Do not resend automatically just because a result exists.
- Do not manually delete queue rows or move active `sent_to_codex` rows back to
  `pending` from this runbook.

### 2. Tool Registry To Repair Path

Use this when the question is "what capabilities are actually available?" or
when a bridge/tool problem may be configuration drift rather than queue logic.

1. Start from the read-only tool baseline:

```powershell
python .\mobile_openclaw_cli.py tool-registry-health
```

2. If the registry suggests bridge health is degraded or drifted, read the
   operator summary and then the structured diagnosis:

```powershell
python .\mobile_openclaw_cli.py maintenance summary
python .\mobile_openclaw_cli.py maintenance doctor
```

3. Use `doctor` to separate:
   - true current health issues in `diagnosis.issues`
   - read-only next-step guidance in `advisories`

4. Use `repair` dry-run only after the above two views agree on the issue
   class:

```powershell
python .\mobile_openclaw_cli.py maintenance repair
```

5. Apply repair only when the issue is inside the safe automatic boundary:

```powershell
python .\mobile_openclaw_cli.py maintenance repair --apply
```

Boundaries:

- `advisories` are review guidance, not repair permission.
- `tool-registry-health` is the preferred bounded validation step before
  promoting bridge/tool rules into longer-lived knowledge.
- Do not let a static registry note override live health output.

### 3. Resource Layer And Iteration Quick Validation

Use this when external-resource behavior changed, resource strategy was updated,
or you want to validate whether a new lesson is stable enough for proposal-only
promotion.

1. Verify the resource layer itself:

```powershell
python .\mobile_openclaw_cli.py resource-layer-smoke-check
```

2. Verify the local tool baseline that the resource layer depends on:

```powershell
python .\mobile_openclaw_cli.py tool-registry-health
```

3. Run the bounded controlled-iteration validation loop:

```powershell
python ..\iteration_layer_review.py --json --recent-limit 12 --run-validation --validation-profile quick
```

4. Review these fields in the iteration output:
   - `decision_summary`
   - `proposal_groups`
   - `recommended_next_actions`
   - `promotion_readiness_summary`

5. Promote nothing automatically. Use the report only to decide whether a
   proposal should be reviewed, documented, or ignored.

Boundaries:

- `resource-layer-smoke-check` validates bounded acquisition behavior; it is
  not permission to install new tools or broaden resource policy.
- `quick` is the default operational validation path; reserve `full` for
  deliberate deeper sweeps.
- Do not convert temp artifacts, cache contents, or smoke output directly into
  long-term facts without a separate review step.

### 4. Operator Routing Table

Use this table when you need a fast, bounded first step and do not want to guess which surface is authoritative.

| Symptom | First command | Second command | Safe boundary |
| --- | --- | --- | --- |
| Reply backlog or delayed Weixin delivery | `maintenance summary` | `maintenance doctor` | Read-only until the repair plan matches the backlog class |
| Tool or plugin availability looks wrong | `tool-registry-health` | `maintenance summary` | Do not treat static notes as source of truth over live health |
| Resource acquisition or external fetch behavior changed | `resource-layer-smoke-check` | `iteration_layer_review.py --run-validation --validation-profile quick` | Proposal-only; do not promote temp/cache evidence directly |

The routing table is only a front door. It points you to the existing runbooks and validation checks, not to new repair behavior.
## Regression Checks

Use these read-only temp checks after bridge dispatch, routing, or reply
changes:

```powershell
python .\mobile_openclaw_cli.py onboarding-check
python .\mobile_openclaw_cli.py result-ownership-check
python .\mobile_openclaw_cli.py fair-scheduling-check
python .\mobile_openclaw_cli.py active-slot-release-check
python .\mobile_openclaw_cli.py thread-busy-status-check
python .\mobile_openclaw_cli.py thread-prewarm-budget-check
python .\mobile_openclaw_cli.py thread-prewarm-execution-check
python .\mobile_openclaw_cli.py cdp-visible-delivery-check
python .\mobile_openclaw_cli.py app-server-sync-check
```

For thread route health, `notLoaded`, `loading`, and `unloaded` are
recoverable app-server states, not fatal bridge failures. The worker can still
dispatch to the thread and schedules a background prewarm so the thread becomes
readable again. Treat missing/unlisted/error threads as blocking conditions.

## Local Conversation Dashboard

`mobile_dashboard.py` provides a read-only Codex-style conversation panel for
inspecting phone messages, per-user task state, receiver account routing,
target Codex thread, final result text, and task events. It exists because
Codex Desktop's native UI does not reliably render multiple background
app-server conversations at the same time.

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\mobile_openclaw_bridge
python .\mobile_dashboard.py --host 127.0.0.1 --port 18808
```

Open `http://127.0.0.1:18808/` after starting it. The dashboard only reads
`mobile_openclaw_bridge.db` and `config.local.json`; it does not send Weixin
messages, dispatch Codex turns, modify queue state, or expose secrets.

The second-stage UI follows the local Codex app shape:

- left: per-Weixin-user conversation list
- center: phone message and Codex final-reply stream
- right: selected task details and event trace

Keep the front end framework-free. Use semantic HTML, CSS variables, shallow
selectors, and small single-purpose JavaScript functions so Codex can safely
rewrite one region without breaking the rest of the page.

Verify the panel with Playwright through the system Microsoft Edge or Chrome,
without downloading Playwright's bundled Chromium:

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\mobile_openclaw_bridge
python .\dashboard_smoke.py
```

The smoke test writes `logs\mobile-dashboard-smoke.png` and fails on frontend
console errors, an empty task list, or an unhealthy dashboard header.

The desktop shortcut `微信桥接面板.lnk` runs `open-dashboard.ps1`. The script
starts the dashboard in a hidden background process only when
`http://127.0.0.1:18808/api/state` is not already healthy, then opens the panel
URL. When the dashboard HTTP service is already healthy, the script opens the
visible browser page before non-essential backend repair so app-server or login
startup delays do not block manual access to the panel.

For manual opening failures, validate the actual desktop shortcut path instead
of only checking the HTTP endpoint or running the script directly:

```powershell
Invoke-Item 'C:\Users\45543\Desktop\微信桥接面板.lnk'
Get-Content .\runtime\dashboard_open_last.log -Tail 30
```

The shortcut is considered healthy only when the log records URL opening and a
visible browser window loads `http://127.0.0.1:18808/`.

## Script Inventory

Use `..\script_inventory.py` before broad script searches. It classifies active,
helper, legacy, dependency, and backup scripts while excluding noisy directories
such as `backups`, `node_modules`, `dist`, `extract`, `logs`, and `attachments`
by default.

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
python .\_bridge\script_inventory.py
python .\_bridge\script_inventory.py --json
```

Only use `--include-history` for explicit rollback or historical comparison
work. It is intentionally noisy.

## Script Quality Check

Use `..\script_quality_check.py` after the inventory step when you need a
low-noise read-only check over active and helper scripts. It runs Python
`py_compile`, JavaScript/MJS `node --check`, and PowerShell parser checks.
`PSScriptAnalyzer` is optional and only runs when requested and installed.
In this workspace, the checker prepends `_tools\powershell_modules` to
`PSModulePath` for its own subprocesses and prefers
`C:\Program Files\nodejs\node.exe` when present, so it does not depend on the
current Codex process PATH order.

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
python .\_bridge\script_quality_check.py
python .\_bridge\script_quality_check.py --json
python .\_bridge\script_quality_check.py --psscriptanalyzer
```

This tool is diagnostic only. It is not part of the worker startup path.

## Codex Startup Baseline Audit

Use `..\codex_state_audit.py` after restarting Codex if MCP servers, plugins,
memories, or project permissions appear missing. It is read-only and checks the
global config, project config BOM/parseability, expected MCP registrations,
expected plugin enablement, memory flags, and `codex mcp list`.

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
python .\_bridge\codex_state_audit.py
```

The expected state is declared in `..\codex_startup_baseline.json`. This keeps
the baseline out of the audit code so future changes are explicit and reviewable.

If the audit fails after a Codex restart, use the merge repair tool instead of
restoring old config files wholesale:

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
python .\_bridge\codex_state_repair.py --dry-run
python .\_bridge\codex_state_repair.py
python .\_bridge\codex_state_audit.py
```

The repair tool creates a timestamped backup under `..\backups`, removes UTF-8
BOM from the project config when needed, restores missing baseline MCP/plugin
sections, validates TOML, and reports whether Codex should be restarted.
The baseline pins sandbox defaults in both global and project config. This is
intentional: if Codex starts outside the saved project path or project trust is
not applied early enough, the global sandbox defaults prevent another sandbox
setup prompt.

When Codex intentionally changes in a large way, update the baseline after the
new state has been verified. Do not let the repair tool become a time machine
that drags Codex back to an old startup state:

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
python .\_bridge\codex_baseline_update.py
python .\_bridge\codex_baseline_update.py --adopt-current --reason "verified new Codex MCP/plugin baseline"
python .\_bridge\codex_state_audit.py
```

The updater only modifies `..\codex_startup_baseline.json`; it does not edit
Codex config files. The repair tool is also non-deleting: extra MCP servers or
plugins that exist in live config are reported by audit but not removed.

## Network Doctor

Use `..\network_doctor.py` when Codex or the bridge appears to "lose internet"
or when a tool needs current docs but the network path is unclear. It checks a
small set of official endpoints through multiple local runtimes:

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
python .\_bridge\network_doctor.py
python .\_bridge\network_doctor.py --json
```

The doctor is read-only. It does not install packages automatically. If a path
fails, the output tells you which runtime to inspect first:

- PowerShell: WinHTTP proxy, TLS trust, `Invoke-WebRequest`
- Python: certificate bundle, proxy env, runtime packaging
- Node: `NODE_USE_ENV_PROXY`, proxy env, Node executable

If the problem is clearly missing tooling rather than network itself, install
the dependency in `_tools` or the project runtime first, then rerun the doctor.

## Worker Behavior

- `shadow_mode=true`: pending tasks are closed with a recorded "would dispatch"
  result. Codex is not triggered.
- `shadow_mode=false`: pending tasks pass queue safety checks, then the worker
  dispatches the combined prompt to Codex. The preferred
  `delivery_mode=codex-app-server` uses Codex app-server on
  `127.0.0.1:18791`, resumes the configured `thread_id`, starts a turn, and
  polls that turn for a `final_answer`. This supports background delivery to a
  mapped Codex thread without relying on the currently visible Desktop window.
  The older `delivery_mode=codex-cdp` path is still available as a fallback for
  the currently visible Codex Desktop thread through `127.0.0.1:9229`, but it
  cannot safely deliver to arbitrary background threads.
- The worker respects the `PAUSE` file, cooldown, running-lock semantics in
  `MobileQueue.queue_for_codex`, allowlist, dedupe, and stale Codex timeout
  cleanup.
- Exact `stop` and `resume` messages from an allowlisted Weixin user are
  control-plane commands. They are handled during enqueue before normal Codex
  dispatch. `stop` creates `PAUSE`, writes `STOP_REQUEST`, enables shadow mode,
  stops current worker/supervisor processes, and disables the scheduled worker
  task. `resume` clears the stop markers, disables shadow mode, enables the
  scheduled task, and starts it.
- Exact `status` from an allowlisted Weixin user is also handled before normal
  dispatch. The worker replies directly with bridge state and does not wake
  Codex.
- Exact `切换线程`, `选择线程`, `线程列表`, `thread`, or `threads` opens the
  Codex project-thread selector. The reply lists numbered display names and
  short descriptions without exposing thread ids. The user can then reply with
  a number, display name, stable id, or alias within the configured TTL; future
  normal messages from that Weixin user route to the selected thread id.
- Default routing is stable-id based. Do not special-case a hidden "main" name.
  Each selectable thread should have a stable `id`, editable display `name`,
  optional aliases, and a hidden `thread_id`.
- New Weixin users must have their own route before worker delivery. Do not let
  an unmapped user silently fall back to the bridge's main thread. Use
  `thread-route set` after creating or selecting the user's Codex thread.
- `STOP_REQUEST` is a soft interrupt marker for Codex-side work. It blocks
  future mobile dispatch immediately, but it does not kill Codex Desktop.
- Phone confirmation secrets are only for high-risk L3 tasks. The user replies
  with the secret directly; it confirms the latest waiting high-risk task for
  that allowlisted user and starts a background `worker-once` for that task.
  Logs and config should use a hash, not the cleartext secret.
- `permission_table.json` is the bridge permission table. Mobile prompts should
  carry a compact permission profile/table reference, not a long inline
  capability list. Codex uses the profile for reasoning, while CLI, dashboard,
  and maintenance entry points enforce the same table before side effects.
  Admin is the bridge superuser: the primary-account user owns all defined and
  currently unspecified permissions, with audit and risk controls. For normal
  users, `ask` is not a wildcard. It is a whitelist action for non-sensitive
  questions, processing user-provided data, and explicitly requested external
  public resources. It does not grant local file/database/log/system
  diagnostic/resource-library reads, secret access, local-data export, local
  data modification/deletion, or local system side effects. If a normal-user
  mobile request needs an action absent from the active permission profile, the
  execution layer must reject it and Codex must refuse that part even when the
  user explicitly asks for it.
- `risk_rules.json` owns the keyword lists used for L2/L3 classification.
  If it is missing or invalid, the queue falls back to built-in defaults.
- `scan-stop-log` is an emergency fallback for manual or future watcher use. It
  conservatively scans OpenClaw logs for exact admin-authorized `stop` evidence; it
  should not be used as a broad natural-language parser.
- `stability-check` is a low-noise health summary covering queue health,
  worker/scheduled-task state, scheduled-task action target, config/SQLite
  integrity, attachment directory writability, latest worker stderr/log sizes,
  OpenClaw/CDP ports and CDP `/json/version`. It
  does not process queue tasks or change bridge mode. Its attachment write
  probe creates and removes `attachments\.write-probe`.
- Evolvability rule: add future health probes to `health_checks.py` first.
  Keep `mobile_openclaw_cli.py` focused on argument parsing, queue operations,
  dispatch orchestration, and phone control-plane behavior.
- `stuck-tasks` is read-only by default. It lists active `queued_for_codex`,
  `sent_to_codex`, and `processing` tasks. Only use
  `stuck-tasks --mark-failed --confirm mark-failed` after manual review.

## Delivery Backends

The inbound queue, Codex delivery path, manual Weixin reply path, and automatic
reply plumbing are implemented. The current default backend is
`codex-app-server`: it starts a loopback Codex app-server if needed, dispatches
by configured thread id, and returns only the completed final answer to Weixin.
The CDP backend remains useful for current-window diagnostics and stop-button
compatibility, but it is not the route for multi-thread background delivery.

## Weixin Reply Semantics

Phone-visible Weixin reply delivery uses direct iLink `sendmessage`. The
`weixin_send_reply.mjs` default transport is `direct-ilink`; `--transport
gateway` is diagnostic-only. OpenClaw Gateway `HTTP 200` with a `messageId`
means the local gateway accepted the send request, but it does not prove that
the phone UI displayed the message. Treat the bridge status `pushed_to_wecom`
as phone-visible only when the direct iLink path returned `HTTP 200 {}` or the
user confirmed receipt.

Final replies that contain both text and media must be sent as two logical
parts: a text message first, then the media message. The media transport receipt
does not prove that the text was displayed. The bridge records
`final_reply_text_accepted` and `final_reply_media_accepted` separately, and the
whole task should be treated as complete only when both accepted events exist.

Reply account routing must follow the receiving Weixin user. The worker first
checks `openclaw-weixin\accounts\<slot>.context-tokens.json`, then account
`userId` bindings, before falling back to `openclaw.account_id`. This prevents
messages from a user bound to `backup1` from being replied to through
`primary`, which can produce a gateway message id while the phone sees no
reply.

## Message Envelope

Mobile tasks carry a lot of fixed envelope text by design. The actual user
request is the `text=` field; the rest is routing, safety, and ownership
metadata.

Typical envelope fields:

- `mobile_batch_id`: groups a batch of same-thread messages.
- `task_id`: unique task identity for ack, supplement handling, and result
  ownership.
- `risk`, `from`, `command`: route and control metadata.
- `mobile_ack`: receipt marker only.
- `mobile_result_begin` / `mobile_result_end`: final reply boundary markers.
- `legacy_required_result_markers` / `required_result_markers`: compatibility
  and enforcement fields for downstream consumers.
- `Supplement rule`: read pending supplements first, ack consumed supplements,
  then return one final reply only inside the result markers.

In other words: the envelope can be shortened for readability, but the
`text=` field and the result boundaries must stay intact.

## Long-Running Worker

- OpenClaw Gateway is the upstream inlet for Weixin messages. It must listen on
  `127.0.0.1:18789` before phone messages can enter the bridge queue. The local
  OpenClaw config must contain `gateway.mode=local`; `--allow-unconfigured` is
  only a temporary recovery flag and must not be treated as the stable baseline.
- `run-openclaw-gateway-loop.ps1`: foreground Gateway supervisor for Task
  Scheduler. It starts the Gateway when port `18789` is not listening and
  restarts after exit. Logs are written under
  `_tools\openclaw-codex\clean-install\logs`.
- `start-openclaw-gateway-hidden.ps1`: starts `run-openclaw-gateway-loop.ps1`
  once in a hidden PowerShell process.
- `install-openclaw-gateway-task.ps1`: registers `OpenClawGatewayWorker` as a
  user logon scheduled task. Use `-StartNow` to start it immediately.
- `run-worker-loop.ps1`: foreground worker runner for Task Scheduler. It keeps
  the task alive and writes three separate logs:
  `worker-loop-*.stdout.log`, `worker-loop-*.stderr.log`, and
  `worker-loop-*.lifecycle.log`.
- `start-worker-hidden.ps1`: starts `run-worker-loop.ps1` once in a hidden
  PowerShell process.
- `install-worker-task.ps1`: registers `MobileOpenClawBridgeWorker` as a user
  logon scheduled task. Use `-StartNow` to start it immediately.

Safety defaults:

- `mode real` enables real Codex delivery and auto reply for allowlisted,
  low-risk messages.
- `mode shadow` disables real Codex delivery.
- `mode pause` creates a PAUSE file; the worker keeps running but stops
  processing.
- `stop` is stronger than `mode pause`: it also writes `STOP_REQUEST`, switches
  to shadow mode, stops worker processes, and disables the scheduled task.
- `resume` is explicit recovery after `stop`; it does not require a secret but
  still requires the sender to be the Weixin user bound to the `primary`
  OpenClaw account slot.

## Attachment Previews

Local attachments are copied into `attachments\YYYYMMDD\` and summarized through
`_bridge\file_toolkit`. Supported preview types include text/config files, CSV,
XLSX/XLSM, DOCX, PPTX, PDF, common image formats, ZIP/JAR-like archives, and
optional XLS/OpenDocument/7z support when dependencies are installed.

Install or refresh the Python dependency set with:

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
.\_bridge\file_toolkit\install-deps.ps1
```

The file toolkit is read-only. Any future attachment editing workflow must be
separate and must create a backup before changing files.
