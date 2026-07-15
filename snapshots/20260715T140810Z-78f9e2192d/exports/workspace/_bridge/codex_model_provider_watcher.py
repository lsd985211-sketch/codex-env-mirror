#!/usr/bin/env python3
"""Watch Codex provider/catalog changes and reconcile Desktop model runtime.

Ownership: provider-change detection and runtime reconciliation for the active
Codex Desktop model picker.
Non-goals: edit config.toml, rewrite CC Switch catalogs, patch app.asar, choose
a provider, or restart/kill Codex.
State behavior: read-only source monitoring plus runtime-only CDP shims; writes
only bounded watcher state and JSONL receipts under ``_bridge/runtime``.
Caller context: hidden scheduled task, elevated launcher fallback, diagnostics,
and regression tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from bounded_output import governed_cli_payload

try:
    import codex_appserver_model_bridge
    import codex_config_guard
    import codex_desktop_model_runtime
except ModuleNotFoundError:  # Package-style imports from the workspace root.
    from _bridge import codex_appserver_model_bridge, codex_config_guard, codex_desktop_model_runtime


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "_bridge" / "runtime" / "codex_model_provider_watcher"
STATE_PATH = RUNTIME_DIR / "state.json"
LOG_PATH = RUNTIME_DIR / "events.jsonl"
LOCK_PATH = RUNTIME_DIR / "watcher.lock"
TASK_NAME = "CodexModelProviderWatcher"
RESTART_FOR_IMPLEMENTATION_CHANGE = 75


def _implementation_paths() -> tuple[Path, ...]:
    paths = [
        Path(__file__).resolve(),
        Path(codex_appserver_model_bridge.__file__).resolve(),
        Path(codex_config_guard.__file__).resolve(),
        Path(codex_desktop_model_runtime.__file__).resolve(),
    ]
    return tuple(dict.fromkeys(paths))


def _implementation_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in _implementation_paths():
        digest.update(str(path).encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError as exc:
            digest.update(repr(exc).encode("utf-8"))
    return digest.hexdigest()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _append_event(payload: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return {}
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def source_state() -> dict[str, Any]:
    config_path = codex_config_guard.CODEX_CONFIG
    config = codex_config_guard.safe_toml(config_path)
    catalog_path = codex_config_guard.model_catalog_path(config, config_path)
    signature = codex_config_guard.model_signature(config, catalog_path)
    host_module = codex_appserver_model_bridge.discover_host_module()
    persisted_state_module = codex_desktop_model_runtime.discover_persisted_state_host_module()
    signature["desktop_appserver_module"] = str(host_module.get("module_specifier") or "")
    signature["desktop_persisted_state_module"] = str(persisted_state_module.get("module_specifier") or "")
    signature["desktop_app_asar"] = str(host_module.get("asar_path") or "")
    catalog_models = codex_config_guard.read_catalog_slugs(catalog_path)
    return {
        "schema": "codex-model-provider-watcher/source-state/v1",
        "ok": bool(config),
        "config_path": str(config_path),
        "catalog_path": str(catalog_path) if catalog_path else "",
        "model": str(config.get("model") or ""),
        "model_provider": str(config.get("model_provider") or ""),
        "signature": signature,
        "signature_hash": codex_config_guard.model_signature_hash(signature),
        "catalog_models": catalog_models,
        "catalog_reasoning_efforts": codex_desktop_model_runtime.catalog_requested_reasoning_efforts(catalog_path),
        "model_picker_policy": "full_catalog" if catalog_models else "native_default",
        "require_advanced_model_picker": bool(catalog_models),
        "desktop_host_module": host_module,
        "desktop_persisted_state_module": persisted_state_module,
    }


def reconcile(*, reload_if_changed: bool = True, dry_run: bool = False) -> dict[str, Any]:
    source = source_state()
    catalog_text = str(source.get("catalog_path") or "")
    catalog_path = Path(catalog_text) if catalog_text else None
    result: dict[str, Any] = {
        "schema": "codex-model-provider-watcher/reconcile/v1",
        "ok": False,
        "generated_at": _now(),
        "dry_run": dry_run,
        "source": source,
        "catalog_reasoning": {},
        "reasoning_hot_refresh": {},
        "bridge_shim": {},
        "statsig_protection": {},
        "model_picker_view_sync": {},
        "page_reload": {},
        "reason": "",
    }
    if not source.get("ok"):
        return {**result, "reason": "config_unreadable"}
    if not catalog_path or not source.get("catalog_models"):
        return {
            **result,
            "ok": True,
            "reason": "native_provider_without_catalog_no_runtime_override_required",
        }
    catalog_reasoning = codex_desktop_model_runtime.catalog_reasoning_repair_plan(catalog_path)
    if dry_run:
        return {**result, "ok": True, "catalog_reasoning": catalog_reasoning, "reason": "dry_run"}

    catalog_reasoning = codex_desktop_model_runtime.apply_catalog_reasoning_repair(catalog_path)
    if not catalog_reasoning.get("ok"):
        return {
            **result,
            "catalog_reasoning": catalog_reasoning,
            "reason": "catalog_reasoning_repair_failed",
        }

    appserver = codex_desktop_model_runtime.apply_appserver_model_shim(
        catalog_path,
        wait_seconds=2.0,
    )
    appserver_ready = bool(appserver.get("ok")) and not bool(appserver.get("skipped"))
    bridge = (
        {
            "schema": "codex-desktop-model-runtime/model-list-bridge-shim/v1",
            "ok": True,
            "applied": False,
            "skipped": True,
            "reason": "appserver_primary_active",
        }
        if appserver_ready
        else codex_desktop_model_runtime.apply_model_list_bridge_shim(
            catalog_path,
            reload_page=False,
            wait_seconds=2.0,
        )
    )
    protection = codex_desktop_model_runtime.statsig_allowlist_protection_state(
        catalog_path,
        apply=True,
        reload_if_changed=False,
        wait_seconds=2.0,
    )
    picker_sync = codex_desktop_model_runtime.model_picker_view_sync_state(
        str(source.get("signature_hash") or ""),
        apply=True,
        require_advanced=bool(source.get("require_advanced_model_picker")),
        wait_seconds=2.0,
    )
    reasoning_hot_refresh = codex_desktop_model_runtime.reasoning_hot_refresh_state(
        catalog_path,
        apply=True,
        reload_if_changed=False,
        wait_seconds=2.0,
    )
    bridge_ready = bool(bridge.get("ok")) and not bool(bridge.get("skipped"))
    protection_ready = bool(protection.get("ok")) and not bool(protection.get("skipped"))
    picker_ready = bool(picker_sync.get("ok")) and not bool(picker_sync.get("skipped"))
    reasoning_ready = bool(reasoning_hot_refresh.get("ok")) and not bool(reasoning_hot_refresh.get("skipped"))
    picker_result = picker_sync.get("result") if isinstance(picker_sync.get("result"), dict) else {}
    reasoning_result = (
        reasoning_hot_refresh.get("result") if isinstance(reasoning_hot_refresh.get("result"), dict) else {}
    )
    reload_required = (
        bool(picker_result.get("changed")) and bool(picker_result.get("reloadSafe"))
    ) or (
        bool(reasoning_result.get("settingChanged")) and bool(reasoning_result.get("settingApplied"))
    )
    page_reload = (
        codex_desktop_model_runtime.request_desktop_page_reload(wait_seconds=2.0)
        if reload_if_changed and reload_required
        else {"ok": True, "requested": False, "reason": "reload_not_required"}
    )
    reload_ready = bool(page_reload.get("ok"))
    ok = (appserver_ready or bridge_ready) and protection_ready and picker_ready and reasoning_ready and reload_ready
    return {
        **result,
        "ok": ok,
        "catalog_reasoning": catalog_reasoning,
        "appserver_shim": appserver,
        "bridge_shim": bridge,
        "statsig_protection": protection,
        "model_picker_view_sync": picker_sync,
        "reasoning_hot_refresh": reasoning_hot_refresh,
        "page_reload": page_reload,
        "reason": "" if ok else "desktop_runtime_not_ready_or_reconcile_failed",
    }


def runtime_binding_state(
    expected_models: list[str],
    expected_reasoning_efforts: list[str] | None = None,
    expected_picker_signature: str = "",
    require_advanced_picker: bool = True,
) -> dict[str, Any]:
    expected_reasoning_efforts = list(expected_reasoning_efforts or [])
    port, ws_url, pages, reason = codex_desktop_model_runtime._find_codex_page()
    result: dict[str, Any] = {
        "schema": "codex-model-provider-watcher/runtime-binding/v1",
        "ok": True,
        "bound": False,
        "cdp_port": port,
        "page_count": len(pages),
        "expected_models": expected_models,
        "expected_reasoning_efforts": expected_reasoning_efforts,
        "expected_picker_signature": expected_picker_signature,
        "reason": reason,
    }
    if not ws_url:
        return {**result, "reason": reason or "codex_page_not_ready"}
    client = None
    picker_probe: dict[str, Any] = {}
    appserver_probe: dict[str, Any] = {}
    statsig_probe: dict[str, Any] = {}
    try:
        client = codex_desktop_model_runtime._CdpClient(ws_url)
        state = client.evaluate(
            """(() => ({
              appServerVersion: String(window.__codexAppServerModelShimVersion || ''),
              appServerModels: (window.__codexAppServerModelShimModels || []).map((item) => item.model || item.slug || ''),
              appServerConsumedModels: window.__codexAppServerModelShimConsumedModels || [],
              appServerGeneration: String(window.__codexAppServerModelShimGeneration || ''),
              appServerConsumedGeneration: String(window.__codexAppServerModelShimConsumedGeneration || ''),
              appServerConsumedNextCursor: window.__codexAppServerModelShimConsumedNextCursor ?? null,
              appServerConsumedAt: Number(window.__codexAppServerModelShimConsumedAt || 0),
              modelListVersion: String(window.__codexModelListShimVersion || ''),
              modelListModels: (window.__codexModelListShimModels || []).map((item) => item.slug || item.model || ''),
              modelListSignature: String(window.__codexModelListShimSignature || ''),
              modelListConsumedSignature: String(window.__codexModelListShimConsumedSignature || ''),
              modelListConsumedModels: window.__codexModelListShimConsumedModels || [],
              modelListConsumedNextCursor: window.__codexModelListShimConsumedNextCursor ?? null,
              modelListConsumedAt: Number(window.__codexModelListShimConsumedAt || 0),
              statsigVersion: String(window.__codexStatsigAllowlistProtectionVersion || ''),
              statsigRequiredModels: window.__codexStatsigAllowlistProtectionRequiredModels || [],
              reasoningVersion: String(window.__codexReasoningCapabilityVersion || ''),
              reasoningEfforts: window.__codexReasoningCapabilityEfforts || [],
              reasoningGates: window.__codexReasoningCapabilityGateOverrides || {}
            }))()"""
        )
        appserver_discovery = codex_appserver_model_bridge.discover_host_module()
        if appserver_discovery.get("ok"):
            appserver_value = client.evaluate(
                codex_appserver_model_bridge.build_probe_source(
                    str(appserver_discovery.get("module_specifier") or "")
                )
            )
            appserver_probe = appserver_value if isinstance(appserver_value, dict) else {}
        statsig_value = client.evaluate(
            codex_desktop_model_runtime._statsig_allowlist_live_probe_expression()
        )
        statsig_probe = statsig_value if isinstance(statsig_value, dict) else {}
        picker_value = client.evaluate(
            codex_desktop_model_runtime._model_picker_view_sync_expression(
                expected_picker_signature or "runtime-binding-probe",
                apply=False,
                require_advanced=require_advanced_picker,
            )
        )
        picker_probe = picker_value if isinstance(picker_value, dict) else {}
        state = state if isinstance(state, dict) else {}
        appserver_models = [str(item) for item in state.get("appServerModels", []) if item]
        appserver_consumed_models = [str(item) for item in state.get("appServerConsumedModels", []) if item]
        model_list_models = [str(item) for item in state.get("modelListModels", []) if item]
        model_list_consumed_models = [str(item) for item in state.get("modelListConsumedModels", []) if item]
        statsig_required_models = [str(item) for item in state.get("statsigRequiredModels", []) if item]
        statsig_available_models = [str(item) for item in statsig_probe.get("availableModels", []) if item]
        reasoning_efforts = [str(item) for item in state.get("reasoningEfforts", []) if item]
        appserver_live_models = [str(item) for item in appserver_probe.get("models", []) if item]
        appserver_live_consumed_models = [
            str(item) for item in appserver_probe.get("consumedModels", []) if item
        ]
        appserver_bound = (
            appserver_probe.get("version") == codex_appserver_model_bridge.SHIM_VERSION
            and bool(appserver_probe.get("wrapperActive"))
            and appserver_live_models == expected_models
            and appserver_live_consumed_models == expected_models
            and appserver_probe.get("consumedGeneration") == appserver_probe.get("generation")
            and appserver_probe.get("consumedNextCursor") in (None, "")
            and bool(appserver_probe.get("queryRefetchConfirmed"))
        )
        legacy_bound = (
            state.get("modelListVersion") == "codex-model-list-bridge-shim/v6"
            and model_list_models == expected_models
            and state.get("modelListConsumedSignature") == state.get("modelListSignature")
            and model_list_consumed_models == expected_models
            and state.get("modelListConsumedNextCursor") in (None, "")
        )
        appserver_supported = bool(appserver_discovery.get("ok"))
        binding_route = (
            "appserver"
            if appserver_bound
            else "legacy_model_list"
            if not appserver_supported and legacy_bound
            else ""
        )
        statsig_bound = (
            statsig_probe.get("version") == "codex-statsig-allowlist-protection/v4"
            and statsig_required_models == expected_models
            and bool(statsig_probe.get("ok"))
            and bool(statsig_probe.get("nativeSetItemRecovered"))
            and bool(statsig_probe.get("storageWrapperActive"))
            and int(statsig_probe.get("clientsProtected") or 0)
            == int(statsig_probe.get("clientsFound") or 0)
            and all(model in statsig_available_models for model in expected_models)
        )
        model_runtime_bound = bool(binding_route) and statsig_bound
        reasoning_bound = (
            state.get("reasoningVersion") == "codex-reasoning-capability-bridge/v1"
            and reasoning_efforts == expected_reasoning_efforts
        )
        picker_signature = str(
            picker_probe.get("signatureAfter") or picker_probe.get("previousSignature") or ""
        )
        picker_view = str(picker_probe.get("viewAfter") or picker_probe.get("viewBefore") or "")
        picker_persistence_confirmed = bool(picker_probe.get("persistenceConfirmed"))
        picker_signature_bound = not expected_picker_signature or picker_signature == expected_picker_signature
        picker_view_bound = (
            not expected_picker_signature
            or not require_advanced_picker
            or (picker_view == "advanced" and picker_persistence_confirmed)
        )
        picker_bound = picker_signature_bound and picker_view_bound
        bound = model_runtime_bound and reasoning_bound and picker_bound
        return {
            **result,
            "bound": bound,
            "appserver_supported": appserver_supported,
            "appserver_bound": appserver_bound,
            "appserver_models": appserver_models,
            "appserver_consumed_models": appserver_consumed_models,
            "appserver_live_probe": appserver_probe,
            "appserver_live_models": appserver_live_models,
            "appserver_live_consumed_models": appserver_live_consumed_models,
            "appserver_generation": appserver_probe.get("generation"),
            "appserver_consumed_generation": appserver_probe.get("consumedGeneration"),
            "appserver_query_refetch_confirmed": bool(appserver_probe.get("queryRefetchConfirmed")),
            "appserver_consumed_at": state.get("appServerConsumedAt"),
            "appserver_consumed_next_cursor": state.get("appServerConsumedNextCursor"),
            "legacy_bound": legacy_bound,
            "model_list_models": model_list_models,
            "model_list_consumed_models": model_list_consumed_models,
            "model_list_consumed_at": state.get("modelListConsumedAt"),
            "model_list_consumed_next_cursor": state.get("modelListConsumedNextCursor"),
            "binding_route": binding_route,
            "statsig_required_models": statsig_required_models,
            "statsig_available_models": statsig_available_models,
            "statsig_clients_found": int(statsig_probe.get("clientsFound") or 0),
            "statsig_clients_protected": int(statsig_probe.get("clientsProtected") or 0),
            "statsig_live_probe": statsig_probe,
            "statsig_bound": statsig_bound,
            "reasoning_bound": reasoning_bound,
            "reasoning_efforts": reasoning_efforts,
            "reasoning_gates": state.get("reasoningGates") if isinstance(state.get("reasoningGates"), dict) else {},
            "model_picker_view": picker_view,
            "model_picker_view_sync_signature": picker_signature,
            "model_picker_persistence_route": str(picker_probe.get("persistenceRoute") or ""),
            "model_picker_persistence_confirmed": picker_persistence_confirmed,
            "model_picker_signature_bound": picker_signature_bound,
            "model_picker_view_bound": picker_view_bound,
            "model_picker_view_sync_required": not picker_bound,
            "reason": "" if bound else "runtime_binding_missing_or_stale",
        }
    except Exception as exc:
        return {**result, "ok": False, "reason": "runtime_binding_probe_failed", "error": repr(exc)}
    finally:
        if client is not None:
            client.close()


def _scheduled_task_state() -> dict[str, Any]:
    script = f"""
