#!/usr/bin/env python3
"""Purpose-owned note analysis helpers for memory governance.

This module is intentionally read-only and text-focused. It classifies ad hoc
notes, detects sensitive/current-state language, and proposes destinations or
consolidation themes. It does not read note directories, write memory, archive
files, or update PMB state.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SENSITIVE_PATTERNS = (
    ("github_token_shape", "high", re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("openai_key_shape", "high", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    (
        "credential_value_shape",
        "medium",
        re.compile(
            r"(?i)\b("
            r"api[_-]?key|private[_-]?key|password|passwd|cookie|authorization"
            r")\b\s*[:=]\s*['\"]?[^'\"\s]{8,}"
            r"|authorization\s*:\s*bearer\s+[^'\"\s]{12,}"
        ),
    ),
    ("secret_policy_keyword", "low", re.compile(r"(?i)\b(token|secret|password|cookie|authorization|recovery code|验证码|授权码|口令)\b")),
)

DRIFT_HIGH_PATTERNS = (
    re.compile(r"(?i)\b(right now|current turn|post-reboot|active-turn|tool-surface|transport closed)\b"),
    re.compile(r"(当前 turn|当前工具|已重启|刚刚)"),
)

DRIFT_LOW_PATTERNS = (
    re.compile(r"(?i)\b(currently|current|now|now supports)\b"),
    re.compile(r"(当前状态|当前|现在|目前)"),
)

NOTE_DESTINATION_RULES = (
    ("tools.mcp.stability", ("mcp", "tool", "transport", "stdio", "codegraph", "工具")),
    ("system.maintenance.lessons", ("maintenance", "doctor", "repair", "validate", "baseline", "治理", "维护")),
    ("email.workflow", ("email", "mail", "smtp", "imap", "邮箱", "邮件")),
    ("workspace.mcsmanager.operational", ("bridge", "weixin", "openclaw", "queue", "owned-result", "回发", "桥接")),
    ("skills.index", ("skill", "技能")),
)

USER_PROFILE_DESTINATION_KEYWORDS = (
    "user preference",
    "user profile",
    "profile fact",
    "stable user",
    "用户偏好",
    "用户画像",
    "画像事实",
    "个人偏好",
)

EXTERNAL_KNOWLEDGE_MARKERS = (
    "external knowledge absorption candidate",
    "source_item_id: ek_",
    "trust_tier:",
    "freshness_class:",
)

CONSOLIDATION_THEME_RULES = (
    (
        "mobile_bridge_owned_result_recovery",
        "workspace.mcsmanager.operational",
        (
            "owned-result",
            "owned result",
            "mobile",
            "weixin",
            "openclaw",
            "回发",
            "仍在处理",
            "protocol_violation_no_owned_result",
            "inprogress",
            "waiting-followup",
        ),
    ),
    (
        "bridge_capability_tokens",
        "workspace.mcsmanager.operational",
        (
            "capability token",
            "generated_artifact",
            "generated file",
            "attachments/generated",
            "令牌",
            "口令",
            "生成文件",
        ),
    ),
    (
        "mcp_tool_layer_stability",
        "tools.mcp.stability",
        (
            "mcp",
            "transport closed",
            "tool-surface",
            "current-turn",
            "stdio",
            "codegraph",
            "filesystem",
            "custom-slash",
            "tool_available",
        ),
    ),
    (
        "email_scheduler_maintenance",
        "email.workflow",
        (
            "email",
            "mail",
            "smtp",
            "imap",
            "scheduler",
            "outbox",
            "inbox",
            "邮件",
            "邮箱",
        ),
    ),
    (
        "backup_record_governance",
        "system.maintenance.lessons",
        (
            "backup",
            "record-store",
            "record store",
            "archive",
            "备份",
            "归档",
            "执行记录",
        ),
    ),
    (
        "memory_system_governance",
        "system.maintenance.lessons",
        (
            "memory",
            "pmb",
            "ad_hoc",
            "note",
            "absorb",
            "profile",
            "记忆",
            "画像",
            "吸收",
        ),
    ),
    (
        "encoding_windows_shell_baseline",
        "system.maintenance.lessons",
        (
            "utf-8",
            "mojibake",
            "powershell",
            "encoding",
            "乱码",
            "编码",
        ),
    ),
    (
        "codex_cdp_delivery_route",
        "workspace.mcsmanager.operational",
        (
            "cdp",
            "app-server",
            "codex desktop",
            "remote-debugging-port",
            "visible",
        ),
    ),
)


def sensitive_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for code, severity, pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            hits.append({"code": code, "severity": severity})
    return hits


def highest_severity(hits: list[dict[str, str]]) -> str:
    order = {"high": 3, "medium": 2, "low": 1}
    if not hits:
        return ""
    return max((str(item.get("severity") or "low") for item in hits), key=lambda severity: order.get(severity, 0))


def drift_hits(text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    compact = " ".join(str(text or "").split())
    if re.search(r"(用户当前状态|用户.*当前状态|user profile|用户画像|画像)", compact, flags=re.IGNORECASE):
        if re.search(r"(用户当前状态|大一|学期|课程|user profile|用户画像|画像)", compact, flags=re.IGNORECASE):
            return [{"code": "temporal_profile_review", "severity": "low"}]
    if "[migrated legacy vector/chroma memory]" in compact and re.search(
        r"(?i)\b(now supports|currently|current verified|current core baselines|baseline|implemented|updated|verified|temporary write/search/delete tests)\b",
        compact,
    ):
        return [{"code": "implementation_history_current_language", "severity": "low"}]
    if re.search(r"(?i)\b(post-reboot validation|confirmed|verified)\b", compact) and re.search(r"\b20\d{2}-\d{2}-\d{2}\b", compact):
        return [{"code": "dated_validation_history", "severity": "low"}]
    for pattern in DRIFT_HIGH_PATTERNS:
        if pattern.search(text):
            hits.append({"code": "current_state_or_session_specific", "severity": "medium"})
            break
    if not hits:
        for pattern in DRIFT_LOW_PATTERNS:
            if pattern.search(text):
                hits.append({"code": "generic_current_language", "severity": "low"})
                break
    return hits


def recommend_note_destination(path: Path, text: str) -> dict[str, Any]:
    haystack = f"{path.name}\n{text}".lower()
    if any(marker in haystack for marker in EXTERNAL_KNOWLEDGE_MARKERS):
        if any(keyword in haystack for keyword in ("local-mcp:", "context7", "markitdown", "mcp", "tool")):
            return {
                "destination": "tools.mcp.stability",
                "confidence": "high",
                "candidates": [{"destination": "tools.mcp.stability", "score": 3, "matched": ["external_knowledge", "mcp"]}],
            }
        return {
            "destination": "system.maintenance.lessons",
            "confidence": "high",
            "candidates": [{"destination": "system.maintenance.lessons", "score": 3, "matched": ["external_knowledge"]}],
        }
    if any(keyword in haystack for keyword in USER_PROFILE_DESTINATION_KEYWORDS):
        return {
            "destination": "user_profile",
            "confidence": "high",
            "candidates": [{"destination": "user_profile", "score": 3, "matched": ["explicit_user_profile"]}],
        }
    scores: list[dict[str, Any]] = []
    for destination, keywords in NOTE_DESTINATION_RULES:
        matched = [keyword for keyword in keywords if keyword.lower() in haystack]
        if matched:
            scores.append({"destination": destination, "score": len(matched), "matched": matched})
    scores.sort(key=lambda item: (-int(item["score"]), str(item["destination"])))
    if not scores:
        return {"destination": "workspace.mcsmanager.operational", "confidence": "low", "candidates": []}
    return {
        "destination": scores[0]["destination"],
        "confidence": "high" if int(scores[0]["score"]) >= 2 else "medium",
        "candidates": scores[:4],
    }


def normalize_memory_text(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def stable_point_candidates(text: str, limit: int = 8) -> list[str]:
    points: list[str] = []
    stable_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            stable_section = bool(re.search(r"(?i)(stable points|stable facts|to review|稳定)", line))
            continue
        if not line.startswith(("- ", "* ")):
            continue
        if "##" in text and not stable_section:
            continue
        item = line.lstrip("-* ").strip()
        if not item or len(item) < 18:
            continue
        if re.search(r"(?i)^(source_item_id|url|trust_tier|freshness_class|namespace)\s*:", item):
            continue
        if re.search(r"(?i)\b(log|stdout|stderr|traceback|task_id|result code|raw)\b", item):
            continue
        if re.search(r"(?i)^(added|updated|rewrote|linked|source:|evidence:|validation:)\b", item):
            continue
        if re.search(r"^(新增|更新|改写|已验证|证据|来源|验证)[:：]", item):
            continue
        if len(item) < 28 and not re.search(r"(must|should|不得|必须|应该|不要|not|only|默认)", item, flags=re.IGNORECASE):
            continue
        points.append(item)
    if not points:
        for sentence in re.split(r"(?<=[。.!?])\s+", " ".join(text.split())):
            sentence = sentence.strip()
            if (
                40 <= len(sentence) <= 240
                and not re.search(r"(?i)\b(raw log|traceback|task_id|added|updated|rewrote|linked)\b", sentence)
                and re.search(r"(must|should|do not|cannot|only|默认|必须|应该|不得|不要|不能)", sentence, flags=re.IGNORECASE)
            ):
                points.append(sentence)
            if len(points) >= limit:
                break
    deduped: list[str] = []
    seen: set[str] = set()
    for point in points:
        compact = normalize_memory_text(point)
        if compact in seen:
            continue
        seen.add(compact)
        deduped.append(point[:260])
        if len(deduped) >= limit:
            break
    return deduped


def recommend_consolidation_theme(path: Path, text: str) -> dict[str, Any]:
    haystack = f"{path.name}\n{text}".lower()
    scored: list[dict[str, Any]] = []
    for theme_id, destination, keywords in CONSOLIDATION_THEME_RULES:
        matched = [keyword for keyword in keywords if keyword.lower() in haystack]
        if matched:
            scored.append(
                {
                    "theme_id": theme_id,
                    "destination": destination,
                    "score": len(matched),
                    "matched": matched[:8],
                }
            )
    scored.sort(key=lambda item: (-int(item["score"]), str(item["theme_id"])))
    if not scored:
        destination = recommend_note_destination(path, text)
        return {
            "theme_id": str(destination.get("destination") or "workspace.mcsmanager.operational").replace(".", "_"),
            "destination": destination.get("destination", "workspace.mcsmanager.operational"),
            "confidence": "low",
            "matched": [],
        }
    return {
        **scored[0],
        "confidence": "high" if int(scored[0]["score"]) >= 2 else "medium",
    }
