---
name: windows-codex-ops
description: "Windows operations for Codex on local machines: PowerShell vs Bash syntax, safe file/process commands, encoding, admin/UAC, scheduled startup, tool selection, MCP stdio lifecycle, and Codex skill/source-locator discipline. Use when Codex works on Windows, launches or inspects processes, writes scripts, diagnoses missing tools/MCP servers, handles permissions, or translates Linux shell habits to PowerShell."
---

# Windows Codex Ops

## Scope

- Use this skill as a Windows-local constraint and operations method layer for shell semantics, process inspection, permissions, encoding, MCP lifecycle, and tool-choice discipline.
- Use it to keep Windows work on the right execution surface and to avoid Linux-shaped mistakes in PowerShell.
- Do not use it as a general domain router for docs, web research, browser automation, or project-specific execution beyond Windows-local operations concerns.

## Handoff Rules

- **Windows shell/process/encoding/permission/MCP diagnosis**: stay in this skill.
- **Browser DOM automation or local webapp testing**: hand off to `playwright`, `webapp-testing`, or `agent-browser` as appropriate.
- **Native desktop GUI control**: hand off to `gui-automation`.
- **Domain/project-specific workflows after the Windows-local constraint is resolved**: hand off back to the relevant domain or execution skill.

Use this skill before Windows-local operations that depend on shell semantics,
process state, permissions, startup behavior, encoding, MCP servers, or Codex
tool discovery. The goal is to choose the right tool first, then verify with
evidence instead of retrying a Linux-shaped command in PowerShell.

## Operating Rules

- Treat the active shell as PowerShell unless verified otherwise. Check
  `$PSVersionTable` when syntax compatibility matters.
- Prefer PowerShell 7 (`pwsh`) semantics for ordinary local commands, JSON,
  UTF-8 text, and cross-platform-style scripting. Use explicit
  `powershell.exe -NoProfile -ExecutionPolicy Bypass` only when a legacy
  Windows module, Defender/CFA command, scheduled-task compatibility, or
  existing Windows PowerShell script requires it. Do not mix assumptions:
  identify which shell a command targets before using version-specific syntax.
- Do not use Bash-only forms in PowerShell, especially `cmd <<EOF`,
  `python - <<'PY'`, `VAR=value command`, `&&` for Windows PowerShell 5.1,
  or slash-first path assumptions.
- Prefer purpose-built tools in this order: direct MCP/API, structured parser,
  native PowerShell cmdlet, small Python script through a PowerShell here-string,
  `cmd /c` only for CMD-native behavior, GUI/browser automation last.
- Before changing files, follow the active project rules: ask, create a marked
  routed backup, edit with `apply_patch` for manual changes, and verify content
  after. In this workspace, routed backup means using
  `_bridge/shared/backup_router.py plan/create` or
  `mobile_openclaw_cli.py backup-router`; do not create source-adjacent
  `.bak-*` files unless the source directory is itself a planned backup root.
  Every backup must have a remark, category, purpose, manifest, hashes, and a
  clear restore path.
- When a command fails, identify whether the root cause is shell syntax,
  encoding, permissions, process lifetime, stale MCP process, or wrong path
  before changing the implementation.
- Use the exact skill source locator from the current skill list. Do not infer
  paths such as `~/.codex/skills/<name>` when the listed locator is under
  `.system/`, plugin cache, or a project skill directory.


## Encoding Baseline

