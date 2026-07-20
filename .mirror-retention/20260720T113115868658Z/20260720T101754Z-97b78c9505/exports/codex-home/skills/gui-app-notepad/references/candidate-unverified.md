# Candidate Unverified

Workflows that may be useful but are not yet strict enough to use as primary automation routes. Retest with fresh evidence before promotion.

## File Menu Open

Observed:
- `文件 > 打开` menu item is discoverable and clickable by UIA text.

Missing proof:
- The file picker did not reliably become foreground.
- The requested file did not reliably open.

Promotion requirement:
- Verify a stable file dialog foreground.
- Fill exact path.
- Verify final Notepad title and document text.

## Save As

Observed:
- `文件 > 另存为` menu item is discoverable and clickable by UIA text.

Missing proof:
- The filename/path field did not reliably become focusable.
- Target file creation did not verify.

Promotion requirement:
- Verify Save As dialog foreground and filename field focus.
- Save to a temporary path.
- Verify file exists and content matches.

## File Menu New Window

Observed:
- `文件 > 新建窗口` menu item is discoverable and clickable by UIA text.

Missing proof:
- Top-level Notepad window count did not reliably increase.

Promotion requirement:
- Test from a fresh Notepad state.
- Verify window count and new `无标题 - Notepad` title.

## New Markdown Tab

Observed:
- `文件 > “新建 Markdown”选项卡` can be clicked.
- It adds another `无标题. 未修改。` tab.

Missing proof:
- UIA did not distinguish the new tab as Markdown.
- No disk-verified Markdown-mode output was produced from this route.

Promotion requirement:
- Verify Markdown-specific tab identity or disk behavior unique to Markdown mode.

## View Menu Toggles

Observed:
- `查看 > 自动换行`, `状态栏`, and `缩放` menu items are clickable.

Missing proof:
- UIA did not expose stable toggle state or zoom percentage.

Promotion requirement:
- Read a reliable post-action state from UIA/OCR or a known settings source.

## Markdown Toolbar Buttons

Observed:
- `斜体`, `删除线`, `标题`, `列表`, `表`, and `清除格式设置` toolbar buttons are clickable inside the target Notepad window.

Missing proof:
- Saved file content did not change in tested cases.

Promotion requirement:
- Produce disk-verified Markdown output for each button.
