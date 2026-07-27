# Candidate Unverified

Use these only for deliberate testing. Promote only after strict verification.

## Actual Message Sending

- Scope: input-flow, representational communication.
- Candidate steps:
  1. Select verified target chat.
  2. Type approved message.
  3. Confirm at action time unless the user has narrowly pre-approved the exact
     destination and content.
  4. Click send or press `Return`.
  5. Verify outgoing bubble content.
- Missing proof:
  - No deliberate send test has been run through GUI automation in this skill.
- Risk:
  - Sends content to another person or account.

## Attach And Send File

- Scope: file transfer.
- Status:
  - Promoted to `verified-success.md` after a strictly approved file-send test.
- Remaining candidate coverage:
  - Multiple-file selection.
  - Drag-and-drop attachment.
  - Sending files from non-standard locations such as network shares.
- Missing proof for remaining variants:
  - Only single-file selection through the Windows `选择文件` dialog has been
    verified.
- Risk:
  - Can transmit local files.

## Fast Attach File By Path Tool

- Scope: file-picker speed optimization.
- Candidate route:
  1. Start from a freshly verified Weixin main-window session and target chat.
  2. Call `gui_attach_file_by_path` with the approved absolute file path.
  3. Use coordinates only from the current screenshot layout; override
     `file_button_point`, `filename_point`, and `open_button_point` when the
     window size or layout differs from the tested scene.
  4. After the native picker closes, verify the pending file card, target chat,
     filename, and size before any send action.
- Current proof:
  - Tool registration, self-check, `tools/list`, UTF-8 JSON parsing, and
    missing-file error path were verified in the MCP layer.
- Missing proof:
  - Not yet live-verified against the Weixin main window after this tool was
    added. Do not promote to `verified-success.md` until a controlled attach
    test proves the file card appears in the intended chat.
- Risk:
  - May attach a local file to the current chat input if used with stale target
    chat evidence. The tool deliberately does not click `发送`.

## More Menu And Contact Details

- Scope: app-specific, contact management.
- Candidate steps:
  1. Open the top-right more menu.
  2. Inspect only after screenshot evidence.
- Missing proof:
  - Not tested.
- Risk:
  - May expose or change contact settings, privacy settings, chat deletion, or
    pin/mute state.

## Voice Or Video Call Buttons

- Scope: representational communication.
- Missing proof:
  - Not tested.
- Risk:
  - Starts communication with the contact.
