# Maintenance Selection Guide

Load this guide only when registry results are ambiguous or a new surface is being considered.

## Selection Guide

- Unknown or cross-domain task: start with `workflow_orchestrator.py plan`.
- Need to audit global mechanism redundancy, contradiction, ownership drift, or
  cross-layer proposal/closeout/tool-routing fragmentation: use
  `global_coherence_doctor.py doctor|validate`. It aggregates existing
  read-only owners and does not repair or grant authority.
- Adding or changing a system member, MCP route, Hub adapter, resource owner
  route, startup-affecting tool, or architecture surface: use
  `system_membership.py plan|impact|upgrade-plan|validate` before closeout so
  required synchronized surfaces and the member's exit strategy are explicit.
- Retiring or removing a system member: use
  `system_membership.py retirement-plan|retirement-signal|repair-plan|validate`.
  Remove every active registration, generator, route, runtime, maintenance,
  guidance, dependency, and authority trace through its owning surface. Keep
  only the startup baseline's minimal machine-readable negative tombstone.
  Historical backups, checkpoints, archives, and migration sources may remain
  as isolated evidence, but they must never route, generate, recommend, repair,
  or validate the retired member as current.
- Planning non-trivial maintenance or upgrade work for an existing system: use
  `maintenance_upgrade_governance.py plan` to produce task-specific batches and
  evidence needs. It points to existing tool policies; it does not define a new
  CodeGraph/SQLite/validator priority chain.
- Need online/external resources or direct generic web fallback: use
  `online_access_gate.py plan|check|exception|validate`. Generic web is valid
  only with resource-layer unavailable/route-exhaustion evidence, explicit
  user direct-web instruction, or an explicit structured flag proving that a
  higher-precedence platform instruction required generic web. The platform
  flag is never inferred from natural language.
- Need a compact machine-first entry package for actual Codex work: use `codex_workflow_entry.py plan|preflight|closeout`. It wraps existing owners and does not execute business actions. At closeout, use its package as the primary entry; work notes are one field inside that package, not a parallel closeout path.
- Need to promote lessons after broad system work: use `iteration_layer_review.py`
  or `mobile_openclaw_cli.py maintenance iteration` as the read-only proposal
  gate. Even after approval, promote only the specific verified rule or
  validation entrypoint, not raw recent-file metadata. Run the referenced
  validation command before writing and re-read the target after writing.
- Skill choice unclear, MySkills underused, or a skill trigger may be missing:
  use `skill_orchestrator.py plan`. It is a routing/evidence layer only. It may
  propose MySkills scenario assignments or new-skill gaps, but applying those
  proposals still goes through MySkills with explicit approval.
- Need to know whether memory should be loaded: use `codex_workflow_gate.py memory-preflight`.
- Codex Desktop restart/resume is slow or old conversations recover poorly: use
  `codex_session_store_doctor.py doctor|repair-plan` first. Treat oversized
  active JSONL transcripts as restore-performance risk and use compression only;
  storage maintenance must not create continuation tasks, move sessions, archive
  sessions, or delete sessions. Restart-boundary maintenance may run
  `auto-maintain --apply --boundary pre-launch` through the governed Desktop launcher;
  it skips while Codex is running, backs up before rewriting, preserves
  messages and existing compacted context blocks, and only compacts event/tool
  output/reasoning payload bloat. Its non-blocking owner lock uses PID plus
  process-creation identity so an old PID-reused lock cannot suppress maintenance.
