---
name: mcp-builder
description: Guide for creating high-quality MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. Use when building MCP servers to integrate external APIs or services, whether in Python (FastMCP) or Node/TypeScript (MCP SDK).
license: Complete terms in LICENSE.txt
---

# MCP Server Development Guide

Use this skill to design, implement, review, and evaluate MCP servers. Keep this file as the router; load detailed references only for the phase and language currently needed.

## Operating Rules

- Design MCP tools around agent workflows, not raw API endpoint mirroring.
- Keep tool output concise by default, with explicit detailed modes when useful.
- Make errors actionable: include what failed, why it likely failed, and what the agent should try next.
- Prefer a small set of high-leverage tools over broad low-level API wrappers.
- Verify current MCP protocol and SDK behavior before making version-sensitive claims.
- Preserve existing `reference/` directory name; do not rename it to `references/` unless explicitly requested.
- Use bundled scripts for connection checks and evaluation scaffolding when applicable.
- For database MCPs, make the default writable target a dedicated scratch/work database. Production or business databases must be explicit profiles and should default to read-only unless a mature maintenance command owns the write path.
- For database MCPs, expose structured record/workflow write tools for complex inserts or upserts. Keep raw SQL execution as a short bounded escape hatch, because long SQL plus large parameter payloads can stress client tool dispatch and cancellation paths even when the database and MCP server are healthy.

## Workflow

1. Define target user workflows and select tools that complete those workflows.
2. Read current MCP protocol docs and the relevant local reference files.
3. Choose implementation stack: Python/FastMCP or Node/TypeScript MCP SDK.
4. Implement server infrastructure, auth/config handling, tool schemas, and response formatting.
5. Add tests or runnable smoke checks for tool behavior and error paths.
6. Create realistic agent-facing evaluation questions before calling the server finished.

## Reference Routing

| Task | Read |
|---|---|
| Agent-centric MCP design principles, tool quality checklist, protocol gotchas | `reference/mcp_best_practices.md` |
| Python / FastMCP server implementation | `reference/python_mcp_server.md` |
| Node / TypeScript MCP SDK implementation | `reference/node_mcp_server.md` |
| Evaluation design and scoring MCP server quality | `reference/evaluation.md` |

## Scripts

- `scripts/connections.py`: use for connection or transport-oriented checks when applicable.
- `scripts/evaluation.py`: use for evaluation workflow support.
- `scripts/example_evaluation.xml`: example evaluation artifact.
- `scripts/requirements.txt`: Python dependencies for bundled scripts.

## Validation

- Run the relevant server locally and exercise at least one success path and one failure path.
- Check `tools/list` output for clear names, descriptions, schemas, and discoverability.
- For write-capable database MCPs, test both `tools/list` and an actual `tools/call` write through the intended transport; a successful local database write alone is not enough.
- Confirm outputs are bounded, high-signal, and do not leak secrets.
- Use evaluation questions from `reference/evaluation.md` or project-specific equivalents for non-trivial servers.
- If modifying this skill, run `quick_validate.py` and verify all referenced local files exist.

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.
## Output Contract
- State the result, blockers, and any key tradeoffs.
- Keep it concise and actionable.
