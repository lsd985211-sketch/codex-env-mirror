
# Task Group: mcsmanager awesome-selfhosted research reports and approved memory absorption

scope: Cited Markdown research reports and governed absorption of explicitly approved iteration candidates.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse the report/governance workflow in this checkout family, but re-fetch repo facts and require new explicit approval

## Task 1: analyze awesome-selfhosted, append 20 categorized project analyses, and absorb six approved candidates, success

### rollout_summary_files

- rollout_summaries/2026-07-10T07-51-07-5TU4-awesome_selfhosted_report_20_projects_and_memory_absorption.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\07\10\rollout-2026-07-10T15-51-09-019f4b02-4562-7f83-a1c9-e0154223a2f8.jsonl, updated_at=2026-07-17T05:00:49+00:00, thread_id=019f4b02-4562-7f83-a1c9-e0154223a2f8, completed)

### keywords

- awesome-selfhosted, awesome-selfhosted-项目分析报告.md, GitHub API, raw README, 20 个值得重点关注的项目, workflow_review_queue.py, workflow_iteration_owner.py, candidate_not_approved, memory_absorption_index.json

## User preferences

- when requesting research, the user asked: "将分析写成报告文件，格式md文件，附带主要内容的引用链接" -> create a Markdown artifact with inline primary-source links [Task 1]
- when extending a list, the user asked: "逐个分析，整理分类，同样为主要内容附上引用链接" -> use individual entries and category grouping [Task 1]
- when approving candidates, the user said "批准吸收" after exactly six were enumerated -> approval is scoped to that list only [Task 1]

## Reusable knowledge

- The report is `awesome-selfhosted-项目分析报告.md`; use GitHub metadata, contents, README, commits, releases, raw README, and homepage evidence, then read it back. Its appended section is `## 十二、从 awesome-selfhosted 中筛出的 20 个值得重点关注的项目`. [Task 1]
- In PowerShell, use `@' ... '@ | python -`; `python - <<'PY'` raises `ParserError: Missing file specification after redirection operator.` Scan raw README headings/lines before final candidate selection. [Task 1]
- Absorption path: approve, `workflow_iteration_owner.py plan`, `apply --confirm-apply`, `validate`, resolve. The owner writes `C:\Users\45543\Desktop\Codex资源库\memory\governance\memory_absorption_index.json`; apply backs up and validate/readback must pass. [Task 1]

## Failures and how to do differently

- Symptom: `candidate_not_approved`. Cause: item remains pending. Fix: transition explicitly approved items before planning; never expand the approval scope. [Task 1]
- Symptom: `source_assets_changed` after absorption. Cause: unrelated concurrent drift. Fix: keep valid absorption evidence separate; do not redo items solely for mirror staleness. [Task 1]

# Task Group: mcsmanager CC Switch proxy logging crash mitigation

scope: Diagnose CC Switch auto-exit around logging and apply the narrow DB-backed log-level mitigation without changing routing.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager with runtime data at C:\Users\45543\.cc-switch; reuse_rule=reuse for this local installation only after inspecting current source and DB

## Task 1: diagnose logging-path crash and apply approved medium mitigation, success

### rollout_summary_files

- rollout_summaries/2026-06-28T16-49-54-n31u-cc_switch_logging_crash_mitigation.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\06\29\rollout-2026-06-29T00-49-57-019f0f23-37a4-78b3-ab69-500913b42310.jsonl, updated_at=2026-07-17T13:32:49+00:00, thread_id=019f0f23-37a4-78b3-ab69-500913b42310, completed)

### keywords

- cc-switch.exe, cc-switch.db, log_config, proxy_config, forwarder.rs, tauri_plugin_log, os error 232, c0000409, BEX64, 127.0.0.1:15721, quick_check

## User preferences

- before changes, the user said "先不要修改，只做计划" -> provide read-only diagnosis and a concrete plan [Task 1]
- after evidence, the user said "批准中等方案" -> make a bounded change that preserves proxy/provider/Codex settings and verify it [Task 1]

## Reusable knowledge

