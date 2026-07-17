# Task Group: mcsmanager bridge/workflow safe modularization and validation

scope: Conservative refactors inside the `_bridge` Python codebase, especially when the user wants safer automation, behavior-preserving module splits, and validation-backed changes.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for `_bridge` refactor and verification work in this checkout family, but re-check current module ownership and live validators before editing

## Task 1: safe modularization and verification of bridge/workflow code, success

### rollout_summary_files

- rollout_summaries/2026-07-01T06-51-01-XY1G-mobile_bridge_workflow_modularization_and_safe_refactor.md (cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\07\01\rollout-2026-07-01T14-51-06-019f1c72-03c3-7032-aa56-dff625d7c720.jsonl, updated_at=2026-07-05T17:21:31+00:00, thread_id=019f1c72-03c3-7032-aa56-dff625d7c720, validation-first refactor batch completed cleanly)

### keywords

- _bridge, code_maintainability.py, module-context, build-module-index, workflow_orchestrator.py, workflow_plan_build_steps.py, capability_tokens.py, mobile_observability_metrics.py, mobile_diagnosis_issue_rules.py, mobile_bridge_mcp_server.py --self-test, supplement-fallback

## User preferences

- when automation or cleanup logic is being changed, the user repeatedly asked for '验证脚本可行性，做出优化，要求稳定准确' and earlier emphasized '安全准确' -> default to real validation output, conservative edits, and explicit proof that behavior still works [Task 1]
- when ghost/config cleanup was discussed, the user warned '判断幽灵配置一定需要谨慎，防止误删有用的配置' -> destructive cleanup should prefer full inventory + reporting over automatic deletion [Task 1]
- when automation was requested, the user pushed for generic tooling rather than hardcoded examples -> prefer data-driven helpers and reusable modules instead of one-off scripts [Task 1]
- before nontrivial edits, the user repeatedly reinforced backup-before-edit behavior -> create rollback points first and keep facades in place until validation passes [Task 1]

## Reusable knowledge

- Before placing new `_bridge` logic, run `python _bridge\code_maintainability.py module-context --term <module_or_feature>` so module ownership and boundaries come from the repo’s own index instead of guesswork [Task 1]
- After adding helper modules, rebuild the derived ownership graph with `python _bridge\code_maintainability.py build-module-index --all-bridge --limit 1000`; the successful refactor batch rebuilt coverage for 177 `_bridge` modules [Task 1]
- Safe extractions that already worked here: `workflow_orchestrator.py` moved pure planning helpers into `_bridge\workflow_plan_build_steps.py`; `capability_tokens.grant` split into `grant_request_error`, `grant_expiry_policy`, and `build_grant_item`; `mobile_maintenance.py` moved diagnosis rule groups and observability metrics into helper modules while keeping compatibility facades [Task 1]
- Validation gates that passed after the refactor batch: `python -m py_compile <changed files>`, `ruff check <changed owner files>`, `python _bridge\workflow_orchestrator.py validate`, `python _bridge\mobile_openclaw_bridge\mobile_bridge_mcp_server.py --self-test`, `python _bridge\mobile_openclaw_bridge\mobile_maintenance.py metrics --no-deep`, and `python _bridge\code_maintainability.py validate` [Task 1]
- When native mobile MCP transport is closed, a local fallback exists for supplement retrieval: `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id <thread_id> --timeout-seconds 8` [Task 1]
- `python _bridge\codex_workflow_entry.py closeout` was used as the final check that no work notes or pending proposals were left hanging after the refactor batch [Task 1]

## Failures and how to do differently

- Symptom: `ruff` shows many undefined names in `regression_checks_capability.py`. Cause: that test file is intentionally rebound into CLI globals via `FunctionType(..., env)`. Fix: lint only the changed owner files unless the harness itself is being redesigned [Task 1]
- Symptom: `git diff --stat` with a file list behaves unexpectedly. Cause: pathspec usage in this environment is brittle. Fix: use `git status --short`, timestamps, or direct file inspection instead of assuming every git diff form will work [Task 1]
- Symptom: a large file looks mechanically splittable but refactor risk rises. Cause: some areas such as `mobile_bridge_mcp_server` self-tests and `mobile_maintenance.inspect_system` cross boundaries and can create circular imports. Fix: design dependency-injection or probe/snapshot boundaries before further extraction [Task 1]
- Symptom: cleanup logic over-deletes. Cause: decisions were made from an incomplete inventory or narrow pattern list. Fix: default to full inventory + reporting and require stronger evidence before deletion [Task 1]

