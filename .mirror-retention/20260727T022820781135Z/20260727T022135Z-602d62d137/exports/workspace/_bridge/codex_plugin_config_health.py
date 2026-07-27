#!/usr/bin/env python3
"""Governed Codex plugin health and explicit marketplace installation.

Ownership: plugin cache completeness plus explicit marketplace/plugin installation.
Non-goals: choosing plugins for a task, editing plugin content, or copying
plugin state into source control. State behavior: read-only by default; the
install path is confirmation-, backup-, and lease-protected. Caller context:
the skills lifecycle, WSL plugin projection, and approved environment changes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import codex_state_repair
import state_write_authority
from codex_desktop_environment_selection import atomic_write_text
from shared.backup_router import create_backup


EXPECTED_PLUGINS: dict[str, dict[str, Any]] = {
    "chrome@openai-bundled": {
        "marketplace": "openai-bundled",
        "plugin": "chrome",
        "cli_required": False,
        "cli_visibility_optional": True,
    },
    "computer-use@openai-bundled": {
        "marketplace": "openai-bundled",
        "plugin": "computer-use",
        "cli_required": False,
        "cli_visibility_optional": True,
    },
    "canva@openai-curated": {
        "marketplace": "openai-curated",
        "plugin": "canva",
        "cli_required": False,
        "reserved_marketplace": True,
    },
    "game-studio@openai-curated": {
        "marketplace": "openai-curated",
        "plugin": "game-studio",
        "cli_required": False,
        "reserved_marketplace": True,
    },
    "build-web-apps@openai-api-curated": {
        "marketplace": "openai-api-curated",
        "plugin": "build-web-apps",
        "cli_required": False,
    },
    "hyperframes@openai-api-curated": {
        "marketplace": "openai-api-curated",
        "plugin": "hyperframes",
        "cli_required": True,
    },
    "remotion@openai-api-curated": {
        "marketplace": "openai-api-curated",
        "plugin": "remotion",
        "cli_required": True,
    },
    "mixpanel-headless@openai-api-curated": {
        "marketplace": "openai-api-curated",
        "plugin": "mixpanel-headless",
        "cli_required": True,
    },
    "build-web-data-visualization@openai-api-curated": {
        "marketplace": "openai-api-curated",
        "plugin": "build-web-data-visualization",
        "cli_required": True,
    },
}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def default_config_path() -> Path:
    return codex_home() / "config.toml"


def host_path(value: str | Path) -> Path:
    """Make a Windows executable or source path callable from WSL."""

    text = str(value or "").strip()
    if text.startswith("\\\\?\\"):
        text = text[4:]
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match and os.name != "nt":
        return Path("/mnt") / match.group(1).lower() / match.group(2).replace("\\", "/")
    return Path(text)


def load_toml(path: Path) -> tuple[dict[str, Any], str]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh), ""
    except Exception as exc:
        return {}, str(exc)


def plugin_enabled(config: dict[str, Any], plugin_key: str) -> bool:
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return False
    item = plugins.get(plugin_key)
    return isinstance(item, dict) and item.get("enabled") is True


def _reserved_marketplace(marketplace: str) -> bool:
    return any(str(item.get("marketplace") or "") == marketplace for item in EXPECTED_PLUGINS.values())


def _remove_plugin_declarations(text: str, *, selector: str, marketplace: str) -> tuple[str, dict[str, bool]]:
    """Remove one plugin declaration and its now-unreferenced custom marketplace."""

    updated, plugin_changed = codex_state_repair.remove_table_tree(text, (f'plugins."{selector}"',))
    payload = tomllib.loads(updated)
    plugins = payload.get("plugins") if isinstance(payload.get("plugins"), dict) else {}
    marketplace_referenced = any(
        str(identity).rpartition("@")[2] == marketplace
        for identity in plugins
    )
    marketplace_changed = False
    if not marketplace_referenced and not _reserved_marketplace(marketplace):
        updated, marketplace_changed = codex_state_repair.remove_table_tree(
            updated,
            (f"marketplaces.{marketplace}",),
        )
    tomllib.loads(updated)
    return updated, {
        "plugin_changed": plugin_changed,
        "marketplace_changed": marketplace_changed,
        "marketplace_referenced": marketplace_referenced,
    }


def configured_enabled_plugins(config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Return every enabled plugin contract declared by the active config."""
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return {}, []
    configured: dict[str, dict[str, Any]] = {}
    invalid: list[str] = []
    for identity, settings in sorted(plugins.items()):
        if not isinstance(settings, dict) or settings.get("enabled") is not True:
            continue
        name, separator, marketplace = str(identity).rpartition("@")
        if not separator or not name or not marketplace:
            invalid.append(str(identity))
            continue
        configured[str(identity)] = {
            "marketplace": marketplace,
            "plugin": name,
            "cli_required": False,
            "configured_discovery": True,
        }
    return configured, invalid


