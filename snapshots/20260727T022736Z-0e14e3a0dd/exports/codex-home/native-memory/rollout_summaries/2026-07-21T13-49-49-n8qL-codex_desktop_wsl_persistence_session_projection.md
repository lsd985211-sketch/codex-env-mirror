thread_id: 019f84f0-9cee-7f70-a1e7-67b0acf40f23
updated_at: 2026-07-23T00:56:35+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/07/21/rollout-2026-07-21T21-49-54-019f84f0-9cee-7f70-a1e7-67b0acf40f23.jsonl
cwd: /home/codexlab/work/codex-workspace

# Codex Desktop WSL Persistence Diagnosis and Repair

Rollout context: The user wanted to know whether selecting WSL in Codex Desktop would survive a reboot with sessions, tools, and plugins intact. After reboot, Codex reverted to Windows and the current conversation showed the Windows-side history. The work occurred in `/home/codexlab/work/codex-workspace`, with Windows Desktop as the host and `Codex-Wsl-Lab` as the execution environment.

## Task 1: Read-Only Diagnosis of WSL Fallback and History Divergence

Outcome: success

Preference signals:

- The user explicitly asked to find the root cause of the environment fallback and later reported that the visible history remained the Windows history. Future investigations should compare both runtime stores, identify the actual startup writer and timestamps, and avoid treating the Desktop UI alone as authoritative.
- The user expects careful, stability-first, read-only diagnosis before configuration changes and wants the ability to intentionally return to Windows preserved.

Key steps:

- Verified the Desktop project was registered at `\\wsl.localhost\Codex-Wsl-Lab\home\codexlab\work\codex-workspace` with project ID `7729bf8c-a9a4-42e4-b89a-6264ae14dfa8`.
- Compared Windows and WSL configurations, selection state, startup logs, scheduled tasks, ConfigGuard, watcher, and launcher source.
- Found the decisive boot evidence: the 2026-07-22 launcher preflight logged `WslEnabled=False`, while manual selection later wrote both configs and selection state back to true.
- Boot-window backups showed selection state changing from true to false with `selection_source="host_changed"`; this was the automatic fallback event rather than a Desktop registration failure.
- Confirmed Windows and WSL use separate `CODEX_HOME`/SQLite/session stores. The same title `测试信息` referred to different task IDs and independently evolving rollout files; there is no safe bidirectional history merge protocol.

Reusable knowledge:

- Always separate Windows Desktop/native binaries, WSL2 execution, WSL app-server, and MCP/plugin layers in reports.
- Inspect `C:\Users\45543\.codex\.codex-global-state.json`, `C:\Users\45543\.codex\config.toml`, `/home/codexlab/.codex-app/config.toml`, and both environment-selection state files together.
- The launcher chain is `CodexDesktopElevatedAtLogon` -> `wscript.exe` -> `run-hidden-wait.vbs` -> `start-codex-desktop-elevated.ps1`.

Failures and how to do differently:

- A codegraph query returned `codegraph_scope_insufficient` due to incorrect target path prefixes; use direct bounded reads when target coverage is zero.
- A shell loop accidentally expanded commands to empty strings, and a nested Python `-c` probe failed from quoting. Use explicit PowerShell arrays and here-strings piped to `python3 -`.
- A Windows SQLite read encountered `database is locked`; treat this as active ownership and use direct WSL reads or SQLite online backup rather than copying live WAL state.

References:

- Root-cause log: `WslEnabled=False; WslRuntimeReady=True; WslProjectionStatus=not_required` during boot.
- Selection key: `desktop.runCodexInWindowsSubsystemForLinux`.
- Selection schema: `codex-desktop-environment-selection.v1`.
- Windows state path: `C:\Users\45543\.codex\state\desktop-environment-selection.json`.

## Task 2: Protected Runtime Apply and Session Projection

Outcome: partial

Preference signals:

- The user requires no destructive overwrite, no task-ID merging, preservation of active sessions, and careful validation before asking for a reboot.
- The user prefers exact evidence and clear distinction between completed work, active-source drift, and unverified reboot behavior.

Key steps:

- Created and validated routed backups for the canonical WSL long rollout, an SQLite online snapshot, metadata, and the session projection manifest. Source and backup hashes matched; both SQLite source and snapshot integrity checks returned `ok`.
- Quiesced the WSL app-server through its owner, executed `wsl_codex_runtime.py apply`, and restarted the user-systemd service.
- Applied projection result: 309 Windows source sessions, 304 projected, 1 translated sub-result, 5 conflicts preserved; manifest upgraded to v6. No divergent destinations were overwritten.
- Verified the WSL app-server was active with a Unix socket and passed `codex-app-server-validate`.
- Verified both configs and selection state remained true, the canonical WSL rollout hash was unchanged, SQLite integrity was `ok`, and real Local MCP Hub and Node REPL calls worked.

Reusable knowledge:

- Use `sqlite3.Connection.backup()` plus `PRAGMA integrity_check`; never copy active `state_5.sqlite`, `-wal`, or `-shm` directly.
- Use `backup_router.py create` for routed backup manifests and verify every copied item hash.
- Treat a stopped service as confirmed when `ActiveState=inactive` and its socket is absent, even if a wrapper returns `ok=false` for the inactive status.
- Recompute session conflicts from the current manifest and fingerprints. The live conflict count changed from an earlier stale estimate of 4 to 5.

Failures and how to do differently:

- The detached orchestration script misclassified a successful stop and skipped apply; direct apply was then run safely in the confirmed stopped window.
- Strict validation remained non-green because the active Windows rollout continued growing after apply. This is active-source drift, not evidence that the WSL canonical task was damaged.
- A page reload left advisory `electron_list_models_for_host_handler_unavailable`; only a full Desktop process restart or computer reboot can validate the Electron main-process handler.

References:

- Backup manifest: `/home/codexlab/.codex-app/backups/202607/codex-runtime/20260723-002659-491076-4e2ca992-wsl-canonical-long-task--sqlite-online-snapshot--metadata-and-v5-projection-mani/manifest.json`
- Apply receipt: `/home/codexlab/.codex-app/state/wsl-runtime-apply-receipt-20260723.json`
- Canonical WSL task: `019f7979-3c05-72b0-b3b7-8c1c6e5b0ed2`, rollout size `108768046`, SHA-256 `d41bc9cc9d0685de3a14a0a044381fa01af55552cb64aa2e50d432f2e31c5435`
- Remaining acceptance: perform a real reboot, confirm WSL remains selected, confirm the canonical session is visible, re-test MCP/plugin calls, check the Electron advisory disappears, and verify no new WSL console popups.
