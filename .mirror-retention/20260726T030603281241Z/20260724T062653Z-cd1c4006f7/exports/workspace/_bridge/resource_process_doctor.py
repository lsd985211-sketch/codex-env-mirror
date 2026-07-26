#!/usr/bin/env python3
"""Resource/MCP process doctor for this workspace.

This module observes process fan-out caused by MCP/resource tools and proposes
dry-run cleanup boundaries. Cleanup is opt-in only: without --apply it never
kills processes, edits config, starts services, sends messages, or changes
bridge state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "_bridge") not in sys.path:
    sys.path.insert(0, str(ROOT / "_bridge"))

from bounded_output import governed_cli_payload  # noqa: E402
from mcp_execution_priority import HUB_MANAGED_MCP_NAMES  # noqa: E402
from mcp_session_doctor import thread_freshness_anchor  # noqa: E402
from system_membership import retirement_tombstones  # noqa: E402
from shared.json_cli import now_iso  # noqa: E402
from shared.windows_powershell import powershell_encoded_command, resolve_powershell_executable  # noqa: E402
import resource_process_lifecycle  # noqa: E402
import resource_process_observations  # noqa: E402
import resource_process_reporting  # noqa: E402
import resource_startup_sources  # noqa: E402

NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

MCP_SESSION_OBSERVATION_LOG = ROOT / "_bridge" / "mobile_openclaw_bridge" / "runtime" / "mcp_session_observations.jsonl"

DUAL_HOST_STDIO_GROUPS = {
    "chrome-devtools",
    "playwright",
    "markitdown-mcp",
    "myskills-mcp",
    "gui_automation_mcp",
    "codegraph_mcp",
    "custom_slash_commands_mcp",
    "microsoftdocs_mcp",
    "context7_mcp",
    "next_ai_drawio_mcp",
    "desktop_weixin_mcp",
}

HUB_MANAGED_PROCESS_GROUPS = {
    "codegraph": "codegraph_mcp",
    "context7": "context7_mcp",
    "custom-slash-commands": "custom_slash_commands_mcp",
    "local-pmb-memory": "local_pmb_proxy",
    "markitdown": "markitdown-mcp",
    "microsoftdocs": "microsoftdocs_mcp",
    "openai-docs": "openai_docs_mcp",
    "myskills": "myskills-mcp",
    "sqlite-bridge-ro": "sqlite_bridge_ro_mcp",
    "sqlite-scratch": "sqlite_scratch_mcp",
}

SESSION_OWNED_STDIO_GROUPS = {
    "chrome-devtools",
    "playwright",
    "markitdown-mcp",
    "myskills-mcp",
    "gui_automation_mcp",
    "codegraph_mcp",
    "custom_slash_commands_mcp",
    "microsoftdocs_mcp",
    "openai_docs_mcp",
    "context7_mcp",
    "next_ai_drawio_mcp",
    "desktop_weixin_mcp",
    "filesystem_mcp",
    "filesystem_admin_mcp",
    "sqlite_scratch_mcp",
    "sqlite_bridge_ro_mcp",
    "local_pmb_proxy",
    "node_repl",
}
MCP_PRESSURE_EXCLUDED_GROUPS = {"mcp_lazy_stdio_proxy"}

CODEX_HOST_MARKERS = {
    "desktop": "\\app\\resources\\codex.exe",
    "bridge": "app-server --listen ws://127.0.0.1:18791",
}

MCP_ROOT_WARN_BUDGET = 12
MCP_ROOT_RISK_BUDGET = 24
MCP_WORKING_SET_WARN_MB = 300.0
MCP_WORKING_SET_RISK_MB = 600.0
FANOUT_MIN_AGE_MINUTES = 2.0


def mcp_budget_state(root_count: int, working_set_mb: float) -> str:
    """Classify actionable multiplication separately from memory-only pressure."""
    if root_count >= MCP_ROOT_RISK_BUDGET:
        return "risk"
    if root_count >= MCP_ROOT_WARN_BUDGET and working_set_mb >= MCP_WORKING_SET_RISK_MB:
        return "risk"
    if root_count >= MCP_ROOT_WARN_BUDGET or working_set_mb >= MCP_WORKING_SET_WARN_MB:
        return "advisory"
    return "ok"


@dataclass(frozen=True)
class ProcessPattern:
    group: str
    patterns: tuple[str, ...]
    expected_max: int
    category: str
    cleanup_policy: str
    protected: bool = False
    decommissioned_member: bool = False
    member: str = ""


def decommissioned_process_patterns() -> tuple[ProcessPattern, ...]:
    patterns: list[ProcessPattern] = []
    for item in retirement_tombstones():
        member = str(item.get("member") or "").strip()
        if not member:
            continue
        aliases = tuple(dict.fromkeys((member.casefold(), member.replace("-", "_").casefold())))
        patterns.append(
            ProcessPattern(
                f"decommissioned:{member}",
                aliases,
                0,
                "decommissioned_member",
                "stop_and_remove_retired_member_process",
                False,
                True,
                member,
            )
        )
    return tuple(patterns)


PROCESS_PATTERNS: tuple[ProcessPattern, ...] = decommissioned_process_patterns() + (
    ProcessPattern(
        "mcp_lazy_stdio_proxy",
        (
            "mcp_lazy_stdio_proxy.py",
            "mcp_profile_launcher.py gui",
            "mcp_profile_launcher.py cdev",
            "mcp_profile_launcher.py pw",
            "mcp_profile_launcher.py drawio",
        ),
        64,
        "mcp_lazy_proxy",
        "session_owned_proxy_exits_on_stdio_eof",
    ),
    ProcessPattern("chrome-devtools", ("chrome-devtools-mcp",), 1, "browser_mcp", "manual_stop_duplicate_mcp_only"),
    ProcessPattern("playwright", ("playwright-mcp", "@playwright\\mcp", "@playwright/mcp"), 1, "browser_mcp", "manual_stop_duplicate_mcp_only"),
    ProcessPattern("markitdown-mcp", ("markitdown-mcp",), 1, "document_mcp", "manual_stop_duplicate_mcp_only"),
    ProcessPattern("myskills-mcp", ("myskills-mcp.exe",), 1, "skill_mcp", "manual_stop_duplicate_mcp_only"),
    ProcessPattern("gui_automation_mcp", ("gui_automation_mcp.py",), 1, "gui_mcp", "manual_stop_duplicate_mcp_only"),
    ProcessPattern("desktop_weixin_mcp", ("desktop_weixin_mcp_server.py",), 1, "desktop_chat_mcp", "manual_stop_idle_mcp_only"),
    ProcessPattern("local_pmb_daemon", ("pmb.cli daemon run", "pmb.exe daemon run", "pmb daemon run"), 1, "memory_daemon", "controlled_restart_duplicate_or_stale_memory_daemon"),
    ProcessPattern("local_pmb_proxy", ("pmb.exe mcp proxy", "pmb mcp proxy"), 4, "memory_mcp_proxy", "manual_stop_idle_proxy_only"),
    ProcessPattern(
        "codegraph_mcp",
        (
            "codegraph.cmd serve --mcp",
            "codegraph serve --mcp",
            "codegraph.js serve --mcp",
            "codegraph_fresh_mcp_server.py",
        ),
        1,
        "codegraph_mcp",
        "manual_stop_duplicate_mcp_only",
    ),
    ProcessPattern("filesystem_admin_mcp", ("server-filesystem",), 2, "filesystem_mcp", "manual_stop_idle_mcp_only"),
    ProcessPattern("filesystem_mcp", ("@modelcontextprotocol/server-filesystem", "server-filesystem"), 4, "filesystem_mcp", "manual_stop_idle_mcp_only"),
    ProcessPattern("custom_slash_commands_mcp", ("custom_slash_commands_mcp.py",), 1, "slash_command_mcp", "manual_stop_idle_mcp_only"),
    ProcessPattern("sqlite_scratch_mcp", ("sqlite_mcp_server.py", "codex_scratch.sqlite"), 2, "sqlite_mcp", "manual_stop_idle_mcp_only"),
    ProcessPattern("sqlite_bridge_ro_mcp", ("sqlite_mcp_server.py", "mobile_openclaw_bridge.db"), 2, "sqlite_mcp", "manual_stop_idle_mcp_only"),
    ProcessPattern("microsoftdocs_mcp", ("microsoftdocs_stdio_proxy.js",), 2, "docs_mcp_proxy", "manual_stop_idle_mcp_only"),
    ProcessPattern("openai_docs_mcp", ("openai_docs_stdio_proxy.js",), 2, "docs_mcp_proxy", "manual_stop_idle_mcp_only"),
    ProcessPattern("context7_mcp", ("context7_stdio_proxy.js",), 2, "docs_mcp_proxy", "manual_stop_idle_mcp_only"),
    ProcessPattern(
        "next_ai_drawio_mcp",
        ("@next-ai-drawio/mcp-server", "@next-ai-drawio\\mcp-server", "next-ai-drawio-mcp"),
        1,
        "diagram_mcp",
        "manual_stop_idle_mcp_only",
    ),
    ProcessPattern("mobile_bridge_mcp_server", ("mobile_bridge_mcp_server.py",), 1, "bridge_mcp", "manual_stop_duplicate_mcp_only", True),
    ProcessPattern("bridge_server_v2", ("bridge_server_v2.py",), 1, "reasonix_bridge_mcp", "manual_reasonix_review_before_stop", True),
    ProcessPattern("reasonix_responder", ("reasonix_responder.py",), 1, "reasonix_responder", "never_auto_stop", True),
    ProcessPattern("mobile_openclaw_cli", ("mobile_openclaw_cli.py worker-loop",), 1, "bridge_worker", "never_auto_stop_active_worker", True),
    ProcessPattern("openclaw_gateway", ("openclaw.mjs gateway",), 1, "bridge_gateway", "never_auto_stop_gateway", True),
    ProcessPattern("codex_app_live_watch", ("codex_app_live_watch.js",), 1, "bridge_observer", "controlled_stop_duplicate_dashboard_observer", True),
    ProcessPattern("node_repl", ("node_repl.exe",), 4, "runtime_repl", "manual_stop_idle_repl_only"),
)


def parse_iso_datetime(value: Any) -> datetime | None:
    return resource_process_observations.parse_iso_datetime(value)


def current_turn_tool_observations(
    max_age_minutes: int = resource_process_observations.CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES,
    anchor_at: datetime | None = None,
) -> dict[str, Any]:
    return resource_process_observations.current_turn_tool_observations(
        log_path=MCP_SESSION_OBSERVATION_LOG,
        max_age_minutes=max_age_minutes,
        anchor_at=anchor_at,
    )


def matches_process_pattern(spec: ProcessPattern, haystack: str) -> bool:
    if spec.group == "codegraph_mcp":
        normalized = " ".join(haystack.split())
        if any(pattern in normalized for pattern in ("codegraph.cmd serve --mcp", "codegraph serve --mcp", "codegraph.js serve --mcp")):
            return True
        if "codegraph_fresh_mcp_server.py" not in normalized:
            return False
        return bool(
            re.search(r"(?:^|\s)(?:python(?:\.exe)?|pythonw(?:\.exe)?)\s+\S*codegraph_fresh_mcp_server\.py(?:\s|$)", normalized)
            or (
                re.search(r"mcp_launch_guard\.py\s+--profile\s+cg\b", normalized)
                and re.search(r"(?:python(?:\.exe)?|pythonw(?:\.exe)?)\s+\S*codegraph_fresh_mcp_server\.py(?:\s|$)", normalized)
            )
        )
    if spec.group == "filesystem_admin_mcp":
        normalized = " ".join(haystack.split())
        return bool(
            re.search(r"--profile\s+fs-admin\b", normalized)
            or re.search(r"mcp-server-filesystem\s+c:\\\s*$", normalized)
            or re.search(r"server-filesystem@\S+\s+c:\\\s*$", normalized)
        )
    if spec.group in {"sqlite_scratch_mcp", "sqlite_bridge_ro_mcp"}:
        return all(pattern.lower() in haystack for pattern in spec.patterns)
    return any(pattern.lower() in haystack for pattern in spec.patterns)


def is_resource_process_observer_command(command_line: Any) -> bool:
    """Exclude this doctor and its CLI wrapper from resource target matching."""
    normalized = " ".join(str(command_line or "").lower().replace("\\", "/").split())
    return (
        "_bridge/resource_process_doctor.py" in normalized
        or ("mobile_openclaw_cli.py" in normalized and " resource-process " in f" {normalized} ")
    )


def has_resource_process_observer_ancestor(
    item: dict[str, Any],
    process_by_pid: dict[Any, dict[str, Any]],
    max_depth: int = 6,
) -> bool:
    current = item
    seen: set[Any] = set()
    for _ in range(max_depth):
        parent_pid = current.get("parent_pid")
        if parent_pid is None or parent_pid in seen:
            return False
        seen.add(parent_pid)
        parent = process_by_pid.get(parent_pid)
        if not parent:
            return False
        if is_resource_process_observer_command(parent.get("command_line")):
            return True
        current = parent
    return False


def codex_host_role(command_line: Any) -> str:
    text = str(command_line or "").lower()
    if CODEX_HOST_MARKERS["bridge"] in text:
        return "bridge_app_server"
    if "openai.codex_" in text and CODEX_HOST_MARKERS["desktop"] in text:
        return "desktop_app_server"
    return "other"


def process_host_role(item: dict[str, Any], process_by_pid: dict[Any, dict[str, Any]], max_depth: int = 8) -> tuple[str, list[dict[str, Any]]]:
    """Classify the MCP root's owning host by walking its parent chain.

    Wrapper layers such as cmd.exe, pwsh.exe, python.exe, or mcp_launch_guard.py
    sit between Codex and the MCP server, so direct-parent checks misclassify
    normal Codex-owned processes as "other".
    """
    chain: list[dict[str, Any]] = []
    current = item
    seen: set[Any] = set()
    for _ in range(max_depth):
        parent_pid = current.get("parent_pid")
        if parent_pid is None or parent_pid in seen:
            break
        seen.add(parent_pid)
        parent = process_by_pid.get(parent_pid)
        if not parent:
            break
        role = codex_host_role(parent.get("command_line"))
        chain.append(
            {
                "pid": parent.get("pid"),
                "name": parent.get("name"),
                "role": role,
                "command_line": normalize_command(parent.get("command_line")),
            }
        )
        if role != "other":
            return role, chain
        current = parent
    return "other", chain


def process_host_chain_orphaned(
    item: dict[str, Any],
    process_by_pid: dict[Any, dict[str, Any]],
    max_depth: int = 8,
) -> bool:
    """Return true when a non-host process chain ends at a missing parent.

    This distinguishes an abandoned CLI/MCP session from a live Desktop or
    bridge-owned chain. The caller must still apply age, identity, launch-batch,
    and protected-process gates before cleanup.
    """
    current = item
    seen: set[Any] = set()
    for _ in range(max_depth):
        parent_pid = current.get("parent_pid")
        if parent_pid in (None, 0) or parent_pid in seen:
            return False
        seen.add(parent_pid)
        parent = process_by_pid.get(parent_pid)
        if not parent:
            return True
        if codex_host_role(parent.get("command_line")) != "other":
            return False
        current = parent
    return False


def descendant_pids(root_pid: Any, processes: list[dict[str, Any]]) -> list[int]:
    """Return process descendants for a PID using the observed parent links.

    The process table is a read-only snapshot. Missing parents, non-integer
    PIDs, and parent cycles are ignored so cleanup planning can stay bounded
    even when Windows returns partial or stale process metadata.
    """
    try:
        root = int(root_pid)
    except (TypeError, ValueError):
        return []

    children_by_parent: dict[int, list[int]] = {}
    for proc in processes:
        try:
            pid = int(proc.get("pid"))
            parent_pid = int(proc.get("parent_pid"))
        except (TypeError, ValueError):
            continue
        if pid == parent_pid:
            continue
        children_by_parent.setdefault(parent_pid, []).append(pid)

    descendants: list[int] = []
    seen: set[int] = {root}
    stack = list(children_by_parent.get(root, []))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        descendants.append(pid)
        stack.extend(children_by_parent.get(pid, []))
    return sorted(descendants)


def effective_expected_max(group: dict[str, Any]) -> int:
    if bool(group.get("decommissioned_member")):
        return 0
    base = max(1, int(group.get("expected_max") or 1))
    group_name = str(group.get("group") or "")
    if group_name not in DUAL_HOST_STDIO_GROUPS:
        return base
    host_counts = group.get("host_root_counts") if isinstance(group.get("host_root_counts"), dict) else {}
    codex_host_count = sum(
        1
        for key in ("desktop_app_server", "bridge_app_server")
        if int(host_counts.get(key) or 0) > 0
    )
    return max(base, codex_host_count)


def parse_process_start_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def process_age_minutes(proc: dict[str, Any], now: datetime | None = None) -> float | None:
    started = parse_process_start_time(proc.get("start_time"))
    if not started:
        return None
    current = now or datetime.now(started.tzinfo or timezone.utc)
    if current.tzinfo is None and started.tzinfo is not None:
        current = current.replace(tzinfo=started.tzinfo)
    if started.tzinfo is None and current.tzinfo is not None:
        started = started.replace(tzinfo=current.tzinfo)
    return max(0.0, (current - started).total_seconds() / 60.0)


def codex_package_version(path: str) -> tuple[int, ...]:
    match = re.search(r"OpenAI\.Codex_([0-9]+(?:\.[0-9]+){1,3})_", str(path or ""))
    if not match:
        return ()
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return ()


def version_text(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def powershell_json_command(script: str) -> list[str]:
    return powershell_encoded_command(
        script,
        executable=resolve_powershell_executable(),
        execution_policy_bypass=True,
    )


def run_hidden_powershell(command: list[str], timeout: int) -> subprocess.CompletedProcess[str] | dict[str, Any]:
    try:
        return subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **NO_WINDOW_KW,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    except OSError as exc:
        return {
            "ok": False,
            "timed_out": False,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "error": "powershell_unavailable",
        }


def parse_powershell_json_result(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    text = (proc.stdout or "").strip()
    try:
        parsed = json.loads(text) if text else []
    except json.JSONDecodeError:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout": text[:2000],
            "stderr": (proc.stderr or "").strip()[:2000],
            "error": "powershell_json_parse_failed",
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "items": parsed if isinstance(parsed, list) else [parsed],
        "stderr": (proc.stderr or "").strip()[:2000],
    }


def run_powershell_json(script: str, timeout: int = 20) -> dict[str, Any]:
    command = powershell_json_command(script)
    result = run_hidden_powershell(command, timeout)
    if isinstance(result, dict):
        return result
    return parse_powershell_json_result(result)


def process_executable_name(item: dict[str, Any]) -> str:
    """Return the actual process executable name, never a nested command string."""
    reported = str(item.get("name") or "").strip().strip('"').lower()
    if reported:
        return Path(reported).name
    command_line = str(item.get("command_line") or "").strip()
    match = re.match(r'^\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s]+))', command_line)
    token = next((value for value in match.groups() if value), "") if match else ""
    return Path(token).name.lower() if token else ""


def is_codex_executable_process(item: dict[str, Any]) -> bool:
    return process_executable_name(item) in {"codex", "codex.exe"}


def is_codex_resource_process(item: dict[str, Any]) -> bool:
    command_line = str(item.get("command_line") or "").lower()
    return is_codex_executable_process(item) and "openai.codex_" in command_line and "\\app\\resources\\codex.exe" in command_line


def latest_codex_package_version(processes: list[dict[str, Any]]) -> tuple[int, ...]:
    versions = [codex_package_version(str(item.get("command_line") or "")) for item in processes if is_codex_resource_process(item)]
    return max([version for version in versions if version], default=())


def codex_app_server_owner_entry(
    *,
    item: dict[str, Any],
    latest_version: tuple[int, ...],
) -> dict[str, Any]:
    command_line = str(item.get("command_line") or "")
    owner_version = codex_package_version(command_line)
    is_codex_app_server = is_codex_executable_process(item)
    version_known = bool(owner_version)
    version_ok = True
    if latest_version and owner_version:
        version_ok = bool(owner_version and owner_version >= latest_version)
    return {
        "pid": item.get("pid"),
        "parent_pid": item.get("parent_pid"),
        "parent_name": item.get("parent_name"),
        "version": version_text(owner_version),
        "version_known": version_known,
        "command_line": command_line,
        "is_codex_app_server": is_codex_app_server,
        "version_ok": version_ok,
        "version_comparable": bool(not latest_version or owner_version),
        "healthy": bool(is_codex_app_server and version_ok),
    }


def codex_app_server_owner_state(processes: list[dict[str, Any]], host: str = "127.0.0.1", port: int = 18791) -> dict[str, Any]:
    listen = f"ws://{host}:{port}"
    latest_version = latest_codex_package_version(processes)
    owners: list[dict[str, Any]] = []
    for item in processes:
        command_line = str(item.get("command_line") or "")
        if not is_codex_executable_process(item):
            continue
        if not re.search(r"(?:^|\s)app-server(?:\s|$)", command_line, re.IGNORECASE) or listen not in command_line:
            continue
        owners.append(codex_app_server_owner_entry(item=item, latest_version=latest_version))
    healthy = len(owners) == 1 and bool(owners[0].get("healthy"))
    version_advisory = bool(healthy and latest_version and not owners[0].get("version_known"))
    return {
        "host": host,
        "port": port,
        "listen": listen,
        "latest_detected_version": version_text(latest_version),
        "owner_count": len(owners),
        "healthy": healthy,
        "owners": owners,
        "issue": "version_unknown" if version_advisory else ("" if healthy else ("missing" if not owners else "version_drift_or_unexpected_owner")),
    }


PROCESS_SNAPSHOT_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$all = @{}
foreach ($p in Get-CimInstance Win32_Process) {
  $all[[int]$p.ProcessId] = $p
}
$rows = foreach ($proc in $all.Values) {
  $gp = Get-Process -Id $proc.ProcessId -ErrorAction SilentlyContinue
  $parent = $all[[int]$proc.ParentProcessId]
  [pscustomobject]@{
    pid = [int]$proc.ProcessId
    parent_pid = [int]$proc.ParentProcessId
    parent_name = if ($parent) { [string]$parent.Name } else { '' }
    parent_command_line = if ($parent) { [string]$parent.CommandLine } else { '' }
    name = [string]$proc.Name
    command_line = [string]$proc.CommandLine
    working_set_mb = if ($gp) { [math]::Round($gp.WorkingSet64 / 1MB, 1) } else { 0 }
    cpu_seconds = if ($gp -and $null -ne $gp.CPU) { [math]::Round($gp.CPU, 2) } else { 0 }
    start_time = if ($gp -and $gp.StartTime) { $gp.StartTime.ToString('o') } else { '' }
  }
}
$rows | ConvertTo-Json -Depth 4
"""


