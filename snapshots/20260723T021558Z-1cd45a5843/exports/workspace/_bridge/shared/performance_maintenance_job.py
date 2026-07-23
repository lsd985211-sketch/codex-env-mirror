#!/usr/bin/env python3
"""Safe 30-minute workstation performance maintenance job.

This job may apply only narrow, pre-approved repairs. Bridge queue and email
delivery anomalies are report-only by design.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from codex_reporter import enqueue_report, stable_digest, write_raw_payload
from platform_paths import resource_library_root  # noqa: E402


RESOURCE_ROOT = resource_library_root()
MAINTENANCE_ROOT = RESOURCE_ROOT / "文档" / "系统维护"
REPORT_ROOT = MAINTENANCE_ROOT / "异常报告"
RECORD_ROOT = MAINTENANCE_ROOT / "执行记录"
RUNTIME_ROOT = MAINTENANCE_ROOT / "运行态"
LOCK_PATH = RUNTIME_ROOT / "performance-maintenance.lock"
STATE_PATH = RUNTIME_ROOT / "performance-maintenance-state.json"
CODEX_IDLE_LOAD_ISSUE_CODES = {"codex_idle_sustained_load", "codex_app_or_renderer_load"}
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


@dataclass
class Step:
    name: str
    ok: bool
    applied: bool = False
    skipped: bool = False
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


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
        self.handle.write(json.dumps({"pid": os.getpid(), "started_at": now_iso()}, ensure_ascii=False))
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_json(command: list[str], timeout: int = 300) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def issue_codes(value: Any, *, limit: int = 100) -> list[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(item, dict):
            code = item.get("code")
            if isinstance(code, str) and code.strip():
                found.add(code.strip())
            for key, child in item.items():
                if key in {"generated_at", "timestamp", "updated_at", "created_at", "pid"}:
                    continue
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(found)


def compact_step_record(step: Step) -> dict[str, Any]:
    payload = step.payload if isinstance(step.payload, dict) else {}
    return {
        "name": step.name,
        "ok": bool(step.ok),
        "applied": bool(step.applied),
        "skipped": bool(step.skipped),
        "reason": step.reason or payload.get("reason", ""),
        "schema": payload.get("schema", ""),
        "status": payload.get("status", ""),
        "severity": payload.get("severity", ""),
        "issue_codes": issue_codes(payload),
    }


def latest_record_path() -> str:
    if not RECORD_ROOT.exists():
        return ""
    paths = sorted(RECORD_ROOT.glob("*-performance-maintenance.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    return str(paths[0]) if paths else ""


def build_trigger(args: argparse.Namespace | None = None, request_id: str = "") -> dict[str, Any]:
    if args is None:
        return {}
    return {
        "source": str(getattr(args, "trigger_source", "") or "manual"),
        "user": str(getattr(args, "trigger_user", "") or ""),
        "account": str(getattr(args, "trigger_account", "") or ""),
        "mode": str(getattr(args, "trigger_mode", "") or "manual"),
        "request_id": request_id,
    }


def request_codex_report(kind: str, title: str, payload: dict[str, Any], policy: str = "report_only") -> dict[str, Any]:
    return enqueue_report(kind=kind, title=title, evidence=payload, policy=policy, priority=30)


def should_persist_maintenance_record(
    previous_state: dict[str, Any],
    *,
    state_changed: bool,
    action_applied: bool,
    ok: bool,
    now: datetime | None = None,
    heartbeat_hours: float = 6.0,
) -> bool:
    """Persist changes/failures immediately and unchanged success only periodically."""
    if state_changed or action_applied or not ok:
        return True
    last_text = str(previous_state.get("last_persisted_at") or "").strip()
    if not last_text:
        return True
    try:
        last = datetime.fromisoformat(last_text)
    except ValueError:
        return True
    current = now or datetime.now(timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return current - last >= timedelta(hours=max(0.25, float(heartbeat_hours)))


def powershell_json(script: str, timeout: int = 60) -> dict[str, Any]:
    return run_json(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        timeout=timeout,
    )


def backup_defender_preferences() -> Path:
    RECORD_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RECORD_ROOT / f"{stamp}-defender-preferences-backup.json"
    script = r"""
