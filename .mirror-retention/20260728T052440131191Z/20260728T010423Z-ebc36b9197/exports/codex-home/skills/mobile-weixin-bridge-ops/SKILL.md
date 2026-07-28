---
name: mobile-weixin-bridge-ops
description: OpenClaw Weixin mobile bridge operations for this Windows Codex workspace. Use when diagnosing or changing phone-to-Codex message delivery, worker loops, task queue status, Weixin replies, attachments, stop/resume/status commands, CDP delivery to Codex Desktop, OpenClaw Gateway, or mobile bridge safety rules.
---

# Mobile Weixin Bridge Ops

Use this skill when the user interacts with Codex through Weixin or when the
mobile bridge behaves unexpectedly. Keep the bridge stable before adding new
features.

## Baseline Checks

1. Run the bridge stability check before changing behavior.
1. Treat "update baseline" as updating the project progress checkpoint: what
   state is currently verified, what changed, what evidence backs it, and what
   remains. Put reusable lessons and operating rules in skills, README, or tool
   registry separately; do not confuse those rule updates with the baseline
   itself.
2. Run `maintenance summary` or `maintenance inspect` before and after
   nontrivial changes. `maintenance summary` is quick by default and skips deep
   CDP/MCP/GUI/scheduled-task probes; use `maintenance summary --deep`,
   `inspect`, or `doctor` when route/MCP/tool evidence matters. Use maintenance
   output as the shared state model for accounts, routes, active tasks, reply
   backlog, and safe repair boundaries.
3. Check these layers separately:
   OpenClaw Gateway, local worker, SQLite queue, codex-app-server, Codex CDP,
   reply sender.
4. Read concise health output before opening long logs.
5. Treat `status`, `stop`, and `resume` as worker-level control commands where
   possible, not ordinary Codex prompts.
6. Treat mobile `repair` as the total computer maintenance shortcut: it should
   start the safe system maintenance boundary asynchronously
   (`performance_maintenance_job.py --apply-safe`) with trigger audit metadata,
   then reply quickly. The background job owns lock/cooldown, execution records,
   and safe repairs. Use `repair bridge ...` or `/repair_bridge ...` for the old
   Weixin bridge scoped repair modes.
7. For app-server work, verify turn creation/readability and scheduler state
   separately; a readable turn does not prove fair scheduling or UI visibility.
   App-server restart/recovery must also prove queue idle, no supplement wait,
   no recent bridge delivery/recovery events, and a successful recent-activity
   probe; probe failure fails closed. For
   `protocol_violation_no_owned_result`, check app-server owner churn and
   maintenance restart records before blaming the reply prompt/protocol alone.
8. For reply bugs, distinguish sender acceptance, phone-visible confirmation,
   retryable Weixin failure, and dashboard display state before changing retry
   logic.
   For direct mobile control commands such as `repair`, `status`, `stop`,
   `resume`, and `repair bridge`, do not treat the control action event as a
   completed user response. Require the receipt contract:
   `receipt_id` on the action, `control_reply_outbox_created`, then either
   `control_reply_sent` or `control_reply_failed`. Use
   `control-receipt-contract-check` and maintenance `control_replies` metrics
   to validate this without sending real Weixin messages. Historical
   pre-contract events without `receipt_id` are legacy evidence, not current
   contract failures.
9. For tool availability questions, run `tool-registry-health` before making
   broad claims about local tools, MCP, app-server, or CDP.
10. For mobile delegation prompt changes, keep the prompt contract compact and
    testable. Use `prompt_schema=mobile-openclaw-final-reply/v2`, compact
    `rules={...}` and `supplement_contract={...}` blocks, and reference the
    permission table instead of embedding full capability lists or long
    fallback command prose in every task. Preserve exact ack/result markers,
    execute-before-result semantics, permission-table authority, supplement
    get/ack twice, and fail-closed fallback behavior. Validate with
    `mobile-execution-contract-check`, `mobile-permission-prompt-compact-check`,
    and `supplement-cli-fallback-check` before refreshing the live worker.