- Runtime: `C:\Users\45543\AppData\Local\Programs\CC Switch\cc-switch.exe`, DB `C:\Users\45543\.cc-switch\cc-switch.db`, proxy `127.0.0.1:15721`. The verified setting after backup was `settings.log_config={"enabled":true,"level":"error"}`. [Task 1]
- `proxy_config.enable_logging` does not remove the `forwarder.rs` `log::info!` path. `level="error"` maps to `LevelFilter::Error`; validate with SQLite `quick_check`, unchanged `proxy_config`, and a listening proxy port. [Task 1]

## Failures and how to do differently

- Anonymous GitHub code search was rate-limited; use the authenticated local GitHub hub and temporary source zip for read-only grepping. Prefer source/DB evidence over noisy GUI attempts. [Task 1]

# Task Group: mcsmanager workflow closeout bounded output and mirror verification

scope: Global CLI output projections for closeout workflows and post-closeout mirror publishing.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for `_bridge` workflow governance, but treat mirror freshness as time-sensitive

## Task 1: implement bounded closeout projection with distinct default and full modes, success

### rollout_summary_files

- rollout_summaries/2026-06-20T07-35-55-D3iv-global_bounded_output_governance_closeout_full_mode.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T15-35-55-019ee3f5-27e9-7d20-9cf5-802aaef0e1af.jsonl, updated_at=2026-07-16T14:33:28+00:00, thread_id=019ee3f5-27e9-7d20-9cf5-802aaef0e1af, tests and publish passed)

### keywords

- bounded_output.py, default_bounded, failure_bounded, full_bounded, --full-output, closeout_cli_projection, safe_next_step, manual_action, post_closeout_mirror, raw_result_ref, source_assets_changed

## User preferences

- the user said "命令输出只展示有价值部分，这应该是全局要求" and "输出很大" -> default terminal output must be compact and decision-focused [Task 1]
- the user corrected: "那样两者就没有区别了" -> default is an actionable summary; `--full-output` must remain richer but bounded [Task 1]

## Reusable knowledge

- `_bridge/bounded_output.py` is the shared contract: `default_bounded`, `failure_bounded`, `full_bounded`. Preserve `reason`, `next_action`, `safe_next_step`, `manual_action`, `decision_evidence`, `finalization`, and `post_closeout_mirror`. Raw packages belong at `record_path` / `raw_result_ref`. [Task 1]
- Publish is post-closeout; inspect `finalization.post_closeout_mirror.result.push.remote_verification` only after edits cease. Recorded gates: `maintenance_control_plane_tests.py` 37 tests, `workflow_closeout_package_tests.py` 10 tests, `workflow_orchestrator.py validate` 40/40. [Task 1]

## Failures and how to do differently

- Symptom: a projection hides finalization or next action. Fix: improve the shared preserve/priority policy, not a one-off test special case. `source_assets_changed` during edits is expected; finish, close out, then recheck. [Task 1]

# Task Group: mcsmanager mobile OpenClaw bridge worker idle-backoff repair

scope: Minimal, regression-driven worker activity detection changes and paused-state closeout.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for worker-loop behavior here, preserving live STOP_REQUEST and checking current bridge state

## Task 1: remove skipped-only retries from worker activity detection, success

### rollout_summary_files

- rollout_summaries/2026-07-04T06-00-52-3NvG-mobile_worker_idle_backoff_fix.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\07\04\rollout-2026-07-04T14-00-54-019f2bb7-2d6d-7963-a33a-a14dfbf1f238.jsonl, updated_at=2026-07-16T10:18:22+00:00, thread_id=019f2bb7-2d6d-7963-a33a-a14dfbf1f238, minimal fix and tests passed)

### keywords

- worker_loop_has_activity, worker_loop_observability.py, pending_reply_retries.skipped, skipped-only, skipped_busy_route, idle backoff, STOP_REQUEST, fair-scheduling-check, backup_router.py validate --root

## User preferences

- the user said "不要引入新的漏洞" -> use a minimal single-point repair, a narrow reproducer, and regression validation [Task 1]
- after "继续", continue the verification/closeout chain without restating the task [Task 1]

## Reusable knowledge

