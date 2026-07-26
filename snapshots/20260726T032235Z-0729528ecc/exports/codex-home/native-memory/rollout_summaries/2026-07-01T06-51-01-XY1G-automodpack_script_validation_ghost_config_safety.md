thread_id: 019f1c72-03c3-7032-aa56-dff625d7c720
updated_at: 2026-07-21T14:17:37+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/07/01/rollout-2026-07-01T14-51-06-019f1c72-03c3-7032-aa56-dff625d7c720.jsonl
cwd: /mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager

# AutoModpack organization automation exposed a destructive validation bug

Rollout context: The user manages a Windows MCSManager/Fabric server and wanted a reusable, generic PowerShell script to keep AutoModpack aligned: classify MODs from `fabric.mod.json`, move client-only MODs, copy dual-side MODs while retaining server copies, handle file- and directory-shaped configs, update AutoModpack paths, and identify orphan/ghost configs. The user repeatedly required rigorous validation and warned against assumptions.

## Task 1: Generic MOD/config organization script

Outcome: partial

Preference signals:

- The user required a generic script without a hard-coded MOD list, intended for ongoing AutoModpack use. Future implementations should inspect current JAR metadata and filesystem state dynamically.
- The user requested rigorous verification and objected to overconfident conclusions. Future reports should separate isolated-test evidence, real-instance evidence, and unverified assumptions.
- The user defined ghost configuration conservatively: a config is ghost-like only when the server cannot find its corresponding MOD, but later emphasized that matching must be global and cautious. Future cleanup should be report-only by default and never delete fuzzy unmatched entries automatically.
- The user clarified that dual-side MODs must be copied to the client distribution location while remaining in server `mods/`; only confirmed client-only MODs should be moved.
- Configs can be files or folders and both forms must be handled.

Key steps and findings:

- A script was created at `daemon/data/organize-mods.ps1` to scan `fabric.mod.json`, classify `client`/`*`/`server`, move/copy JARs, migrate configs, update `automodpack-server.json`, and produce a report.
- Isolated tests found and fixed PowerShell-specific collection issues, JSON parsing/encoding problems, quoting of JAR paths, robocopy exit-code handling, preservation of unrelated AutoModpack settings, and repeated-run behavior.
- The real-instance run exposed a severe flaw: ghost matching used only MODs newly moved/copied during that invocation. Since already-organized files were skipped, valid deployed MODs were missing from the matching set, and roughly 100 valid config entries were deleted. `config/` fell from 15 to 1 and `client-config/` from 26 to 10.
- The correct pivot was to derive matching data from all current MODs in both `mods/` and `client-mods/`, and to switch ghost handling to conservative report-only output with `[可疑]` and `[残留]` labels. Isolated verification then showed zero unintended deletions.

Failures and how to do differently:

- Do not treat clean isolated tests or a successful server boot as sufficient validation for destructive filesystem cleanup.
- Before any real run, create a complete snapshot, run a dry-run, list every planned deletion, and require explicit confirmation for deletion.
- Never use broad fuzzy substring matching as a deletion authority. Unknown or unmatched configs should be preserved and reported for review.
- Do not claim recovery is complete merely because the server may regenerate defaults; the rollout did not verify restoration of all previously deleted configs.

Reusable knowledge:

- `environment=client`: move from server `mods/` to `client-mods/`.
- `environment=*` or confirmed dual-side: copy to `client-mods/`, retain the original in server `mods/`.
- `environment=server`: retain only in server `mods/` and exclude from client distribution through AutoModpack’s server-side exclusion behavior.
- AutoModpack distribution paths in this project include `/mods/*.jar`, `/client-mods/*`, `/config/**`, `/client-config/**`, resourcepacks, and shaderpacks.
- Repeated runs must be idempotent and must use full current-state scans, not only files changed during the current invocation.

References:

- Script: `C:\\Users\\45543\\Downloads\\mcsmanager_windows_release\\mcsmanager\\daemon\\data\\organize-mods.ps1`
- Real-run failure: about 100 config items removed because the MOD pattern set was incomplete.
- Safety rule: ghost detection should be report-only by default; any deletion requires a separate explicit operation after review.
- User’s durable operating expectation: learn from failures, but do not discard an entire tool path after a few failed attempts; isolate the failing mechanism and keep validating alternatives.
