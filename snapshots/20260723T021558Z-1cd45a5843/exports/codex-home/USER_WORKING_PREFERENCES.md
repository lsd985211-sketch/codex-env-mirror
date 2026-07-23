# User Working Preferences

This file is a fast working index for high-frequency user preferences. It is not a full memory database. Load it when starting non-trivial local, system, bridge, GUI, resource, automation, or maintenance work.

## File Changes

- Before modifying local files, ask for user confirmation unless the current turn contains explicit approval for the exact change.
- Before modifying local files, create a marked backup that can be used for rollback.
- Do not revert or overwrite unrelated user changes.
- For local system or registry changes, record the pre-change state when practical.

## User-Facing Resources

- Resources provided for the user's own use should default to:
  `C:\Users\45543\Desktop\Codex资源库`
- Use category subfolders where appropriate: documents, images, audio, video, installers, scripts/tools, spreadsheets, and temporary uncategorized.
- Codex internal temporary files, backups, logs, intermediate artifacts, bridge backups, and maintenance outputs should stay in their normal working locations unless explicitly requested as user-facing resources.

## Scope Control

- Do not broaden a repair beyond the user-approved target.
- If the user may intentionally customize a setting, do not add broad persistent guards for that setting unless explicitly requested.
- For default-app/file-association repairs, only touch the file types or programs the user names or approves.

## System-Level Engineering

- System-level work must include maintenance-system interaction, not be developed in isolation.
- For bridge, agent interaction, resource layer, GUI automation, memory, configuration, validation framework, and automation changes, maintain or update snapshot/doctor/repair-plan dry-run/validate/metrics or equivalent outputs.
- Business-system changes to states, requirements, risk types, result contracts, or execution semantics must update maintenance outputs and validation matrices.
- Maintenance-system changes to diagnosis categories, repair semantics, validation profiles, or health metrics must be reflected back into the relevant business system.
- Maintenance repair defaults to dry-run/proposal-only unless the user explicitly approves execution.

## Mechanism Changes

- When repairing system-level engineering issues, explain the mechanism before and after the change.
- Be clear about why the change is effective and what new risks it avoids.
- Do not solve an integration failure by silently dropping, acking, or ignoring user information that should be processed.

## Controlled Iteration

- For broad bridge, maintenance, resource, GUI, configuration, automation, or agent-interaction changes in the mcsmanager workspace, run before the final report when available:
  `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py maintenance iteration`
- Treat maintenance iteration output as a proposal gate, not authorization to mutate skills, memory, project knowledge, CLI files, bridge queue state, or Weixin replies.

## Bridge And Agent Work

- For bridge behavior, use actual evidence from `bridge.db`, logs, task completion history, and visible delivery state instead of relying only on stale status flags.
- Mobile/desktop task origin must not reduce reasoning depth, verification rigor, or execution quality.
- Supplements must be semantically consumed or safely promoted; do not merely mark them consumed because a base task has a historical result.
- New messages in a queue should trigger the existing oldest-message flow rather than bypassing older pending work.

## Common Workspace Anchors

Frequently reused modules should be treated as first-class lookup targets:

- `_bridge/codex_startup_baseline.json`
- `_bridge/codex_state_audit.py`
- `_bridge/codex_state_repair.py`
- `_bridge/codex_baseline_update.py`
- `_bridge/mobile_openclaw_bridge/TOOL_REGISTRY.md`
- `_bridge/mobile_openclaw_bridge/mobile_maintenance.py`
- `_bridge/backup_hygiene_doctor.py`


## Preference Index Maintenance

- This file should be updated when the user explicitly asks to persist a preference, working rule, or operating boundary.
- This file should also be updated when repeated work reveals a stable high-frequency preference that affects future actions, after user approval when the update is not directly requested.
- Keep this file concise and operational. Put detailed histories, evidence, and one-off lessons in memory notes or rollout summaries instead.
- Before editing this file, create a backup under `C:\Users\45543\.codex\backups\user-working-preferences`.
- When a preference is added here, summarize the new rule in the final response so the user can correct it immediately.

## Communication

- Keep final answers compact but specific.
- When a command result matters, relay the important output because the user may not see tool output.
- For system repairs, report exactly what changed, what was left untouched, where backups are, and what verification passed.

## GitHub Publishing

- Newly created GitHub repositories default to public unless the user explicitly
  requests private visibility.
- Existing repository visibility is never changed by this default. Read back
  visibility before any edit, and block public publication when secrets,
  sessions, raw databases, private archives, or other non-publishable machine
  state are present.

