---
name: cli-anything-weixin
description: Use when Codex needs to operate the Windows Weixin desktop app through a local CLI harness: list/activate windows, capture screenshots, paste or clear drafts, and guarded send-current operations.
---

# CLI-Anything Weixin

Use `cli-anything-weixin` for Windows Weixin desktop GUI operations. This is a
local CLI harness for the real desktop app, not the mobile OpenClaw bridge.

## Commands

```powershell
cli-anything-weixin --json status
cli-anything-weixin --json activate
cli-anything-weixin --json screenshot
cli-anything-weixin --json chat select-row --index 3
cli-anything-weixin --json chat search "联系人或会话名"
cli-anything-weixin --json chat search "联系人或会话名" --select-first
cli-anything-weixin --json chat clear-search
cli-anything-weixin --json panel emoji-smoke --confirm-smoke PANEL
cli-anything-weixin --json file picker-smoke --confirm-smoke PICKER
cli-anything-weixin --json draft focus-input
cli-anything-weixin --json draft paste "draft text"
cli-anything-weixin --json draft clear
cli-anything-weixin --json draft smoke "CLI_WEIXIN_DRAFT_TEST_DO_NOT_SEND" --confirm-smoke DRAFT
cli-anything-weixin --json draft send-current --confirm-send SEND
cli-anything-weixin --json message prepare "draft text" --confirm-prepare DRAFT
cli-anything-weixin --json message send-text "approved text" --confirm-send SEND
```

## Safety

- `status`, `activate`, and `screenshot` are read-only or local window actions.
- `chat select-row` switches a visible chat row by approximate geometry and
  captures before/after evidence; it does not send.
- `chat search` uses the Weixin search box and captures evidence. Selecting the
  first result changes the active chat but does not send.
- `panel emoji-smoke` and `file picker-smoke` open and close safe panels without
  inserting emoji or selecting files.
- `draft focus-input`, `draft paste`, and `draft clear` affect the current input
  field but do not send.
- `draft smoke` requires `--confirm-smoke DRAFT`, creates before/paste/clear
  screenshots, and clears the marker before returning.
- `send-current` requires `--confirm-send SEND`.
- `message prepare` requires `--confirm-prepare DRAFT` and leaves the draft
  visible for verification.
- `message send-text` requires `--confirm-send SEND` and should only be used
  after the recipient/current chat is verified.
- Do not use this skill for contact deletion, calls, payments, login automation,
  account settings, or extracting private chat transcripts.

## Verification

Prefer `--json` output and screenshot read-back. If a command cannot find the
Weixin window, do not infer success from process status alone.
