# Verified Success

Strictly verified workflows for Windows Notepad in this workspace. These may be used as primary automation routes when the same preconditions apply.

## Verification Standard

- Use a temporary or explicitly approved file.
- Verify the target Notepad window is foreground before typing or shortcuts.
- Prefer UIA names/AutomationIds over OCR and coordinates.
- Verify output by UIA/OCR when visual, clipboard readback when clipboard-related, and disk readback when persistence matters.

## Open File by Launch

Use `notepad.exe <absolute_path>` rather than `文件 > 打开`.

Steps:
1. Launch `notepad.exe` with the absolute file path.
2. Wait for a top-level Notepad title containing the file name.
3. Force foreground and verify expected document text through UIA/OCR.

Verified result: launching a named temporary file opened `launch-open.txt - Notepad`; UIA document text exposed the expected sentinel.

## Fresh Blank Window

Use plain `notepad.exe` for a new top-level blank window.

Steps:
1. Count current top-level Notepad windows.
2. Launch `notepad.exe`.
3. Verify the count increased and one title is `无标题 - Notepad` / `Untitled - Notepad`.
4. Bind to that window before typing.

Verified result: plain launch created a new top-level `无标题 - Notepad`.

## Safe Typing

Steps:
1. Start or bind Notepad by process/title.
2. Force the target foreground.
3. Click or focus the document editor.
4. Type in the same continuous tool action or bounded script.
5. Verify the target text by screenshot, OCR, or UIA.

Verified result: Notepad accepted Chinese text after foreground activation and document focus were performed continuously.

## Native Save

Use for already-named documents.

Steps:
1. Verify Notepad is foreground.
2. Click `文件` / `File`.
3. Click exact UIA menu item `保存` / `Save`.
4. Verify disk content when a path is known.

Verified result: named temporary files saved through `文件 > 保存`; disk readback matched expected text.

## Find

Steps:
1. Force the target Notepad foreground.
2. Click `编辑` / `Edit`.
3. Click exact UIA menu item `查找` / `Find`, or use `Ctrl+F` after foreground check.
4. Type the query with Unicode-safe input.
5. Verify search controls such as `搜索`, query text, `向下搜索`, and `向上搜索`.

Verified result: Chinese query text appeared in the search panel with direction controls.

## Close Find Replace Panel

Steps:
1. Do not target by `automation_id=CloseButton` alone.
2. Click exact UIA button name `退出查找和替换` / `Close find and replace`.
3. Verify `向下搜索` and `向上搜索` are no longer visible.

Verified result: exact-name close removed search direction controls. A generic `CloseButton` selector can hit tab close and must be avoided.

## Replace All

Use only when document modification is authorized.

Steps:
1. Work on a temporary copy or backed-up file.
2. Open Replace with `Ctrl+H`.
3. Verify UIA controls including `替换` and `全部替换`.
4. Fill find text, tab to replacement field, fill replacement text.
5. Click exact UIA button `全部替换` / `Replace All`.
6. Save through native Save and verify disk content.

Verified result: `replace_me` became `replaced_ok` and disk readback confirmed the replacement.

## Clipboard Editing

Steps:
1. Force foreground and focus document.
2. Use `Ctrl+A` for whole-document operations.
3. Verify `Ctrl+C` with clipboard readback.
4. Verify `Ctrl+X` with clipboard readback plus text disappearing from document UIA.
5. Verify `Ctrl+V` with document UIA showing restored text.
6. Save and disk-verify if persistence matters.

Verified result: copy/cut/paste behaved correctly and native Save persisted the final document.

## Undo Redo

Steps:
1. Make one identifiable edit.
2. Use `Ctrl+Z` and verify the marker disappears.
3. Use `Ctrl+Y` and verify the marker reappears.
4. Save and disk-verify if the redo state should persist.

Verified result: `UNDO_MARKER` disappeared after undo, reappeared after redo, and persisted after Save.

## Markdown Bold

Use only for Markdown-capable Notepad content where the target `.md` file can be disk-verified.

Steps:
1. Open a named `.md` file.
2. Focus editor and select target text.
3. Click exact toolbar button `加粗(Ctrl+B)` / `Bold`.
4. Save through native Save.
5. Read the Markdown file back and verify `**text**`.

Verified result: selecting `boldword` and clicking `加粗(Ctrl+B)` produced `**boldword**` on disk.

## Date Time Insertion

Steps:
1. Force foreground and focus the document editor.
2. Press `F5`.
3. Verify visible local time/date text.
4. Save and disk-verify when persistence is requested.

Verified result: `F5` inserted a local timestamp and native Save persisted it.

## New Tab

Use when the user wants a new blank tab in the current Notepad window.

Steps:
1. Force foreground.
2. Click UIA `automation_id=AddButton` or name `添加新标签页`.
3. Verify `无标题 - Notepad` / `无标题 - 记事本` or a tab named `无标题. 未修改。`.

Verified result: `AddButton` created a blank tab and the Notepad title changed to `无标题 - Notepad`.
