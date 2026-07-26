# Memory Checkpoint Reference

## Current Endpoint Baseline

- Lightweight navigation: `MEMORY.md`.
- Preferred durable recall: `hub.pmb_prepare` and `hub.pmb_recall`.
- Configured MCP fallback: follow the capability matrix from the failed Hub
  stage without restarting the chain.
- Local read-only fallback:
  `python _bridge\local_pmb_memory.py pmb-recall`.
- Durable write, review, cleanup, and validation owner:
  `python _bridge\memory_governance.py`.
- Recovery and handoff evidence: use the checkpoint surface owned by the
  relevant project or subsystem.

## Routing

- Use `MEMORY.md` to decide whether deeper recall is necessary.
- Use PMB for verified facts and compact reusable conclusions.
- Use owner-managed checkpoints for baselines, manifests, runbooks, recovery,
  and rollback evidence.
- Use `knowledge_set` only for short-lived cross-agent coordination.
- Do not write durable memory directly when the memory owner provides a review
  or approved-apply path.

## Checkpoint Quality

A good checkpoint includes:

- project id
- timestamp
- changed facts
- verification commands or evidence paths
- remaining risks
- rollback or recovery note when relevant

Avoid long transcripts. Prefer concise evidence and file references.

## New Agent Bootstrap

Use the bootstrap context pack only when a new thread or agent needs project
state. Do not load full memory automatically for every task; retrieve only the
layers relevant to the current request.
