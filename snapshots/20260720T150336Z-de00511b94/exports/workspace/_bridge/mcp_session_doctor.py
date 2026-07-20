#!/usr/bin/env python3
"""MCP session health doctor for Codex-local tools.

This layer is intentionally separate from resource_process_doctor:
resource_process_doctor answers "are the MCP/resource processes sane?";
this module answers "does the current Codex session have a reliable way to use
each MCP, and what should happen if its transport is closed?".

It does not call live Codex MCP tools directly because a failed tool call is
only observable inside the active model session. Instead, callers can pass
observations such as "codegraph:transport_closed"; the doctor combines that
with config, process, and fallback evidence to produce a conservative plan.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bounded_output import governed_cli_payload
from shared.process_liveness import process_is_alive as _shared_process_is_alive

from mcp_execution_priority import DESKTOP_NATIVE_MCP_NAMES, HUB_MANAGED_MCP_NAMES, codex_config_path, runtime_platform  # noqa: E402
from mcp_session_profile_drift import profile_registration_issues  # noqa: E402
from mcp_session_doctor_routes import direct_command_payload  # noqa: E402
from mcp_route_policy import execution_affinity, hub_attempt_placeholder, route_contract_check as mcp_route_contract_check, route_policy
from shared.json_cli import now_iso


ROOT = Path(__file__).resolve().parents[1]
CODEX_CONFIG = codex_config_path()
CODEX_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"
OBSERVATION_LOG = ROOT / "_bridge" / "mobile_openclaw_bridge" / "runtime" / "mcp_session_observations.jsonl"
GATEWAY_STATE_PATH = ROOT / "_bridge" / "mobile_openclaw_bridge" / "runtime" / "mcp_tool_gateway_state.json"
MCP_GUARD_LOCK_DIR = ROOT / "_bridge" / "runtime" / "mcp_launch_guard"
DEFAULT_OBSERVATION_MAX_AGE_MINUTES = 12 * 60
CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES = 30
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_GUARD_LOCK_MAX_AGE_SECONDS = 120
RETIRED_PROFILES: set[str] = set()
CURRENT_TURN_PROBE_SOURCE = "current-codex-turn"
HUB_COMPLETE_ROUTE_SOURCE = "local-mcp-hub"
REQUIRED_NATIVE_MCP_PROFILES = {
    "node_repl",
}
CONTROLLED_REBIND_SCRIPT = Path.home() / ".codex" / "scripts" / "start-codex-desktop-elevated.ps1"

TOOL_TIERS: dict[str, str] = {
    "codegraph": "A",
    "mobile-openclaw-bridge": "A",
    "local-pmb-memory": "A",
    "filesystem": "A",
    "custom-slash-commands": "A",
    "local-mcp-hub": "A",
    "sqlite-scratch": "A",
    "sqlite-bridge-ro": "A",
    "github": "A",
    "agent-bridge": "A",
    "filesystem-admin": "B",
    "node_repl": "B",
    "context7": "B",
    "chrome-devtools": "B",
    "playwright": "B",
    "next-ai-drawio": "B",
    "markitdown": "B",
    "microsoftdocs": "B",
    "openai-docs": "B",
    "myskills": "B",
    "gui-automation": "C",
}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class McpProfile:
    name: str
    config_name: str
    process_group: str
    guard_profile: str
    protected: bool
    fallback: str
    fallback_command: str
    recovery_policy: str
    notes: str = ""
    transport_topology: str = "external_stdio"


@dataclass(frozen=True)
class McpSmokeSpec:
    profile: str
    command: tuple[str, ...]
    env: dict[str, str]
    expected_tools: tuple[str, ...]
    timeout_seconds: int = 20


def _runtime_python() -> str:
    candidate = Path(r"C:\Users\45543\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
    return str(candidate) if candidate.exists() else "python"


def _profile_launcher_command(profile: str, *, eager: bool = False) -> tuple[str, ...]:
    command = (
        _runtime_python(),
        str(ROOT / "_bridge" / "mcp_profile_launcher.py"),
        profile,
    )
    return (*command, "--eager") if eager else command


def _guarded_python_command(profile: str, min_age_minutes: int, *server_args: str) -> tuple[str, ...]:
    return (
        _runtime_python(),
        str(ROOT / "_bridge" / "mcp_launch_guard.py"),
        "--profile",
        profile,
        "--min-age-minutes",
        str(min_age_minutes),
        "--",
        *server_args,
    )


def _ms_to_datetime(value: Any) -> datetime | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def thread_freshness_anchor(thread_id: str | None = None) -> dict[str, Any]:
    requested_thread_id = str(thread_id or "").strip()
    if not CODEX_STATE_DB.exists():
        return {
            "state": "state_db_missing",
            "path": str(CODEX_STATE_DB),
            "requested_thread_id": requested_thread_id,
            "selected_thread_id": "",
            "anchor_field": "",
            "anchor_at": "",
            "note": "No thread freshness anchor is available because the Codex state database is missing.",
        }
    try:
        con = sqlite3.connect(CODEX_STATE_DB)
        con.row_factory = sqlite3.Row
        if requested_thread_id:
            row = con.execute(
                "select id, created_at_ms, updated_at_ms, recency_at_ms, source, model_provider, cli_version, cwd from threads where id=?",
                (requested_thread_id,),
            ).fetchone()
            lookup_mode = "requested_thread"
        else:
            row = con.execute(
                """
                select id, created_at_ms, updated_at_ms, recency_at_ms, source, model_provider, cli_version, cwd
                from threads
                order by coalesce(updated_at_ms, 0) desc, coalesce(recency_at_ms, 0) desc, coalesce(created_at_ms, 0) desc
                limit 1
                """
            ).fetchone()
            lookup_mode = "latest_thread"
    except Exception as exc:
        return {
            "state": "state_db_error",
            "path": str(CODEX_STATE_DB),
            "requested_thread_id": requested_thread_id,
            "selected_thread_id": "",
            "anchor_field": "",
            "anchor_at": "",
            "error": repr(exc),
        }
    if row is None:
        return {
            "state": "thread_not_found",
            "path": str(CODEX_STATE_DB),
            "requested_thread_id": requested_thread_id,
            "selected_thread_id": "",
            "anchor_field": "",
            "anchor_at": "",
            "lookup_mode": lookup_mode,
        }
    candidates: list[tuple[str, datetime]] = []
    for field in ("updated_at_ms", "recency_at_ms", "created_at_ms"):
        anchor = _ms_to_datetime(row[field])
        if anchor is not None:
            candidates.append((field, anchor))
    if not candidates:
        return {
            "state": "thread_timestamp_missing",
            "path": str(CODEX_STATE_DB),
            "requested_thread_id": requested_thread_id,
            "selected_thread_id": str(row["id"] or ""),
            "anchor_field": "",
            "anchor_at": "",
            "lookup_mode": lookup_mode,
        }
    anchor_field, anchor_at = max(candidates, key=lambda item: item[1])
    return {
        "state": "ok",
        "path": str(CODEX_STATE_DB),
        "requested_thread_id": requested_thread_id,
        "selected_thread_id": str(row["id"] or ""),
        "lookup_mode": lookup_mode,
        "anchor_field": anchor_field,
        "anchor_at": anchor_at.isoformat(),
        "anchor_ms": int(anchor_at.timestamp() * 1000),
        "thread": {key: row[key] for key in row.keys()},
    }


SMOKE_SPECS: dict[str, McpSmokeSpec] = {
    "codegraph": McpSmokeSpec(
        profile="codegraph",
        command=_profile_launcher_command("cg"),
        env={"CODEGRAPH_NO_DAEMON": "1", "CODEGRAPH_WATCH_DEBOUNCE_MS": "2000"},
        expected_tools=("codegraph_explore",),
        timeout_seconds=25,
    ),
    "mobile-openclaw-bridge": McpSmokeSpec(
        profile="mobile-openclaw-bridge",
        command=(
            _runtime_python(),
            str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_bridge_mcp_server.py"),
        ),
        env={
            "MOBILE_OPENCLAW_BRIDGE_CONFIG": str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "config.toml"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
        expected_tools=("bridge.get_pending_batch", "bridge.ack_message"),
        timeout_seconds=12,
    ),
    "local-pmb-memory": McpSmokeSpec(
        profile="local-pmb-memory",
        command=_profile_launcher_command("pmb"),
        env={
            "PMB_HOME": str(Path.home() / "Desktop" / "Codex资源库" / "memory" / "pmb" / "data"),
            "PMB_WORKSPACE": "mcsmanager",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
        expected_tools=("recall", "prepare", "record_batch"),
        timeout_seconds=30,
    ),
    "filesystem": McpSmokeSpec(
        profile="filesystem",
        command=_profile_launcher_command("fs"),
        env={},
        expected_tools=("read_file", "list_directory", "write_file"),
        timeout_seconds=30,
    ),
    "filesystem-admin": McpSmokeSpec(
        profile="filesystem-admin",
        command=_profile_launcher_command("fs-admin"),
        env={},
        expected_tools=("read_file", "list_directory", "write_file"),
        timeout_seconds=30,
    ),
    "custom-slash-commands": McpSmokeSpec(
        profile="custom-slash-commands",
        command=_guarded_python_command(
            "slash",
            15,
            _runtime_python(),
            str(ROOT / "_bridge" / "custom_slash_commands_mcp.py"),
            "--registry",
            str(ROOT / "_bridge" / "slash_commands" / "commands.json"),
        ),
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        expected_tools=("slash.list_commands", "slash.render_command", "slash.validate_registry"),
        timeout_seconds=15,
    ),
    "sqlite-scratch": McpSmokeSpec(
        profile="sqlite-scratch",
        command=_guarded_python_command(
            "sqlite-scratch",
            15,
            _runtime_python(),
            str(ROOT / "_bridge" / "sqlite_mcp_server.py"),
            "--db",
            str(ROOT / "_bridge" / "data" / "sqlite" / "codex_scratch.sqlite"),
            "--permissions",
            "list,read,create,update,delete,ddl,transaction,utility",
        ),
        env={},
        expected_tools=(
            "sqlite_query",
            "sqlite_tables",
            "sqlite_schema",
            "sqlite_execute",
            "sqlite_insert_record",
            "sqlite_upsert_record",
        ),
        timeout_seconds=45,
    ),
    "sqlite-bridge-ro": McpSmokeSpec(
        profile="sqlite-bridge-ro",
        command=_guarded_python_command(
            "sqlite-bridge-ro",
            15,
            _runtime_python(),
            str(ROOT / "_bridge" / "sqlite_mcp_server.py"),
            "--db",
            str(ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_bridge.db"),
            "--permissions",
            "list,read",
            "--readonly",
        ),
        env={},
        expected_tools=("sqlite_query", "sqlite_tables", "sqlite_schema"),
        timeout_seconds=45,
    ),
    "playwright": McpSmokeSpec(
        profile="playwright",
        command=_profile_launcher_command("pw", eager=True),
        env={},
        expected_tools=("browser_navigate", "browser_snapshot", "browser_close"),
        timeout_seconds=60,
    ),
    "next-ai-drawio": McpSmokeSpec(
        profile="next-ai-drawio",
        command=_profile_launcher_command("drawio", eager=True),
        env={},
        expected_tools=("start_session", "create_new_diagram", "load_diagram", "edit_diagram", "get_diagram", "export_diagram"),
        timeout_seconds=45,
    ),
    "chrome-devtools": McpSmokeSpec(
        profile="chrome-devtools",
        command=_profile_launcher_command("cdev", eager=True),
        env={},
        expected_tools=("list_pages",),
        timeout_seconds=60,
    ),
    "markitdown": McpSmokeSpec(
        profile="markitdown",
        command=_profile_launcher_command("mid"),
        env={},
        expected_tools=("convert_to_markdown",),
        timeout_seconds=60,
    ),
    "gui-automation": McpSmokeSpec(
        profile="gui-automation",
        command=_profile_launcher_command("gui", eager=True),
        env={
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
        expected_tools=("gui_list_windows", "gui_ocr_status", "gui_ensure_window"),
        timeout_seconds=45,
    ),
    "desktop-weixin": McpSmokeSpec(
        profile="desktop-weixin",
        command=_profile_launcher_command("weixin"),
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        expected_tools=("desktop_weixin.status", "desktop_weixin.capabilities", "desktop_weixin.open", "desktop_weixin.close", "desktop_weixin.message_prepare"),
        timeout_seconds=20,
    ),
    "myskills": McpSmokeSpec(
        profile="myskills",
        command=_profile_launcher_command("skills"),
        env={},
        expected_tools=(),
        timeout_seconds=45,
    ),
    "agent-bridge": McpSmokeSpec(
        profile="agent-bridge",
        command=(
            _runtime_python(),
            r"C:\Users\45543\Downloads\HMCL-3.9.1\.minecraft\versions\3c3u\.reasonix\agent-bridge-mcp\bridge_server_v2.py",
        ),
        env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        expected_tools=("agent_status", "agent_bridge_receive", "agent_bridge_send"),
        timeout_seconds=20,
    ),
    "microsoftdocs": McpSmokeSpec(
        profile="microsoftdocs",
        command=_profile_launcher_command("msdocs"),
        env={},
        expected_tools=("microsoft_docs_search", "microsoft_code_sample_search", "microsoft_docs_fetch"),
        timeout_seconds=45,
    ),
    "context7": McpSmokeSpec(
        profile="context7",
        command=_profile_launcher_command("ctx7"),
        env={},
        expected_tools=("resolve_library_id", "query_docs"),
        timeout_seconds=45,
    ),
    "openai-docs": McpSmokeSpec(
        profile="openai-docs",
        command=_profile_launcher_command("oadocs"),
        env={},
        expected_tools=("search_openai_docs", "fetch_openai_doc", "list_openai_docs", "get_openapi_spec"),
        timeout_seconds=45,
    ),
}

HTTP_MCP_SMOKE_PROFILES = {"local-mcp-hub"}


def profile_tier(profile: McpProfile) -> str:
    tier = str(TOOL_TIERS.get(profile.name, "B")).upper()
    return tier if tier in {"A", "B", "C", "R"} else "B"


def profile_execution_affinity(profile: McpProfile) -> dict[str, str]:
    capability = "mobile_bridge" if profile.name == "mobile-openclaw-bridge" else ""
    return execution_affinity(profile.name, "", capability)


def recovery_strategy_for_topology(profile: McpProfile) -> dict[str, Any]:
    topology = str(profile.transport_topology or "external_stdio")
    tier = profile_tier(profile)
    affinity = profile_execution_affinity(profile)
    base: dict[str, Any] = {
        "profile": profile.name,
        "tool_tier": tier,
        "transport_topology": topology,
        **affinity,
        "native_first": affinity.get("execution_affinity") in {"native_first", "session_native_first"},
        "max_auto_recover_attempts": 1 if tier in {"A", "B"} and not profile.protected else 0,
        "records_observation_before_fallback": True,
        "preserve_permission_boundary": True,
    }
    if profile.protected:
        base.update(
            {
                "strategy": "protected_report_only",
                "recover_steps": [
                    "record current-turn negative evidence",
                    "run read-only protocol smoke if supported",
                    "use documented protected fallback for the active task",
                    "do not restart protected bridge/agent processes automatically",
                ],
            }
        )
        return base
    if topology == "daemon_backed_stdio_proxy":
        base.update(
            {
                "strategy": "reopen_stdio_proxy_keep_daemon",
                "recover_steps": [
                    "record current-turn negative evidence",
                    "verify daemon/service health",
                    "reopen a fresh stdio proxy or gateway call once",
                    "record positive evidence only after the fresh call completes",
                    "fallback to daemon CLI/API if fresh proxy fails",
                ],
            }
        )
        return base
    if topology in {"local_stateless_stdio", "external_stdio", "external_stateless_stdio", "external_stateless_stdio_elevated"}:
        base.update(
            {
                "strategy": "fresh_stdio_once_then_same_boundary_fallback",
                "recover_steps": [
                    "record current-turn negative evidence",
                    "run protocol smoke for the profile if supported",
                    "try one fresh-stdio gateway/tool-call when route allows it",
                    "record positive evidence only after fresh call completion",
                    "fallback without elevating permissions if fresh stdio fails",
                ],
            }
        )
        return base
    if topology == "local_http_mcp_hub":
        base.update(
            {
                "strategy": "reinitialize_http_session",
                "recover_steps": [
                    "record current-turn negative evidence",
                    "run local hub smoke",
                    "reinitialize HTTP MCP session once",
                    "record positive evidence only after native HTTP tool completion",
                    "fallback to per-tool route only with native failure evidence",
                ],
            }
        )
        return base
    base.update(
        {
            "strategy": "record_and_use_profile_fallback",
            "recover_steps": [
                "record current-turn negative evidence",
                "run supported smoke or health check",
                "use profile-specific fallback for the active task",
            ],
        }
    )
    return base


CURRENT_TURN_PROBES: dict[str, dict[str, Any]] = {
    "codegraph": {
        "tool": "mcp__codegraph.codegraph_explore",
        "tool_search_query": "codegraph_explore codegraph current turn MCP",
        "warmup_required": True,
        "probe": "Call codegraph_explore with a tiny query such as '_bridge/mcp_session_doctor.py' and maxFiles=1.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile codegraph --status current_turn_callable --source current-codex-turn --detail \"codegraph_explore returned a tool response in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile codegraph --status tool_unbound --source current-codex-turn --detail \"active turn could not call codegraph_explore\"",
    },
    "filesystem": {
        "tool": "mcp__filesystem.list_allowed_directories",
        "tool_search_query": "filesystem list_allowed_directories MCP",
        "warmup_required": True,
        "probe": "Call list_allowed_directories first; use read_file only for a known small file after that. Record success only after the tool returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile filesystem --status current_turn_callable --source current-codex-turn --detail \"filesystem list_allowed_directories succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile filesystem --status tool_unbound --source current-codex-turn --detail \"active turn could not call filesystem\"",
    },
    "filesystem-admin": {
        "tool": "mcp__filesystem_admin.list_allowed_directories",
        "tool_search_query": "filesystem-admin list_allowed_directories MCP",
        "warmup_required": True,
        "probe": "Call list_allowed_directories first; use read_file only for a known safe file after that. Record success only after the tool returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile filesystem-admin --status current_turn_callable --source current-codex-turn --detail \"filesystem-admin list_allowed_directories succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile filesystem-admin --status tool_unbound --source current-codex-turn --detail \"active turn could not call filesystem-admin\"",
    },
    "custom-slash-commands": {
        "tool": "mcp__custom_slash_commands.slash_validate_registry",
        "tool_search_query": "custom-slash-commands slash_validate_registry slash command MCP",
        "warmup_required": True,
        "probe": "Call slash_validate_registry first; optionally render a tiny known template. Record success only after the tool returns. Rendered output is not executed.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile custom-slash-commands --status current_turn_callable --source current-codex-turn --detail \"custom slash commands validate/render succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile custom-slash-commands --status tool_surface_unstable --source current-codex-turn --detail \"custom slash commands call aborted, hung, or failed in this turn\"",
    },
    "local-mcp-hub": {
        "tool": "mcp__local_mcp_hub.*",
        "tool_search_query": "local-mcp-hub slash.validate_registry sqlite_scratch.sqlite_health MCP",
        "warmup_required": True,
        "probe": "Call a read-only local-mcp-hub tool such as slash.validate_registry or sqlite_scratch.sqlite_health and record success only after the native HTTP MCP returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile local-mcp-hub --status current_turn_callable --source current-codex-turn --detail \"local HTTP MCP hub returned a native tool response in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile local-mcp-hub --status tool_unbound --source current-codex-turn --detail \"active turn could not call local HTTP MCP hub\"",
    },
    "sqlite-scratch": {
        "tool": "mcp__sqlite_scratch.sqlite_health",
        "tool_search_query": "sqlite-scratch sqlite_health MCP",
        "warmup_required": True,
        "probe": "Call sqlite_tables against the scratch profile and record success only after the tool returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile sqlite-scratch --status current_turn_callable --source current-codex-turn --detail \"sqlite-scratch sqlite_health succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile sqlite-scratch --status tool_unbound --source current-codex-turn --detail \"active turn could not call sqlite-scratch\"",
    },
    "sqlite-bridge-ro": {
        "tool": "mcp__sqlite_bridge_ro.sqlite_health",
        "tool_search_query": "sqlite-bridge-ro sqlite_health MCP",
        "warmup_required": True,
        "probe": "Call sqlite_health against the read-only bridge profile and record success only after the tool returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile sqlite-bridge-ro --status current_turn_callable --source current-codex-turn --detail \"sqlite-bridge-ro sqlite_health succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile sqlite-bridge-ro --status tool_unbound --source current-codex-turn --detail \"active turn could not call sqlite-bridge-ro\"",
    },
    "mobile-openclaw-bridge": {
        "tool": "mcp__mobile_openclaw_bridge.bridge_health",
        "tool_search_query": "mobile-openclaw-bridge bridge_health get_pending_batch ack_message MCP",
        "warmup_required": True,
        "probe": "Call bridge_health if exposed; if the tool is missing, use the supplement CLI fallback for the current task and record tool_unbound.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile mobile-openclaw-bridge --status current_turn_callable --source current-codex-turn --detail \"mobile bridge tool returned a response in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile mobile-openclaw-bridge --status tool_unbound --source current-codex-turn --detail \"active turn could not call mobile bridge MCP\"",
    },
    "local-pmb-memory": {
        "tool": "mcp__local_pmb_memory.prepare",
        "tool_search_query": "local-pmb-memory project_structure record_batch list_goals MCP",
        "warmup_required": True,
        "probe": "Call prepare with the current task summary and record success only after the tool returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile local-pmb-memory --status current_turn_callable --source current-codex-turn --detail \"local PMB prepare succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile local-pmb-memory --status tool_unbound --source current-codex-turn --detail \"active turn could not call local PMB memory\"",
    },
    "agent-bridge": {
        "tool": "mcp__agent_bridge.agent_status",
        "tool_search_query": "agent-bridge agent_status MCP",
        "warmup_required": True,
        "probe": "Call agent_status and record success only after the protected bridge tool returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile agent-bridge --status current_turn_callable --source current-codex-turn --detail \"agent-bridge agent_status succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile agent-bridge --status tool_unbound --source current-codex-turn --detail \"active turn could not call agent-bridge\"",
    },
    "node_repl": {
        "tool": "mcp__node_repl.js",
        "tool_search_query": "node_repl js MCP",
        "warmup_required": True,
        "probe": "Run a tiny JS expression through node_repl.js and record success only after the tool returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile node_repl --status current_turn_callable --source current-codex-turn --detail \"node_repl js succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile node_repl --status tool_unbound --source current-codex-turn --detail \"active turn could not call node_repl\"",
    },
    "gui-automation": {
        "tool": "mcp__gui_automation.gui_ocr_status",
        "tool_search_query": "gui-automation gui_ocr_status gui_list_windows MCP",
        "warmup_required": True,
        "probe": "First warm the deferred tool surface with the exact query, then call a lightweight GUI status/list tool and record MCP exposure success separately from GUI/OCR backend readiness.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile gui-automation --status current_turn_callable --source current-codex-turn --detail \"gui-automation tool returned a response in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile gui-automation --status tool_unbound --source current-codex-turn --detail \"active turn could not call gui-automation\"",
    },
    "desktop-weixin": {
        "tool": "mcp__desktop_weixin.desktop_weixin.status",
        "tool_search_query": "desktop-weixin desktop_weixin.status Weixin MCP",
        "warmup_required": True,
        "probe": "First warm the deferred tool surface with the exact query, then call desktop_weixin.status or desktop_weixin.capabilities. Record success only after the desktop Weixin MCP returns; do not substitute mobile OpenClaw bridge evidence.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile desktop-weixin --status current_turn_callable --source current-codex-turn --detail \"desktop-weixin MCP returned a response in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile desktop-weixin --status tool_unbound --source current-codex-turn --detail \"active turn could not call desktop-weixin\"",
    },
    "chrome-devtools": {
        "tool": "mcp__chrome_devtools.list_pages",
        "tool_search_query": "chrome-devtools list_pages MCP",
        "warmup_required": True,
        "probe": "Call list_pages and record success only after Chrome DevTools MCP returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile chrome-devtools --status current_turn_callable --source current-codex-turn --detail \"chrome-devtools list_pages succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile chrome-devtools --status tool_unbound --source current-codex-turn --detail \"active turn could not call chrome-devtools\"",
    },
    "context7": {
        "tool": "mcp__context7.resolve_library_id",
        "tool_search_query": "context7 resolve_library_id query_docs MCP",
        "warmup_required": True,
        "probe": "Resolve a small public library name and record success only after the local Context7 stdio proxy returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile context7 --status current_turn_callable --source current-codex-turn --detail \"context7 resolve_library_id succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile context7 --status tool_unbound --source current-codex-turn --detail \"active turn could not call context7\"",
    },
    "github": {
        "tool": "mcp__github.search_repositories",
        "tool_search_query": "github get_me search_repositories MCP",
        "warmup_required": True,
        "probe": "First warm the deferred tool surface with the exact query, then call get_me or a bounded GitHub search and record success only after the GitHub MCP returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile github --status current_turn_callable --source current-codex-turn --detail \"github search succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile github --status tool_unbound --source current-codex-turn --detail \"active turn could not call github MCP\"",
    },
    "markitdown": {
        "tool": "mcp__markitdown.convert_to_markdown",
        "tool_search_query": "markitdown convert_to_markdown MCP",
        "warmup_required": True,
        "probe": "Call a tiny or no-op-safe conversion probe and record success only after MarkItDown MCP returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile markitdown --status current_turn_callable --source current-codex-turn --detail \"markitdown conversion tool returned in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile markitdown --status tool_unbound --source current-codex-turn --detail \"active turn could not call markitdown\"",
    },
    "microsoftdocs": {
        "tool": "mcp__microsoftdocs.microsoft_docs_search",
        "tool_search_query": "microsoftdocs microsoft_docs_search Microsoft Learn MCP",
        "warmup_required": True,
        "probe": "Call microsoft_docs_search with a tiny Microsoft Learn query and record success only after the local stdio proxy returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile microsoftdocs --status current_turn_callable --source current-codex-turn --detail \"microsoftdocs MCP returned a tool response in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile microsoftdocs --status tool_unbound --source current-codex-turn --detail \"microsoftdocs MCP not exposed in current turn\"",
    },
    "openai-docs": {
        "tool": "mcp__openaiDeveloperDocs.search_openai_docs",
        "tool_search_query": "OpenAI Developer Docs search_openai_docs fetch_openai_doc MCP",
        "warmup_required": True,
        "probe": "Call search_openai_docs with a narrow public OpenAI query and fetch one returned official page. Record success only after non-empty official content returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile openai-docs --status current_turn_callable --source current-codex-turn --detail \"OpenAI Docs MCP search/fetch returned official content in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile openai-docs --status tool_unbound --source current-codex-turn --detail \"OpenAI Docs MCP was unavailable or returned no usable content\"",
    },
    "myskills": {
        "tool": "mcp__myskills.skills_inventory",
        "tool_search_query": "myskills skills_inventory MCP",
        "warmup_required": True,
        "probe": "Call skills_inventory and record success only after MySkills MCP returns.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile myskills --status current_turn_callable --source current-codex-turn --detail \"myskills inventory succeeded in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile myskills --status tool_unbound --source current-codex-turn --detail \"active turn could not call myskills\"",
    },
    "playwright": {
        "tool": "mcp__playwright.*",
        "tool_search_query": "playwright browser_tabs browser_resize MCP",
        "warmup_required": True,
        "probe": "Use the exact tool_search query to expose Playwright MCP, then call a read-only browser/status/list tool if available; if no namespace is exposed after warmup, record tool_unbound.",
        "success_record": "python _bridge\\mcp_session_doctor.py record-observation --profile playwright --status current_turn_callable --source current-codex-turn --detail \"playwright MCP returned a tool response in this turn\"",
        "failure_record": "python _bridge\\mcp_session_doctor.py record-observation --profile playwright --status tool_unbound --source current-codex-turn --detail \"playwright MCP not exposed in current turn\"",
    },
}


MCP_PROFILES: tuple[McpProfile, ...] = (
    McpProfile(
        "codegraph",
        "codegraph",
        "codegraph_mcp",
        "cg",
        False,
        "project_local_cli",
        r'_bridge\tools\codegraph\node_modules\.bin\codegraph.cmd status . --json',
        "fallback_first_then_session_refresh",
        "CLI fallback can read the same index when MCP transport is closed.",
    ),
    McpProfile(
        "mobile-openclaw-bridge",
        "mobile-openclaw-bridge",
        "mobile_bridge_mcp_server",
        "",
        True,
        "local_stdio_mcp",
        r'python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback health',
        "fallback_first_protected_restart_only_with_bridge_evidence",
        "Protected bridge MCP; fallback can read/ack supplements through a fresh local stdio server.",
    ),
    McpProfile(
        "local-pmb-memory",
        "local-pmb-memory",
        "local_pmb_proxy",
        "pmb",
        False,
        "pmb_cli",
        r'python _bridge\local_pmb_memory.py daemon-status',
        "warm_daemon_proxy_then_session_refresh",
        "PMB memory MCP should use a lightweight stdio proxy to the warm local daemon.",
        "daemon_backed_stdio_proxy",
    ),
    McpProfile(
        "filesystem",
        "filesystem",
        "filesystem_mcp",
        "fs",
        False,
        "shell_or_project_cli",
        r'python _bridge\mcp_session_doctor.py smoke --profile filesystem',
        "bounded_roots_then_session_refresh",
        "Native filesystem MCP is the preferred path when current-turn callable. It is an external stateless stdio server with bounded roots; shell/apply_patch fallback preserves functionality after host-side transport cleanup.",
        "external_stateless_stdio",
    ),
    McpProfile(
        "filesystem-admin",
        "filesystem-admin",
        "filesystem_admin_mcp",
        "fs-admin",
        False,
        "shell_or_project_cli",
        r'python _bridge\mcp_session_doctor.py smoke --profile filesystem-admin',
        "c_drive_scope_then_session_refresh",
        "Native filesystem-admin MCP is an explicit elevated fallback only. It is an external stateless stdio server; actual protected-path access still depends on Codex host elevation and user approval.",
        "external_stateless_stdio_elevated",
    ),
    McpProfile(
        "custom-slash-commands",
        "custom-slash-commands",
        "custom_slash_commands_mcp",
        "slash",
        False,
        "local_registry",
        r'python _bridge\mcp_session_doctor.py smoke --profile custom-slash-commands',
        "registry_validate_then_session_refresh",
        "Local slash commands are prompt templates only; this MCP does not execute shell commands. Cross-module task packages are governed by _bridge/tool_coordination.py.",
        "local_stateless_stdio",
    ),
    McpProfile(
        "local-mcp-hub",
        "local-mcp-hub",
        "",
        "",
        False,
        "local_http_mcp",
        r'python _bridge\local_mcp_hub.py smoke --host 127.0.0.1 --port 18881',
        "native_http_mcp_first_then_stdio_compatibility",
        "Pilot local HTTP MCP hub. Native HTTP MCP is preferred for stable core read-only tools; stdio MCP remains registered as compatibility and fallback.",
        "local_http_mcp_hub",
    ),
    McpProfile(
        "sqlite-scratch",
        "sqlite-scratch",
        "sqlite_scratch_mcp",
        "sqlite-scratch",
        False,
        "sqlite_cli_or_python",
        r'python _bridge\mcp_session_doctor.py smoke --profile sqlite-scratch',
        "scratch_db_then_session_refresh",
        "Default writable SQLite MCP. It uses a lightweight local Python sqlite3 server and can mutate only the dedicated scratch database, not production bridge databases. Complex writes should use sqlite_insert_record/sqlite_upsert_record or _bridge/tool_coordination.py record-* commands instead of long raw sqlite_execute calls.",
        "local_stateless_stdio",
    ),
    McpProfile(
        "sqlite-bridge-ro",
        "sqlite-bridge-ro",
        "sqlite_bridge_ro_mcp",
        "sqlite-bridge-ro",
        False,
        "sqlite_cli_or_python_readonly",
        r'python _bridge\mcp_session_doctor.py smoke --profile sqlite-bridge-ro',
        "readonly_bridge_db_then_session_refresh",
        "Read-only SQLite MCP uses a lightweight local Python sqlite3 server for bridge database inspection. Do not grant write permissions to this profile.",
        "local_stateless_stdio",
    ),
    McpProfile(
        "node_repl",
        "node_repl",
        "node_repl",
        "",
        False,
        "none",
        "",
        "session_refresh_if_transport_closed",
        "Node REPL MCP is a persistent JavaScript kernel. Current-turn exposure must be proven with a tiny js call; do not infer availability from config alone.",
    ),
    McpProfile(
        "context7",
        "context7",
        "context7_mcp",
        "ctx7",
        False,
        "remote_docs_alternative",
        r'python _bridge\mcp_session_doctor.py smoke --profile context7',
        "local_stdio_proxy_then_session_refresh",
        "Context7 MCP is exposed through a local read-only stdio proxy to avoid remote Streamable HTTP tool-surface binding drift. A protocol smoke can verify initialize/tools-list; current-turn use still requires an active tool call.",
    ),
    McpProfile(
        "github",
        "github",
        "",
        "",
        False,
        "git_or_web_fallback",
        "",
        "remote_mcp_session_refresh",
        "Remote GitHub MCP. No local process is expected; configuration/token health does not prove active-turn tool exposure.",
    ),
    McpProfile(
        "myskills",
        "myskills",
        "myskills-mcp",
        "skills",
        False,
        "none",
        "",
        "session_refresh_if_transport_closed",
    ),
    McpProfile(
        "gui-automation",
        "gui-automation",
        "gui_automation_mcp",
        "gui",
        False,
        "none",
        "",
        "session_refresh_if_transport_closed",
    ),
    McpProfile(
        "desktop-weixin",
        "desktop-weixin",
        "desktop_weixin_mcp",
        "weixin",
        False,
        "cli_anything_weixin",
        r'python _bridge\mcp_session_doctor.py smoke --profile desktop-weixin',
        "native_mcp_then_hub_or_cli_fallback",
        "Desktop Weixin MCP wraps the local CLI-Anything Weixin harness. It is separate from the mobile OpenClaw bridge; sending still requires confirm_send=SEND.",
        "local_session_bound_stdio_ui",
    ),
    McpProfile(
        "chrome-devtools",
        "chrome-devtools",
        "chrome-devtools",
        "cdev",
        False,
        "browser_or_cli_alternative",
        "",
        "start_on_demand_or_session_refresh",
    ),
    McpProfile(
        "playwright",
        "playwright",
        "playwright",
        "pw",
        False,
        "none",
        "",
        "start_on_demand_or_session_refresh",
    ),
    McpProfile(
        "next-ai-drawio",
        "next-ai-drawio",
        "next_ai_drawio_mcp",
        "drawio",
        False,
        "mermaid_or_manual_drawio_export",
        r'python _bridge\mcp_session_doctor.py smoke --profile next-ai-drawio',
        "native_session_then_forward_fallback_chain",
        "Optional editable Draw.io owner. Use only for explicit editable-diagram tasks; it does not replace Mermaid, whitepaper visualization, or general browser tools.",
        "local_stateless_stdio_ui",
    ),
    McpProfile(
        "markitdown",
        "markitdown",
        "markitdown-mcp",
        "mid",
        False,
        "cli_or_python_library",
        "",
        "fallback_first_then_session_refresh",
    ),
    McpProfile(
        "microsoftdocs",
        "microsoftdocs",
        "microsoftdocs_mcp",
        "msdocs",
        False,
        "web_docs_fallback",
        r'python _bridge\mcp_session_doctor.py smoke --profile microsoftdocs',
        "local_stdio_proxy_then_session_refresh",
        "Microsoft Learn MCP is exposed through a local read-only stdio proxy to avoid remote Streamable HTTP tool-surface binding drift. A protocol smoke can verify initialize/tools-list; current-turn use still requires an active tool call.",
    ),
    McpProfile(
        "openai-docs",
        "openai-docs",
        "openai_docs_mcp",
        "oadocs",
        False,
        "official_openai_domain_search",
        r'python _bridge\mcp_session_doctor.py smoke --profile openai-docs',
        "hub_first_stdio_proxy_then_official_domain_search",
        "Official OpenAI Developer Docs MCP is Hub-managed, read-only, and started as a fresh stdio proxy per call. Official OpenAI-domain search is the bounded fallback after MCP failure or insufficient coverage.",
        "external_stateless_stdio",
    ),
    McpProfile(
        "agent-bridge",
        "agent-bridge",
        "bridge_server_v2",
        "",
        True,
        "none",
        "",
        "protected_report_only",
        "Reasonix/bridge critical path; do not auto-restart without scoped evidence.",
    ),
)


TRANSPORT_CLOSED_MARKERS = (
    "transport closed",
    "mcp transport closed",
    "app_server_mcp_transport_closed",
    "closed transport",
)

TOOL_BINDING_MARKERS = (
    "unsupported call",
    "unsupported_call",
    "tool_unbound",
    "current_session_unbound",
    "tool missing",
    "tool_missing",
    "unknown tool",
    "tool not found",
)

SCHEMA_MISMATCH_MARKERS = (
    "schema mismatch",
    "schema_mismatch",
    "invalid schema",
    "invalid request",
    "protocol mismatch",
)

TOOL_SURFACE_UNSTABLE_MARKERS = (
    "aborted",
    "hang",
    "hung",
    "timeout",
    "timed out",
    "cancelled",
    "canceled",
    "dispatch failure",
    "dispatch_failure",
    "tool call stalled",
    "tool_call_stalled",
    "tool_surface_unstable",
    "current_turn_tool_surface_unstable",
)

POSITIVE_OBSERVATION_STATUSES = {
    "current_turn_callable",
    "tool_available",
    "session_tool_available",
    "tool_call_succeeded",
    "call_succeeded",
    "mcp_session_available",
}

PROTOCOL_OBSERVATION_STATUSES = {
    "protocol_ok",
}

NEGATIVE_OBSERVATION_STATUSES = {
    "transport_closed",
    "tool_unbound",
    "schema_mismatch",
    "tool_surface_unstable",
}

CURRENT_TURN_SOURCE_MARKERS = (
    "current-codex-turn",
    "active-codex-turn",
    "this-codex-turn",
    "current-turn",
)

CURRENT_SESSION_SOURCE_MARKERS = (
    *CURRENT_TURN_SOURCE_MARKERS,
    "current-codex-session",
    "active-codex-session",
    "this-codex-session",
    "current-session",
)

NON_CALLABLE_EVIDENCE_SOURCE_MARKERS = (
    "smoke",
    "fallback",
    "protocol-smoke",
    "tool_search",
    "tool-search",
    "codex mcp list",
    "mcp list",
    "config",
    "process",
    "discovery",
)


def run_cmd(cmd: list[str], timeout: int = 15) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "reason": "command_not_found", "error": str(exc), "cmd": cmd}
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "timeout",
            "cmd": cmd,
            "stdout": (exc.stdout or "")[:1000],
            "stderr": (exc.stderr or "")[:1000],
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "cmd": cmd,
        "stdout": (proc.stdout or "")[:2000],
        "stderr": (proc.stderr or "")[:2000],
    }


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _parse_json_lines(stdout: str) -> tuple[list[dict[str, Any]], list[str]]:
    messages: list[dict[str, Any]] = []
    polluted: list[str] = []
    for raw in (stdout or "").splitlines():
        text = raw.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            polluted.append(text[:500])
            continue
        if isinstance(item, dict):
            messages.append(item)
        else:
            polluted.append(text[:500])
    return messages, polluted


def _message_by_id(messages: list[dict[str, Any]], message_id: int) -> dict[str, Any] | None:
    for item in messages:
        if item.get("id") == message_id:
            return item
    return None


def _start_stdio_pumps(proc: subprocess.Popen[str]) -> tuple[list[str], list[str], queue.Queue[str | None]]:
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    stdout_queue: queue.Queue[str | None] = queue.Queue()

    def pump_stdout() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                stdout_lines.append(line)
                stdout_queue.put(line)
        except Exception:
            return
        finally:
            stdout_queue.put(None)

    def pump_stderr() -> None:
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_lines.append(line)
        except Exception:
            return

    threading.Thread(target=pump_stdout, daemon=True).start()
    threading.Thread(target=pump_stderr, daemon=True).start()
    return stdout_lines, stderr_lines, stdout_queue


def _send_stdio_message(proc: subprocess.Popen[str] | None, payload: dict[str, Any]) -> None:
    if proc is None or proc.stdin is None:
        raise BrokenPipeError("mcp stdin unavailable")
    proc.stdin.write(_json_line(payload))
    proc.stdin.flush()


def _wait_for_stdio_message_id(
    proc: subprocess.Popen[str] | None,
    stdout_queue: queue.Queue[str | None],
    stdout_lines: list[str],
    message_id: int,
    deadline: float,
) -> tuple[dict[str, Any] | None, str, list[str]]:
    stdout_text = ""
    polluted_stdout: list[str] = []
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        try:
            item = stdout_queue.get(timeout=min(0.25, remaining))
        except queue.Empty:
            if proc is not None and proc.poll() is not None:
                break
            continue
        if item is None:
            break
        stdout_text = "".join(stdout_lines)
        messages, polluted_stdout = _parse_json_lines(stdout_text)
        found = _message_by_id(messages, message_id)
        if found is not None:
            return found, stdout_text, polluted_stdout
    stdout_text = "".join(stdout_lines)
    messages, polluted_stdout = _parse_json_lines(stdout_text)
    return _message_by_id(messages, message_id), stdout_text, polluted_stdout


def _tool_names_from_list(message: dict[str, Any]) -> list[str]:
    result = message.get("result") if isinstance(message, dict) else {}
    tools = result.get("tools") if isinstance(result, dict) else []
    names: list[str] = []
    if isinstance(tools, list):
        for item in tools:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item.get("name")))
    return names


def _protocol_smoke_once(name: str, spec: McpSmokeSpec, timeout: int, attempt: int) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(spec.env)
    initialize_message: dict[str, Any] | None = None
    tools_message: dict[str, Any] | None = None
    stderr_text = ""
    stdout_text = ""
    polluted_stdout: list[str] = []
    reason = ""
    timed_out = False
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            list(spec.command),
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout_lines, stderr_lines, stdout_queue = _start_stdio_pumps(proc)
        deadline = time.monotonic() + max(2, timeout)
        _send_stdio_message(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "codex-mcp-session-smoke", "version": "1.0"},
                },
            }
        )
        initialize_message, stdout_text, polluted_stdout = _wait_for_stdio_message_id(
            proc,
            stdout_queue,
            stdout_lines,
            1,
            min(deadline, time.monotonic() + max(2.0, timeout * 0.45)),
        )
        if initialize_message is not None:
            _send_stdio_message(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            _send_stdio_message(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            tools_message, stdout_text, polluted_stdout = _wait_for_stdio_message_id(
                proc,
                stdout_queue,
                stdout_lines,
                2,
                deadline,
            )
        _close_stdio_process(proc)
        stdout_text = "".join(stdout_lines)
        stderr_text = "".join(stderr_lines)
        messages, polluted_stdout = _parse_json_lines(stdout_text or "")
        initialize_message = initialize_message or _message_by_id(messages, 1)
        tools_message = tools_message or _message_by_id(messages, 2)
        reason = _protocol_smoke_missing_response_reason(initialize_message, tools_message, polluted_stdout, reason)
    except FileNotFoundError as exc:
        reason = "command_not_found"
        stderr_text = str(exc)
    except (BrokenPipeError, OSError) as exc:
        reason = f"stdio_broken: {exc}"
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass

    return _protocol_smoke_result_payload(
        name=name,
        spec=spec,
        timeout=timeout,
        attempt=attempt,
        reason=reason,
        timed_out=timed_out,
        polluted_stdout=polluted_stdout,
        initialize_message=initialize_message,
        tools_message=tools_message,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
    )


def protocol_smoke(profile_name: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    name = str(profile_name or "").strip()
    if name in HTTP_MCP_SMOKE_PROFILES:
        return http_mcp_protocol_smoke(name, timeout_seconds=timeout_seconds)
    spec = SMOKE_SPECS.get(name)
    if not spec:
        return {
            "schema": "mcp_session.protocol_smoke.v1",
            "ok": False,
            "profile": name,
            "generated_at": now_iso(),
            "error": "smoke_spec_missing",
        }
    timeout = int(timeout_seconds or spec.timeout_seconds)
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, 4):
        result = _protocol_smoke_once(name, spec, timeout, attempt)
        attempts.append(result)
        if result.get("ok"):
            if attempt > 1:
                result = dict(result)
                result["recovered_after_attempts"] = attempt
                result["attempts"] = [
                    {
                        "attempt": item.get("attempt"),
                        "ok": item.get("ok"),
                        "reason": item.get("reason"),
                        "tool_names": item.get("tool_names"),
                        "missing_tools": item.get("missing_tools"),
                    }
                    for item in attempts
                ]
            return result
        if result.get("reason") not in {
            "tools_list_response_missing",
            "initialize_response_missing",
            "read_timeout",
            "expected_tools_missing",
        }:
            break
        time.sleep(0.35 * attempt)
    final = dict(attempts[-1])
    final["attempts"] = [
        {
            "attempt": item.get("attempt"),
            "ok": item.get("ok"),
            "reason": item.get("reason"),
            "tool_names": item.get("tool_names"),
            "missing_tools": item.get("missing_tools"),
        }
        for item in attempts
    ]
    return final


def http_mcp_protocol_smoke(profile_name: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    name = str(profile_name or "").strip()
    if name != "local-mcp-hub":
        return {
            "schema": "mcp_session.protocol_smoke.v1",
            "ok": False,
            "profile": name,
            "generated_at": now_iso(),
            "error": "http_smoke_spec_missing",
        }
    timeout = int(timeout_seconds or 8)
    command = ["python", "_bridge\\local_mcp_hub.py", "smoke", "--host", "127.0.0.1", "--port", "18881"]
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        result = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "cmd": command,
            "stdout": proc.stdout or "",
            "stderr": (proc.stderr or "")[:2000],
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "ok": False,
            "reason": "timeout",
            "cmd": command,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "")[:2000],
        }
    except FileNotFoundError as exc:
        result = {"ok": False, "reason": "command_not_found", "cmd": command, "stdout": "", "stderr": str(exc)}
    payload: dict[str, Any] = {}
    if result.get("ok"):
        try:
            parsed = json.loads(str(result.get("stdout") or "{}"))
            payload = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            payload = {}
    initialize = payload.get("initialize") if isinstance(payload.get("initialize"), dict) else {}
    tools = payload.get("tools") if isinstance(payload.get("tools"), dict) else {}
    tool_names = _tool_names_from_list(tools)
    expected_tools = [
        "slash.validate_registry",
        "sqlite_scratch.sqlite_health",
        "sqlite_bridge.sqlite_health",
        "pmb.workspace_info",
        "hub.validate",
        "mcp_session.validate",
    ]
    missing_tools = [tool for tool in expected_tools if tool not in tool_names]
    ok = bool(result.get("ok") and payload.get("ok") and not missing_tools)
    reason = ""
    if not result.get("ok"):
        reason = str(result.get("reason") or "http_mcp_smoke_command_failed")
    elif not payload:
        reason = "http_mcp_smoke_json_missing"
    elif missing_tools:
        reason = "expected_tools_missing"
    return {
        "schema": "mcp_session.protocol_smoke.v1",
        "ok": ok,
        "generated_at": now_iso(),
        "profile": name,
        "attempt": 1,
        "command": command,
        "timeout_seconds": timeout,
        "protocol_version": MCP_PROTOCOL_VERSION,
        "transport": "stateless_streamable_http",
        "expected_tools": expected_tools,
        "tool_names": tool_names,
        "missing_tools": missing_tools,
        "reason": reason,
        "timed_out": result.get("reason") == "timeout",
        "stdout_polluted": False,
        "polluted_stdout": [],
        "initialize": initialize,
        "tools_list": tools,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }


def protocol_smoke_summary(smoke: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(smoke, dict):
        return {}
    return {
        "schema": smoke.get("schema", "mcp_session.protocol_smoke.v1"),
        "ok": bool(smoke.get("ok")),
        "generated_at": smoke.get("generated_at", ""),
        "profile": smoke.get("profile", ""),
        "attempt": smoke.get("attempt"),
        "timeout_seconds": smoke.get("timeout_seconds"),
        "expected_tools": smoke.get("expected_tools", []),
        "tool_names": smoke.get("tool_names", []),
        "missing_tools": smoke.get("missing_tools", []),
        "reason": smoke.get("reason", ""),
        "timed_out": bool(smoke.get("timed_out")),
        "stdout_polluted": bool(smoke.get("stdout_polluted")),
        "polluted_stdout": smoke.get("polluted_stdout", []),
        "server_info": (
            ((smoke.get("initialize") or {}).get("result") or {}).get("serverInfo", {})
            if isinstance(smoke.get("initialize"), dict)
            else {}
        ),
        "recovered_after_attempts": smoke.get("recovered_after_attempts"),
        "attempts": smoke.get("attempts", []),
        "detail_note": "Full initialize/tools_list/stdout/stderr are available from `mcp-session smoke --profile <name>`.",
    }


def _tool_call_error_result(name: str, tool: str, attempt: int, error: str) -> dict[str, Any]:
    return {
        "schema": "mcp_session.tool_call.v1",
        "ok": False,
        "profile": name,
        "tool": tool,
        "generated_at": now_iso(),
        "attempt": attempt,
        "error": error,
    }


def _close_stdio_process(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except Exception:
            pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _tool_call_missing_response_reason(
    initialize_message: dict[str, Any] | None,
    call_message: dict[str, Any] | None,
    polluted_stdout: list[str],
) -> str:
    if initialize_message is None:
        return "initialize_response_missing"
    if call_message is None:
        return "tool_call_response_missing"
    if polluted_stdout:
        return "stdout_polluted_or_non_json"
    return ""


def _tool_call_result_payload(
    *,
    name: str,
    tool: str,
    attempt: int,
    command: list[str],
    timeout: int,
    reason: str,
    polluted_stdout: list[str],
    initialize_message: dict[str, Any] | None,
    call_message: dict[str, Any] | None,
    stdout_text: str,
    stderr_text: str,
) -> dict[str, Any]:
    result = call_message.get("result") if isinstance(call_message, dict) else {}
    call_error = call_message.get("error") if isinstance(call_message, dict) else None
    tool_result_is_error = bool(isinstance(result, dict) and result.get("isError"))
    ok = bool(initialize_message and call_message and not call_error and not reason and not tool_result_is_error)
    return {
        "schema": "mcp_session.tool_call.v1",
        "ok": ok,
        "generated_at": now_iso(),
        "profile": name,
        "tool": tool,
        "attempt": attempt,
        "command": command,
        "timeout_seconds": timeout,
        "reason": reason,
        "stdout_polluted": bool(polluted_stdout),
        "polluted_stdout": polluted_stdout[:5],
        "initialize": initialize_message,
        "tool_call": call_message,
        "result": result,
        "error": call_error,
        "tool_result_is_error": tool_result_is_error,
        "stdout": (stdout_text or "")[:2000],
        "stderr": (stderr_text or "")[:2000],
    }


def _protocol_smoke_missing_response_reason(
    initialize_message: dict[str, Any] | None,
    tools_message: dict[str, Any] | None,
    polluted_stdout: list[str],
    reason: str,
) -> str:
    if initialize_message is None and not reason:
        return "initialize_response_missing"
    if tools_message is None and not reason:
        return "tools_list_response_missing"
    if polluted_stdout and not reason:
        return "stdout_polluted_or_non_json"
    return reason


def _protocol_smoke_result_payload(
    *,
    name: str,
    spec: McpSmokeSpec,
    timeout: int,
    attempt: int,
    reason: str,
    timed_out: bool,
    polluted_stdout: list[str],
    initialize_message: dict[str, Any] | None,
    tools_message: dict[str, Any] | None,
    stdout_text: str,
    stderr_text: str,
) -> dict[str, Any]:
    tool_names = _tool_names_from_list(tools_message or {})
    missing_tools = [tool for tool in spec.expected_tools if tool not in tool_names]
    if reason == "read_timeout" and initialize_message and tools_message:
        reason = ""
    ok = bool(initialize_message and tools_message and not missing_tools and not reason)
    if missing_tools and not reason:
        reason = "expected_tools_missing"
    return {
        "schema": "mcp_session.protocol_smoke.v1",
        "ok": ok,
        "generated_at": now_iso(),
        "profile": name,
        "attempt": attempt,
        "command": list(spec.command),
        "timeout_seconds": timeout,
        "protocol_version": MCP_PROTOCOL_VERSION,
        "expected_tools": list(spec.expected_tools),
        "tool_names": tool_names,
        "missing_tools": missing_tools,
        "reason": reason,
        "timed_out": timed_out,
        "stdout_polluted": bool(polluted_stdout),
        "polluted_stdout": polluted_stdout[:5],
        "initialize": initialize_message,
        "tools_list": tools_message,
        "stdout": (stdout_text or "")[:2000],
        "stderr": (stderr_text or "")[:2000],
    }


def _send_initialize_message(proc: subprocess.Popen[str]) -> None:
    _send_stdio_message(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "codex-mcp-session-tool-call", "version": "1.0"},
            },
        }
    )


def _send_tool_call_message(proc: subprocess.Popen[str], tool: str, arguments: dict[str, Any] | None) -> None:
    _send_stdio_message(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    _send_stdio_message(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        }
    )


def _protocol_tool_call_exchange(
    *,
    proc: subprocess.Popen[str],
    stdout_lines: list[str],
    stdout_queue: Any,
    deadline: float,
    timeout: int,
    tool: str,
    arguments: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str, list[str]]:
    _send_initialize_message(proc)
    initialize_message, stdout_text, polluted_stdout = _wait_for_stdio_message_id(
        proc,
        stdout_queue,
        stdout_lines,
        1,
        min(deadline, time.monotonic() + max(2.0, timeout * 0.35)),
    )
    call_message = None
    if initialize_message is not None:
        _send_tool_call_message(proc, tool, arguments)
        call_message, stdout_text, polluted_stdout = _wait_for_stdio_message_id(
            proc,
            stdout_queue,
            stdout_lines,
            2,
            deadline,
        )
    return initialize_message, call_message, stdout_text, polluted_stdout


def _merge_protocol_tool_call_messages(
    *,
    stdout_text: str,
    polluted_stdout: list[str],
    initialize_message: dict[str, Any] | None,
    call_message: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    messages, parsed_polluted_stdout = _parse_json_lines(stdout_text or "")
    return (
        initialize_message or _message_by_id(messages, 1),
        call_message or _message_by_id(messages, 2),
        parsed_polluted_stdout or polluted_stdout,
    )


def _protocol_tool_call_once(
    profile_name: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Call one MCP tool through a fresh local stdio process.

    Use this only as a bounded fallback when the active Codex turn holds a
    stale/closed transport while the profile's local protocol smoke succeeds.
    """

    name = str(profile_name or "").strip()
    tool = str(tool_name or "").strip()
    spec = SMOKE_SPECS.get(name)
    if not spec:
        return _tool_call_error_result(name, tool, attempt, "smoke_spec_missing")
    if not tool:
        return _tool_call_error_result(name, tool, attempt, "tool_name_required")

    timeout = int(timeout_seconds or spec.timeout_seconds)
    env = os.environ.copy()
    env.update(spec.env)
    stdout_text = ""
    stderr_text = ""
    polluted_stdout: list[str] = []
    reason = ""
    initialize_message: dict[str, Any] | None = None
    call_message: dict[str, Any] | None = None
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            list(spec.command),
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout_lines, stderr_lines, stdout_queue = _start_stdio_pumps(proc)
        deadline = time.monotonic() + max(2, timeout)
        initialize_message, call_message, stdout_text, polluted_stdout = _protocol_tool_call_exchange(
            proc=proc,
            stdout_lines=stdout_lines,
            stdout_queue=stdout_queue,
            deadline=deadline,
            timeout=timeout,
            tool=tool,
            arguments=arguments,
        )
        _close_stdio_process(proc)
        stdout_text = "".join(stdout_lines)
        stderr_text = "".join(stderr_lines)
        initialize_message, call_message, polluted_stdout = _merge_protocol_tool_call_messages(
            stdout_text=stdout_text,
            polluted_stdout=polluted_stdout,
            initialize_message=initialize_message,
            call_message=call_message,
        )
        reason = _tool_call_missing_response_reason(initialize_message, call_message, polluted_stdout)
    except FileNotFoundError as exc:
        reason = "command_not_found"
        stderr_text = str(exc)
    except (BrokenPipeError, OSError) as exc:
        reason = f"stdio_broken: {exc}"
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        try:
            if proc is not None:
                proc.kill()
        except Exception:
            pass

    return _tool_call_result_payload(
        name=name,
        tool=tool,
        attempt=attempt,
        command=list(spec.command),
        timeout=timeout,
        reason=reason,
        polluted_stdout=polluted_stdout,
        initialize_message=initialize_message,
        call_message=call_message,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
    )


