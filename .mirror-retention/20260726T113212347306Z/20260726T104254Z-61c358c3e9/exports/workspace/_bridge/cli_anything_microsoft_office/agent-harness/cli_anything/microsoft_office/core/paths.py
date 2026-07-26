"""Ownership: validate Office input and output paths before COM invocation.

Non-goals: open files, modify Office state, or perform format conversion.
State behavior: pure validation except creating an approved output directory.
Caller context: CLI commands and preview publishing.
"""

from __future__ import annotations

from pathlib import Path


APP_BY_EXTENSION = {
    ".doc": "word",
    ".docx": "word",
    ".xls": "excel",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".ppt": "powerpoint",
    ".pptx": "powerpoint",
}


def existing_office_file(value: str | Path, *, expected_app: str | None = None) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Office file does not exist: {path}")
    app = APP_BY_EXTENSION.get(path.suffix.lower())
    if not app:
        raise ValueError(f"Unsupported Office extension: {path.suffix or '<none>'}")
    if expected_app and app != expected_app:
        raise ValueError(f"Expected a {expected_app} file, got {path.suffix}")
    return path


def output_file(
    value: str | Path,
    *,
    extension: str,
    overwrite: bool,
    dry_run: bool,
) -> Path:
    path = Path(value).expanduser().resolve()
    expected = extension.lower()
    if path.suffix.lower() != expected:
        raise ValueError(f"Output must use {expected}: {path}")
    if path.exists() and not overwrite:
        raise ValueError(f"Refusing to overwrite existing file without --overwrite: {path}")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def app_for_path(path: str | Path) -> str:
    resolved = existing_office_file(path)
    return APP_BY_EXTENSION[resolved.suffix.lower()]

