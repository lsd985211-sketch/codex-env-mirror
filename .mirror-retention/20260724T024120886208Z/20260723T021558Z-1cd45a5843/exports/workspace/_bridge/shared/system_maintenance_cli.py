#!/usr/bin/env python3
"""Unified local system maintenance command registry.

This CLI is intentionally small: it makes the maintenance surface discoverable
and runs only existing subsystem snapshot/doctor/repair-plan/validate/metrics
commands. Risky actions stay in subsystem-specific tools.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

try:
    from codex_reporter import enqueue_report
except ModuleNotFoundError:
    from shared.codex_reporter import enqueue_report

from platform_paths import resource_library_root  # noqa: E402


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


RESOURCE_ROOT = resource_library_root()
MAINTENANCE_ROOT = RESOURCE_ROOT / "文档" / "系统维护"
REPORT_ROOT = MAINTENANCE_ROOT / "异常报告"
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def py(*parts: str) -> list[str]:
    return [sys.executable, *parts]


REGISTRY: dict[str, dict[str, Any]] = {
    "scheduler": {
        "name": "统一定时调度",
        "auto_policy": "scheduler_may_run_due_tasks",
        "commands": {
            "snapshot": py("_bridge/shared/codex_scheduler_runner.py", "snapshot"),
            "doctor": py("_bridge/shared/codex_scheduler_runner.py", "doctor"),
            "repair-plan": py("_bridge/shared/codex_scheduler_runner.py", "repair-plan"),
            "apply": py("_bridge/shared/codex_scheduler_runner.py", "run-due"),
            "validate": py("_bridge/shared/codex_scheduler_runner.py", "validate"),
            "metrics": py("_bridge/shared/codex_scheduler_runner.py", "metrics"),
        },
    },
    "performance": {
        "name": "电脑性能维护",
        "auto_policy": "safe_apply_only_with_multi_window_fresh_evidence",
        "commands": {
            "snapshot": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "performance", "snapshot", "--observe-seconds", "5", "--top", "12", "--profile", "standard"),
            "doctor": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "performance", "doctor", "--observe-seconds", "5", "--top", "12", "--profile", "standard"),
            "repair-plan": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "performance", "repair-plan", "--observe-seconds", "5", "--top", "12", "--profile", "standard"),
            "apply": py("_bridge/shared/performance_maintenance_job.py", "--apply-safe", "--trigger-source", "system-maintenance-cli", "--trigger-mode", "controlled-apply"),
            "validate": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "performance", "validate", "--observe-seconds", "5", "--top", "12", "--profile", "quick"),
            "metrics": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "performance", "metrics", "--observe-seconds", "5", "--top", "12", "--profile", "quick"),
        },
    },
    "resource_process": {
        "name": "资源/MCP进程治理",
        "auto_policy": "may_cleanup_revalidated_orphan_roots",
        "commands": {
            "snapshot": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "resource-process", "snapshot"),
            "doctor": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "resource-process", "doctor"),
            "repair-plan": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "resource-process", "repair-plan"),
            "apply": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "resource-process", "cleanup", "--safe-apply", "--apply", "--min-age-minutes", "30"),
            "validate": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "resource-process", "validate"),
            "metrics": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "resource-process", "metrics"),
        },
    },
    "bridge_queue": {
        "name": "微信桥接队列",
        "auto_policy": "report_only_no_queue_mutation",
        "commands": {
            "snapshot": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "maintenance", "metrics"),
            "doctor": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "health"),
            "repair-plan": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "maintenance", "repair"),
            "validate": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "health"),
            "metrics": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "maintenance", "metrics"),
        },
    },
    "maintenance_reports": {
        "name": "Codex维护报告队列",
        "auto_policy": "report_queue_observe_and_dry_run_only",
        "commands": {
            "snapshot": py("_bridge/shared/codex_reporter.py", "snapshot"),
            "doctor": py("_bridge/shared/codex_reporter.py", "doctor"),
            "repair-plan": py("_bridge/shared/codex_reporter.py", "repair-plan"),
            "validate": py("_bridge/shared/codex_reporter.py", "validate"),
            "metrics": py("_bridge/shared/codex_reporter.py", "metrics"),
        },
    },
    "bridge_appserver": {
        "name": "桥接app-server",
        "auto_policy": "may_restart_only_when_idle_owner_is_single",
        "commands": {
            "snapshot": ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "_bridge/shared/restart-bridge-appserver-if-idle.ps1", "-Mode", "dry-run"],
            "doctor": ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "_bridge/shared/restart-bridge-appserver-if-idle.ps1", "-Mode", "dry-run"],
            "repair-plan": ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "_bridge/shared/restart-bridge-appserver-if-idle.ps1", "-Mode", "dry-run"],
            "apply": ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "_bridge/shared/restart-bridge-appserver-if-idle.ps1", "-Mode", "apply", "-Confirm", "restart-idle-bridge-appserver"],
            "validate": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "health"),
            "metrics": ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "_bridge/shared/restart-bridge-appserver-if-idle.ps1", "-Mode", "dry-run"],
        },
    },
    "email": {
        "name": "邮箱模块",
        "auto_policy": "send_due_only_report_send_failures",
        "commands": {
            "snapshot": py("_bridge/shared/email_scheduler.py", "snapshot"),
            "doctor": py("_bridge/shared/email_scheduler.py", "doctor"),
            "repair-plan": py("_bridge/shared/email_scheduler.py", "repair-plan"),
            "validate": py("_bridge/shared/email_scheduler.py", "validate"),
            "metrics": py("_bridge/shared/email_scheduler.py", "metrics"),
        },
    },
    "codex_config": {
        "name": "Codex启动基线/配置漂移",
        "auto_policy": "may_merge_only_repair_after_backup",
        "commands": {
            "snapshot": py("_bridge/codex_config_guard.py", "snapshot"),
            "doctor": py("_bridge/codex_config_guard.py", "doctor"),
            "repair-plan": py("_bridge/codex_config_guard.py", "repair-plan"),
            "apply": py("_bridge/codex_config_guard.py", "run-once", "--apply"),
            "validate": py("_bridge/codex_config_guard.py", "validate"),
            "metrics": py("_bridge/codex_config_guard.py", "metrics"),
        },
    },
    "codex_config_projection": {
        "name": "Codex/CC Switch配置投影",
        "auto_policy": "automatic_additions_only_explicit_apply_for_value_updates_no_implicit_deletion",
        "commands": {
            "snapshot": py("_bridge/codex_config_projection.py", "snapshot"),
            "doctor": py("_bridge/codex_config_projection.py", "doctor"),
            "repair-plan": py("_bridge/codex_config_projection.py", "plan"),
            "apply": py("_bridge/codex_config_projection.py", "apply"),
            "validate": py("_bridge/codex_config_projection.py", "validate"),
        },
    },
    "tool_exposure": {
        "name": "工具暴露层",
        "auto_policy": "read_only_config_cli_runtime_exposure_check",
        "commands": {
            "snapshot": py("_bridge/tool_exposure_doctor.py", "snapshot"),
            "doctor": py("_bridge/tool_exposure_doctor.py", "doctor"),
            "repair-plan": py("_bridge/tool_exposure_doctor.py", "repair-plan"),
            "validate": py("_bridge/tool_exposure_doctor.py", "validate"),
            "metrics": py("_bridge/tool_exposure_doctor.py", "metrics"),
        },
    },
    "local_pmb_memory": {
        "name": "本机PMB记忆本体",
        "auto_policy": "may_start_singleton_local_daemon_and_report_migration_cutover",
        "commands": {
            "snapshot": py("_bridge/local_pmb_memory.py", "snapshot"),
            "doctor": py("_bridge/local_pmb_memory.py", "doctor"),
            "repair-plan": py("_bridge/local_pmb_memory.py", "repair-plan"),
            "apply": py("_bridge/local_pmb_memory.py", "daemon-ensure"),
            "validate": py("_bridge/local_pmb_memory.py", "validate"),
            "metrics": py("_bridge/local_pmb_memory.py", "metrics"),
        },
    },
    "codex_workflow": {
        "name": "Codex工作流守门",
        "auto_policy": "read_only_memory_preflight_and_iteration_finalization_gate",
        "commands": {
            "snapshot": py("_bridge/codex_workflow_gate.py", "snapshot"),
            "doctor": py("_bridge/codex_workflow_gate.py", "doctor"),
            "repair-plan": py("_bridge/codex_workflow_gate.py", "repair-plan"),
            "validate": py("_bridge/codex_workflow_gate.py", "validate"),
            "metrics": py("_bridge/codex_workflow_gate.py", "metrics"),
        },
    },
    "backup_hygiene": {
        "name": "备份治理",
        "auto_policy": "dry_run_plus_controlled_safe_apply_for_archive_only",
        "commands": {
            "snapshot": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "backup-hygiene", "snapshot"),
            "doctor": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "backup-hygiene", "doctor"),
            "repair-plan": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "backup-hygiene", "repair-plan"),
            "apply": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "backup-hygiene", "apply", "--confirm", "archive-old-backups"),
            "validate": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "backup-hygiene", "validate"),
            "metrics": py("_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "backup-hygiene", "metrics"),
        },
    },
    "record_store": {
        "name": "全局记录存储治理",
        "auto_policy": "read_only_inventory_and_dry_run_archive_plan",
        "commands": {
            "snapshot": py("_bridge/shared/record_store_maintenance.py", "snapshot"),
            "doctor": py("_bridge/shared/record_store_maintenance.py", "doctor"),
            "repair-plan": py("_bridge/shared/record_store_maintenance.py", "repair-plan"),
            "validate": py("_bridge/shared/record_store_maintenance.py", "validate"),
            "metrics": py("_bridge/shared/record_store_maintenance.py", "metrics"),
            "query": py("_bridge/shared/record_store_maintenance.py", "query"),
        },
    },
    "system_state": {
        "name": "系统状态索引",
        "auto_policy": "derived_index_only_no_business_mutation",
        "commands": {
            "snapshot": py("_bridge/system_state_index.py", "snapshot"),
            "doctor": py("_bridge/system_state_index.py", "doctor"),
            "repair-plan": py("_bridge/system_state_index.py", "repair-plan"),
            "validate": py("_bridge/system_state_index.py", "validate"),
            "metrics": py("_bridge/system_state_index.py", "metrics"),
        },
    },
    "popup_window": {
        "name": "Codex/MCP弹窗归因",
        "auto_policy": "read_only_attribution_no_function_reduction",
        "commands": {
            "snapshot": py("_bridge/popup_window_doctor.py", "snapshot"),
            "doctor": py("_bridge/popup_window_doctor.py", "doctor"),
            "repair-plan": py("_bridge/popup_window_doctor.py", "doctor"),
            "validate": py("_bridge/popup_window_doctor.py", "validate"),
            "metrics": py("_bridge/popup_window_doctor.py", "metrics"),
        },
    },
    "cli_anything": {
        "name": "CLI-Anything/cli-hub",
        "auto_policy": "trusted_project_read_only_discovery_install_requires_explicit_task",
        "commands": {
            "snapshot": py("_bridge/cli_anything_governance.py", "snapshot"),
            "doctor": py("_bridge/cli_anything_governance.py", "doctor"),
            "repair-plan": py("_bridge/cli_anything_governance.py", "doctor"),
            "validate": py("_bridge/cli_anything_governance.py", "validate"),
            "metrics": py("_bridge/cli_anything_governance.py", "metrics"),
        },
    },
}


def run_json(command: list[str], timeout: int = 240) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **NO_WINDOW_KW,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "command": command,
            "stdout_preview": (exc.stdout or "")[:2000] if isinstance(exc.stdout, str) else "",
            "stderr_preview": (exc.stderr or "")[:2000] if isinstance(exc.stderr, str) else "",
        }
    raw = (proc.stdout or "").strip()
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {
            "ok": proc.returncode == 0,
            "parse_error": "stdout_not_json",
            "stdout_preview": raw[:2000],
            "stderr_preview": (proc.stderr or "")[:2000],
        }
    if isinstance(parsed, dict):
        parsed.setdefault("ok", proc.returncode == 0)
        parsed["_command"] = command
        parsed["_returncode"] = proc.returncode
        if proc.stderr:
            parsed["_stderr_preview"] = proc.stderr[:2000]
        return parsed
    return {"ok": proc.returncode == 0, "value": parsed, "_command": command, "_returncode": proc.returncode}


def selected_systems(targets: list[str], all_targets: bool) -> list[str]:
    if all_targets or not targets:
        return list(REGISTRY)
    unknown = [item for item in targets if item not in REGISTRY]
    if unknown:
        raise SystemExit(f"unknown system(s): {', '.join(unknown)}")
    return targets


def run_action(action: str, targets: list[str], all_targets: bool) -> dict[str, Any]:
    selected = selected_systems(targets, all_targets)
    started = time.perf_counter()

    def execute(key: str) -> tuple[str, dict[str, Any]]:
        command = REGISTRY[key]["commands"].get(action)
        if not command:
            return key, {"ok": False, "reason": f"action {action} not registered"}
        owner_started = time.perf_counter()
        result = run_json([str(part) for part in command])
        result.setdefault("_elapsed_ms", round((time.perf_counter() - owner_started) * 1000, 1))
        return key, result

    unordered: dict[str, Any] = {}
    parallel = action != "apply" and len(selected) > 1
    if parallel:
        with ThreadPoolExecutor(max_workers=min(6, len(selected))) as pool:
            futures = {pool.submit(execute, key): key for key in selected}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    name, result = future.result()
                    unordered[name] = result
                except Exception as exc:
                    unordered[key] = {
                        "ok": False,
                        "reason": "maintenance_owner_execution_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
    else:
        for key in selected:
            name, result = execute(key)
            unordered[name] = result
    rows = {key: unordered[key] for key in selected}
    return {
        "schema": "system-maintenance.aggregate.v1",
        "ok": not any(item.get("ok") is False for item in rows.values() if isinstance(item, dict)),
        "generated_at": now_iso(),
        "action": action,
        "execution": {
            "mode": "parallel_read_only" if parallel else "serial",
            "worker_count": min(6, len(selected)) if parallel else 1,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        },
        "systems": rows,
    }


def list_registry() -> dict[str, Any]:
    return {
        "schema": "system-maintenance.registry.v1",
        "ok": True,
        "generated_at": now_iso(),
        "systems": {
            key: {
                "name": value["name"],
                "auto_policy": value["auto_policy"],
                "actions": sorted(value["commands"].keys()),
            }
            for key, value in REGISTRY.items()
        },
    }


def apply_gap_report() -> dict[str, Any]:
    review_items: list[dict[str, Any]] = []
    for key, value in REGISTRY.items():
        commands = value.get("commands") if isinstance(value.get("commands"), dict) else {}
        policy = str(value.get("auto_policy") or "")
        has_repair_plan = "repair-plan" in commands
        has_apply = "apply" in commands
        policy_allows_apply = any(term in policy for term in ("may_", "controlled", "safe_apply", "apply", "archive"))
        policy_report_only = any(term in policy for term in ("report_only", "read_only", "dry_run_only"))
        if has_repair_plan and policy_allows_apply and not policy_report_only and not has_apply:
            review_items.append(
                {
                    "system": key,
                    "severity": "review",
                    "code": "policy_mentions_apply_but_no_apply_command",
                    "auto_policy": policy,
                    "reason": "The registry policy implies a controlled apply path, but the registry exposes only planning/readback actions.",
                }
            )
    return {
        "schema": "system-maintenance.apply_gap_report.v1",
        "ok": True,
        "generated_at": now_iso(),
        "gap_count": len(review_items),
        "review_items": review_items,
        "rule": "Only low-risk, owner-bounded, explicitly confirmed actions should get an apply command; other repair-plan outputs remain review-only.",
    }


def enqueue_report_request(target: str, payload: dict[str, Any]) -> dict[str, Any]:
    return enqueue_report(
        kind=f"{target}_maintenance",
        title=f"系统维护报告 - {target}",
        evidence=payload,
        policy=str(REGISTRY[target].get("auto_policy", "report_only")),
        priority=40,
    )


def run_record_store(
    command: str,
    *,
    term: str = "",
    limit: int = 20,
    apply: bool = False,
    area: str = "",
    kind: str = "",
    status: str = "",
    since: str = "",
    source_contains: str = "",
) -> dict[str, Any]:
    args = py("_bridge/shared/record_store_maintenance.py", command)
    if command == "query":
        if term:
            args.extend(["--term", term])
        args.extend(["--limit", str(limit)])
        if area:
            args.extend(["--area", area])
        if kind:
            args.extend(["--kind", kind])
        if status:
            args.extend(["--status", status])
        if since:
            args.extend(["--since", since])
        if source_contains:
            args.extend(["--source-contains", source_contains])
    if command == "index" and apply:
        args.append("--apply")
    return run_json(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified system maintenance command registry")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("apply-gap")
    for action in ("snapshot", "doctor", "repair-plan", "validate", "metrics"):
        p = sub.add_parser(action)
        p.add_argument("--all", action="store_true")
        p.add_argument("--system", action="append", default=[])
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--system", action="append", required=True, choices=sorted(REGISTRY.keys()))
    p_report = sub.add_parser("report")
    p_report.add_argument("--target", required=True, choices=sorted(REGISTRY.keys()))
    p_report.add_argument("--action", default="doctor", choices=["snapshot", "doctor", "repair-plan", "validate", "metrics"])
    p_record = sub.add_parser("record-store")
    p_record.add_argument("action", choices=["index", "query"])
    p_record.add_argument("--apply", action="store_true")
    p_record.add_argument("--term", default="")
    p_record.add_argument("--limit", type=int, default=20)
    p_record.add_argument("--area", default="")
    p_record.add_argument("--kind", default="")
    p_record.add_argument("--status", default="")
    p_record.add_argument("--since", default="")
    p_record.add_argument("--source-contains", default="")
    args = parser.parse_args(argv)

    if args.command == "list":
        payload = list_registry()
    elif args.command == "apply-gap":
        payload = apply_gap_report()
    elif args.command == "apply":
        payload = run_action("apply", args.system, False)
    elif args.command == "report":
        payload = run_action(args.action, [args.target], False)
        report_request = enqueue_report_request(args.target, payload)
        payload["report_request"] = report_request
        payload["report_request_path"] = report_request.get("request_path", "")
    elif args.command == "record-store":
        payload = run_record_store(
            args.action,
            term=args.term,
            limit=args.limit,
            apply=bool(args.apply),
            area=args.area,
            kind=args.kind,
            status=args.status,
            since=args.since,
            source_contains=args.source_contains,
        )
    else:
        payload = run_action(args.command, args.system, bool(args.all))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
