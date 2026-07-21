"""Pure classifiers for final Weixin reply delivery results.

Owns nested reply-result inspection, Weixin business ret/errcode extraction,
phone-visible and delivery-accepted checks, and retry category classification.
It does not send messages, read/write queue state, or expose context tokens.

Normal caller: `mobile_openclaw_cli.py` final-reply facade.
"""

from __future__ import annotations

from typing import Any


def weixin_business_ret(reply: dict[str, Any]) -> Any:
    if not isinstance(reply, dict):
        return None
    ret = reply.get("weixin_ret")
    if ret is not None:
        return ret
    ret = reply.get("weixinRet")
    if ret is not None:
        return ret
    stdout = reply.get("stdout")
    if isinstance(stdout, dict):
        ret = stdout.get("weixinRet")
        if ret is not None:
            return ret
        for attempt in stdout.get("attempts") or []:
            ret = weixin_business_ret(attempt)
            if ret is not None:
                return ret
    final = reply.get("final")
    if isinstance(final, dict):
        ret = weixin_business_ret(final)
        if ret is not None:
            return ret
    for attempt in reply.get("attempts") or []:
        ret = weixin_business_ret(attempt)
        if ret is not None:
            return ret
    return None


def weixin_business_errcode(reply: dict[str, Any]) -> Any:
    if not isinstance(reply, dict):
        return None
    errcode = reply.get("weixin_errcode")
    if errcode is not None:
        return errcode
    response = reply.get("response")
    if isinstance(response, dict) and response.get("errcode") is not None:
        return response.get("errcode")
    stdout = reply.get("stdout")
    if isinstance(stdout, dict):
        errcode = stdout.get("weixin_errcode")
        if errcode is not None:
            return errcode
        response = stdout.get("response")
        if isinstance(response, dict) and response.get("errcode") is not None:
            return response.get("errcode")
        for attempt in stdout.get("attempts") or []:
            errcode = weixin_business_errcode(attempt)
            if errcode is not None:
                return errcode
    final = reply.get("final")
    if isinstance(final, dict):
        errcode = weixin_business_errcode(final)
        if errcode is not None:
            return errcode
    for attempt in reply.get("attempts") or []:
        errcode = weixin_business_errcode(attempt)
        if errcode is not None:
            return errcode
    return None


def nested_reply_flag(reply: dict[str, Any], key: str) -> bool:
    if not isinstance(reply, dict):
        return False
    if bool(reply.get(key)):
        return True
    stdout = reply.get("stdout")
    if isinstance(stdout, dict) and nested_reply_flag(stdout, key):
        return True
    response = reply.get("response")
    if isinstance(response, dict) and nested_reply_flag(response, key):
        return True
    final = reply.get("final")
    if isinstance(final, dict) and nested_reply_flag(final, key):
        return True
    for attempt in reply.get("attempts") or []:
        if isinstance(attempt, dict) and nested_reply_flag(attempt, key):
            return True
    return False


def final_reply_phone_visible(reply: dict[str, Any]) -> bool:
    final = reply.get("final") if isinstance(reply.get("final"), dict) else reply
    attempts = reply.get("attempts") if isinstance(reply.get("attempts"), list) else []
    candidates: list[dict[str, Any]] = []
    if isinstance(final, dict):
        candidates.append(final)
    candidates.extend(item for item in attempts if isinstance(item, dict))
    for item in candidates:
        if bool(item.get("phone_visible_confirmed")):
            return True
        stdout = item.get("stdout")
        if isinstance(stdout, dict) and bool(stdout.get("phoneVisibleConfirmed")):
            return True
    return False


def final_reply_delivery_accepted(reply: dict[str, Any]) -> bool:
    final = reply.get("final") if isinstance(reply.get("final"), dict) else reply
    attempts = reply.get("attempts") if isinstance(reply.get("attempts"), list) else []
    candidates: list[dict[str, Any]] = []
    if isinstance(final, dict):
        candidates.append(final)
    candidates.extend(item for item in attempts if isinstance(item, dict))
    for item in candidates:
        if bool(item.get("delivery_accepted")):
            return True
        stdout = item.get("stdout")
        if isinstance(stdout, dict) and bool(stdout.get("deliveryAccepted")):
            return True
    return False


def classify_final_reply_waiting_context(
    *,
    token_present: bool,
    reason: str,
    media_info: dict[str, Any],
) -> dict[str, Any]:
    if reason in {"sendmessage_ret_-2", "media_sendmessage_ret_-2"}:
        category = "token_present_but_send_rejected" if token_present else "send_rejected_without_context_token"
        next_step = (
            "stored context token was present but Weixin/OpenClaw rejected the send; wait for a fresh inbound message, then retry"
            if token_present
            else "wait for a real Weixin inbound message to provide context, then retry"
        )
    elif reason == "sendmessage_errcode_-14":
        category = "openclaw_session_expired"
        next_step = "refresh the OpenClaw session/context before retrying"
    elif reason == "weixin_send_circuit_open":
        category = "send_circuit_open"
        next_step = "wait for the account send circuit to close before retrying"
    else:
        category = "waiting_weixin_context"
        next_step = "wait for the user to send a real Weixin message, then retry with a fresh context token"
    return {
        "diagnostic_category": category,
        "context_token_present": bool(token_present),
        "fresh_inbound_required": reason in {"sendmessage_ret_-2", "media_sendmessage_ret_-2", "weixin_send_circuit_open"},
        "delivery_stage": "media" if str(reason or "").startswith("media_") or bool(media_info) else "text",
        "next_step": next_step,
    }


def classify_media_send_failure(reply: dict[str, Any]) -> dict[str, Any]:
    ret = weixin_business_ret(reply)
    errcode = weixin_business_errcode(reply)
    gateway_submitted = nested_reply_flag(reply, "gatewaySubmitted")
    delivery_accepted = final_reply_delivery_accepted(reply)
    if ret == -2:
        category = "media_sendmessage_ret_-2"
        recoverable = True
    elif gateway_submitted and not delivery_accepted:
        category = "gateway_submitted_phone_delivery_unconfirmed"
        recoverable = True
    elif errcode is not None:
        category = f"media_sendmessage_errcode_{errcode}"
        recoverable = errcode in (-14,)
    else:
        category = "media_send_failed"
        recoverable = False
    return {
        "category": category,
        "recoverable": recoverable,
        "weixin_ret": ret,
        "weixin_errcode": errcode,
        "gateway_submitted": gateway_submitted,
        "delivery_accepted": delivery_accepted,
        "phone_visible_confirmed": final_reply_phone_visible(reply),
        "policy": "gatewaySubmitted is local acceptance only; attachment is complete only after deliveryAccepted evidence",
    }
