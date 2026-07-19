#!/usr/bin/env python3
"""Read-only Reasonix interaction shadow policy.

This module predicts how Codex should interact with Reasonix without touching
bridge state, starting processes, or calling external APIs.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from typing import Any

from intent_routing import matched_terms


MUST_REVIEW_KEYWORDS = (
    "reasonix",
    "reasonsix",
    "架构",
    "配置审查",
    "配置文件审查",
    "跨系统",
    "影响评估",
    "根因",
    "漏洞",
    "风险",
    "回归",
    "审阅",
    "review",
    "architecture",
    "config",
    "root cause",
)

OPTIONAL_REVIEW_KEYWORDS = (
    "分析",
    "检查",
    "验证",
    "优化",
    "计划",
    "策略",
    "diagnose",
    "validate",
    "plan",
    "strategy",
)

LOW_RISK_ACTION_KEYWORDS = (
    "你好",
    "hello",
    "status",
    "状态",
    "测试信息",
    "ok",
)


@dataclass(frozen=True)
class ReasonixState:
    responder_alive: bool = False
    ai_online: bool = False
    credential_sources: tuple[str, ...] = ()
    mcp_transport_ok: bool = True
    desktop_alive: bool = False
    cli_available: bool = False
    last_error: str = ""
    claimed_without_result_count: int = 0
    pending_review_count: int = 0

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ReasonixState":
        if not isinstance(data, dict):
            data = {}
        raw_credentials = data.get("credential_sources", [])
        if not isinstance(raw_credentials, (list, tuple, set)):
            raw_credentials = [raw_credentials] if raw_credentials else []

        def _as_int(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        return cls(
            responder_alive=bool(data.get("responder_alive", False)),
            ai_online=bool(data.get("ai_online", False)),
            credential_sources=tuple(str(x) for x in raw_credentials if x),
            mcp_transport_ok=bool(data.get("mcp_transport_ok", True)),
            desktop_alive=bool(data.get("desktop_alive", False)),
            cli_available=bool(data.get("cli_available", False)),
            last_error=str(data.get("last_error", "") or ""),
            claimed_without_result_count=_as_int(data.get("claimed_without_result_count", 0)),
            pending_review_count=_as_int(data.get("pending_review_count", 0)),
        )


@dataclass(frozen=True)
class TaskContext:
    text: str
    risk: str = "L1"
    explicit_reasonix: bool = False
    command: str = ""
    domain: str = "general"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TaskContext":
        if not isinstance(data, dict):
            data = {}
        text = str(data.get("text", "") or "")
        explicit = bool(data.get("explicit_reasonix", False))
        if re.search(r"\b(reasonix|reasonsix)\b", text, flags=re.IGNORECASE):
            explicit = True
        return cls(
            text=text,
            risk=str(data.get("risk", "L1") or "L1"),
            explicit_reasonix=explicit,
            command=str(data.get("command", "") or ""),
            domain=str(data.get("domain", "general") or "general"),
        )


@dataclass
class PolicyDecision:
    review_need: str
    wake_policy: str
    wait_policy: str
    acceptable_result_kinds: list[str]
    shadow_actions: list[str]
    risks: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    should_call_reasonix: bool = False
    should_block_codex: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_need": self.review_need,
            "wake_policy": self.wake_policy,
            "wait_policy": self.wait_policy,
            "acceptable_result_kinds": self.acceptable_result_kinds,
            "shadow_actions": self.shadow_actions,
            "risks": self.risks,
            "blockers": self.blockers,
            "notes": self.notes,
            "should_call_reasonix": self.should_call_reasonix,
            "should_block_codex": self.should_block_codex,
        }


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return bool(matched_terms(text, keywords))


def classify_review_need(task: TaskContext) -> str:
    text = task.text.strip()
    risk = task.risk.upper()
    if task.explicit_reasonix:
        return "required"
    if risk in {"L2", "L3"}:
        return "required"
    if _contains_any(text, MUST_REVIEW_KEYWORDS):
        return "required"
    if _contains_any(text, OPTIONAL_REVIEW_KEYWORDS):
        return "optional"
    if _contains_any(text, LOW_RISK_ACTION_KEYWORDS):
        return "none"
    return "none"


def decide_reasonix_interaction(task: TaskContext, state: ReasonixState) -> PolicyDecision:
    review_need = classify_review_need(task)
    risks: list[str] = []
    blockers: list[str] = []
    notes: list[str] = ["shadow_only: no bridge writes, process starts, API calls, or workflow interception"]
    actions: list[str] = ["record_shadow_decision"]

    if state.claimed_without_result_count:
        risks.append("claimed_without_result_backlog")
        notes.append("claimed tasks are not successful reviews; only done plus non-empty result can be consumed")
    if state.pending_review_count:
        risks.append("pending_review_backlog")
    if not state.mcp_transport_ok:
        risks.append("mcp_transport_unavailable")
        blockers.append("reasonix_mcp_transport_closed")

    if review_need == "none":
        return PolicyDecision(
            review_need="none",
            wake_policy="none",
            wait_policy="no_wait",
            acceptable_result_kinds=[],
            shadow_actions=actions + ["codex_handles_directly"],
            risks=risks,
            blockers=blockers,
            notes=notes,
            should_call_reasonix=False,
            should_block_codex=False,
        )

    should_block = review_need == "required"
    if not state.responder_alive:
        wake_policy = "responder_then_true_ai"
        actions += ["would_start_responder", "would_recheck_ai_online"]
    elif not state.ai_online:
        wake_policy = "true_ai"
        actions += ["would_check_credentials", "would_probe_true_ai"]
        if not state.credential_sources:
            risks.append("missing_reasonix_credentials")
            blockers.append("true_ai_credentials_missing")
    elif not blockers:
        wake_policy = "none"
        actions += ["would_submit_reasonix_request_with_request_id"]
    else:
        wake_policy = "none"
        actions += ["would_not_submit_until_blocker_cleared"]

    if blockers:
        wait_policy = "blocked"
        acceptable = []
        actions += ["would_surface_blocker"]
    elif state.ai_online:
        wait_policy = "normal_wait" if review_need == "required" else "short_wait"
        acceptable = ["reasonix_ai_review"]
        actions += ["would_wait_for_done_nonempty_result", "would_integrate_and_verify_review"]
    elif review_need == "required":
        wait_policy = "background_wait"
        acceptable = ["reasonix_ai_review", "timeout_pending", "late_result"]
        actions += ["would_not_accept_offline_kb_as_review", "would_preserve_late_result_path"]
    else:
        wait_policy = "short_wait"
        acceptable = ["reasonix_ai_review", "reasonix_offline_kb", "timeout_pending"]
        actions += ["would_label_offline_kb_as_assist_only"]

    if state.last_error:
        risks.append("last_error_present")
        notes.append(f"last_error: {state.last_error[:160]}")

    return PolicyDecision(
        review_need=review_need,
        wake_policy=wake_policy,
        wait_policy=wait_policy,
        acceptable_result_kinds=acceptable,
        shadow_actions=actions,
        risks=sorted(set(risks)),
        blockers=sorted(set(blockers)),
        notes=notes,
        should_call_reasonix=True,
        should_block_codex=should_block and state.ai_online and not blockers,
    )


def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    task = TaskContext.from_mapping(payload.get("task", {}))
    state = ReasonixState.from_mapping(payload.get("state", {}))
    decision = decide_reasonix_interaction(task, state)
    return {
        "task": task.__dict__,
        "state": {
            "responder_alive": state.responder_alive,
            "ai_online": state.ai_online,
            "credential_sources": list(state.credential_sources),
            "mcp_transport_ok": state.mcp_transport_ok,
            "desktop_alive": state.desktop_alive,
            "cli_available": state.cli_available,
            "last_error": state.last_error,
            "claimed_without_result_count": state.claimed_without_result_count,
            "pending_review_count": state.pending_review_count,
        },
        "decision": decision.to_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reasonix read-only interaction policy")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--payload", help="Inline JSON payload with task/state")
    args = parser.parse_args()

    payload = json.loads(args.payload) if args.payload else {"task": {}, "state": {}}
    result = evaluate_payload(payload)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        decision = result["decision"]
        print(f"review_need: {decision['review_need']}")
        print(f"wake_policy: {decision['wake_policy']}")
        print(f"wait_policy: {decision['wait_policy']}")
        print(f"shadow_actions: {', '.join(decision['shadow_actions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
