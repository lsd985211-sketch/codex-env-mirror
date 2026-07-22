# Codex Tool Registry

Date: 2026-06-27
Scope: local `mcsmanager` Codex workspace and mobile OpenClaw bridge.
Status: draft, generated from read-only inspection.

## Operating Policy

- Prefer purpose-built MCP tools and project CLIs before raw shell commands.
- Use PowerShell as the default local shell on Windows.
- Use `apply_patch` for manual file edits.
- Before modifying existing local files, ask for approval and create a marked backup.
- Before modifying this registry, run both
  `tool-registry-health` and `tool-registry-drift-check`; update static text
  only from fresh live evidence, and do not treat registry text as the source
  of truth when live health disagrees.
- Treat CDP as a visible-desktop enhancement route, not the only reliable delivery path.
- Treat app-server as the stable background delivery path for backup accounts.
- Supplements must be read with `bridge.get_pending_batch` and consumed with `bridge.ack_message`.
- If the current Codex session reports the mobile MCP tool transport as closed,
  first complete the same-boundary MCP failure route, then use the local
  fallback commands only if the gateway route cannot complete the task:
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session complete-route --profile mobile-openclaw-bridge --tool bridge.get_pending_batch --status transport_closed --arguments-json "{\"thread_id\":\"<thread_id>\"}"`.
  This records the current-turn negative observation and tries the Hub/fresh
  stdio route before profile fallback. Do not jump directly to:
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id <thread_id>`
  and
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback ack-message --thread-id <thread_id> --message-id <message_id>`.
  The fallback launches the same mobile MCP server over a fresh local stdio
  process, so ack writes the same durable evidence as `bridge.ack_message`.
- Controlled iteration output must be treated as proposals until the user
  approves a specific persistent update and backup.

## Tool Classes

### 1. MCP Tools

| Tool group | Purpose | Current notes |
| --- | --- | --- |
| `mcp__agent_bridge` | Codex/Reasonix task coordination and shared knowledge | Available; check `agent_bridge_receive` and `reasonix-notify` at turn start. |
| `mcp__mobile_openclaw_bridge` | Weixin supplement polling and ack | Available; direct smoke is OK. |
| `mcp__node_repl` | Persistent JavaScript runtime | Available for JS/browser automation support. |
| `mcp__gui_automation` | Windows GUI automation | Available after Codex restart when the MCP host loads the registered `gui-automation` server. |
| `mcp__context7` | Current library/framework/API documentation lookup | Available and smoke-tested; use before web search for library, SDK, CLI, and cloud-service documentation. |
| `mcp__markitdown` | Convert file/http/data resources to Markdown | Available and smoke-tested; use for document/content conversion before ad hoc parsers when markdown output is sufficient. |
| `mcp__chrome_devtools` | Direct Chrome/CDP page inspection and browser control | Available and smoke-tested; prefer for existing Chrome pages and CDP-level inspection. |
| `mcp__playwright` | Browser automation and page-level testing | Available and smoke-tested; prefer for controlled webpage automation and E2E-style checks. |
| `mcp__github` | GitHub repository, issue, PR, and authenticated account operations | Available and smoke-tested for read/auth. Treat writes as explicit user-approved actions. |
| `mcp__myskills` | MySkills skill inventory, health, discovery, and gated maintenance | Available; use for skill-library management with plan-before-write and explicit confirmation for gated changes. |
| `mcp__microsoftdocs` | Microsoft Learn documentation lookup | Registered in the Codex baseline; use for Microsoft platform docs when available in the live tool surface. |

### 1.1 Codex Plugins

Plugin state is session- and restart-sensitive. Use `codex plugin list` for
the live baseline before changing plugin assumptions.

| Plugin | Status | Purpose |
| --- | --- | --- |
| `browser@openai-bundled` | installed, enabled; version `26.616.71553` | Primary in-app browser control surface for local web/browser testing. |
| `build-web-apps@openai-api-curated` | installed, enabled; version `3c06cb2e` | Frontend app guidance, browser-testing workflows, and `frontend-testing-debugging` skill after Codex restart loads plugin skills. |
| `build-web-data-visualization@openai-api-curated` | installed, enabled | Web data visualization guidance and artifacts. |
| `canva@openai-curated` | installed, enabled | Canva workflow skills. |
| `chrome@openai-bundled` | installed, enabled; version `26.616.71553` | Chrome control when existing Chrome state is required. |
| `computer-use@openai-bundled` | installed, enabled; version `26.616.71553` | Desktop automation fallback for non-browser GUI work. |
| `game-studio@openai-curated` | installed, enabled | Browser game design, implementation, and playtest skills. |
| `hyperframes@openai-api-curated` | installed, enabled; version `3c06cb2e` | HTML/video workflow plugin. |
| `mixpanel-headless@openai-api-curated` | installed, enabled | Mixpanel headless analytics workflows. |
| `remotion@openai-api-curated` | installed, enabled; version `3c06cb2e` | Remotion video workflow plugin. |

Plugin installation policy:

- Install or verify plugins through `codex plugin list` and
  `codex plugin add <plugin>@<marketplace>`, not by manually copying plugin
  cache directories.
- Back up `C:\Users\45543\.codex\config.toml` and relevant global state before
  plugin baseline changes.
- A newly installed plugin may not expose skills in the current running Codex
  session until Codex is restarted.

Health check:

```powershell
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py tool-registry-health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py tool-registry-drift-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-cli-fallback-check
python _bridge\tool_exposure_doctor.py doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance summary
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance iteration
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py p0-audit
python _bridge\iteration_layer_review.py --json --recent-limit 12 --run-validation
python _bridge\memory_governance.py validate
```

### 2. Bridge Runtime Tools

| Path | Purpose | Status |
| --- | --- | --- |
| `_bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py` | Main mobile bridge CLI and worker logic | Present |
| `_bridge\mobile_openclaw_bridge\mobile_bridge_mcp_server.py` | Mobile MCP server | Present |
| `_bridge\mobile_openclaw_bridge\start-worker-hidden.ps1` | Hidden worker scheduled-task launcher | Present |
| `_bridge\mobile_openclaw_bridge\run-worker-loop.ps1` | Worker loop runner | Present |
| `_bridge\mobile_openclaw_bridge\start-openclaw-gateway-hidden.ps1` | OpenClaw Gateway launcher | Present |

Current state policy:

- Do not trust static registry text as live bridge state. Run
  `tool-registry-health` or `maintenance summary` before diagnosing current
  OpenClaw Gateway, worker, app-server, MCP, CDP, queue, or route status.
- CDP state is especially drift-prone. Distinguish live listeners from stale OS
  rows, and treat the configured port reported by health checks as the current
  source of truth.

### 3. Resource And File Tools

| Path | Purpose | Status |
| --- | --- | --- |
| `_bridge\resource_cli.py` | Stable resource fetch/copy/verify CLI | Present |
| `_bridge\resource_fetcher.py` | Resource acquisition implementation | Present |
| `_bridge\resource_router.py` | Read-only route planner across MCP lookup, browser evidence, conversion, and resource materialization | Present |
| `_bridge\resource_strategy_review.py` | Read-only resource strategy proposal generator | Present |
| `_bridge\resource_process_doctor.py` | Read-only resource/MCP process fanout doctor with metrics, doctor, validate, and dry-run repair-plan | Present |
| `_bridge\backup_hygiene_doctor.py` | Read-only backup hygiene doctor with snapshot, doctor, repair-plan, metrics, validate, and gated archive apply | Present |
| `_bridge\shared\codex_scheduler_runner.py` | Unified scheduler runner for desktop resource-library automation and maintenance tasks | Present |
| `_bridge\shared\run-codex-scheduler.ps1` | Hidden launcher for the unified scheduler runner | Present |
| `_bridge\shared\install-codex-scheduler-task.ps1` | Windows Scheduled Task installer for `CodexSchedulerRunner` | Present |
| `_bridge\render_mermaid_diagrams.js` | Render Mermaid blocks from Markdown into PNG images using bundled Node + Edge | Present |
| `_bridge\render-mermaid.ps1` | Short PowerShell wrapper for Mermaid-to-PNG rendering | Present |
| `_bridge\file_toolkit\__init__.py` | File analysis toolkit | Present |

Policy:

- Treat this layer as the project-level Codex core for resource acquisition.
- Use `python _bridge\resource_cli.py route --json` or
  `_bridge\resource_router.py` before ambiguous resource work. The router is
  read-only and only plans the tool route.
- Prefer purpose-built information MCPs before materializing resources:
  `context7` for current library/API docs, `microsoftdocs` for Microsoft docs,
  `github` for GitHub metadata, `markitdown` for conversion to Markdown, and
  `playwright` or `chrome-devtools` for browser/page evidence.
- Use the resource layer for downloads, local file ingestion, cache inspection, and sha256 verification.
- Do not replace it with one-off `curl` or `Invoke-WebRequest` calls unless the resource layer is unsuitable.
- Use `_bridge\resource_cli.py` when a resource must become a stable local
  file with cache metadata, size, sha256, and a replayable acquisition record.
- Use `_bridge\render_mermaid_diagrams.js` when Markdown diagrams need to
  become reusable PNG artifacts for Word, PDF, or other office documents.
- Use `_bridge\render-mermaid.ps1` when you want a short office-friendly
  command instead of the longer Node invocation.
- Keep MCP read/conversion results separate from resource materialization:
  MCPs answer or inspect; the resource layer stores and verifies.
- Use `python _bridge\resource_cli.py strategy-review --json` to review
  resource acquisition observations. Treat output as proposals only.
- Use `python _bridge\resource_cli.py strategy-review --hide-legacy --json`
  when reviewing current policy behavior without historical legacy CLI noise.
- Use `python _bridge\resource_cli.py classify-url <url> --json` to classify
  URL semantics before choosing probe/preview/materialize. This command is
  read-only and never fetches the URL.
- Use `resource-process` maintenance commands to inspect duplicated MCP or
  resource helper processes. These commands are strictly read-only; they never
  kill, start, or rewrite processes/config.
- Use `resource-process startup-sources` before any cleanup proposal. It groups
  duplicate helpers by parent process and parent command line so governance can
  target the launcher/session lifecycle instead of terminating child processes.
- Local non-protected stdio MCP entries should launch through direct Python
  entrypoints, usually `_bridge\mcp_profile_launcher.py <profile>` or direct
  `_bridge\mcp_launch_guard.py ... -- <server>`. Avoid `.cmd` wrappers and
  `npx.cmd` in Codex MCP registrations because they add `cmd.exe`/`conhost.exe`
  fanout and can flash visible console windows. The guard still serializes only
  the short prelaunch section, then starts a fresh stdio MCP server for the
  current client session. Stdio MCP processes are pipe-bound to the client that
  launched them, so the guard must not hold a lifecycle lock or attempt to reuse
  an existing process. Do not use this for protected bridge/Reasonix MCPs or
  `node_repl`.
- Duplicate or orphan stdio MCP cleanup is not part of routine launch. Use the
  `resource-process` owner command with dry-run evidence first, then apply only governed non-protected orphan
  cleanup. Do not kill an active MCP merely because another session needs the
  same MCP name.
- Tool reliability diagnosis must distinguish configured, CLI-visible,
  runtime-process, and current-session exposure. Use
  `_bridge\tool_exposure_doctor.py` for the first three layers; the current
  model-turn tool surface still has to be confirmed by the active Codex
  session.
- MCP reliability diagnosis has a separate current-session layer. Use
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session doctor`
  when an MCP tool reports `Transport closed` or disappears inside the active
  Codex turn. This is not the same as `resource-process`: `resource-process`
  governs duplicate/orphan helper processes, while `mcp-session` records
  current-session transport observations, available profile fallbacks, and a
  dry-run refresh plan. It must not kill protected bridge/Reasonix MCPs or
  restart Codex by itself.
