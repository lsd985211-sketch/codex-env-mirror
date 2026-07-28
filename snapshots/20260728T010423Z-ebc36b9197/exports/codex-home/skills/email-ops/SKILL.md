---
name: email-ops
description: Resident email system operations for this Windows Codex workspace. Use when the user asks to send, schedule, inspect, retry, diagnose, or repair email; mentions SMTP, mail identities, outbox, draft box, dead letter, email task table, generated email body, scheduled reports, or email delivery failures.
---

# Email Ops

Use this skill for the local email scheduler and delivery pipeline. Keep the work on the module's own CLI instead of editing resource files directly.

## Core Model

- Treat `_bridge\shared\email_scheduler.py` as the authority for mail state, intent creation, queueing, delivery, and maintenance.
- Treat `_bridge\shared\system_maintenance_cli.py email ...` as the system maintenance facade when a broader maintenance workflow needs email status.
- The email task table is an input source, not the runtime queue. Due sending must flow through schedule runs, content jobs, outbox items, and delivery jobs.
- Do not bypass outbox ordering by scanning the task table and sending directly.
- Do not fabricate sender SMTP fields. If an account has no SMTP config, keep the identity fields empty and block delivery until a valid sender is available.
- Inbox support is read-only by default. IMAP receive must flow through `inbox-validate`, `inbox-fetch`, and `inbox-snapshot`; do not delete, mark read, or execute inbound mail content by default.
- Attachment support is part of the scheduler path. Attachment tasks must preserve attachment paths through outbox and delivery jobs, validate file existence and size before sending, and record attachments in send records.
- Inbound processing uses queue signaling, not direct execution. A subject containing `待处理` may create an inbox job; the job should be ordered by inbox arrival order and processed under worker resource budgets.
- Inbound replies must become normal outbound mail work. The inbox worker creates `mail_kind=reply` tasks with reply metadata; actual sending still flows through content jobs, outbox, delivery jobs, and SMTP receipts.
- Completed immediate/single-use mail tasks should leave the active task table automatically. The dispatcher should archive completed one-time tasks both before scheduling and after a worker run, so a task sent in the current tick is not left active until the next poll.

## Normal Send Flow

1. Parse the user's request into recipient, sender, scheduled time, subject, and content requirement.
2. For clear ordinary mail requests, use the convenience submitter:
   `python _bridge\shared\email_scheduler.py intent-submit --to <recipient> --content <content> --time <time> [--sender <sender>] [--subject <subject>]`
3. `intent-submit` should create only when the module classifies the request as environment-auto or Codex-deferred. Missing fields, ambiguous recipients, invalid sender delivery, attachment-only, and unknown modes must stay review-required with no task-table write.
4. Use preview-only mode when the user asks to inspect, when the request is ambiguous, or when you need to explain why it will not be created:
   `python _bridge\shared\email_scheduler.py intent-dry-run --to <recipient> --content <content> --time <time> [--sender <sender>] [--subject <subject>]`
5. Use low-level creation only when explicitly needed for controlled maintenance or compatibility:
   `python _bridge\shared\email_scheduler.py intent-create --to <recipient> --content <content> --time <time> [--sender <sender>] [--subject <subject>]`
6. For immediate verification, use `validate`, `smoke-test`, `peek-outbox`, or `inspect-run`. Avoid full doctor unless there is a real fault.

## Generated Body Rules

- If Codex must generate the email body, use the module's sealed task package path. The body generator must only use task JSON, allowed local tool output, and allowed memory; it must not borrow facts from the current chat unless they are explicitly in the task package.
- Realtime/research mail remains `content_mode=codex`. Do not silently downgrade it to static mail just to recover a failure. The generation Codex owns live evidence gathering under the task's MCP profile and sealed request package; human static fallback is only an explicit manual override or one-off emergency resend.
- Generation output must be structured first: subject, body_text, used_evidence_ids, assumptions, missing_fields, and should_send.
- If `assumptions` or `missing_fields` is non-empty, do not send. Move or mirror the item into draft handling so a human can fix it.
- Human-readable email text is still the body sent to recipients; structured JSON is an internal gate, not the recipient-facing format.
- Static mail uses explicit templates: immediate sends should normalize to `immediate_static_send`, delayed/future sends to `scheduled_static_send`, and generic fixed notices to `fixed_notice`.

## Outbox, Draft, And Failure Rules

