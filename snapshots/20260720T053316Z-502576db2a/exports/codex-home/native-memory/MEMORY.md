# Task Group: Codex Desktop old-thread resume recovery

scope: Repair persisted thread metadata and prove a real Desktop resume; do not confuse configuration smoke tests with recovery success.
applies_to: cwd=\\?\UNC\wsl.localhost\Codex-Wsl-Lab\ and Codex state under C:\Users\45543\.codex; reuse_rule=reuse the diagnosis and backup sequence for legacy Codex threads, but inspect the current row, rollout, runtime mode, and file locks first

## Task 1: repair thread 019f1c72 old-resume failure, partial

### rollout_summary_files

- rollout_summaries/2026-07-18T04-57-17-cBdb-repair_old_codex_thread_resume.md (cwd=\\?\UNC\wsl.localhost\Codex-Wsl-Lab\, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\07\18\rollout-2026-07-18T12-57-17-019f7395-ff2b-7cc3-99dc-4ca80576a2c5.jsonl, updated_at=2026-07-18T05:20:33+00:00, thread_id=019f7395-ff2b-7cc3-99dc-4ca80576a2c5, partial and requires real-resume verification)

### keywords

- state_5.sqlite, threads, node_repl.exe, required MCP servers failed to initialize, malformed cwd, turn_context, rollout JSONL, hostId:local, backup_router

## User preferences

- when repairing a session, the user repeatedly asked for careful checking and not to make them retry frequently -> validate the actual Desktop recovery path before requesting a retry. [Task 1]
- the user clarified that returning to Windows was their own choice and the WSL failure was a system defect -> preserve the selected runtime while repairing another mode. [Task 1]

## Reusable knowledge

- `read_thread` with `hostId:"local"` can read a target when `list_threads` returns no match. The authoritative row is in `C:\Users\45543\.codex\state_5.sqlite`, table `threads`. [Task 1]
- Back up state and rollout first with `_bridge\shared\backup_router.py`; update the row transactionally and verify the changed-row count plus malformed-path count. The target repair changed exactly one row and the backup manifest validated. [Task 1]
- For cross-platform MCP configuration, prefer runtime-local `node_repl.exe` resolved through a stable PATH entry; shared Windows `CODEX_HOME` paths are a separate failure layer. [Task 1]

## Failures and how to do differently

- Symptom: a repaired `threads.cwd` returns after navigation. Cause: malformed historical `turn_context`/settings metadata remains in the rollout. Fix: normalize both the SQLite row and matching structured rollout context before reopening. [Task 1]
- Symptom: JSONL rewrite reports that the rollout is in use. Fix: close or quiesce the owning thread/process, atomically replace from a temporary validated JSONL file, and verify hashes. [Task 1]
- Do not claim success from `navigate_to_codex_page`, a `node_repl` smoke test, or simulated MCP initialization; require an old thread to complete a real turn without `required MCP servers failed to initialize: node_repl: No such file or directory (os error 2)`. [Task 1]

# Task Group: mcsmanager research artifacts, FreeDomain boundaries, and mirror milestones

scope: Citation-backed project research, safe disposable public-entrypoint planning, and governed Codex mirror milestone work.
applies_to: cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse report and governance patterns in this checkout family, but re-fetch external facts and recheck current mirror closeout state

## Task 1: awesome-selfhosted cited report and 20-project appendix, success

### rollout_summary_files

- rollout_summaries/2026-07-10T07-51-07-5TU4-github_research_reports_freedomain_template_mirror_milestone.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\07\10\rollout-2026-07-10T15-51-09-019f4b02-4562-7f83-a1c9-e0154223a2f8.jsonl, updated_at=2026-07-17T23:52:23+00:00, thread_id=019f4b02-4562-7f83-a1c9-e0154223a2f8, completed)

### keywords

- awesome-selfhosted, awesome-selfhosted-项目分析报告.md, GitHub API, raw README, 94 categories, Open-WebUI, Node RED, Immich, citations

## Task 2: DigitalPlat FreeDomain evaluation and Cloudflare DNS template, success

### rollout_summary_files

- rollout_summaries/2026-07-10T07-51-07-5TU4-github_research_reports_freedomain_template_mirror_milestone.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\07\10\rollout-2026-07-10T15-51-09-019f4b02-4562-7f83-a1c9-e0154223a2f8.jsonl, updated_at=2026-07-17T23:52:23+00:00, thread_id=019f4b02-4562-7f83-a1c9-e0154223a2f8, completed)

