#!/usr/bin/env python3
"""Typed Windows execution-plane agent owned by the WSL control plane.

Ownership: fixed scheduled-task inventory, least-privilege lane validation,
typed task invocation, and bounded execution receipts.
Non-goals: arbitrary shell execution, task creation, permission escalation,
Windows SYSTEM services, business-owner replacement, or reverse writes into
Work Git.
State behavior: status and validation are read-only; invoke requires the exact
per-operation confirmation and can only start a catalogued existing task.
Caller context: ``wsl_workspace_owner`` is the lifecycle facade. Individual
Windows business owners retain the operation contract and result acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "windows_execution_agent.v1"
CONFIRM_PREFIX = "RUN-WINDOWS-EXECUTION"

# This is the single machine-readable authority for the cross-platform task
# lanes. Installers remain owned by their business modules; validation catches
# any installed definition that drifts from this boundary.
TASK_POLICIES: dict[str, dict[str, Any]] = {
    "CodexLocalMcpHub": {
        "owner": "local_mcp_hub",
        "lane": "standard_user",
        "run_level": "Limited",
        "required": True,
        "action_marker": "run-local-mcp-hub.ps1",
        "operations": ["local_mcp_hub.start"],
    },
    "CodexConfigGuard": {
        "owner": "codex_config_guard",
        "lane": "standard_user",
        "run_level": "Limited",
        "required": True,
        "action_marker": "codex_config_guard.py",
        "operations": ["config_guard.run"],
    },
    "CodexModelProviderWatcher": {
        "owner": "codex_model_provider_watcher",
        "lane": "standard_user",
        "run_level": "Limited",
        "required": True,
        "action_marker": "codex_model_provider_watcher.py",
        "operations": ["model_provider_watcher.start"],
    },
    "MobileOpenClawBridgeWorker": {
        "owner": "mobile_openclaw_bridge",
        "lane": "standard_user",
        "run_level": "Limited",
        "required": False,
        "action_marker": "mobile_openclaw",
        "operations": [],
    },
    "OpenClawGatewayWorker": {
        "owner": "mobile_openclaw_bridge",
        "lane": "standard_user",
        "run_level": "Limited",
        "required": False,
        "action_marker": "openclaw",
        "operations": [],
    },
    "CodexSchedulerRunner": {
        "owner": "maintenance_scheduler_service",
        "lane": "standard_user",
        "run_level": "Limited",
        "required": True,
        "action_marker": "codex-maintenance-scheduler.service",
        "operations": ["wsl_control_plane.wake"],
    },
    "CodexDesktopElevatedAtLogon": {
        "owner": "codex_desktop_launcher",
        "lane": "approved_elevated",
        "run_level": "Highest",
        "required": True,
        "action_marker": "start-codex-desktop-elevated.ps1",
        "operations": ["desktop.start_elevated"],
        "elevation_reason": "explicit user-approved administrator desktop lane",
    },
    "Codex-KernelPool-Governance": {
        "owner": "windows_kernel_pool_governance",
        "lane": "approved_elevated",
        "run_level": "Highest",
        "required": False,
        "action_marker": "windows_kernel_pool_diagnostics.py",
        "operations": [],
        "elevation_reason": "administrator-owned kernel diagnostics",
    },
}

INVENTORY_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$names=@(__TASK_NAMES__)
$rows=@()
foreach($name in $names){
  $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if($null -eq $task){continue}
  $info=Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue
  $rows += [pscustomobject]@{
    task_name=[string]$task.TaskName
    state=[string]$task.State
    user_id=[string]$task.Principal.UserId
    logon_type=[string]$task.Principal.LogonType
    run_level=[string]$task.Principal.RunLevel
    execute=[string]$task.Actions.Execute
    arguments=[string]$task.Actions.Arguments
    last_result=if($null -ne $info){[int64]$info.LastTaskResult}else{$null}
    last_run=if($null -ne $info -and $info.LastRunTime -gt [datetime]::MinValue){$info.LastRunTime.ToUniversalTime().ToString('o')}else{''}
    next_run=if($null -ne $info -and $info.NextRunTime -gt [datetime]::MinValue){$info.NextRunTime.ToUniversalTime().ToString('o')}else{''}
  }
}
@($rows) | ConvertTo-Json -Compress -Depth 5
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _windows_system32(name: str) -> Path:
    if os.name == "nt":
        return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name
    return Path("/mnt/c/Windows/System32") / name


def powershell_path() -> Path:
    return _windows_system32("WindowsPowerShell/v1.0/powershell.exe")


def schtasks_path() -> Path:
    return _windows_system32("schtasks.exe")


def _run(argv: list[str], *, timeout: int = 30, env: dict[str, str] | None = None) -> dict[str, Any]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, timeout),
            check=False,
            env=merged_env,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip()[:16000],
        "stderr": completed.stderr.strip()[:4000],
    }


def _task_inventory() -> dict[str, Any]:
    powershell = powershell_path()
    if not powershell.is_file():
        return {"ok": False, "reason": "windows_powershell_unavailable", "tasks": []}
    task_literals = ",".join(f"'{name.replace(chr(39), chr(39) * 2)}'" for name in TASK_POLICIES)
    script = INVENTORY_SCRIPT.replace("__TASK_NAMES__", task_literals)
    result = _run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=30,
    )
    if not result.get("ok"):
        return {"ok": False, "reason": "scheduled_task_inventory_failed", "tasks": [], "detail": result.get("stderr", "")}
    try:
        payload = json.loads(str(result.get("stdout") or "[]"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": "scheduled_task_inventory_invalid_json", "tasks": [], "detail": str(exc)}
    if isinstance(payload, dict):
        payload = [payload]
    tasks = payload if isinstance(payload, list) else []
    return {"ok": True, "reason": "inventory_complete", "tasks": tasks}


def capabilities() -> dict[str, Any]:
    operations = []
    for task_name, policy in TASK_POLICIES.items():
        for operation in policy.get("operations", []):
            operations.append(
                {
                    "operation": operation,
                    "task_name": task_name,
                    "owner": policy["owner"],
                    "lane": policy["lane"],
                    "confirmation": f"{CONFIRM_PREFIX}:{operation}",
                }
            )
    return {
        "schema": f"{SCHEMA}.capabilities",
        "ok": True,
        "generated_at": now_iso(),
        "operations": operations,
        "boundaries": {
            "arbitrary_command": False,
            "arbitrary_arguments": False,
            "task_creation": False,
            "permission_escalation": False,
            "system_account": False,
            "reverse_work_git_write": False,
            "periodic_execution_owner": "codex_scheduler_runner",
            "second_scheduler_created": False,
        },
    }


def snapshot() -> dict[str, Any]:
    inventory = _task_inventory()
    tasks = inventory.get("tasks", []) if isinstance(inventory.get("tasks"), list) else []
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": bool(inventory.get("ok")),
        "generated_at": now_iso(),
        "control_plane": "wsl_work_git_and_systemd",
        "execution_plane": "windows_user_session_scheduled_tasks",
        "transport": "one_shot_windows_interop",
        "resident_privileged_endpoint": False,
        "inventory": inventory,
        "task_count": len(tasks),
        "capabilities": capabilities(),
        "boundary": {
            "linux_root_implies_windows_admin": False,
            "default_lane": "standard_user",
            "elevated_lane": "fixed_catalogued_tasks_only",
            "arbitrary_shell": False,
            "named_pipe_or_tcp_listener": False,
            "windows_source_authority": False,
            "periodic_execution_owner": "codex_scheduler_runner",
        },
    }


def validate(*, inventory: dict[str, Any] | None = None) -> dict[str, Any]:
    current = inventory or _task_inventory()
    issues: list[dict[str, Any]] = []
    if not current.get("ok"):
        issues.append({"severity": "risk", "code": str(current.get("reason") or "windows_task_inventory_failed"), "detail": current.get("detail", "")})
    rows = {
        str(row.get("task_name") or ""): row
        for row in current.get("tasks", [])
        if isinstance(row, dict) and row.get("task_name")
    }
    for task_name, policy in TASK_POLICIES.items():
        row = rows.get(task_name)
        if row is None:
            if policy.get("required"):
                issues.append({"severity": "risk", "code": "required_windows_task_missing", "task_name": task_name, "owner": policy["owner"]})
            continue
        run_level = str(row.get("run_level") or "")
        if run_level.casefold() != str(policy["run_level"]).casefold():
            issues.append({"severity": "risk", "code": "windows_task_run_level_drift", "task_name": task_name, "expected": policy["run_level"], "actual": run_level})
        user_id = str(row.get("user_id") or "").casefold()
        if user_id in {"system", "localsystem", "nt authority\\system"}:
            issues.append({"severity": "risk", "code": "windows_system_principal_forbidden", "task_name": task_name, "user_id": row.get("user_id", "")})
        action_text = f"{row.get('execute', '')} {row.get('arguments', '')}".casefold()
        marker = str(policy.get("action_marker") or "").casefold()
        if marker and marker not in action_text:
            issues.append({"severity": "risk", "code": "windows_task_action_drift", "task_name": task_name, "expected_marker": policy["action_marker"]})
        if policy["lane"] == "approved_elevated" and not policy.get("elevation_reason"):
            issues.append({"severity": "risk", "code": "elevated_lane_reason_missing", "task_name": task_name})
        if "\\\\wsl.localhost\\codex-wsl-lab\\home\\codexlab\\work\\codex-workspace" in action_text:
            issues.append({"severity": "risk", "code": "windows_task_targets_work_git", "task_name": task_name})
    risk = any(item.get("severity") == "risk" for item in issues)
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not risk,
        "status": "ok" if not risk else "risk",
        "generated_at": now_iso(),
        "issues": issues,
        "inventory": current,
        "acceptance": {
            "typed_operations_only": True,
            "default_least_privilege": True,
            "elevated_tasks_fixed_and_justified": True,
            "system_principal_forbidden": True,
            "business_owners_retained": True,
            "reverse_work_git_write_forbidden": True,
        },
    }


def _operation_policy(operation: str) -> tuple[str, dict[str, Any]] | None:
    for task_name, policy in TASK_POLICIES.items():
        if operation in policy.get("operations", []):
            return task_name, policy
    return None


def invoke_plan(operation: str) -> dict[str, Any]:
    selected = _operation_policy(operation)
    if selected is None:
        return {
            "schema": f"{SCHEMA}.invoke_plan",
            "ok": False,
            "status": "blocked",
            "reason": "operation_not_allowlisted",
            "operation": operation,
            "allowed_operations": [item["operation"] for item in capabilities()["operations"]],
        }
    task_name, policy = selected
    return {
        "schema": f"{SCHEMA}.invoke_plan",
        "ok": True,
        "status": "planned",
        "operation": operation,
        "task_name": task_name,
        "owner": policy["owner"],
        "lane": policy["lane"],
        "confirmation": f"{CONFIRM_PREFIX}:{operation}",
        "command_shape": ["schtasks.exe", "/Run", "/TN", task_name],
        "arbitrary_arguments": False,
        "completion_rule": "task start acceptance is not business-operation completion",
    }


def invoke(operation: str, confirm: str) -> dict[str, Any]:
    planned = invoke_plan(operation)
    if not planned.get("ok"):
        return planned
    if confirm != planned["confirmation"]:
        return {
            **planned,
            "ok": False,
            "status": "blocked",
            "reason": "explicit_confirmation_required",
        }
    executable = schtasks_path()
    if not executable.is_file():
        return {**planned, "ok": False, "status": "failed", "reason": "schtasks_unavailable"}
    result = _run([str(executable), "/Run", "/TN", planned["task_name"]], timeout=30)
    return {
        **planned,
        "ok": bool(result.get("ok")),
        "status": "accepted" if result.get("ok") else "failed",
        "accepted_at": now_iso(),
        "transport": {
            "returncode": result.get("returncode"),
            "stderr": result.get("stderr", ""),
        },
        "business_result_consumed": False,
        "next_action": f"query {planned['owner']} for operation-specific completion evidence",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Typed WSL-to-Windows execution agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("status")
    sub.add_parser("validate")
    sub.add_parser("capabilities")
    plan_parser = sub.add_parser("invoke-plan")
    plan_parser.add_argument("--operation", required=True)
    invoke_parser = sub.add_parser("invoke")
    invoke_parser.add_argument("--operation", required=True)
    invoke_parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    if args.command in {"snapshot", "status"}:
        payload = snapshot()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "capabilities":
        payload = capabilities()
    elif args.command == "invoke-plan":
        payload = invoke_plan(args.operation)
    else:
        payload = invoke(args.operation, args.confirm)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
