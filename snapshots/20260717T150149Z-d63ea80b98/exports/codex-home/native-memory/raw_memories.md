# Raw Memories

Merged stage-1 raw memories (stable ascending thread-id order):

## Thread `019eca40-a8ff-72e2-a7da-43b8f9befc65`
updated_at: 2026-07-09T16:24:24+00:00
cwd: \\?\C:\Users\45543\Documents\mc
rollout_path: C:\Users\45543\.codex\sessions\2026\06\15\rollout-2026-06-15T15-48-15-019eca40-a8ff-72e2-a7da-43b8f9befc65.jsonl
rollout_summary_file: 2026-06-15T07-48-15-yZEx-fabric_mc_26_1_2_skill_research_and_install.md

---
description: Researched Minecraft Fabric 26.1.2 ecosystem, verified version/background info from FabricMC and search results, and generated a global Codex skill for Fabric client/server, mods, shaders, resource packs, and migration guidance. Also confirmed the skill was installed under the user-global `.codex\skills` directory so it can be reused across projects.
task: research fabric 26.1.2 knowledge and generate skill
task_group: minecraft/fabric knowledge skill generation
task_outcome: success
cwd: C:\Users\45543\Documents\mc
keywords: FabricMC, Minecraft 26.1.2, Fabric Loader 0.18.4, Java 25, Loom 1.15, Gradle 9.4.0, Iris, Sodium, Modrinth, CurseForge, SKILL.md, codex skills
---

### Task 1: Research Minecraft 26.1.2 Fabric ecosystem and generate SKILL.md

task: search/analyze Minecraft 26.1.2 Fabric server-client knowledge and create a skill file
task_group: Minecraft/Fabric knowledge base generation
task_outcome: success

Preference signals:
- the user asked for "信息准确，覆盖面广，具有时效性" -> future similar knowledge tasks should prioritize freshness, broad coverage, and verification from current sources rather than relying on generic memory
- the user wanted both "mc服务端及客户端知识" plus "相关mod，资源包及光影" -> future similar skills should cover both client and server workflows, not just one side

Reusable knowledge:
- FabricMC official site/blog showed a 26.1 release page; the rollout captured that 26.1 is the first unobfuscated Minecraft version and that Fabric now expects Mojang official mappings for this line.
- The researched notes recorded these version/tooling requirements for 26.1: Java 25, Fabric Loader 0.18.4, Gradle 9.4.0, Fabric Loom 1.15, and IntelliJ IDEA 2025.3+.
- The skill content also captured a 26.1-era mod ecosystem summary: Fabric + Sodium + Iris for mainstream client use; Lithium/Fabric API/LuckPerms/Carpet for server-side use; and popular UI/build/worldgen mods like EMI, REI, MiniHUD, Litematica, Terralith, Waystones, and AppleSkin.
- Verified output showed the skill was installed at `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md` and the file length was reported as 9820 bytes.

Failures and how to do differently:
- Writing into `C:\Users\45543\Documents\mc` repeatedly failed under the managed permission profile when creating new directories/files, even though reads worked; the successful path was to write the skill in a temp location first, then copy it into `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md` with an escalated shell command.
- Browser automation via the plugin backend was flaky at first (`unknown MCP server 'browser'`, Playwright missing executable, and timeout issues), so the rollout pivoted to the in-app browser runtime after reading the bundled browser skill docs.
- Some generated file attempts used the wrong assumption that the workspace root was writable; future similar tasks should check actual writable locations early and prefer the global skills directory for skill installation.

References:
- [1] Official Fabric site snapshot: `Fabric Loader`, `Fabric for Minecraft 26.1`, and the blog text noting `Minecraft 26.1` and `Players should install the latest stable version of Fabric Loader (currently 0.18.4)`.
- [2] Verified installed skill path: `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md`
- [3] Search evidence captured in Google DOM snapshots for `fabric26.1.2 minecraft`, including results like `How To Download & Install Fabric (Minecraft 26.1.2)` and `How To Host a Fabric Minecraft Server (26.1.2)`.
- [4] The final saved skill content included sections for client install, server install, mod recommendations, shaders, resource packs, troubleshooting, and developer migration notes.

### Task 2: Explain the generated skill to the user

task: answer what the Fabric skill does, whether it works across projects, and how context compression/plugin mentions should be interpreted
task_group: user education / workflow clarification
task_outcome: success

Preference signals:
- the user asked "这个skill有什么作用" and then "这个skill在我打开其他项目时能够使用吗" -> future explanations should be direct about purpose and scope, especially whether a skill is global or project-local
- the user repeatedly asked "怎么压缩上下文" -> future similar conversations should expect the user may want a plain-language explanation of context compression rather than a tool-heavy answer
- the user repeatedly asked about `[@电脑](plugin://computer-use@openai-bundled)` and later "你是谁" -> future replies can safely clarify what the computer-use plugin is and what role the assistant is playing when the user seems uncertain

Reusable knowledge:
- The skill was installed under the user-global Codex skills directory, not inside a specific repo, so it can be reused from other projects when the topic matches its description.
- The assistant explained that context compression is system-managed, not a user-triggered command, and that `@电脑` refers to the Computer Use plugin for GUI control.

Failures and how to do differently:
- None material; the user did not reject the explanation.

References:
- User wording to preserve for future reuse: `这个skill有什么作用`, `这个skill在我打开其他项目时能够使用吗`, `怎么压缩上下文`, `[@电脑](plugin://computer-use@openai-bundled) 这是什么`, `你是谁`
- Confirmed answer artifact: the skill lives at `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md` and is therefore global across projects

## Thread `019ed5ce-de73-7c63-9b71-8a266262729b`
updated_at: 2026-06-17T13:41:38+00:00
cwd: C:\Users\45543\AppData\Local\OpenAI\Codex\bin\330bd0cba6496126
rollout_path: C:\Users\45543\.codex\sessions\2026\06\17\rollout-2026-06-17T21-39-24-019ed5ce-de73-7c63-9b71-8a266262729b.jsonl
rollout_summary_file: 2026-06-17T13-39-24-HPpF-codex_gui_launch_and_admin_check.md

