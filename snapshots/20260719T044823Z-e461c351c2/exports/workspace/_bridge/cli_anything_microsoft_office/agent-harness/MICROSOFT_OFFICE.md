# Microsoft Office CLI-Anything Harness

## Ownership

This harness exposes the installed 64-bit Microsoft Word, Excel, and
PowerPoint desktop applications through bounded COM Automation commands.
Microsoft Office remains the real rendering, calculation, pagination, and
export backend.

## Non-Goals

- No arbitrary VBA or macro execution.
- No arbitrary PowerShell passthrough.
- No unattended reuse of a user's existing visible Office process.
- No replacement OOXML renderer implemented in Python.
- No remote document upload or cloud account automation.

## Backend

The Python CLI calls `utils/office_backend.ps1` through Windows PowerShell. Each
operation creates an isolated hidden Office COM instance, disables automation
macros and alerts, performs one bounded action, closes all documents, calls
`Quit()`, and releases COM objects in `finally` blocks.

## Command Model

- `system status`: read-only installation and COM registration probe.
- `word create|info|inspect|operations|edit|export-pdf`
- `excel create|info|inspect|operations|edit|export-pdf`
- `powerpoint create|info|inspect|operations|edit|export-pdf`
- `preview recipes|capture|latest`: publish real Office PDF output as a
  `preview-bundle/v1` bundle for later inspection through `cli-hub previews`.

All commands support machine-readable JSON. Mutating commands refuse overwrite
unless `--overwrite` is supplied and honor the root `--dry-run` option.

`edit` accepts an ordered JSON batch of declared operations, edits a temporary
copy, returns one receipt per operation, and promotes the destination only
after a successful native Office save. `inspect` returns bounded structure for
planning edits without dumping unlimited document content.

## State Behavior

Office documents are the source of truth. The harness does not keep a long-lived
COM session. Preview bundles are immutable; `preview latest` is read-only.

## Safety Boundary

The harness accepts only declared document paths and structured content. It
does not expose COM member names, VBA entrypoints, shell commands, or arbitrary
Office object model expressions.

Unknown operations and fields are rejected before Office starts. Source and
output paths must differ, failed edits remove incomplete temporary output, and
the original source remains unchanged.
