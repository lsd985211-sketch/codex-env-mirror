#!/usr/bin/env python3
"""Read-only encoding and mojibake governance for the local Codex workspace."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "_bridge"
CODEX_HOME = Path.home() / ".codex"
RESOURCE_ROOT = Path.home() / "Desktop" / "Codex资源库"

SCAN_TARGETS = [
    {"path": Path.home() / "Desktop", "depth": 1},
    {"path": RESOURCE_ROOT, "depth": 4},
    {"path": ROOT / "AGENTS.md", "depth": 0},
    {"path": BRIDGE_ROOT, "depth": 2},
    {"path": CODEX_HOME / "AGENTS.md", "depth": 0},
    {"path": CODEX_HOME / "config.toml", "depth": 0},
    {"path": CODEX_HOME / "skills", "depth": 2},
]
MAX_PATHS = 5000

EXCLUDED_PARTS = {
    ".git",
    "node_modules",
    "runtime",
    "logs",
    "backups",
    "_backup",
    "archived_sessions",
    "sessions",
    "dashboard-browser-profile",
    "dashboard-chrome-profile",
    "login-runs",
    "attachments",
    "执行记录",
    "__pycache__",
}

TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".py",
    ".ps1",
    ".cmd",
    ".bat",
    ".js",
    ".mjs",
    ".ts",
    ".html",
    ".css",
}

MOJIBAKE_PATTERNS = [
    "�",
    "璧勬簮",
    "搴�",
    "搴揬",
    "锟",
]

POWERSHELL_WRITER_RE = re.compile(r"\b(Set-Content|Out-File|Add-Content)\b", re.IGNORECASE)
POWERSHELL_ENCODING_RE = re.compile(r"(^|\s)-Encoding(\s|$)", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def is_excluded(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return bool(parts.intersection(EXCLUDED_PARTS))


def has_mojibake(text: str) -> bool:
    return any(pattern in text for pattern in MOJIBAKE_PATTERNS)


def safe_read_bytes(path: Path, limit: int = 2 * 1024 * 1024) -> bytes | None:
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_bytes()
    except OSError:
        return None


def decode_utf8_status(path: Path) -> dict[str, Any]:
    data = safe_read_bytes(path)
    if data is None:
        return {"checked": False, "reason": "too_large_or_unreadable"}
    bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {
            "checked": True,
            "ok": False,
            "bom": bom,
            "error": str(exc),
        }
    return {
        "checked": True,
        "ok": True,
        "bom": bom,
        "mojibake_in_content": has_mojibake(text),
    }


def within_depth(root: Path, path: Path, max_depth: int) -> bool:
    if max_depth <= 0:
        return path == root
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    return len(rel.parts) <= max_depth


def iter_scan_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for target in SCAN_TARGETS:
        root = target["path"]
        max_depth = int(target["depth"])
        if not root.exists():
            continue
        if root.is_file():
            key = str(root.resolve())
            if key not in seen:
                paths.append(root)
                seen.add(key)
            continue
        for current_root, dirnames, filenames in os.walk(root):
            current = Path(current_root)
            dirnames[:] = [name for name in dirnames if name.lower() not in EXCLUDED_PARTS]
            if is_excluded(current) or not within_depth(root, current, max_depth):
                dirnames[:] = []
                continue
            for name in dirnames:
                path = current / name
                if within_depth(root, path, max_depth):
                    key = str(path.resolve())
                    if key not in seen:
                        paths.append(path)
                        seen.add(key)
            for name in filenames:
                path = current / name
                if within_depth(root, path, max_depth):
                    key = str(path.resolve())
                    if key not in seen:
                        paths.append(path)
                        seen.add(key)
            if len(paths) >= MAX_PATHS:
                return paths[:MAX_PATHS]
    return paths


def powershell_encoding_findings(path: Path, text: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if path.suffix.lower() != ".ps1":
        return findings
    for lineno, line in enumerate(text.splitlines(), start=1):
        if POWERSHELL_WRITER_RE.search(line) and not POWERSHELL_ENCODING_RE.search(line):
            findings.append(
                {
                    "path": str(path),
                    "line": lineno,
                    "code": "powershell_writer_missing_encoding",
                    "preview": line.strip()[:300],
                }
            )
    return findings


def snapshot() -> dict[str, Any]:
    paths = iter_scan_paths()
    mojibake_paths: list[dict[str, Any]] = []
    text_issues: list[dict[str, Any]] = []
    ps_writer_issues: list[dict[str, Any]] = []
    checked_text_files = 0
    utf8_bom_files = 0
    decode_error_files = 0
    content_mojibake_files = 0

    for path in paths:
        path_text = str(path)
        if has_mojibake(path.name) or has_mojibake(path_text):
            mojibake_paths.append(
                {
                    "path": path_text,
                    "name": path.name,
                    "is_dir": path.is_dir(),
                    "exists": path.exists(),
                }
            )
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and not is_excluded(path):
            status = decode_utf8_status(path)
            if status.get("checked"):
                checked_text_files += 1
                if status.get("bom"):
                    utf8_bom_files += 1
                    text_issues.append({"path": path_text, "code": "utf8_bom_present", "detail": status})
                if status.get("ok") is False:
                    decode_error_files += 1
                    text_issues.append({"path": path_text, "code": "utf8_decode_error", "detail": status})
                if status.get("mojibake_in_content"):
                    content_mojibake_files += 1
                    text_issues.append({"path": path_text, "code": "mojibake_content_marker", "detail": status})
                if path.suffix.lower() == ".ps1" and status.get("ok"):
                    data = safe_read_bytes(path)
                    if data is not None:
                        ps_writer_issues.extend(powershell_encoding_findings(path, data.decode("utf-8", errors="replace")))

    env_status = {
        "PYTHONUTF8": os.environ.get("PYTHONUTF8", ""),
        "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING", ""),
        "PYTHONUTF8_ok": os.environ.get("PYTHONUTF8", "") == "1",
        "PYTHONIOENCODING_ok": os.environ.get("PYTHONIOENCODING", "").lower().startswith("utf-8"),
    }

    return {
        "schema": "encoding-governance.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "scan_targets": [{"path": str(item["path"]), "depth": item["depth"]} for item in SCAN_TARGETS],
        "excluded_parts": sorted(EXCLUDED_PARTS),
        "resource_root": {
            "path": str(RESOURCE_ROOT),
            "exists": RESOURCE_ROOT.exists(),
        },
        "environment": env_status,
        "summary": {
            "path_count": len(paths),
            "path_limit": MAX_PATHS,
            "path_limit_reached": len(paths) >= MAX_PATHS,
            "checked_text_files": checked_text_files,
            "mojibake_path_count": len(mojibake_paths),
            "utf8_bom_file_count": utf8_bom_files,
            "utf8_decode_error_count": decode_error_files,
            "content_mojibake_file_count": content_mojibake_files,
            "powershell_writer_missing_encoding_count": len(ps_writer_issues),
        },
        "mojibake_paths": mojibake_paths[:200],
        "text_issues": text_issues[:300],
        "powershell_writer_issues": ps_writer_issues[:200],
        "dry_run_contract": {
            "writes_files": False,
            "renames_paths": False,
            "deletes_paths": False,
            "changes_environment": False,
        },
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    issues: list[dict[str, Any]] = []
    summary = snap.get("summary") if isinstance(snap.get("summary"), dict) else {}
    env = snap.get("environment") if isinstance(snap.get("environment"), dict) else {}
    if int(summary.get("mojibake_path_count") or 0) > 0:
        issues.append(
            {
                "severity": "risk",
                "code": "mojibake_path_detected",
                "message": "Malformed or mojibake path names were detected in managed scan roots.",
                "items": snap.get("mojibake_paths", []),
            }
        )
    if int(summary.get("utf8_decode_error_count") or 0) > 0:
        issues.append(
            {
                "severity": "risk",
                "code": "utf8_decode_error",
                "message": "Text files could not be decoded as UTF-8.",
                "items": [item for item in snap.get("text_issues", []) if item.get("code") == "utf8_decode_error"],
            }
        )
    if int(summary.get("content_mojibake_file_count") or 0) > 0:
        issues.append(
            {
                "severity": "advisory",
                "code": "mojibake_content_marker",
                "message": "Mojibake markers were found inside text files; review before changing.",
                "items": [item for item in snap.get("text_issues", []) if item.get("code") == "mojibake_content_marker"],
            }
        )
    if int(summary.get("utf8_bom_file_count") or 0) > 0:
        issues.append(
            {
                "severity": "advisory",
                "code": "utf8_bom_present",
                "message": "UTF-8 BOM was found. Config files should generally stay UTF-8 without BOM unless a Windows script requires BOM.",
                "items": [item for item in snap.get("text_issues", []) if item.get("code") == "utf8_bom_present"],
            }
        )
    if int(summary.get("powershell_writer_missing_encoding_count") or 0) > 0:
        issues.append(
            {
                "severity": "advisory",
                "code": "powershell_writer_missing_encoding",
                "message": "PowerShell writers without explicit -Encoding were found.",
                "items": snap.get("powershell_writer_issues", []),
            }
        )
    if env.get("PYTHONUTF8_ok") is False or env.get("PYTHONIOENCODING_ok") is False:
        issues.append(
            {
                "severity": "advisory",
                "code": "python_utf8_environment_not_forced",
                "message": "Current process does not have both PYTHONUTF8=1 and PYTHONIOENCODING=utf-8.",
                "environment": env,
            }
        )
    return {
        "schema": "encoding-governance.doctor.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": {
            **summary,
            "issue_count": len(issues),
            "risk_count": sum(1 for item in issues if item.get("severity") == "risk"),
            "advisory_count": sum(1 for item in issues if item.get("severity") == "advisory"),
        },
        "snapshot": snap,
    }


def repair_plan(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = doctor(snap)
    actions: list[dict[str, Any]] = []
    for issue in doc.get("issues", []):
        code = str(issue.get("code") or "")
        action: dict[str, Any] = {
            "id": f"review_{code}",
            "source_issue": issue,
            "dry_run_only": True,
            "auto_apply": False,
        }
        if code == "mojibake_path_detected":
            action["recommended_flow"] = [
                "compare malformed path contents with the intended canonical path",
                "backup the malformed path into _bridge/backups with a README",
                "migrate unique data only if proven missing from canonical location",
                "delete or rename only after user approval",
            ]
        elif code == "utf8_decode_error":
            action["recommended_flow"] = [
                "identify the original encoding with a bounded sample",
                "create a backup",
                "convert using a structured tool, not blind replacement",
            ]
        elif code == "powershell_writer_missing_encoding":
            action["recommended_flow"] = [
                "add explicit -Encoding UTF8 to PowerShell text writers",
                "prefer Python pathlib write_text(..., encoding='utf-8') for generated JSON/Markdown",
            ]
        else:
            action["recommended_flow"] = ["review manually; no automatic mutation is allowed by encoding governance"]
        actions.append(action)
    return {
        "schema": "encoding-governance.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "doctor_ok": doc.get("ok"),
        "action_count": len(actions),
        "actions": actions,
        "contract": {
            "auto_rename": False,
            "auto_delete": False,
            "auto_convert": False,
            "requires_backup_before_mutation": True,
            "requires_user_approval_before_mutation": True,
        },
    }


def validate(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = doctor(snap)
    failures = [item for item in doc.get("issues", []) if item.get("severity") == "risk"]
    return {
        "schema": "encoding-governance.validate.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "failures": failures,
        "advisory_count": sum(1 for item in doc.get("issues", []) if item.get("severity") == "advisory"),
    }


def metrics(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    summary = snap.get("summary") if isinstance(snap.get("summary"), dict) else {}
    return {
        "schema": "encoding-governance.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        **summary,
    }


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Read-only encoding/mojibake governance")
    parser.add_argument("command", choices=["snapshot", "doctor", "repair-plan", "validate", "metrics"])
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "repair-plan":
        payload = repair_plan()
    elif args.command == "validate":
        payload = validate()
    else:
        payload = metrics()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