def matched_process_row(item: dict[str, Any], spec: ProcessPattern) -> dict[str, Any]:
    row = dict(item)
    row.update(
        {
            "group": spec.group,
            "category": spec.category,
            "expected_max": spec.expected_max,
            "cleanup_policy": spec.cleanup_policy,
            "protected": spec.protected,
            "decommissioned_member": spec.decommissioned_member,
            "member": spec.member,
        }
    )
    return row


def empty_process_group_bucket(spec: ProcessPattern) -> dict[str, Any]:
    return {
        "group": spec.group,
        "category": spec.category,
        "expected_max": spec.expected_max,
        "cleanup_policy": spec.cleanup_policy,
        "protected": spec.protected,
        "decommissioned_member": spec.decommissioned_member,
        "member": spec.member,
        "count": 0,
        "working_set_mb": 0.0,
        "cpu_seconds": 0.0,
        "pids": [],
        "oldest_start_time": "",
        "newest_start_time": "",
    }


def update_process_group_bucket(
    *,
    bucket: dict[str, Any],
    row: dict[str, Any],
    matched: list[dict[str, Any]],
) -> None:
    bucket["count"] += 1
    bucket["working_set_mb"] = round(float(bucket["working_set_mb"]) + float(row.get("working_set_mb") or 0), 1)
    bucket["cpu_seconds"] = round(float(bucket["cpu_seconds"]) + float(row.get("cpu_seconds") or 0), 2)
    bucket["pids"].append(row.get("pid"))
    starts = sorted(str(x.get("start_time") or "") for x in matched if x.get("group") == bucket.get("group") and x.get("start_time"))
    bucket["oldest_start_time"] = starts[0] if starts else ""
    bucket["newest_start_time"] = starts[-1] if starts else ""


