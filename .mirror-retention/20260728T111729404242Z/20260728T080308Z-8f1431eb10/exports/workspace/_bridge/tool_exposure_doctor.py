#!/usr/bin/env python3
"""Read-only tool exposure doctor for Codex MCP/plugin reliability.

This doctor separates four states that are often conflated:

- configured: declared in the Codex config/baseline
- cli_visible: visible to `codex mcp list`
- runtime_process: a matching local process exists for local stdio MCPs
- current_session_exposure: the active Codex turn actually exposes the tool

It cannot prove that the current model turn has an exposed tool surface. That
last step still requires the running Codex session to expose the tool metadata,
so this script reports session_probe_required instead of guessing.

For stdio MCPs, a missing runtime process is normally an idle state, not a
failure. Many stdio servers are spawned on demand by the MCP client and exit
when the session closes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "_bridge"
BASELINE_PATH = BRIDGE_ROOT / "codex_startup_baseline.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
CODEX_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"
MCP_SESSION_OBSERVATION_LOG = BRIDGE_ROOT / "mobile_openclaw_bridge" / "runtime" / "mcp_session_observations.jsonl"
CURRENT_TURN_POSITIVE_STATUSES = {
    "current_turn_callable",
    "tool_available",
    "tool_call_succeeded",
    "session_tool_available",
    "call_succeeded",
}
CURRENT_TURN_UNSTABLE_STATUSES = {"tool_surface_unstable"}
CURRENT_TURN_NEGATIVE_STATUSES = {"tool_unbound", "transport_closed", "schema_mismatch", *CURRENT_TURN_UNSTABLE_STATUSES}
DEFAULT_OBSERVATION_MAX_AGE_MINUTES = 720
CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES = 30
CURRENT_TURN_SOURCE_MARKERS = ("current-codex-turn",)
CURRENT_SESSION_SOURCE_MARKERS = (
    "current-codex-turn",
    "active-codex-turn",
    "this-codex-turn",
    "current-turn",
    "current-codex-session",
    "active-codex-session",
    "this-codex-session",
    "current-session",
)

if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from resource_process_doctor import process_snapshot  # noqa: E402
from mcp_session_doctor import CURRENT_TURN_PROBES, SMOKE_SPECS  # noqa: E402
from mcp_session_doctor import thread_freshness_anchor  # noqa: E402
from mcp_execution_priority import HUB_MANAGED_MCP_NAMES  # noqa: E402


MCP_TO_PROCESS_GROUP = {
    "chrome-devtools": "chrome-devtools",
    "next-ai-drawio": "next_ai_drawio_mcp",
    "playwright": "playwright",
    "markitdown": "markitdown-mcp",
    "myskills": "myskills-mcp",
    "gui-automation": "gui_automation_mcp",
    "local-pmb-memory": "local_pmb_proxy",
    "codegraph": "codegraph_mcp",
    "mobile-openclaw-bridge": "mobile_bridge_mcp_server",
    "agent-bridge": "bridge_server_v2",
    "context7": "context7_mcp",
    "microsoftdocs": "microsoftdocs_mcp",
    "filesystem": "filesystem_mcp",
    "custom-slash-commands": "custom_slash_commands_mcp",
    "sqlite-scratch": "sqlite_scratch_mcp",
    "sqlite-bridge-ro": "sqlite_bridge_ro_mcp",
}


ON_DEMAND_STDIO_MCPS = {
    "agent-bridge",
    "chrome-devtools",
    "next-ai-drawio",
    "codegraph",
    "gui-automation",
    "markitdown",
    "local-pmb-memory",
    "mobile-openclaw-bridge",
    "myskills",
    "playwright",
    "context7",
    "microsoftdocs",
    "filesystem",
    "filesystem-admin",
    "custom-slash-commands",
    "sqlite-scratch",
    "sqlite-bridge-ro",
}


TOOL_FALLBACKS: dict[str, dict[str, Any]] = {
    "agent-bridge": {
        "available": True,
        "kind": "bridge_db_or_worker_log_readonly",
        "summary": "Use bridge.db/worker.log read-only inspection for diagnosis; mutate bridge state only through owning commands.",
        "safety": "read_only_default",
    },
    "chrome-devtools": {
        "available": True,
        "kind": "node_repl_or_browser_skill_when_task_requires_browser",
        "summary": "Use browser automation fallback only for browser tasks; avoid starting Chrome just for health probing.",
        "safety": "task_required_only",
    },
    "codegraph": {
        "available": True,
        "kind": "rg_plus_targeted_reads",
        "summary": "Use rg and targeted file reads when codegraph is unavailable; record lower structural confidence.",
        "safety": "read_only",
    },
    "context7": {
        "available": True,
        "kind": "web_or_local_docs_search",
        "summary": "Use official docs or web lookup when Context7 is unbound; prefer primary sources.",
        "safety": "read_only",
    },
    "custom-slash-commands": {
        "available": True,
        "kind": "local_registry_or_stdio_render",
        "summary": "Read commands.json or call custom_slash_commands_mcp.py through bounded stdio; never execute rendered text.",
        "safety": "no_execution",
    },
    "filesystem": {
        "available": True,
        "kind": "bounded_cli_file_ops",
        "summary": "Use Get-Content/Test-Path/Get-Item/rg with explicit paths and narrow roots; use apply_patch for edits after approval and backup.",
        "safety": "same_scope_no_admin_escalation",
    },
    "filesystem-admin": {
        "available": True,
        "kind": "explicit_admin_cli_file_ops",
        "summary": "Use explicit CLI paths only when the task truly needs C:\\ scope; do not broad-scan or destructively edit.",
        "safety": "explicit_high_scope_only",
    },
    "github": {
        "available": True,
        "kind": "gh_cli_or_web",
        "summary": "Use gh CLI or web/GitHub API with the same token boundary; do not expose tokens in logs.",
        "safety": "credential_redaction_required",
    },
    "gui-automation": {
        "available": True,
        "kind": "manual_gui_or_cli_specific_task",
        "summary": "Use GUI fallback only when task requires GUI; prefer CLI/structured APIs for system maintenance.",
        "safety": "task_required_only",
    },
    "local-pmb-memory": {
        "available": True,
        "kind": "local_pmb_cli_or_memory_files",
        "summary": "Use local PMB maintenance/CLI or memory notes; do not restore legacy memory MCPs as defaults.",
        "safety": "memory_policy_required",
    },
    "markitdown": {
        "available": True,
        "kind": "python_converter_or_workspace_dependency",
        "summary": "Use local conversion libraries or bundled workspace dependencies for documents.",
        "safety": "read_generated_output_before_use",
    },
    "microsoftdocs": {
        "available": True,
        "kind": "web_official_microsoft_docs",
        "summary": "Use Microsoft Learn web lookup as fallback; keep citations to official docs.",
        "safety": "read_only",
    },
    "mobile-openclaw-bridge": {
        "available": True,
        "kind": "mobile_openclaw_cli_supplement_fallback",
        "summary": "Use the dedicated supplement-fallback CLI for pending/ack flows when MCP is unbound.",
        "safety": "bridge_contract_required",
    },
    "myskills": {
        "available": True,
        "kind": "skills_filesystem_registry",
        "summary": "Use local skill folders and manifests; keep skill edits under approval and validation.",
        "safety": "approval_backup_required_for_edits",
    },
    "node_repl": {
        "available": False,
        "kind": "no_equivalent_general_fallback",
        "summary": "Use shell or browser-specific tools depending on the task; do not emulate persistent JS state implicitly.",
        "safety": "task_specific",
    },
    "playwright": {
        "available": True,
        "kind": "node_repl_or_cli_playwright_when_installed",
        "summary": "Use browser automation fallback only when required; avoid heavy startup for health checks.",
        "safety": "task_required_only",
    },
    "sqlite-bridge-ro": {
        "available": True,
        "kind": "readonly_sqlite3_query",
        "summary": "Use read-only sqlite3 or bridge maintenance inspection; never write production bridge DB.",
        "safety": "read_only",
    },
    "sqlite-scratch": {
        "available": True,
        "kind": "tool_coordination_or_local_python_sqlite3",
        "summary": "Use tool_coordination.py or local Python sqlite3 only against the dedicated scratch DB.",
        "safety": "scratch_db_only",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def resolve_codex_cli(config: dict[str, Any] | None = None) -> str:
    candidates: list[str] = []
    env_value = os.environ.get("CODEX_CLI_PATH", "").strip()
    if env_value:
        candidates.append(env_value)
    if isinstance(config, dict):
        node_repl = (config.get("mcp_servers") or {}).get("node_repl")
        if isinstance(node_repl, dict):
            cli_value = str((node_repl.get("env") or {}).get("CODEX_CLI_PATH") or "").strip()
            if cli_value:
                candidates.append(cli_value)
    bin_root = Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin"
    if bin_root.exists():
        latest_bins = sorted(
            (path for path in bin_root.glob("*/codex.exe") if path.exists()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        candidates.extend(str(path) for path in latest_bins)
    candidates.extend([shutil.which("codex") or "", shutil.which("codex.exe") or ""])
    for candidate in candidates:
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    return ""


def codex_cli_version(cli: str) -> str:
    if not cli:
        return ""
    result = run_command([cli, "--version"], timeout=10)
    output = ((result.get("stdout") or "") + "\n" + (result.get("stderr") or "")).strip()
    match = re.search(r"(\d+\.\d+\.\S+)", output)
    return match.group(1) if match else output[:80]


def run_command(command: list[str], timeout: int = 30) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "command": command,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": command,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def parse_mcp_list(output: str) -> dict[str, str]:
    servers: dict[str, str] = {}
    for line in output.splitlines():
        text = line.strip()
        if not text or text.lower().startswith(("name", "server", "---")):
            continue
        match = re.match(r"^([A-Za-z0-9_.@:/-]+)\s+(.+)$", text)
        if match:
            servers[match.group(1)] = match.group(2).strip()
            continue
        first = text.split()[0] if text.split() else ""
        if first:
            servers[first] = text
    return servers


def mcp_cli_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    cli = resolve_codex_cli(config)
    if not cli:
        return {"ok": False, "reason": "codex_cli_not_found", "cli": "", "servers": {}, "output_preview": ""}
    result = run_command([cli, "mcp", "list"], timeout=30)
    output = ((result.get("stdout") or "") + "\n" + (result.get("stderr") or "")).strip()
    return {
        "ok": bool(result.get("ok")),
        "cli": cli,
        "cli_version": codex_cli_version(cli),
        "returncode": result.get("returncode"),
        "servers": parse_mcp_list(output),
        "output_preview": output[:4000],
    }


def _normal_version(value: str) -> str:
    match = re.search(r"(\d+\.\d+\.\S+)", str(value or ""))
    return match.group(1) if match else str(value or "").strip()


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def current_turn_positive_observations(
    max_age_minutes: int = CURRENT_TURN_OBSERVATION_MAX_AGE_MINUTES,
    anchor_at: datetime | None = None,
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    if anchor_at is not None and anchor_at > cutoff:
        cutoff = anchor_at
    latest_positive_by_profile: dict[str, dict[str, Any]] = {}
    latest_negative_by_profile: dict[str, dict[str, Any]] = {}
    scanned = 0
    if not MCP_SESSION_OBSERVATION_LOG.exists():
        return {
            "state": "observation_log_missing",
            "path": str(MCP_SESSION_OBSERVATION_LOG),
            "max_age_minutes": max_age_minutes,
            "profiles": {},
            "positive_profiles": {},
            "negative_profiles": {},
            "profile_count": 0,
            "positive_count": 0,
            "negative_count": 0,
        }
    try:
        with MCP_SESSION_OBSERVATION_LOG.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                scanned += 1
                try:
                    item = json.loads(text)
                except json.JSONDecodeError:
                    continue
                status = str(item.get("status") or "")
                if status not in CURRENT_TURN_POSITIVE_STATUSES and status not in CURRENT_TURN_NEGATIVE_STATUSES:
                    continue
                source = str(item.get("source") or "").lower()
                if status in CURRENT_TURN_POSITIVE_STATUSES:
                    if not any(marker in source for marker in CURRENT_TURN_SOURCE_MARKERS):
                        continue
                elif not any(marker in source for marker in CURRENT_SESSION_SOURCE_MARKERS):
                    continue
                recorded_at = _parse_time(str(item.get("recorded_at") or ""))
                if recorded_at is None or recorded_at < cutoff:
                    continue
                profile = str(item.get("profile") or "").strip()
                if not profile:
                    continue
                target = latest_positive_by_profile if status in CURRENT_TURN_POSITIVE_STATUSES else latest_negative_by_profile
                previous = target.get(profile)
                previous_time = _parse_time(str((previous or {}).get("recorded_at") or "")) if previous else None
                if previous is None or previous_time is None or recorded_at >= previous_time:
                    target[profile] = item
    except OSError as exc:
        return {
            "state": "observation_log_error",
            "path": str(MCP_SESSION_OBSERVATION_LOG),
            "error": repr(exc),
            "max_age_minutes": max_age_minutes,
            "profiles": {},
            "positive_profiles": {},
            "negative_profiles": {},
            "profile_count": 0,
            "positive_count": 0,
            "negative_count": 0,
        }
    unresolved_negative_by_profile = {
        profile: item
        for profile, item in latest_negative_by_profile.items()
        if _parse_time(str(item.get("recorded_at") or ""))
        and (
            profile not in latest_positive_by_profile
            or (
                _parse_time(str(latest_positive_by_profile.get(profile, {}).get("recorded_at") or ""))
                or datetime.min.replace(tzinfo=timezone.utc)
            )
            < (_parse_time(str(item.get("recorded_at") or "")) or datetime.min.replace(tzinfo=timezone.utc))
        )
    }
    return {
        "state": (
            "current_turn_negative_observed"
            if unresolved_negative_by_profile
            else "current_turn_positive_observed"
            if latest_positive_by_profile
            else "current_turn_positive_not_observed"
        ),
        "path": str(MCP_SESSION_OBSERVATION_LOG),
        "max_age_minutes": max_age_minutes,
        "scanned_count": scanned,
        "profiles": latest_positive_by_profile,
        "positive_profiles": latest_positive_by_profile,
        "negative_profiles": unresolved_negative_by_profile,
        "profile_count": len(latest_positive_by_profile),
        "positive_count": len(latest_positive_by_profile),
        "negative_count": len(unresolved_negative_by_profile),
        "probe_required_for_full_confidence": "Current-turn usability must be proven by a real MCP tool call in the active turn, then recorded as source=current-codex-turn.",
        "probe_command_template": "python _bridge\\mcp_session_doctor.py record-observation --profile <profile> --status current_turn_callable --source current-codex-turn --detail \"<tool call succeeded in this turn>\"",
    }


def current_turn_state_for_profile(name: str, observations: dict[str, Any]) -> dict[str, Any]:
    positives = observations.get("positive_profiles") if isinstance(observations.get("positive_profiles"), dict) else {}
    negatives = observations.get("negative_profiles") if isinstance(observations.get("negative_profiles"), dict) else {}
    negative = negatives.get(name) if isinstance(negatives.get(name), dict) else {}
    positive = positives.get(name) if isinstance(positives.get(name), dict) else {}
    if negative:
        status = str(negative.get("status") or "")
        state = "unstable" if status in CURRENT_TURN_UNSTABLE_STATUSES else "unavailable"
        return {
            "state": state,
            "callable": False,
            "status": status,
            "evidence": negative,
            "note": "A current-turn negative observation is newer than any positive observation for this profile.",
        }
    if positive:
        return {
            "state": "ok",
            "callable": True,
            "status": str(positive.get("status") or ""),
            "evidence": positive,
            "note": "A current-turn positive observation confirms this active turn called the tool successfully.",
        }
    return {
        "state": "unverified",
        "callable": None,
        "status": "",
        "evidence": {},
        "note": "No fresh current-turn tool-call observation exists for this profile.",
    }


def fallback_for_tool(name: str) -> dict[str, Any]:
    fallback = dict(TOOL_FALLBACKS.get(name, {}))
    if fallback:
        return fallback
    return {
        "available": False,
        "kind": "not_defined",
        "summary": "No bounded fallback is defined for this tool yet.",
        "safety": "do_not_infer_extra_permissions",
    }


def probe_plan_for_tool(name: str) -> dict[str, Any]:
    probe = dict(CURRENT_TURN_PROBES.get(name, {}))
    if not probe:
        return {
            "defined": False,
            "required_for_full_confidence": True,
            "reason": "No explicit current-turn probe plan is defined for this tool.",
        }
    return {
        "defined": True,
        "required_for_full_confidence": True,
        "tool": probe.get("tool", ""),
        "tool_search_query": probe.get("tool_search_query", ""),
        "warmup_required": bool(probe.get("warmup_required")),
        "probe": probe.get("probe", ""),
        "success_record": probe.get("success_record", ""),
        "failure_record": probe.get("failure_record", ""),
    }


def usable_state_for_row(row: dict[str, Any], current_turn: dict[str, Any]) -> str:
    if row.get("hub_managed"):
        if current_turn.get("state") == "ok":
            return "usable_current_turn"
        if current_turn.get("state") == "unstable":
            return "blocked_current_turn_use_fallback"
        if current_turn.get("state") == "unavailable":
            return "unavailable_current_turn_use_fallback"
        return "hub_route_probe_required"
    if row.get("state") in {"config_missing", "cli_unverified", "cli_not_visible"}:
        return str(row.get("state"))
    if current_turn.get("state") == "ok":
        return "usable_current_turn"
    if current_turn.get("state") == "unstable":
        return "blocked_current_turn_use_fallback"
    if current_turn.get("state") == "unavailable":
        return "unavailable_current_turn_use_fallback"
    if row.get("cli_visible"):
        return "probe_required_before_claiming_usable"
    return "unverified"


def exposure_layers_for_row(row: dict[str, Any], current_turn: dict[str, Any]) -> dict[str, Any]:
    callable_value = current_turn.get("callable")
    evidence = current_turn.get("evidence") if isinstance(current_turn.get("evidence"), dict) else {}
    return {
        "config_ok": bool(row.get("configured")) if not row.get("hub_managed") else True,
        "config_required": not bool(row.get("hub_managed")),
        "hub_route_expected": bool(row.get("hub_managed")),
        "cli_visible": bool(row.get("cli_visible")),
        "protocol_smoke_supported": bool(row.get("protocol_smoke_supported")),
        "current_turn_exposed": (
            False
            if current_turn.get("state") in {"unavailable", "unstable"}
            else True
            if current_turn.get("state") == "ok"
            else None
        ),
        "current_turn_callable": callable_value,
        "call_completed": bool(callable_value is True and evidence),
        "claim_rule": "Do not report this MCP as usable unless call_completed is true for the active Codex turn.",
    }


def thread_surface_snapshot(thread_id: str | None, current_cli_version: str) -> dict[str, Any]:
    if not thread_id:
        return {
            "state": "not_requested",
            "note": "Pass --thread-id to compare a Codex thread's stored runtime metadata and dynamic tool registry.",
        }
    if not CODEX_STATE_DB.exists():
        return {"state": "state_db_missing", "thread_id": thread_id, "path": str(CODEX_STATE_DB)}
    try:
        con = sqlite3.connect(CODEX_STATE_DB)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "select id, source, model_provider, cli_version, cwd, created_at_ms, updated_at_ms, recency_at_ms from threads where id=?",
            (thread_id,),
        ).fetchone()
        dynamic_count = con.execute(
            "select count(*) from thread_dynamic_tools where thread_id=?",
            (thread_id,),
        ).fetchone()[0]
        dynamic_global_count = con.execute("select count(*) from thread_dynamic_tools").fetchone()[0]
    except Exception as exc:
        return {"state": "state_db_error", "thread_id": thread_id, "path": str(CODEX_STATE_DB), "error": repr(exc)}
    if row is None:
        return {
            "state": "thread_not_found",
            "thread_id": thread_id,
            "path": str(CODEX_STATE_DB),
            "dynamic_tool_global_count": dynamic_global_count,
        }
    thread_cli = str(row["cli_version"] or "")
    current_norm = _normal_version(current_cli_version)
    thread_norm = _normal_version(thread_cli)
    cli_version_stale = bool(thread_norm and current_norm and thread_norm != current_norm)
    dynamic_tools_empty = int(dynamic_count or 0) == 0
    if cli_version_stale and dynamic_tools_empty:
        state = "thread_runtime_surface_drift"
    elif cli_version_stale:
        state = "thread_cli_version_stale"
    elif dynamic_tools_empty:
        state = "thread_dynamic_tools_empty"
    else:
        state = "thread_surface_metadata_present"
    return {
        "state": state,
        "thread_id": thread_id,
        "path": str(CODEX_STATE_DB),
        "thread": {key: row[key] for key in row.keys()},
        "current_cli_version": current_norm,
        "thread_cli_version": thread_norm,
        "cli_version_stale": cli_version_stale,
        "dynamic_tool_count": int(dynamic_count or 0),
        "dynamic_tool_global_count": int(dynamic_global_count or 0),
        "dynamic_tools_empty": dynamic_tools_empty,
        "interpretation": (
            "The configured MCP services can be healthy while this existing thread/turn has a stale or empty tool surface. "
            "Use fallbacks for the current task and refresh the Codex session/new turn after config health is verified."
            if state in {"thread_runtime_surface_drift", "thread_cli_version_stale", "thread_dynamic_tools_empty"}
            else "Thread surface metadata does not show this drift pattern."
        ),
    }


def command_exists(path_text: str) -> bool | None:
    if not path_text:
        return None
    if path_text.startswith("http://") or path_text.startswith("https://"):
        return None
    candidate = Path(path_text)
    if candidate.exists():
        return True
    return shutil.which(path_text) is not None


def snapshot(thread_id: str | None = None) -> dict[str, Any]:
    baseline = load_json(BASELINE_PATH)
    config = load_toml(CODEX_CONFIG)
    expected_mcp = baseline.get("expected_mcp") if isinstance(baseline.get("expected_mcp"), dict) else {}
    configured_mcp = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
    cli = mcp_cli_snapshot(config)
    process = process_snapshot()
    anchor = thread_freshness_anchor(thread_id)
    anchor_at = _parse_time(str(anchor.get("anchor_at") or ""))
    processes = process.get("processes") if isinstance(process.get("processes"), list) else []
    by_group: dict[str, list[dict[str, Any]]] = {}
    for item in processes:
        group = str(item.get("group") or "")
        if group:
            by_group.setdefault(group, []).append(item)

    current_turn_observations = current_turn_positive_observations(anchor_at=anchor_at)
    rows: list[dict[str, Any]] = []
    for name in sorted(expected_mcp):
        spec = expected_mcp.get(name) if isinstance(expected_mcp.get(name), dict) else {}
        live_spec = configured_mcp.get(name) if isinstance(configured_mcp.get(name), dict) else {}
        command = str(live_spec.get("command") or spec.get("command") or "")
        url = str(live_spec.get("url") or spec.get("url") or "")
        registration_mode = str(live_spec.get("registration_mode") or spec.get("registration_mode") or "")
        hub_managed = registration_mode == "hub_managed" or name in HUB_MANAGED_MCP_NAMES
        process_group = MCP_TO_PROCESS_GROUP.get(name, "")
        group_processes = by_group.get(process_group, []) if process_group else []
        cli_visible = name in (cli.get("servers") or {})
        configured = name in configured_mcp
        local_stdio = bool(command) and not url
        on_demand_stdio = local_stdio and name in ON_DEMAND_STDIO_MCPS
        current_turn = current_turn_state_for_profile(name, current_turn_observations)
        fallback = fallback_for_tool(name)
        probe_plan = probe_plan_for_tool(name)
        protocol_smoke_supported = name in SMOKE_SPECS
        row = {
            "name": name,
            "configured": configured,
            "required": bool(spec.get("required", True)),
            "transport": "remote_url" if url else "stdio" if command else "unknown",
            "on_demand": on_demand_stdio,
            "command": command,
            "url": url,
            "registration_mode": registration_mode or ("hub_managed" if hub_managed else "desktop_native"),
            "hub_managed": hub_managed,
            "hub_route_expected": hub_managed,
            "command_exists": command_exists(command),
            "cli_visible": cli_visible,
            "cli_status": (cli.get("servers") or {}).get(name, ""),
            "runtime_process_group": process_group,
            "runtime_process_count": len(group_processes),
            "runtime_pids": [item.get("pid") for item in group_processes[:8]],
            "runtime_process_expected_now": local_stdio and bool(process_group) and not on_demand_stdio,
            "protocol_smoke_supported": protocol_smoke_supported,
            "current_turn": current_turn,
            "exposure_layers": exposure_layers_for_row(
                {
                    "configured": configured,
                    "hub_managed": hub_managed,
                    "cli_visible": cli_visible,
                    "protocol_smoke_supported": protocol_smoke_supported,
                },
                current_turn,
            ),
            "fallback": fallback,
            "probe_plan": probe_plan,
            "circuit_breaker": {
                "tripped": current_turn.get("state") in {"unstable", "unavailable"},
                "reason": current_turn.get("status", ""),
                "action": "do_not_call_this_tool_again_this_turn; use bounded fallback and record the observation",
            },
            "state": "unknown",
            "usable_state": "unknown",
            "notes": [],
        }
        if hub_managed:
            row["state"] = "hub_managed"
            row["notes"].append("Profile is intentionally absent from desktop MCP config; use the Hub route.")
            if configured:
                row["state"] = "hub_managed_configured"
                row["notes"].append("Profile is registered in desktop config despite Hub ownership; remove through the config owner.")
        elif not configured:
            row["state"] = "config_missing"
            row["notes"].append("MCP is in baseline but not in live config.")
        elif not cli.get("ok"):
            row["state"] = "cli_unverified"
            row["notes"].append("codex mcp list did not run successfully.")
        elif not cli_visible:
            row["state"] = "cli_not_visible"
            row["notes"].append("Live Codex CLI MCP list does not include this server.")
        elif on_demand_stdio and len(group_processes) == 0:
            row["state"] = "cli_visible_on_demand_idle"
            row["notes"].append("CLI lists the MCP. No runtime process is expected until the client starts this on-demand stdio server.")
        elif row["runtime_process_expected_now"] and len(group_processes) == 0:
            row["state"] = "visible_but_runtime_expected_missing"
            row["notes"].append("CLI lists the MCP, and this profile expects a local runtime process now, but none is visible.")
        else:
            row["state"] = "visible"
        row["usable_state"] = usable_state_for_row(row, current_turn)
        rows.append(row)

    return {
        "schema": "tool-exposure.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "baseline_path": str(BASELINE_PATH),
        "config_path": str(CODEX_CONFIG),
        "codex_mcp_list": cli,
        "thread_surface": thread_surface_snapshot(thread_id, str(cli.get("cli_version") or "")),
        "current_turn_anchor": anchor,
        "current_turn_tool_observations": current_turn_observations,
        "mcp": rows,
        "process_snapshot_ok": process.get("ok"),
        "session_exposure": {
            "state": "session_probe_required",
            "note": "This CLI can verify config, Codex CLI visibility, and local process evidence. Current model-turn tool exposure must still be confirmed by the active Codex session/tool surface.",
        },
        "dry_run_contract": {
            "writes_files": False,
            "starts_processes": False,
            "kills_processes": False,
            "changes_config": False,
        },
        "current_turn_stability_contract": {
            "service_ok_is_not_current_turn_callable": True,
            "probe_before_reporting_usable": True,
            "controlled_rebind_max_attempts_per_incident": 1,
            "rebind_path": "approved Codex Desktop restart/start-codex-desktop-elevated.ps1 followed by a new-turn probe",
        },
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    issues: list[dict[str, Any]] = []
    reported_current_turn_negative: set[str] = set()
    for row in snap.get("mcp", []) if isinstance(snap.get("mcp"), list) else []:
        if not isinstance(row, dict):
            continue
        state = str(row.get("state") or "")
        severity = ""
        if state == "config_missing":
            severity = "risk"
        elif state in {"cli_unverified", "cli_not_visible"}:
            severity = "risk"
        elif state == "visible_but_runtime_expected_missing":
            severity = "advisory"
        if severity:
            issues.append(
                {
                    "severity": severity,
                    "code": f"tool_exposure_{state}",
                    "tool": row.get("name"),
                    "transport": row.get("transport"),
                    "detail": row,
                }
            )
        current_turn = row.get("current_turn") if isinstance(row.get("current_turn"), dict) else {}
        current_state = str(current_turn.get("state") or "")
        if current_state in {"unstable", "unavailable"}:
            reported_current_turn_negative.add(str(row.get("name") or ""))
            issues.append(
                {
                    "severity": "risk",
                    "code": f"tool_exposure_current_turn_{current_state}",
                    "tool": row.get("name"),
                    "transport": "codex-session",
                    "detail": {
                        "current_turn": current_turn,
                        "fallback": row.get("fallback"),
                        "circuit_breaker": row.get("circuit_breaker"),
                        "usable_state": row.get("usable_state"),
                    },
                }
            )
        elif current_state == "unverified" and row.get("cli_visible"):
            probe_plan = row.get("probe_plan") if isinstance(row.get("probe_plan"), dict) else {}
            issues.append(
                {
                    "severity": "advisory",
                    "code": "tool_exposure_current_turn_probe_required",
                    "tool": row.get("name"),
                    "transport": row.get("transport"),
                    "detail": {
                        "probe_plan": probe_plan,
                        "tool_search_query": probe_plan.get("tool_search_query", ""),
                        "warmup_required": bool(probe_plan.get("warmup_required")),
                        "warmup_instruction": (
                            f"Call tool_search with query: {probe_plan.get('tool_search_query')}"
                            if probe_plan.get("tool_search_query")
                            else "Call tool_search with exact MCP namespace/tool names before recording a binding failure."
                        ),
                        "fallback": row.get("fallback"),
                        "usable_state": row.get("usable_state"),
                    },
                }
            )
    thread_surface = snap.get("thread_surface") if isinstance(snap.get("thread_surface"), dict) else {}
    current_turn_observations = (
        snap.get("current_turn_tool_observations")
        if isinstance(snap.get("current_turn_tool_observations"), dict)
        else {}
    )
    current_turn_positive_count = int(current_turn_observations.get("positive_count") or 0)
    current_turn_negative_profiles = (
        current_turn_observations.get("negative_profiles")
        if isinstance(current_turn_observations.get("negative_profiles"), dict)
        else {}
    )
    for profile_name, observation in current_turn_negative_profiles.items():
        if profile_name in reported_current_turn_negative:
            continue
        issues.append(
            {
                "severity": "risk",
                "code": "codex_current_turn_tool_unavailable",
                "tool": profile_name,
                "transport": "codex-session",
                "detail": {
                    "observation": observation,
                    "interpretation": "The MCP may be configured and CLI-visible, but this active Codex turn recorded a later negative tool-surface observation than any positive one.",
                },
            }
        )
    if thread_surface.get("state") in {"thread_runtime_surface_drift", "thread_cli_version_stale"}:
        if current_turn_positive_count > 0:
            issues.append(
                {
                    "severity": "advisory",
                    "code": "codex_thread_metadata_stale_but_current_turn_callable",
                    "tool": "*",
                    "transport": "codex-session",
                    "detail": {
                        "thread_surface": thread_surface,
                        "current_turn_tool_observations": current_turn_observations,
                    },
                }
            )
        else:
            issues.append(
                {
                    "severity": "risk",
                    "code": "codex_thread_runtime_surface_drift",
                    "tool": "*",
                    "transport": "codex-session",
                    "detail": thread_surface,
                }
            )
    elif thread_surface.get("state") == "thread_dynamic_tools_empty":
        issues.append(
            {
                "severity": "advisory",
                "code": "codex_thread_dynamic_tools_empty",
                "tool": "*",
                "transport": "codex-session",
                "detail": thread_surface,
            }
        )
    return {
        "schema": "tool-exposure.doctor.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "summary": {
            "configured_count": sum(1 for row in snap.get("mcp", []) if isinstance(row, dict) and row.get("configured")),
            "cli_visible_count": sum(1 for row in snap.get("mcp", []) if isinstance(row, dict) and row.get("cli_visible")),
            "usable_current_turn_count": sum(1 for row in snap.get("mcp", []) if isinstance(row, dict) and row.get("usable_state") == "usable_current_turn"),
            "probe_required_count": sum(1 for row in snap.get("mcp", []) if isinstance(row, dict) and row.get("usable_state") == "probe_required_before_claiming_usable"),
            "fallback_required_count": sum(1 for row in snap.get("mcp", []) if isinstance(row, dict) and str(row.get("usable_state") or "").endswith("use_fallback")),
            "circuit_breaker_tripped_count": sum(
                1
                for row in snap.get("mcp", [])
                if isinstance(row, dict)
                and isinstance(row.get("circuit_breaker"), dict)
                and row.get("circuit_breaker", {}).get("tripped")
            ),
            "issue_count": len(issues),
            "risk_count": sum(1 for item in issues if item.get("severity") == "risk"),
            "session_probe_required": True,
            "thread_surface_state": thread_surface.get("state", "not_requested"),
            "current_turn_positive_observed_count": current_turn_positive_count,
            "current_turn_negative_observed_count": len(current_turn_negative_profiles),
        },
        "snapshot": snap,
    }


def repair_plan(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = doctor(snap)
    actions: list[dict[str, Any]] = []
    for issue in doc.get("issues", []):
        code = str(issue.get("code") or "")
        tool = str(issue.get("tool") or "")
        action: dict[str, Any] = {
            "id": f"review_{code}_{tool}",
            "tool": tool,
            "source_issue": issue,
            "dry_run_only": True,
            "would_mutate": "nothing",
        }
        if code == "tool_exposure_config_missing":
            action["candidate_commands"] = [
                "python _bridge\\codex_config_guard.py repair-plan",
                "python _bridge\\codex_config_guard.py run-once --apply",
            ]
            action["guardrails"] = ["merge-only repair", "backup before writes", "restart Codex after config repair"]
        elif code in {"tool_exposure_cli_unverified", "tool_exposure_cli_not_visible"}:
            action["candidate_commands"] = [
                "python _bridge\\codex_config_guard.py doctor --run-cli",
                "restart Codex Desktop after confirming config is healthy",
            ]
            action["guardrails"] = ["do not edit config if codex_config_guard is healthy", "treat current session exposure as stale until restart"]
        elif code == "codex_thread_runtime_surface_drift":
            action["candidate_commands"] = [
                "python _bridge\\codex_config_guard.py doctor --run-cli",
                "python _bridge\\mcp_session_doctor.py doctor --run-smoke --smoke-profile <profile>",
                "Use current-task fallbacks, then perform a controlled Codex Desktop restart via start-codex-desktop-elevated.ps1 to force a fresh turn binding if the active session still does not expose the tool surface.",
            ]
            action["guardrails"] = [
                "do not hand-edit thread_dynamic_tools",
                "do not restart individual MCPs when protocol smoke is healthy",
                "use profile fallback for the current task before controlled session restart",
                "treat current-turn tool visibility as a session binding fault, not a service outage",
            ]
        elif code == "codex_thread_dynamic_tools_empty":
            action["candidate_commands"] = [
                "confirm with tool_search in the active turn",
                "if MCP tools are missing, use local fallback and refresh Codex session after the turn",
            ]
            action["guardrails"] = ["empty thread_dynamic_tools alone is advisory; current turn callable evidence decides severity"]
        elif code == "tool_exposure_current_turn_probe_required":
            detail = issue.get("detail") if isinstance(issue.get("detail"), dict) else {}
            probe_plan = detail.get("probe_plan") if isinstance(detail.get("probe_plan"), dict) else {}
            warmup_query = str(detail.get("tool_search_query") or probe_plan.get("tool_search_query") or "").strip()
            action["candidate_commands"] = [
                f"tool_search query={warmup_query}" if warmup_query else "tool_search query=<exact MCP namespace/tool names>",
                str(probe_plan.get("probe") or "call a read-only MCP probe in the active Codex turn"),
                str(probe_plan.get("success_record") or "record current_turn_callable only after the real tool call returns"),
                str(probe_plan.get("failure_record") or "record tool_unbound only after exact warmup still fails"),
            ]
            action["guardrails"] = [
                "do not treat generic tool_search miss as a service failure",
                "do not run process cleanup for a deferred tool-surface warmup miss",
                "record success only after a real current-turn tool call returns",
                "record failure only after exact tool_search warmup still cannot expose the namespace",
            ]
        else:
            action["candidate_commands"] = [
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process startup-sources",
                "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py resource-process cleanup",
            ]
            action["guardrails"] = ["cleanup defaults to dry-run", "do not stop protected or active sessions"]
        actions.append(action)
    return {
        "schema": "tool-exposure.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "doctor_ok": doc.get("ok"),
        "action_count": len(actions),
        "actions": actions,
        "dry_run_contract": {
            "writes_files": False,
            "starts_processes": False,
            "kills_processes": False,
            "changes_config": False,
        },
    }


def metrics(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    rows = [row for row in snap.get("mcp", []) if isinstance(row, dict)]
    return {
        "schema": "tool-exposure.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "configured_count": sum(1 for row in rows if row.get("configured")),
        "cli_visible_count": sum(1 for row in rows if row.get("cli_visible")),
        "config_missing_count": sum(1 for row in rows if row.get("state") == "config_missing"),
        "hub_managed_count": sum(1 for row in rows if row.get("hub_managed")),
        "cli_not_visible_count": sum(1 for row in rows if row.get("state") == "cli_not_visible"),
        "on_demand_idle_count": sum(1 for row in rows if row.get("state") == "cli_visible_on_demand_idle"),
        "runtime_expected_missing_count": sum(1 for row in rows if row.get("state") == "visible_but_runtime_expected_missing"),
        "usable_current_turn_count": sum(1 for row in rows if row.get("usable_state") == "usable_current_turn"),
        "probe_required_count": sum(1 for row in rows if row.get("usable_state") == "probe_required_before_claiming_usable"),
        "fallback_required_count": sum(1 for row in rows if str(row.get("usable_state") or "").endswith("use_fallback")),
        "circuit_breaker_tripped_count": sum(
            1
            for row in rows
            if isinstance(row.get("circuit_breaker"), dict)
            and row.get("circuit_breaker", {}).get("tripped")
        ),
        "fallback_defined_count": sum(
            1
            for row in rows
            if isinstance(row.get("fallback"), dict)
            and row.get("fallback", {}).get("available")
        ),
        "protocol_smoke_supported_count": sum(1 for row in rows if row.get("protocol_smoke_supported")),
        "usable_state_counts": {
            state: sum(1 for row in rows if row.get("usable_state") == state)
            for state in sorted({str(row.get("usable_state") or "unknown") for row in rows})
        },
        "session_probe_required": True,
        "thread_surface_state": (snap.get("thread_surface") or {}).get("state") if isinstance(snap.get("thread_surface"), dict) else "not_requested",
    }


def validate(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = doctor(snap)
    failures = [item for item in doc.get("issues", []) if item.get("severity") == "risk"]
    return {
        "schema": "tool-exposure.validate.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "failures": failures,
        "advisory_count": sum(1 for item in doc.get("issues", []) if item.get("severity") == "advisory"),
        "note": "This validates config/CLI/process exposure plus recorded current-turn tool-surface failures; a MCP is usable only after a fresh active-turn success observation.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Codex tool exposure doctor")
    parser.add_argument("command", choices=["snapshot", "doctor", "repair-plan", "validate", "metrics"])
    parser.add_argument("--thread-id", default="", help="Optional Codex thread id to inspect for runtime/tool-surface drift.")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        payload = snapshot(args.thread_id or None)
    elif args.command == "doctor":
        payload = doctor(snapshot(args.thread_id or None))
    elif args.command == "repair-plan":
        payload = repair_plan(snapshot(args.thread_id or None))
    elif args.command == "validate":
        payload = validate(snapshot(args.thread_id or None))
    else:
        payload = metrics(snapshot(args.thread_id or None))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
