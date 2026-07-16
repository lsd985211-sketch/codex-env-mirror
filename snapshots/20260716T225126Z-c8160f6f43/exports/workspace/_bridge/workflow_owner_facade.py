#!/usr/bin/env python3
"""Contract-driven lifecycle facade over existing workflow owners.

Ownership: versioned action/receipt contracts, thin owner adapters, normalized
status, and lightweight run references that point back to owner state.
Non-goals: business execution logic, permission escalation, central queues,
worker scheduling, retry policy, or a second business-state database.
State behavior: writes only bounded workflow run-reference JSON files after a
run attempt; mutable business state remains owned by the selected owner.
Caller context: codex_workflow_entry plan/run/status/wait/cancel and validators.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from bounded_output import bounded_payload, governed_cli_payload
from maintenance_capability_registry import resolve_capability
from mcp_route_policy import call_priority_pack, preferred_direct_hub_tool
from shared.json_cli import configure_utf8_stdio, now_iso, print_json
from workflow_action_synthesis import synthesize
from workflow_failure_diagnostics import extract_failure_diagnostics


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
RUNS_DIR = BRIDGE / "runtime" / "workflow_runs"
PYTHON = Path(sys.executable)

ACTION_SCHEMA = "workflow.action.v1"
RECEIPT_SCHEMA = "workflow.receipt.v1"
RUN_REF_SCHEMA = "workflow.run_ref.v1"
TERMINAL_STATUSES = {"completed", "failed", "blocked", "cancelled", "unsupported"}
NORMALIZED_STATUSES = {
    "planned",
    "submitted",
    "running",
    "completed",
    "failed",
    "blocked",
    "deferred",
    "cancel_requested",
    "cancelled",
    "unsupported",
    "handoff_required",
}
READ_ONLY_MAINTENANCE_COMMANDS = {
    "snapshot",
    "doctor",
    "validate",
    "plan",
    "metrics",
    "status",
    "progress",
    "inspect",
    "query",
    "state-query",
    "commands",
    "interfaces",
    "recommend",
    "env",
    "probe",
    "probe-suite",
    "batch-plan",
    "task-drift",
    "override-plan",
}
MUTATING_MAINTENANCE_FLAGS = {
    "--apply",
    "--write",
    "--delete",
    "--remove",
    "--install",
    "--dispatch",
    "--send",
    "--reset",
    "--archive",
    "--repair",
}

OWNER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "resource": {
        "operations": ["resource_job"],
        "lifecycle": ["run", "status", "wait"],
        "state_source": "resource_layer_receipt",
    },
    "email": {
        "operations": ["intent_submit"],
        "lifecycle": ["run", "status"],
        "conditional_lifecycle": {"wait": "schedule_run_id_required"},
        "state_source": "email_state.sqlite",
    },
    "maintenance": {
        "operations": ["owner_command"],
        "lifecycle": ["run", "status", "wait"],
        "state_source": "immutable_owner_command_result",
    },
    "mcp": {
        "operations": ["tool_call", "snapshot", "validate", "recover_plan"],
        "lifecycle": ["run", "status", "wait"],
        "state_source": "hub_or_current_turn_owner_result",
    },
    "mobile": {
        "operations": ["task_get", "status", "stop_status", "session_tool_call"],
        "lifecycle": ["run", "status", "wait"],
        "state_source": "mobile_bridge_owner_result",
    },
    "network": {
        "operations": ["plan", "snapshot", "validate", "lease_start", "lease_status", "lease_stop"],
        "lifecycle": ["run", "status", "wait", "cancel"],
        "state_source": "network_gateway_owner_result",
    },
    "office": {
        "operations": ["office_command"],
        "lifecycle": ["run", "status", "wait"],
        "state_source": "installed_office_harness_result",
    },
}

configure_utf8_stdio()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def parse_argument_items(values: list[str] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in values or []:
        text = str(item or "").strip()
        if not text:
            continue
        if "=" not in text:
            key, value = "cli_arg", text
        else:
            key, value = (part.strip() for part in text.split("=", 1))
        if not key:
            continue
        if key in result:
            current = result[key]
            result[key] = current + [value] if isinstance(current, list) else [current, value]
        else:
            result[key] = value
    return result


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _stable_idempotency_key(owner: str, operation: str, arguments: dict[str, Any]) -> str:
    payload = json.dumps(
        {"owner": owner, "operation": operation, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _resource_defaults(plan: dict[str, Any], message: str) -> dict[str, Any]:
    route_pack = _as_dict(plan.get("execution_route_pack"))
    for capsule in _as_list(route_pack.get("capsules")):
        if isinstance(capsule, dict) and capsule.get("kind") == "resource":
            command = _as_list(_as_dict(capsule.get("contract")).get("job_run_command"))
            task = message
            target = message
            for index, token in enumerate(command):
                if token == "--task" and index + 1 < len(command):
                    task = str(command[index + 1])
                if token == "--target" and index + 1 < len(command):
                    target = str(command[index + 1])
            return {"task": task, "target": target, "receipt_detail": "compact"}
    return {"task": message, "target": message, "receipt_detail": "compact"}


def build_action(
    plan: dict[str, Any],
    *,
    message: str,
    owner: str = "",
    operation: str = "",
    arguments: dict[str, Any] | None = None,
    approved: bool = False,
    deadline_seconds: int = 300,
) -> dict[str, Any]:
    synthesis = synthesize(
        plan,
        message=message,
        owner=owner,
        operation=operation,
        arguments=arguments,
        owner_capabilities=OWNER_CAPABILITIES,
    )
    selected_owner = str(synthesis.get("owner") or "")
    selected_operation = str(synthesis.get("operation") or "")
    supplied = dict(_as_dict(synthesis.get("arguments")))
    if selected_owner == "resource":
        merged = _resource_defaults(plan, message)
        merged.update(supplied)
        synthesis = synthesize(
            plan,
            message=message,
            owner=selected_owner,
            operation=selected_operation,
            arguments=merged,
            owner_capabilities=OWNER_CAPABILITIES,
        )
        supplied = dict(_as_dict(synthesis.get("arguments")))

    capabilities = _as_dict(OWNER_CAPABILITIES.get(selected_owner))
    issues = [str(item) for item in _as_list(synthesis.get("issues"))]
    office_mutation = selected_owner == "office" and str(supplied.get("command") or "") in {"create", "edit", "export-pdf"}
    approval_required = selected_owner == "email" or office_mutation or _bool(supplied.get("install_approved")) or _bool(supplied.get("requires_approval")) or (selected_owner == "network" and selected_operation == "lease_stop")
    workflow_run_id = f"run_{uuid.uuid4().hex[:16]}"
    route_contract = {}
    if selected_owner == "mcp":
        route_contract = call_priority_pack(
            str(supplied.get("profile") or ""),
            str(supplied.get("tool") or ""),
            str(supplied.get("capability") or ""),
        )
    elif selected_owner == "mobile" and selected_operation == "session_tool_call":
        route_contract = call_priority_pack("mobile-openclaw-bridge", str(supplied.get("tool") or ""), "mobile_bridge")
    read_only = (
        selected_owner == "maintenance"
        or _bool(supplied.get("read_only"))
        or route_contract.get("direct_hub_tools") == ["owner_mcp.call_readonly"]
        or (selected_owner == "mobile" and selected_operation in {"status", "stop_status", "task_get", "session_tool_call"})
        or (selected_owner == "network" and selected_operation in {"plan", "snapshot", "validate", "lease_status"})
        or (selected_owner == "mcp" and selected_operation in {"snapshot", "validate", "recover_plan", "tool_call"})
        or (selected_owner == "office" and str(supplied.get("command") or "") in {"status", "info", "inspect", "operations"})
    )
    session_binding = route_contract.get("session_binding", "none")
    auto_eligible = not issues and not approval_required and session_binding == "none" and (
        (selected_owner == "resource" and not _bool(supplied.get("install_approved")))
        or (selected_owner == "maintenance" and read_only)
        or (selected_owner == "mobile" and selected_operation in {"status", "stop_status", "task_get"})
        or (selected_owner == "network" and selected_operation in {"plan", "snapshot", "validate", "lease_status"})
        or (selected_owner == "mcp" and selected_operation in {"snapshot", "validate", "recover_plan"})
        or (selected_owner == "office" and read_only)
    )
    return {
        "schema": ACTION_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "created_at": now_iso(),
        "owner": selected_owner,
        "operation": selected_operation,
        "arguments": supplied,
        "read_only": read_only,
        "approval_required": approval_required,
        "approved": bool(approved),
        "idempotency_key": _stable_idempotency_key(selected_owner, selected_operation, supplied),
        "deadline_seconds": max(1, int(deadline_seconds or 300)),
        "capabilities": _as_list(capabilities.get("lifecycle")),
        "conditional_capabilities": _as_dict(capabilities.get("conditional_lifecycle")),
        "state_source": capabilities.get("state_source", ""),
        "execution_affinity": route_contract.get("execution_affinity", "owner_cli_first" if selected_owner in {"mobile", "network", "office"} else ""),
        "session_binding": session_binding,
        "owner_profile": route_contract.get("profile", str(supplied.get("profile") or selected_owner)),
        "hub_tool": preferred_direct_hub_tool(str(supplied.get("profile") or ("mobile-openclaw-bridge" if selected_owner == "mobile" else "")), str(supplied.get("tool") or ""), str(supplied.get("capability") or ("mobile_bridge" if selected_owner == "mobile" else ""))) if route_contract else "",
        "native_tool": route_contract.get("tool", str(supplied.get("tool") or "")),
        "route_steps": route_contract.get("steps", []),
        "route_evidence_required": ["same_workflow_run_id", "owner_tool_status", "permission_boundary_preserved"] if selected_owner == "mcp" else [],
        "complete": not issues,
        "issues": issues,
        "needs_input": _as_dict(synthesis.get("needs_input")),
        "synthesis": _as_dict(synthesis.get("synthesis")),
        "auto_lifecycle": {
            "eligible": auto_eligible,
            "stages": ["run", "wait", "consume", "closeout"] if auto_eligible else [],
            "reason": "low_risk_complete_owner_action" if auto_eligible else "manual_or_owner_session_step_required",
        },
        "trace": {"parent_id": "", "correlation_id": workflow_run_id},
    }


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _run_command(argv: list[str], timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, timeout),
            creationflags=_creation_flags(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "returncode": None,
            "stdout": str(exc.stdout or "")[:2000],
            "stderr": str(exc.stderr or "")[:2000],
            "payload": {},
        }
    except OSError as exc:
        return {
            "ok": False,
            "timed_out": False,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc)[:4000],
            "payload": {},
        }
    stdout = proc.stdout.strip()
    payload: dict[str, Any] = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
            payload = parsed if isinstance(parsed, dict) else {"result": parsed}
        except json.JSONDecodeError:
            payload = {}
    return {
        "ok": proc.returncode == 0,
        "timed_out": False,
        "returncode": proc.returncode,
        "stdout": stdout[:4000],
        "stderr": proc.stderr.strip()[:4000],
        "payload": payload,
    }


def _resource_command(arguments: dict[str, Any]) -> list[str]:
    command = [str(PYTHON), str(BRIDGE / "resource_cli.py"), "job", "run"]
    scalar_options = {
        "task": "--task",
        "target": "--target",
        "intent": "--intent",
        "resource_kind": "--resource-kind",
        "source_kind": "--source-kind",
        "site_or_domain": "--site-or-domain",
        "language": "--language",
        "freshness": "--freshness",
        "authority": "--authority",
        "format": "--format",
        "license": "--license",
        "relevance_threshold": "--relevance-threshold",
        "required_source_count": "--required-source-count",
        "receipt_detail": "--receipt-detail",
    }
    for key, option in scalar_options.items():
        value = arguments.get(key)
        if value not in (None, ""):
            command.extend([option, str(value)])
    for value in _as_list(arguments.get("owner_tool")) if isinstance(arguments.get("owner_tool"), list) else [arguments.get("owner_tool")]:
        if value:
            command.extend(["--owner-tool", str(value)])
    if _bool(arguments.get("candidate_review")):
        command.append("--candidate-review")
    if _bool(arguments.get("install_approved")):
        command.append("--install-approved")
    command.append("--json")
    return command


def _email_command(arguments: dict[str, Any]) -> list[str]:
    command = [
        str(PYTHON),
        str(BRIDGE / "shared" / "email_scheduler.py"),
        "intent-submit",
        "--to",
        str(arguments.get("to")),
        "--content",
        str(arguments.get("content")),
        "--time",
        str(arguments.get("time")),
    ]
    for key, option in (("sender", "--sender"), ("subject", "--subject"), ("task_name", "--task-name")):
        if arguments.get(key):
            command.extend([option, str(arguments.get(key))])
    if _bool(arguments.get("dispatch_if_due")):
        command.append("--dispatch-if-due")
        command.extend(["--confirm-dispatch", str(arguments.get("confirm_dispatch") or "")])
    return command


def _maintenance_command(arguments: dict[str, Any]) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    subcommand = str(arguments.get("subcommand") or arguments.get("action") or "").strip()
    capability = str(arguments.get("capability_id") or "").strip()
    if capability:
        resolved = resolve_capability(capability, subcommand)
        if not resolved.get("ok"):
            issues.append(f"maintenance_capability_resolution_failed:{resolved.get('reason') or 'unknown'}")
            script = BRIDGE / "missing-maintenance-capability.py"
        else:
            script = Path(str(resolved.get("script") or "")).resolve()
    else:
        script_text = str(arguments.get("script") or "").replace("\\", "/").lstrip("./")
        script = (ROOT / script_text).resolve()
    bridge_root = BRIDGE.resolve()
    try:
        script.relative_to(bridge_root)
    except ValueError:
        issues.append("maintenance_script_outside_bridge")
    if script.suffix.lower() != ".py" or not script.is_file():
        issues.append("maintenance_script_missing_or_not_python")
    if subcommand not in READ_ONLY_MAINTENANCE_COMMANDS:
        issues.append(f"maintenance_subcommand_not_read_only:{subcommand}")
    cli_args = arguments.get("cli_arg", [])
    if not isinstance(cli_args, list):
        cli_args = [cli_args] if cli_args else []
    for item in cli_args:
        flag = str(item or "").split("=", 1)[0].strip().lower()
        if flag in MUTATING_MAINTENANCE_FLAGS or flag.startswith("--confirm-"):
            issues.append(f"maintenance_mutating_flag_blocked:{flag}")
    return [str(PYTHON), str(script), subcommand, *[str(item) for item in cli_args]], issues


def _mcp_owner_command(operation: str, arguments: dict[str, Any]) -> list[str]:
    if operation == "snapshot":
        return [str(PYTHON), str(BRIDGE / "local_mcp_hub.py"), "snapshot"]
    if operation == "validate":
        return [str(PYTHON), str(BRIDGE / "mcp_session_doctor.py"), "validate"]
    if operation == "recover_plan":
        return [
            str(PYTHON), str(BRIDGE / "mcp_session_doctor.py"), "recover-plan",
            "--profile", str(arguments.get("profile") or ""),
            "--status", str(arguments.get("status") or "transport_closed"),
        ]
    return []


def _mobile_command(operation: str, arguments: dict[str, Any]) -> list[str]:
    base = [str(PYTHON), str(BRIDGE / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py")]
    if operation == "task_get":
        return [*base, "get", str(arguments.get("task_id") or "")]
    if operation == "stop_status":
        return [*base, "stop-status"]
    if operation == "status":
        return [*base, "status"]
    return []


def _network_command(operation: str, arguments: dict[str, Any]) -> list[str]:
    base = [str(PYTHON), str(BRIDGE / "codex_network_gateway.py")]
    if operation == "plan":
        command = [*base, "plan", "--target-kind", str(arguments.get("target_kind") or "web")]
        for key, option in (("target", "--target"), ("runtime", "--runtime"), ("owner_tool", "--owner-tool")):
            if arguments.get(key) not in (None, ""):
                command.extend([option, str(arguments[key])])
        return command
    if operation == "snapshot":
        return [*base, "snapshot"]
    if operation == "validate":
        return [*base, "validate"]
    if operation == "lease_start":
        command = [*base, "lease-start", "--target-kind", str(arguments.get("target_kind") or "web")]
        for key, option in (("target", "--check-url"), ("node", "--node"), ("group", "--group"), ("ttl_seconds", "--ttl-seconds")):
            if arguments.get(key) not in (None, ""):
                command.extend([option, str(arguments[key])])
        return command
    if operation == "lease_status":
        return [*base, "lease-status", "--lease-id", str(arguments.get("lease_id") or "")]
    if operation == "lease_stop":
        return [*base, "lease-stop", "--lease-id", str(arguments.get("lease_id") or "")]
    return []


def _office_command(arguments: dict[str, Any]) -> list[str]:
    entrypoint = shutil.which("cli-anything-microsoft-office") or "cli-anything-microsoft-office"
    command = [entrypoint, "--json"]
    if _bool(arguments.get("dry_run")):
        command.append("--dry-run")
    if _bool(arguments.get("overwrite")):
        command.append("--overwrite")
    if arguments.get("timeout") not in (None, ""):
        command.extend(["--timeout", str(arguments.get("timeout"))])
    command.extend([str(arguments.get("app") or ""), str(arguments.get("command") or "")])
    cli_args = arguments.get("cli_arg", [])
    if not isinstance(cli_args, list):
        cli_args = [cli_args] if cli_args not in (None, "") else []
    command.extend(str(item) for item in cli_args)
    return command


def _handoff_receipt(action: dict[str, Any]) -> dict[str, Any]:
    arguments = _as_dict(action.get("arguments"))
    tool_arguments = _json_object(arguments.get("tool_arguments") or arguments.get("arguments"))
    hub_tool = str(action.get("hub_tool") or "")
    native_tool = str(action.get("native_tool") or arguments.get("tool") or "")
    target_tool = hub_tool if action.get("execution_affinity") == "hub_first" and hub_tool else native_tool
    return _receipt(
        action,
        status="handoff_required",
        ok=True,
        owner_status="awaiting_current_turn_owner_result",
        status_source="workflow_current_turn_handoff",
        next_action="call_owner_tool_then_attach_result",
        owner_metadata={
            "owner_tool": target_tool,
            "hub_tool": hub_tool,
            "native_tool": native_tool,
            "owner_profile": action.get("owner_profile", ""),
            "execution_affinity": action.get("execution_affinity", ""),
            "session_binding": action.get("session_binding", "none"),
            "arguments": tool_arguments,
            "attach_command": f"python _bridge\\codex_workflow_entry.py attach-result --workflow-run-id {action.get('workflow_run_id')} --owner-result-file <json>",
        },
    )


def _receipt(
    action: dict[str, Any],
    *,
    status: str,
    ok: bool,
    owner_request_id: str = "",
    owner_status: str = "",
    status_source: str = "",
    raw_result: dict[str, Any] | None = None,
    error_class: str = "",
    error_reason: str = "",
    next_action: str = "",
    owner_metadata: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
    artifacts: list[Any] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    normalized = status if status in NORMALIZED_STATUSES else ("completed" if ok else "failed")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "ok": bool(ok),
        "workflow_run_id": action.get("workflow_run_id"),
        "owner": action.get("owner"),
        "owner_request_id": owner_request_id,
        "status": normalized,
        "owner_status": owner_status or normalized,
        "status_source": status_source or action.get("state_source"),
        "updated_at": now_iso(),
        "progress": progress or {},
        "retryable": bool(retryable),
        "error": {"class": error_class, "reason": error_reason},
        "artifacts": artifacts or [],
        "next_action": next_action,
        "capabilities": {
            key: key in _as_list(action.get("capabilities"))
            for key in ("run", "status", "wait", "cancel")
        },
        "owner_metadata": owner_metadata or {},
        "trace": _as_dict(action.get("trace")),
    }
    if raw_result:
        receipt["raw_result"] = raw_result
        if not ok:
            diagnostics = extract_failure_diagnostics(raw_result)
            if diagnostics.get("diagnostic_count") or diagnostics.get("reason"):
                receipt["diagnostics"] = diagnostics
                if receipt["error"]["reason"] in {"", "owner_returned_not_ok"} and diagnostics.get("reason"):
                    receipt["error"]["reason"] = diagnostics["reason"]
    return receipt


def _normalize_resource(action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    payload = _as_dict(result.get("payload"))
    status = str(payload.get("status") or ("completed" if result.get("ok") else "failed"))
    ok = bool(payload.get("ok", result.get("ok")))
    if result.get("timed_out"):
        status, ok = "deferred", False
    next_action = str(payload.get("next_action") or payload.get("codex_next_action") or "")
    progress = dict(_as_dict(payload.get("status_summary")))
    progress.update(_as_dict(payload.get("progress")))
    artifacts = _as_list(payload.get("required_consume_paths"))
    if not artifacts:
        consume = _as_dict(payload.get("consume_contract"))
        artifacts = _as_list(consume.get("required_paths"))
    return _receipt(
        action,
        status=status,
        ok=ok,
        owner_request_id=str(payload.get("request_id") or ""),
        owner_status=status,
        status_source="resource_layer_receipt",
        raw_result=payload or result,
        error_class="timeout" if result.get("timed_out") else ("owner_command_failed" if not ok else ""),
        error_reason=str(payload.get("reason") or _as_dict(payload.get("error")).get("reason") or payload.get("error") or result.get("stderr") or "owner_returned_not_ok") if not ok else "",
        next_action=next_action,
        progress=progress,
        artifacts=artifacts,
        retryable=status in {"deferred"},
    )


def _normalize_email(action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if result.get("timed_out"):
        return _receipt(
            action,
            status="deferred",
            ok=False,
            status_source="email_state.sqlite",
            raw_result=result,
            error_class="timeout",
            error_reason="email owner command exceeded facade deadline",
            next_action="status_or_owner_inspection",
        )
    payload = _as_dict(result.get("payload"))
    created = bool(payload.get("created"))
    ok = bool(payload.get("ok", result.get("ok"))) and created
    status = "submitted" if created else ("blocked" if payload.get("automation_decision", {}).get("requires_review") else "failed")
    task = _as_dict(payload.get("task"))
    task_name = str(task.get("任务名") or action.get("arguments", {}).get("task_name") or "")
    dispatch = _as_dict(payload.get("dispatch"))
    dispatch_jobs = _as_list(dispatch.get("jobs"))
    first_schedule_id = next(
        (str(item.get("schedule_run_id") or "") for item in dispatch_jobs if isinstance(item, dict) and item.get("schedule_run_id")),
        "",
    )
    schedule_run_id = str(dispatch.get("schedule_run_id") or payload.get("schedule_run_id") or first_schedule_id)
    return _receipt(
        action,
        status=status,
        ok=ok,
        owner_request_id=task_name,
        owner_status=str(payload.get("status") or status),
        status_source="email_state.sqlite",
        raw_result=payload or result,
        error_class="owner_review_required" if status == "blocked" else ("owner_command_failed" if not ok else ""),
        error_reason=str(payload.get("next_step") or result.get("stderr") or "") if not ok else "",
        next_action="status" if created else "review",
        owner_metadata={"task_name": task_name, "schedule_run_id": schedule_run_id},
    )


def _normalize_maintenance(action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if result.get("timed_out"):
        return _receipt(
            action,
            status="deferred",
            ok=False,
            status_source="immutable_owner_command_result",
            raw_result=result,
            error_class="timeout",
            error_reason="maintenance owner command exceeded facade deadline",
            next_action="inspect_owner_process_or_rerun_explicitly",
        )
    payload = _as_dict(result.get("payload"))
    ok = bool(payload.get("ok", result.get("ok")))
    return _receipt(
        action,
        status="completed" if ok else "failed",
        ok=ok,
        owner_status="completed" if ok else "failed",
        status_source="immutable_owner_command_result",
        raw_result=payload or result,
        error_class="owner_command_failed" if not ok else "",
        error_reason=str(payload.get("reason") or _as_dict(payload.get("error")).get("reason") or payload.get("error") or result.get("stderr") or "owner_returned_not_ok") if not ok else "",
        next_action="closeout" if ok else "inspect_owner_result",
    )


def _normalize_owner_command(action: dict[str, Any], result: dict[str, Any], *, status_source: str) -> dict[str, Any]:
    if result.get("timed_out"):
        return _receipt(
            action,
            status="deferred",
            ok=False,
            status_source=status_source,
            raw_result=result,
            error_class="timeout",
            error_reason="owner command exceeded facade deadline",
            next_action="status",
            retryable=True,
        )
    payload = _as_dict(result.get("payload"))
    ok = bool(payload.get("ok", result.get("ok")))
    status = str(payload.get("status") or ("completed" if ok else "failed")).lower()
    if status not in NORMALIZED_STATUSES:
        status = "completed" if ok else "failed"
    owner_request_id = str(payload.get("lease_id") or payload.get("task_id") or payload.get("request_id") or "")
    return _receipt(
        action,
        status=status,
        ok=ok,
        owner_request_id=owner_request_id,
        owner_status=status,
        status_source=status_source,
        raw_result=payload or result,
        error_class="owner_command_failed" if not ok else "",
        error_reason=str(payload.get("reason") or _as_dict(payload.get("error")).get("reason") or payload.get("error") or result.get("stderr") or "owner_returned_not_ok") if not ok else "",
        next_action="closeout" if ok and status == "completed" else ("status" if status in {"submitted", "running", "deferred"} else "inspect_owner_result"),
        retryable=status == "deferred",
    )


def _run_path(workflow_run_id: str) -> Path:
    safe = "".join(ch for ch in str(workflow_run_id or "") if ch.isalnum() or ch in {"_", "-"})
    return RUNS_DIR / f"{safe}.json"


def _write_run_ref(action: dict[str, Any], receipt: dict[str, Any]) -> Path:
    path = _run_path(str(action.get("workflow_run_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    compact_receipt = dict(receipt)
    raw_result = compact_receipt.pop("raw_result", {})
    if raw_result:
        receipt_path = path.with_suffix(".receipt.json")
        receipt_tmp = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
        receipt_tmp.write_text(json.dumps(raw_result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(receipt_tmp, receipt_path)
        compact_receipt["raw_result_ref"] = str(receipt_path)
        receipt["raw_result_ref"] = str(receipt_path)
        receipt.pop("raw_result", None)
    payload = {
        "schema": RUN_REF_SCHEMA,
        "workflow_run_id": action.get("workflow_run_id"),
        "owner": action.get("owner"),
        "operation": action.get("operation"),
        "owner_request_id": receipt.get("owner_request_id"),
        "state_source": receipt.get("status_source"),
        "action": action,
        "latest_receipt": compact_receipt,
        "updated_at": now_iso(),
        "rule": "reference only; owner remains the mutable business-state source",
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_run_ref(workflow_run_id: str) -> dict[str, Any]:
    path = _run_path(workflow_run_id)
    if not path.is_file():
        return {"ok": False, "reason": "workflow_run_not_found", "workflow_run_id": workflow_run_id}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"workflow_run_unreadable:{type(exc).__name__}", "workflow_run_id": workflow_run_id}
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "workflow_run_invalid"}


def _finalize_inline_receipt(action: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    path = _write_run_ref(action, receipt)
    receipt["run_ref"] = str(path)
    artifact_ref = str(receipt.get("raw_result_ref") or path)
    return bounded_payload(
        receipt,
        max_bytes=8 * 1024,
        max_items=24,
        max_string=900,
        preserve_keys=(
            "schema",
            "ok",
            "status",
            "workflow_run_id",
            "owner",
            "operation",
            "owner_request_id",
            "progress",
            "error",
            "diagnostics",
            "next_action",
            "raw_result_ref",
            "run_ref",
        ),
        artifact_ref=artifact_ref,
    )


def save_planned_action(action: dict[str, Any]) -> dict[str, Any]:
    receipt = _receipt(
        action,
        status="planned",
        ok=bool(action.get("complete")),
        status_source="workflow_action_contract",
        error_class="" if action.get("complete") else "incomplete_contract",
        error_reason=";".join(str(item) for item in _as_list(action.get("issues"))),
        next_action="run" if action.get("complete") else "refine_action_contract",
    )
    return _finalize_inline_receipt(action, receipt)


def action_from_run_ref(workflow_run_id: str) -> dict[str, Any]:
    ref = load_run_ref(workflow_run_id)
    action = _as_dict(ref.get("action"))
    if action.get("schema") != ACTION_SCHEMA:
        return {"schema": ACTION_SCHEMA, "complete": False, "issues": [str(ref.get("reason") or "workflow_run_action_missing")]}
    return action


def execute_action(action: dict[str, Any], *, timeout_seconds: int | None = None) -> dict[str, Any]:
    if action.get("schema") != ACTION_SCHEMA:
        return bounded_payload(
            _receipt(action, status="blocked", ok=False, error_class="invalid_contract", error_reason="action_schema_invalid"),
            preserve_keys=("schema", "ok", "status", "error"),
        )
    if not action.get("complete"):
        receipt = _receipt(
            action,
            status="blocked",
            ok=False,
            error_class="incomplete_contract",
            error_reason=";".join(str(item) for item in _as_list(action.get("issues"))),
            next_action="refine_action_contract",
        )
        receipt["needs_input"] = _as_dict(action.get("needs_input"))
        return _finalize_inline_receipt(action, receipt)
    if action.get("approval_required") and not action.get("approved"):
        receipt = _receipt(
            action,
            status="blocked",
            ok=False,
            error_class="approval_required",
            error_reason="explicit --approve required for this owner action",
            next_action="request_approval",
        )
        return _finalize_inline_receipt(action, receipt)

    timeout = int(timeout_seconds or action.get("deadline_seconds") or 300)
    owner = str(action.get("owner") or "")
    arguments = _as_dict(action.get("arguments"))
    if owner == "resource":
        result = _run_command(_resource_command(arguments), timeout)
        receipt = _normalize_resource(action, result)
    elif owner == "email":
        result = _run_command(_email_command(arguments), timeout)
        receipt = _normalize_email(action, result)
    elif owner == "maintenance":
        command, issues = _maintenance_command(arguments)
        if issues:
            receipt = _receipt(
                action,
                status="blocked",
                ok=False,
                error_class="maintenance_contract_invalid",
                error_reason=";".join(issues),
                next_action="refine_action_contract",
            )
        else:
            receipt = _normalize_maintenance(action, _run_command(command, timeout))
    elif owner == "mcp":
        operation = str(action.get("operation") or "")
        if operation == "tool_call":
            receipt = _handoff_receipt(action)
        else:
            command = _mcp_owner_command(operation, arguments)
            receipt = _normalize_owner_command(action, _run_command(command, timeout), status_source="mcp_owner_cli") if command else _receipt(
                action, status="unsupported", ok=False, error_class="mcp_operation_not_supported", error_reason=operation
            )
    elif owner == "mobile":
        operation = str(action.get("operation") or "")
        if operation == "session_tool_call":
            receipt = _handoff_receipt(action)
        else:
            command = _mobile_command(operation, arguments)
            receipt = _normalize_owner_command(action, _run_command(command, timeout), status_source="mobile_owner_cli") if command else _receipt(
                action, status="unsupported", ok=False, error_class="mobile_operation_not_supported", error_reason=operation
            )
    elif owner == "network":
        operation = str(action.get("operation") or "")
        command = _network_command(operation, arguments)
        receipt = _normalize_owner_command(action, _run_command(command, timeout), status_source="network_gateway_cli") if command else _receipt(
            action, status="unsupported", ok=False, error_class="network_operation_not_supported", error_reason=operation
        )
    elif owner == "office":
        receipt = _normalize_owner_command(action, _run_command(_office_command(arguments), timeout), status_source="installed_office_harness_result")
    else:
        receipt = _receipt(
            action,
            status="unsupported",
            ok=False,
            error_class="owner_not_supported",
            error_reason=owner,
            next_action="use_owner_native_entrypoint",
        )
    return _finalize_inline_receipt(action, receipt)


def attach_owner_result(workflow_run_id: str, owner_result: dict[str, Any]) -> dict[str, Any]:
    ref = load_run_ref(workflow_run_id)
    if ref.get("schema") != RUN_REF_SCHEMA:
        return {"schema": RECEIPT_SCHEMA, "ok": False, "status": "failed", "workflow_run_id": workflow_run_id, "error": {"class": "run_ref_missing", "reason": ref.get("reason", "")}}
    action = _as_dict(ref.get("action"))
    latest = _as_dict(ref.get("latest_receipt"))
    if latest.get("status") != "handoff_required":
        return _receipt(
            action,
            status="blocked",
            ok=False,
            error_class="handoff_not_pending",
            error_reason=str(latest.get("status") or "unknown"),
            next_action="status",
        )
    if not isinstance(owner_result, dict) or not owner_result:
        return _receipt(action, status="blocked", ok=False, error_class="owner_result_invalid", error_reason="nonempty JSON object required", next_action="attach_result")
    expected = _as_dict(latest.get("owner_metadata"))
    expected_tools = {str(expected.get("owner_tool") or ""), str(expected.get("hub_tool") or ""), str(expected.get("native_tool") or "")} - {""}
    source_tool = str(owner_result.get("source_tool") or owner_result.get("tool") or "").strip()
    if expected_tools and source_tool and source_tool not in expected_tools:
        return _receipt(
            action,
            status="blocked",
            ok=False,
            error_class="owner_tool_mismatch",
            error_reason=f"expected one of {sorted(expected_tools)}, got {source_tool}",
            next_action="attach_result",
        )
    ok = bool(owner_result.get("ok"))
    status = str(owner_result.get("status") or ("completed" if ok else "failed")).lower()
    if status not in {"completed", "failed", "blocked", "deferred"}:
        return _receipt(action, status="blocked", ok=False, error_class="owner_result_status_invalid", error_reason=status, next_action="attach_result")
    receipt = _receipt(
        action,
        status=status,
        ok=ok and status == "completed",
        owner_request_id=str(owner_result.get("owner_request_id") or ""),
        owner_status=str(owner_result.get("owner_status") or status),
        status_source="attached_current_turn_owner_result",
        raw_result=owner_result,
        error_class=str(_as_dict(owner_result.get("error")).get("class") or ("owner_tool_failed" if status != "completed" else "")),
        error_reason=str(_as_dict(owner_result.get("error")).get("reason") or owner_result.get("reason") or ""),
        next_action="closeout" if status == "completed" else ("refine_or_retry_owner_route" if status == "deferred" else "inspect_owner_result"),
        artifacts=_as_list(owner_result.get("artifacts")),
        owner_metadata={**expected, "attached_source_tool": source_tool, "attached": True},
        retryable=status == "deferred",
    )
    path = _write_run_ref(action, receipt)
    receipt["run_ref"] = str(path)
    return receipt


def _resource_status(action: dict[str, Any], receipt: dict[str, Any], *, wait: bool, timeout: int, interval: float) -> dict[str, Any]:
    request_id = str(receipt.get("owner_request_id") or "")
    if not request_id:
        return _receipt(action, status="blocked", ok=False, error_class="owner_request_id_missing", error_reason="resource request id missing")
    command = [str(PYTHON), str(BRIDGE / "resource_cli.py"), "job", "wait" if wait else "status", "--request-id", request_id]
    if wait:
        command.extend(["--timeout", str(timeout), "--interval", str(interval)])
    command.append("--json")
    return _normalize_resource(action, _run_command(command, timeout + 10 if wait else 60))


def _email_status(action: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    task_name = str(_as_dict(receipt.get("owner_metadata")).get("task_name") or receipt.get("owner_request_id") or "")
    result = _run_command(
        [str(PYTHON), str(BRIDGE / "shared" / "email_scheduler.py"), "state-query", "--table", "tasks", "--limit", "1000"],
        60,
    )
    payload = _as_dict(result.get("payload"))
    row = next((item for item in _as_list(payload.get("rows")) if isinstance(item, dict) and item.get("task_name") == task_name), {})
    if not row:
        return _receipt(
            action,
            status="failed",
            ok=False,
            owner_request_id=task_name,
            status_source="email_state.sqlite",
            raw_result=payload or result,
            error_class="owner_state_not_found",
            error_reason=task_name,
        )
    return _receipt(
        action,
        status="submitted",
        ok=True,
        owner_request_id=task_name,
        owner_status=str(row.get("status") or ""),
        status_source="email_state.sqlite",
        raw_result=row,
        next_action="wait_for_scheduler_or_inspect_run",
        owner_metadata=_as_dict(receipt.get("owner_metadata")),
    )


def _email_schedule_status(action: dict[str, Any], receipt: dict[str, Any], *, wait: bool, timeout: int, interval: float) -> dict[str, Any]:
    metadata = _as_dict(receipt.get("owner_metadata"))
    schedule_run_id = str(metadata.get("schedule_run_id") or "")
    deadline = time.monotonic() + max(1, timeout)
    last_payload: dict[str, Any] = {}
    while True:
        result = _run_command(
            [str(PYTHON), str(BRIDGE / "shared" / "email_scheduler.py"), "inspect-run", "--schedule-run-id", schedule_run_id],
            60,
        )
        last_payload = _as_dict(result.get("payload"))
        schedule = _as_dict(last_payload.get("schedule_run"))
        owner_status = str(schedule.get("status") or "").lower()
        related = _as_list(last_payload.get("content_jobs")) + _as_list(last_payload.get("delivery_jobs")) + _as_list(last_payload.get("outbox_items"))
        related_statuses = {str(item.get("status") or "").lower() for item in related if isinstance(item, dict)}
        statuses = {owner_status, *related_statuses} - {""}
        if statuses & {"failed", "dead_letter", "content_failed", "delivery_failed", "expired", "stale"}:
            normalized, ok, next_action = "failed", False, "inspect_owner_result"
        elif statuses & {"blocked", "draft", "needs_review"}:
            normalized, ok, next_action = "blocked", False, "review_email_owner_state"
        elif owner_status in {"completed", "sent", "delivered", "archived", "success"}:
            normalized, ok, next_action = "completed", True, "closeout"
        else:
            normalized, ok, next_action = "running", True, "wait"
        if not wait or normalized in TERMINAL_STATUSES or time.monotonic() >= deadline:
            if wait and normalized == "running" and time.monotonic() >= deadline:
                normalized, ok, next_action = "deferred", False, "status"
            return _receipt(
                action,
                status=normalized,
                ok=ok,
                owner_request_id=str(receipt.get("owner_request_id") or ""),
                owner_status=owner_status or ",".join(sorted(statuses)),
                status_source="email_scheduler_stage_store",
                raw_result=last_payload or result,
                error_class="wait_timeout" if normalized == "deferred" else ("owner_reported_failure" if not ok else ""),
                error_reason="email schedule run did not reach terminal state before deadline" if normalized == "deferred" else "",
                next_action=next_action,
                owner_metadata=metadata,
            )
        time.sleep(max(0.2, interval))


def lifecycle_status(workflow_run_id: str, *, wait: bool = False, timeout: int = 300, interval: float = 1.0) -> dict[str, Any]:
    ref = load_run_ref(workflow_run_id)
    if not ref.get("schema") == RUN_REF_SCHEMA:
        return {"schema": RECEIPT_SCHEMA, "ok": False, "status": "failed", "workflow_run_id": workflow_run_id, "error": {"class": "run_ref_missing", "reason": ref.get("reason", "")}}
    action = _as_dict(ref.get("action"))
    receipt = _as_dict(ref.get("latest_receipt"))
    if receipt.get("status") == "handoff_required":
        receipt["run_ref"] = str(_run_path(workflow_run_id))
        return receipt
    if receipt.get("status") == "planned":
        if wait:
            return _receipt(
                action,
                status="blocked",
                ok=False,
                status_source="workflow_action_contract",
                error_class="run_not_submitted",
                error_reason="planned action must be run before wait",
                next_action="run",
            )
        receipt["run_ref"] = str(_run_path(workflow_run_id))
        return receipt
    owner = str(ref.get("owner") or "")
    if owner == "resource":
        updated = _resource_status(action, receipt, wait=wait, timeout=timeout, interval=interval)
    elif owner == "email":
        schedule_run_id = _as_dict(receipt.get("owner_metadata")).get("schedule_run_id")
        if wait and not schedule_run_id:
            updated = _receipt(
                action,
                status="unsupported",
                ok=False,
                owner_request_id=str(receipt.get("owner_request_id") or ""),
                status_source="email_state.sqlite",
                error_class="wait_not_supported",
                error_reason="email wait requires schedule_run_id",
                next_action="status",
                owner_metadata=_as_dict(receipt.get("owner_metadata")),
            )
        elif schedule_run_id:
            updated = _email_schedule_status(action, receipt, wait=wait, timeout=timeout, interval=interval)
        else:
            updated = _email_status(action, receipt)
    elif owner == "maintenance":
        updated = dict(receipt)
        updated["updated_at"] = now_iso()
    elif owner in {"mcp", "mobile", "office"}:
        updated = dict(receipt)
        updated["updated_at"] = now_iso()
    elif owner == "network":
        lease_id = str(receipt.get("owner_request_id") or _as_dict(action.get("arguments")).get("lease_id") or "")
        if lease_id:
            status_action = dict(action)
            status_action["operation"] = "lease_status"
            status_action["arguments"] = {**_as_dict(action.get("arguments")), "lease_id": lease_id}
            updated = _normalize_owner_command(
                status_action,
                _run_command(_network_command("lease_status", _as_dict(status_action.get("arguments"))), timeout),
                status_source="network_gateway_cli",
            )
        else:
            updated = dict(receipt)
            updated["updated_at"] = now_iso()
    else:
        updated = _receipt(action, status="unsupported", ok=False, error_class="owner_not_supported", error_reason=owner)
    _write_run_ref(action, updated)
    updated["run_ref"] = str(_run_path(workflow_run_id))
    return updated


def _bounded_result_preview(path: Path, *, max_chars: int = 2000) -> dict[str, Any]:
    item = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return item
    item["size_bytes"] = path.stat().st_size
    item["suffix"] = path.suffix.lower()
    if path.suffix.lower() not in {".json", ".jsonl", ".md", ".txt", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".html", ".htm"}:
        item["preview_kind"] = "metadata_only"
        return item
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        item["read_error"] = f"{type(exc).__name__}: {exc}"
        return item
    item["preview_kind"] = "bounded_text"
    item["preview"] = text[:max_chars]
    item["truncated"] = len(text) > max_chars
    return item


def lifecycle_consume(workflow_run_id: str) -> dict[str, Any]:
    ref = load_run_ref(workflow_run_id)
    if ref.get("schema") != RUN_REF_SCHEMA:
        return {"schema": RECEIPT_SCHEMA, "ok": False, "status": "failed", "workflow_run_id": workflow_run_id, "error": {"class": "run_ref_missing", "reason": ref.get("reason", "")}}
    action = _as_dict(ref.get("action"))
    receipt = dict(_as_dict(ref.get("latest_receipt")))
    if receipt.get("status") != "completed":
        return _receipt(
            action,
            status="blocked",
            ok=False,
            owner_request_id=str(receipt.get("owner_request_id") or ""),
            error_class="result_not_completed",
            error_reason=str(receipt.get("status") or "unknown"),
            next_action="wait_or_status",
        )
    declared: list[tuple[Path, dict[str, Any]]] = []
    for item in _as_list(receipt.get("artifacts")):
        if isinstance(item, dict):
            path_text = str(item.get("path") or "").strip()
            metadata = {key: value for key, value in item.items() if key != "path"}
        else:
            path_text = str(item or "").strip()
            metadata = {}
        if path_text:
            declared.append((Path(path_text), metadata))
    raw_ref = str(receipt.get("raw_result_ref") or "")
    previews = []
    for path, metadata in declared:
        preview = _bounded_result_preview(path)
        if metadata:
            preview["reference_metadata"] = metadata
        previews.append(preview)
    if raw_ref:
        previews.append(_bounded_result_preview(Path(raw_ref)))
    missing = [item["path"] for item in previews if not item.get("exists")]
    receipt["consumption"] = {
        "ok": not missing,
        "consumed": not missing,
        "declared_artifact_count": len(declared),
        "result_reference_count": len(previews),
        "missing_paths": missing,
        "references": previews,
        "bounded_preview_chars": 2000,
    }
    receipt["ok"] = bool(receipt.get("ok")) and not missing
    receipt["next_action"] = "closeout" if not missing else "repair_owner_result_reference"
    receipt["updated_at"] = now_iso()
    _write_run_ref(action, receipt)
    receipt["run_ref"] = str(_run_path(workflow_run_id))
    return receipt


def execute_lifecycle(action: dict[str, Any], *, timeout_seconds: int = 300, interval: float = 1.0) -> dict[str, Any]:
    eligibility = _as_dict(action.get("auto_lifecycle"))
    if not eligibility.get("eligible"):
        return {
            "schema": "workflow.lifecycle.v1",
            "ok": False,
            "status": "blocked",
            "workflow_run_id": action.get("workflow_run_id"),
            "error": {"class": "auto_lifecycle_not_eligible", "reason": eligibility.get("reason", "manual_owner_step_required")},
            "needs_input": _as_dict(action.get("needs_input")),
            "next_action": "run_single_step_or_refine_action",
            "stages": [],
        }
    stages: list[dict[str, Any]] = []
    receipt = execute_action(action, timeout_seconds=timeout_seconds)
    stages.append({"stage": "run", "status": receipt.get("status"), "ok": receipt.get("ok")})
    if receipt.get("status") == "handoff_required":
        return {"schema": "workflow.lifecycle.v1", "ok": True, "status": "handoff_required", "workflow_run_id": action.get("workflow_run_id"), "receipt": receipt, "stages": stages}
    if receipt.get("status") in {"submitted", "running", "deferred"} and "wait" in _as_list(action.get("capabilities")):
        receipt = lifecycle_status(str(action.get("workflow_run_id") or ""), wait=True, timeout=timeout_seconds, interval=interval)
        stages.append({"stage": "wait", "status": receipt.get("status"), "ok": receipt.get("ok")})
    if receipt.get("status") == "completed":
        receipt = lifecycle_consume(str(action.get("workflow_run_id") or ""))
        stages.append({"stage": "consume", "status": receipt.get("status"), "ok": _as_dict(receipt.get("consumption")).get("ok")})
    if receipt.get("status") == "completed" and _as_dict(receipt.get("consumption")).get("ok"):
        receipt["closeout"] = {"completed": True, "completed_at": now_iso(), "owner_state_preserved": True}
        receipt["next_action"] = "done"
        action_from_ref = action_from_run_ref(str(action.get("workflow_run_id") or ""))
        _write_run_ref(action_from_ref, receipt)
        stages.append({"stage": "closeout", "status": "completed", "ok": True})
    return {
        "schema": "workflow.lifecycle.v1",
        "ok": bool(receipt.get("ok")) and receipt.get("status") == "completed",
        "status": receipt.get("status"),
        "workflow_run_id": action.get("workflow_run_id"),
        "receipt": receipt,
        "stages": stages,
        "trace": _as_dict(action.get("trace")),
    }


def lifecycle_cancel(workflow_run_id: str, *, approved: bool = False) -> dict[str, Any]:
    ref = load_run_ref(workflow_run_id)
    if not ref.get("schema") == RUN_REF_SCHEMA:
        return {"schema": RECEIPT_SCHEMA, "ok": False, "status": "failed", "workflow_run_id": workflow_run_id, "error": {"class": "run_ref_missing", "reason": ref.get("reason", "")}}
    action = _as_dict(ref.get("action"))
    receipt = _as_dict(ref.get("latest_receipt"))
    if "cancel" not in _as_list(action.get("capabilities")):
        return _receipt(
            action,
            status="unsupported",
            ok=False,
            owner_request_id=str(receipt.get("owner_request_id") or ""),
            status_source=str(receipt.get("status_source") or action.get("state_source") or ""),
            error_class="cancel_not_supported",
            error_reason=str(action.get("owner") or ""),
            next_action="use_owner_native_entrypoint_if_available",
        )
    if not approved:
        return _receipt(action, status="blocked", ok=False, error_class="approval_required", error_reason="cancel requires --approve")
    if str(action.get("owner") or "") == "network":
        lease_id = str(receipt.get("owner_request_id") or _as_dict(action.get("arguments")).get("lease_id") or "")
        if not lease_id:
            return _receipt(action, status="blocked", ok=False, error_class="lease_id_missing", error_reason="network cancel requires exact lease id")
        cancel_action = dict(action)
        cancel_action["operation"] = "lease_stop"
        cancel_action["arguments"] = {**_as_dict(action.get("arguments")), "lease_id": lease_id}
        result = _normalize_owner_command(
            cancel_action,
            _run_command(_network_command("lease_stop", _as_dict(cancel_action.get("arguments"))), int(action.get("deadline_seconds") or 60)),
            status_source="network_gateway_cli",
        )
        if result.get("ok"):
            result["status"] = "cancelled"
            result["owner_status"] = "cancelled"
        _write_run_ref(action, result)
        return result
    return _receipt(action, status="unsupported", ok=False, error_class="cancel_adapter_missing", error_reason=str(action.get("owner") or ""))


def snapshot() -> dict[str, Any]:
    return {
        "schema": "workflow_owner_facade.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "action_schema": ACTION_SCHEMA,
        "receipt_schema": RECEIPT_SCHEMA,
        "owners": OWNER_CAPABILITIES,
        "run_reference_dir": str(RUNS_DIR),
        "state_rule": "owner is the mutable business-state source; run references only locate owner state",
        "future_bus_ready_fields": [
            "workflow_run_id",
            "owner_request_id",
            "idempotency_key",
            "deadline_seconds",
            "capabilities",
            "status",
            "error",
            "trace",
            "execution_affinity",
            "session_binding",
            "handoff_required",
        ],
    }


def validate() -> dict[str, Any]:
    resource_plan = {
        "execution_route_pack": {
            "route_decision": {
                "primary_domain": "external_docs_research",
                "confidence": 1.0,
                "match_quality": "strong",
                "ambiguity": {"is_ambiguous": False},
                "resource_delegation_required": True,
            },
            "capsules": [{"kind": "resource", "contract": {"job_run_command": ["python", "resource_cli.py", "job", "run", "--task", "test", "--target", "test"]}}],
        }
    }
    email_plan = {"execution_route_pack": {"route_decision": {"primary_domain": "email", "confidence": 1.0, "match_quality": "strong", "ambiguity": {"is_ambiguous": False}, "resource_delegation_required": False}}}
    resource_action = build_action(resource_plan, message="test")
    email_action = build_action(email_plan, message="mail", arguments={"to": "a", "content": "b", "time": "立即"})
    maintenance_action = build_action(
        {},
        message="validate",
        owner="maintenance",
        arguments={"script": "_bridge/workflow_orchestrator.py", "subcommand": "validate"},
    )
    mcp_action = build_action(
        {},
        message="context7",
        owner="mcp",
        operation="tool_call",
        arguments={"profile": "context7", "tool": "resolve_library_id", "capability": "external_docs_research", "tool_arguments": {"libraryName": "React", "query": "hooks"}},
    )
    mcp_handoff = _handoff_receipt(mcp_action)
    session_action = build_action(
        {},
        message="current page",
        owner="mcp",
        operation="tool_call",
        arguments={"profile": "chrome-devtools", "tool": "take_snapshot", "capability": "browser_session"},
    )
    gui_union_action = build_action(
        {"execution_route_pack": {"route_decision": {"primary_domain": "gui_browser", "confidence": 1.0, "match_quality": "strong", "ambiguity": {"is_ambiguous": False}, "owner_route": {"mcp_profile": "chrome-devtools|playwright", "tool": "snapshot", "capability": "browser_runtime"}}}},
        message="snapshot current page",
    )
    network_action = build_action(
        {"execution_route_pack": {"route_decision": {"primary_domain": "general", "confidence": 0.0, "match_quality": "fallback", "ambiguity": {"is_ambiguous": True}}, "capsules": [{"kind": "network", "contract": {"entrypoint": "_bridge/codex_network_gateway.py plan"}}]}},
        message="check network route",
    )
    office_action = build_action(
        {"execution_route_pack": {"route_decision": {"primary_domain": "office_native", "confidence": 1.0, "match_quality": "strong", "ambiguity": {"is_ambiguous": False}}}},
        message="inspect local Word document",
        arguments={"app": "word", "command": "inspect", "cli_arg": ["C:/Temp/example.docx"]},
    )
    mobile_action = build_action({}, message="mobile status", owner="mobile", operation="status")
    malformed_action = build_action({}, message="bad", owner="unknown", operation="bad")
    auto_action = build_action(
        {},
        message="snapshot workflow facade",
        owner="maintenance",
        arguments={"script": "_bridge/workflow_owner_facade.py", "subcommand": "snapshot"},
    )
    auto_action["workflow_run_id"] = f"validate_auto_{uuid.uuid4().hex[:12]}"
    auto_action["trace"] = {"parent_id": "", "correlation_id": auto_action["workflow_run_id"]}
    auto_result = execute_lifecycle(auto_action, timeout_seconds=60)
    auto_path = _run_path(auto_action["workflow_run_id"])
    auto_receipt_path = auto_path.with_suffix(".receipt.json")
    structured_action = build_action({}, message="structured result", owner="maintenance", arguments={"script": "_bridge/workflow_owner_facade.py", "subcommand": "snapshot"})
    structured_action["workflow_run_id"] = f"validate_consume_{uuid.uuid4().hex[:12]}"
    structured_action["trace"] = {"parent_id": "", "correlation_id": structured_action["workflow_run_id"]}
    structured_artifact = RUNS_DIR / f"{structured_action['workflow_run_id']}.artifact.txt"
    structured_artifact.parent.mkdir(parents=True, exist_ok=True)
    structured_artifact.write_text("structured consume evidence", encoding="utf-8")
    structured_receipt = _receipt(structured_action, status="completed", ok=True, artifacts=[{"path": str(structured_artifact), "kind": "owner_result", "codex_action": "read_owner_result"}])
    _write_run_ref(structured_action, structured_receipt)
    structured_consumed = lifecycle_consume(structured_action["workflow_run_id"])
    structured_path = _run_path(structured_action["workflow_run_id"])
    handoff_test_action = dict(mcp_action)
    handoff_test_action["workflow_run_id"] = f"validate_handoff_{uuid.uuid4().hex[:12]}"
    handoff_test_action["trace"] = {"parent_id": "", "correlation_id": handoff_test_action["workflow_run_id"]}
    handoff_run = execute_action(handoff_test_action)
    handoff_mismatch = attach_owner_result(
        handoff_test_action["workflow_run_id"],
        {"ok": True, "status": "completed", "source_tool": "wrong.tool"},
    )
    handoff_attached = attach_owner_result(
        handoff_test_action["workflow_run_id"],
        {"ok": True, "status": "completed", "source_tool": "owner_mcp.call_readonly", "result": {"ok": True}},
    )
    handoff_status = lifecycle_status(handoff_test_action["workflow_run_id"])
    handoff_path = _run_path(handoff_test_action["workflow_run_id"])
    handoff_receipt_path = handoff_path.with_suffix(".receipt.json")
    for cleanup_path in (handoff_path, handoff_receipt_path):
        try:
            cleanup_path.unlink()
        except FileNotFoundError:
            pass
    for cleanup_path in (auto_path, auto_receipt_path):
        try:
            cleanup_path.unlink()
        except FileNotFoundError:
            pass
    for cleanup_path in (structured_path, structured_path.with_suffix(".receipt.json"), structured_artifact):
        try:
            cleanup_path.unlink()
        except FileNotFoundError:
            pass
    _, invalid_maintenance = _maintenance_command({"script": "outside.py", "subcommand": "repair", "cli_arg": ["--apply"]})
    issues = []
    if resource_action.get("owner") != "resource" or not resource_action.get("complete"):
        issues.append("resource_action_contract_invalid")
    if email_action.get("owner") != "email" or not email_action.get("approval_required"):
        issues.append("email_approval_contract_invalid")
    if maintenance_action.get("owner") != "maintenance" or not maintenance_action.get("read_only"):
        issues.append("maintenance_action_contract_invalid")
    if not invalid_maintenance:
        issues.append("maintenance_boundary_not_enforced")
    if not any("mutating_flag_blocked" in item for item in invalid_maintenance):
        issues.append("maintenance_mutating_flag_not_blocked")
    if "cancel" in OWNER_CAPABILITIES["resource"]["lifecycle"]:
        issues.append("resource_cancel_must_not_be_claimed_without_owner_support")
    if mcp_action.get("execution_affinity") != "hub_first" or mcp_action.get("hub_tool") != "owner_mcp.call_readonly":
        issues.append("mcp_hub_first_contract_invalid")
    if mcp_handoff.get("status") != "handoff_required" or mcp_handoff.get("next_action") != "call_owner_tool_then_attach_result":
        issues.append("mcp_handoff_contract_invalid")
    if session_action.get("execution_affinity") != "session_native_first" or session_action.get("session_binding") != "current_chrome":
        issues.append("session_bound_affinity_invalid")
    gui_fields = [item.get("name") for item in _as_list(_as_dict(gui_union_action.get("needs_input")).get("fields"))]
    if gui_union_action.get("owner") != "mcp" or gui_fields != ["profile"] or gui_union_action.get("auto_lifecycle", {}).get("eligible"):
        issues.append("gui_union_needs_input_invalid")
    if network_action.get("owner") != "network" or network_action.get("operation") != "plan" or not network_action.get("auto_lifecycle", {}).get("eligible"):
        issues.append("network_plan_synthesis_invalid")
    if office_action.get("owner") != "office" or office_action.get("operation") != "office_command" or not office_action.get("read_only"):
        issues.append("office_action_synthesis_invalid")
    if mobile_action.get("owner") != "mobile" or not mobile_action.get("auto_lifecycle", {}).get("eligible"):
        issues.append("mobile_status_synthesis_invalid")
    if malformed_action.get("complete") or not _as_dict(malformed_action.get("needs_input")).get("required"):
        issues.append("malformed_action_not_rejected")
    if auto_result.get("status") != "completed" or [item.get("stage") for item in _as_list(auto_result.get("stages"))] != ["run", "consume", "closeout"]:
        issues.append("auto_lifecycle_regression")
    structured_refs = _as_list(_as_dict(structured_consumed.get("consumption")).get("references"))
    if not _as_dict(structured_consumed.get("consumption")).get("ok") or _as_dict(structured_refs[0] if structured_refs else {}).get("reference_metadata", {}).get("kind") != "owner_result":
        issues.append("structured_consume_reference_invalid")
    if "cancel" not in OWNER_CAPABILITIES["network"]["lifecycle"]:
        issues.append("network_cancel_capability_missing")
    if handoff_run.get("status") != "handoff_required":
        issues.append("handoff_run_not_pending")
    if _as_dict(handoff_mismatch.get("error")).get("class") != "owner_tool_mismatch":
        issues.append("handoff_tool_mismatch_not_rejected")
    if handoff_attached.get("status") != "completed" or handoff_status.get("status") != "completed":
        issues.append("handoff_attachment_not_persisted")
    return {
        "schema": "workflow_owner_facade.validate.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "issues": issues,
        "checks": {
            "resource_action": resource_action,
            "email_action": email_action,
            "maintenance_action": maintenance_action,
            "mcp_action": mcp_action,
            "mcp_handoff": mcp_handoff,
            "session_action": session_action,
            "gui_union_action": gui_union_action,
            "network_action": network_action,
            "office_action": office_action,
            "mobile_action": mobile_action,
            "malformed_action": malformed_action,
            "auto_lifecycle": {
                "status": auto_result.get("status"),
                "stages": auto_result.get("stages"),
                "cleanup_ok": not auto_path.exists() and not auto_receipt_path.exists(),
            },
            "structured_consume": {
                "ok": _as_dict(structured_consumed.get("consumption")).get("ok"),
                "reference_count": len(structured_refs),
                "metadata_preserved": bool(structured_refs and _as_dict(structured_refs[0]).get("reference_metadata")),
                "cleanup_ok": not structured_path.exists() and not structured_artifact.exists(),
            },
            "handoff_regression": {
                "run_status": handoff_run.get("status"),
                "mismatch_error": _as_dict(handoff_mismatch.get("error")).get("class"),
                "attached_status": handoff_attached.get("status"),
                "persisted_status": handoff_status.get("status"),
                "cleanup_ok": not handoff_path.exists() and not handoff_receipt_path.exists(),
            },
            "invalid_maintenance_issues": invalid_maintenance,
            "no_central_queue": True,
            "no_business_state_database": True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Contract-driven owner facade support module")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "validate"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("--full", action="store_true", help="Emit the complete successful result.")
    args = parser.parse_args(argv)
    if args.command == "validate":
        payload = validate()
    else:
        payload = snapshot()
    output = governed_cli_payload(
        payload,
        full=bool(args.full),
        full_result_ref=f"command:python _bridge/workflow_owner_facade.py {args.command} --full",
    )
    print_json(output)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
