#!/usr/bin/env python3
"""Approved npm metadata and isolated installation owner for the resource layer."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from resource_network_execution import apply_execution_env


JsonResultFactory = Callable[..., dict[str, Any]]
BRIDGE_ROOT = Path(__file__).resolve().parent
WINDOWS_NODE = Path("C:/Program Files/nodejs/node.exe")
WINDOWS_NPM_CLI = Path("C:/Program Files/nodejs/node_modules/npm/bin/npm-cli.js")
INSTALL_ACTIONS = {"install", "add"}
SAFE_PACKAGE_SPEC = re.compile(
    r"^(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+(?:@[A-Za-z0-9_.~^<>=!+*-]+)?$"
)


def _metadata(request: dict[str, Any]) -> dict[str, Any]:
    value = request.get("metadata")
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def node_package_action(request: dict[str, Any]) -> str:
    metadata = _metadata(request)
    return str(metadata.get("package_action") or metadata.get("action") or "inspect").strip().lower()


def _package_spec(request: dict[str, Any]) -> str:
    metadata = _metadata(request)
    raw = str(metadata.get("package_spec") or metadata.get("package_id") or request.get("target") or request.get("name") or "").strip()
    return raw if SAFE_PACKAGE_SPEC.fullmatch(raw) else ""


def _package_name(package_spec: str) -> str:
    if package_spec.startswith("@"):
        slash = package_spec.find("/")
        at = package_spec.find("@", slash)
        return package_spec if at < 0 else package_spec[:at]
    return package_spec.split("@", 1)[0]


def _default_dependency_root(platform_name: str | None = None) -> Path:
    if (platform_name or os.name) == "nt":
        return BRIDGE_ROOT / "runtime_dependencies" / "node"
    data_home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return data_home / "codex-resource-dependencies" / "node"


def _npm_command(platform_name: str | None = None) -> list[str]:
    if (platform_name or os.name) == "nt":
        return [str(WINDOWS_NODE), str(WINDOWS_NPM_CLI)]
    node = shutil.which("node")
    npm = shutil.which("npm")
    return [npm] if node and npm else []


def _npm_runtime_available() -> bool:
    if os.name == "nt":
        return WINDOWS_NODE.exists() and WINDOWS_NPM_CLI.exists()
    return bool(_npm_command())


def _target_dir(request: dict[str, Any], package_name: str) -> tuple[Path, bool]:
    metadata = _metadata(request)
    raw = str(request.get("target_dir") or "").strip()
    explicit = _truthy(metadata.get("package_target_dir_explicit")) and bool(raw)
    if explicit:
        return Path(raw).expanduser().resolve(), True
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", package_name).strip("-").lower()
    return (_default_dependency_root() / safe_name).resolve(), False


def _run_npm(arguments: list[str], package: dict[str, Any], timeout: int) -> subprocess.CompletedProcess[str]:
    env = apply_execution_env(dict(os.environ), package)
    env["PYTHONIOENCODING"] = "utf-8"
    env["NPM_CONFIG_UPDATE_NOTIFIER"] = "false"
    env["NPM_CONFIG_FUND"] = "false"
    command = _npm_command()
    if not command:
        raise FileNotFoundError("node and npm must both be available for a managed npm request")
    command.extend(arguments)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(5, min(timeout, 300)),
        env=env,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0,
    )


def _installed_package_json(target_dir: Path, package_name: str) -> tuple[Path, dict[str, Any]]:
    path = target_dir / "node_modules" / Path(*package_name.split("/")) / "package.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return path, {}
    return path, payload if isinstance(payload, dict) else {}


def execute_node_package_request(
    request: dict[str, Any],
    package: dict[str, Any],
    timeout: int,
    json_result: JsonResultFactory,
) -> dict[str, Any]:
    package_spec = _package_spec(request)
    if not package_spec:
        return json_result(
            ok=False,
            status="failed",
            source="package_manager",
            result_kind="node_package_plan",
            error_class="unsafe_or_invalid_package_spec",
            reason="npm package spec must be a bounded package name with an optional version",
            next_action="refine_package_spec_and_retry",
        )
    package_name = _package_name(package_spec)
    action = node_package_action(request)
    if action not in INSTALL_ACTIONS:
        if not _npm_runtime_available():
            return json_result(
                ok=False,
                status="failed",
                source="package_manager",
                result_kind="node_package_runtime",
                error_class="node_or_npm_unavailable",
                reason="managed npm requests require node and npm in the current platform runtime",
                next_action="install_or_repair_the_platform_node_runtime",
            )
        try:
            proc = _run_npm(["view", package_spec, "--json", "--registry", "https://registry.npmjs.org"], package, timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return json_result(ok=False, status="failed", source="package_manager", result_kind="node_package_metadata", error_class="node_package_metadata_timeout", reason="npm view timed out", next_action="retry_with_larger_timeout_or_refine_network_route")
        try:
            metadata_payload = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
        except json.JSONDecodeError:
            metadata_payload = {}
        ok = proc.returncode == 0 and isinstance(metadata_payload, dict) and bool(metadata_payload)
        return json_result(
            ok=ok,
            status="completed" if ok else "failed",
            source="package_manager",
            result_kind="node_package_metadata",
            content=json.dumps(metadata_payload, ensure_ascii=False)[:4000] if ok else "",
            error_class="" if ok else "node_package_metadata_failed",
            reason="" if ok else str(proc.stderr or proc.stdout or f"returncode={proc.returncode}")[-1000:],
            metadata={
                "package": package_name,
                "package_spec": package_spec,
                "package_ecosystem": "npm",
                "latest": str(metadata_payload.get("version") or "") if isinstance(metadata_payload, dict) else "",
                "bin": metadata_payload.get("bin") if isinstance(metadata_payload, dict) else {},
                "network_route_mode": package.get("route_mode", ""),
            },
            next_action="consume_resource" if ok else "verify_package_name_or_refine_network_route",
        )
    metadata = _metadata(request)
    if not _truthy(metadata.get("install_approved")):
        return json_result(
            ok=False,
            status="handoff_required",
            source="package_manager",
            result_kind="node_package_install_plan",
            error_class="install_requires_explicit_approval",
            reason="npm package install requires metadata.install_approved=true",
            metadata={"package": package_name, "package_spec": package_spec, "will_install": False},
            next_action="request_explicit_install_approval_or_keep_as_plan",
        )
    if not _truthy(request.get("allow_filesystem_write")):
        return json_result(
            ok=False,
            status="failed",
            source="package_manager",
            result_kind="node_package_install_plan",
            error_class="filesystem_write_not_allowed",
            reason="npm package install requires allow_filesystem_write=true",
            next_action="resubmit_with_explicit_filesystem_write_permission",
        )
    if not _npm_runtime_available():
        return json_result(
            ok=False,
            status="failed",
            source="package_manager",
            result_kind="node_package_runtime",
            error_class="node_or_npm_unavailable",
            reason="managed npm requests require node and npm in the current platform runtime",
            next_action="install_or_repair_the_platform_node_runtime",
        )
    target_dir, target_explicit = _target_dir(request, package_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    arguments = [
        "install",
        "--prefix",
        str(target_dir),
        "--no-audit",
        "--no-fund",
        "--registry",
        "https://registry.npmjs.org",
    ]
    if not _truthy(metadata.get("allow_install_scripts")):
        arguments.append("--ignore-scripts")
    arguments.append(package_spec)
    try:
        proc = _run_npm(arguments, package, timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return json_result(
            ok=False,
            status="failed",
            source="package_manager",
            result_kind="node_package_install",
            writes_files=True,
            error_class="node_package_install_timeout",
            reason="npm install exceeded the resource request timeout",
            metadata={"package": package_name, "package_spec": package_spec, "target_dir": str(target_dir)},
            next_action="retry_with_larger_timeout_or_refine_network_route",
        )
    package_json_path, package_json = _installed_package_json(target_dir, package_name)
    installed_version = str(package_json.get("version") or "")
    ok = proc.returncode == 0 and bool(installed_version)
    return json_result(
        ok=ok,
        status="completed" if ok else "failed",
        source="package_manager",
        result_kind="node_package_install",
        content=str(proc.stdout or "")[:4000],
        writes_files=True,
        permission_boundary="approved_isolated_package_install",
        error_class="" if ok else "node_package_install_verification_failed" if proc.returncode == 0 else "node_package_install_failed",
        reason="" if ok else str(proc.stderr or proc.stdout or f"returncode={proc.returncode}")[-1000:],
        metadata={
            "package": package_name,
            "package_spec": package_spec,
            "package_action": action,
            "package_ecosystem": "npm",
            "installed_version": installed_version,
            "target_dir": str(target_dir),
            "target_dir_explicit": target_explicit,
            "package_json_path": str(package_json_path),
            "bin": package_json.get("bin") if isinstance(package_json.get("bin"), (dict, str)) else {},
            "install_scripts_allowed": _truthy(metadata.get("allow_install_scripts")),
            "install_returncode": proc.returncode,
            "network_route_mode": package.get("route_mode", ""),
            "install_stderr_tail": str(proc.stderr or "")[-1000:],
        },
        next_action="consume_resource" if ok else "inspect_npm_output_and_retry_with_correct_package_spec",
    )


def validate() -> dict[str, Any]:
    blocked = execute_node_package_request(
        {"target": "example", "metadata": {"package_action": "install", "package_ecosystem": "npm"}},
        {"ok": True, "env": {}, "unset_env": []},
        1,
        lambda **payload: payload,
    )
    return {
        "schema": "resource_node_package_owner.validate.v1",
        "ok": blocked.get("error_class") == "install_requires_explicit_approval" and _npm_runtime_available(),
        "approval_gate_ok": blocked.get("error_class") == "install_requires_explicit_approval",
        "node_available": bool(shutil.which("node")) if os.name != "nt" else WINDOWS_NODE.exists(),
        "npm_cli_available": bool(shutil.which("npm")) if os.name != "nt" else WINDOWS_NPM_CLI.exists(),
        "default_target_root": str(_default_dependency_root()),
        "global_install_supported": False,
        "install_scripts_default": "disabled",
    }
