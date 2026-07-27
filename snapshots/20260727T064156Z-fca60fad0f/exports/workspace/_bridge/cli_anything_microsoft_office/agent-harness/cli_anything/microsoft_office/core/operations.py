"""Validate bounded, versioned Office edit operation batches.

Ownership: operation names, accepted fields, basic types, and batch limits.
Non-goals: execute Office, expose COM members, or infer document structure.
State behavior: pure validation only.
Caller context: CLI parsing and document edit core functions.
"""

from __future__ import annotations

from typing import Any


SCHEMA = "microsoft_office.operations.v1"
MAX_OPERATIONS = 100
MAX_TEXT = 200_000


def _spec(required: str = "", optional: str = "") -> tuple[set[str], set[str]]:
    return set(required.split()) if required else set(), set(optional.split()) if optional else set()


SCHEMAS: dict[str, dict[str, tuple[set[str], set[str]]]] = {
    "word": {
        "replace_text": _spec("find replace", "match_case whole_word"),
        "delete_text": _spec("find", "match_case whole_word"),
        "append_text": _spec("text"),
        "insert_paragraph": _spec("text", "index"),
        "add_heading": _spec("text", "level"),
        "add_page_break": _spec("", "index"),
        "add_table": _spec("rows", "index style"),
        "format_text": _spec("find", "bold italic underline font_size font_name color match_case"),
        "set_paragraph_format": _spec("index", "alignment space_before space_after line_spacing"),
        "set_page_setup": _spec("", "top_margin bottom_margin left_margin right_margin orientation"),
        "set_header": _spec("text", "section"),
        "set_footer": _spec("text", "section"),
        "set_property": _spec("name value"),
    },
    "excel": {
        "add_sheet": _spec("name", "after"),
        "delete_sheet": _spec("sheet"),
        "rename_sheet": _spec("sheet name"),
        "set_cell": _spec("sheet cell value"),
        "set_range": _spec("sheet range values"),
        "set_formula": _spec("sheet range formula"),
        "clear_range": _spec("sheet range"),
        "format_range": _spec("sheet range", "bold italic font_size font_name number_format fill_color font_color horizontal_alignment"),
        "merge_range": _spec("sheet range"),
        "unmerge_range": _spec("sheet range"),
        "autofit": _spec("sheet range"),
        "sort_range": _spec("sheet range key", "descending header"),
        "filter_range": _spec("sheet range field", "criteria"),
        "add_chart": _spec("sheet source_range name", "chart_type left top width height title"),
        "delete_chart": _spec("sheet name"),
        "set_property": _spec("name value"),
    },
    "powerpoint": {
        "add_slide": _spec("", "index layout title"),
        "delete_slide": _spec("index"),
        "move_slide": _spec("index to"),
        "set_slide_title": _spec("index text"),
        "add_textbox": _spec("index text left top width height", "name font_size bold color"),
        "replace_text": _spec("find replace", "index match_case"),
        "add_image": _spec("index path left top width height", "name"),
        "add_table": _spec("index rows left top width height", "name"),
        "add_shape": _spec("index shape_type left top width height", "name text fill_color line_color"),
        "update_shape": _spec("index name", "text left top width height fill_color line_color"),
        "delete_shape": _spec("index name"),
        "set_background": _spec("index color"),
        "set_property": _spec("name value"),
    },
}


def _validate_value(key: str, value: Any) -> None:
    if key in {"rows", "values"}:
        if not isinstance(value, list) or any(not isinstance(row, list) for row in value):
            raise ValueError(f"{key} must be a two-dimensional JSON array")
        return
    if key in {"index", "level", "section", "after", "to", "field", "layout", "shape_type", "chart_type"}:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
        return
    if key in {"left", "top", "width", "height", "font_size", "space_before", "space_after", "line_spacing", "top_margin", "bottom_margin", "left_margin", "right_margin"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        return
    if key in {"bold", "italic", "underline", "match_case", "whole_word", "descending", "header"}:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
        return
    if key == "value":
        if isinstance(value, (dict, list)):
            raise ValueError("value must be a JSON scalar")
        return
    enums = {
        "alignment": {"left", "center", "right", "justify"},
        "horizontal_alignment": {"left", "center", "right"},
        "orientation": {"portrait", "landscape"},
    }
    if key in enums:
        if not isinstance(value, str) or value not in enums[key]:
            raise ValueError(f"{key} must be one of: {', '.join(sorted(enums[key]))}")
        return
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    if len(value) > MAX_TEXT:
        raise ValueError(f"{key} exceeds {MAX_TEXT} characters")


def normalize_operations(app: str, value: Any) -> list[dict[str, Any]]:
    schemas = SCHEMAS.get(app)
    if not schemas:
        raise ValueError(f"Unsupported Office application: {app}")
    if not isinstance(value, list) or not value:
        raise ValueError("operations must be a non-empty JSON array")
    if len(value) > MAX_OPERATIONS:
        raise ValueError(f"operations exceeds the limit of {MAX_OPERATIONS}")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise ValueError(f"operations[{index}] must be an object")
        op = raw.get("op")
        if not isinstance(op, str) or op not in schemas:
            raise ValueError(f"operations[{index}].op is unsupported for {app}: {op!r}")
        required, optional = schemas[op]
        allowed = {"op"} | required | optional
        unknown = sorted(set(raw) - allowed)
        missing = sorted(key for key in required if key not in raw)
        if unknown:
            raise ValueError(f"operations[{index}] has unknown fields: {', '.join(unknown)}")
        if missing:
            raise ValueError(f"operations[{index}] is missing fields: {', '.join(missing)}")
        item = {"op": op}
        for key, field_value in raw.items():
            if key == "op":
                continue
            _validate_value(key, field_value)
            item[key] = field_value
        normalized.append(item)
    return normalized


def describe_operations(app: str) -> dict[str, Any]:
    schemas = SCHEMAS.get(app)
    if not schemas:
        raise ValueError(f"Unsupported Office application: {app}")
    return {
        "schema": SCHEMA,
        "application": app,
        "max_operations": MAX_OPERATIONS,
        "operations": {
            name: {"required": sorted(required), "optional": sorted(optional)}
            for name, (required, optional) in schemas.items()
        },
    }
