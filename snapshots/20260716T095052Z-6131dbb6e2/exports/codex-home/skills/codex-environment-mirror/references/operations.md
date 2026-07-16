# Mirror Operations

Run commands from:

`C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`

| Intent | Unified command | Write boundary |
| --- | --- | --- |
| Current readiness | `python _bridge\codex_workflow_entry.py mirror status` | Read-only |
| Source and size plan | `python _bridge\codex_workflow_entry.py mirror plan` | Read-only |
| Affected source and dependency plan | `python _bridge\codex_workflow_entry.py mirror affected-source-plan --changed <path>` | Read-only |
| Full health check | `python _bridge\codex_workflow_entry.py mirror doctor` | Read-only |
| Snapshot validation | `python _bridge\codex_workflow_entry.py mirror validate` | Read-only |
| Refresh verified snapshot | `python _bridge\codex_workflow_entry.py mirror refresh --confirm REFRESH-CODEX-MIRROR` | Creates, validates, prunes superseded snapshots, commits |
| Refresh with known changed paths | `python _bridge\codex_workflow_entry.py mirror refresh --changed <path> --confirm REFRESH-CODEX-MIRROR` | Dependency-closed capture, complete candidate, validation, atomic publication |
| Isolated restore plan | `python _bridge\codex_workflow_entry.py mirror restore-plan --target-root C:\CodexRestoreStage` | Read-only |
| Isolated restore stage | `python _bridge\codex_workflow_entry.py mirror stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE` | Writes only to empty isolated target |

## Finalization And Publication Order

Use this order for a public recovery seed:

1. Finish source edits and owner validation.
2. Run durable workflow finalization once so selected checkpoints are final.
3. Run the governed mirror refresh and validate/stage that exact snapshot.
4. Publish the Git commit, tag, and Release only after the snapshot is stable.

Do not publish before finalization and do not treat the mirror repository as a
source change. Repeating `mirror refresh` with a valid committed snapshot and
unchanged live sources returns `reused=true`, creates no snapshot, and creates
no Git commit.

Before adding or changing a working-environment module, inspect the active
membership projection:

```powershell
python _bridge\system_membership.py mirror-source-projection
python _bridge\system_membership.py validate
```

The projection is the scope authority for active members, source IDs, generated
source IDs, and closeout change roots. `source-authorities.json` remains the
capture manifest for modes, exclusions, redaction, restore paths, and archive
dispositions. Refresh fails when the two projections disagree.

The `--changed` form is an optimization hint, not a trust bypass. Every
changed path must map to a declared source, the dependency graph must be valid,
and the previous snapshot must be valid before assets can be reused. Otherwise
the owner reports the fallback reason and performs a full rebuild. Both modes
produce the same self-contained asset set and restore graph.

## Result Fields

- `mirror_valid`: committed manifest, text/binary hashes, text secret scan, generated snapshot exports, governance files, active-members-only guard, and references pass.
- `capability_restore_ready`: rules, workflow owners, configuration templates, active `.codex` and compatibility `.agents` skills and dependencies, current native memory text, Codex helper tools, plugin inventory, current checkpoints, and bootstrap capability can be staged.
- `source_freshness.checked` / `source_freshness.ok`: the unified source-side facade ran active-source coverage, generated-source freshness, and top-level disposition checks.
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
python scripts\mirror_cli.py validate --live-sources  # capture source only
python scripts\mirror_cli.py restore-plan --target-root C:\CodexRestoreStage
python scripts\mirror_cli.py stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE
```

This fallback does not activate the stage and does not replace the unified
entry after workspace recovery.