- Codex Desktop model choices or reasoning levels drift after CC Switch changes
  provider: use `codex_model_provider_watcher.py snapshot|validate|once`. The
  hidden `CodexModelProviderWatcher` task starts at logon, keeps its event log
  bounded by single-file tail compaction, never reloads Desktop for renderer-local
  drift repair, and has an `IgnoreNew`-guarded
  five-minute recovery trigger as a fallback. Its supervisor immediately replaces
  a watcher child that exits with the implementation-change code, with a bounded
  anti-spin restart window. The two-second hot path uses file metadata probes;
  full implementation/source hashing and projection run only after a relevant
  input changes. The source signature includes the CC Switch provider ID,
  provider configuration fingerprint, and model-catalog declaration/hash; a
  provider change is reconciled only after two stable source polls. Unreadable
  sources, unresolved catalog provenance, and reconcile failures use bounded retry,
  and events retain compact decision evidence instead of complete source
  structures. Source changes are still detected on the two-second loop; the
  unchanged-state Desktop runtime-binding self-heal probe runs every ten seconds,
  with the five-minute scheduled trigger retained as an independent fallback.
  It watches `config.toml` and the active catalog plus the active
  Desktop AppServer module signature. Current Desktop
  builds are repaired at the real `list-models-for-host` AppServer request
  boundary and the local `models/list` React query is invalidated through the
  incoming `ipc-broadcast` path; the Electron bridge remains an old-build
  fallback. The same reconcile pass refreshes reasoning fields and Statsig
  state without editing provider configuration, then requests one Desktop page
  reload for a confirmed provider change. A failed hot refresh records
  `restart_required` as a terminal user-visible result instead of retrying in a
  loop; watcher implementation reloads use a separate internal restart signal
  and never imply that Codex itself must restart. Install or repair it through
  `install-codex-model-provider-watcher-task.ps1`; the elevated launcher also
  starts the same supervisor as a fallback. The governed launcher serializes with
  a named mutex and exclusive file-lock fallback, treats process-census failure as
  unknown rather than empty, and permits stale-process force cleanup only after
  explicit `CODEX_ALLOW_STALE_CODEX_CLEANUP=1` opt-in.
- Need PMB context for work: use Hub PMB tools first. The Hub may restart the configured idle-exited daemon once and retry the same read-only call. Continue forward through the capability-matrix fallback chain only after Hub recovery fails; use `_bridge/local_pmb_memory.py pmb-prepare` or `pmb-recall` only at the local CLI stage.
- Need code structure through CodeGraph: query with the exact file path and
  domain-specific symbols first. If CodeGraph returns a wrong area because the
  query used generic symbols, retry with tighter anchors before treating the
  index as unhealthy. Do not use CodeGraph as the first choice for broad
  Markdown/config/rule lookups; use `rg` with generated-tree exclusions. If a
  prior CodeGraph result reported auto-sync disabled, verify current state with
  protocol smoke or the project-local CLI before carrying that diagnosis
  forward; a later smoke showing watcher active supersedes the old observation
  for current work. If native CodeGraph is not current-turn callable, use Hub
  `codegraph_explore` before dropping to local CLI or `rg`.
- Need indexed state or record evidence: use SQLite MCP/Hub query first for
  `.sqlite`/`.db`, record-store, email-state, scheduler, queue, receipt, or
  database-backed evidence. Use raw file scans only after a bounded SQLite
  query is unavailable, insufficient, or points to a specific source path.
- Native MCP seems unstable: use `mcp_session_doctor.py validate` before Hub fallback.
- Need tool route selection data for Codex internal planning: use `mcp_capability_routes.py build|lookup|validate`; the generated JSON is machine-first and derived from the Markdown matrix.
- Need to acquire or classify a resource without hand-building tool calls: use
  `resource_cli.py request` as the Codex-facing broker entry. It may execute
  local safe `resource_cli` fetch/probe/materialize paths and returns a
  receipt plus a persisted manifest under the resource store. If the owner is
  an MCP/browser/domain/package-manager tool, treat `handoff_required` as an
  internal intermediate state for the same resource need: continue through the
  resource layer, call the requested owner tool or resource-layer-selected web
  route under its own permission boundary, then attach the result to the same
  request with `resource.attach_result` or `resource_cli.py attach-result`.
  Do not treat probe-only metadata as fetched content, and do not start an
  independent replacement fetch while `same_need_fetch_allowed=false`.
