---
name: obsidian-bases
description: Create and edit Obsidian .base files with filters, formulas, properties, summaries, and table, card, list, or map views. Use for database-like views over notes in an Obsidian vault.
metadata: {"codex":{"compatibility":"Bases syntax can evolve with Obsidian. Preserve unknown fields and verify against the installed app or current owner documentation when exact compatibility matters."}}
---

# Obsidian Bases

## Core Contract

- `.base` files are YAML documents describing global filters, formulas, property presentation, summaries, and one or more views.
- Preserve unknown keys when editing an existing file.
- Quote YAML values when operators, dates, tags, links, or punctuation could change parsing.

## Workflow

1. Identify the target notes and required view type.
2. Define the narrowest global/view filters.
3. Add only the formulas and property settings needed by the requested view.
4. Validate YAML structure, property references, formula names, and view-specific keys.
5. Reopen the file and report assumptions about vault properties or folders.

## Progressive Reference

Read `references/full-guide.md` for the complete schema, operators, functions, view definitions, summaries, embedding rules, and worked examples.

## Output Contract

- State the Base path, views created or changed, and filters/formulas introduced.
- Report missing vault properties and compatibility assumptions explicitly.