### keywords

- DigitalPlatDev-FreeDomain, FreeDomain-Cloudflare-DNS-初始化模板.md, mcs-demo.dpdns.org, Cloudflare Access, Tunnel, Public Suffix List, qd.je

## Task 3: Codex environment mirror seed-v2.3.1 milestone, partial

### rollout_summary_files

- rollout_summaries/2026-07-10T07-51-07-5TU4-github_research_reports_freedomain_template_mirror_milestone.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\07\10\rollout-2026-07-10T15-51-09-019f4b02-4562-7f83-a1c9-e0154223a2f8.jsonl, updated_at=2026-07-17T23:52:23+00:00, thread_id=019f4b02-4562-7f83-a1c9-e0154223a2f8, release published; final closeout unresolved)

### keywords

- codex-environment-mirror, seed-v2.3.1, RELEASE-CODEX-MIRROR, release-plan, snapshot_only_or_no_change, system_membership, main_task_complete, closeout

## User preferences

- when requesting research, the user asked: "将分析写成报告文件，格式md文件，附带主要内容的引用链接" and later requested individual categorized analyses -> create a Markdown artifact with inline source links, not chat-only prose. [Task 1]
- when an existing report is extended, the user asked to append the 20-project analysis to it -> preserve and extend the referenced artifact after a pre-edit backup. [Task 1]
- the user defined FreeDomain as a "免费公共子域名服务" for demos, docs, callbacks, and temporary public access -> do not frame it as a production identity or complete self-hostable system. [Task 2]
- the user asked to place the DNS template beside the project "方便后续codex阅读" -> materialize reusable operational guidance as project-local Markdown. [Task 2]

## Reusable knowledge

- `awesome-selfhosted-项目分析报告.md` was verified by readback. Treat `awesome-selfhosted` as a discovery/index project; use GitHub API, README/raw README, official site, releases, and upstream data repository citations. In PowerShell, use `@' ... '@ | python -`, not Bash heredocs. [Task 1]
- FreeDomain local material is read-only reference and the full backend is not public. Use a disposable root such as `mcs-demo.dpdns.org` with `docs`, `demo`, `status`, and `verify`; reserve `gate` for Access/Tunnel protection. Prefer PSL-listed `dpdns.org`, `us.kg`, `qzz.io`, or `xx.kg`; `qd.je` is compatibility-test-only. [Task 2]
- Never expose MCSManager, Codex, bridge/gateway, databases, unauthenticated APIs, or writable admin panels directly through this entrypoint. [Task 2]
- The governed release command created and remotely verified `seed-v2.3.1` from snapshot `20260717T232807Z-ad02ce78b0`; remote tag head was `5fdcbeff6826d64d0c843803d894d2b95766c9bc`. [Task 3]

## Failures and how to do differently

- Candidate matching needs actual README-entry inspection and case-insensitive variants such as `Open-WebUI`, `Node RED`, and `Immich`. [Task 1]
- When `release-plan` says `snapshot_only_or_no_change` but the user asks to update a milestone, clarify snapshot/control-plane update versus new Git tag; record any explicit semantic choice. [Task 3]
- The mirror release was published, but final closeout was interrupted after `system_membership.py validate`; rerun closeout with required receipts and require `main_task_complete: true` before claiming completion. Avoid broad recursive `_bridge`/backup searches; use targeted reads and bounded `rg`. [Task 3]

# Task Group: mcsmanager CC Switch proxy logging crash mitigation

scope: Diagnose CC Switch auto-exit around logging and apply the narrow DB-backed log-level mitigation without changing routing.
applies_to: cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager with runtime data at C:\Users\45543\.cc-switch; reuse_rule=reuse only after inspecting current source, database, and active proxy settings

## Task 1: diagnose logging-path crash and apply approved medium mitigation, success

### rollout_summary_files

- rollout_summaries/2026-06-28T16-49-54-n31u-cc_switch_logging_crash_mitigation.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\06\29\rollout-2026-06-29T00-49-57-019f0f23-37a4-78b3-ab69-500913b42310.jsonl, updated_at=2026-07-17T13:32:49+00:00, thread_id=019f0f23-37a4-78b3-ab69-500913b42310, completed)

### keywords

- cc-switch.exe, cc-switch.db, log_config, proxy_config, forwarder.rs, tauri_plugin_log, os error 232, c0000409, BEX64, 127.0.0.1:15721, quick_check

## User preferences

