---
name: gui-app-weixin
description: Windows Weixin GUI automation guidance. Use when Codex needs to inspect or operate Weixin/WeChat on Windows, select chats, search contacts, focus the message input, test drafts without sending, open/close emoji or file picker panels, or avoid unsafe send/delete/contact-modification actions during GUI automation.
---

# Weixin GUI Automation

Use this skill for Windows Weixin (`Weixin.exe`) GUI work. Keep all message-sending,
file-transfer, deletion, call, and contact-modification actions behind explicit
user confirmation.

## Entry Conditions

- Process: `C:\Program Files\Tencent\Weixin\Weixin.exe`
- Main window title: `微信`
- Expected main window size in the tested layout: about `882x641` logical pixels.
- Also verified on a wider layout around `1118x809` logical pixels. Treat
  coordinates as layout-bound and refresh screenshots before reuse.
- Weixin may expose multiple windows with the same title. Prefer the larger main
  chat window after screenshot verification.

## Operating Rules

1. Activate the selected Weixin window before the first meaningful screenshot.
2. Rehydrate the window object and take a fresh screenshot before coordinate input.
3. Do not trust UI Automation text for Weixin chat controls; it may expose only
   the top-level window.
4. Use screenshot evidence or OCR for Weixin's self-drawn controls.
5. Never send a message as part of a "draft" test. Verify by button color/state,
   then clear with `Control_L+a` and `BackSpace`.
6. Do not use stale coordinates after a rejected action such as "call
   get_window_state before issuing coordinate input"; refresh first.
7. Within a bounded, already verified Weixin workflow, reuse the verified main
   chat window session for repeated actions instead of re-running window
   discovery before every click.
8. Treat file pickers, popups, and temporary Weixin child windows as disposable
   sessions. Bind them only for the local task, then return to the main Weixin
   session and verify the chat state.
9. If the main session shows unexpected focus, stale screenshot content,
   missing target chat, failed verification, or a changed/minimized window,
   abandon reuse and call recover/rebind before continuing.
10. Do not use Weixin main-window `text_present` verification as a sole success
    condition. In tested chat views it can time out even when screenshots show
    the correct chat state.
11. For Windows `选择文件` dialogs opened by Weixin, prefer UIA controls such as
    the `取消` button or `文件名(N):` field over OCR or raw coordinates.

## Route Map

- Verified safe workflows: read `references/verified-success.md`.
- Plausible but not fully verified workflows: read
  `references/candidate-unverified.md`.
- Failed or unsafe routes: read `references/failed-or-avoid.md`.
- Cause-level lessons and selectors: read `references/lessons.md`.

For unknown Weixin states, start with window activation and screenshot
verification from `verified-success.md`, then choose the smallest matching
workflow.

## Preflight

- Confirm the window is the actual Weixin main chat window before acting.
- Prefer screenshot/OCR evidence over UIA text for self-drawn controls.
- Treat send, delete, call, and contact changes as explicit-user-only actions.

## Output Contract

- Return the verified chat/window state and the exact workflow used.
- Mention whether the result is verified-success, candidate, or failed/avoid.
- If a send or file action was blocked, say which boundary stopped it.