$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
if ($task) {{
  $info = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue
  [pscustomobject]@{{
    exists = $true
    state = [string]$task.State
    lastRunTime = $info.LastRunTime
    lastTaskResult = $info.LastTaskResult
    nextRunTime = $info.NextRunTime
    actions = @($task.Actions | ForEach-Object {{ [pscustomobject]@{{ execute = $_.Execute; arguments = $_.Arguments }} }})
  }} | ConvertTo-Json -Depth 6 -Compress
}} else {{
  [pscustomobject]@{{ exists = $false }} | ConvertTo-Json -Compress
}}
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        check=False,
    )
    try:
        value = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def snapshot() -> dict[str, Any]:
    state = _read_state()
    source = source_state()
    implementation_fingerprint = _implementation_fingerprint()
    binding = runtime_binding_state(
        list(source.get("catalog_models") or []),
        list(source.get("catalog_reasoning_efforts") or []),
        str(source.get("signature_hash") or ""),
    )
    return {
        "schema": "codex-model-provider-watcher/snapshot/v1",
        "ok": True,
        "generated_at": _now(),
        "source": source,
        "state_path": str(STATE_PATH),
        "state": state,
        "implementation_fingerprint": implementation_fingerprint,
        "implementation_current": state.get("implementation_fingerprint") == implementation_fingerprint,
        "runtime_binding": binding,
        "signature_current": bool(source.get("signature_hash"))
        and source.get("signature_hash") == state.get("last_successful_signature_hash"),
        "scheduled_task": _scheduled_task_state(),
    }


