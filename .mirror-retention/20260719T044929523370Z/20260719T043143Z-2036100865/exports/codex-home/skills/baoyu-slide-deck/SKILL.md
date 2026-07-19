---
name: baoyu-slide-deck
description: Generate a professional slide deck from source content using outline, prompt, image, PPTX, and PDF stages. Use when the user requests a presentation, slide deck, PPT, or slide images and the image-first Baoyu workflow is appropriate.
---

# Baoyu Slide Deck

## Workflow

1. Read the source and identify audience, language, slide count, format, and visual direction.
2. Select an existing style preset or define explicit style dimensions.
3. Produce an outline and request confirmation when the content or visual direction is materially ambiguous.
4. Generate slide prompts and images with stable numbering and output paths.
5. Merge the verified slides into PPTX/PDF when requested.
6. Inspect representative slides and report failed or regenerated pages.

## Commands

```bash
/baoyu-slide-deck <content-file>
/baoyu-slide-deck <content-file> --style <preset> --slides <count>
/baoyu-slide-deck <content-file> --outline-only
```

## Progressive References

- Read `references/full-guide.md` for detailed style dimensions, confirmation rules, partial workflows, slide editing, and merge behavior.
- Load only the specific style, layout, or workflow reference needed for the current deck.
- Use `presentation-craft` instead when the task is primarily editing an existing PowerPoint rather than generating image-first slides.

## Output Contract

- State the output directory, slide count, selected style, generated formats, and verification status.
- Preserve editable source and intermediate artifacts when they are needed for regeneration.