- before changes, the user said "先不要修改，只做计划" -> provide read-only diagnosis and a concrete plan. [Task 1]
- after evidence, the user said "批准中等方案" -> make only the bounded mitigation that preserves proxy/provider/Codex configuration and verify it. [Task 1]

## Reusable knowledge

- Runtime DB: `C:\Users\45543\.cc-switch\cc-switch.db`; proxy: `127.0.0.1:15721`. The verified mitigation is `settings.log_config={"enabled":true,"level":"error"}` after backup. [Task 1]
- `proxy_config.enable_logging` does not suppress the `forwarder.rs` `log::info!` path. `level="error"` maps to `LevelFilter::Error`; validate SQLite `quick_check`, unchanged `proxy_config`, and listening proxy port. [Task 1]

## Failures and how to do differently

- Anonymous GitHub code search was rate-limited; use the authenticated local GitHub hub and a temporary source zip for read-only grepping. Prefer source/DB evidence over GUI experiments. [Task 1]

# Task Group: mcsmanager mobile bridge worker idle-backoff repair

scope: Minimal worker activity detection changes, regression coverage, and paused-state validation.
applies_to: cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for current bridge worker-loop behavior while preserving live STOP_REQUEST and checking present state

## Task 1: remove skipped-only retries from worker activity detection, success

### rollout_summary_files

- rollout_summaries/2026-07-04T06-00-52-3NvG-mobile_worker_idle_backoff_fix.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\07\04\rollout-2026-07-04T14-00-54-019f2bb7-2d6d-7963-a33a-a14dfbf1f238.jsonl, updated_at=2026-07-16T10:18:22+00:00, thread_id=019f2bb7-2d6d-7963-a33a-a14dfbf1f238, fix verified; full closeout uncertain)

### keywords

- worker_loop_has_activity, worker_loop_observability.py, pending_reply_retries.skipped, skipped-only, skipped_busy_route, idle backoff, STOP_REQUEST, fair-scheduling-check, backup_router.py validate --root

## User preferences

- the user said "不要引入新的漏洞" -> use a minimal single-point repair, a narrow reproducer, and regression validation. [Task 1]
- after "继续", continue the verification/closeout chain without restating the task. [Task 1]

## Reusable knowledge

- Remove only `int(pending_retry.get("skipped") or 0)` from activity counting. A pure reproducer with `action=idle`, `processed=0`, `scheduled=0`, `skipped=3` must become inactive; scheduled, processed, and `skipped_busy_route=1` remain active. [Task 1]
- Back up first and validate with `backup_router.py validate --root <backup-dir>`. The intended state was paused: `STOP_REQUEST` present and worker down. `maintenance summary` skips deep probes; `maintenance iteration` is proposal-only. [Task 1]

## Failures and how to do differently

- `reply-pending-account-scope-check` can raise `KeyError` through the facade: use its owner module. For `fair-scheduling-check`, temporarily override the stop path in-process; never delete the live marker. Wait for closeout helper processes before final status. [Task 1]

# Task Group: mcsmanager workflow closeout bounded output and mirror verification

scope: Global CLI output projections for closeout workflows and post-closeout mirror publishing.
applies_to: cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for `_bridge` workflow governance, but treat mirror freshness and archive readiness as time-sensitive

## Task 1: implement bounded closeout projection with distinct default and full modes, success

### rollout_summary_files

- rollout_summaries/2026-06-20T07-35-55-D3iv-global_bounded_output_governance_closeout_full_mode.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T15-35-55-019ee3f5-27e9-7d20-9cf5-802aaef0e1af.jsonl, updated_at=2026-07-16T14:33:28+00:00, thread_id=019ee3f5-27e9-7d20-9cf5-802aaef0e1af, tests and publish passed)

### keywords

- bounded_output.py, default_bounded, failure_bounded, full_bounded, --full-output, closeout_cli_projection, safe_next_step, manual_action, post_closeout_mirror, raw_result_ref, source_assets_changed

## User preferences

- the user said "命令输出只展示有价值部分，这应该是全局要求" and "输出很大" -> default terminal output must be compact and decision-focused. [Task 1]
- the user corrected: "那样两者就没有区别了" -> default is an actionable summary; `--full-output` must remain richer but bounded. [Task 1]

## Reusable knowledge

