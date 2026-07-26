thread_id: 019f743d-069f-7a32-bd75-8e1ab7020b7b
updated_at: 2026-07-18T12:03:16+00:00
rollout_path: \\?\C:\Users\45543\.codex\sessions\2026\07\18\rollout-2026-07-18T15-59-44-019f743d-069f-7a32-bd75-8e1ab7020b7b.jsonl
cwd: C:\Users\45543\Documents\Codex\2026-07-18\ni

# Codex WSL popup root cause fixed and deployed

Rollout context: The user questioned whether Codex Desktop was truly running natively after selecting the native environment, then asked for root-cause diagnosis of sandbox-setting failure and transient console windows. The substantive engineering work occurred in `/home/codexlab/work/codex-workspace` and the live Windows checkout at `/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager`.

## Task 1: Environment and sandbox diagnosis

Outcome: partial

Key steps:

- Verified the distinction between native Windows Desktop UI and WSL2 command execution. `uname` reported `microsoft-standard-WSL2`; distro was `Codex-Wsl-Lab`.
- Confirmed native Windows Codex binaries existed, including `codex.exe` and `codex-windows-sandbox-setup.exe`.
- Native `codex doctor` showed `CODEX_HOME` resolving to the WSL projected path and reported `config could not be loaded`, supporting a configuration/environment-boundary problem rather than native binaries being absent.
- Inspected the elevated desktop shortcut and launcher chain: `Codex Current Admin.lnk` invokes `wscript.exe`, `run-hidden.vbs`, then `start-codex-desktop-elevated.ps1`.
- The sandbox helper’s embedded strings showed operations involving sandbox users, DPAPI, ACLs, WFP/firewall rules, and setup markers, confirming that sandbox initialization is an administrative Windows operation.

The exact persisted sandbox error marker was not found, so the precise sandbox setup failure remained partially unverified. The native environment itself was not proven permanently unavailable.

## Task 2: Diagnose and fix transient console popups

Outcome: success

Root cause:

- `CodexModelProviderWatcher` repeatedly observed an unbound runtime state (`appserver_bridge_unavailable`) and called full startup state repair.
- On Windows, the repair path launched visible WSL subprocesses, producing the observed `wsl.exe -> conhost.exe` chain roughly every 32 seconds.

Implementation:

- Added `CREATE_NO_WINDOW` flags to both WSL subprocess launch sites in `codex_state_repair.py`.
- Added a runtime-only reconciliation path in `codex_model_provider_watcher.py` that skips full startup repair.
- Added a 300-second cooldown for successful repeated unbound reconciliation while retaining 15-second retries for actual failures and resetting on source changes.
- Added regression tests for hidden WSL launches, cooldown behavior, failure retries, and runtime-only repair.

Validation:

- Focused tests passed 7/7 in both WSL and the Windows live checkout.
- Full related suite: 70/73 passed; the three failures were unchanged pre-existing Windows/WSL discovery probes.
- `popup_window_doctor validate`: passed with `risk_count: 0`.
- Forty-second observation found zero provider-watcher popup chains; only diagnostic Codex shell processes appeared.
- The live watcher automatically reloaded the new implementation fingerprint and restarted, so no manual restart was required.
- Changes were committed as `918429e` and `ab8a0bf`, pushed to the Windows bare Git origin, and the targeted Windows live source was deployed after routed backups.

Important process lessons:

- In shared worktrees, coordinate active Codex threads before editing; a concurrent task temporarily ran `git restore` over the target files.
- Use `git commit --only` when unrelated changes are present.
- Do not claim a committed fix is live until hashes of the actual scheduled-task checkout match the committed source.
- `code_maintainability.py validate` was not green because `uv`, `uvx`, and `ruff` were unavailable and existing placement advisories remained; this was unrelated to the popup fix.

