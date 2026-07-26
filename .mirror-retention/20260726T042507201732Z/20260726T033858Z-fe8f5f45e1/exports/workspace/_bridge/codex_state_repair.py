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
import hashlib
import json
import ntpath
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path

from mcp_execution_priority import DESKTOP_NATIVE_MCP_NAMES, HUB_MANAGED_MCP_NAMES
from codex_wsl_resume_context import project_wsl_resume_state
from platform_paths import host_accessible_path, windows_user_root

try:
    from shared.codex_desktop_package import query_desktop_host_processes
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.codex_desktop_package import query_desktop_host_processes

try:
    from shared.backup_router import create_backup
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge.shared.backup_router import create_backup


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "_bridge" / "codex_startup_baseline.json"
BACKUP_ROOT = ROOT / "_bridge" / "backups"
WSL_DISTRIBUTION = "Codex-Wsl-Lab"
WSL_USER = "codexlab"
WSL_RUNTIME_OWNER = "/home/codexlab/work/codex-workspace/workspace/_bridge/wsl_codex_runtime.py"
RUNTIME_REPAIR_NO_WINDOW_FLAG = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
WINDOWS_SESSION_BACKUP_ROOT = Path.home() / ".codex" / "backups" / "codex-session"
WINDOWS_WSL_STATE_SNAPSHOT = Path.home() / ".codex" / "state" / "wsl-projection" / "state_5.sqlite"
WINDOWS_MOUNT_PATH_RE = re.compile(r"^/mnt/(?P<drive>[A-Za-z])(?:/(?P<rest>.*))?$")
MALFORMED_WINDOWS_MOUNT_PATH_RE = re.compile(
    r"^(?P<outer>[A-Za-z]):[\\/]+mnt[\\/]+(?P<drive>[A-Za-z])(?:[\\/]+(?P<rest>.*))?$"
)
WSL_ENVIRONMENT_SELECTION_OWNER = (
    "/home/codexlab/work/codex-workspace/workspace/_bridge/codex_desktop_environment_selection.py"
)
WSL_CODEX_CONFIG = "/home/codexlab/.codex-app/config.toml"
WSL_ENVIRONMENT_SELECTION_STATE = (
    "/home/codexlab/.codex-app/state/desktop-environment-selection.json"
)
HOST_ENVIRONMENT_SELECTION_STATE = Path.home() / ".codex" / "state" / "desktop-environment-selection.json"
VOLATILE_NODE_REPL_ENV_KEYS = frozenset({
    "BROWSER_USE_CODEX_APP_VERSION",
    "CODEX_CLI_PATH",
    "NODE_REPL_NODE_MODULE_DIRS",
    "NODE_REPL_NODE_PATH",
    "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
    "SKY_CUA_NATIVE_PIPE_DIRECTORY",
})


def baseline_host_path(value: str | Path) -> Path:
    """Resolve baseline paths on the platform performing the repair."""

    return host_accessible_path(value)


def wsl_runtime_owns_global_config(baseline: dict) -> bool:
    return baseline.get("configuration_authority") == "wsl_active"


def desktop_host_config_path(baseline: dict, global_config: Path) -> Path:
    """Keep Desktop environment selection anchored to the Windows host config."""

    declared = str(baseline.get("desktop_host_config") or "").strip()
    if declared:
        return baseline_host_path(declared)
    if wsl_runtime_owns_global_config(baseline):
        return windows_user_root() / ".codex" / "config.toml"
    return global_config


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
    global_state_path = baseline_host_path(
        baseline.get("global_state", Path.home() / ".codex" / ".codex-global-state.json")
    )
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


