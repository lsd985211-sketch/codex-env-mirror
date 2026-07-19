#!/usr/bin/env python3
"""Merge-repair the Codex startup baseline for this Windows workspace.

This script is intentionally conservative: it backs up and writes only files
whose resulting UTF-8 bytes differ, removes a config BOM if present, adds
missing baseline sections, and fixes only the small set of scalar values
declared in codex_startup_baseline.json. It also removes MCP profiles explicitly
classified as decommissioned or Hub-managed. Unclassified extra MCPs and
plugins remain.
"""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import shutil
import sqlite3
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

from mcp_execution_priority import DESKTOP_NATIVE_MCP_NAMES, HUB_MANAGED_MCP_NAMES
from codex_wsl_resume_context import project_wsl_resume_state

try:
    from shared.codex_desktop_package import query_desktop_host_processes
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.codex_desktop_package import query_desktop_host_processes


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "_bridge" / "codex_startup_baseline.json"
BACKUP_ROOT = ROOT / "_bridge" / "backups"
WSL_DISTRIBUTION = "Codex-Wsl-Lab"
WSL_USER = "codexlab"
WSL_RUNTIME_OWNER = "/home/codexlab/work/codex-workspace/workspace/_bridge/wsl_codex_runtime.py"
WSL_ENVIRONMENT_SELECTION_OWNER = (
    "/home/codexlab/work/codex-workspace/workspace/_bridge/codex_desktop_environment_selection.py"
)
WSL_CODEX_CONFIG = "/home/codexlab/.codex-app/config.toml"
WSL_ENVIRONMENT_SELECTION_STATE = (
    "/home/codexlab/.codex-app/state/desktop-environment-selection.json"
)


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


def utf8_write_required(path: Path, text: str) -> bool:
    """Return whether writing UTF-8 text would change the file bytes."""
    return not path.exists() or path.read_bytes() != text.encode("utf-8")


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


def windows_path_to_wsl_path(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.startswith("/"):
        return text
    if len(text) >= 3 and text[1] == ":" and text[2] in ("\\", "/"):
        drive = text[0].lower()
        rest = text[3:].replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return text


def stable_node_repl_windows_path() -> Path:
    return Path.home() / ".local" / "bin" / "node_repl.exe"


def stable_node_repl_wsl_command() -> str:
    return "/home/codexlab/.local/bin/codex-node-repl"


def stable_node_repl_command(*, desktop_wsl_enabled: bool) -> str:
    if desktop_wsl_enabled:
        return stable_node_repl_wsl_command()
    return "cmd.exe"


def stable_node_repl_args(*, desktop_wsl_enabled: bool = False) -> list[str]:
    if desktop_wsl_enabled:
        return []
    executable = str(stable_node_repl_windows_path()).replace("\\", "/")
    return ["/d", "/c", executable]


def codex_desktop_wsl_enabled(baseline: dict) -> bool:
    desktop = baseline.get("desktop")
    if not isinstance(desktop, dict):
        return False
    return bool(desktop.get("runCodexInWindowsSubsystemForLinux"))


def _desktop_wsl_value_from_config(path: Path) -> bool:
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    desktop = parsed.get("desktop") if isinstance(parsed, dict) else None
    return bool(isinstance(desktop, dict) and desktop.get("runCodexInWindowsSubsystemForLinux"))


def ensure_desktop_environment_selection(*, host_config: Path, dry_run: bool) -> dict:
    """Run the cross-platform selection owner before mode-specific repair."""
    host_value = _desktop_wsl_value_from_config(host_config)
    if os.name != "nt":
        return {
            "ok": True,
            "status": "not_windows_host",
            "changed": False,
            "ready": True,
            "selected_value": host_value,
        }
    if not dry_run and codex_desktop_running():
        return {
            "ok": True,
            "status": "deferred_desktop_running",
            "changed": False,
            "ready": False,
            "deferred": True,
            "selected_value": host_value,
        }
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return {
            "ok": False,
            "status": "wsl_executable_missing",
            "changed": False,
            "ready": False,
            "selected_value": host_value,
        }
    host_path = windows_path_to_wsl_path(str(host_config))
    command = [
        wsl,
        "-d",
        WSL_DISTRIBUTION,
        "-u",
        WSL_USER,
        "--",
        "python3",
        WSL_ENVIRONMENT_SELECTION_OWNER,
        "plan" if dry_run else "apply",
        "--host-config",
        host_path,
        "--wsl-config",
        WSL_CODEX_CONFIG,
        "--state-path",
        WSL_ENVIRONMENT_SELECTION_STATE,
    ]
    try:
        process = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "status": "owner_invocation_failed",
            "changed": False,
            "ready": False,
            "selected_value": host_value,
            "error": str(exc),
        }
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        payload = {}
    owner_ok = process.returncode == 0 and bool(payload.get("ok"))
    selected = payload.get("selected_value")
    if not isinstance(selected, bool):
        selected = host_value
    return {
        "ok": owner_ok,
        "status": str(payload.get("status") or ("owner_failed" if not owner_ok else "ready")),
        "changed": bool(payload.get("changed")),
        "ready": owner_ok,
        "selected_value": selected,
        "selection_source": str(payload.get("selection_source") or "host_fallback"),
        "owner_result": payload,
        "returncode": process.returncode,
        "stderr": (process.stderr or "")[-1000:],
    }


