# Tool Coordination Contract

This contract defines how custom slash commands, scratch SQLite, and existing
business modules cooperate without creating a new source of truth.

## Roles

- `custom-slash-commands`: renders repeatable task prompts and task packages.
  It never executes shell commands, sends mail, mutates databases, or bypasses
  approval and backup rules.
- `sqlite-scratch`: default writable SQLite workbench for temporary structured
  data, coordination records, evidence references, and intermediate analysis.
- `sqlite-bridge-ro`: read-only inspection of the active mobile OpenClaw bridge
  database.
- `filesystem-admin` / `filesystem`: bounded file evidence owners. Start at the
  capability matrix's configured stage, then move only forward through the
  shared Hub/native/CLI fallback chain while preserving the same permissions,
  approval, backup, and owner boundaries.
- Business modules keep authority over their own state: mail owns mail state,
  scheduler owns schedule state, bridge owns bridge state, memory owns memory
  state, and maintenance commands own repair semantics.

## Execution Flow

1. Consume the workflow route pack, including `required_gates`, `stop_if`,
   owner route, validation, and closeout obligations.
2. Render a slash template only when it adds a useful checklist, then validate
   required fields and permissions before execution.
3. Gather evidence through the appropriate read path: CodeGraph for indexed
   code, `rg` for broad search, filesystem MCP for known bounded paths, or
   the next configured fallback stage after a failed MCP/Hub attempt.
4. Record only coordination metadata or evidence references in
   `sqlite-scratch`.
5. Hand execution to the owning module or maintenance command.
6. Record result receipts, blocker reasons, or report references in
   coordination tables.

## Rule Resolution And Member Changes

- Global AGENTS defines machine-wide boundaries; workspace AGENTS defines local
  entrypoints and invariants; the route pack selects mandatory gates; owner
  contracts define detailed behavior and evidence. Higher layers reference
  lower layers instead of copying their catalogs.
- A route pack is executable policy, not explanatory text. Reading it without
  satisfying a triggered gate is not compliance.
- Adding, integrating, replacing, renaming, splitting, merging, or retiring a
  member requires `system_membership.py plan` before activation and
  `system_membership.py impact --changed ...` after the changed files are known.
  Owner validation and the actual reload/restart boundary complete the change.
- Membership is a reconciliation contract, not a second inventory database:
  each owner remains authoritative for registration and runtime state. Closeout
  receives the changed files, re-runs membership impact, and requires the
  `system_membership=ok` receipt before it can report successful completion.
  This post-change guard also catches member changes missed by text routing.

## Current-Turn MCP Drift

Config health, protocol smoke health, and current-turn tool exposure are
separate. When a tool is configured and smoke-healthy but unavailable in this
turn, classify it as `current_turn_tool_unbound`. Complete the active task with
the bounded fallback if possible, then record the observation. Do not let
coordination records claim a tool was usable unless it was callable in the
current turn.

## Write Path

- Prefer `python _bridge\tool_coordination.py record-task`,
  `record-event`, and `record-artifact` for coordination records.
- When using SQLite MCP directly, prefer `sqlite_insert_record` and
  `sqlite_upsert_record` for structured writes.
- `sqlite_execute` is reserved for short bounded SQL statements. Do not send
  long UPSERT SQL plus many parameters through the Codex MCP tool surface; that
  path has shown session-level dispatch/cancel instability even when SQLite and
  the MCP server are healthy.

## Scratch Tables

- `coordination_tasks`: normalized task package and current coordination
  status.
- `coordination_events`: timestamped events, failures, and receipts.
- `coordination_artifacts`: report paths, evidence references, or generated
  files related to a task.
- `coordination_kv`: small coordination settings such as schema version.

## Boundaries

- Scratch data is not authoritative production state.
- Production database writes must go through the owning maintenance command with
  backup, dry-run, validation, and explicit approval.
- Bridge database inspection uses `sqlite-bridge-ro`; bridge repair does not use
  ad hoc SQL writes.
- Slash command templates may describe commands, but must not contain executable
  shell fields.
- Filesystem and filesystem-admin reads do not grant write authority. Writes
  still require the normal approval, backup, and owning-module maintenance
  contract.

## Maintenance

```powershell
python _bridge\tool_coordination.py snapshot
python _bridge\tool_coordination.py doctor
python _bridge\tool_coordination.py repair-plan
python _bridge\tool_coordination.py validate
python _bridge\tool_coordination.py metrics
python _bridge\tool_coordination.py init-schema
python _bridge\tool_coordination.py record-task --task-key example --source codex --target-module general --intent "example coordination task" --status ready --payload-json "{}"
python _bridge\tool_coordination.py record-event --task-key example --event-type validation_passed --severity info --detail-json "{}"
python _bridge\tool_coordination.py record-artifact --task-key example --artifact-type report --path-or-ref "_bridge\\reports\\example.md"
```
