v1

## User Profile

The user works across Windows/PowerShell and WSL2, especially the `/home/codexlab/work/codex-workspace` Codex Desktop/runtime tooling and a MCSManager/Fabric/AutoModpack checkout. They want bounded, reversible work backed by actual runtime, filesystem, and route-ownership evidence. They use Codex skills and project-local Markdown when it prevents future rework. They distinguish native Windows Desktop, native CLI, and WSL execution layers, and expect agents to preserve that distinction.

## User preferences

- For a requested read-only audit, do not install or modify anything; separate blockers from optional dependencies.
- "你不是应该通过hub调用吗": validate Hub-first owner tools in the current turn before diagnosing missing local CLI/package fallbacks as unavailable.
- For debugging, "找到根本原因": separate confirmed cause, uncertainty, commands, and validation; smoke tests do not equal end-to-end success.
- Before consequential changes when requested, provide a read-only diagnosis/plan, then apply the smallest approved change with real verification output.
- Preserve ownership boundaries in shared worktrees: pause overlapping source writes when another task owns them; use isolated, backed-up runtime-state repair where possible.
- "判断幽灵配置一定需要谨慎，防止误删有用的配置": inventory first; uncertain MOD/config cleanup needs preview, backup, and explicit confirmation.
- "注意不要破坏现有机制": change only confirmed state fields and preserve MCP, configuration, startup logic, and unrelated data.

## General Tips

- For Codex/runtime diagnosis, distinguish Windows Desktop host, Windows/native process state, WSL2 execution, app-server, and MCP/plugin layers; inspect current selection state, not just registration.
- In the governed WSL workspace, try `mcp__local_mcp_hub__hub_validate` then direct PMB/CodeGraph calls before local fallbacks. A successful Hub call still requires current-workspace binding and target-index coverage.
- For state/rollout edits, back up first; use SQLite online backup for WAL DBs, stage and parse/hash-check JSONL, then atomically replace only after the source is quiesced.
- For AutoModpack organization, derive ownership from every current JAR in both `mods/` and `client-mods/`; second-run idempotency is required; fuzzy ownership stays report-only.
- In shared worktrees, `git commit --only` limits unrelated changes, but deployment is not live until the scheduled-task checkout hash matches.

## What's in Memory

### /home/codexlab/work/codex-workspace

#### 2026-07-25

- Hub-first WSL workspace audit and Desktop active-state repair: Hub-first, mcp__local_mcp_hub__hub_validate, pmb.prepare, codegraph_index_unusable, selected-project, active-workspace-roots, codex-cdp-port.ps1, 9229, 9231
  - desc: Read-only dependency audit, PMB/CodeGraph workspace-routing diagnosis, and guarded Desktop/CDP runtime-state repair for `cwd=/home/codexlab/work/codex-workspace`; search before declaring local tools unavailable or changing Desktop routing.
  - learnings: Hub capability was available, but PMB was bound to old Windows scope and WSL CodeGraph path projection/index was invalid; active selection needs separate inspection; do not choose a shortcut-only CDP port override while 9229/9231 authority conflicts.

#### 2026-07-23

- Codex Desktop WSL persistence and session projection: desktop.runCodexInWindowsSubsystemForLinux, WslEnabled=False, host_changed, state_5.sqlite, wsl_codex_runtime.py, conflict_count=5
  - desc: Root cause for reboot fallback and split Windows/WSL histories, plus protected projection v6 under `cwd=/home/codexlab/work/codex-workspace`.
  - learnings: Projection completed with five preserved conflicts and active-source drift; reboot acceptance, handler reload, and no-popup checks remained pending.

### mcsmanager Windows release

#### 2026-07-21

- AutoModpack MOD/config organization safety: organize-mods.ps1, fabric.mod.json, ghost-config, knownPatterns, fzzy_config, client-mods, allowEditsInFiles
  - desc: Generic PowerShell MOD/config classification and client-preservation evidence under `cwd=C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`; search before rerunning or altering AutoModpack cleanup.
  - learnings: A real run deleted about 100 valid config items because `knownPatterns` omitted pre-existing MODs; full inventory, exact ownership, dry-run, verified backup, and second-run idempotency are required.

### Older Memory Topics

#### /home/codexlab/work/codex-workspace and Codex state

- Session recovery and cwd metadata repair: state_5.sqlite, 0-byte JSONL, SQLite online backup, 13 cwd fields, node_repl, backup_router
  - desc: Evidence-backed recovery of thread `019f1c72-03c3-7032-aa56-dff625d7c720`; use before editing legacy Codex state/rollout metadata under `C:\Users\45543\.codex`.
- Windows startup and WSL popup diagnostics: CodexModelProviderWatcher, appserver_bridge_unavailable, CREATE_NO_WINDOW, wsl.exe, conhost.exe, CODEX_HOME
  - desc: Windows Desktop/WSL2 layering, elevated launcher chain, and verified live watcher-popup fix; source work was in `/home/codexlab/work/codex-workspace` with targeted Windows deployment.

#### C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager

- Research artifacts, FreeDomain, mirror milestone: awesome-selfhosted, FreeDomain-Cloudflare-DNS-初始化模板.md, seed-v2.3.1, system_membership
  - desc: Cited reports, disposable Cloudflare public-entrypoint boundaries, and a published but not fully closed mirror milestone.
- CC Switch logging crash mitigation: cc-switch.db, log_config, forwarder.rs, 127.0.0.1:15721
  - desc: DB-backed mitigation for CC Switch logging-path exits; consult before changing proxy logging.
- Worker idle-backoff repair and bounded closeout: worker_loop_has_activity, pending_reply_retries.skipped, bounded_output.py, --full-output
  - desc: Narrow mobile bridge worker activity fix plus shared closeout projection/mirror verification for `_bridge` worker loops or closeout-output contracts.
- Mobile OpenClaw reply protocol: protocol_violation_no_owned_result, visible-CDP, mobile_tasks, mobile_events
  - desc: Primary visible-CDP follow-up recovery and backup1 boundary; live bridge state is checkout-sensitive.
- Bridge governance and maintenance notes: backup_router.py create, positional paths, stdio UTF-8, owned-result idempotency, resource_process_doctor.py cleanup
  - desc: Cross-cutting backup, MCP, bounded-output, fixture-isolation, and document-publishing rules for the mcsmanager/Codex workspace. [ad-hoc note]

#### C:\Users\45543\Documents\mc

- Minecraft Fabric 26.1.2 global skill: fabric-mc-26-1-2, Java 25, Fabric Loom 1.15, Mojang official mappings
  - desc: Current Fabric client/server/mod/shader guidance and global skill location `C:\Users\45543\.codex\skills\fabric-mc-26-1-2\SKILL.md`; recheck versions before use.
