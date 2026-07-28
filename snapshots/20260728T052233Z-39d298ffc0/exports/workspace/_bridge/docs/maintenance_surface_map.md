# Maintenance Surface Map

This map keeps the maintenance layer simple. It tells Codex which existing
maintenance surface owns each kind of evidence or repair planning. It does not
create a new source of truth.

## Rules

- Use the smallest surface that owns the state being inspected.
- Prefer `snapshot` or `metrics` for orientation, `doctor` for diagnosis,
  `repair-plan` for proposed changes, and `validate` after changes.
- Do not run every doctor by default.
- Default command output is a bounded summary. Lists require a limit/cursor,
  metrics exclude raw payloads, and full logs/tool results are fetched only by
  explicit artifact or run reference.
- Successful receipts may stay compact, but failed receipts must inline
  decision-complete diagnostics: concrete causes, affected objects, severity,
  stable identifiers, and the next safe action. Keep the complete owner result
  available by artifact reference instead of forcing a second investigation.
- Aggregate health by declared severity and ownership scope, not only the
  owner's top-level `ok`. Advisory and external-dependency failures stay
  visible without becoming blockers. Reuse structured owner diagnostics and
  indexed query surfaces before opening raw logs; logs are last-mile evidence
  fetched by stable run, task, request, or artifact reference.
- Discover owner actions through the derived maintenance capability index;
  keep each system shard as the contract source of truth, this file as compact
  navigation, and SQLite as the derived query projection.
- Do not let one maintenance surface mutate another module's production state.
- Use Hub only for tool continuity and bounded fallback, not as a permission
  shortcut.
- If a surface is missing a state or risk, update that surface rather than
  creating a parallel checker.

## On-Demand Index

This file is the compact human entrypoint. Full maintenance contracts live in
one authoritative shard per system under `maintenance_surfaces/`. Machine
callers must query `maintenance_capability_registry.py` first; they must not
load every shard to answer a scoped request. The derived SQLite registry is a
cache, while `maintenance_surfaces/index.json` is navigation metadata only.

| System | Contract shard | Contracts |
| --- | --- | ---: |
| `audio` | [`maintenance_surfaces/audio.md`](maintenance_surfaces/audio.md) | 1 |
| `backup` | [`maintenance_surfaces/backup.md`](maintenance_surfaces/backup.md) | 4 |
| `bridge` | [`maintenance_surfaces/bridge.md`](maintenance_surfaces/bridge.md) | 16 |
| `drafts` | [`maintenance_surfaces/drafts.md`](maintenance_surfaces/drafts.md) | 1 |
| `hardware` | [`maintenance_surfaces/hardware.md`](maintenance_surfaces/hardware.md) | 6 |
| `mail` | [`maintenance_surfaces/mail.md`](maintenance_surfaces/mail.md) | 2 |
| `mcp` | [`maintenance_surfaces/mcp.md`](maintenance_surfaces/mcp.md) | 12 |
| `memory` | [`maintenance_surfaces/memory.md`](maintenance_surfaces/memory.md) | 6 |
| `network` | [`maintenance_surfaces/network.md`](maintenance_surfaces/network.md) | 5 |
| `office` | [`maintenance_surfaces/office.md`](maintenance_surfaces/office.md) | 1 |
| `records` | [`maintenance_surfaces/records.md`](maintenance_surfaces/records.md) | 3 |
| `resource` | [`maintenance_surfaces/resource.md`](maintenance_surfaces/resource.md) | 21 |
| `scheduler` | [`maintenance_surfaces/scheduler.md`](maintenance_surfaces/scheduler.md) | 4 |
| `skills` | [`maintenance_surfaces/skills.md`](maintenance_surfaces/skills.md) | 2 |
| `startup` | [`maintenance_surfaces/startup.md`](maintenance_surfaces/startup.md) | 8 |
| `workflow` | [`maintenance_surfaces/workflow.md`](maintenance_surfaces/workflow.md) | 34 |
| `wsl_workspace` | [`maintenance_surfaces/wsl_workspace.md`](maintenance_surfaces/wsl_workspace.md) | 6 |

- Scoped lookup: `python _bridge\codex_workflow_entry.py maintenance catalog --system <system> --term <term> --limit 20`
- Registry build/validation: `python _bridge\maintenance_capability_registry.py build --apply` then `validate`
- Ambiguous selection or new-surface checks: [`maintenance_surfaces/selection-guide.md`](maintenance_surfaces/selection-guide.md)
- Unscoped fallback is deliberately refused when the derived index is absent; choose a system or rebuild the index.
