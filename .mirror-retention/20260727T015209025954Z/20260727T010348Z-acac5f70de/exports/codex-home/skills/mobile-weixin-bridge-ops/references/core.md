# Mobile Weixin Bridge Reference

## Local Baseline

- OpenClaw Gateway: `127.0.0.1:18789`
- codex-app-server: `127.0.0.1:18791`
- Codex Desktop CDP: `127.0.0.1:9229`
- Worker scheduled task: `MobileOpenClawBridgeWorker`
- Worker script:
  `_bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py`
- Shared queue module:
  `_bridge\mobile_wecom_bridge\mobile_queue.py`
- Attachments:
  `_bridge\mobile_openclaw_bridge\attachments\`

## Current Delivery Baseline

- The `primary` account uses the Codex Desktop visible-window CDP route by
  default, so phone input should appear in the current desktop Codex thread.
- Backup accounts use the `codex-app-server` background route by default. A
  primary CDP failure must stay primary-scoped and must not block backup
  app-server dispatch.
- Do not silently switch `primary` between CDP and app-server. That is a
  manual-only boundary requiring explicit user approval and regression checks.
- CDP probe failure is transient and is not the same as generation busy.
- Pre-delivery `generationActive=true` is advisory only. It may be stale or
  reflect old DOM state, so it must record
  `thread_delivery_visible_cdp_busy_observed` but must not by itself block
  dispatch, schedule ordinary `visible_cdp_busy` retry, publish a supplement,
  or send a busy acknowledgement. Normal CDP delivery should proceed unless
  durable queue ownership or actual submission failure says otherwise.
- Same-thread supplement publishing is reserved for messages that can be tied
  to a valid active final-reply owner with durable task evidence. Supplements
  must still go through `bridge_supplement:<thread_id>` and
  `bridge.get_pending_batch`, but a weak pre-delivery busy signal alone is not
  enough to create supplement identity.
- CDP diagnostics should distinguish transport failures: `transport_down`,
  `listener_unresponsive`, and `stale_os_listener`. A stale OS listener means
  Windows reports port 9229 LISTEN rows whose owner PIDs no longer exist; this
  usually needs Codex Desktop or machine/network-stack restart rather than
  repeated start-script retries.
- CDP recovery must reuse the configured elevated Codex Desktop startup path:
  `start-codex-desktop-elevated.ps1` with `CODEX_CDP_PORT` for the selected
  port. Do not recover the primary visible route by launching a plain
  non-admin Codex process; that can lose the permission baseline the user
  explicitly depends on.
- The app-server route has verified turn creation, turn readability, Desktop
  thread hydration, and resume evidence through `app-server-sync-check`, but
  UI visibility is separate from background turn dispatch.

## Important States

- `pending`: task is waiting to be processed.
- `queued_for_codex`: selected for Codex delivery.
- `sent_to_codex`: delivered to a Codex thread but result may not yet be
  captured.
- `pushed_to_wecom`: response was pushed back to Weixin.
- `codex_timeout` or `failed`: investigate before retrying.

## Reply Delivery Semantics

Keep these states distinct. Mixing them caused repeated backup-account replies.

- Sender accepted, phone visibility proven:
  `ok=true` and `phone_visible_confirmed=true`; mark pushed.
- Sender accepted, phone visibility unknown:
  `delivery_accepted=true` but `phone_visible_confirmed=false`; mark pushed once
  with a visibility-unconfirmed audit event. Do not auto-resend.
- Retryable Weixin failure:
  `ret=-2`, missing context token, send circuit open, or sender failure; keep or
  move to reply recovery state and retry only through the scoped retry policy.
- Dashboard/UI state:
  display freshness does not prove phone delivery and must not drive resend
  decisions.

Regression commands for this boundary:

```powershell
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py final-reply-visibility-unconfirmed-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py final-reply-visibility-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py reply-dedupe-policy-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py reply-pending-account-scope-check
```

## Account And Thread Routing

- OpenClaw account slots determine control authority. The `primary` slot is the
  admin slot; the Weixin user currently bound in `primary.json` may use global
  controls.
- Weixin conversation continuity is user-based. Runtime keys for active thread
  and continuation windows should remain keyed by `external_user`, so a user's
  conversation follows that Weixin identity even if account-slot binding changes.
- Tasks may carry `receiver_account_id`; Weixin replies should use that account
  first and fall back to `openclaw.account_id` for legacy tasks.
- Message fingerprints include `receiver_account_id` to avoid cross-account
  collisions when multiple OpenClaw accounts are active.

## Recovery Order

1. Run `stability-check`.
2. Run `maintenance summary` to identify global stop state, per-account backlog,
   active tasks, reply backlog, and safe repairs. The default summary is quick
   and explicitly marks deep CDP/MCP/GUI/scheduled-task probes as skipped; use
   `maintenance summary --deep`, `inspect`, or `doctor` before making route,
   MCP, GUI, or scheduled-task conclusions.
3. Check whether `PAUSE` or `STOP_REQUEST` exists.
4. Check worker process count and scheduled task state.
5. Check app-server sync evidence with `app-server-sync-check`.
6. Check scheduler evidence with `fair-scheduling-check` and
   `thread-busy-status-check`.
7. Check latest stderr; avoid dumping large stdout logs.
8. Check `maintenance summary` CDP Route before assuming CDP is simply down. If
   it reports `stale_os_listener`, repeated start-script retries are not enough;
   clear the stale 9229 listener by restarting Codex Desktop or the
   machine/network stack.
9. Check CDP `/json/version` and OpenClaw port only when fallback or desktop
   control is involved.
10. For stuck `sent_to_codex` tasks, inspect active Codex thread state before
   marking failed or resetting to pending.
11. After repairs that expose reusable rules, run the controlled iteration
    layer and review grouped proposals before editing skills or docs:
    `python _bridge\iteration_layer_review.py --json --recent-limit 12 --run-validation`.

## Controlled Iteration And Validation

- The iteration layer is read-only by default and must remain proposal-only:
  it may identify skill, tool-registry, project-knowledge, and CLI automation
  candidates, but it must not write those destinations without explicit user
  approval and a marked backup.
- Use `proposal_groups` to distinguish established safety gates, CLI
  automation candidates, tool-registry review, and project-knowledge review.
- Use `recommended_next_actions` as an ordered review checklist, not as
  automatic permission.
- Bridge-specific validators currently promoted for iteration review are:
  `event-noise-coalescing-check`, `codex-log-sqlite-health`,
  `reply-dedupe-policy-check`, `cdp-route-doctor-check`, and
  `route-fallback-dispatch-check`.
- These validators are checks only. Do not replace them with repair, reply
  sending, task cleanup, or route switching unless the user explicitly approves
  that separate action.

## Maintenance System

- Prefer `maintenance repair` dry-run before manual DB/state edits.
- `maintenance summary` is quick by default. Treat skipped deep probes as
  unknown rather than healthy or failed. Use `maintenance summary --deep` for
  complete CDP/MCP/GUI/scheduled-task evidence.
- `maintenance repair` dry-run includes a structured plan describing
  preconditions, possible mutations, backup/rollback notes, validations, and
  whether Weixin sending is possible. Review that plan before `--apply`.
- `maintenance repair --apply` may perform safe local repairs only within the
  reported policy. It must not send reply-pending messages unless
  `--include-reply-send` is explicitly passed.
- Historical visibility-unconfirmed reply-pending tasks may be reconciled as
  accepted without sending Weixin messages. This is for old tasks created before
  the accepted-but-unproven semantic was fixed.
- Do not clear `PAUSE`, `STOP_REQUEST`, enable scheduled tasks, reset active
  tasks, or change slot bindings as part of repair unless the user explicitly
  approved that action.
- `reply_sending` is a transient lease state for an already generated final
  reply being sent to Weixin. Expired leases may be recovered by maintenance,
  but accepted-without-phone-visibility tasks should be reconciled once instead
  of resent repeatedly.
- Event/log cleanup and SQLite VACUUM are separate maintenance activities:
  do them only in a quiet window, with a backup, and with event-noise checks
  passing first.

## Attachment Handling

- Attachment resources are materialized at enqueue time. Local files are copied
  through `_bridge/resource_fetcher.py` into the dated attachment cache.
  Explicit attachment URLs are downloaded only for HTTP/HTTPS schemes, with the
  bridge attachment size cap, timeout, retry, sha256 validation when provided,
  and JSONL resource logging.
- Resource acquisition policy belongs in `_bridge/resource_fetcher.py`.
  Callers should classify the resource with a `ResourceIntent`; the resource
  layer decides whether to allow, block, defer for confirmation, or degrade with
  metadata. Keep implicit message-text URLs as `inline_url_candidate` and do not
  auto-download them.
- Use `_bridge\resource_cli.py acquire --intent ...` as the project-controlled
  entrypoint for resources outside ordinary attachments. Supported intent
  classes include explicit attachment, explicit local file, explicit user URL,
  inline URL candidate, external dependency, package dependency, documentation
  lookup, generated output, tool output, and unknown. Package dependencies and
  documentation lookups are policy/audit classifications first; package
  managers, MCP docs tools, web search, browser actions, and GUI actions are
  not globally intercepted by the resource layer.
- Network acquisition is staged. Prefer `probe-url` or `preview-url` before
  materialization when freshness or source safety is unclear. `probe` records
  status, content type, content length, final URL, and redirect state without
  downloading the body. `preview` fetches only a bounded sample. `materialize`
  is for policy-approved cache writes. Existing tools may be detected and
  ranked automatically, but only low-risk read-only use is automatic within
  policy; installing tools, changing persistent config, using login/session
  state, package-manager operations, repository clones, and writes outside the
  cache require explicit approval and backup.
- Resource strategy evolution is read-only and proposal-only. Use
  `_bridge\resource_cli.py strategy-review` to inspect recent resource JSONL
  outcomes and suggest category-specific routes, such as docs lookup before
  preview or probe before materialization. The strategy review must not fetch
  resources, invoke external tools, install packages, clone repositories, mutate
  policy, or write promotion files. A proposal becomes a policy only after
  explicit approval, backup, and regression validation.
- Tool completion starts with existing capability discovery, not installation.
  Check `tool-registry-health`, Codex bundled workspace dependencies, project
  OCR venvs, and `_bridge\script_inventory.py --json` before adding packages.
  Update static registry notes when live health proves tools already exist.
  Install new tools only as separate approved package-manager actions with
  validation and rollback notes.
- For GitHub-release-backed package candidates, preflight with resource-layer
  `probe-url` when the direct artifact URL is known. A probe timeout or stalled
  package-manager download means the current route is unhealthy; stop that
  package, record any dependency that did install, and use a verified alternate
  source or defer. Do not repeatedly retry the same failing route.
- For Python-package tool candidates, treat mirror selection as a resource
  strategy decision: run a short `pip index versions` probe first, choose the
  fastest healthy mirror, install one package at a time, and validate through
  `python -m ...` when script shims are outside `PATH`. On 2026-06-25, Aliyun
  PyPI was the verified fast mirror for `pytest`, `ruff`, `uv`, and `yt-dlp`.
- Attachment metadata should carry `resource_status`, `resource_source`,
  `resource_decision`, `resource_policy`, `resource_policy_reason`,
  `resource_next_action`, `stored_path`, `sha256`, `size`,
  `resource_cache_hit`, `resource_error`, `analysis_kind`, `analysis_ok`, and
  `analysis_preview` when available. Codex prompts should prefer this persisted
  metadata over repeated ad-hoc analysis.
- Resource failures should be visible but should not block a text task from
  entering the queue. Failed resources must show the reason in the prompt.
- Do not scrape arbitrary URLs from message text as implicit attachments.
  Process only explicit attachment/resource fields unless the user asks for URL
  fetching as a separate task.
- Images: inspect visually when the prompt asks about image content.
- Text/document/spreadsheet/slides/PDF/audio: route to the relevant toolkit
  after resource materialization.
- Do not store secrets or full private attachments in vector memory.

## User Experience

Send concise state feedback for phone tasks: received, queued, delivered,
processing anomaly, recovered, completed, or rejected. Avoid repeated anomaly
messages for the same stuck task until recovery is detected.

Supplement messages have separate UX semantics:

- A message consumed as a same-thread supplement may keep the initial
  `status_ack_received` acknowledgement so the user knows the bridge received
  it, but after it is identified as supplement context it must not continue
  through the normal delivery acknowledgement flow.
- Supplements must not emit visible `dispatching` or `dispatched` status
  acknowledgements.
- Supplements must also suppress normal queue/retry/waiting acknowledgements
  such as `status_ack_delivery_queue_entered`, `status_ack_dispatching`,
  `status_ack_dispatched`, and `status_ack_waiting`.
- Supplement-specific notices, such as `status_ack_continuation_deferred`,
  `status_ack_attachment_supplement`, or
  `status_ack_pending_backlog_supplement`, are allowed and should be
  de-duplicated by task identity or active-turn content signature.
- Same-thread supplements must keep supplement identity. They must not receive
  normal dispatching/dispatched acknowledgements, must not be promoted to
  ordinary delivery while their active owner can still consume them, and must
  fall back to ordinary delivery/retry only when no valid active final-reply
  owner exists or supplement publish fails. Do not classify a primary message
  as a supplement from pre-delivery `generationActive=true` alone.
- A primary message may receive a received acknowledgement and at most one
  processing/delivered acknowledgement.
- Status acknowledgement dedupe should be keyed by task identity and semantic
  stage so nearby stages do not look like duplicate phone replies.
- Stale supplement runtime payloads are unsafe context. `bridge.get_pending_batch`
  should return only supplements that are still pending/consumable and whose
  base owner is still a valid active supplement host.