11. For MCP failures, separate current-session transport health from process
    and config health. If a tool reports `Transport closed`, run
    `mcp-session record-observation --profile <name> --status transport_closed`
    first, then run `mcp-session doctor` or `mcp-session repair-plan`. The
    live model-session failure is evidence that later external checks cannot
    infer by themselves. Prefer profile-specific fallback for the current
    task; do not auto-kill protected bridge/Reasonix MCPs or claim a full
    Codex restart is required unless the session layer actually points there.
    If a tool reports `unsupported call`, missing dispatch, missing tool,
    unexposed/unbound tool, or schema/protocol mismatch, record `tool_unbound`
    or `schema_mismatch` the same way. For mobile supplement checks, do not
    jump straight to local fallback. When the active session tool is closed,
    missing, unbound, unsupported, or cannot be dispatched, first run
    `mcp-session complete-route` for `mobile-openclaw-bridge` and the specific
    supplement tool. That records the current-turn negative observation and
    tries the Hub/fresh-stdio route under the same permission boundary. Use the
    local `supplement-fallback get-pending-batch` / `ack-message` commands for
    the current task only after that route reports a same-boundary blocker or
    profile fallback command. For
    CodeGraph specifically, immediately continue through
    `codegraph-fallback explore` because its local CLI output is equivalent to
    the MCP explore tool; keep the MCP session issue open separately. In
    `mcp-session repair-plan`, `health_command` is only a bounded probe for the
    fallback route; `command` / `commands` on `use_fallback_for_current_task`
    are the actual task-continuation commands.
    For owned local stdio MCPs such as sqlite and custom slash commands, the
    fallback must be a fresh stdio `mcp-session tool-call`, not just a smoke
    test. A smoke test proves server readiness; it does not perform the user's
    task and must not be reported as a completed fallback. The fresh stdio
    fallback may retry once with a new process for transient
    `initialize_response_missing` or `tool_call_response_missing`; it must not
    retry real tool errors, schema errors, or missing commands.
    MCP readiness must be checked by layers: config, process, protocol
    initialize, `tools/list`, and active Codex session exposure. Use
    `mcp-session smoke --profile <profile>` for protocol/tools-list evidence.
    The smoke probe must use sequential MCP handshake semantics:
    send `initialize`, wait for its response, then send
    `notifications/initialized`, then request `tools/list`. Piping all
    messages at once can falsely report CodeGraph or other stricter stdio MCPs
    as missing tools.
    If smoke passes but the active session still lacks the tool, classify the
    fault as a Codex session exposure/binding issue and use fallback without
    blaming the MCP server itself.
    A successful real MCP call in the active Codex turn is positive current-turn
    evidence, not just service health. Before final reporting after tool-layer
    work, record it with `mcp-session record-observation --status
    tool_available --source current-codex-turn` for each profile actually
    called, or batch all real successful calls with
    `mcp-session record-observations --items-json <json-array>`. Do not record
    `tool_search` discovery, `codex mcp list`, fallback success, or protocol
    smoke as current-turn callability; those belong to
    discovery/config/protocol layers only. Use `protocol_ok` for successful
    initialize/tools-list smoke evidence. Validate this boundary with
    `mcp-session batch-recording-contract-check` after changing the recorder.
    For local stdio MCP wrappers, `mcp_launch_guard` must only serialize the
    short prelaunch section. Do not hold a guard lock for the MCP server
    lifetime and do not treat an existing stdio MCP process as reusable by a
    later Codex session; each session needs its own child process and pipe.
    The stdio supervisor must proxy newline-delimited JSON-RPC messages line by
    line and flush every line; large blocking reads can cause the fallback to
    receive `initialize` but miss later tool calls. After client stdin EOF, the
    supervisor should close child stdin, wait briefly, and clean up the child
    tree only if it stays alive.
    A short-lived stdio MCP launch wave after `tool_search`, protocol smoke,
    Codex restart, or current-turn probing is not by itself a leak. Wait for the
    configured age gate and re-run `resource-process metrics/cleanup` before
    applying cleanup. Treat fanout as persistent only when it remains after the
    supervisor grace window and age gate, or when a fresh current-turn negative
    observation points to a retained dead transport.
    Duplicate/orphan cleanup belongs to resource-process governance after
    dry-run evidence, not routine MCP launch.
    `mcp-session doctor` must distinguish current risk from historical
    evidence: stale current-turn negative observations are retained for
    forensics, but only fresh unsuperseded negatives should create risk issues.
    For resource/MCP process fanout, run `resource-process cleanup` first as a
    dry-run and apply only revalidated non-protected orphan roots after the age
    gate. Cleanup must be idempotent: if `taskkill /T` already removed a
    sibling PID in the same selected tree, a later "process not found" result
    is `already_gone` and should not make the whole cleanup fail.
