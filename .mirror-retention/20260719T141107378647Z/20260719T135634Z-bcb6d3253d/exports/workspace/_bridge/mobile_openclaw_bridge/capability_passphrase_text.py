"""Pure text helpers for temporary capability-token passphrases.

Owns parsing, candidate extraction, direct-reply detection, grant matching,
and redaction for passphrase text. It does not read or write queue state,
grant stores, files, or Weixin messages.

Normal caller: `mobile_openclaw_cli.py` capability-token dispatch facade.
"""

from __future__ import annotations

import re
from typing import Any, Callable


CAPABILITY_PASSPHRASE_RE = re.compile(
    r"(?im)(?:^|[\s,，;；])(?:令牌|口令|授权码|capability[-_ ]?token|token|passphrase)\s*[:：=]\s*(?P<value>[^\s,，;；]+)"
)

CAPABILITY_PASSPHRASE_CANCEL_WORDS = {
    "cancel",
    "取消",
    "取消授权",
    "取消这条",
    "取消请求",
    "不用了",
}

CAPABILITY_PASSPHRASE_NEUTRAL_WORDS = {
    "ok",
    "okay",
    "yes",
    "no",
    "wait",
    "hello",
    "hi",
    "好",
    "好的",
    "收到",
    "等等",
    "等下",
    "继续",
}

CAPABILITY_PASSPHRASE_REDACTION = "[capability-token-redacted]"


def extract_capability_passphrase(text: str) -> str:
    match = CAPABILITY_PASSPHRASE_RE.search(str(text or ""))
    return str(match.group("value") if match else "").strip()


def capability_passphrase_candidates(text: str) -> list[str]:
    explicit = extract_capability_passphrase(text)
    if explicit:
        return [explicit]
    candidates: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]{3,64})(?![A-Za-z0-9_.-])", str(text or "")):
        value = str(match.group(1) or "").strip()
        lowered = value.lower()
        if lowered in {"http", "https", "token", "passphrase", "capability"}:
            continue
        if value not in candidates:
            candidates.append(value)
        if len(candidates) >= 12:
            break
    return candidates


def is_capability_passphrase_cancel(text: str) -> bool:
    return str(text or "").strip().lower() in {item.lower() for item in CAPABILITY_PASSPHRASE_CANCEL_WORDS}


def is_direct_capability_passphrase_reply(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if extract_capability_passphrase(raw):
        return True
    if len(raw.split()) != 1:
        return False
    lowered = raw.lower()
    if lowered in {item.lower() for item in CAPABILITY_PASSPHRASE_NEUTRAL_WORDS}:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", raw))


def resolve_capability_passphrase(
    text: str,
    grants: list[dict[str, Any]],
    *,
    passphrase_required: Callable[[dict[str, Any]], bool],
    verify_passphrase: Callable[[dict[str, Any], str], dict[str, Any]],
) -> str:
    if not any(passphrase_required(item) for item in grants):
        return ""
    for candidate in capability_passphrase_candidates(text):
        if all(
            not passphrase_required(item)
            or verify_passphrase(item, candidate).get("ok")
            for item in grants
        ):
            return candidate
    return extract_capability_passphrase(text)


def redact_capability_passphrase(text: str) -> str:
    return CAPABILITY_PASSPHRASE_RE.sub(f" {CAPABILITY_PASSPHRASE_REDACTION}", str(text or "")).strip()


def redact_capability_passphrase_value(text: str, passphrase: str) -> str:
    redacted = redact_capability_passphrase(text)
    secret = str(passphrase or "").strip()
    if not secret:
        return redacted
    return re.sub(
        rf"(?<![A-Za-z0-9_.-]){re.escape(secret)}(?![A-Za-z0-9_.-])",
        CAPABILITY_PASSPHRASE_REDACTION,
        redacted,
    ).strip()
