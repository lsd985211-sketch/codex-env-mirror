#!/usr/bin/env python3
"""Low-noise health checks for the OpenClaw mobile bridge."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def tail_text(path: Path, max_chars: int = 2000) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"read_error={exc}"
    return text[-max_chars:]


def run_powershell(project_root: Path, script: str, timeout: int = 20) -> dict[str, Any]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        proc = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            cwd=str(project_root),
        )
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-3000:],
        "stderr": (proc.stderr or "")[-3000:],
    }


def powershell_json(project_root: Path, script: str, timeout: int = 20) -> dict[str, Any]:
    result = run_powershell(project_root, script, timeout=timeout)
    stdout = str(result.get("stdout") or "").strip()
    if not stdout:
        return result
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return result
    if isinstance(parsed, dict):
        parsed.setdefault("ok", bool(result.get("ok")))
        parsed["_powershell_returncode"] = result.get("returncode")
        if result.get("stderr"):
            parsed["_powershell_stderr"] = result.get("stderr")
        return parsed
    return {"ok": bool(result.get("ok")), "returncode": result.get("returncode"), "result": parsed}


def tcp_check(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "host": host, "port": port}
    except Exception as exc:
        return {"ok": False, "host": host, "port": port, "reason": str(exc)}


def http_health(port: int, host: str = "127.0.0.1", timeout: float = 3.0) -> dict[str, Any]:
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "url": url, "reason": str(exc)}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "url": url, "reason": "non-json health response", "body_preview": body[:300]}
    parsed["url"] = url
    return parsed


def http_json(path: str, port: int, host: str = "127.0.0.1", timeout: float = 3.0) -> dict[str, Any]:
    url = f"http://{host}:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(8192).decode("utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "url": url, "reason": str(exc)}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "url": url, "reason": "non-json response", "body_preview": body[:300]}
    if isinstance(parsed, dict):
        parsed["ok"] = True
        parsed["url"] = url
        return parsed
    return {"ok": True, "url": url, "result": parsed}


def inspect_worker_processes(project_root: Path) -> dict[str, Any]:
    script = r"""
$targets = Get-CimInstance Win32_Process | Where-Object {
  $_.Name -in @('powershell.exe','pwsh.exe','python.exe','pythonw.exe') -and
  ($_.CommandLine -match 'mobile_openclaw_cli\.py worker-loop' -or $_.CommandLine -match 'run-worker-loop\.ps1')
} | Select-Object ProcessId,ParentProcessId,Name,CommandLine
[pscustomobject]@{ ok = $true; count = @($targets).Count; processes = $targets } | ConvertTo-Json -Depth 5
"""
    return powershell_json(project_root, script, timeout=10)


def inspect_openclaw_gateway_processes(project_root: Path) -> dict[str, Any]:
    script = r"""
$targets = Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -in @('powershell.exe','pwsh.exe') -and $_.CommandLine -match 'run-openclaw-gateway-loop\.ps1') -or
  ($_.Name -eq 'node.exe' -and $_.CommandLine -match 'openclaw\.mjs' -and $_.CommandLine -match '\bgateway\b')
} | Select-Object ProcessId,ParentProcessId,Name,CommandLine
[pscustomobject]@{ ok = (@($targets).Count -gt 0); count = @($targets).Count; processes = $targets } | ConvertTo-Json -Depth 5
"""
    return powershell_json(project_root, script, timeout=10)


def inspect_scheduled_task(project_root: Path, task_name: str) -> dict[str, Any]:
    escaped = task_name.replace("'", "''")
    script = f"""
$task = Get-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue
if ($task) {{
  $info = Get-ScheduledTaskInfo -TaskName '{escaped}' -ErrorAction SilentlyContinue
  [pscustomobject]@{{
    ok = $true
    existed = $true
    task_name = '{escaped}'
    state = [string]$task.State
    last_result = if ($info) {{ $info.LastTaskResult }} else {{ $null }}
    last_run_time = if ($info) {{ $info.LastRunTime }} else {{ $null }}
  }} | ConvertTo-Json -Depth 4
}} else {{
  [pscustomobject]@{{ ok = $false; existed = $false; task_name = '{escaped}' }} | ConvertTo-Json -Depth 4
}}
"""
    return powershell_json(project_root, script, timeout=10)


def scheduled_task_script_health(project_root: Path, task_name: str, expected_scripts: list[Path]) -> dict[str, Any]:
    escaped = task_name.replace("'", "''")
    script = f"""
