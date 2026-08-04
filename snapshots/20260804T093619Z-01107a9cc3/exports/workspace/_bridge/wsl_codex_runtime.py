#!/usr/bin/env python3
"""Materialize the isolated, Linux-facing Codex runtime for Codex-Wsl-Lab.

The work Git owns templates and active capability files. The WSL home owns
credentials and databases. Windows session files are imported into an isolated
WSL projection whose working directories are translated without mutating the
Windows source or importing the rest of the Windows runtime state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from codex_desktop_environment_selection import (
    DEFAULT_HOST_CONFIG,
    DEFAULT_HOST_STATE_PATH,
    atomic_write_text,
    reconcile_environment_selection,
)
from shared.backup_router import create_backup
import codex_state_repair
import state_write_authority


ROOT = Path(__file__).resolve().parents[2]
CODEX_HOME = Path(os.environ.get("WSL_CODEX_HOME", str(Path.home() / ".codex-app"))).expanduser().resolve()
SQLITE_HOME = Path(os.environ.get("CODEX_SQLITE_HOME", str(CODEX_HOME))).expanduser().resolve()
CANONICAL_SKILLS_ROOT = Path.home() / ".codex" / "skills"
WORK_GIT_SKILLS = ROOT / "codex-home" / "skills"
TEMPLATE = ROOT / "codex-home" / "config.wsl.template.toml"
HOOKS_TEMPLATE = ROOT / "codex-home" / "hooks.wsl.template.json"
NODE_WRAPPER = ROOT / "workspace" / "_bridge" / "codex_node_repl_wsl.sh"
NODE_PROXY = ROOT / "workspace" / "_bridge" / "codex_node_repl_wsl_proxy.py"
NODE_ENTRY = Path.home() / ".local" / "bin" / "codex-node-repl"
RUNTIME_ROOT = ROOT / "workspace" / "_bridge" / "runtime" / "wsl_codex"
WINDOWS_SESSIONS = Path("/mnt/c/Users/45543/.codex/sessions")
WINDOWS_STATE_DB = Path(
    os.environ.get("CODEX_WINDOWS_STATE_SNAPSHOT")
    or "/mnt/c/Users/45543/.codex/state/wsl-projection/state_5.sqlite"
)
WINDOWS_CODEX_HOME = Path("/mnt/c/Users/45543/.codex")
SESSION_MANIFEST = CODEX_HOME / "session-projection-manifest.json"
PLUGIN_MANIFEST = CODEX_HOME / "plugin-projection-manifest.json"
SESSION_TRANSITION_ROOT = CODEX_HOME / ".session-projection-transition"
STATE_DB = SQLITE_HOME / "state_5.sqlite"
DRIVE_OVERRIDES = {"w": ROOT}
PORTABLE_CONFIG_ROOTS = ("personality",)
SAFE_INSERT_SOURCE_FIELDS = frozenset({
    "id",
    "rollout_path",
    "created_at",
    "updated_at",
    "source",
    "model_provider",
    "cwd",
    "title",
    "tokens_used",
    "has_user_event",
    "archived",
    "archived_at",
    "cli_version",
    "first_user_message",
    "agent_nickname",
    "agent_role",
    "memory_mode",
    "model",
    "reasoning_effort",
    "created_at_ms",
    "updated_at_ms",
    "thread_source",
    "preview",
    "recency_at",
    "recency_at_ms",
    "history_mode",
})
SAFE_INSERT_SANDBOX_POLICY = '{"type":"read-only"}'
SAFE_INSERT_APPROVAL_MODE = "on-request"
PROFILE_PATH = Path.home() / ".profile"
PROFILE_START = "# >>> codex-desktop-wsl-runtime >>>"
PROFILE_END = "# <<< codex-desktop-wsl-runtime <<<"
SESSION_PROJECTION_SCHEMA = "codex-wsl-session-projection.v6"
LEGACY_SESSION_PROJECTION_SCHEMAS = frozenset({
    "codex-wsl-session-projection.v4",
    "codex-wsl-session-projection.v5",
})
PLUGIN_PROJECTION_SCHEMA = "codex-wsl-plugin-projection.v1"
SESSION_FINGERPRINT_BYTES = 64 * 1024


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def desktop_profile_block() -> str:
    return "\n".join([
        PROFILE_START,
        'if [ -z "${CODEX_HOME:-}" ]; then',
        '    export CODEX_HOME="$HOME/.codex-app"',
        "fi",
        PROFILE_END,
    ])


def render_profile(current: str) -> str:
    start = current.find(PROFILE_START)
    end = current.find(PROFILE_END)
    if start >= 0 and end >= start:
        end += len(PROFILE_END)
        current = current[:start].rstrip() + "\n" + current[end:].lstrip()
    return current.rstrip() + "\n\n" + desktop_profile_block() + "\n"


def managed_link_status(source: Path, target: Path) -> dict[str, object]:
    if target.is_symlink():
        linked = target.resolve() == source.resolve()
        return {
            "path": str(target),
            "source": str(source),
            "status": "linked" if linked else "conflicting_symlink",
            "target": os.readlink(target),
            "ok": linked,
        }
    if target.exists():
        return {
            "path": str(target),
            "source": str(source),
            "status": "conflicting_existing_path",
            "ok": False,
        }
    return {"path": str(target), "source": str(source), "status": "would_link", "ok": True}


def link_or_verify(source: Path, target: Path) -> dict[str, object]:
    inspected = managed_link_status(source, target)
    if inspected["status"] != "would_link":
        return inspected
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=source.is_dir())
    return {
        "path": str(target),
        "source": str(source),
        "status": "linked",
        "target": os.readlink(target),
        "ok": True,
    }


def link_skill_tree(source: Path, target: Path, *, write: bool) -> dict[str, object]:
    """Link user skills individually so Codex system skills stay runtime-local."""
    if target.is_symlink():
        if target.resolve() == source.resolve():
            if not write:
                return {
                    "path": str(target),
                    "source": str(source),
                    "status": "would_migrate_shared_tree",
                    "ok": True,
                }
            generated = source / ".system"
            staged = target.parent / ".system-migration"
            if generated.exists() and not staged.exists():
                shutil.copytree(generated, staged)
            target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            if staged.exists() and not (target / ".system").exists():
                shutil.move(str(staged), str(target / ".system"))
            if generated.exists():
                shutil.rmtree(generated)
        else:
            return {
                "path": str(target),
                "source": str(source),
                "status": "conflicting_symlink",
                "ok": False,
            }
    if target.exists() and not target.is_dir():
        return {
            "path": str(target),
            "source": str(source),
            "status": "conflicting_existing_path",
            "ok": False,
        }
    conflicts: list[str] = []
    missing: list[Path] = []
    for child in sorted(source.iterdir()):
        if child.name == ".system":
            continue
        destination = target / child.name
        if destination.is_symlink() and destination.resolve() == child.resolve():
            continue
        if destination.exists() or destination.is_symlink():
            conflicts.append(child.name)
        else:
            missing.append(child)
    if conflicts:
        return {
            "path": str(target),
            "source": str(source),
            "status": "conflicting_children",
            "conflicts": conflicts[:20],
            "ok": False,
        }
    if not write:
        return {
            "path": str(target),
            "source": str(source),
            "status": "would_link_children" if missing else "linked_children",
            "linked_count": 0,
            "missing_count": len(missing),
            "ok": True,
        }
    target.mkdir(parents=True, exist_ok=True)
    linked = 0
    for child in missing:
        destination = target / child.name
        destination.symlink_to(child, target_is_directory=child.is_dir())
        linked += 1
    return {
        "path": str(target),
        "source": str(source),
        "status": "linked_children",
        "linked_count": linked,
        "ok": True,
    }


def skills_projection(*, write: bool) -> dict[str, object]:
    """Project Work Git skills to both runtime and canonical user roots only."""
    targets = list(dict.fromkeys((CODEX_HOME / "skills", CANONICAL_SKILLS_ROOT)))
    if not WORK_GIT_SKILLS.is_dir():
        return {
            "schema": "codex-wsl-skills.v1",
            "ok": False,
            "write": write,
            "changed": False,
            "status": "source_missing",
            "source": str(WORK_GIT_SKILLS),
            "targets": [],
        }

    plans = [link_skill_tree(WORK_GIT_SKILLS, target, write=False) for target in targets]
    if not all(bool(item.get("ok")) for item in plans):
        return {
            "schema": "codex-wsl-skills.v1",
            "ok": False,
            "write": write,
            "changed": False,
            "status": "blocked",
            "source": str(WORK_GIT_SKILLS),
            "targets": plans,
            "boundary": "skills_only_no_config_session_state_or_plugin_writes",
        }

    missing_count = sum(int(item.get("missing_count") or 0) for item in plans)
    migration_required = any(item.get("status") == "would_migrate_shared_tree" for item in plans)
    if not write:
        return {
            "schema": "codex-wsl-skills.v1",
            "ok": True,
            "write": False,
            "changed": bool(missing_count or migration_required),
            "status": "would_project" if missing_count or migration_required else "projection_current",
            "source": str(WORK_GIT_SKILLS),
            "targets": plans,
            "boundary": "skills_only_no_config_session_state_or_plugin_writes",
        }

    results = [link_skill_tree(WORK_GIT_SKILLS, target, write=True) for target in targets]
    linked_count = sum(int(item.get("linked_count") or 0) for item in results)
    return {
        "schema": "codex-wsl-skills.v1",
        "ok": all(bool(item.get("ok")) for item in results),
        "write": True,
        "changed": bool(linked_count or migration_required),
        "status": "projected",
        "source": str(WORK_GIT_SKILLS),
        "targets": results,
        "linked_count": linked_count,
        "boundary": "skills_only_no_config_session_state_or_plugin_writes",
    }


def windows_cwd_to_wsl(value: str) -> tuple[str, str]:
    """Translate a Windows session cwd without making the Windows source mutable."""
    raw = str(value or "").strip()
    if not raw:
        return str(ROOT), "fallback_workspace"
    if raw.startswith("/"):
        candidate = Path(raw)
        return (raw, "native") if candidate.is_dir() else (str(ROOT), "fallback_workspace")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("//?/"):
        normalized = normalized[4:]
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        drive = normalized[0].lower()
        if drive in DRIVE_OVERRIDES:
            return str(DRIVE_OVERRIDES[drive]), "drive_override"
        candidate = Path(f"/mnt/{drive}") / normalized[3:]
        if candidate.is_dir():
            return str(candidate), "drive_mount"
    return str(ROOT), "fallback_workspace"


def windows_file_path_to_wsl(value: str) -> Path | None:
    """Lexically map a Windows file path without accepting UNC fallbacks."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("/"):
        return Path(raw)
    normalized = raw.replace("\\", "/")
    if normalized.startswith("//?/"):
        normalized = normalized[4:]
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        drive = normalized[0].lower()
        return Path(f"/mnt/{drive}") / normalized[3:]
    return None


