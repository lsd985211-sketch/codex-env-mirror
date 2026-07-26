# Mirror Operations

Run commands from:

the active WSL workspace, normally:

`/home/codexlab/work/codex-workspace/workspace`

The supported publication chain is:

```text
WSL Work Git -> Windows bare Git -> validated mirror publish -> GitHub recovery repository
```

Use Windows-native paths only for bootstrap recovery or Windows-host owners
that explicitly require them.

| Intent | Unified command | Write boundary |
| --- | --- | --- |
| Current readiness | `python _bridge\codex_workflow_entry.py mirror status` | Read-only |
| Source and size plan | `python _bridge\codex_workflow_entry.py mirror plan` | Read-only |
| Affected source and dependency plan | `python _bridge\codex_workflow_entry.py mirror affected-source-plan --changed <path>` | Read-only |
| Full health check | `python _bridge\codex_workflow_entry.py mirror doctor` | Read-only |
| Snapshot validation | `python _bridge\codex_workflow_entry.py mirror validate` | Read-only |
| Refresh verified snapshot | `python _bridge\codex_workflow_entry.py mirror refresh --confirm REFRESH-CODEX-MIRROR` | Creates, validates, prunes superseded snapshots, commits |
| Refresh with known changed paths | `python _bridge\codex_workflow_entry.py mirror refresh --changed <path> --confirm REFRESH-CODEX-MIRROR` | Dependency-closed capture, complete candidate, validation, atomic publication |
| Publish recovery seed | `python _bridge\codex_workflow_entry.py mirror publish --confirm PUBLISH-CODEX-MIRROR` | Consumes validated Work Git state, derives changed paths from the last mirrored Work Git head when possible, refreshes or reuses, validates, commits mirror repo, pushes GitHub recovery remote, verifies remote head |
| Publish with known changed paths | `python _bridge\codex_workflow_entry.py mirror publish --changed <path> --confirm PUBLISH-CODEX-MIRROR` | Dependency-closed publish with the same GitHub remote verification |
| Plan milestone | `python _bridge\codex_workflow_entry.py mirror release-plan` | Read-only classification since the latest semantic tag |
| Plan contract review | `python _bridge\codex_workflow_entry.py mirror contract-review-plan` | Read-only mapping from material changes to stable documents |
| Record Codex review | `python _bridge\codex_workflow_entry.py mirror contract-review --decision AGENTS.md=updated --decision README.md=compatible ... --release-impact minor --summary <text> --confirm REVIEW-CODEX-MIRROR-CONTRACTS` | Writes, commits, pushes fingerprint-bound milestone evidence |
| Publish milestone | `python _bridge\codex_workflow_entry.py mirror release --tag seed-vX.Y.Z --confirm RELEASE-CODEX-MIRROR` | Updates generated current state, verifies, tags, publishes Release and manifest attachment |
| Isolated restore plan | `python _bridge\codex_workflow_entry.py mirror restore-plan --target-root C:\CodexRestoreStage` | Read-only |
| Isolated restore stage | `python _bridge\codex_workflow_entry.py mirror stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE` | Writes only to empty isolated target |

## Finalization And Publication Order

Use this order for a public recovery seed:

1. Finish source edits and owner validation.
2. Commit the WSL worktree and push/synchronize it to the Windows bare Git store.
3. Run durable workflow finalization once so selected checkpoints are final.
4. Run the governed mirror publish and validate/stage that exact snapshot when
   the outcome must be available from the remote repository.
5. Run `release-plan`; create a milestone only for an approved semantic change.
6. Run explicit `release` after `publish` reports remote-head verification.
   Routine publish remains tag-free.

Do not publish before Work Git handoff and finalization, and do not treat the
mirror repository as a source change. Repeating `mirror refresh` with a valid
committed snapshot and unchanged live sources returns `reused=true`, creates no
snapshot, and creates no Git commit. Repeating `mirror publish` may reuse the
snapshot, commit only remaining repository metadata, and still push/verify the
GitHub recovery branch. When Work Git has advanced, publish first derives the
delta from the last mirrored Work Git head and asks `affected-source-plan`
whether directed capture is safe. Unknown paths, invalid previous snapshot
metadata, non-ancestor history, membership/schema/redaction/restore changes, or
an unsafe affected plan automatically fall back to a full rebuild with an
explicit `refresh_scope.fallback_reason`.

During `refresh`, `publish`, or `validate --live-sources`, source-side export
commands run in mirror read-only mode. They may inspect committed Work Git and
declared host surfaces, but must not call WSL runtime `apply`, mutate source
files, or reverse-project Windows-native state into the WSL worktree.

