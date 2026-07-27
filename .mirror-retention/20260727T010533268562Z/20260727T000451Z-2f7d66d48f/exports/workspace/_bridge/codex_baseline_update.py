#!/usr/bin/env python3
"""Adopt verified current Codex config state into codex_startup_baseline.json.

Use this after intentional Codex changes such as adding MCP servers, enabling
plugins, or changing stable startup policy. It updates the baseline only; it
does not modify Codex config files.

The baseline should also carry governance notes for post-allow residual cleanup
so defender allowlisting and threat-history reset stay aligned across future
adoptions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from mcp_execution_priority import HUB_MANAGED_MCP_NAMES
import platform_paths

try:
    from shared.backup_router import create_backup
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.backup_router import create_backup


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "_bridge" / "codex_startup_baseline.json"

STARTUP_REQUIRED_MCP_NAMES = {
    "node_repl",
}

NON_BLOCKING_MCP_NAMES = {
    "agent-bridge",
    "gui-automation",
    "mobile-openclaw-bridge",
    "chrome-devtools",
    "context7",
    "github",
    "markitdown",
    "microsoftdocs",
    "playwright",
    "codegraph",
    "local-pmb-memory",
    "filesystem-admin",
    "local-mcp-hub",
}

def startup_required(name: str, value: dict, old: dict) -> bool:
    """Return the durable startup-required policy for an MCP profile.

    Required means "may block Codex Desktop startup/session recovery". It is
    intentionally narrower than "configured" or "important"; non-required
    MCPs still stay in the baseline and are validated by smoke/probe/doctor.
    """

    if name in STARTUP_REQUIRED_MCP_NAMES:
        return True
    if name in NON_BLOCKING_MCP_NAMES:
        return False
    if "required" in value:
        return bool(value["required"])
    if isinstance(old, dict) and "required" in old:
        return bool(old["required"])
    return False


def load_toml(path: Path) -> dict:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))


def backup_baseline() -> Path:
    receipt = create_backup(
        [str(BASELINE_PATH)],
        purpose="codex-startup-baseline-adoption",
        category="workspace-source",
        trigger="codex_baseline_update",
    )
    if not receipt.get("ok"):
        raise RuntimeError(f"baseline_backup_failed:{receipt.get('reason', 'unknown')}")
    manifests = receipt.get("manifest_paths") or []
    return Path(str(manifests[0])).parent


def plugin_enabled_names(config: dict, existing_baseline: dict) -> list[str]:
    names = set() if existing_baseline.get("configuration_authority") == "wsl_active" else set(existing_baseline.get("expected_plugins", []))
    plugins = config.get("plugins", {})
    if not isinstance(plugins, dict):
        return sorted(names)
    names.update(
        name
        for name, value in plugins.items()
        if isinstance(value, dict) and value.get("enabled") is True
    )
    return sorted(names)


def decommissioned_mcp_names(existing_baseline: dict) -> set[str]:
    """Read the sole MCP tombstone authority from the startup baseline."""

    raw = existing_baseline.get("decommissioned_mcp", {})
    return set(str(name) for name in raw) if isinstance(raw, dict) else set()


def mcp_specs(config: dict, existing_baseline: dict) -> dict:
    servers = config.get("mcp_servers", {})
    existing = existing_baseline.get("expected_mcp", {})
    decommissioned = decommissioned_mcp_names(existing_baseline)
    adopted: dict[str, dict] = {}
    retain_existing = existing_baseline.get("configuration_authority") != "wsl_active"
    for name, spec in existing.items():
        if not retain_existing:
            continue
        if name in decommissioned or not isinstance(spec, dict):
            continue
        if name in HUB_MANAGED_MCP_NAMES:
            adopted[name] = {**spec, "required": False, "registration_mode": "hub_managed"}
    if not isinstance(servers, dict):
        return adopted
    for name, value in sorted(servers.items()):
        if name in decommissioned or name in HUB_MANAGED_MCP_NAMES:
            continue
        if not isinstance(value, dict):
            continue
        old = existing.get(name, {})
        spec: dict = {
            "required": startup_required(name, value, old),
            "registration_mode": "desktop_native",
        }
        for key in ("command", "args", "url", "bearer_token_env_var", "startup_timeout_sec", "env"):
            if key in value:
                spec[key] = value[key]
        # Keep explicit approval policy metadata from the old baseline. The
        # parsed config nests tool approval as sub-tables, but preserving the
        # stable declaration avoids overfitting to parser details.
        if "tool_approval_mode" in old:
            spec["tool_approval_mode"] = old["tool_approval_mode"]
        if "tools" in old:
            spec["tools"] = old["tools"]
        adopted[name] = spec
    return adopted


def required_values_from_config(global_config: dict, project_config: dict, workspace: str) -> tuple[dict, dict]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    wsl_active = baseline.get("configuration_authority") == "wsl_active"
    global_values: dict[str, object] = {} if wsl_active else dict(baseline.get("global_required_values", {}))
    for key in ("sandbox_mode", "approval_policy"):
        if key in global_config:
            global_values[key] = global_config[key]
    if isinstance(global_config.get("features"), dict) and "memories" in global_config["features"]:
        global_values["features.memories"] = global_config["features"]["memories"]
    if isinstance(global_config.get("memories"), dict):
        for key in ("generate_memories", "use_memories"):
            if key in global_config["memories"]:
                global_values[f"memories.{key}"] = global_config["memories"][key]
    if isinstance(global_config.get("windows"), dict) and "sandbox" in global_config["windows"]:
        global_values["windows.sandbox"] = global_config["windows"]["sandbox"]
    projects = global_config.get("projects", {})
    if isinstance(projects, dict):
        project_items = projects.items() if wsl_active else [(workspace.lower(), projects.get(workspace.lower()))]
        for project_name, project in project_items:
            if isinstance(project, dict) and "trust_level" in project:
                global_values[f"projects.{str(project_name).lower()}.trust_level"] = project["trust_level"]

    project_values: dict[str, object] = dict(baseline.get("project_required_values", {}))
    for key in ("sandbox_mode", "approval_policy"):
        if key in project_config:
            project_values[key] = project_config[key]
    if isinstance(project_config.get("windows"), dict) and "sandbox" in project_config["windows"]:
        project_values["windows.sandbox"] = project_config["windows"]["sandbox"]
    return global_values, project_values


def marketplace_specs(config: dict, existing_baseline: dict) -> dict:
    if existing_baseline.get("configuration_authority") != "wsl_active":
        return dict(existing_baseline.get("expected_marketplaces") or {})
    marketplaces = config.get("marketplaces")
    return {
        str(name): {key: value for key, value in spec.items() if key in {"source", "source_type"}}
        for name, spec in marketplaces.items()
        if isinstance(spec, dict)
    } if isinstance(marketplaces, dict) else {}


def run_audit() -> tuple[bool, str]:
    audit_python = (
        str(Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe")
        if sys.platform == "win32"
        else sys.executable
    )
    proc = subprocess.run(
        [
            audit_python,
            str(ROOT / "_bridge" / "codex_state_audit.py"),
        ],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60,
    )
    return proc.returncode == 0, ((proc.stdout or "") + (proc.stderr or "")).strip()


def build_updated_baseline(reason: str) -> tuple[dict, dict]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    global_config_path = platform_paths.host_accessible_path(baseline["global_config"])
    project_config_required = bool(baseline.get("project_config_required", True))
    project_config_path = (
        platform_paths.host_accessible_path(baseline["project_config"])
        if project_config_required
        else None
    )
    global_config = load_toml(global_config_path)
    project_config = load_toml(project_config_path) if project_config_path is not None else {}
    workspace = baseline["workspace"]

    global_values, project_values = required_values_from_config(global_config, project_config, workspace)
    updated = dict(baseline)
    updated["expected_mcp"] = mcp_specs(global_config, baseline)
    updated["expected_plugins"] = plugin_enabled_names(global_config, baseline)
    updated["expected_marketplaces"] = marketplace_specs(global_config, baseline)
    updated["global_required_values"] = global_values
    updated["project_required_values"] = project_values if project_config_required else {}
    updated.setdefault("global_state", str(Path.home() / ".codex" / ".codex-global-state.json"))
    updated.setdefault(
        "global_state_required",
        {
            "electron-saved-workspace-roots": {"contains": [workspace]},
            "active-workspace-roots": {"contains": [workspace], "prefer_first": True},
            "electron-persisted-atom-state.skip-full-access-confirm": True,
        },
    )
    updated["last_adopted_at"] = datetime.now(timezone.utc).isoformat()
    updated["last_adopted_reason"] = reason

    diff = {
        "mcp_added": sorted(set(updated["expected_mcp"]) - set(baseline.get("expected_mcp", {}))),
        "mcp_removed_from_baseline": sorted(set(baseline.get("expected_mcp", {})) - set(updated["expected_mcp"])),
        "plugins_added": sorted(set(updated["expected_plugins"]) - set(baseline.get("expected_plugins", []))),
        "plugins_removed_from_baseline": sorted(set(baseline.get("expected_plugins", [])) - set(updated["expected_plugins"])),
        "global_required_values": updated["global_required_values"],
        "project_required_values": updated["project_required_values"],
        "global_state_required": updated.get("global_state_required", {}),
    }
    return updated, diff


def convergence_report(reason: str) -> dict:
    updated, diff = build_updated_baseline(reason)
    baseline_stale = bool(diff["mcp_added"] or diff["plugins_added"])
    return {
        "schema": "codex-baseline-update/check-current/v1",
        "ok": not baseline_stale,
        "baseline": str(BASELINE_PATH),
        "baseline_stale": baseline_stale,
        "diff": diff,
        "next_step": (
            "Run with --adopt-current after audit if these global MCP/plugin additions are intentional."
            if baseline_stale
            else "Baseline already covers current global MCP/plugin config."
        ),
        "adopt_command": (
            f"python _bridge\\codex_baseline_update.py --adopt-current --reason {json.dumps(reason)}"
            if baseline_stale
            else ""
        ),
        "updated_preview": {
            "expected_mcp_count": len(updated.get("expected_mcp", {})),
            "expected_plugin_count": len(updated.get("expected_plugins", [])),
            "last_adopted_reason": reason,
        },
        "policy": "registration-aware convergence: preserve Hub-managed logical profiles, track current Desktop-native profiles, and prune stale Desktop registrations only on explicit adoption",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Adopt current Codex config into the startup baseline.")
    parser.add_argument("--adopt-current", action="store_true", help="Write current verified config state into the baseline.")
    parser.add_argument("--check-current", action="store_true", help="Read-only check that the baseline covers current global MCP/plugin config.")
    parser.add_argument("--reason", default="manual verified Codex change", help="Short reason stored in the baseline.")
    parser.add_argument("--skip-audit", action="store_true", help="Allow adoption even if the current audit fails.")
    args = parser.parse_args()

    if args.check_current:
        result = convergence_report(args.reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    updated, diff = build_updated_baseline(args.reason)
    if not args.adopt_current:
        print(json.dumps({"ok": True, "dry_run": True, "diff": diff}, ensure_ascii=False, indent=2))
        return 0

    audit_ok, audit_output = run_audit()
    if not audit_ok and not args.skip_audit:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "current audit failed; use --skip-audit only after manual review",
                    "audit_output": audit_output[:2000],
                    "diff": diff,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    backup_dir = backup_baseline()
    BASELINE_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "ok": True,
                "backup_dir": str(backup_dir),
                "audit_ok": audit_ok,
                "diff": diff,
                "baseline": str(BASELINE_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
