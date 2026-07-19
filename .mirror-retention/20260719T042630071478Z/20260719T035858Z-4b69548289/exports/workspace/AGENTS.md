# Workspace Working Guide

## 0. Scope

Applies to `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`.
Global boundaries live in `C:\Users\45543\.codex\AGENTS.md`. This file keeps
only local authority, entrypoints, editing requirements, and project invariants.

## 1. Sources Of Truth

- Route and validation plan: `_bridge/workflow_orchestrator.py plan`.
- Rule authority, lifecycle, and changed-file impact: `_bridge/rule_governance.py` and `_bridge/policies/rule_authority_registry.json`.
- MCP affinity, session binding, and fallback: `_bridge/docs/mcp_capability_matrix.md` and generated capability routes.
- Maintenance ownership and commands: `_bridge/docs/maintenance_surface_map.md` and its derived capability index.
- System membership and architecture impact: `_bridge/system_membership.py`.
- Skill behavior: the current skill inventory and selected `SKILL.md`; slash templates are checklists, not executors.

Do not duplicate inventories, owner maps, maintenance catalogs, migration
history, or incidents in AGENTS files.

## 2. Workflow And Editing Entry

For non-simple work, start with:

`python _bridge\workflow_orchestrator.py plan --message "<task>" --detail micro`

Consume its task facts, owner route, gates, stop conditions, validation, and
closeout. If unavailable, state and apply the smallest equivalent route.

Before non-simple code edits, run the bounded module-context and placement-plan
entrypoints. Use `apply_patch` for manual edits, preserve existing facades and
owner boundaries, and route backups through `_bridge/shared/backup_router.py`.

## 3. Project Invariants

- External lookup, URL discovery, downloads, packages, and installs use structured resource-layer jobs and receipts unless the approved direct-network exception applies.
- Query records, queues, receipts, email/scheduler state, and `.sqlite`/`.db` evidence through SQLite/indexed owner surfaces before raw files or logs.
- Discover maintenance actions through `codex_workflow_entry.py maintenance catalog`; default diagnostics are bounded, while failures retain actionable rows and a full-result reference.
- Reply attachments belong to the mail task context and must travel with the reply for Codex processing.
- GitHub Hub/MCP owns remote repository state and writes; local git owns only local history, diffs, branches, and commits.
- WSL Work Git is the daily source authority; the Windows bare Git repository is the same-history backing store; `codex-env-mirror` is a downstream recovery/release output that publishes validated Work Git state to the GitHub recovery repository.
- Mirror live-source reads are read-only: generated exports may inspect declared sources but must not trigger WSL runtime `apply` or reverse-project Windows-native state into Work Git.
- GUI/browser work verifies visible or machine-readable state after action; source changes or clicks alone are not runtime proof.
- Per-request network work must not globally rewrite proxy, DNS, credentials, or Codex conversation routing.
- Mirror operations use the smallest evidence path: known changed paths go through `mirror affected-source-plan`; safe plans may use directed refresh, while unmapped, broad, membership, rule, manifest, or uncertain changes use full rebuild. Every refresh still produces a complete candidate and requires validation before `latest` changes.

## 4. Validation And Closeout

- Run the route pack's smallest relevant owner validator; do not run every doctor or broad scan by default.
- Pass changed architecture and rule-bearing files to closeout. Required membership and rule-governance receipts block a false-success closeout when reconciliation is incomplete.
- For intentional Codex working-environment configuration changes, use `codex_workflow_entry.py closeout --config-changed --auto-finalize` after validation.
- Close out other work only when durable state, evidence, lessons, drift, or proposals changed.
- When either AGENTS source changes, sync and validate `agents_rule_mirror.py`; the mirror remains review-only and is not a second rule authority.
- Mirror refresh is a closeout operation, not an intermediate edit step. Run it once after finalization, consume the owner receipt, and report snapshot ID, capture mode, validation, fallback reason, and actionable failures without dumping the full manifest. Mirror publish follows the chain `WSL Work Git -> Windows bare Git -> validated mirror publish -> GitHub recovery repository`; do not use the mirror repo as a working source.
- Mirror writes are single-owner operations. On `mirror_operation_busy`, wait for the reported owner process and then inspect status; never launch parallel refresh/snapshot retries or remove an active lock.