- `_bridge/bounded_output.py` is the shared contract: `default_bounded`, `failure_bounded`, `full_bounded`. Preserve `reason`, `next_action`, `safe_next_step`, `manual_action`, `decision_evidence`, `finalization`, and `post_closeout_mirror`; raw packages belong at `record_path` / `raw_result_ref`. [Task 1]
- Publish is post-closeout. Inspect `finalization.post_closeout_mirror.result.push.remote_verification` only after edits cease. Recorded gates: `maintenance_control_plane_tests.py` 37 tests, `workflow_closeout_package_tests.py` 10 tests, `workflow_orchestrator.py validate` 40/40. [Task 1]

## Failures and how to do differently

- Symptom: projection hides finalization, next action, or safe next step. Fix the shared preserve/priority policy, not a one-off test. `source_assets_changed` during active edits is expected; finish, close out, then recheck. [Task 1]

# Task Group: mcsmanager bridge and workflow validation-first modularization

scope: Conservative `_bridge` module extraction, ownership discovery, compatibility facades, and behavior-preserving validation.
applies_to: cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse for `_bridge` refactors only after deriving current ownership and rebuilding the module index after new helpers

## Task 1: modularize bridge/workflow helpers with validation, success

### rollout_summary_files

- rollout_summaries/2026-07-01T06-51-01-XY1G-mobile_bridge_workflow_modularization_and_safe_refactor.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\07\01\rollout-2026-07-01T14-51-06-019f1c72-03c3-7032-aa56-dff625d7c720.jsonl, updated_at=2026-07-05T17:21:31+00:00, thread_id=019f1c72-03c3-7032-aa56-dff625d7c720, completed)

### keywords

- code_maintainability.py module-context, build-module-index, workflow_plan_build_steps.py, capability_tokens.py, grant_request_error, mobile_observability_metrics.py, mobile_diagnosis_issue_rules.py, supplement-fallback, Transport closed

## User preferences

- the user asked for "验证脚本可行性，做出优化，要求稳定准确" and "安全准确" -> show real validation output and make conservative behavior-preserving changes. [Task 1]
- the user warned "判断幽灵配置一定需要谨慎，防止误删有用的配置" -> begin cleanup with complete inventory/reporting, not automatic deletion. [Task 1]
- the user wanted generic, data-driven automation and backup-before-edit behavior. [Task 1]

## Reusable knowledge

- Start with `python _bridge\code_maintainability.py module-context --term <module_or_feature>`, then `build-module-index --all-bridge --limit 1000` after helper additions; the recorded rebuild covered 177 modules. [Task 1]
- Proven extractions: `workflow_plan_build_steps.py`, `grant_request_error` / `grant_expiry_policy` / `build_grant_item`, observability/diagnosis helpers with compatibility facades. Validate compile, changed-owner `ruff`, `workflow_orchestrator.py validate`, MCP self-test, metrics, and maintainability. [Task 1]

## Failures and how to do differently

- `regression_checks_capability.py` uses `FunctionType(..., env)`, so lint changed owner files unless redesigning the harness. Defer self-test/inspection extractions that would create circular imports until a dependency-injection or probe boundary exists. [Task 1]

# Task Group: mcsmanager mobile OpenClaw reply protocol and dashboard access

scope: Owned-result recovery diagnosis, primary/backup permission boundaries, and verified Weixin dashboard/login entrypoints.
applies_to: cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager; reuse_rule=reuse only after checking live task routes, bridge state, and login service availability

## Task 1: diagnose primary visible-CDP owned-result recovery and rule loading, partial

### rollout_summary_files

- rollout_summaries/2026-06-20T04-27-13-CjBd-mobile_openclaw_bridge_owned_result_redelivery_and_backup1_b.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T12-27-13-019ee348-662d-7fa0-99c8-3138aa86db2f.jsonl, updated_at=2026-07-12T13:51:08+00:00, thread_id=019ee348-662d-7fa0-99c8-3138aa86db2f, diagnosis partial)

### keywords

- visible-CDP, owned-result markers, protocol_violation_no_owned_result, task_waits_for_followup_redelivery, backup1, mobile_tasks, mobile_events, result_after_work_only

## Task 2: verify unified Weixin dashboard and on-demand QR login, success

### rollout_summary_files

- rollout_summaries/2026-06-21T16-20-49-m1fM-weixin_dashboard_login_on_demand_memory.md (cwd=\\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager, rollout_path=C:\Users\45543\.codex\sessions\2026\06\22\rollout-2026-06-22T00-20-50-019eeafc-1677-7723-992f-b31590c0fe66.jsonl, updated_at=2026-06-22T17:43:15+00:00, thread_id=019eeafc-1677-7723-992f-b31590c0fe66, completed)