---
description: User asked to distinguish the graphical Codex app window from the terminal/CLI and the agent identified the Windows AppsFolder entry for the Codex GUI, but final window visibility was not confirmed.
task: inspect admin status and launch Codex GUI window
task_group: Windows Codex app / PowerShell
atask_outcome: uncertain
cwd: C:\Users\45543\AppData\Local\OpenAI\Codex\bin\330bd0cba6496126
keywords: PowerShell, administrator, codex.exe, AppsFolder, OpenAI.Codex_2p2nqsd0c76g0!App, Start-Process, GUI, terminal
---
### Task 1: Check admin status and launch Codex GUI

task: determine whether the current Codex session is running as administrator, then launch the Codex app window
task_group: Windows / Codex desktop app
task_outcome: uncertain

Preference signals:
- when the assistant said it would open a “Codex 窗口” and launched `codex.exe`, the user corrected: “我说的不是终端窗口” -> future attempts should explicitly distinguish GUI app windows from terminal/CLI windows before launching anything.
- the user’s wording “启动codex窗口吧” followed by the correction indicates they want the graphical Codex app window, not the shell session that happens to be running Codex.

Reusable knowledge:
- Current PowerShell session reported `User : LSD的PC\user` and `IsAdministrator : True` from `[WindowsPrincipal].IsInRole([WindowsBuiltInRole]::Administrator)`.
- In this environment, the GUI Codex app entry was discoverable via `Get-StartApps | Where-Object { $_.Name -match 'Codex|OpenAI|ChatGPT' }`, which returned `Codex  OpenAI.Codex_2p2nqsd0c76g0!App`.
- `Start-Process 'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App'` was used to try to open the GUI app.

Failures and how to do differently:
- Launching `codex.exe` opened the CLI/terminal-style Codex process, which was not what the user meant.
- The GUI app launch attempt had no visible confirmation in the rollout; if this recurs, verify window appearance or foreground state explicitly instead of assuming success from `Start-Process` exit code 0.

References:
- Exact admin check output: `User : LSD的PC\user` / `IsAdministrator : True`
- Files found in `C:\Users\45543\AppData\Local\OpenAI\Codex\bin\330bd0cba6496126`: `codex-command-runner.exe`, `codex-windows-sandbox-setup.exe`, `codex.exe`
- Process evidence: `codex 18832 ⠹ 330bd0cba6496126 C:\Users\45543\AppData\Local\OpenAI\Codex\bin\330bd0cba6496126\codex.exe`
- GUI AppID: `OpenAI.Codex_2p2nqsd0c76g0!App`
- Launch command used: `Start-Process 'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App'`

## Thread `019ee348-662d-7fa0-99c8-3138aa86db2f`
updated_at: 2026-07-12T13:51:08+00:00
cwd: \\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
rollout_path: C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T12-27-13-019ee348-662d-7fa0-99c8-3138aa86db2f.jsonl
rollout_summary_file: 2026-06-20T04-27-13-CjBd-mobile_openclaw_bridge_owned_result_redelivery_and_backup1_b.md

---
description: Mobile bridge final-reply diagnostics showed that primary visible-CDP tasks can fail the owned-result protocol on the first turn and only recover after same-thread follow-up redelivery; backup1 remains limited to ordinary low-risk Q&A and cannot query local state or sensitive tokens.
task: diagnose mobile-openclaw bridge reply-format and permission behavior for Weixin tasks
task_group: mobile-openclaw_bridge / weixin_final_replies
 task_outcome: partial
cwd: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
keywords: mobile-openclaw-bridge, mobile_result_begin, mobile_result_end, mobile_ack, protocol_violation_no_owned_result, active_waiting_followup_redelivery, submission_confirmation_timeout, visible_cdp, codex-cdp, backup1, primary, permission-boundary
---

### Task 1: Weixin final-reply protocol and recovery behavior

task: investigate primary visible-CDP tasks that first missed owned-result markers, then recovered after same-thread follow-up; also handle backup1 low-risk Q&A

task_group: mobile-openclaw_bridge / Weixin reply protocol

task_outcome: partial

Preference signals:
- the user repeatedly supplied strict mobile delegation envelopes with `ack_first`, `result_after_work_only`, `result_markers_only`, and exact marker IDs -> the workflow needs exact marker handling and should preserve ownership/result-boundary discipline by default
- when a reply looked wrong on Weixin, the user corrected it with follow-up messages like “它一开始确实没有按格式生成回复，是后面信息重发才按照格式的” -> future agents should distinguish first-turn failure from later recovery and not assume the first visible success was the original state
- when asking about sensitive local state from `backup1`, the system had to refuse or redirect to primary -> backup1 should be treated as ordinary /ask only, with local diagnostics and state inspection kept behind the primary/admin boundary

Reusable knowledge:
- Primary visible-CDP tasks can enter `protocol_violation_no_owned_result` when a Codex turn ends without owned markers; in this workspace that path is intentionally handled by `wait_for_same_thread_followup_before_redelivery` rather than immediate auto-retype.
- `visible_cdp_no_owned_result_manual_after_seconds` is bounded by config; the code path explicitly treats primary visible-CDP no-owned-result as a follow-up-wait case.
- `reply_to_weixin()` sends via `weixin_send_reply.mjs` and considers delivery accepted when the transport succeeds, the stdout JSON is ok, and Weixin business-layer errors are absent; phone visibility may still be false.
- The mobile bridge protocol strips `mobile_result_*` markers before sending to Weixin; seeing plain text in Weixin does not mean the markers were missing from the protocol stream.

Failures and how to do differently:
- The first primary turn (`9ed09e7c39bb`) really did fail the owned-result contract: `ack_seen=false`, `begin_seen=false`, `end_seen=false`, `result_complete=false`, `ownership.valid=false`, `terminal_without_text=true`.
- The later success came from same-thread redelivery triggered by a follow-up message (`5d5fab93b4cb` / later `b9760c6855a0`), not from the original turn; future agents should not collapse the two into one success.
- For protocol complaints, do not only explain the final visible text; inspect the event chain and distinguish: send acceptance, owned-result protocol, same-thread redelivery, and final Weixin acceptance.
- Backup1 tasks that ask for main-account state should be answered as permission-limited refusals or high-level guidance only; do not inspect or disclose primary/local diagnostics under backup1.

