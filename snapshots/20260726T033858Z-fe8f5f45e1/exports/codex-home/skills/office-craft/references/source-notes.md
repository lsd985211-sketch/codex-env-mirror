# Source Notes

Use these source categories when refreshing office-workflow assumptions:

- python-docx official documentation: document creation, paragraphs, runs,
  styles, headings, and tables.
- openpyxl official documentation: xlsx workbook structure, cells, formulas,
  charts, and styles.
- XlsxWriter official documentation: generated Excel workbooks, formatting,
  charts, and formulas.
- pypdf official documentation: PDF reading, writing, splitting, merging, and
  metadata.
- pdfplumber documentation: PDF text/table extraction and page-level inspection.
- Mammoth documentation: semantic `.docx` to HTML conversion.
- docxtpl documentation: templated `.docx` report generation.
- ReportLab documentation: programmatic PDF generation.
- Pandoc user guide: semantic document conversion across markdown, HTML, docx,
  and PDF-related workflows.
- LibreOffice help/wiki: `soffice --headless` conversion behavior.
- Tesseract documentation: OCR command-line usage and language data.

Current policy:

- Treat Python library docs as the default implementation surface.
- Treat external tools as optional capability, not mandatory baseline.
- Treat installation-source choice as evidence-driven: use local inventory,
  resource-layer probes, package-manager results, and command probes before
  declaring a route usable.
- Prefer the least disruptive verified source. If one source repeatedly stalls
  or fails, switch source once and record the reason instead of extending the
  retry loop.
- Refresh source knowledge before major office-system changes or when a user
  asks for a feature beyond the current local toolchain.