11. For Defender/CFA performance pressure, use `defender-governance` as the
    persistent repair boundary. It owns dynamic Codex executable allow-listing,
    Codex/WebView cache exclusions, Codex资源库 maintenance paths, and bridge
    runtime/index exclusions. It must back up Defender preferences before
    apply, and it must not disable real-time protection or exclude broad roots
    such as the whole user profile or Downloads directory.
    If `scan_policy_ok=true` and required exclusions/CFA allow-list entries are
    present, treat recent Defender threat events or config churn as observe/report
    evidence, not as permission to repeatedly write Defender preferences.
12. For CDP diagnosis, distinguish a live listener from stale OS listener rows;
   only a live listener with a working `/json/version` response means the
   visible desktop CDP route is usable. Use `cdp-route-quick-check` for routine
   validation; reserve `cdp-route-doctor-check` for deliberate temp-only
   regression diagnosis.
11. After broad bridge work, run the controlled iteration layer before
    promoting lessons to skills or project knowledge. Prefer the maintenance
    contract entry point:
    `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance iteration`.
    The direct fallback is:
    `python _bridge\iteration_layer_review.py --json --recent-limit 12 --run-validation --validation-profile quick`.
    Use `--validation-profile full` only for deliberate deep validation or
    release-style checks, because full validation may include slow health probes.
    Before promoting a recent bridge automation lesson into a skill, project
    rule, tool table, or long-term knowledge, inspect the concrete automation
    or check it came from and run its smallest safe `--help`, dry-run,
    `validate`, or equivalent read-only proof. Promote only the reusable rule
    and its validation boundary; do not promote raw incident noise or an
    untested action path.
    Treat `proposal_groups` and `recommended_next_actions` as review output,
    not as permission to modify files. The final user-facing report after broad
    bridge/maintenance/resource/GUI/config/automation/agent work must include a
    concise proposal summary or explicitly state why the iteration gate was not
    available.
    If `maintenance iteration` returns `proposal_groups`,
    `recommended_next_actions`, or an `approval_block`, the final response must
    show the pending review/approval items in user-readable form. Do not reduce
    this to "iteration passed"; passing validation only means the proposal gate
    is healthy, not that the user has seen or approved the proposals.
12. Keep maintenance checks separate from maintenance actions. `maintenance
    summary`, `maintenance summary --deep`, `tool-registry-health`,
    `cdp-route-quick-check`, and iteration quick validation are safe
    observation routes. Review the structured `maintenance repair` dry-run
    plan before applying repair. `maintenance repair --apply`,
    `--include-reply-send`, queue mutation, route switching, and replay/send
    commands require explicit task-scoped approval and fresh pre-action
    evidence.
13. Consume the maintenance outputs by role:
    `maintenance summary` for human-readable queue/route state plus
    `Iteration Decision`; `maintenance doctor` for structured health issues plus
    read-only `advisories`; `maintenance repair` for dry-run repair plans plus
    the same `advisories`; `maintenance iteration` for the controlled
    finalization gate and concrete proposal display. Do not treat `advisories`,
    `proposal_groups`, or `recommended_next_actions` as repair permission or
    runtime commands.
14. For bridge code governance, keep the live CLI stable while reducing
    complexity. Treat `mobile_openclaw_cli.py` as a facade during incremental
    refactors. Extract pure responsibilities first, such as command parsing,
    reply text formatting, read-only repair evidence, OpenClaw account file
    lookup, JSON/time/hash helpers, and worker-loop observability. Do not move
    queue mutation, permission decisions, delivery/retry logic, supplement
    ack/drop behavior, or repair execution gates until a focused module
    boundary and regression matrix exist. Every new peer module should have a
    purpose-coupled name and a module docstring stating owns, non-goals, state
    behavior, and normal caller context. Keep old entry points as facades until
    validation passes.
