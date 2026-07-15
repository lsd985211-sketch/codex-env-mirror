# Codex, Skills, MCP, And Tool Selection

Use this reference when Codex itself, MCP servers, skills, browser/UI tools, or
tool-discovery behavior are part of the task.

## Tool Selection Matrix

Choose the least brittle interface that can prove the result:

| Need | Prefer | Avoid |
|---|---|---|
| Current external docs | Web/docs MCP from official sources | Memory-only claims |
| Repo or file facts | `rg`, targeted `Get-Content`, structured parsers | Full-tree dumps |
| File edit | `apply_patch` | PowerShell writers for manual edits |
| JSON/YAML/TOML | Parser or native module | Regex/string slicing |
| Running code snippet | PowerShell here-string to Python/Node | Bash heredoc in PowerShell |
| Process command line | `Get-CimInstance Win32_Process` | Guessing from process name |
| Browser with login/cookies | Chrome/agent-browser skill | Fresh Playwright profile |
| Local web UI test | In-app browser or Playwright | Static HTML assumptions |
| Desktop GUI | computer-use only after non-GUI paths fail | Fixed coordinates first |
| MCP state | initialize/tools/list/stderr/process restart | Assuming config reloads live |

## Skill Source Locator Discipline

- Always open the exact `file:` locator shown in the current skill list.
- Do not infer `C:\Users\<user>\.codex\skills\<skill>\SKILL.md`; system skills may
  live under `.system`, plugin skills under cache directories, and project skills
  under the workspace.
- If a skill read fails, first re-check the current skill list and path spelling.
  Treat a path inference failure as an agent error, not as missing content.
- If terminal output shows mojibake, validate bytes/UTF-8/hash before editing.

## MCP stdio Lifecycle

The MCP spec defines stdio as a client-launched subprocess. The server reads
newline-delimited JSON-RPC from stdin and writes JSON-RPC to stdout; logs belong
on stderr. The client initializes, negotiates capabilities, then calls tools.

Practical implications:

- A stdio MCP server normally does not reload code just because files changed on
  disk. Restart Codex or the MCP process after changing server code, tool names,
  schemas, environment variables, or import paths.
- If CLI tests import new code but Codex tools still behave old, suspect a stale
  already-running MCP process before suspecting failed edits.
- If a tool is missing from Codex but direct JSON-RPC `tools/list` shows it,
  Codex likely needs MCP/tool-discovery refresh or restart.
- stdout pollution breaks stdio MCP. Print diagnostics to stderr or logs only.
- For HTTP MCP, verify endpoint, `initialize`, session/protocol headers, and
  `tools/list`; do not mix HTTP and stdio assumptions.

## MCP Diagnosis Steps

1. Read the configured MCP command and args from the actual Codex config.
2. Verify executable exists with `Get-Command` or `Test-Path`.
3. Run a minimal direct JSON-RPC initialize/tools-list test outside Codex when
   safe.
4. Check stderr/logs, not stdout, for diagnostics.
5. If code changed, restart the MCP host and re-run `tools/list`.
6. If tools are intentionally hidden by a gateway boundary, respect the boundary
   rather than editing databases directly.

## Codex Windows Permissions

- Codex filesystem sandbox mode and Windows process elevation are separate
  concepts. `danger-full-access` permits filesystem operations in Codex policy,
  but it does not by itself make the Windows process elevated.
- If an operation needs admin rights, verify current elevation first. Then decide
  whether to relaunch the parent app elevated, use a scheduled task/shortcut
  route, or ask the user to perform an admin-only action.
- In this workspace, recovering Codex Desktop CDP must preserve the existing
  admin startup baseline: use
  `C:\Users\45543\.codex\scripts\start-codex-desktop-elevated.ps1` and pass the
  intended CDP port through `CODEX_CDP_PORT`. Do not substitute a plain
  non-admin Codex launch when fixing visible-window delivery.
- Be conservative with "auto-fix" startup changes. Verify actual process
  elevation after reboot/login, not only task/shortcut creation.

## Browser And GUI Choices

- Use direct APIs, configs, logs, or launch scripts before GUI automation when a
  reliable non-GUI path exists.
- Use Chrome control when the task depends on the user's existing Chrome profile,
  cookies, logged-in state, or extensions.
- Use the in-app browser/Playwright for local web verification when a fresh
  browser context is acceptable.
- Use computer-use for desktop apps only when required. First inspect windows,
  dimensions, and state. Avoid fixed coordinates; compute from actual window
  rectangles or use accessibility/keyboard paths.
- If GUI automation fails repeatedly, stop and re-classify the task rather than
  trying the same clicks.

## Codex Tool/Path Pitfalls Observed Locally

- Wrong: assuming `openai-docs` lived at
  `C:\Users\45543\.codex\skills\openai-docs\SKILL.md`.
- Right: use the session-provided locator:
  `C:\Users\45543\.codex\skills\.system\openai-docs\SKILL.md`.
- Wrong: using Bash heredoc syntax in PowerShell.
- Right: use a single-quoted here-string piped to Python.
- Wrong: assuming a project MCP code patch changes a currently running MCP tool.
- Right: restart Codex/MCP and verify `tools/list` or behavior again.

## Durable Learning Rule

When a Windows/Codex operation exposes a reusable mistake or correction:

1. Verify it with current evidence.
2. Add the short stable conclusion to vector memory.
3. Add long evidence or a project checkpoint when it affects an engineering
   workflow.
4. Update this skill only after the lesson generalizes beyond one incident.
5. Keep the entry concise and place detail in references, not in global routers.
