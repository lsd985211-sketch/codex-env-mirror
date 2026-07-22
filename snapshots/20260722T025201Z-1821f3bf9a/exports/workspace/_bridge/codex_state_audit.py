#!/usr/bin/env python3
"""Read-only audit of the Windows host compatibility projection."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tomllib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from mcp_execution_priority import DESKTOP_NATIVE_MCP_NAMES, HUB_MANAGED_MCP_NAMES

try:
    from bounded_output import output_evidence_policy
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.bounded_output import output_evidence_policy

try:
    from shared.codex_desktop_package import query_desktop_host_processes
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.codex_desktop_package import query_desktop_host_processes


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "_bridge" / "codex_startup_baseline.json"
WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\/](.*)$")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def load_toml(path: Path) -> tuple[dict, bool, str]:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8")
    return tomllib.loads(text), bom, ""


def load_baseline() -> dict:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def host_path(value: str | Path) -> Path:
    text = str(value or "")
    match = WINDOWS_PATH.match(text)
    if os.name != "nt" and match:
        rest = match.group(2).replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{match.group(1).lower()}/{rest}")
    return Path(text)


def windows_path_text(value: str | Path) -> str:
    text = str(value or "")
    match = re.match(r"^/mnt/([A-Za-z])(?:/(.*))?$", text.replace("\\", "/"))
    if match:
        rest = str(match.group(2) or "").replace("/", "\\")
        return f"{match.group(1).upper()}:\\{rest}" if rest else f"{match.group(1).upper()}:\\"
    return text


def normalize_path_text(value: str) -> str:
    return str(host_path(value)).casefold() if value else ""


def discover_latest_codex_cli() -> str | None:
    candidates: list[Path] = []
    bin_roots = [Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin"]
    if os.name != "nt":
        bin_roots.append(Path("/mnt/c/Users/45543/AppData/Local/OpenAI/Codex/bin"))
    for bin_root in bin_roots:
        if bin_root.exists():
            candidates.extend(path for path in bin_root.glob("*/codex.exe") if path.exists())
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        return str(candidate)
    for executable in ("codex", "codex.exe"):
        resolved = shutil.which(executable)
        if resolved:
            return resolved
    return None


def node_repl_cli_path(config: dict) -> str:
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return ""
    node_repl = servers.get("node_repl")
    if not isinstance(node_repl, dict):
        return ""
    node_env = node_repl.get("env")
    if not isinstance(node_env, dict):
        return ""
    return str(node_env.get("CODEX_CLI_PATH") or "").strip()


def resolve_codex_cli(config: dict | None = None) -> str | None:
    candidates: list[str] = []
    env_value = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env_value:
        candidates.append(env_value)
    if isinstance(config, dict):
        cli_value = node_repl_cli_path(config)
        if cli_value:
            candidates.append(cli_value)
    latest = discover_latest_codex_cli()
    if latest:
        candidates.append(latest)
    candidates.extend([
        shutil.which("codex") or "",
        shutil.which("codex.exe") or "",
    ])
    for candidate in candidates:
        mapped = host_path(candidate) if candidate else Path()
        if candidate and (mapped.exists() or shutil.which(candidate)):
            return str(mapped if mapped.exists() else candidate)
    return None


def dotted_get(data: dict, dotted: str) -> object:
    current: object = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def project_trust_get(data: dict, project_path: str) -> object:
    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        return None
    project = projects.get(project_path)
    if not isinstance(project, dict):
        return None
    return project.get("trust_level")


def load_global_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def global_state_rule_ok(global_state: dict, key: str, rule: object) -> tuple[bool, str]:
    if isinstance(rule, dict) and "contains" in rule:
        current = global_state.get(key)
        if not isinstance(current, list):
            return False, f"{key}=not-list"
        current_lower = [str(item).lower() for item in current]
        expected = [str(item) for item in rule.get("contains", [])]
        missing = [item for item in expected if item.lower() not in current_lower]
        if missing:
            return False, f"missing={missing} actual={current}"
        if rule.get("prefer_first") and expected:
            first_ok = bool(current) and str(current[0]).lower() == expected[0].lower()
            return first_ok, f"first={current[0] if current else None!r} expected={expected[0]!r} actual={current}"
        return True, f"actual={current}"
    actual = dotted_get(global_state, key)
    return actual == rule, f"actual={actual!r} expected={rule!r}"


def run_codex_mcp_list(config: dict | None = None, baseline: dict | None = None) -> tuple[bool, str]:
    cli = resolve_codex_cli(config)
    if not cli:
        return False, "codex CLI not found in CODEX_CLI_PATH, node_repl env, or PATH"
    command: list[str]
    cwd = ROOT
    if (
        os.name != "nt"
        and isinstance(baseline, dict)
        and baseline.get("workspace_role") == "windows_host_compatibility_projection"
        and baseline.get("configuration_authority") != "wsl_active"
    ):
        python_candidates = [
            str(spec.get("command") or "")
            for spec in (baseline.get("expected_mcp") or {}).values()
            if isinstance(spec, dict) and str(spec.get("command") or "").lower().endswith("python.exe")
        ]
        windows_python = next((host_path(item) for item in python_candidates if host_path(item).is_file()), None)
        if windows_python is None:
            return False, "windows host Python is unavailable for the compatibility-projection CLI probe"
        wrapper = (
            "import os,subprocess,sys; "
            "env=os.environ.copy(); "
            "env['CODEX_HOME']=r'C:\\Users\\45543\\.codex'; "
            "env['USERPROFILE']=r'C:\\Users\\45543'; "
            "p=subprocess.run([sys.argv[1],'mcp','list'],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace'); "
            "sys.stdout.write((p.stdout or '')+(p.stderr or '')); sys.exit(p.returncode)"
        )
        command = [str(windows_python), "-c", wrapper, windows_path_text(cli)]
        cwd = host_path(baseline.get("workspace") or ROOT)
    else:
        command = [cli, "mcp", "list"]
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        return False, repr(exc)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, output.strip()


def codex_desktop_running() -> bool:
    try:
        if query_desktop_host_processes(main_only=True):
            return True
    except Exception:
        pass
    if os.name != "nt":
        try:
            import codex_desktop_model_runtime

            _port, ws_url, _pages, _reason = codex_desktop_model_runtime._find_codex_page()
            return bool(ws_url)
        except Exception:
            pass
    return False


def build_checks(run_cli: bool = True) -> list[Check]:
    checks: list[Check] = []
    baseline = load_baseline()
    if baseline.get("workspace_role") == "windows_host_compatibility_projection":
        work_git_root = Path(str(baseline.get("declarative_work_git_root") or ""))
        checks.extend([
            Check("authority_host_projection_role", True, str(baseline.get("workspace"))),
            Check("authority_host_projection_not_source", baseline.get("source_authority") is False, f"source_authority={baseline.get('source_authority')!r}"),
            Check("authority_work_git_root_exists", work_git_root.is_dir() and (work_git_root / ".git").exists(), str(work_git_root)),
        ])
    global_config_path = host_path(baseline["global_config"])
    project_config_required = bool(baseline.get("project_config_required", True))
    project_config_path = host_path(baseline["project_config"]) if project_config_required else None
    expected_mcp_specs = baseline.get("expected_mcp") or {}
    expected_mcp = {
        name
        for name, spec in expected_mcp_specs.items()
        if isinstance(spec, dict) and name in DESKTOP_NATIVE_MCP_NAMES
    }
    hub_managed_mcp = set(HUB_MANAGED_MCP_NAMES)
    optional_mcp = {
        name
        for name, spec in expected_mcp_specs.items()
        if isinstance(spec, dict) and spec.get("required") is not True
    }
    expected_plugins = set(baseline["expected_plugins"])
    expected_marketplaces = baseline.get("expected_marketplaces") or {}
    try:
        global_config, global_bom, _ = load_toml(global_config_path)
        mcp = set(global_config.get("mcp_servers", {}).keys())
        marketplace_table = global_config.get("marketplaces", {})
        marketplaces = set(marketplace_table.keys()) if isinstance(marketplace_table, dict) else set()
        plugin_table = global_config.get("plugins", {})
        plugins = {name for name, value in plugin_table.items() if isinstance(value, dict) and value.get("enabled") is True}
        checks.append(Check("baseline_parse", True, str(BASELINE_PATH)))
        checks.append(Check("global_config_parse", True, str(global_config_path)))
        checks.append(Check("global_config_no_bom", not global_bom, f"bom={global_bom}"))
        checks.append(Check("expected_mcp_registered", expected_mcp <= mcp, f"mcp={sorted(mcp)}"))
        mcp_servers = global_config.get("mcp_servers", {})
        for name in sorted(expected_mcp):
            server_spec = mcp_servers.get(name, {}) if isinstance(mcp_servers, dict) else {}
            expected_required = bool(expected_mcp_specs.get(name, {}).get("required") is True)
            actual_required = server_spec.get("required") if isinstance(server_spec, dict) else None
            checks.append(
                Check(
                    f"expected_mcp_required_flag_{name}",
                    isinstance(server_spec, dict) and actual_required is expected_required,
                    f"required={actual_required!r} expected={expected_required!r}",
                )
            )
        baseline_mcp = set(expected_mcp_specs.keys())
        decommissioned_mcp = set((baseline.get("decommissioned_mcp") or {}).keys())
        extra_mcp = sorted(mcp - expected_mcp)
        baseline_missing_mcp = sorted(mcp - baseline_mcp - decommissioned_mcp)
        configured_decommissioned_mcp = sorted(mcp & decommissioned_mcp)
        configured_hub_managed_mcp = sorted(mcp & hub_managed_mcp)
        checks.append(Check("extra_mcp_not_in_baseline", True, f"extra={extra_mcp}"))
        checks.append(
            Check(
                "hub_managed_mcp_not_configured",
                not configured_hub_managed_mcp,
                f"configured_hub_managed={configured_hub_managed_mcp}",
            )
        )
        checks.append(
            Check(
                "decommissioned_mcp_not_configured",
                not configured_decommissioned_mcp,
                f"configured_decommissioned={configured_decommissioned_mcp}",
            )
        )
        checks.append(
            Check(
                "baseline_covers_global_mcp",
                not baseline_missing_mcp,
                f"missing_from_baseline={baseline_missing_mcp}",
            )
        )
        configured_optional = sorted(mcp & optional_mcp)
        optional_required = sorted(
            name
            for name in configured_optional
            if isinstance(mcp_servers.get(name), dict) and mcp_servers.get(name, {}).get("required") is True
        )
        checks.append(
            Check(
                "optional_mcp_not_required",
                not optional_required,
                f"configured_optional={configured_optional} optional_required={optional_required}",
            )
        )
        configured_cli = node_repl_cli_path(global_config)
        latest_cli = discover_latest_codex_cli()
        configured_cli_exists = bool(configured_cli and host_path(configured_cli).exists())
        checks.append(
            Check(
                "codex_runtime_cli_path_exists",
                configured_cli_exists,
                f"configured={configured_cli!r} exists={configured_cli_exists} latest={latest_cli!r}",
            )
        )
        wsl_active_config = baseline.get("configuration_authority") == "wsl_active"
        current_ok = bool(
            configured_cli
            and configured_cli_exists
            and (
                wsl_active_config
                or (latest_cli and normalize_path_text(configured_cli) == normalize_path_text(latest_cli))
            )
        )
        checks.append(
            Check(
                "codex_runtime_cli_path_current",
                current_ok,
                f"configured={configured_cli!r} latest={latest_cli!r} wsl_active_config={wsl_active_config}",
            )
        )
        checks.append(
            Check(
                "expected_plugins_enabled",
                expected_plugins <= plugins,
                f"plugins={sorted(plugins)}",
            )
        )
        extra_plugins = sorted(plugins - expected_plugins)
        checks.append(Check("extra_plugins_not_in_baseline", True, f"extra={extra_plugins}"))
        checks.append(
            Check(
                "baseline_covers_global_plugins",
                not extra_plugins,
                f"missing_from_baseline={extra_plugins}",
            )
        )
        expected_marketplace_names = set(expected_marketplaces.keys())
        checks.append(
            Check(
                "expected_marketplaces_present",
                expected_marketplace_names <= marketplaces,
                f"marketplaces={sorted(marketplaces)} expected={sorted(expected_marketplace_names)}",
            )
        )
        for name, spec in expected_marketplaces.items():
            actual = marketplace_table.get(name, {}) if isinstance(marketplace_table, dict) else {}
            for key in ("source_type", "source"):
                if key in spec:
                    checks.append(
                        Check(
                            f"marketplace_{name}_{key}",
                            isinstance(actual, dict) and actual.get(key) == spec[key],
                            f"actual={(actual.get(key) if isinstance(actual, dict) else None)!r} expected={spec[key]!r}",
                        )
                    )
        for dotted, expected in baseline["global_required_values"].items():
            if dotted.startswith("projects."):
                project_path = dotted.removeprefix("projects.").removesuffix(".trust_level")
                actual = project_trust_get(global_config, project_path)
            else:
                actual = dotted_get(global_config, dotted)
            checks.append(Check(f"global_value_{dotted}", actual == expected, f"actual={actual!r} expected={expected!r}"))
        checks.append(
            Check(
                "memories_enabled",
                bool(global_config.get("features", {}).get("memories"))
                and bool(global_config.get("memories", {}).get("use_memories")),
                f"features={global_config.get('features')} memories={global_config.get('memories')}",
            )
        )
    except Exception as exc:
        checks.append(Check("global_config_parse", False, repr(exc)))

    try:
        global_state_path = host_path(baseline.get("global_state", Path.home() / ".codex" / ".codex-global-state.json"))
        global_state = load_global_state(global_state_path)
        active_roots = [str(item).lower() for item in global_state.get("active-workspace-roots", [])]
        saved_roots = [str(item).lower() for item in global_state.get("electron-saved-workspace-roots", [])]
        workspace_lower = str(Path(baseline["workspace"])).lower()
        checks.append(Check("global_state_parse", True, str(global_state_path)))
        checks.append(
            Check(
                "workspace_saved_root_present",
                workspace_lower in saved_roots,
                f"saved_roots={saved_roots}",
            )
        )
        checks.append(
            Check(
                "workspace_active_root_observed",
                True,
                (
                    f"required_workspace_active={workspace_lower in active_roots} "
                    f"active_roots={active_roots}; active roots are dynamic Desktop UI state and are not a startup invariant"
                ),
            )
        )
        for key, rule in baseline.get("global_state_required", {}).items():
            rule_ok, detail = global_state_rule_ok(global_state, key, rule)
            checks.append(Check(f"global_state_rule_{key}", rule_ok, detail))
    except Exception as exc:
        checks.append(Check("global_state_parse", False, repr(exc)))

    if not project_config_required:
        checks.append(Check("project_config_not_required", True, "host compatibility projection"))
    else:
        try:
            project_config, project_bom, _ = load_toml(project_config_path)
            checks.append(Check("project_config_parse", True, str(project_config_path)))
            checks.append(Check("project_config_no_bom", not project_bom, f"bom={project_bom}"))
            project_mcp = project_config.get("mcp_servers")
            project_plugins = project_config.get("plugins")
            checks.append(
            Check(
                "project_scope_no_mcp_servers",
                not isinstance(project_mcp, dict) or not project_mcp,
                f"project_mcp={sorted(project_mcp.keys()) if isinstance(project_mcp, dict) else []}",
            )
            )
            checks.append(
            Check(
                "project_scope_no_plugins",
                not isinstance(project_plugins, dict) or not project_plugins,
                f"project_plugins={sorted(project_plugins.keys()) if isinstance(project_plugins, dict) else []}",
            )
            )
            for dotted, expected in baseline["project_required_values"].items():
                actual = dotted_get(project_config, dotted)
                checks.append(Check(f"project_value_{dotted}", actual == expected, f"actual={actual!r} expected={expected!r}"))
        except Exception as exc:
            checks.append(Check("project_config_parse", False, repr(exc)))

    if run_cli:
        cli_config = global_config if isinstance(locals().get("global_config"), dict) else None
        ok, output = run_codex_mcp_list(cli_config, baseline)
        checks.append(Check("codex_mcp_list_runs", ok, output[:1200]))
        for name in sorted(expected_mcp):
            checks.append(Check(f"codex_mcp_list_has_{name}", name in output, output[:1200]))
    return checks


def check_surface(name: str) -> str:
    if name.startswith("authority_"):
        return "authority_boundary"
    if name.startswith("baseline_"):
        return "baseline"
    if name.startswith(("expected_mcp_", "extra_mcp_", "hub_managed_", "decommissioned_", "optional_mcp_")):
        return "mcp_configuration"
    if name.startswith(("codex_runtime_", "codex_mcp_")):
        return "mcp_runtime"
    if name.startswith(("expected_plugins_", "extra_plugins_", "marketplace_")) or "global_plugins" in name:
        return "plugins"
    if name.startswith(("workspace_", "global_state_")):
        return "workspace_state"
    if name.startswith(("project_", "project_value_")):
        return "project_configuration"
    if name.startswith(("global_config_", "global_value_")):
        return "global_configuration"
    return "other"


def compact_check_receipt(checks: list[Check]) -> dict:
    groups: dict[str, dict] = {}
    failures: list[dict] = []
    for check in checks:
        surface = check_surface(check.name)
        group = groups.setdefault(
            surface,
            {"surface": surface, "check_count": 0, "passed": 0, "failed": 0, "evidence": []},
        )
        group["check_count"] += 1
        group["passed" if check.ok else "failed"] += 1
        if len(group["evidence"]) < 3:
            group["evidence"].append(
                {
                    "name": check.name,
                    "ok": check.ok,
                    "detail": check.detail[:400],
                }
            )
        if not check.ok:
            failures.append(asdict(check))
    return {
        "check_count": len(checks),
        "passed_count": sum(1 for check in checks if check.ok),
        "failed_count": len(failures),
        "surfaces": [groups[key] for key in sorted(groups)],
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the durable Codex startup baseline.")
    parser.add_argument("--full", action="store_true", help="Return every individual check and its detail.")
    args = parser.parse_args()
    checks = build_checks()
    baseline = load_baseline()
    ok = all(check.ok for check in checks)
    global_state_ok = all(
        check.ok
        for check in checks
        if check.name.startswith("workspace_") or check.name.startswith("global_state_rule_")
    )
    payload = {
        "ok": ok,
        "workspace": str(baseline.get("declarative_work_git_root") or ROOT.parent),
        "audit_target": str(host_path(baseline.get("workspace") or ROOT)),
        "workspace_role": baseline.get("workspace_role", "legacy_unspecified"),
        "codex_desktop_running": codex_desktop_running(),
        "status_note": (
            "Codex Desktop is running and may rewrite .codex-global-state.json from memory. "
            "Only persisted workspace membership and explicit durable state rules are repairable startup evidence; the currently active workspace is informational."
            if not global_state_ok
            else ""
        ),
        "summary": compact_check_receipt(checks),
        "commands": {
            "full": "python _bridge\\codex_state_audit.py --full",
            "validate": "python _bridge\\codex_state_audit.py",
        },
        "output_evidence_policy": output_evidence_policy(),
    }
    if args.full:
        payload["checks"] = [asdict(check) for check in checks]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
