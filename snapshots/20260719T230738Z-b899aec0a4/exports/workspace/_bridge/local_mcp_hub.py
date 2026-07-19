#!/usr/bin/env python3
"""Local HTTP MCP hub for stable core tools.

This hub exposes stable local read/prepare tools directly, a guarded Hub-first
read-only owner MCP adapter, and a narrow diagnostic gateway fallback. Session-
bound tools remain native-current-session first. Every route preserves the
original tool's permission boundary.

The hub binds to 127.0.0.1 by default and provides maintenance endpoints so
Codex can diagnose transport health without depending on stdio MCP pipes.
The MCP endpoint is deliberately stateless: every request is self-contained and
the server does not issue MCP-Session-Id.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "_bridge"
RUNTIME_DIR = BRIDGE_ROOT / "runtime" / "local_mcp_hub"
STATE_PATH = RUNTIME_DIR / "state.json"
RECORD_STORE_INDEX_PATH = Path.home() / "Desktop" / "Codex资源库" / "文档" / "系统维护" / "索引" / "record_store.sqlite"
EMAIL_STATE_INDEX_PATH = BRIDGE_ROOT / "shared" / "email_scheduler_state" / "email_state.sqlite"
PMB_HOME = Path.home() / "Desktop" / "Codex资源库" / "memory" / "pmb" / "data"
PMB_TOKEN_PATH = PMB_HOME / "daemon.token"
PMB_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18881
MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "local-mcp-hub"
SERVER_VERSION = "0.1.0"


def first_existing_path(*candidates: Path | str | None) -> Path:
    fallback: Path | None = None
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if fallback is None:
            fallback = path
        if path.exists():
            return path
    return fallback or Path("")


WINDOWS_NATIVE_BRIDGE_ROOT = Path("/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager/_bridge")
SCRATCH_DB_PATH = first_existing_path(
    os.environ.get("CODEX_SCRATCH_SQLITE"),
    Path.home() / ".codex-app" / "sqlite" / "codex_scratch.sqlite",
    BRIDGE_ROOT / "data" / "sqlite" / "codex_scratch.sqlite",
    WINDOWS_NATIVE_BRIDGE_ROOT / "data" / "sqlite" / "codex_scratch.sqlite",
)
BRIDGE_DB_PATH = first_existing_path(
    os.environ.get("MOBILE_OPENCLAW_BRIDGE_DB"),
    BRIDGE_ROOT / "mobile_openclaw_bridge" / "mobile_openclaw_bridge.db",
    WINDOWS_NATIVE_BRIDGE_ROOT / "mobile_openclaw_bridge" / "mobile_openclaw_bridge.db",
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(BRIDGE_ROOT))

from custom_slash_commands_mcp import DEFAULT_REGISTRY, SlashCommandService  # noqa: E402
try:
    from desktop_weixin_mcp_server import TOOL_REGISTRY as DESKTOP_WEIXIN_TOOLS  # noqa: E402
    from desktop_weixin_mcp_server import DesktopWeixinService  # noqa: E402
    DESKTOP_WEIXIN_IMPORT_ERROR = ""
except ImportError as exc:  # WSL/non-GUI hosts should not break stateless Hub validation.
    DESKTOP_WEIXIN_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    DESKTOP_WEIXIN_TOOLS = {
        "desktop_weixin.capabilities": None,
        "desktop_weixin.status": None,
    }

    class DesktopWeixinService:  # type: ignore[no-redef]
        def tool_specs(self) -> list[dict[str, Any]]:
            return [
                {
                    "name": "desktop_weixin.capabilities",
                    "description": "Report desktop Weixin platform availability.",
                    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                },
                {
                    "name": "desktop_weixin.status",
                    "description": "Report desktop Weixin platform availability.",
                    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            ]

        def tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
            name = str(params.get("name") or "")
            payload = {
                "ok": False,
                "code": "desktop_weixin_platform_deferred",
                "tool": name,
                "reason": DESKTOP_WEIXIN_IMPORT_ERROR,
                "activation_rule": "Use the Windows-native desktop-weixin owner path for live Weixin GUI tasks.",
            }
            if name == "desktop_weixin.capabilities":
                payload["capabilities"] = {"available": False, "platform_deferred": True}
            return text_result(payload, is_error=True)
from local_mcp_hub_specs import (  # noqa: E402
    agent_bridge_tool_specs,
    chrome_devtools_tool_specs,
    codegraph_tool_specs,
    gateway_tool_specs,
    github_app_tool_specs,
    github_tool_specs,
    maintenance_tool_specs,
    metamcp_lab_tool_specs,
    network_tool_specs,
    pmb_tool_specs,
    resource_tool_specs,
    secret_vault_tool_specs,
    workflow_tool_specs,
)
from local_mcp_hub_process import hub_runtime_state, reload_local_hub, windows_interop_command  # noqa: E402
from local_mcp_hub_pmb_runtime import PmbRecoverySingleFlight  # noqa: E402
import local_mcp_hub_catalog as hub_catalog  # noqa: E402
import local_mcp_hub_metamcp_lab as metamcp_lab  # noqa: E402
import local_mcp_hub_routes as hub_routes  # noqa: E402
from local_mcp_hub_owner_mcp import call as owner_mcp_call  # noqa: E402
from local_mcp_hub_owner_mcp import tool_specs as owner_mcp_tool_specs  # noqa: E402
from local_mcp_hub_owner_mcp import validate as owner_mcp_validate  # noqa: E402
from local_mcp_hub_network_gateway import network_gateway_call  # noqa: E402
from codegraph_query_runtime import ROOT as CODEGRAPH_PROJECT_ROOT, query_codegraph  # noqa: E402
from local_mcp_hub_mobile_bridge import mobile_bridge_call, mobile_bridge_tool_specs  # noqa: E402
from local_mcp_hub_resource_search import resource_search_call, resource_search_tool_specs  # noqa: E402
from resource_broker import DEFAULT_RECEIPT_LOG, ResourceBrokerRequest, attach_result_to_request, handle_request, read_receipt  # noqa: E402
from resource_progress_view import progress_view as resource_progress_view  # noqa: E402
from resource_scheduler import batch_config_from_payload, execute_batch, requests_from_payload  # noqa: E402
from github_app_auth import doctor as github_app_doctor  # noqa: E402
from github_app_auth import snapshot as github_app_snapshot  # noqa: E402
from github_app_auth import validate as github_app_validate  # noqa: E402
from github_hub_client import github_api as github_hub_api  # noqa: E402
from secret_vault import doctor as secret_vault_doctor  # noqa: E402
from secret_vault import snapshot as secret_vault_snapshot  # noqa: E402
from secret_vault import validate as secret_vault_validate  # noqa: E402
from shared.json_cli import now_iso  # noqa: E402
from sqlite_mcp_server import SqliteMcpService  # noqa: E402
from mcp_session_doctor import gateway_call as owner_gateway_call  # noqa: E402


PMB_RECOVERY_SINGLEFLIGHT = PmbRecoverySingleFlight()

CHROME_DEVTOOLS_ALIAS_TO_TOOL = {
    "chrome_devtools.list_pages": "list_pages",
    "chrome_devtools.new_page": "new_page",
    "chrome_devtools.select_page": "select_page",
    "chrome_devtools.navigate_page": "navigate_page",
    "chrome_devtools.take_snapshot": "take_snapshot",
    "chrome_devtools.evaluate_script": "evaluate_script",
    "chrome_devtools.take_screenshot": "take_screenshot",
    "chrome_devtools.list_console_messages": "list_console_messages",
    "chrome_devtools.list_network_requests": "list_network_requests",
    "chrome_devtools.wait_for": "wait_for",
    "chrome_devtools.resize_page": "resize_page",
    "chrome_devtools.close_page": "close_page",
}
CHROME_DEVTOOLS_WRAPPER_KEYS = {"fallback_ack", "timeout_seconds"}
SQLITE_READ_TOOLS = {"sqlite_health", "sqlite_tables", "sqlite_schema", "sqlite_query"}
SQLITE_ALIAS_ROUTES = {
    "sqlite_scratch_health": ("sqlite_scratch.", "sqlite_health"),
    "sqlite_scratch_tables": ("sqlite_scratch.", "sqlite_tables"),
    "sqlite_scratch_schema": ("sqlite_scratch.", "sqlite_schema"),
    "sqlite_scratch_query": ("sqlite_scratch.", "sqlite_query"),
    "sqlite_bridge_health": ("sqlite_bridge.", "sqlite_health"),
    "sqlite_bridge_tables": ("sqlite_bridge.", "sqlite_tables"),
    "sqlite_bridge_schema": ("sqlite_bridge.", "sqlite_schema"),
    "sqlite_bridge_query": ("sqlite_bridge.", "sqlite_query"),
    "record_store_health": ("record_store.", "sqlite_health"),
    "record_store_tables": ("record_store.", "sqlite_tables"),
    "record_store_schema": ("record_store.", "sqlite_schema"),
    "record_store_query": ("record_store.", "sqlite_query"),
    "email_state_health": ("email_state.", "sqlite_health"),
    "email_state_tables": ("email_state.", "sqlite_tables"),
    "email_state_schema": ("email_state.", "sqlite_schema"),
    "email_state_query": ("email_state.", "sqlite_query"),
}


def hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def windows_console_encoding() -> str:
    return "mbcs" if os.name == "nt" else "utf-8"


def console_python_executable() -> str:
    executable = Path(sys.executable)
    if os.name == "nt" and executable.name.lower() == "pythonw.exe":
        console_executable = executable.with_name("python.exe")
        if console_executable.exists():
            return str(console_executable)
    return str(executable)


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return payload if isinstance(payload, dict) else default


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def text_result(payload: dict[str, Any], *, is_error: bool | None = None) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": (not bool(payload.get("ok", True))) if is_error is None else bool(is_error),
    }


def run_json_command(cmd: list[str], timeout: int = 20) -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=hidden_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "reason": "timeout", "stdout": exc.stdout or "", "stderr": exc.stderr or ""}
    except FileNotFoundError as exc:
        return {"ok": False, "reason": "command_not_found", "error": str(exc)}
    text = (proc.stdout or "").strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {"ok": proc.returncode == 0, "stdout": text[:8000]}
    if isinstance(payload, dict):
        payload.setdefault("ok", proc.returncode == 0)
        payload.setdefault("returncode", proc.returncode)
        if proc.stderr:
            payload.setdefault("stderr", proc.stderr[:2000])
        return payload
    return {"ok": proc.returncode == 0, "result": payload, "returncode": proc.returncode}


def run_text_command(cmd: list[str], timeout: int = 45, input_text: str = "") -> dict[str, Any]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            input=input_text if input_text else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=hidden_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "reason": "timeout", "stdout": (exc.stdout or "")[:20000], "stderr": (exc.stderr or "")[:4000]}
    except FileNotFoundError as exc:
        return {"ok": False, "reason": "command_not_found", "error": str(exc)}
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    parsed: Any = None
    stripped = stdout.strip()
    if stripped and stripped[:1] in "[{":
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": stdout[:20000],
        "stderr": stderr[:4000],
        "json": parsed,
    }


def gateway_command(args: list[str], timeout: int = 45) -> dict[str, Any]:
    return run_json_command(["python", "_bridge\\mcp_session_doctor.py", *args], timeout=timeout)


def parse_sse_json(text: str) -> dict[str, Any]:
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line.split(":", 1)[1].strip()
        if not data:
            continue
        payload = json.loads(data)
        if isinstance(payload, dict):
            return payload
    raise ValueError("sse_data_json_missing")


def pmb_token() -> str:
    return PMB_TOKEN_PATH.read_text(encoding="utf-8").strip()


def pmb_http_request(payload: dict[str, Any], *, session_id: str = "", timeout: float = 8.0) -> tuple[dict[str, Any], str]:
    import urllib.error
    import urllib.request

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Authorization": f"Bearer {pmb_token()}",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(PMB_URL, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            next_session = response.headers.get("Mcp-Session-Id", session_id)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"pmb_http_{exc.code}: {body[:500]}") from exc
    if body.lstrip().startswith("event:"):
        return parse_sse_json(body), next_session
    parsed = json.loads(body) if body.strip() else {}
    return parsed if isinstance(parsed, dict) else {"result": parsed}, next_session


def pmb_daemon_ensure() -> dict[str, Any]:
    return run_json_command(
        [console_python_executable(), str(BRIDGE_ROOT / "local_pmb_memory.py"), "daemon-ensure"],
        timeout=45,
    )


def _pmb_tool_call_once(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not PMB_TOKEN_PATH.exists():
        return {
            "ok": False,
            "reason": "pmb_daemon_token_missing",
            "path": str(PMB_TOKEN_PATH),
            "transport_error": True,
        }
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        },
    }
    try:
        _, session_id = pmb_http_request(init_payload, timeout=8.0)
        pmb_http_request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, session_id=session_id, timeout=4.0)
        response, _ = pmb_http_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
            session_id=session_id,
            timeout=15.0,
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "tool": tool,
            "transport_error": True,
        }
    result = response.get("result") if isinstance(response, dict) else {}
    if not isinstance(result, dict):
        return {"ok": False, "reason": "pmb_result_not_object", "tool": tool}
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        payload = dict(structured)
    else:
        payload = {"content": result.get("content", [])}
    payload.setdefault("ok", not bool(result.get("isError")))
    payload.setdefault("tool", f"pmb.{tool}")
    return payload


def pmb_tool_call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    recovery: dict[str, Any] | None = None
    attempt_started_at = time.monotonic()
    if not PMB_TOKEN_PATH.exists():
        recovery = PMB_RECOVERY_SINGLEFLIGHT.recover(
            pmb_daemon_ensure,
            failure_observed_at=attempt_started_at,
        )
    payload = _pmb_tool_call_once(tool, arguments)
    if payload.get("transport_error") and recovery is None:
        recovery = PMB_RECOVERY_SINGLEFLIGHT.recover(
            pmb_daemon_ensure,
            failure_observed_at=attempt_started_at,
        )
        if recovery.get("ok"):
            payload = _pmb_tool_call_once(tool, arguments)
    elif payload.get("transport_error") and recovery and recovery.get("ok"):
        payload = _pmb_tool_call_once(tool, arguments)
    if recovery is not None:
        singleflight = recovery.get("_singleflight") if isinstance(recovery.get("_singleflight"), dict) else {}
        payload["daemon_recovery"] = {
            "attempted": True,
            "ok": bool(recovery.get("ok")),
            "reason": recovery.get("reason") or recovery.get("error") or "",
            "singleflight_role": singleflight.get("role") or "",
            "coalesced": bool(singleflight.get("coalesced")),
            "policy": "idle daemon may exit; Hub restarts it once on the next PMB call",
        }
    payload.pop("transport_error", None)
    return payload


class LocalMcpHub:
    def __init__(self) -> None:
        self.started_at = now_iso()
        self.request_count = 0
        self.tool_call_count = 0
        self.lock = threading.Lock()
        self.slash = SlashCommandService(DEFAULT_REGISTRY)
        self.desktop_weixin = DesktopWeixinService()
        self.sqlite_scratch = SqliteMcpService(
            SCRATCH_DB_PATH,
            permissions={"list", "read"},
            readonly=True,
        )
        self.sqlite_bridge = SqliteMcpService(
            BRIDGE_DB_PATH,
            permissions={"list", "read"},
            readonly=True,
        )
        self.sqlite_record_store = SqliteMcpService(
            RECORD_STORE_INDEX_PATH,
            permissions={"list", "read"},
            readonly=True,
        )
        self.sqlite_email_state = SqliteMcpService(
            EMAIL_STATE_INDEX_PATH,
            permissions={"list", "read"},
            readonly=True,
        )

    def note_request(self, *, tool_call: bool = False) -> None:
        with self.lock:
            self.request_count += 1
            if tool_call:
                self.tool_call_count += 1
            state = {
                "schema": "local_mcp_hub.state.v1",
                "updated_at": now_iso(),
                "started_at": self.started_at,
                "request_count": self.request_count,
                "tool_call_count": self.tool_call_count,
            }
        write_json(STATE_PATH, state)

    def instructions(self) -> str:
        return (
            "Local HTTP MCP hub for stable core workspace tools. Stateless and "
            "owner-service capabilities are Hub-first; current browser, GUI, and "
            "mobile-thread capabilities remain native-current-session first. "
            "Known Hub tools are called directly, owner_mcp.call_readonly covers "
            "explicit read-only profiles, and complete_route is diagnostic or "
            "dynamic only. No route grants new permissions."
        )

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": self.instructions(),
        }

    def tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        all_tools = self.all_tool_specs()
        visible_tools, _hidden_tools = hub_catalog.split_specs(all_tools)
        return {"tools": visible_tools}

    def all_tool_specs(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        tools.extend(hub_catalog.tool_specs())
        for tool in self.slash.tool_specs():
            tools.append(tool)
        for tool in self.sqlite_scratch.tool_specs():
            name = str(tool.get("name") or "")
            if name in {"sqlite_health", "sqlite_tables", "sqlite_schema", "sqlite_query"}:
                scoped = dict(tool)
                scoped["name"] = f"sqlite_scratch.{name}"
                tools.append(scoped)
        for tool in self.sqlite_bridge.tool_specs():
            name = str(tool.get("name") or "")
            if name in {"sqlite_health", "sqlite_tables", "sqlite_schema", "sqlite_query"}:
                scoped = dict(tool)
                scoped["name"] = f"sqlite_bridge.{name}"
                tools.append(scoped)
        for tool in self.sqlite_record_store.tool_specs():
            name = str(tool.get("name") or "")
            if name in {"sqlite_health", "sqlite_tables", "sqlite_schema", "sqlite_query"}:
                scoped = dict(tool)
                scoped["name"] = f"record_store.{name}"
                tools.append(scoped)
        for tool in self.sqlite_email_state.tool_specs():
            name = str(tool.get("name") or "")
            if name in {"sqlite_health", "sqlite_tables", "sqlite_schema", "sqlite_query"}:
                scoped = dict(tool)
                scoped["name"] = f"email_state.{name}"
                tools.append(scoped)
        tools.extend(self.sqlite_alias_tool_specs())
        tools.extend(self.pmb_tool_specs())
        tools.extend(self.codegraph_tool_specs())
        tools.extend(self.chrome_devtools_tool_specs())
        tools.extend(self.desktop_weixin.tool_specs())
        tools.extend(self.github_tool_specs())
        tools.extend(self.github_app_tool_specs())
        tools.extend(self.secret_vault_tool_specs())
        tools.extend(self.resource_tool_specs())
        tools.extend(resource_search_tool_specs())
        tools.extend(self.mobile_bridge_tool_specs())
        tools.extend(self.workflow_tool_specs())
        tools.extend(self.network_tool_specs())
        tools.extend(self.agent_bridge_tool_specs())
        tools.extend(owner_mcp_tool_specs())
        tools.extend(self.gateway_tool_specs())
        tools.extend(self.metamcp_lab_tool_specs())
        tools.extend(self.maintenance_tool_specs())
        return tools

    def pmb_tool_specs(self) -> list[dict[str, Any]]:
        return pmb_tool_specs()

    def gateway_tool_specs(self) -> list[dict[str, Any]]:
        return gateway_tool_specs()

    def metamcp_lab_tool_specs(self) -> list[dict[str, Any]]:
        return metamcp_lab_tool_specs()

    def codegraph_tool_specs(self) -> list[dict[str, Any]]:
        return codegraph_tool_specs()

    def chrome_devtools_tool_specs(self) -> list[dict[str, Any]]:
        return chrome_devtools_tool_specs()

    def github_tool_specs(self) -> list[dict[str, Any]]:
        return github_tool_specs()

    def github_app_tool_specs(self) -> list[dict[str, Any]]:
        return github_app_tool_specs()

    def secret_vault_tool_specs(self) -> list[dict[str, Any]]:
        return secret_vault_tool_specs()

    def resource_tool_specs(self) -> list[dict[str, Any]]:
        return resource_tool_specs()

    def mobile_bridge_tool_specs(self) -> list[dict[str, Any]]:
        return mobile_bridge_tool_specs()

    def workflow_tool_specs(self) -> list[dict[str, Any]]:
        return workflow_tool_specs()

    def network_tool_specs(self) -> list[dict[str, Any]]:
        return network_tool_specs()

    def agent_bridge_tool_specs(self) -> list[dict[str, Any]]:
        return agent_bridge_tool_specs()

    def maintenance_tool_specs(self) -> list[dict[str, Any]]:
        return maintenance_tool_specs()

    def sqlite_alias_tool_specs(self) -> list[dict[str, Any]]:
        """Return Codex-discoverable Hub SQLite aliases without dotted names."""

        service_specs = {
            "sqlite_scratch.": self.sqlite_scratch.tool_specs(),
            "sqlite_bridge.": self.sqlite_bridge.tool_specs(),
            "record_store.": self.sqlite_record_store.tool_specs(),
            "email_state.": self.sqlite_email_state.tool_specs(),
        }
        descriptions = {
            "sqlite_scratch.": "Hub SQLite scratch database read-only query and inspection alias.",
            "sqlite_bridge.": "Hub SQLite bridge database read-only query and inspection alias.",
            "record_store.": "Hub record-store SQLite index read-only query and inspection alias.",
            "email_state.": "Hub email scheduler SQLite derived index read-only query and inspection alias.",
        }
        aliases: list[dict[str, Any]] = []
        for alias_name, (prefix, inner_name) in SQLITE_ALIAS_ROUTES.items():
            base = next((dict(tool) for tool in service_specs[prefix] if tool.get("name") == inner_name), None)
            if not base:
                continue
            base["name"] = alias_name
            base["description"] = f"{descriptions[prefix]} Maps to {prefix}{inner_name}."
            aliases.append(base)
        return aliases

    def sqlite_tools_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        if name in SQLITE_ALIAS_ROUTES:
            prefix, inner_name = SQLITE_ALIAS_ROUTES[name]
            name = f"{prefix}{inner_name}"
        sqlite_routes = {
            "sqlite_scratch.": (self.sqlite_scratch, "sqlite_write_tools_disabled_in_http_hub_pilot"),
            "sqlite_bridge.": (self.sqlite_bridge, "sqlite_bridge_is_readonly"),
            "record_store.": (self.sqlite_record_store, "record_store_is_readonly"),
            "email_state.": (self.sqlite_email_state, "email_state_is_readonly"),
        }
        for prefix, (service, write_error) in sqlite_routes.items():
            if not name.startswith(prefix):
                continue
            inner_name = name.split(".", 1)[1]
            if inner_name not in SQLITE_READ_TOOLS:
                return text_result({"ok": False, "reason": write_error, "tool": name}, is_error=True)
            return service.tools_call({"name": inner_name, "arguments": arguments})
        return None

    def tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        self.note_request(tool_call=True)
        if name.startswith("slash."):
            return self.slash.tools_call({"name": name, "arguments": arguments})
        sqlite_result = self.sqlite_tools_call(name, arguments)
        if sqlite_result is not None:
            return sqlite_result
        if name.startswith("pmb."):
            inner_name = name.split(".", 1)[1]
            if inner_name not in {"workspace_info", "prepare", "recall", "project_overview", "stats", "list_goals"}:
                return text_result({"ok": False, "reason": "pmb_write_or_admin_tool_not_exposed_in_hub", "tool": name}, is_error=True)
            return text_result(pmb_tool_call(inner_name, arguments))
        if name == "codegraph.explore":
            return text_result(self.codegraph_explore(arguments))
        if name.startswith("chrome_devtools."):
            return text_result(self.chrome_devtools_call(name, arguments))
        if name.startswith("desktop_weixin."):
            return self.desktop_weixin.tools_call({"name": name, "arguments": arguments})
        if name == "github.api":
            return text_result(self.github_api(arguments))
        if name == "github.gh":
            return text_result(self.github_gh(arguments))
        if name == "github_app.snapshot":
            return text_result(github_app_snapshot())
        if name == "github_app.doctor":
            return text_result(github_app_doctor())
        if name == "github_app.validate":
            return text_result(github_app_validate(online=bool(arguments.get("online"))))
        if name == "secret_vault.snapshot":
            return text_result(secret_vault_snapshot())
        if name == "secret_vault.doctor":
            return text_result(secret_vault_doctor())
        if name == "secret_vault.validate":
            return text_result(secret_vault_validate())
        if name == "resource.request":
            return text_result(self.resource_request(arguments))
        if name == "resource.request_batch":
            return text_result(self.resource_request_batch(arguments))
        if name == "resource.status":
            return text_result(self.resource_status(arguments))
        if name == "resource.progress":
            return text_result(self.resource_progress(arguments))
        if name == "resource.attach_result":
            return text_result(self.resource_attach_result(arguments))
        resource_search_result = resource_search_call(name, arguments)
        if resource_search_result is not None:
            return text_result(resource_search_result, is_error=resource_search_result.get("ok") is False)
        mobile_bridge_result = mobile_bridge_call(name, arguments, lambda command, timeout: run_json_command(command, timeout=timeout))
        if mobile_bridge_result is not None:
            return text_result(mobile_bridge_result)
        if name == "workflow.route_pack":
            return text_result(hub_routes.workflow_route_pack(arguments))
        network_gateway_result = network_gateway_call(name, arguments, lambda command, timeout: run_json_command(command, timeout=timeout))
        if network_gateway_result is not None:
            return text_result(network_gateway_result)
        network_doctor_result = hub_routes.network_doctor_call(
            name,
            arguments,
            lambda command, timeout: run_json_command(command, timeout=timeout),
        )
        if network_doctor_result is not None:
            return text_result(network_doctor_result, is_error=network_doctor_result.get("ok") is False)
        if name == "agent_bridge.status":
            return text_result(self.agent_bridge_status(arguments))
        if name == "owner_mcp.call_readonly":
            result = owner_mcp_call(arguments, owner_gateway_call)
            return text_result(result, is_error=result.get("ok") is False)
        if name == "mcp_gateway.route":
            profile = str(arguments.get("profile") or "").strip()
            tool = str(arguments.get("tool") or "").strip()
            return text_result(gateway_command(["gateway-route", "--profile", profile, "--tool", tool], timeout=20))
        if name == "mcp_gateway.call":
            return text_result(self.gateway_call(arguments))
        if name == "mcp_gateway.complete_route":
            return text_result(self.gateway_complete_route(arguments))
        if name.startswith("metamcp_lab."):
            return text_result(metamcp_lab.tools_call(name, arguments))
        if name == "hub.catalog":
            return text_result(hub_catalog.catalog(self.all_tool_specs(), arguments))
        if name == "hub.search":
            return text_result(hub_catalog.search(self.all_tool_specs(), arguments))
        if name == "hub.describe":
            return text_result(hub_catalog.describe(self.all_tool_specs(), arguments))
        if name == "hub.call":
            result = hub_catalog.call(self.all_tool_specs(), arguments, lambda tool, args: self.tools_call({"name": tool, "arguments": args}))
            return result if isinstance(result, dict) and "content" in result else text_result(result, is_error=not bool(result.get("ok")))
        if name == "hub.capabilities":
            return text_result(self.capabilities())
        if name == "hub.validate":
            return text_result(self.validate())
        if name == "hub.metrics":
            return text_result(self.metrics())
        mcp_session_result = hub_routes.mcp_session_doctor_call(
            name,
            arguments,
            lambda command, timeout: run_json_command(command, timeout=timeout),
        )
        if mcp_session_result is not None:
            return text_result(mcp_session_result, is_error=mcp_session_result.get("ok") is False)
        return text_result({"ok": False, "reason": "unknown_tool", "tool": name}, is_error=True)

    def codegraph_explore(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"ok": False, "reason": "query_required", "tool": "codegraph.explore"}
        payload = query_codegraph(
            query,
            project_path=str(arguments.get("projectPath") or CODEGRAPH_PROJECT_ROOT).strip(),
            max_files=int(arguments.get("maxFiles") or 4),
            timeout_seconds=int(arguments.get("timeout_seconds") or 60),
            freshness_targets=arguments.get("freshness_targets"),
            exclude_paths=arguments.get("exclude_paths"),
        )
        payload.setdefault("alias", "codegraph.explore")
        return payload

    def chrome_devtools_call(self, alias_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        target_tool = CHROME_DEVTOOLS_ALIAS_TO_TOOL.get(alias_name)
        if not target_tool:
            return {"ok": False, "reason": "unknown_chrome_devtools_alias", "tool": alias_name}
        target_arguments = {key: value for key, value in arguments.items() if key not in CHROME_DEVTOOLS_WRAPPER_KEYS}
        timeout_seconds = int(arguments.get("timeout_seconds") or 45)
        payload = self.gateway_call(
            {
                "profile": "chrome-devtools",
                "tool": target_tool,
                "arguments": target_arguments,
                "timeout_seconds": max(1, min(timeout_seconds, 120)),
                "fallback_ack": str(arguments.get("fallback_ack") or "").strip(),
            }
        )
        payload.setdefault("alias", alias_name)
        payload["target_profile"] = "chrome-devtools"
        payload["target_tool"] = target_tool
        payload["policy"] = {
            "native_first": True,
            "requires_current_turn_negative_observation": True,
            "does_not_expand_permissions": True,
        }
        return payload

    def resource_request(self, arguments: dict[str, Any]) -> dict[str, Any]:
        allowed = set(ResourceBrokerRequest.__dataclass_fields__)
        payload = {key: value for key, value in arguments.items() if key in allowed}
        store_root = Path(str(arguments.get("store_root") or "")).expanduser().resolve() if arguments.get("store_root") else None
        try:
            request = ResourceBrokerRequest(**payload)
            receipt = handle_request(request, store_root=store_root) if store_root else handle_request(request)
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "tool": "resource.request"}
        result = dict(receipt.__dict__)
        result.setdefault("tool", "resource.request")
        return result

    def resource_request_batch(self, arguments: dict[str, Any]) -> dict[str, Any]:
        store_root = Path(str(arguments.get("store_root") or "")).expanduser().resolve() if arguments.get("store_root") else None
        try:
            requests = requests_from_payload(arguments)
            config = batch_config_from_payload(arguments)
            batch = execute_batch(requests, config=config, store_root=store_root) if store_root else execute_batch(requests, config=config)
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "tool": "resource.request_batch"}
        if str(arguments.get("detail") or "compact").strip().lower() != "full":
            item_limit = max(1, min(int(arguments.get("item_limit") or 50), 100))
            compact = resource_progress_view(
                batch_manifest_path=str(batch.get("manifest_path") or ""),
                include_items=True,
                limit=item_limit,
            )
            compact["tool"] = "resource.request_batch"
            compact["receipt_detail"] = "compact"
            compact["full_manifest_path"] = str(batch.get("manifest_path") or "")
            compact["network_batch"] = batch.get("network_batch", {})
            return compact
        batch.setdefault("tool", "resource.request_batch")
        batch["receipt_detail"] = "full"
        return batch

    def resource_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = str(arguments.get("request_id") or "").strip()
        if not request_id:
            return {"ok": False, "reason": "request_id_required", "tool": "resource.status"}
        payload = read_receipt(DEFAULT_RECEIPT_LOG, request_id)
        payload.setdefault("tool", "resource.status")
        return payload

    def resource_progress(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = resource_progress_view(
                request_id=str(arguments.get("request_id") or "").strip(),
                manifest_path=str(arguments.get("manifest_path") or "").strip(),
                batch_manifest_path=str(arguments.get("batch_manifest_path") or "").strip(),
                include_items=bool(arguments.get("include_items")),
                limit=int(arguments.get("limit") or 20),
                receipt_log=DEFAULT_RECEIPT_LOG,
            )
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "tool": "resource.progress"}
        payload.setdefault("tool", "resource.progress")
        return payload

    def resource_attach_result(self, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = str(arguments.get("request_id") or "").strip()
        source_tool = str(arguments.get("source_tool") or "").strip()
        if not request_id:
            return {"ok": False, "reason": "request_id_required", "tool": "resource.attach_result"}
        if not source_tool:
            return {"ok": False, "reason": "source_tool_required", "tool": "resource.attach_result"}
        metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {}
        try:
            payload = attach_result_to_request(
                request_id=request_id,
                source_tool=source_tool,
                result_kind=str(arguments.get("result_kind") or "owner_result"),
                content=str(arguments.get("content") or ""),
                artifact_path=str(arguments.get("artifact_path") or ""),
                metadata=metadata,
                receipt_log=DEFAULT_RECEIPT_LOG,
            )
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "tool": "resource.attach_result"}
        payload.setdefault("tool", "resource.attach_result")
        return payload

    def github_api(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return github_hub_api(arguments)
        except ValueError as exc:
            detail = str(exc)
            if detail.startswith("only_api_github_com_urls_allowed:"):
                return {"ok": False, "reason": "only_api_github_com_urls_allowed", "netloc": detail.split(":", 1)[1]}
            return {"ok": False, "reason": f"ValueError: {detail}"}

    def github_gh(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_args = arguments.get("args")
        if not isinstance(raw_args, list) or not all(isinstance(item, str) and item for item in raw_args):
            return {"ok": False, "reason": "args_must_be_nonempty_string_array"}
        args = [str(item) for item in raw_args]
        lowered = [item.lower() for item in args]
        if lowered[:2] == ["auth", "token"] or lowered[:2] == ["auth", "status"] and "--show-token" in lowered:
            return {"ok": False, "reason": "github_token_printing_blocked", "args": args[:4]}
        likely_mutating_roots = {"auth", "repo", "issue", "pr", "release", "workflow", "run", "gist", "secret", "variable", "label", "api"}
        read_patterns = {
            ("repo", "view"),
            ("repo", "list"),
            ("issue", "view"),
            ("issue", "list"),
            ("pr", "view"),
            ("pr", "list"),
            ("release", "view"),
            ("release", "list"),
            ("run", "view"),
            ("run", "list"),
            ("workflow", "list"),
            ("search", "repos"),
            ("search", "issues"),
            ("search", "prs"),
            ("api",),
        }
        root = lowered[0] if lowered else ""
        prefix1 = tuple(lowered[:1])
        prefix2 = tuple(lowered[:2])
        is_read = prefix2 in read_patterns or prefix1 in read_patterns
        if root == "api":
            mutating_methods = {"post", "patch", "put", "delete"}
            for idx, item in enumerate(lowered):
                if item in {"--method", "-x"} and idx + 1 < len(lowered) and lowered[idx + 1] in mutating_methods:
                    is_read = False
                if item.startswith("--method=") and item.split("=", 1)[1] in mutating_methods:
                    is_read = False
                if item in {"--field", "-f", "--raw-field", "-F", "--input"} or item.startswith("--field=") or item.startswith("--raw-field=") or item.startswith("--input="):
                    is_read = False
        if "--show-token" in lowered:
            return {"ok": False, "reason": "github_token_printing_blocked", "args": args[:4]}
        if root in likely_mutating_roots and not is_read:
            write_ack = str(arguments.get("write_ack") or "").strip()
            if write_ack != "github-write-through-hub-uses-existing-permissions":
                return {
                    "ok": False,
                    "reason": "write_ack_required",
                    "required": "github-write-through-hub-uses-existing-permissions",
                    "args": args[:4],
                }
        timeout_seconds = int(arguments.get("timeout_seconds") or 60)
        stdin = str(arguments.get("stdin") or "")
        payload = run_text_command(["gh", *args], timeout=max(1, min(timeout_seconds, 120)), input_text=stdin)
        payload.update({"tool": "github.gh", "args": args[:20], "uses_existing_gh_auth": True})
        return payload

    def agent_bridge_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _ = arguments
        return run_json_command(
            [
                "python",
                "_bridge\\mcp_session_doctor.py",
                "tool-call",
                "--profile",
                "agent-bridge",
                "--tool",
                "agent_status",
                "--arguments-json",
                "{}",
                "--timeout-seconds",
                "20",
            ],
            timeout=30,
        )

    def gateway_call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        profile = str(arguments.get("profile") or "").strip()
        tool = str(arguments.get("tool") or "").strip()
        tool_arguments = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
        timeout_seconds = int(arguments.get("timeout_seconds") or 45)
        fallback_ack = str(arguments.get("fallback_ack") or "").strip()
        if fallback_ack != "native-mcp-unavailable-and-original-permissions-apply":
            return {
                "ok": False,
                "reason": "fallback_ack_required",
                "required": "native-mcp-unavailable-and-original-permissions-apply",
                "profile": profile,
                "tool": tool,
            }
        if not profile or not tool:
            return {"ok": False, "reason": "profile_and_tool_required", "profile": profile, "tool": tool}
        route = gateway_command(["gateway-route", "--profile", profile, "--tool", tool], timeout=20)
        if not route.get("ok"):
            return {"ok": False, "reason": "gateway_route_failed", "profile": profile, "tool": tool, "route": route}
        if route.get("route") != "fresh_stdio":
            return {
                "ok": False,
                "reason": "hub_gateway_has_no_safe_call_route_for_profile",
                "profile": profile,
                "tool": tool,
                "route": route,
                "next_step": "Continue with the next later stage from the generated priority chain; do not jump backward to native or invent a write path inside the Hub.",
            }
        payload = gateway_command(
            [
                "gateway-call",
                "--profile",
                profile,
                "--tool",
                tool,
                "--arguments-json",
                json.dumps(tool_arguments, ensure_ascii=False),
                "--timeout-seconds",
                str(max(1, min(timeout_seconds, 120))),
            ],
            timeout=max(5, min(timeout_seconds + 10, 140)),
        )
        payload.setdefault("hub_gateway_call_policy", {})
        if isinstance(payload["hub_gateway_call_policy"], dict):
            payload["hub_gateway_call_policy"].update(
                {
                    "classified_affinity_respected": True,
                    "direct_current_turn_allowed": bool(route.get("direct_current_turn_allowed")),
                    "active_negative": bool(route.get("active_negative")),
                    "permission_boundary": "same_as_target_mcp_profile",
                }
            )
        return payload

    def gateway_complete_route(self, arguments: dict[str, Any]) -> dict[str, Any]:
        profile = str(arguments.get("profile") or "").strip()
        tool = str(arguments.get("tool") or "").strip()
        status = str(arguments.get("status") or "transport_closed").strip() or "transport_closed"
        detail = str(arguments.get("detail") or "").strip()
        tool_arguments = arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}
        timeout_seconds = int(arguments.get("timeout_seconds") or 45)
        fallback_ack = str(arguments.get("fallback_ack") or "").strip()
        if fallback_ack != "native-mcp-unavailable-and-original-permissions-apply":
            return {
                "ok": False,
                "reason": "fallback_ack_required",
                "required": "native-mcp-unavailable-and-original-permissions-apply",
                "profile": profile,
                "tool": tool,
            }
        if not profile or not tool:
            return {"ok": False, "reason": "profile_and_tool_required", "profile": profile, "tool": tool}
        command = [
            "complete-route",
            "--profile",
            profile,
            "--tool",
            tool,
            "--status",
            status,
            "--source",
            "local-mcp-hub",
            "--arguments-json",
            json.dumps(tool_arguments, ensure_ascii=False),
            "--timeout-seconds",
            str(max(1, min(timeout_seconds, 120))),
        ]
        if detail:
            command.extend(["--detail", detail])
        return gateway_command(command, timeout=max(5, min(timeout_seconds + 10, 140)))

    def capabilities(self) -> dict[str, Any]:
        fresh_stdio_profiles = [
            "codegraph",
            "custom-slash-commands",
            "filesystem",
            "filesystem-admin",
            "sqlite-scratch",
            "sqlite-bridge-ro",
            "local-pmb-memory",
            "mobile-openclaw-bridge",
            "myskills",
            "gui-automation",
            "chrome-devtools",
            "playwright",
            "markitdown",
            "microsoftdocs",
            "openai-docs",
            "context7",
            "desktop-weixin",
        ]
        profile_specific_profiles = [
            "node_repl",
            "agent-bridge",
        ]
        return {
            "schema": "local_mcp_hub.capabilities.v1",
            "ok": True,
            "generated_at": now_iso(),
            "policy": {
                "classified_affinity": True,
                "stateless_owner_services_hub_first": True,
                "session_bound_tools_native_first": True,
                "native_first": False,
                "gateway_requires_current_turn_negative": False,
                "known_hub_direct_before_complete_route": True,
                "gateway_ack": "native-mcp-unavailable-and-original-permissions-apply",
                "does_not_expand_permissions": True,
            },
            "hub_native": {
                "slash": {"mode": "read_only_templates", "tools": ["slash.list_commands", "slash.get_command", "slash.render_command", "slash.validate_registry"]},
                "sqlite_scratch": {"mode": "read_only", "tools": ["sqlite_scratch.sqlite_health", "sqlite_scratch.sqlite_tables", "sqlite_scratch.sqlite_schema", "sqlite_scratch.sqlite_query"]},
                "sqlite_bridge": {"mode": "read_only", "tools": ["sqlite_bridge.sqlite_health", "sqlite_bridge.sqlite_tables", "sqlite_bridge.sqlite_schema", "sqlite_bridge.sqlite_query"]},
                "sqlite_aliases": {
                    "mode": "read_only_codex_discoverable_aliases",
                    "tools": sorted(SQLITE_ALIAS_ROUTES),
                    "note": "aliases exist because some Codex tool surfaces do not reliably expose dotted SQLite Hub names",
                },
                "record_store": {
                    "mode": "read_only_index_query",
                    "tools": ["record_store.sqlite_health", "record_store.sqlite_tables", "record_store.sqlite_schema", "record_store.sqlite_query"],
                    "index_path": str(RECORD_STORE_INDEX_PATH),
                },
                "email_state": {
                    "mode": "read_only_derived_email_index_query",
                    "tools": ["email_state.sqlite_health", "email_state.sqlite_tables", "email_state.sqlite_schema", "email_state.sqlite_query"],
                    "index_path": str(EMAIL_STATE_INDEX_PATH),
                },
                "pmb": {"mode": "read_prepare_only", "tools": ["pmb.workspace_info", "pmb.prepare", "pmb.recall", "pmb.project_overview", "pmb.stats", "pmb.list_goals"]},
                "codegraph": {
                    "mode": "hub_first_shared_runtime_read_only",
                    "tools": ["codegraph.explore"],
                    "policy": "use a validated index immediately; freshness uncertainty schedules a coalesced background refresh",
                },
                "chrome_devtools": {
                    "mode": "gateway_alias_after_native_failure",
                    "tools": sorted(CHROME_DEVTOOLS_ALIAS_TO_TOOL),
                    "target_profile": "chrome-devtools",
                    "fallback_ack": "native-mcp-unavailable-and-original-permissions-apply",
                    "safety_policy": "native chrome-devtools first; Hub alias only after current-turn negative observation; target browser permissions still apply",
                },
                "desktop_weixin": {
                    "mode": "same_safety_hub_native_and_fresh_stdio_fallback" if not DESKTOP_WEIXIN_IMPORT_ERROR else "platform_deferred_windows_gui_owner",
                    "tools": sorted(DESKTOP_WEIXIN_TOOLS),
                    "safety_policy": "same as desktop-weixin MCP; sends require confirm_send=SEND",
                    "available": not bool(DESKTOP_WEIXIN_IMPORT_ERROR),
                    "deferred_reason": DESKTOP_WEIXIN_IMPORT_ERROR,
                },
                "github": {
                    "mode": "full_proxy_existing_permissions",
                    "tools": ["github.api", "github.gh"],
                    "write_ack": "github-write-through-hub-uses-existing-permissions",
                    "token_sources": ["environment", "github_app.installation_token", "secret_vault:github.token"],
                    "secret_policy": "blocks_token_printing_and_never_returns_token",
                },
                "github_app": {
                    "mode": "secret_vault_backed_installation_token_handoff",
                    "tools": ["github_app.snapshot", "github_app.doctor", "github_app.validate"],
                    "required_aliases": ["github_app.app_id", "github_app.installation_id", "github_app.private_key"],
                    "secret_policy": "private_key_and_tokens_are_never_returned",
                },
                "resource": {
                    "mode": "broker_receipts_local_safe_attempts_owner_tool_orchestration",
                    "tools": ["resource.request", "resource.request_batch", "resource.status", "resource.progress", "resource.attach_result"],
                    "permission_policy": "resource requests authorize automatic owner-tool orchestration only within resource-acquisition boundaries; owner permissions still apply",
                },
                "workflow": {
                    "mode": "compact_read_only_execution_route_pack",
                    "tools": ["workflow.route_pack"],
                    "policy": "returns execution_route_pack only, not the full workflow plan, so tool-side routing can stay low-token",
                },
                "network": {
                    "mode": "codex_gateway_control_plane_plus_read_only_route_discovery_and_probe",
                    "tools": [
                        "network_gateway.snapshot",
                        "network_gateway.interfaces",
                        "network_gateway.plan",
                        "network_gateway.env",
                        "network_gateway.smoke",
                        "network_gateway.lease_start",
                        "network_gateway.lease_status",
                        "network_gateway.lease_stop",
                        "network_gateway.lease_cleanup",
                        "network_gateway.validate",
                        "network.snapshot",
                        "network.recommend",
                        "network.env",
                        "network.plan",
                        "network.probe",
                        "network.probe_suite",
                        "network.validate",
                    ],
                    "policy": "network_gateway.* is the caller-facing control plane; network.* remains lower-level diagnostics. Neither modifies system proxy, DNS, Clash rules, or permanent environment variables",
                },
                "agent_bridge": {"mode": "read_only_status", "tools": ["agent_bridge.status"]},
                "mcp_session": {"mode": "read_only_maintenance", "tools": ["mcp_session.validate", "mcp_session.metrics", "mcp_session.recover_plan"]},
                "owner_mcp": {
                    "mode": "hub_first_explicit_read_only_allowlist",
                    "tools": ["owner_mcp.call_readonly"],
                    "policy": "no prior native failure required; session-bound and write tools are excluded",
                },
                "hub_on_demand": {
                    "mode": "stable_core_catalog_search_describe_call",
                    "tools": ["hub.catalog", "hub.search", "hub.describe", "hub.call"],
                    "ack": hub_catalog.HUB_ON_DEMAND_ACK,
                    "policy": "default tools/list exposes stable core tools; hidden or low-frequency tools are discovered and called on demand without expanding permissions",
                },
            },
            "gateway": {
                "tools": ["mcp_gateway.route", "mcp_gateway.call"],
                "fresh_stdio_profiles": fresh_stdio_profiles,
                "profile_specific_fallback_profiles": profile_specific_profiles,
                "note": "Only fresh_stdio_profiles can be called through mcp_gateway.call. Profile-specific fallbacks are listed so Codex can route honestly without overclaiming Hub proxy coverage.",
            },
            "metamcp_lab": {
                "tools": ["metamcp_lab.catalog", "metamcp_lab.search", "metamcp_lab.describe", "metamcp_lab.call_readonly", "metamcp_lab.validate"],
                "mode": "experimental_hidden_lab_only",
                "ack": metamcp_lab.LAB_READONLY_ACK,
                "note": "Experimental only and hidden from default tools/list: Hub stays the governance layer, MetaMCP lab is a child MCP aggregation backend for tests. Native MCP remains first and production config is not changed.",
            },
        }

    def dispatch_jsonrpc(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = str(request.get("method") or "")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            result = self.initialize(params)
        elif method == "tools/list":
            result = self.tools_list(params)
        elif method == "tools/call":
            result = self.tools_call(params)
        else:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}

    def snapshot(self) -> dict[str, Any]:
        state = read_json(STATE_PATH, {})
        return {
            "schema": "local_mcp_hub.snapshot.v1",
            "ok": True,
            "generated_at": now_iso(),
            "server": {"name": SERVER_NAME, "version": SERVER_VERSION, "started_at": self.started_at},
            "bind": {"host": DEFAULT_HOST, "default_port": DEFAULT_PORT},
            "tools": {
                "slash": {"enabled": True, "mode": "read_only_templates"},
                "sqlite_scratch": {"enabled": True, "mode": "read_only"},
                "sqlite_bridge": {"enabled": True, "mode": "read_only"},
                "record_store": {"enabled": True, "mode": "read_only_index_query", "path": str(RECORD_STORE_INDEX_PATH)},
                "email_state": {"enabled": True, "mode": "read_only_derived_email_index_query", "path": str(EMAIL_STATE_INDEX_PATH)},
                "pmb": {"enabled": True, "mode": "read_prepare_only"},
                "codegraph": {"enabled": True, "mode": "hub_first_gateway_alias_read_only"},
                "chrome_devtools": {
                    "enabled": True,
                    "mode": "gateway_alias_after_native_failure",
                    "tools": sorted(CHROME_DEVTOOLS_ALIAS_TO_TOOL),
                },
                "desktop_weixin": {
                    "enabled": not bool(DESKTOP_WEIXIN_IMPORT_ERROR),
                    "mode": "same_safety_hub_native_and_fresh_stdio_fallback" if not DESKTOP_WEIXIN_IMPORT_ERROR else "platform_deferred_windows_gui_owner",
                    "deferred_reason": DESKTOP_WEIXIN_IMPORT_ERROR,
                },
                "github": {
                    "enabled": True,
                    "mode": "full_proxy_existing_permissions",
                    "tools": ["github.api", "github.gh"],
                    "token_sources": ["environment", "github_app.installation_token", "secret_vault:github.token"],
                },
                "github_app": {
                    "enabled": True,
                    "mode": "secret_vault_backed_installation_token_handoff",
                    "tools": ["github_app.snapshot", "github_app.doctor", "github_app.validate"],
                    "required_aliases": ["github_app.app_id", "github_app.installation_id", "github_app.private_key"],
                },
                "secret_vault": {
                    "enabled": True,
                    "mode": "metadata_read_and_backend_validate_no_secret_printing",
                    "tools": ["secret_vault.snapshot", "secret_vault.doctor", "secret_vault.validate"],
                },
                "resource": {
                    "enabled": True,
                    "mode": "broker_receipts_local_safe_attempts_owner_tool_orchestration",
                    "tools": ["resource.request", "resource.request_batch", "resource.status", "resource.progress", "resource.attach_result"],
                },
                "workflow": {
                    "enabled": True,
                    "mode": "compact_read_only_execution_route_pack",
                    "tools": ["workflow.route_pack"],
                },
                "network": {
                    "enabled": True,
                    "mode": "read_only_route_discovery_and_probe",
                    "tools": ["network.snapshot", "network.recommend", "network.env", "network.plan", "network.probe", "network.probe_suite", "network.validate"],
                },
                "agent_bridge": {"enabled": True, "mode": "read_only_status"},
                "owner_mcp": {"enabled": True, "mode": "hub_first_explicit_read_only_allowlist"},
                "mcp_gateway": {"enabled": True, "mode": "diagnostic_or_dynamic_fallback"},
                "hub_on_demand": {
                    "enabled": True,
                    "mode": "stable_core_catalog_search_describe_call",
                    "tools": ["hub.catalog", "hub.search", "hub.describe", "hub.call"],
                    "hidden_tool_policy": "discover with hub.catalog/search, expand one schema with hub.describe, call with hub.call and target ack requirements",
                },
                "metamcp_lab": {
                    "enabled": False,
                    "mode": "experimental_hidden_lab_only",
                    "tools": ["metamcp_lab.catalog", "metamcp_lab.search", "metamcp_lab.describe", "metamcp_lab.call_readonly", "metamcp_lab.validate"],
                },
                "mcp_session": {"enabled": True, "mode": "read_only_maintenance"},
                "filesystem": {"enabled": True, "mode": "hub_first_readonly_owner_adapter"},
            },
            "state": state,
        }

    def doctor(self) -> dict[str, Any]:
        snap = self.snapshot()
        issues: list[dict[str, Any]] = []
        runtime = hub_runtime_state()
        if not runtime.get("ok"):
            issues.append({"severity": "risk", "code": "hub_listener_unavailable", "detail": runtime})
        if not DEFAULT_REGISTRY.exists():
            issues.append({"severity": "risk", "code": "slash_registry_missing", "path": str(DEFAULT_REGISTRY)})
        sqlite_db = SCRATCH_DB_PATH
        if not sqlite_db.exists():
            issues.append({"severity": "risk", "code": "sqlite_scratch_missing", "path": str(sqlite_db)})
        bridge_db = BRIDGE_DB_PATH
        if not bridge_db.exists():
            issues.append({"severity": "risk", "code": "sqlite_bridge_missing", "path": str(bridge_db)})
        if not RECORD_STORE_INDEX_PATH.exists():
            issues.append({"severity": "advisory", "code": "record_store_index_missing", "path": str(RECORD_STORE_INDEX_PATH)})
        if not EMAIL_STATE_INDEX_PATH.exists():
            issues.append({"severity": "advisory", "code": "email_state_index_missing", "path": str(EMAIL_STATE_INDEX_PATH)})
        if not PMB_TOKEN_PATH.exists():
            issues.append({"severity": "advisory", "code": "pmb_daemon_token_missing", "path": str(PMB_TOKEN_PATH)})
        task = scheduled_task_state()
        if not task.get("ok"):
            issues.append({"severity": "advisory", "code": "scheduled_task_check_failed", "detail": task})
        elif not task.get("exists"):
            issues.append({"severity": "risk", "code": "scheduled_task_missing", "task": "CodexLocalMcpHub"})
        else:
            if task.get("startWhenAvailable") is not True:
                issues.append({"severity": "risk", "code": "scheduled_task_start_when_available_disabled", "task": "CodexLocalMcpHub", "detail": task})
            if int(task.get("restartCount") or 0) < 3:
                issues.append({"severity": "risk", "code": "scheduled_task_restart_count_too_low", "task": "CodexLocalMcpHub", "detail": task})
            if str(task.get("restartInterval") or "").upper() in {"", "PT0S"}:
                issues.append({"severity": "risk", "code": "scheduled_task_restart_interval_missing", "task": "CodexLocalMcpHub", "detail": task})
        return {
            "schema": "local_mcp_hub.doctor.v1",
            "ok": not any(item.get("severity") == "risk" for item in issues),
            "generated_at": now_iso(),
            "issues": issues,
            "snapshot": snap,
            "scheduled_task": task,
            "runtime": runtime,
        }

    def metrics(self) -> dict[str, Any]:
        state = read_json(STATE_PATH, {})
        return {
            "schema": "local_mcp_hub.metrics.v1",
            "ok": True,
            "generated_at": now_iso(),
            "request_count": state.get("request_count", 0),
            "tool_call_count": state.get("tool_call_count", 0),
            "started_at": state.get("started_at", self.started_at),
        }

    def validate(self) -> dict[str, Any]:
        doctor = self.doctor()
        tool_names = [tool.get("name") for tool in self.tools_list({}).get("tools", [])]
        required = {
            "slash.validate_registry",
            "sqlite_bridge_query",
            "sqlite_scratch_query",
            "record_store_query",
            "pmb.workspace_info",
            "codegraph.explore",
            "desktop_weixin.capabilities",
            "desktop_weixin.status",
            "github.api",
            "github.gh",
            "secret_vault.snapshot",
            "secret_vault.doctor",
            "secret_vault.validate",
            "resource.request",
            "resource.request_batch",
            "resource.status",
            "resource.progress",
            "resource.attach_result",
            "mobile_bridge.get_pending_batch",
            "mobile_bridge.ack_message",
            "workflow.route_pack",
            "network.snapshot",
            "network_gateway.snapshot",
            "network_gateway.interfaces",
            "network_gateway.plan",
            "network_gateway.env",
            "network_gateway.smoke",
            "network_gateway.lease_start",
            "network_gateway.lease_status",
            "network_gateway.lease_stop",
            "network_gateway.lease_cleanup",
            "network_gateway.validate",
            "network.recommend",
            "network.env",
            "network.plan",
            "network.probe",
            "network.probe_suite",
            "network.validate",
            "agent_bridge.status",
            "hub.capabilities",
            "hub.catalog",
            "hub.search",
            "hub.describe",
            "hub.call",
            "mcp_gateway.route",
            "owner_mcp.call_readonly",
            "mcp_gateway.call",
            "mcp_gateway.complete_route",
            "hub.validate",
            "mcp_session.validate",
            "mcp_session.recover_plan",
        }
        missing = sorted(required - set(str(item) for item in tool_names))
        issues = list(doctor.get("issues", []))
        owner_adapter = owner_mcp_validate()
        if not owner_adapter.get("ok"):
            issues.append({"severity": "risk", "code": "owner_mcp_readonly_adapter_invalid", "details": owner_adapter})
        for name in missing:
            issues.append({"severity": "risk", "code": "required_tool_missing", "tool": name})
        all_tool_names = {str(tool.get("name") or "") for tool in self.all_tool_specs()}
        hidden_required = {
            "chrome_devtools.list_pages",
            "chrome_devtools.take_snapshot",
            "chrome_devtools.evaluate_script",
            "sqlite_scratch.sqlite_health",
            "sqlite_bridge.sqlite_health",
            "record_store.sqlite_health",
            "record_store.sqlite_query",
            "email_state.sqlite_health",
            "email_state.sqlite_query",
            "email_state_query",
            "metamcp_lab.validate",
        }
        for name in sorted(hidden_required - all_tool_names):
            issues.append({"severity": "risk", "code": "on_demand_tool_missing", "tool": name})
        return {
            "schema": "local_mcp_hub.validate.v1",
            "ok": not any(item.get("severity") == "risk" for item in issues),
            "generated_at": now_iso(),
            "issues": issues,
            "tool_count": len(tool_names),
        }


HUB = LocalMcpHub()


class HubHandler(BaseHTTPRequestHandler):
    server_version = "LocalMcpHub/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any] | list[Any], status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int = 202, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _origin_allowed(self) -> bool:
        origin = str(self.headers.get("Origin") or "").strip().lower()
        if not origin:
            return True
        return origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost")

    def _read_json(self) -> dict[str, Any] | list[Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, (dict, list)):
            raise ValueError("json root must be object or array")
        return payload

    def do_GET(self) -> None:
        HUB.note_request()
        if self.path == "/mcp":
            self._send_json(
                {"ok": False, "reason": "sse_stream_not_supported_by_stateless_http_hub"},
                status=405,
                extra_headers={"Allow": "POST"},
            )
            return
        if self.path in {"/health", "/"}:
            self._send_json({"ok": True, "schema": "local_mcp_hub.health.v1", "generated_at": now_iso()})
            return
        if self.path == "/snapshot":
            self._send_json(HUB.snapshot())
            return
        if self.path == "/doctor":
            self._send_json(HUB.doctor())
            return
        if self.path == "/metrics":
            self._send_json(HUB.metrics())
            return
        if self.path == "/validate":
            self._send_json(HUB.validate())
            return
        if self.path == "/repair-plan":
            self._send_json(repair_plan())
            return
        self._send_json({"ok": False, "reason": "not_found", "path": self.path}, status=404)

    def do_POST(self) -> None:
        HUB.note_request()
        if self.path != "/mcp":
            self._send_json({"ok": False, "reason": "not_found", "path": self.path}, status=404)
            return
        if not self._origin_allowed():
            self._send_json({"ok": False, "reason": "cross_origin_mcp_request_rejected"}, status=403)
            return
        try:
            payload = self._read_json()
            if isinstance(payload, list):
                responses = [HUB.dispatch_jsonrpc(item) for item in payload if isinstance(item, dict)]
                filtered = [item for item in responses if item is not None]
                if not filtered:
                    self._send_empty(status=202)
                    return
                self._send_json(filtered)
                return
            response = HUB.dispatch_jsonrpc(payload)
            if response is None:
                self._send_empty(status=202)
                return
            self._send_json(response)
        except Exception as exc:
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
                },
                status=500,
            )


def serve(host: str, port: int) -> int:
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("local_mcp_hub refuses non-local bind host")
    server = ThreadingHTTPServer((host, port), HubHandler)
    write_json(
        STATE_PATH,
        {
            "schema": "local_mcp_hub.state.v1",
            "updated_at": now_iso(),
            "started_at": HUB.started_at,
            "host": host,
            "port": port,
            "request_count": 0,
            "tool_call_count": 0,
        },
    )
    print(json.dumps({"ok": True, "url": f"http://{host}:{port}/mcp", "health": f"http://{host}:{port}/health"}, ensure_ascii=False))
    server.serve_forever()
    return 0


def http_get_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {"ok": False, "reason": "json_root_not_object"}


def smoke(host: str, port: int) -> dict[str, Any]:
    import urllib.request

    url = f"http://{host}:{port}/mcp"
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": MCP_PROTOCOL_VERSION}}
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        initialized = json.loads(response.read().decode("utf-8"))
    payload2 = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    request2 = urllib.request.Request(url, data=json.dumps(payload2).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(request2, timeout=5) as response:
        tools = json.loads(response.read().decode("utf-8"))
    return {"schema": "local_mcp_hub.smoke.v1", "ok": True, "transport": "stateless_streamable_http", "initialize": initialized, "tools": tools}


def scheduled_task_state(task_name: str = "CodexLocalMcpHub") -> dict[str, Any]:
    command = (
        "$task = Get-ScheduledTask -TaskName "
        + json.dumps(task_name)
        + " -ErrorAction SilentlyContinue; "
        "if (-not $task) { [pscustomobject]@{ exists=$false } | ConvertTo-Json -Depth 4; exit 0 }; "
        "[pscustomobject]@{ "
        "exists=$true; "
        "taskName=$task.TaskName; "
        "state=[string]$task.State; "
        "startWhenAvailable=[bool]$task.Settings.StartWhenAvailable; "
        "restartCount=[int]$task.Settings.RestartCount; "
        "restartInterval=[string]$task.Settings.RestartInterval; "
        "executionTimeLimit=[string]$task.Settings.ExecutionTimeLimit "
        "} | ConvertTo-Json -Depth 4"
    )
    try:
        proc = subprocess.run(
            windows_interop_command("powershell.exe", "-NoProfile", "-Command", command),
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding=windows_console_encoding(),
            errors="replace",
            timeout=10,
            creationflags=hidden_creationflags(),
        )
    except Exception as exc:
        return {"ok": False, "reason": repr(exc), "taskName": task_name}
    text = (proc.stdout or "").strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {"stdout": text[:2000]}
    if not isinstance(payload, dict):
        payload = {"result": payload}
    payload.setdefault("ok", proc.returncode == 0)
    payload.setdefault("returncode", proc.returncode)
    if proc.stderr:
        payload.setdefault("stderr", proc.stderr[:1000])
    return payload


def repair_plan() -> dict[str, Any]:
    return {
        "schema": "local_mcp_hub.repair_plan.v1",
        "ok": True,
        "dry_run": True,
        "actions": [
            {
                "id": "start_local_mcp_hub",
                "mode": "manual_or_approved_background_start",
                "command": "schtasks.exe /Run /TN CodexLocalMcpHub",
                "writes_files": False,
                "network_scope": "127.0.0.1 only",
                "recovery_policy": "bounded retry; returns if an existing local_mcp_hub.py listener is healthy; only restarts listeners whose command line contains local_mcp_hub.py",
            },
            {
                "id": "refresh_stale_local_mcp_hub",
                "mode": "manual_or_approved_controlled_restart",
                "command": "python _bridge\\local_mcp_hub.py reload --confirm-reload",
                "writes_files": False,
                "network_scope": "127.0.0.1 only",
                "safety_boundary": "only stops a listener whose command line contains local_mcp_hub.py, serve, and the target port; starts CodexLocalMcpHub through schtasks without visible PowerShell",
                "use_when": "running HTTP Hub exposes an older tool schema than local source or smoke/validate disagree after a code update",
            },
            {
                "id": "verify_logon_autostart_task",
                "mode": "read_only_or_approved_task_scheduler_repair",
                "command": "Get-ScheduledTask -TaskName CodexLocalMcpHub | Select-Object TaskName,State,Settings",
                "writes_files": False,
                "network_scope": "none",
                "safety_boundary": "Task Scheduler state only; repair should preserve existing action and only harden StartWhenAvailable/retry settings",
                "use_when": "Hub works during the current session but is unavailable after reboot or logon",
            }
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local HTTP MCP hub")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_p = sub.add_parser("serve")
    serve_p.add_argument("--host", default=DEFAULT_HOST)
    serve_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    for name in ("snapshot", "doctor", "metrics", "validate", "repair-plan"):
        sub.add_parser(name)
    smoke_p = sub.add_parser("smoke")
    smoke_p.add_argument("--host", default=DEFAULT_HOST)
    smoke_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    reload_p = sub.add_parser("reload")
    reload_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    reload_p.add_argument("--wait-seconds", type=float, default=5.0)
    reload_p.add_argument("--confirm-reload", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "serve":
        return serve(str(args.host), int(args.port))
    if args.command == "snapshot":
        payload = HUB.snapshot()
    elif args.command == "doctor":
        payload = HUB.doctor()
    elif args.command == "metrics":
        payload = HUB.metrics()
    elif args.command == "validate":
        payload = HUB.validate()
    elif args.command == "repair-plan":
        payload = repair_plan()
    elif args.command == "smoke":
        payload = smoke(str(args.host), int(args.port))
    elif args.command == "reload":
        payload = reload_local_hub(confirm_reload=bool(args.confirm_reload), port=int(args.port), wait_seconds=float(args.wait_seconds))
    else:
        payload = {"ok": False, "reason": "unknown_command"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