15. Validate bridge refactors against the old failure class, not only syntax.
    Pair `python -m py_compile` with targeted checks such as
    `mobile-repair-command-entry-check`, `mobile-repair-specialized-modes-check`,
    `control-receipt-contract-check`, `mobile-execution-contract-check`,
    `supplement-cli-fallback-check`, `final-reply-media-text-split-check`,
    `capability-passphrase-state-machine-check`, and
    `code_maintainability.py validate` according to the touched surface. If a
    CodeGraph query misses the intended function, re-anchor with concrete file
    and function names or use local AST/targeted reads; do not let a misleading
    query drive edits.
    For large `mobile_openclaw_cli.py` refactors, choose the business boundary
    and regression boundary together. Prefer low-state-risk modules first:
    config loading, parsing, formatting, read-only diagnostics, resource
    descriptors, client wrappers, and auditable envelope builders. Keep
    production protections default-on; use explicit temp-only or test-only
    allowlists for regression isolation. Do not split `worker_once`, supplement
    lifecycle, owned-result recovery, permission decisions, or reply sending
    until the module contract, old-mechanism purpose, and focused regression
    matrix are clear. If CodeGraph is stale, disabled, or query drift occurs,
    record a one-shot work note and continue with local AST/targeted reads
    instead of letting an unreliable index drive edits.
    After CodeGraph sync or query-route work that touches bridge refactors, run
    `python _bridge\codegraph_health.py validate --json` and require the
    bridge-specific relevance smokes to pass before treating CodeGraph as a
    reliable bridge-editing aid again.
    Temp-only regression checks must isolate unrelated live maintenance paths
    before using elapsed-time assertions. If a check is meant to validate thread
    routing, prewarm, dispatch fallback, or probe behavior, explicitly disable
    worker onboarding sync, current-session MCP gates, real app-server creation,
    or other unrelated external probes unless that path is the subject of the
    check. Keep those production protections enabled outside the fixture.
16. For dashboard shortcut failures, validate the human entry path, not just
    the service. Exercise `C:\Users\45543\Desktop\微信桥接面板.lnk`, inspect
    `_bridge\mobile_openclaw_bridge\runtime\dashboard_open_last.log`, and
    confirm a visible browser window. Do not close the issue based only on
    `Invoke-WebRequest`, direct `open-dashboard.ps1`, or `-NoOpen` success.
17. The dashboard shortcut must keep bootstrap duties but open the visible
    dashboard before non-essential backend repair when `18808/api/state` is
    already healthy. App-server, login-service, or live-watcher repair failures
    should be logged and surfaced, not allowed to block manual panel opening.

## Safety Rules

- Only authenticated/whitelisted Weixin users may trigger Codex work.
- Admin is the bridge superuser. The Weixin user bound to the `primary` OpenClaw account owns all defined permissions and currently unspecified future permissions; unknown admin actions should be allowed with audit and normal risk/confirmation controls.
- `/ask` is whitelist-only for normal mobile users: non-sensitive questions, processing data supplied by the user in the current request, and explicitly requested external public resources. It must not be treated as a wildcard for local-machine reads, local data export, local data modification/deletion, system diagnostics, secrets, repairs, or other local side effects.
- Permission checks for `/ask` must run in the execution layer before Codex dispatch, not only in prompts. Obvious denial guards apply to non-admin users; admin should not be rejected by the ordinary-user ask whitelist and should instead be audited when a request is sensitive, local-machine-affecting, or currently unspecified.
- Temporary capability tokens are admin-granted, expiring, and non-renewing without a new admin grant. For normal users they may only add narrow generated-artifact abilities such as creating/sending new generated files inside `attachments/generated/<account_id>/`; they must never grant local data read/export/write, secrets, repairs, installs, process control, or other destructive/system side effects.
- Global controls such as `status`, `stop`, `resume`, `hardstop`, and high-risk
  confirmation are admin-only. Admin privilege is determined by the OpenClaw
  account slot: the Weixin user currently bound to the `primary` slot is admin.
- Conversation thread selection and continuation windows are keyed by Weixin
  user identity, not by OpenClaw account slot.