$ErrorActionPreference = 'Stop'
$pref = Get-MpPreference
[pscustomobject]@{
  generated_at = (Get-Date).ToString('o')
  exclusion_path = @($pref.ExclusionPath)
  controlled_folder_access_allowed_applications = @($pref.ControlledFolderAccessAllowedApplications)
  enable_controlled_folder_access = [int]$pref.EnableControlledFolderAccess
  disable_realtime_monitoring = [bool]$pref.DisableRealtimeMonitoring
} | ConvertTo-Json -Depth 5
"""
    payload = powershell_json(script, timeout=60)
    write_json(path, payload)
    return path


def apply_defender_repairs(performance_doctor: dict[str, Any], apply_safe: bool) -> Step:
    plan = run_json([sys.executable, "_bridge/defender_governance.py", "repair-plan"], timeout=180)
    auto_actions = plan.get("auto_apply_actions") if isinstance(plan.get("auto_apply_actions"), list) else []
    manual_actions = plan.get("manual_only_actions") if isinstance(plan.get("manual_only_actions"), list) else []
    if not auto_actions and not manual_actions:
        return Step("defender_safe_repair", ok=True, skipped=True, reason="no_safe_defender_repairs")
    if not apply_safe:
        return Step(
            "defender_safe_repair",
            ok=True,
            skipped=True,
            reason="dry_run",
            payload=plan,
        )
    if not auto_actions:
        return Step(
            "defender_safe_repair",
            ok=True,
            skipped=True,
            reason="manual_only_defender_actions_present",
            payload=plan,
        )
    apply_result = run_json([sys.executable, "_bridge/defender_governance.py", "apply"], timeout=300)
    return Step(
        "defender_safe_repair",
        ok=bool(apply_result.get("ok")),
        applied=True,
        payload=apply_result,
    )


def issue_pids(payload: dict[str, Any], codes: str | set[str]) -> set[int]:
    wanted = {codes} if isinstance(codes, str) else set(codes)
    pids: set[int] = set()
    for issue in payload.get("issues", []) if isinstance(payload.get("issues"), list) else []:
        if isinstance(issue, dict) and issue.get("code") in wanted and issue.get("pid") is not None:
            try:
                pids.add(int(issue["pid"]))
            except (TypeError, ValueError):
                pass
    return pids


def codex_issue_roles(payload: dict[str, Any], codes: str | set[str]) -> dict[str, str]:
    wanted = {codes} if isinstance(codes, str) else set(codes)
    roles: dict[str, str] = {}
    for issue in payload.get("issues", []) if isinstance(payload.get("issues"), list) else []:
        if not isinstance(issue, dict) or issue.get("code") not in wanted or issue.get("pid") is None:
            continue
        try:
            pid = str(int(issue["pid"]))
        except (TypeError, ValueError):
            continue
        roles[pid] = str(issue.get("process_role") or "")
    return roles


def run_maintenance(apply_safe: bool, trigger: dict[str, Any] | None = None) -> dict[str, Any]:
    steps: list[Step] = []
    reports: list[str] = []
    trigger = trigger or {}

    performance = run_json(
        [
            sys.executable,
            "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
            "performance",
            "doctor",
            "--observe-seconds",
            "2",
            "--top",
            "8",
            "--profile",
            "quick",
        ],
        timeout=180,
    )
    steps.append(Step("performance_doctor", ok=bool(performance.get("ok")), payload=performance))

    mcp_session_doc = run_json(
        [sys.executable, "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "mcp-session", "doctor"],
        timeout=180,
    )
    steps.append(Step("mcp_session_doctor", ok=bool(mcp_session_doc.get("ok")), payload=mcp_session_doc))
    if not mcp_session_doc.get("ok"):
        mcp_session_plan = run_json(
            [sys.executable, "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "mcp-session", "repair-plan"],
            timeout=180,
        )
        steps.append(Step("mcp_session_repair_plan", ok=bool(mcp_session_plan.get("ok")), payload=mcp_session_plan))

    pmb_doc = run_json([sys.executable, "_bridge/local_pmb_memory.py", "doctor"], timeout=240)
    steps.append(Step("local_pmb_memory_doctor", ok=bool(pmb_doc.get("ok")), payload=pmb_doc))
    pmb_issue_codes = {
        str(item.get("code") or "")
        for item in pmb_doc.get("issues", [])
        if isinstance(item, dict)
    }
    if apply_safe and "pmb_daemon_not_running" in pmb_issue_codes:
        pmb_ensure = run_json([sys.executable, "_bridge/local_pmb_memory.py", "daemon-ensure"], timeout=300)
        steps.append(Step("local_pmb_daemon_ensure", ok=bool(pmb_ensure.get("ok")), applied=True, payload=pmb_ensure))

    resource_group_args: list[str] = []
    resource_safe_args = ["--safe-apply"] if apply_safe else []
    resource_dry = run_json(
        [
            sys.executable,
            "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
            "resource-process",
            "cleanup",
            *resource_group_args,
            *resource_safe_args,
        ],
        timeout=240,
    )
    steps.append(Step("resource_cleanup_dry_run", ok=bool(resource_dry.get("ok")), payload=resource_dry))
    if apply_safe and int(resource_dry.get("selected_count") or 0) > 0:
        resource_apply = run_json(
            [
                sys.executable,
                "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
                "resource-process",
                "cleanup",
                *resource_group_args,
                *resource_safe_args,
                "--apply",
            ],
            timeout=300,
        )
        steps.append(Step("resource_cleanup_apply", ok=bool(resource_apply.get("ok")), applied=True, payload=resource_apply))

    bridge_metrics = run_json(
        [sys.executable, "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py", "maintenance", "metrics"],
        timeout=240,
    )
    queue = bridge_metrics.get("queue") if isinstance(bridge_metrics.get("queue"), dict) else {}
    bridge_issue = (
        bridge_metrics.get("ok") is False
        or int(queue.get("pending") or 0) > 0
        or int(queue.get("active") or 0) > 0
        or int(queue.get("queued_for_codex") or 0) > 0
        or int(queue.get("sent_to_codex") or 0) > 0
        or int(queue.get("processing") or 0) > 0
        or int(queue.get("supplement_waiting_mcp_ack") or 0) > 0
    )
    steps.append(Step("bridge_queue_pre_appserver_restart_guard", ok=True, payload=bridge_metrics))

    codex_load_pids = issue_pids(performance, CODEX_IDLE_LOAD_ISSUE_CODES)
    codex_load_roles = codex_issue_roles(performance, CODEX_IDLE_LOAD_ISSUE_CODES)
    if codex_load_pids:
        appserver_dry = run_json(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "_bridge/shared/restart-bridge-appserver-if-idle.ps1",
                "-Mode",
                "dry-run",
            ],
            timeout=240,
        )
        steps.append(Step("bridge_appserver_idle_restart_dry_run", ok=bool(appserver_dry.get("ok")), payload=appserver_dry))
    else:
        appserver_dry = {"ok": True, "skipped": True, "reason": "no_codex_load_issue"}
        steps.append(Step("bridge_appserver_idle_restart_dry_run", ok=True, skipped=True, reason="no_codex_load_issue", payload=appserver_dry))
    owner_pids = {
        int(item.get("pid"))
        for item in appserver_dry.get("owners_before", [])
        if isinstance(item, dict) and str(item.get("pid") or "").isdigit()
    }
    bridge_hot_pids = {
        pid for pid in (owner_pids & codex_load_pids) if codex_load_roles.get(str(pid)) in {"", "bridge_app_server_18791"}
    }
    duplicate_watchers_selected = any(
        item.get("group") == "codex_app_live_watch"
        for item in (resource_dry.get("selected") or [])
        if isinstance(item, dict)
    )
    should_restart_bridge_appserver = bool(
        apply_safe
        and appserver_dry.get("would_restart")
        and bridge_hot_pids
        and not duplicate_watchers_selected
        and not bridge_issue
    )
    if not should_restart_bridge_appserver:
        if not apply_safe:
            restart_skip_reason = "dry_run"
        elif bridge_issue:
            restart_skip_reason = "bridge_queue_not_idle_pre_restart"
        elif duplicate_watchers_selected:
            restart_skip_reason = "duplicate_live_watch_cleanup_first"
        elif not appserver_dry.get("would_restart"):
            restart_skip_reason = "bridge_appserver_not_restartable_or_queue_not_idle"
        elif not owner_pids:
            restart_skip_reason = "bridge_appserver_owner_not_found"
        elif not codex_load_pids:
            restart_skip_reason = "no_codex_idle_load_issue_pid"
        elif not (owner_pids & codex_load_pids):
            restart_skip_reason = "hot_codex_pids_do_not_own_bridge_appserver"
        else:
            restart_skip_reason = "hot_codex_pid_role_not_bridge_appserver"
        steps.append(
            Step(
                "bridge_appserver_idle_restart_decision",
                ok=True,
                skipped=True,
                reason=restart_skip_reason,
                payload={
                    "owner_pids": sorted(owner_pids),
                    "codex_load_pids": sorted(codex_load_pids),
                    "bridge_hot_pids": sorted(bridge_hot_pids),
                    "codex_load_roles": codex_load_roles,
                    "would_restart": bool(appserver_dry.get("would_restart")),
                    "apply_safe": apply_safe,
                    "duplicate_watchers_selected": duplicate_watchers_selected,
                    "bridge_issue": bool(bridge_issue),
                    "bridge_queue": queue,
                },
            )
        )
    if should_restart_bridge_appserver:
        appserver_apply = run_json(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "_bridge/shared/restart-bridge-appserver-if-idle.ps1",
                "-Mode",
                "apply",
                "-Confirm",
                "restart-idle-bridge-appserver",
            ],
            timeout=300,
        )
        steps.append(Step("bridge_appserver_idle_restart_apply", ok=bool(appserver_apply.get("ok")), applied=True, payload=appserver_apply))

    config_doc = run_json([sys.executable, "_bridge/codex_config_guard.py", "doctor"], timeout=180)
    steps.append(Step("codex_startup_baseline_guard_doctor", ok=bool(config_doc.get("ok")), payload=config_doc))
    if apply_safe and not config_doc.get("ok"):
        config_plan = run_json([sys.executable, "_bridge/codex_config_guard.py", "repair-plan"], timeout=180)
        steps.append(Step("codex_startup_baseline_guard_repair_plan", ok=bool(config_plan.get("ok")), payload=config_plan))
        config_apply = run_json([sys.executable, "_bridge/codex_config_guard.py", "run-once", "--apply"], timeout=240)
        steps.append(Step("codex_startup_baseline_guard_apply", ok=bool(config_apply.get("ok")), applied=True, payload=config_apply))

    encoding_doc = run_json([sys.executable, "_bridge/encoding_governance.py", "doctor"], timeout=180)
    steps.append(Step("encoding_governance_doctor", ok=True, payload=encoding_doc))
    if encoding_doc.get("ok") is False:
        report = request_codex_report(
            "encoding_governance",
            "编码/乱码治理异常报告",
            encoding_doc,
            policy="codex_report_only_no_encoding_mutation",
        )
        reports.append(report["request_path"])

    defender_step = apply_defender_repairs(performance, apply_safe=apply_safe)
    steps.append(defender_step)

    steps.append(Step("bridge_queue_report_only_check", ok=True, payload=bridge_metrics))
    if bridge_issue:
        report = request_codex_report("bridge_queue", "微信桥接队列异常报告", bridge_metrics, policy="codex_report_only_no_queue_mutation")
        reports.append(report["request_path"])

    email_doc = run_json([sys.executable, "_bridge/shared/email_scheduler.py", "doctor"], timeout=240)
    steps.append(Step("email_report_only_check", ok=True, payload=email_doc))
    if email_doc.get("ok") is False or str(email_doc.get("severity") or "").lower() not in {"", "ok"}:
        report = request_codex_report("email", "邮件发送/调度异常报告", email_doc, policy="codex_report_only_no_email_state_mutation")
        reports.append(report["request_path"])

    main_codex_pids = codex_load_pids - owner_pids
    if main_codex_pids:
        maintenance_run_id = str(trigger.get("request_id") or "")
        report = request_codex_report(
            "codex_main_process",
            "Codex主进程异常报告",
            {
                "maintenance_run_id": maintenance_run_id,
                "performance_doctor": performance,
                "main_codex_issue_pids": sorted(main_codex_pids),
            },
            policy="codex_report_only_no_main_process_kill",
        )
        reports.append(report["request_path"])

    payload = {
        "schema": "performance-maintenance-job.v1",
        "ok": not any(step.ok is False and not step.name.endswith("_doctor") for step in steps),
        "generated_at": now_iso(),
        "apply_safe": apply_safe,
        "trigger": trigger,
        "steps": [step.__dict__ for step in steps],
        "reports": reports,
        "contract": {
            "performance_evidence": "multi_window_required_for_auto_decisions",
            "transient_service_spike_policy": "report_or_observe; no cleanup/restart from single-window spikes",
            "service_tool_classes": ["codegraph", "playwright", "chrome-devtools", "markitdown", "browser_webview"],
            "bridge_queue_mutation": False,
            "email_state_mutation_on_failure": False,
            "defender_realtime_disable": False,
            "defender_auto_apply_scope": "required_exclusion_or_cfa_drift_and_low_impact_scan_policy_only",
            "defender_no_churn_policy": "if defender_governance policy is already ok, do not rewrite preferences just because MsMpEng is hot",
            "defender_legacy_cleanup": "manual_only",
            "external_webview_mutation": False,
            "external_webview_policy": "Windows Search, cc-switch, clash-verge, and other external WebView owners are report/observe only; preserve user data dirs",
            "nvidia_display_container_mutation": False,
            "nvidia_display_policy": "observe repeated samples and NVIDIA log growth first; do not stop NVIDIA Display Container automatically",
            "main_codex_kill": False,
            "dashboard_live_watch_policy": "only sync Codex turns while dashboard has recent activity; inactive dashboards must not poll thread/turns/list",
            "duplicate_live_watch_cleanup_allowed": True,
            "bridge_appserver_restart_allowed_when_idle": True,
            "resource_orphan_cleanup_allowed": True,
            "resource_orphan_cleanup_scope": "all_revalidated_orphan_roots_via_resource_process_doctor",
            "codex_startup_baseline_merge_only_repair_allowed": True,
            "codex_startup_baseline_repairer": "codex_config_guard -> codex_state_repair",
            "encoding_governance_policy": "read_only_report_only; no auto rename/delete/convert",
            "encoding_baseline": "UTF-8 for Chinese paths, resource library docs, JSON, Markdown, TOML, and script output",
            "mcp_session_transport_repair_allowed": False,
            "mcp_session_recovery_policy": "fallback_first_report_plan_then_operator_or_session_refresh",
            "local_pmb_memory_daemon_policy": "must_stay_warm_singleton; safe maintenance may start missing daemon; no routine kill-all",
            "local_pmb_memory_codex_mcp_policy": "Codex uses lightweight pmb mcp proxy to warm daemon with fallback disabled",
            "codex_idle_load_issue_codes": sorted(CODEX_IDLE_LOAD_ISSUE_CODES),
            "codex_active_workload_policy": "observe_only_no_restart",
        },
    }
    RECORD_ROOT.mkdir(parents=True, exist_ok=True)
    record = RECORD_ROOT / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-performance-maintenance.json"
    compact_steps = [compact_step_record(step) for step in steps]
    semantic_state = {
        "apply_safe": apply_safe,
        "steps": compact_steps,
        "report_count": len(reports),
    }
    semantic_digest = stable_digest(semantic_state)
    previous_state = read_json(STATE_PATH)
    state_changed = semantic_digest != str(previous_state.get("semantic_digest") or "")
    action_applied = any(step.applied for step in steps)
    raw_ref: dict[str, Any] = {}
    if state_changed or action_applied:
        request_id = str(trigger.get("request_id") or record.stem)
        raw_ref = write_raw_payload("performance_maintenance_job", request_id, payload)
    compact_record = {
        "schema": "performance-maintenance-record.v2",
        "ok": payload["ok"],
        "generated_at": payload["generated_at"],
        "apply_safe": apply_safe,
        "trigger": trigger,
        "steps": compact_steps,
        "reports": reports,
        "semantic_digest": semantic_digest,
        "state_changed": state_changed,
        "raw_ref": raw_ref,
        "storage_policy": "compact record on change/failure/action and periodic audit; unchanged successful runs update state only",
    }
    persist_record = should_persist_maintenance_record(
        previous_state,
        state_changed=state_changed,
        action_applied=action_applied,
        ok=bool(payload["ok"]),
    )
    persisted_at = now_iso() if persist_record else str(previous_state.get("last_persisted_at") or "")
    record_path = str(record) if persist_record else str(previous_state.get("record_path") or "")
    if persist_record:
        write_json(record, compact_record)
    write_json(
        STATE_PATH,
        {
            "schema": "performance-maintenance-state.v2",
            "updated_at": now_iso(),
            "semantic_digest": semantic_digest,
            "record_path": record_path,
            "last_persisted_at": persisted_at,
            "suppressed_unchanged_success_runs": 0 if persist_record else int(previous_state.get("suppressed_unchanged_success_runs") or 0) + 1,
            "raw_ref": raw_ref or previous_state.get("raw_ref", {}),
        },
    )
    payload["record_path"] = record_path
    payload["record_compact"] = True
    payload["record_suppressed"] = not persist_record
    payload["record_suppression_reason"] = "unchanged_success_within_periodic_audit_window" if not persist_record else ""
    payload["record_state_changed"] = state_changed
    payload["record_raw_ref"] = raw_ref or previous_state.get("raw_ref", {})
    return payload


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Safe workstation performance maintenance job")
    parser.add_argument("--apply-safe", action="store_true", help="Apply only pre-approved safe repairs")
    parser.add_argument("--trigger-source", default="manual", help="Audit source, for example mobile-repair or scheduler")
    parser.add_argument("--trigger-user", default="", help="External user that requested the job")
    parser.add_argument("--trigger-account", default="", help="Account/slot that accepted the trigger")
    parser.add_argument("--trigger-mode", default="manual", help="Trigger mode, for example manual or scheduled")
    parser.add_argument("--request-id", default="", help="Caller request id for audit correlation")
    args = parser.parse_args(argv)
    request_id = args.request_id or f"maintenance-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    trigger = build_trigger(args, request_id=request_id)
    lock = SingleInstanceLock(LOCK_PATH)
    if not lock.acquire():
        payload = {
            "schema": "performance-maintenance-job.v1",
            "ok": True,
            "skipped": True,
            "reason": "lock_held",
            "generated_at": now_iso(),
            "trigger": trigger,
            "lock_path": str(LOCK_PATH),
            "latest_record_path": latest_record_path(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    try:
        payload = run_maintenance(apply_safe=bool(args.apply_safe), trigger=trigger)
    finally:
        lock.release()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
