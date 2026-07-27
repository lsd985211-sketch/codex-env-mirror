# Workflow Entry Optimization Draft

Content maturity: draft
Workflow status: retained_reference
Pending action: none

Created: 2026-07-04 21:13 +08:00

## Purpose

Make the Codex workspace workflow more deterministic, faster to use, and easier
for lower-capability models to inherit without relying on fragile chat context.

This is a draft. It is not an implementation approval, not a rule change, and
not a long-term memory update.

## Target Flow

```text
user request
 -> workflow_orchestrator unified entry
 -> memory / PMB / work-note recall
 -> skill_orchestrator skill selection
 -> slash template checklist
 -> capability matrix owning MCP / Hub / fallback route
 -> execution
 -> validate / doctor / readback
 -> closeout / notes / proposals / memory-skill-template updates
```

## Current Gaps

- `workflow_orchestrator` gives useful planning output, but it is still mostly
  advisory and does not expose a strict staged execution checklist.
- Memory, PMB, skills, slash templates, and MCP capability routes still require
  Codex to manually stitch the pieces together in many turns.
- Closeout can propose memory, skill, baseline, or template updates, but there is
  no single standard package showing planned-vs-actual tool and knowledge use.
- Lower-capability models may read the rules but skip the right entry, confuse
  protocol health with current-turn callability, or miss validation.

## Proposed Architecture

1. Enhance `workflow_orchestrator` with staged machine-first phases. Human
   readability is not a goal for this output. Optimize for deterministic tool
   routing, stable keys, enum values, compact payloads, and direct execution
   planning.

   Required phase ids:
   - `phase_1_preflight`
   - `phase_2_recall`
   - `phase_3_skill_selection`
   - `phase_4_template_render`
   - `phase_5_tool_route`
   - `phase_6_execution`
   - `phase_7_validation`
   - `phase_8_closeout`

   Each phase should use stable machine fields, not prose-first guidance:
   - `id`
   - `owner`
   - `commands`
   - `read_only`
   - `approval_required`
   - `approval_reason`
   - `fallback`
   - `validation`
   - `stop_conditions`
   - `evidence_to_record`
   - `next_phase`

2. Add a unified read-only workflow facade:

   ```powershell
   python _bridge\codex_workflow_entry.py plan --message "..."
   python _bridge\codex_workflow_entry.py preflight --message "..."
   python _bridge\codex_workflow_entry.py closeout --task-kind ...
   ```

   This facade should orchestrate existing modules, not replace them:
   `workflow_orchestrator`, `memory_governance`, `skill_orchestrator`,
   `custom-slash-commands` / `slash_command_governance`, and the MCP capability
   matrix.

3. Upgrade high-value slash templates into phase checklist templates. Templates
   remain prompt/checklist output only and must not execute commands or grant
   permission.

4. Derive a machine-first MCP route index from the capability matrix:

   ```text
   _bridge/runtime/mcp_capability_routes.json
   ```

   This derived index is for Codex/tool orchestration only. Human readability is
   not a design goal for this file. Optimize it for fast lookup, low ambiguity,
   stable enum values, compact token footprint, and direct tool-route decisions.
   Keep human-facing explanations in the Markdown source document when the user
   needs to review them.

   Candidate fields:
   - `capability`
   - `native_mcp`
   - `hub_route`
   - `local_fallback`
   - `permission_boundary`
   - `validation_command`
   - `current_turn_evidence_rule`

5. Standardize closeout packages:
   - memory / PMB consulted
   - skills selected and actually used
   - slash templates rendered
   - owning MCP / Hub / fallback used
   - positive and negative current-turn tool evidence
   - validations run
   - work-note items and disposition
   - proposed memory, skill, template, baseline, or matrix changes

6. Add a `strict` or weak-model-compatible mode:
   - give explicit commands for each phase;
   - mark read-only vs write actions;
   - mark approval requirements;
   - require backup and validation before writes;
   - forbid treating protocol smoke as current-turn callable;
   - forbid treating rendered templates as execution rights.

## Validation Matrix

Use real local task classes:

- MCP failure diagnosis.
- New slash template creation.
- Mail module modification.
- Mobile Weixin bridge task.
- GitHub repository work.
- Memory absorption / PMB organization.
- GUI or local Weixin operation.

For each class verify:

- Did it recall relevant memory?
- Did it select the right skill set?
- Did it render the right template?
- Did it choose the owning MCP / Hub / fallback route correctly?
- Did it verify through the smallest relevant doctor, validate command, or
  readback?
- Did it close out with visible proposals instead of silent drift?

## Suggested Rollout

1. Build `codex_workflow_entry.py` as read-only plan/preflight/closeout facade.
2. Add MCP capability route JSON derivation from the existing matrix.
3. Extend `workflow_orchestrator` staged output without changing its current
   read-only nature.
4. Add strict mode and regression examples.

## Boundaries

- Do not replace owning modules with another large governance layer.
- Do not make Hub the default when native MCP is callable.
- Do not turn slash templates into executors.
- Do not let closeout automatically write long-term memory, skills, baselines,
  or external state without the existing approval flow.
- Keep the first implementation read-only by default.
