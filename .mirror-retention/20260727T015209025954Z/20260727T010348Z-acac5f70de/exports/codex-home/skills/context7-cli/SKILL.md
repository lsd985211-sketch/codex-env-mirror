---
name: context7-cli
description: Context7 ctx7 CLI routing and execution guide for fetching current software-library documentation, managing Context7-backed skills, and configuring Context7 MCP. Use when the task is specifically about ctx7/context7 operations, software dependency documentation retrieval through Context7, skill installation/search/removal via ctx7, or Context7 MCP setup. Do not use for generic webpage retrieval or non-Context7 web research.
---

# ctx7 CLI

## Role Boundaries

- Use this skill when the task is explicitly about the Context7 CLI surface: docs lookup through ctx7, Context7 skill management, or Context7 MCP setup.
- Use it as the routing and execution guide for ctx7-specific commands.
- Do not use it as a generic web-research or webpage-capture skill.
- Do not keep ownership of generic library-doc lookup when a narrower `find-docs` path is enough.

## Handoff Rules

- **Current library/API/SDK docs lookup only**: prefer `find-docs`.
- **Context7 CLI usage, login, installation, skill management, or MCP setup**: stay in this skill.
- **Generic webpage/docs-site crawling or URL capture**: hand off to `firecrawl-cli`.
- **Platform/source selection across the internet**: hand off to `agent-reach`.

The Context7 CLI does three things: fetches up-to-date library documentation, manages AI coding skills, and sets up Context7 MCP for your editor.

Make sure the CLI is up to date before running commands:

```bash
npm install -g ctx7@latest
```

Or run directly without installing:

```bash
npx ctx7@latest <command>
```

## What this skill covers

- **[Documentation](references/docs.md)** — Fetch current docs for any library. Use when writing code, verifying API signatures, or when training data may be outdated.
- **[Skills management](references/skills.md)** — Install, search, suggest, list, remove, and generate AI coding skills.
- **[Setup](references/setup.md)** — Configure Context7 MCP for Claude Code / Cursor / OpenCode.

## When to Load References

- **Library documentation through ctx7 CLI** -> `references/docs.md`
- **Context7 skill install/search/remove/generate flows** -> `references/skills.md`
- **Context7 MCP setup/login/configuration** -> `references/setup.md`

## Quick Reference

```bash
# Documentation
ctx7 library <name> <query>           # Step 1: resolve library ID
ctx7 docs <libraryId> <query>         # Step 2: fetch docs

# Skills
ctx7 skills install /owner/repo       # Install from a repo (interactive)
ctx7 skills install /owner/repo name  # Install a specific skill
ctx7 skills search <keywords>         # Search the registry
ctx7 skills suggest                   # Auto-suggest based on project deps
ctx7 skills list                      # List installed skills
ctx7 skills remove <name>             # Uninstall a skill
ctx7 skills generate                  # Generate a custom skill with AI (requires login)

# Setup
ctx7 setup                            # Configure Context7 MCP (interactive)
ctx7 login                            # Log in for higher rate limits + skill generation
ctx7 whoami                           # Check current login status
```

## Authentication

```bash
ctx7 login               # Opens browser for OAuth
ctx7 login --no-browser  # Prints URL instead of opening browser
ctx7 logout              # Clear stored tokens
ctx7 whoami              # Show current login status (name + email)
```

Most commands work without login. Exceptions: `skills generate` always requires it; `ctx7 setup` requires it unless `--api-key` or `--oauth` is passed. Login also unlocks higher rate limits on docs commands.

Set an API key via environment variable to skip interactive login entirely:

```bash
export CONTEXT7_API_KEY=your_key
```

## Common Mistakes

- Library IDs require a `/` prefix — `/facebook/react` not `facebook/react`
- Always run `ctx7 library` first — `ctx7 docs react "hooks"` will fail without a valid ID
- Repository format for skills is `/owner/repo` — e.g., `ctx7 skills install /anthropics/skills`
- `skills generate` requires login — run `ctx7 login` first

## Preflight

- Confirm the request is about ctx7 or Context7-managed docs/skills/setup.
- Prefer the narrowest ctx7 command that can answer the task.
- Avoid more than three lookup attempts unless the user explicitly wants broader exploration.

## Output Contract

- Report the exact ctx7 command or path used.
- Distinguish setup, lookup, and skill-management outcomes.
- Mention quota or auth limits plainly.
