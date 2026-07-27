thread_id: 019f743d-069f-7a32-bd75-8e1ab7020b7b
updated_at: 2026-07-19T03:58:27+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/07/18/rollout-2026-07-18T15-59-44-019f743d-069f-7a32-bd75-8e1ab7020b7b.jsonl
cwd: /mnt/c/Users/45543/Documents/Codex/2026-07-18/ni

# Codex environment diagnosis and popup fix

Rollout context: The user first asked about the current runtime and whether Codex was running natively or under WSL, then requested root-cause diagnosis of sandbox/native-environment behavior and recurring black console windows. Work involved `/home/codexlab/work/codex-workspace` plus the Windows live checkout under `/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager`.

## Task 1: Identify the actual runtime environment

Outcome: success

Preference signals:

- The user distinguished the Windows desktop display from the command runtime and asked for careful verification -> future responses should state both layers explicitly.
- Exact marker-only resume tests were requested and correctly answered without tools.

Key steps:

- Checked kernel and environment variables rather than relying on the mounted path.
- Confirmed WSL2 kernel `6.18.33.2-microsoft-standard-WSL2`, distro `Codex-Wsl-Lab`, Linux bash, and `/mnt/c` Windows-drive mounting.
- Confirmed native Windows Codex binaries also existed, so the Desktop host and tool runtime were separate layers.

## Task 2: Diagnose and fix recurring black console windows

Outcome: success

Preference signals:

- The user requested the root cause, rigorous evidence, and no hallucinated conclusions.
- The user clarified that Codex was launched through an elevated Desktop shortcut, requiring inspection of the shortcut and script chain.

Key steps:

- Inspected WSL/native configs, Codex binaries, the native sandbox helper, the elevated shortcut, launcher scripts, watcher state, and process trees.
- Confirmed the native executable and sandbox helper existed; native environment was not permanently unavailable.
- Found the actual fault pattern: `CodexModelProviderWatcher` repeatedly reconciled an unbound runtime and invoked full startup repair, which spawned visible `wsl.exe -> conhost.exe` processes.
- Added `CREATE_NO_WINDOW` to both Windows-side WSL subprocess calls.
- Separated runtime-only reconciliation from full startup repair and added a 300-second success cooldown with a 15-second failure retry.
- Added focused regression tests for hidden WSL launches, cooldown behavior, retry behavior, and skipping full startup repair.
- Backed up the Windows live files before deployment, deployed the matching patch to the legacy Windows checkout used by the scheduled watcher, and verified all four production/test hashes matched the WSL implementation.
- The watcher auto-restarted on implementation fingerprint change, so no manual restart was needed.
- Verification: focused live tests 7/7 passed; a 40-second reproduction window found zero provider-watcher popup chains; `popup_window_doctor validate` passed with zero risks; related suite was 70/73 with three unchanged environment-probe failures.
- Commits `918429e` and `ab8a0bf` were pushed to `origin/main`; closeout and backup receipts were recorded.

Failures and how to do differently:

- A concurrent task temporarily restored the four target files. Coordinate shared worktree edits and use scoped commits such as `git commit --only`.
- A cooldown test initially used a WSL fixture that reached Windows-only `msvcrt`; mark projection state as already synchronized when isolating watcher behavior.
- Do not claim runtime effectiveness from a WSL commit alone; validate the actual Windows live source, then run focused tests and an observation window.

Reusable knowledge:

- The relevant implementation points are `codex_state_repair.py` and `codex_model_provider_watcher.py`.
- The supported repository flow is WSL Work Git -> Windows bare Git -> validated mirror publish -> GitHub recovery repository; mirror operations are single-owner and should not run concurrently.
- Backup manifests are under the Windows checkout’s `_bridge/backups/manual/202607/codex-startup/` directory.

