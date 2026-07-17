#!/usr/bin/env python3
"""Merge-repair the Codex startup baseline for this Windows workspace.

This script is intentionally conservative: it creates a backup, removes a
project config BOM if present, adds missing baseline sections, and fixes only
the small set of scalar values declared in codex_startup_baseline.json. It also
removes MCP profiles explicitly classified as decommissioned or Hub-managed.
Unclassified extra MCPs and plugins remain.
"""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import shutil
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

from mcp_execution_priority import DESKTOP_NATIVE_MCP_NAMES, HUB_MANAGED_MCP_NAMES

try:
    from shared.codex_desktop_package import query_desktop_host_processes
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.codex_desktop_package import query_desktop_host_processes


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "_bridge" / "codex_startup_baseline.json"
BACKUP_ROOT = ROOT / "_bridge" / "backups"


def toml_quote(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_quote(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def literal_toml_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def read_text_no_bom(path: Path) -> tuple[str, bool]:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    if bom:
        raw = raw[3:]
    return raw.decode("utf-8"), bom


def validate_toml(path: Path) -> None:
    tomllib.loads(path.read_text(encoding="utf-8"))


def table_header(table: str) -> str:
    return f"[{table}]"


def find_table(lines: list[str], table: str) -> tuple[int | None, int | None]:
    header = table_header(table)
    start = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    if start is None:
        return None, None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    return start, end


def _project_table_identity(header_line: str) -> str | None:
    stripped = header_line.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    try:
        projects = tomllib.loads(stripped + "\n").get("projects")
    except tomllib.TOMLDecodeError:
        return None
    if not isinstance(projects, dict) or len(projects) != 1:
        return None
    project_path, value = next(iter(projects.items()))
    if value != {}:
        return None
    return ntpath.normcase(ntpath.normpath(str(project_path)))


def normalize_duplicate_project_tables(text: str) -> tuple[str, bool]:
    """Remove semantically identical project tables with different TOML quoting."""

    lines = text.splitlines()
    headers = [
        index
        for index, line in enumerate(lines)
        if line.strip().startswith("[") and line.strip().endswith("]")
    ]
    seen: dict[str, dict] = {}
    removals: list[tuple[int, int]] = []
    for position, start in enumerate(headers):
        identity = _project_table_identity(lines[start])
        if identity is None:
            continue
        end = headers[position + 1] if position + 1 < len(headers) else len(lines)
        section = "\n".join(lines[start:end]).strip() + "\n"
        parsed_projects = tomllib.loads(section).get("projects") or {}
        project_value = next(iter(parsed_projects.values()))
        if identity not in seen:
            seen[identity] = project_value
            continue
        if seen[identity] != project_value:
            raise ValueError(f"conflicting_duplicate_project_table:{identity}")
        removals.append((start, end))
    if not removals:
        return text, False
    for start, end in reversed(removals):
        del lines[start:end]
    compact: list[str] = []
    for line in lines:
        if not line.strip() and compact and not compact[-1].strip():
            continue
        compact.append(line)
    return "\n".join(compact).rstrip() + "\n", True


def set_table_key(text: str, table: str | None, key: str, value: object) -> tuple[str, bool]:
    lines = text.splitlines()
    assignment = f"{key} = {toml_quote(value)}"
    if table is None:
        start = 0
        end = len(lines)
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                end = index
                break
    else:
        start, end = find_table(lines, table)
        if start is None or end is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(table_header(table))
            lines.append(assignment)
            return "\n".join(lines) + "\n", True
        start += 1
    for index in range(start, end):
        stripped = lines[index].strip()
        if stripped.startswith(f"{key} ") or stripped.startswith(f"{key}="):
            if stripped == assignment:
                return text, False
            lines[index] = assignment
            return "\n".join(lines) + "\n", True
    lines.insert(end, assignment)
    return "\n".join(lines) + "\n", True


def has_table(text: str, table: str) -> bool:
    return any(line.strip() == table_header(table) for line in text.splitlines())


def remove_table_tree(text: str, table_names: tuple[str, ...]) -> tuple[str, bool]:
    lines = text.splitlines()
    kept: list[str] = []
    removing = False
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            header = stripped[1:-1]
            removing = any(header == name or header.startswith(f"{name}.") for name in table_names)
            if removing:
                changed = True
                continue
        if not removing:
            kept.append(line)
    if not changed:
        return text, False
    compact: list[str] = []
    for line in kept:
        if not line.strip() and compact and not compact[-1].strip():
            continue
        compact.append(line)
    return "\n".join(compact).rstrip() + "\n", True


def ensure_plugin(text: str, plugin: str) -> tuple[str, bool]:
    return set_table_key(text, f'plugins."{plugin}"', "enabled", True)


def ensure_marketplace(text: str, name: str, spec: dict) -> tuple[str, bool]:
    table = f"marketplaces.{name}"
    changed = False
    for key in ("last_updated", "source_type", "source"):
        if key in spec:
            text, did = set_table_key(text, table, key, spec[key])
            changed = changed or did
    return text, changed


def ensure_mcp_server(text: str, name: str, spec: dict) -> tuple[str, bool]:
    table_name = f'mcp_servers.{name}'
    quoted_table_name = f'mcp_servers."{name}"'
    existing_table = quoted_table_name if has_table(text, quoted_table_name) else (
        table_name if has_table(text, table_name) else None
    )
    if existing_table:
        changed = False
        try:
            parsed_servers = tomllib.loads(text).get("mcp_servers", {})
            parsed_server = parsed_servers.get(name, {}) if isinstance(parsed_servers, dict) else {}
        except Exception:
            parsed_server = {}
        if "command" in spec and str(parsed_server.get("command") or "").lower() != str(spec["command"]).lower():
            text, did = set_table_key(text, existing_table, "command", spec["command"])
            changed = changed or did
        parsed_args = parsed_server.get("args") if isinstance(parsed_server, dict) else None
        parsed_arg_strings = [str(item) for item in parsed_args] if isinstance(parsed_args, list) else []
        expected_arg_strings = [str(item) for item in spec.get("args", [])]
        if "args" in spec and parsed_arg_strings != expected_arg_strings:
            text, did = set_table_key(text, existing_table, "args", spec.get("args", []))
            changed = changed or did
        for key in ("url", "bearer_token_env_var", "startup_timeout_sec", "required"):
            if key in spec and parsed_server.get(key) != spec[key]:
                text, did = set_table_key(text, existing_table, key, spec[key])
                changed = changed or did
        env = spec.get("env") or {}
        if env:
            env_table = f"{existing_table}.env"
            parsed_env = parsed_server.get("env") if isinstance(parsed_server.get("env"), dict) else {}
            for key, value in env.items():
                if str(parsed_env.get(key) or "") != str(value):
                    text, did = set_table_key(text, env_table, key, value)
                    changed = changed or did
        return text, changed
    if "command" not in spec and "url" not in spec:
        return text, False

    quoted_name = name if "-" not in name else f'"{name}"'
    lines = [""]
    lines.append(f"[mcp_servers.{quoted_name}]")
    if "url" in spec:
        lines.append(f"url = {toml_quote(spec['url'])}")
        if "bearer_token_env_var" in spec:
            lines.append(f"bearer_token_env_var = {toml_quote(spec['bearer_token_env_var'])}")
    else:
        lines.append(f"args = {toml_quote(spec.get('args', []))}")
        lines.append(f"command = {literal_toml_quote(spec['command'])}")
    if "startup_timeout_sec" in spec:
        lines.append(f"startup_timeout_sec = {toml_quote(spec['startup_timeout_sec'])}")
    if "required" in spec:
        lines.append(f"required = {toml_quote(bool(spec['required']))}")

    env = spec.get("env") or {}
    if env:
        lines.append("")
        lines.append(f"[mcp_servers.{quoted_name}.env]")
        for key, value in env.items():
            lines.append(f"{key} = {toml_quote(value)}")

    tools = spec.get("tools") or []
    approval_mode = spec.get("tool_approval_mode")
    if approval_mode:
        for tool in tools:
            lines.append("")
            lines.append(f"[mcp_servers.{quoted_name}.tools.{tool}]")
            lines.append(f"approval_mode = {toml_quote(approval_mode)}")
    return text.rstrip() + "\n" + "\n".join(lines) + "\n", True


def ensure_project_trusted(text: str, dotted: str, expected: str) -> tuple[str, bool]:
    project_path = dotted.removeprefix("projects.").removesuffix(".trust_level")
    text, normalized = normalize_duplicate_project_tables(text)
    expected_identity = ntpath.normcase(ntpath.normpath(project_path))
    for line in text.splitlines():
        if _project_table_identity(line) == expected_identity:
            table = line.strip()[1:-1]
            text, changed = set_table_key(text, table, "trust_level", expected)
            return text, normalized or changed
    text, changed = set_table_key(text, f"projects.'{project_path}'", "trust_level", expected)
    return text, normalized or changed


def json_dotted_get(data: dict, dotted: str) -> object:
    current: object = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def json_dotted_set(data: dict, dotted: str, value: object) -> bool:
    current = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    old = current.get(parts[-1])
    if old == value:
        return False
    current[parts[-1]] = value
    return True


def ensure_json_list_contains(data: dict, key: str, expected_items: list[str], prefer_first: bool = False) -> bool:
    current = data.get(key)
    changed = False
    if not isinstance(current, list):
        current = []
        data[key] = current
        changed = True
    existing_lower = {str(item).lower() for item in current}
    for item in expected_items:
        if str(item).lower() not in existing_lower:
            current.append(item)
            existing_lower.add(str(item).lower())
            changed = True
    if prefer_first and expected_items:
        expected_lower = str(expected_items[0]).lower()
        old = list(current)
        current[:] = [item for item in current if str(item).lower() == expected_lower] + [
            item for item in current if str(item).lower() != expected_lower
        ]
        changed = changed or old != current
    return changed


def repair_global_state(baseline: dict) -> tuple[dict, list[str]]:
    global_state_path = Path(baseline.get("global_state", Path.home() / ".codex" / ".codex-global-state.json"))
    if not global_state_path.exists():
        return {}, [f"global_state_missing_{global_state_path}"]
    data = json.loads(global_state_path.read_text(encoding="utf-8"))
    changed: list[str] = []
    for key, rule in baseline.get("global_state_required", {}).items():
        if isinstance(rule, dict) and "contains" in rule:
            did = ensure_json_list_contains(
                data,
                key,
                list(rule.get("contains", [])),
                bool(rule.get("prefer_first", False)),
            )
            if did:
                changed.append(f"global_state_list_set_{key}")
        elif json_dotted_set(data, key, rule):
            changed.append(f"global_state_value_set_{key}")
    return data, changed


def codex_cli_candidates_from_baseline(baseline: dict | None = None) -> list[str]:
    candidates: list[str] = []
    if isinstance(baseline, dict):
        node_repl = (baseline.get("expected_mcp") or {}).get("node_repl")
        if isinstance(node_repl, dict):
            env = node_repl.get("env")
            if isinstance(env, dict):
                value = str(env.get("CODEX_CLI_PATH") or "").strip()
                if value:
                    candidates.append(value)
    return candidates


def normalize_path_text(value: str) -> str:
    return str(Path(value)).casefold() if value else ""


def discover_latest_codex_cli() -> str | None:
    candidates: list[Path] = []
    bin_root = Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin"
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


def discover_latest_node_repl_runtime() -> dict[str, str]:
    runtime_root = Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "runtimes" / "cua_node"
    candidates = [path for path in runtime_root.glob("*/bin/node_repl.exe") if path.exists()]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for executable in candidates:
        bin_dir = executable.parent
        node_path = bin_dir / "node.exe"
        modules_path = bin_dir / "node_modules"
        if node_path.exists() and modules_path.exists():
            return {
                "command": str(executable),
                "node_path": str(node_path),
                "node_modules": str(modules_path),
            }
    return {}


def refresh_runtime_pointers(baseline: dict) -> list[str]:
    changed: list[str] = []
    latest_cli = discover_latest_codex_cli()
    expected_mcp = baseline.get("expected_mcp")
    if not isinstance(expected_mcp, dict):
        return changed
    node_repl = expected_mcp.get("node_repl")
    if not isinstance(node_repl, dict):
        return changed
    env = node_repl.setdefault("env", {})
    if not isinstance(env, dict):
        env = {}
        node_repl["env"] = env
        changed.append("baseline_node_repl_env_recreated")
    current_cli = str(env.get("CODEX_CLI_PATH") or "").strip()
    if latest_cli and normalize_path_text(current_cli) != normalize_path_text(latest_cli):
        env["CODEX_CLI_PATH"] = latest_cli
        changed.append("baseline_node_repl_codex_cli_path_set")
    runtime = discover_latest_node_repl_runtime()
    if runtime:
        if normalize_path_text(str(node_repl.get("command") or "")) != normalize_path_text(runtime["command"]):
            node_repl["command"] = runtime["command"]
            changed.append("baseline_node_repl_command_set")
        if normalize_path_text(str(env.get("NODE_REPL_NODE_PATH") or "")) != normalize_path_text(runtime["node_path"]):
            env["NODE_REPL_NODE_PATH"] = runtime["node_path"]
            changed.append("baseline_node_repl_node_path_set")
        if normalize_path_text(str(env.get("NODE_REPL_NODE_MODULE_DIRS") or "")) != normalize_path_text(runtime["node_modules"]):
            env["NODE_REPL_NODE_MODULE_DIRS"] = runtime["node_modules"]
            changed.append("baseline_node_repl_node_modules_set")
    return changed


def codex_desktop_running() -> bool:
    try:
        return bool(query_desktop_host_processes(main_only=True))
    except Exception:
        return False


def backup_files(paths: list[Path], *, changed: list[str] | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}-codex-state-repair"
    backup_dir.mkdir(parents=True, exist_ok=True)
    labels = ["global", "project", "global_state"]
    for index, path in enumerate(paths):
        if path.exists():
            label = labels[index] if index < len(labels) else f"file{index + 1}"
            shutil.copy2(path, backup_dir / f"{label}_{path.name}")
    shutil.copy2(BASELINE_PATH, backup_dir / "codex_startup_baseline.json")
    note_lines = [
        "purpose: Codex startup baseline merge-repair backup",
        "policy: additive/merge-only repair; no wholesale config restore; preserve extra user config",
        "changed:",
    ]
    for item in changed or []:
        note_lines.append(f"- {item}")
    (backup_dir / "BACKUP_NOTE.txt").write_text("\n".join(note_lines) + "\n", encoding="utf-8")
    return backup_dir


def resolve_codex_cli(baseline: dict | None = None) -> str | None:
    candidates = []
    env_value = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env_value:
        candidates.append(env_value)
    candidates.extend(codex_cli_candidates_from_baseline(baseline))
    latest = discover_latest_codex_cli()
    if latest:
        candidates.append(latest)
    candidates.extend([
        shutil.which("codex") or "",
        shutil.which("codex.exe") or "",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def expected_mcp_specs(baseline: dict) -> dict[str, dict]:
    expected = baseline.get("expected_mcp") or {}
    if not isinstance(expected, dict):
        return {}
    return {
        name: spec
        for name, spec in expected.items()
        if isinstance(spec, dict) and name in DESKTOP_NATIVE_MCP_NAMES
    }


def repair(dry_run: bool, *, runtime_validation: bool = True) -> dict:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_changed = refresh_runtime_pointers(baseline)
    global_config = Path(baseline["global_config"])
    project_config = Path(baseline["project_config"])
    global_state_path = Path(baseline.get("global_state", Path.home() / ".codex" / ".codex-global-state.json"))
    backup_dir: Path | None = None

    changed: list[str] = list(baseline_changed)
    global_text, global_bom = read_text_no_bom(global_config)
    if global_bom:
        changed.append("global_config_remove_bom")
    global_text, project_tables_normalized = normalize_duplicate_project_tables(global_text)
    if project_tables_normalized:
        changed.append("global_config_normalize_duplicate_project_tables")

    managed_mcp = expected_mcp_specs(baseline)
    decommissioned_mcp = baseline.get("decommissioned_mcp") or {}
    if not isinstance(decommissioned_mcp, dict):
        decommissioned_mcp = {}
    for name in sorted(decommissioned_mcp):
        table_names = (f"mcp_servers.{name}", f'mcp_servers."{name}"')
        global_text, did = remove_table_tree(global_text, table_names)
        if did:
            changed.append(f"global_mcp_remove_decommissioned_{name}")

    for name in sorted(HUB_MANAGED_MCP_NAMES):
        table_names = (f"mcp_servers.{name}", f'mcp_servers."{name}"')
        global_text, did = remove_table_tree(global_text, table_names)
        if did:
            changed.append(f"global_mcp_remove_hub_managed_{name}")

    startup_optional_mcp = sorted(
        name
        for name, spec in (baseline.get("expected_mcp") or {}).items()
        if isinstance(spec, dict) and spec.get("required") is not True
    )

    for name, spec in managed_mcp.items():
        global_text, did = ensure_mcp_server(global_text, name, spec)
        if did:
            changed.append(f"global_mcp_add_{name}")

    for plugin in baseline["expected_plugins"]:
        global_text, did = ensure_plugin(global_text, plugin)
        if did:
            changed.append(f"global_plugin_enable_{plugin}")

    for name, spec in (baseline.get("expected_marketplaces") or {}).items():
        global_text, did = ensure_marketplace(global_text, name, spec)
        if did:
            changed.append(f"global_marketplace_set_{name}")

    for dotted, expected in baseline["global_required_values"].items():
        if dotted.startswith("projects."):
            global_text, did = ensure_project_trusted(global_text, dotted, expected)
        else:
            parts = dotted.split(".")
            if len(parts) == 1:
                global_text, did = set_table_key(global_text, None, parts[0], expected)
            else:
                global_text, did = set_table_key(global_text, parts[0], parts[1], expected)
        if did:
            changed.append(f"global_value_set_{dotted}")

    project_text, project_bom = read_text_no_bom(project_config)
    if project_bom:
        changed.append("project_config_remove_bom")
    for dotted, expected in baseline["project_required_values"].items():
        parts = dotted.split(".")
        if len(parts) == 1:
            project_text, did = set_table_key(project_text, None, parts[0], expected)
        else:
            project_text, did = set_table_key(project_text, parts[0], parts[1], expected)
        if did:
            changed.append(f"project_value_set_{dotted}")

    global_state_data, global_state_changed = repair_global_state(baseline)
    changed.extend(global_state_changed)

    if not dry_run:
        backup_dir = backup_files([global_config, project_config, global_state_path], changed=changed)
        if baseline_changed:
            BASELINE_PATH.write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        global_config.write_text(global_text, encoding="utf-8", newline="\n")
        project_config.write_text(project_text, encoding="utf-8", newline="\n")
        if global_state_data:
            global_state_path.write_text(
                json.dumps(global_state_data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
                newline="\n",
            )
        validate_toml(global_config)
        validate_toml(project_config)

    mcp_ok: bool | None = None
    mcp_output = "skipped in dry-run"
    if not dry_run and runtime_validation:
        codex_cli = resolve_codex_cli(baseline)
        if not codex_cli:
            mcp_ok = False
            mcp_output = "codex CLI not found; skipped mcp list"
        else:
            try:
                proc = subprocess.run(
                    [codex_cli, "mcp", "list"],
                    cwd=str(ROOT),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=30,
                )
                mcp_ok = proc.returncode == 0
                mcp_output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            except Exception as exc:
                mcp_ok = False
                mcp_output = f"mcp list skipped: {exc!r}"

    return {
        "ok": True,
        "dry_run": dry_run,
        "backup_dir": str(backup_dir) if backup_dir else None,
        "changed": changed,
        "startup_optional_mcp": startup_optional_mcp,
        "needs_codex_restart": bool(changed),
        "runtime_validation": runtime_validation,
        "codex_desktop_running": codex_desktop_running() if runtime_validation else None,
        "note": (
            "global_state changes may be overwritten by a running Codex Desktop; "
            "for those changes, run this before starting Codex or restart Codex after repair."
            if any(item.startswith("global_state_") for item in changed)
            else ""
        ),
        "codex_mcp_list_ok": mcp_ok,
        "codex_mcp_list": mcp_output[:2000],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair this workspace's Codex startup baseline.")
    parser.add_argument("--dry-run", action="store_true", help="Create backup and report intended changes without writing configs.")
    args = parser.parse_args()
    try:
        result = repair(args.dry_run)
    except Exception as exc:
        result = {"ok": False, "error": repr(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
