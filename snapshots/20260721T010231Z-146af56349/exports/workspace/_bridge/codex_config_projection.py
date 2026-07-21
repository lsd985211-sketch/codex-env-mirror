#!/usr/bin/env python3
"""Preserve Codex configuration across CC Switch provider rebuilds.

CC Switch owns provider selection only. This owner keeps safe non-provider
settings in an independent ledger, restores missing active values, and syncs
known Desktop settings through the native host API. The retired CC Switch
common-config surfaces are detected and removed, never used for recovery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tomllib
from collections import Counter
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from bounded_output import aggregate_validator_cli_payload, governed_cli_payload
from platform_paths import cc_switch_database_path, codex_config_path, workspace_root


ROOT = workspace_root()
BRIDGE = ROOT / "_bridge"
CODEX_CONFIG = codex_config_path()
CC_SWITCH_DB = cc_switch_database_path()
LEGACY_COMMON_KEY = "common_config_codex"
LEGACY_MANAGED_DB_KEY = "codex_managed_projection_v1"
MANAGED_FILE_NAME = "managed-config-projection.json"
RUNTIME_DIR = BRIDGE / "runtime" / "codex_config_projection"
LOCK_PATH = RUNTIME_DIR / "projection.lock"
SCHEMA = "codex-config-projection/v1"
MAX_ACTION_ROWS = 40
CAPTURE_LEASE_ENV = "CODEX_MIRROR_CAPTURE_LEASE_PATH"

if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_desktop_model_runtime  # noqa: E402
import codex_state_repair  # noqa: E402
from shared.backup_router import create_backup  # noqa: E402


def capture_lease_state() -> dict[str, Any]:
    mirror_root = Path(os.environ.get("CODEX_ENV_MIRROR_ROOT", str(Path.home() / "codex-env-mirror")))
    path = Path(os.environ.get(CAPTURE_LEASE_ENV, str(mirror_root / "runtime" / "capture-lease.json")))
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        expires_at = float(payload.get("expires_at_epoch") or 0.0)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"active": False, "path": str(path)}
    if expires_at <= datetime.now(timezone.utc).timestamp():
        return {"active": False, "expired": True, "path": str(path)}
    return {"active": True, "path": str(path), "expires_at_epoch": expires_at, "purpose": str(payload.get("purpose") or "")}


PathTuple = tuple[str, ...]
PROVIDER_ROOTS = {
    "model",
    "model_catalog_json",
    "model_provider",
    "model_reasoning_effort",
    "model_providers",
}
STARTUP_ROOTS = {
    "approval_policy",
    "features",
    "marketplaces",
    "mcp_servers",
    "memories",
    "notify",
    "plugins",
    "projects",
    "sandbox_mode",
    "windows",
}
RUNTIME_LOCAL_ROOTS = {"projects"}
TRANSIENT_NAMES = {"last_updated", "last_checked", "generated_at", "updated_at"}
SECRET_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "cookies",
    "experimental_bearer_token",
    "password",
    "private_key",
    "refresh_token",
    "secret",
}
SAFE_TOKEN_METADATA = {"bearer_token_env_var"}
DESKTOP_SETTING_KEYS: dict[PathTuple, str] = {
    ("desktop", "show-context-window-usage"): "show-context-window-usage",
    ("desktop", "show-ultra-in-model-picker-slider"): "show-ultra-in-model-picker-slider",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def flatten(value: Any, prefix: PathTuple = ()) -> dict[PathTuple, Any]:
    rows: dict[PathTuple, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            rows.update(flatten(child, (*prefix, str(key))))
    elif prefix:
        rows[prefix] = value
    return rows


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def path_pointer(path: PathTuple) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in path)


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def is_secret(path: PathTuple, value: Any) -> bool:
    for part in path:
        normalized = _normalized_name(part)
        if normalized in SAFE_TOKEN_METADATA:
            continue
        if normalized in SECRET_NAMES or normalized.endswith("_password") or normalized.endswith("_secret"):
            return True
        if normalized.endswith("_token") and normalized not in SAFE_TOKEN_METADATA:
            return True
    if isinstance(value, str):
        stripped = value.strip()
        if re.match(r"^(sk|sess|ghp|github_pat)-[A-Za-z0-9_-]{12,}$", stripped):
            return True
    return False


def supported_value(value: Any) -> bool:
    if value is None or isinstance(value, (date, datetime, time, dict)):
        return False
    if isinstance(value, (str, bool, int, float)):
        return True
    return isinstance(value, list) and all(supported_value(item) for item in value)


def classify_path(
    path: PathTuple,
    value: Any,
    *,
    provider_paths: set[PathTuple],
) -> str:
    if not path:
        return "unowned"
    if path in provider_paths or path[0] in PROVIDER_ROOTS:
        return "provider_owned"
    if is_secret(path, value):
        return "secret"
    if path[-1].casefold() in TRANSIENT_NAMES:
        return "transient_generated"
    if path[0] == "desktop":
        return "desktop_state"
    if path[0] in RUNTIME_LOCAL_ROOTS or (
        len(path) > 1
        and path[0] == "mcp_servers"
        and path[1] in codex_state_repair.HUB_MANAGED_MCP_NAMES
    ):
        return "runtime_local"
    if path[0] in STARTUP_ROOTS:
        return "startup_managed"
    return "unowned"


def eligible(classification: str, value: Any) -> bool:
    return classification not in {"provider_owned", "secret", "transient_generated", "runtime_local"} and supported_value(value)


def read_provider(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id, name, settings_config FROM providers "
        "WHERE lower(app_type) = 'codex' AND is_current = 1 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {
            "id": "",
            "name": "",
            "config": {},
            "config_text": "",
            "model_catalog_declared": False,
            "model_catalog_active": False,
            "model_catalog_sha256": "",
            "found": False,
        }
    payload = json.loads(str(row[2] or "{}"))
    payload = payload if isinstance(payload, dict) else {}
    config_text = str(payload.get("config") or "") if isinstance(payload, dict) else ""
    config = tomllib.loads(config_text) if config_text.strip() else {}
    model_catalog_declared = "modelCatalog" in payload and payload.get("modelCatalog") is not None
    model_catalog = payload.get("modelCatalog") if model_catalog_declared else None
    model_catalog_active = model_catalog_declared and model_catalog not in ({}, [])
    return {
        "id": str(row[0]),
        "name": str(row[1]),
        "config": config,
        "config_text": config_text,
        "model_catalog_declared": model_catalog_declared,
        "model_catalog_active": model_catalog_active,
        "model_catalog_sha256": canonical_json_hash(model_catalog) if model_catalog_declared else "",
        "found": True,
    }


def provider_authority_state(db_path: Path = CC_SWITCH_DB) -> dict[str, Any]:
    try:
        connection = sqlite3.connect(db_path, timeout=5)
        try:
            provider = read_provider(connection)
        finally:
            connection.close()
    except Exception as exc:
        return {
            "schema": f"{SCHEMA}/provider-authority",
            "ok": False,
            "found": False,
            "reason": "provider_authority_unreadable",
            "error": type(exc).__name__,
        }
    return {
        "schema": f"{SCHEMA}/provider-authority",
        "ok": True,
        "found": bool(provider.get("found")),
        "provider_id": str(provider.get("id") or ""),
        "provider_name": str(provider.get("name") or ""),
        "model_catalog_declared": bool(provider.get("model_catalog_declared")),
        "model_catalog_active": bool(provider.get("model_catalog_active")),
        "model_catalog_sha256": str(provider.get("model_catalog_sha256") or ""),
    }


def read_legacy_common_config_flags(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(providers)")}
    if "meta" not in columns:
        return []
    rows: list[dict[str, Any]] = []
    for provider_id, name, is_current, raw_meta in connection.execute(
        "SELECT id, name, is_current, meta FROM providers WHERE lower(app_type) = 'codex' ORDER BY is_current DESC, sort_index"
    ):
        try:
            meta = json.loads(str(raw_meta or "{}"))
        except json.JSONDecodeError:
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        rows.append(
            {
                "id": str(provider_id),
                "name": str(name),
                "is_current": bool(is_current),
                "present": "commonConfigEnabled" in meta,
                "enabled": meta.get("commonConfigEnabled") is True,
            }
        )
    return rows


def remove_legacy_common_config_flags(connection: sqlite3.Connection) -> list[str]:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(providers)")}
    if "meta" not in columns:
        return []
    changed: list[str] = []
    for provider_id, raw_meta in connection.execute(
        "SELECT id, meta FROM providers WHERE lower(app_type) = 'codex'"
    ).fetchall():
        try:
            meta = json.loads(str(raw_meta or "{}"))
        except json.JSONDecodeError:
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        if "commonConfigEnabled" not in meta:
            continue
        del meta["commonConfigEnabled"]
        connection.execute(
            "UPDATE providers SET meta = ? WHERE id = ? AND lower(app_type) = 'codex'",
            (json.dumps(meta, ensure_ascii=False, separators=(",", ":")), str(provider_id)),
        )
        changed.append(f"remove-provider-meta:/{provider_id}/commonConfigEnabled")
    return changed


def read_legacy_common_state(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = dict(connection.execute(
        "SELECT key, value FROM settings WHERE key IN (?, ?)",
        (LEGACY_COMMON_KEY, LEGACY_MANAGED_DB_KEY),
    ).fetchall())
    common_text = str(rows.get(LEGACY_COMMON_KEY) or "")
    return {
        "common_exists": LEGACY_COMMON_KEY in rows,
        "common_bytes": len(common_text.encode("utf-8")),
        "managed_db_exists": LEGACY_MANAGED_DB_KEY in rows,
    }


def managed_projection_path(config_path: Path = CODEX_CONFIG) -> Path:
    return config_path.parent / "state" / MANAGED_FILE_NAME


def read_managed_projection(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not text.strip():
        return "", {}
    payload = json.loads(text)
    values = payload.get("values") if isinstance(payload, dict) else {}
    return text, values if isinstance(values, dict) else {}


def load_state(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> dict[str, Any]:
    live_text = config_path.read_text(encoding="utf-8")
    live = tomllib.loads(live_text)
    connection = sqlite3.connect(db_path, timeout=5)
    try:
        provider = read_provider(connection)
        legacy_common = read_legacy_common_state(connection)
        legacy_common_flags = read_legacy_common_config_flags(connection)
    finally:
        connection.close()
    managed_path = managed_projection_path(config_path)
    managed_text, managed = read_managed_projection(managed_path)
    return {
        "live_text": live_text,
        "live": live,
        "provider": provider,
        "legacy_common": legacy_common,
        "legacy_common_flags": legacy_common_flags,
        "managed_text": managed_text,
        "managed": managed,
        "managed_path": managed_path,
    }


def _set_nested(root: dict[str, Any], path: PathTuple, value: Any) -> None:
    current = root
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    if path:
        current[path[-1]] = value


def _desktop_fallback_values(readback: dict[str, Any] | None) -> dict[PathTuple, Any]:
    by_key = {
        str(row.get("key") or ""): row.get("value")
        for row in (readback or {}).get("rows", [])
        if isinstance(row, dict) and row.get("found")
    }
    return {
        path: by_key[setting_key]
        for path, setting_key in DESKTOP_SETTING_KEYS.items()
        if setting_key in by_key and supported_value(by_key[setting_key])
    }


def managed_projection_values(
    state: dict[str, Any],
    *,
    desktop_readback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adopt verified live values, then fill missing Desktop values from native state."""

    values: dict[str, Any] = {}
    live_flat = flatten(state["live"])
    provider_paths = set(flatten(state["provider"]["config"]))
    for path, value in flatten(state.get("managed") or {}).items():
        classification = classify_path(path, value, provider_paths=provider_paths)
        if eligible(classification, value):
            _set_nested(values, path, value)
    for path, value in live_flat.items():
        classification = classify_path(path, value, provider_paths=provider_paths)
        if eligible(classification, value):
            _set_nested(values, path, value)
    managed_flat = flatten(values)
    for path, value in _desktop_fallback_values(desktop_readback).items():
        if path not in live_flat and path not in managed_flat:
            _set_nested(values, path, value)
    return values


