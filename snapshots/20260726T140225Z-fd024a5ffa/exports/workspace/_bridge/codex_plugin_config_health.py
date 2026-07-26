#!/usr/bin/env python3
"""Read-only Codex plugin configuration and cache health checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


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
    direct = plugin_root / ".codex-plugin" / "plugin.json"
    if direct.exists():
        return direct
    manifests = sorted(plugin_root.glob("*/.codex-plugin/plugin.json"))
    return manifests[-1] if manifests else None


def marketplace_files(home: Path) -> dict[str, Path]:
    return {
        "openai-bundled": home / ".tmp" / "bundled-marketplaces" / "openai-bundled" / ".agents" / "plugins" / "marketplace.json",
        "openai-curated": home / ".tmp" / "plugins" / ".agents" / "plugins" / "marketplace.json",
        "openai-api-curated": home / ".tmp" / "plugins" / ".agents" / "plugins" / "api_marketplace.json",
    }


def codex_cli_from_config(config: dict[str, Any]) -> str:
    servers = config.get("mcp_servers")
    if isinstance(servers, dict):
        node_repl = servers.get("node_repl")
        if isinstance(node_repl, dict):
            env = node_repl.get("env")
            if isinstance(env, dict):
                value = str(env.get("CODEX_CLI_PATH") or "").strip()
                if value and Path(value).exists():
                    return value
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
            [cli, "plugin", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc), "plugins": {}, "path": cli}
    seen: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        stripped = line.strip()
        if not stripped or "@" not in stripped:
            continue
        parts = stripped.split()
        plugin_key = parts[0]
        if "@" not in plugin_key:
            continue
        status = stripped[len(plugin_key) :].strip()
        seen[plugin_key] = status
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
    home = codex_home()
    path = Path(config_path) if config_path else default_config_path()
    config, parse_error = load_toml(path)
    cache_root = home / "plugins" / "cache"
    market_files = marketplace_files(home)
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


def main() -> int:
    run_cli = "--no-cli" not in sys.argv[1:]
    config_path = None
    args = [arg for arg in sys.argv[1:] if arg != "--no-cli"]
    if args:
        config_path = args[0]
    result = codex_plugin_config_health(config_path, run_cli=run_cli)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