def first_manifest(cache_root: Path, marketplace: str, plugin: str) -> Path | None:
    plugin_root = cache_root / marketplace / plugin
    if not plugin_root.exists():
        return None
    for metadata_dir in (".codex-plugin", ".claude-plugin"):
        direct = plugin_root / metadata_dir / "plugin.json"
        if direct.exists():
            return direct
    manifests = sorted(
        [*plugin_root.glob("*/.codex-plugin/plugin.json"), *plugin_root.glob("*/.claude-plugin/plugin.json")]
    )
    return manifests[-1] if manifests else None


def marketplace_files(home: Path) -> dict[str, Path]:
    return {
        "openai-bundled": home / ".tmp" / "bundled-marketplaces" / "openai-bundled" / ".agents" / "plugins" / "marketplace.json",
        "openai-curated": home / ".tmp" / "plugins" / ".agents" / "plugins" / "marketplace.json",
        "openai-api-curated": home / ".tmp" / "plugins" / ".agents" / "plugins" / "api_marketplace.json",
    }


def configured_marketplace_files(home: Path, config: dict[str, Any]) -> dict[str, Path]:
    """Return static and explicitly configured marketplace manifests."""

    files = marketplace_files(home)
    marketplaces = config.get("marketplaces")
    if not isinstance(marketplaces, dict):
        return files
    for name, settings in marketplaces.items():
        if not isinstance(name, str) or name in files or not isinstance(settings, dict):
            continue
        if name == "openai-primary-runtime":
            continue
        source = str(settings.get("source") or "").strip()
        if source:
            if str(settings.get("source_type") or "").strip().casefold() == "git":
                files[name] = home / ".tmp" / "marketplaces" / name / ".claude-plugin" / "marketplace.json"
            else:
                files[name] = host_path(source) / ".agents" / "plugins" / "marketplace.json"
    return files