- Remove only `int(pending_retry.get("skipped") or 0)` from activity counting. A pure reproducer with `action=idle`, `processed=0`, `scheduled=0`, `skipped=3` must become inactive; scheduled, processed, and `skipped_busy_route=1` remain active. [Task 1]
- Back up first and validate with `backup_router.py validate --root <backup-dir>`. The intended state was paused: `STOP_REQUEST` present and worker down. `maintenance summary` skips deep probes; `maintenance iteration` is proposal-only. [Task 1]

## Failures and how to do differently

- `reply-pending-account-scope-check` can raise `KeyError` through the facade: use its owner module. For `fair-scheduling-check`, temporarily override the stop path in-process; never delete the live marker. Wait for closeout helper processes before final status. [Task 1]

# Task Group: mcsmanager bridge and workflow validation-first modularization

scope: Conservative `_bridge` module extraction, ownership discovery, compatibility facades, and behavior-preserving verification.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for `_bridge` refactors after deriving current ownership; source metadata recorded a packaged-app cwd

## Task 1: modularize bridge/workflow helpers with validation, success

### rollout_summary_files

- rollout_summaries/2026-07-01T06-51-01-XY1G-mobile_bridge_workflow_modularization_and_safe_refactor.md (cwd=C:\Program Files\WindowsApps\OpenAI.Codex_26.715.2305.0_x64__2p2nqsd0c76g0\app\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\07\01\rollout-2026-07-01T14-51-06-019f1c72-03c3-7032-aa56-dff625d7c720.jsonl, updated_at=2026-07-05T17:21:31+00:00, thread_id=019f1c72-03c3-7032-aa56-dff625d7c720, completed)

### keywords

- code_maintainability.py module-context, build-module-index, workflow_plan_build_steps.py, capability_tokens.py, grant_request_error, mobile_observability_metrics.py, mobile_diagnosis_issue_rules.py, supplement-fallback, Transport closed

## User preferences

- the user asked for "验证脚本可行性，做出优化，要求稳定准确" and "安全准确" -> show real validation output and make conservative behavior-preserving changes [Task 1]
- the user warned "判断幽灵配置一定需要谨慎，防止误删有用的配置" -> begin cleanup with complete inventory/reporting, not automatic deletion [Task 1]
- the user wanted generic, data-driven automation and backup-before-edit behavior [Task 1]

## Reusable knowledge

- Start with `python _bridge\code_maintainability.py module-context --term <module_or_feature>`, then run `build-module-index --all-bridge --limit 1000` after helper additions; the recorded rebuild covered 177 modules. [Task 1]
- Proven extractions: `workflow_plan_build_steps.py`, `grant_request_error` / `grant_expiry_policy` / `build_grant_item`, observability/diagnosis helpers with compatibility facades. Validate compile, changed-owner `ruff`, `workflow_orchestrator.py validate`, MCP self-test, metrics, and maintainability. [Task 1]

## Failures and how to do differently

- `regression_checks_capability.py` uses `FunctionType(..., env)`, so lint changed owner files unless redesigning the harness. Defer self-test/inspection extractions that would form circular imports until a dependency-injection or probe boundary exists. [Task 1]

# Task Group: mcsmanager mobile OpenClaw reply protocol and dashboard login access

scope: Exact Weixin owned-result handling, primary/backup boundaries, recovery diagnosis, and dashboard/login access guidance.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for this bridge checkout after verifying live tasks, routes, and login service

## Task 1: diagnose primary visible-CDP owned-result recovery and explain rule loading, partial

### rollout_summary_files

- rollout_summaries/2026-06-20T04-27-13-CjBd-mobile_openclaw_bridge_owned_result_redelivery_and_backup1_b.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T12-27-13-019ee348-662d-7fa0-99c8-3138aa86db2f.jsonl, updated_at=2026-07-12T13:51:08+00:00, thread_id=019ee348-662d-7fa0-99c8-3138aa86db2f, first failure separated from recovery)

### keywords

- mobile_ack, mobile_result_begin, mobile_result_end, protocol_violation_no_owned_result, active_waiting_followup_redelivery, visible_cdp_no_owned_result_manual_after_seconds, reply_to_weixin, backup1, primary, mobile_tasks, mobile_events

