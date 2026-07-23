"""OpenClaw Weixin account file helpers.

Owns: locating OpenClaw account files, reading bound users/context tokens, and
building small account maps for bridge callers.
Non-goals: permission decisions, queue mutation, reply sending, or repair.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
BRIDGE_ROOT = PROJECT_ROOT / "_bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from shared.windows_runtime_assets import openclaw_state_path  # noqa: E402

FIXED_ACCOUNT_SLOTS = ("primary", "backup1", "backup2")
ACCOUNT_FILE_SUFFIXES = (".context-tokens.json", ".sync.json")


def openclaw_state_dir(config: dict[str, Any]) -> Path:
    return Path(
        config.get("openclaw", {}).get("state_dir")
        or openclaw_state_path()
    )


def openclaw_accounts_dir(config: dict[str, Any]) -> Path:
    return openclaw_state_dir(config) / "openclaw-weixin" / "accounts"


def openclaw_accounts_index(config: dict[str, Any]) -> list[str]:
    path = openclaw_state_dir(config) / "openclaw-weixin" / "accounts.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def openclaw_account_file_ids(config: dict[str, Any]) -> list[str]:
    accounts_dir = openclaw_accounts_dir(config)
    if not accounts_dir.exists():
        return []
    result: list[str] = []
    for path in sorted(accounts_dir.glob("*.json")):
        name = path.name
        if any(name.endswith(suffix) for suffix in ACCOUNT_FILE_SUFFIXES):
            continue
        account_id = path.stem.strip()
        if account_id:
            result.append(account_id)
    return result


def read_openclaw_account(config: dict[str, Any], account_id: str) -> dict[str, Any]:
    if not account_id:
        return {}
    path = openclaw_accounts_dir(config) / f"{account_id}.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def openclaw_account_user_id(config: dict[str, Any], account_id: str) -> str:
    return str(read_openclaw_account(config, account_id).get("userId") or "").strip()


def read_openclaw_context_tokens(config: dict[str, Any], account_id: str) -> dict[str, Any]:
    if not account_id:
        return {}
    path = openclaw_accounts_dir(config) / f"{account_id}.context-tokens.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def openclaw_context_token_for_user(config: dict[str, Any], account_id: str, external_user: str) -> str:
    if not account_id or not external_user:
        return ""
    tokens = read_openclaw_context_tokens(config, account_id)
    return str(tokens.get(external_user) or "").strip()


def openclaw_account_has_context_for_user(config: dict[str, Any], account_id: str, external_user: str) -> bool:
    if not account_id or not external_user:
        return False
    return bool(openclaw_context_token_for_user(config, account_id, external_user))


def configured_openclaw_account_ids(config: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    configured = str(config.get("openclaw", {}).get("account_id") or "").strip()
    for account_id in [configured, *FIXED_ACCOUNT_SLOTS, *openclaw_accounts_index(config), *openclaw_account_file_ids(config)]:
        if account_id and account_id not in seen:
            seen.add(account_id)
            result.append(account_id)
    return result


def bound_weixin_users(config: dict[str, Any]) -> dict[str, str]:
    users: dict[str, str] = {}
    for account_id in configured_openclaw_account_ids(config):
        account = read_openclaw_account(config, account_id)
        user_id = str(account.get("userId") or "").strip()
        token = str(account.get("token") or "").strip()
        if not token:
            continue
        if user_id:
            users[user_id] = account_id
    return users


def permission_account_map(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    accounts: dict[str, dict[str, str]] = {}
    for account_id in configured_openclaw_account_ids(config):
        account = read_openclaw_account(config, account_id)
        user_id = str(account.get("userId") or "").strip()
        token = str(account.get("token") or "").strip()
        accounts[account_id] = {"user_id": user_id, "token_present": "yes" if token else "no"}
    inline_accounts = config.get("openclaw_accounts") if isinstance(config.get("openclaw_accounts"), dict) else {}
    for account_id, payload in inline_accounts.items():
        if not isinstance(payload, dict):
            continue
        user_id = str(payload.get("userId") or payload.get("user_id") or "").strip()
        token = str(payload.get("token") or "").strip()
        existing = accounts.get(str(account_id)) or {}
        if existing.get("user_id") and existing.get("token_present") == "yes":
            continue
        accounts[str(account_id)] = {"user_id": user_id, "token_present": "yes" if token else "no"}
    return accounts


def enrich_allowed_users_from_openclaw_accounts(config: dict[str, Any]) -> None:
    """Allow currently bound OpenClaw Weixin users without hard-coding every userId."""
    security = config.setdefault("security", {})
    existing = security.get("allowed_users", [])
    allowed = [str(user).strip() for user in existing if str(user).strip()] if isinstance(existing, list) else []
    for user_id in bound_weixin_users(config):
        if user_id not in allowed:
            allowed.append(user_id)
    security["allowed_users"] = allowed


def account_id_for_weixin_user(config: dict[str, Any], external_user: str) -> str:
    """Resolve the OpenClaw account/slot that can reply to a Weixin user."""
    external_user = str(external_user or "").strip()
    if not external_user:
        return ""
    for account_id in configured_openclaw_account_ids(config):
        if openclaw_account_has_context_for_user(config, account_id, external_user):
            return account_id
    return bound_weixin_users(config).get(external_user, "")


def is_real_weixin_user(external_user: str) -> bool:
    value = str(external_user or "").strip()
    return bool(value and value not in {"unknown", "unknown@im.wechat"} and value.endswith("@im.wechat"))


def is_openclaw_bound_user(config: dict[str, Any], external_user: str) -> bool:
    return bool(is_real_weixin_user(external_user) and account_id_for_weixin_user(config, external_user))


def receiver_account_id(config: dict[str, Any], explicit: str = "", external_user: str = "") -> str:
    explicit = str(explicit or "").strip()
    user_bound = account_id_for_weixin_user(config, external_user)
    if user_bound:
        return user_bound
    return str(explicit or config.get("openclaw", {}).get("account_id") or "primary").strip()
