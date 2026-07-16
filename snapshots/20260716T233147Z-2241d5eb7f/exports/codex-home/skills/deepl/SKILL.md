---
name: deepl
description: Translate text, supported documents, or XLIFF through the DeepL API. Use when the user explicitly requests DeepL, document translation, glossary-aware translation, or XLIFF target population.
metadata: {"codex":{"compatibility":"Requires DEEPL_API_KEY and network access. API endpoints, document limits, and supported languages may change; verify current owner documentation when exact behavior matters."}}
---

# DeepL Translation

## Workflow

1. Confirm source format, source/target languages, glossary, formality, and output path.
2. Check `DEEPL_API_KEY` without printing it and select the correct Free or Pro endpoint.
3. For text, batch within API limits and preserve paragraph boundaries.
4. For documents, preserve the original and write a separate translated output.
5. For XLIFF, parse XML structurally, translate intended units only, and preserve namespaces, IDs, placeholders, and segmentation.
6. Validate nonempty output, target language, file structure, placeholders, and untranslated units.

## Progressive Reference

Read `references/full-guide.md` for detailed API requests, file upload/download, XLIFF handling, status codes, retries, and language codes. Treat its endpoint and limit examples as reference material that may require live verification.

## Output Contract

- State source/target languages, translated unit count, output path, and validation performed.
- Never log API keys or overwrite the source without explicit approval.
- Report quota, permission, unsupported-format, or partial-translation failures precisely.