- Reply delivery should use the task's `receiver_account_id` when present, and
  fall back to `openclaw.account_id` only for legacy tasks.
- Reply delivery semantics must stay separated:
  `ret=-2`, missing context, send circuit, and actual send failures are
  retryable; `delivery_accepted=true` with `phone_visible_confirmed=false` is
  accepted-but-unproven and must be recorded once, audited, and not auto-resent.
- Final replies with both text and an attachment/media file must be split into
  separate text and media sends. A media receipt does not prove text delivery.
  Require separate `final_reply_text_accepted` and
  `final_reply_media_accepted` evidence before treating the combined reply as
  complete.
- Deduplicate messages by stable task identity or content source.
- Keep status acknowledgements semantically exclusive. A message consumed as a
  supplement should only receive the supplement-stored acknowledgement; it must
  not also emit dispatching or dispatched acknowledgements.
- Reserve dispatching/dispatched acknowledgements for primary Codex deliveries.
  Do not use them for same-thread supplements, attachment supplements, or MCP
  supplement acknowledgements.
- Same-thread pending backlog supplements may emit the supplement-specific
  `status_ack_pending_backlog_supplement` acknowledgement so the user sees that
  the follow-up was received and attached to the active owner. This is distinct
  from normal dispatching/dispatched/waiting acknowledgements, which must stay
  suppressed for supplement tasks.
- For app-server delivery, mobile result ownership depends on the exact
  `mobile_ack`, `mobile_result_begin`, `mobile_result_end`, and task id markers.
  Visible input or visible assistant text is diagnostic evidence only; it must
  not be used as the ownership source for retry, completion, or redelivery.
  Use durable receipt/outbox state and owned-result markers as the authority,
  and before redelivery check whether the old expected result code already
  appeared in the thread history or bridge evidence.
- Owned-result recovery must be idempotent at the task/result-consumption layer,
  not only at the final Weixin send layer. When both live app-server polling and
  thread-history fallback can see the same final result, only one branch may
  complete the task and spawn final reply handling; later branches should record
  duplicate suppression and skip completion/send work. Validate with
  `reply-send-idempotency-check`, `historical-owned-result-fallback-check`, and
  `thread-history-owned-result-fallback-check` after changing recovery logic.
- `inProgress` is not a terminal protocol failure. If a Codex turn is still
  active, acked, or parked for same-thread follow-up, do not convert it to
  `protocol_violation_no_owned_result` merely because no owned result is visible
  yet. First check durable owned-result sources; if none are complete, keep the
  task observable and block duplicate redelivery or side effects. A
  `waiting-followup` marker blocks redelivery only; it must not block
  owned-result recovery. Validate with
  `app-server-repair-continuation-check`,
  `waiting-followup-owned-result-recovery-check`,
  `waiting-followup-owned-result-redelivery-gate-check`, and
  `protocol-violation-no-owned-result-check`.
- Treat authenticated mobile messages as normal user instructions with the same
  execution priority, autonomy, reasoning depth, verification rigor, answer
  completeness, and risk analysis as desktop messages. The mobile protocol is
  only a transport and ownership boundary. It must not downgrade a task into
  status-only reporting, shallow analysis, skipped verification, extra
  hesitation, or reduced action unless a desktop request with the same content
  would also require confirmation, refusal, or a safety boundary. A Weixin reply
  may be concise for readability, but concision must not reduce the underlying
  work, analysis quality, or completeness.
- `mobile_ack` only means the task was received. For explicit mobile commands,
  approvals, repairs, verification, continuation, GUI/file/resource operations,
  or "continue working" instructions, Codex must do the requested work before
  sending `mobile_result`. Do not use `mobile_result` for placeholder status
  such as "received", "continuing", "will do", or "working on it"; reserve it
  for completion, failure, a real blocking condition, or required confirmation.
- The text inside `mobile_result_begin`/`mobile_result_end` must be the
  Weixin-user-facing answer only. Do not place context-compaction summaries,
  handoff notes, protocol explanations, raw tool traces, or internal checkpoint
  text inside the result body unless the user explicitly requested that exact
  artifact as the Weixin reply.
