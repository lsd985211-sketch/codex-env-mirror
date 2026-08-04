# Weixin Desktop Harness

This local CLI-Anything harness wraps the Windows Weixin desktop application.
It is intentionally conservative: read-only status, activation, screenshots,
and draft operations are supported first. Sending a message is guarded by an
explicit confirmation flag.

The harness controls the real Windows Weixin GUI through window activation,
clipboard paste, hotkeys, screenshots, and optional pywinauto inspection. It is
not the mobile OpenClaw bridge and does not use bridge queues.

## Supported Surface

- Discover visible Weixin windows.
- Activate the best matching main window.
- Capture a screenshot of the active Weixin window.
- Paste a draft into the current input focus.
- Clear the current input field through Ctrl+A and Backspace.
- Send only with `--confirm-send SEND`.

## Boundaries

- No contact deletion, calls, payments, login automation, or settings changes.
- No send action without explicit `--confirm-send SEND`.
- No claim that a message was delivered unless the GUI state is verified.
- No private chat transcript extraction.