## Task 2: record unified dashboard and on-demand QR login entry, success

### rollout_summary_files

- rollout_summaries/2026-06-21T16-20-49-m1fM-weixin_dashboard_login_on_demand_memory.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\22\rollout-2026-06-22T00-20-50-019eeafc-1677-7723-992f-b31590c0fe66.jsonl, updated_at=2026-06-22T17:43:15+00:00, thread_id=019eeafc-1677-7723-992f-b31590c0fe66, primary entry verified)

### keywords

- 18808, /login/, 18790, 微信桥接面板.lnk, OpenClaw 微信登录二维码.lnk, open-dashboard.ps1, generate-weixin-login-qr.ps1, mobile_dashboard.py:2639, browser heartbeat

## User preferences

- exact `ack_first`, `result_after_work_only`, `result_markers_only`, and marker IDs are contractual; do not infer correctness from plain Weixin text [Task 1]
- the user said "它一开始确实没有按格式生成回复，是后面信息重发才按照格式的" -> distinguish original failure from same-thread recovery [Task 1]
- after asking about shortcuts and then saying "记录记忆", the user wanted a direct stable access answer retained durably [Task 2]

## Reusable knowledge

- `codex-cdp` + `primary` may wait after `protocol_violation_no_owned_result`; inspect `mobile_tasks` and `mobile_events` to separate markers, transport/business acceptance, redelivery, and phone visibility. Marker stripping in Weixin display is expected. `backup1` is low-risk Q&A only. [Task 1]
- Stable access: dashboard `http://127.0.0.1:18808/`, QR login `http://127.0.0.1:18808/login/`; `微信桥接面板.lnk` is primary and standalone QR is legacy. Start 18790 on demand at `/login/`; `mobile_dashboard.py:2639` is the anchor. [Task 2]

## Failures and how to do differently

- A later success can be same-thread redelivery, not proof the first turn succeeded: inspect the event chain. Eager QR startup fails without browser heartbeat; start it at `/login/`. [Task 1][Task 2]

# Task Group: Minecraft Fabric 26.1.2 global knowledge skill

scope: Current Fabric client/server/mod/shader/resource-pack guidance and the user-global Codex skill that captures it.
applies_to: cwd=C:\Users\45543\Documents\mc; reuse_rule=reuse the installed global skill across projects for matching Fabric 26.1.2 work, but refresh version-sensitive facts

## Task 1: research Fabric 26.1.2 and install a global skill, success

### rollout_summary_files

- rollout_summaries/2026-06-15T07-48-15-yZEx-fabric_mc_26_1_2_skill_research_and_install.md (cwd=\\?\C:\Users\45543\Documents\mc, rollout_path=C:\Users\45543\.codex\sessions\2026\06\15\rollout-2026-06-15T15-48-15-019eca40-a8ff-72e2-a7da-43b8f9befc65.jsonl, updated_at=2026-07-09T16:24:24+00:00, thread_id=019eca40-a8ff-72e2-a7da-43b8f9befc65, global skill installed)

### keywords

- FabricMC, Minecraft 26.1.2, Fabric Loader 0.18.4, Java 25, Gradle 9.4.0, Fabric Loom 1.15, Mojang official mappings, Iris, Sodium, fabric-mc-26-1-2\SKILL.md, context compression

## User preferences

- the user asked for "信息准确，覆盖面广，具有时效性" plus server/client/mod/resource-pack/shader coverage -> use fresh source-backed information for the complete requested stack [Task 1]
- the user wanted direct explanations of a skill's purpose/global scope, context compression, and `@电脑` [Task 1]

## Reusable knowledge

- Global skill: `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md`. Global skills persist across projects; context compression is system-managed and `@电脑` is the Computer Use GUI-control plugin. [Task 1]
- Recorded version-sensitive facts: Java 25, Loader 0.18.4, Gradle 9.4.0, Loom 1.15, IntelliJ 2025.3+, first unobfuscated Minecraft, Mojang official mappings migration. [Task 1]

## Failures and how to do differently

- If managed permissions block workspace writes or browser setup is flaky, find a writable location early, use the global skill directory, and pivot to the in-app browser runtime after reading its docs. [Task 1]
