#!/usr/bin/env python3
"""Shared permission policy for the OpenClaw Weixin bridge."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

import capability_tokens


ROOT = Path(__file__).resolve().parent
PERMISSION_TABLE_PATH = ROOT / "permission_table.json"
DEFAULT_TABLE_REF = "mobile-weixin-bridge/permission_table:v1"

ACTION_ALIASES = {
    "status": "status_global",
    "repair": "repair_system",
    "control_stop": "stop",
    "control_resume": "resume",
}


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    action: str
    role: str
    reason: str
    capabilities: tuple[str, ...]
    actor: str = ""
    account_id: str = ""
    implicit_admin_allow: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "role": self.role,
            "reason": self.reason,
            "capabilities": list(self.capabilities),
            "actor": self.actor,
            "account_id": self.account_id,
            "implicit_admin_allow": self.implicit_admin_allow,
        }


@dataclass(frozen=True)
class AskScopeDecision:
    allowed: bool
    scope: str
    reason: str
    required_actions: tuple[str, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "scope": self.scope,
            "reason": self.reason,
            "required_actions": list(self.required_actions),
        }


LOCAL_READ_PATTERNS = (
    r"读取.{0,12}(本机|电脑|本地|桌面|数据库|日志|配置|资源库|进程|性能|系统)",
    r"(打开|查看|扫描|搜索|列出|导出|发送|发给我).{0,12}(本机|电脑|本地|桌面|数据库|日志|配置|资源库)",
    r"(read|open|scan|search|list|dump|export|send).{0,24}(local|desktop|file|folder|directory|database|db|log|config|workspace)",
)

LOCAL_WRITE_PATTERNS = (
    r"(修改|写入|覆盖|移动|重命名|创建|保存|清理|删除|卸载|安装|修复|停止|重启|关闭).{0,16}(本机|电脑|本地|桌面|数据|文件|目录|文件夹|数据库|日志|配置|进程|服务|系统)",
    r"(delete|remove|modify|write|overwrite|move|rename|create|save|clean|install|uninstall|repair|restart|stop|kill).{0,24}(local|desktop|file|folder|directory|database|db|log|config|process|service|system)",
)

SECRET_PATTERNS = (
    r"(token|cookie|secret|password|passwd|授权码|密钥|私钥|凭据|密码|上下文.?token|confirmation.?secret)",
)

GENERATED_ARTIFACT_PATTERNS = (
    r"(生成|创建|制作|整理|导出).{0,16}(文件|附件|文档|表格|报告|pdf|docx|xlsx).{0,24}(发送|发给|回发|传给|给我|过去)",
    r"(发送|发给|回发|传给).{0,12}(生成物|新生成.{0,6}文件|刚生成.{0,6}文件|生成.{0,6}附件)",
    r"(generate|create|make|draft|export).{0,24}(file|attachment|document|report|pdf|docx|xlsx).{0,24}(send|reply|return|attach)",
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_ask_scope(text: str) -> AskScopeDecision:
    """Classify the safe subset that /ask may cover by default.

    The classifier is intentionally conservative. It catches obvious local data
    access or local side-effect requests before they are sent to Codex. Ambiguous
    content is left to the prompt-level permission table, but never upgrades
    the actor's capabilities.
    """
    value = str(text or "").strip()
    if not value:
        return AskScopeDecision(True, "non_sensitive_question", "empty ask text has no local-data requirement")
    if _matches_any(value, SECRET_PATTERNS):
        return AskScopeDecision(False, "secret_access", "ask cannot disclose secrets or credentials", ("secret_read",))
    if _matches_any(value, GENERATED_ARTIFACT_PATTERNS):
        return AskScopeDecision(
            False,
            "generated_artifact_create_or_send",
            "ask may create and send generated artifacts only with an active admin-granted capability token scoped to the account attachment area",
            ("generated_file_create", "generated_file_send"),
        )
    if _matches_any(value, LOCAL_WRITE_PATTERNS):
        return AskScopeDecision(
            False,
            "local_data_write_or_side_effect",
            "ask cannot modify, delete, move, overwrite, clean, install, stop, restart, or otherwise change local machine state",
            ("local_data_write",),
        )
    if _matches_any(value, LOCAL_READ_PATTERNS):
        actions = ["local_data_read"]
        lowered = value.lower()
        if any(term in value for term in ("文件", "目录", "文件夹", "桌面")) or any(term in lowered for term in ("file", "folder", "directory", "desktop", "workspace")):
            actions.append("workspace_file_read")
        if any(term in value for term in ("数据库", "日志")) or any(term in lowered for term in ("database", "db", "log")):
            actions.append("bridge_db_read")
        if any(term in value for term in ("进程", "性能", "系统")) or any(term in lowered for term in ("process", "performance", "system")):
            actions.append("system_diagnostics_read")
        if "资源库" in value or "resource" in lowered:
            actions.append("resource_library_read")
        if any(term in value for term in ("导出", "发送", "发给我")) or any(term in lowered for term in ("export", "send", "dump")):
            actions.append("local_data_export")
        return AskScopeDecision(
            False,
            "local_data_read_or_export",
            "ask cannot read, summarize, copy, export, transmit, or disclose local-machine data",
            tuple(dict.fromkeys(actions)),
        )
    if re.search(r"https?://", value, flags=re.IGNORECASE):
        return AskScopeDecision(True, "external_resource", "ask may handle explicit external public resources within resource policy")
    return AskScopeDecision(True, "non_sensitive_or_user_provided_data", "ask whitelist covers non-sensitive questions and user-provided data")


def allowed_users(config: dict[str, Any]) -> list[str]:
    security = config.get("security") if isinstance(config.get("security"), dict) else {}
    values = security.get("allowed_users") if isinstance(security.get("allowed_users"), list) else []
    return [str(item).strip() for item in values if str(item).strip()]


def load_permission_table() -> dict[str, Any]:
    try:
        table = json.loads(PERMISSION_TABLE_PATH.read_text(encoding="utf-8-sig"))
        return table if isinstance(table, dict) else {}
    except Exception:
        return {}


def openclaw_accounts(config: dict[str, Any], account_map: dict[str, dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    if account_map is not None:
        return {
            str(account_id): {
                "user_id": str(payload.get("user_id") or payload.get("userId") or "").strip(),
                "token_present": str(payload.get("token_present") or "no"),
            }
            for account_id, payload in account_map.items()
            if isinstance(payload, dict)
        }
    accounts_config = config.get("openclaw_accounts") if isinstance(config.get("openclaw_accounts"), dict) else {}
    accounts: dict[str, dict[str, str]] = {}
    for account_id, payload in accounts_config.items():
        if not isinstance(payload, dict):
            continue
        accounts[str(account_id)] = {
            "user_id": str(payload.get("userId") or payload.get("user_id") or "").strip(),
            "token_present": "yes" if str(payload.get("token") or "").strip() else "no",
        }
    return accounts


def bound_account_id(config: dict[str, Any], actor: str, account_map: dict[str, dict[str, str]] | None = None) -> str:
    actor = str(actor or "").strip()
    if not actor:
        return ""
    for account_id, payload in openclaw_accounts(config, account_map).items():
        if payload.get("user_id") == actor and payload.get("token_present") == "yes":
            return account_id
    return ""


def primary_admin_user(config: dict[str, Any], account_map: dict[str, dict[str, str]] | None = None) -> str:
    accounts = openclaw_accounts(config, account_map)
    primary = accounts.get("primary") or {}
    return str(primary.get("user_id") or "").strip()


def is_allowed_user(config: dict[str, Any], actor: str) -> bool:
    actor = str(actor or "").strip()
    allowed = allowed_users(config)
    return bool(actor and (not allowed or actor in allowed))


def role_for_actor(config: dict[str, Any], actor: str, account_map: dict[str, dict[str, str]] | None = None) -> str:
    if not is_allowed_user(config, actor):
        return "blocked"
    if actor and actor == primary_admin_user(config, account_map):
        return "admin"
    return "user"


def admin_superuser_enabled(table: dict[str, Any] | None = None) -> bool:
    table = table if isinstance(table, dict) else load_permission_table()
    return bool(table.get("admin_superuser", True))


def unknown_action_policy(table: dict[str, Any] | None = None) -> dict[str, str]:
    table = table if isinstance(table, dict) else load_permission_table()
    value = table.get("unknown_action_policy") if isinstance(table.get("unknown_action_policy"), dict) else {}
    return {
        "admin": str(value.get("admin") or "allow_audit"),
        "user": str(value.get("user") or "deny"),
        "blocked": str(value.get("blocked") or "deny"),
    }


def all_defined_actions(table: dict[str, Any] | None = None) -> tuple[str, ...]:
    table = table if isinstance(table, dict) else load_permission_table()
    rules = table.get("action_rules") if isinstance(table.get("action_rules"), dict) else {}
    return tuple(str(key) for key in rules.keys() if str(key))


def ask_policy_for_role(role: str, table: dict[str, Any] | None = None) -> dict[str, Any]:
    table = table if isinstance(table, dict) else load_permission_table()
    policy = table.get("ask_policy") if isinstance(table.get("ask_policy"), dict) else {}
    role_policy = policy.get(role) if isinstance(policy.get(role), dict) else {}
    if role_policy:
        return role_policy
    if role == "admin":
        return {"mode": "superuser", "unknown_or_sensitive_request_policy": "allow_with_audit_and_risk_controls"}
    return {
        "mode": "whitelist",
        "allowed_scopes": [
            "non_sensitive_question",
            "user_provided_data_processing",
            "explicit_external_resource",
        ],
        "denied_scopes": [],
    }


def capabilities_for_role(role: str) -> tuple[str, ...]:
    table = load_permission_table()
    if role == "admin" and admin_superuser_enabled(table):
        inherited = list(capabilities_for_role("user"))
        inherited.extend(all_defined_actions(table))
        return tuple(dict.fromkeys(inherited))
    profiles = table.get("profiles") if isinstance(table.get("profiles"), dict) else {}
    profile = profiles.get(role) if isinstance(profiles.get(role), dict) else {}
    actions: list[str] = []
    inherited = str(profile.get("inherits") or "")
    if inherited:
        actions.extend(capabilities_for_role(inherited))
    actions.extend(str(item) for item in (profile.get("actions") or []) if str(item))
    if actions:
        return tuple(dict.fromkeys(actions))
    if role == "user":
        return ("ask", "supplement", "thread_switch", "status_self")
    if role == "admin":
        return (
            "ask",
            "supplement",
            "thread_switch",
            "status_self",
            "status_global",
            "repair_system",
            "repair_bridge",
            "stop",
            "resume",
            "hardstop",
            "confirm_l3",
            "dashboard_send",
            "dashboard_send_to_weixin",
            "dashboard_retry",
            "dashboard_cancel",
            "set_secret_hash",
            "mode_change",
        )
    return tuple()


def temporary_capabilities_for_actor(
    config: dict[str, Any],
    actor: str,
    account_id: str = "",
    account_map: dict[str, dict[str, str]] | None = None,
) -> tuple[str, ...]:
    role = role_for_actor(config, actor, account_map)
    if role in {"blocked", "admin"}:
        return tuple()
    account = str(account_id or bound_account_id(config, actor, account_map)).strip()
    return capability_tokens.active_capabilities(account_id=account, actor=str(actor or ""))


def generated_artifact_dir_for_actor(
    config: dict[str, Any],
    actor: str,
    account_id: str = "",
    account_map: dict[str, dict[str, str]] | None = None,
) -> str:
    account = str(account_id or bound_account_id(config, actor, account_map)).strip()
    if not account:
        return ""
    caps = temporary_capabilities_for_actor(config, actor, account, account_map)
    if not any(cap in caps for cap in ("generated_file_create", "generated_file_send", "reply_with_generated_artifact")):
        return ""
    return str(capability_tokens.generated_artifact_dir(account))


def normalize_action(action: str) -> str:
    value = str(action or "").strip().lower()
    return ACTION_ALIASES.get(value, value)


def decide(
    config: dict[str, Any],
    actor: str,
    action: str,
    account_id: str = "",
    account_map: dict[str, dict[str, str]] | None = None,
) -> PermissionDecision:
    normalized = normalize_action(action)
    role = role_for_actor(config, actor, account_map)
    capabilities = capabilities_for_role(role)
    account = str(account_id or bound_account_id(config, actor, account_map)).strip()
    if role == "blocked":
        return PermissionDecision(
            False,
            normalized,
            role,
            "sender not in allowed_users",
            capabilities,
            str(actor or ""),
            account,
        )
    if normalized in capabilities:
        return PermissionDecision(True, normalized, role, "allowed", capabilities, str(actor or ""), account)
    temporary = temporary_capabilities_for_actor(config, actor, account, account_map)
    if normalized in temporary:
        combined = tuple(dict.fromkeys([*capabilities, *temporary]))
        return PermissionDecision(
            True,
            normalized,
            role,
            "allowed by active admin-granted temporary capability token",
            combined,
            str(actor or ""),
            account,
        )
    if role == "admin" and admin_superuser_enabled():
        return PermissionDecision(
            True,
            normalized,
            role,
            "admin superuser implicit allow for unspecified action",
            capabilities,
            str(actor or ""),
            account,
            True,
        )
    return PermissionDecision(
        False,
        normalized,
        role,
        f"role {role} lacks capability {normalized}",
        capabilities,
        str(actor or ""),
        account,
    )


def codex_context(
    config: dict[str, Any],
    actor: str,
    account_id: str = "",
    risk_level: str = "",
    account_map: dict[str, dict[str, str]] | None = None,
    include_temporary_capabilities: bool = False,
) -> dict[str, Any]:
    role = role_for_actor(config, actor, account_map)
    decision = decide(config, actor, "ask", account_id, account_map)
    high_risk = str(risk_level or "").strip().upper() == "L3"
    table = load_permission_table()
    policy = unknown_action_policy(table)
    ask_policy = ask_policy_for_role(role, table)
    temporary = list(temporary_capabilities_for_actor(config, actor, account_id, account_map)) if include_temporary_capabilities else []
    generated_dir = generated_artifact_dir_for_actor(config, actor, account_id, account_map) if include_temporary_capabilities else ""
    return {
        "auth": "verified" if decision.allowed else "blocked",
        "role": role,
        "permission_profile": role,
        "permission_table_ref": str(table.get("table_ref") or DEFAULT_TABLE_REF),
        "account_id": str(account_id or decision.account_id or ""),
        "allowed_for_ask": bool(decision.allowed),
        "allowed_actions": list(capabilities_for_role(role)),
        "admin_superuser": bool(role == "admin" and admin_superuser_enabled(table)),
        "unknown_action_policy": policy.get(role, "deny"),
        "ask_policy": ask_policy,
        "must_check_permission_table": True,
        "ordinary_user_must_refuse_missing_action": role != "admin",
        "admin_may_execute_unspecified_action_with_audit": bool(role == "admin" and admin_superuser_enabled(table)),
        "risk_level": str(risk_level or ""),
        "requires_confirmation_for_l3": high_risk,
        "temporary_capabilities": temporary,
        "generated_artifact_dir": generated_dir,
    }


def compact_codex_context(
    config: dict[str, Any],
    actor: str,
    account_id: str = "",
    risk_level: str = "",
    account_map: dict[str, dict[str, str]] | None = None,
    include_temporary_capabilities: bool = False,
) -> dict[str, Any]:
    """Return the minimal prompt-facing permission context."""
    role = role_for_actor(config, actor, account_map)
    decision = decide(config, actor, "ask", account_id, account_map)
    table = load_permission_table()
    temporary = list(temporary_capabilities_for_actor(config, actor, account_id, account_map)) if include_temporary_capabilities else []
    generated_dir = generated_artifact_dir_for_actor(config, actor, account_id, account_map) if include_temporary_capabilities else ""
    return {
        "schema": "mobile-permission-context-compact/v1",
        "auth": "verified" if decision.allowed else "blocked",
        "permission_profile": role,
        "permission_table_ref": str(table.get("table_ref") or DEFAULT_TABLE_REF),
        "account_id": str(account_id or decision.account_id or ""),
        "allowed_for_ask": bool(decision.allowed),
        "admin_superuser": bool(role == "admin" and admin_superuser_enabled(table)),
        "unknown_action_policy": unknown_action_policy(table).get(role, "deny"),
        "ordinary_user_must_refuse_missing_action": role != "admin",
        "risk_level": str(risk_level or ""),
        "requires_confirmation_for_l3": str(risk_level or "").strip().upper() == "L3",
        "temporary_capabilities": temporary,
        "generated_artifact_dir": generated_dir,
    }


def snapshot(config: dict[str, Any], account_map: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    allowed = allowed_users(config)
    accounts = openclaw_accounts(config, account_map)
    primary_user = primary_admin_user(config, account_map)
    table = load_permission_table()
    actors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for user in allowed:
        seen.add(user)
        account_id = bound_account_id(config, user, account_map)
        role = role_for_actor(config, user, account_map)
        actors.append(
            {
                "external_user": user,
                "account_id": account_id,
                "role": role,
                "capabilities": list(capabilities_for_role(role)),
                "temporary_capabilities": list(temporary_capabilities_for_actor(config, user, account_id, account_map)),
            }
        )
    for account_id, payload in accounts.items():
        user = str(payload.get("user_id") or "")
        if user and user not in seen:
            role = role_for_actor(config, user, account_map)
            actors.append(
                {
                    "external_user": user,
                    "account_id": account_id,
                    "role": role,
                    "capabilities": list(capabilities_for_role(role)),
                    "temporary_capabilities": list(temporary_capabilities_for_actor(config, user, account_id, account_map)),
                    "implicit_from_openclaw_account": True,
                }
            )
    issues: list[dict[str, Any]] = []
    if not primary_user:
        issues.append({"code": "primary_admin_unbound", "severity": "high", "summary": "No primary OpenClaw account user is available as bridge admin."})
    if primary_user and primary_user not in allowed and allowed:
        issues.append({"code": "primary_admin_not_allowed", "severity": "high", "summary": "Primary admin user is not present in allowed_users."})
    if not allowed:
        issues.append({"code": "allowlist_empty_allows_any_user", "severity": "high", "summary": "allowed_users is empty, so MobileQueue currently treats every sender as allowed."})
    return {
        "ok": not any(item.get("severity") == "high" for item in issues),
        "schema": str(table.get("schema") or "mobile-weixin-bridge-permission-table/v1"),
        "table_ref": str(table.get("table_ref") or DEFAULT_TABLE_REF),
        "table_path": str(PERMISSION_TABLE_PATH),
        "allowed_user_count": len(allowed),
        "primary_admin_user": primary_user,
        "actors": actors,
        "issues": issues,
        "deny_by_default_for_unknown_actions": unknown_action_policy(table).get("user") == "deny",
        "admin_superuser_enabled": admin_superuser_enabled(table),
        "unknown_action_policy": unknown_action_policy(table),
        "ask_is_whitelist_only": True,
        "ask_guard_applies_to_roles": list((table.get("ask_policy") or {}).get("obvious_denial_guard_applies_to_roles") or ["user", "blocked"]),
        "ask_allowed_scopes": list(ask_policy_for_role("user", table).get("allowed_scopes") or []),
        "ask_denied_scopes": list(ask_policy_for_role("user", table).get("denied_scopes") or []),
        "policy": table,
    }
