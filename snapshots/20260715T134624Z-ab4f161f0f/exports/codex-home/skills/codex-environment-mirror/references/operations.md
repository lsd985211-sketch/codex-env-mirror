# Mirror Operations

Run commands from:

`C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`

| Intent | Unified command | Write boundary |
| --- | --- | --- |
| Current readiness | `python _bridge\codex_workflow_entry.py mirror status` | Read-only |
| Source and size plan | `python _bridge\codex_workflow_entry.py mirror plan` | Read-only |
| Full health check | `python _bridge\codex_workflow_entry.py mirror doctor` | Read-only |
| Snapshot validation | `python _bridge\codex_workflow_entry.py mirror validate` | Read-only |
| Refresh verified snapshot | `python _bridge\codex_workflow_entry.py mirror refresh --confirm REFRESH-CODEX-MIRROR` | Creates, validates, prunes superseded snapshots, commits |
| Isolated restore plan | `python _bridge\codex_workflow_entry.py mirror restore-plan --target-root C:\CodexRestoreStage` | Read-only |
| Isolated restore stage | `python _bridge\codex_workflow_entry.py mirror stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE` | Writes only to empty isolated target |

## Result Fields

- `mirror_valid`: manifest, text/binary hashes, text secret scan, coverage-required sources, generated semantic exports, active-members-only guard, and references pass.
- `capability_restore_ready`: rules, workflow owners, configuration templates, active `.codex` and compatibility `.agents` skills and dependencies, current native memory text, Codex helper tools, plugin inventory, current checkpoints, and bootstrap capability can be staged.
- `full_state_restore_ready`: required encrypted state archives and an off-machine Git remote are available.
- `issues`: actionable failures that block the requested operation.
- `advisories.required_archive_gaps`: explicit state that remains outside the Git mirror.
- `activation_performed`: must remain `false` for every `stage` receipt.
- `action_sample` / `asset_sample`: bounded representative rows for terminal review.
- `full_plan_artifact`: complete restore mapping written under `_bridge/runtime/codex_environment_mirror`.
- `full_receipt_artifact`: complete staged-asset hash receipt written under the same runtime owner directory.

The unified facade keeps routine output bounded. Artifact references preserve full functionality and are the authority for per-asset review; they are runtime evidence and are not mirrored as recoverable configuration.

A refresh also verifies that `.disabled`, `.system`, plugin cache payloads,
retired members, and tombstones are absent. The Git snapshot contains a
sanitized CC Switch semantic export, enabled plugin version/hash inventory,
Codex Desktop/native-host compatibility evidence, and only the checkpoint files
selected by the current checkpoint manifest. Raw databases and full historical
checkpoint archives remain explicit external gaps.

## Bootstrap Fallback

When `_bridge/codex_workflow_entry.py` has not yet been restored, use the mirror
repository directly:

```powershell
cd C:\Users\45543\codex-env-mirror
python scripts\mirror_cli.py validate
python scripts\mirror_cli.py restore-plan --target-root C:\CodexRestoreStage
python scripts\mirror_cli.py stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE
```

This fallback does not activate the stage and does not replace the unified
entry after workspace recovery.
