# LSD Seed Bootstrap — Codex+Reasonix v1.0.0

> Absorbed from github.com/lsd985211-sketch/lsd v1.0.0

## Architecture (4 layers)

| Layer | Purpose | Codex owns | Reasonix owns |
|-------|---------|-----------|---------------|
| iteration | Versioning, releases, compatibility | AGENTS.md rules | architecture-contract |
| resource | Workspace discovery, context extraction | codex_worker.py | bridge.knowledge |
| maintenance | Diagnostics, validation, repair planning | WeCom bridge health | reasonix_responder.py |
| tool | MCP wrappers, read-only surfaces | agent-bridge MCP | reasonix-ai MCP |

## 5 Guardrails

1. read-only-first — inspect before mutation
2. sanitize-public-outputs — no secrets in shared artifacts
3. verify-drift-prone-facts — re-check live state before acting
4. separate-repair-from-diagnosis — doctor read-only, repair dry-run
5. version-public-contracts — SemVer for schemas/tools/rules

## Growth Model

observe → orient → act → verify → retain

Every task must complete all 5 steps. 
- verify: run syntax check, query bridge for confirmation
- retain: promote reusable lesson to knowledge/vector/graph

## 4 Knowledge Types

- stable_contract: can reuse after version check
- live_state: must verify in current environment
- private_runtime: never publish
- reusable_lesson: promote to docs/skills

## Quick Start

```python
# Verify current state
reasonix-ai__reasonix_status()
agent-bridge__agent_status()

# Ask Reasonix
reasonix-ai__reasonix_ask(question="...", domain="arch")
```

## Canonical Artifacts

- _bridge/shared/ARCHITECTURE.md
- _bridge/shared/GUARDRAILS.md
- knowledge:architecture-contract
- knowledge:maintenance-contract
- knowledge:tool-catalog
- knowledge:growth-model
