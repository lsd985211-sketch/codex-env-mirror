---
name: obsidian-markdown
description: Create and edit Obsidian-flavored Markdown with wikilinks, embeds, callouts, properties, tags, tasks, math, Mermaid, and block references. Use when working with Markdown files intended for an Obsidian vault.
metadata: {"codex":{"compatibility":"Preserve vault conventions and existing frontmatter. Standard Markdown tools may not understand every Obsidian extension."}}
---

# Obsidian Markdown

## Core Workflow

1. Inspect the target note and nearby vault conventions before editing.
2. Preserve existing YAML properties, link style, heading hierarchy, tags, and embed conventions.
3. Use wikilinks and block references only when their targets are known or intentionally future-facing.
4. Keep callouts, tasks, tables, math, and Mermaid syntax valid.
5. Re-read the result and verify frontmatter delimiters, code fences, links, and embeds.

## Progressive Reference

Read `references/full-guide.md` only when the task needs exact syntax for wikilinks, embeds, callouts, properties, tags, math, Mermaid, comments, or complex examples.

## Output Contract

- State the note path and major constructs changed.
- Preserve unrelated note content and vault-specific conventions.
- Report unresolved links or assumptions about vault structure.