def protocol_tool_call(
    profile_name: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Call one MCP tool through fresh stdio with bounded startup retries."""

    attempts: list[dict[str, Any]] = []
    retryable_reasons = {
        "initialize_response_missing",
        "tool_call_response_missing",
    }
    for attempt in range(1, 3):
        result = _protocol_tool_call_once(
            profile_name,
            tool_name,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            attempt=attempt,
        )
        attempts.append(result)
        if result.get("ok"):
            if attempt > 1:
                result["recovered_after_attempts"] = attempt
                result["attempts"] = [
                    {
                        "attempt": item.get("attempt"),
                        "ok": item.get("ok"),
                        "reason": item.get("reason"),
                        "error": item.get("error"),
                    }
                    for item in attempts
                ]
            return result
        reason = str(result.get("reason") or result.get("error") or "")
        if not (
            reason in retryable_reasons
            or reason.startswith("stdio_broken:")
            or reason.startswith("TimeoutError:")
        ):
            break
        time.sleep(0.35 * attempt)

    final = dict(attempts[-1]) if attempts else {
        "schema": "mcp_session.tool_call.v1",
        "ok": False,
        "profile": str(profile_name or "").strip(),
        "tool": str(tool_name or "").strip(),
        "generated_at": now_iso(),
        "reason": "tool_call_not_attempted",
    }
    final["attempts"] = [
        {
            "attempt": item.get("attempt"),
            "ok": item.get("ok"),
            "reason": item.get("reason"),
            "error": item.get("error"),
        }
        for item in attempts
    ]
    return final


def profile_supports_protocol_smoke(name: Any) -> bool:
    normalized = str(name or "").strip()
    return normalized in SMOKE_SPECS or normalized in HTTP_MCP_SMOKE_PROFILES


def profile_by_name() -> dict[str, McpProfile]:
    items: dict[str, McpProfile] = {}
    for profile in MCP_PROFILES:
        items[profile.name] = profile
        items[profile.config_name] = profile
    return items


def smoke_exempt_reason(profile: McpProfile) -> str:
    if profile.name == "github":
        return "remote_http_mcp"
    if profile.name == "node_repl":
        return "bundled_persistent_runtime"
    return ""


def config_text() -> str:
    try:
        return CODEX_CONFIG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            parsed = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def config_has_profile(text: str, profile: McpProfile) -> bool:
    quoted = f'[mcp_servers."{profile.config_name}"]'
    bare = f"[mcp_servers.{profile.config_name}]"
    return quoted in text or bare in text


def resource_process_snapshot() -> dict[str, Any]:
    try:
        from resource_process_doctor import process_snapshot

        return process_snapshot()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "groups": []}


def group_by_name(snapshot: dict[str, Any], name: str) -> dict[str, Any]:
    for group in snapshot.get("groups", []) if isinstance(snapshot.get("groups"), list) else []:
        if isinstance(group, dict) and group.get("group") == name:
            return group
    return {}


def parse_observations(values: list[str] | None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for raw in values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        if ":" in text:
            name, status = text.split(":", 1)
        elif "=" in text:
            name, status = text.split("=", 1)
        else:
            name, status = "*", text
        result.setdefault(name.strip(), []).append(status.strip())
    return result


def _normalize_observation_status(status: str) -> str:
    text = str(status or "").strip().lower().replace(" ", "_").replace("-", "_")
    if text in {"protocolok", "protocol_ok", "smokeok", "smoke_ok", "toolslistok", "tools_list_ok", "initializeok", "initialize_ok"}:
        return "protocol_ok"
    if text in {"closed", "transportclosed", "mcp_closed", "mcp_transport_closed"}:
        return "transport_closed"
    if text in {"unsupported", "unsupportedcall", "unsupported_call", "toolunbound", "tool_unbound", "toolmissing", "tool_missing", "unknown_tool"}:
        return "tool_unbound"
    if text in {"schemamismatch", "schema_mismatch", "invalidschema", "invalid_schema", "protocolmismatch", "protocol_mismatch"}:
        return "schema_mismatch"
    if text in {
        "aborted",
        "abort",
        "hang",
        "hung",
        "timeout",
        "timedout",
        "timed_out",
        "cancelled",
        "canceled",
        "dispatchfailure",
        "dispatch_failure",
        "toolcallstalled",
        "tool_call_stalled",
        "toolsurfaceunstable",
        "tool_surface_unstable",
        "current_turn_tool_surface_unstable",
    }:
        return "tool_surface_unstable"
    if text in {
        "available",
        "ok",
        "healthy",
        "success",
        "succeeded",
        "currentturncallable",
        "current_turn_callable",
        "toolavailable",
        "tool_available",
        "session_tool_available",
        "tool_call_succeeded",
        "call_succeeded",
        "mcp_session_available",
    }:
        return "current_turn_callable"
    return text or "unknown"


def _observation_kind(status: str) -> str:
    normalized = _normalize_observation_status(status)
    if normalized in POSITIVE_OBSERVATION_STATUSES:
        return "positive"
    if normalized in PROTOCOL_OBSERVATION_STATUSES:
        return "protocol"
    if normalized in NEGATIVE_OBSERVATION_STATUSES:
        return "negative"
    return "other"


def _is_current_turn_source(source: str) -> bool:
    text = str(source or "").strip().lower()
    return bool(text and any(marker in text for marker in CURRENT_TURN_SOURCE_MARKERS))


def _is_current_session_source(source: str) -> bool:
    text = str(source or "").strip().lower()
    return bool(text and any(marker in text for marker in CURRENT_SESSION_SOURCE_MARKERS))


def _source_has_marker(source: str, markers: tuple[str, ...]) -> bool:
    text = str(source or "").strip().lower()
    return bool(text and any(marker in text for marker in markers))


def _validate_observation_record(profile: str, normalized_status: str, source: str) -> list[str]:
    reasons: list[str] = []
    if not str(profile or "").strip():
        reasons.append("profile_required")
    if not normalized_status or normalized_status == "unknown":
        reasons.append("status_required")
    if normalized_status in POSITIVE_OBSERVATION_STATUSES:
        if not _is_current_turn_source(source):
            reasons.append("current_turn_callable_requires_current_turn_real_tool_call")
        if _source_has_marker(source, NON_CALLABLE_EVIDENCE_SOURCE_MARKERS):
            reasons.append("non_callable_evidence_cannot_record_current_turn_callable")
    return reasons


def _build_observation_payload(profile: str, status: str, source: str = "", detail: str = "") -> dict[str, Any]:
    return {
        "schema": "mcp_session.observation.v1",
        "recorded_at": now_iso(),
        "profile": str(profile or "").strip(),
        "status": _normalize_observation_status(status),
        "kind": _observation_kind(status),
        "source": str(source or "").strip(),
        "detail": str(detail or "").strip()[:1000],
    }


def record_observation(profile: str, status: str, source: str = "", detail: str = "", dry_run: bool = False) -> dict[str, Any]:
    profile_name = str(profile or "").strip()
    normalized_status = _normalize_observation_status(status)
    reject_reasons = _validate_observation_record(profile_name, normalized_status, str(source or ""))
    if reject_reasons:
        return {
            "schema": "mcp_session.record_observation.v1",
            "ok": False,
            "error": reject_reasons[0],
            "reject_reasons": reject_reasons,
            "generated_at": now_iso(),
            "observation_log": str(OBSERVATION_LOG),
        }
    payload = _build_observation_payload(profile_name, normalized_status, source=source, detail=detail)
    if not dry_run:
        OBSERVATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with OBSERVATION_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {
        "schema": "mcp_session.record_observation.v1",
        "ok": True,
        "generated_at": now_iso(),
        "observation_log": str(OBSERVATION_LOG),
        "dry_run": bool(dry_run),
        "observation": payload,
    }


def record_observations(
    items: list[dict[str, Any]],
    *,
    default_source: str = "",
    default_status: str = "",
    default_detail: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    if not isinstance(items, list):
        return {
            "schema": "mcp_session.record_observations.v1",
            "ok": False,
            "error": "items_must_be_array",
            "generated_at": now_iso(),
            "observation_log": str(OBSERVATION_LOG),
        }
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            rejected.append({"index": index, "error": "item_must_be_object"})
            continue
        profile_name = str(item.get("profile") or "").strip()
        status = str(item.get("status") or default_status or "").strip()
        source = str(item.get("source") or default_source or "").strip()
        detail = str(item.get("detail") or default_detail or "").strip()
        normalized_status = _normalize_observation_status(status)
        reasons = _validate_observation_record(profile_name, normalized_status, source)
        dedupe_key = (profile_name, normalized_status, source, detail[:1000])
        if not reasons and dedupe_key in seen:
            skipped.append(
                {
                    "index": index,
                    "profile": profile_name,
                    "status": normalized_status,
                    "source": source,
                    "detail": detail[:1000],
                    "reason": "duplicate_in_batch",
                }
            )
            continue
        if reasons:
            rejected.append(
                {
                    "index": index,
                    "profile": profile_name,
                    "status": normalized_status,
                    "source": source,
                    "detail": detail[:1000],
                    "reject_reasons": reasons,
                }
            )
            continue
        seen.add(dedupe_key)
        accepted.append(_build_observation_payload(profile_name, normalized_status, source=source, detail=detail))
    if accepted and not dry_run:
        OBSERVATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with OBSERVATION_LOG.open("a", encoding="utf-8") as fh:
            for payload in accepted:
                fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {
        "schema": "mcp_session.record_observations.v1",
        "ok": not rejected,
        "generated_at": now_iso(),
        "observation_log": str(OBSERVATION_LOG),
        "dry_run": bool(dry_run),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "skipped_count": len(skipped),
        "observations": accepted,
        "rejected": rejected,
        "skipped": skipped,
    }


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _update_gateway_state(profile: str, patch: dict[str, Any]) -> dict[str, Any]:
    state = _read_json_file(GATEWAY_STATE_PATH, {"schema": "mcp_tool_gateway_state.v1", "profiles": {}})
    if not isinstance(state, dict):
        state = {"schema": "mcp_tool_gateway_state.v1", "profiles": {}}
    profiles = state.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}
        state["profiles"] = profiles
    row = profiles.setdefault(profile, {})
    if not isinstance(row, dict):
        row = {}
        profiles[profile] = row
    row.update(patch)
    row["updated_at"] = now_iso()
    state["updated_at"] = row["updated_at"]
    _write_json_file(GATEWAY_STATE_PATH, state)
    return state


def gateway_route(profile: str, tool: str = "") -> dict[str, Any]:
    name = str(profile or "").strip()
    route = "fresh_stdio"
    reason = "stable_default_for_local_mcp"
    smoke_supported = profile_supports_protocol_smoke(name)
    state = _read_json_file(GATEWAY_STATE_PATH, {"profiles": {}})
    profile_state = (state.get("profiles") or {}).get(name, {}) if isinstance(state, dict) else {}
    recent_observations = load_recent_observations(DEFAULT_OBSERVATION_MAX_AGE_MINUTES)
    profile_recent = [item for item in recent_observations if isinstance(item, dict) and item.get("profile") == name]
    latest_negative_at: datetime | None = None
    latest_positive_at: datetime | None = None
    for item in profile_recent:
        raw_at = str(item.get("recorded_at") or "")
        try:
            recorded_at = datetime.fromisoformat(raw_at)
        except ValueError:
            continue
        if item.get("kind") == "negative":
            if latest_negative_at is None or recorded_at > latest_negative_at:
                latest_negative_at = recorded_at
        elif item.get("kind") == "positive":
            if latest_positive_at is None or recorded_at > latest_positive_at:
                latest_positive_at = recorded_at
    active_negative = bool(latest_negative_at and not (latest_positive_at and latest_positive_at >= latest_negative_at))
    recent_negative = [item for item in profile_recent if item.get("kind") == "negative"]
    if active_negative:
        route = "fresh_stdio"
        reason = "current_turn_or_recent_negative_observation"
    if not smoke_supported:
        route = "configured_fallback"
        reason = "protocol_smoke_not_supported_by_local_doctor"
    return {
        "schema": "mcp_tool_gateway.route.v1",
        "ok": True,
        "generated_at": now_iso(),
        "profile": name,
        "tool": str(tool or "").strip(),
        "route": route,
        "reason": reason,
        "direct_current_turn_allowed": False,
        "fresh_stdio_supported": smoke_supported,
        "recent_negative_count": len(recent_negative),
        "active_negative": active_negative,
        "latest_negative_at": latest_negative_at.isoformat() if latest_negative_at else "",
        "latest_positive_at": latest_positive_at.isoformat() if latest_positive_at else "",
        "gateway_state": profile_state,
        "policy": {
            "direct_current_turn": "acceleration_only_after_explicit_current_turn_positive_probe",
            "fresh_stdio": "default_stable_path_for_supported_local_profiles",
            "failure": "record_observation_and_open_circuit_for_direct_path",
        },
    }


def gateway_call(profile: str, tool: str, arguments: dict[str, Any] | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    name = str(profile or "").strip()
    tool_name = str(tool or "").strip()
    route = gateway_route(name, tool_name)
    if route.get("route") != "fresh_stdio":
        payload = {
            "schema": "mcp_tool_gateway.call.v1",
            "ok": False,
            "generated_at": now_iso(),
            "profile": name,
            "tool": tool_name,
            "route": route,
            "error": "fresh_stdio_not_supported_for_profile",
            "manual_action": "Use the profile-specific fallback or add a protocol smoke/tool-call spec before using gateway-call.",
        }
        _update_gateway_state(name, {"last_gateway_ok": False, "last_route": route.get("route"), "last_error": payload["error"]})
        return payload
    result = protocol_tool_call(name, tool_name, arguments=arguments or {}, timeout_seconds=timeout_seconds)
    gateway_status = "gateway_tool_call_ok" if result.get("ok") else "gateway_tool_call_failed"
    detail = "" if result.get("ok") else str(result.get("reason") or result.get("error") or "unknown")
    _update_gateway_state(
        name,
        {
            "last_gateway_ok": bool(result.get("ok")),
            "last_route": "fresh_stdio",
            "last_tool": tool_name,
            "last_error": detail,
            "last_gateway_status": gateway_status,
            "last_call_transport": "fresh_stdio",
        },
    )
    return {
        "schema": "mcp_tool_gateway.call.v1",
        "ok": bool(result.get("ok")),
        "generated_at": now_iso(),
        "profile": name,
        "tool": tool_name,
        "route": route,
        "result": result,
        "gateway_status": gateway_status,
        "gateway_state_path": str(GATEWAY_STATE_PATH),
        "transport_isolated_from_current_turn": True,
        "observation_policy": "gateway calls do not record current_turn_callable; direct MCP calls must be observed separately",
    }


def _compact_gateway_call_evidence(call: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(call, dict):
        return None
    result = call.get("result") if isinstance(call.get("result"), dict) else {}
    tool_call = result.get("tool_call") if isinstance(result.get("tool_call"), dict) else {}
    tool_result = tool_call.get("result") if isinstance(tool_call.get("result"), dict) else {}
    content = tool_result.get("content") if isinstance(tool_result.get("content"), list) else []
    text_lengths = [len(str(item.get("text") or "")) for item in content if isinstance(item, dict)]
    return {
        "schema": "mcp_tool_gateway.call_evidence.v1",
        "ok": bool(call.get("ok")),
        "generated_at": call.get("generated_at", ""),
        "profile": call.get("profile", ""),
        "tool": call.get("tool", ""),
        "gateway_status": call.get("gateway_status", ""),
        "route": (call.get("route") or {}).get("route") if isinstance(call.get("route"), dict) else "",
        "result_ok": bool(result.get("ok")),
        "attempt": result.get("attempt"),
        "reason": result.get("reason") or result.get("error") or "",
        "tool_result_is_error": bool(result.get("tool_result_is_error")),
        "content_items": len(content),
        "content_text_bytes": sum(text_lengths),
        "gateway_state_path": call.get("gateway_state_path", ""),
        "transport_isolated_from_current_turn": bool(call.get("transport_isolated_from_current_turn")),
    }


def _hub_complete_route_status(reason: str, *, expected_before_cli: bool = True) -> dict[str, Any]:
    return hub_attempt_placeholder(reason, expected_before_local=expected_before_cli)


def complete_route_after_native_failure(
    profile: str,
    tool: str,
    *,
    status: str = "transport_closed",
    detail: str = "",
    arguments: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
    source: str = CURRENT_TURN_PROBE_SOURCE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Complete the bounded route after a native MCP handle fails in this turn."""
    name = str(profile or "").strip()
    tool_name = str(tool or "").strip()
    normalized_status = _normalize_observation_status(status or "transport_closed")
    if not name:
        return {
            "schema": "mcp_session.route_completion.v1",
            "ok": False,
            "generated_at": now_iso(),
            "error": "profile_required",
        }
    record = record_observation(
        name,
        normalized_status,
        source=source or CURRENT_TURN_PROBE_SOURCE,
        detail=detail or "native MCP call failed in active turn; completing bounded route",
        dry_run=dry_run,
    )
    hub_attempt = _hub_complete_route_status(
        "hub_direct_or_complete_route_unavailable_cli_fallback"
        if str(source or "").strip() != HUB_COMPLETE_ROUTE_SOURCE
        else "already_inside_hub_complete_route",
        expected_before_cli=str(source or "").strip() != HUB_COMPLETE_ROUTE_SOURCE,
    )
    route = gateway_route(name, tool_name)
    call: dict[str, Any] | None = None
    if tool_name and not dry_run and route.get("route") == "fresh_stdio":
        call = gateway_call(name, tool_name, arguments=arguments or {}, timeout_seconds=timeout_seconds)
    recover = recover_plan(name, status=normalized_status)
    same_boundary_blocker = ""
    if not tool_name:
        same_boundary_blocker = "tool_required_for_gateway_call"
    elif dry_run:
        same_boundary_blocker = "dry_run_no_gateway_call"
    elif route.get("route") != "fresh_stdio":
        same_boundary_blocker = "gateway_has_no_fresh_stdio_route"
    elif call and not call.get("ok"):
        same_boundary_blocker = str(call.get("error") or call.get("reason") or "gateway_call_failed")
    route_complete = bool(call and call.get("ok"))
    return {
        "schema": "mcp_session.route_completion.v1",
        "ok": route_complete or bool(same_boundary_blocker),
        "route_complete": route_complete,
        "generated_at": now_iso(),
        "profile": name,
        "tool": tool_name,
        "native_failure": {
            "status": normalized_status,
            "source": source or CURRENT_TURN_PROBE_SOURCE,
            "detail": detail or "native MCP call failed in active turn; completing bounded route",
        },
        "negative_observation": record,
        "hub_attempt": hub_attempt,
        "gateway_route": route,
        "gateway_call": _compact_gateway_call_evidence(call),
        "same_boundary_blocker": same_boundary_blocker,
        "fallback_commands": recover.get("fallback_commands") if isinstance(recover, dict) else [],
        "route_policy": route_policy(),
    }


def gateway_warmup(profiles: list[str], timeout_seconds: int | None = None) -> dict[str, Any]:
    probes = {
        "custom-slash-commands": ("slash.validate_registry", {}),
        "sqlite-scratch": ("sqlite_health", {}),
        "sqlite-bridge-ro": ("sqlite_health", {}),
    }
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        name = str(profile or "").strip()
        if not name:
            continue
        tool, args = probes.get(name, ("", {}))
        if not tool:
            rows.append(
                {
                    "profile": name,
                    "ok": False,
                    "skipped": True,
                    "reason": "no_gateway_warmup_probe_registered",
                    "route": gateway_route(name),
                }
            )
            continue
        call = gateway_call(name, tool, arguments=args, timeout_seconds=timeout_seconds)
        rows.append({"profile": name, "ok": bool(call.get("ok")), "tool": tool, "call": call})
    return {
        "schema": "mcp_tool_gateway.warmup.v1",
        "ok": all(row.get("ok") or row.get("skipped") for row in rows),
        "generated_at": now_iso(),
        "rows": rows,
        "state_path": str(GATEWAY_STATE_PATH),
    }


def gateway_state_summary() -> dict[str, Any]:
    state = _read_json_file(GATEWAY_STATE_PATH, {"schema": "mcp_tool_gateway_state.v1", "profiles": {}})
    profiles = state.get("profiles") if isinstance(state, dict) else {}
    if not isinstance(profiles, dict):
        profiles = {}
    rows = [item for item in profiles.values() if isinstance(item, dict)]
    return {
        "schema": "mcp_tool_gateway.summary.v1",
        "state_path": str(GATEWAY_STATE_PATH),
        "exists": GATEWAY_STATE_PATH.exists(),
        "profile_count": len(profiles),
        "last_ok_count": sum(1 for item in rows if item.get("last_gateway_ok") is True),
        "last_failed_count": sum(1 for item in rows if item.get("last_gateway_ok") is False),
        "updated_at": state.get("updated_at", "") if isinstance(state, dict) else "",
    }


def batch_recording_contract_check() -> dict[str, Any]:
    items = [
        {
            "profile": "codegraph",
            "status": "current_turn_callable",
            "source": CURRENT_TURN_PROBE_SOURCE,
            "detail": "active MCP tool call returned successfully",
        },
        {
            "profile": "microsoftdocs",
            "status": "protocol_ok",
            "source": "protocol-smoke",
            "detail": "initialize and tools/list succeeded",
        },
        {
            "profile": "sqlite-scratch",
            "status": "current_turn_callable",
            "source": "protocol-smoke",
            "detail": "negative regression case",
        },
        {
            "profile": "filesystem",
            "status": "current_turn_callable",
            "source": "fallback",
            "detail": "negative regression case",
        },
    ]
    payload = record_observations(items, dry_run=True)
    accepted = payload.get("observations") if isinstance(payload.get("observations"), list) else []
    rejected = payload.get("rejected") if isinstance(payload.get("rejected"), list) else []
    accepted_by_profile = {str(item.get("profile")): item for item in accepted if isinstance(item, dict)}
    rejected_by_profile = {str(item.get("profile")): item for item in rejected if isinstance(item, dict)}
    issues: list[str] = []
    if accepted_by_profile.get("codegraph", {}).get("status") != "current_turn_callable":
        issues.append("current_turn_callable_not_canonical_status")
    if accepted_by_profile.get("codegraph", {}).get("kind") != "positive":
        issues.append("current_turn_callable_not_positive")
    if accepted_by_profile.get("microsoftdocs", {}).get("kind") != "protocol":
        issues.append("protocol_ok_not_protocol_kind")
    if "sqlite-scratch" not in rejected_by_profile:
        issues.append("protocol_smoke_current_turn_callable_not_rejected")
    if "filesystem" not in rejected_by_profile:
        issues.append("fallback_current_turn_callable_not_rejected")
    if any(str(item.get("status")) == "protocol_ok" and item.get("kind") == "positive" for item in accepted if isinstance(item, dict)):
        issues.append("protocol_ok_misclassified_as_positive")
    return {
        "schema": "mcp_session.batch_recording_contract_check.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "issues": issues,
        "sample": payload,
    }


def route_completion_contract_check() -> dict[str, Any]:
    payload = complete_route_after_native_failure(
        "mobile-openclaw-bridge",
        "bridge.get_pending_batch",
        status="transport_closed",
        arguments={"thread_id": "contract-thread"},
        dry_run=True,
    )
    policy = payload.get("route_policy") if isinstance(payload.get("route_policy"), dict) else {}
    policy_check = mcp_route_contract_check(policy)
    policy_rules = policy.get("rules") if isinstance(policy.get("rules"), dict) else {}
    hub_attempt = payload.get("hub_attempt") if isinstance(payload.get("hub_attempt"), dict) else {}
    fallback_commands = payload.get("fallback_commands") if isinstance(payload.get("fallback_commands"), list) else []
    issues: list[str] = []
    issues.extend(str(issue) for issue in policy_check.get("issues", []))
    if payload.get("schema") != "mcp_session.route_completion.v1":
        issues.append("unexpected_route_completion_schema")
    if policy_rules.get("record_native_negative_before_fallback") is not True:
        issues.append("negative_observation_not_before_gateway")
    if policy_rules.get("hub_mcp_before_local_hub") is not True:
        issues.append("hub_mcp_not_required_before_local_hub")
    if policy_rules.get("direct_known_hub_tool_before_complete_route") is not True:
        issues.append("direct_known_hub_tool_not_preferred")
    if policy_rules.get("complete_route_is_diagnostic_or_dynamic_not_default_transit") is not True:
        issues.append("complete_route_role_not_diagnostic")
    if policy_rules.get("local_hub_cli_only_after_hub_mcp_unavailable_or_insufficient") is not True:
        issues.append("local_hub_cli_role_not_fallback")
    if policy_rules.get("permission_boundary") != "same_as_native_tool":
        issues.append("permission_boundary_not_preserved")
    if hub_attempt.get("schema") != "mcp_session.hub_mcp_attempt.v1":
        issues.append("missing_hub_mcp_attempt_evidence")
    if hub_attempt.get("attempted") is not False or hub_attempt.get("reason") != "hub_direct_or_complete_route_unavailable_cli_fallback":
        issues.append("cli_fallback_hub_unavailable_status_not_explicit")
    if hub_attempt.get("expected_before_local") is not True:
        issues.append("hub_mcp_not_expected_before_local")
    if not any("supplement-fallback get-pending-batch" in str(command) for command in fallback_commands):
        issues.append("missing_supplement_get_fallback_command")
    if not any("supplement-fallback ack-message" in str(command) for command in fallback_commands):
        issues.append("missing_supplement_ack_fallback_command")
    return {
        "schema": "mcp_session.route_completion_contract_check.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "issues": issues,
        "route_policy_contract": policy_check,
        "route_completion": payload,
        "assertion": "native MCP failure must try Hub MCP direct/gateway routes before local Hub CLI or owner CLI fallback",
    }


def _load_observation_items(items_json: str = "", items_file: str = "") -> tuple[list[dict[str, Any]], str]:
    source_text = str(items_json or "").strip()
    if items_file:
        try:
            source_text = Path(items_file).read_text(encoding="utf-8")
        except OSError as exc:
            return [], f"items_file_read_failed: {exc}"
    if not source_text:
        return [], "items_json_or_file_required"
    try:
        loaded = json.loads(source_text)
    except json.JSONDecodeError as exc:
        return [], f"items_json_invalid: {exc}"
    if not isinstance(loaded, list):
        return [], "items_must_be_array"
    return loaded, ""


def load_recent_observations(max_age_minutes: int = DEFAULT_OBSERVATION_MAX_AGE_MINUTES) -> list[dict[str, Any]]:
    if not OBSERVATION_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(max_age_minutes)))
    records: list[dict[str, Any]] = []
    try:
        lines = OBSERVATION_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines[-1000:]:
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        recorded_raw = str(item.get("recorded_at") or "")
        try:
            recorded_at = datetime.fromisoformat(recorded_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        if recorded_at < cutoff:
            continue
        profile = str(item.get("profile") or "").strip()
        status = _normalize_observation_status(str(item.get("status") or ""))
        if not profile or not status:
            continue
        records.append(
            {
                "recorded_at": recorded_at.isoformat(),
                "profile": profile,
                "status": status,
                "kind": _observation_kind(status),
                "source": str(item.get("source") or ""),
                "detail": str(item.get("detail") or ""),
            }
        )
    return records


def merge_observations(
    cli_observations: list[str] | None,
    persisted: list[dict[str, Any]],
) -> dict[str, list[str]]:
    result = parse_observations(cli_observations)
    for item in persisted:
        profile = str(item.get("profile") or "").strip()
        status = _normalize_observation_status(str(item.get("status") or ""))
        source = str(item.get("source") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if not profile or not status:
            continue
        rendered = status
        extra = " ".join(part for part in (source, detail) if part)
        if extra:
            rendered = f"{status} ({extra})"
        result.setdefault(profile, []).append(rendered)
    return result


def _set_latest_observation_value(target: dict[str, Any], key: str, when: datetime, value: Any) -> None:
    existing = _parse_iso_datetime(target.get(f"latest_{key}_at"))
    if existing is not None and existing > when:
        return
    target[f"latest_{key}_at"] = when.isoformat()
    target[f"latest_{key}"] = value


def _update_observation_state(
    state: dict[str, dict[str, Any]],
    profile: str,
    status: str,
    recorded_at: datetime,
    source: str = "",
    detail: str = "",
) -> None:
    profile_name = str(profile or "").strip()
    normalized_status = _normalize_observation_status(status)
    if not profile_name or not normalized_status:
        return
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    recorded_at = recorded_at.astimezone(timezone.utc)
    target = state.setdefault(profile_name, {"profile": profile_name, "counts": {}})
    target["counts"][normalized_status] = int(target["counts"].get(normalized_status) or 0) + 1
    event = {
        "recorded_at": recorded_at.isoformat(),
        "status": normalized_status,
        "kind": _observation_kind(normalized_status),
        "source": str(source or ""),
        "detail": str(detail or ""),
    }
    _set_latest_observation_value(target, "observation", recorded_at, event)
    kind = _observation_kind(normalized_status)
    if kind in {"positive", "negative"}:
        _set_latest_observation_value(target, kind, recorded_at, event)
    if kind == "protocol":
        _set_latest_observation_value(target, normalized_status, recorded_at, event)
    if normalized_status in NEGATIVE_OBSERVATION_STATUSES:
        _set_latest_observation_value(target, normalized_status, recorded_at, event)
    if _is_current_turn_source(source) or _is_current_session_source(source):
        target["current_turn_observed"] = True
        _set_latest_observation_value(target, "current_turn_observation", recorded_at, event)
        if kind in {"positive", "negative"}:
            _set_latest_observation_value(target, f"current_turn_{kind}", recorded_at, event)


def observation_state(
    cli_observations: list[str] | None,
    persisted: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    cli_recorded_at = datetime.now(timezone.utc)
    for profile, statuses in parse_observations(cli_observations).items():
        for status in statuses:
            _update_observation_state(state, profile, status, cli_recorded_at, source="cli")
    for item in persisted:
        profile = str(item.get("profile") or "").strip()
        status = _normalize_observation_status(str(item.get("status") or ""))
        recorded_at = _parse_iso_datetime(item.get("recorded_at"))
        if recorded_at is None:
            continue
        _update_observation_state(
            state,
            profile,
            status,
            recorded_at,
            source=str(item.get("source") or ""),
            detail=str(item.get("detail") or ""),
        )
    return state


def observation_state_after_anchor(
    cli_observations: list[str] | None,
    persisted: list[dict[str, Any]],
    anchor_at: datetime | None,
) -> dict[str, dict[str, Any]]:
    if anchor_at is None:
        return observation_state(cli_observations, persisted)
    state: dict[str, dict[str, Any]] = {}
    cli_recorded_at = datetime.now(timezone.utc)
    for profile, statuses in parse_observations(cli_observations).items():
        for status in statuses:
            _update_observation_state(state, profile, status, cli_recorded_at, source="cli")
    current_turn_grace_cutoff = datetime.now(timezone.utc) - timedelta(minutes=CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES)
    if anchor_at.tzinfo is None:
        anchor_at = anchor_at.replace(tzinfo=timezone.utc)
    effective_anchor_at = min(anchor_at.astimezone(timezone.utc), current_turn_grace_cutoff)
    for item in persisted:
        profile = str(item.get("profile") or "").strip()
        status = _normalize_observation_status(str(item.get("status") or ""))
        recorded_at = _parse_iso_datetime(item.get("recorded_at"))
        if recorded_at is None or recorded_at < effective_anchor_at:
            continue
        _update_observation_state(
            state,
            profile,
            status,
            recorded_at,
            source=str(item.get("source") or ""),
            detail=str(item.get("detail") or ""),
        )
    return state


def profile_observations(profile: McpProfile, observations: dict[str, list[str]]) -> list[str]:
    items = [
        *observations.get("*", []),
        *observations.get(profile.name, []),
        *observations.get(profile.config_name, []),
        *observations.get(profile.process_group, []),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def profile_observation_state(profile: McpProfile, state: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {"profiles": [], "counts": {}}
    seen_keys: set[str] = set()
    for key in ("*", profile.name, profile.config_name, profile.process_group):
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        item = state.get(key)
        if not isinstance(item, dict):
            continue
        merged["profiles"].append(key)
        for status, count in (item.get("counts") or {}).items():
            merged["counts"][status] = int(merged["counts"].get(status) or 0) + int(count or 0)
        for field in (
            "latest_observation",
            "latest_positive",
            "latest_protocol_ok",
            "latest_negative",
            "latest_current_turn_observation",
            "latest_current_turn_positive",
            "latest_current_turn_negative",
            "latest_transport_closed",
            "latest_tool_unbound",
            "latest_schema_mismatch",
            "latest_tool_surface_unstable",
        ):
            event = item.get(field)
            if not isinstance(event, dict):
                continue
            when = _parse_iso_datetime(event.get("recorded_at"))
            if when is None:
                continue
            existing = merged.get(field)
            existing_when = _parse_iso_datetime(existing.get("recorded_at")) if isinstance(existing, dict) else None
            if existing_when is None or when >= existing_when:
                merged[field] = event
    latest_positive = merged.get("latest_positive") if isinstance(merged.get("latest_positive"), dict) else {}
    latest_negative = merged.get("latest_negative") if isinstance(merged.get("latest_negative"), dict) else {}
    positive_at = _parse_iso_datetime(latest_positive.get("recorded_at"))
    negative_at = _parse_iso_datetime(latest_negative.get("recorded_at"))
    merged["latest_positive_at"] = positive_at.isoformat() if positive_at else ""
    merged["latest_negative_at"] = negative_at.isoformat() if negative_at else ""
    merged["negative_superseded_by_positive"] = bool(positive_at and negative_at and positive_at >= negative_at)
    current_positive = merged.get("latest_current_turn_positive") if isinstance(merged.get("latest_current_turn_positive"), dict) else {}
    current_negative = merged.get("latest_current_turn_negative") if isinstance(merged.get("latest_current_turn_negative"), dict) else {}
    current_positive_at = _parse_iso_datetime(current_positive.get("recorded_at"))
    current_negative_at = _parse_iso_datetime(current_negative.get("recorded_at"))
    merged["latest_current_turn_positive_at"] = current_positive_at.isoformat() if current_positive_at else ""
    merged["latest_current_turn_negative_at"] = current_negative_at.isoformat() if current_negative_at else ""
    merged["current_turn_observed"] = bool(current_positive_at or current_negative_at)
    return merged


def _observation_status_superseded(state: dict[str, Any], status: str) -> bool:
    latest_positive = state.get("latest_positive") if isinstance(state.get("latest_positive"), dict) else {}
    latest_negative = state.get(f"latest_{status}") if isinstance(state.get(f"latest_{status}"), dict) else {}
    positive_at = _parse_iso_datetime(latest_positive.get("recorded_at"))
    negative_at = _parse_iso_datetime(latest_negative.get("recorded_at"))
    return bool(positive_at and negative_at and positive_at >= negative_at)


def _current_turn_status_active(state: dict[str, Any], status: str) -> bool:
    event = state.get(f"latest_{status}") if isinstance(state.get(f"latest_{status}"), dict) else {}
    source = str(event.get("source") or "")
    if not event or not (_is_current_turn_source(source) or _is_current_session_source(source)):
        return False
    recorded_at = _parse_iso_datetime(event.get("recorded_at"))
    if not recorded_at:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES)
    if recorded_at < cutoff:
        return False
    latest_positive = state.get("latest_current_turn_positive") if isinstance(state.get("latest_current_turn_positive"), dict) else {}
    positive_at = _parse_iso_datetime(latest_positive.get("recorded_at"))
    if positive_at and positive_at >= recorded_at:
        return False
    return True


def _current_turn_callable_state(state: dict[str, Any], anchor_at: datetime | None = None) -> dict[str, Any]:
    latest_positive = state.get("latest_current_turn_positive") if isinstance(state.get("latest_current_turn_positive"), dict) else {}
    latest_negative = state.get("latest_current_turn_negative") if isinstance(state.get("latest_current_turn_negative"), dict) else {}
    positive_at = _parse_iso_datetime(latest_positive.get("recorded_at"))
    negative_at = _parse_iso_datetime(latest_negative.get("recorded_at"))
    grace_cutoff = datetime.now(timezone.utc) - timedelta(minutes=CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES)
    if anchor_at is not None and anchor_at.tzinfo is None:
        anchor_at = anchor_at.replace(tzinfo=timezone.utc)
    cutoff = min(anchor_at.astimezone(timezone.utc), grace_cutoff) if anchor_at else grace_cutoff
    if positive_at and positive_at < cutoff:
        positive_at = None
        latest_positive = {}
    if negative_at and negative_at < cutoff:
        negative_at = None
        latest_negative = {}
    if positive_at and (not negative_at or positive_at >= negative_at):
        return {
            "state": "ok",
            "callable": True,
            "evidence": latest_positive,
            "note": "Current Codex turn has a positive tool-call observation for this profile.",
        }
    if negative_at:
        return {
            "state": "unavailable",
            "callable": False,
            "evidence": latest_negative,
            "note": "Current Codex turn has a negative tool-call/binding observation for this profile.",
        }
    return {
        "state": "unverified",
        "callable": None,
        "evidence": {},
        "note": f"Service/config checks do not prove this active model turn can call the MCP tool; current-turn observations expire after {CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES} minutes.",
        "anchor_at": cutoff.isoformat(),
    }


def _mcp_readiness_layers(
    *,
    configured: bool,
    service_protocol_state: str,
    current_turn_callable: dict[str, Any],
    transport_closed: bool,
    tool_binding_issue: bool,
    schema_mismatch: bool,
    tool_surface_unstable: bool,
) -> dict[str, Any]:
    """Five-layer MCP readiness model; later layers never imply earlier ones."""
    current_state = str(current_turn_callable.get("state") or "unverified")
    callable_value = current_turn_callable.get("callable")
    evidence = current_turn_callable.get("evidence") if isinstance(current_turn_callable.get("evidence"), dict) else {}
    call_completed = bool(callable_value is True and evidence)
    return {
        "config_ok": bool(configured),
        "protocol_ok": True if service_protocol_state == "ok" else False if service_protocol_state == "unready" else None,
        "current_turn_exposed": (
            False
            if tool_binding_issue
            else True
            if current_state in {"ok", "unavailable", "unstable"} or bool(evidence)
            else None
        ),
        "current_turn_callable": callable_value,
        "call_completed": call_completed,
        "failed_layer": (
            "config"
            if not configured
            else "protocol"
            if service_protocol_state == "unready"
            else "current_turn_transport"
            if transport_closed
            else "current_turn_binding"
            if tool_binding_issue
            else "schema"
            if schema_mismatch
            else "current_turn_call"
            if tool_surface_unstable or callable_value is False
            else "current_turn_probe"
            if callable_value is None
            else ""
        ),
        "rule": "Only call_completed=true may be reported as current-turn usable; config/protocol/discovery evidence is lower-layer evidence only.",
    }


def has_transport_closed_observation(items: list[str]) -> bool:
    lowered = " ".join(items).lower()
    return any(marker in lowered for marker in TRANSPORT_CLOSED_MARKERS)


def has_tool_binding_observation(items: list[str]) -> bool:
    lowered = " ".join(items).lower()
    return any(marker in lowered for marker in TOOL_BINDING_MARKERS)


def has_schema_mismatch_observation(items: list[str]) -> bool:
    lowered = " ".join(items).lower()
    return any(marker in lowered for marker in SCHEMA_MISMATCH_MARKERS)


def has_tool_surface_unstable_observation(items: list[str]) -> bool:
    lowered = " ".join(items).lower()
    return any(marker in lowered for marker in TOOL_SURFACE_UNSTABLE_MARKERS)


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _pid_alive(pid: Any) -> bool:
    return _shared_process_is_alive(pid)


def guard_lock_snapshot(profile: McpProfile) -> dict[str, Any]:
    if not profile.guard_profile:
        return {"configured": False, "present": False}
    path = MCP_GUARD_LOCK_DIR / f"{profile.guard_profile}.lock.json"
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            return {
                "configured": True,
                "present": True,
                "path": str(path),
                "parse_ok": False,
                "error": str(exc),
            }
    else:
        return {"configured": True, "present": False, "path": str(path)}
    started = _parse_iso_datetime(payload.get("started_at"))
    age_seconds = None
    if started:
        age_seconds = max(0.0, (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())
    lock_scope = str(payload.get("lock_scope") or "")
    stale = bool(age_seconds is not None and age_seconds > MCP_GUARD_LOCK_MAX_AGE_SECONDS)
    legacy_lifecycle_lock = bool(lock_scope != "prelaunch")
    return {
        "configured": True,
        "present": True,
        "path": str(path),
        "parse_ok": True,
        "lock_scope": lock_scope,
        "guard_pid": payload.get("guard_pid"),
        "guard_pid_alive": _pid_alive(payload.get("guard_pid")),
        "started_at": payload.get("started_at"),
        "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "stale": stale,
        "legacy_lifecycle_lock": legacy_lifecycle_lock,
        "command": payload.get("command"),
    }


def fallback_work_commands(profile: McpProfile) -> list[str]:
    if profile.name == "codegraph":
        return [
            r"python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py codegraph-fallback explore --max-files 4 <query>"
        ]
    if profile.name == "custom-slash-commands":
        return [
            r"python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session tool-call --profile custom-slash-commands --tool slash.validate_registry",
            'python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session tool-call --profile custom-slash-commands --tool slash.render_command --arguments-json \'{"name":"<command>","variables":{}}\'',
        ]
    if profile.name == "sqlite-scratch":
        return [
            r"python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session tool-call --profile sqlite-scratch --tool sqlite_health",
            'python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session tool-call --profile sqlite-scratch --tool sqlite_insert_record --arguments-json \'{"table":"<table>","record":{}}\'',
            'python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session tool-call --profile sqlite-scratch --tool sqlite_upsert_record --arguments-json \'{"table":"<table>","key_columns":["<key>"],"record":{}}\'',
        ]
    if profile.name == "sqlite-bridge-ro":
        return [
            r"python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py mcp-session tool-call --profile sqlite-bridge-ro --tool sqlite_health",
            'python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session tool-call --profile sqlite-bridge-ro --tool sqlite_query --arguments-json \'{"sql":"SELECT name FROM sqlite_master WHERE type=\\"table\\" LIMIT 20","limit":20}\'',
        ]
    if profile.name == "local-mcp-hub":
        return [
            r"python _bridge\local_mcp_hub.py smoke --host 127.0.0.1 --port 18881",
            r"Invoke-RestMethod http://127.0.0.1:18881/validate",
        ]
    if profile.name == "mobile-openclaw-bridge":
        return [
            r"python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback get-pending-batch --thread-id <thread_id>",
            r"python _bridge\mobile_openclaw_bridge\mobile_openclaw_cli.py supplement-fallback ack-message --thread-id <thread_id> --message-id <message_id>",
        ]
    if profile.name == "desktop-weixin":
        return [
            r"python _bridge\mcp_session_doctor.py smoke --profile desktop-weixin",
            r"python _bridge\mcp_session_doctor.py tool-call --profile desktop-weixin --tool desktop_weixin.capabilities",
            r"cli-anything-weixin --json status",
        ]
    if profile.fallback_command:
        return [profile.fallback_command]
    return []


def fallback_probe(profile: McpProfile, run_fallback: bool = False) -> dict[str, Any]:
    work_commands = fallback_work_commands(profile)
    if not profile.fallback_command:
        return {
            "available": profile.fallback != "none",
            "ran": False,
            "ok": None,
            "work_commands": work_commands,
        }
    if not run_fallback:
        return {
            "available": True,
            "ran": False,
            "ok": None,
            "command": profile.fallback_command,
            "health_command": profile.fallback_command,
            "work_commands": work_commands,
            "reason": "skipped_by_default",
        }
    if profile.name == "codegraph":
        cmd = [str(ROOT / "_bridge" / "tools" / "codegraph" / "node_modules" / ".bin" / "codegraph.cmd"), "status", ".", "--json"]
    elif profile.name == "mobile-openclaw-bridge":
        cmd = ["python", "_bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py", "supplement-fallback", "health"]
    elif profile.name == "local-pmb-memory":
        cmd = ["python", "_bridge\\local_pmb_memory.py", "daemon-status"]
    elif profile.name == "local-mcp-hub":
        cmd = ["python", "_bridge\\local_mcp_hub.py", "smoke", "--host", "127.0.0.1", "--port", "18881"]
    else:
        return {
            "available": True,
            "ran": False,
            "ok": None,
            "command": profile.fallback_command,
            "health_command": profile.fallback_command,
            "work_commands": work_commands,
            "reason": "fallback_probe_not_implemented_for_profile",
        }
    result = run_cmd(cmd, timeout=20)
    return {
        "available": True,
        "ran": True,
        "ok": bool(result.get("ok")),
        "command": profile.fallback_command,
        "health_command": profile.fallback_command,
        "work_commands": work_commands,
        "result": result,
    }


def current_turn_probe_plan(profile: McpProfile, configured: bool, retired: bool, service_ok: bool) -> dict[str, Any]:
    probe = dict(CURRENT_TURN_PROBES.get(profile.name, {}))
    if retired:
        return {"required": False, "reason": "retired_profile"}
    if not configured:
        return {"required": False, "reason": "profile_not_configured"}
    if not probe:
        return {
            "required": False,
            "reason": "probe_not_defined_for_profile",
            "record_negative_command": (
                f"python _bridge\\mcp_session_doctor.py record-observation --profile {profile.name} "
                f"--status tool_unbound --source {CURRENT_TURN_PROBE_SOURCE} "
                "\"--detail active turn could not call this MCP profile\""
            ),
        }
    probe["required"] = bool(service_ok)
    probe["reason"] = "service_ok_requires_current_turn_probe" if service_ok else "run_protocol_smoke_first"
    probe["source"] = CURRENT_TURN_PROBE_SOURCE
    return probe


def _profile_protocol_state(
    smoke: dict[str, Any],
    smoke_ran: bool,
    smoke_ok: bool | None,
    smoke_supported: bool,
) -> dict[str, Any]:
    smoke_missing = bool(smoke_ran and smoke.get("error") == "smoke_spec_missing")
    service_protocol_state = (
        "ok"
        if smoke_ok is True
        else "unsupported"
        if smoke_missing or (not smoke_supported and not smoke_ran)
        else "unready"
        if smoke_ok is False
        else "unverified"
    )
    protocol_unready = (
        smoke_ran
        and not smoke_ok
        and not smoke_missing
        and smoke.get("reason") not in {"expected_tools_missing"}
    )
    expected_tool_missing = smoke_ran and bool(smoke.get("missing_tools"))
    return {
        "service_protocol_state": service_protocol_state,
        "protocol_unready": protocol_unready,
        "expected_tool_missing": expected_tool_missing,
    }


def _profile_config_process_issues(
    *,
    profile_name: str,
    configured: bool,
    retired: bool,
    process_group: str,
    process_present: bool,
    smoke_ok: bool | None,
    current_turn_callable: dict[str, Any],
) -> list[dict[str, str]]:
    issues = profile_registration_issues(
        profile_name=profile_name,
        configured=configured,
        process_present=process_present,
        retired=retired,
        platform_scope=runtime_platform(),
    )
    if (
        configured
        and profile_name not in HUB_MANAGED_MCP_NAMES
        and bool(process_group)
        and not process_present
        and not smoke_ok
        and not retired
        and current_turn_callable.get("callable") is not True
    ):
        issues.append({"severity": "advisory", "code": "mcp_process_not_observed", "message": "No matching local MCP process is currently observed."})
    return issues


def _profile_surface_issues(
    *,
    configured: bool,
    retired: bool,
    smoke_ok: bool | None,
    current_turn_callable: dict[str, Any],
    session_surface_missing: bool,
    fallback_tool_call_ok: bool,
) -> list[dict[str, str]]:
    if session_surface_missing and fallback_tool_call_ok:
        return [
            {
                "severity": "advisory",
                "code": "mcp_native_turn_unbound_fallback_available",
                "message": "Native current-turn MCP namespace is unbound, but a same-boundary fresh-stdio route succeeded; keep functionality through the profile's classified execution affinity and do not record fallback success as native callability.",
            }
        ]
    if session_surface_missing:
        return [
            {
                "severity": "risk",
                "code": "mcp_session_surface_missing_or_stale",
                "message": "MCP server initializes and lists expected tools, but the active Codex session did not expose or bind the tool.",
            }
        ]
    if configured and smoke_ok and current_turn_callable.get("state") == "unverified" and not retired:
        return [
            {
                "severity": "advisory",
                "code": "mcp_current_turn_callable_unverified",
                "message": "MCP server smoke check passed, but that does not prove this active Codex turn exposes the tool.",
            }
        ]
    return []


def _profile_transport_binding_issues(
    *,
    session_surface_missing: bool,
    fallback_tool_call_ok: bool,
    transport_closed: bool,
    tool_binding_issue: bool,
    schema_mismatch: bool,
    tool_surface_unstable: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if transport_closed and not session_surface_missing:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_session_transport_closed",
                "message": "Current Codex session reported a closed MCP transport; verify the MCP server with protocol smoke before blaming the service.",
            }
        )
    if tool_binding_issue and not session_surface_missing and fallback_tool_call_ok:
        issues.append(
            {
                "severity": "advisory",
                "code": "mcp_session_tool_unbound_fallback_available",
                "message": "Current Codex session could not bind the native MCP namespace, but a fresh stdio/fallback tool call succeeded.",
            }
        )
    elif tool_binding_issue and not session_surface_missing:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_session_tool_unbound",
                "message": "Current Codex session could not bind or dispatch the MCP tool call; this is a session tool-surface fault unless protocol smoke also fails.",
            }
        )
    if schema_mismatch:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_session_schema_mismatch",
                "message": "Current Codex session reported an MCP tool schema/protocol mismatch.",
            }
        )
    if tool_surface_unstable:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_current_turn_tool_surface_unstable",
                "message": "Current Codex turn reported an aborted, hung, timed-out, or cancelled MCP tool call. Stop probing this tool path in the current turn and use the bounded fallback.",
            }
        )
    return issues


