---
name: ppocrv5
description: Route OCR tasks across the available local OCR, GUI, PDF, and office-document capabilities. Use for extracting text from images, screenshots, scanned PDFs, or visible desktop regions; use a PP-OCR API only when the user explicitly requires that provider and credentials are available.
metadata: {"codex":{"compatibility":"The historical bundled PP-OCRv5 API scripts are not present. This skill now provides a stable OCR routing contract instead of claiming missing executables."}}
---

# OCR Routing

## Route Selection

1. Existing local image: use the configured GUI/OCR owner for bounded recognition.
2. Visible desktop region or application: use `gui-automation` OCR inside the active GUI session.
3. Scanned PDF: use `office-craft` or `pdf` to render pages, then OCR the rendered images.
4. Structured Office document: extract native text before OCR; OCR is only a fallback for image-only content.
5. Remote image or PDF: acquire it through the resource layer first, then process the local artifact.
6. Explicit PP-OCR/Paddle provider request: require user-approved credentials and route the network call through the resource/network owner; never invent a local script path.

## Quality Rules

- Preserve reading order when possible.
- State language assumptions and low-confidence regions.
- For tables, forms, or invoices, return both extracted text and structural caveats.
- Never report OCR success from an empty or obviously incomplete result.

## Output Contract

- State the selected OCR route and source artifact.
- Return extracted text or the exact blocker.
- Mention confidence, layout loss, and pages or regions processed.
