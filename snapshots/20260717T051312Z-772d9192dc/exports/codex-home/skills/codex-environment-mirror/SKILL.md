---
name: codex-environment-mirror
description: Operate the governed Codex environment recovery mirror on this Windows machine. Use when inspecting mirror readiness, refreshing or publishing the active-only snapshot, validating control-plane freshness, planning or publishing semantic recovery milestones, diagnosing drift, preparing an isolated restore plan, or staging a recovery without activation. Trigger for Codex environment backup/mirror/recovery requests, not ordinary project backups.
---

# Codex Environment Mirror

## Ownership

This is an execution-routing skill. The workspace adapter
`_bridge/codex_environment_mirror.py` owns the standardized workflow and
delegates mirror implementation to
`C:\Users\45543\codex-env-mirror\scripts\mirror_cli.py`.

Do not duplicate mirror logic in the skill, modify active state from a staged
restore, or treat the mirror as a live configuration authority.

## Entry

Use the unified facade from the workspace root:

```powershell
python _bridge\codex_workflow_entry.py mirror <action>
```

Start with `status` for an existing mirror. Use `publish` when the user asks to
publish the mirror: it is the one-step recovery-seed path and must refresh or
reuse the verified snapshot, update the local Git repository, push the remote,
and verify the remote head. Use `plan` before a refresh when source scope or
readiness is uncertain. Read
[references/operations.md](references/operations.md) only when exact commands,
confirmations, or result fields are needed.

## Fast Decision Table

Use the smallest path that preserves evidence:

| Situation | First action | Capture path |
| --- | --- | --- |
| Inspect readiness or drift | `mirror status` | No write |
| No known changed path | `mirror plan` then full `mirror refresh` if approved | Full |
| One or more known changed paths | `mirror affected-source-plan --changed <path>` | Directed only when safe |
| Plan is unmapped, ambiguous, membership-related, or invalid | Read the fallback reasons | Full rebuild |
| User asks to publish the mirror | `mirror publish --confirm PUBLISH-CODEX-MIRROR` | Refresh/reuse, validate, commit, push, verify remote |
| Inspect milestone need | `mirror release-plan` | Read-only semantic change classification |
| Plan stable-contract maintenance | `mirror contract-review-plan` | Maps material changes to Codex review obligations |
| Publish an approved milestone | `mirror release --tag <seed-vX.Y.Z> --confirm RELEASE-CODEX-MIRROR` | Validates current state, tags, publishes GitHub Release, verifies remote |
| Need recovery confidence | `mirror validate`, then isolated `restore-plan`/`stage` | Never activate |

Do not run `status`, `plan`, `affected-source-plan`, and `doctor` as a routine
chain. Start with `status`; expand only when its evidence or the task requires
it. `doctor` is for a health investigation, not a prerequisite for every
refresh.

Mirror writes are single-owner operations. If a command returns
`mirror_operation_busy`, do not start another refresh or snapshot. Wait for the
reported PID/operation to finish, then run `mirror status`. Command timeout or
missing terminal output is not evidence that the owner stopped.

## Capture Workflow

1. Run `mirror status` and consume readiness, Git cleanliness, issues, and archive gaps.
2. For scope changes, run `python _bridge\system_membership.py mirror-source-projection` first. Active membership selects candidate source IDs and change roots; the mirror manifest still owns redaction, exclusions, restore mappings, and archive disposition.
3. If paths changed, run `mirror affected-source-plan --changed <path>` for each changed root. Use `mirror plan` only when source selection, inventory, or size needs review. A mismatch between the membership projection and the manifest blocks refresh.
4. Complete durable workflow finalization before publishing. Finalization may create a checkpoint that belongs in the snapshot.
5. Run one approved publish only after finalization when the user wants the mirror released to the remote repository. `publish` wraps the approved refresh/reuse path, then commits any mirror repository metadata and pushes the selected remote branch after validation. Use `refresh` only when the requested outcome is local capture without remote publication. Use `--changed` only when the affected plan is safe; otherwise use full refresh. A valid committed snapshot with unchanged live sources is reused only when no explicit changed paths were supplied.
6. Require `mirror_valid=true`, `capability_restore_ready=true`, and `source_freshness.ok=true` for a live capture before reporting success.
7. Publish or tag only after the final refresh. A successful publish also requires a clean local Git status before push and remote head verification after push. Do not run a source-changing closeout after publication.
8. Report `full_state_restore_ready=false` as an explicit archive/remote gap, not as mirror failure.

