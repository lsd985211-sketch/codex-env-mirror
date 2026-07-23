---
name: self-improvement
description: Capture verified reusable lessons, corrections, stale knowledge, and capability changes by routing each durable change to its authoritative governance owner. Use after repeated failures, user corrections, verified drift, or a change that should improve future Codex work.
---

# Self-Improvement Governance

## Framework Layer

- Primary layer: `evolution-governance`.
- This skill classifies and hands off durable improvements. It does not own
  memory storage, rule authority, skill mutation, system membership, or repair.

## Trigger

Use after a verified correction, repeated failure, stale instruction, durable
preference, changed capability, successful root-cause fix, or evidence that a
workflow, rule, skill, memory, resource route, or system member has drifted.

Do not trigger for transient errors, unverified guesses, raw incident noise, or
simple successful tasks that produced no reusable change.

## Evolution Route

1. Verify the current fact through the owning runtime or structured state.
2. Classify the durable destination:
   - reusable fact or root cause: `memory_governance.py` and PMB;
   - skill content, routing, freshness, or usage: skill lifecycle owner;
   - mandatory behavior or precedence: `rule_governance.py`;
   - member, dependency, activation, or retirement: `system_membership.py`;
   - workflow/resource behavior: the corresponding workflow or resource owner;
   - operational evidence and retention: the record-store owner.
3. Obtain the owner plan, approval, or exact authorized apply path. This skill
   cannot inherit permissions or authorize a write.
4. Validate the owner result and consume its receipt. Transport or owner `ok`
   alone is insufficient when the acceptance predicate is unmet.
5. Close out durable changes with changed files, validation, backup references,
   stable conclusions, and the relevant rule/member receipts.

For coordinated changes, use `self_update_governance.py` to produce the stable
change set and affected owner checks. Repairs remain with the listed owners.

## Memory Boundary

- Do not create or append `MEMORY.md`, `.learnings`, candidate notes, or PMB
  facts directly from this skill.
- Recall may seed hypotheses, but live state must be verified by its owner.
- Long-term absorption, recall verification, stale proposals, and dispositions
  go through `memory_governance.py`.
- Store reusable conclusions, not full logs, secrets, or one-off command output.

## Skill Boundary

- Do not rewrite, delete, archive, or route a skill based only on this skill's
  judgment.
- Use skill lifecycle discovery, repair plans, approved apply paths, lineage,
  usage evidence, and validation.
- A skill update must state what changed, why it is durable, and which validator
  proved the new behavior.

## Output Contract

- State the verified lesson or drift.
- Name the authoritative destination and owner.
- State whether the result was applied, proposed, deferred, or discarded.
- Include the validation or receipt reference for applied durable changes.
- If nothing durable changed, explicitly report that no persistence was needed.
