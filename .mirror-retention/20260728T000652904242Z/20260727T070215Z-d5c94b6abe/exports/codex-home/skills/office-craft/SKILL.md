---
name: office-craft
description: Create, analyze, edit, convert, and validate common office files, including Word documents (.docx), Excel workbooks (.xlsx/.csv), PDFs, and mixed office deliverables. Use when Codex needs to inspect, clean, restructure, format, summarize, generate, or quality-check office documents outside dedicated slide-deck work.
---

# Office Craft

## Overview

Handle common office files as finished deliverables, not raw text dumps. Prefer
backup-first editing, explicit output paths, readable structure, and validation
after generation.

## Framework Layer

- Primary layer: execution
- Reason for this layer: this skill owns concrete office-file operations once
  the user has asked for document, workbook, PDF, or mixed office work.

## Role Boundaries

- Use `presentation-craft` for PowerPoint-specific planning, restructuring,
  and slide design.
- Use this skill for Word, Excel, PDF, CSV, and mixed office workflows.
- Use GUI automation only when a desktop app must be visually inspected or the
  file needs an in-app final pass.
- Hand off to `cli-anything-microsoft-office` when installed Word, Excel, or
  PowerPoint must own pagination, formula calculation, rendering, application
  compatibility, or native PDF export. Do not use GUI automation for structured
  native Office operations already covered by that harness.
- For long architecture reports or other mixed Markdown/diagram/table
  deliverables, use `office-craft` for semantic generation and structure, then
  use `cli-anything-microsoft-office` for native Word inspection and PDF export.
  These are complementary owners, not competing routes.
- Use web/research skills when the content itself requires current external
  sources.

## Operating Rules

- Before modifying a local file, ask for approval when the workspace rules
  require it.
- Preserve the original file. Write backups with clear suffixes and generate a
  new output file unless the user explicitly requests overwrite.
- Identify the deliverable type first: report/document, table/workbook, PDF
  review, conversion, or mixed package.
- State the planned output briefly before editing when the change is
  substantial.
- Validate generated files by reopening them with code and checking core
  structure: page/paragraph/table counts, sheet names, row/column counts, or PDF
  page/text availability.
- Do not treat a successful file write as sufficient. Verify content exists and
  that obvious formatting risks are controlled.

## When to Load References

- Read `references/office-workflows.md` before non-trivial office work.
- Read `references/tool-routing.md` when conversion, OCR, visual fidelity, or
  external desktop tools may matter.
- Read `references/source-notes.md` when you need the rationale for library
  choices or current documentation pointers.
- For simple inspection questions, direct file probing is enough.

## Tooling

- Word: use `python-docx` for `.docx` creation, extraction, basic formatting,
  headings, tables, and styles.
- Excel: use `openpyxl` for `.xlsx` structure and formulas; use `pandas` for
  tabular analysis; use `xlsxwriter` when creating polished new workbooks.
- PDF: use `pypdf` for splitting/merging/metadata; use `pdfplumber` for text
  and table extraction when available.
- Reports and conversion helpers: use `docxtpl` for templated Word output,
  `mammoth` for semantic docx-to-HTML extraction, `reportlab` for generated
  PDFs, and `matplotlib`/`XlsxWriter` for chart-ready workbook deliverables.
- PowerPoint: route to `presentation-craft`; use `python-pptx` only when that
  skill is active or the task is a small structural inspection.
- Run `scripts/check_office_deps.py` when dependency availability is uncertain.
- Run `scripts/check_office_tools.py` when external conversion, OCR, or visual
  fidelity may be required.
- Use `scripts/inspect_office_file.py <file>` for a first-pass structure audit.
- In Codex Desktop, call `load_workspace_dependencies` before document
  generation and use its bundled Python when the system Python lacks
  `python-docx`, `Pillow`, `pypdf`, or other document libraries. Do not discover
  this only after a long conversion fails.
- For Markdown reports containing Mermaid diagrams, read
  `references/docx-mermaid-reporting.md` before conversion.

## Core Workflow

1. Classify the file and user goal.
2. Inspect source structure before editing.
3. Make a backup or choose a new output path.
4. Apply the smallest reliable transformation.
5. Reopen and validate the output.
6. Report exact output paths, validation results, and any remaining risks.

## Complex Report Workflow

For a human-readable report that needs Markdown, HTML, DOCX, PDF, diagrams, and
a machine snapshot:

1. Keep one canonical Markdown source and one machine-readable snapshot.
2. Render Mermaid once and reuse the rendered assets across HTML, DOCX, and PDF.
3. Generate DOCX with the bundled document runtime.
4. Reopen the DOCX structurally, then inspect it with native Word.
5. Use Word for the final compatibility/page-count check and native PDF export.
6. Verify headings, tables, images, page count, extracted text, and portable
   asset paths before publishing.

## Preflight
- Confirm the skill matches the task before using it.
- Keep the scope tight and avoid unrelated changes.

## Output Contract

- Preserve the original format unless the user asks for redesign.
- Prefer one clear deliverable per run.
- State what was verified after writing: structure, content, and obvious rendering risk.
