"""Ownership: publish immutable preview bundles from real Office PDF exports.

Non-goals: render documents independently or act as the preview consumer.
State behavior: writes immutable bundle directories and a small latest pointer.
Caller context: `preview capture` and `preview latest` CLI commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cli_anything.microsoft_office.core.documents import export_pdf
from cli_anything.microsoft_office.core.paths import app_for_path, existing_office_file


def recipes() -> dict[str, Any]:
    return {
        "ok": True,
        "recipes": [
            {
                "name": "office-pdf",
                "description": "Export the source through its real Microsoft Office application to PDF.",
                "artifacts": ["document-preview"],
            }
        ],
    }


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def capture(
    source: str | Path,
    *,
    output_root: str | Path | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    source_path = existing_office_file(source)
    app = app_for_path(source_path)
    sha256 = _fingerprint(source_path)
    root = Path(output_root).expanduser().resolve() if output_root else Path(tempfile.gettempdir()) / "cli-anything-microsoft-office" / "previews"
    project_root = root / f"{source_path.stem}-{sha256[:8]}"
    bundle_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + f"-{sha256[:8]}"
    bundle_dir = project_root / bundle_id
    artifacts_dir = bundle_dir / "artifacts"
    pdf_path = artifacts_dir / f"{source_path.stem}.pdf"
    result = export_pdf(app, source_path, pdf_path, overwrite=False, timeout=timeout)
    created_at = datetime.now(UTC).isoformat()
    manifest = {
        "schema": "preview-bundle/v1",
        "bundle_id": bundle_id,
        "created_at": created_at,
        "producer": "cli-anything-microsoft-office",
        "recipe": "office-pdf",
        "source": {"path": str(source_path), "sha256": sha256, "application": app},
        "artifacts": [
            {
                "role": "document-preview",
                "path": str(pdf_path),
                "media_type": "application/pdf",
                "backend": f"microsoft-{app}-com",
            }
        ],
    }
    summary = {
        "schema": "preview-summary/v1",
        "ok": True,
        "bundle_id": bundle_id,
        "source": str(source_path),
        "application": app,
        "artifact_count": 1,
        "output": str(pdf_path),
        "backend_result": result,
    }
    _write_json(bundle_dir / "manifest.json", manifest)
    _write_json(bundle_dir / "summary.json", summary)
    latest = {
        "schema": "preview-latest/v1",
        "bundle_id": bundle_id,
        "bundle_dir": str(bundle_dir),
        "manifest_path": str(bundle_dir / "manifest.json"),
        "summary_path": str(bundle_dir / "summary.json"),
        "source_sha256": sha256,
    }
    _write_json(project_root / "latest.json", latest)
    return {"ok": True, **latest, "artifact_path": str(pdf_path)}


def latest(source: str | Path, *, output_root: str | Path | None = None) -> dict[str, Any]:
    source_path = existing_office_file(source)
    sha256 = _fingerprint(source_path)
    root = Path(output_root).expanduser().resolve() if output_root else Path(tempfile.gettempdir()) / "cli-anything-microsoft-office" / "previews"
    latest_path = root / f"{source_path.stem}-{sha256[:8]}" / "latest.json"
    if not latest_path.is_file():
        raise ValueError(f"No preview bundle exists for the current source fingerprint: {source_path}")
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    return {"ok": True, **payload}

