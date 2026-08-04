#!/usr/bin/env python3
"""Repair narrow Windows aliases for Desktop-owned WSL mount paths.

Ownership:
  ``codex_state_repair.py`` owns this Windows host compatibility helper.

Non-goals:
  It does not rewrite Codex configuration, replace existing directories, or
  mirror the Windows profile into ``C:\\mnt``.

State behavior:
  Read-only by default. Apply creates only missing directory junctions whose
  targets already exist; any conflicting path blocks without replacement.

Caller context:
  The Windows config guard calls this while Desktop WSL mode has emitted local
  marketplace paths such as ``/mnt/c/Users/...`` into the host config.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Callable


WINDOWS_MOUNT_SOURCE = re.compile(r"^/mnt/(?P<drive>[A-Za-z])(?:/|$)")


def configured_windows_mount_sources(config_path: Path) -> list[str]:
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    marketplaces = config.get("marketplaces")
    if not isinstance(marketplaces, dict):
        return []
    return sorted({
        source
        for settings in marketplaces.values()
        if isinstance(settings, dict)
        and str(settings.get("source_type") or "local").casefold() == "local"
        and (source := str(settings.get("source") or "").strip())
        and WINDOWS_MOUNT_SOURCE.match(source)
    })


def default_alias_pairs(user_profile: Path) -> list[tuple[Path, Path]]:
    match = re.match(r"^(?P<drive>[A-Za-z]):[\\/](?P<rest>.*)$", str(user_profile))
    if not match:
        return []
    drive = match.group("drive")
    rest = Path(*[part for part in re.split(r"[\\/]", match.group("rest")) if part])
    mounted_profile = Path(f"{drive}:\\mnt\\{drive.casefold()}") / rest
    return [
        (mounted_profile / ".codex" / ".tmp", user_profile / ".codex" / ".tmp"),
        (
            mounted_profile / ".codex" / "plugins" / "cache",
            user_profile / ".codex" / "plugins" / "cache",
        ),
        (
            mounted_profile / ".cache" / "codex-runtimes",
            user_profile / ".cache" / "codex-runtimes",
        ),
    ]


def _is_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(callable(is_junction) and is_junction())


def alias_status(alias: Path, target: Path) -> dict[str, object]:
    if _is_link(alias):
        try:
            current = alias.resolve(strict=True)
            expected = target.resolve(strict=True)
        except OSError:
            return {"ok": False, "status": "broken_alias", "alias": str(alias), "target": str(target)}
        return {
            "ok": current == expected,
            "status": "current" if current == expected else "alias_target_conflict",
            "alias": str(alias),
            "target": str(target),
            "actual_target": str(current),
        }
    if alias.exists():
        return {"ok": False, "status": "existing_path_conflict", "alias": str(alias), "target": str(target)}
    if not target.is_dir():
        return {"ok": False, "status": "target_missing", "alias": str(alias), "target": str(target)}
    return {"ok": True, "status": "would_create", "alias": str(alias), "target": str(target)}


def _create_junction(alias: Path, target: Path) -> None:
    completed = subprocess.run(
        [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", "mklink", "/J", str(alias), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "mklink failed").strip()
        raise OSError(detail[-1000:])


def reconcile(
    *,
    config_path: Path,
    apply: bool,
    platform_name: str = os.name,
    user_profile: Path | None = None,
    alias_pairs: list[tuple[Path, Path]] | None = None,
    create_junction: Callable[[Path, Path], None] = _create_junction,
) -> dict[str, object]:
    sources = configured_windows_mount_sources(config_path)
    if platform_name != "nt":
        return {"ok": True, "status": "not_windows_host", "changed": False, "sources": sources, "aliases": []}
    if not sources:
        return {"ok": True, "status": "not_required", "changed": False, "sources": [], "aliases": []}
    profile = user_profile or Path(os.environ.get("USERPROFILE") or Path.home())
    pairs = alias_pairs if alias_pairs is not None else default_alias_pairs(profile)
    before = [alias_status(alias, target) for alias, target in pairs]
    blockers = [row for row in before if not bool(row.get("ok"))]
    if blockers or not apply:
        return {
            "ok": not blockers and bool(pairs),
            "status": "blocked" if blockers else "would_create" if any(row["status"] == "would_create" for row in before) else "current",
            "changed": False,
            "sources": sources,
            "aliases": before,
        }
    changed = False
    try:
        for alias, target in pairs:
            row = alias_status(alias, target)
            if row["status"] != "would_create":
                continue
            alias.parent.mkdir(parents=True, exist_ok=True)
            create_junction(alias, target)
            changed = True
    except Exception as exc:
        after = [alias_status(alias, target) for alias, target in pairs]
        return {
            "ok": False,
            "status": "apply_failed",
            "changed": changed,
            "sources": sources,
            "aliases": after,
            "error": type(exc).__name__,
            "detail": str(exc)[:1000],
        }
    after = [alias_status(alias, target) for alias, target in pairs]
    return {
        "ok": all(bool(row.get("ok")) and row.get("status") == "current" for row in after),
        "status": "applied" if changed else "current",
        "changed": changed,
        "sources": sources,
        "aliases": after,
    }
