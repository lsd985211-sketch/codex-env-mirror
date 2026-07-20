thread_id: 019ee348-662d-7fa0-99c8-3138aa86db2f
updated_at: 2026-07-12T13:51:08+00:00
rollout_path: C:\Users\45543\.codex\sessions\2026\06\20\rollout-2026-06-20T12-27-13-019ee348-662d-7fa0-99c8-3138aa86db2f.jsonl
cwd: \\?\C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager

# Mobile bridge reply-format debugging and permission-boundary handling

Rollout context: The conversation was dominated by mobile-openclaw bridge traffic for Weixin-backed Codex tasks. The main durable issue was a first-turn failure to produce owned-result markers on a primary visible-CDP route, followed by recovery only after same-thread follow-up redelivery. The thread also included repeated backup1 low-risk questions that were answered or refused based on permission scope, plus one task explaining how project/global/mobile task rules are loaded.

## Task 1: Diagnose mobile bridge reply-format / recovery behavior

Outcome: partial

Preference signals:
- The user repeatedly sent strict mobile delegation envelopes with exact protocol fields like `ack_first`, `result_after_work_only`, `result_markers_only`, and required marker IDs, indicating they care about exact bridge ownership/format discipline and not just the final visible Weixin text.
- After a reply appeared wrong, the user clarified: “它一开始确实没有按格式生成回复，是后面信息重发才按照格式的” -> future agents should separate the first turn from later recovery, and not treat a later successful redelivery as proof the original turn was correct.
- Multiple backup1 messages asked for state or status-style information about primary work; the system had to refuse or redirect due to ordinary-user scope -> backup1 should be treated as low-risk Q&A only, not a route to inspect primary/local diagnostics.

Key steps:
- Read the mobile bridge skills and the reference file for routing/marker rules before doing deeper analysis.
- Queried the read-only SQLite bridge tables `mobile_tasks` and `mobile_events` for the relevant task IDs and event chains.
- Inspected `mobile_openclaw_cli.py` around the follow-up redelivery logic and visible-CDP handling.
- Confirmed that the bridge strips protocol markers before sending to Weixin, so Weixin-visible text alone is not evidence that markers were absent.

Failures and how to do differently:
- The initial interpretation that the bridge reply was simply “formatted correctly” was incomplete. The deeper evidence showed that the original primary turn had `ack_seen=false`, `begin_seen=false`, `end_seen=false`, `result_complete=false`, and `ownership.valid=false` before later recovery.
- The later success was due to same-thread follow-up redelivery, not the original turn. Future agents should avoid compressing those into a single success signal.
- For primary visible-CDP tasks, the code intentionally waits for same-thread follow-up after `protocol_violation_no_owned_result`; do not expect immediate auto-retry.

Reusable knowledge:
- `task_waits_for_followup_redelivery()` is true for `codex-cdp` + `primary`, so a primary visible-CDP failure can intentionally park and wait for a same-thread continuation instead of immediate redelivery.
- `visible_cdp_no_owned_result_manual_after_seconds()` bounds that waiting behavior; the code prefers a follow-up-aware retry model over immediate retype.
- `reply_to_weixin()` treats transport/business acceptance separately from phone-visible confirmation; phone visibility may remain false even when delivery is accepted.
- Successful mobile protocol turns include the owned-result markers `[[mobile_ack:...]]`, `[[mobile_result_begin:...]]`, and `[[mobile_result_end:...]]`; Weixin display may remove them after parsing.

References:
- `mobile_openclaw_cli.py` lines around `24409-24423`: primary visible-CDP turns wait for follow-up redelivery.
- `mobile_openclaw_cli.py` lines around `27325-27364`: `protocol_violation_no_owned_result` on a waiting task leads to `mark_waiting_followup_redelivery(...)` and cooldown instead of immediate retry.
- `mobile_openclaw_cli.py` lines around `27194-27214`: when a recovered `new_text` exists, the task is completed and pushed.
- SQLite evidence for `9ed09e7c39bb`: first failure event chain showed `recovery_protocol_violation_no_owned_result`, then `active_waiting_followup_redelivery_triggered`, then later `owned_result_recovered` after the same-thread follow-up.
- SQLite evidence for `b9760c6855a0`: later backup1 explanation task that summarized the first-turn failure and follow-up recovery.

## Task 2: Explain how project/global/mobile rules are loaded into the session

Outcome: success

Preference signals:
- The user asked, “项目准则和全局准则是怎么在会话中加载的” -> they want a layered, concrete explanation of rule loading rather than a vague summary.
- The task was asked from `backup1`, but the content stayed conceptual; that suggests backup1 can still receive high-level explanations, while local state inspection remains restricted.

Key steps:
- Answered with a layered explanation: system/developer globals, Codex workspace rules, project `AGENTS.md`, mobile bridge task protocol, and skills/memory.
- Emphasized that task-specific mobile protocol envelopes are temporary and sit on top of the broader session rules.

Failures and how to do differently:
- None significant; the explanation matched the user’s question and did not require correction.

Reusable knowledge:
- Rule loading is layered, with higher-priority system/developer rules overriding project-level instructions and task-level mobile protocol envelopes.
- Project rules are injected from the workspace, not remembered ad hoc; mobile delegation adds an extra temporary protocol layer for the current task.

References:
- Response given in the rollout: the explanation listed system/developer globals, Codex workspace rules, project rules, mobile task protocol, and the priority ordering.

