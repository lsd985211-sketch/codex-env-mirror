# Microsoft Office Harness Test Plan

## Unit Coverage

- Installed application and COM registration status has a stable JSON schema.
- Output path validation refuses accidental overwrite.
- File-type routing recognizes DOCX, XLSX, PPTX and rejects unsupported input.
- Preview bundle manifests preserve source fingerprint and real backend method.
- CLI `--help`, `--json`, and `--dry-run` contracts are available.
- Operation schemas reject unknown operations and fields before Office starts.
- Edit commands preserve the source and return ordered operation receipts.

## Real Backend E2E

- Word creates a real DOCX and exports it through Word to PDF.
- Excel creates a real XLSX with structured cell data and exports through Excel
  to PDF.
- PowerPoint creates a real PPTX and exports through PowerPoint to PDF.
- Generated OOXML files are valid ZIP containers with the expected main parts.
- Exported PDFs start with `%PDF-` and are non-empty.
- Info commands reopen each real file through the matching Office application.
- Edit commands modify real DOCX, XLSX, and PPTX files, then inspect and export
  the edited artifacts through the matching application.
- The installed `cli-anything-microsoft-office` entrypoint is exercised with
  `CLI_ANYTHING_FORCE_INSTALLED=1`.

## Safety Tests

- Existing outputs are not overwritten without `--overwrite`.
- `--dry-run` does not start Office or write output files.
- Unsupported extensions fail with a structured error.
- COM processes are closed after every E2E command.

## Results

Last verified: 2026-07-12 (Asia/Shanghai)

```text
python -m pytest -q ...\tests\test_core.py
11 passed in 0.11s

CLI_ANYTHING_FORCE_INSTALLED=1 CLI_ANYTHING_OFFICE_E2E=1 \
python -m pytest -v -s --tb=short ...\tests\test_full_e2e.py
2 passed in 189.11s
```

The E2E suite used the installed command at
`C:\Python314\Scripts\cli-anything-microsoft-office.EXE`. Word, Excel, and
PowerPoint each created and batch-edited a real OOXML file, reopened it through
the richer inspect command, and exported a PDF through the matching hidden
Office COM application. The test verified edited content, formulas, shapes,
OOXML main parts, `%PDF-` headers, and no additional orphan Office process.
