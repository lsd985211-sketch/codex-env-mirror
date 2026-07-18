---
name: windows-diagnostic-evidence
description: Use when diagnosing Windows system behavior — process visibility, UAC, scheduled tasks, service permissions, popup windows, or any system-level phenomenon where file contents alone are insufficient for a behavioral claim.
---

# Windows Diagnostic Evidence

## Core Principle

Every claim about Windows process/window behavior must trace to direct observation, not inference from configuration files or API names.

Evidence types (strongest to weakest):
1. **ETW/trace** — Process Monitor, Event Tracing for Windows, WPA
2. **Event log** — Task Scheduler Operational, Security (UAC audits), Application
3. **OS API call result** — verified via PowerShell/C# at runtime
4. **Process listing** — full command line, parent PID, elevation state, window station
5. **File content** — code, config, logs (informative but insufficient alone for behavioral claims)
6. **Inference** — from naming conventions or API semantics (never use as sole basis)

## Preflight Checklist

Before making a claim about Windows behavior, verify:

- [ ] Enabled relevant event logs (Task Scheduler Operational, Security Audit) before reproducing
- [ ] If claiming a task triggers UAC: checked both the task's `RunLevel` AND the actual UAC audit events
- [ ] If claiming a process shows a window: used ProcMon or ETW to capture `CreateProcess` flags / window creation
- [ ] If claiming a task "doesn't need admin": traced the full call chain (scheduler -> jobs -> API calls)
- [ ] If claiming an API's semantic meaning: checked official Microsoft documentation, not memorized assumption
- [ ] Labeled conclusion: `[direct evidence]` / `[reasonable inference]` / `[unverified]`

## Windows-Specific Traps

| Trap | Correction |
|------|-----------|
| `Task.Settings.Hidden` controls window visibility | Controls display in Task Scheduler UI only |
| `RunLevel = Highest` always triggers UAC | Depends on UAC policy, token state, and whether elevation is needed |
| `shell.Run(cmd, 0, False)` guarantees no flash | Console subsystem process still briefly creates conhost before SW_HIDE |
| `-WindowStyle Hidden` eliminates all console creation | Hides after creation; brief flash can still occur |
| `pythonw.exe` never creates a window | Correct — GUI subsystem, no console |
| Deleting a WindowsApps folder removes the package | AppX registration remains; use `Remove-AppxPackage` |
| `Start-Process -Verb RunAs` is the only elevation path | `AppCompatFlags\Layers RUNASADMIN` achieves the same effect |
| `RunLevel = Highest` in task = child process is elevated | Task principal is elevated, child process inheritance depends on how it is launched |

## Dependency Chain Analysis

Before declaring a task does not need admin:

1. Read the task's action command
2. Trace every script it invokes
3. Trace every API those scripts call (registry, WMI, Defender, service control, etc.)
4. Verify each API's privilege requirement
5. Only then make the claim, and label it `[after chain trace]`

## Binary Proposal Guard

Never propose adding a new native binary to the boot chain unless:

- Source is auditable (no pre-compiled binaries from untrusted sources)
- Error handling covers all failure modes and is visible to the caller
- Process/thread handles are closed properly
- Exit code contract is defined
- Working directory is explicit and documented
- Alternatives (VBS, WMI, schtasks) have been exhausted
- Binary path is under version control

## Environment Boundary

OpenCode and Codex are separate engines with separate skill directories, config paths, and MCP registries. Before writing any file:

1. Determine whether the file belongs to OpenCode, Codex, or the shared workspace
2. Verify the target directory exists and belongs to the correct engine
3. Never write OpenCode artifacts into Codex directories or vice versa
4. Never place ungoverned executables into either engine's paths

## Output Contract

State: claim + evidence type + what would confirm or falsify it. If uncertain, say "I need to collect [X] before I can assert this."