def _top_level_thread_source(value: object) -> bool:
    text = str(value or "").strip()
    if not text.startswith("{"):
        return True
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return True
    return not (isinstance(parsed, dict) and "subagent" in parsed)


def load_top_level_thread_ids(path: Path) -> dict:
    """Read current top-level task identities and cwd values without changing the source DB."""
    if not path.is_file():
        return {"ok": False, "status": "thread_state_missing", "path": str(path), "thread_ids": []}
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")}
        required = {"id", "source", "rollout_path", "archived"}
        if not required.issubset(columns):
            connection.close()
            return {
                "ok": False,
                "status": "thread_state_schema_incomplete",
                "path": str(path),
                "missing_columns": sorted(required - columns),
                "thread_ids": [],
            }
        order_column = next(
            (name for name in ("recency_at_ms", "updated_at_ms", "updated_at") if name in columns),
            "id",
        )
        cwd_expression = "cwd" if "cwd" in columns else "''"
        rows = connection.execute(
            f"SELECT id, source, rollout_path, {cwd_expression} FROM threads "
            f"WHERE archived = 0 ORDER BY {order_column} DESC"
        ).fetchall()
        connection.close()
    except (OSError, sqlite3.Error) as exc:
        return {
            "ok": False,
            "status": "thread_state_unreadable",
            "path": str(path),
            "error": str(exc),
            "thread_ids": [],
        }
    thread_ids = []
    missing_session_count = 0
    thread_cwds: dict[str, str] = {}
    for thread_id, source, rollout_path, cwd in rows:
        if not _top_level_thread_source(source):
            continue
        raw_rollout = str(rollout_path or "")
        candidate = Path(raw_rollout if os.name == "nt" else windows_path_to_wsl_path(raw_rollout))
        if not candidate.is_file():
            missing_session_count += 1
            continue
        normalized_id = str(thread_id)
        thread_ids.append(normalized_id)
        thread_cwds[normalized_id] = str(cwd or "")
    return {
        "ok": True,
        "status": "ready",
        "path": str(path),
        "thread_ids": thread_ids,
        "thread_cwds": thread_cwds,
        "thread_count": len(thread_ids),
        "missing_session_count": missing_session_count,
    }


def ensure_wsl_resume_context_projection(
    *,
    enabled: bool,
    dry_run: bool,
    global_state_path: Path | None = None,
    thread_state_path: Path | None = None,
) -> dict:
    """Repair Desktop resume cwd fields only at a clean startup boundary."""
    if not enabled:
        return {
            "ok": True,
            "status": "not_required",
            "enabled": False,
            "changed": False,
            "ready": True,
        }
    if not dry_run and codex_desktop_running():
        return {
            "ok": True,
            "status": "deferred_desktop_running",
            "enabled": True,
            "changed": False,
            "ready": False,
            "deferred": True,
        }
    if global_state_path is None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        global_state_path = Path(
            baseline.get("global_state", Path.home() / ".codex" / ".codex-global-state.json")
        )
    if not global_state_path.is_file():
        return {
            "ok": False,
            "status": "global_state_missing",
            "enabled": True,
            "changed": False,
            "ready": False,
            "path": str(global_state_path),
        }
    try:
        state = json.loads(global_state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "status": "global_state_invalid",
            "enabled": True,
            "changed": False,
            "ready": False,
            "path": str(global_state_path),
            "error": repr(exc),
        }
    if thread_state_path is None:
        thread_state_path = global_state_path.parent / "state_5.sqlite"
    thread_index = load_top_level_thread_ids(thread_state_path)
    projection = project_wsl_resume_state(
        state,
        list(thread_index.get("thread_ids") or []),
        thread_cwds=dict(thread_index.get("thread_cwds") or {}),
    )
    changed = bool(projection.get("changed"))
    visibility = projection.get("task_visibility") or {}
    result = {
        "ok": True,
        "status": "already_current",
        "enabled": True,
        "changed": changed,
        "ready": True,
        "path": str(global_state_path),
        "changed_field_count": int(projection.get("changed_field_count") or 0),
        "thread_count": int(projection.get("thread_count") or 0),
        "task_index_ok": bool(thread_index.get("ok")),
        "task_index_status": str(thread_index.get("status") or "not_reported"),
        "eligible_task_count": int(thread_index.get("thread_count") or 0),
        "indexed_task_count": int(visibility.get("added_count") or 0),
        "assigned_task_count": int(visibility.get("assigned_count") or 0),
        "removed_projectless_task_count": int(visibility.get("removed_projectless_count") or 0),
        "task_visibility_status": str(visibility.get("status") or "not_reported"),
    }
    if not changed:
        return result
    if dry_run:
        return {**result, "status": "would_apply", "ready": False, "dry_run": True}

    backup_dir = backup_files(
        [global_state_path],
        labels=["global_state"],
        changed=["global_state_wsl_resume_context_task_visibility_and_project_assignment_projection"],
    )
    global_state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )
    json.loads(global_state_path.read_text(encoding="utf-8"))
    return {**result, "status": "applied", "backup_dir": str(backup_dir)}


