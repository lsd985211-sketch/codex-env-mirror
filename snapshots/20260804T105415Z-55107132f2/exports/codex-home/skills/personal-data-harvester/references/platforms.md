# Personal Data Source Reference

Use this reference to choose an authorized intake method. Paths, export formats, APIs, and application schemas are drift-prone; verify them against the installed application or official platform instructions before importing.

## General Priority

| Source | Preferred method | Fallback |
|---|---|---|
| Account history or favorites | Official account export | User-visible browser-assisted capture |
| Reading annotations | Exported notes, CSV, HTML, or text | User-selected local application files |
| Video or social collections | Official export or API | Manual link/share intake |
| Local e-book highlights | User-provided clipping/export file | Read-only inspection of an approved local database |
| RSS-compatible sources | RSS/Atom feed | Approved page discovery through the resource layer |

## Platform Notes

### Douban

Prefer the account's official personal-data export. If the user requests browser assistance, navigate visibly in the user's authenticated session and collect only the requested pages. Stop on captcha, access denial, or ambiguous ownership.

### Bilibili

Prefer official data/export surfaces and documented APIs available to the authenticated user. Do not extract or persist session cookies. Browser network inspection requires explicit user approval and must remain read-only.

### Xiaohongshu, Douyin, Weibo

Prefer official export, manual share links, and user-supplied files. Do not conceal automation, patch browser fingerprints, automate captcha solving, or circumvent platform controls.

### WeRead

Prefer official note/book exports. A local database may be inspected read-only only after the user identifies and approves the exact file. Discover tables and columns before querying; do not assume a macOS path or fixed schema.

### Kindle

Accept user-provided `My Clippings.txt`, notebook exports, or supported account exports. Preserve the source encoding and maintain a raw reference for each parsed highlight.

### Browser-Assisted Intake

Use the existing authenticated browser surface when it is the only reasonable authorized route. Keep the browser visible when user interaction is required. Do not copy cookies, credentials, or protected tokens into scripts or databases.

## Verification Checklist

- Confirm the user owns or is authorized to process the account/data.
- Record the source version or export date.
- Inspect a small sample before full import.
- Validate encoding and stable identifiers.
- Use read-only access for source databases.
- Preserve raw files and hashes when reproducibility matters.
- Report unsupported or blocked sources rather than substituting invasive scraping.