When adding a working-environment member, update the membership contract and
projection before adding its source definition. Retired members are never
projected into the active mirror.

For a known changed file, inspect the dependency closure before refresh:

```powershell
python _bridge\codex_workflow_entry.py mirror affected-source-plan --changed <absolute-or-logical-path>
```

When the plan reports `ok=true` and `full_rebuild_required=false`, refresh may
use `mirror refresh --changed <path> --confirm REFRESH-CODEX-MIRROR`. This is
only a capture optimization: it still creates a complete immutable candidate,
copies unchanged assets from the previous valid snapshot, regenerates all
reverse dependents, runs global validation, and switches `latest` only after
validation. Unknown paths, membership changes, invalid dependency graphs,
invalid previous snapshots, governance/schema/redaction/restore changes,
rename ambiguity, or candidate validation failure require a full rebuild.
Never treat a directed candidate as a partial restore artifact.

`refresh` is the standard local transactional write path. It reconciles an invalid
uncommitted pointer, plans, creates, validates, retries only bounded
source-consistency drift, commits the verified candidate, then removes old
snapshots in a separate retention commit. Failed candidates are removed and
the previous `latest.json` is restored atomically. Do not manually combine
those steps unless repairing the adapter.

`publish` is the standard remote publication path. It requires
`PUBLISH-CODEX-MIRROR`, runs the refresh/reuse transaction, validates the exact
snapshot against live sources, commits any remaining mirror repository metadata,
pushes `HEAD` to the configured remote branch, and verifies that the remote
branch now resolves to the local `HEAD`. A push failure or remote-head mismatch
is a publish failure, not a successful mirror release.
For GitHub remotes, the owner may ask the network gateway for a per-process
GitHub route for `git push` and `git ls-remote`; it must not persistently change
system proxy, DNS, credentials, or Codex conversation routing.

Every successful refresh or publish maintains two generated root surfaces:
`CURRENT.md` for people and `manifests/control-plane-state.json` for tools. They
must match `snapshots/latest.json` and every static file declared by
`control-plane-contract.json`. Do not edit stable contracts merely to refresh a
timestamp; unchanged hashes are valid current compatibility evidence.

Milestones are separate from routine publication. Run `release-plan` after the
final tag-free publish, then run `contract-review-plan`. Codex must read each
required contract, make any semantic edits, validate them, and record each file
as `updated` or `compatible` plus the semantic release impact through the
explicit contract-review command. The
machine nominates and verifies; it does not synthesize policy prose. Use
explicit `release` only when the user approved
the semantic tag. Snapshot-only churn is not a release; docs/tests imply patch,
control-plane or capability changes imply minor, and breaking restore schema or
security boundaries imply major. The release owner must verify an annotated
remote tag, a non-draft GitHub Release, and the attached snapshot manifest.

When workflow finalization detects that a mirrored production source changed
and `--auto-finalize` is active, the post-closeout mirror hook must publish,
not merely refresh. This keeps the target repository as the recovery seed after
local production updates. Preserve the explicit publish confirmation, remote
target, clean-status check, and remote-head verification in that automated path.

The authoritative outcome is the owner receipt, not command completion. On
failure, report the phase, actionable `issues`, fallback reason, restored
snapshot, and artifact reference. Do not repeat diagnostics that already have
complete evidence.