- MCP readiness has five evidence layers: config present, process launched,
  protocol `initialize` succeeds, `tools/list` includes expected tools, and the
  active Codex session exposes/calls the tool. Use
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session smoke --profile <profile>`
  for the protocol/tools-list layer. Use `--run-smoke --smoke-profile <profile>`
  with `doctor`, `repair-plan`, or `metrics` only when that deeper evidence is
  needed, because it launches a temporary MCP subprocess.
- Current-turn positive evidence must come from an actual MCP tool call in the
  active Codex turn. After a real call succeeds, record
  `--status tool_available --source current-codex-turn` for that profile before
  final reporting on tool-layer work. Use `mcp-session record-observations`
  to batch multiple real active-turn successes at closeout. Do not record
  `tool_search` discovery, `codex mcp list`, process presence, fallback
  success, or protocol smoke as current-turn callability; those prove different
  layers and must stay separate. Successful protocol initialize/tools-list smoke
  evidence is `protocol_ok`, not `tool_available`.
- `mcp-session record-observations` validates and deduplicates evidence before
  writing. It rejects `tool_available` unless the source is a current Codex
  turn real tool call, and it rejects smoke/fallback/discovery sources that try
  to claim current-turn callability. Use
  `mcp-session batch-recording-contract-check` after changing this recorder.
- If protocol smoke is healthy but the current Codex session still reports
  `Transport closed`, `unsupported call`, or the tool is absent from the active
  tool surface, the root cause is the Codex session tool exposure/binding
  layer, not the MCP server process or index. `mcp-session doctor --run-smoke`
  should report this as `mcp_session_surface_missing_or_stale` when the actual
  configured wrapper can initialize and `tools/list` includes the expected tool.
  Record the session observation, use profile fallback for the current task, and
  keep the session issue open until a session refresh or app-level fix restores
  exposure.
- If a stdio MCP is configured and protocol-healthy but does not appear in the
  active session, also inspect `_bridge\mcp_launch_guard.py` lock state through
  `mcp-session doctor`. A legacy lifecycle lock such as
  `active_same_profile_session` is a startup-boundary bug: every Codex session
  needs its own stdio child process, and stale lock cleanup must not be
  confused with process reuse.
- Short-lived stdio MCP process fanout can be a normal launch/probe wave after
  `tool_search`, protocol smoke, Codex restart, or current-turn tool probing.
  Do not treat that transient wave as a persistent resource leak. Re-run
  `resource-process metrics` and `resource-process cleanup` after the age gate;
  clean up only revalidated non-protected orphan roots. Persistent fanout after
  the age gate, or fanout paired with fresh current-turn negative evidence,
  remains actionable through resource-process governance.
- A live `Transport closed` error is model-session evidence and must be written
  before relying on later health checks:
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session record-observation --profile codegraph --status transport_closed --source current-codex-session --detail "tool call returned Transport closed"`.
  Replace `codegraph` with the affected profile. Fallback availability is only
  a current-task mitigation; doctor must still report the session as degraded
  until the stale MCP session is refreshed or the observation ages out.
