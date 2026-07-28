---
name: content-digest
description: >
  Transform acquired long-form content such as transcripts, podcasts, interviews,
  videos, and articles into concise insight summaries or narrative articles.
  Use when the source content or a completed acquisition receipt is available and
  the user wants key ideas, repurposed content, short-form posts, or a long-form
  narrative. Do not use as the web or transcript acquisition layer.
---

# Content Digest

Transform source material without changing its factual meaning. This skill owns analysis and writing after acquisition, not URL fetching.

## Role Boundaries

Accept:

- text supplied directly by the user;
- a local file that Codex can read;
- a completed resource-layer receipt and its required content path;
- structured notes with enough source context.

For a URL without acquired content, submit a resource request for the appropriate article, transcript, or media metadata and wait for a consumable receipt. Do not bypass the acquisition owner or invent a transcript.

## Preflight

Determine:

- source type and completeness;
- requested output: short, long, or both;
- target audience and platform constraints;
- whether direct quotations are permitted and supported by the source.

Default to a concise digest when the user does not specify a format. Generate both forms only when both are useful.

## Analysis

1. Read the complete available source or explicitly state the sampled scope.
2. Extract claims, decisions, examples, numbers, tensions, and memorable source language.
3. Separate source facts from interpretation.
4. Rank insights by relevance, distinctiveness, evidence, and consequence.
5. Build one shared insight set before writing multiple output formats.

Do not require an arbitrary count such as fifty viewpoints. The number of extracted ideas should follow source length and density.

## Short Form

Use approximately 5-12 strong insights unless the user specifies otherwise.

- Open with the source and its central tension.
- Explain why each selected point matters.
- Preserve concrete details and uncertainty.
- Avoid repetitive AI phrasing, unsupported certainty, and decorative filler.
- Keep numbered emoji formatting only when it fits the requested platform.

## Long Form

Create a narrative from the same insight set:

1. context and stakes;
2. central problem or contradiction;
3. evidence, examples, and turning points;
4. implications and practical lessons;
5. a restrained conclusion.

Use source quotations sparingly and verify attribution. Paraphrase when exact wording is unavailable.

## Quality Gate

Check that:

- facts are traceable to the supplied source;
- interpretation is labeled or phrased as analysis;
- short and long versions do not contradict each other;
- every major takeaway explains its significance;
- numbers, names, and dates are preserved accurately;
- the output does not imply full-source coverage when only excerpts were available.

## Delivery

Return content in the conversation unless the user asks to save it. Resolve destinations through the current file, Obsidian, document, or publishing owner; never use a historical user-specific path.

## When to Load References

Read [references/style-guide.md](references/style-guide.md) when detailed tone or structure guidance is needed. Read [references/examples.md](references/examples.md) only when a concrete quality reference is useful. Do not load both by default.

## Output Contract

Include:

- source scope;
- selected output format;
- transformed content;
- factual limitations or missing context;
- saved artifact path only when one was actually created.
