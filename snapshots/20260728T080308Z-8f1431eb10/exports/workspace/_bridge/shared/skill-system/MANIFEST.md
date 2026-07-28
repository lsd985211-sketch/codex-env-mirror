# Skill System Manifest

This directory stores the soft-admission review surface for Codex skills.

## Files

- `registry.json`
  - authoritative approval, admission, trust, and stable `skill_id` records
- `reports/`
  - structured audit results written by admission audit tools
- `snapshots/`
  - fingerprints and change-detection material
- `checkpoints/`
  - approval history, baselines, and operator notes

## State semantics

- `discovered`
- `audit-pending`
- `auditing`
- `audited`
- `approval-pending`
- `approved`
- `deferred`
- `rejected`

## Authority boundaries

- `registry.json` is the authority for admission and operator review state.
- `_bridge/runtime/skill_lifecycle/skill_lifecycle.sqlite` is a derived index for
  discovery state, change history, bounded task-quality evidence, and controlled
  lineage. It must not become a second approval authority.
- `_bridge/skill_orchestrator.py` owns task-time routing. Domain and technical
  eligibility remain primary; quality evidence is only a bounded tie-breaker.
- `audit-pending`, `deferred`, and unregistered user skills remain visible and
  usable when their runtime contract is valid. Only an explicit `rejected`
  admission state blocks automatic routing.
- Real-task evidence never approves, rewrites, disables, or retires a skill by
  itself. Those actions remain owner-governed and require explicit review.

Stable identity follows an unambiguous relocation when source, name, and the
complete skill-tree fingerprints match exactly. Ambiguous matches receive a new
identity rather than guessing. Evolution lineage is limited to `FIX`, `DERIVED`,
and `CAPTURED`; the lifecycle owner records evidence but does not mutate skills.