# Task Group: mcsmanager mobile OpenClaw bridge access, delivery diagnostics, and protocol-safe replies

scope: Operating the desktop dashboard/login entry, diagnosing mobile account visibility and reply failures, and finalizing replies with the exact mobile bridge marker contract.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for the mobile OpenClaw / Weixin bridge in this checkout family, but verify live bridge state, account context, and pending supplements before answering

## Task 1: diagnose新增账号显示与回发失败, success

### rollout_summary_files

- rollout_summaries/2026-06-20T04-27-13-CjBd-mobile_openclaw_bridge_account_visibility_and_context_failur.md (cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T12-27-13-019ee348-662d-7fa0-99c8-3138aa86db2f.jsonl, updated_at=2026-06-26T17:15:38+00:00, thread_id=019ee348-662d-7fa0-99c8-3138aa86db2f, root-cause diagnosis separated visibility from delivery failure)

### keywords

- mobile_openclaw_bridge, mobile_users, backup3, waiting_weixin_context, ret=-2, sendmessage_ret_-2, f2328cb1345f, thread-route list, maintenance summary --deep, bridge.get_pending_batch

## Task 2: answer AI圈与GPT趋势 with exact mobile markers, success

### rollout_summary_files

- rollout_summaries/2026-06-20T04-27-13-CjBd-mobile_openclaw_bridge_account_visibility_and_context_failur.md (cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T12-27-13-019ee348-662d-7fa0-99c8-3138aa86db2f.jsonl, updated_at=2026-06-26T17:15:38+00:00, thread_id=019ee348-662d-7fa0-99c8-3138aa86db2f, pending supplement was acked before the final reply)

### keywords

- mobile_ack, mobile_result_begin, mobile_result_end, f29954ecd0ae, 527f300429d4, gpt的未来发展趋势, bridge.get_pending_batch, 06991ce1d849

## Task 3: record unified dashboard/login entry, success

### rollout_summary_files

- rollout_summaries/2026-06-21T16-20-49-m1fM-weixin_dashboard_login_on_demand_memory.md (cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\22\rollout-2026-06-22T00-20-50-019eeafc-1677-7723-992f-b31590c0fe66.jsonl, updated_at=2026-06-22T17:43:15+00:00, thread_id=019eeafc-1677-7723-992f-b31590c0fe66, stable dashboard entry and legacy shortcut were verified)

### keywords

- 18808, /login/, 18790, 微信桥接面板.lnk, OpenClaw 微信登录二维码.lnk, open-dashboard.ps1, generate-weixin-login-qr.ps1, mobile_dashboard.py:2639, on-demand login backend

## User preferences

- for mobile bridge final replies, the user repeatedly required exact marker contracts such as 'include these exact marker(s) in the final answer' and later 'first output the exact mobile_ack marker, then return only the final Weixin reply text between the exact mobile_result_begin and mobile_result_end markers' -> preserve the exact wrapper/ownership contract and do not improvise formatting [Task 1][Task 2]
- when a prior conclusion was challenged with '可是新增账号不仅没在面板上显示而且没有得到回应', the user wanted a root-cause explanation instead of reassurance -> separate account existence, panel visibility, routing, and send-failure causes explicitly [Task 1]
- when asking '我怎么访问这个服务，现有快捷方式有两个，是否失效', the user wanted a direct stable entry answer -> clearly name the working primary entry and label legacy shortcuts as legacy rather than presenting two equal options [Task 3]
- for Weixin/mobile replies, the user expected concise user-facing text only; internal notes are not appropriate inside the reply body [Task 2]
- when the user later said '记录记忆', they explicitly wanted the verified workflow captured durably after validation [Task 3]

## Reusable knowledge