References:
- `mobile_openclaw_cli.py` lines around `24409-24423`: `task_waits_for_followup_redelivery()` returns true for `delivery_mode == "codex-cdp" and account_id == "primary"`.
- `mobile_openclaw_cli.py` lines around `27325-27364`: when a task waits for follow-up redelivery and the terminal reason is `protocol_violation_no_owned_result`, it calls `mark_waiting_followup_redelivery(...)` and keeps the task waiting instead of immediate retry.
- `mobile_openclaw_cli.py` lines around `27194-27214`: when `new_text` is recovered, the task is completed and the result is pushed back to Weixin.
- DB evidence for `9ed09e7c39bb`: `recovery_protocol_violation_no_owned_result` with `own_valid=0`, `begin_seen=0`, `end_seen=0`, then later `active_waiting_followup_redelivery_triggered` from same-thread follow-up.
- DB evidence for `b9760c6855a0`: a later backup1 explanation task that explicitly described the first-turn failure and the same-thread redelivery recovery.
- The exact owned-result markers seen in successful turns are protocol-specific and include `[[mobile_ack:...]]`, `[[mobile_result_begin:...]]`, and `[[mobile_result_end:...]]`.

## Thread `019ee3f5-27e9-7d20-9cf5-802aaef0e1af`
updated_at: 2026-07-16T14:33:28+00:00
cwd: \\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
rollout_path: C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T15-35-55-019ee3f5-27e9-7d20-9cf5-802aaef0e1af.jsonl
rollout_summary_file: 2026-06-20T07-35-55-D3iv-global_bounded_output_governance_closeout_full_mode.md

---
description: Implemented global bounded-output governance for Codex closeout/owner CLIs, keeping default output concise, making `--full-output` a richer bounded diagnostic view (not raw dump), preserving critical failure/action fields, and then completed closeout + mirror publish with verified remote HEAD.
task: global output governance for closeout and bounded CLI projections
 task_group: workflow_orchestration
 task_outcome: success
cwd: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
keywords: bounded_output, codex_workflow_entry, closeout_cli_projection, --full-output, output_mode, safe_next_step, manual_action, decision_evidence, post_closeout_mirror, mirror publish, rule_governance, system_membership, workflow_orchestrator
---

### Task 1: Global output governance and closeout projection

task: tighten closeout CLI output projection and keep full-output distinct from default
 task_group: workflow_governance
 task_outcome: success

Preference signals:
- the user said: "命令输出只展示有价值部分，这应该是全局要求，和之前对命令输出的要求合并优化，并让它真正发挥作用" -> future runs should treat output minimization as a global governance requirement, not a one-off closeout tweak.
- when the assistant suggested making default and full both short, the user corrected: "那样两者就没有区别了" -> preserve a real distinction: default = actionable summary, full = richer bounded diagnostics, raw package via artifact/reference.
- the user objected to oversized terminal output ("输出很大") -> future runs should proactively prefer compact, decision-focused CLI projections and avoid raw JSON dumps unless explicitly requested.

Reusable knowledge:
- `bounded_output.py` now carries the shared contract for CLI projections; default success uses `default_bounded`, failures use `failure_bounded`, and `--full-output` uses `full_bounded` with a larger budget instead of an unbounded dump.
- `codex_workflow_entry.py` closeout projection now prioritizes `output_mode`, `record_path`, `task_kind`, `decision_evidence`, `finalization`, `post_closeout_mirror`, and `section_index` so critical closeout results stay visible under budget pressure.
- The closeout projection now preserves `safe_next_step` and `manual_action` globally in bounded failure evidence, which prevents retrying blindly when a projection is truncated.
- `--full-output` remains distinct from default output, but the complete raw package is still intended to be retrieved via `record_path` / `raw_result_ref`, not printed inline.

Failures and how to do differently:
- Early closeout projection dropped important nested fields; tests exposed that `safe_next_step` was missing from a failure blocker. Fix was to widen the global preserve list rather than special-casing a single test.
- The first pass at full-output still risked hiding key finalization results when the output budget clipped too aggressively. Fix was to lift `finalization`/`post_closeout_mirror` into higher-priority projection fields and add a `section_index` for the richer view.
- A giant `--full-output` closeout payload was initially reduced too far; after that, the projection was tuned so full output remains richer than default while still bounded and readable.

References:
- `_bridge/bounded_output.py`: added `full` evidence policy, `default_bounded` / `failure_bounded` / `full_bounded` modes, and global preserve keys including `safe_next_step`, `manual_action`, `decision_evidence`, `finalization`, `post_closeout_mirror`, and `section_index`.
- `_bridge/codex_workflow_entry.py`: `closeout_cli_projection(payload, full=...)` now emits `output_mode` and summaries for `startup_baseline`, `project_checkpoint`, and `post_closeout_mirror`; `--full-output` now routes to the richer bounded view instead of bypassing projection.
- `_bridge/maintenance_control_plane_tests.py`: regression tests added for bounded success/failure behavior, richer full-output, closeout projection boundedness, and preservation of publish remote verification.
- Exact validation snippets: `python _bridge/maintenance_control_plane_tests.py` -> `Ran 37 tests ... OK`; `python _bridge/workflow_closeout_package_tests.py` -> `Ran 10 tests ... OK`; `python _bridge/workflow_orchestrator.py validate` -> `40/40` passed.

### Task 2: Post-closeout mirror publish and verification

task: complete closeout and refresh the environment mirror after the bounded-output change
 task_group: workflow_orchestration
 task_outcome: success

Preference signals:
- the user’s complaint about output size became a governance request, which implies future workflow changes should be rolled into proper closeout / validation / mirror publication instead of left as ad hoc local edits.
- the user’s correction that `full` must stay distinct suggests future work should preserve separate operator modes rather than collapsing them for simplicity.

Reusable knowledge:
- The mirror release path is post-closeout: successful closeout finalization triggers `post_closeout_mirror` publish, which refreshes, commits retention, pushes to `origin/main`, and verifies remote HEAD.
- The final publish result is now inspectable in the closeout summary via `post_closeout_mirror.result.push.remote_verification`, which is the right place to confirm the remote actually updated.
- After the final save, the mirror status was clean and fresh, and `origin/main` matched local HEAD.

