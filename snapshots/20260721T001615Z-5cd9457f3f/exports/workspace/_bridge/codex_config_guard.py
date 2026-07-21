#!/usr/bin/env python3
"""Persistent guard for Codex config drift.

The guard is deliberately additive: it compares the live Codex config against
the declared startup baseline, then delegates writes to codex_state_repair.py.
It never restores an old config wholesale and never removes user-added entries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bounded_output import aggregate_validator_cli_payload, governed_cli_payload


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "_bridge"
CODEX_HOME = Path.home() / ".codex"
CODEX_CONFIG = CODEX_HOME / "config.toml"
LOG_DIR = BRIDGE_ROOT / "logs" / "codex-config-guard"
RUNTIME_DIR = BRIDGE_ROOT / "runtime" / "codex_config_guard"
DESKTOP_MODEL_REFRESH_STATE = RUNTIME_DIR / "desktop_model_refresh_state.json"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

import codex_state_audit  # noqa: E402
import codex_state_repair  # noqa: E402
import codex_config_projection  # noqa: E402
import codex_session_store_doctor  # noqa: E402
import codex_desktop_model_runtime  # noqa: E402
from shared.codex_desktop_package import codex_process_family_running as process_family_running  # noqa: E402
from shared.process_liveness import process_is_alive as _shared_process_is_alive  # noqa: E402


CRITICAL_PREFIXES = (
    "global_config_parse",
    "expected_mcp_registered",
    "expected_mcp_required_flag_",
    "decommissioned_mcp_not_configured",
    "hub_managed_mcp_not_configured",
    "expected_plugins_enabled",
    "expected_marketplaces_present",
    "marketplace_",
    "codex_runtime_cli_path_",
    "global_value_",
    "memories_enabled",
    "project_value_",
    "project_scope_",
    "global_state_rule_",
)

BASELINE_CONVERGENCE_PREFIXES = (
    "baseline_covers_",
)

PROCESS_MANAGER_RETENTION_HOURS = 24
PROCESS_MANAGER_RECENT_GRACE_MINUTES = 30
STARTUP_SETTLING_SECONDS = 180
APP_SERVER_MODEL_LIST_TIMEOUT_SECONDS = 12
WINDOWS_ERROR_ELEVATION_REQUIRED = 740
LEGACY_SELECTABLE_MODELS = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
)
CC_SWITCH_CATALOG_NAME = "cc-switch-model-catalog.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(event: str, payload: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {"ts": utc_now(), "event": event, **payload}
    path = LOG_DIR / f"{datetime.now().strftime('%Y%m')}.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def audit_checks(*, run_cli: bool = False) -> list[dict[str, Any]]:
    return [item.__dict__ for item in codex_state_audit.build_checks(run_cli=run_cli)]


def desktop_wsl_enabled_for_startup() -> bool:
    """Read the native Desktop WSL switch without relying on repair drift."""
    try:
        config = safe_toml(CODEX_CONFIG)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    desktop = config.get("desktop") if isinstance(config, dict) else None
    return bool(isinstance(desktop, dict) and desktop.get("runCodexInWindowsSubsystemForLinux"))


def failed_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in checks if not bool(item.get("ok"))]


def critical_failures(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = failed_checks(checks)
    critical: list[dict[str, Any]] = []
    for item in failures:
        name = str(item.get("name") or "")
        if name == "codex_mcp_list_runs" or name.startswith("codex_mcp_list_has_"):
            continue
        if name.startswith(CRITICAL_PREFIXES):
            critical.append(item)
    return critical


def codex_process_family_min_age_seconds() -> float | None:
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "$now=Get-Date; "
                "$p=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { "
                "($_.Name -in @('ChatGPT.exe','Codex.exe','codex.exe')) -and ("
                "  ($_.ExecutablePath -like '*\\OpenAI.Codex_*\\app\\ChatGPT.exe') -or "
                "  ($_.ExecutablePath -like '*\\OpenAI.Codex_*\\app\\Codex.exe') -or "
                "  ($_.ExecutablePath -like '*\\OpenAI.Codex_*\\app\\resources\\codex.exe') -or "
                "  ($_.CommandLine -like '*\\OpenAI.Codex_*\\app\\ChatGPT.exe*') -or "
                "  ($_.CommandLine -like '*\\OpenAI.Codex_*\\app\\Codex.exe*') -or "
                "  ($_.CommandLine -like '*\\OpenAI.Codex_*\\app\\resources\\codex.exe*') "
                ") "
                "}; "
                "if($p){ "
                "  ($p | ForEach-Object { ($now - $_.CreationDate).TotalSeconds } | "
                "    Measure-Object -Minimum).Minimum "
                "}",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception:
        return None
    text = (proc.stdout or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def startup_settling_failures(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify narrow post-startup config drift that should not trigger repair yet."""
    age_seconds = codex_process_family_min_age_seconds()
    if age_seconds is None or age_seconds > STARTUP_SETTLING_SECONDS:
        return []
    settling: list[dict[str, Any]] = []
    for item in failures:
        name = str(item.get("name") or "")
        detail = str(item.get("detail") or "")
        if name == "expected_mcp_required_flag_node_repl" and "required=None expected=True" in detail:
            settling.append(
                {
                    **item,
                    "settling_window_seconds": STARTUP_SETTLING_SECONDS,
                    "codex_process_min_age_seconds": age_seconds,
                    "settling_reason": "Codex app-owned node_repl config can be rewritten before async baseline repair converges.",
                }
            )
    return settling


def classify(checks: list[dict[str, Any]], *, include_startup_settling: bool = True) -> dict[str, Any]:
    failures = failed_checks(checks)
    settling = startup_settling_failures(failures) if include_startup_settling else []
    settling_names = {str(item.get("name") or "") for item in settling}
    critical = [
        item
        for item in critical_failures(checks)
        if str(item.get("name") or "") not in settling_names
    ]
    baseline_convergence_failures = [
        item
        for item in failures
        if str(item.get("name") or "").startswith(BASELINE_CONVERGENCE_PREFIXES)
    ]
    cli_failures = [
        item
        for item in failures
        if str(item.get("name") or "") == "codex_mcp_list_runs"
        or str(item.get("name") or "").startswith("codex_mcp_list_has_")
    ]
    return {
        "ok": not critical and not cli_failures and not baseline_convergence_failures,
        "critical_ok": not critical,
        "baseline_convergence_ok": not baseline_convergence_failures,
        "failure_count": len(failures),
        "critical_failure_count": len(critical),
        "startup_settling_failure_count": len(settling),
        "baseline_convergence_failure_count": len(baseline_convergence_failures),
        "cli_failure_count": len(cli_failures),
        "failed": failures,
        "critical": critical,
        "startup_settling": settling,
        "baseline_convergence_failures": baseline_convergence_failures,
        "cli_failures": cli_failures,
        "restart_required": bool(critical),
    }


def snapshot(*, run_cli: bool = False) -> dict[str, Any]:
    baseline = codex_state_repair.json.loads(codex_state_repair.BASELINE_PATH.read_text(encoding="utf-8"))
    global_config = Path(baseline["global_config"])
    project_config = Path(baseline["project_config"])
    state_path = Path(baseline.get("global_state", Path.home() / ".codex" / ".codex-global-state.json"))
    checks = audit_checks(run_cli=run_cli)
    status = classify(checks)
    global_config_data = safe_toml(global_config) if global_config.exists() else {}
    provider_model_list = provider_model_list_state(global_config_data)
    cc_switch_catalog = cc_switch_catalog_state(global_config_data, global_config, provider_model_list)
    desktop_app_model_list = desktop_app_model_list_state(
        global_config_data,
        global_config,
        provider_model_list,
        cc_switch_catalog,
    )
    catalog_path = model_catalog_path(global_config_data, global_config)
    desktop_runtime_model_state = codex_desktop_model_runtime.combined_state(
        expected_desktop_model_ids(global_config_data, provider_model_list, cc_switch_catalog),
        catalog_path,
    )
    return {
        "schema": "codex-config-guard/snapshot/v1",
        "ok": True,
        "generated_at": utc_now(),
        "baseline_path": str(codex_state_repair.BASELINE_PATH),
        "global_config": file_state(global_config),
        "project_config": file_state(project_config),
        "global_state": file_state(state_path),
        "desktop_model_refresh": desktop_model_refresh_state(global_config, app_model_list=desktop_app_model_list),
        "provider_model_list": provider_model_list,
        "cc_switch_catalog": cc_switch_catalog,
        "desktop_app_model_list": desktop_app_model_list,
        "desktop_runtime_model_state": desktop_runtime_model_state,
        "process_manager_state": process_manager_state_metrics(),
        "session_store": safe_session_store_metrics(),
        "config_projection": codex_config_projection.snapshot(),
        "audit": status,
        "checks": checks,
    }


def file_state(path: Path) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "path": str(path),
        "exists": exists,
        "bytes": int(stat.st_size) if stat else 0,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else "",
    }


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def desktop_app_server_processes() -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "$p=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { "
                "$_.Name -eq 'codex.exe' -and "
                "$_.CommandLine -like '*app-server*' -and "
                "$_.CommandLine -like '*--analytics-default-enabled*' -and "
                "(($_.ExecutablePath -like '*\\OpenAI.Codex_*\\app\\resources\\codex.exe') -or "
                " ($_.CommandLine -like '*\\OpenAI.Codex_*\\app\\resources\\codex.exe*'))"
                "}; "
                "$p | Select-Object ProcessId,"
                "@{Name='CreationDate';Expression={try{$_.CreationDate.ToUniversalTime().ToString('o')}catch{[string]$_.CreationDate}}},"
                "ExecutablePath,CommandLine | ConvertTo-Json -Depth 4",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
    except Exception:
        return []
    text = (proc.stdout or "").strip()
    if not text:
        return []
    try:
        raw = json.loads(text)
    except Exception:
        return []
    rows = raw if isinstance(raw, list) else [raw]
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        started = parse_datetime(row.get("CreationDate"))
        result.append(
            {
                "pid": row.get("ProcessId"),
                "started_at": started.isoformat() if started else "",
                "executable": row.get("ExecutablePath") or "",
                "command_line": row.get("CommandLine") or "",
            }
        )
    return result


def model_catalog_path(config: dict[str, Any], config_path: Path) -> Path | None:
    raw = config.get("model_catalog_json")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = config_path.parent / candidate
    return candidate


