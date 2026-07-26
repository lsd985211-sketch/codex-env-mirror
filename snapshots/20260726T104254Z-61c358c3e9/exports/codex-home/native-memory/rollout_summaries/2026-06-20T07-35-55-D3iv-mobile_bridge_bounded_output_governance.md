thread_id: 019ee3f5-27e9-7d20-9cf5-802aaef0e1af
updated_at: 2026-07-16T14:33:28+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/06/20/rollout-2026-06-20T15-35-55-019ee3f5-27e9-7d20-9cf5-802aaef0e1af.jsonl
cwd: /mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager

# Mobile Bridge And Output Governance

Rollout context: The work occurred in `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager` on Windows PowerShell. The rollout began with several Weixin mobile-bridge messages and later became a workflow-governance change after the user objected to oversized command output.

## Task 1: Weixin Bridge Message Handling

Outcome: partial

Preference signals:

- The user expected a request like sending `你好` to a named person to reach that person, not merely the current Weixin conversation. After the bridge sent to the wrong target, the user reported: "他并没有收到". Future handling should verify the stable recipient ID and actual account route before claiming delivery.
- Attachment handling was appropriately conservative: the MSI and MP3 were saved, metadata/hash checked, and neither was executed or played.

Key steps:

- Used `agent_bridge_receive` and `knowledge_get` checks before bridge actions.
- Read the mobile bridge skill and README to identify the reply command and safety rules.
- Verified task records with `mobile_openclaw_cli.py get` and routes with `thread-route list/get`.
- Found the bridge reply command: `python .\\_bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py reply <task_id> --text "..." --send`.
- Confirmed attachment tasks had files in the bridge attachment directory and computed SHA256 values without executing/playing them.

Failures and how to do differently:

- `delivery_accepted=true` only showed gateway acceptance; `phone_visible_confirmed=false` showed that phone-side visibility was not verified. The assistant initially sent `你好` to the current Weixin ID while the user intended another person. Recipient identity, sender account slot, and phone visibility must be treated as separate facts.
- A guessed SQLite table name `tasks` failed; the actual bridge schema uses `mobile_tasks`.
- `ffprobe` was absent from PATH; repository tooling was available, but only metadata/hash inspection was required.

Reusable knowledge:

- Reply routing should use the task's `receiver_account_id` when present. This rollout showed tasks using `backup2`; the bridge skill explicitly says not to fall back incorrectly.
- `primary.json` and `backup2.json` both identified the current Weixin user in this environment, while `backup1` identified another user. A display name such as `刘圣铎` cannot be safely mapped without a stable Weixin identifier.
- Attachment records are stored in `mobile_tasks.attachments_json`, with files under `_bridge\\mobile_openclaw_bridge\\attachments\\YYYYMMDD`.

References:

- `mobile_openclaw_cli.py reply <task_id> --text "reply text" --send`
- `mobile_openclaw_cli.py get <task_id>`
- `mobile_openclaw_cli.py thread-route list`
- Bridge DB tables: `mobile_tasks`, `mobile_events`, `mobile_runtime`, `mobile_users`

## Task 2: Global Command Output Governance

Outcome: success

Preference signals:

- The user said "输出很大" and requested that command output "只展示有价值部分" globally. This indicates a durable preference for concise, actionable, bounded command output.
- The user rejected making default and full output equivalent: "那样两者就没有区别了". Full output must remain a richer diagnostic view while still bounded; raw complete packages should be referenced through files/artifacts.

Key steps:

- Located existing shared output governance in `_bridge/bounded_output.py` and closeout projection in `_bridge/codex_workflow_entry.py`.
- Added distinct shared modes: `default_bounded`, `full_bounded`, and `failure_bounded`.
- Added closeout modes `closeout_default_bounded` and `closeout_full_bounded`; full output includes `section_index` and deeper bounded diagnostics.
- Preserved actionable failure fields globally, including `reason`, `next_action`, `safe_next_step`, and `manual_action`.
- Promoted critical closeout sections such as `decision_evidence`, `finalization`, and `post_closeout_mirror` so byte limits do not hide the outcome.
- Added remote verification fields to mirror publish summaries.
- Used routed backups before editing and completed closeout with post-closeout mirror publication.

Failures and how to do differently:

- The old `--full-output` path bypassed bounded projection and emitted a massive raw closeout package. Both default and full paths must use bounded projections; raw packages remain behind `record_path`/`raw_result_ref`.
- First bounded implementation dropped `safe_next_step`; the regression test caught it and the shared whitelist was corrected.
- First final closeout output compressed away publish details. Critical decision sections must be prioritized before generic truncation.
- Mirror refresh initially failed because source files changed during refresh. Freeze edits before the final publish attempt and inspect retry diagnostics instead of blind retries.

Reusable knowledge:

- Successful validation evidence: maintenance control-plane tests 37 passed; closeout package tests 10 passed; workflow finalization tests 7 passed; closeout signal tests 8 passed; workflow orchestrator 40/40 passed; rule governance and system membership validation passed; mirror validation passed.
- Final mirror state: snapshot `20260716T143104Z-bb0055bcf7`; local and remote `origin/main` both at `2cb691fa03f32f4e0adf8806defaf669f98a7f49`; source freshness and mirror validity were true; capability restore was ready. Existing full-state archive gaps remained for `cc-switch-database`, `codex-native-memory-state`, `codex-goal-state`, and `mail-and-scheduler-state`.

References:

- `_bridge/bounded_output.py`
- `_bridge/codex_workflow_entry.py`
- `_bridge/maintenance_control_plane_tests.py`
- `python _bridge\\maintenance_control_plane_tests.py`
- `python _bridge\\workflow_closeout_package_tests.py`
- `python _bridge\\workflow_finalization_tests.py`
- `python _bridge\\workflow_closeout_signals_tests.py`
- `python _bridge\\workflow_orchestrator.py validate`
- `python _bridge\\rule_governance.py validate`
- `python _bridge\\system_membership.py impact/validate`
- `python _bridge\\codex_workflow_entry.py mirror validate`
