#!/usr/bin/env python3
"""Classify likely console popup sources without changing process state.

The goal is attribution, not suppression.  Blue PowerShell windows and black
console windows can come from different owners: Codex shell tools, MCP
descendants, scheduled tasks, or unrelated user processes.  This doctor keeps
those layers separate so fixes do not weaken MCP functionality.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
WATCH_NAMES = {"powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(value: Any, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def run_powershell_json(script: str, timeout: int = 20) -> dict[str, Any]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        proc = subprocess.run(
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
    raw = (proc.stdout or "").strip()
    try:
        parsed = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout_preview": raw[:2000],
            "stderr_preview": (proc.stderr or "")[:2000],
            "reason": "powershell_json_parse_failed",
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "items": parsed if isinstance(parsed, list) else [parsed],
        "stderr_preview": (proc.stderr or "")[:2000],
    }


def process_rows() -> dict[str, Any]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$all = @{}
foreach ($p in Get-CimInstance Win32_Process) { $all[[int]$p.ProcessId] = $p }
$rows = foreach ($proc in $all.Values) {
  $parent = $all[[int]$proc.ParentProcessId]
  [pscustomobject]@{
    pid = [int]$proc.ProcessId
    parent_pid = [int]$proc.ParentProcessId
    parent_name = if ($parent) { [string]$parent.Name } else { '' }
    parent_command_line = if ($parent) { [string]$parent.CommandLine } else { '' }
    name = [string]$proc.Name
    command_line = [string]$proc.CommandLine
    creation_date = [string]$proc.CreationDate
  }
}
$rows | ConvertTo-Json -Depth 4
"""
    observed = run_powershell_json(script)
    if not observed.get("ok"):
        return observed
    rows = observed.get("items") if isinstance(observed.get("items"), list) else []
    by_pid = {item.get("pid"): item for item in rows if isinstance(item, dict)}
    return {"ok": True, "rows": rows, "by_pid": by_pid}


def parent_chain(item: dict[str, Any], by_pid: dict[Any, dict[str, Any]], max_depth: int = 8) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current = item
    seen: set[Any] = set()
    for _ in range(max_depth):
        parent_pid = current.get("parent_pid")
        if parent_pid is None or parent_pid in seen:
            break
        seen.add(parent_pid)
        parent = by_pid.get(parent_pid)
        if not parent:
            break
        chain.append(
            {
                "pid": parent.get("pid"),
                "name": parent.get("name"),
                "command_line": normalize(parent.get("command_line")),
            }
        )
        current = parent
    return chain


def classify(item: dict[str, Any], chain: list[dict[str, Any]]) -> str:
    text = "\n".join(
        [
            normalize(item.get("name")),
            normalize(item.get("command_line")),
            *[normalize(parent.get("name")) + " " + normalize(parent.get("command_line")) for parent in chain],
        ]
    ).lower()
    parent_text = "\n".join(
        [normalize(parent.get("name")) + " " + normalize(parent.get("command_line")) for parent in chain]
    ).lower()
    own_text = f"{item.get('name', '')} {item.get('command_line', '')}".lower()
    if "taskeng.exe" in parent_text or "taskhostw.exe" in parent_text or "schtasks" in text:
        return "scheduled_task"
    if "openai.codex_" in parent_text or "codex.exe" in parent_text or "codex.exe" in own_text:
        if any(marker in text for marker in (
            "mcp_profile_launcher.py",
            "mcp_launch_guard.py",
            "server-filesystem",
            "chrome-devtools-mcp",
            "playwright-mcp",
            "markitdown-mcp",
            "myskills-mcp",
            "custom_slash_commands_mcp.py",
            "sqlite_mcp_server.py",
            "pmb.exe mcp proxy",
            "codegraph",
        )):
            return "mcp_descendant"
        return "codex_shell_tool"
    if any(marker in text for marker in ("mobile_openclaw", "openclaw", "local_mcp_hub.py", "codex_scheduler_runner.py")):
        return "workspace_service"
    if any(marker in text for marker in ("mcp", "pmb.exe", "node_repl", "gui_automation_mcp.py")):
        return "mcp_descendant"
    return "unknown_or_external"


