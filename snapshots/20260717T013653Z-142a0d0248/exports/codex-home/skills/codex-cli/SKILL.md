---
name: codex-cli
description: Diagnose and maintain OpenAI Codex CLI/Desktop configuration, providers, models, MCP registration, permissions, startup behavior, and local bridge integration. Use for Codex configuration or runtime problems; verify live state instead of trusting historical snapshots.
metadata: {"codex":{"compatibility":"Current live configuration, owner tools, and official OpenAI sources override the historical reference guide."}}
---

# Codex CLI Operations

## Scope

Use this skill for Codex CLI/Desktop configuration, model/provider visibility, MCP registration, startup, sandbox and permission behavior, or bridge integration. Route generic Windows process work to `windows-codex-ops` and generic MCP design to `mcp-builder`.

## Evidence Order

1. Read the current task and applicable global/workspace rules.
2. Query current configuration, version, process, and owner health surfaces.
3. Use the MCP capability matrix and configured priority chain for tool calls.
4. Consult official OpenAI sources for behavior that may have changed.
5. If public docs omit a material Desktop/runtime detail, inspect the current installed package schema or config consumer and verify the live projection. Documentation omission is not proof that a setting is invalid or unsupported.
6. Use current official repository issues/releases or bounded implementation evidence when docs and installed behavior leave a real gap.
7. Read `references/full-guide.md` only for historical architecture and prior local patterns; never use its machine inventory as live evidence.

## Change Rules

- Preserve native Codex behavior unless the user explicitly approves a policy change.
- Back up configuration before modifying startup, provider, model, MCP, or permission state.
- Separate provider catalog, Desktop visibility cache, model allowlist, and reasoning-control state.
- Prefer owner validators and a reversible state transition over direct cache or database edits.
- After a provider or startup change, verify the source config and the projection consumed by Desktop.

## Output Contract

- State the diagnosed layer, root cause, files or state changed, and rollback path.
- Distinguish live evidence from historical reference material.
- Do not claim Desktop behavior was verified unless the relevant projection or user-observed state confirms it.