def ensure_wsl_runtime_projection(*, enabled: bool, dry_run: bool) -> dict:
    """Prepare the WSL-native Codex home before Desktop starts its app-server."""
    if not enabled:
        return {
            "ok": True,
            "status": "not_required",
            "enabled": False,
            "changed": False,
            "ready": True,
        }
    if not dry_run and codex_desktop_running():
        return {
            "ok": True,
            "status": "deferred_desktop_running",
            "enabled": True,
            "changed": False,
            "ready": False,
            "deferred": True,
        }
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return {
            "ok": False,
            "status": "wsl_executable_missing",
            "enabled": True,
            "changed": False,
            "ready": False,
        }
    command = [
        wsl,
        "-d",
        WSL_DISTRIBUTION,
        "-u",
        WSL_USER,
        "--",
        "python3",
        WSL_RUNTIME_OWNER,
        "plan" if dry_run else "apply",
    ]
    try:
        process = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "status": "owner_invocation_failed",
            "enabled": True,
            "changed": False,
            "ready": False,
            "error": str(exc),
        }
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        payload = {}
    owner_ok = process.returncode == 0 and bool(payload.get("ok"))
    session_projection = payload.get("session_projection")
    session_projection = session_projection if isinstance(session_projection, dict) else {}
    state_projection = payload.get("state_projection")
    state_projection = state_projection if isinstance(state_projection, dict) else {}
    session_ready = bool(
        session_projection.get("ok")
        and session_projection.get("status") in {"projected", "source_missing_optional"}
        and session_projection.get("source_count") == session_projection.get("projected_count")
    )
    state_ready = bool(
        state_projection.get("ok")
        and state_projection.get("status") in {"ready", "updated", "missing_optional"}
        and not state_projection.get("source_rejected_row_count")
        and not state_projection.get("source_missing_row_count")
    )
    ready = bool(owner_ok and session_ready and state_ready)
    return {
        "ok": ready,
        "status": (
            "planned" if dry_run and ready
            else "applied" if ready
            else "owner_incomplete" if owner_ok
            else "owner_failed"
        ),
        "enabled": True,
        "changed": bool(payload.get("changed")),
        "ready": ready,
        "owner_result": payload,
        "returncode": process.returncode,
        "stderr": (process.stderr or "")[-1000:],
    }


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
                "stable_command": stable_node_repl_command(desktop_wsl_enabled=False),
                "stable_args": stable_node_repl_args(desktop_wsl_enabled=False),
            }
    return {}


def install_stable_node_repl_entry(runtime: dict[str, str]) -> bool:
    source = Path(runtime.get("command") or "")
    target = stable_node_repl_windows_path()
    if not source.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()
    if target.exists() and target.read_bytes() == source_bytes:
        return False
    target.write_bytes(source_bytes)
    return True


def refresh_runtime_pointers(baseline: dict) -> list[str]:
    changed: list[str] = []
    desktop_wsl_enabled = codex_desktop_wsl_enabled(baseline)
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
        if install_stable_node_repl_entry(runtime):
            changed.append("stable_node_repl_entry_updated")
        runtime_command = runtime["command"]
        expected_command = runtime.get("stable_command", stable_node_repl_command(desktop_wsl_enabled=False))
        if normalize_path_text(str(node_repl.get("command") or "")) != normalize_path_text(expected_command):
            node_repl["command"] = expected_command
            changed.append("baseline_node_repl_command_set")
        expected_args = runtime.get("stable_args", stable_node_repl_args(desktop_wsl_enabled=False))
        if [str(item) for item in node_repl.get("args", [])] != [str(item) for item in expected_args]:
            node_repl["args"] = expected_args
            changed.append("baseline_node_repl_args_set")
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


