---
name: global-framework
description: Lightweight global skill router. Use to choose the smallest useful skill set, resolve project/global conflicts, route memory/MCP work, and avoid token-heavy static skill merging.
---

# Global Skill Framework

This is a router, not a merged skill bundle. Keep full skill bodies out of this
file; load them only when the task needs them.

## Framework Layer

- Primary layer: routing
- Secondary layer: evolution-governance
- This skill chooses the smallest useful skill set and handoff boundary. It
  does not execute the task itself.

## Scenario Layer

- Group work by stable intent clusters before choosing a domain skill.
- Typical clusters: routing/governance, memory/knowledge, GUI/desktop, bridge/mobile, research/docs, content/media, code/tools.
- If a task fits more than one cluster, prefer the narrower operational cluster first, then hand off upward only if needed.

## Framework Contract

- Treat every skill as having one primary layer: `routing`, `execution`,
  `constraint-method`, `evolution-governance`, or `domain-project`.
- Do not let a routing skill quietly retain execution ownership after the next
  layer is known.
- Do not let an execution skill silently expand into source selection, platform
  arbitration, or framework-wide policy.
- Do not let a constraint-method skill behave like a router; it constrains work
  inside a task, it does not choose the domain surface.
- For high-risk or broad skills, require explicit boundary and handoff text in
  the body before trusting the trigger scope.
- Keep this file as a contract and router only. Do not turn it into a static
  merged bundle of other skill bodies.

## Operating Rules

- Use progressive disclosure: metadata first, full `SKILL.md` only after a skill
  is chosen, references/scripts only when needed.
- Load the smallest useful skill set. Use any needed skill, but justify each one.
- Priority: active user request > project `AGENTS.md` > project skills > global
  skills; safety and file-edit rules override convenience.
- Route work through explicit layers instead of merging them mentally:
  request intent -> project rules -> domain skill -> tool/MCP surface ->
  app-specific workflow -> memory/skill evolution. Load only the next layer
  needed to make a reliable decision.
- For domain work, first recall the relevant memory and load the relevant skill,
  then use `custom-slash-commands` to render the smallest matching workflow
  template before executing with the owning MCP/CLI/API. Slash templates are
  workflow accelerators, not authority: memory and skills provide reusable
  guidance, project rules provide constraints, and concrete tools perform the
  validated action.
- For tool-heavy work, choose the MCP through the workspace capability matrix
  at `_bridge/docs/mcp_capability_matrix.md`: memory/skill routing first, slash
  template second, owning MCP third, bounded fallback fourth, closeout last.
  Keep `current_turn_callable` evidence separate from config or protocol
  evidence.
- Strengthen layer connections by naming the handoff condition. Examples:
  global routing hands GUI work to `gui-automation`; GUI app identity hands
  Weixin-specific work to `gui-app-weixin`; a verified GUI success/failure hands
  durable lessons to `gui-skill-evolution`; bridge queue symptoms hand state
  diagnosis to `mobile-weixin-bridge-ops`.
- If facts conflict with memory or prior conclusions, verify first, then update
  memory/skills to match evidence.
- Memory is a continuous work layer for long-lived systems, not a one-time
  preflight. Keep memory in the loop while choosing tools, diagnosing repeated
  faults, deciding rollback/validation routes, and closing out lessons.
- Long-lived system work must keep memory in the loop for bridge, MCP, mail,
  scheduler, maintenance, performance, PMB, skills, baseline, seed modules,
  and automation. Recall relevant memory early, verify drift-prone facts live,
  and revisit memory again when symptoms repeat or the chosen path changes.
- After failure or regression, find the root cause before making another fix.
- For system-level engineering, first identify the maintenance surface: health
  checks, repair commands, state machine, rollback points, and regression
  commands. Prefer improving that maintenance surface over one-off DB or state
  edits.
- Treat repeated symptoms as a state-model problem until proven otherwise. Build
  or update a regression command that captures the state transition before
  changing production behavior.
- Before local file edits, follow project rules: ask first and create a marked
  backup.
- Prefer `rg`; avoid broad dumps of logs, generated folders, or full trees.
- Prefer compact evidence: use health summaries, checkpoint lookups, targeted
  `rg` hits, and extracted JSON fields before reading full files or logs.
