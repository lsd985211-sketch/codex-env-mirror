# Office Workflows

## Deliverable Types

- `document`: Word report, notes, formal document, template, or formatted text.
- `workbook`: Excel workbook, CSV, table cleanup, chart-ready data, or analysis.
- `pdf`: PDF extraction, split/merge, page inspection, summary, or review.
- `mixed package`: multiple files that need a consistent naming, formatting, or
  validation pass.

## Word Patterns

- Preserve structure: title, headings, body paragraphs, tables, references.
- Use heading levels instead of fake bold paragraphs.
- Keep one idea per paragraph for reports and formal documents.
- For rewrites, separate content changes from format cleanup in the report back.
- Validate by reopening the file and checking paragraph/table counts and key
  headings.

## Excel Patterns

- Inspect sheet names, dimensions, merged cells, formulas, and obvious empty
  regions before editing.
- Keep raw data intact when possible; create cleaned or analysis sheets instead
  of overwriting source data.
- Use formulas when the user needs interactive spreadsheets; use computed values
  when the output is a static report.
- For summary workbooks, include a short summary sheet and leave source sheets
  readable.
- Validate row/column counts, sheet names, formulas, and sample cell values.

## PDF Patterns

- First identify whether the PDF is text-based or scanned/image-based.
- Use text extraction for searchable PDFs; use OCR/GUI only if extraction fails
  and the user needs the content.
- For merge/split tasks, preserve page order and report page ranges.
- For summaries, cite page numbers when available.
- Validate by reopening the output PDF and checking page count.

## General Quality Bar

- Prefer clear filenames with task suffixes, for example `_cleaned`,
  `_summary`, `_reviewed`, or `_converted`.
- Report what changed, what was preserved, and what was not verified.
- If a library cannot preserve a complex visual layout, state that limitation
  before editing and prefer outputting a new file.