def codex_cli_from_config(config: dict[str, Any]) -> str:
    servers = config.get("mcp_servers")
    if isinstance(servers, dict):
        node_repl = servers.get("node_repl")
        if isinstance(node_repl, dict):
            env = node_repl.get("env")
            if isinstance(env, dict):
                value = str(env.get("CODEX_CLI_PATH") or "").strip()
                translated = host_path(value)
                if value and translated.exists():
                    return str(translated)
    for candidate in ("codex", "Codex"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return ""


def run_codex_plugin_list(config: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    cli = codex_cli_from_config(config)
    if not cli:
        return {"ok": False, "reason": "codex_cli_not_found", "plugins": {}, "path": ""}
    path = Path(cli)
    if not path.exists() and not shutil.which(cli):
        return {"ok": False, "reason": "codex_cli_path_missing", "plugins": {}, "path": cli}
    try:
        proc = subprocess.run(
            [cli, "plugin", "list", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "plugins": {}, "path": cli}
    seen: dict[str, str] = {}
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    installed = payload.get("installed") if isinstance(payload, dict) else []
    for item in installed if isinstance(installed, list) else []:
        if not isinstance(item, dict):
            continue
        identity = str(item.get("pluginId") or "").strip()
        if identity:
            installed_state = "installed" if item.get("installed") is True else "not_installed"
            enabled_state = "enabled" if item.get("enabled") is True else "disabled"
            seen[identity] = f"{installed_state} {enabled_state}"
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "path": cli,
        "plugins": seen,
        "stderr": (proc.stderr or "").strip(),
    }


def codex_plugin_config_health(
    config_path: str | Path | None = None,
    *,
    run_cli: bool = True,
) -> dict[str, Any]:
    path = Path(config_path) if config_path else default_config_path()
    home = path.parent if config_path else codex_home()
    config, parse_error = load_toml(path)
    cache_root = home / "plugins" / "cache"
    market_files = configured_marketplace_files(home, config)
    marketplace_status = {
        name: {"ok": file.exists(), "path": str(file)}
        for name, file in market_files.items()
    }
    plugins: dict[str, dict[str, Any]] = {}
    missing_enabled: list[str] = []
    missing_cache: list[str] = []
    missing_manifest: list[str] = []
    expected_plugins = dict(EXPECTED_PLUGINS)
    configured_plugins, invalid_configured_plugins = configured_enabled_plugins(config)
    for key, meta in configured_plugins.items():
        expected_plugins.setdefault(key, meta)
    for key, meta in expected_plugins.items():
        marketplace = str(meta["marketplace"])
        plugin = str(meta["plugin"])
        root = cache_root / marketplace / plugin
        manifest = first_manifest(cache_root, marketplace, plugin) if marketplace != "config-only" else None
        enabled = plugin_enabled(config, key)
        cache_ok = root.exists()
        manifest_ok = bool(manifest is not None and manifest.exists()) if marketplace != "config-only" else True
        if not enabled:
            missing_enabled.append(key)
        if not cache_ok:
            missing_cache.append(key)
        if not manifest_ok:
            missing_manifest.append(key)
        plugins[key] = {
            "enabled": enabled,
            "cache_ok": cache_ok,
            "cache_path": str(root),
            "manifest_ok": manifest_ok,
            "manifest_path": str(manifest or ""),
            "cli_required": bool(meta.get("cli_required")),
            "reserved_marketplace": bool(meta.get("reserved_marketplace")),
            "config_only": bool(meta.get("config_only")),
            "configured_discovery": bool(meta.get("configured_discovery")),
        }

    plugin_table = config.get("plugins") if isinstance(config.get("plugins"), dict) else None
    plugin_table_present = bool(plugin_table)
    plugin_table_missing = not plugin_table_present
    plugin_table_population = len(plugin_table) if plugin_table_present else 0

    cli_result: dict[str, Any] = {"ok": None, "skipped": True, "plugins": {}}
    missing_cli_visible: list[str] = []
    if run_cli and not parse_error:
        cli_result = run_codex_plugin_list(config)
        seen = cli_result.get("plugins") if isinstance(cli_result.get("plugins"), dict) else {}
        for key, meta in expected_plugins.items():
            if not meta.get("cli_required"):
                continue
            status = str(seen.get(key) or "")
            cli_ok = "installed" in status and "enabled" in status
            plugins[key]["cli_status"] = status
            plugins[key]["cli_ok"] = cli_ok
            if not cli_ok:
                missing_cli_visible.append(key)
        for key, meta in expected_plugins.items():
            if meta.get("cli_required"):
                continue
            plugins[key]["cli_status"] = str(seen.get(key) or "")
            plugins[key]["cli_ok"] = None
            if meta.get("cli_visibility_optional"):
                plugins[key]["cli_note"] = "bundled plugin visibility is not required in codex plugin list when config/cache/manifest are healthy"
            else:
                plugins[key]["cli_note"] = "reserved_or_implicit_marketplace_not_required_in_codex_plugin_list"

    missing_marketplaces = [
        name for name, status in marketplace_status.items() if not status["ok"]
    ]
    ok = bool(
        not parse_error
        and not missing_enabled
        and not missing_cache
        and not missing_manifest
        and not invalid_configured_plugins
        and not missing_marketplaces
        and not missing_cli_visible
        and (not run_cli or cli_result.get("ok") is True)
    )
    if ok:
        status = "ok"
    elif parse_error or missing_enabled or missing_cache or missing_manifest or invalid_configured_plugins:
        status = "unhealthy"
    else:
        status = "degraded"

    recommendations: list[str] = []
    if parse_error:
        recommendations.append("Fix config.toml parse error before plugin checks can be trusted.")
    if plugin_table_missing:
        recommendations.append("Codex config is missing the entire [plugins] table; restore it from a backup before any sync/write task runs.")
    if missing_enabled:
        recommendations.append("Restore missing [plugins.\"name@marketplace\"] enabled=true entries from the marked backup.")
    if missing_cache or missing_manifest:
        recommendations.append("Reinstall affected plugins or restore their cache directories from backup.")
    if invalid_configured_plugins:
        recommendations.append("Repair invalid enabled plugin identities; each identity must use name@marketplace.")
    if missing_cli_visible:
        recommendations.append("Run codex plugin list/install for CLI-visible marketplaces; keep openai-curated reserved plugins cache/config based.")
    if missing_marketplaces:
        recommendations.append("Restore missing marketplace json files before reinstalling plugins.")
    return {
        "ok": ok,
        "status": status,
        "read_only": True,
        "config_path": str(path),
        "config_parse_ok": not bool(parse_error),
        "config_parse_error": parse_error,
        "codex_home": str(home),
        "expected_plugins": plugins,
        "missing_enabled_plugins": missing_enabled,
        "missing_cache_plugins": missing_cache,
        "missing_manifest_plugins": missing_manifest,
        "invalid_configured_plugins": invalid_configured_plugins,
        "plugin_table_present": plugin_table_present,
        "plugin_table_missing": plugin_table_missing,
        "plugin_table_population": plugin_table_population,
        "marketplaces": marketplace_status,
        "missing_marketplaces": missing_marketplaces,
        "codex_plugin_list": cli_result,
        "missing_cli_visible_plugins": missing_cli_visible,
        "recommendations": recommendations,
        "notes": [
            "openai-curated is treated as reserved or implicit: config and cache are authoritative when codex plugin list does not expose that marketplace.",
            "This check is intentionally read-only; repair must be explicit and backup-protected.",
        ],
    }


def _valid_marketplace(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value))


def _run_codex(cli: str, arguments: list[str], *, timeout: float = 120.0) -> dict[str, Any]:
    """Run one bounded Codex CLI operation without inheriting a WSL profile."""

    try:
        proc = subprocess.run(
            [cli, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": "codex_cli_execution_failed", "error": type(exc).__name__}
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "payload": payload if isinstance(payload, dict) else {},
        "stderr": (proc.stderr or "").strip()[:1000],
    }


def _marketplace_rows(cli: str) -> dict[str, Any]:
    result = _run_codex(cli, ["plugin", "marketplace", "list", "--json"], timeout=30.0)
    rows = result.get("payload", {}).get("marketplaces", []) if isinstance(result.get("payload"), dict) else []
    names = {
        str(row.get("name") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }
    return {**result, "marketplaces": sorted(names)}


def plan_install(
    *,
    source: str,
    ref: str,
    plugin: str,
    marketplace: str,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Produce a no-write install plan with collision and CLI checks."""

    path = Path(config_path) if config_path else default_config_path()
    config, parse_error = load_toml(path)
    selector = f"{plugin}@{marketplace}"
    if parse_error:
        return {"ok": False, "reason": "config_parse_failed", "config_path": str(path)}
    if not source.strip() or not ref.strip() or not plugin.strip() or not _valid_marketplace(marketplace):
        return {"ok": False, "reason": "invalid_install_identity", "selector": selector}
    cli = codex_cli_from_config(config)
    if not cli:
        return {"ok": False, "reason": "codex_cli_not_found", "selector": selector}
    listed = _marketplace_rows(cli)
    if not listed.get("ok"):
        return {"ok": False, "reason": "marketplace_listing_failed", "selector": selector, "cli": listed}
    health = codex_plugin_config_health(path, run_cli=True)
    plugin_state = health.get("expected_plugins", {}).get(selector, {})
    installed = bool(plugin_state.get("enabled") and plugin_state.get("cache_ok") and plugin_state.get("manifest_ok") and plugin_state.get("cli_ok") is not False)
    existing_marketplaces = listed.get("marketplaces", [])
    if installed:
        state = "already_satisfied"
        ok = True
        reason = "plugin_already_installed"
    elif marketplace in existing_marketplaces:
        state = "manual_review_required"
        ok = False
        reason = "marketplace_name_already_exists"
    else:
        state = "ready_to_install"
        ok = True
        reason = ""
    return {
        "schema": "codex_plugin_config_health.install_plan.v1",
        "ok": ok,
        "state": state,
        "reason": reason,
        "config_path": str(path),
        "cli": cli,
        "source": source,
        "ref": ref,
        "plugin": plugin,
        "marketplace": marketplace,
        "selector": selector,
        "confirmation": "INSTALL-CODEX-PLUGIN",
        "existing_marketplaces": existing_marketplaces,
    }


def install(
    *,
    source: str,
    ref: str,
    plugin: str,
    marketplace: str,
    confirm: str,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Install one declared plugin through the sole plugin owner."""

    plan = plan_install(source=source, ref=ref, plugin=plugin, marketplace=marketplace, config_path=config_path)
    if not plan.get("ok"):
        return {"schema": "codex_plugin_config_health.install.v1", "ok": False, "plan": plan}
    if confirm != "INSTALL-CODEX-PLUGIN":
        return {"schema": "codex_plugin_config_health.install.v1", "ok": False, "reason": "confirmation_required", "plan": plan}
    if plan.get("state") == "already_satisfied":
        return {"schema": "codex_plugin_config_health.install.v1", "ok": True, "changed": False, "plan": plan}

    config = Path(str(plan["config_path"]))
    lease = state_write_authority.try_acquire_state_write_lease(
        "codex_config",
        "codex_plugin_config_health",
        state_root=state_write_authority.codex_config_coordination_root(config),
        timeout_seconds=15.0,
        advance_generation_on_acquire=False,
    )
    if lease is None:
        return {"schema": "codex_plugin_config_health.install.v1", "ok": False, "reason": "state_write_authority_busy", "plan": plan}
    try:
        backup = create_backup(
            [str(config)],
            remark="before-codex-plugin-install",
            purpose="Preserve Codex plugin configuration before marketplace installation",
            category="codex-plugin",
        )
        if not backup.get("ok"):
            return {"schema": "codex_plugin_config_health.install.v1", "ok": False, "reason": "backup_failed", "plan": plan, "backup": backup}
        lease.advance_generation()
        lease.assert_current()
        add_marketplace = _run_codex(str(plan["cli"]), ["plugin", "marketplace", "add", source, "--ref", ref, "--json"])
        if not add_marketplace.get("ok"):
            return {"schema": "codex_plugin_config_health.install.v1", "ok": False, "reason": "marketplace_add_failed", "plan": plan, "backup": backup, "marketplace_add": add_marketplace}
        lease.assert_current()
        add_plugin = _run_codex(str(plan["cli"]), ["plugin", "add", str(plan["selector"]), "--json"])
        if not add_plugin.get("ok"):
            rollback = _run_codex(str(plan["cli"]), ["plugin", "marketplace", "remove", marketplace, "--json"])
            return {"schema": "codex_plugin_config_health.install.v1", "ok": False, "reason": "plugin_add_failed", "plan": plan, "backup": backup, "marketplace_add": add_marketplace, "plugin_add": add_plugin, "rollback": rollback}
        lease.assert_current()
        after = codex_plugin_config_health(config, run_cli=True)
        plugin_state = after.get("expected_plugins", {}).get(str(plan["selector"]), {})
        installed = bool(plugin_state.get("enabled") and plugin_state.get("cache_ok") and plugin_state.get("manifest_ok") and plugin_state.get("cli_ok") is not False)
        return {
            "schema": "codex_plugin_config_health.install.v1",
            "ok": installed,
            "changed": True,
            "reason": "" if installed else "post_install_readback_failed",
            "plan": plan,
            "backup": backup,
            "marketplace_add": add_marketplace,
            "plugin_add": add_plugin,
            "post_install": {
                "selector": plan["selector"],
                "marketplace_present": marketplace in _marketplace_rows(str(plan["cli"])).get("marketplaces", []),
                "plugin": plugin_state,
            },
        }
    finally:
        lease.release()


def remove(
    *,
    plugin: str,
    marketplace: str,
    confirm: str,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Remove one installed plugin through the same leased lifecycle owner."""

    path = Path(config_path) if config_path else default_config_path()
    config, parse_error = load_toml(path)
    selector = f"{plugin}@{marketplace}"
    if parse_error or not plugin.strip() or not _valid_marketplace(marketplace):
        return {"schema": "codex_plugin_config_health.remove.v1", "ok": False, "reason": "invalid_remove_identity"}
    cli = codex_cli_from_config(config)
    cache_root = path.parent / "plugins" / "cache" / marketplace / plugin
    marketplaces = config.get("marketplaces") if isinstance(config.get("marketplaces"), dict) else {}
    configured_plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    marketplace_referenced_elsewhere = any(
        str(identity) != selector and str(identity).rpartition("@")[2] == marketplace
        for identity in configured_plugins
    )
    orphaned_custom_marketplace = bool(
        marketplace in marketplaces
        and not marketplace_referenced_elsewhere
        and not _reserved_marketplace(marketplace)
    )
    plugin_present = plugin_enabled(config, selector) or cache_root.exists()
    if not plugin_present and not orphaned_custom_marketplace:
        return {"schema": "codex_plugin_config_health.remove.v1", "ok": True, "changed": False, "selector": selector}
    if confirm != "REMOVE-CODEX-PLUGIN":
        return {"schema": "codex_plugin_config_health.remove.v1", "ok": False, "reason": "confirmation_required", "selector": selector}
    if not cli:
        return {"schema": "codex_plugin_config_health.remove.v1", "ok": False, "reason": "codex_cli_not_found", "selector": selector}
    lease = state_write_authority.try_acquire_state_write_lease(
        "codex_config",
        "codex_plugin_config_health",
        state_root=state_write_authority.codex_config_coordination_root(path),
        timeout_seconds=15.0,
        advance_generation_on_acquire=False,
    )
    if lease is None:
        return {"schema": "codex_plugin_config_health.remove.v1", "ok": False, "reason": "state_write_authority_busy", "selector": selector}
    try:
        marketplace_root = path.parent / ".tmp" / "marketplaces" / marketplace
        backup_paths = [str(item) for item in (path, cache_root, marketplace_root) if item.exists()]
        backup = create_backup(
            backup_paths,
            remark="before-codex-plugin-remove",
            purpose="Preserve Codex plugin configuration and payloads before explicit removal",
            category="codex-plugin",
        )
        if not backup.get("ok"):
            return {"schema": "codex_plugin_config_health.remove.v1", "ok": False, "reason": "backup_failed", "backup": backup}
        lease.advance_generation()
        lease.assert_current()
        operation: dict[str, Any] = {"ok": True, "skipped": True, "reason": "plugin_already_absent"}
        if plugin_present:
            operation = _run_codex(cli, ["plugin", "remove", selector, "--json"])
        if not operation.get("ok"):
            return {
                "schema": "codex_plugin_config_health.remove.v1",
                "ok": False,
                "changed": False,
                "reason": "plugin_remove_failed",
                "selector": selector,
                "backup": backup,
                "operation": operation,
            }
        lease.assert_current()
        after_plugin, after_plugin_error = load_toml(path)
        plugins = after_plugin.get("plugins") if isinstance(after_plugin.get("plugins"), dict) else {}
        marketplace_referenced_elsewhere = any(
            str(identity) != selector and str(identity).rpartition("@")[2] == marketplace
            for identity in plugins
        )
        marketplaces = after_plugin.get("marketplaces") if isinstance(after_plugin.get("marketplaces"), dict) else {}
        remove_marketplace = bool(
            not after_plugin_error
            and not marketplace_referenced_elsewhere
            and not _reserved_marketplace(marketplace)
            and marketplace in marketplaces
        )
        marketplace_operation: dict[str, Any] = {"ok": True, "skipped": True, "reason": "still_referenced_or_reserved"}
        if remove_marketplace:
            marketplace_operation = _run_codex(cli, ["plugin", "marketplace", "remove", marketplace, "--json"])
            if not marketplace_operation.get("ok"):
                return {
                    "schema": "codex_plugin_config_health.remove.v1",
                    "ok": False,
                    "changed": True,
                    "reason": "marketplace_remove_failed",
                    "selector": selector,
                    "backup": backup,
                    "operation": operation,
                    "marketplace_operation": marketplace_operation,
                }
        lease.assert_current()
        current_text = path.read_text(encoding="utf-8")
        rendered, declaration_cleanup = _remove_plugin_declarations(
            current_text,
            selector=selector,
            marketplace=marketplace,
        )
        if rendered != current_text:
            atomic_write_text(path, rendered)
        lease.assert_current()
        after, after_error = load_toml(path)
        after_marketplaces = after.get("marketplaces") if isinstance(after.get("marketplaces"), dict) else {}
        marketplace_absent = bool(
            _reserved_marketplace(marketplace)
            or marketplace_referenced_elsewhere
            or marketplace not in after_marketplaces
        )
        absent = not after_error and not plugin_enabled(after, selector) and not cache_root.exists()
        return {
            "schema": "codex_plugin_config_health.remove.v1",
            "ok": bool(absent and marketplace_absent),
            "changed": True,
            "reason": "" if absent and marketplace_absent else "post_remove_readback_failed",
            "selector": selector,
            "backup": backup,
            "operation": operation,
            "marketplace_operation": marketplace_operation,
            "declaration_cleanup": declaration_cleanup,
            "post_remove_absent": absent,
            "post_remove_marketplace_absent_or_retained": marketplace_absent,
        }
    finally:
        lease.release()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Codex plugin health and governed installation owner")
    parser.add_argument("action", nargs="?", default="doctor", choices=["doctor", "plan-install", "install", "remove"])
    parser.add_argument("--config-path", default="")
    parser.add_argument("--no-cli", action="store_true")
    parser.add_argument("--source", default="")
    parser.add_argument("--ref", default="")
    parser.add_argument("--plugin", default="")
    parser.add_argument("--marketplace", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    config_path = args.config_path or None
    if args.action == "doctor":
        result = codex_plugin_config_health(config_path, run_cli=not args.no_cli)
    elif args.action == "plan-install":
        result = plan_install(source=args.source, ref=args.ref, plugin=args.plugin, marketplace=args.marketplace, config_path=config_path)
    elif args.action == "install":
        result = install(source=args.source, ref=args.ref, plugin=args.plugin, marketplace=args.marketplace, confirm=args.confirm, config_path=config_path)
    else:
        result = remove(plugin=args.plugin, marketplace=args.marketplace, confirm=args.confirm, config_path=config_path)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
