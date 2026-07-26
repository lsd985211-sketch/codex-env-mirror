# PowerPoint Operations

## Model

- Slides and Shapes are one-based collections. Shape names are more stable than positional indexes after edits.
- Check `HasTextFrame` and `HasText` before reading TextFrame/TextRange.
- PowerPoint `Presentations.Open` has an explicit read-only argument. Editing a copy must open it writable.
- Coordinates and sizes are points. Keep slide bounds in mind when adding or moving shapes.

## Operation Guidance

- `add_slide`: layout numbers are Office constants; use blank layout when no placeholder behavior is required.
- `set_slide_title`: a blank layout may not have a title placeholder, so the harness may create a text box.
- `add_textbox`, `add_shape`, `add_table`, and `add_image`: assign stable unique names when later updates are expected.
- `replace_text`: restrict to a slide index when possible. Case-insensitive replacement should be explicit.
- `update_shape` and `delete_shape`: inspect names first; duplicate or localized placeholder names can cause ambiguous edits.
- `set_background`: verify contrast with every text shape after changing fill color.

## Verification

Inspect slide and shape summaries after editing. Export through PowerPoint for layout validation; structural success does not detect clipped text, overlap, or poor contrast.

## Official Sources

- https://learn.microsoft.com/office/vba/api/powerpoint.slides
- https://learn.microsoft.com/office/vba/api/powerpoint.shapes
- https://learn.microsoft.com/office/vba/api/powerpoint.textframe.textrange
- https://learn.microsoft.com/office/vba/api/powerpoint.shapes.addpicture
- https://learn.microsoft.com/office/vba/api/powerpoint.shapes.addtable
- https://learn.microsoft.com/office/vba/api/powerpoint.presentation.saveas
