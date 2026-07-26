thread_id: 019f2bb7-2d6d-7963-a33a-a14dfbf1f238
updated_at: 2026-07-16T10:18:22+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/07/04/rollout-2026-07-04T14-00-54-019f2bb7-2d6d-7963-a33a-a14dfbf1f238.jsonl
cwd: /mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager

# Mobile Bridge Worker Backoff Fix

Rollout context: In the Windows workspace `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`, the user first asked why conversation restoration failed, then authorized a narrowly scoped optimization of the mobile bridge worker while emphasizing that no new vulnerabilities or functional regressions should be introduced. The workspace is not a Git repository, so backups and hashes were used for change tracking.

## Task 1: Diagnose conversation restoration

Outcome: partial

Key steps:
- Code search found the Codex app-server `thread/resume` path, thread hydration checks, `thread/turns/list` verification, and multiple historical repair backups.
- The implementation distinguishes direct `thread/resume` errors from successful resume followed by missing desktop thread hydration/materialization.
- Screenshot evidence was interpreted as MCP initialization/handshake failure during restoration rather than missing conversation content.
- `mcp_session_doctor.py validate` reported configuration/service health as OK but warned that active current-turn MCP callability was not proven. `resource_process_doctor.py doctor` reported the bridge app-server owner on port `18791` missing, as an advisory.

Failures and how to do differently:
- The initial broad `rg` scans traversed generated bundles, virtual environments, backups, and other huge trees, producing tens of millions of tokens and truncated output. Future diagnosis should exclude generated/cache/venv trees and start with targeted paths such as `_tools/codex-app-server-tools`, `_bridge/mobile_openclaw_bridge`, and schema files.
- The rollout did not complete a direct end-to-end restoration repro, so the MCP-handshake explanation remained evidence-supported but not fully isolated to one failing service.

Reusable knowledge:
- `thread/resume` populates turns only when requested with `includeTurns`; the client maps this to `excludeTurns: false` and then verifies via `thread/turns/list`.
- A thread can be listed but not loaded; bridge code explicitly treats this as a resumable state and may resume it when dispatching.

## Task 2: Optimize worker idle backoff

Outcome: partial

Preference signals:
- The user said: "好，你来执行优化，但是注意不要引入新的漏洞" -> use minimal, behavior-preserving changes with focused regression tests.
- The agent preserved the user's paused state and did not clear `STOP_REQUEST`, restart the worker, change routing, or alter queue/database state.

Key steps:
- Confirmed the root cause from logs and pure-function behavior: `idle`, `processed=0`, `scheduled=0`, `skipped=3` was treated as activity, preventing the configured idle backoff.
- Ran workflow/module-context/placement gates and created a SHA256-verified routed backup before editing.
- Added four pure-function tests, observed the skipped-only test fail before the fix, removed only the `skipped` activity count, and reran successfully.
- Ran focused scheduling and busy-thread checks with an isolated temporary stop path. Both passed; the normal scheduler check was correctly blocked by the real stop marker.
- Post-change bridge health passed with DB/schema integrity OK, active tasks 0, paused true, and worker process count 0.
- Maintenance iteration gate passed with no violations; remaining proposal clusters were proposal-only and not applied.

Failures and how to do differently:
- `reply-pending-account-scope-check` via the CLI failed with `KeyError: 'reply_pending_account_scope_check'` because the facade registry did not expose the moved function. Direct owner-module execution passed. Track this as residual validator drift.
- Closeout launched environment mirror snapshot/validation and downstream maintenance/memory governance checks. At the end of the supplied rollout, final mirror status was still being awaited; the implementation itself was verified, but overall rollout closeout remains partial/uncertain.

Reusable knowledge:
- The exact safe semantic boundary is: skipped/cooldown-only historical reply retries do not count as worker activity; scheduled retries, real processing, and busy routes do count.
- Do not remove the real `_bridge\\mobile_openclaw_bridge\\STOP_REQUEST` to run tests. Use the existing `TemporaryStopRequestPath` isolation helper.

References:
- Changed source: `_bridge\\mobile_openclaw_bridge\\worker_loop_observability.py`
- Added tests: `_bridge\\mobile_openclaw_bridge\\worker_loop_observability_tests.py`
- Focused test result: `Ran 4 tests ... OK`
- Backup validation result: `manifest_count=1`, `failure_count=0`
- Final source hash observed: `CCBEE2884E76B15887838D62D62AC3E85E21D2188FF6D499EBB7937E30746AB0`
- Final test hash observed: `207D3513E558D7FB12B33272CE97DF96C4F642ABA5F542E2ABDA3087C4D61E23`
- Real stop marker: `_bridge\\mobile_openclaw_bridge\\STOP_REQUEST`
- Pending final verification command: `python _bridge\\codex_workflow_entry.py mirror status`