def active_model_provider_config(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    provider = config.get("model_provider")
    providers = config.get("model_providers") if isinstance(config.get("model_providers"), dict) else {}
    if not isinstance(provider, str) or not provider:
        return "", {}
    provider_config = providers.get(provider) if isinstance(providers, dict) else None
    return provider, provider_config if isinstance(provider_config, dict) else {}


def provider_config_fingerprint(provider: str, provider_config: dict[str, Any]) -> str:
    """Return a secret-free identity for provider-scoped derived state."""
    material = {
        "provider": provider,
        "name": provider_config.get("name") if isinstance(provider_config.get("name"), str) else "",
        "base_url": provider_config.get("base_url") if isinstance(provider_config.get("base_url"), str) else "",
        "wire_api": provider_config.get("wire_api") if isinstance(provider_config.get("wire_api"), str) else "",
        "requires_openai_auth": bool(provider_config.get("requires_openai_auth")),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def provider_models_endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/models"


def extract_provider_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("data")
    if not isinstance(raw, list):
        raw = payload.get("models")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        model_id = ""
        if isinstance(item, str):
            model_id = item.strip()
        elif isinstance(item, dict):
            for key in ("id", "model", "slug", "name"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    model_id = value.strip()
                    break
        if model_id and model_id not in seen:
            seen.add(model_id)
            result.append(model_id)
    return result


def provider_model_list_state(config: dict[str, Any]) -> dict[str, Any]:
    """Probe the active provider's model list without exposing auth material."""
    provider, provider_config = active_model_provider_config(config)
    current_model = config.get("model") if isinstance(config.get("model"), str) else ""
    base_url = provider_config.get("base_url") if isinstance(provider_config.get("base_url"), str) else ""
    endpoint = provider_models_endpoint(base_url)
    state: dict[str, Any] = {
        "schema": "codex-config-guard/provider-model-list/v1",
        "ok": True,
        "provider": provider,
        "provider_name": str(provider_config.get("name") or "") if provider_config else "",
        "configured_model": current_model,
        "base_url": base_url,
        "endpoint": endpoint,
        "skipped": False,
        "degraded": False,
        "authoritative": False,
        "usable": False,
        "discovery_capability": "unknown",
        "reason": "",
        "http_status": 0,
        "model_count": 0,
        "model_ids": [],
        "configured_model_present": False,
    }
    if not provider:
        return {**state, "ok": False, "skipped": True, "reason": "model_provider_missing"}
    if not base_url:
        return {**state, "ok": False, "skipped": True, "reason": "provider_base_url_missing"}
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        return {**state, "ok": True, "skipped": True, "reason": "provider_base_url_not_http"}
    if not endpoint:
        return {**state, "ok": False, "skipped": True, "reason": "provider_models_endpoint_missing"}
    try:
        request = urllib.request.Request(endpoint, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:
            status = int(getattr(response, "status", 0) or 0)
            body = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        return {
            **state,
            "ok": False,
            "degraded": True,
            "discovery_capability": "http_error",
            "http_status": int(exc.code or 0),
            "reason": "provider_models_http_error",
            "error": str(exc.reason or exc),
        }
    except Exception as exc:
        return {**state, "ok": False, "degraded": True, "discovery_capability": "unreachable", "reason": "provider_models_unreachable", "error": repr(exc)}
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        return {**state, "ok": False, "degraded": True, "discovery_capability": "invalid_response", "http_status": status, "reason": "provider_models_parse_failed", "error": repr(exc)}
    model_ids = extract_provider_model_ids(payload)
    configured_present = bool(current_model and current_model in model_ids)
    if not model_ids:
        return {
            **state,
            "ok": True,
            "degraded": True,
            "authoritative": False,
            "usable": False,
            "discovery_capability": "empty",
            "http_status": status,
            "reason": "provider_models_empty_non_authoritative",
            "model_count": 0,
            "model_ids": [],
            "configured_model_present": False,
        }
    return {
        **state,
        "ok": configured_present if current_model else True,
        "authoritative": True,
        "usable": True,
        "discovery_capability": "supported",
        "http_status": status,
        "reason": "" if configured_present or not current_model else "configured_model_missing_from_provider_models",
        "model_count": len(model_ids),
        "model_ids": model_ids,
        "configured_model_present": configured_present,
    }


def catalog_normalization_disabled(path: Path | None) -> dict[str, Any]:
    return {
        "ok": True,
        "changed": False,
        "skipped": True,
        "path": str(path) if path else "",
        "reason": "catalog_normalization_disabled_by_default",
        "policy": "Desktop model choices must come from the active provider/app-server model list; local catalog mutation is diagnostic/manual only.",
        "issues": desktop_ui_catalog_issues(path),
    }


def read_catalog_slugs(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    slugs: list[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        for key in ("slug", "model", "id", "name"):
            slug = item.get(key)
            if isinstance(slug, str) and slug:
                slugs.append(slug)
                break
    return slugs


def cc_switch_catalog_state(
    config: dict[str, Any],
    config_path: Path,
    provider_models: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Diagnose the CC Switch catalog link used by its local Codex proxy."""
    provider, provider_config = active_model_provider_config(config)
    base_url = provider_config.get("base_url") if isinstance(provider_config.get("base_url"), str) else ""
    raw_catalog = config.get("model_catalog_json") if isinstance(config.get("model_catalog_json"), str) else ""
    references_cc_catalog = Path(raw_catalog).name == CC_SWITCH_CATALOG_NAME if raw_catalog else False
    cc_switch_url = "127.0.0.1:15721" in base_url or "localhost:15721" in base_url
    default_catalog = config_path.parent / CC_SWITCH_CATALOG_NAME
    state: dict[str, Any] = {
        "schema": "codex-config-guard/cc-switch-catalog/v1",
        "ok": True,
        "skipped": False,
        "provider": provider,
        "provider_fingerprint": provider_config_fingerprint(provider, provider_config),
        "base_url": base_url,
        "model_catalog_json": raw_catalog,
        "expected_catalog_name": CC_SWITCH_CATALOG_NAME,
        "default_catalog_path": str(default_catalog),
        "active_catalog_path": "",
        "applicable": False,
        "catalog_exists": False,
        "catalog_model_count": 0,
        "catalog_models": [],
        "provider_model_count": int((provider_models or {}).get("model_count") or 0),
        "reason": "",
    }
    if not (cc_switch_url or references_cc_catalog):
        return {**state, "ok": True, "skipped": True, "reason": "active_provider_not_cc_switch"}
    if not references_cc_catalog:
        ignored_models = read_catalog_slugs(default_catalog)
        return {
            **state,
            "ok": True,
            "skipped": True,
            "reason": "cc_switch_auxiliary_catalog_not_referenced",
            "catalog_exists": default_catalog.exists(),
            "ignored_catalog_model_count": len(ignored_models),
            "ignored_catalog_models": ignored_models,
            "repair_hint": "The orphan catalog is ignored because the active provider config does not reference it. Reapply the provider through CC Switch when a catalog-backed picker is intended.",
        }
    catalog_path = model_catalog_path(config, config_path)
    catalog_models = read_catalog_slugs(catalog_path)
    return {
        **state,
        "ok": bool(catalog_path and catalog_path.exists() and catalog_models),
        "applicable": True,
        "active_catalog_path": str(catalog_path) if catalog_path else "",
        "catalog_exists": bool(catalog_path and catalog_path.exists()),
        "catalog_model_count": len(catalog_models),
        "catalog_models": catalog_models,
        "reason": "" if catalog_path and catalog_path.exists() and catalog_models else "cc_switch_model_catalog_missing_or_empty",
    }


def expected_desktop_model_ids(
    config: dict[str, Any],
    provider_models: dict[str, Any] | None = None,
    cc_switch_catalog: dict[str, Any] | None = None,
) -> list[str]:
    ids: list[str] = []
    if cc_switch_catalog and bool(cc_switch_catalog.get("applicable")) and not bool(cc_switch_catalog.get("skipped")):
        catalog_models = cc_switch_catalog.get("catalog_models")
        if isinstance(catalog_models, list):
            ids.extend(str(item) for item in catalog_models if isinstance(item, str) and item.strip())
    if not ids and provider_models:
        model_ids = provider_models.get("model_ids")
        if isinstance(model_ids, list):
            ids.extend(str(item) for item in model_ids if isinstance(item, str) and item.strip())
    current_model = config.get("model")
    if isinstance(current_model, str) and current_model.strip():
        ids.append(current_model.strip())
    result: list[str] = []
    seen: set[str] = set()
    for model_id in ids:
        if model_id not in seen:
            seen.add(model_id)
            result.append(model_id)
    return result


def desktop_model_list_host_candidates(config: dict[str, Any]) -> list[str]:
    candidates: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            item = value.strip()
            if item and item not in candidates:
                candidates.append(item)

    # Desktop front-end calls model/list with a hostId. Probe the likely host ids
    # instead of treating "local" as a complete proof of UI visibility.
    add("local")
    provider = config.get("model_provider")
    add(provider)
    providers = config.get("model_providers")
    if isinstance(providers, dict) and isinstance(provider, str):
        provider_config = providers.get(provider)
        if isinstance(provider_config, dict):
            add(provider_config.get("name"))
    for fallback in ("custom", "chatgpt"):
        add(fallback)
    return candidates


def desktop_app_server_executable() -> Path | None:
    for process in desktop_app_server_processes():
        raw = process.get("executable") if isinstance(process, dict) else ""
        if isinstance(raw, str) and raw:
            path = Path(raw)
            if path.exists():
                return path
    candidates: list[Path] = []
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")]
    for root in roots:
        if not root:
            continue
        try:
            candidates.extend(Path(root).glob(r"WindowsApps/OpenAI.Codex_*/app/resources/codex.exe"))
        except Exception:
            continue
    existing = [item for item in candidates if item.exists()]
    if not existing:
        return None
    return max(existing, key=lambda item: item.stat().st_mtime)


def app_server_model_id(item: dict[str, Any]) -> str:
    for key in ("id", "model", "slug", "name"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def app_server_reasoning_efforts(item: dict[str, Any]) -> set[str]:
    efforts: set[str] = set()
    for key in ("supportedReasoningEfforts", "supported_reasoning_levels"):
        raw = item.get(key)
        if not isinstance(raw, list):
            continue
        for effort in raw:
            if isinstance(effort, str) and effort.strip():
                efforts.add(effort.strip())
            elif isinstance(effort, dict):
                for effort_key in ("reasoningEffort", "effort", "value", "id"):
                    value = effort.get(effort_key)
                    if isinstance(value, str) and value.strip():
                        efforts.add(value.strip())
                        break
    return efforts


def app_server_reasoning_contract(item: dict[str, Any]) -> dict[str, Any]:
    """Validate only the reasoning choices declared by this model entry."""
    declaration_keys = ("supportedReasoningEfforts", "supported_reasoning_levels")
    declared = any(isinstance(item.get(key), list) for key in declaration_keys)
    efforts = app_server_reasoning_efforts(item)
    default_effort = ""
    for key in ("defaultReasoningEffort", "default_reasoning_level"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            default_effort = value.strip()
            break
    issues: list[str] = []
    if declared and default_effort and default_effort not in efforts:
        issues.append("default_reasoning_effort_not_declared")
    return {
        "ok": not issues,
        "declared": declared,
        "available_efforts": sorted(efforts),
        "selectable_efforts": sorted(effort for effort in efforts if effort != "none"),
        "default_effort": default_effort,
        "issues": issues,
    }


def extract_app_server_model_entries(payload: Any) -> list[dict[str, Any]]:
    result = payload.get("result") if isinstance(payload, dict) else None
    raw: Any = result
    if isinstance(result, dict):
        for key in ("models", "data", "items"):
            value = result.get(key)
            if isinstance(value, list):
                raw = value
                break
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def app_server_probe_route_unavailable(exc: BaseException) -> bool:
    """Return whether an independent app-server probe cannot cross the current privilege boundary."""
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) == WINDOWS_ERROR_ELEVATION_REQUIRED


def desktop_app_model_list_state(
    config: dict[str, Any],
    config_path: Path,
    provider_models: dict[str, Any] | None = None,
    cc_switch_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ask the Desktop app-server what model choices it actually exposes."""
    expected_models = expected_desktop_model_ids(config, provider_models, cc_switch_catalog)
    state: dict[str, Any] = {
        "schema": "codex-config-guard/desktop-app-model-list/v1",
        "ok": True,
        "queried": False,
        "skipped": False,
        "method": "independent_app_server_stdio",
        "config_path": str(config_path),
        "expected_models": expected_models,
        "host_candidates": desktop_model_list_host_candidates(config),
        "selected_host_id": "",
        "host_attempts": [],
        "visible_models": [],
        "visible_model_count": 0,
        "missing_expected_models": [],
        "supports_reasoning_effort_ok": True,
        "models_missing_reasoning_efforts": [],
        "reason": "",
    }
    if not expected_models:
        return {**state, "ok": True, "skipped": True, "reason": "no_expected_models"}
    exe = desktop_app_server_executable()
    if exe is None:
        return {**state, "ok": False, "skipped": True, "reason": "desktop_app_server_executable_not_found"}

    process: subprocess.Popen[str] | None = None
    lines: queue.Queue[tuple[str, str]] = queue.Queue()

    def reader(stream: Any, name: str) -> None:
        try:
            for line in stream:
                lines.put((name, line.rstrip("\n")))
        except Exception as exc:  # pragma: no cover - diagnostic only
            lines.put((name, f"reader_error:{exc!r}"))

    def send_request(request: dict[str, Any]) -> None:
        if process is None or process.stdin is None:
            raise RuntimeError("app_server_stdin_unavailable")
        process.stdin.write(json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def wait_for_response(request_id: str, timeout_seconds: float) -> tuple[dict[str, Any] | None, list[str]]:
        deadline = time.time() + timeout_seconds
        diagnostics: list[str] = []
        while time.time() < deadline:
            if process is not None and process.poll() is not None:
                diagnostics.append(f"process_exited:{process.returncode}")
                break
            try:
                stream_name, line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line and len(diagnostics) < 20:
                diagnostics.append(f"{stream_name}:{line[:500]}")
            if stream_name != "stdout" or not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("id") == request_id:
                return payload, diagnostics
        return None, diagnostics

    try:
        process = subprocess.Popen(
            [str(exe), "app-server", "--analytics-default-enabled"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(exe.parent),
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if process.stdout is not None:
            threading.Thread(target=reader, args=(process.stdout, "stdout"), daemon=True).start()
        if process.stderr is not None:
            threading.Thread(target=reader, args=(process.stderr, "stderr"), daemon=True).start()
        initialize_id = "initialize:codex-config-guard"
        model_list_id = "model/list:codex-config-guard"
        send_request(
            {
                "id": initialize_id,
                "method": "initialize",
                "params": {"clientInfo": {"name": "codex-config-guard", "version": "1"}},
            }
        )
        initialize_payload, initialize_diagnostics = wait_for_response(initialize_id, 4)
        if not initialize_payload or initialize_payload.get("error"):
            return {
                **state,
                "ok": False,
                "queried": True,
                "app_server_executable": str(exe),
                "reason": "desktop_app_server_initialize_failed",
                "error": initialize_payload.get("error") if isinstance(initialize_payload, dict) else "initialize_timeout",
                "diagnostics": initialize_diagnostics,
            }
        host_attempts: list[dict[str, Any]] = []
        best_attempt: dict[str, Any] | None = None
        selected_attempt: dict[str, Any] | None = None
        for index, host_id in enumerate(desktop_model_list_host_candidates(config)):
            request_id = f"{model_list_id}:{index}"
            send_request(
                {
                    "id": request_id,
                    "method": "model/list",
                    "params": {"hostId": host_id, "includeHidden": True, "cursor": None, "limit": 200},
                }
            )
            payload, diagnostics = wait_for_response(request_id, APP_SERVER_MODEL_LIST_TIMEOUT_SECONDS)
            attempt: dict[str, Any] = {
                "host_id": host_id,
                "ok": False,
                "visible_models": [],
                "visible_model_count": 0,
                "missing_expected_models": list(expected_models),
                "supports_reasoning_effort_ok": False,
                "models_missing_reasoning_efforts": [],
                "reason": "",
            }
            if not payload:
                attempt.update({"reason": "desktop_app_model_list_timeout", "diagnostics": diagnostics})
            elif payload.get("error"):
                attempt.update({"reason": "desktop_app_model_list_error", "error": payload.get("error"), "diagnostics": diagnostics})
            else:
                entries = extract_app_server_model_entries(payload)
                by_id = {app_server_model_id(item): item for item in entries if app_server_model_id(item)}
                visible_models = [model_id for model_id, item in by_id.items() if item.get("hidden") is not True]
                missing_expected = [model_id for model_id in expected_models if model_id not in visible_models]
                missing_efforts: list[dict[str, Any]] = []
                for model_id in expected_models:
                    if model_id not in by_id:
                        continue
                    contract = app_server_reasoning_contract(by_id[model_id])
                    if not contract.get("ok"):
                        missing_efforts.append({"model": model_id, **contract})
                attempt.update(
                    {
                        "ok": not missing_expected and not missing_efforts,
                        "visible_models": visible_models,
                        "visible_model_count": len(visible_models),
                        "missing_expected_models": missing_expected,
                        "supports_reasoning_effort_ok": not missing_efforts,
                        "models_missing_reasoning_efforts": missing_efforts,
                        "reason": "" if not missing_expected and not missing_efforts else "desktop_app_model_list_missing_expected_models_or_reasoning_efforts",
                    }
                )
            host_attempts.append(attempt)
            if best_attempt is None or int(attempt.get("visible_model_count") or 0) > int(best_attempt.get("visible_model_count") or 0):
                best_attempt = attempt
            if attempt.get("ok") is True:
                selected_attempt = selected_attempt or attempt
        if selected_attempt is not None:
            return {
                **state,
                "ok": True,
                "queried": True,
                "app_server_executable": str(exe),
                "selected_host_id": str(selected_attempt.get("host_id") or ""),
                "host_attempts": host_attempts,
                "visible_models": selected_attempt.get("visible_models", []),
                "visible_model_count": selected_attempt.get("visible_model_count", 0),
                "missing_expected_models": [],
                "supports_reasoning_effort_ok": True,
                "models_missing_reasoning_efforts": [],
                "reason": "",
            }
    except Exception as exc:
        if app_server_probe_route_unavailable(exc):
            return {
                **state,
                "ok": False,
                "queried": True,
                "skipped": True,
                "route_unavailable": True,
                "app_server_executable": str(exe),
                "reason": "desktop_app_model_list_probe_route_unavailable",
                "winerror": WINDOWS_ERROR_ELEVATION_REQUIRED,
                "error": repr(exc),
            }
        return {**state, "ok": False, "queried": True, "app_server_executable": str(exe), "reason": "desktop_app_model_list_probe_failed", "error": repr(exc)}
    finally:
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except Exception:
                pass

    best_attempt = best_attempt or {}
    return {
        **state,
        "ok": False,
        "queried": True,
        "app_server_executable": str(exe),
        "selected_host_id": str(best_attempt.get("host_id") or ""),
        "host_attempts": host_attempts,
        "visible_models": best_attempt.get("visible_models", []),
        "visible_model_count": best_attempt.get("visible_model_count", 0),
        "missing_expected_models": best_attempt.get("missing_expected_models", list(expected_models)),
        "supports_reasoning_effort_ok": bool(best_attempt.get("supports_reasoning_effort_ok")),
        "models_missing_reasoning_efforts": best_attempt.get("models_missing_reasoning_efforts", []),
        "reason": "desktop_app_model_list_no_host_exposes_expected_models_or_reasoning_efforts",
    }


def read_catalog_models(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    return [item for item in models if isinstance(item, dict)]


def desktop_ui_model_id(item: dict[str, Any]) -> str:
    for key in ("model", "slug"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def desktop_ui_reasoning_efforts(item: dict[str, Any]) -> list[dict[str, str]]:
    raw = item.get("supportedReasoningEfforts")
    if isinstance(raw, list):
        efforts: list[dict[str, str]] = []
        for effort in raw:
            if not isinstance(effort, dict):
                continue
            reasoning_effort = effort.get("reasoningEffort")
            if isinstance(reasoning_effort, str) and reasoning_effort:
                efforts.append(
                    {
                        "reasoningEffort": reasoning_effort,
                        "description": str(effort.get("description") or reasoning_effort),
                    }
                )
        if efforts:
            return efforts
    raw = item.get("supported_reasoning_levels")
    if not isinstance(raw, list):
        return []
    efforts = []
    for effort in raw:
        if not isinstance(effort, dict):
            continue
        reasoning_effort = effort.get("effort")
        if isinstance(reasoning_effort, str) and reasoning_effort:
            efforts.append(
                {
                    "reasoningEffort": reasoning_effort,
                    "description": str(effort.get("description") or reasoning_effort),
                }
            )
    return efforts


def normalize_catalog_for_desktop_ui(path: Path | None) -> dict[str, Any]:
    """Add Desktop WebView model-list fields while preserving CLI fields."""
    if not path or not path.exists():
        return {"ok": False, "changed": False, "reason": "catalog_missing", "path": str(path) if path else ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "changed": False, "reason": "catalog_parse_failed", "error": str(exc), "path": str(path)}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return {"ok": False, "changed": False, "reason": "catalog_models_missing", "path": str(path)}
    changed = False
    touched: list[str] = []
    existing = {desktop_ui_model_id(item) for item in models if isinstance(item, dict)}
    next_priority = max([int(item.get("priority") or 0) for item in models if isinstance(item, dict)] or [999]) + 1
    for model_id in LEGACY_SELECTABLE_MODELS:
        if model_id in existing:
            continue
        models.append(
            {
                "slug": model_id,
                "model": model_id,
                "display_name": model_id,
                "displayName": model_id,
                "description": model_id,
                "default_reasoning_level": "high",
                "defaultReasoningEffort": "high",
                "supported_reasoning_levels": codex_desktop_model_runtime.catalog_reasoning_entries(),
                "supportedReasoningEfforts": codex_desktop_model_runtime.desktop_reasoning_entries(),
                "shell_type": "shell_command",
                "visibility": "list",
                "hidden": False,
                "isDefault": False,
                "supported_in_api": True,
                "priority": next_priority,
                "additional_speed_tiers": [],
                "service_tiers": [],
                "availability_nux": None,
                "upgrade": None,
                "base_instructions": "You are Codex, a coding agent. You and the user share the same workspace and collaborate to achieve the user's goals.",
                "include_skills_usage_instructions": False,
                "supports_reasoning_summaries": True,
                "default_reasoning_summary": "none",
                "support_verbosity": False,
                "default_verbosity": None,
                "apply_patch_tool_type": None,
                "web_search_tool_type": "text",
                "truncation_policy": {"mode": "bytes", "limit": 10000},
                "supports_parallel_tool_calls": False,
                "supports_image_detail_original": False,
                "context_window": 128000,
                "max_context_window": 128000,
                "effective_context_window_percent": 95,
                "experimental_supported_tools": [],
                "input_modalities": ["text"],
                "supports_search_tool": False,
                "use_responses_lite": False,
            }
        )
        existing.add(model_id)
        touched.append(model_id)
        changed = True
        next_priority += 1
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = desktop_ui_model_id(item)
        if not model_id:
            continue
        before = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        item.setdefault("slug", model_id)
        item.setdefault("model", model_id)
        item.setdefault("display_name", model_id)
        item.setdefault("displayName", item.get("display_name") or model_id)
        item.setdefault("description", item.get("display_name") or model_id)
        item.setdefault("hidden", False)
        if "isDefault" not in item:
            item["isDefault"] = False
        default_effort = item.get("defaultReasoningEffort") or item.get("default_reasoning_level")
        if isinstance(default_effort, str) and default_effort:
            item["defaultReasoningEffort"] = default_effort
        efforts = desktop_ui_reasoning_efforts(item)
        if efforts:
            item["supportedReasoningEfforts"] = efforts
        after = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if after != before:
            changed = True
            touched.append(model_id)
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "changed": changed, "path": str(path), "touched_models": touched}


def desktop_ui_catalog_issues(path: Path | None) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    models = read_catalog_models(path)
    if not models:
        return issues
    for index, item in enumerate(models):
        model_id = desktop_ui_model_id(item)
        if not model_id:
            issues.append({"index": index, "field": "model", "reason": "missing_model_or_slug"})
            continue
        if item.get("model") != model_id:
            issues.append({"model": model_id, "field": "model", "reason": "desktop_field_missing_or_mismatched"})
        if item.get("hidden") is not False:
            issues.append({"model": model_id, "field": "hidden", "reason": "desktop_visible_models_must_set_hidden_false"})
        if not desktop_ui_reasoning_efforts(item):
            issues.append({"model": model_id, "field": "supportedReasoningEfforts", "reason": "desktop_reasoning_efforts_missing"})
        else:
            catalog_efforts = codex_desktop_model_runtime.reasoning_efforts_for_key(item, "supported_reasoning_levels")
            desktop_efforts = codex_desktop_model_runtime.reasoning_efforts_for_key(item, "supportedReasoningEfforts")
            required_efforts = set(codex_desktop_model_runtime.DEFAULT_ENABLED_REASONING_EFFORTS)
            if required_efforts - catalog_efforts:
                issues.append(
                    {
                        "model": model_id,
                        "field": "supported_reasoning_levels",
                        "reason": "catalog_reasoning_efforts_incomplete",
                        "missing": sorted(required_efforts - catalog_efforts),
                    }
                )
            if required_efforts - desktop_efforts:
                issues.append(
                    {
                        "model": model_id,
                        "field": "supportedReasoningEfforts",
                        "reason": "desktop_reasoning_efforts_incomplete",
                        "missing": sorted(required_efforts - desktop_efforts),
                    }
                )
        if not isinstance(item.get("defaultReasoningEffort"), str) or not item.get("defaultReasoningEffort"):
            issues.append({"model": model_id, "field": "defaultReasoningEffort", "reason": "desktop_default_reasoning_missing"})
    return issues


def catalog_content_hash(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


def model_signature(
    config: dict[str, Any],
    catalog: Path | None,
    *,
    provider_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider, provider_config = active_model_provider_config(config)
    provider_authority = provider_authority if isinstance(provider_authority, dict) else {}
    return {
        "model": config.get("model") if isinstance(config.get("model"), str) else "",
        "model_provider": provider,
        "provider_name": str(provider_config.get("name") or ""),
        "provider_config_sha256": provider_config_fingerprint(provider, provider_config),
        "cc_switch_provider_id": str(provider_authority.get("provider_id") or ""),
        "cc_switch_provider_name": str(provider_authority.get("provider_name") or ""),
        "cc_switch_model_catalog_declared": bool(provider_authority.get("model_catalog_declared")),
        "cc_switch_model_catalog_active": bool(provider_authority.get("model_catalog_active")),
        "cc_switch_model_catalog_sha256": str(provider_authority.get("model_catalog_sha256") or ""),
        "model_catalog_json": config.get("model_catalog_json") if isinstance(config.get("model_catalog_json"), str) else "",
        "catalog_slugs": read_catalog_slugs(catalog),
        "catalog_sha256": catalog_content_hash(catalog),
    }


def model_catalog_provenance_state(
    config: dict[str, Any],
    config_path: Path,
    provider_authority: dict[str, Any] | None,
) -> dict[str, Any]:
    authority = provider_authority if isinstance(provider_authority, dict) else {}
    catalog = model_catalog_path(config, config_path)
    if authority and authority.get("ok") is False:
        return {
            "schema": "codex-config-guard/model-catalog-provenance/v1",
            "ok": False,
            "skipped": False,
            "reason": "cc_switch_provider_authority_unreadable",
            "authority_reason": str(authority.get("reason") or ""),
            "authority_error": str(authority.get("error") or ""),
        }
    provider_found = bool(authority.get("found"))
    provider_catalog_active = bool(authority.get("model_catalog_active"))
    pointer_present = catalog is not None
    catalog_exists = bool(catalog and catalog.is_file())
    if not provider_found:
        return {
            "schema": "codex-config-guard/model-catalog-provenance/v1",
            "ok": True,
            "skipped": True,
            "reason": "cc_switch_provider_not_available",
        }
    if provider_catalog_active:
        ok = pointer_present and catalog_exists
        reason = "provider_catalog_projection_ready" if ok else "provider_catalog_projection_missing"
    else:
        ok = not pointer_present
        reason = "native_provider_without_catalog" if ok else "stale_cross_provider_catalog_pointer"
    return {
        "schema": "codex-config-guard/model-catalog-provenance/v1",
        "ok": ok,
        "skipped": False,
        "reason": reason,
        "provider_id": str(authority.get("provider_id") or ""),
        "provider_catalog_active": provider_catalog_active,
        "provider_catalog_sha256": str(authority.get("model_catalog_sha256") or ""),
        "pointer_present": pointer_present,
        "catalog_path": str(catalog) if catalog else "",
        "catalog_exists": catalog_exists,
        "catalog_sha256": catalog_content_hash(catalog),
    }


def model_signature_hash(signature: dict[str, Any]) -> str:
    encoded = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_desktop_model_refresh_record() -> dict[str, Any]:
    try:
        data = json.loads(DESKTOP_MODEL_REFRESH_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def confirm_desktop_model_refresh_state() -> dict[str, Any]:
    state = desktop_model_refresh_state(confirm_current_signature=False)
    processes = state.get("desktop_app_server_processes") if isinstance(state.get("desktop_app_server_processes"), list) else []
    if not processes:
        return {**state, "confirmed": False, "confirm_reason": "no_desktop_app_server"}
    if state.get("app_server_started_after_model_files") is not True:
        return {
            **state,
            "confirmed": False,
            "confirm_reason": "app_server_started_before_current_model_files",
        }
    app_model_list = state.get("desktop_app_model_list") if isinstance(state.get("desktop_app_model_list"), dict) else {}
    if not bool(app_model_list.get("ok")) or bool(app_model_list.get("skipped")):
        return {
            **state,
            "confirmed": False,
            "confirm_reason": "desktop_app_model_list_not_verified",
        }
    record = {
        "schema": "codex-config-guard/desktop-model-refresh-state/v1",
        "updated_at": utc_now(),
        "signature_hash": state.get("model_signature_hash") or "",
        "model_signature": state.get("model_signature") or {},
        "desktop_app_model_list": app_model_list,
        "app_server_pids": [item.get("pid") for item in processes if isinstance(item, dict)],
        "newest_app_server_start": state.get("newest_app_server_start") or "",
    }
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DESKTOP_MODEL_REFRESH_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(DESKTOP_MODEL_REFRESH_STATE)
    return {**state, "confirmed": True, "confirm_reason": "recorded_current_app_server_signature", "state_path": str(DESKTOP_MODEL_REFRESH_STATE)}


def desktop_model_refresh_state(
    config_path: Path | None = None,
    *,
    confirm_current_signature: bool = False,
    app_model_list: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Report whether Desktop app-server has confirmed the model signature.

    This is read-only. Restarting Desktop from inside the active Desktop session
    is intentionally left as a manual/launcher action because killing the host
    process can interrupt the current Codex turn.
    """
    cfg_path = config_path or CODEX_CONFIG
    config = safe_toml(cfg_path)
    catalog = model_catalog_path(config, cfg_path)
    if app_model_list is None:
        provider_models = provider_model_list_state(config)
        cc_switch_catalog = cc_switch_catalog_state(config, cfg_path, provider_models)
        app_model_list = desktop_app_model_list_state(config, cfg_path, provider_models, cc_switch_catalog)
    watched_states = [file_state(path) for path in [cfg_path, catalog] if path is not None]
    provider_authority = (
        codex_config_projection.provider_authority_state()
        if cfg_path.resolve() == CODEX_CONFIG.resolve()
        else {}
    )
    signature = model_signature(config, catalog, provider_authority=provider_authority)
    catalog_provenance = model_catalog_provenance_state(config, cfg_path, provider_authority)
    signature_hash = model_signature_hash(signature)

    processes = desktop_app_server_processes()
    starts = [parse_datetime(item.get("started_at")) for item in processes]
    starts = [item for item in starts if item is not None]
    newest_app_server_start = max(starts) if starts else None
    watched_mtimes = [parse_datetime(item.get("mtime")) for item in watched_states]
    watched_mtimes = [item for item in watched_mtimes if item is not None]
    newest_model_file_mtime = max(watched_mtimes) if watched_mtimes else None
    app_server_started_after_model_files = bool(
        newest_app_server_start
        and newest_model_file_mtime
        and newest_app_server_start.timestamp() >= newest_model_file_mtime.timestamp()
    )
    stored = read_desktop_model_refresh_record()
    stored_hash = str(stored.get("signature_hash") or "")
    stored_start = parse_datetime(stored.get("newest_app_server_start"))
    stored_pids = {item for item in stored.get("app_server_pids", []) if item is not None}
    live_pids = {item.get("pid") for item in processes if isinstance(item, dict) and item.get("pid") is not None}
    confirmed = bool(
        signature_hash
        and stored_hash == signature_hash
        and newest_app_server_start
        and stored_start
        and abs(newest_app_server_start.timestamp() - stored_start.timestamp()) < 2
        and (not live_pids or bool(live_pids & stored_pids))
        and app_server_started_after_model_files
    )
    app_model_list_verified = bool(app_model_list.get("ok")) and not bool(app_model_list.get("skipped"))
    if not app_model_list_verified:
        confirmed = False
    if confirm_current_signature and processes and app_server_started_after_model_files and app_model_list_verified:
        confirmed = True
    app_model_list_blocks_refresh = bool(app_model_list) and not bool(app_model_list.get("ok")) and not bool(app_model_list.get("skipped"))
    restart_required = bool(processes and (not confirmed or app_model_list_blocks_refresh))
    return {
        "schema": "codex-config-guard/desktop-model-refresh/v1",
        "ok": True,
        "config_path": str(cfg_path),
        "active_model": config.get("model") if isinstance(config.get("model"), str) else "",
        "model_provider": config.get("model_provider") if isinstance(config.get("model_provider"), str) else "",
        "catalog_path": str(catalog) if catalog else "",
        "catalog_slugs": read_catalog_slugs(catalog),
        "model_signature": signature,
        "model_signature_hash": signature_hash,
        "model_catalog_provenance": catalog_provenance,
        "watched_files": watched_states,
        "desktop_app_server_processes": processes,
        "desktop_app_model_list": app_model_list,
        "newest_app_server_start": newest_app_server_start.isoformat() if newest_app_server_start else "",
        "newest_model_file_mtime": newest_model_file_mtime.isoformat() if newest_model_file_mtime else "",
        "app_server_started_after_model_files": app_server_started_after_model_files,
        "state_path": str(DESKTOP_MODEL_REFRESH_STATE),
        "stored_signature_hash": stored_hash,
        "confirmed_for_current_app_server": confirmed,
        "restart_required_for_desktop_model_refresh": restart_required,
        "reason": (
            "Desktop app-server model/list does not expose the expected visible models or reasoning efforts. Fix model registry/host binding before recording refresh confirmation."
            if app_model_list_blocks_refresh
            else
            "Desktop app-server has not yet recorded the current model signature; model picker may show stale or incomplete choices until Desktop is restarted or startup guard confirms it."
            if restart_required
            else "Current Desktop app-server has confirmed the active model signature, or no active Desktop app-server was found."
        ),
        "manual_action": (
            "Fix Desktop app-server model/list visibility, then fully close and reopen Codex Desktop."
            if app_model_list_blocks_refresh
            else "Fully close and reopen Codex Desktop, or run codex_config_guard run-once --apply from the startup guard after Desktop settles."
            if restart_required
            else ""
        ),
    }


def process_manager_state_path() -> Path:
    return Path.home() / ".codex" / "process_manager" / "chat_processes.json"


def codex_process_family_running() -> bool:
    try:
        return process_family_running()
    except Exception:
        return True


def pid_is_alive(pid: Any) -> bool:
    return _shared_process_is_alive(pid)


def process_manager_timestamp(item: dict[str, Any]) -> int | None:
    timestamp = item.get("updatedAtMs") or item.get("startedAtMs")
    if isinstance(timestamp, int):
        return timestamp
    try:
        return int(timestamp)
    except Exception:
        return None


def classify_process_manager_record(
    item: dict[str, Any],
    *,
    now_ms: int,
    clean_restart_boundary: bool = False,
) -> tuple[bool, str]:
    timestamp = process_manager_timestamp(item)
    if timestamp is None:
        return False, "missing_timestamp"
    age_ms = max(0, now_ms - timestamp)
    recent_grace_ms = PROCESS_MANAGER_RECENT_GRACE_MINUTES * 60 * 1000
    retention_ms = PROCESS_MANAGER_RETENTION_HOURS * 60 * 60 * 1000

    os_pid = item.get("osPid")
    if os_pid is not None and pid_is_alive(os_pid):
        return True, "live_os_pid"
    if clean_restart_boundary:
        if os_pid is None:
            return False, "null_os_pid_at_clean_restart_boundary"
        return False, "dead_os_pid_at_clean_restart_boundary"
    if age_ms <= recent_grace_ms:
        return True, "recent_grace"
    if os_pid is None:
        return False, "no_os_pid_outside_recent_grace"
    if age_ms > retention_ms:
        return False, "dead_os_pid_outside_retention"
    return False, "dead_os_pid"


def process_manager_state_metrics() -> dict[str, Any]:
    path = process_manager_state_path()
    if not path.exists():
        return {"path": str(path), "exists": False, "count": 0, "stale_count": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"path": str(path), "exists": True, "ok": False, "error": repr(exc)}
    if not isinstance(data, list):
        return {"path": str(path), "exists": True, "ok": False, "error": "not_list"}
    now_ms = int(time.time() * 1000)
    stale_count = 0
    reason_counts: dict[str, int] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        keep, reason = classify_process_manager_record(item, now_ms=now_ms)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if not keep:
            stale_count += 1
    return {
        "path": str(path),
        "exists": True,
        "ok": True,
        "count": len(data),
        "stale_count": stale_count,
        "retention_hours": PROCESS_MANAGER_RETENTION_HOURS,
        "recent_grace_minutes": PROCESS_MANAGER_RECENT_GRACE_MINUTES,
        "reason_counts": reason_counts,
    }


def compact_process_manager_state(*, apply: bool) -> dict[str, Any]:
    if os.environ.get("CODEX_PROCESS_MANAGER_HYGIENE") == "0":
        return {"ok": True, "skipped": True, "reason": "disabled_by_env"}
    path = process_manager_state_path()
    before = process_manager_state_metrics()
    if not before.get("exists") or before.get("ok") is False:
        return {"ok": bool(before.get("ok", True)), "applied": False, "before": before}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "applied": False, "before": before, "error": repr(exc)}
    if not isinstance(data, list):
        return {"ok": False, "applied": False, "before": before, "error": "not_list"}

    now_ms = int(time.time() * 1000)
    process_family_running = codex_process_family_running()
    clean_restart_boundary = not process_family_running
    typed_items = [item for item in data if isinstance(item, dict)]
    kept: list[dict[str, Any]] = []
    removed_reasons: dict[str, int] = {}
    kept_reasons: dict[str, int] = {}
    for item in typed_items:
        keep, reason = classify_process_manager_record(
            item,
            now_ms=now_ms,
            clean_restart_boundary=clean_restart_boundary,
        )
        if keep:
            kept.append(item)
            kept_reasons[reason] = kept_reasons.get(reason, 0) + 1
        else:
            removed_reasons[reason] = removed_reasons.get(reason, 0) + 1

    kept.sort(key=lambda item: process_manager_timestamp(item) or 0)
    changed = len(kept) < len(data)
    result = {
        "ok": True,
        "applied": False,
        "changed": changed,
        "before": before,
        "after_count": len(kept),
        "removed_count": len(data) - len(kept),
        "kept_reasons": kept_reasons,
        "removed_reasons": removed_reasons,
        "codex_process_family_running": process_family_running,
        "clean_restart_boundary": clean_restart_boundary,
        "policy": (
            "while Codex is running, keep recent null-PID records; at a clean restart boundary, "
            "drop null-PID records because they cannot be attached to a live OS process and can slow session recovery"
        ),
    }
    if not changed or not apply:
        return result
    if process_family_running and os.environ.get("CODEX_PROCESS_MANAGER_HYGIENE_ALLOW_RUNNING") != "1":
        result["skipped"] = True
        result["reason"] = "skipped_running_codex_process_family"
        result["note"] = (
            "Running Codex app-server can rewrite chat_processes.json from memory. "
            "Durable cleanup runs from the launcher after the old process family exits and before the new Desktop starts."
        )
        return result

    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)
    result["applied"] = True
    result["after"] = process_manager_state_metrics()
    return result


def safe_session_store_metrics() -> dict[str, Any]:
    try:
        return codex_session_store_doctor.metrics()
    except Exception as exc:
        return {
            "schema": "codex-session-store.metrics.v1",
            "ok": False,
            "error": repr(exc),
            "policy": "session-store metrics failure must not block Codex config guard",
        }


def safe_session_store_doctor() -> dict[str, Any]:
    try:
        return codex_session_store_doctor.doctor()
    except Exception as exc:
        return {
            "schema": "codex-session-store.doctor.v1",
            "ok": False,
            "issues": [
                {
                    "code": "codex_session_store_doctor_exception",
                    "severity": "advisory",
                    "summary": "Session-store doctor failed; config guard continues without session compaction.",
                    "detail": {"error": repr(exc)},
                }
            ],
            "error": repr(exc),
        }


def safe_session_store_repair_plan() -> dict[str, Any]:
    try:
        return codex_session_store_doctor.repair_plan()
    except Exception as exc:
        return {
            "schema": "codex-session-store.repair_plan.v1",
            "ok": False,
            "dry_run": True,
            "writes_files": False,
            "plan_items": [],
            "error": repr(exc),
        }


def safe_session_store_auto_maintain(*, apply: bool) -> dict[str, Any]:
    try:
        return codex_session_store_doctor.auto_maintain(apply=apply)
    except Exception as exc:
        return {
            "schema": "codex-session-store.auto_maintain.v1",
            "ok": True,
            "applied": False,
            "error": repr(exc),
            "policy": "session-store auto-maintain is best-effort and must not block Codex startup/config guard",
        }


def delegated_session_store_maintenance(phase: str) -> dict[str, Any]:
    return {
        "schema": "codex-session-store.auto_maintain.v1",
        "ok": True,
        "applied": False,
        "skipped": True,
        "reason": "owned_by_codex_prelaunch_maintenance",
        "phase": phase,
        "owner": "codex_prelaunch_maintenance.py",
        "policy": "config guard reports session state but never competes with the stopped-process pre-launch writer",
    }


def desktop_statsig_allowlist_drift(desktop_runtime_model_state: dict[str, Any]) -> dict[str, Any]:
    runtime = (
        desktop_runtime_model_state.get("runtime")
        if isinstance(desktop_runtime_model_state.get("runtime"), dict)
        else {}
    )
    missing = runtime.get("statsig_missing_expected_models") if isinstance(runtime.get("statsig_missing_expected_models"), list) else []
    findings = runtime.get("advisory_findings") if isinstance(runtime.get("advisory_findings"), list) else []
    return {
        "schema": "codex-config-guard/desktop-statsig-allowlist-drift/v1",
        "ok": not missing,
        "drift": bool(missing),
        "missing_expected_models": [str(item) for item in missing],
        "advisory_findings": [str(item) for item in findings],
        "reason": "desktop_statsig_model_whitelist_missing_expected_models" if missing else "",
    }


def doctor(*, run_cli: bool = False) -> dict[str, Any]:
    snap = snapshot(run_cli=run_cli)
    global_config = snap.get("global_config") if isinstance(snap.get("global_config"), dict) else {}
    cfg_path = global_config.get("path")
    global_config_data = safe_toml(Path(cfg_path)) if isinstance(cfg_path, str) else {}
    catalog_path = model_catalog_path(global_config_data, Path(cfg_path)) if isinstance(cfg_path, str) else None
    desktop_catalog_issues = desktop_ui_catalog_issues(catalog_path)
    provider_models = snap.get("provider_model_list") if isinstance(snap.get("provider_model_list"), dict) else {}
    cc_switch_catalog = snap.get("cc_switch_catalog") if isinstance(snap.get("cc_switch_catalog"), dict) else {}
    audit = snap.get("audit") if isinstance(snap.get("audit"), dict) else {}
    desktop_model_refresh = (
        snap.get("desktop_model_refresh")
        if isinstance(snap.get("desktop_model_refresh"), dict)
        else {}
    )
    desktop_runtime_model_state = (
        snap.get("desktop_runtime_model_state")
        if isinstance(snap.get("desktop_runtime_model_state"), dict)
        else {}
    )
    process_manager_state = (
        snap.get("process_manager_state")
        if isinstance(snap.get("process_manager_state"), dict)
        else {}
    )
    allowlist_drift = desktop_statsig_allowlist_drift(desktop_runtime_model_state)
    runtime_model_effective_ok = bool(desktop_runtime_model_state.get("ok")) and not bool(allowlist_drift.get("drift"))
    session_store_doctor = safe_session_store_doctor()
    issues: list[dict[str, Any]] = []
    if int(audit.get("critical_failure_count") or 0) > 0:
        issues.append(
            {
                "code": "codex_config_baseline_drift",
                "severity": "high",
                "summary": "Codex config has lost required baseline sections or values.",
                "safe_auto_fix": "codex_config_guard_run_once_apply",
                "detail": {
                    "critical": audit.get("critical", []),
                    "policy": "merge-only baseline repair; preserve extra user config",
                },
            }
        )
    stale_process_records = int(process_manager_state.get("stale_count") or 0)
    if stale_process_records > 0:
        process_family_running = codex_process_family_running()
        issues.append(
            {
                "code": "codex_process_manager_stale_records",
                "severity": "advisory" if process_family_running else "risk",
                "summary": (
                    "Codex process-manager state contains stale command records. "
                    "Clean them only at a Codex restart boundary to avoid runtime memory re-write."
                ),
                "safe_auto_fix": "codex_config_guard_run_once_apply_at_clean_restart_boundary",
                "detail": {
                    "process_manager_state": process_manager_state,
                    "codex_process_family_running": process_family_running,
                    "policy": "do not mutate chat_processes.json while Codex process family is running",
                },
            }
        )
    if bool(desktop_model_refresh.get("restart_required_for_desktop_model_refresh")) and not runtime_model_effective_ok:
        issues.append(
            {
                "code": "codex_desktop_model_config_not_refreshed",
                "severity": "medium",
                "summary": "Codex Desktop app-server is older than the active model config/catalog, so the desktop model picker can show stale models.",
                "safe_auto_fix": "",
                "detail": {
                    "desktop_model_refresh": desktop_model_refresh,
                    "policy": "do not kill the current Codex Desktop session from inside the active turn; perform a controlled full Desktop restart after saving work",
                },
            }
        )
    model_catalog_provenance = (
        desktop_model_refresh.get("model_catalog_provenance")
        if isinstance(desktop_model_refresh.get("model_catalog_provenance"), dict)
        else {}
    )
    if model_catalog_provenance and not model_catalog_provenance.get("ok") and not model_catalog_provenance.get("skipped"):
        issues.append(
            {
                "code": "codex_model_catalog_provenance_mismatch",
                "severity": "high",
                "summary": "The live model catalog pointer does not match the current CC Switch provider catalog authority.",
                "safe_auto_fix": "codex_config_projection_apply_then_provider_watcher_once",
                "detail": model_catalog_provenance,
            }
        )
    desktop_app_model_list = desktop_model_refresh.get("desktop_app_model_list") if isinstance(desktop_model_refresh.get("desktop_app_model_list"), dict) else {}
    if desktop_app_model_list and not bool(desktop_app_model_list.get("ok")) and not bool(desktop_app_model_list.get("skipped")):
        issues.append(
            {
                "code": "codex_desktop_app_model_list_unhealthy",
                "severity": "high",
                "summary": "Codex Desktop app-server model/list does not expose the expected visible models or reasoning-effort choices.",
                "safe_auto_fix": "",
                "detail": {
                    "desktop_app_model_list": desktop_app_model_list,
                    "policy": "fix provider/catalog/host binding first; do not record model-refresh confirmation from file timestamps alone",
                },
            }
        )
    if desktop_runtime_model_state and not bool(desktop_runtime_model_state.get("ok")):
        runtime_unhealthy = bool(desktop_runtime_model_state.get("runtime_unhealthy"))
        catalog_reasoning_unhealthy = bool(desktop_runtime_model_state.get("catalog_reasoning_unhealthy"))
        issues.append(
            {
                "code": "codex_desktop_runtime_model_picker_unhealthy",
                "severity": "high" if runtime_unhealthy else "medium",
                "summary": "The running Codex Desktop model picker cannot consume the CC Switch model catalog cleanly.",
                "safe_auto_fix": "",
                "detail": {
                    "desktop_runtime_model_state": desktop_runtime_model_state,
                    "runtime_unhealthy": runtime_unhealthy,
                    "catalog_reasoning_unhealthy": catalog_reasoning_unhealthy,
                    "policy": "diagnose the live Electron bridge, Statsig model gate, and CC Switch catalog reasoning levels before changing config or UI cache",
                },
            }
        )
    if bool(allowlist_drift.get("drift")):
        issues.append(
            {
                "code": "codex_desktop_statsig_allowlist_drift",
                "severity": "medium",
                "summary": "Codex Desktop Statsig available_models omits active CC Switch catalog/provider models.",
                "safe_auto_fix": "codex_config_guard_run_once_apply_or_codex_desktop_model_runtime_statsig_allowlist_protect",
                "detail": {
                    "allowlist_drift": allowlist_drift,
                    "policy": "keep Statsig enabled, but prevent its available_models field from shrinking the active custom provider catalog model set",
                },
            }
        )
    if cc_switch_catalog and not bool(cc_switch_catalog.get("ok")) and not bool(cc_switch_catalog.get("skipped")):
        issues.append(
            {
                "code": "codex_cc_switch_catalog_auxiliary_unhealthy",
                "severity": "medium",
                "summary": "The explicitly referenced CC Switch catalog is missing or invalid for the active provider.",
                "safe_auto_fix": "",
                "detail": {
                    "cc_switch_catalog": cc_switch_catalog,
                    "policy": "Only an explicitly referenced catalog participates in the active model set. Unreferenced catalog files are ignored as stale derived state.",
                },
            }
        )
    if provider_models and (bool(provider_models.get("degraded")) or (not bool(provider_models.get("ok")) and not bool(provider_models.get("skipped")))):
        issues.append(
            {
                "code": "codex_provider_model_list_unhealthy",
                "severity": "medium" if bool(provider_models.get("authoritative")) else "advisory",
                "summary": "The active provider model-discovery endpoint is unavailable or inconsistent. This is compatibility evidence, not a universal startup invariant.",
                "safe_auto_fix": "",
                "detail": {
                    "provider_model_list": provider_models,
                    "policy": "Use provider discovery when supported, then fall back to explicitly referenced catalog and live app-server/runtime evidence. Do not fail startup solely because /v1/models is absent or empty.",
                },
            }
        )
    if desktop_catalog_issues:
        issues.append(
            {
                "code": "codex_desktop_model_catalog_incompatible",
                "severity": "advisory",
                "summary": "A local model catalog is present but should not be used as the default Desktop model-picker source.",
                "safe_auto_fix": "",
                "detail": {
                    "catalog_path": str(catalog_path) if catalog_path else "",
                    "issues": desktop_catalog_issues,
                    "policy": "Catalog normalization is disabled by default; prefer provider/app-server model list and use catalog only for explicit manual experiments.",
                },
            }
        )
    if int(audit.get("startup_settling_failure_count") or 0) > 0:
        issues.append(
            {
                "code": "codex_config_startup_settling",
                "severity": "advisory",
                "summary": "Codex config is still inside the startup settling window; re-check after async baseline repair converges.",
                "safe_auto_fix": "",
                "detail": {
                    "settling": audit.get("startup_settling", []),
                    "policy": "only the narrow app-owned node_repl required-flag drift is downgraded, and only during the startup window",
                    "next_check": "python _bridge\\codex_config_guard.py validate",
                },
            }
        )
    if int(audit.get("cli_failure_count") or 0) > 0:
        issues.append(
            {
                "code": "codex_cli_visibility_degraded",
                "severity": "medium",
                "summary": "Codex CLI validation did not confirm the live MCP/plugin view.",
                "safe_auto_fix": "",
                "detail": audit.get("cli_failures", []),
            }
        )
    if int(audit.get("baseline_convergence_failure_count") or 0) > 0:
        issues.append(
            {
                "code": "codex_baseline_lags_global_config",
                "severity": "medium",
                "summary": "Global Codex config contains MCP/plugin capability not yet adopted into the startup baseline.",
                "safe_auto_fix": "codex_baseline_update_adopt_current_after_audit",
                "detail": {
                    "failures": audit.get("baseline_convergence_failures", []),
                    "dry_run": "python _bridge\\codex_baseline_update.py --check-current",
                    "apply": "python _bridge\\codex_baseline_update.py --adopt-current --reason \"verified global config convergence\"",
                    "policy": "adopt verified current state into baseline; do not prune absent live entries from baseline",
                },
            }
        )
    for item in session_store_doctor.get("issues", []):
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "")
        if severity not in {"risk", "high"}:
            continue
        issues.append(
            {
                "code": item.get("code", "codex_session_store_restore_risk"),
                "severity": "advisory",
                "summary": item.get("summary", "Codex session store may slow restart/resume."),
                "safe_auto_fix": "",
                "detail": {
                    "session_store_issue": item,
                    "policy": "surface restore-performance risk only; do not archive, delete, disable MCP, or reduce model/tool functionality automatically",
                    "repair_plan": "python _bridge\\codex_session_store_doctor.py repair-plan",
                },
            }
        )
    return {
        "schema": "codex-config-guard/doctor/v1",
        "ok": not any(item.get("severity") == "high" for item in issues),
        "generated_at": utc_now(),
        "issues": issues,
        "snapshot": snap,
        "session_store_doctor": session_store_doctor,
    }


def repair_plan() -> dict[str, Any]:
    plan = codex_state_repair.repair(dry_run=True)
    process_manager_hygiene = compact_process_manager_state(apply=False)
    session_store_plan = safe_session_store_repair_plan()
    session_store_maintenance = safe_session_store_auto_maintain(apply=False)
    config = safe_toml(CODEX_CONFIG) if CODEX_CONFIG.exists() else {}
    catalog_path = model_catalog_path(config, CODEX_CONFIG)
    catalog_reasoning_plan = codex_desktop_model_runtime.catalog_reasoning_repair_plan(catalog_path)
    statsig_allowlist_protection_plan = codex_desktop_model_runtime.statsig_allowlist_protection_state(
        catalog_path,
        apply=False,
    )
    protection_result = (
        statsig_allowlist_protection_plan.get("result")
        if isinstance(statsig_allowlist_protection_plan.get("result"), dict)
        else {}
    )
    protection_would_apply = bool(protection_result.get("changed"))
    process_manager_would_apply = bool(process_manager_hygiene.get("changed"))
    checks = audit_checks(run_cli=False)
    status = classify(checks)
    return {
        "schema": "codex-config-guard/repair-plan/v1",
        "ok": True,
        "generated_at": utc_now(),
        "dry_run": True,
        "would_apply": bool(plan.get("changed")) or process_manager_would_apply or bool(catalog_reasoning_plan.get("would_apply")) or protection_would_apply,
        "repair": plan,
        "process_manager_hygiene": process_manager_hygiene,
        "session_store_restore_plan": session_store_plan,
        "session_store_maintenance": session_store_maintenance,
        "catalog_reasoning_repair_plan": catalog_reasoning_plan,
        "statsig_allowlist_protection_plan": statsig_allowlist_protection_plan,
        "process_manager_cleanup_requires_clean_restart_boundary": bool(codex_process_family_running() and process_manager_would_apply),
        "pre_audit": status,
        "contract": {
            "writes_files": False,
            "apply_command": "python _bridge\\codex_config_guard.py run-once --apply",
            "catalog_reasoning_apply_command": "python _bridge\\codex_desktop_model_runtime.py catalog-reasoning-apply --catalog-path <path>",
            "statsig_allowlist_protect_command": "python _bridge\\codex_desktop_model_runtime.py statsig-allowlist-protect --catalog-path <path>",
            "policy": "merge-only config repair plus live-PID-aware process-manager cleanup; catalog reasoning repair is explicit and narrow; session-store restore-performance plan is advisory only and never reduces functionality",
        },
    }


def run_once(apply: bool, startup_delay_seconds: int = 0, phase: str = "manual") -> dict[str, Any]:
    if startup_delay_seconds > 0:
        time.sleep(startup_delay_seconds)
    if phase not in {"manual", "pre-start-static", "pre-start", "post-start"}:
        phase = "manual"
    repair_apply = bool(apply and phase in {"manual", "pre-start-static", "pre-start"})
    if phase == "pre-start-static":
        environment_selection = codex_state_repair.ensure_desktop_environment_selection(
            host_config=CODEX_CONFIG,
            dry_run=not repair_apply,
        )
        config_projection = (
            codex_config_projection.apply_projection(additions_only=True, sync_desktop=False)
            if repair_apply
            else codex_config_projection.snapshot()
        )
        selected_environment = environment_selection.get("effective_value")
        if not isinstance(selected_environment, bool):
            selected_environment = environment_selection.get("selected_value")
        desktop_wsl_enabled = (
            selected_environment
            if isinstance(selected_environment, bool)
            else desktop_wsl_enabled_for_startup()
        )
        session_store_maintenance = delegated_session_store_maintenance(phase)
        before_audit = classify(audit_checks(run_cli=False), include_startup_settling=False)
        if not bool(before_audit.get("critical_ok")) or bool(environment_selection.get("changed")):
            repair = codex_state_repair.repair(
                dry_run=not repair_apply,
                runtime_validation=False,
            )
            after_audit = (
                classify(audit_checks(run_cli=False), include_startup_settling=False)
                if repair_apply
                else before_audit
            )
            wsl_runtime_projection = repair.get("wsl_runtime_projection", {})
        else:
            # A healthy native baseline must not suppress the independent WSL
            # session projection required before Desktop starts its app-server.
            wsl_runtime_projection = codex_state_repair.ensure_wsl_runtime_projection(
                enabled=desktop_wsl_enabled,
                dry_run=not repair_apply,
            )
            repair = {"ok": True, "changed": [], "skipped": True, "reason": "baseline already satisfied"}
            after_audit = before_audit
        wsl_resume_context_projection = codex_state_repair.ensure_wsl_resume_context_projection(
            enabled=desktop_wsl_enabled,
            dry_run=not repair_apply,
        )
        windows_resume_cwd_projection = codex_state_repair.ensure_windows_resume_cwd_projection(
            enabled=not desktop_wsl_enabled,
            dry_run=not repair_apply,
        )
        projection_ok = (
            bool(wsl_runtime_projection.get("ok", True))
            and bool(wsl_resume_context_projection.get("ok", True))
            and bool(windows_resume_cwd_projection.get("ok", True))
        )
        projection_ready = (
            bool(wsl_runtime_projection.get("ready", projection_ok))
            and bool(wsl_resume_context_projection.get("ready", True))
            and bool(windows_resume_cwd_projection.get("ready", True))
        )
        environment_selection_changed = bool(environment_selection.get("changed"))
        projection_changed = bool(wsl_runtime_projection.get("changed")) or bool(
            wsl_resume_context_projection.get("changed")
        ) or bool(windows_resume_cwd_projection.get("changed"))
        startup_changed = environment_selection_changed or projection_changed
        result = {
            "schema": "codex-config-guard/run-once/v1",
            "ok": bool(repair.get("ok")) and bool(after_audit.get("critical_ok")) and projection_ok,
            "phase": phase,
            "applied": bool(repair_apply and (repair.get("changed") or startup_changed)),
            "generated_at": utc_now(),
            "before": before_audit,
            "repair": repair,
            "desktop_environment_selection": environment_selection,
            "wsl_runtime_projection": wsl_runtime_projection,
            "wsl_resume_context_projection": wsl_resume_context_projection,
            "windows_resume_cwd_projection": windows_resume_cwd_projection,
            "wsl_runtime_ready": projection_ready,
            "environment_selection_ready": bool(environment_selection.get("ready", True)),
            "config_projection": config_projection,
            "session_store_maintenance": session_store_maintenance,
            "process_manager_hygiene": {"ok": True, "skipped": True, "reason": "static_preflight"},
            "statsig_allowlist_protection": {"ok": True, "skipped": True, "reason": "runtime_phase_owned"},
            "model_list_bridge_shim": {"ok": True, "skipped": True, "reason": "runtime_phase_owned"},
            "runtime_applied": startup_changed,
            "catalog_normalization": {"ok": True, "skipped": True, "reason": "runtime_phase_owned"},
            "desktop_model_refresh_confirm": {"ok": True, "skipped": True, "reason": "runtime_phase_owned"},
            "after": after_audit,
            "needs_codex_restart": bool(repair_apply and (repair.get("needs_codex_restart") or startup_changed)),
            "policy": "static preflight first reconciles the Desktop environment selection, then performs mode-specific merge-only baseline repair and resume projections; Windows fallback keeps startup available and repairs only provable WSL mount cwd mappings whose target directories exist",
        }
        append_log("run_once_static", result)
        return result
    config_projection = (
        codex_config_projection.apply_projection(additions_only=True, sync_desktop=bool(apply and phase != "post-start"))
        if repair_apply
        else codex_config_projection.snapshot()
    )
    config = safe_toml(CODEX_CONFIG) if CODEX_CONFIG.exists() else {}
    catalog_path = model_catalog_path(config, CODEX_CONFIG)
    catalog_normalization = catalog_normalization_disabled(catalog_path)
    session_store_maintenance = delegated_session_store_maintenance(phase)
    process_manager_hygiene = compact_process_manager_state(apply=repair_apply)
    desktop_model_refresh_confirm = confirm_desktop_model_refresh_state() if apply else desktop_model_refresh_state()
    before = snapshot(run_cli=False)
    before_audit = before.get("audit") if isinstance(before.get("audit"), dict) else {}
    statsig_allowlist_protection = codex_desktop_model_runtime.statsig_allowlist_protection_state(
        catalog_path,
        apply=bool(apply),
        reload_if_changed=False,
    )
    model_list_bridge_shim = (
        codex_desktop_model_runtime.apply_model_list_bridge_shim(catalog_path, reload_page=False)
        if apply
        else {
            "schema": "codex-desktop-model-runtime/model-list-bridge-shim/v1",
            "ok": True,
            "skipped": True,
            "reason": "apply_not_requested",
        }
    )
    if not bool(before_audit.get("critical_ok")):
        repair = codex_state_repair.repair(dry_run=not repair_apply)
        if not repair_apply:
            repair = {
                **repair,
                "applied": False,
                "phase_blocked_apply": bool(apply and phase == "post-start"),
                "phase_policy": "post-start guard observes and confirms loaded Desktop state; merge-only config repair remains a pre-start/manual action",
            }
        runtime_changed = bool(statsig_allowlist_protection.get("protection")) or bool(model_list_bridge_shim.get("applied"))
        after = snapshot(run_cli=False) if repair_apply or runtime_changed else before
        result = {
            "schema": "codex-config-guard/run-once/v1",
            "ok": bool(repair.get("ok")) and (repair_apply or phase != "post-start"),
            "phase": phase,
            "applied": bool(repair_apply),
            "generated_at": utc_now(),
            "before": before_audit,
            "repair": repair,
            "config_projection": config_projection,
            "session_store_maintenance": session_store_maintenance,
            "process_manager_hygiene": process_manager_hygiene,
            "statsig_allowlist_protection": statsig_allowlist_protection,
            "model_list_bridge_shim": model_list_bridge_shim,
            "runtime_applied": runtime_changed,
            "catalog_normalization": catalog_normalization,
            "desktop_model_refresh_confirm": desktop_model_refresh_confirm,
            "after": after.get("audit") if isinstance(after.get("audit"), dict) else {},
            "needs_codex_restart": bool(repair_apply and repair.get("needs_codex_restart")),
            "policy": "merge-only additive baseline repair before launch; post-start only observes and confirms Desktop-visible model state",
        }
        append_log("run_once_repair", result)
        return result
    result = {
        "schema": "codex-config-guard/run-once/v1",
        "ok": True,
        "phase": phase,
        "applied": False,
        "generated_at": utc_now(),
        "before": before_audit,
        "repair": {"ok": True, "changed": [], "skipped": True, "reason": "baseline already satisfied"},
        "config_projection": config_projection,
        "session_store_maintenance": session_store_maintenance,
        "process_manager_hygiene": process_manager_hygiene,
        "statsig_allowlist_protection": statsig_allowlist_protection,
        "model_list_bridge_shim": model_list_bridge_shim,
        "runtime_applied": bool(statsig_allowlist_protection.get("protection")) or bool(model_list_bridge_shim.get("applied")),
        "catalog_normalization": catalog_normalization,
        "desktop_model_refresh_confirm": desktop_model_refresh_confirm,
        "after": before_audit,
        "needs_codex_restart": False,
        "policy": "merge-only additive baseline repair before launch; post-start only observes and confirms Desktop-visible model state",
    }
    append_log("run_once_noop", result)
    return result


def metrics() -> dict[str, Any]:
    snap = snapshot(run_cli=False)
    global_config = snap.get("global_config") if isinstance(snap.get("global_config"), dict) else {}
    cfg_path = global_config.get("path")
    global_config_data = safe_toml(Path(cfg_path)) if isinstance(cfg_path, str) else {}
    catalog_path = model_catalog_path(global_config_data, Path(cfg_path)) if isinstance(cfg_path, str) else None
    desktop_catalog_issues = desktop_ui_catalog_issues(catalog_path)
    audit = snap.get("audit") if isinstance(snap.get("audit"), dict) else {}
    provider_models = snap.get("provider_model_list") if isinstance(snap.get("provider_model_list"), dict) else {}
    cc_switch_catalog = snap.get("cc_switch_catalog") if isinstance(snap.get("cc_switch_catalog"), dict) else {}
    desktop_app_model_list = snap.get("desktop_app_model_list") if isinstance(snap.get("desktop_app_model_list"), dict) else {}
    desktop_runtime_model_state = snap.get("desktop_runtime_model_state") if isinstance(snap.get("desktop_runtime_model_state"), dict) else {}
    desktop_model_refresh = (
        snap.get("desktop_model_refresh")
        if isinstance(snap.get("desktop_model_refresh"), dict)
        else {}
    )
    provider_models_unhealthy = bool(provider_models) and not bool(provider_models.get("ok")) and not bool(provider_models.get("skipped"))
    provider_models_degraded = bool(provider_models.get("degraded"))
    cc_switch_catalog_unhealthy = bool(cc_switch_catalog) and not bool(cc_switch_catalog.get("ok")) and not bool(cc_switch_catalog.get("skipped"))
    desktop_app_model_list_unhealthy = bool(desktop_app_model_list) and not bool(desktop_app_model_list.get("ok")) and not bool(desktop_app_model_list.get("skipped"))
    desktop_runtime_model_state_unhealthy = bool(desktop_runtime_model_state) and not bool(desktop_runtime_model_state.get("ok"))
    allowlist_drift = desktop_statsig_allowlist_drift(desktop_runtime_model_state)
    model_refresh_blocks = bool(desktop_model_refresh.get("restart_required_for_desktop_model_refresh")) and (
        desktop_app_model_list_unhealthy
        or desktop_runtime_model_state_unhealthy
        or bool(allowlist_drift.get("drift"))
    )
    return {
        "schema": "codex-config-guard/metrics/v1",
        "ok": bool(audit.get("critical_ok")) and bool(audit.get("baseline_convergence_ok", True)),
        "generated_at": utc_now(),
        "startup_integrity_ok": bool(audit.get("critical_ok")) and bool(audit.get("baseline_convergence_ok", True)),
        "model_runtime_ok": not desktop_app_model_list_unhealthy and not desktop_runtime_model_state_unhealthy and not bool(allowlist_drift.get("drift")),
        "provider_discovery_ok": not provider_models_unhealthy and not provider_models_degraded,
        "failure_count": int(audit.get("failure_count") or 0),
        "critical_failure_count": int(audit.get("critical_failure_count") or 0),
        "cli_failure_count": int(audit.get("cli_failure_count") or 0),
        "restart_required": bool(audit.get("restart_required")),
        "desktop_model_refresh_degraded": model_refresh_blocks,
        "desktop_model_refresh_blocks_validation": False,
        "desktop_model_refresh": desktop_model_refresh,
        "provider_model_list": provider_models,
        "provider_model_list_unhealthy": provider_models_unhealthy,
        "provider_model_list_degraded": provider_models_degraded,
        "cc_switch_catalog": cc_switch_catalog,
        "cc_switch_catalog_unhealthy": cc_switch_catalog_unhealthy,
        "desktop_app_model_list": desktop_app_model_list,
        "desktop_app_model_list_unhealthy": desktop_app_model_list_unhealthy,
        "desktop_runtime_model_state": desktop_runtime_model_state,
        "desktop_runtime_model_state_unhealthy": desktop_runtime_model_state_unhealthy,
        "desktop_statsig_allowlist_drift": allowlist_drift,
        "desktop_model_catalog_issues": desktop_catalog_issues,
        "global_config": snap.get("global_config"),
        "project_config": snap.get("project_config"),
        "process_manager_state": snap.get("process_manager_state"),
        "session_store": snap.get("session_store"),
    }


def validate_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    """Separate startup integrity from provider/model compatibility diagnostics."""
    global_config = snap.get("global_config") if isinstance(snap.get("global_config"), dict) else {}
    cfg_path = global_config.get("path")
    global_config_data = safe_toml(Path(cfg_path)) if isinstance(cfg_path, str) else {}
    catalog_path = model_catalog_path(global_config_data, Path(cfg_path)) if isinstance(cfg_path, str) else None
    desktop_catalog_issues = desktop_ui_catalog_issues(catalog_path)
    audit = snap.get("audit") if isinstance(snap.get("audit"), dict) else {}
    provider_models = snap.get("provider_model_list") if isinstance(snap.get("provider_model_list"), dict) else {}
    cc_switch_catalog = snap.get("cc_switch_catalog") if isinstance(snap.get("cc_switch_catalog"), dict) else {}
    desktop_app_model_list = snap.get("desktop_app_model_list") if isinstance(snap.get("desktop_app_model_list"), dict) else {}
    desktop_runtime_model_state = snap.get("desktop_runtime_model_state") if isinstance(snap.get("desktop_runtime_model_state"), dict) else {}
    config_projection = snap.get("config_projection") if isinstance(snap.get("config_projection"), dict) else {}
    provider_models_unhealthy = bool(provider_models) and not bool(provider_models.get("ok")) and not bool(provider_models.get("skipped"))
    provider_models_degraded = bool(provider_models.get("degraded"))
    cc_switch_catalog_unhealthy = bool(cc_switch_catalog) and not bool(cc_switch_catalog.get("ok")) and not bool(cc_switch_catalog.get("skipped"))
    desktop_app_model_list_unhealthy = bool(desktop_app_model_list) and not bool(desktop_app_model_list.get("ok")) and not bool(desktop_app_model_list.get("skipped"))
    desktop_runtime_model_state_unhealthy = bool(desktop_runtime_model_state) and not bool(desktop_runtime_model_state.get("ok"))
    allowlist_drift = desktop_statsig_allowlist_drift(desktop_runtime_model_state)
    desktop_model_refresh = (
        snap.get("desktop_model_refresh")
        if isinstance(snap.get("desktop_model_refresh"), dict)
        else {}
    )
    needs_model_refresh = bool(desktop_model_refresh.get("restart_required_for_desktop_model_refresh"))
    model_refresh_blocks = needs_model_refresh and (
        desktop_app_model_list_unhealthy
        or desktop_runtime_model_state_unhealthy
        or bool(allowlist_drift.get("drift"))
    )
    startup_integrity_ok = bool(audit.get("critical_ok")) and bool(audit.get("baseline_convergence_ok", True))
    model_runtime_ok = (
        not desktop_app_model_list_unhealthy
        and not desktop_runtime_model_state_unhealthy
        and not bool(allowlist_drift.get("drift"))
    )
    provider_discovery_ok = not provider_models_unhealthy and not provider_models_degraded
    issues: list[dict[str, Any]] = []
    if not bool(audit.get("critical_ok")):
        issues.append(
            {
                "severity": "high",
                "code": "codex_startup_baseline_integrity_failed",
                "scope": "startup_integrity",
                "message": "Required Codex startup configuration is missing or drifting.",
                "details": audit.get("critical", []),
                "next_action": "python _bridge\\codex_config_guard.py repair-plan",
                "validation_command": "python _bridge\\codex_config_guard.py validate",
            }
        )
    if not bool(audit.get("baseline_convergence_ok", True)):
        issues.append(
            {
                "severity": "risk",
                "code": "codex_startup_baseline_convergence_failed",
                "scope": "startup_integrity",
                "message": "The startup baseline no longer describes the verified live configuration.",
                "details": audit.get("baseline_convergence_failures", []),
                "next_action": "python _bridge\\codex_baseline_update.py --check-current",
                "validation_command": "python _bridge\\codex_config_guard.py validate",
            }
        )
    if config_projection and (not bool(config_projection.get("ok")) or not bool(config_projection.get("projection_current"))):
        issues.append(
            {
                "severity": "medium",
                "code": "codex_config_projection_drift",
                "scope": "config_projection",
                "message": "CC Switch common configuration cannot yet reproduce every safe non-provider Codex setting.",
                "affected_objects": config_projection.get("action_rows", []),
                "next_action": "python _bridge\\codex_config_projection.py doctor",
                "validation_command": "python _bridge\\codex_config_projection.py validate",
            }
        )
    if provider_models_unhealthy or provider_models_degraded:
        issues.append(
            {
                "severity": "medium" if bool(provider_models.get("authoritative")) else "advisory",
                "code": "codex_provider_model_discovery_degraded",
                "scope": "provider_compatibility",
                "message": "Provider model discovery is unavailable or inconsistent; use catalog and live runtime evidence before deciding model availability.",
                "reason": provider_models.get("reason"),
                "affected_objects": [provider_models.get("provider"), provider_models.get("endpoint")],
                "next_action": "python _bridge\\codex_config_guard.py doctor",
            }
        )
    if cc_switch_catalog_unhealthy:
        issues.append(
            {
                "severity": "medium",
                "code": "codex_referenced_catalog_unhealthy",
                "scope": "provider_compatibility",
                "message": "The catalog explicitly referenced by the active provider is missing or empty.",
                "reason": cc_switch_catalog.get("reason"),
                "affected_objects": [cc_switch_catalog.get("active_catalog_path"), cc_switch_catalog.get("provider")],
                "next_action": "Reapply the active provider through CC Switch or remove the invalid explicit catalog reference.",
            }
        )
    if desktop_app_model_list_unhealthy or desktop_runtime_model_state_unhealthy or bool(allowlist_drift.get("drift")):
        issues.append(
            {
                "severity": "medium",
                "code": "codex_desktop_model_runtime_degraded",
                "scope": "model_runtime",
                "message": "Desktop model visibility or reasoning choices have not converged with the active provider state.",
                "affected_objects": desktop_app_model_list.get("missing_expected_models", []) or allowlist_drift.get("missing_expected_models", []),
                "next_action": "python _bridge\\codex_model_provider_watcher.py once",
                "validation_command": "python _bridge\\codex_model_provider_watcher.py validate",
            }
        )
    blockers = [item for item in issues if item.get("scope") == "startup_integrity" and item.get("severity") in {"critical", "high", "risk"}]
    return {
        "schema": "codex-config-guard/validate/v1",
        "ok": startup_integrity_ok,
        "generated_at": utc_now(),
        "status": "risk" if blockers else "advisory" if issues else "ok",
        "startup_integrity_ok": startup_integrity_ok,
        "model_runtime_ok": model_runtime_ok,
        "provider_discovery_ok": provider_discovery_ok,
        "issues": issues,
        "blockers": blockers,
        "next_action": blockers[0].get("next_action") if blockers else ("inspect_model_diagnostics" if issues else ""),
        "query_command": "python _bridge\\codex_config_guard.py doctor",
        "repair_plan_command": "python _bridge\\codex_config_guard.py repair-plan",
        "validation_command": "python _bridge\\codex_config_guard.py validate",
        "audit": audit,
        "desktop_model_refresh": desktop_model_refresh,
        "desktop_model_refresh_blocks_validation": False,
        "desktop_model_refresh_degraded": model_refresh_blocks,
        "provider_model_list": provider_models,
        "provider_model_list_unhealthy": provider_models_unhealthy,
        "provider_model_list_degraded": provider_models_degraded,
        "cc_switch_catalog": cc_switch_catalog,
        "cc_switch_catalog_unhealthy": cc_switch_catalog_unhealthy,
        "desktop_app_model_list": desktop_app_model_list,
        "desktop_app_model_list_unhealthy": desktop_app_model_list_unhealthy,
        "desktop_runtime_model_state": desktop_runtime_model_state,
        "desktop_runtime_model_state_unhealthy": desktop_runtime_model_state_unhealthy,
        "desktop_statsig_allowlist_drift": allowlist_drift,
        "desktop_model_catalog_issues": desktop_catalog_issues,
        "process_manager_state": snap.get("process_manager_state"),
        "session_store": safe_session_store_validate(),
        "config_projection": config_projection,
        "note": (
            "Baseline lags global config; run codex_baseline_update.py --check-current, then adopt current after audit if intentional."
            if not bool(audit.get("baseline_convergence_ok", True))
            else
            "Startup integrity is healthy; provider discovery and Desktop model/runtime findings are reported separately and do not block startup validation."
            if issues
            else
            "Codex config is inside startup settling window; re-run validate after async baseline repair converges."
            if int(audit.get("startup_settling_failure_count") or 0) > 0
            else
            "Codex Desktop restart is still required for current-session plugin/tool visibility after config repair."
            if bool(audit.get("critical_ok")) and int(audit.get("cli_failure_count") or 0) > 0
            else ""
        ),
    }


def validate() -> dict[str, Any]:
    return validate_snapshot(snapshot(run_cli=False))


def safe_session_store_validate() -> dict[str, Any]:
    try:
        return codex_session_store_doctor.validate()
    except Exception as exc:
        return {
            "schema": "codex-session-store.validate.v1",
            "ok": False,
            "error": repr(exc),
            "policy": "session-store validation failure should be reported without blocking config guard validation execution",
        }


def cli_projection(payload: dict[str, Any], action: str, *, full: bool = False) -> dict[str, Any]:
    full_result_ref = f"command:python _bridge/codex_config_guard.py {action} --full"
    if action == "validate":
        return aggregate_validator_cli_payload(payload, full=full, full_result_ref=full_result_ref)
    return governed_cli_payload(payload, full=full, full_result_ref=full_result_ref)


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex config drift guard")
    parser.add_argument(
        "action",
        choices=["snapshot", "doctor", "repair-plan", "validate", "metrics", "run-once"],
    )
    parser.add_argument("--apply", action="store_true", help="Apply merge-only baseline repair for run-once")
    parser.add_argument("--run-cli", action="store_true", help="Include heavier codex CLI visibility checks")
    parser.add_argument("--full", action="store_true", help="Emit the complete successful result.")
    parser.add_argument(
        "--startup-delay-seconds",
        type=int,
        default=0,
        help="Delay run-once checks after Codex startup so app-owned config rewrites can settle.",
    )
    parser.add_argument(
        "--phase",
        choices=["manual", "pre-start-static", "pre-start", "post-start"],
        default="manual",
        help="Guard phase: pre-start-static performs local baseline repair only; pre-start includes runtime owners; post-start observes/confirms loaded Desktop state only.",
    )
    args = parser.parse_args()

    if args.action == "snapshot":
        payload = snapshot(run_cli=bool(args.run_cli))
    elif args.action == "doctor":
        payload = doctor(run_cli=bool(args.run_cli))
    elif args.action == "repair-plan":
        payload = repair_plan()
    elif args.action == "validate":
        payload = validate()
    elif args.action == "metrics":
        payload = metrics()
    elif args.action == "run-once":
        payload = run_once(
            apply=bool(args.apply),
            startup_delay_seconds=max(0, int(args.startup_delay_seconds or 0)),
            phase=str(args.phase or "manual"),
        )
    else:  # pragma: no cover
        payload = {"ok": False, "error": f"unknown action {args.action}"}
    output = cli_projection(payload, args.action, full=bool(args.full))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
