# Windows PowerPoint And CJK Validation

Use this path when the final artifact is a `.pptx` delivered for Windows PowerPoint, especially when slide images contain Chinese, Japanese, or Korean text.

## Acceptance Predicate

A deck is deliverable only when all of the following are true:

1. Source identity and edition facts are verified before slide generation.
2. The numbered source images and prompt notes are complete.
3. PowerPoint opens the generated `.pptx` and reports the expected slide count.
4. PowerPoint exports every slide at the requested dimensions.
5. The exported pages are nonblank and produce a complete contact sheet.
6. The contact sheet and at least one title-heavy and one text-heavy page show correct glyphs, no clipping, and no incoherent overlap.
7. The final Windows path is confirmed with `Test-Path`; a WSL path alone is not File Explorer evidence.

File existence, a merge exit code, or source-image inspection alone does not satisfy this predicate.

## CJK Font Guard

Register an explicit local font before drawing text. For `@napi-rs/canvas`, CSS font syntax is ordered as weight, size, then family:

```js
GlobalFonts.registerFromPath('C:\\Windows\\Fonts\\msyh.ttc', 'Microsoft YaHei');
ctx.font = '700 48px "Microsoft YaHei"';
```

Do not write the family before the size. That malformed declaration can silently fall back to missing-glyph boxes while Latin digits still look correct.

Keep generated text out of image models when exact wording matters. Render CJK text deterministically and use generated or vector visuals only for non-text imagery.

## Bundled Runtime

Prefer the Codex desktop bundled runtime. Its pnpm layout may require both module roots:

```bash
node_modules/.pnpm/node_modules
node_modules
```

The validation launcher discovers these roots and sets `NODE_PATH`; it does not install packages. A missing bundled runtime, PowerPoint, or `sharp` is a prerequisite failure, not permission to modify the managed Python, Node, Office, or search environment.

## Final Validation

Run from WSL:

```bash
scripts/run-windows-deck-validation.sh \
  --pptx /mnt/c/path/to/deck.pptx \
  --out /mnt/c/path/to/render-check \
  --expected 14
```

Add `--replace` only to reuse a directory previously marked by this validator. The launcher refuses to delete an unrelated nonempty directory.

The validation chain:

- opens PowerPoint read-only with `WithWindow=false`;
- does not set `Application.Visible=false`, which PowerPoint can reject;
- exports all pages at 1600 by 900 by default;
- checks count, contiguous numbering, dimensions, minimum file size, and entropy;
- writes `validation-ppt-render.json`, `validation-slide-images.json`, and `contact-sheet.png`.

PowerPoint export is the application-level receipt. The Node receipt and contact sheet prove the exported pages were consumed and inspected, rather than merely created.

## Failure Handling

- Missing glyph boxes: fix font registration or the canvas font declaration, regenerate every affected slide, rebuild the PPTX, and rerun the full validation.
- Wrong slide count: compare numbered images, prompts, and PPTX pages before regenerating.
- Blank or low-entropy page: inspect the corresponding source image and PowerPoint export; do not lower the threshold without visual evidence.
- Existing output directory: choose a new directory or use `--replace` only when the marker proves ownership.
- PowerPoint cannot open the file: preserve the failed artifact and error receipt; do not claim compatibility from OOXML/ZIP validity alone.

## Closeout Evidence

Report the Windows-visible output path, expected and rendered slide counts, dimensions, contact-sheet inspection, regenerated pages, and whether PowerPoint itself opened the deck. Keep the receipts beside the render-check output or provide their stable paths.
