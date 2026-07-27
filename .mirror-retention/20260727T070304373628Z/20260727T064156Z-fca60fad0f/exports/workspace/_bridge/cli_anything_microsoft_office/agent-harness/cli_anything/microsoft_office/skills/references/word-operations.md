# Word Operations

## Model

- Prefer `Range` over `Selection`. A Range is bounded by character positions and does not change UI selection state.
- Word content includes a final paragraph mark. Insert before that mark when appending structured content; an unbounded `Paragraphs.Add()` can replace or reuse the final paragraph.
- Use built-in style identifiers when automation must work across localized Office installations. English names such as `Heading 2` may not exist in Chinese Office.

## Operation Guidance

- `replace_text` and `delete_text`: inspect the source phrase first; choose match-case and whole-word deliberately.
- `add_heading`: use levels 1-9 and verify the new paragraph remains separate from preceding text.
- `add_table`: supply a rectangular row matrix where possible; verify row and column counts after save.
- `format_text`: use a narrow find phrase. Broad repeated text may format more ranges than intended.
- `set_paragraph_format`: paragraph indexes can shift after insertions, so place index-sensitive operations after structural inserts only when indexes were recalculated.
- `set_page_setup`: values are points; 72 points equal one inch. Orientation is `portrait` or `landscape`.
- `set_header` and `set_footer`: sections are one-based. Headers and footers belong to sections, not the whole document globally.

## Verification

Inspect paragraphs, styles, tables, sections, and pages. For page layout or headers/footers, export through Word and inspect the PDF rather than relying only on OOXML.

## Official Sources

- https://learn.microsoft.com/office/vba/word/concepts/working-with-word/working-with-range-objects
- https://learn.microsoft.com/office/vba/api/word.find
- https://learn.microsoft.com/office/vba/api/word.tables.add
- https://learn.microsoft.com/office/vba/api/word.pagesetup
- https://learn.microsoft.com/office/vba/api/word.headersfooters
