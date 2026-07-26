# PowerShell And Windows Operations Reference

Use this reference for Windows-local commands, scripts, process work, startup
automation, encoding, and permission checks.

## Verified Baseline

- Current workspace shell has been observed as Windows PowerShell 5.1, not
  PowerShell 7. Treat PowerShell 7 syntax such as `??`, `&&`, and newer native
  argument behavior as unavailable unless `$PSVersionTable.PSVersion` proves
  otherwise.
- PowerShell is not Bash. If a command was copied from Linux docs, translate it
  before running it.
- Prefer explicit evidence over assumptions: version table, command existence,
  process command line, file hash/content, and actual exit codes.

## Parsing And Native Arguments

Microsoft documents two main PowerShell parsing modes: expression mode and
argument mode. Command arguments containing spaces need quotes, and
metacharacters such as `|`, `&`, `<`, `>`, `(`, `)`, `{}`, `@`, and `#` can be
interpreted by PowerShell before the native program sees them.

Rules:

- Do not run Bash heredocs in PowerShell:

```powershell
# Wrong in PowerShell
python - <<'PY'
print("bad")
PY
```

- Pipe a single-quoted here-string instead:

```powershell
@'
print("ok")
'@ | python -
```

- Use `--%` only when calling a native Windows executable and you need the rest
  of the line passed literally. It is line-scoped and does not work with normal
  PowerShell cmdlets.
- Use `cmd /c` only when the behavior is truly CMD-native, such as batch-file
  semantics, `assoc`, `ftype`, or a command that depends on CMD parsing.
- Prefer `Start-Process -ArgumentList` for difficult native quoting or when you
  need a separate process with working directory, hidden window, or `-Wait`.

## Quoting And Here-Strings

- Single quotes are literal; double quotes expand variables and subexpressions.
- Use single-quoted here-strings `@' ... '@` when generating scripts or passing
  source code that contains `$`, backticks, braces, JSON, Java args, or regex.
- A here-string closing marker must be at the start of a line. Keep it exact.
- Do not use a double-quoted here-string for code unless variable expansion is
  deliberately required.

## Redirection And Output

- PowerShell streams are not identical to POSIX stdout/stderr. Capture command
  output deliberately.
- For MCP stdio servers, never write logs to stdout. stdout must contain only
  valid JSON-RPC messages. Write diagnostics to stderr or a log file.
- For shell commands where output can be large, filter at source:
  `Select-Object -First`, `Select-Object -Last`, `rg -n`, or targeted `find`.
- Do not dump full logs, lockfiles, generated folders, or whole trees unless the
  user explicitly asks.

## Encoding

- Default Windows PowerShell file encoding behavior can surprise you. Read
  structured files with `Get-Content -Raw -Encoding UTF8`.
- For Python validators on Windows, set `PYTHONUTF8=1` if non-ASCII text may be
  read by Python.
- Do not judge corruption from terminal mojibake alone. Verify raw bytes, UTF-8
  decoding, hashes/backups, structural validators, and source comparison.
- For skills, keep frontmatter and trigger metadata ASCII/English when practical;
  keep body content as UTF-8.

## Filesystem Safety

- Use `-LiteralPath` for paths that may contain brackets, parentheses, wildcard
  characters, spaces, or non-ASCII text.
- Windows PowerShell 5.1 does not support every modern cmdlet parameter on every
  command. Example observed locally: `New-Item -LiteralPath` failed; use
  `New-Item -Path` for directory creation when needed, then verify the resolved
  path.
- Before recursive delete or move, resolve and verify the absolute target path.
  Keep the operation inside the intended workspace or explicitly named target.
- Prefer native PowerShell cmdlets end to end for Windows file operations. Do not
  enumerate paths in PowerShell and pass string-built delete commands to CMD.

## Arrays, JSON, And Error Handling

- Keep scripts compatible with Windows PowerShell 5.1 unless `pwsh`/PowerShell 7
  is explicitly required and available. Do not use PS7-only syntax such as `??`
  in workspace scripts that run under `powershell.exe`.