- Supplement context is not implicit model context. Every mobile final-reply
  owner must actively read supplements; this is a global bridge rule for
  primary, backup, CDP, and app-server routes. Immediately after `mobile_ack`
  and before substantive work, explicitly call
  `bridge.get_pending_batch(thread_id=<active thread id>)`; incorporate all
  applicable returned supplement items; then call `bridge.ack_message` for each
  consumed supplement. Repeat `bridge.get_pending_batch` before producing the
  final mobile reply. If the tool is unavailable, fails, or still returns
  applicable unconsumed supplements, do not output a normal final answer as if
  the task were complete; surface the blocker so the bridge can retry or repair
  the same owner. A supplement is consumed only after `bridge.ack_message`
  succeeds and durable bridge evidence such as `mcp_message_acked` is present.
- If the active Codex session reports the mobile MCP tool transport as closed,
  use `mcp-session complete-route` first instead of treating the supplement
  check as impossible or jumping directly to fallback. If the gateway route is
  unavailable or reports a same-boundary blocker, the fixed profile fallback
  commands are:
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id <thread_id>`
  and
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback ack-message --thread-id <thread_id> --message-id <message_id>`.
  This fallback starts a fresh local mobile MCP server process and calls the
  same `bridge.get_pending_batch` / `bridge.ack_message` implementations. It is
  valid only when the JSON result is `ok`; ack still must report `acked` or
  `already_acked` before the supplement is considered consumed.
- Treat pre-delivery Codex Desktop CDP `generationActive=true` as advisory
  evidence, not as a hard dispatch gate. It may be stale or reflect old DOM
  state, so primary messages should still attempt normal CDP delivery unless
  blocked by durable queue ownership or an actual submission failure. Record
  the observation as `thread_delivery_visible_cdp_busy_observed`; do not publish
  it as a supplement, schedule `visible_cdp_busy` retry, or send busy status
  acknowledgements solely from this pre-delivery probe. Same-thread supplements
  still require a valid active final-reply owner and durable task status before
  they can be attached through MCP supplement flow.
- MCP supplement payloads must be validated against durable task status and
  active owner state. If the runtime payload points at completed/non-pending
  supplement rows or an inactive base owner, release/sanitize it before Codex
  can consume it.
- Do not let the worker create infinite self-triggering tasks.
- High-risk actions require the configured confirmation secret.
- `stop` should pause future delivery and attempt CDP stop of the active Codex
  turn; do not claim hard cancellation unless the CDP stop path was tested.
- Keep file attachments in the bridge attachments directory and analyze them
  with the appropriate toolkit. Attachment resources must pass through the
  resource layer at enqueue time: local files are copied into the attachment
  cache, explicit attachment URLs are downloaded only for supported HTTP/HTTPS
  schemes, sha256/size/cache metadata is recorded, and file-toolkit analysis
  preview is persisted into the attachment metadata before Codex delivery.
  Bridge callers should pass a `ResourceIntent` and let the resource layer own
  source/scheme/size/retry/cache/confirmation policy and audit metadata. Do not
  scrape arbitrary URLs from message text as implicit attachments.
- When Codex or bridge automation needs an external resource, classify the
  request before fetching: explicit user URL, inline URL candidate, package
  dependency, documentation lookup, generated output, tool output, or unknown.
  Use `_bridge\resource_cli.py probe-url`, `preview-url`, or
  `acquire --intent ... --stage ...` for project-controlled fetches or audit
  records. Existing tools may be discovered and ranked automatically, but only
  low-risk read-only use should be automatic within policy. Installing tools,
  changing persistent config, using login/session state, running package
  managers, cloning repositories, or modifying local files outside the resource
  cache still requires explicit approval and backup. Do not present this as a
  global interceptor for system tools such as `web.run`, `pip`, `npm`, `git`,
  or browser/GUI actions; those routes still need explicit judgment and should
  not silently download arbitrary message-text URLs.
- Resource acquisition strategy may evolve from observed outcomes, but only
  through read-only proposal review. Use `_bridge\resource_cli.py
  strategy-review` to summarize resource JSONL observations and propose safer
  category-specific routes. The review must not fetch resources, execute
  tools, install packages, clone repositories, mutate policy, or write files.
  Promote a proposal only after user approval, backup, and regression tests.
