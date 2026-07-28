# Reasonix+Codex Architecture — LSD v1.0.0

> Mapped to LSD seed system 4-layer model

## 1. iteration — Versioned evolution & compatibility

- **Purpose**: Version changes, release boundaries, compatibility policy.
- **Artifacts**: Codex AGENTS.md, Cooperation protocol v2, ConversationCheckpoint policy
- **Checkpoints**: `_bridge/shared/checkpoints/MANIFEST.md`

## 2. resource — Workspace discovery & sanitized context

- **Purpose**: Workspace discovery, checkpoint manifests, project knowledge.
- **Artifacts**: PMB reusable memory, indexed project checkpoints, owner snapshots, and SQLite query surfaces.
- **Liveness**: owner-specific doctor/status results with generated timestamps and expiry rules.

## 3. maintenance — Diagnostics & repair planning (read-only by default)

- **Purpose**: Read-only diagnostics, validation profiles, drift reporting.
- **Artifacts**: workflow route packs, owner doctors, repair plans, validators, and closeout receipts.
- **Deep Answer**: use the task-selected owner and MCP fallback route; do not revive retired memory MCPs.

## 4. tool — Explicit command surfaces & MCP wrappers

- **Purpose**: Minimal command surfaces, explicit I/O schemas, read-only defaults.
- **Artifacts**: agent-bridge for cross-agent work, Local MCP Hub for stateless owner calls, and session-native GUI/browser tools.

## Shared Rules

1. Prefer public, reusable abstractions over private local paths.
2. Treat live environment facts as runtime evidence, not static memory.
3. Sanitize secrets and sensitive values before export.
4. Missing artifacts should be reported, not hidden.
5. Keep diagnostics and repair separate from business payloads.
6. Use UTF-8 for Chinese paths, JSON, Markdown, configuration, and bridge output.

## Growth Model

observe → orient → act → verify → retain

## Capability Index

| Capability | Layer | Tool |
|-----------|-------|------|
| architecture | iteration | knowledge:architecture-contract |
| knowledge | resource | PMB recall and indexed checkpoints |
| maintenance | maintenance | owner doctor, repair-plan, validate |
| agent_capabilities | all | agent-bridge tools |
| bootstrap | resource | new_agent_bootstrap.py and live owner checks |