def _profile_guard_issues(guard_lock: dict[str, Any]) -> list[dict[str, str]]:
    if guard_lock.get("legacy_lifecycle_lock"):
        return [
            {
                "severity": "risk",
                "code": "mcp_stdio_guard_lifecycle_lock",
                "message": "MCP launch guard has a legacy lifecycle lock; stdio sessions need a fresh process per client and cannot reuse this lock.",
            }
        ]
    if guard_lock.get("present") and guard_lock.get("stale"):
        return [
            {
                "severity": "risk",
                "code": "mcp_stdio_guard_stale_prelaunch_lock",
                "message": "MCP launch guard prelaunch lock is stale and may block session tool startup.",
            }
        ]
    return []


def _profile_protocol_issues(
    *,
    configured: bool,
    retired: bool,
    current_turn_callable: dict[str, Any],
    protocol_unready: bool,
    smoke_supported: bool,
    expected_tool_missing: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if protocol_unready:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_protocol_unready",
                "message": "MCP server did not complete initialize/tools-list smoke check.",
            }
        )
    if configured and not smoke_supported and not retired and current_turn_callable.get("callable") is not True:
        issues.append(
            {
                "severity": "advisory",
                "code": "mcp_protocol_smoke_unsupported_by_local_doctor",
                "message": "This profile has no local protocol-smoke spec; use current-turn tool exposure or an external fallback instead of treating service health as proven.",
            }
        )
    if expected_tool_missing:
        issues.append(
            {
                "severity": "risk",
                "code": "mcp_tools_list_missing_expected_tool",
                "message": "MCP tools/list did not include one or more expected tools.",
            }
        )
    return issues


