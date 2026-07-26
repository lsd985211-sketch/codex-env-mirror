#!/usr/bin/env python3
"""Windows package-manager adapter for resource package requests.

Ownership: plan, search, explicitly approved install, and verify operations for
Windows command-line package managers used by the resource layer.
Non-goals: changing global proxy/package-manager configuration, silently
installing software, uninstalling software, or granting permissions.
State behavior: search/plan are read-only; install runs only with explicit
request metadata approval.
Caller context: resource_package_owner.execute_package_metadata dispatches
Windows tool ecosystems here.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from typing import Any, Callable

from resource_network_execution import apply_execution_env
from resource_validation_profile import metadata_profile


JsonResultFactory = Callable[..., dict[str, Any]]

WINDOWS_TOOL_ECOSYSTEMS = {"windows", "windows_tool", "win_tool", "choco", "chocolatey", "winget"}
WINDOWS_PACKAGE_MANAGERS = ("choco", "winget")


def _hidden_creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _metadata(request: dict[str, Any]) -> dict[str, Any]:
    value = request.get("metadata")
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "approved", "approve"}


def _package_action(request: dict[str, Any]) -> str:
    metadata = _metadata(request)
    return str(metadata.get("package_action") or metadata.get("action") or "search").strip().lower()


def _preferred_manager(request: dict[str, Any]) -> str:
    metadata = _metadata(request)
    value = str(metadata.get("windows_package_manager") or metadata.get("package_manager") or "").strip().lower()
    if value == "chocolatey":
        return "choco"
    if value in WINDOWS_PACKAGE_MANAGERS:
        return value
    return ""


def availability() -> dict[str, Any]:
    choco_path = shutil.which("choco")
    winget_path = shutil.which("winget")
    return {
        "schema": "resource_windows_package_manager.availability.v1",
        "ok": bool(choco_path or winget_path),
        "choco_available": bool(choco_path),
        "choco_path": choco_path or "",
        "winget_available": bool(winget_path),
        "winget_path": winget_path or "",
    }


def _choose_manager(request: dict[str, Any], state: dict[str, Any]) -> str:
    preferred = _preferred_manager(request)
    if preferred:
        return preferred
    if state.get("choco_available"):
        return "choco"
    if state.get("winget_available"):
        return "winget"
    return "choco"


def _manager_path(manager: str, state: dict[str, Any]) -> str:
    if manager == "winget":
        return str(state.get("winget_path") or "winget")
    return str(state.get("choco_path") or "choco")


def _choco_proxy_args(package: dict[str, Any]) -> list[str]:
    proxy_url = str(package.get("proxy_url") or "")
    route_mode = str(package.get("route_mode") or "")
    if proxy_url and route_mode not in {"probe_selected_direct", "direct"}:
        return [f"--proxy={proxy_url}"]
    return []


def _search_command(
    manager: str,
    package_name: str,
    manager_path: str,
    package: dict[str, Any],
    request: dict[str, Any],
) -> list[str]:
    if manager == "winget":
        metadata = _metadata(request)
        winget_id = str(metadata.get("winget_id") or "").strip()
        if winget_id:
            command = [
                manager_path,
                "search",
                "--id",
                winget_id,
                "--exact",
                "--source",
                "winget",
                "--disable-interactivity",
            ]
        else:
            command = [
                manager_path,
                "search",
                "--query",
                package_name,
                "--disable-interactivity",
            ]
        if _truthy(metadata.get("accept_winget_agreements")):
            command.append("--accept-source-agreements")
        return command
    return [manager_path, "search", package_name, "--exact", "--limit-output", "--no-progress", *_choco_proxy_args(package)]


def _install_command(manager: str, package_name: str, manager_path: str, package: dict[str, Any], request: dict[str, Any]) -> list[str]:
    metadata = _metadata(request)
    if manager == "winget":
        command = [manager_path, "install", "--exact", "--disable-interactivity"]
        if _truthy(metadata.get("accept_winget_agreements")):
            command.extend(["--accept-source-agreements", "--accept-package-agreements"])
        package_id = str(metadata.get("winget_id") or metadata.get("package_id") or package_name).strip()
        command.extend(["--id", package_id])
        return command
    return [manager_path, "install", package_name, "-y", "--no-progress", *_choco_proxy_args(package)]


def _verify_command(package_name: str, request: dict[str, Any]) -> list[str]:
    metadata = _metadata(request)
    command = metadata.get("verify_command")
    if isinstance(command, list) and all(isinstance(item, str) for item in command) and command:
        return command
    binary = str(metadata.get("verify_binary") or package_name).strip()
    return ["where.exe", binary]


def _run(command: list[str], package: dict[str, Any], timeout: int) -> dict[str, Any]:
    env = apply_execution_env(os.environ, package)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=max(1, timeout),
            creationflags=_hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "error_class": type(exc).__name__, "reason": str(exc), "stdout": "", "stderr": "", "returncode": None}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "error_class": "" if proc.returncode == 0 else "package_manager_command_failed",
        "reason": "" if proc.returncode == 0 else ((proc.stderr or proc.stdout or f"returncode={proc.returncode}").strip()[:1000]),
    }


def _metadata_payload(
    *,
    package_name: str,
    ecosystem: str,
    manager: str,
    state: dict[str, Any],
    profile_name: str,
    package: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "package": package_name,
        "package_ecosystem": ecosystem,
        "windows_package_manager": manager,
        "choco_available": bool(state.get("choco_available")),
        "winget_available": bool(state.get("winget_available")),
        "validation_profile": profile_name,
        "network_route_mode": package.get("route_mode", ""),
        "network_target_kind": package.get("target_kind", ""),
        "command_kind": "windows_package_manager",
        **(extra or {}),
    }


def execute_windows_package_request(
    request: dict[str, Any],
    package_name: str,
    ecosystem: str,
    package: dict[str, Any],
    timeout: int,
    json_result: JsonResultFactory,
) -> dict[str, Any]:
    metadata = _metadata(request)
    profile = metadata_profile(metadata)
    state = availability()
    manager = _choose_manager(request, state)
    manager_path = _manager_path(manager, state)
    manager_available = bool(state.get(f"{manager}_available"))
    action = _package_action(request)

    if action in {"install", "add"} and not _truthy(metadata.get("install_approved")):
        return json_result(
            ok=False,
            status="handoff_required",
            source="package_manager",
            result_kind="windows_package_manager_plan",
            error_class="install_requires_explicit_approval",
            reason="windows package install requires metadata.install_approved=true",
            metadata=_metadata_payload(
                package_name=package_name,
                ecosystem=ecosystem,
                manager=manager,
                state=state,
                profile_name=profile.name,
                package=package,
                extra={"package_action": action, "will_install": False},
            ),
            next_action="request_explicit_install_approval_or_keep_as_plan",
        )

    if (profile.name == "quick" and action not in {"install", "add"}) or action in {"plan", "audit"}:
        return json_result(
            ok=True,
            status="completed",
            source="package_manager",
            result_kind="windows_package_manager_plan",
            content=f"{manager} plan for {package_name}; install requires explicit approval.",
            metadata=_metadata_payload(
                package_name=package_name,
                ecosystem=ecosystem,
                manager=manager,
                state=state,
                profile_name=profile.name,
                package=package,
                extra={"package_action": action, "will_install": False, "manager_available": manager_available},
            ),
            next_action="consume_resource",
        )

    if not manager_available:
        return json_result(
            ok=False,
            status="handoff_required",
            source="package_manager",
            result_kind="windows_package_manager_plan",
            error_class="windows_package_manager_unavailable",
            reason=f"{manager} is not available on PATH",
            metadata=_metadata_payload(
                package_name=package_name,
                ecosystem=ecosystem,
                manager=manager,
                state=state,
                profile_name=profile.name,
                package=package,
                extra={"package_action": action, "will_install": False},
            ),
            next_action="install_or_expose_chocolatey_or_winget_then_retry",
        )

    search = _run(
        _search_command(manager, package_name, manager_path, package, request),
        package,
        min(timeout, profile.max_owner_timeout_seconds),
    )
    if action not in {"install", "add"}:
        search_content = str(search.get("stdout") or "").strip()
        return json_result(
            ok=bool(search.get("ok")),
            status="completed" if search.get("ok") else "failed",
            source="package_manager",
            result_kind="windows_package_manager_search",
            content=search_content[:4000],
            error_class=str(search.get("error_class") or ""),
            reason=str(search.get("reason") or ""),
            metadata=_metadata_payload(
                package_name=package_name,
                ecosystem=ecosystem,
                manager=manager,
                state=state,
                profile_name=profile.name,
                package=package,
                extra={
                    "package_action": action,
                    "will_install": False,
                    "returncode": search.get("returncode"),
                    "candidate_count": 1 if search.get("ok") and search_content else 0,
                    "stderr_tail": str(search.get("stderr") or "")[-1000:],
                },
            ),
            next_action="consume_resource" if search.get("ok") else "verify_package_name_or_try_other_package_manager",
        )

    install = _run(_install_command(manager, package_name, manager_path, package, request), package, min(timeout, profile.max_owner_timeout_seconds))
    verify = _run(_verify_command(package_name, request), package, min(10, profile.max_owner_timeout_seconds)) if install.get("ok") else {}
    ok = bool(install.get("ok")) and (not verify or bool(verify.get("ok")))
    return json_result(
        ok=ok,
        status="completed" if ok else "failed",
        source="package_manager",
        result_kind="windows_package_manager_install",
        content=str(install.get("stdout") or "")[:4000],
        writes_files=True,
        error_class="" if ok else str(install.get("error_class") or verify.get("error_class") or "windows_package_install_failed"),
        reason="" if ok else str(install.get("reason") or verify.get("reason") or "windows_package_install_failed"),
        metadata=_metadata_payload(
            package_name=package_name,
            ecosystem=ecosystem,
            manager=manager,
            state=state,
            profile_name=profile.name,
            package=package,
            extra={
                "package_action": action,
                "will_install": True,
                "install_returncode": install.get("returncode"),
                "verify_returncode": verify.get("returncode"),
                "verify_stdout": str(verify.get("stdout") or "")[:1000],
                "install_stderr_tail": str(install.get("stderr") or "")[-1000:],
            },
        ),
        next_action="consume_resource" if ok else "inspect_package_manager_output_and_retry_with_correct_package_id",
    )


def validate() -> dict[str, Any]:
    package = {"ok": True, "route_mode": "probe_selected_direct", "target_kind": "package", "env": {}, "unset_env": []}
    plan = execute_windows_package_request(
        {
            "target": "aria2",
            "metadata": {
                "package_ecosystem": "windows_tool",
                "validation_profile": "quick",
                "windows_package_manager": "choco",
            },
        },
        "aria2",
        "windows_tool",
        package,
        1,
        lambda **payload: payload,
    )
    blocked_install = execute_windows_package_request(
        {
            "target": "aria2",
            "metadata": {
                "package_ecosystem": "windows_tool",
                "validation_profile": "quick",
                "windows_package_manager": "choco",
                "package_action": "install",
            },
        },
        "aria2",
        "windows_tool",
        package,
        1,
        lambda **payload: payload,
    )
    return {
        "schema": "resource_windows_package_manager.validate.v1",
        "ok": bool(
            availability().get("schema")
            and plan.get("status") == "completed"
            and not plan.get("metadata", {}).get("will_install")
            and blocked_install.get("status") == "handoff_required"
            and blocked_install.get("error_class") == "install_requires_explicit_approval"
        ),
        "availability": availability(),
        "quick_plan_ok": plan.get("status") == "completed",
        "install_gate_ok": blocked_install.get("error_class") == "install_requires_explicit_approval",
        "writes_files_by_default": False,
        "writes_remote_state": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows package-manager adapter")
    parser.add_argument("command", choices=("availability", "validate"))
    args = parser.parse_args()
    payload = availability() if args.command == "availability" else validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