- Use Git as the ownership and history layer for tracked declarative files in
  the WSL Work Git repository. When `main` is dirty or work is parallel, use
  `work_git_change_owner.py` for task worktrees, declared-path commits, local
  bare synchronization, and fast-forward integration; Git evidence supplements
  rather than replaces owner validation. Runtime databases, sessions, caches,
  generated artifacts, Windows host state, and files outside Work Git remain
  under their domain owner, hashes, validators, and backup comparison instead
  of being forced into Git.
- Keep routing evidence-based: classify the task, note the owning layer, and
  carry forward only the minimum context needed by the next layer.
- Apply execution economy to every repeatable route: machine-run only the
  declared deterministic low-risk owner steps, reuse current receipts and
  derived indexes by stable input signature, batch independent work under one
  bounded deadline, and skip unchanged downstream steps. Escalate to Codex
  only for ambiguity, design/tradeoffs, approval or external-write boundaries,
  unknown inputs, failed validation, or an unconsumable receipt.
- Apply the single-authority rule to every new routine: store each contract or
  state fact once at its owner, pass references across layers, derive bounded
  summaries at presentation boundaries, and name the authority plus
  invalidation signature before adding a second step, checker, cache, or
  publication.
- Treat information compression as representation deduplication, never
  capability reduction. Keep the consumer's decisions, gates, owner/actions,
  permission boundaries, failure causes, and next steps intact. When these
  cannot fit in the bounded projection, stop compression and provide a stable
  reference or explicit expansion/blocking state; do not silently discard them
  or copy the same contract into another layer to make the summary look full.
- Use `apply_patch` for manual edits.
- Encoding discipline: keep skill trigger/frontmatter metadata in English or
  ASCII when practical, keep knowledge bodies as UTF-8, and judge suspected
  corruption by raw bytes, explicit UTF-8 decoding, hashes/backups,
  validators, and source comparison instead of terminal mojibake alone.

## Routing Preflight

- Identify the smallest useful skill set before loading anything else.
- Say which layer owns the work and which layer only constrains it.
- If the request crosses memory, bridge, GUI, or project rules, name that handoff explicitly.
- Consume `execution_route_pack.asset_guidance` when available. It is a derived navigation aid, not another authority: apply constraints first, read only the selected skills, enter the task-specific owner, then use the tools whose comparative advantage matches the work.
- Act on selected assets without waiting for the user to name them. Using an asset means letting its output change the investigation, implementation, or validation path; availability probes and usage-proof calls do not count as useful work.
- Skip an asset when the task is simple, its boundary excludes the requested truth, or a more specific owner already covers the need. Runtime incidents stay with runtime owners; static code graphs are supporting context only when source structure is genuinely in scope.

## When to Load References

- Load a reference only after routing selects the matching scenario and the
  summary in this file is insufficient for the next decision; do not preload
  the reference directory for unrelated tasks.

## Validation Layer

- For framework changes, check three things: correct layer choice, safe handoff, and no hidden ownership drift.
- Prefer a quick validation pass first; use deeper validation only when a change affects routing boundaries or repeated regressions.
- If a skill or handoff rule changes, validate against a few realistic prompts before treating it as stable.
- Forward validation should test route guidance, not audit completed turns: shared-code work should surface graph advantages, runtime incidents should retain their runtime owner, external research should surface the resource owner, and simple tasks should stay quiet.
- For the current scenario/routing/validation tables, read [scenario-routing-validation.md](references/scenario-routing-validation.md) when you need the concrete matrix.
- After system-level work that runs an iteration/finalization gate, the final
  response must surface any pending proposal groups, recommended actions, or
  approval blocks for the user to review. Do not report only that validation or
  iteration passed; a passed gate is evidence that the proposal workflow is
  healthy, not a substitute for showing the proposals.
- A pending approval card must contain the concrete review items, not only an
  owner status, count, or generic reminder. If the owner cannot supply item
  details, mark the evidence incomplete and repair the owner/closeout contract
  before requesting approval. Do not surface completed fallback evidence as a
  pending approval, and do not duplicate an item already carried by another
  concrete closeout queue.

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.

## Output Contract

- Return a routing decision first, then the reason.
- If a lower layer is required, say why it is the next layer.
- Do not claim execution ownership for this skill.

## Memory Routing

- `MEMORY.md`: lightweight index and navigation hints, not a second durable
  store.
