---
name: presentation-craft
description: Create, restructure, and polish presentation decks (.pptx) for presentations, including slide planning, content reduction, visual hierarchy, layout cleanup, and pptx generation/editing with python-pptx. Use when the user asks to make, analyze, rewrite, or improve PowerPoint decks or presentation-style slide content.
---

# Presentation Craft

## Overview

Build presentation decks that read like finished slides, not notes. Prioritize
story structure, slide hierarchy, and visual pacing over raw text volume.

## Framework Layer

- Primary layer: execution
- Reason for this layer: this skill owns the concrete slide-building workflow
  once the user has decided they want a presentation deck.

## Mode Entry

- `答辩版`: default for thesis defense, viva, formal review, or Q&A-heavy decks.
- `汇报版`: default for project reports, progress reviews, and decision decks.
- `研究进展版`: default for topic overviews, literature progress, and survey decks.
- If the user does not name a mode, pick the smallest mode that fits the audience
  and state the choice before editing.
- Before any file change, first show a short readable proposal of what will change.

## Role Boundaries

- Own deck planning, slide rewriting, layout choices, section ordering, and
  pptx generation or editing.
- Own content compression, speaker-friendly phrasing, and consistency checks.
- Hand routing and source selection back to broader skills when the request is
  actually about research, GUI automation, office suite operation, or web
  discovery.

## Operating Rules

- Start from the user goal:答辩稿、汇报稿、课堂展示、方案演示, or deck
  cleanup.
- Prefer a structure first: title, problem, core logic, examples, progress,
  conclusion.
- Keep one slide to one point.
- Use short bullets, cards, timelines, tables, or flow diagrams instead of long
  paragraphs.
- Preserve important facts; reduce repetition, not meaning.
- When rewriting an existing deck, keep the original file intact and write a new
  output unless the user explicitly asks to overwrite.
- Validate the output by re-reading slide text and checking slide count, page
  balance, and obvious overflow risks.

## When to Load References

- Read `references/ppt-workflow.md` before creating or restructuring a deck.
- Skip the reference only for trivial inspection questions that do not require
  output changes.

## Core Workflow

1. Identify the deck type and audience.
2. Choose a slide count and narrative arc.
3. Classify each source slide as keep, merge, split, or drop.
4. Rewrite slides with short titles and a single takeaway.
5. Use diagrams, dual-column layouts, or compact tables where they carry the
   message better than bullets.
6. Generate or edit the pptx.
7. Re-open the result and check for readability, balance, and missing content.

## Quality Checks

- Are slide titles specific and short?
- Does each slide have one main point?
- Are long paragraphs broken up?
- Is the front half of the deck easy to follow without a narrator?
- Are claims that need freshness or evidence clearly marked?
- Does the deck still retain the important content the user expected?

## Tooling

- Use `python-pptx` for pptx creation and structural edits.
- Use file extraction or text inspection to review the resulting slides.
- Use GUI office apps only when the user explicitly wants in-app editing or the
  file needs a final visual pass in the desktop app.

## Preflight

- Confirm the deck type and audience before choosing a mode.
- Decide whether the main risk is structure, content, or visual balance.
- Keep the slide count and narrative arc small enough to validate.

## Output Contract

- State the mode, slide strategy, and validation method.
- Report what was kept, merged, split, or dropped.
- Mention any unresolved overflow or readability risk.