Failures and how to do differently:
- The mirror initially reported `source_assets_changed` while the workspace was still being edited; that is expected during active code changes. The fix is to finish edits, re-run closeout, then refresh/publish once, rather than repeatedly retrying stale mirror operations mid-edit.
- The first attempt at reading the publish verification used the wrong field path, which produced nulls even though publish succeeded. Correct field path lives under `finalization.post_closeout_mirror.result.push.remote_verification`.

References:
- Final save-style closeout command succeeded and wrote to `_bridge/runtime/workflow_closeouts/closeouts.jsonl`.
- Mirror verification after publish: snapshot `20260716T143104Z-bb0055bcf7`, local HEAD/remote HEAD both `2cb691fa03f32f4e0adf8806defaf669f98a7f49`, `mirror_valid=true`, `capability_restore_ready=true`, `source_freshness.ok=true`.
- `git -C C:\Users\45543\codex-env-mirror log --oneline --decorate -3` showed the refreshed mirror commit on `main` and `origin/main` aligned with it.
- Existing unrelated environment gap remains: `full_state_restore_ready=false` due to archive gaps (`cc-switch-database`, `codex-native-memory-state`, `codex-goal-state`, `mail-and-scheduler-state`).

## Thread `019eeafc-1677-7723-992f-b31590c0fe66`
updated_at: 2026-06-22T17:43:15+00:00
cwd: \\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
rollout_path: C:\Users\45543\.codex\sessions\2026\06\22\rollout-2026-06-22T00-20-50-019eeafc-1677-7723-992f-b31590c0fe66.jsonl
rollout_summary_file: 2026-06-21T16-20-49-m1fM-weixin_dashboard_login_on_demand_memory.md

---
description: Verified unified Weixin bridge dashboard/login entry and on-demand login-service startup behavior; primary shortcut remains valid, legacy QR shortcut is stale
task: record verified dashboard login entry facts and access guidance
task_group: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
keywords: mobile_dashboard.py, open-dashboard.ps1, login proxy, on-demand startup, 18808, 18790, shortcut, legacy QR
---
### Task 1: Record unified dashboard/login entry

task: record verified dashboard login entry facts and access guidance
task_group: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
task_outcome: success

Preference signals:
- after asking “我怎么访问这个服务，现有快捷方式有两个，是否失效”, the user wanted a direct, stable access answer rather than speculation -> future responses should clearly name the working primary entry and call out legacy shortcuts.
- after user said “记录记忆”, they explicitly wanted the verified access pattern stored durably -> future similar confirmed workflow changes should be written to memory.

Reusable knowledge:
- The unified access point is `http://127.0.0.1:18808/`; QR login is intended to be reached at `http://127.0.0.1:18808/login/`.
- `C:\Users\45543\Desktop\微信桥接面板.lnk` remains the primary shortcut and points to `_bridge\mobile_openclaw_bridge\open-dashboard.ps1`.
- `C:\Users\Public\Desktop\OpenClaw 微信登录二维码.lnk` still points to the legacy standalone `generate-weixin-login-qr.ps1` flow and should be treated as legacy unless deliberately updated.
- `mobile_dashboard.py` now starts the Node QR login backend on demand when `/login/` is requested, because `weixin-login-slot-server.mjs` exits after the page heartbeat stops.
- Verified after the change: `18808/`, `/api/state`, `/login/`, `/login/api/state`, and `/login/qr.png` returned HTTP 200; `18790/api/state` also returned HTTP 200 after on-demand startup.

Failures and how to do differently:
- Pre-starting the login backend from the launcher was not reliable because the Node service exits if no browser heartbeat exists; the durable fix is to start it at the actual `/login/` request boundary.
- The old standalone QR shortcut can confuse users once the dashboard becomes the single entry point; future access guidance should explicitly label it as legacy.

References:
- `C:\Users\45543\.codex\memories\extensions\ad_hoc\notes\20260623-014254-weixin-dashboard-login-on-demand.md`
- `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\mobile_openclaw_bridge\mobile_dashboard.py:2639`
- `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\mobile_openclaw_bridge\backups\20260623-013655-login-on-demand`
- `http://127.0.0.1:18808/`
- `http://127.0.0.1:18808/login/`

## Thread `019f0f23-37a4-78b3-ab69-500913b42310`
updated_at: 2026-07-16T10:22:18+00:00
cwd: \\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
rollout_path: \\?\C:\Users\45543\.codex\sessions\2026\06\29\rollout-2026-06-29T00-49-57-019f0f23-37a4-78b3-ab69-500913b42310.jsonl
rollout_summary_file: 2026-06-28T16-49-54-n31u-mcsmanager_skills_and_bridge_kernel_attempt.md

---
description: Built a project-specific MCSManager Fabric 26.1.2 skills KB and then started a separate attempt to add a sidecar persistent task kernel to the bridge layer, but the kernel implementation was only partially written before command-length / shell invocation problems interrupted validation.
task: create project-specific skill knowledge base and attempt bridge-side persistent task kernel
task_group: mcsmanager_windows_release / codex skills + _bridge maintenance
task_outcome: partial
cwd: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
keywords: MCSManager, Fabric 26.1.2, Concerto, AutoModpack, bridge, persistent_task_kernel, system_membership, maintenance_surface_map, SQLite, apply_patch, Windows command length, WSL/bash failure
---

### Task 1: Create project-specific MCSManager skill KB

task: install and maintain a project-specific skill for the MCSManager Fabric 26.1.2 server instance

task_group: codex skills / project knowledge base
task_outcome: success

Preference signals:
- the user asked: "你能生成专门适用这个项目的知识库吗" and then "我需要这个知识库能在后续的工作中自动调用并根据实际情况修改" -> future work should default to creating a reusable project-specific skill when a repo/task has stable operational patterns, and should keep it updated as the project changes.
- the user later explicitly requested: "安装这两个skill" for uploaded skill zips -> future similar requests should be treated as install-to-skills-directory requests, not just file inspection.

