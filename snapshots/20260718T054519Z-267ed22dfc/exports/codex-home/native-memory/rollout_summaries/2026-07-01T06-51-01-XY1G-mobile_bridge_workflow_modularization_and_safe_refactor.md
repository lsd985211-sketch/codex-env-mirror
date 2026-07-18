thread_id: 019f1c72-03c3-7032-aa56-dff625d7c720
updated_at: 2026-07-05T17:21:31+00:00
rollout_path: \\?\C:\Users\45543\.codex\sessions\2026\07\01\rollout-2026-07-01T14-51-06-019f1c72-03c3-7032-aa56-dff625d7c720.jsonl
cwd: C:\Program Files\WindowsApps\OpenAI.Codex_26.715.2305.0_x64__2p2nqsd0c76g0\app\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager

# Iterative safe modularization of the bridge/workflow codebase with validation-first refactors

Rollout context: the work happened in `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager` and centered on the `_bridge` Python codebase. The user wanted a script/refactor process that was stable and accurate, with conservative handling of risky cleanup logic. The later part of the rollout shifted into a broader Codex-environment maintenance batch, but the refactor batch itself completed with compile/lint/owner validation and a rebuilt module index.

## Task 1: safe modularization and verification of bridge/workflow code

Outcome: success

Preference signals:

- the user repeatedly asked for “验证脚本可行性，做出优化，要求稳定准确” and earlier insisted on “安全准确” behavior for cleanup logic -> future changes should be verified with real tool output, not just assumed from code inspection
- when ghost/config cleanup was discussed, the user explicitly warned “判断幽灵配置一定需要谨慎，防止误删有用的配置” -> destructive cleanup should default to conservative reporting and require a complete inventory before deletion
- when script automation was requested, the user emphasized it should be generic rather than hardcoded to specific MODs -> future automation should be data-driven and reusable
- the user repeatedly asked for backup-before-edit behavior (and the repo workflow used backups before each code edit) -> future nontrivial edits should preserve rollback backups first
- the user accepted modularization only when it remained safe and behavior-preserving -> prefer extracting pure helpers with facades left in place until validation passes

Key steps:

- Ran module-boundary discovery via `python _bridge\code_maintainability.py module-context --term ...` before deciding where to place new code.
- Created backups with `_bridge\shared\backup_router.py create ...` before edits.
- Extracted pure planning helpers into a new module `_bridge/workflow_plan_build_steps.py` and rewired `workflow_orchestrator.build_plan` to use them without changing plan schema.
- Split `capability_tokens.grant` into pure helpers: request validation, expiry-policy calculation, and grant-item construction, while keeping write/audit behavior in the original function.
- Moved observability metrics into `_bridge/mobile_openclaw_bridge/mobile_observability_metrics.py` and kept the original maintenance entrypoint as a compatibility facade.
- Continued extracting diagnosis rule groups out of `mobile_maintenance.py` into `mobile_diagnosis_issue_rules.py` so the main file could shrink while keeping the existing behavior accessible.
- Rebuilt `_bridge/runtime/module_capability_index.json` after adding helper modules so later routing decisions would see the updated ownership graph.
- Used a local stdio fallback for supplement retrieval when the native MCP transport returned `Transport closed`; the fallback returned an empty batch and confirmed there were no pending supplements.

Failures and how to do differently:

- `ruff` on `regression_checks_capability.py` surfaced many undefined names, but that file is intentionally rebound into CLI globals via `FunctionType(..., env)`; future linting should target only the changed owner files unless the test harness itself is being rewritten.
- `git diff --stat` with the chosen file list triggered a pathspec usage error in this environment; use `git status --short`, direct file inspection, or timestamps instead of assuming every git diff form will work.
- Some candidate extractions, especially parts of `mobile_bridge_mcp_server` self-tests and `mobile_maintenance.inspect_system`, were intentionally deferred because they would have introduced boundary problems or circular dependencies; these should be handled only after a clearer dependency-injection or probe/snapshot boundary is designed.
- The rollout showed that cleanup logic must be conservative: an incomplete inventory or narrowly scoped pattern list caused over-deletion in earlier experiments, so later logic should prefer full inventory + reporting over automatic deletion.

Reusable knowledge:

- `workflow_orchestrator.py` has been reduced by moving pure build-step logic into `_bridge/workflow_plan_build_steps.py`; the main schema and validation behavior stayed intact.
- `capability_tokens.py` now uses helper functions `grant_request_error`, `grant_expiry_policy`, and `build_grant_item`, which makes the grant path easier to test without changing the permission boundary.
- The maintainability validator (`python _bridge\code_maintainability.py validate`) still passes after the refactor batch, and its output remains the primary gate for verifying module boundary health.
- The module index was successfully rebuilt with `python _bridge\code_maintainability.py build-module-index --all-bridge --limit 1000`, covering 177 `_bridge` modules; future new helper modules should be followed by an index rebuild.
- The local fallback command for mobile supplement retrieval is `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id <thread_id> --timeout-seconds 8`.
- `python _bridge\mobile_openclaw_bridge\mobile_bridge_mcp_server.py --self-test` passed after the refactor batch, so the MCP bridge remained intact while surrounding modules were split.
- `python _bridge\mobile_openclaw_bridge\mobile_maintenance.py metrics --no-deep` returned code 0, confirming maintenance metrics still ran after the extraction work.

References:

- [1] Backup-before-edit examples: `_bridge\shared\backup_router.py create _bridge\mobile_openclaw_bridge\capability_tokens.py ...` and `_bridge\shared\backup_router.py create _bridge\workflow_orchestrator.py ...`
- [2] New module: `_bridge/workflow_plan_build_steps.py` with ownership docstring and pure helpers (`collect_domain_routes`, `build_skill_orchestration`, `phase_execution_summary`, `skill_orchestration_summary`)
- [3] `workflow_orchestrator.py` import update: now imports helpers from `workflow_plan_build_steps` and uses them inside `build_plan`
- [4] `capability_tokens.py` helper extraction: `grant_request_error`, `grant_expiry_policy`, `build_grant_item`
- [5] Validation evidence: `python -m py_compile ...` passed for changed files; `ruff check` passed on the changed owner files; `python _bridge\workflow_orchestrator.py validate` passed; `python _bridge\mobile_openclaw_bridge\mobile_bridge_mcp_server.py --self-test` passed; `python _bridge\code_maintainability.py validate` passed
- [6] Local supplement fallback evidence: `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id 019eca51-3ce9-76e2-9795-83f3af451f3a --timeout-seconds 8` produced `ok: true`, `fallback: local_stdio_mcp`, and an empty `thread_pending` batch
- [7] Closeout evidence: `python _bridge\codex_workflow_entry.py closeout` returned `work_notes.active_count: 0` and no pending proposals, so there was no remaining persisted closeout work from the refactor batch
- [8] The maintainability validator’s latest output still lists large-file risks elsewhere in `_bridge`, but the changed modules and their validation gates were clean at the end of the batch
