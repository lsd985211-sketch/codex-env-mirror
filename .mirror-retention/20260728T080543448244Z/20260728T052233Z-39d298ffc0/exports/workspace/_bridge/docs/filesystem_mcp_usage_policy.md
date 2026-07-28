# Filesystem MCP Usage Policy

This policy keeps filesystem, SQLite, and slash-command MCP useful without turning any of them into a blunt default.

## Profiles

- `filesystem`: bounded local file access for the mcsmanager workspace, desktop `Codex资源库`, and selected `.codex` skills/memories/plugins directories.
- `filesystem-admin`: high-scope local file access with `C:\` as the allowed root. It is separate from `filesystem` and inherits the Codex host process privilege level.
- `sqlite-scratch`: default writable SQLite workbench for temporary structured data, indexes, queues, and analysis tables.
- `sqlite-bridge-ro`: read-only SQLite access to the active mobile OpenClaw bridge database.
- `custom-slash-commands`: local prompt/template registry. It renders reusable prompts only; it does not execute commands.

## Selection Rules

- Use `codegraph` first for indexed code understanding, call paths, blast radius, and code edits that need structural context.
- Use `rg` / `rg --files` first for broad search across many files.
- Use `filesystem.read_text_file` or `filesystem.read_media_file` for known files inside bounded roots, especially with Chinese paths or Windows quoting-sensitive paths.
- Use `filesystem-admin` for explicit full-disk or cross-directory local inspection outside bounded roots.
- Use `apply_patch` as the default manual code edit path after approval and backup.
- Use `filesystem.write_file` for generated non-code artifacts only when the target path and overwrite semantics are explicit.
- Use existing project CLIs and maintenance commands for structured system operations; filesystem MCP should inspect inputs and outputs, not replace mature project commands.
- Use `sqlite-scratch` by default when the user asks for SQLite/database-backed working data without naming a production database.
- Use `sqlite-bridge-ro` for active bridge database inspection; do not use it for repairs or ad hoc writes.
- Use `custom-slash-commands` when a request matches a reusable local flow such as MCP health, filesystem policy, coordination task packaging, closeout, or tool-surface drift handling.

## Current Turn Tool Surface

MCP health has three separate layers:

- Config/list health: the MCP is configured and visible to `codex mcp list`.
- Protocol health: the MCP can initialize and list tools in a smoke test.
- Current-turn exposure: this Codex turn can actually call the MCP namespace.

Do not treat the first two as proof of the third. If filesystem is configured but not exposed in the current turn:

- Use explicit CLI fallback for the active task: `Get-Content -LiteralPath` for known files, `rg` for broad search, `Test-Path`/`Get-Item` for existence and metadata, and `apply_patch` for text edits after approval and backup.
- Keep roots narrow and exclusions explicit. Do not use broad recursive scans over `_bridge`, `.codex`, or `C:\` without a concrete target.
- Record the issue as `current_turn_tool_unbound` in the work summary when it affects the task.
- Do not loop on tool discovery. Defer session rebinding or restart to the MCP maintenance flow.

If custom slash commands are unbound, read `_bridge/slash_commands/commands.json` and apply the template manually. If SQLite MCP is unbound, use `_bridge\tool_coordination.py` or local Python `sqlite3` only against the dedicated scratch database; never use a fallback to mutate production databases.

If a current-turn MCP call aborts or hangs once while the direct stdio/protocol
smoke path is healthy, stop using that MCP tool path for the active task. Classify
the incident as `current_turn_tool_surface_unstable`, finish through the bounded
fallback, and leave deeper rebinding to the MCP maintenance flow. Repeated probes
create exactly the kind of stuck session this policy is meant to avoid.

## Slash Command Usage

- Slash commands are memory aids and prompt templates, not execution rights.
- Rendered slash output must still pass normal permission, backup, validation, and maintenance-contract rules.
- Prefer slash templates for repeatable cross-cutting flows: MCP health, filesystem policy reminders, SQLite scratch planning, task packages, current-turn tool drift, and post-work closeout.
- Do not store secrets in slash command variables or rendered outputs.
- If `slash_render_command` is unstable in the current turn, render by reading the JSON template directly or by sending one bounded stdio protocol call to `_bridge\custom_slash_commands_mcp.py`.

## SQLite Profile Rules

- Default SQLite work is writable, but only inside the dedicated scratch database.
- Production, bridge, mail, scheduler, memory, and config databases are never the default writable target.
- If a production database write is needed, use the owning maintenance command with backup, dry-run, validation, and explicit approval.
- Do not store secrets, tokens, private logs, or authoritative production state in the scratch database.

## Admin Profile Rules

- Start read-only.
- Use only when the task genuinely needs `C:\` scope.
- Do not run broad recursive scans over `C:\` without a narrow target, pattern, and excludes.
- Do not use it for destructive recursive delete or move operations.
- Do not use it to bypass approval, backup, permission, or maintenance contracts.

## Validation

```powershell
python _bridge\mcp_session_doctor.py smoke --profile filesystem --timeout-seconds 90
python _bridge\mcp_session_doctor.py smoke --profile filesystem-admin --timeout-seconds 90
python _bridge\mcp_session_doctor.py smoke --profile sqlite-scratch --timeout-seconds 90
python _bridge\mcp_session_doctor.py smoke --profile sqlite-bridge-ro --timeout-seconds 90
python _bridge\mcp_session_doctor.py smoke --profile custom-slash-commands --timeout-seconds 90
python _bridge\codex_state_audit.py --json
```

When the custom slash command namespace is exposed in the current turn, also
call `slash_validate_registry` directly because it validates the active registry
through the same tool surface Codex will use.