The mirror repository is an output, not a mirror source. Its own commits,
tags, releases, and files must not trigger another refresh. Post-closeout
refresh is idempotent: reuse an existing successful receipt within one
closeout and reuse the committed snapshot when live-source validation proves
that source content is unchanged.

## Recovery Workflow

1. In a standalone recovery clone, run direct `mirror_cli.py validate`; it is
   snapshot-only and must not require the publisher's active paths. In the live
   source workspace, `mirror doctor` also checks source freshness.
2. Run `mirror restore-plan --target-root <isolated-empty-path>` and inspect the bounded summary. Open `full_plan_artifact` when every mapping must be reviewed.
3. Run `mirror stage --target-root <path> --confirm STAGE-RESTORE` only with authorization.
4. Require hash verification and `activation_performed=false`; use `full_receipt_artifact` for the complete per-asset evidence.
5. Hand activation to the target environment's owners after backup and domain validation.

Never stage into an active Codex, workspace, CC Switch, or resource-library
root. Staging is not activation.

## Boundaries

- Keep secrets, cookies, sessions, runtime databases, logs, caches, external
  archives, and unapproved binaries outside Git. Approved skill dependencies
  may be binary when the source policy names them; they remain hash-only assets.
- Mirror only active recoverable capability. Active `.codex` and compatibility
  `.agents` user skills include declared fonts, schemas, templates, reference
  media, scripts, licenses, and packaged dependencies. `.disabled`, `.system`,
  plugin caches, inactive member records, tombstones, and historical
  backup/checkpoint trees must not enter snapshots.
- Require every top-level item in the inventoried Codex, Agent compatibility,
  and CC Switch homes to have an explicit disposition. Unknown items block
  refresh; valuable private or version-sensitive state becomes an external
  archive gap rather than a silent omission.
- Keep the mirror repository's `AGENTS.md` concise and authoritative for Agent
  entry. It points to manifests, validation, isolated staging, and activation
  boundaries without duplicating owner catalogs or snapshot contents.
- Mirror current native memory text through the memory owner while excluding its
  nested Git metadata, backups, archived ad-hoc records, and runtime SQLite state.
- Preserve CC Switch through a sanitized semantic export plus an explicit raw
  database archive gap. Preserve plugins through enabled identity/version/hash
  inventory and reacquire them through the plugin owner.
- Do not weaken confirmation strings, active-root overlap checks, hash checks,
  source budgets, owner activation, or closeout requirements.
- Use direct `mirror_cli.py` only for bootstrap recovery when the workspace
  facade is unavailable; return to the unified facade once restored.
- Treat `--changed` as an optimization hint, never as a scope override. The
  candidate must contain the complete active asset set and the current
  dependency graph.

## Acceptance

A successful operation requires the requested owner result to be consumed:

- source inspection: structured status returned with live-source freshness,
  generated-source existence, source coverage, and zero unclassified top-level
  assets;
- recovery inspection: portable snapshot validation passes without publisher
  paths, and `source_freshness_checked=false` is explicit;
- refresh: verified snapshot ID plus local Git receipt, with active user skills complete and generated semantic exports present;
- publish: verified snapshot ID plus local Git receipt, clean pre-push status, remote push receipt, and remote-head verification;
- release: published semantic tag and GitHub Release both resolve to the validated snapshot commit, with manifest attachment evidence;
- contract review: all required stable files have a current fingerprint-bound Codex decision before milestone release;
- validation: no actionable issues;
- restore plan: bounded summary returned and the complete action list persisted by reference;
- stage: all hashes verified, activation not performed, and the complete receipt persisted by reference.

## Minimal Closeout

For ordinary source changes, closeout needs only: changed roots, affected-source
plan, final snapshot ID, validation result, and whether the snapshot was full or
directed. Include archive gaps only when they affect recovery readiness. Do not
reprint the full asset list; use the owner artifact reference.

Persist durable mirror changes through workflow closeout. Do not create a
second mirror skill, command registry, or recovery state store.
