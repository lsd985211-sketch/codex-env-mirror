thread_id: 019ee348-662d-7fa0-99c8-3138aa86db2f
updated_at: 2026-07-12T13:51:08+00:00
rollout_path: /home/codexlab/.codex-app/sessions/2026/06/20/rollout-2026-06-20T12-27-13-019ee348-662d-7fa0-99c8-3138aa86db2f.jsonl
cwd: /mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager

# Primary Weixin Reply Format Diagnosis

Rollout context: Windows workspace `C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager`; the user asked through the mobile bridge to find why a primary-account reply initially lacked the required mobile result format and only became correct after a later message. The investigation was read-only; no files were modified.

## Task 1: Diagnose Initial Missing Mobile Markers

Outcome: success

Preference signals:

- The user explicitly corrected the earlier interpretation: the first reply was genuinely unformatted, and only a later resend followed the protocol. Future analysis must separate original-turn behavior from recovery behavior.
- The user expects mobile bridge protocol markers, supplement handling, and ownership state to be verified from evidence rather than inferred from the final visible reply.

Key steps:

- Ran `maintenance summary`; bridge/gateway/worker/app-server/CDP/database were generally reachable, with active primary/backup tasks and some historical reply backlog.
- Queried the read-only SQLite bridge database tables `mobile_tasks` and `mobile_events`.
- Verified the original primary task `9ed09e7c39bb` first ended with `recovery_protocol_violation_no_owned_result`: `ack_seen=false`, `begin_seen=false`, `end_seen=false`, `ownership.valid=false`, `result_complete=false`, `terminal_without_text=true`.
- Found the later complaint task `5d5fab93b4cb` triggered same-thread follow-up redelivery, after which the original task produced an owned result and was pushed successfully.
- Inspected `_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py` and confirmed the deliberate policy: primary visible-CDP tasks with missing owned results wait for a new same-thread message before redelivery.
- Confirmed `submission_confirmation_timeout` during visible-CDP submission, meaning the desktop submission was unverified; it was not proof that the prompt had been accepted.

Failures and how to do differently:

- The first conclusion conflated a later recovered/pushed response with success of the original turn. Always inspect the complete event chain from the first `codex_turn_started`.
- Marker stripping from the Weixin-facing text is expected, but it does not explain `begin_seen=false/end_seen=false` on the original turn.
- The systemic issue is the combination of unverified CDP submission and a recovery policy that parks the task under `wait_for_same_thread_followup_before_redelivery`, forcing the user to send another message before retry.

Reusable knowledge:

- Primary route is visible Codex Desktop CDP; backup accounts use app-server. Do not silently switch routes.
- Relevant recovery functions are `task_waits_for_followup_redelivery`, `mark_waiting_followup_redelivery`, `pending_task_can_trigger_waiting_followup_redelivery`, and the recovery logic around lines 27325-27364 of `mobile_openclaw_cli.py`.
- Suggested future fixes are to classify `submission_confirmation_timeout` as `unverified_submission`, perform a bounded controlled retry when no ack arrives, distinguish prompt-not-confirmed from model-protocol failure and polling failure, and add doctor/metrics signals such as `visible_cdp_unverified_submission_without_ack` and `primary_waiting_followup_redelivery_loop`.

References:

- Read-only commands used: `python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py maintenance summary`; `python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py protocol-violation-no-owned-result-check`; `python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mobile-execution-contract-check`.
- Database: `_bridge/mobile_openclaw_bridge/mobile_openclaw_bridge.db`, tables `mobile_tasks`, `mobile_events`.
- Representative tasks: original `9ed09e7c39bb`; complaint/follow-up `5d5fab93b4cb`; later successful primary `8095b0383ceb`; another repeated failure `229173008ac0`.
- Exact source policy text: `primary visible-CDP turns should not auto-redeliver on missing owned result ... We only retry after a new same-thread message arrives`.