def _profile_observation_context(
    *,
    profile: McpProfile,
    observations: dict[str, list[str]],
    observation_states: dict[str, dict[str, Any]] | None,
    anchor: dict[str, Any] | None,
) -> dict[str, Any]:
    obs = profile_observations(profile, observations)
    obs_state = profile_observation_state(profile, observation_states or {})
    current_turn_callable = _current_turn_callable_state(
        obs_state,
        _parse_iso_datetime(anchor.get("anchor_at")) if isinstance(anchor, dict) else None,
    )
    latest_protocol = obs_state.get("latest_protocol_ok") if isinstance(obs_state.get("latest_protocol_ok"), dict) else {}
    protocol_source = str(latest_protocol.get("source") or "").lower()
    return {
        "observations": obs,
        "observation_state": obs_state,
        "current_turn_callable": current_turn_callable,
        "fallback_tool_call_ok": bool(latest_protocol and any(marker in protocol_source for marker in ("fallback", "fresh-stdio"))),
        "transport_closed": _current_turn_status_active(obs_state, "transport_closed"),
        "tool_binding_issue": _current_turn_status_active(obs_state, "tool_unbound"),
        "schema_mismatch": _current_turn_status_active(obs_state, "schema_mismatch"),
        "tool_surface_unstable": _current_turn_status_active(obs_state, "tool_surface_unstable"),
        "superseded_observation": bool(obs_state.get("negative_superseded_by_positive")),
    }