- The unified dashboard entry is `http://127.0.0.1:18808/`; the QR login page is intended to be `http://127.0.0.1:18808/login/`; the login backend on `18790` should be started on demand at the `/login/` boundary rather than eagerly [Task 3]
- Verified on-demand login flow endpoints returned HTTP 200 after the change: `18808/`, `/api/state`, `/login/`, `/login/api/state`, `/login/qr.png`, and `18790/api/state` [Task 3]
- `C:\Users\45543\Desktop\微信桥接面板.lnk` remains the primary shortcut and points to `_bridge\mobile_openclaw_bridge\open-dashboard.ps1`; `C:\Users\Public\Desktop\OpenClaw 微信登录二维码.lnk` is the legacy standalone QR flow and points to `generate-weixin-login-qr.ps1` [Task 3]
- Dashboard user visibility is task-derived from aggregation plus `mobile_users`, not directly from configured account files; a configured account can exist but still look absent/sparse in the panel if it has no recent task data [Task 1]
- `backup3` / `o9cq803g9lpCU06wtW0gnC7e_7P4@im.wechat` had a live route/thread and historical success, but dashboard-send task `f2328cb1345f` failed with `sendmessage_ret_-2` and was classified as `waiting_weixin_context` [Task 1]
- Before finalizing a mobile reply on an active thread, check `bridge.get_pending_batch`; if items are returned, ack them before sending the final wrapped response [Task 1][Task 2]
- `mobile_dashboard.py:2639` is the file-level implementation handle for the on-demand `/login/` backend start [Task 3]

## Failures and how to do differently

- Symptom: the panel looks like a new account is missing and the user receives no reply. Cause: the first answer overgeneralized from route configuration. Fix: separately verify configured account presence, `mobile_users`, task history, route entries, and the concrete send error before concluding anything [Task 1]
- Symptom: a direct dashboard send fails for an existing account. Cause: `ret=-2` / `sendmessage_ret_-2` means a Weixin business-layer rejection that left the system in `waiting_weixin_context`, not a missing route. Fix: interpret it as needing a fresh Weixin context token before retry [Task 1]
- Symptom: a CLI query path seems plausible but fails. Cause: `mobile_openclaw_cli.py get ... --events` was not a supported shape here. Fix: use supported CLI subcommands plus direct DB/event inspection when necessary [Task 1]
- Symptom: login QR startup behaves unreliably if launched too early. Cause: the Node service exits when browser heartbeat is absent. Fix: start it on demand from the `/login/` request path, not from eager launcher startup [Task 3]

# Task Group: mcsmanager automation resource library design and Codex thread-creation troubleshooting

scope: Reusable automation architecture for the desktop resource library plus the failure pattern around slow or blocked Codex thread creation from this repo context.
applies_to: cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for architecture discussions and thread-creation debugging in this checkout family, but treat thread availability and backend latency as time-sensitive

## Task 1: refactor the automation resource library into reusable modules, partial

### rollout_summary_files

- rollout_summaries/2026-06-28T16-49-54-n31u-codex_resource_library_scheduler_bridge_thread_reorg.md (cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\29\rollout-2026-06-29T00-49-57-019f0f23-37a4-78b3-ab69-500913b42310.jsonl, updated_at=2026-06-28T16:53:40+00:00, thread_id=019f0f23-37a4-78b3-ab69-500913b42310, final accepted architecture was smaller than the early design)

### keywords

- Codex资源库, 邮箱区, 定时模块, 调度桥, README.md, task_id, route_id, payload, idempotency_key, lease_owner, lease_expires_at, ack_at, ack_by, .md/.txt pairs

## Task 2: diagnose and fix slow Codex thread creation, fail

### rollout_summary_files

- rollout_summaries/2026-06-28T16-49-54-n31u-codex_resource_library_scheduler_bridge_thread_reorg.md (cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\29\rollout-2026-06-29T00-49-57-019f0f23-37a4-78b3-ab69-500913b42310.jsonl, updated_at=2026-06-28T16:53:40+00:00, thread_id=019f0f23-37a4-78b3-ab69-500913b42310, one blocker was fixed but backend slowness remained unresolved)

### keywords

- create_thread, fork_thread, list_threads, .codex/config.toml, duplicate key, [plugins.'computer-use@openai-bundled'], TOML parse error, schedule-executor-thread, projectless
