#!/usr/bin/env python3
"""Python package metadata owner and approved-install routing adapter.

Ownership: package metadata lookup plus routing explicitly approved isolated
Python installs to the dedicated installer module.
Non-goals: global installs, mutating package-manager config, changing PATH, or
choosing broader resource routing policy.
State behavior: metadata lookup is read-only apart from its cache; install
writes are delegated and require explicit approval and filesystem permission.
Caller context: `resource_owner_executor.execute_package_metadata` facade.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from intent_routing import matched_terms
from resource_network_execution import apply_execution_env, execution_package_from_gateway_plan
from resource_package_metadata_cache import fixture_result, read_cache, write_cache
from resource_python_package_installer import (
    execute_python_package_install,
    is_python_install_request,
    validate as validate_python_package_installer,
)
from resource_node_package_owner import execute_node_package_request, validate as validate_node_package_owner
from resource_validation_profile import metadata_profile
from resource_windows_package_manager import (
    WINDOWS_TOOL_ECOSYSTEMS,
    execute_windows_package_request,
    validate as validate_windows_package_manager,
)


JsonResultFactory = Callable[..., dict[str, Any]]


def _open_pypi_json(package_name: str, package: dict[str, Any], timeout: int) -> dict[str, Any]:
    quoted = urllib.parse.quote(package_name.strip(), safe="")
    url = f"https://pypi.org/pypi/{quoted}/json"
    proxy_url = str(package.get("proxy_url") or "")
    route_mode = str(package.get("route_mode") or "")
    proxies = {} if route_mode in {"probe_selected_direct", "direct"} else {"http": proxy_url, "https": proxy_url} if proxy_url else None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "codex-resource-layer"})
    with opener.open(request, timeout=max(1, min(timeout, 8))) as response:
        body = response.read(512_000)
        payload = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("pypi_json_returned_non_object")
    return payload


def package_name_from_request(request: dict[str, Any]) -> str:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    raw = str(metadata.get("package_id") or request.get("target") or request.get("name") or "").strip()
    match = re.search(r"[A-Za-z0-9_.-]+", raw)
    return match.group(0) if match else ""


def package_ecosystem_from_request(request: dict[str, Any]) -> str:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    value = str(metadata.get("package_ecosystem") or metadata.get("ecosystem") or "").strip().lower()
    text = " ".join(str(request.get(key) or "") for key in ("task", "target", "name")).lower()
    if value:
        return value
    if matched_terms(text, ("winget", "choco", "chocolatey", "windows tool", "windows package")):
        return "windows_tool"
    if matched_terms(text, ("npm", "pnpm", "yarn", "npx", "node package")):
        return "npm"
    return "python"


def _cached_result(package_name: str, cached: dict[str, Any], profile: Any, json_result: JsonResultFactory) -> dict[str, Any]:
    metadata = cached.get("metadata") if isinstance(cached.get("metadata"), dict) else {}
    if not cached.get("ok"):
        return json_result(
            ok=False,
            status="failed",
            source="package_manager",
            result_kind="package_index_metadata",
            content=str(cached.get("content") or "")[:4000],
            error_class=str(metadata.get("error_class") or "package_index_cached_failure"),
            reason=str(cached.get("content") or metadata.get("reason") or "cached package metadata failure"),
            metadata={
                "package": package_name,
                "package_ecosystem": "python",
                "cache_kind": str(cached.get("cache_kind") or "disk"),
                "cache_source": str(cached.get("source") or ""),
                "validation_profile": profile.name,
                "command_kind": "package_metadata_negative_cache",
                **metadata,
            },
            next_action="verify_package_name_or_use_codex_current_turn_owner_tool",
        )
    return json_result(
        ok=True,
        status="completed",
        source="package_manager",
        result_kind="package_index_metadata",
        content=str(cached.get("content") or "")[:4000],
        metadata={
            "package": package_name,
            "package_ecosystem": "python",
            "latest": str(cached.get("latest") or ""),
            "cache_kind": str(cached.get("cache_kind") or "disk"),
            "cache_source": str(cached.get("source") or ""),
            "validation_profile": profile.name,
            "command_kind": "package_metadata_cache",
        },
        next_action="consume_resource",
    )


def _fixture_result(package_name: str, profile: Any, json_result: JsonResultFactory) -> dict[str, Any]:
    fixture = fixture_result("python", package_name)
    if fixture:
        return json_result(
            ok=True,
            status="completed",
            source="package_manager",
            result_kind="package_index_metadata",
            content=str(fixture.get("content") or "")[:4000],
            metadata={
                "package": package_name,
                "package_ecosystem": "python",
                "latest": str(fixture.get("latest") or ""),
                "cache_kind": "fixture",
                "cache_source": str(fixture.get("source") or ""),
                "validation_profile": profile.name,
                "command_kind": "package_metadata_fixture",
            },
            next_action="consume_resource",
        )
    return json_result(
        ok=False,
        status="handoff_required",
        error_class="package_metadata_fixture_missing",
        reason=f"validation_profile={profile.name} does not allow live package-index lookup for package={package_name}",
        metadata={"package": package_name, "package_ecosystem": "python", "validation_profile": profile.name},
        next_action="run_smoke_or_full_profile_for_live_package_index",
    )


def _pypi_success_result(
    *,
    package_name: str,
    package: dict[str, Any],
    profile: Any,
    pypi_payload: dict[str, Any],
    json_result: JsonResultFactory,
) -> dict[str, Any]:
    info = pypi_payload.get("info") if isinstance(pypi_payload.get("info"), dict) else {}
    latest = str(info.get("version") or "")
    releases = pypi_payload.get("releases") if isinstance(pypi_payload.get("releases"), dict) else {}
    versions = sorted(releases.keys(), reverse=True)[:20]
    content = "\n".join(
        [
            f"{package_name} ({latest})" if latest else package_name,
            "Available versions: " + ", ".join(versions) if versions else "Available versions: <unknown>",
            str(info.get("summary") or ""),
        ]
    ).strip()
    result_payload = json_result(
        ok=True,
        status="completed",
        source="package_manager",
        result_kind="package_index_metadata",
        content=content[:4000],
        metadata={
            "package": package_name,
            "package_ecosystem": "python",
            "latest": latest,
            "network_route_mode": package.get("route_mode", ""),
            "network_target_kind": package.get("target_kind", ""),
            "command_kind": "pypi_json_metadata",
            "validation_profile": profile.name,
            "release_count": len(releases),
        },
        next_action="consume_resource",
    )
    write_cache(
        "python",
        package_name,
        {
            "ok": True,
            "latest": latest,
            "content": content[:4000],
            "source": "pypi_json_metadata",
            "metadata": {"release_count": len(releases)},
        },
    )
    return result_payload


def _pypi_not_found_result(package_name: str, package: dict[str, Any], profile: Any, json_result: JsonResultFactory) -> dict[str, Any]:
    write_cache(
        "python",
        package_name,
        {
            "ok": False,
            "latest": "",
            "content": f"pypi_package_not_found:{package_name}",
            "source": "pypi_json_metadata_404",
            "metadata": {
                "http_status": 404,
                "error_class": "package_not_found",
                "reason": f"pypi_package_not_found:{package_name}",
                "negative_ttl_seconds": 300,
            },
        },
    )
    return json_result(
        ok=False,
        status="failed",
        source="package_manager",
        result_kind="package_index_metadata",
        error_class="package_not_found",
        reason=f"pypi_package_not_found:{package_name}",
        metadata={
            "package": package_name,
            "package_ecosystem": "python",
            "http_status": 404,
            "network_route_mode": package.get("route_mode", ""),
            "network_target_kind": package.get("target_kind", ""),
            "command_kind": "pypi_json_metadata",
            "validation_profile": profile.name,
        },
        next_action="verify_package_name_or_use_codex_current_turn_owner_tool",
    )


def _pip_index_result(
    *,
    package_name: str,
    package: dict[str, Any],
    profile: Any,
    timeout: int,
    json_result: JsonResultFactory,
) -> dict[str, Any]:
    env = apply_execution_env(os.environ, package)
    command = [sys.executable, "-m", "pip", "index", "versions", package_name, "--disable-pip-version-check"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=max(1, min(timeout, profile.max_owner_timeout_seconds)),
        )
    except Exception as exc:
        return json_result(ok=False, status="failed", error_class=type(exc).__name__, reason=str(exc))
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    ok = proc.returncode == 0 and bool(stdout)
    first_line = stdout.splitlines()[0] if stdout else ""
    version_match = re.search(r"\(([^()]+)\)", first_line)
    latest = version_match.group(1) if version_match else ""
    result_payload = json_result(
        ok=ok,
        status="completed" if ok else "failed",
        source="package_manager",
        result_kind="package_index_metadata",
        content=stdout[:4000],
        metadata={
            "package": package_name,
            "package_ecosystem": "python",
            "latest": latest,
            "returncode": proc.returncode,
            "stderr_tail": stderr[-1000:],
            "network_route_mode": package.get("route_mode", ""),
            "network_target_kind": package.get("target_kind", ""),
            "command_kind": "pip_index_versions",
            "validation_profile": profile.name,
        },
        error_class="" if ok else "package_index_failed",
        reason="" if ok else (stderr[-500:] or stdout[-500:] or f"returncode={proc.returncode}"),
        next_action="consume_resource" if ok else "use_codex_current_turn_owner_tool",
    )
    if ok:
        write_cache(
            "python",
            package_name,
            {
                "ok": True,
                "latest": latest,
                "content": stdout[:4000],
                "source": "pip_index_versions",
                "metadata": {"returncode": proc.returncode},
            },
        )
    return result_payload


def execute_package_metadata(request: dict[str, Any], gateway_plan: dict[str, Any], timeout: int, json_result: JsonResultFactory) -> dict[str, Any]:
    package_name = package_name_from_request(request)
    if not package_name:
        return json_result(ok=False, status="handoff_required", reason="package_name_missing")
    ecosystem = package_ecosystem_from_request(request)
    if ecosystem in WINDOWS_TOOL_ECOSYSTEMS:
        package = execution_package_from_gateway_plan(gateway_plan)
        if not package.get("ok"):
            return json_result(ok=False, status="handoff_required", reason="network_package_unavailable")
        return execute_windows_package_request(request, package_name, ecosystem, package, timeout, json_result)
    if ecosystem in {"npm", "node", "nodejs"}:
        package = execution_package_from_gateway_plan(gateway_plan)
        if not package.get("ok"):
            return json_result(ok=False, status="handoff_required", reason="network_package_unavailable")
        return execute_node_package_request(request, package, timeout, json_result)
    if ecosystem not in {"python", "pip", "pypi"}:
        return json_result(
            ok=False,
            status="handoff_required",
            error_class="package_ecosystem_not_supported_for_auto_owner",
            reason=f"package_ecosystem={ecosystem} requires owning package manager adapter",
            metadata={"package": package_name, "package_ecosystem": ecosystem},
            next_action="use_codex_current_turn_owner_tool",
        )
    if is_python_install_request(request):
        package = execution_package_from_gateway_plan(gateway_plan)
        if not package.get("ok"):
            return json_result(ok=False, status="handoff_required", reason="network_package_unavailable")
        return execute_python_package_install(request, package_name, package, timeout, json_result)
    profile = metadata_profile(request.get("metadata") if isinstance(request.get("metadata"), dict) else {})
    cached = read_cache("python", package_name, ttl_seconds=profile.package_metadata_cache_ttl_seconds)
    if cached:
        return _cached_result(package_name, cached, profile, json_result)
    if not profile.live_package_index:
        return _fixture_result(package_name, profile, json_result)
    package = execution_package_from_gateway_plan(gateway_plan)
    if not package.get("ok"):
        return json_result(ok=False, status="handoff_required", reason="network_package_unavailable")
    try:
        pypi_payload = _open_pypi_json(package_name, package, min(timeout, profile.max_owner_timeout_seconds))
        return _pypi_success_result(
            package_name=package_name,
            package=package,
            profile=profile,
            pypi_payload=pypi_payload,
            json_result=json_result,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _pypi_not_found_result(package_name, package, profile, json_result)
    except Exception:
        pass
    return _pip_index_result(
        package_name=package_name,
        package=package,
        profile=profile,
        timeout=timeout,
        json_result=json_result,
    )


def validate() -> dict[str, Any]:
    gateway_plan = {"ok": True, "plan": {"route_mode": "probe_selected_direct", "target_kind": "package", "env": {}, "unset_env": []}}
    npm_probe = execute_package_metadata(
        {"target": "left-pad", "metadata": {"package_ecosystem": "npm", "package_action": "install"}},
        gateway_plan,
        1,
        lambda **payload: payload,
    )
    windows_probe = execute_package_metadata(
        {
            "target": "aria2",
            "metadata": {
                "package_ecosystem": "windows_tool",
                "validation_profile": "quick",
                "windows_package_manager": "choco",
            },
        },
        gateway_plan,
        1,
        lambda **payload: payload,
    )
    windows_validation = validate_windows_package_manager()
    python_installer_validation = validate_python_package_installer()
    node_package_validation = validate_node_package_owner()
    return {
        "schema": "resource_package_owner.validate.v1",
        "ok": bool(
            npm_probe.get("status") == "handoff_required"
            and npm_probe.get("error_class") == "install_requires_explicit_approval"
            and windows_probe.get("status") == "completed"
            and windows_probe.get("result_kind") == "windows_package_manager_plan"
            and windows_validation.get("ok")
            and python_installer_validation.get("ok")
            and node_package_validation.get("ok")
        ),
        "npm_owner_ok": npm_probe.get("error_class") == "install_requires_explicit_approval",
        "windows_package_manager_ok": windows_validation.get("ok"),
        "python_package_installer_ok": python_installer_validation.get("ok"),
        "node_package_owner_ok": node_package_validation.get("ok"),
        "windows_quick_plan_ok": windows_probe.get("status") == "completed",
        "writes_files_by_default": False,
        "writes_remote_state": False,
    }
