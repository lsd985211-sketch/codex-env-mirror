# Failed Or Avoid

Known unsafe or misleading routes. Avoid these unless deliberately retesting.

## Generic CloseButton

Do not use `automation_id=CloseButton` alone in Notepad.

Reason:
- Notepad uses `CloseButton` for both tab close and find/replace close.
- A generic selector clicked `关闭标签页` instead of `退出查找和替换`.

Safe route:
- Use exact UIA name `退出查找和替换` for closing the find/replace panel.

## In-App Open As Primary Route

Avoid using `文件 > 打开` as the primary route.

Reason:
- It is clickable but did not reliably surface a focusable file picker in the tested environment.

Safe route:
- Launch `notepad.exe <absolute_path>`.

## Save As As Primary Route

Avoid using `文件 > 另存为` unless you are explicitly validating that dialog behavior.

Reason:
- The dialog/filename field did not reliably become focusable.

Safe route:
- Create or choose a named file first, open it with Notepad, then use native `文件 > 保存`.

## Menu New Window As Primary Route

Avoid `文件 > 新建窗口` as the primary way to create blank windows.

Reason:
- It was clickable but top-level window count did not reliably change.

Safe route:
- Launch plain `notepad.exe`.

## Unverified Markdown Formatting

Avoid assuming these toolbar buttons changed file content:
- `斜体`
- `删除线`
- `标题`
- `列表`
- `表`
- `清除格式设置`

Reason:
- The buttons were clickable in the target window, but saved Markdown content did not change in tested cases.

Safe route:
- Only `加粗(Ctrl+B)` is currently disk-verified.

## Visual-Only View State

Avoid claiming success for `自动换行`, `状态栏`, or `缩放` based only on the click.

Reason:
- UIA did not expose stable toggle/percentage state in the tested environment.

Safe route:
- Promote only after a reliable state source is found.
