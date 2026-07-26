# Failed Or Avoid

## Do Not Trust A Weixin Window Handle Before Activation

- Failed scene:
  - A window discovered as `Weixin.exe` with title `微信` returned a screenshot
    containing Codex Desktop content before explicit activation and re-capture.
- Cause:
  - Window identity and capture target can be stale or visually mismatched in
    multi-window/occluded desktop state.
- Risk:
  - Coordinate actions could target the wrong visible surface.
- Safer route:
  - Activate the Weixin window, rehydrate the handle, capture a fresh screenshot,
    and verify Weixin-specific UI before any input.

## Do Not Depend On UI Automation Text For Weixin Chat Controls

- Failed scene:
  - The accessibility tree exposed only the top-level `微信` window and did not
    surface search box, chat list, send button, or message input text.
  - A GUI MCP `text_present` verification for a selected chat timed out even
    though the screenshot showed the selected row and matching chat header.
- Cause:
  - Weixin uses self-drawn/Chromium-style surfaces where UIA may be incomplete.
  - The GUI tool's text verification may rely on UIA-style text surfaces that
    do not reflect Weixin's visual chat content.
- Risk:
  - False negatives or missing controls.
  - Repeating a successful coordinate click because of a false negative can
    waste time or disturb the current selection.
- Safer route:
  - Use UIA as a probe only, then fall back to screenshot/OCR and verified
    window-relative coordinates.
  - If screenshot evidence proves the state, record the UIA text check as a
    verification failure and continue through visual/OCR verification.

## Do Not Treat Search Text Cleanup As Search-State Cleanup

- Failed or misleading scene:
  - After typing a search term, `ctrl+a` plus `backspace` cleared the search box
    but left an empty search-result panel open.
- Cause:
  - Weixin keeps the search UI active even when the query text is blank.
- Risk:
  - Subsequent clicks may target the search result panel instead of the normal
    chat list.
  - A fixed flow can continue from the wrong UI state if it only checks that the
    search text is empty.
- Safer route:
  - Press `Escape` after clearing search text, then take a screenshot to verify
    the normal chat list has returned.

## Do Not Send During Draft Tests

- Failed or unsafe pattern:
  - Pressing `Return` or clicking the green send button while validating text
    entry.
- Cause:
  - Draft validation does not require external communication.
- Risk:
  - Unintended message transmission.
- Safer route:
  - Verify green send-button state from screenshot, then clear with
    `Control_L+a` and `BackSpace`.

## Do Not Reuse A File Picker Session After Handle Rebuild

- Failed scene:
  - After entering an absolute file path and opening it, the `选择文件` dialog
    changed or closed while the old session still existed in the automation
    flow. A subsequent action against the old session reported
    `bound window no longer exists`.
- Cause:
  - Windows file dialogs can rebuild their window handle during navigation,
    validation, or completion.
- Risk:
  - Retrying against the stale session can misclassify success as failure or
    send input to the wrong window.
- Safer route:
  - After opening a file path, refresh the window list. If the picker is gone,
    recover the Weixin main window and verify the pending file card. If a new
    picker exists, rebind by title/process before continuing.

## Do Not Assume Unsupported Hotkey Names

- Failed scene:
  - Sending `pagedown` through the GUI MCP hotkey action returned
    `Unknown code: PAGEDOWN`.
- Cause:
  - The MCP hotkey layer accepts a bounded key-name vocabulary.
- Risk:
  - A failed hotkey adds noise to session failure counters and can obscure the
    real GUI state.
- Safer route:
  - Prefer supported keys already verified in the current tool layer, or use
    scroll/click actions with fresh screenshot evidence.

## Do Not Send From A Blank Main-Window Chat Pane

- Failed scene:
  - The main Weixin window highlighted the target row, but the right chat pane
    stayed as a blank placeholder without the chat title, input box, or draft.
- Cause:
  - Weixin can open or render the chat in a detached child window while the main
    window's right pane remains blank or stale.
- Risk:
  - Coordinates aimed at the main window can miss the real input box or send
    button, and draft text may not be visible for verification.
- Safer route:
  - List Weixin child windows, switch to the detached window whose title equals
    the target recipient, then require title + draft + send button evidence in
    that child window before sending.
