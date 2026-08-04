"""Ownership: structured Word, Excel, and PowerPoint operations via real Office.

Non-goals: parse OOXML directly, run VBA, or hold long-lived Office sessions.
State behavior: writes only explicit output files after path validation.
Caller context: Click commands, preview capture, and E2E tests.
"""

from __future__ import annotations

from pathlib import Path
import uuid
from typing import Any

from cli_anything.microsoft_office.core.paths import existing_office_file, output_file
from cli_anything.microsoft_office.core.operations import describe_operations, normalize_operations
from cli_anything.microsoft_office.utils.backend import invoke


def system_status(*, timeout: float = 30.0) -> dict[str, Any]:
    return invoke("system.status", timeout=timeout)


def create_word(
    output: str | Path,
    *,
    title: str = "",
    body: str = "",
    overwrite: bool = False,
    dry_run: bool = False,
    timeout: float = 120.0,
) -> dict[str, Any]:
    path = output_file(output, extension=".docx", overwrite=overwrite, dry_run=dry_run)
    if dry_run:
        return {"ok": True, "dry_run": True, "action": "word.create", "output": str(path)}
    return invoke("word.create", {"output": str(path), "title": title, "body": body}, timeout=timeout)


def word_info(path: str | Path, *, timeout: float = 120.0) -> dict[str, Any]:
    source = existing_office_file(path, expected_app="word")
    return invoke("word.info", {"path": str(source)}, timeout=timeout)


def create_excel(
    output: str | Path,
    *,
    sheet: str = "Sheet1",
    rows: list[list[Any]] | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    timeout: float = 120.0,
) -> dict[str, Any]:
    path = output_file(output, extension=".xlsx", overwrite=overwrite, dry_run=dry_run)
    if dry_run:
        return {"ok": True, "dry_run": True, "action": "excel.create", "output": str(path), "row_count": len(rows or [])}
    return invoke("excel.create", {"output": str(path), "sheet": sheet, "rows": rows or []}, timeout=timeout)


def excel_info(path: str | Path, *, timeout: float = 120.0) -> dict[str, Any]:
    source = existing_office_file(path, expected_app="excel")
    return invoke("excel.info", {"path": str(source)}, timeout=timeout)


def create_powerpoint(
    output: str | Path,
    *,
    title: str = "",
    subtitle: str = "",
    overwrite: bool = False,
    dry_run: bool = False,
    timeout: float = 120.0,
) -> dict[str, Any]:
    path = output_file(output, extension=".pptx", overwrite=overwrite, dry_run=dry_run)
    if dry_run:
        return {"ok": True, "dry_run": True, "action": "powerpoint.create", "output": str(path)}
    return invoke("powerpoint.create", {"output": str(path), "title": title, "subtitle": subtitle}, timeout=timeout)


def powerpoint_info(path: str | Path, *, timeout: float = 120.0) -> dict[str, Any]:
    source = existing_office_file(path, expected_app="powerpoint")
    return invoke("powerpoint.info", {"path": str(source)}, timeout=timeout)


def inspect(app: str, path: str | Path, *, timeout: float = 120.0) -> dict[str, Any]:
    source = existing_office_file(path, expected_app=app)
    return invoke(f"{app}.inspect", {"path": str(source)}, timeout=timeout)


def edit(
    app: str,
    source: str | Path,
    output: str | Path,
    operations: list[dict[str, Any]],
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    timeout: float = 180.0,
) -> dict[str, Any]:
    source_path = existing_office_file(source, expected_app=app)
    extension = {"word": ".docx", "excel": ".xlsx", "powerpoint": ".pptx"}[app]
    output_path = output_file(output, extension=extension, overwrite=overwrite, dry_run=dry_run)
    if source_path == output_path:
        raise ValueError("Edit output must differ from the source file")
    normalized = normalize_operations(app, operations)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": f"{app}.edit",
            "source": str(source_path),
            "output": str(output_path),
            "operation_count": len(normalized),
            "operations": [item["op"] for item in normalized],
        }
    temporary = output_path.with_name(f".{output_path.stem}.cli-anything-{uuid.uuid4().hex}{output_path.suffix}")
    return invoke(
        f"{app}.edit",
        {
            "path": str(source_path),
            "output": str(output_path),
            "temporary": str(temporary),
            "operations": normalized,
        },
        timeout=timeout,
    )


def operation_schema(app: str) -> dict[str, Any]:
    return {"ok": True, "action": f"{app}.operations", **describe_operations(app)}


def export_pdf(
    app: str,
    source: str | Path,
    output: str | Path,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    timeout: float = 180.0,
) -> dict[str, Any]:
    source_path = existing_office_file(source, expected_app=app)
    output_path = output_file(output, extension=".pdf", overwrite=overwrite, dry_run=dry_run)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "action": f"{app}.export_pdf",
            "source": str(source_path),
            "output": str(output_path),
        }
    return invoke(
        f"{app}.export_pdf",
        {"path": str(source_path), "output": str(output_path)},
        timeout=timeout,
    )
