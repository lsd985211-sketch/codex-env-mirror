# Office Tool Routing

## Default Path

- Prefer Python libraries for deterministic inspection, generation, and
  validation.
- Prefer desktop/GUI tools only when visual fidelity, manual layout review, or
  app-specific features matter.
- Prefer optional external CLI tools only after probing availability.
- On Windows, run external office executables in hidden/no-window mode for
  probes and batch work. Avoid visible console popups unless the user explicitly
  asks for an interactive desktop app.
- Treat tool status as `ok`, `present-timeout`, `missing`, or `error`. Only
  `ok` is ready for unattended use. `present-timeout` means installed but not
  yet safe for automation until tested on a small real conversion.

## Python Library Roles

- `python-docx`: docx paragraphs, headings, tables, styles, and basic document
  creation.
- `docxtpl`: Word templates with Jinja-style placeholders.
- `mammoth`: semantic docx-to-HTML extraction when preserving meaning matters
  more than exact layout.
- `openpyxl`: xlsx workbook structure, cells, formulas, styles, and charts.
- `pandas`: data cleaning, joins, grouping, summaries, and CSV/XLSX ingestion.
- `xlsxwriter`: polished generated Excel files with formats and charts.
- `pypdf`: PDF page split/merge/metadata/encryption-level operations.
- `pdfplumber`: PDF text and table extraction from text-based PDFs.
- `reportlab`: generated PDFs when the deliverable is a new report.
- `matplotlib`: charts for reports and workbook/image outputs.

## External Tool Roles

- `soffice` / LibreOffice: best-effort conversion between Office formats and
  PDF when installed; use headless mode and validate output. If probing returns
  `present-timeout`, run a tiny conversion smoke test before relying on it.
- `pandoc`: markdown/html/docx conversion when semantic conversion matters and
  the tool is installed.
- `tesseract`: OCR for scanned PDFs or images when text extraction fails.
- `pdftotext`: fast text extraction when Poppler is installed.
- `qpdf`: robust PDF structural repair, split/merge, and encryption handling.
- `mutool`: PDF rendering and low-level inspection when MuPDF is installed.
- `magick`: image conversion and raster preprocessing when ImageMagick is
  installed.

## Windows Install Candidates

Install these only after explicit approval because they change system state:

- LibreOffice: `winget install --id TheDocumentFoundation.LibreOffice`
- Pandoc: `winget install --id JohnMacFarlane.Pandoc`
- Tesseract OCR: prefer the first verified route that completes on this
  machine. `winget install --id UB-Mannheim.TesseractOCR` and direct GitHub
  release downloads have stalled or failed here; `choco install tesseract -y`
  has been verified locally.
- qpdf: `winget install --id qpdf.qpdf`
- MuPDF / mutool: `choco install mupdf -y`
- ImageMagick: `winget install --id ImageMagick.ImageMagick`

After installation, rerun `scripts/check_office_tools.py`.

## Installation Source Strategy

- Inventory first: run `scripts/check_office_tools.py` and direct executable
  probes before installing anything.
- Install one missing tool at a time. Do not keep retrying the same source when
  the failure mode is network reachability, stalled download, or package-manager
  timeout.
- Prefer a verified package-manager route over a partially downloaded direct
  installer. Direct artifact URLs are useful for `probe-url`, but should not be
  treated as better than a local package-manager install unless the download,
  checksum/source identity, silent install, and command probe all succeed.
- Do not mutate user or system `PATH` just to make a new tool visible in the
  current shell. Record direct executable paths and let probes find them.
- If an installer or downloader is still running, observe and report it instead
  of killing it. Treat the tool as usable only after the command probe reports
  `ok`.

## Known Local Paths

- LibreOffice: `C:\\Program Files\\LibreOffice\\program\\soffice.exe`
- Pandoc: Winget package path under
  `C:\\Users\\45543\\AppData\\Local\\Microsoft\\WinGet\\Packages\\`
- Tesseract: standard installer path under `C:\\Program Files\\Tesseract-OCR\\`
- qpdf: `C:\\Program Files\\qpdf 12.3.2\\bin\\qpdf.exe`
- MuPDF / mutool: Chocolatey shim under
  `C:\\ProgramData\\chocolatey\\bin\\mutool.exe`
- ImageMagick: Winget package or `C:\\Program Files\\ImageMagick-*\\`
- Poppler: Winget package or a bin folder containing `pdftotext.exe`

On Windows, do not treat a missing PATH command as immediate install failure.
Winget packages may install under `Program Files`, `%LOCALAPPDATA%\Programs`, or
`%LOCALAPPDATA%\Microsoft\WinGet\Packages` before the current terminal sees a
PATH refresh. Probe the direct executable path first, then decide whether a new
terminal, explicit path invocation, or a separate install attempt is needed.

## Current Local Status Notes

- Python office stack is the stable baseline and should be used first.
- Pandoc, qpdf, pdftotext, MuPDF/mutool, ImageMagick, Tesseract, and
  LibreOffice have been observed as usable external tools on this machine.
- LibreOffice `--version` may time out on first probe, so
  `scripts/check_office_tools.py` falls back to a hidden headless HTML-to-PDF
  smoke conversion and reports `ok` when that succeeds. For conversion tasks,
  use `C:\\Program Files\\LibreOffice\\program\\soffice.exe` directly and
  validate the output file.
- Tesseract is verified through Chocolatey at
  `C:\\Program Files\\Tesseract-OCR\\tesseract.exe`
  (`tesseract v5.5.0.20241111`). Winget failed with `InternetOpenUrl`, and a
  direct GitHub release download was slow/partial, so do not prefer those routes
  for this workspace unless Chocolatey later fails.
- The partial direct Tesseract installer in the workspace installer cache is a
  leftover from the abandoned GitHub route. It is not capability evidence and
  can be cleaned separately if cache cleanup is requested.
- MuPDF is verified through Chocolatey as `mutool version 1.27.0`.

## Decision Rules

- For editable `.docx`: use `python-docx`; use `mammoth` only for extraction to
  HTML/Markdown-like content.
- For template reports: use `docxtpl`; keep the template separate from output.
- For Excel analysis: use `pandas` for computation, then `openpyxl` or
  `xlsxwriter` for workbook output.
- For chart-heavy workbooks: create charts with `xlsxwriter` or `openpyxl`;
  use `matplotlib` when the chart must also appear in Word/PDF/PPT.
- For searchable PDFs: use `pdfplumber` first, then `pypdf` for structural
  operations.
- For scanned PDFs: state that OCR is required, probe `tesseract`, and avoid
  pretending text extraction succeeded.
- For high-fidelity Office-to-PDF: prefer LibreOffice or the native Office GUI
  if installed; Python libraries cannot reliably preserve complex visual
  layouts.
