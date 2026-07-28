---
name: context7-mcp
description: "Routing skill for fetching current software library/framework/API documentation through the Context7 MCP tools. Use when the user needs current docs or code examples for a named software dependency and the Context7 MCP tool path is the right execution surface. Do not use for generic webpage retrieval, docs-site crawling, or non-library web research."
---

## Role Boundaries

- Use this skill when the task is specifically a Context7 MCP documentation lookup for a named library/framework/API.
- Use it to route and execute the MCP docs flow, not as a generic web-research surface.
- Do not use it for general webpage capture, generic internet lookup, or Context7 CLI setup/management tasks.

## Handoff Rules

- **Named library/framework/API docs via MCP**: stay in this skill.
- **Generic webpage/article/docs-site capture**: hand off to `firecrawl-cli`.
- **Platform/source selection or multi-source web research**: hand off to `agent-reach`.
- **Context7 CLI installation, skill management, or MCP setup work**: hand off to `context7-cli`.

When the user asks about libraries, frameworks, or needs code examples, use Context7 to fetch current documentation instead of relying on training data.

## When to Use This Skill

Activate this skill when the user:

- Asks setup or configuration questions ("How do I configure Next.js middleware?")
- Requests code involving libraries ("Write a Prisma query for...")
- Needs API references ("What are the Supabase auth methods?")
- Mentions specific frameworks (React, Vue, Svelte, Express, Tailwind, etc.)

## How to Fetch Documentation

### Step 1: Resolve the Library ID

Call `resolve-library-id` with:

- `libraryName`: The library name extracted from the user's question
- `query`: The user's full question (improves relevance ranking)

### Step 2: Select the Best Match

From the resolution results, choose based on:

- Exact or closest name match to what the user asked for
- Higher benchmark scores indicate better documentation quality
- If the user mentioned a version (e.g., "React 19"), prefer version-specific IDs

### Step 3: Fetch the Documentation

Call `query-docs` with:

- `libraryId`: The selected Context7 library ID (e.g., `/vercel/next.js`)
- `query`: The user's specific question

### Step 4: Use the Documentation

Incorporate the fetched documentation into your response:

- Answer the user's question using current, accurate information
- Include relevant code examples from the docs
- Cite the library version when relevant

## Guidelines

- **Be specific**: Pass the user's full question as the query for better results
- **Version awareness**: When users mention versions ("Next.js 15", "React 19"), use version-specific library IDs if available from the resolution step
- **Prefer official sources**: When multiple matches exist, prefer official/primary packages over community forks

## Preflight

- Confirm the user wants Context7 MCP, not CLI or generic web lookup.
- Resolve the library ID before querying docs.
- Stop after the best available docs if the lookup remains ambiguous.

## Output Contract

- Return the resolved library ID and the docs answer.
- State when the docs are current, ambiguous, or incomplete.
- Do not present MCP lookup as generic web research.