- Avoid `@()` plus `Select-Object -Unique` in code that later appends with `+=`;
  prior project work observed type pollution. Prefer hashtable/set-style
  de-duplication or explicit `[string]` conversion.
- In Windows PowerShell 5.1, prefer assigning `foreach (...) { ... }` results to
  an intermediate variable before piping to another command; this avoids parser
  surprises seen with inline block pipelines.
- Keep `$ErrorActionPreference = "Continue"` for broad scripts; wrap critical
  steps in targeted `try/catch`.
- Treat `robocopy` exit codes 0 through 7 as success.
- Use structured JSON parsing, not regex, for config files. Read with
  `Get-Content -Raw -Encoding UTF8 | ConvertFrom-Json`.

## Environment Variables

PowerShell environment variables are strings and are inherited by child
processes. On Windows they have Process, User, and Machine scopes. Changes made
through `$Env:NAME = ...` affect only the current PowerShell process and its
children.

Rules:

- Do not assume `$Env:` assignments are persistent.
- Use `[Environment]::SetEnvironmentVariable(name, value, 'User'|'Machine')` for
  persistent changes, and verify in a new process.
- Avoid injecting `JAVA_HOME` through `$Env:` in this workspace when a full Java
  executable path is available; prior Java launch work hit environment dictionary
  conflicts.
- Prefer full executable paths for Java, Node, Gradle, and local helper tools
  when PATH is uncertain.

## Process Inspection And Launch

Use `Get-Process` for simple liveness. Use CIM/WMI when you need command line,
parent process, executable path, or owner-like diagnostics:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -like '*java*' } |
  Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine
```

Start processes explicitly:

```powershell
$p = Start-Process -FilePath $exe `
  -ArgumentList $args `
  -WorkingDirectory $cwd `
  -WindowStyle Hidden `
  -PassThru
```

Use `-Wait` only when the current task should block until the child exits. For
interactive GUI programs, do not assume process start means task success; verify
window state, logs, or application-specific readiness.

## Admin, UAC, And Startup

- Verify elevation from the current process token:

```powershell
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$p = [Security.Principal.WindowsPrincipal]::new($id)
$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
```

- A shortcut or compatibility setting can request elevation, but the current
  Codex session only inherits it if the parent process was actually elevated.
- For scheduled tasks, distinguish:
  - Task exists.
  - Trigger is enabled.
  - Principal/run level is high enough.
  - LastRunTime/LastTaskResult show it actually ran.
  - The launched process is the intended elevated process.
- Startup folder shortcuts are simpler than scheduled tasks but depend on the
  shortcut target, current user login, and UAC prompt behavior.
- Do not kill user processes automatically to "fix" elevation unless the user
  explicitly approves that policy.

## Network And Web Fallbacks

- Prefer web tools for internet research. If a local Node `fetch` path is blocked
  while shell/network is available, PowerShell `Invoke-WebRequest` can be a
  fallback, but only after checking current permission policy.
- Capture status code, URL, and short response snippets. Do not dump large pages.

## Root-Cause Checklist

When a Windows command or local operation fails, classify it before editing:

1. Wrong shell syntax: Bash form in PowerShell, PS7 syntax in PS5.1, bad quoting.
2. Wrong executable/path: PATH missing, inferred skill path, wrong working dir.
3. Encoding: terminal mojibake, non-UTF8 read/write, Python default encoding.
4. Permissions: non-elevated parent, UAC, file lock, antivirus, ACL.
5. Process lifecycle: stale process, wrong instance, command line differs.
6. MCP lifecycle: server process still running old code/tool schema.
7. Tool mismatch: should have used MCP/API/parser instead of shell or GUI.

## Source Anchors

- Microsoft Learn: PowerShell `about_Parsing`, `about_Quoting_Rules`,
  `about_Redirection`, `about_Environment_Variables`, `Start-Process`,
  `Get-CimInstance`, and Windows `schtasks`.
- Local verified failures: Bash heredoc failed in PowerShell; `@'...'@ | python -`
  succeeded; `New-Item -LiteralPath` failed in Windows PowerShell 5.1 here.