Reusable knowledge:
- The project-specific skill that was created and then installed under `C:\Users\45543\.codex\skills\mcsmanager-fabric-mc\` contains `SKILL.md` plus reference docs for `mods`, `concerto`, and `known-issues`.
- The skill is intended to auto-trigger for this MCSManager instance when discussing the Fabric 26.1.2 server, the instance nickname `lsd`, UUID `178ab7fc73354fe684b15e2ac9c173a0`, MOD management, AutoModpack sync, Concerto music issues, and performance tuning.
- The installed skill’s frontmatter `description` is what determines automatic triggering; keeping that description aligned to the current server instance matters more than the README.
- The installed references captured validated facts: roughly 105+ MODs on the server, Concerto architecture/config, and a known-issues catalog including EBUSY file-lock errors and TPS overage.

Failures and how to do differently:
- Initial attempts to create skills in `C:\Users\45543\.codex\skills\...` failed with permission errors; the working path was to create the skill under the project workspace first, then copy/install to `C:\Users\45543\.codex\skills\mcsmanager-fabric-mc\` using an elevated command.
- Attempting to create `agents/openai.yaml` under the codex skills path hit directory permission issues; the successful install only guaranteed `SKILL.md` and `references/*` were present.

References:
- Installed skill path: `C:\Users\45543\.codex\skills\mcsmanager-fabric-mc\SKILL.md`
- Skill files created: `references\mods.md`, `references\concerto.md`, `references\known-issues.md`
- Trigger identifiers in the skill frontmatter: `mcsmanager-fabric-mc`, `lsd`, `178ab7fc73354fe684b15e2ac9c173a0`
- High-signal project facts used in the KB: MCSManager Web Panel `10.16.2`, Daemon `4.16.2`, Fabric Loader `0.19.3`, Java `25.0.3`, server mods and Concerto config directories

### Task 2: Add sidecar persistent task kernel to bridge layer

task: implement a new isolated persistent task lifecycle kernel plus a behavior regression test and register it in bridge maintenance docs
task_group: _bridge / scheduler-memory architecture
task_outcome: partial

Preference signals:
- the user repeatedly interrupted with "继续" after partial progress, indicating they wanted the agent to keep pushing the implementation forward rather than stopping at design discussion.
- the user’s target was described by the assistant as a "旁路" / sidecar approach that should not replace existing scheduler or mail execution paths; future similar requests should default to isolation-first changes, not invasive rewrites.

Reusable knowledge:
- The bridge layer already has a strong pattern for lifecycle-aware modules: `system_membership.py` contains contract templates and impact rules, while `docs/maintenance_surface_map.md` is the discoverability surface for new owners.
- `shared/codex_scheduler_runner.py` owns the unified maintenance wake loop and explicit task overrides; the attempted new kernel was intentionally kept out of that loop.
- A successful phase-1 design for a new task kernel should be sidecar-only: SQLite-backed task rows, `enqueue`, `claim`, `ack`, `begin`, approval pause/decision, `complete`, `fail`, `recover_expired`, plus `snapshot`/`doctor`/`repair-plan`/`validate`/`metrics`/`behavior-eval`.
- The attempted kernel file was started as `_bridge/persistent_task_kernel.py`, and the test harness as `_bridge/persistent_task_kernel_tests.py`; `system_membership.py` and `docs/maintenance_surface_map.md` were patched to reference them as bridge members/surfaces.

Failures and how to do differently:
- Multiple patch attempts failed because the command text was too long for Windows / shell invocation limits, producing errors like `文件名或扩展名太长` and patch parsing failures; future edits of this size should be split into much smaller patches or written via a file-generation script.
- A validation command later failed because Bash/WSL was unavailable in the environment (`CreateProcessCommon ... /bin/bash failed: No such file or directory`); future validation on Windows should use PowerShell-native commands or the repo’s Python entrypoints instead of assuming bash.
- The last attempted validation also exposed a logic issue in the state machine design: approval-required tasks needed to remain claimable/ackable without immediately becoming non-claimable, otherwise the intended ack→approval→resume flow breaks; future implementations should keep approval gating at execution start, not at initial enqueue.
- Because the kernel was not fully validated, do not assume the partially written files are production-ready; re-run `py_compile` and the isolated behavior test after completing the remaining methods.

References:
- Files touched: `_bridge/persistent_task_kernel.py`, `_bridge/persistent_task_kernel_tests.py`, `_bridge/system_membership.py`, `_bridge/docs/maintenance_surface_map.md`
- Important integration points: bridge contract in `system_membership.py` with an advisory health check for `persistent_task_kernel`, and maintenance surface entry describing the kernel as sidecar-only
- Validation command that failed due shell choice: `python _bridge/persistent_task_kernel_tests.py` was planned, but the combined validation step instead attempted a bash shell and failed on Windows
- Notable environment/tool errors: `Invalid patch: The last line of the patch must be '*** End Patch'`, `apply_patch requires a UTF-8 PATCH argument`, `CreateProcessCommon ... execvpe(/bin/bash) failed: No such file or directory`
- Most recent external error mentioned by the user after the interruption: `Custom tool call output is missing for call id: call_ydlBX1RhXBawo3HnhqVJme49`

## Thread `019f1c72-03c3-7032-aa56-dff625d7c720`
updated_at: 2026-07-05T17:21:31+00:00
cwd: \\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
rollout_path: \\?\C:\Users\45543\.codex\sessions\2026\07\01\rollout-2026-07-01T14-51-06-019f1c72-03c3-7032-aa56-dff625d7c720.jsonl
rollout_summary_file: 2026-07-01T06-51-01-XY1G-mobile_bridge_workflow_modularization_and_safe_refactor.md

---
description: Iterative modularization and verification of the _bridge/mobile_openclaw_bridge and workflow routing code, with emphasis on safe extraction, backup-before-edit, and validation-driven refactors
task: modularize _bridge mobile_openclaw_bridge and workflow_orchestrator while keeping behavior stable
task_group: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
keywords: workflow_orchestrator, workflow_plan_build_steps, capability_tokens, mobile_maintenance, mobile_diagnosis_issue_rules, mobile_observability_metrics, mobile_bridge_mcp_server, code_maintainability, py_compile, ruff, module_capability_index, backup_router, stdio fallback, Transport closed
---

### Task 1: safe modularization and verification of bridge/workflow code

task: refactor _bridge/mobile_openclaw_bridge and _bridge/workflow_orchestrator into smaller purpose-owned modules, then validate with compile/lint/owner checks

task_group: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager

task_outcome: success

Preference signals:
- when asked to "验证脚本可行性，做出优化，要求稳定准确", the user was explicitly steering toward evidence-based verification before trusting refactors -> future code changes should be validated with targeted compile/lint/owner checks, not just reasoning
- when the user pointed out `knownPatterns` / ghost-config logic could misclassify useful configs, they said "判断幽灵配置一定需要谨慎，防止误删有用的配置" -> future cleanup or deletion logic should default conservative, with read-only reporting or explicit confirmation before destructive actions
- when the user asked to make the workflow generic, they insisted the script "不应该包含具体的mod，它应该是通用的" -> future helpers should be data-driven and not hardcode task-specific inventories unless clearly unavoidable
- the user later wanted the process turned into a script and then asked to optimize it for "稳定准确" -> future automation should prioritize reproducible, reusable tooling over one-off manual steps

Reusable knowledge:
- Before non-trivial edits in this repo, run `python _bridge\code_maintainability.py module-context --term ...` to inspect ownership/boundary guidance; the module index is the source of truth for choosing where to place code
- `python _bridge\code_maintainability.py build-module-index --all-bridge --limit 1000` successfully rebuilt the derived module index for 177 `_bridge` modules after adding new helper modules
- `workflow_orchestrator.py` was reduced by extracting pure planning helpers into `_bridge/workflow_plan_build_steps.py` while keeping the plan schema intact; the new helpers were `collect_domain_routes`, `build_skill_orchestration`, `phase_execution_summary`, and `skill_orchestration_summary`
- `capability_tokens.grant` was safely split into pure helpers (`grant_request_error`, `grant_expiry_policy`, `build_grant_item`) while preserving authorization, artifact directory creation, write/store, and audit semantics
- `mobile_maintenance.py` was reduced by moving diagnosis rule groups to `_bridge/mobile_openclaw_bridge/mobile_diagnosis_issue_rules.py`; a new `_bridge/mobile_openclaw_bridge/mobile_observability_metrics.py` was added; both kept facades in the original module
- Validation that passed on the refactor batch: `python -m py_compile <changed files>`, `ruff check <changed files>`, `python _bridge\workflow_orchestrator.py validate`, `python _bridge\mobile_openclaw_bridge\mobile_bridge_mcp_server.py --self-test`, `python _bridge\mobile_openclaw_bridge\mobile_maintenance.py metrics --no-deep`, and `python _bridge\code_maintainability.py validate`
- A local fallback exists for mobile supplement retrieval when the native MCP transport is closed: `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id <thread_id> --timeout-seconds 8`

Failures and how to do differently:
- `regression_checks_capability.py` is intentionally rebound into CLI globals with `FunctionType(..., env)`, so `ruff` reports many undefined names there even though the tests are designed to run that way; future linting should target only the changed owner files unless the test harness itself is being redesigned
- `git diff --stat` with a file list hit a pathspec usage issue in this environment; use `git status --short`, timestamps, or direct file inspection instead of assuming git diff flags will behave
- Repeated attempts to split `mobile_bridge_mcp_server` self-test blocks were deferred because they risked creating circular imports and a worse boundary; this module needs explicit dependency-injection or facade design before further extraction
- `mobile_maintenance.inspect_system` remained intentionally large because it mixes real probes, DB reads, and snapshot assembly; the safer next step is to design a snapshot/probe boundary first, not mechanically split on line count alone
- The user emphasized that omissions or deletions must be approached cautiously; destructive cleanup logic should be conservative and based on a complete inventory, not partial success paths

References:
- [1] Backup creation before edits: `python _bridge\shared\backup_router.py create ...` succeeded repeatedly, e.g. backups for `capability_tokens.py`, `workflow_orchestrator.py`, and other files were created before patching
- [2] New helper module added: `_bridge/workflow_plan_build_steps.py` with ownership docstring and pure helpers for route collection, skill orchestration fallback, phase summary, and skill summary projection
- [3] `workflow_orchestrator.py` now imports the new helpers and its `build_plan` body is shorter (validation output later showed `build_plan` decision count down to 3 and line count reduced to ~211 decisions/lines as reported by the maintainability validator)
- [4] `capability_tokens.py` now contains the extracted helpers `grant_request_error`, `grant_expiry_policy`, and `build_grant_item`
- [5] `mobile_openclaw_bridge/mobile_observability_metrics.py` was added and the main maintenance module kept a compatibility facade
- [6] `python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id 019eca51-3ce9-76e2-9795-83f3af451f3a --timeout-seconds 8` returned a clean fallback result with `has_supplement: false` and an empty batch, proving the local stdio fallback path works when the native MCP transport is closed
- [7] Final closeout from `python _bridge\codex_workflow_entry.py closeout` reported `work_notes.active_count: 0` and no pending proposals, so there was nothing left to persist from the refactor batch
- [8] `python _bridge\code_maintainability.py validate` returned `ok: true` after the new module index was rebuilt, confirming the derived index and module-boundary checks remained healthy after the refactor batch

## Thread `019f2bb7-2d6d-7963-a33a-a14dfbf1f238`
updated_at: 2026-07-16T10:18:22+00:00
cwd: \\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
rollout_path: C:\Users\45543\.codex\sessions\2026\07\04\rollout-2026-07-04T14-00-54-019f2bb7-2d6d-7963-a33a-a14dfbf1f238.jsonl
rollout_summary_file: 2026-07-04T06-00-52-3NvG-mobile_worker_idle_backoff_fix.md

---
description: Fixed mobile bridge worker idle-backoff bug by removing `pending_reply_retries.skipped` from activity detection, added regression coverage, and validated that busy/processed/scheduled paths still stay responsive while skipped-only idle now backs off.
task: optimize mobile bridge worker idle backoff without introducing regressions
task_group: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
+keywords: worker_loop_has_activity, pending_reply_retries.skipped, idle backoff, skipped-only, fair_scheduling_check, thread_busy_status_check, backup_router, maintenance iteration, mobile_openclaw_bridge
---

### Task 1: Fix worker idle-backoff activity detection

task: worker_loop_has_activity idle-backoff fix in _bridge/mobile_openclaw_bridge

task_group: mobile bridge / worker loop

task_outcome: success

Preference signals:
- the user approved optimization but said “不要引入新的漏洞” -> future edits in this area should stay minimal, single-point, and regression-driven rather than broad refactors.
- the user later said “继续” -> once a safe fix is underway, the user is fine with the agent continuing the closeout/verification chain without restating the whole task.

Reusable knowledge:
- `worker_loop_has_activity()` had been counting `pending_reply_retries.skipped` as activity; removing that one term restored idle backoff while leaving `scheduled`, `processed`, and busy-route activity intact.
- The strongest reproducer was a pure-function case: `action=idle`, `processed=0`, `pending_reply_retries={scheduled:0, skipped:3}` originally returned `True`; after the fix it returns `False`.
- Busy-route responsiveness still matters: `skipped_busy_route=1` must remain activity, so the fix should not collapse all “skipped” states into idle.
- The worker pause state was intentionally preserved; `STOP_REQUEST` remained present and the worker process count stayed zero during validation.
- A backup was created before edits with `backup_router create`, and the resulting manifest validated successfully.

Failures and how to do differently:
- A first regression test intentionally failed before the patch, confirming the test matched the real bug shape; future similar fixes should keep that pattern: write a narrow failing test first, then patch one line.
- A CLI check for `reply-pending-account-scope-check` hit a `KeyError` because the facade exposed the command name but not the function in that path; use the actual owner module check directly when this happens.
- `fair-scheduling-check` was blocked by the real `STOP_REQUEST`; to test semantics without changing live bridge state, isolate the stop path in-process with a temporary override instead of deleting the real marker.
- The final `codex_workflow_entry.py closeout --auto-finalize` chain spawned mirror/memory-governance follow-up processes; wait for those to exit before reading final state, and don’t start duplicate validation while that governance chain is active.

References:
- [1] Before/after diff: removed `int(pending_retry.get("skipped") or 0),` from `worker_loop_observability.py` activity aggregation.
- [2] Added `worker_loop_observability_tests.py` with 4 cases:
  - skipped-only idle is inactive
  - scheduled retry remains active
  - processed task remains active
  - busy route wait remains active
- [3] Backup manifest: `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\mobile_openclaw_bridge\backups\202607\bridge\20260716-100541-before-worker-skipped-activity-fix\manifest.json`
- [4] Backup validation output: `ok: true`, `failure_count: 0`.
- [5] Test outputs: initial run failed only the skipped-only test; after patch, `.... Ran 4 tests ... OK`.
- [6] Bridge health after fix: `paused=true`, `active_count=0`, `pending_count=1`, DB integrity/schema OK.
- [7] Hashes after fix: `worker_loop_observability.py` SHA256 `CCBEE2884E76B15887838D62D62AC3E85E21D2188FF6D499EBB7937E30746AB0`; `worker_loop_observability_tests.py` SHA256 `207D3513E558D7FB12B33272CE97DF96C4F642ABA5F542E2ABDA3087C4D61E23`.

### Task 2: Verification / closeout sequence

task: validate bridge state, tests, backup, and shutdown behavior after worker fix
task_group: maintenance / closeout

task_outcome: uncertain

Preference signals:
- the user asked to continue rather than re-specify the whole validation package -> future closeout should keep moving and summarize current state succinctly.

Reusable knowledge:
- `mobile_weixin_bridge` maintenance summary in quick mode reports deep probes as skipped, not failed; do not misread skipped layers as healthy evidence.
- The bridge summary consistently reported `paused=true` and `worker=down`, which matched the intentional no-restart policy during this fix.
- `maintenance iteration` passed with no violations and only proposal-only review items (`kcl-002`, `kcl-004`, `kcl-005`); it did not authorize extra edits.

Failures and how to do differently:
- `backup_router.py validate` must be invoked with `--root <backup-dir>`; calling it with the manifest path caused an argument error.
- The `codex_workflow_entry.py mirror status` / `closeout` chain was slow and spawned intermediate snapshot/doctor processes; final acceptance should wait for those processes to exit instead of inferring completion from a silent command return.
- `Get-FileHash` on two paths in one command is fragile if the tool or shell truncates; if a future agent needs artifact hashes for closeout, fetch them separately or use a narrower one-shot command.

References:
- [8] `maintenance summary` quick output showed `paused=true`, `shadow_mode=true`, `worker=down`, `database: ok-size-high`, `pending_count=1`, `active_count=0`.
- [9] `maintenance iteration` output: `ok: true`, no violations, proposal-only review items `kcl-002`, `kcl-004`, `kcl-005`.
- [10] `backup_router.py validate --root _bridge\mobile_openclaw_bridge\backups\202607\bridge\20260716-100541-before-worker-skipped-activity-fix` returned `ok: true`, `failure_count: 0`.
- [11] The live `STOP_REQUEST` file remained at `_bridge\mobile_openclaw_bridge\STOP_REQUEST`, and worker process count stayed zero during validation.

## Thread `019f4b02-4562-7f83-a1c9-e0154223a2f8`
updated_at: 2026-07-17T05:00:49+00:00
cwd: \\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
rollout_path: C:\Users\45543\.codex\sessions\2026\07\10\rollout-2026-07-10T15-51-09-019f4b02-4562-7f83-a1c9-e0154223a2f8.jsonl
rollout_summary_file: 2026-07-10T07-51-07-5TU4-awesome_selfhosted_report_20_projects_and_memory_absorption.md

---
description: User asked for an awesome-selfhosted GitHub project analysis report in Markdown with cited links, then requested the report be expanded with a separately analyzed, categorized list of 20 notable projects and finally approved absorption of six iteration-memory candidates.
task: analyze awesome-selfhosted and write/update a markdown report with cited links; expand with 20 notable projects; absorb approved iteration candidates
 task_group: docs/research + memory_governance
 task_outcome: success
cwd: C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
keywords: awesome-selfhosted, github, markdown report, cited links, project analysis, categorized shortlist, Home Assistant, Nextcloud, Paperless-ngx, Immich, SearXNG, memory_governance, workflow_iteration_owner, workflow_review_queue
---

### Task 1: awesome-selfhosted analysis report

task: analyze awesome-selfhosted GitHub repo and write Markdown report with citation links

task_group: docs/research

task_outcome: success

Preference signals:
- The user asked: “将分析写成报告文件，格式md文件，附带主要内容的引用链接” -> future similar requests should default to creating a Markdown file, not just replying in chat, and should include source links inline.
- The user later asked to extend it with “逐个分析，整理分类，同样为主要内容附上引用链接” -> future similar report work should favor per-item analysis plus category grouping instead of a shallow summary.

Reusable knowledge:
- The report was written to `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\awesome-selfhosted-项目分析报告.md` and read back successfully.
- The report included GitHub repo metadata, README-derived structure, homepage, release notes, and a final reference list of primary links.
- Source evidence used from GitHub API and raw README included: repo metadata, `/contents`, `/readme`, `/commits?per_page=3`, `/releases?per_page=3`, plus the raw README for section/category extraction.

Failures and how to do differently:
- An initial attempt to use PowerShell here-string with `python - <<'PY'` failed with `ParserError: Missing file specification after redirection operator.` The working pattern in PowerShell was `@' ... '@ | python -`.
- A search for a preexisting report pattern using a wildcard file search timed out for a long time; later direct file listing and explicit filename edits were faster.

References:
- Report file: `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\awesome-selfhosted-项目分析报告.md`
- Repo URL: `https://github.com/awesome-selfhosted/awesome-selfhosted`
- README: `https://github.com/awesome-selfhosted/awesome-selfhosted/blob/master/README.md`
- Raw README: `https://raw.githubusercontent.com/awesome-selfhosted/awesome-selfhosted/master/README.md`
- Home page: `https://awesome-selfhosted.net/`
- Release note used for analysis: `https://github.com/awesome-selfhosted/awesome-selfhosted/releases/tag/1.0.0`

### Task 2: add 20 notable projects with category-by-category analysis

task: append 20 selected awesome-selfhosted projects, each analyzed individually and grouped by category, to the markdown report

task_group: docs/research

task_outcome: success

Preference signals:
- The user asked to “把这个也附在报告里，注意要逐个分析，整理分类，同样为主要内容附上引用链接” -> future similar list-building should default to individual writeups per item plus a category overview table.
- The user implicitly preferred a curated shortlist rather than a raw dump: the assistant selected 20 representative projects across categories and the user accepted the expansion by moving on to the next request.

Reusable knowledge:
- The 20-item shortlist that was appended covered these categories/projects: Plausible Analytics, Healthchecks, Mastodon, Paperless-ngx, Stirling-PDF, Miniflux, Nextcloud, Open-WebUI, Home Assistant, Node RED, Navidrome Music Server, Jellyfin, Actual, Vaultwarden, Homepage by gethomepage, Immich, SearXNG, Gitea, Vikunja, Wiki.js.
- The report now contains a category table, per-project analysis, and “if you only try 5” guidance.
- For repo fact extraction, a quick raw-README scan plus GitHub API calls on candidate repos was enough to collect category, description, stars, forks, pushed_at, license, and homepage.

Failures and how to do differently:
- Candidate matching by simple keyword search can miss variants or collide on similar names; for example some search terms returned `null` or ambiguous partial matches, so it helped to search the raw README headings and lines directly before finalizing the 20 entries.
- `apply_patch` via the shell failed because the patch payload was not accepted as UTF-8 in that route; switching to the filesystem edit tool was the reliable fix.

References:
- Appended section title: `## 十二、从 awesome-selfhosted 中筛出的 20 个值得重点关注的项目`
- Key retrieval command shape that worked for README parsing: `@' ... '@ | python -`
- Project repo examples used for cited links: `https://github.com/nextcloud/server`, `https://github.com/paperless-ngx/paperless-ngx`, `https://github.com/immich-app/immich`, `https://github.com/searxng/searxng`, `https://github.com/go-gitea/gitea`

### Task 3: absorb the six explicitly approved iteration candidates

task: approve/apply/validate/resolve six memory governance iteration candidates selected by the user

task_group: memory_governance

task_outcome: success

Preference signals:
- The user replied “批准吸收” after the assistant enumerated the six candidate conclusions -> future similar memory-selection workflows should treat an explicit approval like this as authorization to promote only the listed candidates.
- The user’s approval was scoped to the six enumerated items; the assistant preserved that scope and did not absorb other pending candidates.

Reusable knowledge:
- The memory absorption path for these candidates was: `workflow_review_queue.py transition --status approved` → `workflow_iteration_owner.py plan` → `apply --confirm-apply` → `validate` → `resolve`.
- The owner plan/apply/validate operations all targeted `memory.project_conclusions` and wrote to `C:\Users\45543\Desktop\Codex资源库\memory\governance\memory_absorption_index.json`.
- Each apply created a backup automatically under `_backup\202607\memory\...` before writing.
- Validate returned `identity_ok=true`, `content_ok=true`, and recall succeeded for all six items; the queue ended with `pending_count=0`.
- The six absorbed conclusions were about: ffprobe/audio_toolkit reuse for music organization, USB identity/health evidence not implying device control, capability-index reuse before adding tools/owners, scoped-only skip instructions, mirror staleness due to changed source assets after snapshot, and owner tests covering plan integrity/traversal/hash drift/collision/recovery/rollback/sidecars/hardware drift/inventory reuse/special versions.

Failures and how to do differently:
- `workflow_iteration_owner.py plan --review-id ...` failed while items were still `pending` with `candidate_not_approved`; the fix was to transition the queue items to `approved` first.
- `codex_workflow_entry.py mirror status` later reported `source_assets_changed` for unrelated workspace bridge files; that was correctly treated as concurrent source drift, not as a reason to redo or invalidate the six approved memory absorptions.

References:
- Queue IDs: `iteration:0a0dc08d9a1e295870a5a47a`, `iteration:0b591e4383b4de33b05c2a38`, `iteration:0cf2e0e172584be3b553bd63`, `iteration:6cc1e8d2138f82895ddd710b`, `iteration:a6fc41b9878c6523f2afa7e5`, `iteration:b6ad0ae1adbd21beb3c6da59`
- Memory index: `C:\Users\45543\Desktop\Codex资源库\memory\governance\memory_absorption_index.json`
- Validation evidence: all six validate calls returned `readback_ok=true`
- Queue final state: `workflow_review_queue.validate` showed `pending_count=0` after resolution
- Mirror drift evidence after the memory run: `source_assets_changed` for `_bridge\codex_resource_delegation.py`, `_bridge\intent_resource_router.py`, `_bridge\online_access_gate.py`, `_bridge\resource_cli_resource.py`, `_bridge\system_membership.py`