- Outbox order should be precomputed and persisted. At send time, the sender only takes the front eligible item and performs minimum safety checks.
- Minimum send checks: ready status, scheduled time reached, not expired, no sent receipt, resolvable recipient, available sender SMTP, and no queue-disqualification reason.
- Items that cannot become sendable without human correction belong in draft handling, not active outbox. This includes generation assumptions, missing fields, invalid recipient identity, missing sender SMTP, and dead-letter items that need manual data or config fixes.
- Retryable transport problems can stay in the retry/dead-letter chain until classified as requiring human correction.
- Dead-letter items must not remain silently eligible in the outbox. The outbox maintainer should move dead-letter items that require human correction into draft handling.
- If a later successful resend covers the same sender, subject, and all intended recipients of an older failed/draft/dead-letter run, archive the older run and its draft/dead-letter state as superseded. Keep SMTP receipts and audit JSON; remove it only from active draft/dead-letter/actionable queues.
- Smoke/validation commands may create temporary state. They must clean only their own smoke IDs or stale smoke artifacts, and maintenance metrics/doctor should ignore `smoke-*` stage artifacts unless a smoke test explicitly opts into them. Parallel validation must not corrupt another smoke run or produce false doctor failures.
- Draft resend must use `resend-draft`, not ad hoc SMTP. If time is omitted or immediate (`立即`/`即刻`/`现在`), convert the draft's existing subject/body/sender/recipients into an immediate outbox item without regenerating the body. If a future or delayed time is specified, update the mail task table as a static scheduled task, preserving the source task name by default so the task remains normal scheduler work.
- After building a draft resend task row, validate it by re-parsing the row through the scheduler runtime: scheduled time must parse, content_mode must be `static`, template must be immediate/scheduled static, sender and recipients must resolve, and the static body must match the draft. Block the resend if this round-trip validation fails.

## Diagnostics

Use the lightest command that answers the question:

```powershell
python _bridge\shared\email_scheduler.py snapshot
python _bridge\shared\email_scheduler.py inbox-snapshot
python _bridge\shared\email_scheduler.py inbox-validate
python _bridge\shared\email_scheduler.py inbox-fetch --account 3633922805@qq.com --limit 10
python _bridge\shared\email_scheduler.py inbox-poll --account 3633922805@qq.com --limit 10
python _bridge\shared\email_scheduler.py inbox-refresh-index
python _bridge\shared\email_scheduler.py peek-inbox
python _bridge\shared\email_scheduler.py validate
python _bridge\shared\email_scheduler.py smoke-test
python _bridge\shared\email_scheduler.py metrics
python _bridge\shared\email_scheduler.py peek-outbox
python _bridge\shared\email_scheduler.py doctor
python _bridge\shared\email_scheduler.py repair-plan
python _bridge\shared\email_scheduler.py inspect-run <run-id-or-task-name>
```

Use `doctor` after a visible failure or suspicious state. Use `repair-plan` before any repair. Do not apply repair, reset runs, archive runs, or retry delivery unless the user explicitly asked for that action or the current approved task requires it.

## Performance Rules

- For ordinary mail requests, prefer `intent-submit -> validate/smoke-test`; use `intent-dry-run` for preview or ambiguity, and `intent-create` only for explicit low-level creation.
- Let the environment create complete static mail tasks and complete Codex-deferred generation tasks. Codex should only handle analysis, research, generation, ambiguity, review-required cases, or failures.
- Do not update docs, run full maintenance, or perform broad postmortems for routine mail creation.
- Run backups, doctor, repair-plan, and iteration only when changing code/config, SMTP, task structure, queue semantics, or after a delivery failure.
- Prefer batched/resident module commands over repeated heavy Codex executions when the module already provides a deterministic path.
- On Windows, concurrent maintenance commands can overlap on JSON state writes. Shared JSON writers should use unique temporary paths plus bounded replace retries; never use a single fixed `.tmp` path for files that may be refreshed by validate/doctor/metrics in parallel.

## Maintenance Contract

Before and after nontrivial email-system changes, verify the maintenance contract exists and still works:

```powershell
python _bridge\shared\email_scheduler.py snapshot
python _bridge\shared\email_scheduler.py doctor
python _bridge\shared\email_scheduler.py repair-plan
python _bridge\shared\email_scheduler.py validate
python _bridge\shared\email_scheduler.py metrics
```

For system-level work, also run the controlled iteration gate through the bridge maintenance layer before final reporting.

## Safety Boundaries

- Never expose SMTP passwords, tokens, app passwords, or private mailbox secrets in chat or memory.
- Never send mail directly from an ad hoc script when the scheduler can represent the task.
- Never treat a draft, failed item, or dead-letter item as sent without a durable receipt.
- Never guess recipients from partial names if identity resolution is ambiguous.
- Use UTF-8 for Chinese subjects, bodies, task files, JSON, and logs.

## Output Contract

When reporting back, state: created/scheduled/sent/drafted/blocked, recipient, sender, scheduled time, subject or content summary, validation result, and any remaining action needed.
