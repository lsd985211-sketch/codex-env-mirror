---
name: codex-environment-mirror
description: Operate the governed Codex environment recovery mirror on this Windows machine. Use when inspecting mirror readiness, refreshing the active-only snapshot, validating recovery assets, diagnosing mirror drift, preparing an isolated restore plan, or staging a recovery without activation. Trigger for Codex environment backup/mirror/recovery requests, not ordinary project backups.
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

Start with `status` for an existing mirror. Use `plan` before a refresh when
source scope or readiness is uncertain. Read
[references/operations.md](references/operations.md) only when exact commands,
confirmations, or result fields are needed.

## Capture Workflow

1. Run `mirror status` and consume readiness, Git cleanliness, issues, and archive gaps.
2. Run `mirror plan` when source selection or size needs review.
3. Run `mirror refresh --confirm REFRESH-CODEX-MIRROR` only with authorization.
4. Require `mirror_valid=true` and `capability_restore_ready=true` before reporting capture success.
5. Report `full_state_restore_ready=false` as an explicit archive/remote gap, not as mirror failure.

`refresh` is the standard transactional write path. It reconciles an invalid
uncommitted pointer, plans, creates, validates, retries only bounded
source-consistency drift, commits the verified candidate, then removes old
snapshots in a separate retention commit. Failed candidates are removed and
the previous `latest.json` is restored atomically. Do not manually combine
those steps unless repairing the adapter.

## Recovery Workflow

1. Run `mirror doctor` before recovery work.
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

## Acceptance

A successful operation requires the requested owner result to be consumed:

- inspection: structured status returned, including generated-source existence,
  source coverage, and zero unclassified top-level assets;
- refresh: verified snapshot ID plus Git receipt, with active user skills complete and generated semantic exports present;
- validation: no actionable issues;
- restore plan: bounded summary returned and the complete action list persisted by reference;
- stage: all hashes verified, activation not performed, and the complete receipt persisted by reference.

Persist durable mirror changes through workflow closeout. Do not create a
second mirror skill, command registry, or recovery state store.