def _plugin_version_dir(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    latest = root / "latest"
    if latest.is_dir() and _plugin_manifest(latest) is not None:
        return latest
    candidates = [
        item for item in root.iterdir()
        if item.is_dir()
        and not item.name.startswith(("plugin-", "."))
        and _plugin_manifest(item) is not None
    ]
    return sorted(candidates, key=lambda item: item.name)[-1] if candidates else None


def _plugin_manifest(root: Path) -> Path | None:
    for metadata_dir in (".codex-plugin", ".claude-plugin"):
        candidate = root / metadata_dir / "plugin.json"
        if candidate.is_file():
            return candidate
    return None


def _enabled_plugins() -> list[dict[str, str]]:
    config_path = WINDOWS_CODEX_HOME / "config.toml"
    if not config_path.is_file():
        return []
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return []
    rows: list[dict[str, str]] = []
    for identifier, settings in sorted(plugins.items()):
        if not isinstance(identifier, str) or not isinstance(settings, dict) or settings.get("enabled") is not True:
            continue
        if "@" not in identifier:
            continue
        plugin, marketplace = identifier.rsplit("@", 1)
        rows.append({"identifier": identifier, "plugin": plugin, "marketplace": marketplace})
    return rows


def _plugin_config_tables() -> tuple[str, str]:
    rows = _enabled_plugins()
    marketplaces: list[str] = []
    for row in rows:
        if row["marketplace"] not in marketplaces:
            marketplaces.append(row["marketplace"])
    marketplace_lines: list[str] = []
    for marketplace in marketplaces:
        relative = _marketplace_config_source_relative(marketplace)
        source = CODEX_HOME / ".tmp" / relative
        marketplace_lines.extend([
            f"[marketplaces.{marketplace}]",
            f"source = {json.dumps(str(source))}",
            'source_type = "local"',
            "",
        ])
    plugin_lines: list[str] = []
    for row in rows:
        plugin_lines.extend([f'[plugins."{row["identifier"]}"]', "enabled = true", ""])
    return "\n".join(marketplace_lines).rstrip(), "\n".join(plugin_lines).rstrip()


def _marketplace_projection_relative(marketplace: str) -> Path:
    """Return a CLI-supported marketplace root for each configured source."""
    if marketplace == "openai-bundled":
        return Path("bundled-marketplaces") / "openai-bundled"
    if marketplace == "openai-api-curated":
        return Path("marketplaces") / marketplace
    if marketplace == "openai-primary-runtime":
        return Path("marketplaces") / marketplace
    if marketplace == "openai-curated":
        # Curated connectors are managed by the shared local plugin tree.
        return Path("plugins")
    return Path("marketplaces") / marketplace


def _marketplace_config_source_relative(marketplace: str) -> Path:
    if marketplace == "openai-bundled":
        return Path("bundled-marketplaces") / "openai-bundled"
    if marketplace == "openai-api-curated":
        return Path("marketplaces") / marketplace
    if marketplace == "openai-primary-runtime":
        return Path("marketplaces") / marketplace
    if marketplace == "openai-curated":
        return Path("plugins")
    return Path("marketplaces") / marketplace


def _windows_marketplace_root(marketplace: str) -> Path:
    if marketplace == "openai-bundled":
        return WINDOWS_CODEX_HOME / ".tmp" / "bundled-marketplaces" / "openai-bundled"
    if marketplace in {"openai-api-curated", "openai-curated"}:
        return WINDOWS_CODEX_HOME / ".tmp" / "plugins"
    return WINDOWS_CODEX_HOME / ".tmp" / "marketplaces" / marketplace


def _marketplace_manifest_name(marketplace: str) -> str:
    # Codex discovers marketplace.json; api_marketplace.json is a source-side
    # companion manifest and is not loaded when a marketplace is configured.
    return "marketplace.json"


def _marketplace_plugin_source(marketplace: str, plugin: str) -> tuple[Path | None, Path | None]:
    """Resolve one plugin source from native Codex or Claude marketplace metadata."""

    source_root = _windows_marketplace_root(marketplace)
    direct_relative = Path("plugins") / plugin
    direct = source_root / direct_relative
    if _plugin_manifest(direct) is not None:
        return direct, direct_relative
    manifest = source_root / ".claude-plugin" / "marketplace.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    rows = payload.get("plugins") if isinstance(payload, dict) else []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict) or str(item.get("name") or "") != plugin:
            continue
        relative = Path(str(item.get("source") or "").removeprefix("./"))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            return None, None
        source = source_root / relative
        return (source, relative) if _plugin_manifest(source) is not None else (None, None)
    return None, None


def _project_marketplace_plugin_source(row: dict[str, str], *, write: bool) -> tuple[bool, str]:
    marketplace = row["marketplace"]
    if marketplace == "openai-primary-runtime":
        windows_source = _plugin_version_dir(
            WINDOWS_CODEX_HOME / "plugins" / "cache" / marketplace / row["plugin"]
        )
        if windows_source is None:
            return False, "runtime_cache_missing"
        local_source = _plugin_version_dir(
            CODEX_HOME / "plugins" / "cache" / marketplace / row["plugin"]
        )
        source = local_source if local_source is not None and not local_source.is_symlink() else windows_source
        target = CODEX_HOME / ".tmp" / _marketplace_projection_relative(marketplace) / "plugins" / row["plugin"]
        if target.exists() or target.is_symlink():
            if target.is_symlink() and target.resolve() == source.resolve():
                return True, "linked"
            if target.is_symlink():
                if write:
                    target.unlink()
                    target.symlink_to(source, target_is_directory=True)
                    return True, "relinked"
                return True, "would_relink"
            if (target / ".codex-plugin" / "plugin.json").is_file():
                return True, "existing_valid"
            return False, "target_conflict"
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)
        return True, "linked" if write else "would_link"
    source, source_relative = _marketplace_plugin_source(marketplace, row["plugin"])
    if source is None or source_relative is None:
        # Some managed bundled/runtime entries are cache-only and intentionally
        # absent from the marketplace snapshot.
        return (True, "source_missing_optional") if marketplace == "openai-bundled" else (False, "source_missing")
    target_root = CODEX_HOME / ".tmp" / _marketplace_projection_relative(marketplace)
    target = target_root / source_relative
    if target.exists() or target.is_symlink():
        if target.is_symlink() and target.resolve() == source.resolve():
            return True, "linked"
        if _plugin_manifest(target) is not None:
            return True, "existing_valid"
        return False, "target_conflict"
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
    return True, "linked" if write else "would_link"