def matched_processes_and_groups(all_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    matched: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}
    process_by_pid = {item.get("pid"): item for item in all_items}
    for item in all_items:
        command_line = str(item.get("command_line") or "")
        if is_resource_process_observer_command(command_line) or has_resource_process_observer_ancestor(item, process_by_pid):
            continue
        name = str(item.get("name") or "")
        haystack = f"{name}\n{command_line}".lower()
        for spec in PROCESS_PATTERNS:
            if matches_process_pattern(spec, haystack):
                row = matched_process_row(item, spec)
                matched.append(row)
                bucket = grouped.setdefault(spec.group, empty_process_group_bucket(spec))
                update_process_group_bucket(bucket=bucket, row=row, matched=matched)
                break
    return matched, grouped


def annotate_matched_process_roots(matched: list[dict[str, Any]], all_items: list[dict[str, Any]]) -> None:
    all_by_pid = {item.get("pid"): item for item in all_items}
    matched_by_pid = {int(row.get("pid") or 0): row for row in matched}
    for row in matched:
        parent = matched_by_pid.get(int(row.get("parent_pid") or 0))
        row["instance_root"] = not (parent and parent.get("group") == row.get("group"))
        role, chain = process_host_role(row, all_by_pid)
        row["host_role"] = role
        row["host_parent_chain"] = chain[:5]
        row["host_parent_chain_orphaned"] = process_host_chain_orphaned(row, all_by_pid)


def finalize_process_group_buckets(grouped: dict[str, dict[str, Any]], matched: list[dict[str, Any]]) -> None:
    for bucket in grouped.values():
        group_rows = [row for row in matched if row.get("group") == bucket.get("group")]
        roots = [
            row
            for row in group_rows
            if row.get("instance_root")
        ]
        bucket["root_instance_count"] = len(roots)
        bucket["root_instance_pids"] = [row.get("pid") for row in roots]
        root_details = [
            {
                "pid": row.get("pid"),
                "age_minutes": process_age_minutes(row),
                "working_set_mb": float(row.get("working_set_mb") or 0),
                "executable_name": process_executable_name(row),
            }
            for row in roots
        ]
        process_details = [
            {
                "pid": row.get("pid"),
                "age_minutes": process_age_minutes(row),
                "working_set_mb": float(row.get("working_set_mb") or 0),
            }
            for row in group_rows
        ]
        bucket["root_instance_details"] = root_details
        bucket["fanout_age_evidence_complete"] = all(
            item.get("age_minutes") is not None for item in root_details
        )
        bucket["persistent_root_instance_count"] = sum(
            1
            for item in root_details
            if item.get("age_minutes") is not None
            and float(item["age_minutes"]) >= FANOUT_MIN_AGE_MINUTES
        )
        bucket["persistent_process_count"] = sum(
            1
            for item in process_details
            if item.get("age_minutes") is not None
            and float(item["age_minutes"]) >= FANOUT_MIN_AGE_MINUTES
        )
        bucket["persistent_working_set_mb"] = round(
            sum(
                float(item.get("working_set_mb") or 0)
                for item in process_details
                if item.get("age_minutes") is not None
                and float(item["age_minutes"]) >= FANOUT_MIN_AGE_MINUTES
            ),
            1,
        )
        host_root_counts: dict[str, int] = {}
        host_root_pids: dict[str, list[Any]] = {}
        for row in roots:
            role = str(row.get("host_role") or "other")
            host_root_counts[role] = int(host_root_counts.get(role, 0)) + 1
            host_root_pids.setdefault(role, []).append(row.get("pid"))
        bucket["host_root_counts"] = host_root_counts
        bucket["host_root_pids"] = host_root_pids
        orphan_roots = [row for row in roots if row.get("host_parent_chain_orphaned")]
        bucket["orphaned_host_root_pids"] = [row.get("pid") for row in orphan_roots]
        bucket["orphaned_host_root_details"] = [
            {
                "pid": row.get("pid"),
                "age_minutes": process_age_minutes(row),
                "executable_name": process_executable_name(row),
            }
            for row in orphan_roots
        ]
        bucket["effective_expected_max"] = effective_expected_max(bucket)


def process_snapshot() -> dict[str, Any]:
    observed = run_powershell_json(PROCESS_SNAPSHOT_SCRIPT)
    all_items = observed.get("items") if isinstance(observed.get("items"), list) else []
    matched, grouped = matched_processes_and_groups(all_items)
    annotate_matched_process_roots(matched, all_items)
    finalize_process_group_buckets(grouped, matched)
    groups = sorted(grouped.values(), key=lambda row: (-int(row["count"]), str(row["group"])))
    app_server_owner = codex_app_server_owner_state(all_items)
    return {
        "schema": "resource_process.snapshot.v1",
        "ok": bool(observed.get("ok")),
        "generated_at": now_iso(),
        "root": str(ROOT),
        "groups": groups,
        "processes": sorted(matched, key=lambda row: (str(row.get("group")), int(row.get("pid") or 0))),
        "codex_app_server_owner": app_server_owner,
        "reporting_safety_contract": process_reporting_safety_contract(),
        "observer": {
            "stderr": observed.get("stderr", ""),
            "error": observed.get("error", ""),
            "timed_out": bool(observed.get("timed_out")),
        },
    }


def process_reporting_safety_contract() -> dict[str, Any]:
    return {
        "schema": "resource_process.reporting_safety.v1",
        "fixed_pid_cleanup_forbidden": True,
        "parent_missing_alone_is_insufficient": True,
        "required_candidate_evidence": [
            "fresh_process_snapshot",
            "normalized_command_identity",
            "minimum_process_age",
            "owner_or_session_anchor",
            "protection_group_classification",
            "launch_batch_membership",
        ],
        "cleanup_entrypoint": "fresh_owner_repair_plan_then_safe_apply",
        "direct_stop_entrypoint_exposed": False,
        "apply_requires_revalidation": True,
    }


def classify_group(group: dict[str, Any]) -> dict[str, Any] | None:
    observed_count = int(group.get("root_instance_count") or group.get("count") or 0)
    process_count = int(group.get("count") or 0)
    expected_max = int(group.get("expected_max") if group.get("expected_max") is not None else 1)
    effective_expected = effective_expected_max(group)
    if observed_count <= effective_expected:
        return None
    age_evidence_complete = bool(group.get("fanout_age_evidence_complete"))
    persistent_count = int(group.get("persistent_root_instance_count") or 0)
    if age_evidence_complete and persistent_count <= effective_expected:
        group["transient_fanout"] = True
        group["transient_fanout_observed_roots"] = observed_count
        group["fanout_min_age_minutes"] = FANOUT_MIN_AGE_MINUTES
        return None
    count = persistent_count if age_evidence_complete else observed_count
    excess = count - effective_expected
    severity = "advisory"
    if excess >= 8 or float(group.get("working_set_mb") or 0) >= 400:
        severity = "risk"
    if bool(group.get("protected")):
        severity = "advisory" if severity == "risk" else severity
    return {
        "severity": severity,
        "code": "resource_process_fanout",
        "group": group.get("group"),
        "message": (
            f"{group.get('group')} has {count} age-qualified root instances "
            f"({observed_count} observed; {process_count} matching processes); expected <= {effective_expected}."
        ),
        "count": count,
        "observed_root_instance_count": observed_count,
        "persistent_root_instance_count": persistent_count if age_evidence_complete else None,
        "fanout_age_evidence_complete": age_evidence_complete,
        "fanout_min_age_minutes": FANOUT_MIN_AGE_MINUTES,
        "process_count": process_count,
        "expected_max": expected_max,
        "effective_expected_max": effective_expected,
        "excess": excess,
        "working_set_mb": group.get("working_set_mb"),
        "cpu_seconds": group.get("cpu_seconds"),
        "host_root_counts": group.get("host_root_counts"),
        "protected": bool(group.get("protected")),
        "cleanup_policy": group.get("cleanup_policy"),
        "manual_action": "Review dry-run repair plan; stop only duplicate non-active MCP children after confirming current sessions do not depend on them.",
    }