def validate() -> dict[str, Any]:
    snap = snapshot()
    task = snap.get("scheduled_task") if isinstance(snap.get("scheduled_task"), dict) else {}
    binding = snap.get("runtime_binding") if isinstance(snap.get("runtime_binding"), dict) else {}
    desktop_ready = bool(binding.get("page_count"))
    action_text = " ".join(str(item.get("arguments") or "") for item in task.get("actions", []) if isinstance(item, dict))
    checks = [
        {"name": "source_signature_available", "ok": bool(snap.get("source", {}).get("signature_hash"))},
        {"name": "scheduled_task_exists", "ok": bool(task.get("exists"))},
        {"name": "scheduled_task_owns_watcher", "ok": "codex_model_provider_watcher.py" in action_text and " watch" in action_text},
        {"name": "state_directory_bounded", "ok": str(STATE_PATH).startswith(str(ROOT / "_bridge" / "runtime"))},
        {"name": "source_signature_converged", "ok": bool(snap.get("signature_current"))},
        {"name": "watcher_implementation_current", "ok": bool(snap.get("implementation_current"))},
        {"name": "runtime_binding_current", "ok": not desktop_ready or bool(binding.get("bound"))},
    ]
    return {
        "schema": "codex-model-provider-watcher/validate/v1",
        "ok": all(item["ok"] for item in checks),
        "generated_at": _now(),
        "checks": checks,
        "snapshot": snap,
    }


