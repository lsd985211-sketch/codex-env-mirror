#!/usr/bin/env python3
"""Shared Codex executable discovery for background owners.

Avoids pinning one Desktop build hash. Callers may pass an explicit configured
path, but stale paths are skipped in favor of currently installed candidates.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping


def _existing_file(value: Any) -> str:
    text = str(value or "").strip().strip('"')
    if not text:
        return ""
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    return str(path) if path.is_file() else ""


def _latest_desktop_bins(local_appdata: Path) -> list[str]:
    root = local_appdata / "OpenAI" / "Codex" / "bin"
    if not root.is_dir():
        return []
    candidates = [path for path in root.glob("*/codex.exe") if path.is_file()]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [str(path) for path in candidates]


def _baseline_candidate(path: Path | None) -> str:
    if not path or not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
    return str(env.get("CODEX_CLI_PATH") or "").strip()


def codex_executable_candidates(
    *,
    explicit_path: str = "",
    env: Mapping[str, str] | None = None,
    local_appdata: Path | None = None,
    startup_baseline: Path | None = None,
) -> list[dict[str, str]]:
    """Return ordered, de-duplicated candidates with their discovery source."""
    current_env = dict(os.environ if env is None else env)
    local_root = local_appdata or Path(current_env.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    rows: list[tuple[str, str]] = [
        ("explicit", explicit_path),
        ("env_CODEX_CLI_PATH", current_env.get("CODEX_CLI_PATH", "")),
    ]
    rows.extend(("desktop_latest_bin", value) for value in _latest_desktop_bins(local_root))
    rows.append(("windows_app_alias", str(local_root / "Microsoft" / "WindowsApps" / "codex.exe")))
    rows.append(("startup_baseline", _baseline_candidate(startup_baseline)))
    rows.extend(
        [
            ("path_codex_exe", shutil.which("codex.exe") or ""),
            ("path_codex", shutil.which("codex") or ""),
        ]
    )
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for source, value in rows:
        normalized = _existing_file(value)
        if not normalized:
            continue
        key = os.path.normcase(os.path.abspath(normalized))
        if key in seen:
            continue
        seen.add(key)
        result.append({"source": source, "path": normalized})
    return result


def discover_codex_executable(
    *,
    explicit_path: str = "",
    env: Mapping[str, str] | None = None,
    local_appdata: Path | None = None,
    startup_baseline: Path | None = None,
) -> str:
    candidates = codex_executable_candidates(
        explicit_path=explicit_path,
        env=env,
        local_appdata=local_appdata,
        startup_baseline=startup_baseline,
    )
    return candidates[0]["path"] if candidates else ""