- `local-pmb-memory`: durable, reusable, workspace-scoped facts and evidence.
- Owner-managed checkpoints: project recovery, handoff, rollback, and
  milestone evidence.
- `knowledge_set`: temporary cross-agent coordination only; it is not the
  long-term authority.
- Default technical-work flow: do a quick memory pass before fresh reasoning.
  Read `MEMORY.md` first, then use `hub.pmb_prepare` or `hub.pmb_recall` when
  durable history is relevant. Follow the configured MCP chain after a Hub
  failure; the local read-only fallback is
  `python _bridge\local_pmb_memory.py pmb-recall`.
- During long-lived system work, treat memory as a navigation layer throughout
  the task. It should influence where to look, which prior root causes to test,
  which commands to reuse, and whether a result belongs in memory, skills,
  baseline, docs, or command registries.
- Quick-pass order: `MEMORY.md` -> PMB prepare/recall -> owner-managed
  checkpoint or rollout evidence only when the answer still needs detail.
- Skip rule: if a layer is skipped, state the reason briefly instead of
  silently skipping it.
- Do not escalate to a broad memory sweep unless the question is broad,
  cross-project, or the quick-pass returns a direct pointer.
- Never store secrets, cookies, passwords, raw transcripts, full logs, or
  one-off guesses.
- Query memory on demand by project/task. Do not load all history by default.
- After nontrivial work, evolve memory only from verified durable facts:
  reusable conclusions and compact evidence go to PMB, while recovery,
  rollback, runbook, and milestone evidence remains in its owner-managed
  checkpoint surface.
- Write-back rule: durable memory changes go through
  `_bridge/memory_governance.py`; short bridge-facing facts go to
  `knowledge_set` only when cooperating agents need them during active
  coordination.
- If a fresh turn reuses a prior conclusion, cite the memory layer consulted
  in the final answer or state that no memory lookup was needed.
- Use `_bridge/memory_governance.py snapshot|doctor|repair-plan|validate|metrics`
  to inspect memory-loop health, pending candidate notes, PMB availability, and
  whether memory work rules are present. These commands are read-only except
  for explicit downstream repair commands they may recommend.
- Prefer project-scoped checkpoints/config over conversation-scoped summaries.
  When a project-specific config exists, it overrides default workflow rules.

## Skill Routing

- Skill install/create/share/cleanup: `skill-installer`, `skill-creator`,
  `plugin-creator`, `skill-share`.
- Creative design, feature shaping, or open-ended behavior changes:
  `brainstorming` first.
- Do not use `brainstorming` as a blocker for already-approved plans,
  evidence-driven repairs, operations, skill maintenance, or narrow config
  fixes; proceed with the relevant project/diagnostic skill and file-edit
  rules.
- Debugging, crashes, regressions, or root-cause work: `diagnose`.
- Codex/OpenAI/current official behavior: `openai-docs`.
- Windows shell/process/permission/MCP tool-use issues: `windows-codex-ops`.
- Windows audio playback, active speaker output, app mute/volume state, local
  player control, or audio-file operations: `windows-audio-ops`.
- Weixin/OpenClaw mobile bridge delivery, app-server routing, CDP fallback,
  worker state, queue recovery, phone-side stop/status/resume, status
  acknowledgements, supplements, or attachment ingress:
  `mobile-weixin-bridge-ops`.
- Project checkpoints, PMB changes, memory conflict resolution, or durable
  learning decisions: `memory-checkpoint-ops`.
- MCP design or gateway quality: `mcp-builder`.
- Memory systems: `memory-systems`; query local memory tools for facts.
- Multi-agent/Reasonix/handoff: `multi-agent-patterns` plus bridge rules.
- Browser/UI testing: `playwright`, `webapp-testing`,
  `browser:control-in-app-browser`, `chrome:control-chrome`, `agent-browser`.
- Desktop GUI operations: route first to `gui-automation`; then add the
  smallest app-specific skill such as `gui-app-weixin`, `gui-app-notepad`, or
  `gui-app-formatfactory` only after the target app is known. After meaningful
  GUI success/failure, route to `gui-skill-evolution` for candidate/trusted/
  failed classification.