- Treat UTF-8 as the default for Chinese paths, resource-library documents, JSON, Markdown, TOML, logs, and script output.
- For PowerShell file writes, always specify `-Encoding UTF8` on `Set-Content`, `Add-Content`, and `Out-File`; avoid relying on host defaults.
- For Python subprocesses and maintenance jobs, set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`, and use `encoding="utf-8", errors="replace"` when capturing text output.
- Never reinterpret Chinese paths by manually encoding/decoding through the active console code page. Use .NET/PowerShell `-LiteralPath` or Python `Path` objects end to end.
- If a mojibake path or content marker is found, do not keep using it as a normal path. First compare with the intended canonical path, back it up, then migrate unique data or remove the malformed path only with approval.
- For encoding investigations, avoid broad scans of `.codex/sessions`, `archived_sessions`, `backups`, `runtime`, logs, browser profiles, dependency caches, and generated attachments unless those directories are the explicit target.
- Use `_bridge/encoding_governance.py snapshot|doctor|repair-plan|validate|metrics` for bounded encoding/mojibake checks in this workspace.
## Tool Choice

- For Codex system-level iteration, use the narrowest stable route first:
  project MCP tool, project `_bridge` CLI, structured Python helper,
  PowerShell, then GUI/browser automation. Do not inspect the desktop when a
  health CLI or MCP endpoint can answer the question.
- For fixed local workflows, use `custom-slash-commands` as the prompt-template
  entrypoint when it is exposed in the current turn. Validate/list/render the
  registry as needed, then treat rendered output as a plan or checklist only;
  it must not execute shell commands, bypass approval, skip backups, or replace
  command-specific validation. If the MCP is not current-turn callable, use the
  matching local CLI/fallback or proceed manually and record the tool-surface
  observation.
- For file edits, render or follow `backup-safe-edit` first when available. It
  should lead to `backup-router plan/create`, then the edit, then targeted
  validation. Use `backup-hygiene doctor/repair-plan/validate` for ongoing
  backup governance; it is not a substitute for creating the pre-edit backup.
- For broad MCP usage, route through the workspace matrix at
  `_bridge/docs/mcp_capability_matrix.md`. It defines each configured MCP's
  purpose, trigger, best-use path, fallback, validation, and circuit breaker.
  Do not optimize one MCP in isolation when the symptom is tool-layer wide.
- Use `workflow-router` first when a task spans multiple local systems or the
  right workflow is unclear. Use domain commands such as `email-inbox-flow`,
  `github-repo-flow`, `backup-safe-edit`, or `system-change-contract` when the
  workflow is obvious. After nontrivial work, use `post-work-closeout` or
  `memory-skill-closeout` to decide whether the lesson belongs in memory, a
  skill, a baseline, a checkpoint, or nowhere. Slash commands organize work;
  memory and skills retain reusable knowledge.
- Shell selection: use the active `pwsh`/PowerShell 7 path for normal
  inspection and JSON/text work; call `powershell.exe` explicitly for Windows
  compatibility modules and scripts that were written for Windows PowerShell.
  If a command failed, check whether it was run under the intended shell before
  retrying with rewritten syntax.
- For routine system health, prefer `tool-registry-health`,
  `maintenance summary`, and `iteration_layer_review.py --run-validation
  --validation-profile quick`. Use full/deep checks only when the quick result
  points at a specific subsystem or the user asks for deep validation.
- File search: `rg` / `rg --files`; use `Get-Content -Raw -Encoding UTF8` for
  targeted file reads.
- Git is not the default local-change verifier in this Windows workspace. Many
  active targets are outside the repo, untracked, generated, or under user
  profile skill/memory paths, so `git status`/`git diff` may return no useful
  evidence. For ordinary edits, verify with targeted file reads,
  `Select-String`, `rg`, JSON/encoding validators, explicit backup paths, and
  command-specific smoke tests. Use Git only for repository questions,
  tracked-file diffs, commits, branches, remotes, or history.
- In this workspace, never broad-scan `_tools/openclaw-codex` without explicit
  excludes. Prefer `_bridge/script_inventory.py` for script discovery, or use
  `rg` with excludes for `node_modules`, `pnpm-store`, `npm-cache*`,
  `openclaw-extract`, `dist`, `logs`, `attachments`, `backups`, and
  `login-runs`.
- For bridge/dashboard shortcut diagnosis, also exclude `_bridge/**/runtime/**`
  and browser profile/cache directories such as `dashboard-browser-profile` and
  `dashboard-chrome-profile` unless those generated files are the explicit
  target. Broad scans over browser profile caches produce unusable evidence.
- Do not promote `_bridge/tmp`, temp smoke artifacts, dependency directories,
  backup copies, logs, caches, or generated runtime files as project knowledge
  candidates. They may be evidence for a current diagnosis, but not durable
  rules by themselves.
- For Codex local log/SSD-write issues, never recursively scan the whole user
  profile for `logs_2.sqlite`. In the mcsmanager bridge workspace, use the
  bounded command `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py
  codex-log-sqlite-health --observe-seconds 30`, which checks only
  `%USERPROFILE%\.codex\logs_2.sqlite*` and
  `%USERPROFILE%\.codex\sqlite\logs_2.sqlite*`.
- If Codex `logs_2.sqlite` or its WAL is the active high-frequency writer,
  prefer a reversible guard: back up `config.toml` and active
  `logs_2.sqlite*`, disable Codex analytics/otel in `config.toml`, and use a
  SQLite `BEFORE INSERT ON logs BEGIN SELECT RAISE(IGNORE); END` trigger when
  SSD protection matters more than retaining local Codex diagnostic logs.
- Avoid recursive size/count probes over dependency trees. If a command is for
  orientation, make it bounded first; only use noisy history/dependency scans
  when explicitly investigating those directories.
- File edits: `apply_patch`; avoid PowerShell writers for UTF-8 skill text
  unless encoding is deliberately controlled and validated.
- Python snippets in PowerShell:

```powershell
@'
print("ok")
'@ | python -
```

- Process inspection: `Get-CimInstance Win32_Process` when command line, parent
  process, or executable path matters; `Get-Process` only for basic liveness.
- Background launch: `Start-Process` with explicit `-FilePath`,
  `-ArgumentList`, `-WorkingDirectory`, and `-WindowStyle Hidden` for helpers
  that should not disturb the user.
- For user-facing desktop shortcuts, validate the actual `.lnk` launch path
  and its visible result. A successful direct script run or HTTP health probe
  does not prove a human double-click opened a window.
- Guard PowerShell path APIs against null or empty strings before calling
  `Split-Path`, `Test-Path -LiteralPath`, `Join-Path`, or `Start-Process
  -FilePath`. Hidden Explorer-launched scripts may see different environment
  and process metadata than an interactive shell.
- Admin checks: verify the current token, do not infer elevation from config or
  shortcut existence.
- MCP diagnosis: check the configured command, process lifetime, stderr/stdout
  discipline, sequential `initialize` -> `notifications/initialized` ->
  `tools/list`, and active Codex session exposure separately. If a direct
  protocol smoke lists tools but the current session still cannot call them,
  classify it as a Codex session tool-surface/binding issue and use a local
  fallback for the current task. Restart Codex/MCP after server code or tool
  schema changes.
- Stdio MCP supervisors must preserve JSON-RPC message framing. For the local
  line-delimited MCP servers in this workspace, proxy stdin/stdout line by line
  and flush each line; do not use large blocking `read(size)` loops that can
  hold short `initialize` or `tools/call` messages until timeout.
- Treat `Transport closed` from an exposed MCP namespace as a current-turn
  circuit breaker for that profile. Record the observation immediately, stop
  calling that profile again in the same turn, verify the backend with protocol
  smoke, and continue through the profile's fresh-stdio/CLI fallback when one
  exists. Do not repair one MCP in isolation until the whole tool layer has
  been checked for the same current-turn binding pattern.
- In this workspace, prefer the Tool Gateway for local stdio MCP recovery:
  `python _bridge\mcp_session_doctor.py gateway-route --profile <name>`,
  `gateway-call --profile <name> --tool <tool>`, and `gateway-warmup`. Gateway
  success proves the fresh stdio path is usable; it must not be recorded as
  current-turn `tool_available`. A separate real MCP tool call is still required
  before marking the active Codex turn directly callable.
- Do not confuse MCP stability with avoiding MCP use. Follow the workspace
  execution affinity: stateless owner services use Hub first, session-bound
  browser/GUI/mobile-thread tools use the current native session first, and
  native transport health is still maintained at the configuration/session
  layer. In this workspace, lightweight local core
  MCPs (`custom-slash-commands`, `filesystem`, `markitdown`, `myskills`,
  `sqlite-scratch`, `sqlite-bridge-ro`) should be `required = true` in Codex
  config so startup catches stale or broken native transports early. Keep
  remote, GUI/browser, protected bridge, memory-daemon, and heavy index MCPs
  governed by smoke/probe/repair-plan instead of blanket required startup.
- Current-turn negative MCP observations expire quickly. Keep them as history,
  but do not let old `transport_closed` or `tool_unbound` records keep doctor
  in risk state after a fresh validation window has passed or a newer positive
  probe exists.
- When `codex mcp list` and protocol smoke are healthy but `tool_search` in
  the active turn does not expose the expected `mcp__...` namespace, do not
  restart individual MCP servers or edit Codex internal state tables. Check
  `python _bridge\tool_exposure_doctor.py doctor --thread-id <thread_id>` for
  stale thread `cli_version` and empty dynamic tool registry, then use fallback
  for the current task and refresh the Codex session/new turn after health
  checks pass.

## References

Read only the relevant reference:

- [references/powershell-windows.md](references/powershell-windows.md):
  PowerShell parsing, quoting, here-strings, native arguments, redirection,
  environment variables, filesystem safety, processes, UAC, startup tasks, and
  Windows compatibility traps.
- [references/codex-mcp-tooling.md](references/codex-mcp-tooling.md):
  Codex tool routing, skill locator discipline, MCP stdio lifecycle, stale tool
  schemas, browser/computer-use selection, and failure triage.

## Script

- `scripts/inspect_windows_context.ps1`: read-only environment snapshot for
  PowerShell version, code page, elevation, common executable paths, and selected
  process command lines. Use before permission, PATH, encoding, or process-state
  conclusions.

## Preflight

- Confirm the command is Windows-local and shell-sensitive before changing anything.
- Identify whether the problem is syntax, path, permissions, process, or MCP lifecycle.
- Prefer bounded inspection over broad scans.

## Output Contract

- Return the concrete Windows-local cause or the narrowest confirmed hypothesis.
- Mention the shell/tool choice used.
- If the issue is still ambiguous, state the next bounded check instead of guessing.