- Current-session tool binding failures such as `unsupported call`, missing
  tool dispatch, or schema/protocol mismatch are also MCP session evidence.
  Record them with `--status tool_unbound` or `--status schema_mismatch`.
  Do not confuse them with process health: the local MCP process and config can
  be healthy while the active Codex session cannot dispatch the tool.
- CodeGraph is Hub-first. Use Hub `codegraph.explore`, then native CodeGraph if
  the Hub route is unavailable or insufficient. A local CLI fallback provides equivalent `explore` output:
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py codegraph-fallback explore --max-files 4 <query>`.
  Use it only after both same-capability MCP routes are unavailable, closed, or
  insufficient; then keep the MCP session issue open
  through `mcp-session doctor` instead of blocking the current code-reading
  task.
- CodeGraph protocol smoke must test the same configured launcher
  `_bridge\mcp_profile_launcher.py cg`, not only the raw
  `codegraph.cmd serve --mcp` command. This prevents false confidence when the
  server binary is healthy but the Codex-visible launcher or session tool
  surface is the failing boundary.
- In `mcp-session repair-plan`, `health_command` is only the bounded probe for
  the fallback route. The task-continuation command is `command` /
  `commands` from the `use_fallback_for_current_task` action. Do not treat a
  health probe such as `codegraph status` as sufficient replacement for the
  actual CodeGraph `explore` workflow.
- For stdio MCPs, `codex mcp list` visible plus no matching process usually
  means on-demand idle, not failure. Treat it as a fault only when config or
  CLI visibility is missing, a launch failure is recorded, or a live session
  explicitly expected the tool and could not expose it.
- Use `performance` maintenance commands for workstation load triage. They
  sample CPU, memory, and disk write pressure, classify hot processes by
  subsystem, and return dry-run-only repair guidance. They do not stop
  processes, change services, change Defender, or restart Codex/app-server.
- Use `defender-governance` for persistent Microsoft Defender/CFA drift around
  Codex paths. It owns the dynamic Codex executable allow-list, Codex/WebView
  cache exclusions, Codex资源库 maintenance paths, and bridge/runtime/cache
  exclusions. It must back up Defender preferences before apply and must not
  disable real-time protection or exclude broad roots such as the whole user
  profile or Downloads directory.
- Defender governance also owns the low-impact scan policy: real-time
  protection stays enabled, scheduled scans use low CPU priority, scan CPU load
  target is 30, idle-only scanning stays enabled, and scheduled scan time is
  03:30 local time. Auto-apply may fix required Codex exclusion/CFA drift and
  this scan policy only. Legacy cleanup such as malformed historical entries or
  null `ExclusionProcess` is manual-only. Recent Defender threat/config events
  are diagnostic evidence for performance analysis and do not by themselves
  mean a repair action failed.
- `performance snapshot` is a single-window现场快照. `metrics`, `doctor`,
  `validate`, and `repair-plan` use multi-window evidence so short CodeGraph,
  Playwright, Chrome DevTools, MarkItDown, browser/WebView, Defender, or WMI
  spikes are separated from sustained load before maintenance decisions.
- Index/service-style tools such as CodeGraph are part of the performance plan:
  govern duplicate startup sources, check index health, and allow only
  controlled idle cleanup/restart after repeated-window evidence. Do not treat
  one short CPU spike as a confirmed fault.
- Use `performance --profile quick` for routine low-noise metrics. Use
  `--profile deep` only when diagnosing a concrete issue because deep probes
  may briefly heat WMI/PowerShell while collecting process ownership evidence.
- Dashboard live watch should write only on state changes plus low-frequency
  heartbeat updates. If `dashboard_live_state.json` starts writing constantly,
  treat it as a performance regression.
- Resource process governance is a `CodexSchedulerRunner` maintenance provider.
  be restored. Dry run remains the default; apply mode requires explicit
  confirmation and only cleans revalidated non-protected orphan root batches.
- Bridge app-server restart is a separate governed action. Use the idle-restart
  helper only when the queue is idle; it restarts the 18791 app-server path, not
  the visible Codex desktop UI.
- Dashboard shortcut diagnosis belongs to the manual-entry validation path. The
  dashboard service being HTTP-healthy is not enough; validate the desktop
  `.lnk`, `runtime\dashboard_open_last.log`, and a visible browser window.
  Backend repair for 18791 app-server or login service must not block opening
  the visible `18808` dashboard when the dashboard service is already healthy.
- Desktop scheduled automation should route through the unified scheduler
  runner. Windows Task Scheduler only wakes `CodexSchedulerRunner`; the runner
  owns Beijing-time due checks, missed-run retry, idempotency records, and
  dry-run defaults before delegating to maintenance providers.
- Automatic email scheduling is now a unified-scheduler task. Keep
  `_bridge\shared\email_scheduler.py` as the action provider for due mail.
  The unified scheduler calls `email_scheduler.py dispatch-due`, which creates
  or reuses an email job and starts a worker. The worker waits for Codex body
  retired; rollback must be implemented through the unified scheduler owner,
  not by restoring the old task or launcher.
- Email content routing is staged. `schedule_runs` records a trigger instance,
  `content_jobs` handles Codex generation or allowlisted command reports, and
  `delivery_jobs` handles SMTP delivery per recipient. Static/template mail
  can skip Codex and go straight to delivery jobs; command reports such as
  workstation performance reports must use allowlisted providers, not arbitrary
  task-table shell commands.
- The email module reads only
  `C:\Users\45543\Desktop\Codex资源库\文档\邮箱区\邮件任务表.txt`.
  Maintenance tasks stay in the unified scheduler/maintenance tables and must
  not be mixed into the email module's scan set.
- Use the email intent layer for low-friction task creation. Start with
  `email_scheduler.py intent-dry-run --to <identity> --content <description>
  --time <beijing time phrase>`; after reviewing the inferred sender,
  recipients, content mode, template, provider, and task row, use
  `intent-create` to write the mail task table. Intent creation writes tasks
  only and never sends mail directly.
- Verify the layer with:

```powershell
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-layer-smoke-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py performance metrics --observe-seconds 5 --profile quick
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py performance doctor --observe-seconds 5 --profile standard
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py performance repair-plan --observe-seconds 5 --profile deep
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py performance validate --observe-seconds 5 --profile standard
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py email-scheduler metrics
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py email-scheduler doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py email-scheduler repair-plan
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py email-scheduler validate
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-process metrics
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-process doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-process startup-sources
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-process repair-plan
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-process validate
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py defender-governance doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py defender-governance repair-plan
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py defender-governance apply
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py defender-governance validate
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session snapshot
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session record-observation --profile codegraph --status transport_closed --source current-codex-session --detail "tool call returned Transport closed"
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session record-observation --profile codegraph --status tool_unbound --source current-codex-session --detail "tool call returned unsupported call"
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session record-observations --items-json "[{\"profile\":\"codegraph\",\"status\":\"tool_available\",\"source\":\"current-codex-turn\",\"detail\":\"active MCP call returned successfully\"}]"
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session batch-recording-contract-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session repair-plan --observe codegraph:transport_closed --run-fallback
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session metrics
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session validate
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py codegraph-fallback explore --max-files 4 mcp_session_doctor record_observation
python _bridge\tool_exposure_doctor.py metrics
python _bridge\tool_exposure_doctor.py doctor
python _bridge\tool_exposure_doctor.py repair-plan
python _bridge\tool_exposure_doctor.py validate
python _bridge\mcp_launch_guard.py --profile cdev --dry-run -- cmd /c exit 0
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-process repair-plan
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-process cleanup --min-age-minutes 15
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-process cleanup --min-age-minutes 15 --apply
_bridge\shared\restart-bridge-appserver-if-idle.ps1 -Mode dry-run
_bridge\shared\restart-bridge-appserver-if-idle.ps1 -Mode apply -Confirm restart-idle-bridge-appserver
python _bridge\shared\codex_scheduler_runner.py validate
python _bridge\shared\codex_scheduler_runner.py metrics
python _bridge\shared\codex_scheduler_runner.py run-due --dry-run
_bridge\shared\install-codex-scheduler-task.ps1 -DryRun -StartNow
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py backup-hygiene metrics
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py backup-hygiene doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py backup-hygiene repair-plan
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py backup-hygiene validate
```

Backup hygiene policy:

- Keep at most 3 backup copies per source directory in place.
- Archive older copies into `_bridge\backups\archive\<YYYYMM>\<module>\...`.
- Do not delete or compress files in the default tool path.
- Do not recursively re-archive the archive tree itself.
- Use `backup-hygiene apply --confirm archive-old-backups` only after reviewing the dry-run plan.

### 4. Local Command Tools

| Command | Status | Notes |
| --- | --- | --- |
| `python` | OK | `C:\Python314\python.exe` |
| `py` | OK | Windows Python launcher |
| `pip` | OK | Python package manager |
| `node` | OK | Local Node runtime |
| `npm` | OK | Node package manager |
| `npx` | OK | Node package runner |
| `git` | OK | Git command line |
| `rg` | OK | Preferred search tool |
| `curl` | OK | HTTP transfer fallback |
| `tar` | OK | Archive support |
| `7z` | OK | 7-Zip archive support; verify live with `tool-registry-health`. |
| `ffmpeg` | OK | Media processing |
| `ffprobe` | OK | Media inspection |
| `sox` | OK | Audio inspection/conversion; useful for waveform, trim, normalize, and format checks. |
| `java` | OK | Java runtime |
| `javac` | OK | Java compiler |
| `pwsh` | OK | PowerShell 7 |
| `powershell` | OK | Windows PowerShell |
| `pnpm` | OK | Available through WinGet links and Codex bundled runtime. Package installs still require approval. |
| `sqlite3` | OK | CLI available; Python sqlite3 remains preferred for structured scripts. |
| `jq` | OK | JSON CLI available; Python JSON parsing remains preferred in scripts. |
| `winget` | OK | Package discovery/install route; installs require explicit approval and post-install validation. |
| `choco` | OK | Secondary package manager; prefer `winget` unless a package is unavailable there. |

### 4.1 Bundled Runtime Capabilities

Codex Desktop provides a bundled runtime independent of the user PATH. Prefer
these for document/sheet/slide/PDF/browser work when available:

| Capability | Available examples | Notes |
| --- | --- | --- |
| Bundled Python | `pandas`, `openpyxl`, `pypdf`, `pdfplumber`, `python-docx`, `python-pptx`, `lxml`, `Pillow`, `playwright` | Use `load_workspace_dependencies` for exact paths before relying on them. |
| Bundled Node | `sharp`, `pdfjs-dist`, `tesseract.js`, `pptxgenjs`, `docx`, `playwright`, `pngjs`, `pixelmatch` | Useful for image/PDF/OCR/browser workflows without installing new packages. |
| OCR venvs | `%LOCALAPPDATA%\Codex\runtimes\ocr\cpu-venv` and `gpu-venv`; `pip-cache` links to the single Windows pip cache authority | Use purpose-built OCR/GUI helpers before raw imports; do not recreate duplicate runtimes or wheel stores in the retired project tree. |

### 4.2 Installation Candidates

The following were found through read-only discovery and are useful candidates
when the task needs them. Installation remains a separate approved action with
one command, one validation, and a rollback note per package.

Download policy:

- Probe known direct artifact URLs before package-manager install when the
  package is GitHub-release-backed. If `probe-url` times out, skip that package
  for the current run or choose a non-GitHub source instead of retrying blindly.
- Prefer already available bundled/project tools when package downloads are
  unstable.
- Install one package at a time. Validate immediately with `Get-Command` and a
  version command. If a dependency installs but the target package fails, record
  the partial state before continuing.
- For Python-package tools, first probe candidate indexes with short
  timeouts/retries. In the 2026-06-25 network, Aliyun PyPI responded quickly
  and installed `pytest`, `ruff`, `uv`, and `yt-dlp` successfully. Prefer
  `python -m <module>` when the per-user scripts directory is not on `PATH`;
  do not change `PATH` just to finish a tool install.
- Source preference for approved installs: existing bundled/project tool,
  `winget` when its artifact route probes cleanly, `choco` as fallback,
  PyPI mirror for Python-package tools after a fast index probe,
  direct resource-layer materialization only for explicit user-approved
  artifact URLs with sha256/size verification.

| Candidate | Suggested source | Why |
| --- | --- | --- |
| `uv` | installed via Aliyun PyPI mirror | Faster isolated Python tool/dependency management. Validated `python -m uv --version` -> `uv 0.11.24`. |
| `pytest` | installed via Aliyun PyPI mirror | Python regression test runner. Validated `python -m pytest --version` -> `pytest 9.1.1`. |
| `ruff` | installed via Aliyun PyPI mirror | Fast Python lint/format checks. Validated `python -m ruff --version` -> `ruff 0.15.19`. |
| `ImageMagick` | `winget` id `ImageMagick.ImageMagick` | Broad image conversion/inspection. |
| `Tesseract OCR` | installed via `choco` package `tesseract`; avoid current winget/GitHub direct route unless revalidated | Local OCR fallback outside Node/browser. Validated `C:\Program Files\Tesseract-OCR\tesseract.exe --version` -> `tesseract v5.5.0.20241111`. |
| `MuPDF / mutool` | installed via `choco` package `mupdf` | PDF rendering/inspection helper. Validated `mutool -v` -> `mutool version 1.27.0`. |
| `Pandoc` | `winget` id `JohnMacFarlane.Pandoc` | Document format conversion. |
| `GitHub CLI` | `winget` id `GitHub.cli` | GitHub issue/release/repo metadata workflows; auth use requires explicit judgment. |
| `ripgrep` / `rg` | installed via `choco` package `ripgrep` | Fast local file search. Validated `rg --version` -> `ripgrep 14.1.0`. |
| `fd` | installed via `choco` package `fd` | Faster local file search. Validated `fd 10.4.2`. |
| `bat`, `fzf` | `winget`/`choco` candidates | GitHub release probes timed out in the current network; skip until a clean source is available. |
| `less` | installed as `bat` dependency via `choco` | Pager available even though `bat` install failed. |
| `yt-dlp` | installed via Aliyun PyPI mirror | Media metadata/download workflows. Validated `python -m yt_dlp --version` -> `2026.06.09`; use only when explicitly needed. |
| `ExifTool` | Source pending | Read-only package lookup did not confirm a stable exact winget ID. |

### 5. Knowledge And Skills

Project skills present:

- `codex-cli`
- `fabric-mc-architecture`
- `mcsmanager-fabric-mc`
- `workspace-knowledge`

Knowledge policy:

- Put stable workflows in skills.
- Put verified reusable facts in memory.
- Put long baselines, audits, and runbooks in project knowledge.
- Do not update skill framework files without explicit approval and a backup.

### 6. Controlled Iteration Layer

| Path or command | Purpose | Status |
| --- | --- | --- |
| `_bridge\iteration_layer_review.py` | Read-only proposal generator for skill, tool-registry, project-knowledge, and CLI automation updates | Present |
| `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance iteration` | Maintenance contract entry point for the controlled iteration finalization gate; runs quick validation and returns proposal groups for user review | Present |
| `python _bridge\iteration_layer_review.py --json --recent-limit 12 --run-validation` | Generate grouped proposals and run safe validation checks | Present |

Iteration report fields:

- `proposal_packages`: raw candidate changes; every item requires user
  confirmation before persistence.
- `proposal_groups`: priority grouping for established rules, CLI automation
  review, tool-registry review, and project-knowledge review.
- `recommended_next_actions`: ordered review guidance, not authorization.
- `safety.writes_files=false`: required invariant for default review mode.
- `decision_summary`: compact read-only promotion focus for downstream
  consumers; treat it as prioritization help, not as permission to edit.

Maintenance consumers of iteration output:

- `maintenance summary` exposes a human-readable `Iteration Decision` block
  with primary batch, promotion boundary, and ready versus validation-first
  split.
- `maintenance doctor` returns `advisories` alongside real `diagnosis` issues.
  `advisories` carry read-only next-step guidance and must not be mixed into
  health severity or repair gating.
- `maintenance repair` dry-run returns the same `advisories` so repair-plan
  consumers can see what to review after repairs, without changing repair
  semantics or applying any promotion automatically.

Validation matrix:

- `snapshot`: read-only state capture.
- `doctor`: read-only issue classification.
- `repair-plan`: dry-run only.
- `validate`: read-only gate check.
- `apply`: gated confirm-only where supported.

Current stable promotion focus:

- `tool-registry-health` is the preferred bounded validation step before making
  bridge/tool capability claims or promoting a new operational rule.
- `tool-registry-drift-check` is the preferred read-only comparison between
  static registry text and live health. If it reports drift, update this file
  only after approval and backup.
- `resource-layer-smoke-check` is the preferred bounded validation step before
  promoting resource acquisition strategy into longer-lived guidance.
- `backup-hygiene` is the preferred bounded validation step before promoting
  backup retention or archive policy changes into longer-lived guidance.
- `iteration-layer-self-check` remains the bounded guard that proves the
  iteration review command is still read-only and structurally healthy.
- `memory-governance-validate` is the bounded guard that proves the long-lived
  memory loop, PMB metrics, memory manifest, user profile partition, and
  candidate-review visibility are available before promoting durable knowledge.

Promoted validation checks:

```powershell
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance summary
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance repair
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py tool-registry-health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py tool-registry-drift-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py resource-layer-smoke-check
python _bridge\memory_governance.py validate
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py backup-hygiene metrics
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py backup-hygiene doctor
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py backup-hygiene repair-plan
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py backup-hygiene validate
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py p0-audit
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py event-noise-coalescing-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py codex-log-sqlite-health
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py reply-dedupe-policy-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py cdp-route-doctor-check
python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py route-fallback-dispatch-check
```

Cross-reference:

- The README high-frequency runbooks now include a compact operator routing table for the common first-step choice.
- Use that table together with `tool-registry-health`, `maintenance summary`, `maintenance doctor`, and `maintenance repair` dry-run; none of those read-only views grant repair or send permission by themselves.
## Recommended Routing Order

1. Purpose-built MCP tool.
2. Resource layer when the task requires durable local materialization,
   cache metadata, or sha256 verification.
3. Project CLI under `_bridge`.
4. Structured local parser or Python helper.
5. PowerShell command.
6. Browser automation through `playwright`/`chrome-devtools` when direct MCP
   lookup is not enough.
7. Desktop GUI automation only for native apps or browser cases that cannot be
   handled through browser/CDP tools.

## Follow-Up Candidates

1. Add a stale-tool audit that flags registry drift when live
   `tool-registry-health` disagrees with static notes. Initial command:
   `tool-registry-drift-check`.
2. Keep comparing this registry with `tool-registry-health` after bridge tool
   changes so static notes do not become the diagnosis source of truth.