def classify_dead_transport_group(group: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any] | None:
    root_count = int(group.get("root_instance_count") or group.get("count") or 0)
    process_count = int(group.get("count") or 0)
    if root_count <= 0:
        return None
    protected = bool(group.get("protected"))
    severity = "advisory" if protected else "risk"
    status = str(observation.get("status") or "unknown")
    profile = str(observation.get("profile") or "")
    return {
        "severity": severity,
        "code": "mcp_dead_transport_process_retained",
        "group": group.get("group"),
        "profile": profile,
        "status": status,
        "message": (
            f"{profile or group.get('group')} recorded current-turn {status}, "
            f"but {root_count} root process(es) / {process_count} matching process(es) are still present."
        ),
        "count": root_count,
        "process_count": process_count,
        "expected_max": group.get("expected_max"),
        "working_set_mb": group.get("working_set_mb"),
        "protected": protected,
        "cleanup_policy": group.get("cleanup_policy"),
        "observation": observation,
        "manual_action": "Do not call this MCP again in the current turn; use fallback, then refresh/rebind the Codex session. Review stale child processes through repair-plan before cleanup.",
    }


def classify_orphaned_stdio_host_group(group: dict[str, Any]) -> dict[str, Any] | None:
    group_name = str(group.get("group") or "")
    if group_name not in SESSION_OWNED_STDIO_GROUPS:
        return None
    root_pids = [pid for pid in group.get("orphaned_host_root_pids") or [] if pid is not None]
    if not root_pids:
        return None
    details = group.get("orphaned_host_root_details") if isinstance(group.get("orphaned_host_root_details"), list) else []
    ages = [float(item.get("age_minutes")) for item in details if isinstance(item, dict) and item.get("age_minutes") is not None]
    min_age_minutes = 2.0
    persistent = bool(ages) and len(ages) == len(root_pids) and min(ages) >= min_age_minutes
    severity = "risk" if persistent and not bool(group.get("protected")) else "advisory"
    return {
        "severity": severity,
        "code": "mcp_orphaned_stdio_host_chain",
        "group": group_name,
        "message": f"{group_name} has {len(root_pids)} session-owned stdio root(s) whose launcher parent chain no longer exists.",
        "count": len(root_pids),
        "root_pids": root_pids,
        "root_details": details,
        "persistent_after_age_gate": persistent,
        "risk_min_age_minutes": min_age_minutes,
        "protected": bool(group.get("protected")),
        "cleanup_policy": group.get("cleanup_policy"),
        "manual_action": "Re-sample after the age gate, then use the owner repair plan and safe cleanup; broken host-chain evidence must still pass age and identity gates.",
    }


def stale_bridge_host_root_pids(group: dict[str, Any]) -> list[Any]:
    if str(group.get("group") or "") not in SESSION_OWNED_STDIO_GROUPS:
        return []
    host_root_pids = group.get("host_root_pids") if isinstance(group.get("host_root_pids"), dict) else {}
    bridge_pids = [pid for pid in host_root_pids.get("bridge_app_server", []) if pid is not None]
    desktop_pids = [pid for pid in host_root_pids.get("desktop_app_server", []) if pid is not None]
    if desktop_pids:
        return []
    return bridge_pids