$task = Get-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue
if ($task) {{
  $actions = @($task.Actions | ForEach-Object {{ [pscustomobject]@{{ Execute = $_.Execute; Arguments = $_.Arguments; WorkingDirectory = $_.WorkingDirectory }} }})
  [pscustomobject]@{{ ok = $true; existed = $true; task_name = '{escaped}'; actions = $actions }} | ConvertTo-Json -Depth 5
}} else {{
  [pscustomobject]@{{ ok = $false; existed = $false; task_name = '{escaped}' }} | ConvertTo-Json -Depth 4
}}
"""
    result = powershell_json(project_root, script, timeout=10)
    actions = result.get("actions") if isinstance(result, dict) else []
    if isinstance(actions, dict):
        actions = [actions]
    command_text = "\n".join(
        " ".join(str(action.get(field) or "") for field in ("Execute", "Arguments", "WorkingDirectory"))
        for action in actions
        if isinstance(action, dict)
    )
    expected = [str(path) for path in expected_scripts]
    result["expected_scripts"] = expected
    result["points_to_expected"] = any(path in command_text for path in expected) or any(
        path.name in command_text for path in expected_scripts
    )
    result["ok"] = bool(result.get("ok")) and bool(result.get("points_to_expected"))
    return result


def scheduled_task_action_health(project_root: Path, root: Path, task_name: str) -> dict[str, Any]:
    escaped = task_name.replace("'", "''")
    script = f"""