def snapshot() -> dict[str, Any]:
    observed = process_rows()
    if not observed.get("ok"):
        return {"schema": "popup_window.snapshot.v1", "ok": False, "generated_at": now_iso(), "error": observed}
    rows = observed.get("rows") if isinstance(observed.get("rows"), list) else []
    by_pid = observed.get("by_pid") if isinstance(observed.get("by_pid"), dict) else {}
    watched: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        if name not in WATCH_NAMES:
            continue
        chain = parent_chain(item, by_pid)
        watched.append(
            {
                "pid": item.get("pid"),
                "name": item.get("name"),
                "parent_pid": item.get("parent_pid"),
                "parent_name": item.get("parent_name"),
                "classification": classify(item, chain),
                "command_line": normalize(item.get("command_line")),
                "parent_chain": chain,
            }
        )
    counts = Counter(str(item.get("classification")) for item in watched)
    return {
        "schema": "popup_window.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "watched_process_count": len(watched),
        "classification_counts": dict(sorted(counts.items())),
        "processes": watched,
        "dry_run_contract": {
            "writes_files": False,
            "kills_processes": False,
            "starts_services": False,
            "changes_mcp_config": False,
        },
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    issues: list[dict[str, Any]] = []
    counts = snap.get("classification_counts") if isinstance(snap.get("classification_counts"), dict) else {}
    mcp_count = int(counts.get("mcp_descendant") or 0)
    codex_count = int(counts.get("codex_shell_tool") or 0)
    unknown_count = int(counts.get("unknown_or_external") or 0)
    if codex_count:
        issues.append(
            {
                "severity": "advisory",
                "code": "codex_shell_tool_console_processes",
                "count": codex_count,
                "meaning": "Codex built-in shell/tool commands can create transient PowerShell console windows; workspace code can reduce its own launches but cannot fully rewrite this app behavior.",
            }
        )
    if mcp_count:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_descendant_console_processes",
                "count": mcp_count,
                "meaning": "MCP descendants are using console-capable processes; prefer mcp_profile_launcher/mcp_launch_guard no-window launches and avoid .cmd shims where verified safe.",
            }
        )
    if unknown_count:
        issues.append(
            {
                "severity": "advisory",
                "code": "unclassified_console_processes",
                "count": unknown_count,
                "meaning": "Some console processes are not attributable to Codex/MCP from the current parent chain; inspect before changing startup policy.",
            }
        )
    return {
        "schema": "popup_window.doctor.v1",
        "ok": bool(snap.get("ok")),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": {
            "watched_process_count": snap.get("watched_process_count"),
            "classification_counts": counts,
        },
        "recommended_policy": [
            "Keep native MCP functionality; reduce windows through no-window process creation where the owning launcher is local.",
            "Do not disable MCPs or scheduled tasks merely to hide popups.",
            "Treat Codex built-in shell popups as app-level behavior; prefer MCP/Hub/project CLIs for repeated checks when they are current-turn usable.",
        ],
    }


def validate(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    doc = doctor(snap)
    blockers = [issue for issue in doc.get("issues", []) if issue.get("severity") == "blocker"]
    return {
        "schema": "popup_window.validate.v1",
        "ok": bool(snap.get("ok")) and not blockers,
        "generated_at": now_iso(),
        "blockers": blockers,
        "risk_count": sum(1 for issue in doc.get("issues", []) if issue.get("severity") == "risk"),
        "advisory_count": sum(1 for issue in doc.get("issues", []) if issue.get("severity") == "advisory"),
        "note": "Read-only validation. Risk means popup sources are observable, not that functionality is broken.",
    }


def metrics(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    counts = snap.get("classification_counts") if isinstance(snap.get("classification_counts"), dict) else {}
    return {
        "schema": "popup_window.metrics.v1",
        "ok": bool(snap.get("ok")),
        "generated_at": now_iso(),
        "watched_process_count": snap.get("watched_process_count"),
        "classification_counts": counts,
        "mcp_descendant_count": int(counts.get("mcp_descendant") or 0),
        "codex_shell_tool_count": int(counts.get("codex_shell_tool") or 0),
        "scheduled_task_count": int(counts.get("scheduled_task") or 0),
        "unknown_or_external_count": int(counts.get("unknown_or_external") or 0),
    }


def observe(seconds: int) -> dict[str, Any]:
    seconds = max(1, min(int(seconds), 120))
    first = snapshot()
    time.sleep(seconds)
    second = snapshot()
    first_pids = {
        item.get("pid")
        for item in first.get("processes", [])
        if isinstance(item, dict)
    }
    new_processes = [
        item
        for item in second.get("processes", [])
        if isinstance(item, dict) and item.get("pid") not in first_pids
    ]
    counts = Counter(str(item.get("classification")) for item in new_processes)
    return {
        "schema": "popup_window.observe.v1",
        "ok": bool(first.get("ok")) and bool(second.get("ok")),
        "generated_at": now_iso(),
        "observe_seconds": seconds,
        "new_watched_process_count": len(new_processes),
        "new_classification_counts": dict(sorted(counts.items())),
        "new_processes": new_processes,
        "dry_run_contract": {
            "writes_files": False,
            "kills_processes": False,
            "starts_services": False,
            "changes_mcp_config": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Popup window attribution doctor")
    parser.add_argument("command", choices=["snapshot", "doctor", "validate", "metrics", "observe"])
    parser.add_argument("--seconds", type=int, default=10)
    args = parser.parse_args(argv)
    snap = None if args.command == "observe" else snapshot()
    if args.command == "snapshot":
        payload = snap
    elif args.command == "doctor":
        payload = doctor(snap)
    elif args.command == "validate":
        payload = validate(snap)
    elif args.command == "metrics":
        payload = metrics(snap)
    else:
        payload = observe(args.seconds)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