def _profile_protocol_context(
    *,
    profile: McpProfile,
    configured: bool,
    smoke_results: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    smoke = (smoke_results or {}).get(profile.name, {})
    smoke_ran = bool(smoke)
    smoke_ok = bool(smoke.get("ok")) if smoke_ran else None
    smoke_supported = profile_supports_protocol_smoke(profile.name)
    protocol_state = _profile_protocol_state(smoke, smoke_ran, smoke_ok, smoke_supported)
    protocol_unready = protocol_state["protocol_unready"]
    expected_tool_missing = protocol_state["expected_tool_missing"]
    return {
        "smoke": smoke,
        "smoke_ran": smoke_ran,
        "smoke_ok": smoke_ok,
        "smoke_supported": smoke_supported,
        "service_protocol_state": protocol_state["service_protocol_state"],
        "protocol_unready": protocol_unready,
        "expected_tool_missing": expected_tool_missing,
        "service_ok": bool(configured and smoke_ok is True and not protocol_unready and not expected_tool_missing),
    }


def _profile_session_surface_missing(
    *,
    configured: bool,
    smoke_ok: bool | None,
    current_turn_callable: dict[str, Any],
    transport_closed: bool,
    tool_binding_issue: bool,
    protocol_unready: bool,
    expected_tool_missing: bool,
) -> bool:
    return bool(
        configured
        and smoke_ok
        and (transport_closed or tool_binding_issue or current_turn_callable.get("callable") is False)
        and not protocol_unready
        and not expected_tool_missing
    )


def _profile_fallback_role(profile: McpProfile) -> str:
    if profile.transport_topology in {"daemon_backed_stdio_proxy", "local_stateless_stdio", "external_stateless_stdio", "external_stateless_stdio_elevated"}:
        return "functional_continuity_after_native_transport_failure"
    return "profile_specific_fallback"


def _profile_status(
    *,
    issues: list[dict[str, str]],
    fallback: dict[str, Any],
    transport_closed: bool,
    tool_binding_issue: bool,
    schema_mismatch: bool,
    tool_surface_unstable: bool,
    protocol_unready: bool,
    expected_tool_missing: bool,
) -> str:
    status = "ok"
    if any(item["severity"] == "risk" for item in issues):
        status = "risk"
    elif issues:
        status = "degraded"
    if (
        transport_closed
        or tool_binding_issue
        or schema_mismatch
        or tool_surface_unstable
        or protocol_unready
        or expected_tool_missing
    ) and fallback.get("available") and fallback.get("ok") is not False:
        return "risk_fallback_available" if status == "risk" else "fallback_available"
    return status


def _profile_issue_list(
    *,
    profile_name: str,
    configured: bool,
    retired: bool,
    process_group: str,
    process_present: bool,
    smoke_ok: bool | None,
    current_turn_callable: dict[str, Any],
    session_surface_missing: bool,
    fallback_tool_call_ok: bool,
    transport_closed: bool,
    tool_binding_issue: bool,
    schema_mismatch: bool,
    tool_surface_unstable: bool,
    guard_lock: dict[str, Any],
    protocol_unready: bool,
    smoke_supported: bool,
    expected_tool_missing: bool,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    issues.extend(
        _profile_config_process_issues(
            profile_name=profile_name,
            configured=configured,
            retired=retired,
            process_group=process_group,
            process_present=process_present,
            smoke_ok=smoke_ok,
            current_turn_callable=current_turn_callable,
        )
    )
    issues.extend(
        _profile_surface_issues(
            configured=configured,
            retired=retired,
            smoke_ok=smoke_ok,
            current_turn_callable=current_turn_callable,
            session_surface_missing=session_surface_missing,
            fallback_tool_call_ok=fallback_tool_call_ok,
        )
    )
    issues.extend(
        _profile_transport_binding_issues(
            session_surface_missing=session_surface_missing,
            fallback_tool_call_ok=fallback_tool_call_ok,
            transport_closed=transport_closed,
            tool_binding_issue=tool_binding_issue,
            schema_mismatch=schema_mismatch,
            tool_surface_unstable=tool_surface_unstable,
        )
    )
    issues.extend(_profile_guard_issues(guard_lock))
    issues.extend(
        _profile_protocol_issues(
            configured=configured,
            retired=retired,
            current_turn_callable=current_turn_callable,
            protocol_unready=protocol_unready,
            smoke_supported=smoke_supported,
            expected_tool_missing=expected_tool_missing,
        )
    )
    return issues


def classify_profile(
    profile: McpProfile,
    config: str,
    process_snapshot_payload: dict[str, Any],
    observations: dict[str, list[str]],
    observation_states: dict[str, dict[str, Any]] | None = None,
    anchor: dict[str, Any] | None = None,
    run_fallback: bool = False,
    smoke_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    retired = profile.name in RETIRED_PROFILES
    group = group_by_name(process_snapshot_payload, profile.process_group)
    configured = config_has_profile(config, profile)
    process_present = bool((group.get("root_instance_count") or 0) > 0 or (group.get("count") or 0) > 0)
    observation_context = _profile_observation_context(
        profile=profile,
        observations=observations,
        observation_states=observation_states,
        anchor=anchor,
    )
    guard_lock = guard_lock_snapshot(profile)
    fallback = fallback_probe(profile, run_fallback=run_fallback)
    protocol_context = _profile_protocol_context(
        profile=profile,
        configured=configured,
        smoke_results=smoke_results,
    )
    session_surface_missing = _profile_session_surface_missing(
        configured=configured,
        smoke_ok=protocol_context["smoke_ok"],
        current_turn_callable=observation_context["current_turn_callable"],
        transport_closed=observation_context["transport_closed"],
        tool_binding_issue=observation_context["tool_binding_issue"],
        protocol_unready=protocol_context["protocol_unready"],
        expected_tool_missing=protocol_context["expected_tool_missing"],
    )
    readiness_layers = _mcp_readiness_layers(
        configured=configured,
        service_protocol_state=protocol_context["service_protocol_state"],
        current_turn_callable=observation_context["current_turn_callable"],
        transport_closed=observation_context["transport_closed"],
        tool_binding_issue=observation_context["tool_binding_issue"],
        schema_mismatch=observation_context["schema_mismatch"],
        tool_surface_unstable=observation_context["tool_surface_unstable"],
    )
    affinity = profile_execution_affinity(profile)
    native_mcp_preferred = bool(configured and not retired and affinity.get("execution_affinity") in {"native_first", "session_native_first"})
    fallback_role = _profile_fallback_role(profile)

    issues = _profile_issue_list(
        profile_name=profile.name,
        configured=configured,
        retired=retired,
        process_group=profile.process_group,
        process_present=process_present,
        smoke_ok=protocol_context["smoke_ok"],
        current_turn_callable=observation_context["current_turn_callable"],
        session_surface_missing=session_surface_missing,
        fallback_tool_call_ok=observation_context["fallback_tool_call_ok"],
        transport_closed=observation_context["transport_closed"],
        tool_binding_issue=observation_context["tool_binding_issue"],
        schema_mismatch=observation_context["schema_mismatch"],
        tool_surface_unstable=observation_context["tool_surface_unstable"],
        guard_lock=guard_lock,
        protocol_unready=protocol_context["protocol_unready"],
        smoke_supported=protocol_context["smoke_supported"],
        expected_tool_missing=protocol_context["expected_tool_missing"],
    )
    status = _profile_status(
        issues=issues,
        fallback=fallback,
        transport_closed=observation_context["transport_closed"],
        tool_binding_issue=observation_context["tool_binding_issue"],
        schema_mismatch=observation_context["schema_mismatch"],
        tool_surface_unstable=observation_context["tool_surface_unstable"],
        protocol_unready=protocol_context["protocol_unready"],
        expected_tool_missing=protocol_context["expected_tool_missing"],
    )

    return {
        "name": profile.name,
        "config_name": profile.config_name,
        "retired": retired,
        "process_group": profile.process_group,
        "protected": profile.protected,
        "configured": configured,
        "process_present": process_present,
        "process_group_snapshot": group,
        "guard_lock": guard_lock,
        "observations": observation_context["observations"],
        "observation_state": observation_context["observation_state"],
        "stale_negative_observation_superseded": observation_context["superseded_observation"],
        "service_protocol_state": protocol_context["service_protocol_state"],
        "service_ok": protocol_context["service_ok"],
        "tool_tier": profile_tier(profile),
        "fallback_tool_call_ok": observation_context["fallback_tool_call_ok"],
        "readiness_layers": readiness_layers,
        "current_turn_callable": observation_context["current_turn_callable"],
        "current_turn_probe_plan": current_turn_probe_plan(profile, configured, retired, protocol_context["service_ok"]),
        "transport_closed_observed": observation_context["transport_closed"],
        "tool_binding_issue_observed": observation_context["tool_binding_issue"],
        "session_surface_missing_or_stale": session_surface_missing,
        "schema_mismatch_observed": observation_context["schema_mismatch"],
        "tool_surface_unstable_observed": observation_context["tool_surface_unstable"],
        "protocol_smoke": protocol_smoke_summary(protocol_context["smoke"]),
        "protocol_smoke_ran": protocol_context["smoke_ran"],
        "protocol_smoke_ok": protocol_context["smoke_ok"],
        "protocol_smoke_supported": protocol_context["smoke_supported"],
        "protocol_unready": protocol_context["protocol_unready"],
        "expected_tool_missing": protocol_context["expected_tool_missing"],
        "fallback": {
            "kind": profile.fallback,
            "role": fallback_role,
            **fallback,
        },
        "native_mcp_preferred": native_mcp_preferred,
        "execution_affinity": affinity.get("execution_affinity"),
        "session_binding": affinity.get("session_binding"),
        "transport_topology": profile.transport_topology,
        "recover_strategy": recovery_strategy_for_topology(profile),
        "recovery_policy": profile.recovery_policy,
        "status": status,
        "issues": issues,
        "notes": profile.notes,
        "current_turn_anchor": anchor or {},
    }


def snapshot(
    observations: list[str] | None = None,
    run_fallback: bool = False,
    run_smoke: bool = False,
    smoke_profiles: list[str] | None = None,
    observation_max_age_minutes: int = DEFAULT_OBSERVATION_MAX_AGE_MINUTES,
    thread_id: str | None = None,
) -> dict[str, Any]:
    config = config_text()
    proc = resource_process_snapshot()
    persisted = load_recent_observations(max_age_minutes=observation_max_age_minutes)
    anchor = thread_freshness_anchor(thread_id)
    anchor_at = _parse_iso_datetime(anchor.get("anchor_at")) if isinstance(anchor, dict) else None
    obs = merge_observations(observations, persisted)
    obs_states = observation_state_after_anchor(observations, persisted, anchor_at)
    smoke_names = {str(item).strip() for item in (smoke_profiles or []) if str(item).strip()}
    if run_smoke and not smoke_names:
        smoke_names = set(SMOKE_SPECS) | set(HTTP_MCP_SMOKE_PROFILES)
    smoke_results: dict[str, dict[str, Any]] = {}
    if run_smoke:
        for name in sorted(smoke_names):
            smoke_results[name] = protocol_smoke(name)
    profiles = [
        classify_profile(
            profile,
            config,
            proc,
            obs,
            observation_states=obs_states,
            anchor=anchor,
            run_fallback=run_fallback,
            smoke_results=smoke_results,
        )
        for profile in MCP_PROFILES
    ]
    return {
        "schema": "mcp_session.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "config_path": str(CODEX_CONFIG),
        "observation_log": str(OBSERVATION_LOG),
        "recent_observation_count": len(persisted),
        "observation_max_age_minutes": observation_max_age_minutes,
        "current_turn_anchor": anchor,
        "protocol_smoke_ran": bool(run_smoke),
        "protocol_smoke_profiles": sorted(smoke_results),
        "resource_process_ok": bool(proc.get("ok")),
        "profiles": profiles,
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    issues: list[dict[str, Any]] = []
    for profile in payload.get("profiles", []):
        for issue in profile.get("issues", []) if isinstance(profile, dict) else []:
            issues.append({"profile": profile.get("name"), **issue})
    return {
        "schema": "mcp_session.doctor.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": {
            "profile_count": len(payload.get("profiles", [])),
            "recent_observation_count": int(payload.get("recent_observation_count") or 0),
            "transport_closed_count": sum(1 for item in payload.get("profiles", []) if item.get("transport_closed_observed")),
            "tool_binding_issue_count": sum(1 for item in payload.get("profiles", []) if item.get("tool_binding_issue_observed")),
            "session_surface_missing_or_stale_count": sum(1 for item in payload.get("profiles", []) if item.get("session_surface_missing_or_stale")),
            "current_turn_callable_unavailable_count": sum(
                1
                for item in payload.get("profiles", [])
                if isinstance(item.get("current_turn_callable"), dict)
                and item.get("current_turn_callable", {}).get("callable") is False
            ),
            "current_turn_callable_unverified_count": sum(
                1
                for item in payload.get("profiles", [])
                if isinstance(item.get("current_turn_callable"), dict)
                and item.get("current_turn_callable", {}).get("callable") is None
            ),
            "schema_mismatch_count": sum(1 for item in payload.get("profiles", []) if item.get("schema_mismatch_observed")),
            "tool_surface_unstable_count": sum(1 for item in payload.get("profiles", []) if item.get("tool_surface_unstable_observed")),
            "protocol_unready_count": sum(1 for item in payload.get("profiles", []) if item.get("protocol_unready")),
            "expected_tool_missing_count": sum(1 for item in payload.get("profiles", []) if item.get("expected_tool_missing")),
            "fallback_available_count": sum(1 for item in payload.get("profiles", []) if item.get("fallback", {}).get("available")),
            "configured_count": sum(1 for item in payload.get("profiles", []) if item.get("configured")),
        },
        "snapshot": payload,
    }


def profile_needs_session_repair(profile: dict[str, Any]) -> bool:
    return bool(
        profile.get("transport_closed_observed")
        or profile.get("tool_binding_issue_observed")
        or profile.get("session_surface_missing_or_stale")
        or profile.get("schema_mismatch_observed")
        or profile.get("tool_surface_unstable_observed")
        or profile.get("protocol_unready")
        or profile.get("expected_tool_missing")
    )


def config_repair_action(name: Any) -> dict[str, Any]:
    return {
        "profile": name,
        "action": "repair_codex_config_merge_only",
        "apply_default": False,
        "risk": "config_write_requires_backup",
    }


def protocol_smoke_action(name: Any) -> dict[str, Any]:
    return {
        "profile": name,
        "action": "run_protocol_smoke_to_separate_service_from_session_fault",
        "apply_default": False,
        "command": f"python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py mcp-session doctor --run-smoke --smoke-profile {name}",
        "risk": "bounded_temporary_mcp_subprocess_probe",
    }


def protocol_smoke_unsupported_action(name: Any) -> dict[str, Any]:
    return {
        "profile": name,
        "action": "protocol_smoke_not_supported_by_local_doctor",
        "apply_default": False,
        "risk": "no_service_health_claim_from_local_doctor",
        "purpose": "Do not recommend an impossible smoke command. Use current-turn tool exposure and the profile fallback to avoid a false available verdict.",
    }


def fallback_actions(name: Any, fallback: dict[str, Any]) -> list[dict[str, Any]]:
    if not fallback.get("available"):
        return []
    work_commands = [str(item) for item in fallback.get("work_commands", []) if str(item)]
    command = work_commands[0] if work_commands else fallback.get("command", "")
    if command:
        return [
            {
                "profile": name,
                "action": "use_fallback_for_current_task",
                "apply_default": False,
                "command": command,
                "commands": work_commands or [command],
                "health_command": fallback.get("health_command") or fallback.get("command", ""),
                "risk": "low_read_only_or_profile_specific",
            }
        ]
    return [
        {
            "profile": name,
            "action": "fallback_not_actionable_for_current_task",
            "apply_default": False,
            "fallback_kind": fallback.get("kind", ""),
            "risk": "manual_session_refresh_or_profile_specific_repair_required",
        }
    ]


def current_turn_probe_action(name: Any, probe_plan: dict[str, Any]) -> dict[str, Any]:
    warmup_query = str(probe_plan.get("tool_search_query") or "").strip()
    return {
        "profile": name,
        "action": "warm_then_probe_current_turn_tool_surface",
        "apply_default": False,
        "tool": probe_plan.get("tool", ""),
        "tool_search_query": warmup_query,
        "warmup_required": bool(probe_plan.get("warmup_required")),
        "warmup_instruction": (
            f"Call tool_search with query: {warmup_query}"
            if warmup_query
            else "Call tool_search with the exact MCP namespace/tool names before declaring the tool unbound."
        ),
        "probe": probe_plan.get("probe", "Call the actual MCP tool in this active Codex turn."),
        "success_record": probe_plan.get("success_record", ""),
        "failure_record": probe_plan.get("failure_record", ""),
        "risk": "read_only_or_low_side_effect_probe",
        "purpose": "Do not report the MCP as usable from service smoke or generic discovery alone; first warm the deferred tool surface, then prove current-turn callability with a real tool call.",
    }


def refresh_session_action(name: Any, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": name,
        "action": "refresh_codex_tool_surface" if profile.get("session_surface_missing_or_stale") else "refresh_codex_mcp_session",
        "apply_default": False,
        "protected": bool(profile.get("protected")),
        "policy": profile.get("recovery_policy"),
        "risk": "may_require_codex_app_server_or_desktop_session_refresh",
        "only_after": "fallback used for current task and protocol smoke confirms service/tools are healthy",
        "controlled_rebind_path": "If the active Codex turn still cannot expose the tool surface after those checks, use the approved elevated Codex Desktop restart path to force a fresh turn binding rather than retrying the same stale turn.",
        "controlled_rebind_command": (
            f"powershell -NoProfile -ExecutionPolicy Bypass -File \"{CONTROLLED_REBIND_SCRIPT}\""
        ),
        "max_attempts_per_incident": 1,
        "after_rebind_validation": [
            "start a new turn",
            "run probe_current_turn_tool_surface for the affected profile",
            "record a current-codex-turn positive or negative observation",
        ],
    }


def guard_lock_repair_action(name: Any, guard_lock: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": name,
        "action": "repair_stdio_launch_guard_lock_boundary",
        "apply_default": False,
        "lock_path": guard_lock.get("path", ""),
        "lock_scope": guard_lock.get("lock_scope", ""),
        "risk": "low_if_backup_and_guard_only",
        "notes": [
            "Do not reuse old stdio MCP processes across Codex sessions.",
            "The guard lock must cover only prelaunch cleanup, not the MCP server lifetime.",
            "Remove only stale prelaunch lock files; do not kill active MCP servers from this repair plan.",
        ],
    }


def repair_actions_for_profile(profile: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    name = profile.get("name")
    if profile.get("retired"):
        return actions
    if not profile.get("configured"):
        actions.append(config_repair_action(name))
    if profile_needs_session_repair(profile):
        smoke_supported = bool(profile.get("protocol_smoke_supported"))
        if (
            smoke_supported
            and not profile.get("protocol_smoke_ran")
            and not profile.get("session_surface_missing_or_stale")
        ):
            actions.append(protocol_smoke_action(name))
        elif not smoke_supported and not profile.get("session_surface_missing_or_stale"):
            actions.append(protocol_smoke_unsupported_action(name))

        fallback = profile.get("fallback", {})
        actions.extend(fallback_actions(name, fallback if isinstance(fallback, dict) else {}))

        current_turn = profile.get("current_turn_callable") if isinstance(profile.get("current_turn_callable"), dict) else {}
        probe_plan = profile.get("current_turn_probe_plan") if isinstance(profile.get("current_turn_probe_plan"), dict) else {}
        if profile.get("service_ok") and current_turn.get("callable") is not True:
            actions.append(current_turn_probe_action(name, probe_plan))
        actions.append(refresh_session_action(name, profile))

    guard_lock = profile.get("guard_lock") if isinstance(profile.get("guard_lock"), dict) else {}
    if guard_lock.get("legacy_lifecycle_lock") or guard_lock.get("stale"):
        actions.append(guard_lock_repair_action(name, guard_lock))
    return actions


def repair_plan_notes() -> list[str]:
    return [
        "This plan does not kill processes or restart Codex by itself.",
        "resource_process_doctor remains responsible for duplicate/orphan process cleanup.",
        "mcp_session_doctor is responsible for current-session transport/fallback decisions.",
        "Protocol smoke is opt-in because it launches temporary MCP subprocesses.",
        "If protocol smoke succeeds while the active session is unbound/closed, the root fault is Codex session tool-surface binding, not the MCP service.",
        "The durable recovery for a stale current-turn surface is: fallback for the task, then a controlled Codex Desktop restart/new-turn rebind if the same turn remains stale.",
        "A profile is not fully current-turn usable until an active-turn probe records current_turn_callable after the service/config checks pass.",
        "Controlled rebind is capped at one attempt per incident; repeated failure must be investigated as startup/session binding drift instead of looping restarts.",
    ]


def repair_plan(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    actions: list[dict[str, Any]] = []
    for profile in payload.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        actions.extend(repair_actions_for_profile(profile))
    return {
        "schema": "mcp_session.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "dry_run": True,
        "actions": actions,
        "notes": repair_plan_notes(),
    }


def metrics(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    profiles = payload.get("profiles", [])
    gateway = gateway_state_summary()
    return {
        "schema": "mcp_session.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "profile_count": len(profiles),
        "recent_observation_count": int(payload.get("recent_observation_count") or 0),
        "configured_count": sum(1 for item in profiles if item.get("configured")),
        "process_present_count": sum(1 for item in profiles if item.get("process_present")),
        "transport_closed_observed_count": sum(1 for item in profiles if item.get("transport_closed_observed")),
        "tool_binding_issue_observed_count": sum(1 for item in profiles if item.get("tool_binding_issue_observed")),
        "session_surface_missing_or_stale_count": sum(1 for item in profiles if item.get("session_surface_missing_or_stale")),
        "current_turn_callable_unavailable_count": sum(
            1
            for item in profiles
            if isinstance(item.get("current_turn_callable"), dict)
            and item.get("current_turn_callable", {}).get("callable") is False
        ),
        "current_turn_callable_unverified_count": sum(
            1
            for item in profiles
            if isinstance(item.get("current_turn_callable"), dict)
            and item.get("current_turn_callable", {}).get("callable") is None
        ),
        "schema_mismatch_observed_count": sum(1 for item in profiles if item.get("schema_mismatch_observed")),
        "tool_surface_unstable_observed_count": sum(1 for item in profiles if item.get("tool_surface_unstable_observed")),
        "guard_legacy_lifecycle_lock_count": sum(
            1 for item in profiles
            if isinstance(item.get("guard_lock"), dict) and item.get("guard_lock", {}).get("legacy_lifecycle_lock")
        ),
        "guard_stale_prelaunch_lock_count": sum(
            1 for item in profiles
            if isinstance(item.get("guard_lock"), dict) and item.get("guard_lock", {}).get("stale")
        ),
        "protocol_smoke_ran_count": sum(1 for item in profiles if item.get("protocol_smoke_ran")),
        "protocol_smoke_ok_count": sum(1 for item in profiles if item.get("protocol_smoke_ok") is True),
        "protocol_unready_count": sum(1 for item in profiles if item.get("protocol_unready")),
        "expected_tool_missing_count": sum(1 for item in profiles if item.get("expected_tool_missing")),
        "fallback_available_count": sum(1 for item in profiles if item.get("fallback", {}).get("available")),
        "protected_count": sum(1 for item in profiles if item.get("protected")),
        "tier_counts": {
            tier: sum(1 for item in profiles if item.get("tool_tier") == tier)
            for tier in ("A", "B", "C", "R")
        },
        "gateway_state_path": gateway.get("state_path"),
        "gateway_profile_count": gateway.get("profile_count"),
        "gateway_last_ok_count": gateway.get("last_ok_count"),
        "gateway_last_failed_count": gateway.get("last_failed_count"),
        "profiles_by_status": {
            status: sum(1 for item in profiles if item.get("status") == status)
            for status in sorted({str(item.get("status") or "unknown") for item in profiles})
        },
    }


def _validate_configured_servers(issues: list[str]) -> None:
    cfg = parse_toml(CODEX_CONFIG)
    configured_servers = cfg.get("mcp_servers") if isinstance(cfg.get("mcp_servers"), dict) else {}
    registry = profile_by_name()
    if not isinstance(configured_servers, dict):
        return
    platform_scope = runtime_platform()
    configured_names = set(configured_servers)
    if platform_scope == "windows":
        for profile_name in sorted(configured_names & HUB_MANAGED_MCP_NAMES):
            issues.append(f"Hub-managed MCP must not be registered in Codex Desktop config: {profile_name}")
        for profile_name in sorted(DESKTOP_NATIVE_MCP_NAMES - configured_names):
            issues.append(f"Desktop-native MCP missing from Codex config: {profile_name}")
    elif "node_repl" not in configured_names:
        issues.append("WSL Codex config missing required native MCP: node_repl")
    for server_name, server_spec in configured_servers.items():
        if server_name not in registry:
            issues.append(f"configured MCP missing governance profile: {server_name}")
            continue
        profile = registry[server_name]
        command = ""
        if isinstance(server_spec, dict):
            command = str(server_spec.get("command") or "")
        is_local_stdio = bool(command and not str(server_spec.get("url") if isinstance(server_spec, dict) else "").strip())
        if is_local_stdio and not profile_supports_protocol_smoke(profile.name) and not smoke_exempt_reason(profile):
            issues.append(f"local stdio MCP missing protocol smoke spec: {server_name}")
        if profile.name in REQUIRED_NATIVE_MCP_PROFILES and not bool(server_spec.get("required")):
            issues.append(f"required native MCP is not marked required in Codex config: {server_name}")


def _validate_registry_profiles(profile_names: list[Any], issues: list[str]) -> None:
    smoke_spec_names = set(SMOKE_SPECS) | set(HTTP_MCP_SMOKE_PROFILES)
    governed_names = {profile.name for profile in MCP_PROFILES}
    for smoke_name in sorted(smoke_spec_names - governed_names):
        issues.append(f"protocol smoke spec has no governance profile: {smoke_name}")
    for required in ("codegraph", "mobile-openclaw-bridge", "local-pmb-memory"):
        if required not in profile_names:
            issues.append(f"missing registry profile: {required}")
    for registry_profile in MCP_PROFILES:
        if profile_tier(registry_profile) not in {"A", "B", "C", "R"}:
            issues.append(f"profile has invalid tool tier: {registry_profile.name}")
        if registry_profile.name not in RETIRED_PROFILES and not str(registry_profile.transport_topology or "").strip():
            issues.append(f"profile missing transport topology: {registry_profile.name}")


def _validate_snapshot_profiles(
    payload: dict[str, Any],
    gateway_profiles: dict[str, Any],
    issues: list[str],
    advisories: list[str],
    unproven_native_callability: list[str],
) -> None:
    if not isinstance(payload.get("profiles"), list):
        issues.append("snapshot profiles must be a list")
        return
    for profile in payload.get("profiles", []):
        if not isinstance(profile, dict):
            continue
        current_turn = profile.get("current_turn_callable") if isinstance(profile.get("current_turn_callable"), dict) else {}
        if profile.get("session_surface_missing_or_stale") or current_turn.get("callable") is False:
            if profile.get("fallback_tool_call_ok"):
                advisories.append(
                    f"native current turn cannot use {profile.get('name')} but bounded fallback/fresh stdio call is available"
                )
                continue
            gateway_profile = gateway_profiles.get(str(profile.get("name") or ""))
            if isinstance(gateway_profile, dict) and gateway_profile.get("last_gateway_ok") is True:
                advisories.append(
                    f"native current turn cannot use {profile.get('name')} but tool gateway fresh stdio call is available"
                )
                continue
            issues.append(
                f"current turn cannot use {profile.get('name')}: "
                f"{current_turn.get('state') or 'session_surface_missing_or_stale'}"
            )
        elif not profile.get("retired") and current_turn.get("callable") is None:
            unproven_native_callability.append(str(profile.get("name") or "unknown"))


def _validate_state_paths(issues: list[str], advisories: list[str]) -> None:
    if not CODEX_CONFIG.exists():
        issues.append(f"codex config missing: {CODEX_CONFIG}")
    if not str(OBSERVATION_LOG).endswith("mcp_session_observations.jsonl"):
        issues.append(f"unexpected observation log path: {OBSERVATION_LOG}")
    if OBSERVATION_LOG.exists() and not OBSERVATION_LOG.is_file():
        issues.append(f"observation log is not a file: {OBSERVATION_LOG}")
    if GATEWAY_STATE_PATH.exists() and not GATEWAY_STATE_PATH.is_file():
        issues.append(f"gateway state path is not a file: {GATEWAY_STATE_PATH}")
    if not GATEWAY_STATE_PATH.parent.exists():
        advisories.append(f"gateway state directory has not been created yet: {GATEWAY_STATE_PATH.parent}")


def validate(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = snap or snapshot()
    issues: list[str] = []
    advisories: list[str] = []
    unproven_native_callability: list[str] = []
    gateway_summary = gateway_state_summary()
    gateway_state = _read_json_file(GATEWAY_STATE_PATH, {"profiles": {}})
    gateway_profiles = gateway_state.get("profiles") if isinstance(gateway_state, dict) else {}
    if not isinstance(gateway_profiles, dict):
        gateway_profiles = {}
    names = [item.get("name") for item in payload.get("profiles", []) if isinstance(item, dict)]
    _validate_configured_servers(issues)
    _validate_registry_profiles(names, issues)
    _validate_snapshot_profiles(payload, gateway_profiles, issues, advisories, unproven_native_callability)
    _validate_state_paths(issues, advisories)
    route_contract = route_completion_contract_check()
    if not route_contract.get("ok"):
        for issue in route_contract.get("issues", []):
            issues.append(f"route completion contract failed: {issue}")
    advisory_summary = {
        "unproven_native_callability_count": len(unproven_native_callability),
        "unproven_native_callability_profiles": unproven_native_callability,
        "actionable_advisory_count": max(0, len(advisories) - len(unproven_native_callability)),
        "rule": "Unproven current-turn callability is a task-routing signal, not a server failure. Probe only task-relevant native tools before falling back.",
    }
    return {
        "schema": "mcp_session.validate.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "platform_scope": runtime_platform(),
        "config_path": str(CODEX_CONFIG),
        "issues": issues,
        "advisories": advisories,
        "advisory_summary": advisory_summary,
        "profile_count": len(payload.get("profiles", [])) if isinstance(payload.get("profiles"), list) else 0,
        "observation_log": str(OBSERVATION_LOG),
        "gateway": gateway_summary,
        "route_completion_contract": route_contract,
    }


def recover_plan(profile_name: str, status: str = "transport_closed") -> dict[str, Any]:
    registry = profile_by_name()
    profile = registry.get(str(profile_name or "").strip())
    if profile is None:
        return {
            "schema": "mcp_session.recover_plan.v1",
            "ok": False,
            "reason": "unknown_profile",
            "profile": profile_name,
            "known_profiles": sorted(registry),
            "generated_at": now_iso(),
        }
    strategy = recovery_strategy_for_topology(profile)
    return {
        "schema": "mcp_session.recover_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "profile": profile.name,
        "status": status or "transport_closed",
        "tool_tier": profile_tier(profile),
        "protected": profile.protected,
        "transport_topology": profile.transport_topology,
        "strategy": strategy,
        "record_negative_command": (
            f"python _bridge\\mcp_session_doctor.py record-observation --profile {profile.name} "
            f"--status {status or 'transport_closed'} --source {CURRENT_TURN_PROBE_SOURCE} "
            "--detail \"native MCP call failed in active turn; entering bounded recovery plan\""
        ),
        "protocol_smoke_command": (
            f"python _bridge\\mcp_session_doctor.py smoke --profile {profile.name}"
            if profile_supports_protocol_smoke(profile.name)
            else ""
        ),
        "gateway_route_command": (
            f"python _bridge\\mcp_session_doctor.py gateway-route --profile {profile.name} --tool <tool>"
            if not profile.protected and profile_tier(profile) in {"A", "B"}
            else ""
        ),
        "fallback_commands": fallback_work_commands(profile),
        "current_turn_probe_plan": current_turn_probe_plan(
            profile,
            configured=True,
            retired=profile.name in RETIRED_PROFILES,
            service_ok=True,
        ),
        "limits": {
            "max_auto_recover_attempts": strategy.get("max_auto_recover_attempts"),
            "never_escalate_permissions": True,
            "record_positive_only_after_call_completed": True,
            "do_not_repeat_failed_native_handle": True,
        },
    }


def _emit_cli_payload(payload: dict[str, Any], *, full: bool = False, command: str = "") -> int:
    output = governed_cli_payload(
        payload,
        full=full,
        full_result_ref=f"command:python _bridge/mcp_session_doctor.py {command} --full" if command else "",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


def _load_cli_json_object(raw: str, schema: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        payload = json.loads(str(raw or "{}"))
    except json.JSONDecodeError as exc:
        return None, {"schema": schema, "ok": False, "error": f"arguments_json_invalid: {exc}"}
    if not isinstance(payload, dict):
        return None, {"schema": schema, "ok": False, "error": "arguments_json_must_be_object"}
    return payload, None


def _gateway_warmup_profiles(args: argparse.Namespace) -> list[str]:
    profiles = [str(item).strip() for item in (args.gateway_profile or []) if str(item).strip()]
    if not profiles and str(args.profile or "").strip():
        profiles = [str(args.profile).strip()]
    if not profiles:
        profiles = ["custom-slash-commands", "sqlite-scratch", "sqlite-bridge-ro"]
    return profiles


def _direct_command_payload(args: argparse.Namespace) -> dict[str, Any] | None:
    return direct_command_payload(
        args,
        {
            "record_observation": record_observation,
            "load_observation_items": _load_observation_items,
            "record_observations": record_observations,
            "batch_recording_contract_check": batch_recording_contract_check,
            "route_completion_contract_check": route_completion_contract_check,
            "protocol_smoke": protocol_smoke,
            "load_cli_json_object": _load_cli_json_object,
            "protocol_tool_call": protocol_tool_call,
            "gateway_route": gateway_route,
            "gateway_call": gateway_call,
            "complete_route_after_native_failure": complete_route_after_native_failure,
            "gateway_warmup_profiles": _gateway_warmup_profiles,
            "gateway_warmup": gateway_warmup,
            "recover_plan": recover_plan,
        },
        observation_log=OBSERVATION_LOG,
        current_turn_probe_source=CURRENT_TURN_PROBE_SOURCE,
    )


def _snapshot_command_payload(args: argparse.Namespace) -> dict[str, Any]:
    snap = snapshot(
        observations=list(args.observe or []),
        run_fallback=bool(args.run_fallback),
        run_smoke=bool(args.run_smoke),
        smoke_profiles=list(args.smoke_profile or []),
        thread_id=str(args.thread_id or "").strip() or None,
    )
    if args.command == "snapshot":
        return snap
    if args.command == "doctor":
        return doctor(snap)
    if args.command == "repair-plan":
        return repair_plan(snap)
    if args.command == "metrics":
        return metrics(snap)
    return validate(snap)


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex MCP session health doctor")
    parser.add_argument(
        "command",
        choices=[
            "snapshot",
            "doctor",
            "repair-plan",
            "metrics",
            "validate",
            "record-observation",
            "record-observations",
            "batch-recording-contract-check",
            "route-completion-contract-check",
            "smoke",
            "tool-call",
            "gateway-route",
            "gateway-call",
            "complete-route",
            "gateway-warmup",
            "recover-plan",
        ],
    )
    parser.add_argument(
        "--observe",
        action="append",
        default=[],
        help="Observation in profile:status form, e.g. codegraph:transport_closed",
    )
    parser.add_argument("--run-fallback", action="store_true", help="Run bounded fallback probes where available")
    parser.add_argument("--run-smoke", action="store_true", help="Run bounded protocol initialize/tools-list smoke probes")
    parser.add_argument("--smoke-profile", action="append", default=[], help="MCP profile name for protocol smoke; may repeat")
    parser.add_argument("--timeout-seconds", type=int, default=0, help="Timeout for direct smoke command")
    parser.add_argument("--profile", default="", help="MCP profile name for record-observation")
    parser.add_argument("--status", default="", help="Observation status for record-observation, e.g. transport_closed")
    parser.add_argument("--source", default="", help="Observation source for record-observation")
    parser.add_argument("--detail", default="", help="Observation detail for record-observation")
    parser.add_argument("--items-json", default="", help="JSON array for record-observations")
    parser.add_argument("--items-file", default="", help="UTF-8 JSON array file for record-observations")
    parser.add_argument("--dry-run", action="store_true", help="Validate record-observation(s) without writing")
    parser.add_argument("--tool", default="", help="MCP tool name for tool-call")
    parser.add_argument("--arguments-json", default="{}", help="JSON object arguments for tool-call")
    parser.add_argument("--gateway-profile", action="append", default=[], help="MCP profile for gateway-warmup; may repeat")
    parser.add_argument("--thread-id", default="", help="Optional Codex thread id to anchor current-turn observations")
    parser.add_argument("--full", action="store_true", help="Emit the complete successful result.")
    args = parser.parse_args()

    payload = _direct_command_payload(args) or _snapshot_command_payload(args)
    return _emit_cli_payload(payload, full=bool(args.full), command=str(args.command))


if __name__ == "__main__":
    raise SystemExit(main())