def classify_stale_bridge_host_group(
    group: dict[str, Any],
    observation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    bridge_pids = stale_bridge_host_root_pids(group)
    if not bridge_pids:
        return None
    protected = bool(group.get("protected"))
    severity = "advisory" if protected else "risk"
    profile = str((observation or {}).get("profile") or "")
    status = str((observation or {}).get("status") or "host_owner_stale")
    return {
        "severity": severity,
        "code": "mcp_stale_bridge_app_server_owner",
        "group": group.get("group"),
        "profile": profile,
        "status": status,
        "message": (
            f"{group.get('group')} has session-owned stdio MCP root(s) only under the bridge app-server. "
            "A stdio MCP process is tied to its client transport, so a bridge-owned root must not be treated "
            "as current desktop-turn callability evidence."
        ),
        "count": len(bridge_pids),
        "process_count": group.get("count"),
        "bridge_root_pids": bridge_pids,
        "host_root_counts": group.get("host_root_counts"),
        "protected": protected,
        "cleanup_policy": group.get("cleanup_policy"),
        "observation": observation or {},
        "manual_action": (
            "Use fallback for this turn, then dry-run cleanup stale non-protected bridge-owned MCP roots so the "
            "next current session starts fresh stdio transports on demand."
        ),
    }


def current_turn_observation_context() -> tuple[dict[str, Any], dict[str, Any]]:
    anchor = thread_freshness_anchor(None)
    anchor_at = parse_iso_datetime(anchor.get("anchor_at")) if isinstance(anchor, dict) else None
    observations = current_turn_tool_observations(anchor_at=anchor_at)
    return anchor, observations


def negative_observations_by_group(observations: dict[str, Any]) -> dict[str, Any]:
    return (
        observations.get("negative_by_group")
        if isinstance(observations.get("negative_by_group"), dict)
        else {}
    )


def codex_app_server_owner_issue(owner: dict[str, Any]) -> dict[str, Any] | None:
    if not owner:
        return None
    owner_issue = str(owner.get("issue") or "")
    if owner.get("healthy") and owner_issue != "version_unknown":
        return None
    if owner_issue == "version_unknown":
        message = "Bridge app-server version is not encoded in its bin path; ownership is healthy but version comparison is unavailable."
    elif owner_issue == "missing":
        message = "Bridge app-server primary route is not currently owned; verify CDP/CLI fallback before treating the whole bridge as down."
    else:
        message = "Bridge app-server port is not owned by exactly one current Codex app-server."
    return {
        "severity": "advisory" if owner_issue in {"missing", "version_unknown"} else "risk",
        "code": "codex_app_server_owner_version_unknown" if owner_issue == "version_unknown" else "codex_app_server_owner_unhealthy",
        "message": message,
        "owner": owner,
        "route_scope": "primary_app_server_route",
        "fallback_check_required": owner_issue != "version_unknown",
        "manual_action": "Do not restart for version_unknown; use governed repair only for verified ownership or version drift.",
    }


def resource_process_issues(groups: list[dict[str, Any]], observations: dict[str, Any], owner: dict[str, Any]) -> list[dict[str, Any]]:
    issues = [issue for group in groups if (issue := classify_group(group))]
    negative_by_group = negative_observations_by_group(observations)
    for group in groups:
        orphaned_issue = classify_orphaned_stdio_host_group(group)
        if orphaned_issue:
            issues.append(orphaned_issue)
        observation = negative_by_group.get(str(group.get("group") or ""))
        if isinstance(observation, dict):
            issue = classify_dead_transport_group(group, observation)
            if issue:
                issues.append(issue)
        stale_issue = classify_stale_bridge_host_group(
            group,
            observation if isinstance(observation, dict) else None,
        )
        if stale_issue:
            issues.append(stale_issue)
    owner_issue = codex_app_server_owner_issue(owner)
    if owner_issue:
        issues.append(owner_issue)
    groups_by_name = {str(group.get("group") or ""): group for group in groups}
    stale_hub_desktop_roots: list[dict[str, Any]] = []
    for profile, group_name in HUB_MANAGED_PROCESS_GROUPS.items():
        if profile not in HUB_MANAGED_MCP_NAMES:
            continue
        group = groups_by_name.get(group_name, {})
        host_counts = group.get("host_root_counts") if isinstance(group.get("host_root_counts"), dict) else {}
        desktop_roots = int(host_counts.get("desktop_app_server") or 0)
        if desktop_roots:
            stale_hub_desktop_roots.append(
                {
                    "profile": profile,
                    "group": group_name,
                    "desktop_root_count": desktop_roots,
                    "desktop_instance_budget": 0,
                    "working_set_mb": float(group.get("working_set_mb") or 0),
                }
            )
    if stale_hub_desktop_roots:
        issues.append(
            {
                "severity": "advisory",
                "code": "hub_managed_desktop_roots_pending_restart",
                "message": f"{sum(item['desktop_root_count'] for item in stale_hub_desktop_roots)} pre-change Desktop MCP roots remain for Hub-managed profiles.",
                "profiles": stale_hub_desktop_roots,
                "manual_action": "Use a controlled Codex Desktop restart; do not terminate current-session MCP children individually. Re-run resource_process_doctor.py validate after restart.",
            }
        )
    mcp_groups = [
        group
        for group in groups
        if str(group.get("group") or "") not in MCP_PRESSURE_EXCLUDED_GROUPS
        and ("mcp" in str(group.get("category") or "").lower() or str(group.get("group") or "") in SESSION_OWNED_STDIO_GROUPS)
    ]
    observed_mcp_roots = sum(int(group.get("root_instance_count") or group.get("count") or 0) for group in mcp_groups)
    observed_mcp_working_set = round(sum(float(group.get("working_set_mb") or 0) for group in mcp_groups), 1)
    mcp_roots = sum(
        int(group.get("persistent_root_instance_count") or 0)
        if bool(group.get("fanout_age_evidence_complete"))
        else int(group.get("root_instance_count") or group.get("count") or 0)
        for group in mcp_groups
    )
    mcp_working_set = round(
        sum(
            float(group.get("persistent_working_set_mb") or 0)
            if bool(group.get("fanout_age_evidence_complete"))
            else float(group.get("working_set_mb") or 0)
            for group in mcp_groups
        ),
        1,
    )
    budget_state = mcp_budget_state(mcp_roots, mcp_working_set)
    if budget_state != "ok":
        severity = budget_state
        issues.append({
            "severity": severity,
            "code": "mcp_session_multiplication_pressure",
            "message": (
                f"Configured MCP process chains retain {mcp_roots} age-qualified root instances "
                f"and {mcp_working_set} MB after the {FANOUT_MIN_AGE_MINUTES:g}-minute fanout gate "
                f"({observed_mcp_roots} roots / {observed_mcp_working_set} MB observed)."
            ),
            "root_instance_count": mcp_roots,
            "working_set_mb": mcp_working_set,
            "observed_root_instance_count": observed_mcp_roots,
            "observed_working_set_mb": observed_mcp_working_set,
            "fanout_min_age_minutes": FANOUT_MIN_AGE_MINUTES,
            "warn_budget": {"roots": MCP_ROOT_WARN_BUDGET, "working_set_mb": MCP_WORKING_SET_WARN_MB},
            "risk_budget": {"roots": MCP_ROOT_RISK_BUDGET, "working_set_mb": MCP_WORKING_SET_RISK_MB},
            "pressure_kind": "multiplication_and_memory" if mcp_roots >= MCP_ROOT_WARN_BUDGET else "memory_only",
            "manual_action": "Prefer shared Hub/on-demand adapters for stateless profiles; preserve session-native tools only where current browser, GUI, thread, or REPL state requires them.",
        })
    return issues


def resource_process_summary(
    *,
    groups: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    owner: dict[str, Any],
    observations: dict[str, Any],
) -> dict[str, Any]:
    total_ws = round(sum(float(group.get("working_set_mb") or 0) for group in groups), 1)
    mcp_groups = [
        group
        for group in groups
        if str(group.get("group") or "") not in MCP_PRESSURE_EXCLUDED_GROUPS
        and ("mcp" in str(group.get("category") or "").lower() or str(group.get("group") or "") in SESSION_OWNED_STDIO_GROUPS)
    ]
    mcp_root_count = sum(int(group.get("root_instance_count") or group.get("count") or 0) for group in mcp_groups)
    mcp_working_set_mb = round(sum(float(group.get("working_set_mb") or 0) for group in mcp_groups), 1)
    persistent_mcp_root_count = sum(
        int(group.get("persistent_root_instance_count") or 0)
        if bool(group.get("fanout_age_evidence_complete"))
        else int(group.get("root_instance_count") or group.get("count") or 0)
        for group in mcp_groups
    )
    persistent_mcp_working_set_mb = round(
        sum(
            float(group.get("persistent_working_set_mb") or 0)
            if bool(group.get("fanout_age_evidence_complete"))
            else float(group.get("working_set_mb") or 0)
            for group in mcp_groups
        ),
        1,
    )
    return {
        "matched_group_count": len(groups),
        "matched_process_count": sum(int(group.get("count") or 0) for group in groups),
        "root_instance_count": sum(int(group.get("root_instance_count") or group.get("count") or 0) for group in groups),
        "matched_working_set_mb": total_ws,
        "mcp_root_instance_count": mcp_root_count,
        "mcp_working_set_mb": mcp_working_set_mb,
        "persistent_mcp_root_instance_count": persistent_mcp_root_count,
        "persistent_mcp_working_set_mb": persistent_mcp_working_set_mb,
        "fanout_min_age_minutes": FANOUT_MIN_AGE_MINUTES,
        "mcp_instance_budget_state": mcp_budget_state(
            persistent_mcp_root_count,
            persistent_mcp_working_set_mb,
        ),
        "fanout_groups": [issue.get("group") for issue in issues if issue.get("group")],
        "transient_fanout_groups": [
            group.get("group") for group in groups if group.get("transient_fanout")
        ],
        "dead_transport_groups": [
            issue.get("group")
            for issue in issues
            if issue.get("code") == "mcp_dead_transport_process_retained"
        ],
        "stale_bridge_host_groups": [
            issue.get("group")
            for issue in issues
            if issue.get("code") == "mcp_stale_bridge_app_server_owner"
        ],
        "codex_app_server_owner_healthy": bool(owner.get("healthy")) if owner else None,
        "codex_app_server_owner_issue": str(owner.get("issue") or "") if owner else "",
        "current_turn_observation_state": observations.get("state"),
    }


def doctor(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or process_snapshot()
    groups = snap.get("groups") if isinstance(snap.get("groups"), list) else []
    owner = snap.get("codex_app_server_owner") if isinstance(snap.get("codex_app_server_owner"), dict) else {}
    anchor, current_turn_observations = current_turn_observation_context()
    issues = resource_process_issues(groups, current_turn_observations, owner)
    return {
        "schema": "resource_process.doctor.v1",
        "ok": not any(issue.get("severity") in {"blocker", "risk"} for issue in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": resource_process_summary(
            groups=groups,
            issues=issues,
            owner=owner,
            observations=current_turn_observations,
        ),
        "current_turn_anchor": anchor,
        "reporting_safety_contract": process_reporting_safety_contract(),
        "snapshot": snap,
    }


def dead_transport_lifecycle(
    root_candidates: list[dict[str, Any]],
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    stop_root_pids = {item.get("pid") for item in root_candidates if item.get("pid") is not None}
    stop_pids = set(stop_root_pids)
    for pid in list(stop_root_pids):
        stop_pids.update(descendant_pids(pid, processes))
    return {
        "policy": "current_turn_dead_transport_review_roots",
        "reason": "The active Codex turn recorded a closed/unbound transport; stdio MCP sessions cannot be reused after their client pipe is closed, so retained non-protected roots are stale cleanup candidates after age gating.",
        "keep_pids": [],
        "keep_root_instance_pids": [],
        "orphan_candidate_pids": sorted(stop_pids),
        "orphan_candidate_root_instance_pids": sorted(stop_root_pids),
        "orphan_batches": [],
        "latest_batches_kept": [],
    }


def orphaned_stdio_lifecycle(
    issue: dict[str, Any],
    root_candidates: list[dict[str, Any]],
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_roots = set(issue.get("root_pids") or [])
    stop_root_pids = {
        item.get("pid") for item in root_candidates
        if item.get("pid") in selected_roots and item.get("host_parent_chain_orphaned")
    }
    keep_root_pids = {item.get("pid") for item in root_candidates if item.get("pid") not in stop_root_pids}
    stop_pids = set(stop_root_pids)
    for pid in list(stop_root_pids):
        stop_pids.update(descendant_pids(pid, processes))
    return {
        "policy": "broken_stdio_host_chain_roots",
        "reason": "Session-owned stdio roots cannot outlive their launching client chain; select only roots with a freshly observed broken host chain.",
        "keep_pids": sorted(keep_root_pids),
        "keep_root_instance_pids": sorted(keep_root_pids),
        "orphan_candidate_pids": sorted(stop_pids),
        "orphan_candidate_root_instance_pids": sorted(stop_root_pids),
        "orphan_batches": [{"pids": sorted(stop_root_pids), "reason": "broken_host_parent_chain"}],
        "latest_batches_kept": [{"pids": sorted(keep_root_pids), "reason": "host_chain_still_live"}] if keep_root_pids else [],
    }


def stale_bridge_owner_lifecycle(
    group: dict[str, Any],
    root_candidates: list[dict[str, Any]],
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    stale_root_pids = {
        pid for pid in stale_bridge_host_root_pids(group)
        if pid is not None
    }
    stop_root_pids = {
        item.get("pid")
        for item in root_candidates
        if item.get("pid") in stale_root_pids
    }
    stop_pids = set(stop_root_pids)
    for pid in list(stop_root_pids):
        stop_pids.update(descendant_pids(pid, processes))
    return {
        "policy": "stale_bridge_app_server_stdio_roots",
        "reason": (
            "The only live roots for this session-owned stdio MCP are under the bridge app-server, "
            "while the current desktop turn recorded stale or unverified transport evidence. "
            "Clear non-protected bridge-owned roots after age gating so the current session must "
            "open a fresh stdio transport instead of inheriting a stale owner."
        ),
        "keep_pids": [],
        "keep_root_instance_pids": [],
        "orphan_candidate_pids": sorted(stop_pids),
        "orphan_candidate_root_instance_pids": sorted(stop_root_pids),
        "orphan_batches": [
            {
                "host_role": "bridge_app_server",
                "pids": sorted(stop_root_pids),
                "reason": "session_owned_stdio_root_without_desktop_owner",
            }
        ] if stop_root_pids else [],
        "latest_batches_kept": [],
    }


def repair_plan_lifecycle(
    issue: dict[str, Any],
    group: dict[str, Any],
    source_group: dict[str, Any],
    candidates: list[dict[str, Any]],
    root_candidates: list[dict[str, Any]],
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    if issue.get("code") == "mcp_dead_transport_process_retained" and not bool(group.get("protected")):
        return dead_transport_lifecycle(root_candidates, processes)
    if issue.get("code") == "mcp_stale_bridge_app_server_owner" and not bool(group.get("protected")):
        return stale_bridge_owner_lifecycle(group, root_candidates, processes)
    if issue.get("code") == "mcp_orphaned_stdio_host_chain":
        return orphaned_stdio_lifecycle(issue, root_candidates, processes)
    return lifecycle_candidates(group, source_group, candidates, root_candidates, processes)


def repair_plan_action(
    issue: dict[str, Any],
    group_name: str,
    group: dict[str, Any],
    candidates: list[dict[str, Any]],
    root_candidates: list[dict[str, Any]],
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    keep_pids = set(lifecycle.get("keep_pids") or [])
    keep_root_pids = set(lifecycle.get("keep_root_instance_pids") or [])
    stop_pids = set(lifecycle.get("orphan_candidate_pids") or [])
    stop_root_pids = set(lifecycle.get("orphan_candidate_root_instance_pids") or [])
    stop_candidates = [item for item in candidates if item.get("pid") in stop_pids]
    stop_root_candidates = [item for item in root_candidates if item.get("pid") in stop_root_pids]
    return {
        "code": "review_duplicate_resource_processes",
        "source_issue_code": issue.get("code"),
        "group": group_name,
        "dry_run_only": True,
        "protected": bool(group.get("protected")),
        "cleanup_policy": group.get("cleanup_policy"),
        "lifecycle_policy": lifecycle.get("policy"),
        "lifecycle_reason": lifecycle.get("reason"),
        "would_keep_pids": sorted(keep_pids),
        "would_keep_root_instance_pids": sorted(keep_root_pids),
        "would_review_stop_pids": [item.get("pid") for item in stop_candidates],
        "would_review_stop_root_instance_pids": [item.get("pid") for item in stop_root_candidates],
        "orphan_batch_candidates": lifecycle.get("orphan_batches"),
        "latest_batches_kept": lifecycle.get("latest_batches_kept"),
        "would_mutate": "nothing in this tool; process termination is intentionally not implemented",
        "guardrails": [
            "do_not_stop_codex_main_process",
            "do_not_stop_bridge_worker_or_gateway",
            "do_not_stop_reasonix_responder",
            "confirm_current_mcp_session_dependency_before_stop",
            "prefer fixing launcher singletons over manual repeated cleanup",
        ],
        "validation": "rerun resource-process-doctor and affected MCP health checks after any external cleanup",
    }


def repair_plan_counts(actions: list[dict[str, Any]]) -> dict[str, int]:
    orphan_action_count = sum(
        1 for action in actions
        if action.get("would_review_stop_root_instance_pids")
    )
    protected_action_count = sum(
        1 for action in actions
        if action.get("protected") and action.get("would_review_stop_root_instance_pids")
    )
    non_protected_action_count = sum(
        1 for action in actions
        if not action.get("protected") and action.get("would_review_stop_root_instance_pids")
    )
    root_count = sum(
        len(action.get("would_review_stop_root_instance_pids") or [])
        for action in actions
    )
    non_protected_root_count = sum(
        len(action.get("would_review_stop_root_instance_pids") or [])
        for action in actions
        if not action.get("protected")
    )
    process_count = sum(
        len(action.get("would_review_stop_pids") or [])
        for action in actions
    )
    return {
        "orphan_candidate_action_count": orphan_action_count,
        "protected_orphan_candidate_action_count": protected_action_count,
        "non_protected_orphan_candidate_action_count": non_protected_action_count,
        "orphan_candidate_root_count": root_count,
        "non_protected_orphan_candidate_root_count": non_protected_root_count,
        "orphan_candidate_process_count": process_count,
    }


def repair_plan_actions(
    snap: dict[str, Any],
    doc: dict[str, Any],
    sources: dict[str, Any],
    processes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    groups = snap.get("groups", []) if isinstance(snap.get("groups"), list) else []
    source_groups = sources.get("groups", []) if isinstance(sources.get("groups"), list) else []
    for issue in doc.get("issues", []):
        group_name = str(issue.get("group") or "")
        group = next((item for item in groups if item.get("group") == group_name), {})
        source_group = next((item for item in source_groups if item.get("group") == group_name), {})
        candidates = sorted(
            [proc for proc in processes if proc.get("group") == group_name],
            key=lambda item: str(item.get("start_time") or ""),
        )
        root_candidates = [item for item in candidates if item.get("instance_root")]
        lifecycle = repair_plan_lifecycle(
            issue,
            group,
            source_group,
            candidates,
            root_candidates,
            processes,
        )
        actions.append(repair_plan_action(issue, group_name, group, candidates, root_candidates, lifecycle))
    return actions


def cleanup_dry_run_command() -> str:
    return (
        r"python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py "
        r"resource-process cleanup --min-age-minutes 15"
    )


def cleanup_apply_command(dry_run_command: str) -> str:
    return f"{dry_run_command} --apply"


def repair_plan_contracts(apply_supported: bool, apply_command: str) -> tuple[dict[str, bool], dict[str, Any]]:
    dry_run_contract = {
        "writes_files": False,
        "kills_processes": False,
        "starts_processes": False,
        "sends_messages": False,
    }
    apply_contract = {
        "command": apply_command,
        "available": apply_supported,
        "writes_files": False,
        "kills_processes": apply_supported,
        "starts_processes": False,
        "sends_messages": False,
        "requires_fresh_snapshot": True,
        "fixed_pid_cleanup_forbidden": True,
        "parent_missing_alone_is_insufficient": True,
        "required_candidate_evidence": process_reporting_safety_contract()["required_candidate_evidence"],
        "requires_age_gate": True,
        "default_min_age_minutes": 15,
        "protected_groups_default": "skip unless auto-safe evidence is complete or --include-protected is explicitly used",
    }
    return dry_run_contract, apply_contract


def repair_plan_governance_state(*, apply_supported: bool, orphan_candidate_root_count: int) -> str:
    if apply_supported:
        return "cleanup_candidate_available"
    if orphan_candidate_root_count:
        return "protected_or_observe_only_candidates"
    return "no_cleanup_candidate"


def repair_plan_next_step(apply_supported: bool) -> str:
    if apply_supported:
        return "Run resource-process cleanup dry-run, then apply only the revalidated non-protected orphan roots."
    return "Use startup-sources to distinguish transient launch waves from repeated launcher/session lifecycle drift before any cleanup."


def repair_plan_cleanup_commands(dry_run_command: str, apply_command: str, apply_supported: bool) -> dict[str, str]:
    return {
        "dry_run": dry_run_command,
        "apply": apply_command if apply_supported else "",
    }


def repair_plan_count_fields(counts: dict[str, int]) -> dict[str, int]:
    return {
        "orphan_candidate_action_count": counts["orphan_candidate_action_count"],
        "non_protected_orphan_candidate_action_count": counts["non_protected_orphan_candidate_action_count"],
        "protected_orphan_candidate_action_count": counts["protected_orphan_candidate_action_count"],
        "orphan_candidate_root_count": counts["orphan_candidate_root_count"],
        "non_protected_orphan_candidate_root_count": counts["non_protected_orphan_candidate_root_count"],
        "orphan_candidate_process_count": counts["orphan_candidate_process_count"],
    }


def repair_plan(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or process_snapshot()
    doc = doctor(snap)
    processes = snap.get("processes") if isinstance(snap.get("processes"), list) else []
    sources = startup_sources(snap)
    actions = repair_plan_actions(snap, doc, sources, processes)
    counts = repair_plan_counts(actions)
    orphan_candidate_root_count = counts["orphan_candidate_root_count"]
    non_protected_orphan_candidate_root_count = counts["non_protected_orphan_candidate_root_count"]
    cleanup_apply_supported = non_protected_orphan_candidate_root_count > 0
    dry_run_command = cleanup_dry_run_command()
    apply_command = cleanup_apply_command(dry_run_command)
    dry_run_contract, apply_contract = repair_plan_contracts(cleanup_apply_supported, apply_command)
    return {
        "schema": "resource_process.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "apply_supported": cleanup_apply_supported,
        "dry_run_contract": dry_run_contract,
        "apply_contract": apply_contract,
        "reporting_safety_contract": process_reporting_safety_contract(),
        "doctor_summary": doc.get("summary"),
        "startup_source_summary": sources.get("summary"),
        **repair_plan_count_fields(counts),
        "latest_batch_policy": "keep newest launch roots up to effective_expected_max; older batches are review candidates only",
        "cleanup_commands": repair_plan_cleanup_commands(dry_run_command, apply_command, cleanup_apply_supported),
        "governance_state": repair_plan_governance_state(
            apply_supported=cleanup_apply_supported,
            orphan_candidate_root_count=orphan_candidate_root_count,
        ),
        "actions": actions,
        "next_step": repair_plan_next_step(cleanup_apply_supported),
    }


def normalize_command(value: Any) -> str:
    return resource_startup_sources.normalize_command(value)


def launch_batches(processes: list[dict[str, Any]], window_seconds: int = 5) -> list[dict[str, Any]]:
    return resource_startup_sources.launch_batches(processes, window_seconds=window_seconds)


def lifecycle_candidates(
    group: dict[str, Any],
    source_group: dict[str, Any],
    candidates: list[dict[str, Any]],
    root_candidates: list[dict[str, Any]],
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = effective_expected_max(group)
    return resource_process_lifecycle.lifecycle_candidates(
        group_name=str(group.get("group") or ""),
        expected=expected,
        source_group=source_group,
        root_candidates=root_candidates,
        processes=processes,
    )


def startup_governance_policy(group: dict[str, Any], parent_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return resource_startup_sources.startup_governance_policy(group, parent_rows, effective_expected_max)


def startup_sources(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or process_snapshot()
    return resource_startup_sources.build_startup_sources(snap, effective_expected_max)


def resource_metrics_base_counts(snap: dict[str, Any], groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": bool(snap.get("ok")),
        "matched_group_count": len(groups),
        "matched_process_count": sum(int(group.get("count") or 0) for group in groups),
        "root_instance_count": sum(int(group.get("root_instance_count") or group.get("count") or 0) for group in groups),
        "matched_working_set_mb": round(sum(float(group.get("working_set_mb") or 0) for group in groups), 1),
    }


def resource_metrics_owner_fields(owner: dict[str, Any]) -> dict[str, Any]:
    return {
        "codex_app_server_owner_healthy": bool(owner.get("healthy")) if owner else None,
        "codex_app_server_owner_issue": str(owner.get("issue") or "") if owner else "",
        "codex_app_server_owner_count": int(owner.get("owner_count") or 0) if owner else 0,
    }


def resource_metrics_risk_counts(groups: list[dict[str, Any]], negative_by_group: dict[str, Any]) -> dict[str, int]:
    return {
        "fanout_group_count": sum(
            1 for group in groups
            if int(group.get("root_instance_count") or group.get("count") or 0) > effective_expected_max(group)
        ),
        "dead_transport_group_count": sum(
            1
            for group in groups
            if str(group.get("group") or "") in negative_by_group
            and int(group.get("root_instance_count") or group.get("count") or 0) > 0
        ),
        "stale_bridge_host_group_count": sum(
            1 for group in groups
            if stale_bridge_host_root_pids(group)
        ),
    }


def resource_metrics_summary(
    *,
    snap: dict[str, Any],
    groups: list[dict[str, Any]],
    owner: dict[str, Any],
    negative_by_group: dict[str, Any],
) -> dict[str, Any]:
    return {
        **resource_metrics_base_counts(snap, groups),
        **resource_metrics_owner_fields(owner),
        **resource_metrics_risk_counts(groups, negative_by_group),
    }


def resource_metrics_group_rows(
    *,
    groups: list[dict[str, Any]],
    negative_by_group: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "group": group.get("group"),
            "count": group.get("count"),
            "root_instance_count": group.get("root_instance_count"),
            "expected_max": group.get("expected_max"),
            "effective_expected_max": effective_expected_max(group),
            "host_root_counts": group.get("host_root_counts"),
            "working_set_mb": group.get("working_set_mb"),
            "protected": group.get("protected"),
            "current_turn_negative": negative_by_group.get(str(group.get("group") or "")),
            "stale_bridge_host_root_pids": stale_bridge_host_root_pids(group),
        }
        for group in groups
    ]


def metrics(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or process_snapshot()
    groups = snap.get("groups") if isinstance(snap.get("groups"), list) else []
    owner = snap.get("codex_app_server_owner") if isinstance(snap.get("codex_app_server_owner"), dict) else {}
    anchor = thread_freshness_anchor(None)
    anchor_at = parse_iso_datetime(anchor.get("anchor_at")) if isinstance(anchor, dict) else None
    current_turn_observations = current_turn_tool_observations(anchor_at=anchor_at)
    negative_by_group = (
        current_turn_observations.get("negative_by_group")
        if isinstance(current_turn_observations.get("negative_by_group"), dict)
        else {}
    )
    summary = resource_metrics_summary(
        snap=snap,
        groups=groups,
        owner=owner,
        negative_by_group=negative_by_group,
    )
    return {
        "schema": "resource_process.metrics.v1",
        "ok": summary["ok"],
        "generated_at": now_iso(),
        "matched_group_count": summary["matched_group_count"],
        "matched_process_count": summary["matched_process_count"],
        "root_instance_count": summary["root_instance_count"],
        "matched_working_set_mb": summary["matched_working_set_mb"],
        "codex_app_server_owner_healthy": summary["codex_app_server_owner_healthy"],
        "codex_app_server_owner_issue": summary["codex_app_server_owner_issue"],
        "codex_app_server_owner_count": summary["codex_app_server_owner_count"],
        "fanout_group_count": summary["fanout_group_count"],
        "dead_transport_group_count": summary["dead_transport_group_count"],
        "stale_bridge_host_group_count": summary["stale_bridge_host_group_count"],
        "current_turn_observation_state": current_turn_observations.get("state"),
        "current_turn_anchor": anchor,
        "groups": resource_metrics_group_rows(groups=groups, negative_by_group=negative_by_group),
    }


def validate(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snapshot or process_snapshot()
    doc = doctor(snap)
    failures = [
        issue
        for issue in doc.get("issues", [])
        if issue.get("severity") in {"blocker", "risk"}
    ]
    return {
        "schema": "resource_process.validate.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "failures": failures,
        "advisory_count": sum(1 for issue in doc.get("issues", []) if issue.get("severity") == "advisory"),
        "reporting_safety_contract": process_reporting_safety_contract(),
        "note": "Validation reports risk-level resource fanout and stale session-owned MCP roots only; it does not stop processes.",
    }


def stop_process_tree(pid: Any, *, apply: bool) -> dict[str, Any]:
    try:
        numeric_pid = int(pid)
    except Exception:
        return {"ok": False, "pid": pid, "reason": "invalid_pid"}
    if not apply:
        return {
            "ok": True,
            "dry_run": True,
            "pid": numeric_pid,
            "would_run": ["taskkill.exe", "/PID", str(numeric_pid), "/T", "/F"],
        }
    proc = subprocess.run(
        ["taskkill.exe", "/PID", str(numeric_pid), "/T", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        **NO_WINDOW_KW,
    )
    stdout = (proc.stdout or "").strip()[:2000]
    stderr = (proc.stderr or "").strip()[:2000]
    combined_output = f"{stdout}\n{stderr}".lower()
    already_gone_markers = (
        "not found",
        "no running instance",
        "没有找到",
        "找不到",
        "û���ҵ",
    )
    already_gone = proc.returncode != 0 and any(marker in combined_output for marker in already_gone_markers)
    return {
        "ok": proc.returncode == 0 or already_gone,
        "dry_run": False,
        "pid": numeric_pid,
        "returncode": proc.returncode,
        "already_gone": already_gone,
        "stdout": stdout,
        "stderr": stderr,
    }


def protected_cleanup_evidence(action: dict[str, Any], pid: Any, proc: dict[str, Any]) -> dict[str, Any]:
    group = str(action.get("group") or "")
    keep_roots = set(action.get("would_keep_root_instance_pids") or [])
    stop_roots = set(action.get("would_review_stop_root_instance_pids") or [])
    orphan_batches = action.get("orphan_batch_candidates") if isinstance(action.get("orphan_batch_candidates"), list) else []
    kept_batches = action.get("latest_batches_kept") if isinstance(action.get("latest_batches_kept"), list) else []
    orphan_batch_has_pid = any(pid in set(batch.get("pids") or []) for batch in orphan_batches)
    keep_batch_has_pid = any(pid in set(batch.get("pids") or []) for batch in kept_batches)
    parent_ids = {
        batch.get("parent_pid")
        for batch in [*orphan_batches, *kept_batches]
        if batch.get("parent_pid") is not None
    }
    checks = {
        "protected": bool(action.get("protected")),
        "same_group": proc.get("group") == group,
        "instance_root": bool(proc.get("instance_root")),
        "pid_is_orphan_root": pid in stop_roots,
        "pid_not_kept_root": pid not in keep_roots,
        "pid_in_orphan_batch": orphan_batch_has_pid,
        "pid_not_in_latest_batch": not keep_batch_has_pid,
        "has_latest_batch": bool(kept_batches),
        "same_parent_launch_batches": len(parent_ids) == 1,
        "orphaned_host_chain": bool(proc.get("host_parent_chain_orphaned")),
    }
    required_checks = (
        "protected",
        "same_group",
        "instance_root",
        "pid_is_orphan_root",
        "pid_not_kept_root",
        "pid_in_orphan_batch",
        "pid_not_in_latest_batch",
        "has_latest_batch",
    )
    parent_evidence_ok = checks["same_parent_launch_batches"] or checks["orphaned_host_chain"]
    ok = all(checks[key] for key in required_checks) and parent_evidence_ok
    return {
        "ok": ok,
        "mode": "auto_safe_protected" if ok else "protected_review_required",
        "checks": checks,
        "reason": "protected orphan root is outside the latest kept launch batch and has a same-parent or broken-host-chain ownership proof"
        if ok else "protected orphan evidence is incomplete; skip automatic cleanup",
    }


def session_owned_stdio_cleanup_evidence(action: dict[str, Any], pid: Any, proc: dict[str, Any]) -> dict[str, Any]:
    """Return whether a session-owned stdio root is an older launch-batch orphan.

    This is deliberately narrower than a generic duplicate-process rule. Stdio
    MCP roots cannot be reused across Codex sessions, but the newest launch
    batch is the safest proxy for the active session when current-turn tool
    evidence is absent. Safe cleanup may remove only roots that the repair plan
    already marked as old-batch candidates and that are outside the latest kept
    batch.
    """

    orphan_batches = action.get("orphan_batch_candidates") if isinstance(action.get("orphan_batch_candidates"), list) else []
    kept_batches = action.get("latest_batches_kept") if isinstance(action.get("latest_batches_kept"), list) else []
    stop_roots = set(action.get("would_review_stop_root_instance_pids") or [])
    keep_roots = set(action.get("would_keep_root_instance_pids") or [])
    orphan_batch_has_pid = any(pid in set(batch.get("pids") or []) for batch in orphan_batches)
    keep_batch_has_pid = any(pid in set(batch.get("pids") or []) for batch in kept_batches)
    checks = {
        "protected": not bool(action.get("protected")),
        "same_group": proc.get("group") == action.get("group"),
        "instance_root": bool(proc.get("instance_root")),
        "pid_is_orphan_root": pid in stop_roots,
        "pid_not_kept_root": pid not in keep_roots,
        "pid_in_orphan_batch": orphan_batch_has_pid,
        "pid_not_in_latest_batch": not keep_batch_has_pid,
        "has_latest_batch": bool(kept_batches),
        "orphaned_host_chain": bool(proc.get("host_parent_chain_orphaned")),
    }
    identity_keys = (
        "protected",
        "same_group",
        "instance_root",
        "pid_is_orphan_root",
        "pid_not_kept_root",
        "pid_in_orphan_batch",
        "pid_not_in_latest_batch",
    )
    launch_batch_proof = checks["has_latest_batch"]
    ok = all(checks[key] for key in identity_keys) and (launch_batch_proof or checks["orphaned_host_chain"])
    return {
        "ok": ok,
        "mode": "safe_apply_session_owned_stdio_old_batch" if ok else "session_owned_stdio_review_required",
        "checks": checks,
        "reason": "session-owned stdio root is an older launch-batch orphan outside the latest kept batch"
        if ok else "session-owned stdio orphan evidence is incomplete; keep process",
    }


def cleanup_candidate_age_or_identity_skip(
    *,
    group: str,
    pid: Any,
    proc: dict[str, Any] | None,
    now_for_age: datetime,
    min_age_minutes: float,
) -> tuple[float | None, dict[str, Any] | None]:
    if not proc:
        return None, {"group": group, "pid": pid, "reason": "pid_not_found_in_fresh_snapshot"}
    if proc.get("group") != group or not proc.get("instance_root"):
        return None, {
            "group": group,
            "pid": pid,
            "reason": "fresh_snapshot_identity_mismatch",
            "actual_group": proc.get("group"),
            "actual_instance_root": bool(proc.get("instance_root")),
        }
    age_minutes = process_age_minutes(proc, now=now_for_age)
    if age_minutes is None:
        return None, {"group": group, "pid": pid, "reason": "start_time_unparseable_for_age_gate"}
    if age_minutes < float(min_age_minutes):
        return None, {
            "group": group,
            "pid": pid,
            "reason": "candidate_too_young",
            "age_minutes": round(age_minutes, 1),
            "min_age_minutes": float(min_age_minutes),
        }
    return age_minutes, None


def session_stdio_cleanup_required(*, safe_apply: bool, group: str) -> bool:
    return bool(safe_apply and group in SESSION_OWNED_STDIO_GROUPS)


def session_stdio_positive_skip(
    *,
    group: str,
    pid: Any,
    positive_by_group: dict[str, Any],
) -> dict[str, Any] | None:
    if group not in positive_by_group:
        return None
    return {
        "group": group,
        "pid": pid,
        "reason": "safe_apply_current_turn_positive_evidence",
        "current_turn_positive": positive_by_group.get(group),
    }


def session_stdio_old_batch_evidence_skip(
    *,
    action: dict[str, Any],
    group: str,
    pid: Any,
    proc: dict[str, Any],
    negative_by_group: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if group in negative_by_group:
        return None, None
    session_safety_evidence = session_owned_stdio_cleanup_evidence(action, pid, proc)
    if session_safety_evidence.get("ok"):
        return session_safety_evidence, None
    return session_safety_evidence, {
        "group": group,
        "pid": pid,
        "reason": "safe_apply_requires_current_turn_negative_or_old_batch_evidence_for_session_owned_stdio",
        "safe_apply_policy": "session-owned stdio MCP roots may be auto-stopped only with fresh current-turn negative evidence or repair-plan old-batch evidence outside the latest kept batch",
        "safety_evidence": session_safety_evidence,
    }


def session_stdio_negative_freshness_skip(
    *,
    group: str,
    pid: Any,
    proc: dict[str, Any],
    negative_by_group: dict[str, Any],
    session_safety_evidence: dict[str, Any] | None,
) -> dict[str, Any] | None:
    negative_at = parse_iso_datetime((negative_by_group.get(group) or {}).get("recorded_at"))
    if not negative_at:
        return None
    proc_started = parse_process_start_time(proc.get("start_time"))
    if proc_started and negative_at >= proc_started.astimezone(negative_at.tzinfo or timezone.utc):
        return None
    return {
        "group": group,
        "pid": pid,
        "reason": "safe_apply_negative_evidence_not_after_candidate_start",
        "candidate_start_time": proc.get("start_time"),
        "current_turn_negative": negative_by_group.get(group),
    }


def session_stdio_cleanup_skip(
    *,
    action: dict[str, Any],
    group: str,
    pid: Any,
    proc: dict[str, Any],
    safe_apply: bool,
    positive_by_group: dict[str, Any],
    negative_by_group: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not session_stdio_cleanup_required(safe_apply=safe_apply, group=group):
        return None, None
    positive_skip = session_stdio_positive_skip(group=group, pid=pid, positive_by_group=positive_by_group)
    if positive_skip:
        return None, positive_skip
    session_safety_evidence, old_batch_skip = session_stdio_old_batch_evidence_skip(
        action=action,
        group=group,
        pid=pid,
        proc=proc,
        negative_by_group=negative_by_group,
    )
    if old_batch_skip:
        return session_safety_evidence, old_batch_skip
    freshness_skip = session_stdio_negative_freshness_skip(
        group=group,
        pid=pid,
        proc=proc,
        negative_by_group=negative_by_group,
        session_safety_evidence=session_safety_evidence,
    )
    if freshness_skip:
        return session_safety_evidence, freshness_skip
    return session_safety_evidence, None


def protected_cleanup_skip(
    *,
    action: dict[str, Any],
    group: str,
    pid: Any,
    proc: dict[str, Any],
    include_protected: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not action.get("protected"):
        return None, None
    if group == "codex_app_live_watch":
        safety_evidence = {
            "ok": True,
            "reason": "duplicate_dashboard_observer_same_port_output",
            "policy": action.get("lifecycle_policy"),
        }
    else:
        safety_evidence = protected_cleanup_evidence(action, pid, proc)
    if not include_protected and not safety_evidence.get("ok"):
        return safety_evidence, {
            "group": group,
            "pid": pid,
            "reason": "protected_auto_safe_evidence_incomplete",
            "safety_evidence": safety_evidence,
        }
    return safety_evidence, None


def selected_cleanup_candidate(
    *,
    action: dict[str, Any],
    group: str,
    pid: Any,
    proc: dict[str, Any],
    age_minutes: float,
    include_protected: bool,
    safe_apply: bool,
    safety_evidence: dict[str, Any] | None,
    session_safety_evidence: dict[str, Any] | None,
    negative_by_group: dict[str, Any],
) -> dict[str, Any]:
    return {
        "group": group,
        "pid": pid,
        "name": proc.get("name"),
        "start_time": proc.get("start_time"),
        "age_minutes": round(age_minutes, 1),
        "command_line": proc.get("command_line"),
        "protected": bool(action.get("protected")),
        "selection_mode": (
            "manual_include_protected"
            if action.get("protected") and include_protected
            else "auto_safe_protected"
            if action.get("protected")
            else "safe_apply_session_owned_stdio_orphaned_host_chain"
            if safe_apply and group in SESSION_OWNED_STDIO_GROUPS and bool((session_safety_evidence or {}).get("checks", {}).get("orphaned_host_chain"))
            else "safe_apply_session_owned_stdio_with_negative_evidence"
            if safe_apply and group in SESSION_OWNED_STDIO_GROUPS
            else "normal_orphan"
        ),
        "safety_evidence": safety_evidence or session_safety_evidence,
        "current_turn_negative": negative_by_group.get(group),
    }


def evaluate_cleanup_candidate(
    *,
    action: dict[str, Any],
    group: str,
    pid: Any,
    process_by_pid: dict[Any, dict[str, Any]],
    now_for_age: datetime,
    min_age_minutes: float,
    safe_apply: bool,
    include_protected: bool,
    positive_by_group: dict[str, Any],
    negative_by_group: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Evaluate one cleanup root and return either a selected item or skip reason."""
    proc = process_by_pid.get(pid)
    age_minutes, skip = cleanup_candidate_age_or_identity_skip(
        group=group,
        pid=pid,
        proc=proc,
        now_for_age=now_for_age,
        min_age_minutes=min_age_minutes,
    )
    if skip:
        return None, skip
    assert proc is not None and age_minutes is not None

    session_safety_evidence, skip = session_stdio_cleanup_skip(
        action=action,
        group=group,
        pid=pid,
        proc=proc,
        safe_apply=safe_apply,
        positive_by_group=positive_by_group,
        negative_by_group=negative_by_group,
    )
    if skip:
        return None, skip

    safety_evidence, skip = protected_cleanup_skip(
        action=action,
        group=group,
        pid=pid,
        proc=proc,
        include_protected=include_protected,
    )
    if skip:
        return None, skip

    return (
        selected_cleanup_candidate(
            action=action,
            group=group,
            pid=pid,
            proc=proc,
            age_minutes=age_minutes,
            include_protected=include_protected,
            safe_apply=safe_apply,
            safety_evidence=safety_evidence,
            session_safety_evidence=session_safety_evidence,
            negative_by_group=negative_by_group,
        ),
        None,
    )


def build_cleanup_selection_context() -> dict[str, Any]:
    """Build one fresh cleanup context shared by planning, selection, and validation.

    Owns snapshot freshness and current-turn MCP evidence lookup. It does not
    stop processes; callers must keep apply/dry-run semantics outside this
    helper.
    """
    pre_snapshot = process_snapshot()
    plan = repair_plan(pre_snapshot)
    anchor = thread_freshness_anchor(None)
    anchor_at = parse_iso_datetime(anchor.get("anchor_at")) if isinstance(anchor, dict) else None
    current_turn_observations = current_turn_tool_observations(anchor_at=anchor_at)
    positive_by_group = (
        current_turn_observations.get("positive_by_group")
        if isinstance(current_turn_observations.get("positive_by_group"), dict)
        else {}
    )
    negative_by_group = (
        current_turn_observations.get("negative_by_group")
        if isinstance(current_turn_observations.get("negative_by_group"), dict)
        else {}
    )
    processes = pre_snapshot.get("processes") if isinstance(pre_snapshot.get("processes"), list) else []
    return {
        "pre_snapshot": pre_snapshot,
        "plan": plan,
        "anchor": anchor,
        "current_turn_observations": current_turn_observations,
        "positive_by_group": positive_by_group,
        "negative_by_group": negative_by_group,
        "process_by_pid": {item.get("pid"): item for item in processes},
        "now_for_age": datetime.now().astimezone(),
    }


def select_cleanup_candidates(
    *,
    plan: dict[str, Any],
    process_by_pid: dict[Any, dict[str, Any]],
    allowed_groups: set[str],
    now_for_age: datetime,
    min_age_minutes: float,
    safe_apply: bool,
    include_protected: bool,
    positive_by_group: dict[str, Any],
    negative_by_group: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select revalidated cleanup roots without executing process stops."""
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for action in plan.get("actions") or []:
        group = str(action.get("group") or "")
        if allowed_groups and group not in allowed_groups:
            skipped.append({"group": group, "reason": "group_not_selected"})
            continue
        for pid in action.get("would_review_stop_root_instance_pids") or []:
            candidate, skip = evaluate_cleanup_candidate(
                action=action,
                group=group,
                pid=pid,
                process_by_pid=process_by_pid,
                now_for_age=now_for_age,
                min_age_minutes=min_age_minutes,
                safe_apply=safe_apply,
                positive_by_group=positive_by_group,
                negative_by_group=negative_by_group,
                include_protected=include_protected,
            )
            if skip:
                skipped.append(skip)
                continue
            if candidate:
                selected.append(candidate)
    return selected, skipped


def cleanup_pre_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_count": len(plan.get("actions") or []),
        "orphan_candidate_action_count": plan.get("orphan_candidate_action_count"),
        "protected_orphan_candidate_action_count": plan.get("protected_orphan_candidate_action_count"),
        "orphan_candidate_root_count": plan.get("orphan_candidate_root_count"),
        "orphan_candidate_process_count": plan.get("orphan_candidate_process_count"),
        "latest_batch_policy": plan.get("latest_batch_policy"),
    }


def cleanup_execution_result(*, selected: list[dict[str, Any]], apply: bool) -> dict[str, Any]:
    """Run the process-stop phase after selection has already passed all gates."""
    results: list[dict[str, Any]] = []
    for item in selected:
        result = stop_process_tree(item.get("pid"), apply=apply)
        results.append({**item, "stop_result": result})
    post_snapshot = process_snapshot() if apply else None
    post_validation = validate(post_snapshot) if post_snapshot else None
    return {
        "results": results,
        "post_validation": post_validation,
        "cleanup_ok": all(bool(item.get("stop_result", {}).get("ok")) for item in results),
        "post_validation_ok": bool(post_validation.get("ok")) if isinstance(post_validation, dict) else None,
    }


def cleanup_orphan_candidates(
    *,
    apply: bool = False,
    safe_apply: bool = False,
    include_protected: bool = False,
    groups: list[str] | None = None,
    min_age_minutes: float = 15.0,
) -> dict[str, Any]:
    """Stop only fresh repair-plan orphan root candidates.

    The root PID is revalidated against the same snapshot used to build the
    plan. Descendant termination is delegated to taskkill /T so the command does
    not independently enumerate and kill unrelated process names.
    """
    allowed_groups = {str(item) for item in (groups or []) if str(item).strip()}
    context = build_cleanup_selection_context()
    selected, skipped = select_cleanup_candidates(
        plan=context["plan"],
        process_by_pid=context["process_by_pid"],
        allowed_groups=allowed_groups,
        now_for_age=context["now_for_age"],
        min_age_minutes=min_age_minutes,
        safe_apply=safe_apply,
        include_protected=include_protected,
        positive_by_group=context["positive_by_group"],
        negative_by_group=context["negative_by_group"],
    )
    execution = cleanup_execution_result(selected=selected, apply=apply)
    return {
        "schema": "resource_process.cleanup.v1",
        "ok": execution["cleanup_ok"],
        "generated_at": now_iso(),
        "apply_requested": bool(apply),
        "applied": bool(apply and selected),
        "safe_apply": bool(safe_apply),
        "include_protected": bool(include_protected),
        "min_age_minutes": float(min_age_minutes),
        "protected_auto_safe_enabled": True,
        "current_turn_observation_state": context["current_turn_observations"].get("state"),
        "current_turn_anchor": context["anchor"],
        "selected_groups": sorted(allowed_groups),
        "pre_plan_summary": cleanup_pre_plan_summary(context["plan"]),
        "dry_run_contract": {
            "kills_processes": bool(apply and selected),
            "writes_files": False,
            "starts_processes": False,
            "sends_messages": False,
            "changes_bridge_state": False,
        },
        "selected_count": len(selected),
        "skipped_count": len(skipped),
        "cleanup_ok": execution["cleanup_ok"],
        "post_validation_ok": execution["post_validation_ok"],
        "selected": selected,
        "skipped": skipped[:50],
        "results": execution["results"],
        "post_validation": execution["post_validation"],
        "note": "Default dry-run only. Safe apply never stops session-owned stdio MCP groups with fresh current-turn positive evidence; without negative evidence it may stop only repair-plan old-batch roots outside the latest kept batch.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resource/MCP process lifecycle doctor")
    parser.add_argument("command", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate", "startup-sources", "cleanup"])
    parser.add_argument("--quick", action="store_true", help="Compatibility alias for older callers. Doctor is already bounded/read-only.")
    parser.add_argument("--apply", action="store_true", help="Apply cleanup for revalidated orphan root candidates")
    parser.add_argument("--safe-apply", action="store_true", help="Apply only candidates that pass current-turn MCP safety gates")
    parser.add_argument("--include-protected", action="store_true", help="Allow cleanup candidates from protected groups")
    parser.add_argument("--group", action="append", default=[], help="Restrict cleanup to one group; can be repeated")
    parser.add_argument("--min-age-minutes", type=float, default=15.0, help="Only select orphan root candidates at least this old")
    parser.add_argument("--full", action="store_true", help="Emit the complete successful read-only result.")
    args = parser.parse_args()
    snap = process_snapshot()
    if args.command == "snapshot":
        payload = snap
    elif args.command == "doctor":
        payload = doctor(snap)
    elif args.command == "repair-plan":
        payload = repair_plan(snap)
    elif args.command == "metrics":
        payload = metrics(snap)
    elif args.command == "startup-sources":
        payload = startup_sources(snap)
    elif args.command == "cleanup":
        payload = cleanup_orphan_candidates(
            apply=bool(args.apply),
            safe_apply=bool(args.safe_apply),
            include_protected=bool(args.include_protected),
            groups=list(args.group or []),
            min_age_minutes=float(args.min_age_minutes),
        )
    else:
        payload = validate(snap)
    if args.command == "cleanup" and payload.get("ok") is True and not args.full:
        output_payload = resource_process_reporting.cleanup_success_projection(payload)
    elif args.command == "doctor" and not args.full:
        output_payload = resource_process_reporting.doctor_projection(payload)
    else:
        output_payload = payload
    output = governed_cli_payload(
        output_payload,
        full=bool(args.full),
        full_result_ref=f"command:python _bridge/resource_process_doctor.py {args.command} --full",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