### keywords

- 127.0.0.1:18808, /login/, 18790, 微信桥接面板.lnk, OpenClaw 微信登录二维码.lnk, mobile_dashboard.py, login-on-demand

## User preferences

- in mobile delegation, the user used exact fields such as `ack_first`, `result_after_work_only`, and `result_markers_only` -> retain strict ownership/format discipline, not only phone-visible text. [Task 1]
- after a wrong reply, the user clarified: "它一开始确实没有按格式生成回复，是后面信息重发才按照格式的" -> distinguish first-turn failure from later redelivery recovery. [Task 1]
- when asking about two shortcuts, the user wanted a direct stable access answer and explicitly requested "记录记忆" after verification -> name the working primary entry and label stale legacy paths. [Task 2]

## Reusable knowledge

- Primary visible-CDP `protocol_violation_no_owned_result` can intentionally wait for same-thread follow-up: `task_waits_for_followup_redelivery()` is true for `codex-cdp` + `primary`. Inspect `mobile_tasks`/`mobile_events`; Weixin strips `[[mobile_ack:...]]`, `[[mobile_result_begin:...]]`, and `[[mobile_result_end:...]]`, so visible text alone is not marker evidence. [Task 1]
- `backup1` is limited to ordinary low-risk Q&A and cannot inspect primary/local diagnostics. Rule loading is layered: system/developer, workspace, project `AGENTS.md`, mobile envelope, then skills/memory. [Task 1]
- Primary dashboard: `http://127.0.0.1:18808/`; QR login: `http://127.0.0.1:18808/login/`, which starts backend `18790` on demand. `C:\Users\45543\Desktop\微信桥接面板.lnk` remains primary; `C:\Users\Public\Desktop\OpenClaw 微信登录二维码.lnk` is legacy. [Task 2]

## Failures and how to do differently

- Do not merge original and recovered events into a single success: the original task had all ownership markers absent, while same-thread follow-up later recovered it. Do not expect immediate automatic retry for this primary route. [Task 1]
- Starting QR backend early is unreliable because it exits without browser heartbeat; start it at the `/login/` request boundary and verify the dashboard, state, login, and QR endpoints. [Task 2]

# Task Group: Minecraft Fabric 26.1.2 global skill research

scope: Current Fabric 26.1.2 client/server/mod/shader/resource-pack knowledge and global skill scope.
applies_to: cwd=\\?\C:\Users\45543\Documents\mc; reuse_rule=reuse the installed global skill across projects, but recheck fast-moving version/toolchain facts before advising implementation

## Task 1: research Fabric 26.1.2 ecosystem and install a global skill, success

### rollout_summary_files

- rollout_summaries/2026-06-15T07-48-15-yZEx-fabric_mc_26_1_2_skill_research_and_install.md (cwd=\\?\C:\Users\45543\Documents\mc, rollout_path=\\?\C:\Users\45543\.codex\sessions\2026\06\15\rollout-2026-06-15T15-48-15-019eca40-a8ff-72e2-a7da-43b8f9befc65.jsonl, updated_at=2026-07-09T16:24:24+00:00, thread_id=019eca40-a8ff-72e2-a7da-43b8f9befc65, completed)

### keywords

- fabric-mc-26-1-2, SKILL.md, Java 25, Fabric Loader 0.18.4, Gradle 9.4.0, Fabric Loom 1.15, Mojang official mappings, shaders, resource packs

## User preferences

- the user asked for "信息准确，覆盖面广，具有时效性" and both "mc服务端及客户端知识" plus "相关mod，资源包及光影" -> use fresh, source-backed coverage spanning client, server, mods, shaders, and resource packs. [Task 1]
- the user asked "这个skill有什么作用" and whether it works in other projects -> explain purpose and global scope directly. [Task 1]

## Reusable knowledge

- The installed global skill is `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md`; skills under `C:\Users\45543\.codex\skills\` apply across projects. [Task 1]
- Recorded 26.1-era guidance: Java 25, Fabric Loader 0.18.4, Gradle 9.4.0, Fabric Loom 1.15; Fabric 26.1 is unobfuscated and migrations need Mojang official mappings plus world backups. [Task 1]

## Failures and how to do differently

- Browser/MCP setup was noisy (`unknown MCP server 'browser'`, missing Playwright executable, timeouts); pivot to the available in-app browser runtime and its bundled guidance. [Task 1]