def build_plan(
    state: dict[str, Any],
    *,
    additions_only: bool = False,
    desktop_readback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    live_flat = flatten(state["live"])
    provider_flat = flatten(state["provider"]["config"])
    provider_paths = set(provider_flat)
    managed_flat = flatten(state.get("managed") or {})
    desired_managed = managed_projection_values(state, desktop_readback=desktop_readback)
    desired_managed_flat = flatten(desired_managed)
    classifications: dict[PathTuple, str] = {}
    unsupported: list[PathTuple] = []
    for path, value in live_flat.items():
        classification = classify_path(path, value, provider_paths=provider_paths)
        classifications[path] = classification
        if not supported_value(value):
            unsupported.append(path)
    replay = deep_merge(state["provider"]["config"], desired_managed)
    replay_flat = flatten(replay)
    replay_losses = [
        path
        for path, value in live_flat.items()
        if eligible(classifications[path], value) and replay_flat.get(path, object()) != value
    ]
    managed_updates = [
        path for path, value in desired_managed_flat.items()
        if path not in managed_flat or managed_flat[path] != value
    ]
    managed_removals = [path for path in managed_flat if path not in desired_managed_flat]
    active_recovery_paths = [
        path
        for path, value in desired_managed_flat.items()
        if path not in live_flat
        and eligible(classify_path(path, value, provider_paths=provider_paths), value)
    ]
    stale_provider_derived_paths = []
    if (
        state["provider"].get("found")
        and not state["provider"].get("model_catalog_active")
        and ("model_catalog_json",) in live_flat
    ):
        stale_provider_derived_paths.append(("model_catalog_json",))
    legacy_flag_rows = [row for row in state["legacy_common_flags"] if row.get("present")]
    legacy_common = state["legacy_common"]
    legacy_surface_count = int(bool(legacy_common.get("common_exists"))) + int(bool(legacy_common.get("managed_db_exists"))) + len(legacy_flag_rows)
    counts = Counter(classifications.values())
    action_rows = [
        {"path": path_pointer(path), "action": "adopt-managed", "owner_class": "owner_projection"}
        for path in managed_updates
    ] + [
        {"path": path_pointer(path), "action": "remove-managed", "owner_class": "runtime_local"}
        for path in managed_removals
    ] + [
        {"path": path_pointer(path), "action": "restore-active", "owner_class": "owner_projection"}
        for path in active_recovery_paths
    ] + [
        {"path": path_pointer(path), "action": "remove-stale-provider-derived", "owner_class": "provider_owned"}
        for path in stale_provider_derived_paths
    ]
    if legacy_surface_count:
        action_rows.append({
            "path": "/cc-switch/codex-common-config",
            "action": "retire-legacy-surface",
            "owner_class": "owner_projection",
            "surface_count": legacy_surface_count,
        })
    return {
        "schema": f"{SCHEMA}/plan",
        "ok": True,
        "generated_at": now_iso(),
        "additions_only": additions_only,
        "projection_current": not managed_updates and not managed_removals
        and not active_recovery_paths and not stale_provider_derived_paths and not legacy_surface_count,
        "unsupported": unsupported,
        "classifications": classifications,
        "classification_counts": dict(sorted(counts.items())),
        "replay_losses": replay_losses,
        "active_recovery_paths": active_recovery_paths,
        "stale_provider_derived_paths": stale_provider_derived_paths,
        "managed_updates": managed_updates,
        "managed_removals": managed_removals,
        "legacy_surface_count": legacy_surface_count,
        "legacy_flag_rows": legacy_flag_rows,
        "desired_managed": desired_managed,
        "action_rows": action_rows[:MAX_ACTION_ROWS],
        "action_row_count": len(action_rows),
        "authority_policy": "CC Switch provider state owns provider fields and model catalog projection; independent ledger owns safe non-provider recovery; retired common surfaces must remain absent",
    }


def _bare(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", value))


def _toml_name(value: str) -> str:
    return value if _bare(value) else json.dumps(value, ensure_ascii=False)


def _table_name(path: PathTuple) -> str | None:
    return ".".join(_toml_name(part) for part in path) if path else None


def _set_path(text: str, path: PathTuple, value: Any) -> tuple[str, bool]:
    if not path:
        return text, False
    if not _bare(path[-1]):
        raise ValueError(f"unsupported_non_bare_leaf:{path_pointer(path)}")
    return codex_state_repair.set_table_key(text, _table_name(path[:-1]), path[-1], value)


def _remove_path(text: str, path: PathTuple) -> tuple[str, bool]:
    if not path or not _bare(path[-1]):
        raise ValueError(f"unsupported_remove_path:{path_pointer(path)}")
    lines = text.splitlines()
    if len(path) == 1:
        start = 0
        end = len(lines)
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                end = index
                break
    else:
        start, end = codex_state_repair.find_table(lines, _table_name(path[:-1]) or "")
        if start is None or end is None:
            return text, False
        start += 1
    key_pattern = re.compile(rf"^{re.escape(path[-1])}\s*=")
    for index in range(start, end):
        if key_pattern.match(lines[index].strip()):
            del lines[index]
            return "\n".join(lines).rstrip() + "\n", True
    return text, False


def render_active_config(
    state: dict[str, Any],
    *,
    managed_values: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Restore missing owner-managed fields without overriding explicit live values."""

    text = state["live_text"]
    live_flat = flatten(state["live"])
    provider_paths = set(flatten(state["provider"]["config"]))
    managed_flat = flatten(managed_values if managed_values is not None else (state.get("managed") or {}))
    changed: list[str] = []
    if state["provider"].get("found") and not state["provider"].get("model_catalog_active"):
        text, did_change = _remove_path(text, ("model_catalog_json",))
        if did_change:
            changed.append("remove-active-provider-derived:/model_catalog_json")
    for path, value in managed_flat.items():
        classification = classify_path(path, value, provider_paths=provider_paths)
        if path in live_flat or not eligible(classification, value):
            continue
        text, did_change = _set_path(text, path, value)
        if did_change:
            changed.append(f"active-config:{path_pointer(path)}")
    tomllib.loads(text)
    return text, changed


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.projection-{os.getpid()}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _desktop_setting_expression(entries: list[dict[str, Any]], *, apply: bool) -> str:
    payload = json.dumps({"entries": entries, "apply": apply}, ensure_ascii=False, separators=(",", ":"))
    return r"""
(async () => {
  const payload = __PAYLOAD__;
  const result = {ok: true, rows: [], reason: ''};
  function appPost(method, body) {
    return new Promise((resolve) => {
      const requestId = crypto.randomUUID();
      const timer = setTimeout(() => { cleanup(); resolve({timeout: true}); }, 5000);
      function cleanup() { clearTimeout(timer); window.removeEventListener('message', onMessage); }
      function onMessage(event) {
        const data = event.data;
        if (!data || data.type !== 'fetch-response' || data.requestId !== requestId) return;
        cleanup(); resolve(data);
      }
      window.addEventListener('message', onMessage);
      window.electronBridge.sendMessageFromView({
        type: 'fetch', requestId, method: 'POST', url: 'vscode://codex/' + method,
        body: body == null ? undefined : JSON.stringify(body),
      }).catch((error) => { cleanup(); resolve({bridgeError: String(error)}); });
    });
  }
  function body(response) {
    if (!response || response.responseType !== 'success' || typeof response.bodyJsonString !== 'string') return null;
    try { return JSON.parse(response.bodyJsonString); } catch { return null; }
  }
  for (const entry of payload.entries || []) {
    const beforeResponse = await appPost('get-setting', {key: entry.key});
    const beforeBody = body(beforeResponse);
    const before = beforeBody ? beforeBody.value : undefined;
    const changed = JSON.stringify(before) !== JSON.stringify(entry.value);
    let applied = false;
    if (payload.apply && changed) {
      const setResponse = await appPost('set-setting', {key: entry.key, value: entry.value});
      applied = Boolean(setResponse && setResponse.responseType === 'success');
    }
    const afterResponse = payload.apply && changed ? await appPost('get-setting', {key: entry.key}) : beforeResponse;
    const afterBody = body(afterResponse);
    const after = afterBody ? afterBody.value : undefined;
    const confirmed = JSON.stringify(after) === JSON.stringify(entry.value);
    result.rows.push({key: entry.key, changed, applied, confirmed});
    if (!confirmed) result.ok = false;
  }
  if (!result.ok) result.reason = 'one_or_more_desktop_settings_not_confirmed';
  return result;
})()
""".replace("__PAYLOAD__", payload)


def _desktop_readback_expression(keys: list[str]) -> str:
    payload = json.dumps({"keys": keys}, ensure_ascii=False, separators=(",", ":"))
    return r"""
(async () => {
  const payload = __PAYLOAD__;
  const result = {ok: true, rows: [], reason: ''};
  function appPost(method, body) {
    return new Promise((resolve) => {
      const requestId = crypto.randomUUID();
      const timer = setTimeout(() => { cleanup(); resolve({timeout: true}); }, 5000);
      function cleanup() { clearTimeout(timer); window.removeEventListener('message', onMessage); }
      function onMessage(event) {
        const data = event.data;
        if (!data || data.type !== 'fetch-response' || data.requestId !== requestId) return;
        cleanup(); resolve(data);
      }
      window.addEventListener('message', onMessage);
      window.electronBridge.sendMessageFromView({
        type: 'fetch', requestId, method: 'POST', url: 'vscode://codex/get-setting',
        body: JSON.stringify({key: body.key}),
      }).catch((error) => { cleanup(); resolve({bridgeError: String(error)}); });
    });
  }
  for (const key of payload.keys || []) {
    const response = await appPost('get-setting', {key});
    let value;
    if (response && response.responseType === 'success' && typeof response.bodyJsonString === 'string') {
      try { value = JSON.parse(response.bodyJsonString).value; } catch {}
    }
    const found = value !== undefined;
    result.rows.push({key, found, value});
  }
  return result;
})()
""".replace("__PAYLOAD__", payload)


def desktop_readback_state() -> dict[str, Any]:
    base = {
        "schema": f"{SCHEMA}/desktop-readback",
        "ok": True,
        "rows": [],
        "skipped": False,
        "reason": "",
    }
    port, ws_url, pages, reason = codex_desktop_model_runtime._find_codex_page()
    if not ws_url:
        return {**base, "skipped": True, "cdp_port": port, "page_count": len(pages), "reason": reason or "desktop_not_running"}
    client = None
    try:
        client = codex_desktop_model_runtime._CdpClient(ws_url)
        result = client.evaluate(_desktop_readback_expression(list(DESKTOP_SETTING_KEYS.values())))
    except Exception as exc:
        return {**base, "ok": False, "reason": "desktop_setting_readback_failed", "error": type(exc).__name__}
    finally:
        if client is not None:
            client.close()
    result = result if isinstance(result, dict) else {}
    return {
        **base,
        "ok": bool(result.get("ok")),
        "cdp_port": port,
        "page_count": len(pages),
        "rows": result.get("rows") if isinstance(result.get("rows"), list) else [],
        "reason": str(result.get("reason") or ""),
    }


def desktop_projection_state(effective_config: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    live_flat = flatten(effective_config)
    entries = [
        {"key": setting_key, "value": live_flat[path]}
        for path, setting_key in DESKTOP_SETTING_KEYS.items()
        if path in live_flat and supported_value(live_flat[path])
    ]
    base = {
        "schema": f"{SCHEMA}/desktop-state",
        "ok": True,
        "apply": apply,
        "entry_count": len(entries),
        "rows": [],
        "skipped": False,
        "reason": "",
    }
    if not entries:
        return {**base, "skipped": True, "reason": "no_mapped_desktop_settings"}
    port, ws_url, pages, reason = codex_desktop_model_runtime._find_codex_page()
    if not ws_url:
        return {**base, "skipped": True, "cdp_port": port, "page_count": len(pages), "reason": reason or "desktop_not_running"}
    client = None
    try:
        client = codex_desktop_model_runtime._CdpClient(ws_url)
        result = client.evaluate(_desktop_setting_expression(entries, apply=apply))
    except Exception as exc:
        return {**base, "ok": False, "reason": "desktop_setting_projection_failed", "error": type(exc).__name__}
    finally:
        if client is not None:
            client.close()
    result = result if isinstance(result, dict) else {}
    return {
        **base,
        "ok": bool(result.get("ok")),
        "cdp_port": port,
        "page_count": len(pages),
        "rows": result.get("rows") if isinstance(result.get("rows"), list) else [],
        "reason": str(result.get("reason") or ""),
    }


def _acquire_lock() -> Any:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def projection_signature(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> str:
    try:
        state = load_state(config_path, db_path)
        plan = build_plan(state)
        live_flat = flatten(state["live"])
        safe_live = {
            path_pointer(path): value
            for path, value in live_flat.items()
            if eligible(plan["classifications"][path], value)
        }
        payload = {
            "live": safe_live,
            "managed": state.get("managed") or {},
            "provider": state["provider"]["id"],
            "provider_model_catalog_active": bool(state["provider"].get("model_catalog_active")),
            "provider_model_catalog_sha256": str(state["provider"].get("model_catalog_sha256") or ""),
            "legacy_common": state["legacy_common"],
            "legacy_common_flags": [
                {"id": row.get("id"), "present": row.get("present")}
                for row in state["legacy_common_flags"]
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    except Exception as exc:
        return f"unavailable:{type(exc).__name__}"


def snapshot(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> dict[str, Any]:
    try:
        state = load_state(config_path, db_path)
        plan = build_plan(state)
    except Exception as exc:
        return {
            "schema": f"{SCHEMA}/snapshot",
            "ok": False,
            "generated_at": now_iso(),
            "config_path": str(config_path),
            "db_path": str(db_path),
            "reason": "projection_source_unreadable",
            "error": type(exc).__name__,
        }
    unowned = [path_pointer(path) for path, owner in plan["classifications"].items() if owner == "unowned"]
    legacy_flags = [row for row in state["legacy_common_flags"] if row.get("present")]
    return {
        "schema": f"{SCHEMA}/snapshot",
        "ok": True,
        "generated_at": now_iso(),
        "config_path": str(config_path),
        "db_path": str(db_path),
        "managed_path": str(state["managed_path"]),
        "provider": {
            "id": state["provider"]["id"],
            "name": state["provider"]["name"],
            "found": state["provider"]["found"],
            "model_catalog_declared": bool(state["provider"].get("model_catalog_declared")),
            "model_catalog_active": bool(state["provider"].get("model_catalog_active")),
            "model_catalog_sha256": str(state["provider"].get("model_catalog_sha256") or ""),
        },
        "legacy_common_exists": bool(state["legacy_common"].get("common_exists")),
        "legacy_common_bytes": int(state["legacy_common"].get("common_bytes") or 0),
        "legacy_managed_db_exists": bool(state["legacy_common"].get("managed_db_exists")),
        "legacy_common_flag_count": len(legacy_flags),
        "legacy_common_flag_providers": legacy_flags[:MAX_ACTION_ROWS],
        "legacy_surface_count": int(plan["legacy_surface_count"]),
        "live_leaf_count": len(flatten(state["live"])),
        "managed_leaf_count": len(flatten(state.get("managed") or {})),
        "provider_leaf_count": len(flatten(state["provider"]["config"])),
        "projection_current": plan["projection_current"],
        "classification_counts": plan["classification_counts"],
        "action_rows": plan["action_rows"],
        "action_row_count": plan["action_row_count"],
        "replay_loss_count": len(plan["replay_losses"]),
        "replay_loss_paths": [path_pointer(path) for path in plan["replay_losses"][:MAX_ACTION_ROWS]],
        "active_recovery_count": len(plan["active_recovery_paths"]),
        "active_recovery_paths": [path_pointer(path) for path in plan["active_recovery_paths"][:MAX_ACTION_ROWS]],
        "stale_provider_derived_count": len(plan["stale_provider_derived_paths"]),
        "stale_provider_derived_paths": [
            path_pointer(path) for path in plan["stale_provider_derived_paths"][:MAX_ACTION_ROWS]
        ],
        "managed_update_count": len(plan["managed_updates"]),
        "managed_removal_count": len(plan["managed_removals"]),
        "unowned_count": len(unowned),
        "unowned_paths": unowned[:MAX_ACTION_ROWS],
        "unsupported_count": len(plan["unsupported"]),
        "signature": projection_signature(config_path, db_path),
        "policy": {
            "unknown_safe_fields": "preserve",
            "provider_and_secrets": "never_copy_to_managed_projection",
            "model_catalog": "CC Switch provider-owned; generated file and config pointer are replaceable projections",
            "runtime_local_roots": "excluded_from_owner_projection",
            "recovery_authority": "owner_managed_ledger_plus_startup_baseline_plus_native_desktop_readback",
            "cc_switch_common_config": "retired_and_must_remain_absent",
            "explicit_live_conflict": "live_value_wins_and_is_adopted",
        },
    }


def doctor(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> dict[str, Any]:
    snap = snapshot(config_path, db_path)
    issues: list[dict[str, Any]] = []
    advisories: list[dict[str, Any]] = []
    if not snap.get("ok"):
        issues.append({"severity": "high", "code": "projection_source_unreadable", "next_action": "repair CC Switch/config source before applying projection"})
    elif not snap.get("projection_current"):
        issues.append({
            "severity": "medium",
            "code": "codex_owner_projection_drift",
            "message": "Safe non-provider settings are not synchronized across the active config and owner-managed projection.",
            "actionable_rows": snap.get("action_rows", []),
            "next_action": "python _bridge\\codex_config_projection.py apply",
        })
    if snap.get("replay_loss_count"):
        issues.append({
            "severity": "medium",
            "code": "cc_switch_replay_would_lose_fields",
            "message": "A provider rebuild plus independent ledger replay would not reproduce all safe live settings.",
            "affected_paths": snap.get("replay_loss_paths", []),
            "next_action": "python _bridge\\codex_config_projection.py apply",
        })
    if snap.get("active_recovery_count"):
        issues.append({
            "severity": "high",
            "code": "active_config_missing_managed_fields",
            "message": "The active Codex config is missing safe fields preserved by the owner-managed projection.",
            "affected_paths": snap.get("active_recovery_paths", []),
            "next_action": "python _bridge\\codex_config_projection.py apply",
        })
    if snap.get("stale_provider_derived_count"):
        issues.append({
            "severity": "high",
            "code": "stale_provider_model_catalog_pointer",
            "message": "The active provider has no CC Switch modelCatalog, but the Codex config still references a generated catalog.",
            "affected_paths": snap.get("stale_provider_derived_paths", []),
            "next_action": "python _bridge\\codex_config_projection.py apply",
        })
    if snap.get("legacy_surface_count"):
        issues.append({
            "severity": "medium",
            "code": "retired_cc_switch_common_config_present",
            "message": "Retired CC Switch Codex common-config data or provider flags are still present.",
            "affected_providers": snap.get("legacy_common_flag_providers", []),
            "next_action": "python _bridge\\codex_config_projection.py apply",
        })
    return {
        "schema": f"{SCHEMA}/doctor",
        "ok": bool(snap.get("ok")) and not issues,
        "generated_at": now_iso(),
        "issues": issues,
        "advisories": advisories,
        "snapshot": snap,
    }


def apply_projection(
    config_path: Path = CODEX_CONFIG,
    db_path: Path = CC_SWITCH_DB,
    *,
    additions_only: bool = False,
    sync_desktop: bool = True,
    backup: bool = True,
    reseed_managed: bool = False,
) -> dict[str, Any]:
    capture_lease = capture_lease_state()
    if capture_lease.get("active"):
        return {
            "schema": f"{SCHEMA}/apply",
            "ok": True,
            "deferred": True,
            "reason": "mirror_capture_lease_active",
            "capture_lease": capture_lease,
            "changed_paths": [],
            "changed_count": 0,
        }
    lock = _acquire_lock()
    if lock is None:
        return {"schema": f"{SCHEMA}/apply", "ok": False, "reason": "projection_operation_busy"}
    backup_receipt: dict[str, Any] = {"ok": True, "skipped": True, "reason": "no_projection_write"}
    changed: list[str] = []
    active_changed: list[str] = []
    try:
        state = load_state(config_path, db_path)
        desktop_readback = desktop_readback_state() if sync_desktop else {
            "schema": f"{SCHEMA}/desktop-readback",
            "ok": True,
            "skipped": True,
            "reason": "desktop_sync_not_requested",
            "rows": [],
        }
        if reseed_managed:
            state = {**state, "managed_text": "", "managed": {}}
        plan = build_plan(state, additions_only=additions_only, desktop_readback=desktop_readback)
        active_candidate, active_changed = render_active_config(state, managed_values=plan["desired_managed"])
        managed_candidate_text = json.dumps(
            {"schema": "codex-managed-config-projection/v1", "values": plan["desired_managed"]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        managed_change_needed = managed_candidate_text != state.get("managed_text", "")
        active_config_change_needed = active_candidate != state["live_text"]
        legacy_cleanup_needed = bool(plan["legacy_surface_count"])
        if managed_change_needed or active_config_change_needed or legacy_cleanup_needed:
            if backup:
                backup_paths = [str(config_path)]
                if legacy_cleanup_needed:
                    backup_paths.append(str(db_path))
                if Path(state["managed_path"]).exists():
                    backup_paths.append(str(state["managed_path"]))
                backup_receipt = create_backup(
                    backup_paths,
                    remark="before-codex-config-projection",
                    purpose="Preserve configuration authorities before independent Codex projection",
                    category="codex-config",
                )
                if not backup_receipt.get("ok"):
                    return {"schema": f"{SCHEMA}/apply", "ok": False, "reason": "backup_failed", "backup": backup_receipt}
            if legacy_cleanup_needed:
                connection = sqlite3.connect(db_path, timeout=10)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    deleted_common = connection.execute(
                        "DELETE FROM settings WHERE key = ?", (LEGACY_COMMON_KEY,)
                    ).rowcount
                    deleted_managed = connection.execute(
                        "DELETE FROM settings WHERE key = ?", (LEGACY_MANAGED_DB_KEY,)
                    ).rowcount
                    if deleted_common:
                        changed.append("remove-legacy-setting:/common_config_codex")
                    if deleted_managed:
                        changed.append("remove-legacy-setting:/codex_managed_projection_v1")
                    changed.extend(remove_legacy_common_config_flags(connection))
                    connection.commit()
                finally:
                    connection.close()
            if managed_change_needed:
                changed.extend(f"managed:{path_pointer(path)}" for path in plan["managed_updates"])
                changed.extend(f"remove-managed:{path_pointer(path)}" for path in plan["managed_removals"])
                _atomic_write_text(Path(state["managed_path"]), managed_candidate_text + "\n")
            if active_config_change_needed:
                changed.extend(active_changed)
                _atomic_write_text(config_path, active_candidate)
        after_state = load_state(config_path, db_path)
        after_plan = build_plan(after_state, additions_only=additions_only, desktop_readback=desktop_readback)
        if after_plan["active_recovery_paths"]:
            active_candidate, active_changed = render_active_config(
                after_state,
                managed_values=after_plan["desired_managed"],
            )
            _atomic_write_text(config_path, active_candidate)
            changed.extend(active_changed)
            after_state = load_state(config_path, db_path)
            after_plan = build_plan(after_state, additions_only=additions_only, desktop_readback=desktop_readback)
        desktop_state = desktop_projection_state(after_state["live"], apply=True) if sync_desktop else {
            "schema": f"{SCHEMA}/desktop-state",
            "ok": True,
            "skipped": True,
            "reason": "desktop_sync_not_requested",
        }
        return {
            "schema": f"{SCHEMA}/apply",
            "ok": not after_plan["managed_updates"]
            and not after_plan["active_recovery_paths"]
            and not after_plan["managed_removals"]
            and not after_plan["legacy_surface_count"]
            and bool(desktop_state.get("ok")),
            "generated_at": now_iso(),
            "additions_only": additions_only,
            "reseed_managed": reseed_managed,
            "changed_paths": changed,
            "changed_count": len(changed),
            "active_config_changed_paths": active_changed,
            "active_config_changed_count": len(active_changed),
            "backup": backup_receipt,
            "desktop_state": desktop_state,
            "desktop_readback": desktop_readback,
            "remaining_active_recovery_count": len(after_plan["active_recovery_paths"]),
            "remaining_stale_provider_derived_count": len(after_plan["stale_provider_derived_paths"]),
            "remaining_managed_update_count": len(after_plan["managed_updates"]),
            "remaining_legacy_surface_count": int(after_plan["legacy_surface_count"]),
            "replay_loss_count": len(after_plan["replay_losses"]),
        }
    except Exception as exc:
        return {
            "schema": f"{SCHEMA}/apply",
            "ok": False,
            "generated_at": now_iso(),
            "reason": "projection_apply_failed",
            "error": type(exc).__name__,
            "detail": str(exc)[:500],
        }
    finally:
        lock.close()


def validate(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> dict[str, Any]:
    snap = snapshot(config_path, db_path)
    checks = [
        {"name": "sources_readable", "ok": bool(snap.get("ok"))},
        {"name": "safe_projection_current", "ok": bool(snap.get("projection_current"))},
        {"name": "managed_projection_current", "ok": int(snap.get("managed_update_count") or 0) == 0},
        {"name": "managed_projection_has_no_runtime_local_state", "ok": int(snap.get("managed_removal_count") or 0) == 0},
        {"name": "retired_common_config_surfaces_absent", "ok": int(snap.get("legacy_surface_count") or 0) == 0},
        {"name": "replay_has_no_safe_field_loss", "ok": int(snap.get("replay_loss_count") or 0) == 0},
        {"name": "active_config_has_all_owner_managed_fields", "ok": int(snap.get("active_recovery_count") or 0) == 0},
        {"name": "provider_catalog_projection_has_no_stale_pointer", "ok": int(snap.get("stale_provider_derived_count") or 0) == 0},
        {"name": "secret_values_not_exposed", "ok": "experimental_bearer_token" not in json.dumps(snap, ensure_ascii=False)},
    ]
    return {
        "schema": f"{SCHEMA}/validate",
        "ok": all(row["ok"] for row in checks if row.get("required", True)),
        "generated_at": now_iso(),
        "checks": checks,
        "actionable_rows": snap.get("action_rows", []),
        "snapshot": snap,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex/CC Switch configuration projection owner")
    parser.add_argument("action", choices=["snapshot", "doctor", "plan", "apply", "validate"])
    parser.add_argument("--additions-only", action="store_true", help="Compatibility alias; explicit live values always win")
    parser.add_argument("--no-desktop-sync", action="store_true")
    parser.add_argument("--reseed-managed", action="store_true", help="Rebuild the owner-managed projection from current verified active state")
    parser.add_argument("--confirm-reseed", default="")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)
    if args.reseed_managed and args.confirm_reseed != "RESEED-MANAGED":
        result = {"schema": f"{SCHEMA}/apply", "ok": False, "reason": "managed_reseed_confirmation_required"}
    elif args.action == "snapshot":
        result = snapshot()
    elif args.action == "doctor":
        result = doctor()
    elif args.action == "plan":
        state = load_state()
        plan = build_plan(state, additions_only=args.additions_only)
        result = {
            **{key: value for key, value in plan.items() if key not in {"unsupported", "classifications", "replay_losses", "active_recovery_paths", "stale_provider_derived_paths", "managed_updates", "managed_removals", "legacy_flag_rows", "desired_managed"}},
            "replay_loss_paths": [path_pointer(path) for path in plan["replay_losses"][:MAX_ACTION_ROWS]],
            "active_recovery_paths": [path_pointer(path) for path in plan["active_recovery_paths"][:MAX_ACTION_ROWS]],
            "stale_provider_derived_paths": [
                path_pointer(path) for path in plan["stale_provider_derived_paths"][:MAX_ACTION_ROWS]
            ],
            "managed_update_paths": [path_pointer(path) for path in plan["managed_updates"][:MAX_ACTION_ROWS]],
        }
    elif args.action == "apply":
        result = apply_projection(
            additions_only=args.additions_only,
            sync_desktop=not args.no_desktop_sync,
            reseed_managed=args.reseed_managed,
        )
    else:
        result = validate()
    projector = aggregate_validator_cli_payload if args.action == "validate" else governed_cli_payload
    output = projector(
        result,
        full=args.full,
        full_result_ref=f"command:python _bridge/codex_config_projection.py {args.action} --full",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
