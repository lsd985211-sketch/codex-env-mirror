#!/usr/bin/env python3
"""Approved isolated Python package installation for the resource layer.

Ownership: execute explicitly approved pip installs into a bounded target
directory and verify the installed distribution metadata.
Non-goals: global/site-packages installs, PATH changes, uninstall/upgrade
policy, dependency selection, or package metadata discovery.
State behavior: writes only after install_approved, filesystem permission, and
an isolated target directory have been established by the resource request.
Caller context: resource_package_owner routes Python install actions here.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

import managed_python_dependency_runtime as managed_python_runtime
from resource_network_execution import apply_execution_env


JsonResultFactory = Callable[..., dict[str, Any]]
BRIDGE_ROOT = Path(__file__).resolve().parent
DEFAULT_DEPENDENCY_ROOT = BRIDGE_ROOT / "runtime_dependencies"
INSTALL_ACTIONS = {"install", "add"}
SAFE_PACKAGE_SPEC = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~]=?[A-Za-z0-9_.+-]+)?$"
)


def _metadata(request: dict[str, Any]) -> dict[str, Any]:
    value = request.get("metadata")
    return value if isinstance(value, dict) else {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def python_package_action(request: dict[str, Any]) -> str:
    metadata = _metadata(request)
    return str(metadata.get("package_action") or metadata.get("action") or "search").strip().lower()


def is_python_install_request(request: dict[str, Any]) -> bool:
    return python_package_action(request) in INSTALL_ACTIONS


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _package_spec(request: dict[str, Any], package_name: str) -> str:
    metadata = _metadata(request)
    raw = str(metadata.get("package_spec") or request.get("target") or package_name).strip()
    return raw if SAFE_PACKAGE_SPEC.fullmatch(raw) else ""


def _target_dir(request: dict[str, Any], package_name: str) -> tuple[Path, bool]:
    metadata = _metadata(request)
    explicit = _truthy(metadata.get("package_target_dir_explicit"))
    raw = str(request.get("target_dir") or "").strip()
    if explicit and raw:
        return Path(raw).expanduser().resolve(), True
    return managed_python_runtime.scoped_dependency_target(DEFAULT_DEPENDENCY_ROOT, package_name).resolve(), False


def _installed_version(target_dir: Path, package_name: str) -> str:
    expected = _canonical_name(package_name)
    for distribution in importlib.metadata.distributions(path=[str(target_dir)]):
        name = str(distribution.metadata.get("Name") or "")
        if _canonical_name(name) == expected:
            return str(distribution.version or "")
    return ""


def runtime_identity() -> dict[str, str]:
    return managed_python_runtime.runtime_identity()


def _atomic_install_target(
    target_dir: Path,
    package_spec: str,
    package: dict[str, Any],
    timeout: int,
    *,
    required_imports: tuple[str, ...] = (),
) -> tuple[dict[str, Any], Path]:
    parent = target_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging_dir = parent / f".{target_dir.name}.install-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    staging_dir.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--upgrade",
        "--force-reinstall",
        "--target",
        str(staging_dir),
        package_spec,
    ]
    env = apply_execution_env(dict(os.environ), package)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, min(timeout, 300)),
            env=env,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "python package install timeout"}, staging_dir
    if proc.returncode != 0:
        shutil.rmtree(staging_dir, ignore_errors=True)
        return {"ok": False, "returncode": proc.returncode, "stdout": proc.stdout or "", "stderr": proc.stderr or ""}, staging_dir
    import_probe = managed_python_runtime.probe_imports(
        staging_dir,
        required_imports,
        python_executable=sys.executable,
        timeout=min(timeout, 60),
    )
    if not import_probe.get("ok"):
        shutil.rmtree(staging_dir, ignore_errors=True)
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": str(import_probe.get("error") or import_probe.get("stderr") or "managed dependency import smoke failed"),
            "reason": "managed_dependency_import_smoke_failed",
            "import_probe": import_probe,
        }, staging_dir
    previous_dir = parent / f".{target_dir.name}.previous-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    try:
        if target_dir.exists():
            target_dir.replace(previous_dir)
        staging_dir.replace(target_dir)
    except OSError:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        if previous_dir.exists():
            previous_dir.replace(target_dir)
        shutil.rmtree(staging_dir, ignore_errors=True)
        return {"ok": False, "returncode": proc.returncode, "stdout": proc.stdout or "", "stderr": "atomic dependency replacement failed"}, staging_dir
    shutil.rmtree(previous_dir, ignore_errors=True)
    return {
        "ok": True,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "import_probe": import_probe,
    }, staging_dir


def normalize_managed_dependency_acl(target_dir: Path) -> dict[str, Any]:
    """Restore inherited ACLs for resource-layer managed dependencies on Windows."""
    if os.name != "nt":
        return {"ok": True, "applied": False, "reason": "non_windows_platform"}
    try:
        proc = subprocess.run(
            ["icacls", str(target_dir), "/inheritance:e", "/T", "/C", "/Q"],
            capture_output=True,
            text=True,
            encoding="mbcs" if os.name == "nt" else "utf-8",
            errors="replace",
            timeout=60,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    except Exception as exc:
        return {"ok": False, "applied": True, "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "applied": True,
        "returncode": proc.returncode,
        "stdout_tail": str(proc.stdout or "")[-1000:],
        "stderr_tail": str(proc.stderr or "")[-1000:],
        "policy": "enable inherited ACLs recursively without removing explicit entries",
    }


def execute_python_package_install(
    request: dict[str, Any],
    package_name: str,
    package: dict[str, Any],
    timeout: int,
    json_result: JsonResultFactory,
) -> dict[str, Any]:
    metadata = _metadata(request)
    if not _truthy(metadata.get("install_approved")):
        return json_result(
            ok=False,
            status="handoff_required",
            source="package_manager",
            result_kind="python_package_install_plan",
            error_class="install_requires_explicit_approval",
            reason="Python package install requires metadata.install_approved=true",
            metadata={"package": package_name, "package_action": python_package_action(request), "will_install": False},
            next_action="request_explicit_install_approval_or_keep_as_plan",
        )
    if not _truthy(request.get("allow_filesystem_write")):
        return json_result(
            ok=False,
            status="handoff_required",
            source="package_manager",
            result_kind="python_package_install_plan",
            error_class="install_requires_filesystem_write_permission",
            reason="Python package install requires allow_filesystem_write=true",
            metadata={"package": package_name, "package_action": python_package_action(request), "will_install": False},
            next_action="resubmit_with_filesystem_write_permission",
        )
    package_spec = _package_spec(request, package_name)
    if not package_spec:
        return json_result(
            ok=False,
            status="failed",
            source="package_manager",
            result_kind="python_package_install_plan",
            error_class="unsafe_or_invalid_package_spec",
            reason="package spec must be a bounded package name with an optional version constraint",
            metadata={"package": package_name, "will_install": False},
            next_action="refine_package_spec_and_retry",
        )

    target_dir, target_explicit = _target_dir(request, package_name)
    import_contract = managed_python_runtime.required_imports(package_name)
    install, _staging_dir = _atomic_install_target(
        target_dir,
        package_spec,
        package,
        timeout,
        required_imports=import_contract,
    )
    if not install.get("ok"):
        install_error_class = (
            "managed_dependency_import_smoke_failed"
            if install.get("reason") == "managed_dependency_import_smoke_failed"
            else "python_package_install_timeout"
            if install.get("returncode") is None
            else "python_package_install_failed"
        )
        return json_result(
            ok=False,
            status="failed",
            source="package_manager",
            result_kind="python_package_install",
            writes_files=True,
            error_class=install_error_class,
            reason=str(install.get("stderr") or install.get("stdout") or install.get("reason") or "python package install failed")[-1000:],
            metadata={"package": package_name, "package_spec": package_spec, "target_dir": str(target_dir), "will_install": True, "runtime": runtime_identity(), "runtime_key": managed_python_runtime.runtime_key(), "required_imports": list(import_contract), "import_probe": install.get("import_probe", {}), "atomic_replace": True},
            next_action=(
                "inspect_import_probe_and_reinstall_for_the_reported_runtime_key"
                if install_error_class == "managed_dependency_import_smoke_failed"
                else "retry_with_larger_timeout_or_refine_network_route"
                if install_error_class == "python_package_install_timeout"
                else "inspect_pip_output_and_retry_with_correct_package_spec"
            ),
        )

    acl_state = (
        normalize_managed_dependency_acl(target_dir)
        if not target_explicit
        else {"ok": True, "applied": False, "reason": "explicit_target_acl_preserved" if target_explicit else "install_failed"}
    )
    installed_version = _installed_version(target_dir, package_name)
    ok = bool(installed_version) and bool(acl_state.get("ok"))
    if ok:
        error_class = ""
    elif not install.get("ok"):
        error_class = "python_package_install_failed"
    elif not acl_state.get("ok"):
        error_class = "python_package_runtime_acl_normalization_failed"
    else:
        error_class = "python_package_install_verification_failed"
    return json_result(
        ok=ok,
        status="completed" if ok else "failed",
        source="package_manager",
        result_kind="python_package_install",
        content=str(install.get("stdout") or "")[:4000],
        writes_files=True,
        permission_boundary="approved_isolated_package_install",
        error_class=error_class,
        reason=(
            ""
            if ok
            else str(acl_state.get("reason") or acl_state.get("stderr_tail") or install.get("stderr") or install.get("stdout") or f"returncode={install.get('returncode')}")[-1000:]
        ),
        metadata={
            "package": package_name,
            "package_spec": package_spec,
            "package_action": python_package_action(request),
            "installed_version": installed_version,
            "target_dir": str(target_dir),
            "target_dir_explicit": target_explicit,
            "will_install": True,
            "install_returncode": install.get("returncode"),
            "network_route_mode": package.get("route_mode", ""),
            "network_target_kind": package.get("target_kind", ""),
            "install_stderr_tail": str(install.get("stderr") or "")[-1000:],
            "runtime_acl": acl_state,
            "runtime": runtime_identity(),
            "runtime_key": managed_python_runtime.runtime_key(),
            "required_imports": list(import_contract),
            "import_probe": install.get("import_probe", {}),
            "atomic_replace": True,
        },
        next_action=(
            "consume_resource"
            if ok
            else "repair_managed_dependency_acl_and_revalidate"
            if error_class == "python_package_runtime_acl_normalization_failed"
            else "inspect_pip_output_and_retry_with_correct_package_spec"
        ),
    )


def validate() -> dict[str, Any]:
    blocked = execute_python_package_install(
        {"target": "example", "metadata": {"package_action": "install"}},
        "example",
        {"ok": True, "env": {}, "unset_env": []},
        1,
        lambda **payload: payload,
    )
    return {
        "schema": "resource_python_package_installer.validate.v1",
        "ok": blocked.get("error_class") == "install_requires_explicit_approval",
        "approval_gate_ok": blocked.get("error_class") == "install_requires_explicit_approval",
        "default_target_root": str(DEFAULT_DEPENDENCY_ROOT),
        "default_target_scope": "python_abi_and_platform",
        "runtime_key": managed_python_runtime.runtime_key(),
        "global_install_supported": False,
        "managed_dependency_acl_policy": "enable inherited ACLs recursively after successful default-target installs on Windows",
    }
