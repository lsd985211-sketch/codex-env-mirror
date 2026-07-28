---
name: next-ai-drawio-ops
description: Use the optional Next AI Draw.io MCP owner when a task requires editable Draw.io XML, multi-page diagrams, iterative diagram editing, or a verified Draw.io export. Do not use it for ordinary Mermaid diagrams or general browser automation.
---

# Next AI Draw.io Ops

## Route

1. Confirm that editable Draw.io output or the Next AI Draw.io preview is required. Otherwise use Mermaid or the existing visualization skill.
2. Resolve MCP priority through the capability matrix. This profile is session-bound, so start with the current native `next-ai-drawio` surface and continue forward through the normal MCP fallback chain after a real failure.
3. Keep one diagram session for related operations: `start_session`, create/load, coherent edits, readback, then export.
4. Validate the final XML or exported artifact. A successful tool call alone is not completion.

## Boundaries

- The resource layer owns the isolated npm package at `_bridge/runtime_dependencies/next-ai-drawio-mcp`.
- Do not use runtime `npx@latest` as the normal route.
- The MCP is optional and must remain nonblocking at startup.
- Do not use this owner as a generic browser, whitepaper renderer, or replacement for Mermaid.
- When edits are complex, batch logically related changes to reduce session churn.

## Diagnostics

- Protocol smoke: `python _bridge/mcp_session_doctor.py smoke --profile next-ai-drawio`
- Package launcher: `python _bridge/mcp_profile_launcher.py drawio`
- Expected core tools: `start_session`, `create_new_diagram`, `load_diagram`, `edit_diagram`, `get_diagram`, `export_diagram`.
