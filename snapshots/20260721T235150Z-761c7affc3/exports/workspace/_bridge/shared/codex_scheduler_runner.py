#!/usr/bin/env python3
"""Unified Codex resource scheduler runner.

Windows Task Scheduler should only wake this runner. This file owns due checks,
retry bookkeeping, idempotency, and execution records for lightweight local
maintenance tasks. Domain modules still own their actual actions.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
SHARED_ROOT = BRIDGE_ROOT / "shared"
LOG_DIR = BRIDGE_ROOT / "logs" / "codex_scheduler"
LOG_PATH = LOG_DIR / "codex-scheduler.log"
MAX_LOG_BYTES = 2 * 1024 * 1024
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from shared.backup_router import create_backup as create_routed_backup  # noqa: E402
from platform_paths import host_accessible_path, host_compatibility_root, resource_library_root  # noqa: E402


RESOURCE_ROOT = resource_library_root()
WINDOWS_BRIDGE_IDLE_RESTART_SCRIPT = host_accessible_path(
    host_compatibility_root() / "_bridge" / "shared" / "restart-bridge-appserver-if-idle.ps1",
    platform_name="nt",
)
SCHEDULE_ROOT = RESOURCE_ROOT / "文档" / "定时模块"
RUNTIME_ROOT = SCHEDULE_ROOT / "运行态" / "统一调度"
RECORD_ROOT = SCHEDULE_ROOT / "执行记录" / "统一调度"
GOVERNANCE_ROOT = SCHEDULE_ROOT / "治理"
STATE_PATH = RUNTIME_ROOT / "scheduler-state.json"
HEARTBEAT_PATH = RUNTIME_ROOT / "scheduler-heartbeat.json"
LOCK_PATH = RUNTIME_ROOT / "scheduler.lock"
TASKS_PATH = RUNTIME_ROOT / "maintenance_tasks.json"
TASK_OVERRIDES_PATH = RUNTIME_ROOT / "maintenance_task_overrides.json"

try:
    BEIJING = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    BEIJING = timezone(timedelta(hours=8))

DEFAULT_LOOP_SECONDS = 300
DEFAULT_TASKS: list[dict[str, Any]] = [
    {
        "id": "email_scheduler_run_due",
        "name": "自动邮件到期执行",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 60},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/shared/email_scheduler.py",
                "dispatch-due",
                "--timeout-seconds",
                "1800",
            ],
        },
        "policy": {
            "mode": "controlled-apply",
            "risk": "medium",
            "timeout_seconds": 1900,
            "retry_interval_seconds": 300,
            "max_retry_count": 3,
            "latest_lag_seconds": 86400,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "performance_quick_metrics",
        "name": "性能快速指标采样",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 3600},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
                "performance",
                "metrics",
                "--observe-seconds",
                "2",
                "--top",
                "6",
                "--profile",
                "quick",
            ],
        },
        "policy": {
            "mode": "read-only",
            "risk": "low",
            "timeout_seconds": 90,
            "retry_interval_seconds": 300,
            "max_retry_count": 3,
            "latest_lag_seconds": 7200,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "windows_memory_governance_capture",
        "name": "Windows内存分类趋势采样",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 1800},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/windows_memory_governance.py",
                "capture",
            ],
        },
        "policy": {
            "mode": "derived-index-write",
            "risk": "low",
            "timeout_seconds": 90,
            "retry_interval_seconds": 600,
            "max_retry_count": 2,
            "latest_lag_seconds": 3600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "resource_process_governance_dry_run",
        "name": "资源进程治理预案",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 900},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
                "resource-process",
                "cleanup",
                "--min-age-minutes",
                "15",
            ],
        },
        "policy": {
            "mode": "dry-run",
            "risk": "medium",
            "timeout_seconds": 180,
            "retry_interval_seconds": 900,
            "max_retry_count": 2,
            "latest_lag_seconds": 3600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "resource_process_governance_safe_apply",
        "name": "资源进程安全自动清理",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 7200},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
                "resource-process",
                "cleanup",
                "--safe-apply",
                "--apply",
                "--min-age-minutes",
                "30",
            ],
        },
        "policy": {
            "mode": "controlled-safe-apply",
            "risk": "medium",
            "timeout_seconds": 180,
            "retry_interval_seconds": 1800,
            "max_retry_count": 1,
            "latest_lag_seconds": 7200,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "bridge_appserver_idle_restart_dry_run",
        "name": "桥接 app-server 空闲重启预检",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 1800},
        "action": {
            "type": "powershell",
            "command": [
                str(WINDOWS_BRIDGE_IDLE_RESTART_SCRIPT),
                "-Mode",
                "dry-run",
            ],
        },
        "policy": {
            "mode": "dry-run",
            "risk": "medium",
            "timeout_seconds": 180,
            "retry_interval_seconds": 600,
            "max_retry_count": 2,
            "latest_lag_seconds": 14400,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "backup_hygiene_repair_plan",
        "name": "备份治理修复预案",
        "enabled": True,
        "trigger": {"type": "daily", "at": "03:10"},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
                "backup-hygiene",
                "repair-plan",
            ],
        },
        "policy": {
            "mode": "dry-run",
            "risk": "low",
            "paired_apply_task_id": "backup_hygiene_archive_apply",
            "timeout_seconds": 180,
            "retry_interval_seconds": 1800,
            "max_retry_count": 3,
            "latest_lag_seconds": 21600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "backup_hygiene_archive_apply",
        "name": "备份治理低风险自动归档",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 21600},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
                "backup-hygiene",
                "apply",
                "--confirm",
                "archive-old-backups",
            ],
        },
        "policy": {
            "mode": "controlled-safe-apply",
            "risk": "low",
            "timeout_seconds": 180,
            "retry_interval_seconds": 1800,
            "max_retry_count": 1,
            "latest_lag_seconds": 21600,
            "retry_exhausted_action": "record_and_continue",
            "source_repair_plan_task_id": "backup_hygiene_repair_plan",
            "allowed_effect": "move eligible old backups into _bridge/backups/archive; no delete/compress/source rewrite",
        },
    },
    {
        "id": "backup_hygiene_doctor",
        "name": "备份治理健康检查",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 7200},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
                "backup-hygiene",
                "doctor",
            ],
        },
        "policy": {
            "mode": "read-only",
            "risk": "low",
            "timeout_seconds": 180,
            "retry_interval_seconds": 1800,
            "max_retry_count": 2,
            "latest_lag_seconds": 21600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "codex_maintenance_report_worker",
        "name": "Codex维护报告生成",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 300},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/shared/codex_reporter.py",
                "worker",
                "--max-jobs",
                "1",
                "--timeout-seconds",
                "900",
            ],
        },
        "policy": {
            "mode": "codex-report-worker",
            "risk": "low",
            "timeout_seconds": 930,
            "retry_interval_seconds": 600,
            "max_retry_count": 2,
            "latest_lag_seconds": 3600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "tool_registry_drift_check",
        "name": "工具和插件配置漂移检查",
        "enabled": True,
        "trigger": {"type": "daily", "at": "03:20"},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
                "tool-registry-drift-check",
            ],
        },
        "policy": {
            "mode": "read-only",
            "risk": "low",
            "timeout_seconds": 180,
            "retry_interval_seconds": 1800,
            "max_retry_count": 2,
            "latest_lag_seconds": 21600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "resource_cache_transient_cleanup",
        "name": "资源缓存未完成残片治理",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 21600},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/resource_cli.py",
                "clean-cache",
                "--target-dir",
                "_bridge/resources",
                "--older-than-days",
                "2",
                "--transient-only",
                "--limit",
                "50",
                "--json",
                "--no-log",
            ],
        },
        "policy": {
            "mode": "controlled-transient-cache-cleanup",
            "risk": "low",
            "timeout_seconds": 180,
            "retry_interval_seconds": 1800,
            "max_retry_count": 1,
            "latest_lag_seconds": 21600,
            "retry_exhausted_action": "record_and_continue",
            "allowed_effect": "delete only incomplete resource-cache suffixes older than two days; never delete completed resources",
        },
    },
    {
        "id": "record_store_index_refresh",
        "name": "全局记录索引刷新",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 21600},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/shared/record_store_maintenance.py",
                "index",
                "--apply",
            ],
        },
        "policy": {
            "mode": "controlled-derived-index-refresh",
            "risk": "low",
            "timeout_seconds": 600,
            "retry_interval_seconds": 1800,
            "max_retry_count": 2,
            "latest_lag_seconds": 21600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "record_store_governance_doctor",
        "name": "全局记录存储治理检查",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 7200},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/shared/record_store_maintenance.py",
                "doctor",
            ],
        },
        "policy": {
            "mode": "read-only",
            "risk": "low",
            "timeout_seconds": 300,
            "retry_interval_seconds": 1800,
            "max_retry_count": 1,
            "latest_lag_seconds": 21600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
    {
        "id": "windows_execution_plane_validate",
        "name": "Windows执行平面权限与任务漂移检查",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 1800},
        "action": {
            "type": "command",
            "command": [
                "python",
                "_bridge/windows_execution_agent.py",
                "validate",
            ],
        },
        "policy": {
            "mode": "read-only",
            "risk": "low",
            "timeout_seconds": 90,
            "retry_interval_seconds": 600,
            "max_retry_count": 2,
            "latest_lag_seconds": 3600,
            "retry_exhausted_action": "record_and_continue",
        },
    },
]


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if key == "id":
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _mapping_diff(base: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key, value in current.items():
        if key == "id":
            continue
        if key not in base:
            diff[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            nested = _mapping_diff(base[key], value)
            if nested:
                diff[key] = nested
        elif base.get(key) != value:
            diff[key] = copy.deepcopy(value)
    return diff


def load_task_overrides() -> dict[str, Any]:
    payload = read_json(TASK_OVERRIDES_PATH, {"schema": "codex_scheduler.task_overrides.v1", "tasks": []})
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        return {"schema": "codex_scheduler.task_overrides.v1", "tasks": []}
    return payload


def desired_tasks() -> list[dict[str, Any]]:
    ordered = [copy.deepcopy(task) for task in DEFAULT_TASKS]
    positions = {str(task.get("id") or ""): index for index, task in enumerate(ordered)}
    for item in load_task_overrides().get("tasks", []):
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or "")
        if not task_id:
            continue
        if item.get("remove") is True:
            if task_id in positions:
                ordered.pop(positions[task_id])
                positions = {str(task.get("id") or ""): index for index, task in enumerate(ordered)}
            continue
        if isinstance(item.get("add"), dict):
            added = copy.deepcopy(item["add"])
            added["id"] = task_id
            if task_id in positions:
                ordered[positions[task_id]] = added
            else:
                positions[task_id] = len(ordered)
                ordered.append(added)
            continue
        patch = item.get("patch") if isinstance(item.get("patch"), dict) else {}
        if task_id in positions:
            ordered[positions[task_id]] = _deep_merge(ordered[positions[task_id]], patch)
    return ordered


def task_drift_snapshot() -> dict[str, Any]:
    runtime_payload = read_json(TASKS_PATH, {"tasks": []})
    if not isinstance(runtime_payload, dict) or not isinstance(runtime_payload.get("tasks", []), list):
        return {
            "schema": "codex_scheduler.task_drift.v1",
            "ok": False,
            "reason": "runtime_task_table_invalid",
            "runtime_path": str(TASKS_PATH),
            "override_path": str(TASK_OVERRIDES_PATH),
        }
    runtime = {
        str(task.get("id") or ""): task
        for task in runtime_payload.get("tasks", []) if isinstance(task, dict) and task.get("id")
    }
    desired = {str(task.get("id") or ""): task for task in desired_tasks() if task.get("id")}
    missing = sorted(desired.keys() - runtime.keys())
    runtime_only = sorted(runtime.keys() - desired.keys())
    changed = []
    for task_id in sorted(runtime.keys() & desired.keys()):
        fields = sorted(key for key in set(runtime[task_id]) | set(desired[task_id]) if runtime[task_id].get(key) != desired[task_id].get(key))
        if fields:
            changed.append({"id": task_id, "fields": fields})
    return {
        "schema": "codex_scheduler.task_drift.v1",
        "ok": not missing and not runtime_only and not changed,
        "desired_task_count": len(desired),
        "runtime_task_count": len(runtime),
        "missing_task_ids": missing,
        "runtime_only_task_ids": runtime_only,
        "changed_tasks": changed,
        "override_path": str(TASK_OVERRIDES_PATH),
        "rule": "runtime tasks must equal defaults plus explicit overrides; no silent configuration drift",
    }


def task_override_plan() -> dict[str, Any]:
    runtime_payload = read_json(TASKS_PATH, {"tasks": []})
    if not isinstance(runtime_payload, dict) or not isinstance(runtime_payload.get("tasks", []), list):
        return {
            "schema": "codex_scheduler.task_override_plan.v1",
            "ok": False,
            "apply_requested": False,
            "reason": "runtime_task_table_invalid",
            "runtime_path": str(TASKS_PATH),
            "override_path": str(TASK_OVERRIDES_PATH),
            "tasks": [],
        }
    runtime_tasks = [task for task in runtime_payload.get("tasks", []) if isinstance(task, dict) and task.get("id")]
    defaults = {str(task.get("id") or ""): task for task in DEFAULT_TASKS}
    overrides = []
    for task in runtime_tasks:
        task_id = str(task.get("id") or "")
        if task_id not in defaults:
            overrides.append({"id": task_id, "add": copy.deepcopy(task), "reason": "existing_runtime_only_task"})
            continue
        patch = _mapping_diff(defaults[task_id], task)
        if patch:
            overrides.append({"id": task_id, "patch": patch, "reason": "preserve_existing_runtime_configuration"})
    return {
        "schema": "codex_scheduler.task_override_plan.v1",
        "ok": True,
        "apply_requested": False,
        "override_count": len(overrides),
        "override_path": str(TASK_OVERRIDES_PATH),
        "tasks": overrides,
    }


def migrate_task_overrides(*, apply: bool, confirm: str) -> dict[str, Any]:
    plan = task_override_plan()
    if not plan.get("ok"):
        return {**plan, "applied": False}
    if not apply:
        return plan
    if confirm != "MIGRATE-SCHEDULER-OVERRIDES":
        return {**plan, "ok": False, "reason": "confirmation_required", "required_confirm": "MIGRATE-SCHEDULER-OVERRIDES"}
    existing = [path for path in (TASKS_PATH, TASK_OVERRIDES_PATH) if path.is_file()]
    backup = create_routed_backup(
        existing,
        remark="scheduler task override migration",
        purpose="scheduler-declarative-reconciliation",
        category="scheduler",
        trigger="codex_scheduler_runner.migrate_task_overrides",
    ) if existing else {"ok": True, "created_count": 0, "manifest_paths": []}
    if not backup.get("ok"):
        return {
            "schema": "codex_scheduler.task_override_migration.v1",
            "ok": False,
            "applied": False,
            "reason": "backup_failed",
            "backup": backup,
        }
    payload = {
        "schema": "codex_scheduler.task_overrides.v1",
        "generated_at": iso(now_bj()),
        "tasks": plan["tasks"],
    }
    write_json(TASK_OVERRIDES_PATH, payload)
    write_json(TASKS_PATH, {"schema_version": 2, "timezone": "Asia/Shanghai", "tasks": desired_tasks()})
    return {
        "schema": "codex_scheduler.task_override_migration.v1",
        "ok": bool(backup.get("ok")) and task_drift_snapshot().get("ok", False),
        "applied": True,
        "override_count": len(plan["tasks"]),
        "override_path": str(TASK_OVERRIDES_PATH),
        "tasks_path": str(TASKS_PATH),
        "backup": backup,
        "drift": task_drift_snapshot(),
    }


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"pid": os.getpid(), "started_at": now_bj().isoformat()}, ensure_ascii=False))
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass
class TaskRun:
    task_id: str
    ok: bool
    mode: str
    due_reason: str
    started_at: str
    finished_at: str
    exit_code: int | None = None
    duration_seconds: float = 0.0
    skipped: bool = False
    timed_out: bool = False
    stdout_preview: str = ""
    stderr_preview: str = ""
    record_path: str = ""
    error: str = ""


def now_bj() -> datetime:
    return datetime.now(tz=BEIJING)


def iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=BEIJING)
        return dt.astimezone(BEIJING)
    except ValueError:
        return None


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def rotate_log_if_needed() -> None:
    if not LOG_PATH.exists() or LOG_PATH.stat().st_size <= MAX_LOG_BYTES:
        return
    rotated = LOG_PATH.with_name(f"{LOG_PATH.stem}-{now_bj().strftime('%Y%m%d-%H%M%S')}{LOG_PATH.suffix}")
    LOG_PATH.replace(rotated)


def append_log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log_if_needed()
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"[{now_bj().isoformat()}] {message}\n")


def ensure_default_tasks() -> None:
    configured_defaults = desired_tasks()
    if not TASKS_PATH.exists():
        write_json(
            TASKS_PATH,
            {
                "schema_version": 2,
                "timezone": "Asia/Shanghai",
                "tasks": configured_defaults,
            },
        )
        return
    payload = read_json(TASKS_PATH, {"tasks": []})
    if not isinstance(payload, dict):
        return
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return
    existing_ids = {str(item.get("id") or "") for item in tasks if isinstance(item, dict)}
    missing_defaults = [task for task in configured_defaults if str(task.get("id") or "") not in existing_ids]
    defaults_by_id = {str(task.get("id") or ""): task for task in configured_defaults}
    changed = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        default_task = defaults_by_id.get(str(task.get("id") or ""))
        if not default_task:
            continue
        policy = task.setdefault("policy", {})
        default_policy = default_task.get("policy", {}) if isinstance(default_task.get("policy"), dict) else {}
        if not isinstance(policy, dict):
            continue
        for key in ("paired_apply_task_id", "source_repair_plan_task_id", "allowed_effect"):
            if key in default_policy and key not in policy:
                policy[key] = default_policy[key]
                changed = True
    if not missing_defaults:
        if changed:
            write_json(TASKS_PATH, payload)
        return
    payload.setdefault("schema_version", 2)
    payload.setdefault("timezone", "Asia/Shanghai")
    tasks.extend(missing_defaults)
    changed = True
    if not changed:
        return
    write_json(TASKS_PATH, payload)


def load_tasks() -> list[dict[str, Any]]:
    ensure_default_tasks()
    payload = read_json(TASKS_PATH, {"tasks": []})
    tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
    return [item for item in tasks if isinstance(item, dict)]


def load_state() -> dict[str, Any]:
    return read_json(STATE_PATH, {"schema_version": 1, "tasks": {}})


def save_state(state: dict[str, Any]) -> None:
    state["schema_version"] = 1
    state["updated_at"] = iso(now_bj())
    write_json(STATE_PATH, state)


def normalize_retry_exhausted_state(tasks: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    tasks_by_id = {str(task.get("id") or ""): task for task in tasks}
    changed: list[str] = []
    for task_id, task_state in state.setdefault("tasks", {}).items():
        if not isinstance(task_state, dict) or task_state.get("last_status") != "failed":
            continue
        policy = tasks_by_id.get(str(task_id), {}).get("policy") or {}
        max_retry_count = int(policy.get("max_retry_count") or 0)
        retry_count = int(task_state.get("retry_count") or 0)
        if retry_count <= max_retry_count:
            continue
        task_state["last_status"] = "retry_exhausted"
        task_state.setdefault("retry_exhausted_at", task_state.get("last_attempt_at") or iso(now_bj()))
        task_state.pop("retry_after", None)
        changed.append(str(task_id))
    return {"changed": changed}


def daily_due_time(now: datetime, at_text: str) -> datetime | None:
    try:
        hour, minute = [int(x) for x in at_text.split(":", 1)]
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return None


def get_due_reason(task: dict[str, Any], task_state: dict[str, Any], now: datetime) -> str | None:
    trigger = task.get("trigger", {})
    if not isinstance(trigger, dict):
        return None
    last_success = parse_iso(task_state.get("last_success_at"))
    last_attempt = parse_iso(task_state.get("last_attempt_at"))
    retry_after = parse_iso(task_state.get("retry_after"))
    retry_count = int(task_state.get("retry_count") or 0)
    policy = task.get("policy", {}) if isinstance(task.get("policy"), dict) else {}
    max_retry_count = int(policy.get("max_retry_count") or 0)

    if task_state.get("last_status") == "failed" and retry_count <= max_retry_count:
        if retry_after is None or now >= retry_after:
            return "retry"
        return None

    trigger_type = trigger.get("type")
    if trigger_type == "interval":
        every = int(trigger.get("every_seconds") or 0)
        if every <= 0:
            return None
        if task_state.get("last_status") in {"failed", "retry_exhausted"} and last_attempt:
            baseline = last_attempt
        else:
            baseline = last_success or last_attempt
        if baseline is None or (now - baseline).total_seconds() >= every:
            return "interval"
        return None
    if trigger_type == "daily":
        due_at = daily_due_time(now, str(trigger.get("at", "")))
        if due_at is None or now < due_at:
            return None
        if last_success and last_success.date() == now.date() and last_success >= due_at:
            return None
        last_attempt = parse_iso(task_state.get("last_attempt_at"))
        if (
            task_state.get("last_status") == "missed_latest_window"
            and last_attempt
            and last_attempt.date() == now.date()
            and last_attempt >= due_at
        ):
            return None
        latest_lag = int(policy.get("latest_lag_seconds") or 0)
        if latest_lag > 0 and (now - due_at).total_seconds() > latest_lag:
            return "missed_latest_window"
        return "daily"
    return None


def command_for_task(task: dict[str, Any]) -> list[str]:
    action = task.get("action", {})
    if not isinstance(action, dict):
        raise ValueError("task action must be an object")
    command = action.get("command")
    if not isinstance(command, list) or not command:
        raise ValueError("task action.command must be a non-empty list")
    if action.get("type") == "powershell":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            *[str(x) for x in command],
        ]
    normalized = [str(x) for x in command]
    if normalized and normalized[0].lower() in {"python", "python.exe"}:
        normalized[0] = sys.executable
    return normalized


def preview(text: str, limit: int = 3000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"


def summarize_success_stdout(text: str, limit: int = 1200) -> str:
    """Keep successful run records useful without copying whole validator snapshots."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return preview(raw, limit=limit)
    if not isinstance(payload, dict):
        return preview(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), limit=limit)
    keep = {
        key: value
        for key, value in payload.items()
        if key in {"schema", "ok", "status", "severity", "summary", "reason", "next_action", "record_path", "record_suppressed"}
        or (key.endswith("_count") and isinstance(value, (int, float)))
    }
    issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
    if issues:
        keep["issue_count"] = len(issues)
        keep["issue_codes"] = [
            str(item.get("code") or item)[:120]
            for item in issues[:20]
            if isinstance(item, (dict, str))
        ]
    keep["full_output_omitted"] = len(raw) > limit
    keep["original_chars"] = len(raw)
    return preview(json.dumps(keep, ensure_ascii=False, separators=(",", ":")), limit=limit)