def project_plugins(*, write: bool) -> dict[str, object]:
    """Project enabled plugin versions and marketplace indexes into WSL."""
    rows = _enabled_plugins()
    result: dict[str, object] = {
        "path": str(CODEX_HOME / "plugins"),
        "source": str(WINDOWS_CODEX_HOME / "plugins"),
        "status": "source_missing_optional",
        "changed": False,
        "enabled_count": len(rows),
        "projected_count": 0,
        "missing": [],
        "ok": True,
    }
    if not rows:
        return result
    source_cache = WINDOWS_CODEX_HOME / "plugins" / "cache"
    target_cache = CODEX_HOME / "plugins" / "cache"
    missing: list[str] = []
    projected: list[dict[str, str]] = []
    projection_needed = False
    for row in rows:
        source = _plugin_version_dir(source_cache / row["marketplace"] / row["plugin"])
        if source is None:
            missing.append(row["identifier"])
            continue
        plugin_root = target_cache / row["marketplace"] / row["plugin"]
        target = plugin_root / source.name
        direct_primary_projection = bool(
            row["marketplace"] == "openai-primary-runtime"
            and plugin_root.is_dir()
            and not plugin_root.is_symlink()
            and (plugin_root / ".codex-plugin" / "plugin.json").is_file()
        )
        if direct_primary_projection and write:
            direct_manifest = plugin_root / ".codex-plugin" / "plugin.json"
            if direct_manifest.read_bytes() != (source / ".codex-plugin" / "plugin.json").read_bytes():
                missing.append(f'{row["identifier"]}:direct_projection_conflict')
                continue
            shutil.rmtree(plugin_root)
        if target.exists() or target.is_symlink():
            valid_existing = _plugin_manifest(target) is not None
            direct_link_ok = target.is_symlink() and target.resolve() == source.resolve()
            if row["marketplace"] == "openai-primary-runtime" and target.is_symlink() and write:
                target.unlink()
                valid_existing = False
                direct_link_ok = False
            if row["marketplace"] == "openai-primary-runtime" and not valid_existing and write:
                target.parent.mkdir(parents=True, exist_ok=True)
                staging_parent = Path(tempfile.mkdtemp(prefix=f".{row['plugin']}-projection-", dir=target.parent))
                staging = staging_parent / "payload"
                try:
                    shutil.copytree(source, staging, symlinks=True)
                    if not (staging / ".codex-plugin" / "plugin.json").is_file():
                        raise ValueError("projected primary-runtime plugin is missing plugin.json")
                    os.replace(staging, target)
                finally:
                    shutil.rmtree(staging_parent, ignore_errors=True)
                valid_existing = True
            if not valid_existing and not direct_link_ok:
                missing.append(row["identifier"])
                continue
        elif write:
            target.parent.mkdir(parents=True, exist_ok=True)
            if row["marketplace"] == "openai-primary-runtime":
                shutil.copytree(source, target, symlinks=True)
            else:
                target.symlink_to(source, target_is_directory=True)
        elif not target.exists() and not target.is_symlink():
            projection_needed = True
        source_ok, source_status = _project_marketplace_plugin_source(row, write=write)
        if source_status.startswith("would_"):
            projection_needed = True
        if not source_ok:
            missing.append(f'{row["identifier"]}:marketplace_source_{source_status}')
        projected.append({"identifier": row["identifier"], "source": str(source), "target": str(target)})
    if write:
        for marketplace in {row["marketplace"] for row in rows}:
            target_root = CODEX_HOME / ".tmp" / _marketplace_projection_relative(marketplace)
            target = target_root / ".agents" / "plugins" / _marketplace_manifest_name(marketplace)
            if marketplace == "openai-primary-runtime":
                runtime_plugins = [
                    {
                        "name": row["plugin"],
                        "source": {"source": "local", "path": f'./plugins/{row["plugin"]}'},
                        "policy": {"installation": "AVAILABLE", "authentication": "ON_USE", "products": ["CODEX"]},
                    }
                    for row in rows
                    if row["marketplace"] == marketplace
                ]
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(
                    target,
                    lambda handle: handle.write(
                        (json.dumps({"name": marketplace, "plugins": runtime_plugins}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                    ),
                )
                continue
            source_root = _windows_marketplace_root(marketplace)
            source_filename = "marketplace.json"
            if marketplace == "openai-api-curated":
                source_filename = "api_marketplace.json"
            source = source_root / ".agents" / "plugins" / source_filename
            claude_source = source_root / ".claude-plugin" / "marketplace.json"
            if not source.is_file() and claude_source.is_file():
                source = claude_source
            if not source.is_file():
                missing.append(f"marketplace:{marketplace}")
                continue
            stale_claude_target = target_root / ".claude-plugin" / "marketplace.json"
            if source == claude_source and stale_claude_target.is_file():
                if stale_claude_target.read_bytes() != source.read_bytes():
                    missing.append(f"marketplace:{marketplace}:stale_claude_projection_conflict")
                    continue
                stale_claude_target.unlink()
                try:
                    stale_claude_target.parent.rmdir()
                except OSError:
                    pass
            target.parent.mkdir(parents=True, exist_ok=True)
            if source == claude_source:
                claude_payload = json.loads(source.read_text(encoding="utf-8"))
                translated_plugins = []
                for item in claude_payload.get("plugins", []):
                    if not isinstance(item, dict):
                        continue
                    plugin_name = str(item.get("name") or "").strip()
                    source_path = str(item.get("source") or "").strip()
                    relative = Path(source_path.removeprefix("./"))
                    if not plugin_name or relative.is_absolute() or not relative.parts or ".." in relative.parts:
                        continue
                    translated = {
                        key: item[key]
                        for key in ("name", "description", "version", "license")
                        if key in item
                    }
                    translated["source"] = {"source": "local", "path": source_path}
                    translated["policy"] = {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                        "products": ["CODEX"],
                    }
                    translated_plugins.append(translated)
                translated_payload = {
                    "name": str(claude_payload.get("name") or marketplace),
                    "plugins": translated_plugins,
                }
                rendered_manifest = (
                    json.dumps(translated_payload, ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8")
                atomic_write_bytes(target, lambda handle: handle.write(rendered_manifest))
            else:
                atomic_write_bytes(target, lambda handle, source=source: handle.write(source.read_bytes()))
        PLUGIN_MANIFEST.write_text(
            json.dumps({"schema": PLUGIN_PROJECTION_SCHEMA, "generated_at": now_iso(), "plugins": projected}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    result.update({
        "status": "projected" if write else "would_project",
        "changed": projection_needed or not PLUGIN_MANIFEST.is_file(),
        "projected_count": len(projected),
        "missing": missing[:20],
    })
    return result


def safe_session_relative_path(value: str) -> Path | None:
    candidate = Path(str(value or ""))
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts or candidate.suffix != ".jsonl":
        return None
    return candidate


def session_source_files(source_root: Path) -> tuple[list[Path], str]:
    """List regular JSONL sources without resolving every Windows-mounted file."""

    files: list[Path] = []
    try:
        with os.scandir(source_root) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink():
                    return [], str(path)
                if entry.is_dir(follow_symlinks=False):
                    nested, unsafe = session_source_files(path)
                    if unsafe:
                        return [], unsafe
                    files.extend(nested)
                elif entry.is_file(follow_symlinks=False) and path.suffix == ".jsonl":
                    files.append(path)
    except OSError as exc:
        raise OSError(f"unable to enumerate session source {source_root}: {exc}") from exc
    return files, ""


def session_projection_identity(source: Path, source_root: Path) -> dict[str, object]:
    """Resolve source identity while preserving Codex's authoritative relative path."""
    source_relative = source.relative_to(source_root).as_posix()
    if safe_session_relative_path(source_relative) is None:
        raise ValueError(f"unsafe source session path: {source_relative}")

    record: dict[str, object] | None = None
    with source.open("r", encoding="utf-8", errors="strict") as handle:
        for line in handle:
            if line.strip():
                candidate = json.loads(line)
                if isinstance(candidate, dict):
                    record = candidate
                break
    if not record or record.get("type") != "session_meta":
        raise ValueError(f"session metadata missing from first record: {source}")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"session metadata payload is invalid: {source}")
    thread_id = str(payload.get("id") or "").strip()
    if not thread_id:
        raise ValueError(f"session metadata id is missing: {source}")

    projected_relative = Path(source_relative)
    if safe_session_relative_path(projected_relative.as_posix()) is None:
        raise ValueError(f"unsafe projected session path: {projected_relative}")
    return {
        "thread_id": thread_id,
        "cwd": str(payload.get("cwd") or ""),
        "source_relative": source_relative,
        "projected_relative": projected_relative.as_posix(),
    }


def safe_projection_destination(root: Path, relative: Path, *, create: bool) -> Path:
    resolved_root = root.resolve()
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"projection target contains a symlink: {current}")
        if create:
            current.mkdir(exist_ok=True)
    destination = root / relative
    if destination.is_symlink():
        raise ValueError(f"projection target is a symlink: {destination}")
    if destination.parent.exists() and not destination.parent.resolve().is_relative_to(resolved_root):
        raise ValueError(f"projection target escapes root: {destination}")
    return destination


def atomic_write_bytes(target: Path, content_writer: object) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            content_writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _project_path_value(value: object) -> tuple[object, int]:
    if not isinstance(value, str):
        return value, 0
    projected, _ = windows_cwd_to_wsl(value)
    return projected, int(projected != value)


def _project_resume_context(value: object) -> int:
    """Translate structured paths in a session resume/state record in place."""
    if isinstance(value, list):
        return sum(_project_resume_context(item) for item in value)
    if not isinstance(value, dict):
        return 0

    changed = 0
    for key in ("cwd",):
        if key in value:
            value[key], count = _project_path_value(value[key])
            changed += count
    for key in ("workspace_roots", "workspaceRoots"):
        roots = value.get(key)
        if not isinstance(roots, list):
            continue
        for index, root in enumerate(roots):
            roots[index], count = _project_path_value(root)
            changed += count
    path_record = value.get("type") == "path"
    if path_record and "path" in value:
        value["path"], count = _project_path_value(value["path"])
        changed += count

    for key, child in value.items():
        if key not in {"cwd", "workspace_roots", "workspaceRoots"} and not (path_record and key == "path"):
            changed += _project_resume_context(child)
    return changed


def _project_session_line(raw_line: bytes) -> tuple[bytes, str, str]:
    translated_from = ""
    translated_to = ""
    if not raw_line.strip() or (b'"session_meta"' not in raw_line and b'"turn_context"' not in raw_line):
        return raw_line, translated_from, translated_to
    record = json.loads(raw_line.decode("utf-8", errors="strict"))
    if record.get("type") not in {"session_meta", "turn_context"}:
        return raw_line, translated_from, translated_to
    payload = record.get("payload")
    original_cwd = payload.get("cwd") if isinstance(payload, dict) else None
    changed_field_count = _project_resume_context(payload)
    if not changed_field_count:
        return raw_line, translated_from, translated_to
    projected_cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if isinstance(original_cwd, str) and isinstance(projected_cwd, str):
        translated_from = original_cwd
        translated_to = projected_cwd
    newline = b"\r\n" if raw_line.endswith(b"\r\n") else b"\n" if raw_line.endswith(b"\n") else b""
    projected_line = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + newline
    return projected_line, translated_from, translated_to


def _session_projection_file(source: Path, target: Path) -> tuple[bool, str]:
    """Atomically rebuild a JSONL session and translate resume-context paths."""
    translated_from = ""
    translated_to = ""

    def copy_projected(handle: object) -> None:
        nonlocal translated_from, translated_to
        with source.open("rb") as source_handle:
            for raw_line in source_handle:
                if not raw_line.strip():
                    handle.write(raw_line)
                    continue
                projected_line, line_from, line_to = _project_session_line(raw_line)
                translated_from = translated_from or line_from
                translated_to = translated_to or line_to
                handle.write(projected_line)

    atomic_write_bytes(target, copy_projected)
    return translated_to != translated_from, translated_to or ""


def _session_prefix_fingerprint(source: Path, size: int) -> str:
    if size < 0 or source.stat().st_size < size:
        return ""
    with source.open("rb") as handle:
        head = handle.read(min(size, SESSION_FINGERPRINT_BYTES))
        tail = b""
        if size > SESSION_FINGERPRINT_BYTES:
            handle.seek(max(0, size - SESSION_FINGERPRINT_BYTES))
            tail = handle.read(min(size, SESSION_FINGERPRINT_BYTES))
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    digest.update(b"\0")
    digest.update(head)
    digest.update(b"\0")
    digest.update(tail)
    return digest.hexdigest()


def _session_projection_destination_matches(prior: object, destination: Path) -> bool:
    """Verify that a current-schema destination still matches its manifest owner."""
    if not isinstance(prior, dict) or not destination.is_file():
        return False
    projected_size = prior.get("projected_size")
    projected_fingerprint = str(prior.get("projected_prefix_fingerprint") or "")
    if not isinstance(projected_size, int) or projected_size < 0 or not projected_fingerprint:
        return False
    return bool(
        destination.stat().st_size == projected_size
        and _session_prefix_fingerprint(destination, projected_size) == projected_fingerprint
    )


def _session_projected_prefix_signature(source: Path, end: int) -> tuple[int, str]:
    """Hash the exact projected bytes for a complete historical source prefix."""
    if end <= 0 or source.stat().st_size < end:
        return -1, ""
    digest = hashlib.sha256()
    projected_size = 0
    with source.open("rb") as source_handle:
        while source_handle.tell() < end:
            remaining = end - source_handle.tell()
            raw_line = source_handle.readline(remaining)
            if not raw_line or (source_handle.tell() == end and not raw_line.endswith(b"\n")):
                return -1, ""
            projected_line, _, _ = _project_session_line(raw_line)
            digest.update(projected_line)
            projected_size += len(projected_line)
    return projected_size, digest.hexdigest()


def _legacy_session_projection_destination_is_tracked(
    prior: object,
    *,
    schema: str,
    source: Path,
    source_relative: str,
    projected_relative: str,
    destination: Path,
) -> bool:
    """Admit one bounded v4/v5 manifest upgrade without trusting untracked files."""
    if schema not in LEGACY_SESSION_PROJECTION_SCHEMAS or not isinstance(prior, dict):
        return False
    if prior.get("source_relative") not in (None, source_relative):
        return False
    if prior.get("projected_relative") not in (None, projected_relative):
        return False
    prior_size = prior.get("size")
    if not isinstance(prior_size, int) or prior_size <= 0 or prior_size > source.stat().st_size:
        return False
    if schema.endswith(".v4"):
        return prior_size == source.stat().st_size
    projected_size = prior.get("projected_size")
    recorded_fingerprint = str(prior.get("source_prefix_fingerprint") or "")
    if recorded_fingerprint and _session_prefix_fingerprint(source, prior_size) != recorded_fingerprint:
        return False
    expected_size, expected_sha256 = _session_projected_prefix_signature(source, prior_size)
    return bool(
        expected_size >= 0
        and (not isinstance(projected_size, int) or projected_size == expected_size)
        and destination.stat().st_size == expected_size
        and sha256(destination) == expected_sha256
    )


def _session_projection_append(source: Path, target: Path, *, start: int, end: int) -> tuple[bool, str]:
    """Append a stable, complete JSONL suffix after caller-validated prefix checks."""
    translated_from = ""
    translated_to = ""
    original_target_size = target.stat().st_size
    with source.open("rb") as source_handle, target.open("ab") as target_handle:
        try:
            source_handle.seek(start)
            while source_handle.tell() < end:
                remaining = end - source_handle.tell()
                raw_line = source_handle.readline(remaining)
                if not raw_line:
                    raise OSError("source session ended before captured append boundary")
                if source_handle.tell() == end and not raw_line.endswith(b"\n"):
                    raise ValueError("captured session suffix does not end on a JSONL boundary")
                projected_line, line_from, line_to = _project_session_line(raw_line)
                translated_from = translated_from or line_from
                translated_to = translated_to or line_to
                target_handle.write(projected_line)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        except Exception:
            target_handle.truncate(original_target_size)
            target_handle.flush()
            os.fsync(target_handle.fileno())
            raise
    return translated_to != translated_from, translated_to or ""


def _manifest_row_is_fresh(
    prior: object,
    *,
    source_relative: str,
    projected_relative: str,
    size: int,
    mtime_ns: int,
) -> bool:
    return bool(
        isinstance(prior, dict)
        and prior.get("source_relative") == source_relative
        and prior.get("projected_relative") == projected_relative
        and prior.get("size") == size
        and prior.get("mtime_ns") == mtime_ns
    )


def _load_manifest() -> dict[str, object]:
    if not SESSION_MANIFEST.is_file():
        return {"files": {}}
    try:
        value = json.loads(SESSION_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"files": {}}
    if not isinstance(value, dict):
        return {"files": {}}
    # Any transform revision must rebuild every target before the signature
    # fast path is trusted.
    if value.get("schema") != SESSION_PROJECTION_SCHEMA:
        return {
            "files": value.get("files", {}),
            "schema_mismatch": True,
            "previous_schema": str(value.get("schema") or ""),
        }
    return value


def session_projection_identity_from_manifest(
    source: Path,
    source_root: Path,
    *,
    previous_files: dict[str, object],
    schema_mismatch: bool,
) -> dict[str, object]:
    """Reuse a validated unchanged session identity without reopening JSONL."""

    source_relative = source.relative_to(source_root).as_posix()
    if safe_session_relative_path(source_relative) is None:
        raise ValueError(f"unsafe source session path: {source_relative}")
    prior = previous_files.get(source_relative)
    stat_result = source.stat()
    if isinstance(prior, dict) and not schema_mismatch:
        projected_relative = str(prior.get("projected_relative") or "")
        thread_id = str(prior.get("thread_id") or "").strip()
        if (
            thread_id
            and safe_session_relative_path(projected_relative) is not None
            and _manifest_row_is_fresh(
                prior,
                source_relative=source_relative,
                projected_relative=projected_relative,
                size=stat_result.st_size,
                mtime_ns=stat_result.st_mtime_ns,
            )
        ):
            return {
                "thread_id": thread_id,
                "source_relative": source_relative,
                "projected_relative": projected_relative,
                "source": "validated_manifest",
            }
    return session_projection_identity(source, source_root)


def project_sessions(*, write: bool) -> dict[str, object]:
    """Keep an isolated WSL session projection; never rewrite Windows sessions."""
    target = CODEX_HOME / "sessions"
    result: dict[str, object] = {
        "path": str(target),
        "source": str(WINDOWS_SESSIONS),
        "manifest": str(SESSION_MANIFEST),
        "status": "source_missing_optional",
        "source_count": 0,
        "projected_count": 0,
        "translated_count": 0,
        "changed": False,
        "native_files_preserved": 0,
        "conflict_count": 0,
        "stale_preserved_count": 0,
        "fallback_cwd": str(ROOT),
        "ok": True,
    }
    if not WINDOWS_SESSIONS.is_dir():
        return result
    source_root = WINDOWS_SESSIONS
    try:
        source_files, unsafe_source = session_source_files(source_root)
    except OSError as exc:
        result["status"] = "source_unreadable"
        result["ok"] = False
        result["error"] = str(exc)
        return result
    if unsafe_source:
        result["status"] = "unsafe_source_path"
        result["ok"] = False
        result["error"] = unsafe_source
        return result
    source_files.sort()
    result["source_count"] = len(source_files)
    if target.is_symlink():
        if target.resolve() != WINDOWS_SESSIONS.resolve():
            result["status"] = "conflicting_symlink"
            return result
        if not write:
            result["status"] = "would_replace_shared_symlink"
            result["changed"] = True
            return result
        SESSION_TRANSITION_ROOT.mkdir(parents=True, exist_ok=True)
        legacy = SESSION_TRANSITION_ROOT / "sessions-shared-windows"
        if legacy.exists() or legacy.is_symlink():
            legacy.unlink()
        target.rename(legacy)
        target.mkdir(parents=True, exist_ok=True)
        result["changed"] = True
        result["replaced_symlink"] = str(legacy)
    elif target.exists() and not target.is_dir():
        result["status"] = "conflicting_non_directory"
        return result
    elif write:
        target.mkdir(parents=True, exist_ok=True)
    elif not target.exists():
        result["status"] = "would_create_projection"
        result["changed"] = True
        return result

    loaded_manifest = _load_manifest()
    previous = loaded_manifest.get("files")
    previous_files = previous if isinstance(previous, dict) else {}
    invalid_manifest_keys = [key for key in previous_files if safe_session_relative_path(str(key)) is None]
    invalid_manifest_destinations = [
        str(value.get("projected_relative"))
        for key, value in previous_files.items()
        if isinstance(value, dict)
        and value.get("projected_relative") is not None
        and safe_session_relative_path(str(value.get("projected_relative"))) is None
    ]
    if invalid_manifest_keys or invalid_manifest_destinations:
        result["status"] = "manifest_invalid"
        result["ok"] = False
        result["invalid_manifest_keys"] = invalid_manifest_keys[:10]
        result["invalid_manifest_destinations"] = invalid_manifest_destinations[:10]
        return result
    schema_mismatch = bool(loaded_manifest.get("schema_mismatch"))
    previous_schema = str(loaded_manifest.get("previous_schema") or "")
    current_files: dict[str, dict[str, object]] = {}
    current_projected_paths: set[str] = set()
    projection_sources: dict[str, str] = {}
    result["source_count"] = len(source_files)
    identities: dict[str, dict[str, object]] = {}

    def identity_for(source: Path) -> dict[str, object]:
        key = str(source)
        if key not in identities:
            identities[key] = session_projection_identity_from_manifest(
                source,
                WINDOWS_SESSIONS,
                previous_files=previous_files,
                schema_mismatch=schema_mismatch,
            )
        return identities[key]

    destination_conflicts: list[dict[str, object]] = []
    legacy_tracked_sources: set[str] = set()
    preflight_paths: dict[str, str] = {}
    for source in source_files:
        try:
            identity = identity_for(source)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            result["status"] = "session_metadata_unreadable"
            result["ok"] = False
            result["error"] = str(exc)
            return result
        relative = str(identity["source_relative"])
        projected_relative = str(identity["projected_relative"])
        prior_source = preflight_paths.get(projected_relative)
        if prior_source is not None and prior_source != relative:
            result["status"] = "projection_path_collision"
            result["ok"] = False
            result["error"] = {"projected_relative": projected_relative, "sources": [prior_source, relative]}
            return result
        preflight_paths[projected_relative] = relative
        try:
            destination = safe_projection_destination(
                target,
                safe_session_relative_path(projected_relative),
                create=False,
            )
        except ValueError as exc:
            result["status"] = "unsafe_target_path"
            result["ok"] = False
            result["error"] = str(exc)
            return result
        if not destination.is_file():
            continue
        prior = previous_files.get(relative)
        if not schema_mismatch and (not isinstance(prior, dict) or not str(prior.get("thread_id") or "")):
            result["identity_upgrade_count"] = int(result.get("identity_upgrade_count") or 0) + 1
        if schema_mismatch:
            tracked = _legacy_session_projection_destination_is_tracked(
                prior,
                schema=previous_schema,
                source=source,
                source_relative=relative,
                projected_relative=projected_relative,
                destination=destination,
            )
            if tracked:
                legacy_tracked_sources.add(relative)
        else:
            tracked = _session_projection_destination_matches(prior, destination)
        if not tracked:
            destination_conflicts.append({
                "source_relative": relative,
                "projected_relative": projected_relative,
                "reason": (
                    "native_destination_conflict"
                    if not isinstance(prior, dict)
                    or prior.get("projection_status") == "native_destination_preserved"
                    else "projection_destination_diverged"
                ),
            })
    if destination_conflicts:
        result["conflict_count"] = len(destination_conflicts)
        result["conflicts"] = destination_conflicts[:20]
    conflict_by_source = {
        str(row["source_relative"]): row
        for row in destination_conflicts
    }

    if write:
        for source in source_files:
            try:
                identity = identity_for(source)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                result["status"] = "session_metadata_unreadable"
                result["ok"] = False
                result["error"] = str(exc)
                return result
            relative = str(identity["source_relative"])
            projected_relative = str(identity["projected_relative"])
            prior_source = projection_sources.get(projected_relative)
            if prior_source is not None and prior_source != relative:
                result["status"] = "projection_path_collision"
                result["ok"] = False
                result["error"] = {"projected_relative": projected_relative, "sources": [prior_source, relative]}
                return result
            projection_sources[projected_relative] = relative
            current_projected_paths.add(projected_relative)
            conflict = conflict_by_source.get(relative)
            if conflict is not None:
                prior = previous_files.get(relative)
                preserved_row = dict(prior) if isinstance(prior, dict) else {
                    "source_relative": relative,
                    "projected_relative": projected_relative,
                    "size": source.stat().st_size,
                    "mtime_ns": source.stat().st_mtime_ns,
                }
                preserved_row["thread_id"] = str(identity["thread_id"])
                preserved_row["projection_status"] = (
                    "native_destination_preserved"
                    if conflict.get("reason") == "native_destination_conflict"
                    else "diverged_destination_preserved"
                )
                current_files[relative] = preserved_row
                continue
            try:
                destination = safe_projection_destination(
                    target,
                    safe_session_relative_path(projected_relative),
                    create=True,
                )
            except ValueError as exc:
                result["status"] = "unsafe_target_path"
                result["ok"] = False
                result["error"] = str(exc)
                return result
            stat_result = source.stat()
            signature = {"size": stat_result.st_size, "mtime_ns": stat_result.st_mtime_ns}
            prior = previous_files.get(relative)
            manifest_row = {
                "source_relative": relative,
                "projected_relative": projected_relative,
                **signature,
            }
            if (
                previous_schema == "codex-wsl-session-projection.v5"
                and relative in legacy_tracked_sources
                and isinstance(prior, dict)
                and prior.get("size") == stat_result.st_size
                and destination.is_file()
            ):
                current_files[relative] = {
                    **manifest_row,
                    "thread_id": str(identity["thread_id"]),
                    "source_prefix_fingerprint": _session_prefix_fingerprint(source, stat_result.st_size),
                    "projected_size": destination.stat().st_size,
                    "projected_prefix_fingerprint": _session_prefix_fingerprint(
                        destination,
                        destination.stat().st_size,
                    ),
                }
                result["projected_count"] = int(result["projected_count"]) + 1
                result["changed"] = True
                continue
            if not schema_mismatch and _manifest_row_is_fresh(
                prior,
                source_relative=relative,
                projected_relative=projected_relative,
                size=stat_result.st_size,
                mtime_ns=stat_result.st_mtime_ns,
            ) and _session_projection_destination_matches(prior, destination):
                current_row = dict(prior) if isinstance(prior, dict) else manifest_row
                current_row["thread_id"] = str(identity["thread_id"])
                if current_row != prior:
                    result["changed"] = True
                current_files[relative] = current_row
                result["projected_count"] = int(result["projected_count"]) + 1
                continue
            appended = False
            translated = False
            projected_cwd = ""
            if (
                not schema_mismatch
                and isinstance(prior, dict)
                and prior.get("source_relative") == relative
                and prior.get("projected_relative") == projected_relative
                and isinstance(prior.get("size"), int)
                and 0 < int(prior["size"]) < stat_result.st_size
                and destination.is_file()
            ):
                prior_size = int(prior["size"])
                expected_target_size = int(prior.get("projected_size") or destination.stat().st_size)
                recorded_fingerprint = str(prior.get("source_prefix_fingerprint") or "")
                current_fingerprint = _session_prefix_fingerprint(source, prior_size)
                with source.open("rb") as boundary_handle:
                    boundary_handle.seek(prior_size - 1)
                    prior_boundary = boundary_handle.read(1)
                    boundary_handle.seek(stat_result.st_size - 1)
                    current_boundary = boundary_handle.read(1)
                if (
                    _session_projection_destination_matches(prior, destination)
                    and destination.stat().st_size == expected_target_size
                    and prior_boundary == b"\n"
                    and current_boundary == b"\n"
                    and current_fingerprint
                    and (not recorded_fingerprint or recorded_fingerprint == current_fingerprint)
                ):
                    translated, projected_cwd = _session_projection_append(
                        source,
                        destination,
                        start=prior_size,
                        end=stat_result.st_size,
                    )
                    appended = True
                    result["incremental_count"] = int(result.get("incremental_count") or 0) + 1
            if not appended:
                translated, projected_cwd = _session_projection_file(source, destination)
            final_stat = source.stat()
            final_signature = {"size": final_stat.st_size, "mtime_ns": final_stat.st_mtime_ns}
            if final_signature != signature and not appended:
                translated_again, projected_cwd_again = _session_projection_file(source, destination)
                translated = translated or translated_again
                projected_cwd = projected_cwd_again or projected_cwd
                final_stat = source.stat()
                final_signature = {"size": final_stat.st_size, "mtime_ns": final_stat.st_mtime_ns}
            elif appended:
                final_signature = signature
            current_files[relative] = {
                "source_relative": relative,
                "projected_relative": projected_relative,
                "thread_id": str(identity["thread_id"]),
                **final_signature,
                "source_prefix_fingerprint": _session_prefix_fingerprint(source, int(final_signature["size"])),
                "projected_size": destination.stat().st_size,
                "projected_prefix_fingerprint": _session_prefix_fingerprint(
                    destination,
                    destination.stat().st_size,
                ),
            }
            result["projected_count"] = int(result["projected_count"]) + 1
            result["translated_count"] = int(result["translated_count"]) + int(translated)
            result["changed"] = True
            if projected_cwd and len(result.setdefault("sample_cwds", [])) < 5:
                result.setdefault("sample_cwds", []).append({"source": relative, "cwd": projected_cwd})
        previous_projected_rows = {
            str(value.get("projected_relative") or relative) if isinstance(value, dict) else str(relative): value
            for relative, value in previous_files.items()
        }
        previous_projected_paths = set(previous_projected_rows)
        for relative in previous_projected_paths - current_projected_paths:
            stale = safe_projection_destination(
                target,
                safe_session_relative_path(relative),
                create=False,
            )
            if stale.is_file():
                prior = previous_projected_rows.get(relative)
                if schema_mismatch or not _session_projection_destination_matches(prior, stale):
                    result["stale_preserved_count"] = int(result["stale_preserved_count"]) + 1
                    if len(result.setdefault("stale_preserved", [])) < 20:
                        result.setdefault("stale_preserved", []).append({
                            "projected_relative": relative,
                            "reason": "projection_destination_diverged",
                        })
                    continue
                stale.unlink()
                result["changed"] = True
        manifest = {
            "schema": SESSION_PROJECTION_SCHEMA,
            "source": str(WINDOWS_SESSIONS),
            "target": str(target),
            "generated_at": now_iso(),
            "files": current_files,
        }
        if result["changed"] or not SESSION_MANIFEST.is_file():
            SESSION_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
            content = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            atomic_write_bytes(SESSION_MANIFEST, lambda handle: handle.write(content))
    else:
        existing_count = 0
        fresh_count = 0
        for source in source_files:
            try:
                identity = identity_for(source)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                result["status"] = "session_metadata_unreadable"
                result["ok"] = False
                result["error"] = str(exc)
                return result
            relative = str(identity["source_relative"])
            projected_relative = str(identity["projected_relative"])
            try:
                destination = safe_projection_destination(
                    target,
                    safe_session_relative_path(projected_relative),
                    create=False,
                )
            except ValueError as exc:
                result["status"] = "unsafe_target_path"
                result["ok"] = False
                result["error"] = str(exc)
                return result
            if not destination.is_file():
                continue
            existing_count += 1
            if relative in conflict_by_source:
                continue
            stat_result = source.stat()
            manifest_row = {
                "source_relative": relative,
                "projected_relative": projected_relative,
                "size": stat_result.st_size,
                "mtime_ns": stat_result.st_mtime_ns,
            }
            if not schema_mismatch and _manifest_row_is_fresh(
                previous_files.get(relative),
                source_relative=relative,
                projected_relative=projected_relative,
                size=stat_result.st_size,
                mtime_ns=stat_result.st_mtime_ns,
            ):
                fresh_count += 1
        result["existing_count"] = existing_count
        result["projected_count"] = fresh_count
        complete_count = fresh_count + int(result["conflict_count"])
        current = (
            complete_count == result["source_count"]
            and SESSION_MANIFEST.is_file()
            and not int(result.get("identity_upgrade_count") or 0)
        )
        result["status"] = (
            "projected_with_conflicts"
            if current and result["conflict_count"]
            else "projected" if current else "would_project"
        )
        result["changed"] = not current
        return result
    result["native_files_preserved"] = sum(
        1
        for path in target.rglob("*.jsonl")
        if path.relative_to(target).as_posix() not in current_projected_paths
    )
    result["status"] = (
        "projected_with_conflicts"
        if result["conflict_count"] or result["stale_preserved_count"]
        else "projected"
    )
    return result


def project_state_db(*, write: bool) -> dict[str, object]:
    """Merge native thread-list metadata into WSL without replacing WSL policy state."""
    result: dict[str, object] = {
        "path": str(STATE_DB),
        "source": str(WINDOWS_STATE_DB),
        "status": "missing_optional",
        "rows": 0,
        "source_rows": 0,
        "source_session_count": 0,
        "source_missing_row_count": 0,
        "metadata_update_count": 0,
        "inserted_count": 0,
        "translated_count": 0,
        "local_rows_preserved": 0,
        "changed": False,
        "source_rejected_row_count": 0,
        "session_metadata_fallback_count": 0,
        "ok": True,
    }
    if not STATE_DB.is_file():
        return result
    session_targets: dict[str, str] = {}
    session_sources: dict[str, Path] = {}
    session_identities: dict[str, dict[str, object]] = {}
    duplicate_ids: set[str] = set()
    loaded_manifest = _load_manifest()
    manifest_files_value = loaded_manifest.get("files")
    manifest_files = manifest_files_value if isinstance(manifest_files_value, dict) else {}
    manifest_schema_mismatch = bool(loaded_manifest.get("schema_mismatch"))
    if WINDOWS_SESSIONS.is_dir():
        for source in sorted(WINDOWS_SESSIONS.rglob("*.jsonl")):
            try:
                if source.is_symlink():
                    raise ValueError(f"symlinked source session: {source}")
                identity = session_projection_identity_from_manifest(
                    source,
                    WINDOWS_SESSIONS,
                    previous_files=manifest_files,
                    schema_mismatch=manifest_schema_mismatch,
                )
            except (OSError, json.JSONDecodeError, UnicodeError, ValueError) as exc:
                result["status"] = "session_metadata_unreadable"
                result["error"] = f"{source}: {exc}"
                result["ok"] = False
                return result
            thread_id = str(identity["thread_id"])
            target = CODEX_HOME / "sessions" / str(identity["projected_relative"])
            if thread_id in session_targets and session_targets[thread_id] != str(target):
                duplicate_ids.add(thread_id)
            session_targets[thread_id] = str(target)
            session_sources[thread_id] = source
            session_identities[thread_id] = identity
    result["source_session_count"] = len(session_targets)
    if duplicate_ids:
        result["status"] = "duplicate_session_ids"
        result["duplicate_ids"] = sorted(duplicate_ids)[:10]
        result["ok"] = False
        return result

    text_fill_fields = ("title", "first_user_message", "preview", "thread_source", "history_mode")
    max_fields = (
        "tokens_used",
        "has_user_event",
        "updated_at",
        "updated_at_ms",
        "recency_at",
        "recency_at_ms",
    )
    min_fields = ("created_at", "created_at_ms")
    projected_cwds: dict[str, str] = {}

    def project_cwd(value: object) -> str:
        raw = str(value or "")
        if raw not in projected_cwds:
            projected_cwds[raw] = windows_cwd_to_wsl(raw)[0]
        return projected_cwds[raw]

    def table_columns(connection: sqlite3.Connection) -> list[tuple[str, bool, object]]:
        return [(str(row[1]), bool(row[3]), row[4]) for row in connection.execute("PRAGMA table_info(threads)")]

    def row_dict(row: sqlite3.Row) -> dict[str, object]:
        return {key: row[key] for key in row.keys()}

    def quote_identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def min_present(left: object, right: object) -> object:
        values = [value for value in (left, right) if value is not None and value != 0]
        return min(values) if values else left if left is not None else right

    def max_present(left: object, right: object) -> object:
        values = [value for value in (left, right) if value is not None]
        return max(values) if values else None

    try:
        state_uri = f"file:{STATE_DB.as_posix()}?mode={'rw' if write else 'ro'}"
        connection = sqlite3.connect(state_uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        destination_schema = table_columns(connection)
        destination_columns = [name for name, _, _ in destination_schema]
        if "id" not in destination_columns:
            raise sqlite3.DatabaseError("WSL threads table has no id column")
        destination_rows = {
            str(row["id"]): row_dict(row)
            for row in connection.execute("SELECT * FROM threads")
        }
        result["rows"] = len(destination_rows)
        result["local_rows_preserved"] = len(set(destination_rows) - set(session_targets))

        source_rows: dict[str, dict[str, object]] = {}
        source_columns: list[str] = []
        state_thread_ids: set[str] = set()
        rejected_thread_ids: set[str] = set()
        state_snapshot_available = WINDOWS_STATE_DB.is_file()
        if state_snapshot_available:
            source_uri = f"file:{WINDOWS_STATE_DB.as_posix()}?mode=ro"
            source_connection = sqlite3.connect(source_uri, uri=True, timeout=5)
            source_connection.row_factory = sqlite3.Row
            source_connection.execute("PRAGMA query_only = ON")
            source_connection.execute("BEGIN")
            source_columns = [name for name, _, _ in table_columns(source_connection)]
            if "id" not in source_columns:
                raise sqlite3.DatabaseError("Windows threads table has no id column")
            rejected_rows: list[dict[str, str]] = []
            for row in source_connection.execute("SELECT * FROM threads"):
                thread_id = str(row["id"])
                state_thread_ids.add(thread_id)
                if thread_id not in session_targets:
                    continue
                source_row = row_dict(row)
                reasons: list[str] = []
                if int(source_row.get("archived") or 0) != 0:
                    reasons.append("archived")
                source_rollout = windows_file_path_to_wsl(str(source_row.get("rollout_path") or ""))
                if source_rollout is None or source_rollout != session_sources[thread_id]:
                    reasons.append("rollout_path_mismatch")
                if reasons:
                    rejected_rows.append({"id": thread_id, "reason": ",".join(reasons)})
                    rejected_thread_ids.add(thread_id)
                    continue
                source_rows[thread_id] = source_row
            result["source_rows"] = len(source_rows)
            result["source_rejected_row_count"] = len(rejected_rows)
            if rejected_rows:
                result["source_rejected_rows"] = rejected_rows[:10]
            source_connection.rollback()
            source_connection.close()

        fallback_ids = sorted(
            thread_id
            for thread_id in session_targets
            if state_snapshot_available and thread_id not in state_thread_ids and thread_id not in rejected_thread_ids
        )
        session_metadata_rows: dict[str, dict[str, object]] = {}
        for thread_id in fallback_ids:
            identity = session_identities[thread_id]
            session_metadata_rows[thread_id] = {
                "id": thread_id,
                "rollout_path": session_targets[thread_id],
                "created_at": 0,
                "updated_at": 0,
                "source": "wsl_session_projection",
                "model_provider": "custom",
                "cwd": project_cwd(identity.get("cwd")),
                "title": "",
                "tokens_used": 0,
                "has_user_event": 0,
                "archived": 0,
                "created_at_ms": 0,
                "updated_at_ms": 0,
                "thread_source": "wsl_session_projection",
                "preview": "",
                "recency_at": 0,
                "recency_at_ms": 0,
                "history_mode": "session_meta_only",
            }
        result["session_metadata_fallback_count"] = len(session_metadata_rows)
        missing_source_ids = sorted(set(session_targets) - set(source_rows) - set(session_metadata_rows))
        result["source_missing_row_count"] = len(missing_source_ids)
        if missing_source_ids:
            result["source_missing_ids"] = missing_source_ids[:10]

        updates: list[tuple[str, dict[str, object]]] = []
        inserts: list[dict[str, object]] = []
        for thread_id, source in {**source_rows, **session_metadata_rows}.items():
            target = destination_rows.get(thread_id)
            projected_rollout = session_targets[thread_id]
            projected_cwd = project_cwd(source.get("cwd"))
            if target is None:
                inserted = {
                    column: source.get(column)
                    for column in destination_columns
                    if column in source and (column in source_columns or thread_id in session_metadata_rows)
                    and column in SAFE_INSERT_SOURCE_FIELDS
                }
                inserted["id"] = thread_id
                inserted["rollout_path"] = projected_rollout
                inserted["cwd"] = projected_cwd
                if "sandbox_policy" in destination_columns:
                    inserted["sandbox_policy"] = source.get("sandbox_policy") or SAFE_INSERT_SANDBOX_POLICY
                if "approval_mode" in destination_columns:
                    inserted["approval_mode"] = source.get("approval_mode") or SAFE_INSERT_APPROVAL_MODE
                if "archived" in destination_columns:
                    inserted["archived"] = 0
                if "archived_at" in destination_columns:
                    inserted["archived_at"] = None
                inserts.append(inserted)
                continue

            merged: dict[str, object] = {
                "rollout_path": projected_rollout,
                "cwd": projected_cwd,
            }
            for field in text_fill_fields:
                if field in destination_columns and field in source_columns:
                    merged[field] = target.get(field) or source.get(field)
            for field in max_fields:
                if field in destination_columns and field in source_columns:
                    merged[field] = max_present(target.get(field), source.get(field))
            for field in min_fields:
                if field in destination_columns and field in source_columns:
                    merged[field] = min_present(target.get(field), source.get(field))
            changed_values = {field: value for field, value in merged.items() if target.get(field) != value}
            if changed_values:
                updates.append((thread_id, changed_values))
                result["translated_count"] = int(result["translated_count"]) + int(
                    target.get("cwd") != projected_cwd
                )

        updated_ids = {thread_id for thread_id, _ in updates}
        for thread_id, target in destination_rows.items():
            if thread_id in updated_ids:
                continue
            current_cwd = str(target.get("cwd") or "")
            projected_cwd = project_cwd(current_cwd)
            if projected_cwd != current_cwd:
                updates.append((thread_id, {"cwd": projected_cwd}))
                result["translated_count"] = int(result["translated_count"]) + 1

        result["metadata_update_count"] = len(updates)
        result["inserted_count"] = len(inserts)
        result["changed"] = bool(updates or inserts)

        if write and (updates or inserts):
            connection.execute("BEGIN IMMEDIATE")
            for thread_id, values in updates:
                assignments = ", ".join(f"{quote_identifier(field)} = ?" for field in values)
                connection.execute(
                    f"UPDATE threads SET {assignments} WHERE id = ?",
                    (*values.values(), thread_id),
                )
            for values in inserts:
                missing_required = [
                    name
                    for name, not_null, default in destination_schema
                    if not_null and default is None and name not in values
                ]
                if missing_required:
                    raise sqlite3.DatabaseError(
                        "Windows threads schema cannot populate WSL required columns: "
                        + ", ".join(missing_required)
                    )
                columns = list(values)
                placeholders = ", ".join("?" for _ in columns)
                connection.execute(
                    f"INSERT INTO threads ({', '.join(quote_identifier(column) for column in columns)}) "
                    f"VALUES ({placeholders})",
                    tuple(values[column] for column in columns),
                )
            connection.commit()
        connection.close()
        result["status"] = "updated" if write and result["changed"] else "would_update" if result["changed"] else "ready"
        if result["source_rejected_row_count"] or result["source_missing_row_count"]:
            result["status"] += "_with_source_gaps"
    except (OSError, sqlite3.Error) as exc:
        result["status"] = "locked_or_unreadable"
        result["error"] = str(exc)
        result["ok"] = False
    return result


def workspace_thread_index() -> dict[str, object]:
    """Return only top-level tasks whose cwd belongs to the WSL Work Git."""
    if not STATE_DB.is_file():
        return {
            "schema": "codex-wsl-runtime.workspace-thread-index.v1",
            "ok": False,
            "status": "state_db_missing",
            "path": str(STATE_DB),
            "thread_ids": [],
            "thread_cwds": {},
            "thread_count": 0,
        }

    root = str(ROOT).rstrip("/")
    try:
        connection = sqlite3.connect(f"file:{STATE_DB.as_posix()}?mode=ro", uri=True, timeout=5)
        connection.execute("PRAGMA query_only = ON")
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")}
        required = {"id", "source", "rollout_path", "cwd", "archived"}
        if not required.issubset(columns):
            connection.close()
            return {
                "schema": "codex-wsl-runtime.workspace-thread-index.v1",
                "ok": False,
                "status": "state_db_schema_incomplete",
                "path": str(STATE_DB),
                "missing_columns": sorted(required - columns),
                "thread_ids": [],
                "thread_cwds": {},
                "thread_count": 0,
            }
        rows = connection.execute(
            "SELECT id, source, rollout_path, cwd FROM threads WHERE archived = 0"
        ).fetchall()
        connection.close()
    except (OSError, sqlite3.Error) as exc:
        return {
            "schema": "codex-wsl-runtime.workspace-thread-index.v1",
            "ok": False,
            "status": "state_db_unreadable",
            "path": str(STATE_DB),
            "error": str(exc),
            "thread_ids": [],
            "thread_cwds": {},
            "thread_count": 0,
        }

    thread_cwds: dict[str, str] = {}
    missing_session_count = 0
    for thread_id, source, rollout_path, cwd in rows:
        source_text = str(source or "").strip()
        if source_text.startswith("{"):
            try:
                parsed_source = json.loads(source_text)
            except json.JSONDecodeError:
                parsed_source = None
            if isinstance(parsed_source, dict) and "subagent" in parsed_source:
                continue
        normalized_cwd = str(cwd or "").rstrip("/")
        if normalized_cwd != root and not normalized_cwd.startswith(root + "/"):
            continue
        if not Path(str(rollout_path or "")).is_file():
            missing_session_count += 1
            continue
        thread_cwds[str(thread_id)] = str(cwd or "")

    thread_ids = list(thread_cwds)
    return {
        "schema": "codex-wsl-runtime.workspace-thread-index.v1",
        "ok": True,
        "status": "ready",
        "path": str(STATE_DB),
        "workspace_root": str(ROOT),
        "thread_ids": thread_ids,
        "thread_cwds": thread_cwds,
        "thread_count": len(thread_ids),
        "missing_session_count": missing_session_count,
    }


def portable_root_values_from_text(text: str) -> dict[str, object]:
    """Keep explicitly portable user choices without retaining runtime paths."""

    if not text.strip():
        return {}
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    return {
        key: parsed[key]
        for key in PORTABLE_CONFIG_ROOTS
        if isinstance(parsed.get(key), (str, bool, int, float))
    }


def portable_root_text(values: dict[str, object] | None) -> str:
    lines: list[str] = []
    for key in PORTABLE_CONFIG_ROOTS:
        value = (values or {}).get(key)
        if isinstance(value, str):
            rendered = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (int, float)):
            rendered = repr(value)
        else:
            continue
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines)


def desktop_table_from_config(path: Path) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == "[desktop]"), None)
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    table = "\n".join(lines[start:end]).rstrip() + "\n"
    tomllib.loads(table)
    return table


def render_config(*, desktop_table: str = "", portable_values: dict[str, object] | None = None) -> str:
    if not TEMPLATE.is_file():
        raise FileNotFoundError(TEMPLATE)
    if not NODE_WRAPPER.is_file():
        raise FileNotFoundError(NODE_WRAPPER)
    if not NODE_PROXY.is_file():
        raise FileNotFoundError(NODE_PROXY)
    replacements = {
        "__WSL_WORKSPACE_ROOT__": str(ROOT),
        "__WSL_CODEX_HOME__": str(CODEX_HOME),
        "__WSL_NODE_REPL_ENTRY__": str(NODE_ENTRY),
        "__WSL_DESKTOP_TABLE__": desktop_table.rstrip(),
        "__WSL_PLUGIN_MARKETPLACES__": _plugin_config_tables()[0],
        "__WSL_PLUGIN_TABLE__": _plugin_config_tables()[1],
    }
    rendered = TEMPLATE.read_text(encoding="utf-8")
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    portable_text = portable_root_text(portable_values)
    if portable_text:
        rendered = f"{portable_text}\n{rendered}"
    if "<SECRET:" in rendered or "C:\\Users\\" in rendered:
        raise ValueError("WSL config contains a secret placeholder or Windows-only path")
    return rendered.rstrip() + "\n"


def render_hooks() -> str:
    if not HOOKS_TEMPLATE.is_file():
        raise FileNotFoundError(HOOKS_TEMPLATE)
    rendered = HOOKS_TEMPLATE.read_text(encoding="utf-8").replace("__WSL_WORKSPACE_ROOT__", str(ROOT))
    payload = json.loads(rendered)
    if set((payload.get("hooks") or {}).keys()) != {"UserPromptSubmit", "PostToolUse", "Stop"}:
        raise ValueError("WSL hook template does not contain the governed observer event set")
    if "C:\\Users\\" in rendered or "mcsmanager_windows_release" in rendered:
        raise ValueError("WSL hooks contain a Windows-only path")
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def hooks_projection(*, write: bool) -> dict[str, object]:
    """Project only governed hooks without touching sessions or other runtime roots."""

    hooks = CODEX_HOME / "hooks.json"
    rendered = render_hooks()
    current = hooks.read_text(encoding="utf-8") if hooks.is_file() else ""
    changed = current != rendered
    backup: dict[str, object] | None = None
    if write and changed and hooks.is_file():
        backup = create_backup(
            [str(hooks)],
            remark="wsl-codex-hooks-materialize",
            purpose="Atomic WSL Codex observer hook projection",
            category="wsl-desktop-runtime",
        )
        if not backup.get("ok"):
            return {
                "schema": "codex-wsl-hooks.v1",
                "ok": False,
                "write": True,
                "changed": False,
                "status": "backup_failed",
                "backup": backup,
            }
    if write and changed:
        CODEX_HOME.mkdir(parents=True, exist_ok=True)
        state_lease = state_write_authority.try_acquire_state_write_lease(
            "codex_config",
            "wsl_codex_runtime",
            state_root=DEFAULT_HOST_CONFIG.parent,
            timeout_seconds=10.0,
        )
        if state_lease is None:
            return {
                "schema": "codex-wsl-hooks.v1",
                "ok": False,
                "write": True,
                "changed": False,
                "status": "state_write_authority_busy",
            }
        try:
            current_after_lease = hooks.read_text(encoding="utf-8") if hooks.is_file() else ""
            if current_after_lease != current:
                return {
                    "schema": "codex-wsl-hooks.v1",
                    "ok": False,
                    "write": True,
                    "changed": False,
                    "status": "source_changed_before_hooks_write",
                }
            state_lease.assert_current()
            atomic_write_text(hooks, rendered)
        finally:
            state_lease.release()
    current_hash = sha256(hooks) if hooks.is_file() else ""
    expected_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return {
        "schema": "codex-wsl-hooks.v1",
        "ok": bool((write and current_hash == expected_hash) or (not write)),
        "write": write,
        "changed": changed,
        "path": str(hooks),
        "expected_sha256": expected_hash,
        "actual_sha256": current_hash,
        "matches_template": current_hash == expected_hash,
        "events": ["UserPromptSubmit", "PostToolUse", "Stop"],
        "activation": "requires_codex_reload_and_exact_hash_trust" if changed else "projection_current",
        "unrelated_runtime_projection_touched": False,
        "backup": backup,
    }


def plugins_projection(*, write: bool) -> dict[str, object]:
    """Project only plugin config/cache state and reject unrelated config drift."""

    config = CODEX_HOME / "config.toml"
    if not config.is_file():
        return {
            "schema": "codex-wsl-plugins.v1",
            "ok": False,
            "write": write,
            "changed": False,
            "status": "config_missing",
        }
    current = config.read_text(encoding="utf-8")
    try:
        current_payload = tomllib.loads(current)
        rendered, _ = codex_state_repair.remove_table_tree(current, ("marketplaces", "plugins"))
        marketplace_text, plugin_text = _plugin_config_tables()
        blocks = [rendered.rstrip(), marketplace_text, plugin_text]
        rendered = "\n\n".join(block for block in blocks if block).rstrip() + "\n"
        desired_payload = tomllib.loads(rendered)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        return {
            "schema": "codex-wsl-plugins.v1",
            "ok": False,
            "write": write,
            "changed": False,
            "status": "config_unreadable",
            "error": type(exc).__name__,
        }
    current_other = {key: value for key, value in current_payload.items() if key not in {"marketplaces", "plugins"}}
    desired_other = {key: value for key, value in desired_payload.items() if key not in {"marketplaces", "plugins"}}
    unrelated_config_matches = current_other == desired_other
    config_matches = all(
        current_payload.get(key) == desired_payload.get(key)
        for key in ("marketplaces", "plugins")
    )
    preview = project_plugins(write=False)
    if not unrelated_config_matches:
        return {
            "schema": "codex-wsl-plugins.v1",
            "ok": False,
            "write": write,
            "changed": False,
            "status": "unrelated_config_drift",
            "unrelated_runtime_projection_touched": False,
            "plugin_projection": preview,
        }
    changed = not config_matches or bool(preview.get("changed")) or bool(preview.get("missing"))
    if not write:
        return {
            "schema": "codex-wsl-plugins.v1",
            "ok": not bool(preview.get("missing")),
            "write": False,
            "changed": changed,
            "status": "would_project" if changed else "projection_current",
            "config_matches": config_matches,
            "unrelated_config_matches": True,
            "unrelated_runtime_projection_touched": False,
            "plugin_projection": preview,
        }
    backup: dict[str, object] | None = None
    if changed and config.is_file():
        backup = create_backup(
            [str(config)],
            remark="wsl-codex-plugins-materialize",
            purpose="Focused WSL Codex plugin and marketplace projection",
            category="wsl-desktop-runtime",
        )
        if not backup.get("ok"):
            return {
                "schema": "codex-wsl-plugins.v1",
                "ok": False,
                "write": True,
                "changed": False,
                "status": "backup_failed",
                "backup": backup,
            }
    lease = state_write_authority.try_acquire_state_write_lease(
        "codex_config",
        "wsl_codex_runtime",
        state_root=DEFAULT_HOST_CONFIG.parent,
        timeout_seconds=10.0,
    )
    if lease is None:
        return {
            "schema": "codex-wsl-plugins.v1",
            "ok": False,
            "write": True,
            "changed": False,
            "status": "state_write_authority_busy",
        }
    try:
        current_after_lease = config.read_text(encoding="utf-8") if config.is_file() else ""
        if current_after_lease != current:
            return {
                "schema": "codex-wsl-plugins.v1",
                "ok": False,
                "write": True,
                "changed": False,
                "status": "source_changed_before_plugin_write",
            }
        lease.assert_current()
        applied = project_plugins(write=True)
        if applied.get("missing"):
            return {
                "schema": "codex-wsl-plugins.v1",
                "ok": False,
                "write": True,
                "changed": True,
                "status": "plugin_projection_incomplete",
                "backup": backup,
                "plugin_projection": applied,
            }
        if not config_matches:
            atomic_write_text(config, rendered)
    finally:
        lease.release()
    after = plugins_projection(write=False)
    return {
        **after,
        "schema": "codex-wsl-plugins.v1",
        "ok": bool(after.get("ok") and not after.get("changed")),
        "write": True,
        "changed": changed,
        "status": "projected" if after.get("ok") and not after.get("changed") else "post_projection_validation_failed",
        "backup": backup,
    }


def materialize(*, write: bool) -> dict[str, object]:
    if write:
        CODEX_HOME.mkdir(parents=True, exist_ok=True)
        SQLITE_HOME.mkdir(parents=True, exist_ok=True)
    config = CODEX_HOME / "config.toml"
    hooks = CODEX_HOME / "hooks.json"
    environment_selection = reconcile_environment_selection(
        host_config=DEFAULT_HOST_CONFIG,
        wsl_config=config,
        state_path=CODEX_HOME / "state" / "desktop-environment-selection.json",
        host_state_path=DEFAULT_HOST_STATE_PATH,
        write=write,
    )
    current = config.read_text(encoding="utf-8") if config.is_file() else ""
    portable_values = portable_root_values_from_text(current)
    rendered = render_config(
        desktop_table=desktop_table_from_config(config),
        portable_values=portable_values,
    )
    hooks_current = hooks.read_text(encoding="utf-8") if hooks.is_file() else ""
    hooks_rendered = render_hooks()
    profile_current = PROFILE_PATH.read_text(encoding="utf-8") if PROFILE_PATH.is_file() else ""
    profile_rendered = render_profile(profile_current)
    config_changed = current != rendered
    profile_changed = profile_current != profile_rendered
    hooks_changed = hooks_current != hooks_rendered
    changed = config_changed or hooks_changed or profile_changed or bool(environment_selection.get("changed"))
    backup: dict[str, object] | None = None
    backup_paths = [
        str(path)
        for path, required in ((config, config_changed), (hooks, hooks_changed), (PROFILE_PATH, profile_changed))
        if write and required and path.is_file()
    ]
    if backup_paths:
        backup = create_backup(
            backup_paths,
            remark="wsl-codex-runtime-materialize",
            purpose="Atomic WSL Codex runtime config materialization",
            category="wsl-desktop-runtime",
        )
        if not backup.get("ok"):
            return {
                "schema": "codex-wsl-runtime.v1",
                "ok": False,
                "degraded": True,
                "generated_at": now_iso(),
                "write": write,
                "changed": changed,
                "status": "backup_failed",
                "backup": backup,
                "environment_selection": environment_selection,
            }
    links = []
    links.append(
        link_or_verify(NODE_WRAPPER, NODE_ENTRY)
        if write
        else managed_link_status(NODE_WRAPPER, NODE_ENTRY)
    )
    for name in ("AGENTS.md", "MEMORY.md", "USER_WORKING_PREFERENCES.md", "skills", "scripts", "tools", "automations"):
        source = ROOT / "codex-home" / name
        target = CODEX_HOME / name
        if source.exists():
            if name == "skills":
                links.append(link_skill_tree(source, target, write=write))
            else:
                links.append(link_or_verify(source, target) if write else managed_link_status(source, target))
    session_projection = project_sessions(write=write)
    state_projection = project_state_db(write=write)
    plugin_projection = project_plugins(write=write)
    required_link_ok = bool(links and links[0].get("ok", True))
    state_complete = bool(
        state_projection.get("ok", True)
        and not state_projection.get("source_rejected_row_count")
        and not state_projection.get("source_missing_row_count")
    )
    degraded = bool(
        not session_projection.get("ok", True)
        or not state_complete
        or not plugin_projection.get("ok", True)
        or any(link.get("ok") is False for link in links[1:])
    )
    changed = changed or bool(session_projection.get("changed")) or bool(state_projection.get("changed"))
    if write and (config_changed or hooks_changed):
        state_lease = state_write_authority.try_acquire_state_write_lease(
            "codex_config",
            "wsl_codex_runtime",
            state_root=DEFAULT_HOST_CONFIG.parent,
            timeout_seconds=10.0,
        )
        if state_lease is None:
            return {
                "schema": "codex-wsl-runtime.v1",
                "ok": False,
                "status": "state_write_authority_busy",
                "write": True,
                "changed": False,
            }
        try:
            current_after_lease = config.read_text(encoding="utf-8") if config.is_file() else ""
            hooks_after_lease = hooks.read_text(encoding="utf-8") if hooks.is_file() else ""
            if current_after_lease != current or hooks_after_lease != hooks_current:
                return {
                    "schema": "codex-wsl-runtime.v1",
                    "ok": False,
                    "status": "source_changed_before_config_or_hooks_write",
                    "write": True,
                    "changed": False,
                }
            state_lease.assert_current()
            if config_changed:
                atomic_write_text(config, rendered)
            if hooks_changed:
                atomic_write_text(hooks, hooks_rendered)
        finally:
            state_lease.release()
    if write and profile_changed:
        atomic_write_text(PROFILE_PATH, profile_rendered)
    return {
        "schema": "codex-wsl-runtime.v1",
        "ok": required_link_ok,
        "degraded": degraded,
        "generated_at": now_iso(),
        "write": write,
        "changed": changed,
        "root": str(ROOT),
        "codex_home": str(CODEX_HOME),
        "sqlite_home": str(SQLITE_HOME),
        "config": str(config),
        "config_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "hooks": str(hooks),
        "hooks_changed": hooks_changed,
        "hooks_sha256": hashlib.sha256(hooks_rendered.encode("utf-8")).hexdigest(),
        "hooks_activation": "requires_codex_reload_and_exact_hash_trust",
        "preserved_portable_roots": sorted(portable_values),
        "desktop_profile": str(PROFILE_PATH),
        "desktop_profile_changed": profile_changed,
        "desktop_profile_sha256": hashlib.sha256(profile_rendered.encode("utf-8")).hexdigest(),
        "links": links,
        "session_projection": session_projection,
        "state_projection": state_projection,
        "plugin_projection": plugin_projection,
        "secrets_imported": False,
        "windows_runtime_imported": False,
        "session_state_imported": state_complete,
        "session_continuity": "isolated_wsl_session_projection",
        "environment_selection": environment_selection,
        "backup": backup,
    }


def validate() -> dict[str, object]:
    result = materialize(write=False)
    config = CODEX_HOME / "config.toml"
    result["config_exists"] = config.is_file()
    result["config_matches_template"] = bool(config.is_file() and sha256(config) == result["config_sha256"])
    hooks = CODEX_HOME / "hooks.json"
    result["hooks_match_template"] = bool(hooks.is_file() and sha256(hooks) == result["hooks_sha256"])
    result["desktop_profile_current"] = bool(
        PROFILE_PATH.is_file()
        and sha256(PROFILE_PATH) == result["desktop_profile_sha256"]
    )
    result["node_wrapper_exists"] = NODE_WRAPPER.is_file()
    result["node_proxy_exists"] = NODE_PROXY.is_file()
    result["node_entry_ok"] = bool(
        NODE_ENTRY.is_symlink()
        and NODE_ENTRY.resolve() == NODE_WRAPPER.resolve()
        and os.access(NODE_ENTRY, os.X_OK)
    )
    result["node_repl_exists"] = Path("/mnt/c/Users/45543/.local/bin/node_repl.exe").is_file()
    session_projection = result.get("session_projection") or {}
    state_projection = result.get("state_projection") or {}
    result["session_continuity_ok"] = bool(
        session_projection.get("status")
        in {"projected", "projected_with_conflicts", "source_missing_optional"}
        and session_projection.get("source_count")
        == int(session_projection.get("projected_count") or 0)
        + int(session_projection.get("conflict_count") or 0)
    )
    result["state_projection_ok"] = state_projection.get("status") in {"ready", "missing_optional"}
    result["required"] = [
        "config_exists",
        "config_matches_template",
        "hooks_match_template",
        "desktop_profile_current",
        "node_wrapper_exists",
        "node_proxy_exists",
        "node_entry_ok",
        "node_repl_exists",
        "session_continuity_ok",
        "state_projection_ok",
    ]
    result["ok"] = all(bool(result[key]) for key in result["required"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize the WSL Codex runtime projection")
    parser.add_argument(
        "command",
        choices=("plan", "apply", "validate", "hooks-plan", "hooks-apply", "hooks-validate", "plugins-plan", "plugins-apply", "plugins-validate", "skills-plan", "skills-apply", "skills-validate", "thread-index"),
    )
    parser.add_argument("--windows-state-snapshot", type=Path)
    args = parser.parse_args(argv)
    global WINDOWS_STATE_DB
    if args.windows_state_snapshot is not None:
        WINDOWS_STATE_DB = args.windows_state_snapshot
    if args.command in {"hooks-plan", "hooks-validate"}:
        payload = hooks_projection(write=False)
        if args.command == "hooks-validate":
            payload["ok"] = bool(payload.get("matches_template"))
    elif args.command == "hooks-apply":
        if os.environ.get("CODEX_MIRROR_SOURCE_READ_ONLY") == "1":
            payload = {
                "schema": "codex-wsl-hooks.v1",
                "ok": False,
                "write": False,
                "changed": False,
                "status": "blocked",
                "reason": "mirror_source_read_only",
            }
        else:
            payload = hooks_projection(write=True)
    elif args.command in {"plugins-plan", "plugins-validate"}:
        payload = plugins_projection(write=False)
        if args.command == "plugins-validate":
            payload["ok"] = bool(payload.get("ok") and not payload.get("changed"))
    elif args.command == "plugins-apply":
        if os.environ.get("CODEX_MIRROR_SOURCE_READ_ONLY") == "1":
            payload = {
                "schema": "codex-wsl-plugins.v1",
                "ok": False,
                "write": False,
                "changed": False,
                "status": "blocked",
                "reason": "mirror_source_read_only",
            }
        else:
            payload = plugins_projection(write=True)
    elif args.command in {"skills-plan", "skills-validate"}:
        payload = skills_projection(write=False)
        if args.command == "skills-validate":
            payload["ok"] = bool(payload.get("ok") and not payload.get("changed"))
    elif args.command == "skills-apply":
        if os.environ.get("CODEX_MIRROR_SOURCE_READ_ONLY") == "1":
            payload = {
                "schema": "codex-wsl-skills.v1",
                "ok": False,
                "write": False,
                "changed": False,
                "status": "blocked",
                "reason": "mirror_source_read_only",
            }
        else:
            payload = skills_projection(write=True)
    elif args.command == "plan":
        payload = materialize(write=False)
    elif args.command == "apply":
        if os.environ.get("CODEX_MIRROR_SOURCE_READ_ONLY") == "1":
            payload = {
                "schema": "codex-wsl-runtime.v1",
                "ok": False,
                "write": False,
                "changed": False,
                "generated_at": now_iso(),
                "status": "blocked",
                "reason": "mirror_source_read_only",
                "detail": "WSL runtime apply is blocked while the mirror owner is reading live sources.",
            }
        else:
            payload = materialize(write=True)
    elif args.command == "validate":
        payload = validate()
    else:
        payload = workspace_thread_index()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
