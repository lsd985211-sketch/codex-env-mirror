---
name: agent-browser
description: "Execution skill for controlling the user's Chrome browser when the task depends on existing browser state such as cookies, logged-in sessions, tabs, or extensions via the agent-browser CLI. Use this instead of generic browser automation when preserving the user's live Chrome context matters."
---

# Agent Browser Skill

## Role Boundaries

- Use this skill when the work depends on the user's current Chrome state.
- Use it for tab/session/cookie/extension-aware browser actions.
- Do not use it as the default browser automation path when a clean terminal-driven browser is enough.
- Do not use it for native desktop GUI automation outside Chrome.

## Handoff Rules

- **Existing Chrome state matters**: stay in this skill.
- **Clean browser automation from the terminal is enough**: hand off to `playwright`.
- **Local webapp testing workflow with helper-managed servers**: hand off to `webapp-testing`.
- **Native desktop windows, file pickers, or non-Chrome GUI surfaces**: hand off to `gui-automation`.

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

## Preflight

- Confirm the task depends on current Chrome state before using this skill.
- Prefer it only when sessions, cookies, tabs, or extensions must be preserved.
- If a clean browser route is enough, hand off to the browser automation skill instead.

## Output Contract

- Return the browser state used and the result reached.
- Mention whether Chrome state preservation was the reason for choosing this skill.
- If the route fails, say whether the failure was state-related or selector-related.
