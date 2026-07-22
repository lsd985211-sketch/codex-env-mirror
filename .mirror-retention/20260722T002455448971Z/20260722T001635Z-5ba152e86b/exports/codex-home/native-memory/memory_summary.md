v1

## User Profile

The user operates Windows/PowerShell workflows around the MCSManager checkout, Codex Desktop/runtime governance, a mobile OpenClaw/Weixin bridge, and Minecraft Fabric. They favor narrowly scoped, reversible operational work with proof from the actual runtime. They use Codex skills and expect durable project-local Markdown when it will help later tasks.

## User preferences

- For debugging, "找到根本原因": distinguish confirmed cause, uncertainty, commands, and validation; do not substitute a smoke test for end-to-end success.
- For session recovery, avoid repeated trial-and-error. Diagnose first, get explicit authorization before restoration, preserve the selected runtime, and do not damage existing mechanisms.
- Before consequential changes when requested, provide a read-only diagnosis/plan, then apply the smallest approved change with real verification output.
- "判断幽灵配置一定需要谨慎，防止误删有用的配置": inventory/report first; never silently remove live configuration.
- Default command output must be compact and decision-focused; `--full-output` stays richer but bounded, not a raw dump.
- Preserve user-specified runtime boundaries: state Windows Desktop host, native CLI, and WSL2 execution as separate layers when relevant.
- For research, create Markdown artifacts with major citation links; put reusable templates/access guidance beside the project for later Codex use.

## General Tips

- This environment uses Windows PowerShell: use `@' ... '@ | python -`, not Bash heredocs.
- For Codex state/rollout edits: back up first, use SQLite online backup for WAL DBs, stage and parse/hash-check JSONL, then atomically replace only after the live source is safely quiesced.
- In shared worktrees, coordinate active tasks and use `git commit --only`; a committed fix is not live until the actual scheduled-task checkout hash matches.
- In `_bridge`, find ownership with `code_maintainability.py module-context`, rebuild the module index after helper additions, lint changed owners, and retain compatibility facades.

## What's in Memory

### Codex Desktop and runtime

#### 2026-07-18

- Session recovery and cwd metadata repair: state_5.sqlite, 0-byte JSONL, SQLite online backup, 13 cwd fields, node_repl, backup_router
  - desc: Evidence-backed recovery of thread `019f1c72-03c3-7032-aa56-dff625d7c720`; search before editing Codex state or legacy rollout metadata under `C:\Users\45543\.codex`.
  - learnings: Do not overwrite a live JSONL after a read/lock error; repair both SQLite and structured historical cwd fields, then separate repair checks from an actual completed resume.
- Windows startup and WSL popup diagnostics: CodexModelProviderWatcher, appserver_bridge_unavailable, CREATE_NO_WINDOW, wsl.exe, conhost.exe, CODEX_HOME
  - desc: Windows Desktop/WSL2 layering, elevated launcher chain, and verified live watcher popup fix; source work was in `/home/codexlab/work/codex-workspace` with targeted Windows deployment.
  - learnings: Use hidden WSL subprocess launches plus runtime-only reconciliation/cooldown; inspect `CODEX_HOME` leakage before interpreting native diagnostics.

### mcsmanager Windows release

#### 2026-07-17

- Research artifacts, FreeDomain, mirror milestone: awesome-selfhosted, FreeDomain-Cloudflare-DNS-初始化模板.md, seed-v2.3.1, system_membership
  - desc: Cited reports, disposable Cloudflare public-entrypoint boundaries, and a published but not fully closed mirror milestone under `cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`.
  - learnings: Recheck closeout for `main_task_complete: true`; never directly expose bridge, Codex, databases, or admin surfaces.
- CC Switch logging crash mitigation: cc-switch.db, log_config, forwarder.rs, 127.0.0.1:15721
  - desc: DB-backed mitigation for CC Switch logging-path exits; consult before changing proxy logging.
  - learnings: `proxy_config.enable_logging` is separate from global log level; validate DB integrity, unchanged routing, and listening port.

#### 2026-07-16

- Worker idle-backoff repair: worker_loop_has_activity, pending_reply_retries.skipped, STOP_REQUEST
  - desc: Narrow mobile bridge worker fix and paused-state validation.
  - learnings: Remove only skipped historical retries from activity; retain scheduled, processed, and busy-route signals.
- Bounded closeout output: bounded_output.py, full_bounded, --full-output, post_closeout_mirror
  - desc: Shared closeout projection contract and mirror verification path.
  - learnings: Preserve reason/action/finalization fields; raw payloads belong behind `record_path` or `raw_result_ref`.

### Older Memory Topics

#### C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager

- Validation-first bridge modularization: code_maintainability.py, build-module-index, workflow_plan_build_steps.py, supplement-fallback
  - desc: Safe `_bridge` helper extraction, facades, owner-focused linting, and validation; rebuild index after module additions.
- Mobile OpenClaw reply protocol and dashboard: protocol_violation_no_owned_result, backup1, 127.0.0.1:18808, login-on-demand
  - desc: Primary visible-CDP follow-up recovery, backup1 boundary, and verified dashboard/login entrypoints; live bridge state is checkout-sensitive.

#### C:\Users\45543\Documents\mc

- Minecraft Fabric 26.1.2 global skill: fabric-mc-26-1-2, Java 25, Fabric Loom 1.15, Mojang official mappings
  - desc: Current Fabric client/server/mod/shader guidance and global skill location `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md`; recheck versions before use.