def _acquire_lock() -> Any:
    import msvcrt

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def watch(
    *,
    poll_seconds: float,
    debounce_seconds: float,
    drift_check_seconds: float,
    max_iterations: int = 0,
) -> int:
    lock = _acquire_lock()
    if lock is None:
        return 0
    implementation_fingerprint = _implementation_fingerprint()
    previous_state = _read_state()
    last_seen = ""
    last_successful = str(previous_state.get("last_successful_signature_hash") or "")
    changed_at = 0.0
    last_drift_check = time.monotonic()
    iterations = 0
    try:
        _write_json(
            STATE_PATH,
            {
                **previous_state,
                "schema": "codex-model-provider-watcher/state/v1",
                "updated_at": _now(),
                "pid": os.getpid(),
                "implementation_fingerprint": implementation_fingerprint,
                "restart_required": False,
                "last_successful_signature_hash": last_successful,
            },
        )
        while True:
            iterations += 1
            active_implementation_fingerprint = _implementation_fingerprint()
            if active_implementation_fingerprint != implementation_fingerprint:
                state = {
                    **_read_state(),
                    "schema": "codex-model-provider-watcher/state/v1",
                    "updated_at": _now(),
                    "pid": os.getpid(),
                    "implementation_fingerprint": implementation_fingerprint,
                    "next_implementation_fingerprint": active_implementation_fingerprint,
                    "restart_required": True,
                    "restart_reason": "implementation_changed",
                }
                _write_json(STATE_PATH, state)
                _append_event(
                    {
                        "schema": "codex-model-provider-watcher/event/v1",
                        "time": _now(),
                        "event": "implementation_changed",
                        "implementation_fingerprint": implementation_fingerprint,
                        "next_implementation_fingerprint": active_implementation_fingerprint,
                    }
                )
                return RESTART_FOR_IMPLEMENTATION_CHANGE
            source = source_state()
            signature_hash = str(source.get("signature_hash") or "")
            now = time.monotonic()
            if signature_hash and signature_hash != last_seen:
                last_seen = signature_hash
                changed_at = now
                _append_event({"schema": "codex-model-provider-watcher/event/v1", "time": _now(), "event": "source_changed", "source": source})
            if signature_hash and signature_hash != last_successful and now - changed_at >= debounce_seconds:
                result = reconcile(reload_if_changed=True)
                if result.get("ok"):
                    last_successful = signature_hash
                    last_drift_check = now
                state = {
                    "schema": "codex-model-provider-watcher/state/v1",
                    "updated_at": _now(),
                    "pid": os.getpid(),
                    "implementation_fingerprint": implementation_fingerprint,
                    "restart_required": False,
                    "last_seen_signature_hash": signature_hash,
                    "last_successful_signature_hash": last_successful,
                    "last_result": result,
                }
                _write_json(STATE_PATH, state)
                _append_event({"schema": "codex-model-provider-watcher/event/v1", "time": _now(), "event": "reconcile", "result": result})
            elif signature_hash and now - last_drift_check >= max(1.0, drift_check_seconds):
                last_drift_check = now
                binding = runtime_binding_state(
                    list(source.get("catalog_models") or []),
                    list(source.get("catalog_reasoning_efforts") or []),
                    signature_hash,
                )
                if not binding.get("bound"):
                    result = reconcile(reload_if_changed=True)
                    if result.get("ok"):
                        last_successful = signature_hash
                    state = {
                        "schema": "codex-model-provider-watcher/state/v1",
                        "updated_at": _now(),
                        "pid": os.getpid(),
                        "implementation_fingerprint": implementation_fingerprint,
                        "restart_required": False,
                        "last_seen_signature_hash": signature_hash,
                        "last_successful_signature_hash": last_successful,
                        "runtime_binding": binding,
                        "last_result": result,
                    }
                    _write_json(STATE_PATH, state)
                    _append_event(
                        {
                            "schema": "codex-model-provider-watcher/event/v1",
                            "time": _now(),
                            "event": "runtime_binding_reconcile",
                            "runtime_binding": binding,
                            "result": result,
                        }
                    )
            if max_iterations and iterations >= max_iterations:
                return 0
            time.sleep(max(0.25, poll_seconds))
    finally:
        try:
            lock.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex model provider change watcher")
    parser.add_argument("action", choices=["snapshot", "validate", "once", "watch"])
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--debounce-seconds", type=float, default=1.5)
    parser.add_argument(
        "--drift-check-seconds",
        type=float,
        default=3.0,
        help="Lightweight runtime-binding probe interval; full reconcile runs only when a bridge is missing or stale",
    )
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full", action="store_true", help="Emit the complete successful result.")
    args = parser.parse_args(argv)
    if args.action == "snapshot":
        result = snapshot()
    elif args.action == "validate":
        result = validate()
    elif args.action == "once":
        result = reconcile(dry_run=args.dry_run)
    else:
        return watch(
            poll_seconds=args.poll_seconds,
            debounce_seconds=args.debounce_seconds,
            drift_check_seconds=args.drift_check_seconds,
            max_iterations=args.max_iterations,
        )
    output = governed_cli_payload(
        result,
        full=bool(args.full),
        full_result_ref=f"command:python _bridge/codex_model_provider_watcher.py {args.action} --full",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if bool(result.get("ok", True)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
