#!/usr/bin/env python3
"""Pure evidence parsing for the scoped-authorization owner.

Ownership: token verifier material, direct-user token extraction, and a small
allowlist of attenuation constraints.  Non-goals: persistence, rollout I/O,
grant issuance, or business-action interpretation.  State behavior: pure.
Caller context: ``scoped_authorization`` calls this while holding its runtime
lock after it has established that a message belongs to the exact thread.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import Any


TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])AUTHORIZE-([A-Za-z0-9_-]+)(?![A-Za-z0-9_-])")
UNTRUSTED_MARKERS = ("<codex_delegation",)
_DELEGATION_BLOCK_RE = re.compile(r"<codex_delegation\b[^>]*>.*?</codex_delegation\s*>", re.IGNORECASE | re.DOTALL)
_ANNOTATION_BLOCK_RE = re.compile(r"<response-annotations\s*>(?P<body>.*?)</response-annotations\s*>", re.IGNORECASE | re.DOTALL)
DIRECT_APPROVAL_RE = re.compile(
    r"^\s*(?:"
    r"批准(?:继续|执行|实施)?|同意(?:继续|执行|实施)?|确认(?:继续|执行|实施)?|"
    r"(?:你)?继续(?:执行|实施|(?:你的)?(?:任务|工作))?(?:\s*[，,]\s*(?:我)?授权(?:你)?(?:继续|执行|实施|处理|完成)?(?:你的)?(?:任务|工作)?)?|"
    r"(?:我)?授权(?:你)?(?:继续|执行|实施|处理|完成)?(?:你的)?(?:任务|工作)?|approve(?:d)?"
    r")(?=$|[\s,，。；;:：]|[\u4e00-\u9fff])",
    re.IGNORECASE,
)
DIRECT_TASK_RE = re.compile(r"(?:修复|解决|完成|执行|处理|实现|构建|更改|删除|安装|启动|重载|发布|改进|完善|优化|fix|resolve|complete|execute|implement|build|delete|install|start|reload|publish|improve|enhance|optimi[sz]e)", re.IGNORECASE)
BROAD_APPROVAL_NOTE_RE = re.compile(r"(?:所有|全部|任何|一切|everything|\ball\b|\bany\b)", re.IGNORECASE)
CONSTRAINT_MARKERS = ("不要", "最多", "仅", "只", "max", "only", "without")
SCOPE_NEUTRAL_EFFICIENCY_RE = re.compile(
    r"(?:不要|别|无需)\s*(?:浪费(?:时间|成本)|重复(?:授权|确认)?|再(?:次)?(?:授权|确认)?)",
    re.IGNORECASE,
)
EXPANSION_MARKERS = ("增加", "扩大", "解除", "额外", "包括发布", "increase", "expand", "remove prohibition", "also authorize")
ATTACHMENT_CONTEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?P<key>owner|executor|action|phase)\s*[:=]\s*(?P<value>[A-Za-z0-9._:/-]+)",
    re.IGNORECASE,
)


def user_authored_authorization_text(text: str) -> dict[str, Any]:
    """Project user comments while discarding embedded third-party content.

    A user event establishes who submitted the message, but a rendered message
    may contain an untrusted delegated payload or an annotation card that
    quotes an assistant response.  Delegation bodies never authorize.  For an
    annotation card, only the user-authored ``annotation`` values are retained;
    the selected ``text`` is always discarded.  Malformed wrappers fail closed.
    """

    raw = str(text or "")
    without_delegation = _DELEGATION_BLOCK_RE.sub("", raw)
    if "<codex_delegation" in without_delegation.lower():
        return {"ok": False, "reason": "authorization_response_untrusted_wrapper"}
    annotations: list[str] = []
    malformed = False
    had_delegation = without_delegation != raw
    had_annotation = False

    def replace_annotation(match: re.Match[str]) -> str:
        nonlocal had_annotation, malformed
        had_annotation = True
        try:
            payload = json.loads(match.group("body").strip())
        except json.JSONDecodeError:
            malformed = True
            return ""
        if not isinstance(payload, list):
            malformed = True
            return ""
        for row in payload:
            if not isinstance(row, dict) or not isinstance(row.get("annotation"), str):
                malformed = True
                continue
            annotations.append(row["annotation"].strip())
        return ""

    remainder = _ANNOTATION_BLOCK_RE.sub(replace_annotation, without_delegation)
    if malformed or "<response-annotations" in remainder.lower():
        return {"ok": False, "reason": "authorization_response_untrusted_wrapper"}
    if (had_delegation or had_annotation) and not annotations and not remainder.strip():
        return {"ok": False, "reason": "authorization_response_untrusted_wrapper"}
    return {"ok": True, "text": "\n".join([*annotations, remainder.strip()]).strip()}


def token_material() -> tuple[str, str, str]:
    """Create a one-time bearer and the only material safe to persist."""

    token = f"AUTHORIZE-{secrets.token_urlsafe(24)}"
    salt = secrets.token_urlsafe(24)
    return token, salt, token_hash(token, salt)


def token_hash(token: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}\0{token}".encode("utf-8")).hexdigest()


def _normal_note(text: str, token: str) -> str:
    note = text.replace(token, " ")
    note = re.sub(r"[\s,，。；;:：`]+", " ", note).strip()
    note = re.sub(r"^(批准|同意|执行|approve|approved)\b", "", note, flags=re.IGNORECASE).strip()
    return note[:500]


def _budget_limits(note: str) -> dict[str, float]:
    values: dict[str, float] = {}
    if not re.search(r"最多|max(?:imum)?", note, re.IGNORECASE):
        return values
    money = re.search(r"(?:¥|￥|CNY\s*)(\d+(?:\.\d+)?)", note, re.IGNORECASE)
    if money:
        values["money_cny"] = float(money.group(1))
    minutes = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|分|min(?:ute)?s?)", note, re.IGNORECASE)
    if minutes:
        values["wall_seconds"] = float(minutes.group(1)) * 60
    gib = re.search(r"(\d+(?:\.\d+)?)\s*(?:GiB|GB)", note, re.IGNORECASE)
    if gib:
        values["network_bytes"] = float(gib.group(1)) * (1024**3)
    return values


def _attachment_context(note: str) -> dict[str, Any]:
    """Extract non-authorizing owner hints for automatic caller routing.

    These fields can only narrow or identify the already-bound challenge
    scope. They never create a new owner, action, phase, or executor grant.
    """

    context: dict[str, str] = {}
    for match in ATTACHMENT_CONTEXT_RE.finditer(str(note or "")):
        key = match.group("key").lower()
        value = match.group("value").strip()
        if key in context and context[key] != value:
            return {"ok": False, "reason": "authorization_attachment_context_ambiguous"}
        context[key] = value
    return {"ok": True, "context": context, "recognized": bool(context)}


def parse_attenuation(note: str) -> dict[str, Any]:
    """Parse only explicitly supported scope reductions; never guess intent."""

    raw = str(note or "").strip()
    normalized = raw.lower()
    if any(marker.lower() in normalized for marker in EXPANSION_MARKERS):
        return {"ok": False, "reason": "authorization_attenuation_expansion_or_ambiguous"}
    prohibitions: set[str] = set()
    mirror_restricted = "不要发布镜像" in raw or "no mirror" in normalized or "without mirror" in normalized
    if mirror_restricted:
        prohibitions.update({"mirror.refresh", "mirror.publish"})
    if not mirror_restricted and ("不要发布" in raw or "no publish" in normalized or "without publish" in normalized):
        prohibitions.add("publish")
    if "不要删除" in raw or "no delete" in normalized or "without delete" in normalized:
        prohibitions.add("delete")
    if "不要 release" in normalized or "no release" in normalized:
        prohibitions.add("release")
    budgets = _budget_limits(raw)
    attachment = _attachment_context(raw)
    if not attachment.get("ok"):
        return attachment
    context = attachment.get("context") if isinstance(attachment.get("context"), dict) else {}
    recognized = bool(prohibitions or budgets or context)
    if (
        raw
        and any(marker.lower() in normalized for marker in CONSTRAINT_MARKERS)
        and not recognized
        and not SCOPE_NEUTRAL_EFFICIENCY_RE.search(raw)
    ):
        return {"ok": False, "reason": "authorization_attenuation_needs_input"}
    return {
        "ok": True,
        "note": raw,
        "prohibitions": sorted(prohibitions),
        "budget_limits": budgets,
        "context": context,
        "recognized": recognized,
    }


def extract_direct_user_evidence(text: str, *, salt: str, expected_hash: str) -> dict[str, Any]:
    """Validate one complete token and return only canonical attenuation data."""

    projected = user_authored_authorization_text(text)
    if not projected.get("ok"):
        return projected
    raw = str(projected.get("text") or "")
    if any(marker in raw.lower() for marker in UNTRUSTED_MARKERS):
        return {"ok": False, "reason": "authorization_response_untrusted_wrapper"}
    tokens = [match.group(0) for match in TOKEN_RE.finditer(raw)]
    if len(tokens) != 1:
        return {
            "ok": False,
            "reason": "authorization_response_multiple_tokens" if len(tokens) > 1 else "authorization_response_token_not_found",
        }
    token = tokens[0]
    if any(line.lstrip().startswith(">") and token in line for line in raw.splitlines()):
        return {"ok": False, "reason": "authorization_response_quoted_token"}
    if not hmac.compare_digest(token_hash(token, salt), str(expected_hash or "")):
        return {"ok": False, "reason": "authorization_response_token_not_for_challenge"}
    attenuation = parse_attenuation(_normal_note(raw, token))
    if not attenuation.get("ok"):
        return attenuation
    return {"ok": True, "attenuation": attenuation}


def extract_direct_approval_evidence(text: str) -> dict[str, Any]:
    """Accept an explicit direct approval as challenge-bound, non-bearer evidence.

    The caller has already bound this message to one pending challenge by thread,
    time, and scope. This parser only decides whether the user actually approved
    it and whether any attached text narrows the existing scope.
    """

    projected = user_authored_authorization_text(text)
    if not projected.get("ok"):
        return projected
    raw = str(projected.get("text") or "")
    if any(marker in raw.lower() for marker in UNTRUSTED_MARKERS):
        return {"ok": False, "reason": "authorization_response_untrusted_wrapper"}
    if TOKEN_RE.search(raw):
        return {"ok": False, "reason": "authorization_direct_approval_contains_token"}
    match = DIRECT_APPROVAL_RE.match(raw)
    if not match:
        return {"ok": False, "reason": "authorization_direct_approval_not_found"}
    note = raw[match.end():].strip(" \t\r\n,，。；;:：`")[:500]
    attenuation = parse_attenuation(note)
    if not attenuation.get("ok"):
        return attenuation
    bounded_task_note = bool(DIRECT_TASK_RE.search(note)) and not BROAD_APPROVAL_NOTE_RE.search(note)
    if (
        note
        and not attenuation.get("recognized")
        and not SCOPE_NEUTRAL_EFFICIENCY_RE.search(note)
        and not bounded_task_note
    ):
        return {"ok": False, "reason": "authorization_direct_approval_ambiguous"}
    return {"ok": True, "attenuation": attenuation, "binding_mode": "direct_user_approval"}


def extract_direct_task_evidence(text: str) -> dict[str, Any]:
    """Accept one direct execution request only when a challenge pre-binds it."""

    projected = user_authored_authorization_text(text)
    if not projected.get("ok"):
        return projected
    raw = str(projected.get("text") or "")
    if any(marker in raw.lower() for marker in UNTRUSTED_MARKERS):
        return {"ok": False, "reason": "authorization_response_untrusted_wrapper"}
    if TOKEN_RE.search(raw) or not DIRECT_TASK_RE.search(raw):
        return {"ok": False, "reason": "authorization_direct_task_not_found"}
    return {"ok": True, "attenuation": {"ok": True, "note": "", "prohibitions": [], "budget_limits": {}, "context": {}, "recognized": False}, "binding_mode": "pre_challenge_direct_task"}


def attenuation_allows_scope(attenuation: dict[str, Any] | None, scope: dict[str, Any]) -> dict[str, Any]:
    """Check a caller's requested action and supplied budgets against a reduction."""

    value = attenuation if isinstance(attenuation, dict) else {}
    action = str((scope or {}).get("action") or "").lower()
    for prohibition in value.get("prohibitions") or []:
        if str(prohibition).lower() in action:
            return {"ok": False, "reason": "authorization_attenuation_prohibition", "prohibition": prohibition}
    requested_budget = (scope or {}).get("budget") if isinstance((scope or {}).get("budget"), dict) else {}
    for dimension, limit in (value.get("budget_limits") or {}).items():
        if dimension in requested_budget and float(requested_budget[dimension]) > float(limit):
            return {"ok": False, "reason": "authorization_attenuation_budget_exceeded", "dimension": dimension}
    context = value.get("context") if isinstance(value.get("context"), dict) else {}
    expected_owner = str((scope or {}).get("requested_by_owner") or "")
    if context.get("owner") and expected_owner and str(context["owner"]) != expected_owner:
        return {"ok": False, "reason": "authorization_attachment_owner_mismatch"}
    expected_action = str((scope or {}).get("action") or "")
    if context.get("action") and expected_action and str(context["action"]) != expected_action:
        return {"ok": False, "reason": "authorization_attachment_action_mismatch"}
    expected_phase = str((scope or {}).get("phase") or "")
    if context.get("phase") and expected_phase and str(context["phase"]) != expected_phase:
        return {"ok": False, "reason": "authorization_attachment_phase_mismatch"}
    allowed_executors = {str(item) for item in (scope or {}).get("allowed_executors") or [] if str(item).strip()}
    if context.get("executor") and allowed_executors and str(context["executor"]) not in allowed_executors:
        return {"ok": False, "reason": "authorization_attachment_executor_mismatch"}
    return {"ok": True}
