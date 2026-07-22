# Routing And Workflow

## Choose The Owner

- Use this skill when installed Office must determine pagination, layout, calculation, rendering, compatibility, or PDF output.
- Use `office-craft`, `docx`, `xlsx`, `pptx`, or `presentation-craft` when OOXML manipulation is sufficient.
- Use GUI automation only for a visual interaction that the structured harness cannot express. Do not use a visible Office session for routine edits.

## Native Lifecycle

1. Inspect the source and record the application, structure, and target output.
2. Query `operations` instead of guessing operation names or fields.
3. Build one ordered JSON batch; keep it under the operation limit.
4. Run `--dry-run`. Treat schema, path, extension, and overwrite errors as contract errors, not COM failures.
5. Run `edit` with source and output paths that differ.
6. Reopen the output with `inspect` and verify the intended structure or values.
7. Export PDF or capture a preview only when rendering evidence is required.

## Safety

- Never pass raw COM member names, VBA, macros, PowerShell, or shell commands.
- Preserve the source. Existing destinations require `--overwrite`.
- Prefer one hidden isolated Office instance per batch.
- A completed receipt proves execution, not semantic correctness; always inspect relevant output fields.

## Official Basis

Microsoft Learn Office VBA object-model documentation for Word Range, Excel Range, and PowerPoint Slides/Shapes.