def backup_files(
    paths: list[Path],
    *,
    labels: list[str] | None = None,
    changed: list[str] | None = None,
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}-codex-state-repair"
    backup_dir.mkdir(parents=True, exist_ok=True)
    resolved_labels = labels if labels is not None else ["global", "project", "global_state"]
    if len(resolved_labels) != len(paths):
        raise ValueError("backup labels must match backup paths")
    for index, path in enumerate(paths):
        if path.exists():
            label = resolved_labels[index]
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


def expected_mcp_specs(baseline: dict, *, desktop_wsl_enabled: bool = False) -> dict[str, dict]:
    expected = baseline.get("expected_mcp") or {}
    if not isinstance(expected, dict):
        return {}
    specs: dict[str, dict] = {}
    for name, spec in expected.items():
        if not isinstance(spec, dict) or name not in DESKTOP_NATIVE_MCP_NAMES:
            continue
        resolved = dict(spec)
        if name == "node_repl":
            if "command" in resolved:
                resolved["command"] = stable_node_repl_command(desktop_wsl_enabled=desktop_wsl_enabled)
            resolved["args"] = stable_node_repl_args(desktop_wsl_enabled=desktop_wsl_enabled)
        specs[name] = resolved
    return specs


def repair(dry_run: bool, *, runtime_validation: bool = True) -> dict:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    global_config = Path(baseline["global_config"])
    environment_selection = ensure_desktop_environment_selection(
        host_config=global_config,
        dry_run=dry_run,
    )
    baseline_changed = refresh_runtime_pointers(baseline)
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

    selected_environment = environment_selection.get("selected_value")
    desktop_wsl_enabled = (
        selected_environment
        if isinstance(selected_environment, bool)
        else _desktop_wsl_value_from_config(global_config)
    )
    managed_mcp = expected_mcp_specs(baseline, desktop_wsl_enabled=desktop_wsl_enabled)
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

    baseline_text = json.dumps(baseline, ensure_ascii=False, indent=2) + "\n"
    global_state_text = (
        json.dumps(global_state_data, ensure_ascii=False, separators=(",", ":"))
        if global_state_data
        else None
    )
    write_decisions = {
        "baseline": utf8_write_required(BASELINE_PATH, baseline_text),
        "global_config": utf8_write_required(global_config, global_text),
        "project_config": utf8_write_required(project_config, project_text),
        "global_state": bool(
            global_state_text is not None
            and utf8_write_required(global_state_path, global_state_text)
        ),
    }
    written = [name for name, required in write_decisions.items() if required]

    if not dry_run:
        backup_paths: list[Path] = []
        backup_labels: list[str] = []
        for name, path, label in (
            ("global_config", global_config, "global"),
            ("project_config", project_config, "project"),
            ("global_state", global_state_path, "global_state"),
        ):
            if write_decisions[name]:
                backup_paths.append(path)
                backup_labels.append(label)
        if written:
            backup_dir = backup_files(
                backup_paths,
                labels=backup_labels,
                changed=changed,
            )
        if write_decisions["baseline"]:
            BASELINE_PATH.write_text(
                baseline_text,
                encoding="utf-8",
                newline="\n",
            )
        if write_decisions["global_config"]:
            global_config.write_text(global_text, encoding="utf-8", newline="\n")
        if write_decisions["project_config"]:
            project_config.write_text(project_text, encoding="utf-8", newline="\n")
        if write_decisions["global_state"] and global_state_text is not None:
            global_state_path.write_text(
                global_state_text,
                encoding="utf-8",
                newline="\n",
            )
        validate_toml(global_config)
        validate_toml(project_config)

    wsl_runtime_projection = ensure_wsl_runtime_projection(
        enabled=desktop_wsl_enabled,
        dry_run=dry_run,
    )

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
        "ok": bool(wsl_runtime_projection.get("ok")),
        "dry_run": dry_run,
        "backup_dir": str(backup_dir) if backup_dir else None,
        "changed": changed,
        "written": written,
        "write_decisions": write_decisions,
        "startup_optional_mcp": startup_optional_mcp,
        "needs_codex_restart": bool(
            changed
            or written
            or environment_selection.get("changed")
            or wsl_runtime_projection.get("changed")
        ),
        "wsl_runtime_projection": wsl_runtime_projection,
        "desktop_environment_selection": environment_selection,
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
    parser.add_argument("--dry-run", action="store_true", help="Report intended changes without backing up or writing configs.")
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