- External docs: `find-docs`, `context7-cli`, `context7-mcp`.
- End-to-end evidence collection, whitepaper production, multi-format delivery,
  and synchronized public-site publication: route to `whitepaper-pipeline`,
  which then hands each stage to the existing research, document, Office,
  frontend, GitHub, and browser-validation skills.
- Security: use `security-best-practices` or `security-threat-model` only on
  explicit security requests.
- Migration audits: `bringyour-migration-auditor`.
- Images: `imagegen`, or `minecraft-imagegen` for Minecraft visuals.
- `developer-growth-analysis`: use only when the user explicitly asks for a
  developer-growth report, recent-work learning report, or Slack-delivered
  growth analysis. Do not trigger it for ordinary retrospectives, memory
  updates, or skill maintenance.
- Slack/Rube-dependent skills (`skill-share`, `developer-growth-analysis`):
  verify access before relying on those integrations.

## Project/Minecraft Routing

Prefer project skills for this workspace:

- `workspace-knowledge`: 3c3u paths, known state, identities, incidents,
  Reasonix/bridge setup.
- `mcsmanager-fabric-mc`: MCSManager, AutoModpack, configs, Concerto, logs,
  server ops, client launch.
- `fabric-mc-architecture`: Fabric Loader, Mixin, AutoModpack internals,
  ClientModLoader.
- `codex-cli`: local Codex config, permissions, MCP, Codex/Reasonix integration.
- `mc-mod-automation`: client-mods, mod classification, ghost configs, helper
  mod deployment.

Use generic Minecraft skills only outside project-specific scope:
`minecraft-modding`, `fabric-mc-26-1-2`, `minecraft-server-admin`,
`minecraft-testing`, `minecraft-plugin-dev`, `minecraft-multiloader`,
`minecraft-commands-scripting`, `minecraft-datapack`,
`minecraft-resource-pack`, `minecraft-world-generation`,
`minecraft-worldedit-ops`, `minecraft-essentials-ops`, `minecraft-ci-release`.

## Token And Learning Discipline

- Do not statically merge skill bodies.
- Use `context-compression` only for long-context handoff, explicit requests, or
  visible context bloat.
- Use `self-improvement` only after failures, user corrections, verified new
  facts, skill changes, or reusable lessons.
- If no durable fact changed, do not write memory or edit skills.
- If a skill became wrong or inefficient, propose the update before editing when
  project rules require confirmation.
- Promote a lesson into a skill only when it is reusable, verified, and reduces
  future mistakes or token use; otherwise keep it as a project checkpoint or
  short vector memory.
- When a lesson changes operational semantics, encode it in the relevant skill
  and maintenance checks, not only in memory. Memory can preserve the evidence;
  skills should carry the reusable procedure.
- Before broad skill maintenance or framework tightening, run the maintenance
  chain first: encoding -> quick validation -> contract audit -> overlap hints
  -> task-specific forward validation.
- Treat skill creation, deletion, rename, and content/resource edits as lifecycle
  events. Let the workspace skill owner run its incremental refresh before
  routing; unchanged skills reuse the persistent index, while changed skills
  re-enter validation and routing quarantine automatically when needed.
- Do not repeatedly verify stable facts unless they are drift-prone. Ports,
  running processes, permissions, versions, and external services are
  drift-prone; static code paths and freshly validated module boundaries are
  not.
- In reports, distinguish scope from permanence: say "this pass did not change
  X" for current-scope exclusions, and reserve "do not change X" for durable
  constraints.

## Scripts

- `scripts/check_encoding.py`: read-only UTF-8 and metadata risk audit for
  skill files. Use it before repairing apparent mojibake or after skill edits.
- `scripts/check_skill_contracts.py`: read-only contract audit for selected
  skills. Use it during framework iteration to catch missing boundary, scope,
  handoff, or reference-gating sections in high-risk skills before or after
  edits.
- `scripts/check_skill_overlaps.py`: read-only overlap hint checker for broad
  frontmatter descriptions. Use it to spot routing/execution/method skills that
  may be competing for the same user intent and need manual boundary review.

## Maintenance Chain

Use this sequence for framework iteration and high-risk skill maintenance:

1. `scripts/check_encoding.py`
2. `.system/skill-creator/scripts/quick_validate.py`
3. `scripts/check_skill_contracts.py`
4. `scripts/check_skill_overlaps.py`
5. Small forward validation against realistic task prompts
