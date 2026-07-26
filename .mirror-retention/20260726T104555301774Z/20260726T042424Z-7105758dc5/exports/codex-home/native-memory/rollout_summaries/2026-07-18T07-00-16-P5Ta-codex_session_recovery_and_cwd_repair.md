thread_id: 019f7406-9545-7433-b4ec-d82c320c1358
updated_at: 2026-07-18T07:24:37+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/07/18/rollout-2026-07-18T15-00-16-019f7406-9545-7433-b4ec-d82c320c1358.jsonl
cwd: /mnt/c/Users/45543/Documents/Codex/2026-07-18/new-chat-3

# Codex thread recovery and cwd repair

Rollout context: On Windows, the user reported that resuming an old Codex thread failed because its JSONL was empty, then authorized restoration and a narrowly scoped cwd metadata repair after the restored thread failed during MCP initialization.

## Task 1: Diagnose and restore empty session

Outcome: success

The affected thread was `019f1c72-03c3-7032-aa56-dff625d7c720`. The live JSONL was 0 bytes, but a routed backup from 13:20:05 was 312,627,553 bytes, 151,553 lines, valid JSONL, and matched the thread ID. Two older 607 MB backups also existed.

Root cause was confirmed from the preserved repair-session rollout: a repair attempted to read the active thread while another process held it. `StreamReader` failed with a Windows sharing/lock error, but PowerShell continued because the failure was non-terminating. The script had already created an empty temporary file and then unconditionally executed `Move-Item ... -Force`, replacing the real session with the empty file. The thread remained usable until restart because the old process still held the original handle/state; restart exposed the 0-byte path.

The current empty target was backed up, then the validated backup was copied to a stage, checked for expected size and SHA-256, and atomically moved over the target. Full JSONL parsing passed: 151,553 lines, expected session metadata, and no parse errors. Backup-router validation and backup hygiene validation passed.

## Task 2: Repair invalid cwd metadata

Outcome: success

After restoration, resume failed with `required MCP servers failed to initialize: node_repl: 目录名称无效 (os error 267)`. Live config and `codex mcp list` showed `node_repl` was configured correctly, so the issue was thread-specific metadata. The SQLite `threads.cwd` and 13 structured cwd fields in the JSONL pointed to nonexistent WindowsApps-derived or malformed paths. The canonical path was `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`.

The user explicitly authorized a repair that must not disturb existing mechanisms. A routed rollback set was created. Because `state_5.sqlite` was in WAL mode and direct WAL copying failed with WinError 33, SQLite's online backup API created a consistent snapshot. A staged JSONL was then generated and fully read back before mutation; exactly 13 cwd fields changed. A SQLite transaction updated exactly one matching thread row, and the staged JSONL was atomically replaced. Final validation showed SQLite integrity `ok`, zero invalid cwd values, correct thread ID, and successful Codex `read_thread` access.

A direct `node_repl` process launched from the corrected cwd completed JSON-RPC `initialize` successfully with `rmcp 1.5.0`. Backup manifests, hashes, and backup hygiene checks passed. No MCP registration, startup configuration, projection rule, or business code was changed.
