# Guardrails v1.0.0

> Both Reasonix and Codex must follow these. Violations flagged in NEES_REVIEW.

## 1. Read-only First
Inspect with read-only tools before mutation. agent_status before agent_bridge_send. memory_search before knowledge_set. reasonix_audit before any file edit.

## 2. Sanitize Public Outputs
Remove private paths, tokens, logs, account data from shared artifacts. No raw crash reports in shared vector. No kugou cookies in knowledge exports.

## 3. Verify Drift-prone Facts
Re-check tool availability, ports, services, versions before acting. Bridge MCP may crash — verify before sending. Vector services may die — check before querying.

## 4. Separate Repair From Diagnosis
Keep snapshot/doctor read-only. Repair-plan is dry-run by default. Responder claims tasks but never auto-executes beyond read-only tools.

## 5. Version Public Contracts
Bump SemVer when schemas, tool surfaces, or rules change. Architecture-contract versions match LSD seed versions.