$task = Get-ScheduledTask -TaskName '{escaped}' -ErrorAction SilentlyContinue
if ($task) {{
  $actions = @($task.Actions | ForEach-Object {{ [pscustomobject]@{{ Execute = $_.Execute; Arguments = $_.Arguments; WorkingDirectory = $_.WorkingDirectory }} }})
  [pscustomobject]@{{ ok = $true; existed = $true; task_name = '{escaped}'; actions = $actions }} | ConvertTo-Json -Depth 5
}} else {{
  [pscustomobject]@{{ ok = $false; existed = $false; task_name = '{escaped}' }} | ConvertTo-Json -Depth 4
}}
"""
    result = powershell_json(project_root, script, timeout=10)
    actions = result.get("actions") if isinstance(result, dict) else []
    if isinstance(actions, dict):
        actions = [actions]
    expected_starter = str(root / "start-worker-hidden.ps1")
    expected_runner = str(root / "run-worker-loop.ps1")
    expected_hidden_py = str(root / "start_worker_hidden.py")
    expected_script = str(root / "mobile_openclaw_cli.py")
    command_text = "\n".join(
        " ".join(str(action.get(field) or "") for field in ("Execute", "Arguments", "WorkingDirectory"))
        for action in actions
        if isinstance(action, dict)
    )
    result["points_to_starter"] = expected_starter in command_text
    result["points_to_runner"] = expected_runner in command_text
    result["uses_pythonw"] = "pythonw.exe" in command_text.lower()
    result["points_to_hidden_python_starter"] = (
        bool(result.get("uses_pythonw"))
        and (expected_hidden_py in command_text or "start_worker_hidden.py" in command_text)
    )
    result["points_to_cli"] = expected_script in command_text or "mobile_openclaw_cli.py" in command_text
    result["ok"] = bool(result.get("ok")) and (
        bool(result.get("points_to_starter"))
        or bool(result.get("points_to_runner"))
        or bool(result.get("points_to_hidden_python_starter"))
    )
    return result


def latest_worker_stderr(root: Path) -> dict[str, Any]:
    logs_dir = root / "logs"
    logs = sorted(logs_dir.glob("worker-loop-*.stderr.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not logs:
        return {
            "ok": True,
            "state": "unknown",
            "reason": f"no worker stderr logs found in {logs_dir}",
        }
    latest = logs[0]
    stat = latest.stat()
    result = {
        "ok": stat.st_size == 0,
        "state": "clean" if stat.st_size == 0 else "has_output",
        "path": str(latest),
        "bytes": stat.st_size,
        "last_write_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }
    if stat.st_size:
        result["tail"] = tail_text(latest)
    return result


def latest_worker_log_summary(root: Path) -> dict[str, Any]:
    logs_dir = root / "logs"
    if not logs_dir.exists():
        return {"ok": False, "reason": f"logs directory not found: {logs_dir}"}
    logs = sorted(logs_dir.glob("worker-loop-*.*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    items = []
    total_bytes = 0
    for path in logs[:12]:
        stat = path.stat()
        total_bytes += stat.st_size
        items.append(
            {
                "name": path.name,
                "bytes": stat.st_size,
                "last_write_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    return {
        "ok": True,
        "log_dir": str(logs_dir),
        "recent_count": len(items),
        "recent_total_bytes": total_bytes,
        "recent": items,
    }


def path_health(path: Path, kind: str = "file", max_bytes: int | None = None) -> dict[str, Any]:
    exists = path.exists()
    result: dict[str, Any] = {"ok": exists, "path": str(path), "exists": exists}
    if not exists:
        result["reason"] = "missing"
        return result
    stat = path.stat()
    result["last_write_utc"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    if path.is_file():
        result["bytes"] = stat.st_size
    if kind == "dir":
        result["ok"] = path.is_dir()
        result["item_count"] = len(list(path.iterdir())) if path.is_dir() else None
    elif kind == "file":
        result["ok"] = path.is_file()
    if max_bytes is not None and path.is_file():
        result["under_limit"] = stat.st_size <= max_bytes
        result["ok"] = bool(result["ok"]) and bool(result["under_limit"])
    return result


def config_health(config_path: Path, config: dict[str, Any], thread_count: int) -> dict[str, Any]:
    result = path_health(config_path, "file", max_bytes=1024 * 1024)
    result["json_ok"] = bool(config)
    result["shadow_mode"] = bool(config.get("safety", {}).get("shadow_mode", True))
    result["allowed_user_count"] = len(config.get("security", {}).get("allowed_users", []))
    result["thread_count"] = thread_count
    result["has_confirmation_secret_hash"] = bool(config.get("security", {}).get("confirmation_secret_hash"))
    result["risk_rules_path"] = str(config.get("safety", {}).get("risk_rules_path") or "")
    return result


def sqlite_health(db_path: Path) -> dict[str, Any]:
    result = path_health(db_path, "file", max_bytes=64 * 1024 * 1024)
    if not db_path.exists():
        return result
    try:
        with sqlite3.connect(db_path, timeout=5) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            tables = [
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
            ]
            wal_mode = db.execute("PRAGMA journal_mode").fetchone()
        result["integrity_check"] = integrity[0] if integrity else "unknown"
        result["journal_mode"] = wal_mode[0] if wal_mode else "unknown"
        result["tables"] = tables
        result["ok"] = bool(result["ok"]) and result["integrity_check"] == "ok"
    except Exception as exc:
        result["ok"] = False
        result["reason"] = str(exc)
    return result


def codex_logs_sqlite_health(
    codex_home: Path | None = None,
    observe_seconds: float = 0.0,
) -> dict[str, Any]:
    """Bounded health check for Codex local SQLite log sinks.

    This intentionally checks only known Codex log database paths. Do not
    replace it with a recursive user-profile scan; logs_2.sqlite investigations
    previously produced noisy, slow, and SSD-hostile broad scans.
    """

    home = codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    paths = [
        home / "logs_2.sqlite",
        home / "sqlite" / "logs_2.sqlite",
    ]

    def inspect(path: Path) -> dict[str, Any]:
        item: dict[str, Any] = {
            "path": str(path),
            "exists": path.exists(),
            "scope": "allowlisted_codex_log_db",
        }
        if not path.exists():
            return item
        stat = path.stat()
        wal = Path(str(path) + "-wal")
        shm = Path(str(path) + "-shm")
        item.update(
            {
                "bytes": stat.st_size,
                "last_write_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "wal_exists": wal.exists(),
                "wal_bytes": wal.stat().st_size if wal.exists() else 0,
                "shm_exists": shm.exists(),
                "shm_bytes": shm.stat().st_size if shm.exists() else 0,
            }
        )
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as db:
                db.row_factory = sqlite3.Row
                item["max_id"] = db.execute("SELECT max(id) FROM logs").fetchone()[0]
                item["row_count"] = db.execute("SELECT count(*) FROM logs").fetchone()[0]
                item["levels"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT level, count(*) AS count FROM logs GROUP BY level ORDER BY count DESC LIMIT 8"
                    ).fetchall()
                ]
                item["recent_targets"] = [
                    dict(row)
                    for row in db.execute(
                        """
                        SELECT level, target, count(*) AS count
                        FROM logs
                        WHERE id > ?
                        GROUP BY level, target
                        ORDER BY count DESC
                        LIMIT 12
                        """,
                        (max(0, int(item.get("max_id") or 0) - 5000),),
                    ).fetchall()
                ]
                item["triggers"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
                    ).fetchall()
                ]
        except Exception as exc:
            item["ok"] = False
            item["reason"] = str(exc)
            return item
        item["ok"] = True
        return item

    before = [inspect(path) for path in paths]
    after: list[dict[str, Any]] | None = None
    seconds = max(0.0, float(observe_seconds or 0.0))
    if seconds:
        time.sleep(min(seconds, 60.0))
        after = [inspect(path) for path in paths]

    by_path = {item["path"]: item for item in before}
    observations: list[dict[str, Any]] = []
    if after is not None:
        for item in after:
            previous = by_path.get(item["path"]) or {}
            observations.append(
                {
                    "path": item["path"],
                    "delta_max_id": int(item.get("max_id") or 0) - int(previous.get("max_id") or 0),
                    "delta_row_count": int(item.get("row_count") or 0) - int(previous.get("row_count") or 0),
                    "delta_bytes": int(item.get("bytes") or 0) - int(previous.get("bytes") or 0),
                    "delta_wal_bytes": int(item.get("wal_bytes") or 0) - int(previous.get("wal_bytes") or 0),
                }
            )

    return {
        "ok": all(bool(item.get("ok", True)) for item in (after or before)),
        "policy": {
            "scan_scope": "bounded_allowlist_only",
            "allowlisted_paths": [str(path) for path in paths],
            "broad_recursive_scan": "forbidden",
        },
        "observe_seconds": seconds,
        "before": before,
        "after": after,
        "observations": observations,
    }


def attachments_health(attachments_dir: Path) -> dict[str, Any]:
    attachments_dir.mkdir(parents=True, exist_ok=True)
    result = path_health(attachments_dir, "dir")
    probe = attachments_dir / ".write-probe"
    try:
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        result["writable"] = True
    except Exception as exc:
        result["writable"] = False
        result["write_error"] = str(exc)
    result["ok"] = bool(result["ok"]) and bool(result["writable"])
    return result
