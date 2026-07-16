---
name: memory-checkpoint-ops
description: Project memory and checkpoint governance for Codex and cooperating agents. Use after meaningful engineering milestones, verified facts, user corrections, memory-system changes, project handoffs, or when deciding whether material belongs in MEMORY.md, PMB, an owner-managed checkpoint, or temporary cross-agent coordination.
---

# Memory Checkpoint Ops

Use this skill to preserve durable work without flooding memory. Prefer
project-scoped facts over conversation-scoped summaries.

## Scope

- This skill governs retrieval and write-back discipline for memory layers.
- It does not replace project diagnosis, routing, or execution skills.
- Use it after a verified milestone, user correction, or memory change that
  should survive across sessions.

## Scope and Boundaries

- Primary layer: evolution-governance
- This skill decides what to store, where to store it, and when to promote it.
- It does not choose the task domain, run the task itself, or replace project
  evidence collection.

## Memory Layers

- `MEMORY.md`: compact navigation and high-value pointers.
- `local-pmb-memory`: durable, reusable, workspace-scoped facts and evidence.
- Owner-managed checkpoints: recovery, handoff, rollback, milestone, and
  evidence packages that retain their owning system's schema.
- `knowledge_set`: temporary facts needed by active cross-agent coordination;
  never treat it as long-term authority.
- `_bridge/memory_governance.py`: review, absorption, cleanup, recall checks,
  and validation for durable memory changes.

## Write Rules

Write memory only when the fact is verified, durable, and likely reusable.
Choose the narrowest memory layer that matches the evidence:

- Retrieval order before write-back:
  1. `MEMORY.md`
  2. `hub.pmb_prepare` or `hub.pmb_recall`
  3. the configured MCP fallback chain when the Hub stage fails
  4. `python _bridge\local_pmb_memory.py pmb-recall` as the local read-only
     fallback
  5. owner-managed checkpoint or rollout evidence when detailed recovery or
     runbook context is needed
- If a layer is skipped during retrieval, note the reason briefly in the
  working answer instead of pretending the lookup happened.
- Use `knowledge_set` for short bridge-facing facts that Reasonix and Codex
  both need during active coordination.
- Use PMB for compact conclusions and verified evidence that should survive
  across sessions.
- Keep long runbooks, manifests, recovery evidence, and rollback notes in the
  checkpoint surface owned by the relevant system; store a compact PMB pointer
  only when later retrieval benefits from it.
- Apply durable memory changes through `_bridge/memory_governance.py` so review,
  approval, cleanup, and recall verification remain explicit.
- For cross-agent bridge facts, prefer `knowledge_set` only while the fact is
  operationally active; otherwise store the verified durable version in PMB or
  the relevant owner-managed checkpoint.

Do not store:

- secrets, cookies, passwords, tokens
- full logs, full transcripts, raw private attachments
- temporary guesses
- one-off task details with no future value
- raw temporary artifact paths from `_bridge/tmp`, caches, logs, backups, or
  smoke-test output unless the path itself is the durable operating rule
- unverified inferences from iteration reports; promote only the checked
  conclusion and keep raw report details in project evidence

If facts conflict with memory, verify against current files, logs, service
status, runtime behavior, or official docs, then update memory to match facts.

For Codex system iteration, treat `iteration_layer_review.py` output as a
proposal source. Before any memory write, map each candidate to:

1. evidence checked in the current turn;
2. target layer;
3. reason it will change future behavior;
4. privacy/sensitivity review;
5. rollback or correction path.

If any item is missing, leave the candidate as a draft or report note instead
of writing memory.

When a candidate is not yet ready for memory, keep it in the nearest short-
lived place that matches its scope:

- a draft note for a one-off observation;
- project evidence for a verified but not yet generalized fact;
- a checkpoint for a stable project rule;
- memory only after the rule has survived review or repeat use.

This ladder prevents proposal noise from entering long-term memory too early
and keeps later cleanup bounded.

## Abstract Memory Template

Use this four-field template whenever you are considering a new memory entry:

1. Candidate source: where the idea came from.
2. Evidence: what was verified in the current turn.
3. Target layer: draft, project evidence, checkpoint, or memory.
4. Rollback: how to remove or correct it later.

If any field is missing, do not promote the item. Leave it as a draft note or
project evidence until the next verified turn.

## Output Discipline

- When answering a technical question, say which memory layers were consulted
  if memory helped materially.
- If no memory lookup was needed, say that directly.
- Keep the answer compact; memory should reduce rework, not add a mandatory
  report layer.

## Checkpoint Triggers

Create or update a checkpoint after:

- a stable implementation milestone
- a rollback or root-cause finding
- a user correction that changes future behavior
- a tool/MCP/service baseline change
- a project configuration rule change
- a new agent handoff or bootstrap baseline

## References

## When to Load References

- Read `references/core.md` when you need current local memory endpoints,
  checkpoint discipline, or a concrete write-back path.
- Skip the reference if the task is only about a known stable rule and the
  answer can be given from the skill body itself.

Read `references/core.md` for current local memory endpoints and checkpoint
discipline.

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