def windows_resume_cwd_candidate(value: str) -> str:
    """Translate only WSL mount spellings that can be used by Windows resume."""

    text = str(value or "").strip()
    mount = WINDOWS_MOUNT_PATH_RE.fullmatch(text.replace("\\", "/"))
    if mount:
        drive = mount.group("drive").upper()
        rest = str(mount.group("rest") or "").replace("/", "\\").strip("\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    malformed = MALFORMED_WINDOWS_MOUNT_PATH_RE.fullmatch(text)
    if malformed:
        drive = malformed.group("drive").upper()
        rest = str(malformed.group("rest") or "").replace("/", "\\").strip("\\")
        return f"{drive}:\\{rest}" if rest else f"{drive}:\\"
    return ""


def _sqlite_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _online_backup_thread_state(path: Path, candidates: list[dict[str, str]]) -> dict:
    """Create an external, integrity-checked SQLite backup before a state write."""

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = WINDOWS_SESSION_BACKUP_ROOT / stamp / "resume-cwd"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / path.name
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(str(backup_path), timeout=30)
    try:
        source.backup(target)
        integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        target.close()
        source.close()
    if integrity != "ok":
        raise sqlite3.DatabaseError(f"online_backup_integrity_failed:{integrity}")
    digest = _sqlite_sha256(backup_path)
    manifest = {
        "schema": "backup_router.manifest.v2",
        "created_at": datetime.now().astimezone().isoformat(),
        "remark": "resume-cwd",
        "purpose": "Online SQLite backup before exact Windows resume cwd repair",
        "category": "codex-session",
        "trigger": "codex-session-repair",
        "restore": "Replace state_5.sqlite only while Codex is stopped, after verifying hash and SQLite integrity.",
        "items": [{
            "source_path": str(path),
            "backup_path": str(backup_path),
            "backup_mode": "sqlite_online_backup",
            "source_sha256": "",
            "backup_sha256": digest,
            "hash_match": True,
            "size_bytes": backup_path.stat().st_size,
            "sqlite_integrity": integrity,
            "changed_thread_ids": [item["id"] for item in candidates],
        }],
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "backup_dir": str(backup_dir),
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path),
        "backup_sha256": digest,
        "sqlite_integrity": integrity,
    }


def prepare_wsl_state_snapshot(*, source: Path | None = None, target: Path | None = None) -> dict:
    source = source or (Path.home() / ".codex" / "state_5.sqlite")
    target = target or WINDOWS_WSL_STATE_SNAPSHOT
    if not source.is_file():
        return {"ok": False, "status": "source_missing", "source": str(source)}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30)
        target_connection = sqlite3.connect(str(temporary), timeout=30)
        source_connection.backup(target_connection)
        integrity = str(target_connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            return {"ok": False, "status": "snapshot_integrity_failed", "integrity": integrity}
        target_connection.close()
        target_connection = None
        source_connection.close()
        source_connection = None
        os.replace(temporary, target)
    except (OSError, sqlite3.Error) as exc:
        return {
            "ok": False,
            "status": "snapshot_failed",
            "source": str(source),
            "path": str(target),
            "error": repr(exc),
        }
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
        temporary.unlink(missing_ok=True)
    return {
        "ok": True,
        "status": "prepared",
        "source": str(source),
        "path": str(target),
        "sha256": _sqlite_sha256(target),
        "integrity": integrity,
    }


def _path_from_rollout_value(value: str) -> Path:
    text = str(value or "").strip()
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return Path(text)


def _normalize_rollout_resume_context(path: Path) -> tuple[list[str], str, str]:
    """Rewrite only the latest machine context records, preserving history bytes."""

    if not path.is_file():
        return [], "", ""
    before = _sqlite_sha256(path)
    rows = path.read_text(encoding="utf-8").splitlines(keepends=True)
    last_settings = last_turn = last_world = None
    for index, line in enumerate(rows):
        payload = json.loads(line).get("payload")
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("thread_settings"), dict) and isinstance(
            payload["thread_settings"].get("cwd"), str
        ):
            last_settings = index
        if json.loads(line).get("type") == "turn_context" and isinstance(payload.get("cwd"), str):
            last_turn = index
        if json.loads(line).get("type") == "world_state":
            last_world = index

    changed: list[str] = []

    def translate(value: object) -> str:
        old = str(value or "")
        candidate = windows_resume_cwd_candidate(old)
        return candidate if candidate and Path(candidate).exists() else old

    for index in (last_settings, last_turn, last_world):
        if index is None:
            continue
        record = json.loads(rows[index])
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if index == last_settings:
            settings = payload.get("thread_settings")
            if isinstance(settings, dict):
                old = settings.get("cwd")
                new = translate(old)
                if new != old:
                    settings["cwd"] = new
                    changed.append("payload.thread_settings.cwd")
        if index == last_turn:
            old = payload.get("cwd")
            new = translate(old)
            if new != old:
                payload["cwd"] = new
                changed.append("payload.cwd")
            roots = payload.get("workspace_roots")
            if isinstance(roots, list):
                for root_index, root in enumerate(roots):
                    new_root = translate(root)
                    if new_root != root:
                        roots[root_index] = new_root
                        changed.append(f"payload.workspace_roots[{root_index}]")
        if index == last_world:
            try:
                local = payload["state"]["environments"]["environments"]["local"]
            except (KeyError, TypeError):
                local = None
            if isinstance(local, dict):
                old = local.get("cwd")
                new = translate(old)
                if new != old:
                    local["cwd"] = new
                    changed.append("payload.state.environments.environments.local.cwd")
        if index in (last_settings, last_turn, last_world):
            rows[index] = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"

    if not changed:
        return [], before, before
    if _sqlite_sha256(path) != before:
        raise OSError("rollout_changed_before_rewrite")
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".repair-", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text("".join(rows), encoding="utf-8", newline="")
        with temp_path.open(encoding="utf-8") as handle:
            count = sum(1 for line in handle if json.loads(line))
        if count != len(rows):
            raise ValueError("rollout_row_count_changed")
        if _sqlite_sha256(path) != before:
            raise OSError("rollout_changed_before_atomic_replace")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return changed, before, _sqlite_sha256(path)


