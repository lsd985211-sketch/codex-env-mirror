#!/usr/bin/env python3
"""DDGS-backed generic search tools for the Local MCP Hub.

Ownership: execute bounded read-only text, image, news, video, book, and URL
extraction requests through the project-managed DDGS runtime dependency.
Non-goals: resource routing, downloading files, installing dependencies,
changing global proxy state, bypassing access controls, or accepting writes.
State behavior: read-only open-world network access; no persistent state writes.
Caller context: LocalMcpHub dispatch and resource_owner_executor generic_search.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


BRIDGE_ROOT = Path(__file__).resolve().parent
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

import managed_python_dependency_runtime as managed_python_runtime  # noqa: E402


DEPENDENCY_BASE_ROOT = BRIDGE_ROOT / "runtime_dependencies"
SEARCH_BACKENDS = {
    "text": ("duckduckgo", "bing", "brave"),
    "images": ("duckduckgo", "bing"),
    "news": ("duckduckgo", "bing"),
    "videos": ("duckduckgo",),
    "books": ("auto",),
}
SEARCH_TOOL_NAMES = {
    "resource_search.text",
    "resource_search.images",
    "resource_search.news",
    "resource_search.videos",
    "resource_search.books",
    "resource_search.extract",
    "resource_search.health",
    "resource_search.validate",
}
DEFAULT_SEARCH_TIMEOUT_SECONDS = 10
MAX_SEARCH_TIMEOUT_SECONDS = 60
WORKER_EXIT_GRACE_SECONDS = 3


def _dependency_recovery_fields() -> dict[str, Any]:
    return {
        "execution_owner": "resource_package_owner",
        "boundary": "install into the ABI/platform-scoped target for the reported runtime key",
    }


def _total_timeout_seconds(arguments: dict[str, Any]) -> int:
    try:
        requested = int(arguments.get("timeout_seconds") or DEFAULT_SEARCH_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        requested = DEFAULT_SEARCH_TIMEOUT_SECONDS
    return max(1, min(requested, MAX_SEARCH_TIMEOUT_SECONDS))


def _backend_timeout_seconds(total_seconds: int, backend_count: int) -> int:
    usable_seconds = max(1, total_seconds - 2)
    return max(1, min(usable_seconds // max(1, backend_count), 30))


def _runtime_identity() -> dict[str, str]:
    return managed_python_runtime.runtime_identity()


def _dependency_selection() -> dict[str, Any]:
    return managed_python_runtime.select_dependency_target(
        DEPENDENCY_BASE_ROOT,
        "ddgs",
        python_executable=sys.executable,
    )


def _dependency_state() -> dict[str, Any]:
    selection = _dependency_selection()
    dependency_root = Path(str(selection.get("path") or ""))
    selection_fields = {
        "dependency_root": str(dependency_root),
        "runtime": selection.get("runtime") or _runtime_identity(),
        "runtime_key": str(selection.get("runtime_key") or managed_python_runtime.runtime_key()),
        "selected_kind": str(selection.get("selected_kind") or "runtime_scoped"),
        "selection_reason": str(selection.get("reason") or ""),
        "import_probe": selection.get("import_probe") or {},
        "legacy_path": str(selection.get("legacy_path") or ""),
        "legacy_probe": selection.get("legacy_probe") or {},
    }
    if not selection.get("ok"):
        return {
            "ok": False,
            "reason": str(selection.get("reason") or "runtime_scoped_dependency_missing"),
            "required_package": "ddgs==9.14.4",
            "required_imports": list(managed_python_runtime.required_imports("ddgs")),
            "next_action": str(selection.get("next_action") or "install_package_through_resource_owner_for_the_reported_runtime_key"),
            **selection_fields,
            **_dependency_recovery_fields(),
        }
    dependency_path = str(dependency_root)
    package_init = dependency_root / "ddgs" / "__init__.py"
    if not package_init.is_file():
        return {
            "ok": False,
            "reason": "ddgs_package_unreadable_or_incomplete",
            "error": "managed dependency root exists but ddgs/__init__.py is not readable by the worker account",
            "dependency_root": dependency_path,
            "package_init": str(package_init),
            "package_init_readable": False,
            "required_package": "ddgs==9.14.4",
            "next_action": "repair managed dependency ACL inheritance or reinstall through the resource package owner",
            **selection_fields,
            **_dependency_recovery_fields(),
        }
    normalized_dependency = os.path.normcase(os.path.normpath(dependency_path))
    sys.path[:] = [
        entry
        for entry in sys.path
        if os.path.normcase(os.path.normpath(str(entry or os.curdir))) != normalized_dependency
    ]
    sys.path.insert(0, dependency_path)
    try:
        from ddgs import DDGS  # type: ignore
        from ddgs.engines import ENGINES  # type: ignore
    except Exception as exc:
        module = sys.modules.get("ddgs")
        return {
            "ok": False,
            "reason": "ddgs_import_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "dependency_root": dependency_path,
            "package_init": str(package_init),
            "package_init_readable": True,
            "imported_module_origin": str(getattr(module, "__file__", "") or ""),
            "required_package": "ddgs==9.14.4",
            **selection_fields,
        }
    try:
        from lxml import etree  # type: ignore

        _ = etree.XML
    except Exception as exc:
        return {
            "ok": False,
            "reason": "ddgs_runtime_dependency_incompatible",
            "error_class": "managed_dependency_abi_mismatch",
            "error": f"{type(exc).__name__}: {exc}",
            "dependency_root": dependency_path,
            "package_init": str(package_init),
            "package_init_readable": True,
            "required_package": "ddgs==9.14.4",
            **selection_fields,
            "next_action": "reinstall_the_complete_ddgs_dependency_tree_with_the_current_platform_python",
        }
    return {
        "ok": True,
        "dependency_root": dependency_path,
        "required_package": "ddgs==9.14.4",
        **selection_fields,
        "factory": DDGS,
        "runtime_available_backends": {
            category: sorted(str(name) for name in engines)
            for category, engines in ENGINES.items()
        },
    }


def resource_search_tool_specs() -> list[dict[str, Any]]:
    common_properties = {
        "query": {"type": "string", "description": "Search query."},
        "region": {"type": "string", "default": "us-en"},
        "safesearch": {"type": "string", "enum": ["on", "moderate", "off"], "default": "moderate"},
        "timelimit": {"type": "string", "description": "Optional d, w, m, or y time limit."},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
        "backend": {"type": "string", "default": "auto"},
        "site_or_domain": {"type": "string", "description": "Optional domain constraint appended as site:domain."},
        "proxy_url": {"type": "string", "description": "Per-request proxy URL supplied by the network gateway."},
        "route_mode": {"type": "string", "description": "Network route mode; direct routes ignore proxy_url."},
        "timeout_seconds": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_SEARCH_TIMEOUT_SECONDS,
            "default": DEFAULT_SEARCH_TIMEOUT_SECONDS,
        },
    }
    annotations = {
        "title": "Resource Search",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
    specs: list[dict[str, Any]] = []
    for kind in ("text", "images", "news", "videos", "books"):
        specs.append(
            {
                "name": f"resource_search.{kind}",
                "description": f"Run bounded {kind} metasearch through the project-managed DDGS backend.",
                "annotations": annotations,
                "inputSchema": {
                    "type": "object",
                    "properties": common_properties,
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }
        )
    specs.extend(
        [
            {
                "name": "resource_search.extract",
                "description": "Extract bounded content from one explicit URL through DDGS.",
                "annotations": annotations,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "format": {"type": "string", "enum": ["text_markdown", "text_plain", "text_rich", "text"], "default": "text_markdown"},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "resource_search.health",
                "description": "Check the project-managed DDGS dependency without network access.",
                "annotations": {**annotations, "openWorldHint": False},
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "resource_search.validate",
                "description": "Validate the Hub resource-search tool contract without a live search.",
                "annotations": {**annotations, "openWorldHint": False},
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ]
    )
    return specs


def _proxy_for(arguments: dict[str, Any]) -> str | None:
    route_mode = str(arguments.get("route_mode") or "").strip()
    if route_mode in {"direct", "probe_selected_direct"}:
        return None
    proxy_url = str(arguments.get("proxy_url") or "").strip()
    return proxy_url or None


def _bounded_results(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value[:limit] if isinstance(item, dict)]


def _runtime_fingerprint(dependency_root: Path | None = None) -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes())
    if dependency_root is not None:
        package_init = dependency_root / "ddgs" / "__init__.py"
        if package_init.is_file():
            digest.update(package_init.read_bytes())
    return digest.hexdigest()[:16]


def _resource_search_call_in_process(name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    if name not in SEARCH_TOOL_NAMES:
        return None
    state = _dependency_state()
    base = {
        "schema": "local_mcp_hub.resource_search.v1",
        "tool": name,
        "backend": "ddgs",
        "backend_version": "9.14.4",
        "worker_runtime_fingerprint": _runtime_fingerprint(
            Path(str(state["dependency_root"])) if state.get("dependency_root") else None
        ),
        "fresh_worker_boundary": True,
        "permission_boundary": "read_only_open_world_search",
        "writes_files": False,
        "writes_remote_state": False,
    }
    if name in {"resource_search.health", "resource_search.validate"}:
        return {
            **base,
            **{key: value for key, value in state.items() if key != "factory"},
            "registered_tools": sorted(SEARCH_TOOL_NAMES),
        }
    if not state.get("ok"):
        return {**base, **{key: value for key, value in state.items() if key != "factory"}, "status": "backend_unavailable"}
    factory = state["factory"]
    total_timeout_seconds = _total_timeout_seconds(arguments)
    try:
        if name == "resource_search.extract":
            client = factory(
                proxy=_proxy_for(arguments),
                timeout=_backend_timeout_seconds(total_timeout_seconds, 1),
            )
            url = str(arguments.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                return {**base, "ok": False, "status": "invalid_request", "reason": "http_or_https_url_required"}
            result = client.extract(url, fmt=str(arguments.get("format") or "text_markdown"))
            return {**base, "ok": True, "status": "completed", "url": url, "result": result}
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {**base, "ok": False, "status": "invalid_request", "reason": "query_required"}
        domain = str(arguments.get("site_or_domain") or "").strip().lower()
        if domain:
            domain = domain.removeprefix("https://").removeprefix("http://").split("/", 1)[0]
            if domain:
                query = f"{query} site:{domain}"
        kind = name.split(".", 1)[1]
        max_results = max(1, min(int(arguments.get("max_results") or 8), 20))
        requested_backend = str(arguments.get("backend") or "auto").strip().lower()
        configured_backends = SEARCH_BACKENDS[kind]
        runtime_available = set(
            str(item)
            for item in (state.get("runtime_available_backends") or {}).get(kind, configured_backends)
        )
        allowed_backends = tuple(
            backend for backend in configured_backends if backend == "auto" or backend in runtime_available
        )
        if not allowed_backends:
            return {
                **base,
                "ok": False,
                "status": "backend_unavailable",
                "error_class": "no_configured_search_backend_available",
                "reason": f"no configured {kind} backend is enabled by the managed DDGS runtime",
                "query": query,
                "configured_backends": list(configured_backends),
                "runtime_available_backends": sorted(runtime_available),
            }
        if requested_backend != "auto" and requested_backend not in allowed_backends:
            return {
                **base,
                "ok": False,
                "status": "invalid_request",
                "error_class": "unsupported_search_backend",
                "reason": f"backend={requested_backend} is not supported for {kind}",
                "query": query,
                "allowed_backends": list(allowed_backends),
                "runtime_available_backends": sorted(runtime_available),
            }
        backend_attempts = list(allowed_backends) if requested_backend == "auto" else [requested_backend]
        client = factory(
            proxy=_proxy_for(arguments),
            timeout=_backend_timeout_seconds(total_timeout_seconds, len(backend_attempts)),
        )
        kwargs: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
        }
        if kind not in {"books"}:
            region = str(arguments.get("region") or "us-en").strip().lower()
            kwargs["region"] = "us-en" if region == "wt-wt" else region
        if kind not in {"books"}:
            kwargs["safesearch"] = str(arguments.get("safesearch") or "moderate")
        timelimit = str(arguments.get("timelimit") or "").strip()
        if timelimit and kind in {"text", "images", "news", "videos"}:
            kwargs["timelimit"] = timelimit
        method = getattr(client, kind)
        backend_errors: list[dict[str, str]] = []
        for backend in backend_attempts:
            try:
                results = _bounded_results(method(**{**kwargs, "backend": backend}), max_results)
            except Exception as exc:
                backend_errors.append({"backend": backend, "error_class": type(exc).__name__, "reason": str(exc)[:500]})
                continue
            if results:
                return {
                    **base,
                    "ok": True,
                    "status": "completed",
                    "query": query,
                    "result_kind": kind,
                    "result_count": len(results),
                    "results": results,
                    "selected_backend": backend,
                    "attempted_backends": [*backend_errors, {"backend": backend, "status": "completed"}],
                    "route_mode": str(arguments.get("route_mode") or ""),
                    "proxy_used": bool(_proxy_for(arguments)),
                }
            backend_errors.append({"backend": backend, "error_class": "no_results", "reason": "no results"})
        return {
            **base,
            "ok": False,
            "status": "no_results",
            "error_class": "search_backends_exhausted",
            "reason": "all configured search backends returned no usable results",
            "query": query,
            "result_kind": kind,
            "result_count": 0,
            "results": [],
            "attempted_backends": backend_errors,
            "route_mode": str(arguments.get("route_mode") or ""),
            "proxy_used": bool(_proxy_for(arguments)),
        }
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "status": "failed",
            "reason": f"{type(exc).__name__}: {exc}",
            "error_class": type(exc).__name__,
            "query": str(arguments.get("query") or ""),
        }


def resource_search_call(name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    if name not in SEARCH_TOOL_NAMES:
        return None
    # Every operation, including health and validate, crosses a fresh process
    # boundary so a long-lived Hub cannot retain stale dependency imports.
    timeout_seconds = _total_timeout_seconds(arguments)
    command = [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"]
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            command,
            input=json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds + WORKER_EXIT_GRACE_SECONDS,
            creationflags=creationflags,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "schema": "local_mcp_hub.resource_search.v1",
            "tool": name,
            "backend": "ddgs",
            "backend_version": "9.14.4",
            "ok": False,
            "status": "failed",
            "error_class": "search_total_timeout",
            "reason": f"search exceeded total timeout budget of {timeout_seconds} seconds",
            "writes_files": False,
            "writes_remote_state": False,
        }
    if proc.returncode != 0:
        return {
            "schema": "local_mcp_hub.resource_search.v1",
            "tool": name,
            "backend": "ddgs",
            "backend_version": "9.14.4",
            "ok": False,
            "status": "failed",
            "error_class": "search_worker_failed",
            "reason": (proc.stderr or proc.stdout or f"returncode={proc.returncode}")[-1000:],
            "writes_files": False,
            "writes_remote_state": False,
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "schema": "local_mcp_hub.resource_search.v1",
            "tool": name,
            "backend": "ddgs",
            "backend_version": "9.14.4",
            "ok": False,
            "status": "failed",
            "error_class": "search_worker_invalid_json",
            "reason": str(exc),
            "writes_files": False,
            "writes_remote_state": False,
        }
    return payload if isinstance(payload, dict) else {"ok": False, "status": "failed", "reason": "search_worker_non_object"}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "--worker":
        return 2
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        name = str(payload.get("name") or "")
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        result = _resource_search_call_in_process(name, arguments)
        print(json.dumps(result or {"ok": False, "reason": "unknown_search_tool"}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "failed", "error_class": type(exc).__name__, "reason": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
