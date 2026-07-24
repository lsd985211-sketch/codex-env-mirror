v1

## User Profile

The user works mainly in Windows/PowerShell around a MCSManager checkout, Minecraft Fabric/AutoModpack, Codex Desktop/runtime governance, and a mobile OpenClaw/Weixin bridge. They favor tightly scoped, reversible operations backed by actual runtime/filesystem evidence. They use Codex skills and value durable project-local Markdown when it will reduce future rework.

## User preferences

- For debugging, "找到根本原因": distinguish confirmed cause, uncertainty, commands, and validation; do not substitute a smoke test for end-to-end success.
- For session recovery, avoid repeated trial-and-error. Diagnose first, get explicit authorization before restoration, preserve the selected runtime, and do not damage existing mechanisms.
- Before consequential changes when requested, provide a read-only diagnosis/plan, then apply the smallest approved change with real verification output.
- "判断幽灵配置一定需要谨慎，防止误删有用的配置": use complete inventory/reporting first; require preview, backup, and explicit confirmation before destructive MOD/config cleanup.
- For AutoModpack, scripts must be generic rather than hardcoded, and client MODs/configs/assets should remain unchanged while missing files are supplemented; verify from metadata and runtime behavior.
- Default command output must be compact and decision-focused; `--full-output` stays richer but bounded, not a raw dump.
- Preserve user-specified runtime boundaries: Windows Desktop host, native CLI, and WSL2 execution are separate layers when relevant.
- For research, create Markdown artifacts with major citation links; put reusable templates/access guidance beside the project for later Codex use.

## General Tips

- This environment uses Windows PowerShell: use `@' ... '@ | python -`, not Bash heredocs.
- For Codex state/rollout edits: back up first, use SQLite online backup for WAL DBs, stage and parse/hash-check JSONL, then atomically replace only after the live source is safely quiesced.
- For AutoModpack organization: derive ownership from every current JAR in both `mods/` and `client-mods/`; run an idempotency test where a second run makes no valid changes; fuzzy ownership stays report-only.
- In shared worktrees, coordinate active tasks and use `git commit --only`; a committed fix is not live until the actual scheduled-task checkout hash matches.

## What's in Memory

### mcsmanager Windows release

#### 2026-07-21

- AutoModpack MOD/config organization safety: organize-mods.ps1, fabric.mod.json, ghost-config, knownPatterns, fzzy_config, client-mods, allowEditsInFiles
  - desc: Generic PowerShell MOD/config classification and client-preservation evidence under `cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`; search before rerunning or altering AutoModpack cleanup.
  - learnings: A real run deleted about 100 valid config items because `knownPatterns` omitted pre-existing MODs; full inventory, exact ownership, dry-run, verified backup, and second-run idempotency are required.

#### 2026-07-17

- Research artifacts, FreeDomain, mirror milestone: awesome-selfhosted, FreeDomain-Cloudflare-DNS-初始化模板.md, seed-v2.3.1, system_membership
  - desc: Cited reports, disposable Cloudflare public-entrypoint boundaries, and a published but not fully closed mirror milestone under `cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`.
  - learnings: Recheck closeout for `main_task_complete: true`; never directly expose bridge, Codex, databases, or admin surfaces.
- CC Switch logging crash mitigation: cc-switch.db, log_config, forwarder.rs, 127.0.0.1:15721
  - desc: DB-backed mitigation for CC Switch logging-path exits; consult before changing proxy logging.
  - learnings: `proxy_config.enable_logging` is separate from global log level; validate DB integrity, unchanged routing, and listening port.

### Codex Desktop and runtime

#### 2026-07-18

- Session recovery and cwd metadata repair: state_5.sqlite, 0-byte JSONL, SQLite online backup, 13 cwd fields, node_repl, backup_router
  - desc: Evidence-backed recovery of thread `019f1c72-03c3-7032-aa56-dff625d7c720`; search before editing Codex state or legacy rollout metadata under `C:\Users\45543\.codex`.
  - learnings: Do not overwrite a live JSONL after a read/lock error; repair both SQLite and structured historical cwd fields, then separate repair checks from an actual completed resume.
- Windows startup and WSL popup diagnostics: CodexModelProviderWatcher, appserver_bridge_unavailable, CREATE_NO_WINDOW, wsl.exe, conhost.exe, CODEX_HOME
  - desc: Windows Desktop/WSL2 layering, elevated launcher chain, and verified live watcher popup fix; source work was in `/home/codexlab/work/codex-workspace` with targeted Windows deployment.
  - learnings: Use hidden WSL subprocess launches plus runtime-only reconciliation/cooldown; inspect `CODEX_HOME` leakage before interpreting native diagnostics.

### Older Memory Topics

#### C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager

- Worker idle-backoff repair and bounded closeout: worker_loop_has_activity, pending_reply_retries.skipped, bounded_output.py, --full-output
  - desc: Narrow mobile bridge worker activity fix plus shared closeout projection/mirror verification; use for `_bridge` worker loops or closeout-output contracts in `cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`.
- Mobile OpenClaw reply protocol and dashboard: protocol_violation_no_owned_result, backup1, 127.0.0.1:18808, login-on-demand
  - desc: Primary visible-CDP follow-up recovery, backup1 boundary, and verified dashboard/login entrypoints; live bridge state is checkout-sensitive.

#### C:\Users\45543\Documents\mc

- Minecraft Fabric 26.1.2 global skill: fabric-mc-26-1-2, Java 25, Fabric Loom 1.15, Mojang official mappings
  - desc: Current Fabric client/server/mod/shader guidance and global skill location `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md`; recheck versions before use.
