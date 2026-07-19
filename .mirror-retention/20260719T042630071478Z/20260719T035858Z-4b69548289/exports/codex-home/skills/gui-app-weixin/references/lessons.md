# Lessons

## Cause-Level Lessons

- Weixin window discovery can return correct process/window metadata while the
  first capture still shows the wrong surface. Activation and re-capture are a
  required safety gate, not a cosmetic step.
- Weixin's UIA coverage is insufficient for reliable control selection in the
  tested chat view. Screenshot/OCR-backed selectors are the practical route.
- Search and chat-row selection can be verified by header text and highlighted
  row state, not by UIA tree entries.
- GUI MCP `text_present` can be a false negative in Weixin chat views. If a
  `text_present` check times out but the screenshot shows the selected row and
  matching chat header, treat the UIA text check as unreliable and switch to
  screenshot/OCR verification instead of repeating the same click.
- Clearing the search box with `ctrl+a` and `BackSpace` can leave Weixin in an
  empty search-result state. Press `Escape` after clearing to return to the
  normal chat list before continuing a main-window flow.
- Draft input can be safely validated without communication by observing draft
  text and send-button color, then clearing the draft.
- Modal panels in the tested layout respond cleanly to `Escape`: emoji panel
  closes without insertion, and file picker closes without choosing a file.
- The Windows `选择文件` dialog is more reliable through UIA than raw
  coordinates: locate the `文件名(N):` edit field, enter an approved absolute
  path, then verify the Weixin pending file card after the picker closes.
- For cancel-only file picker tests, use the UIA `取消` button when available
  rather than OCR. In the verified dialog, `取消` was a `ButtonControl` with
  `automation_id=2`.
- File-picker completion can invalidate the picker session. Treat
  `bound window no longer exists` immediately after opening a file as a signal
  to return to Weixin and verify outcome, not as a reason to repeat selection.
- File sending needs two proof points: before send, the target chat, approved
  filename, plausible size, and green `发送` button are visible together; after
  send, the input area clears and the chat history or conversation preview
  shows the outgoing file.
- Unsupported hotkey names should be recorded as tool-layer failures and not
  retried without checking the GUI MCP key vocabulary.
- For approved text sending, a detached chat child window can be safer than the
  main window when the main right pane stays blank. Bind the child window by
  recipient title, paste via clipboard, and require recipient title, exact
  draft text, and green send button in one screenshot before clicking send.
- For approved file sending, the same detached-window rule applies. After
  entering a chat, the main Weixin window handle can stop being the actionable
  parent for attachment workflows; if a child window title equals the
  recipient, rebind that child window before opening the file picker.
- A high-level file-attach tool can safely cover the stable picker segment, but
  it must leave the irreversible `发送` click outside the tool. Verify the
  recipient title, pending file card, plausible size, and green send button in
  one fresh screenshot before sending.
- Treat a post-send file card with `上传中` as an intermediate state. Wait and
  capture again; only treat the send as stable when `上传中` disappears and the
  outgoing file card remains in the target chat history.

## Stable Visual Anchors In Tested Layout

- Search box: left panel top, near `x=150,y=56`.
- Search box in wider `1118x809` layout: left panel top, near `x=200,y=69`.
- Visible chat row for `随风而逝`: left panel, near `x=170,y=240` when it is
  the third row.
- Visible chat row in wider `1118x809` layout: left panel, near
  `x=180,y=300` when it is the selected third row.
- Message input area: lower right chat pane, near `x=560,y=540`.
- Message input area in wider `1118x809` layout: lower right chat pane, near
  `x=610,y=655`.
- Detached chat child window:
  - Title is the recipient name.
  - Input area is near the lower left/middle of the child window.
  - Send button is near the lower right of the child window.
  - The file/folder icon remains in the lower toolbar and can open the native
    `选择文件` dialog when the child window is the active parent.
- Emoji icon: lower toolbar, near `x=331,y=606`.
- File icon: lower toolbar, near `x=404,y=606`.
- Wider `1118x809` layout toolbar anchors: emoji near `x=422,y=758`, file
  icon near `x=512,y=758`.
- Send button state:
  - Gray means no draft to send.
  - Green means draft exists and must be cleared during non-send tests.
  - Green with a verified pending file card means the approved file can be
    sent only when the recipient and filename are visible in the same fresh
    screenshot.
