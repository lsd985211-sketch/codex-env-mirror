# Microsoft Office CLI-Anything Harness

This harness exposes the locally installed Microsoft Word, Excel, and
PowerPoint applications as a bounded, JSON-friendly CLI. Microsoft Office is a
hard dependency: the harness uses hidden COM instances for real pagination,
calculation, file generation, and PDF export.

## Install

From the repository root:

```powershell
python -m pip install -e _bridge\cli_anything_microsoft_office\agent-harness
```

## Native Editing

Each application exposes `operations`, `inspect`, and transactional `edit`:

```powershell
cli-anything-microsoft-office --json word operations
cli-anything-microsoft-office --json word inspect C:\Temp\brief.docx
cli-anything-microsoft-office --json word edit C:\Temp\brief.docx C:\Temp\brief-edited.docx --operations-file C:\Temp\word-ops.json
```

The operations file is a non-empty JSON array of allowlisted operations. The
harness rejects unknown operations and fields before Office starts. Edit works
on a temporary copy, preserves the source, and promotes the output only after a
successful save. Use `--dry-run` to validate the complete batch without
starting Office.

Word covers text, paragraphs, headings, tables, formatting, page setup,
headers, footers, and properties. Excel covers sheets, ranges, formulas,
formatting, sorting, filtering, and charts. PowerPoint covers slides, text,
images, tables, shapes, backgrounds, and properties.

The installation does not install Office and does not modify the user's Office
settings. It only installs the Python entry point and the bundled PowerShell
backend.

## Examples

```powershell
cli-anything-microsoft-office --json system status
cli-anything-microsoft-office --json word create C:\Temp\brief.docx --title "Brief" --body "Draft"
cli-anything-microsoft-office --json word info C:\Temp\brief.docx
cli-anything-microsoft-office --json word export-pdf C:\Temp\brief.docx C:\Temp\brief.pdf
cli-anything-microsoft-office --json excel create C:\Temp\table.xlsx --data-json '[["Name","Value"],["A",1]]'
cli-anything-microsoft-office --json powerpoint create C:\Temp\deck.pptx --title "Review"
cli-anything-microsoft-office --json preview capture C:\Temp\brief.docx
```

Mutating commands refuse to overwrite existing files unless `--overwrite` is
provided. Use `--dry-run` to validate output paths without starting Office.
Every command is one-shot and releases its COM objects before returning.

## Preview bundles

`preview capture` produces a `preview-bundle/v1` manifest, summary, and a PDF
artifact. It is a producer only; `cli-hub previews` remains the consumer and
owns rendering or inspection of preview bundles.

## Safety and limitations

- Windows and a compatible installed Microsoft Office desktop application are required.
- The harness never executes VBA/macros or arbitrary PowerShell/COM expressions.
- It never attaches to an already visible user Office process.
- The initial command surface creates, inspects, and exports common document types.
- Existing files are read-only for `info` and export input operations.
