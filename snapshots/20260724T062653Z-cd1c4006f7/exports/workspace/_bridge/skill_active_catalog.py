#!/usr/bin/env python3
"""Resolve the active Codex skill catalog without treating cache history as live.

Ownership: read the configured Codex plugin set and expose the user, system,
and current-plugin SKILL.md files shared by lifecycle and routing owners.
Non-goals: editing skills, installing/updating plugins, choosing task skills,
or mutating Codex configuration.
State behavior: read-only; callers may persist their own derived indexes.
Caller context: imported by skill_lifecycle_governance.py and
skill_orchestrator.py before auditing or routing skills.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CODEX_HOME = Path.home() / ".codex"
GLOBAL_SKILLS = CODEX_HOME / "skills"
SYSTEM_SKILLS = GLOBAL_SKILLS / ".system"
PLUGIN_CACHE = CODEX_HOME / "plugins" / "cache"
CONFIG_PATH = CODEX_HOME / "config.toml"
ARCHIVE_NAMES = {"_backups", ".backups", "backups", "backup", "archive", "archived"}
IGNORED_USER_TOP_LEVEL = {".system", ".disabled", *ARCHIVE_NAMES}
IGNORED_PLUGIN_MARKERS = ("plugin-backup-", "guard-staging", ".staging-")


@dataclass(frozen=True)
class PluginSpec:
    """One enabled plugin as declared in Codex configuration."""

    package: str
    marketplace: str

    @property
    def identifier(self) -> str:
        return f"{self.package}@{self.marketplace}"


def _is_noisy_plugin_path(path: Path) -> bool:
    return any(marker in part.lower() for part in path.parts for marker in IGNORED_PLUGIN_MARKERS)


def _read_config_status(config_path: Path) -> tuple[dict[str, Any], str]:
    if not config_path.is_file():
        return {}, "config_missing"
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    return (payload, "") if isinstance(payload, dict) else ({}, "config_root_not_object")


def _read_config(config_path: Path) -> dict[str, Any]:
    payload, _ = _read_config_status(config_path)
    return payload


def enabled_plugin_specs(config_path: Path = CONFIG_PATH) -> list[PluginSpec]:
    """Return the enabled plugin identities from ``config.toml`` only."""
    plugins = _read_config(config_path).get("plugins")
    if not isinstance(plugins, dict):
        return []
    specs: list[PluginSpec] = []
    for identifier, settings in plugins.items():
        if not isinstance(identifier, str) or "@" not in identifier:
            continue
        if isinstance(settings, dict) and settings.get("enabled") is False:
            continue
        package, marketplace = identifier.rsplit("@", 1)
        if package and marketplace:
            specs.append(PluginSpec(package=package, marketplace=marketplace))
    return sorted(set(specs), key=lambda item: item.identifier)


def _version_sort_key(path: Path) -> tuple[tuple[int, ...], str, int]:
    numeric = tuple(int(value) for value in re.findall(r"\d+", path.name))
    return numeric, path.name.lower(), path.stat().st_mtime_ns


def active_plugin_version_dir(plugin_root: Path) -> Path | None:
    """Pick one configured version, preferring an explicit ``latest`` cache."""
    if not plugin_root.is_dir() or _is_noisy_plugin_path(plugin_root):
        return None
    latest = plugin_root / "latest"
    if latest.is_dir() and not _is_noisy_plugin_path(latest):
        return latest
    candidates = [
        item
        for item in plugin_root.iterdir()
        if item.is_dir() and not _is_noisy_plugin_path(item)
    ]
    return max(candidates, key=_version_sort_key) if candidates else None


def discover_active_plugin_skill_files(
    *,
    plugin_cache: Path = PLUGIN_CACHE,
    config_path: Path = CONFIG_PATH,
) -> list[Path]:
    """Discover skills from configured plugin roots, excluding cache history."""
    paths: list[Path] = []
    for spec in enabled_plugin_specs(config_path):
        version_dir = active_plugin_version_dir(plugin_cache / spec.marketplace / spec.package)
        if version_dir is None:
            continue
        paths.extend(
            skill_file
            for skill_file in version_dir.rglob("SKILL.md")
            if not _is_noisy_plugin_path(skill_file)
        )
    return sorted(set(paths), key=lambda item: str(item).lower())


def discover_active_skill_files(
    *,
    global_skills: Path = GLOBAL_SKILLS,
    system_skills: Path = SYSTEM_SKILLS,
    plugin_cache: Path = PLUGIN_CACHE,
    config_path: Path = CONFIG_PATH,
) -> list[tuple[Path, str]]:
    """Return the source-of-truth active files for lifecycle and routing."""
    rows: list[tuple[Path, str]] = []
    if global_skills.is_dir():
        for directory in sorted(global_skills.iterdir(), key=lambda item: item.name.lower()):
            if not directory.is_dir() or directory.name.lower() in IGNORED_USER_TOP_LEVEL:
                continue
            skill_file = directory / "SKILL.md"
            if skill_file.is_file():
                rows.append((skill_file, "user"))
    if system_skills.is_dir():
        rows.extend((path, "system") for path in sorted(system_skills.glob("*/SKILL.md"), key=str))
    rows.extend(
        (path, "plugin")
        for path in discover_active_plugin_skill_files(plugin_cache=plugin_cache, config_path=config_path)
    )
    return rows


def catalog_snapshot(
    *,
    plugin_cache: Path = PLUGIN_CACHE,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    """Return bounded evidence about configured plugin skill discovery.

    Plugin installation and cache completeness belong to
    ``codex_plugin_config_health.py``. This catalog indexes only skill payloads
    that are currently resolvable and must not turn a configured app-only or
    retired plugin cache entry into a skill-lifecycle failure.
    """
    _, config_error = _read_config_status(config_path)
    specs = enabled_plugin_specs(config_path)
    roots = []
    for spec in specs:
        root = plugin_cache / spec.marketplace / spec.package
        version_dir = active_plugin_version_dir(root)
        roots.append(
            {
                "plugin": spec.identifier,
                "root": str(root),
                "selected_version": str(version_dir) if version_dir else "",
                "available": version_dir is not None,
            }
        )
    active_files = discover_active_plugin_skill_files(plugin_cache=plugin_cache, config_path=config_path)
    deferred_plugins = [item["plugin"] for item in roots if not item["available"]]
    return {
        "schema": "skill_active_catalog.snapshot.v1",
        "ok": not config_error,
        "config_path": str(config_path),
        "config_parse_error": config_error,
        "plugin_cache": str(plugin_cache),
        "enabled_plugin_count": len(specs),
        "configured_roots": roots,
        "deferred_plugins_without_local_skill_payload": deferred_plugins,
        "active_skill_count": len(active_files),
        "active_skill_paths": [str(path) for path in active_files],
        "plugin_health_authority": "codex_plugin_config_health.py",
        "exclusion_rule": "index only enabled plugins with one resolvable current skill payload; backup/staging paths are never active, and unresolved plugin cache health remains with the plugin health owner",
    }