- Many MCP/resource processes exist: use `resource_process_doctor.py doctor`.
- Frequent blue PowerShell or black console windows appear: use `popup_window_doctor.py snapshot|observe` to classify Codex shell, MCP descendant, scheduled-task, workspace-service, and unknown sources before changing startup policy.
- Need an agent-native CLI for a GUI app, desktop app, codebase, or workflow: use the installed `cli-anything` skill and `_bridge/cli_anything_governance.py search|info|validate`. `cli-hub` discovery is trusted; installing a concrete harness still needs explicit task intent and post-install validation.
- Need to absorb memory/knowledge project ideas: use the existing local memory
  governance stack first. Treat `agent-memory-engine`, `ArcRift`, and
  `localmem` as references for local-first, evidence-backed, lifecycle-aware
  memory patterns, then route any durable change through
  `memory_governance.py` and `_bridge/external_knowledge.py`.
- Need base development tools for code work: first check
  `code_maintainability.py toolchain`, then use `rg`, `fd`, `uv`, `uvx`,
  `ruff`, or Playwright as utility accelerators for the owning workflow. They
  are not independent permission or validation authority.
- New edit backup or scattered backup cleanup: use backup router first, then `backup_hygiene_doctor.py`.
- One-shot current-task reminders: use `memory_governance.py work-note-add/read/clear`; after the main task, Codex first reads `codex_workflow_entry.py closeout`, then processes the package's work-note entries as a follow-up queue before final reply. Processing is automatic only for read-only analysis, inspection, validation, planning, and proposal drafting. Work-note-derived writes or external actions need separate explicit approval because the main task's authorization does not carry over. Good candidates include non-blocking tool routing observations, wrong-area index/search results, expected MCP namespace misses, fallback-use reasons, and stale matrix/rule hints discovered while the main task continues.
- Draft artifacts: store them in `_bridge/shared/drafts/` with explicit `Content maturity`, `Workflow status`, and `Pending action` fields. The directory is not a queue and is never a closeout trigger. Pending review uses the persistent `_bridge/workflow_review_queue.py` owner with an `artifact_ref`; closeout renders only `pending` rows, and `dispose` records the approved/revised/rejected/resolved/deferred/discarded outcome so handled cards do not repeat. `retained_reference` uses `Pending action: none`. Validate with both `draft_governance.py validate` and `workflow_review_queue.py validate`.
- Note, PMB, or memory cleanup: use `memory_governance.py` plans; apply note absorption only through explicit approved ids with `apply-approved --confirm-apply`; apply PMB fact cleanup only through `pmb-fact-repair-plan` followed by `pmb-fact-apply-approved --confirm-apply`, which writes review markers and does not delete or rewrite PMB events. After absorption, use `recall-checks` to keep retrieval verification visible and `recall-verify` for read-only local index/PMB evidence.
- Adding or repairing a slash template: use `slash_command_governance.py snapshot|proposal|validate|apply --confirm-apply|render-smoke`. For slash/scratch coordination metadata issues, use `tool_coordination.py validate`.
- Need to improve code maintainability or choose a refactor target: use `code_maintainability.py snapshot|module-context|module-assets|lookup-module|plan|validate` first and apply `_bridge/docs/code_maintainability_guidelines.md` for naming, structure, error handling, validation, and refactor boundaries. Use `module-assets --task-mode maintenance` for system maintenance/governance/owner-boundary work, and `module-assets --task-mode code` for implementation/reuse/scenario-fit work. Build `_bridge/runtime/module_capability_index.json` and `_bridge/runtime/module_asset_catalog.json` when missing or stale; both are derived caches, not source of truth. Apply focused incremental refactors separately with backups and validators.

## Anti-Sprawl Checks

Before adding a new doctor or governance script, answer yes to all:

1. No existing surface owns this state.
2. The new surface will not duplicate another surface's diagnosis.
3. It has clear `snapshot`/`doctor`/`repair-plan`/`validate` or a deliberate smaller equivalent.
4. It is read-only by default.
5. It has a bounded validation command.

If any answer is no, extend the existing owning surface instead.
