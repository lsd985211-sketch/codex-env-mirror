---
name: agent-browser
description: ????? CLI ??????agent-browser, ???????
---

# Agent Browser Skill

Control the user's Chrome browser for tasks that depend on existing Chrome state: tabs, logged-in sessions, cookies, or extensions.

## Tool path

```
C:\Users\45543\Desktop\agent-browser-win32-x64.exe
```

## Key commands

| Task | Command |
|------|---------|
| Open URL | `<exe> open <url>` |
| Navigate | `<exe> navigate <url>` |
| Snapshot | `<exe> snapshot -i` |
| Click element | `<exe> click <selector>` or `<exe> click @ref` |
| Type text | `<exe> type <sel> <text>` |
| Fill field | `<exe> fill <sel> <text>` |
| Press key | `<exe> press <key>` |
| Screenshot | `<exe> screenshot` or `<exe> screenshot --full` |
| Get text | `<exe> get text <sel>` |
| Wait | `<exe> wait <ms>` |
| Find and click | `<exe> find role button click --name Submit` |
| Connect to Chrome | `--auto-connect` |
| Use profile | `--profile <name>` |
| Headed mode | `--headed` |
| JSON output | `--json` |

## Chaining

```
<exe> open <url> && <exe> snapshot -i
```