def _normalize_global_resume_hints(path: Path, thread_ids: set[str]) -> tuple[list[str], str, str]:
    if not path.is_file():
        return [], "", ""
    before = _sqlite_sha256(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    changed: list[str] = []
    for table in ("thread-workspace-root-hints", "thread-projectless-output-directories"):
        values = data.get(table)
        if not isinstance(values, dict):
            continue
        for thread_id in thread_ids:
            old = values.get(thread_id)
            candidate = windows_resume_cwd_candidate(old) if isinstance(old, str) else ""
            if candidate and Path(candidate).exists() and candidate != old:
                values[thread_id] = candidate
                changed.append(f"{table}.{thread_id}")
    if not changed:
        return [], before, before
    if _sqlite_sha256(path) != before:
        raise OSError("global_state_changed_before_rewrite")
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".repair-", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        json.loads(temp_path.read_text(encoding="utf-8"))
        if _sqlite_sha256(path) != before:
            raise OSError("global_state_changed_before_atomic_replace")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return changed, before, _sqlite_sha256(path)


def _backup_resume_context_files(paths: list[Path], thread_ids: list[str]) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = WINDOWS_SESSION_BACKUP_ROOT / stamp / "resume-context"
    items = []
    for index, source in enumerate(dict.fromkeys(paths)):
        if not source.is_file():
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"{index:02d}-{source.name}"
        shutil.copy2(source, target)
        digest = _sqlite_sha256(source)
        backup_digest = _sqlite_sha256(target)
        if digest != backup_digest:
            raise OSError(f"resume_context_backup_hash_mismatch:{source}")
        items.append({
            "source_path": str(source),
            "backup_path": str(target),
            "backup_mode": "external_copy",
            "source_sha256": digest,
            "backup_sha256": backup_digest,
            "hash_match": True,
            "size_bytes": target.stat().st_size,
        })
    manifest_path = backup_dir / "manifest.json"
    manifest = {
        "schema": "backup_router.manifest.v2",
        "created_at": datetime.now().astimezone().isoformat(),
        "remark": "resume-context",
        "purpose": "Back up current rollout and Desktop hints before Windows resume normalization",
        "category": "codex-session",
        "trigger": "codex-session-repair",
        "restore": "Restore only while Codex is stopped after validating hashes.",
        "thread_ids": thread_ids,
        "items": items,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "backup_dir": str(backup_dir),
        "manifest_path": str(manifest_path),
        "item_count": len(items),
    }


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


def _desired_environment_from_host_state(path: Path, fallback: bool) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    value = payload.get("desired_value")
    if not isinstance(value, bool):
        value = payload.get("last_synced_value")
    return value if isinstance(value, bool) else fallback


def _persist_environment_fallback_state(*, desired_value: bool, reason: str, dry_run: bool) -> dict:
    path = HOST_ENVIRONMENT_SELECTION_STATE
    payload = {
        "schema": "codex-desktop-environment-selection.v1",
        "last_synced_value": desired_value,
        "desired_value": desired_value,
        "effective_value": False,
        "fallback_pending": True,
        "fallback_reason": reason,
        "selection_source": "host_runtime_fallback",
        "synchronized_at": datetime.now().astimezone().isoformat(),
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.is_symlink():
        return {"ok": False, "status": "symlink_rejected", "path": str(path)}
    try:
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
    except (OSError, UnicodeError) as exc:
        return {"ok": False, "status": "state_unreadable", "path": str(path), "error": repr(exc)}
    try:
        current_payload = json.loads(current) if current else {}
    except json.JSONDecodeError:
        current_payload = {}
    changed = not all(
        current_payload.get(key) == value
        for key, value in payload.items()
        if key != "synchronized_at"
    )
    if dry_run or not changed:
        return {
            "ok": True,
            "status": "would_preserve" if dry_run and changed else "already_preserved",
            "changed": changed,
            "path": str(path),
        }
    backup = None
    if path.is_file():
        try:
            backup = create_backup(
                [str(path)],
                remark="codex-desktop-environment-fallback",
                purpose="Preserve the user's desired WSL selection during a native compatibility launch",
                category="wsl-desktop-runtime",
            )
        except Exception as exc:
            backup = {"ok": False, "error": repr(exc)}
        if not backup.get("ok"):
            return {"ok": False, "status": "backup_failed", "path": str(path), "backup": backup}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        return {"ok": False, "status": "write_failed", "path": str(path), "error": repr(exc), "backup": backup}
    finally:
        temporary.unlink(missing_ok=True)
    return {"ok": True, "status": "preserved", "changed": True, "path": str(path), "backup": backup}


def ensure_desktop_environment_selection(*, host_config: Path, dry_run: bool) -> dict:
    """Run the cross-platform selection owner before mode-specific repair."""
    host_value = _desktop_wsl_value_from_config(host_config)
    desired_value = _desired_environment_from_host_state(
        HOST_ENVIRONMENT_SELECTION_STATE,
        host_value,
    )
    if os.name != "nt":
        return {
            "ok": True,
            "status": "not_windows_host",
            "changed": False,
            "ready": True,
            "selected_value": host_value,
            "desired_value": desired_value,
            "effective_value": host_value,
        }
    if not dry_run and codex_desktop_running():
        return {
            "ok": True,
            "status": "deferred_desktop_running",
            "changed": False,
            "ready": False,
            "deferred": True,
            "selected_value": host_value,
            "desired_value": desired_value,
            "effective_value": host_value,
        }
    wsl = shutil.which("wsl.exe")
    if not wsl:
        fallback_state = _persist_environment_fallback_state(
            desired_value=desired_value,
            reason="wsl_executable_missing",
            dry_run=dry_run,
        )
        return {
            "ok": False,
            "status": "wsl_executable_missing",
            "changed": False,
            "ready": False,
            "selected_value": host_value,
            "desired_value": desired_value,
            "effective_value": False,
            "fallback_preserved": desired_value,
            "fallback_reason": "wsl_executable_missing",
            "fallback_state": fallback_state,
        }
    host_path = windows_path_to_wsl_path(str(host_config))
    host_state_path = windows_path_to_wsl_path(str(HOST_ENVIRONMENT_SELECTION_STATE))
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
        "--host-state-path",
        host_state_path,
    ]
    try:
        process = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            creationflags=RUNTIME_REPAIR_NO_WINDOW_FLAG,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        fallback_state = _persist_environment_fallback_state(
            desired_value=desired_value,
            reason="owner_invocation_failed",
            dry_run=dry_run,
        )
        return {
            "ok": False,
            "status": "owner_invocation_failed",
            "changed": False,
            "ready": False,
            "selected_value": host_value,
            "desired_value": desired_value,
            "effective_value": False,
            "fallback_preserved": desired_value,
            "fallback_reason": "owner_invocation_failed",
            "fallback_state": fallback_state,
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
    if owner_ok:
        desired_value = selected
    effective_value = selected if owner_ok else False
    fallback_state = None
    if not owner_ok:
        fallback_state = _persist_environment_fallback_state(
            desired_value=desired_value,
            reason="environment_owner_failed",
            dry_run=dry_run,
        )
    return {
        "ok": owner_ok,
        "status": str(payload.get("status") or ("owner_failed" if not owner_ok else "ready")),
        "changed": bool(payload.get("changed")),
        "ready": owner_ok,
        "selected_value": selected,
        "desired_value": desired_value,
        "effective_value": effective_value,
        "fallback_preserved": bool(not owner_ok and desired_value),
        "fallback_reason": "" if owner_ok else "environment_owner_failed",
        "fallback_state": fallback_state,
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


def load_wsl_workspace_thread_index() -> dict:
    """Read the WSL-owned task index through its runtime owner."""
    wsl = shutil.which("wsl.exe")
    if not wsl:
        return {
            "ok": False,
            "status": "wsl_executable_missing",
            "thread_ids": [],
            "thread_cwds": {},
            "thread_count": 0,
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
        "thread-index",
    ]
    try:
        process = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
            creationflags=RUNTIME_REPAIR_NO_WINDOW_FLAG,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "status": "wsl_thread_index_owner_failed",
            "error": str(exc),
            "thread_ids": [],
            "thread_cwds": {},
            "thread_count": 0,
        }
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        payload = {}
    if process.returncode != 0 or not payload.get("ok"):
        return {
            "ok": False,
            "status": str(payload.get("status") or "wsl_thread_index_owner_failed"),
            "error": (process.stderr or "")[-1000:],
            "thread_ids": [],
            "thread_cwds": {},
            "thread_count": 0,
        }
    thread_ids = payload.get("thread_ids")
    thread_cwds = payload.get("thread_cwds")
    if not isinstance(thread_ids, list) or not isinstance(thread_cwds, dict):
        return {
            "ok": False,
            "status": "wsl_thread_index_owner_invalid_result",
            "thread_ids": [],
            "thread_cwds": {},
            "thread_count": 0,
        }
    normalized_ids = [str(item) for item in thread_ids if isinstance(item, str) and item]
    normalized_cwds = {
        str(key): str(value)
        for key, value in thread_cwds.items()
        if isinstance(key, str) and isinstance(value, str)
    }
    return {
        "ok": True,
        "status": "ready",
        "source": "wsl_runtime_owner",
        "thread_ids": normalized_ids,
        "thread_cwds": normalized_cwds,
        "thread_count": len(normalized_ids),
        "missing_session_count": int(payload.get("missing_session_count") or 0),
    }


def ensure_windows_resume_cwd_projection(
    *,
    enabled: bool,
    dry_run: bool,
    thread_state_path: Path | None = None,
) -> dict:
    """Repair provably mapped WSL mount cwd values before Windows fallback starts."""

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
    if thread_state_path is None:
        thread_state_path = Path.home() / ".codex" / "state_5.sqlite"
    if not thread_state_path.is_file():
        return {
            "ok": False,
            "status": "thread_state_missing",
            "enabled": True,
            "changed": False,
            "ready": False,
            "path": str(thread_state_path),
        }
    try:
        connection = sqlite3.connect(
            f"file:{thread_state_path.as_posix()}?mode=ro",
            uri=True,
            timeout=5,
        )
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")}
        required = {"id", "source", "cwd", "archived"}
        if not required.issubset(columns):
            connection.close()
            return {
                "ok": False,
                "status": "thread_state_schema_incomplete",
                "enabled": True,
                "changed": False,
                "ready": False,
                "missing_columns": sorted(required - columns),
            }
        rollout_expression = "rollout_path" if "rollout_path" in columns else "''"
        rows = connection.execute(
            f"SELECT id, source, cwd, {rollout_expression} FROM threads WHERE archived = 0"
        ).fetchall()
        connection.close()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "status": "thread_state_unreadable",
            "enabled": True,
            "changed": False,
            "ready": False,
            "error": str(exc),
        }

    candidates: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    for thread_id, source, cwd, rollout_path in rows:
        if not _top_level_thread_source(source):
            continue
        old_cwd = str(cwd or "")
        new_cwd = windows_resume_cwd_candidate(old_cwd)
        if not new_cwd or normalize_path_text(new_cwd) == normalize_path_text(old_cwd):
            continue
        row = {
            "id": str(thread_id),
            "old_cwd": old_cwd,
            "new_cwd": new_cwd,
            "rollout_path": str(rollout_path or ""),
        }
        if Path(new_cwd).is_dir():
            candidates.append(row)
        else:
            rejected.append({**row, "reason": "mapped_directory_missing"})

    result = {
        "ok": True,
        "status": "already_current",
        "enabled": True,
        "changed": bool(candidates),
        "ready": True,
        "path": str(thread_state_path),
        "candidate_count": len(candidates),
        "candidates": candidates[:20],
        "rejected_count": len(rejected),
        "rejected": rejected[:20],
    }
    if not candidates:
        return result
    if dry_run:
        return {**result, "status": "would_apply", "ready": False, "dry_run": True}

    try:
        backup = _online_backup_thread_state(thread_state_path, candidates)
        global_state_path = thread_state_path.parent / ".codex-global-state.json"
        rollout_paths = list(dict.fromkeys([
            _path_from_rollout_value(item["rollout_path"])
            for item in candidates
            if item.get("rollout_path")
        ]))
        context_backup = _backup_resume_context_files(
            [*rollout_paths, global_state_path],
            [item["id"] for item in candidates],
        )
        connection = sqlite3.connect(str(thread_state_path), timeout=30)
        try:
            connection.execute("BEGIN IMMEDIATE")
            changed_rows = 0
            for item in candidates:
                cursor = connection.execute(
                    "UPDATE threads SET cwd = ? WHERE id = ? AND cwd = ?",
                    (item["new_cwd"], item["id"], item["old_cwd"]),
                )
                changed_rows += int(cursor.rowcount)
            if changed_rows != len(candidates):
                connection.rollback()
                return {
                    **result,
                    "ok": False,
                    "status": "concurrent_thread_state_change",
                    "ready": False,
                    "changed": False,
                    "expected_rows": len(candidates),
                    "changed_rows": changed_rows,
                    "backup": backup,
                }
            connection.commit()
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as exc:
        return {
            **result,
            "ok": False,
            "status": "apply_failed",
            "ready": False,
            "changed": False,
            "error": str(exc),
        }
    if integrity != "ok":
        return {
            **result,
            "ok": False,
            "status": "post_apply_integrity_failed",
            "ready": False,
            "changed": True,
            "sqlite_integrity": integrity,
            "backup": backup,
        }
    context_changes = []
    try:
        for rollout_path in rollout_paths:
            fields, before_hash, after_hash = _normalize_rollout_resume_context(rollout_path)
            if fields:
                context_changes.append({
                    "path": str(rollout_path),
                    "fields": fields,
                    "before_sha256": before_hash,
                    "after_sha256": after_hash,
                })
        fields, before_hash, after_hash = _normalize_global_resume_hints(
            global_state_path,
            {item["id"] for item in candidates},
        )
        if fields:
            context_changes.append({
                "path": str(global_state_path),
                "fields": fields,
                "before_sha256": before_hash,
                "after_sha256": after_hash,
            })
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            **result,
            "ok": False,
            "status": "context_projection_failed_after_cwd_apply",
            "ready": False,
            "changed": True,
            "changed_row_count": len(candidates),
            "sqlite_integrity": integrity,
            "backup": backup,
            "context_backup": context_backup,
            "context_changes": context_changes,
            "error": str(exc),
        }
    return {
        **result,
        "status": "applied",
        "ready": True,
        "changed": True,
        "changed_row_count": len(candidates),
        "sqlite_integrity": integrity,
        "backup": backup,
        "context_backup": context_backup,
        "context_changes": context_changes,
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
    thread_index = (
        load_wsl_workspace_thread_index()
        if thread_state_path is None
        else load_top_level_thread_ids(thread_state_path)
    )
    if not thread_index.get("ok"):
        return {
            "ok": False,
            "status": str(thread_index.get("status") or "wsl_thread_index_unavailable"),
            "enabled": True,
            "changed": False,
            "ready": False,
            "path": str(global_state_path),
            "task_index": thread_index,
        }
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
        "task_index_source": str(thread_index.get("source") or "explicit_state_db"),
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
    state_snapshot = (
        prepare_wsl_state_snapshot()
        if not dry_run
        else {"ok": True, "status": "dry_run", "path": str(WINDOWS_WSL_STATE_SNAPSHOT)}
    )
    if not state_snapshot.get("ok"):
        return {
            "ok": False,
            "status": "state_snapshot_failed",
            "enabled": True,
            "changed": False,
            "ready": False,
            "state_snapshot": state_snapshot,
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
        "--windows-state-snapshot",
        windows_path_to_wsl_path(str(state_snapshot["path"])),
    ]
    try:
        process = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
            creationflags=RUNTIME_REPAIR_NO_WINDOW_FLAG,
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
        and session_projection.get("status")
        in {"projected", "projected_with_conflicts", "source_missing_optional"}
        and session_projection.get("source_count")
        == int(session_projection.get("projected_count") or 0)
        + int(session_projection.get("conflict_count") or 0)
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
        "state_snapshot": state_snapshot,
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


def refresh_runtime_artifacts() -> list[str]:
    """Refresh host runtime artifacts without mutating declarative baseline state."""
    changed: list[str] = []
    runtime = discover_latest_node_repl_runtime()
    if runtime:
        if install_stable_node_repl_entry(runtime):
            changed.append("stable_node_repl_entry_updated")
    return changed


def resolved_node_repl_env(value: object) -> dict[str, str]:
    """Overlay current host pointers onto stable node_repl environment policy."""
    source = value if isinstance(value, dict) else {}
    env = {
        str(key): str(item)
        for key, item in source.items()
        if str(key) not in VOLATILE_NODE_REPL_ENV_KEYS
    }
    for key in VOLATILE_NODE_REPL_ENV_KEYS:
        current = str(os.environ.get(key) or "").strip()
        if current:
            env[key] = current
    latest_cli = discover_latest_codex_cli()
    if latest_cli:
        env["CODEX_CLI_PATH"] = latest_cli
    runtime = discover_latest_node_repl_runtime()
    if runtime:
        env["NODE_REPL_NODE_PATH"] = runtime["node_path"]
        env["NODE_REPL_NODE_MODULE_DIRS"] = runtime["node_modules"]
    return env


def codex_desktop_running() -> bool:
    """Conservatively detect the Desktop family before touching live state."""

    try:
        if query_desktop_host_processes(main_only=True):
            return True
        return bool(query_desktop_host_processes(main_only=False))
    except Exception:
        # An unavailable CIM/command-line view is not proof that Desktop is
        # stopped.  Fail closed so a transient observation cannot authorize a
        # cross-runtime config or SQLite write against a live process.
        return True


def backup_files(
    paths: list[Path],
    *,
    labels: list[str] | None = None,
    changed: list[str] | None = None,
) -> Path:
    candidates = [str(path) for path in paths if path.exists()]
    if BASELINE_PATH.exists():
        candidates.append(str(BASELINE_PATH))
    receipt = create_backup(
        candidates,
        purpose="codex-state-repair:" + ",".join(changed or []),
        category="codex-config",
        trigger="codex_state_repair",
    )
    if not receipt.get("ok"):
        raise RuntimeError(f"state_repair_backup_failed:{receipt.get('reason', 'unknown')}")
    manifests = receipt.get("manifest_paths") or []
    return Path(str(manifests[0])).parent


def resolve_codex_cli(baseline: dict | None = None) -> str | None:
    candidates = []
    env_value = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env_value:
        candidates.append(env_value)
    latest = discover_latest_codex_cli()
    if latest:
        candidates.append(latest)
    candidates.extend(codex_cli_candidates_from_baseline(baseline))
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
            resolved["env"] = resolved_node_repl_env(resolved.get("env"))
        specs[name] = resolved
    return specs


def repair(
    dry_run: bool,
    *,
    runtime_validation: bool = True,
    reconcile_desktop_environment: bool = True,
    reconcile_mcp_registration: bool = False,
    reconcile_plugin_registration: bool = False,
) -> dict:
    baseline_source = BASELINE_PATH.read_text(encoding="utf-8")
    baseline = json.loads(baseline_source)
    global_config = baseline_host_path(baseline["global_config"])
    wsl_runtime_owned = wsl_runtime_owns_global_config(baseline)
    desktop_host_config = desktop_host_config_path(baseline, global_config)
    project_config_required = bool(baseline.get("project_config_required", True))
    environment_selection = (
        ensure_desktop_environment_selection(host_config=desktop_host_config, dry_run=dry_run)
        if reconcile_desktop_environment
        else {
            "ok": True,
            "status": "skipped_not_environment_owner",
            "changed": False,
            "ready": True,
            "selected_value": _desktop_wsl_value_from_config(desktop_host_config),
        }
    )
    explicit_registration_only = wsl_runtime_owned and (
        reconcile_mcp_registration or reconcile_plugin_registration
    )
    runtime_artifact_changes = [] if explicit_registration_only else refresh_runtime_artifacts()
    project_config = baseline_host_path(baseline["project_config"]) if project_config_required else None
    global_state_path = baseline_host_path(
        baseline.get("global_state", Path.home() / ".codex" / ".codex-global-state.json")
    )
    backup_dir: Path | None = None

    changed: list[str] = list(runtime_artifact_changes)
    global_text, global_bom = read_text_no_bom(global_config)
    if global_bom:
        changed.append("global_config_remove_bom")
    global_text, project_tables_normalized = normalize_duplicate_project_tables(global_text)
    if project_tables_normalized:
        changed.append("global_config_normalize_duplicate_project_tables")

    selected_environment = environment_selection.get("effective_value")
    if not isinstance(selected_environment, bool):
        selected_environment = environment_selection.get("selected_value")
    desktop_wsl_enabled = (
        selected_environment
        if isinstance(selected_environment, bool)
        else _desktop_wsl_value_from_config(desktop_host_config)
    )
    managed_mcp = expected_mcp_specs(baseline, desktop_wsl_enabled=desktop_wsl_enabled)
    decommissioned_mcp = baseline.get("decommissioned_mcp") or {}
    if not isinstance(decommissioned_mcp, dict):
        decommissioned_mcp = {}
    reconcile_mcp_tables = not wsl_runtime_owned or reconcile_mcp_registration
    if reconcile_mcp_tables:
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

    if reconcile_mcp_tables:
        for name, spec in managed_mcp.items():
            global_text, did = ensure_mcp_server(global_text, name, spec)
            if did:
                changed.append(f"global_mcp_add_{name}")

    if not wsl_runtime_owned or reconcile_plugin_registration:
        for plugin in baseline["expected_plugins"]:
            global_text, did = ensure_plugin(global_text, plugin)
            if did:
                changed.append(f"global_plugin_enable_{plugin}")

        for name, spec in (baseline.get("expected_marketplaces") or {}).items():
            global_text, did = ensure_marketplace(global_text, name, spec)
            if did:
                changed.append(f"global_marketplace_set_{name}")

    if not explicit_registration_only:
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

    project_text = ""
    if project_config_required and project_config is not None:
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

    global_state_data, global_state_changed = ({}, []) if explicit_registration_only else repair_global_state(baseline)
    changed.extend(global_state_changed)

    global_state_text = (
        json.dumps(global_state_data, ensure_ascii=False, separators=(",", ":"))
        if global_state_data
        else None
    )
    write_decisions = {
        "baseline": False,
        "global_config": utf8_write_required(global_config, global_text),
        "project_config": bool(
            project_config_required
            and project_config is not None
            and utf8_write_required(project_config, project_text)
        ),
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
            if write_decisions[name] and path is not None:
                backup_paths.append(path)
                backup_labels.append(label)
        if written:
            backup_dir = backup_files(
                backup_paths,
                labels=backup_labels,
                changed=changed,
            )
        if write_decisions["global_config"]:
            global_config.write_text(global_text, encoding="utf-8", newline="\n")
        if write_decisions["project_config"] and project_config is not None:
            project_config.write_text(project_text, encoding="utf-8", newline="\n")
        if write_decisions["global_state"] and global_state_text is not None:
            global_state_path.write_text(
                global_state_text,
                encoding="utf-8",
                newline="\n",
            )
        validate_toml(global_config)
        if project_config_required and project_config is not None:
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
        "global_config_owner": (
            "explicit_runtime_registration"
            if wsl_runtime_owned and reconcile_mcp_registration and reconcile_plugin_registration
            else (
                "explicit_mcp_registration"
                if wsl_runtime_owned and reconcile_mcp_registration
                else (
                    "explicit_plugin_registration"
                    if wsl_runtime_owned and reconcile_plugin_registration
                    else ("wsl_runtime" if wsl_runtime_owned else "startup_baseline")
                )
            )
        ),
        "mcp_registration_reconciled": reconcile_mcp_registration,
        "plugin_registration_reconciled": reconcile_plugin_registration,
        "desktop_host_config": str(desktop_host_config),
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
    parser.add_argument(
        "--reconcile-mcp-registration",
        action="store_true",
        help=(
            "Explicitly reconcile only MCP registration tables, including WSL-active configs: "
            "remove Hub-managed/decommissioned profiles and restore baseline desktop-native profiles."
        ),
    )
    parser.add_argument(
        "--reconcile-plugin-registration",
        action="store_true",
        help=(
            "Explicitly enable only baseline-declared plugins in a WSL-active config; "
            "does not alter MCP registration, marketplaces, baseline, project config, or global state."
        ),
    )
    args = parser.parse_args()
    try:
        result = repair(
            args.dry_run,
            reconcile_mcp_registration=args.reconcile_mcp_registration,
            reconcile_plugin_registration=args.reconcile_plugin_registration,
        )
    except Exception as exc:
        result = {"ok": False, "error": repr(exc)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