## Fast Runbooks

### Known source change

```powershell
python _bridge\codex_workflow_entry.py mirror affected-source-plan --changed <path>
python _bridge\codex_workflow_entry.py mirror refresh --changed <path> --confirm REFRESH-CODEX-MIRROR
python _bridge\codex_workflow_entry.py mirror validate
```

Use the directed refresh only when the plan says `ok=true` and
`full_rebuild_required=false`. Multiple paths use repeated `--changed` flags.

### Unknown or broad change

```powershell
python _bridge\codex_workflow_entry.py mirror plan
python _bridge\codex_workflow_entry.py mirror refresh --confirm REFRESH-CODEX-MIRROR
python _bridge\codex_workflow_entry.py mirror validate
```

Do not invent a source mapping to force directed capture. The owner must report
the fallback reason and rebuild completely.

### Publish mirror to remote

```powershell
python _bridge\codex_workflow_entry.py mirror publish --confirm PUBLISH-CODEX-MIRROR
```

Use `--remote <name>` or `--branch <name>` only when the configured GitHub
recovery remote or branch is not the intended target. A successful publish requires
the WSL Work Git release gate to be clean and synchronized with the Windows
bare Git store,
`mirror_valid=true`, `capability_restore_ready=true`,
`source_freshness.ok=true`, a clean local Git status before push, and
`remote_verification.ok=true` after push. A push failure, dirty worktree, or
remote-head mismatch is a failed publish.

With no explicit `--changed`, this command is still the preferred routine
publication entry. It avoids unnecessary full source scans by deriving changed
paths from Work Git and using directed capture only after the affected-source
owner accepts the scope. The mirror remains a complete recovery artifact in
both directed and full modes.

For GitHub remotes, the owner can consume `codex_network_gateway.py plan` and
apply its environment only to the Git child processes used for push and
remote-head verification. Do not rewrite global proxy, DNS, credential, or
conversation routing state.

### Publish a milestone

```powershell
python _bridge\codex_workflow_entry.py mirror release-plan
python _bridge\codex_workflow_entry.py mirror contract-review-plan
python _bridge\codex_workflow_entry.py mirror release --tag seed-v2.2.0 --confirm RELEASE-CODEX-MIRROR
```

The repository must already be clean and source-fresh. The release command
updates `CURRENT.md` and `control-plane-state.json` with the approved milestone,
commits and pushes that state, creates or verifies the annotated tag, creates a
draft GitHub Release with the snapshot manifest, publishes it, then reads the
tag and Release back. Re-running after failure continues from verified existing
state and blocks if any existing tag points to another commit.

Before `release`, Codex consumes `contract-review-plan`, reads each required
stable document against the actual control-plane changes, edits only when its
meaning changed, validates, and records every decision. The release gate checks
that the receipt fingerprint still matches all declared static files.

### Automatic post-closeout publish

When workflow finalization has an applied successful production update and the
changed files match active mirror source roots, the closeout hook should run
the same publish path automatically after Work Git has been committed and
synchronized to the Windows bare Git store. It must not downgrade to a
local-only refresh. The closeout receipt remains under `post_closeout_mirror`,
but its ordering should be `work_git_handoff_then_finalization_then_mirror_publish`,
and failure blocks the finalization with `post_closeout_mirror_publish_failed`.

### Busy owner

When `reason=mirror_operation_busy`, consume `lock_owner.pid`,
`lock_owner.operation`, and `lock_path`. Wait for that owner to exit, then run
`mirror status`. Never retry refresh/snapshot in parallel, and never delete an
active lock file. A stale lock is reclaimed only after its PID is no longer
alive.

### Recovery confidence

```powershell
python _bridge\codex_workflow_entry.py mirror validate
python _bridge\codex_workflow_entry.py mirror restore-plan --target-root C:\CodexRestoreStage
python _bridge\codex_workflow_entry.py mirror stage --target-root C:\CodexRestoreStage --confirm STAGE-RESTORE
```

The target must be a new empty isolated directory. Existing partial staging is
evidence, not permission to overwrite; use another isolated target after
reviewing the failed receipt or directory.

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
- `push.remote_verification.ok`: the remote branch resolves to the pushed local
  `HEAD`; required for `publish`.
- `control_plane`: generated root state matches `latest.json` and declared static hashes.
- `release.tag` / `release.url`: verified semantic milestone and public GitHub Release.
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