def write_run_record(task: dict[str, Any], run: TaskRun) -> Path:
    current = now_bj()
    record_dir = RECORD_ROOT / current.strftime("%Y-%m")
    record_dir.mkdir(parents=True, exist_ok=True)
    path = record_dir / f"{current.strftime('%Y%m%d')}-{task.get('id', 'task')}.jsonl"
    payload = {
        "schema": "codex_scheduler.run_record.v2",
        "task": {
            "id": task.get("id"),
            "name": task.get("name"),
            "trigger": task.get("trigger"),
            "policy": task.get("policy"),
        },
        "run": run.__dict__,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def run_task(task: dict[str, Any], due_reason: str, dry_run: bool) -> TaskRun:
    task_id = str(task.get("id") or "unknown")
    policy = task.get("policy", {}) if isinstance(task.get("policy"), dict) else {}
    mode = str(policy.get("mode") or "dry-run")
    started = now_bj()
    if dry_run:
        run = TaskRun(
            task_id=task_id,
            ok=True,
            mode=mode,
            due_reason=due_reason,
            started_at=iso(started),
            finished_at=iso(now_bj()),
            skipped=True,
            stdout_preview=json.dumps({"would_run": command_for_task(task)}, ensure_ascii=False),
        )
        run.record_path = str(write_run_record(task, run))
        return run

    timeout_seconds = int(policy.get("timeout_seconds") or 180)
    cmd = command_for_task(task)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            **NO_WINDOW_KW,
        )
        finished = now_bj()
        run = TaskRun(
            task_id=task_id,
            ok=proc.returncode == 0,
            mode=mode,
            due_reason=due_reason,
            started_at=iso(started),
            finished_at=iso(finished),
            exit_code=proc.returncode,
            duration_seconds=round((finished - started).total_seconds(), 3),
            stdout_preview=summarize_success_stdout(proc.stdout or "") if proc.returncode == 0 else preview(proc.stdout or ""),
            stderr_preview=preview(proc.stderr or ""),
        )
    except subprocess.TimeoutExpired as exc:
        finished = now_bj()
        run = TaskRun(
            task_id=task_id,
            ok=False,
            mode=mode,
            due_reason=due_reason,
            started_at=iso(started),
            finished_at=iso(finished),
            duration_seconds=round((finished - started).total_seconds(), 3),
            timed_out=True,
            stdout_preview=preview((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
            stderr_preview=preview((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
            error=f"timeout after {timeout_seconds}s",
        )
    except Exception as exc:
        finished = now_bj()
        run = TaskRun(
            task_id=task_id,
            ok=False,
            mode=mode,
            due_reason=due_reason,
            started_at=iso(started),
            finished_at=iso(finished),
            duration_seconds=round((finished - started).total_seconds(), 3),
            error=f"{type(exc).__name__}: {exc}",
        )
    run.record_path = str(write_run_record(task, run))
    return run


def update_task_state(task: dict[str, Any], state: dict[str, Any], run: TaskRun) -> None:
    task_id = run.task_id
    tasks_state = state.setdefault("tasks", {})
    task_state = tasks_state.setdefault(task_id, {})
    policy = task.get("policy", {}) if isinstance(task.get("policy"), dict) else {}
    retry_interval = int(policy.get("retry_interval_seconds") or 300)
    max_retry_count = int(policy.get("max_retry_count") or 0)
    task_state["last_attempt_at"] = run.finished_at
    task_state["last_record_path"] = run.record_path
    if run.ok:
        task_state["last_status"] = "success"
        task_state["last_success_at"] = run.finished_at
        task_state["retry_count"] = 0
        task_state.pop("retry_after", None)
        task_state.pop("retry_exhausted_at", None)
    else:
        retry_count = int(task_state.get("retry_count") or 0) + 1
        task_state["retry_count"] = retry_count
        if retry_count <= max_retry_count:
            task_state["last_status"] = "failed"
            retry_after = parse_iso(run.finished_at) or now_bj()
            task_state["retry_after"] = iso(retry_after + timedelta(seconds=retry_interval))
        else:
            task_state["last_status"] = "retry_exhausted"
            task_state["retry_exhausted_at"] = run.finished_at
            task_state.pop("retry_after", None)


def compact_run_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": result.get("task_id"),
        "ok": result.get("ok"),
        "skipped": result.get("skipped", False),
        "timed_out": result.get("timed_out", False),
        "duration_seconds": result.get("duration_seconds", 0),
        "exit_code": result.get("exit_code"),
        "record_path": result.get("record_path", ""),
        "error": preview(str(result.get("error") or ""), limit=300),
    }


def compact_heartbeat(payload: dict[str, Any]) -> dict[str, Any]:
    last_results = payload.get("last_run_summary")
    if not isinstance(last_results, list):
        legacy = payload.get("last_run_results", [])
        last_results = [compact_run_result(item) for item in legacy[:20] if isinstance(item, dict)] if isinstance(legacy, list) else []
    return {
        "ok": bool(payload.get("ok")),
        "pid": payload.get("pid"),
        "hostname": payload.get("hostname"),
        "updated_at": payload.get("updated_at", ""),
        "timezone": payload.get("timezone", "Asia/Shanghai"),
        "last_run_due_count": int(payload.get("last_run_due_count") or 0),
        "last_run_summary": last_results[:20],
        "last_error": preview(str(payload.get("last_error") or ""), limit=300),
        "state_path": payload.get("state_path", ""),
        "tasks_path": payload.get("tasks_path", ""),
        "overrides_path": str(TASK_OVERRIDES_PATH),
        "log_path": payload.get("log_path", ""),
    }


def run_due(dry_run: bool = False) -> dict[str, Any]:
    now = now_bj()
    tasks = load_tasks()
    state = load_state()
    normalization = normalize_retry_exhausted_state(tasks, state)
    results: list[dict[str, Any]] = []
    due_count = 0
    for task in tasks:
        if not task.get("enabled", False):
            continue
        task_id = str(task.get("id") or "")
        task_state = state.setdefault("tasks", {}).setdefault(task_id, {})
        due_reason = get_due_reason(task, task_state, now)
        if not due_reason:
            continue
        if due_reason == "missed_latest_window":
            task_state["last_status"] = "missed_latest_window"
            task_state["last_attempt_at"] = iso(now)
            task_state["last_record_path"] = ""
            results.append({"task_id": task_id, "skipped": True, "reason": due_reason})
            continue
        due_count += 1
        run = run_task(task, due_reason=due_reason, dry_run=dry_run)
        if not dry_run:
            update_task_state(task, state, run)
        results.append(run.__dict__)
        append_log(f"task={task_id} ok={run.ok} dry_run={dry_run} reason={due_reason} record={run.record_path}")
    if not dry_run:
        save_state(state)
    heartbeat = write_heartbeat(
        {
            "last_run_due_count": due_count,
            "last_run_summary": [compact_run_result(result) for result in results[:20]],
            "state_normalization": normalization,
        }
    )
    return {"ok": True, "dry_run": dry_run, "due_count": due_count, "results": results, "heartbeat": heartbeat, "state_normalization": normalization}


def write_heartbeat(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "ok": True,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "updated_at": iso(now_bj()),
        "timezone": "Asia/Shanghai",
        "state_path": str(STATE_PATH),
        "tasks_path": str(TASKS_PATH),
        "overrides_path": str(TASK_OVERRIDES_PATH),
        "log_path": str(LOG_PATH),
    }
    if extra:
        payload.update(extra)
    write_json(HEARTBEAT_PATH, payload)
    return payload


def snapshot() -> dict[str, Any]:
    tasks = load_tasks()
    state = load_state()
    now = now_bj()
    rows = []
    for task in tasks:
        task_id = str(task.get("id") or "")
        task_state = state.get("tasks", {}).get(task_id, {})
        rows.append(
            {
                "id": task_id,
                "name": task.get("name", ""),
                "enabled": bool(task.get("enabled", False)),
                "trigger": task.get("trigger", {}),
                "policy": task.get("policy", {}),
                "due_reason": get_due_reason(task, task_state, now),
                "last_success_at": task_state.get("last_success_at", ""),
                "last_status": task_state.get("last_status", ""),
                "retry_count": task_state.get("retry_count", 0),
                "retry_after": task_state.get("retry_after", ""),
            }
        )
    return {
        "ok": True,
        "timezone": "Asia/Shanghai",
        "task_count": len(tasks),
        "tasks": rows,
        "configuration": task_drift_snapshot(),
    }


def validate() -> dict[str, Any]:
    tasks = load_tasks()
    issues: list[str] = []
    ids: set[str] = set()
    tasks_by_id: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        if not task_id:
            issues.append("task missing id")
        if task_id in ids:
            issues.append(f"duplicate task id: {task_id}")
        ids.add(task_id)
        if task_id:
            tasks_by_id[task_id] = task
        policy = task.get("policy", {})
        trigger = task.get("trigger", {})
        if not isinstance(policy, dict):
            issues.append(f"{task_id}: policy must be object")
            continue
        for key in ("retry_interval_seconds", "max_retry_count", "latest_lag_seconds", "retry_exhausted_action"):
            if key not in policy:
                issues.append(f"{task_id}: policy missing {key}")
        if not isinstance(trigger, dict) or not trigger.get("type"):
            issues.append(f"{task_id}: trigger missing type")
        try:
            command_for_task(task)
        except Exception as exc:
            issues.append(f"{task_id}: invalid command: {exc}")
    for task in tasks:
        task_id = str(task.get("id") or "")
        policy = task.get("policy", {}) if isinstance(task.get("policy"), dict) else {}
        paired_apply_task_id = str(policy.get("paired_apply_task_id") or "")
        if paired_apply_task_id and paired_apply_task_id not in tasks_by_id:
            issues.append(f"{task_id}: paired apply task missing: {paired_apply_task_id}")
        if "repair_plan" in task_id and "backup_hygiene" in task_id and not paired_apply_task_id:
            issues.append(f"{task_id}: backup repair-plan task must declare paired_apply_task_id")
        source_repair_plan_task_id = str(policy.get("source_repair_plan_task_id") or "")
        if source_repair_plan_task_id and source_repair_plan_task_id not in tasks_by_id:
            issues.append(f"{task_id}: source repair-plan task missing: {source_repair_plan_task_id}")
    drift = task_drift_snapshot()
    if not drift.get("ok"):
        issues.append("runtime task table differs from defaults plus explicit overrides")
    return {"ok": not issues, "issues": issues, "task_count": len(tasks), "configuration": drift}


def metrics() -> dict[str, Any]:
    snap = snapshot()
    heartbeat = compact_heartbeat(read_json(HEARTBEAT_PATH, {}))
    state = load_state()
    tasks_by_id = {str(task.get("id") or ""): task for task in load_tasks()}
    due = [task for task in snap["tasks"] if task.get("due_reason")]
    failed = [
        {"id": key, **value}
        for key, value in state.get("tasks", {}).items()
        if isinstance(value, dict) and value.get("last_status") in {"failed", "retry_exhausted"}
    ]
    retry_exhausted = [
        {"id": key, **value}
        for key, value in state.get("tasks", {}).items()
        if isinstance(value, dict) and value.get("last_status") == "retry_exhausted"
    ]
    retry_storm_candidates = [
        {
            "id": key,
            "retry_count": int(value.get("retry_count") or 0),
            "max_retry_count": int((tasks_by_id.get(key, {}).get("policy") or {}).get("max_retry_count") or 0),
            "last_attempt_at": str(value.get("last_attempt_at") or ""),
            "last_success_at": str(value.get("last_success_at") or ""),
            "last_status": str(value.get("last_status") or ""),
        }
        for key, value in state.get("tasks", {}).items()
        if isinstance(value, dict)
        and str(value.get("last_status") or "") == "failed"
        and int(value.get("retry_count") or 0) > int((tasks_by_id.get(key, {}).get("policy") or {}).get("max_retry_count") or 0)
    ]
    return {
        "ok": True,
        "task_count": snap["task_count"],
        "due_count": len(due),
        "failed_count": len(failed),
        "retry_exhausted_count": len(retry_exhausted),
        "retry_storm_candidate_count": len(retry_storm_candidates),
        "due_task_ids": [task["id"] for task in due],
        "failed_task_ids": [task["id"] for task in failed],
        "retry_exhausted_task_ids": [task["id"] for task in retry_exhausted],
        "retry_storm_candidates": retry_storm_candidates,
        "heartbeat": heartbeat,
        "configuration_ok": bool(snap.get("configuration", {}).get("ok")),
        "configuration_drift_count": (
            len(snap.get("configuration", {}).get("missing_task_ids", []))
            + len(snap.get("configuration", {}).get("runtime_only_task_ids", []))
            + len(snap.get("configuration", {}).get("changed_tasks", []))
        ),
        "log_bytes": LOG_PATH.stat().st_size if LOG_PATH.exists() else 0,
        "max_log_bytes": MAX_LOG_BYTES,
    }


def doctor() -> dict[str, Any]:
    val = validate()
    metric = metrics()
    issues = list(val["issues"])
    if metric["log_bytes"] > MAX_LOG_BYTES:
        issues.append(f"log exceeds rotation threshold: {metric['log_bytes']}")
    if not metric.get("heartbeat"):
        issues.append("heartbeat missing; runner has not written runtime state yet")
    if metric.get("retry_storm_candidates"):
        ids = ", ".join(str(item.get("id") or "") for item in metric["retry_storm_candidates"])
        issues.append(f"retry exhausted tasks require cooldown/state normalization: {ids}")
    severity = "ok" if not issues else ("blocker" if not val["ok"] else "risk")
    return {
        "ok": not issues,
        "severity": severity,
        "issues": issues,
        "summary": {
            "task_count": metric["task_count"],
            "due_count": metric["due_count"],
            "failed_count": metric["failed_count"],
            "retry_exhausted_count": metric.get("retry_exhausted_count", 0),
            "retry_storm_candidate_count": metric.get("retry_storm_candidate_count", 0),
            "heartbeat_updated_at": metric.get("heartbeat", {}).get("updated_at", ""),
        },
    }


def repair_plan() -> dict[str, Any]:
    doc = doctor()
    actions = []
    if any("heartbeat missing" in item for item in doc["issues"]):
        actions.append("run codex scheduler once or start the scheduled task to generate heartbeat")
    if any("log exceeds" in item for item in doc["issues"]):
        actions.append("rotate codex scheduler log")
    if any("policy missing" in item or "invalid command" in item for item in doc["issues"]):
        actions.append("repair maintenance_tasks.json and re-run validate")
    if any("retry exhausted tasks" in item for item in doc["issues"]):
        actions.append("run scheduler once after this fix; exhausted tasks should cool down against last_attempt_at instead of storming every loop")
    actions.append("run: codex_scheduler_runner.py run-due --dry-run")
    return {"ok": True, "dry_run": True, "blocked": doc["severity"] == "blocker", "issues": doc["issues"], "actions": actions}


def retry_storm_check() -> dict[str, Any]:
    now = datetime(2026, 7, 2, 20, 0, 0, tzinfo=BEIJING)
    old_success = iso(now - timedelta(days=1))
    recent_attempt = iso(now - timedelta(seconds=60))
    stale_attempt = iso(now - timedelta(seconds=1900))
    task = {
        "id": "fixture_interval_task",
        "enabled": True,
        "trigger": {"type": "interval", "every_seconds": 1800},
        "action": {"type": "command", "command": ["python", "--version"]},
        "policy": {
            "mode": "fixture",
            "risk": "low",
            "timeout_seconds": 5,
            "retry_interval_seconds": 300,
            "max_retry_count": 2,
            "latest_lag_seconds": 3600,
            "retry_exhausted_action": "record_and_continue",
        },
    }
    exhausted_recent = {
        "last_status": "failed",
        "retry_count": 900,
        "last_success_at": old_success,
        "last_attempt_at": recent_attempt,
    }
    exhausted_elapsed = {
        "last_status": "retry_exhausted",
        "retry_count": 900,
        "last_success_at": old_success,
        "last_attempt_at": stale_attempt,
    }
    retry_ready = {
        "last_status": "failed",
        "retry_count": 1,
        "retry_after": iso(now - timedelta(seconds=1)),
        "last_success_at": old_success,
        "last_attempt_at": recent_attempt,
    }
    state: dict[str, Any] = {"tasks": {"fixture_interval_task": {"retry_count": 2}}}
    failed_run = TaskRun(
        task_id="fixture_interval_task",
        ok=False,
        mode="fixture",
        due_reason="retry",
        started_at=iso(now),
        finished_at=iso(now),
        exit_code=1,
    )
    update_task_state(task, state, failed_run)
    updated_state = state["tasks"]["fixture_interval_task"]
    cases = {
        "exhausted_recent_not_due": get_due_reason(task, exhausted_recent, now),
        "exhausted_elapsed_due_interval": get_due_reason(task, exhausted_elapsed, now),
        "retry_under_limit_due_retry": get_due_reason(task, retry_ready, now),
        "update_after_max_sets_retry_exhausted": updated_state,
    }
    ok = (
        cases["exhausted_recent_not_due"] is None
        and cases["exhausted_elapsed_due_interval"] == "interval"
        and cases["retry_under_limit_due_retry"] == "retry"
        and updated_state.get("last_status") == "retry_exhausted"
        and "retry_after" not in updated_state
    )
    return {
        "ok": ok,
        "temp_only": True,
        "cases": cases,
        "assertion": "retry-exhausted interval tasks cool down by last_attempt_at; retry path remains available before max_retry_count",
    }


def write_governance_files() -> dict[str, str]:
    payloads = {
        "snapshot": snapshot(),
        "doctor": doctor(),
        "repair-plan-dry-run": repair_plan(),
        "validate": validate(),
        "metrics": metrics(),
    }
    paths: dict[str, str] = {}
    for name, payload in payloads.items():
        json_path = GOVERNANCE_ROOT / f"{name}.json"
        md_path = GOVERNANCE_ROOT / f"{name}.md"
        write_json(json_path, payload)
        md = [
            f"# {name}",
            "",
            f"- 更新时间：{iso(now_bj())}",
            f"- ok：{payload.get('ok')}",
            "",
            "```json",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        md_path.write_text("\n".join(md), encoding="utf-8")
        paths[name] = str(json_path)
    return paths


def loop(interval_seconds: int, dry_run: bool) -> int:
    lock = SingleInstanceLock(LOCK_PATH)
    if not lock.acquire():
        append_log("loop skipped because another scheduler instance holds the lock")
        return 0
    append_log(f"loop started interval={interval_seconds} dry_run={dry_run}")
    try:
        while True:
            started = time.monotonic()
            try:
                result = run_due(dry_run=dry_run)
                append_log(f"run_due => {json.dumps({'ok': result['ok'], 'due_count': result['due_count']}, ensure_ascii=False)}")
            except Exception as exc:
                write_heartbeat({"last_error": f"{type(exc).__name__}: {exc}"})
                append_log(f"error: {type(exc).__name__}: {exc}")
            elapsed = time.monotonic() - started
            time.sleep(max(1, interval_seconds - int(elapsed)))
    finally:
        lock.release()


def print_json(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.buffer.write(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified Codex scheduler runner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "doctor", "repair-plan", "validate", "metrics", "write-governance", "retry-storm-check", "task-drift", "override-plan"):
        sub.add_parser(name)
    migrate_parser = sub.add_parser("migrate-overrides")
    migrate_parser.add_argument("--apply", action="store_true")
    migrate_parser.add_argument("--confirm", default="")
    run_due_parser = sub.add_parser("run-due")
    run_due_parser.add_argument("--dry-run", action="store_true")
    loop_parser = sub.add_parser("loop")
    loop_parser.add_argument("--interval-seconds", type=int, default=DEFAULT_LOOP_SECONDS)
    loop_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "snapshot":
        print_json(snapshot())
    elif args.command == "doctor":
        print_json(doctor())
    elif args.command == "repair-plan":
        print_json(repair_plan())
    elif args.command == "validate":
        print_json(validate())
    elif args.command == "metrics":
        print_json(metrics())
    elif args.command == "write-governance":
        print_json({"ok": True, "paths": write_governance_files()})
    elif args.command == "retry-storm-check":
        print_json(retry_storm_check())
    elif args.command == "task-drift":
        print_json(task_drift_snapshot())
    elif args.command == "override-plan":
        print_json(task_override_plan())
    elif args.command == "migrate-overrides":
        print_json(migrate_task_overrides(apply=args.apply, confirm=args.confirm))
    elif args.command == "run-due":
        print_json(run_due(dry_run=args.dry_run))
    elif args.command == "loop":
        return loop(args.interval_seconds, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
