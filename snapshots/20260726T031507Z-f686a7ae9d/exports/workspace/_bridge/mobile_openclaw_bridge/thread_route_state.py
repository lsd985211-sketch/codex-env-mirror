"""Thread route selection and runtime-key helpers for the mobile bridge.

Owns: configured thread list lookup, per-user active thread runtime keys,
thread-selection waiting state, continuation keys, and basic delivery/runtime
key names used around dispatch.
Non-goals: dispatch execution, result recovery, retry timing policy, permission
decisions, or queue schema management.
State behavior: reads config dictionaries; explicit runtime helpers read or
write only their named runtime keys through the supplied queue object.
Normal callers: mobile_openclaw_cli worker, control-message handling, and route
regression checks.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def thread_items(config: dict[str, Any]) -> list[dict[str, str]]:
    items = config.get("threads", {}).get("items", [])
    result: list[dict[str, str]] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            thread_id_value = str(item.get("id") or item.get("project") or "").strip()
            thread_id = str(item.get("thread_id") or "").strip()
            if not thread_id_value or not thread_id:
                continue
            name = str(item.get("name") or item.get("project") or thread_id_value).strip()
            description = str(item.get("description") or item.get("title") or "").strip()
            aliases = item.get("aliases", [])
            alias_values = []
            if isinstance(aliases, list):
                alias_values = [str(alias).strip() for alias in aliases if str(alias).strip()]
            result.append(
                {
                    "id": thread_id_value,
                    "name": name,
                    "description": description,
                    "aliases": "\n".join(alias_values),
                    "thread_id": thread_id,
                }
            )
    legacy_thread_id = str(config.get("trigger", {}).get("codex_thread_id") or "").strip()
    if legacy_thread_id and not result:
        result.append(
            {
                "id": str(config.get("threads", {}).get("default_id") or config.get("threads", {}).get("default_project") or "mobile-openclaw-bridge"),
                "name": "微信桥接项目",
                "description": "微信 OpenClaw 桥接、手机端触发与回发",
                "aliases": "mobile\n微信桥接\n手机桥接\nmobile-openclaw-bridge",
                "thread_id": legacy_thread_id,
            }
        )
    return result


def default_thread_id(config: dict[str, Any]) -> str:
    configured = str(config.get("threads", {}).get("default_id") or config.get("threads", {}).get("default_project") or "").strip()
    if configured:
        return configured
    items = thread_items(config)
    return items[0]["id"] if items else ""


def find_thread(config: dict[str, Any], selector: str) -> dict[str, str] | None:
    selector = (selector or "").strip()
    if not selector:
        return None
    items = thread_items(config)
    if selector.isdigit():
        index = int(selector)
        if 1 <= index <= len(items):
            return items[index - 1]
    selector_lower = selector.lower()
    for item in items:
        aliases = [alias.lower() for alias in item.get("aliases", "").splitlines() if alias.strip()]
        candidates = {item["id"].lower(), item["name"].lower(), *aliases}
        if selector_lower in candidates:
            return item
    return None


def find_thread_for_external_user(config: dict[str, Any], external_user: str) -> dict[str, str] | None:
    external_user = str(external_user or "").strip().lower()
    if not external_user:
        return None
    short_user = external_user.split("@", 1)[0]
    for item in thread_items(config):
        description = str(item.get("description") or "").lower()
        aliases = [alias.lower() for alias in item.get("aliases", "").splitlines() if alias.strip()]
        candidates = {
            str(item.get("id") or "").lower(),
            str(item.get("name") or "").lower(),
            description,
            *aliases,
        }
        if external_user in description or external_user in candidates:
            return item
        if short_user and any(short_user in candidate for candidate in candidates):
            return item
    return None


def active_thread_key(external_user: str) -> str:
    return f"user_active_thread:{external_user}"


def pending_thread_selection_key(external_user: str) -> str:
    return f"user_thread_selection:{external_user}"


def set_active_thread(queue: Any, external_user: str, thread_id_value: str) -> None:
    queue.runtime_set(active_thread_key(external_user), thread_id_value)


def get_active_thread(
    queue: Any,
    config: dict[str, Any],
    external_user: str,
    use_default: bool = True,
) -> dict[str, str] | None:
    thread_id_value = queue.runtime_get(active_thread_key(external_user))
    if not thread_id_value and use_default:
        thread_id_value = default_thread_id(config)
    found = find_thread(config, thread_id_value)
    if found:
        return found
    if use_default:
        return find_thread(config, default_thread_id(config))
    return None


def selection_ttl_seconds(config: dict[str, Any]) -> int:
    return int(config.get("threads", {}).get("selection_ttl_seconds") or 180)


def mark_waiting_thread_selection(queue: Any, config: dict[str, Any], external_user: str) -> None:
    expires = (datetime.now(timezone.utc) + timedelta(seconds=selection_ttl_seconds(config))).isoformat()
    queue.runtime_set(
        pending_thread_selection_key(external_user),
        json.dumps({"expires_at": expires}, ensure_ascii=False),
    )


def clear_waiting_thread_selection(queue: Any, external_user: str) -> None:
    queue.runtime_delete(pending_thread_selection_key(external_user))


def is_waiting_thread_selection(queue: Any, external_user: str) -> bool:
    raw = queue.runtime_get(pending_thread_selection_key(external_user))
    if not raw:
        return False
    try:
        data = json.loads(raw)
        expires_at = datetime.fromisoformat(str(data.get("expires_at") or ""))
    except Exception:
        queue.runtime_delete(pending_thread_selection_key(external_user))
        return False
    if datetime.now(timezone.utc) > expires_at:
        queue.runtime_delete(pending_thread_selection_key(external_user))
        return False
    return True


def thread_menu_text(config: dict[str, Any], current_thread_id_value: str = "") -> str:
    items = thread_items(config)
    if not items:
        return "当前没有可选择的 Codex 项目线程。"
    lines = ["请选择要切换的对话线程："]
    for index, item in enumerate(items, start=1):
        suffix = " [当前]" if current_thread_id_value and item["id"] == current_thread_id_value else ""
        description = f" - {item['description']}" if item.get("description") else ""
        lines.append(f"{index}. {item['name']}{description}{suffix}")
    lines.append("请回复序号、项目名或别名。")
    return "\n".join(lines)


def thread_switch_trigger(text: str) -> bool:
    normalized = " ".join((text or "").strip().split()).lower()
    return normalized in {"切换线程", "选择线程", "线程列表", "threads", "thread"}


def continuation_window_seconds(config: dict[str, Any]) -> int:
    return int(config.get("trigger", {}).get("continuation_window_seconds") or 60)


def continuation_key(external_user: str, thread_project: str) -> str:
    return f"conversation_window:{external_user}:{thread_project}"


def delivery_retry_key(task_id: str) -> str:
    return f"delivery_retry:{task_id}"


def weixin_send_circuit_key(account_id: str) -> str:
    return f"weixin_send_circuit:{str(account_id or '').strip() or 'default'}"


def weixin_status_ack_circuit_key(account_id: str) -> str:
    return f"weixin_status_ack_circuit:{str(account_id or '').strip() or 'default'}"


def thread_recovery_key(task_id: str) -> str:
    return f"thread_recovery:{task_id}"


def thread_prewarm_key(thread_id: str) -> str:
    safe_thread_id = str(thread_id or "").strip()
    return f"thread_prewarm:{safe_thread_id}"


def cdp_start_probe_key() -> str:
    return "codex_cdp_start_probe"
