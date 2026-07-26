# Tool Stability Policy

This policy keeps the existing tool surface usable without expanding privileges
or looping on broken MCP calls.

For per-MCP trigger rules, best-use paths, fallbacks, and validation routes,
use `_bridge/docs/mcp_capability_matrix.md`. This file defines stability
evidence and failure handling; the matrix defines how each configured MCP should
be used.

## Stability Layers

Every tool has separate evidence layers:

- `configured`: present in Codex config/baseline.
- `cli_visible`: visible in `codex mcp list`.
- `protocol_smoke_supported`: can be checked through initialize/tools-list.
- `current_turn`: the active Codex turn actually called the tool.
- `usable_state`: the derived state used for task routing.

Do not claim a tool is fully usable from config or protocol evidence alone.
Current-turn usability requires a real successful tool call recorded as
`source=current-codex-turn`.

## Circuit Breaker

If a tool call in the active turn is aborted, hung, timed out, cancelled, or
hits a dispatch failure, record `tool_surface_unstable` for that profile and
stop calling the same tool path in this turn. Use the bounded fallback for the
current task and leave rebinding/restart to the maintenance flow.

## Fallback Rules

Fallback must preserve the original permission boundary:

- `filesystem` fallback does not promote to `filesystem-admin`.
- `sqlite-scratch` fallback may write only the dedicated scratch DB.
- `sqlite-bridge-ro` fallback is read-only.
- `custom-slash-commands` fallback renders templates only and never executes.
- Browser/GUI fallbacks run only when the task needs browser or GUI behavior.
- Remote docs fallbacks prefer official web sources and remain read-only.

## Startup And Baseline Drift

Reboot stability depends on both persistent configuration and runtime recovery.
Treat these as separate checks:

- Baseline convergence: `codex_startup_baseline.json` should cover current
  global MCP/plugin capability. Use
  `python _bridge\codex_baseline_update.py --check-current` before adopting
  current config into the baseline.
- Guard behavior: `codex_config_guard.py` should repair missing required config
  from the baseline, but a baseline that lags global config is a convergence
  issue, not a reason to remove new global capability.
- Hub recovery: `CodexLocalMcpHub` should use `StartWhenAvailable` and bounded
  restart settings. `local_mcp_hub.py doctor` must check this scheduled task,
  not only the current HTTP endpoint.
- Startup script safety: `run-local-mcp-hub.ps1` may restart only listeners
  whose command line contains `local_mcp_hub.py`. A different process on the
  Hub port is a blocker to report, not a process to kill.

## Scheduled Task Privilege Policy

Scheduled tasks must use the lowest run level that satisfies their owned
operations. The persistent installers and the installed task definitions must
agree on these assignments:

- `Limited`: `CodexLocalMcpHub`, `CodexConfigGuard`,
  `CodexModelProviderWatcher`, `MobileOpenClawBridgeWorker`, and
  `OpenClawGatewayWorker`.
- `Highest`: `CodexSchedulerRunner` and `CodexDesktopElevatedAtLogon`.

`CodexSchedulerRunner` remains elevated because scheduled maintenance includes
administrator-owned operations such as Microsoft Defender preference changes.
`CodexDesktopElevatedAtLogon` remains elevated to preserve the explicit Codex
administrator launch path. Do not weaken either task or remove the Codex
`RUNASADMIN` compatibility setting as a popup workaround.

The Task Scheduler `Hidden` property controls visibility in the scheduler UI;
it does not suppress a console window. Background launch chains must remain
windowless through `pythonw.exe` or `wscript.exe //B //Nologo` with the owned
hidden PowerShell/VBS launcher. Do not add an unowned wrapper executable solely
to hide a window.

New background tasks default to `Limited`. `Highest` requires concrete evidence
that the owned operation needs administrator rights, plus an installer receipt
that reports the selected run level and a validation check that reads it back.

## Required Validation

Use these read-only checks after tool governance changes:

```powershell
python _bridge\codex_baseline_update.py --check-current
python _bridge\tool_exposure_doctor.py metrics
python _bridge\tool_exposure_doctor.py validate
python _bridge\mcp_session_doctor.py metrics
python _bridge\mcp_session_doctor.py validate
python _bridge\codex_state_audit.py
python _bridge\local_mcp_hub.py validate
```

Use `python _bridge\tool_exposure_doctor.py snapshot` when a full per-tool
matrix is needed.
