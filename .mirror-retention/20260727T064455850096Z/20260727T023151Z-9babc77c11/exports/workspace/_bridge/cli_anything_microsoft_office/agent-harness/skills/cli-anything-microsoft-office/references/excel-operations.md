# Excel Operations

## Model

- Use `Value2` semantics for raw values. Dates and currency may arrive as numeric serials; preserve intended number formats separately.
- Prefer `Formula2` for modern dynamic-array formulas and fall back to `Formula` for compatibility.
- Worksheet names and cell/range addresses are user data; inspect them before constructing operations.

## Operation Guidance

- `set_cell` writes one JSON scalar. Use `set_range` for rectangular matrices.
- `set_formula` should include the leading `=`. Verify the calculated value after reopening.
- `format_range` changes presentation, not stored values. Keep number formats explicit for dates, percentages, and currency.
- `sort_range`: include the full table range, use a key inside it, and set `header` correctly.
- `filter_range`: `field` is one-based relative to the filter range.
- `add_chart`: give every chart a stable unique name and verify its source range after sorting or sheet changes.
- `autofit` can create excessively wide columns for long text; inspect the rendered workbook or PDF for polished output.

## Calculation

Native Excel is the owner when formula recalculation matters. After edits, reopen with Excel, inspect sample values, and export only after calculation completes. Do not treat formula text alone as proof of a correct result.

## Official Sources

- https://learn.microsoft.com/office/vba/api/excel.range.value2
- https://learn.microsoft.com/office/vba/api/excel.range.formula2
- https://learn.microsoft.com/office/vba/api/excel.range.autofilter
- https://learn.microsoft.com/office/vba/api/excel.sort
- https://learn.microsoft.com/office/vba/api/excel.chartobjects.add
- https://learn.microsoft.com/office/vba/api/excel.workbook.exportasfixedformat
