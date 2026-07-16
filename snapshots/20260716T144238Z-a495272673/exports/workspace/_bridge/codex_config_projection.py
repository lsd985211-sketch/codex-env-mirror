#!/usr/bin/env python3
"""Preserve Codex configuration across CC Switch rebuilds.

This owner projects non-provider Codex settings into CC Switch's common
configuration without copying credentials or deleting absent keys. Provider
selection remains owned by CC Switch, startup invariants remain owned by the
config guard, and Desktop runtime settings are updated through the native host
API when it is available.
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


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
CC_SWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"
COMMON_KEY = "common_config_codex"
RUNTIME_DIR = BRIDGE / "runtime" / "codex_config_projection"
LOCK_PATH = RUNTIME_DIR / "projection.lock"
SCHEMA = "codex-config-projection/v1"
MAX_ACTION_ROWS = 40

if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_desktop_model_runtime  # noqa: E402
import codex_state_repair  # noqa: E402
from shared.backup_router import create_backup  # noqa: E402


PathTuple = tuple[str, ...]
PROVIDER_ROOTS = {"model", "model_provider", "model_reasoning_effort", "model_providers"}
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


def pointer_path(pointer: str) -> PathTuple:
    if not pointer.startswith("/"):
        raise ValueError("path_must_be_json_pointer")
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/") if part)


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
    common_paths: set[PathTuple],
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
    if path[0] in STARTUP_ROOTS:
        return "startup_managed"
    if path in common_paths:
        return "cc_common"
    return "unowned"


def eligible(classification: str, value: Any) -> bool:
    return classification not in {"provider_owned", "secret", "transient_generated"} and supported_value(value)


def read_provider(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id, name, settings_config FROM providers "
        "WHERE lower(app_type) = 'codex' AND is_current = 1 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"id": "", "name": "", "config": {}, "config_text": "", "found": False}
    payload = json.loads(str(row[2] or "{}"))
    config_text = str(payload.get("config") or "") if isinstance(payload, dict) else ""
    config = tomllib.loads(config_text) if config_text.strip() else {}
    return {"id": str(row[0]), "name": str(row[1]), "config": config, "config_text": config_text, "found": True}


def read_common(connection: sqlite3.Connection) -> tuple[str, dict[str, Any]]:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (COMMON_KEY,)).fetchone()
    text = str(row[0] or "") if row else ""
    return text, tomllib.loads(text) if text.strip() else {}


def load_state(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> dict[str, Any]:
    live_text = config_path.read_text(encoding="utf-8")
    live = tomllib.loads(live_text)
    connection = sqlite3.connect(db_path, timeout=5)
    try:
        provider = read_provider(connection)
        common_text, common = read_common(connection)
    finally:
        connection.close()
    return {
        "live_text": live_text,
        "live": live,
        "provider": provider,
        "common_text": common_text,
        "common": common,
    }


def build_plan(state: dict[str, Any], *, additions_only: bool = False) -> dict[str, Any]:
    live_flat = flatten(state["live"])
    provider_flat = flatten(state["provider"]["config"])
    common_flat = flatten(state["common"])
    provider_paths = set(provider_flat)
    common_paths = set(common_flat)
    classifications: dict[PathTuple, str] = {}
    additions: list[PathTuple] = []
    updates: list[PathTuple] = []
    unsupported: list[PathTuple] = []
    for path, value in live_flat.items():
        classification = classify_path(
            path,
            value,
            provider_paths=provider_paths,
            common_paths=common_paths,
        )
        classifications[path] = classification
        if not supported_value(value):
            unsupported.append(path)
            continue
        if not eligible(classification, value):
            continue
        if path not in common_flat:
            additions.append(path)
        elif common_flat[path] != value and not additions_only:
            updates.append(path)
    replay = deep_merge(state["common"], state["provider"]["config"])
    replay_flat = flatten(replay)
    replay_losses = [
        path
        for path, value in live_flat.items()
        if eligible(classifications[path], value) and replay_flat.get(path, object()) != value
    ]
    common_only = [path for path in common_flat if path not in live_flat]
    counts = Counter(classifications.values())
    action_rows = [
        {"path": path_pointer(path), "action": "add", "owner_class": classifications[path]}
        for path in additions
    ] + [
        {"path": path_pointer(path), "action": "update", "owner_class": classifications[path]}
        for path in updates
    ]
    return {
        "schema": f"{SCHEMA}/plan",
        "ok": True,
        "generated_at": now_iso(),
        "additions_only": additions_only,
        "projection_current": not additions and not updates,
        "additions": additions,
        "updates": updates,
        "common_only": common_only,
        "unsupported": unsupported,
        "classifications": classifications,
        "classification_counts": dict(sorted(counts.items())),
        "replay_losses": replay_losses,
        "action_rows": action_rows[:MAX_ACTION_ROWS],
        "action_row_count": len(action_rows),
        "deletion_policy": "source absence never deletes common configuration; removal requires an explicit JSON pointer and confirmation",
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
        return text, False
    table = _table_name(path[:-1])
    start, end = (0, len(text.splitlines())) if table is None else codex_state_repair.find_table(text.splitlines(), table)
    if start is None or end is None:
        return text, False
    lines = text.splitlines()
    begin = start if table is None else start + 1
    key = path[-1]
    for index in range(begin, end):
        stripped = lines[index].strip()
        if stripped.startswith(f"{key} ") or stripped.startswith(f"{key}="):
            del lines[index]
            return "\n".join(lines).rstrip() + "\n", True
    return text, False


def render_common(
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    removals: tuple[PathTuple, ...] = (),
) -> tuple[str, list[str]]:
    text = state["common_text"]
    changed: list[str] = []
    live_flat = flatten(state["live"])
    for path in [*plan["additions"], *plan["updates"]]:
        text, did_change = _set_path(text, path, live_flat[path])
        if did_change:
            changed.append(path_pointer(path))
    for path in removals:
        text, did_change = _remove_path(text, path)
        if did_change:
            changed.append(f"remove:{path_pointer(path)}")
    tomllib.loads(text)
    return text, changed


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


def desktop_projection_state(live: dict[str, Any], *, apply: bool) -> dict[str, Any]:
    live_flat = flatten(live)
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
    import msvcrt

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
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


def projection_signature(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> str:
    try:
        state = load_state(config_path, db_path)
        plan = build_plan(state)
        live_flat = flatten(state["live"])
        common_flat = flatten(state["common"])
        safe_live = {
            path_pointer(path): value
            for path, value in live_flat.items()
            if eligible(plan["classifications"][path], value)
        }
        safe_common = {path_pointer(path): value for path, value in common_flat.items() if not is_secret(path, value)}
        payload = {"live": safe_live, "common": safe_common, "provider": state["provider"]["id"]}
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
    return {
        "schema": f"{SCHEMA}/snapshot",
        "ok": True,
        "generated_at": now_iso(),
        "config_path": str(config_path),
        "db_path": str(db_path),
        "provider": {"id": state["provider"]["id"], "name": state["provider"]["name"], "found": state["provider"]["found"]},
        "live_leaf_count": len(flatten(state["live"])),
        "common_leaf_count": len(flatten(state["common"])),
        "provider_leaf_count": len(flatten(state["provider"]["config"])),
        "projection_current": plan["projection_current"],
        "classification_counts": plan["classification_counts"],
        "action_rows": plan["action_rows"],
        "action_row_count": plan["action_row_count"],
        "replay_loss_count": len(plan["replay_losses"]),
        "replay_loss_paths": [path_pointer(path) for path in plan["replay_losses"][:MAX_ACTION_ROWS]],
        "common_only_count": len(plan["common_only"]),
        "unowned_count": len(unowned),
        "unowned_paths": unowned[:MAX_ACTION_ROWS],
        "unsupported_count": len(plan["unsupported"]),
        "signature": projection_signature(config_path, db_path),
        "policy": {
            "unknown_safe_fields": "preserve",
            "automatic_deletion": "forbidden",
            "provider_and_secrets": "never_copy_to_common",
            "automatic_mode": "additions_only",
            "explicit_mode": "additions_and_updates",
        },
    }


def doctor(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> dict[str, Any]:
    snap = snapshot(config_path, db_path)
    issues: list[dict[str, Any]] = []
    if not snap.get("ok"):
        issues.append({"severity": "high", "code": "projection_source_unreadable", "next_action": "repair CC Switch/config source before applying projection"})
    elif not snap.get("projection_current"):
        issues.append({
            "severity": "medium",
            "code": "cc_switch_common_projection_drift",
            "message": "Safe non-provider settings are not fully represented in CC Switch common configuration.",
            "actionable_rows": snap.get("action_rows", []),
            "next_action": "python _bridge\\codex_config_projection.py apply",
        })
    if snap.get("replay_loss_count"):
        issues.append({
            "severity": "medium",
            "code": "cc_switch_replay_would_lose_fields",
            "message": "A provider/common rebuild would not reproduce all safe live settings.",
            "affected_paths": snap.get("replay_loss_paths", []),
            "next_action": "python _bridge\\codex_config_projection.py apply",
        })
    return {
        "schema": f"{SCHEMA}/doctor",
        "ok": bool(snap.get("ok")) and not issues,
        "generated_at": now_iso(),
        "issues": issues,
        "snapshot": snap,
    }


def apply_projection(
    config_path: Path = CODEX_CONFIG,
    db_path: Path = CC_SWITCH_DB,
    *,
    additions_only: bool = False,
    removals: tuple[PathTuple, ...] = (),
    sync_desktop: bool = True,
    backup: bool = True,
) -> dict[str, Any]:
    lock = _acquire_lock()
    if lock is None:
        return {"schema": f"{SCHEMA}/apply", "ok": False, "reason": "projection_operation_busy"}
    backup_receipt: dict[str, Any] = {"ok": True, "skipped": True, "reason": "no_database_write"}
    changed: list[str] = []
    try:
        state = load_state(config_path, db_path)
        plan = build_plan(state, additions_only=additions_only)
        candidate, planned_changes = render_common(state, plan, removals=removals)
        if candidate != state["common_text"]:
            if backup:
                backup_receipt = create_backup(
                    [str(db_path)],
                    remark="before-codex-config-projection",
                    purpose="Preserve CC Switch state before common Codex configuration projection",
                    category="codex-config",
                )
                if not backup_receipt.get("ok"):
                    return {"schema": f"{SCHEMA}/apply", "ok": False, "reason": "backup_failed", "backup": backup_receipt}
            connection = sqlite3.connect(db_path, timeout=10)
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_text, current_common = read_common(connection)
                current_state = {**state, "common_text": current_text, "common": current_common}
                current_plan = build_plan(current_state, additions_only=additions_only)
                candidate, changed = render_common(current_state, current_plan, removals=removals)
                if candidate != current_text:
                    connection.execute(
                        "INSERT INTO settings(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (COMMON_KEY, candidate),
                    )
                connection.commit()
            finally:
                connection.close()
        else:
            changed = planned_changes
        after_state = load_state(config_path, db_path)
        after_plan = build_plan(after_state, additions_only=additions_only)
        desktop_state = desktop_projection_state(after_state["live"], apply=True) if sync_desktop else {
            "schema": f"{SCHEMA}/desktop-state",
            "ok": True,
            "skipped": True,
            "reason": "desktop_sync_not_requested",
        }
        return {
            "schema": f"{SCHEMA}/apply",
            "ok": not after_plan["additions"] and (additions_only or not after_plan["updates"]) and bool(desktop_state.get("ok")),
            "generated_at": now_iso(),
            "additions_only": additions_only,
            "changed_paths": changed,
            "changed_count": len(changed),
            "backup": backup_receipt,
            "desktop_state": desktop_state,
            "remaining_addition_count": len(after_plan["additions"]),
            "remaining_update_count": len(after_plan["updates"]),
            "replay_loss_count": len(after_plan["replay_losses"]),
            "deletion_count": len(removals),
        }
    except Exception as exc:
        return {"schema": f"{SCHEMA}/apply", "ok": False, "generated_at": now_iso(), "reason": "projection_apply_failed", "error": type(exc).__name__}
    finally:
        lock.close()


def validate(config_path: Path = CODEX_CONFIG, db_path: Path = CC_SWITCH_DB) -> dict[str, Any]:
    snap = snapshot(config_path, db_path)
    checks = [
        {"name": "sources_readable", "ok": bool(snap.get("ok"))},
        {"name": "safe_projection_current", "ok": bool(snap.get("projection_current"))},
        {"name": "replay_has_no_safe_field_loss", "ok": int(snap.get("replay_loss_count") or 0) == 0},
        {"name": "secret_values_not_exposed", "ok": "experimental_bearer_token" not in json.dumps(snap, ensure_ascii=False)},
    ]
    return {
        "schema": f"{SCHEMA}/validate",
        "ok": all(row["ok"] for row in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "actionable_rows": snap.get("action_rows", []),
        "snapshot": snap,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex/CC Switch configuration projection owner")
    parser.add_argument("action", choices=["snapshot", "doctor", "plan", "apply", "validate"])
    parser.add_argument("--additions-only", action="store_true", help="Only add missing safe keys; do not adopt differing values")
    parser.add_argument("--remove", action="append", default=[], metavar="JSON_POINTER")
    parser.add_argument("--confirm-remove", default="")
    parser.add_argument("--no-desktop-sync", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(argv)
    removals = tuple(pointer_path(item) for item in args.remove)
    if removals and args.confirm_remove != "EXPLICIT-REMOVE":
        result = {"schema": f"{SCHEMA}/apply", "ok": False, "reason": "explicit_removal_confirmation_required"}
    elif args.action == "snapshot":
        result = snapshot()
    elif args.action == "doctor":
        result = doctor()
    elif args.action == "plan":
        state = load_state()
        plan = build_plan(state, additions_only=args.additions_only)
        result = {
            **{key: value for key, value in plan.items() if key not in {"additions", "updates", "common_only", "unsupported", "classifications", "replay_losses"}},
            "replay_loss_paths": [path_pointer(path) for path in plan["replay_losses"][:MAX_ACTION_ROWS]],
        }
    elif args.action == "apply":
        result = apply_projection(
            additions_only=args.additions_only,
            removals=removals,
            sync_desktop=not args.no_desktop_sync,
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
