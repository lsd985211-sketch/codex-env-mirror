# Restore Runbook

## Phase 1: Preflight

- Confirm OS, user identity, target paths, Python/PowerShell versions, Codex
  Desktop version, native-host compatibility, free disk space, and repository
  integrity.
- Run the default `validate` against the committed snapshot. Missing publisher
  paths are not a restore failure; `--live-sources` is reserved for the capture
  source after its paths and owners exist.
- Confirm the snapshot compatibility range and inspect all unresolved external
  archives and secret requirements.
- Confirm the membership export is active-members-only and the membership guard
  reports no blocked asset or registration. Inactive member records are not
  part of the mirror and cannot be staged.
- Confirm the asset disposition inventory has no unknown top-level source item.
  External, reacquired, regenerated, runtime, and historical dispositions are
  explicit recovery decisions, not silent omissions.
- Stop if the target directory is an active source root.

## Phase 2: Isolated Stage

Run `restore-plan`, review every target mapping, then run `stage` into a new
directory. Staging reproduces the logical target layout without modifying the
machine's active Codex installation.

## Phase 3: Domain Validation

Validate in dependency order:

1. platform prerequisites and filesystem layout;
2. global and workspace rules;
3. workflow, membership, and rule-governance source;
4. maintenance capability and MCP route source;
5. user-owned Codex helper tools and runtime prerequisites;
6. Codex configuration template and sanitized CC Switch semantic state;
7. enabled plugin inventory and owner-driven plugin reacquisition;
8. active `.codex` and compatibility `.agents` user skills with text and binary dependencies, then automations;
9. current native memory text, memory semantic exports, and the manifest-selected current checkpoints;
10. Windows scheduled-task and shortcut specifications;
11. external encrypted archives and secret requirements.

For MCP implementations, use the restored Work Git owner to acquire the exact
milestone assets before activation:

```text
python workspace/_bridge/mcp_recovery_bundle_owner.py --archive-root <isolated-bundle-root> import-release --repo lsd985211-sketch/codex-env-mirror --tag <seed-vX.Y.Z>
python workspace/_bridge/mcp_recovery_bundle_owner.py --archive-root <isolated-bundle-root> verify
python workspace/_bridge/mcp_recovery_bundle_owner.py --archive-root <isolated-bundle-root> materialize --target-root <isolated-mcp-root>
```

The import reads `mcp-bundle-index.json` from the GitHub Release and verifies
every archive hash before indexing it. Do not substitute unverified package
manager installs. Windows private archives, enabled plugins, secrets, and
remote MCP credentials still require their recorded owner handoff receipts.

## Phase 4: Owner Activation

Activation is performed in the target environment, not by `mirror_cli.py`.
Each owner must create a target backup, apply its supported migration/import,
run the smallest relevant validator, and emit a receipt. Configuration and
CC Switch semantic state must be merged/imported through current owners rather
than copied wholesale. Platform and plugin-managed skills must be reacquired
from the recorded inventory; only active user-owned skills are file-restored.

## Phase 5: Acceptance

Full restoration requires:

- no unresolved required source;
- no hash or secret-scan failure;
- no owner/rule/route conflict;
- all required archives present and verified;
- required secrets either injected or explicitly accepted as missing;
- staged and live validation receipts linked to the same snapshot ID.
