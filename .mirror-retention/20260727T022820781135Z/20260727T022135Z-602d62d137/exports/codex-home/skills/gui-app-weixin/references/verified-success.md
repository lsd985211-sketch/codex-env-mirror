# Verified Success

These workflows have been verified on Windows Weixin 4.1.9.62 in the current
workspace. Coordinates are window-relative and must be bound to a fresh
screenshot of the tested layout.

## Activate And Verify Main Window

- Scope: window-routing, stability.
- Preconditions: Weixin is running and `list_apps` exposes one or more windows
  titled `微信`.
- Steps:
  1. Select the larger `Weixin.exe` window from app/window discovery.
  2. Call `activate_window`.
  3. Rehydrate the window with `get_window`.
  4. Call `get_window_state` with screenshot.
- Verification:
  - Screenshot shows the Weixin side bar, search field, chat list, current chat
    title, and message input area.
- Evidence:
  - A first passive capture bound to the Weixin handle showed Codex content.
  - Activation plus re-capture produced the real Weixin main window.

## Select A Visible Chat Row

- Scope: app-specific, control-selection.
- Preconditions: main window is verified; target chat row is visible in the left
  conversation list.
- Steps:
  1. Click inside the target row's visible name/icon band.
  2. Wait briefly.
  3. Take a screenshot.
- Verification:
  - The target row is highlighted.
  - The chat title in the conversation header matches the target.
- Verified scenes:
  - `随风而逝`: row click near `x=170,y=240`; header switched to `随风而逝`.
  - In a wider `1118x809` layout, `target_chat` row click near `x=180,y=300`
    selected the chat. GUI MCP `text_present` verification timed out, but the
    screenshot showed the selected row and matching chat header. Use screenshot
    or OCR proof for this route.

## Search And Select A Contact

- Scope: app-specific, control-selection.
- Preconditions: main window is verified.
- Steps:
  1. Click the search box near `x=150,y=56`.
  2. Type the target contact name.
  3. Wait for the search result panel.
  4. Click the first contact result in the panel when it visibly matches.
  5. Take a screenshot.
- Verification:
  - Search panel appears with the contact under `联系人`.
  - After selection, the chat header matches the target and the search box is
    cleared.
- Verified scenes:
  - `随风而逝` searched and selected successfully.

## Search Box Focus, Type, Clear, And Exit Search State

- Scope: app-specific, control-selection, modal-recovery.
- Preconditions: main window is verified and no blocking modal is open.
- Steps:
  1. Click the search box in the left pane.
  2. Type the target chat/contact name.
  3. Take a screenshot to verify the search state/result panel.
  4. Clear the search text with `ctrl+a` and `backspace`.
  5. Press `Escape` to close the empty search panel and return to the normal
     chat list.
  6. Take a final screenshot.
- Verification:
  - Search state appears after typing.
  - Clearing text alone may leave an empty search panel open.
  - `Escape` closes the search panel and restores the normal chat list.
- Verified scenes:
  - In a wider `1118x809` layout, search box focus near `x=200,y=69`, typing
    `target_chat`, clearing, and `Escape` restored the normal chat state.

## Focus Input, Type Draft, And Clear Without Sending

- Scope: input-flow.
- Preconditions: target chat is selected and verified.
- Steps:
  1. Click the message input area near the lower center of the window.
  2. Type a clearly non-send marker such as `GUI_TEST_DO_NOT_SEND`.
  3. Take a screenshot.
  4. Press `Control_L+a`, then `BackSpace`.
  5. Take another screenshot.
- Verification:
  - Draft text appears before clearing and the send button turns green.
  - Draft text disappears after clearing and the send button returns gray.
  - No `Return` key or send button click is used.
- Verified scenes:
  - `微信ClawBot`: draft input and clear succeeded.
  - `随风而逝`: draft input and clear succeeded.
  - In a wider `1118x809` layout, `target_chat` draft input near
    `x=610,y=655` and cleanup with `ctrl+a` plus `backspace` succeeded. The
    before/after screenshots showed a green send button before cleanup and a
    gray send button after cleanup.

## Send Approved Text Through Detached Chat Window

- Scope: message sending.
- Preconditions:
  - The user has explicitly approved the recipient and exact text.
  - The target chat can be opened as a detached Weixin child window whose title
    equals the recipient.
  - The detached window shows the chat history, input box, and green send
    button after the draft is entered.
- Steps:
  1. From the verified main Weixin window, double-click the target chat row.
  2. Wait outside the GUI MCP action layer, then list windows and switch to the
     detached child window titled as the recipient.
  3. Put the approved text on the clipboard, click the detached window input
     box, and paste with `ctrl v`.
  4. Capture a screenshot before sending.
  5. Click `发送` only when the same screenshot shows the recipient title, exact
     draft text, and green send button.
  6. Capture a screenshot after sending.
