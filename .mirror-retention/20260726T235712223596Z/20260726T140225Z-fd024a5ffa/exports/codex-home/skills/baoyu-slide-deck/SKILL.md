---
name: baoyu-slide-deck
description: Generate a professional slide deck from source content using outline, prompt, image, PPTX, and PDF stages. Use for presentations, PowerPoint/PPT, classroom or teaching decks, slide images, and image-first deck generation, including Windows PowerPoint delivery and CJK text validation.
---

# Baoyu Slide Deck

## Workflow

1. Read the source and identify audience, language, slide count, format, and visual direction.
2. Select an existing style preset or define explicit style dimensions.
3. Produce an outline and request confirmation when the content or visual direction is materially ambiguous.
4. Generate slide prompts and images with stable numbering and output paths.
5. Merge the verified slides into PPTX/PDF when requested.
6. Validate the delivered file in its target presentation application; inspect the complete contact sheet plus representative text-heavy slides, then report failed or regenerated pages.

## Commands

```bash
/baoyu-slide-deck <content-file>
/baoyu-slide-deck <content-file> --style <preset> --slides <count>
/baoyu-slide-deck <content-file> --outline-only
```

## When to Load References

- Read `references/full-guide.md` for detailed style dimensions, confirmation rules, partial workflows, slide editing, and merge behavior.
- Read `references/windows-powerpoint-validation.md` before delivering a Windows PowerPoint deck, especially when it contains Chinese, Japanese, or Korean text. Use its bundled scripts for font-safe generation and application-level rendering evidence.
- Load only the specific style, layout, or workflow reference needed for the current deck.
- Use `presentation-craft` instead when the task is primarily editing an existing PowerPoint rather than generating image-first slides.

## Role Boundaries

- Own image-first deck generation, merge, and output validation. Keep source acquisition/version evidence with the resource layer and use `presentation-craft` for editing an existing PowerPoint.
- Treat image generation and PPTX creation as intermediate success. Delivery requires the target application to open and render the expected slide count.
- Do not install or rebuild Node, Office, fonts, or document dependencies as a validation fallback. Reuse the bundled runtime or report the missing prerequisite.
- Do not accept file existence, a merge command exit code, or representative source images as proof of final rendering.

## Output Contract

- State the output directory, slide count, selected style, generated formats, target-application rendering status, and any regenerated pages.
- Preserve editable source and intermediate artifacts when they are needed for regeneration.
