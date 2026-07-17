# Troubleshooting

## Classify The Failure

- CLI/schema error: fix operation names, required fields, types, extensions, overwrite, or source/output paths. Office was not started.
- COM/open error: check `system status`, registration, file locks, Protected View, read-only arguments, and path accessibility.
- Semantic error: the command completed but the content, formula, style, or shape is wrong. Inspect the output and refine the batch.
- Rendering error: structure is correct but pagination, clipping, overlap, contrast, or chart layout is wrong. Export PDF and adjust layout operations.

## Common Causes

- Word style not found: use built-in style identifiers rather than localized display names.
- Word content disappeared: avoid unbounded paragraph insertion at the final paragraph; edit a copy and inspect every structural mutation.
- Excel formula text exists but result is stale: reopen through Excel and verify calculated values.
- Excel chart or sort targets the wrong range: inspect sheet names and used ranges, then rebuild the request.
- PowerPoint save says read-only: open the temporary editing copy with ReadOnly=false.
- PowerPoint shape not found: inspect names; do not guess placeholder or localized names.
- Office process remains: close child documents/collections, call Quit, release COM objects, and run garbage collection only after release.

## Recovery Order

1. Preserve the source and remove only incomplete temporary output.
2. Run `system status` and `<app> inspect`.
3. Run `<app> operations` and rebuild the smallest failing batch.
4. Dry-run, execute to a new output, and inspect again.
5. Escalate to GUI inspection only when the remaining defect is visual or interaction-specific.