- Verification:
  - The post-send screenshot shows the input box cleared.
  - The approved text appears as a right-side outgoing green message bubble in
    the target chat.
- Verified scenes:
  - Sent approved text to `随风而逝` after binding the detached child window
    titled `随风而逝`.

## Open And Close Emoji Panel

- Scope: app-specific, modal-recovery.
- Preconditions: main chat is verified and no blocking modal is open.
- Steps:
  1. Take a fresh screenshot.
  2. Click the smiley icon near `x=331,y=606`.
  3. Wait for the emoji panel.
  4. Press `Escape`.
  5. Take a screenshot.
- Verification:
  - Emoji panel opens with emoji grid and tabs.
  - `Escape` closes the panel.
  - No emoji is inserted into the input box.
- Verified scenes:
  - In a wider `1118x809` layout, smiley icon near `x=422,y=758` opened the
    emoji grid. `Escape` closed it and the input box remained empty.

## Open And Cancel File Picker

- Scope: app-specific, modal-recovery.
- Preconditions: main chat is verified and no blocking modal is open.
- Steps:
  1. Take a fresh screenshot.
  2. Click the folder/file icon near `x=404,y=606`.
  3. Wait for the Windows file picker titled `选择文件`.
  4. Press `Escape`.
  5. Take a screenshot of the Weixin window.
- Verification:
  - File picker opens.
  - `Escape` closes it.
  - No file is selected or uploaded.
- Verified scenes:
  - In a wider `1118x809` layout, folder icon near `x=512,y=758` opened the
    native file picker titled `选择文件`. The dialog exposed a UIA `取消`
    `ButtonControl` with `automation_id=2`; clicking it closed the picker and
    the Weixin main window returned with no pending attachment.

## Attach And Send An Approved File

- Scope: file transfer.
- Preconditions:
  - The user has explicitly approved the exact recipient and local file.
  - The target chat is selected and verified by header text and highlighted row.
  - The message input area is empty or contains only the selected file card.
- Steps:
  1. Activate and re-capture the Weixin main window.
  2. Verify the target chat title and selected conversation row.
  3. Click the bottom file/folder icon to open the Windows file picker.
  4. Bind the `选择文件` dialog and use UIA to locate the `文件名(N):`
     edit field when available.
  5. Enter the approved absolute file path and open it.
  6. Rebind or recover the Weixin main window after the picker closes.
  7. Verify the pending file card shows the approved filename and plausible
     size in the target chat input area.
  8. Click the green `发送` button only after the target chat, filename, and
     size are visible in the same fresh screenshot.
  9. Re-capture the chat and verify the file appears as an outgoing file bubble
     or in the conversation preview.
- Verification:
  - The input area clears after clicking `发送`.
  - The chat history or left conversation preview shows `[文件] <filename>`.
  - The outgoing file bubble shows the approved filename and size.
- Verified scenes:
  - Sent an approved WAV file to a verified contact; post-send screenshot
    showed the outgoing file bubble and `[文件]` conversation preview.

## Attach And Send An Approved File Through Detached Chat Window

- Scope: file transfer, window-routing.
- Preconditions:
  - The user has explicitly approved the exact recipient and local file.
  - The main Weixin window is verified and the target contact row is visible.
  - The right pane in the main window may be blank or unreliable after row
    selection.
- Steps:
  1. Click or double-click the target row from the verified main window.
  2. If Weixin opens or reveals a detached child chat, bind a fresh session by
     the child window title matching the recipient name. Do not continue using
     the old main-window session for file attachment.
  3. Verify the detached chat screenshot shows the recipient title, chat
     history, input area, file icon, and gray `发送` button.
  4. Use the high-level attach-file-by-path route or the native `选择文件`
     dialog to select the approved absolute file path. This step must not click
     `发送`.
  5. Re-capture the detached chat window and verify the pending file card,
     plausible file size, recipient title, and green `发送` button before
     sending.
  6. Click `发送` only after the above state is visible in the same fresh
     screenshot.
  7. Wait briefly and capture a final screenshot.
- Verification:
  - The outgoing file card appears in the chat history in the detached target
    window.
  - Any transient `上传中` text disappears before treating the transfer as
    stable.
  - The input area returns to an empty state or the send button returns gray.
- Verified scenes:
  - Sent an approved `m4a` audio copy with embedded cover to `target_contact`
    after binding the detached child window by recipient title. The first
    attempt to use the old main-window session failed with `bound window is no
    longer targetable`; rebinding the detached title fixed the route.