- Before installing external tools, inventory already available capabilities:
  `tool-registry-health`, `load_workspace_dependencies`, OCR venvs, and
  `_bridge\script_inventory.py --json`. Prefer existing bundled or project
  tools when they cover the task. If installation is approved, install one
  package at a time, verify the command, and update the registry only after
  validation.
- For package downloads, probe known direct artifact URLs first when a package
  is GitHub-release-backed. If `probe-url` times out or package-manager
  download stalls, stop that package, record the partial state, and switch to a
  verified alternate source or skip it for the current run. Do not repeatedly
  retry the same failing `winget`/`choco` route.
- For Python-package tools, probe candidate indexes with short
  timeouts/retries before installing. In this Windows workspace, Aliyun PyPI was
  the fast route for `pytest`, `ruff`, `uv`, and `yt-dlp` after GitHub/winget
  routes stalled. Prefer `python -m <module>` when per-user script shims are not
  on `PATH`; do not mutate global/user PATH just to complete a tool install.
- A degraded maintenance summary is not by itself permission to repair. First
  classify the issue: active current task, historical reply backlog, stale
  listener row, optional tool missing, or actual route failure. Historical
  reply backlog and stale OS listener rows should be reported unless the user
  explicitly asks for cleanup or repair.
- `tool-registry-health` is the preferred bounded check before making claims
  about local bridge-related tool ability or before promoting a new bridge
  operating rule into long-term knowledge.
- `resource-layer-smoke-check` belongs to the resource-layer validation path:
  use it to verify bounded acquisition behavior before promoting resource
  strategy lessons into skills, docs, or CLI guidance.

## Common Commands

Run from the workspace root:

```powershell
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py stability-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py tool-registry-health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-cli-fallback-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mobile-execution-contract-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mobile-permission-prompt-compact-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance summary
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance summary --deep
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance repair
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session record-observation --profile codegraph --status transport_closed --source current-codex-session --detail "tool call returned Transport closed"
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session record-observation --profile codegraph --status tool_unbound --source current-codex-session --detail "tool call returned unsupported call"
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session record-observations --items-json "[{\"profile\":\"codegraph\",\"status\":\"tool_available\",\"source\":\"current-codex-turn\",\"detail\":\"active MCP call returned successfully\"}]"
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session batch-recording-contract-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session repair-plan --observe codegraph:transport_closed --run-fallback
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session validate
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session tool-call --profile custom-slash-commands --tool slash.validate_registry
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session tool-call --profile sqlite-scratch --tool sqlite_health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py codegraph-fallback explore --max-files 4 mcp_session_doctor record_observation
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py defender-governance doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py defender-governance repair-plan
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py defender-governance apply
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py defender-governance validate
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py stuck-tasks
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py fair-scheduling-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py thread-busy-status-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py app-server-sync-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py app-server-repair-continuation-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py protocol-violation-no-owned-result-check
powershell -NoProfile -ExecutionPolicy Bypass -File _bridge\shared\restart-bridge-appserver-if-idle.ps1 -Mode dry-run
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py active-visible-cdp-supplement-publish-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py cdp-route-quick-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py cdp-route-doctor-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py final-reply-visibility-unconfirmed-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py reply-send-idempotency-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py historical-owned-result-fallback-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py thread-history-owned-result-fallback-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py final-reply-media-text-split-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-layer-smoke-check
python _bridge\resource_cli.py probe-url https://example.com --json --no-log
python _bridge\resource_cli.py preview-url https://example.com --preview-bytes 4096 --json --no-log
python _bridge\resource_cli.py acquire --intent explicit_user_url --stage probe --url https://example.com --json --no-log
python _bridge\resource_cli.py strategy-review --json
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mode status
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance iteration
python _bridge\iteration_layer_review.py --json --recent-limit 12 --run-validation --validation-profile quick
python _bridge\iteration_layer_review.py --json --recent-limit 12 --run-validation --validation-profile full
Get-Content _bridge\mobile_openclaw_bridge\runtime\dashboard_open_last.log -Tail 30
```

## References

Read `references/core.md` for current ports, task states, recovery notes, and
reply-state semantics.

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.


