# cli-anything-weixin

Local CLI-Anything harness for Windows Weixin desktop.

## Install

```powershell
python -m pip install -e C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager\_bridge\cli_anything_weixin\agent-harness
```

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

Sending is blocked unless `--confirm-send SEND` is provided.

`draft smoke` is the safe input-field test: it captures a before screenshot,
clicks the expected input area, pastes a marker, captures evidence, clears the
field, and captures the cleared state. It does not send.

`chat select-row` only switches the visible chat row. It does not send.

`chat search` writes into the Weixin search box and captures evidence. With
`--select-first`, it clicks the first visible search result.

`panel emoji-smoke` opens the emoji panel and closes it with Escape. It does not
insert an emoji.

`file picker-smoke` opens the file picker and cancels it. It does not select or
send a file.

`message prepare` leaves a verified draft visible. `message send-text` sends
only with `--confirm-send SEND`.
