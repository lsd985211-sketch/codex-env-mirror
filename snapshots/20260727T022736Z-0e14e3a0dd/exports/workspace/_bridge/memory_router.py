#!/usr/bin/env python3
"""Task-fit router for local memory layers.

This module decides which memory layer should help a task. It does not read or
write PMB, notes, records, or external knowledge by itself; it emits a compact
plan that workflow entrypoints can use before choosing concrete tools.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from intent_routing import matched_terms
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"

configure_utf8_stdio()


SIMPLE_SKIP_TERMS = (
    "翻译",
    "改写一句",
    "一句话",
    "格式化",
    "当前时间",
    "date",
)

LONG_LIVED_TERMS = (
    "mcp",
    "transport closed",
    "桥接",
    "微信",
    "邮箱",
    "调度",
    "资源层",
    "记录库",
    "备份",
    "codegraph",
    "pmb",
    "PMB",
    "记忆",
    "记忆系统",
    "记忆机制",
    "记忆治理",
    "记忆利用",
    "记忆路由",
    "记忆层",
    "长期记忆",
    "候选记忆",
    "记忆候选",
    "记忆重构",
    "记忆优化",
    "记忆检索",
    "记忆写入",
    "记忆吸收",
    "记忆沉淀",
    "记忆验证",
    "记忆整理",
    "回忆",
    "召回",
    "召回机制",
    "memory_router",
    "memory_governance",
    "local_pmb_memory",
    "recall",
    "memory recall",
    "memory routing",
    "memory governance",
    "memory system",
    "memory layer",
    "memory usage",
    "memory optimization",
    "memory refactor",
    "memory retrieval",
    "memory absorption",
    "memory verification",
    "技能",
    "基线",
    "规则",
    "治理",
    "稳定",
    "根因",
    "复现",
    "又",
    "之前",
    "历史",
)

PROFILE_TERMS = (
    "用户画像",
    "个人画像",
    "画像",
    "用户偏好",
    "工作偏好",
    "沟通偏好",
    "长期目标",
    "取舍",
    "身份上下文",
    "user_profile",
    "user profile",
    "profile_guidance",
)
PROFILE_ON_DEMAND_TERMS = (
    *PROFILE_TERMS,
    "我希望",
    "我觉得",
    "我更倾向",
    "我的要求",
    "以后你",
    "长期",
    "目标",
    "偏好",
    "习惯",
)
DECISION_NEED_TERMS = (
    "优化",
    "设计",
    "方案",
    "计划",
    "架构",
    "治理",
    "机制",
    "职责边界",
    "职责划分",
    "冲突",
    "矛盾",
    "拮抗",
    "冗余",
    "整理",
    "重构",
    "实现",
    "落地",
    "执行",
    "继续",
    "扩展",
    "完善",
    "取舍",
    "权衡",
    "优先级",
    "自动化",
    "稳定",
    "安全",
    "效率",
    "简洁",
    "不要牺牲",
    "不牺牲",
    "成本",
    "体验",
    "交互",
    "弹窗",
    "默认",
    "应该",
    "怎么做",
    "怎么办",
    "best",
    "optimize",
    "design",
    "plan",
    "architecture",
    "tradeoff",
    "priority",
    "automation",
    "stable",
    "safe",
    "efficient",
)
PREFERENCE_TERMS = ("我希望", "我觉得", "偏好", "习惯", "以后", "准则", "规则", "约束", *PROFILE_TERMS)
EXTERNAL_TERMS = ("联网", "搜索", "官方", "文档", "github", "项目", "论文")
RECORD_TERMS = ("执行记录", "日志", "record-store", "记录库", "历史记录")
SIDE_ISSUE_TERMS = ("临时笔记", "旁支", "不直接相关", "收口", "后续处理")
WORKFLOW_GOVERNANCE_TERMS = (
    "工作机制",
    "工作流",
    "工作模式",
    "执行策略",
    "全局机制",
    "全局系统",
    "系统机制",
    "机制问题",
    "机制冲突",
    "治理机制",
    "职责冲突",
    "职责重叠",
    "职责边界",
    "职责划分",
    "旧机制",
    "残余机制",
    "旧机制残余",
    "冗余",
    "重复",
    "矛盾",
    "拮抗",
    "冲突",
    "互相矛盾",
    "互相拮抗",
    "不一致",
    "上下文消耗",
    "上下文预算",
    "精简",
    "效率",
    "准确",
    "减少上下文",
    "token",
    "context",
    "workflow optimization",
    "workflow governance",
    "global governance",
    "coherence",
    "redundant",
    "contradiction",
    "conflict",
    "overlap",
    "legacy mechanism",
    "responsibility boundary",
    "context budget",
)
DEEP_TERMS = ("彻底", "根本", "根因", "复现", "全局", "无死角", "严重", "长期", "架构")


def compact_terms(values: list[str], limit: int = 10) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def has_any(text: str, terms: tuple[str, ...]) -> bool:
    return bool(matched_terms(text, terms))


def domain_keys(domains: list[dict[str, Any]] | None = None) -> list[str]:
    return [str(item.get("key") or "") for item in domains or [] if isinstance(item, dict)]


def query_terms(message: str, domains: list[dict[str, Any]] | None = None) -> list[str]:
    terms: list[str] = []
    for domain in domains or []:
        if not isinstance(domain, dict):
            continue
        terms.append(str(domain.get("key") or ""))
        terms.extend(str(item) for item in domain.get("keyword_hits", []) if str(item).strip())
    for token in LONG_LIVED_TERMS + PROFILE_TERMS + WORKFLOW_GOVERNANCE_TERMS + EXTERNAL_TERMS + RECORD_TERMS:
        if matched_terms(message, (token,)):
            terms.append(token)
    return compact_terms(terms, 12)


def layer(
    key: str,
    action: str,
    reason: str,
    *,
    command: str = "",
    verify: str = "",
    max_items: int = 0,
    freshness: str = "stable_or_contextual",
) -> dict[str, Any]:
    return {
        "key": key,
        "action": action,
        "reason": reason,
        "command": command,
        "verify": verify,
        "max_items": max_items,
        "freshness": freshness,
    }


def route(message: str, domains: list[dict[str, Any]] | None = None, risk: str = "unknown") -> dict[str, Any]:
    text = str(message or "").lower()
    keys = domain_keys(domains)
    broad_or_high_risk = risk in {"L2", "L3", "high"} or has_any(text, DEEP_TERMS)
    long_lived = bool({"bridge", "mcp_tools", "memory", "email", "records_resources", "code_maintainability"} & set(keys))
    long_lived = long_lived or has_any(text, LONG_LIVED_TERMS)
    simple = has_any(text, SIMPLE_SKIP_TERMS) and not long_lived and not broad_or_high_risk
    workflow_governance = has_any(text, WORKFLOW_GOVERNANCE_TERMS)
    if workflow_governance and "workflow_governance" not in keys:
        keys.append("workflow_governance")
    explicit_profile_needed = has_any(text, PROFILE_ON_DEMAND_TERMS)
    decision_profile_needed = (
        has_any(text, DECISION_NEED_TERMS)
        and not simple
        and (long_lived or broad_or_high_risk or workflow_governance or bool(keys))
    )
    profile_needed = explicit_profile_needed or decision_profile_needed
    external = has_any(text, EXTERNAL_TERMS)
    record = has_any(text, RECORD_TERMS)
    side_issue = has_any(text, SIDE_ISSUE_TERMS)

    layers: list[dict[str, Any]] = []
    if simple:
        primary = "skip_long_term_memory"
        layers.append(layer("current_context", "use", "self-contained task; current context is enough", max_items=0))
    else:
        primary = "quick_pass"
        layers.append(
            layer(
                "memory_quick_pass",
                "use",
                "cheap routing pass before choosing deeper memory",
                command="python _bridge\\codex_workflow_gate.py memory-preflight --message <task>",
                verify="explicit use_or_skip decision",
                max_items=3,
            )
        )
        if long_lived:
            primary = "pmb_prepare" if broad_or_high_risk else "pmb_recall"
            layers.append(
                layer(
                    "pmb",
                    "use",
                    "long-lived workspace, repeated issue, or project lesson can change the work path",
                    command="hub.pmb_prepare|hub.pmb_recall first; fallback native local-pmb-memory prepare|recall only when Hub is unavailable or insufficient; fallback python _bridge\\local_pmb_memory.py pmb-recall",
                    verify="memory result id/source plus live verification for drift-prone facts",
                    max_items=5,
                    freshness="memory_is_first_hypothesis_not_live_state",
                )
            )
        if profile_needed:
            layers.append(
                layer(
                    "user_profile",
                    "use",
                    "task requires Codex to make preference-sensitive decisions about style, goals, tradeoffs, automation, safety, stability, efficiency, or interaction behavior",
                    command="python _bridge\\memory_governance.py snapshot",
                    verify="use only profile facts relevant to the decision; do not inject the full profile or use it for live-state evidence",
                    max_items=5,
                    freshness="stable_profile_guidance_on_demand",
                )
            )
        if external:
            layers.append(
                layer(
                    "external_knowledge",
                    "use_or_capture_candidate",
                    "external sources may become reusable evidence",
                    command="python _bridge\\external_knowledge.py capture-decision ... at closeout",
                    verify="official/primary, scoped, reusable, non-sensitive",
                    max_items=4,
                    freshness="source_version_or_capture_date_required",
                )
            )
        if record:
            layers.append(
                layer(
                    "record_store",
                    "use_index",
                    "historical execution evidence should be queried through the record index",
                    command="python _bridge\\shared\\record_store_maintenance.py query --term <term> --limit 5; use --area/--kind/--status/--since filters before any broad file scan",
                    verify="indexed row/source path readback",
                    max_items=5,
                )
            )
        if side_issue:
            layers.append(
                layer(
                    "one_shot_work_notes",
                    "use",
                    "valuable but non-blocking side issues should wait for closeout",
                    command="python _bridge\\memory_governance.py work-note-add --text <short>",
                    verify="closeout reads and disposes entries; no authorization inheritance",
                    max_items=10,
                    freshness="current_task_only",
                )
            )

    if not any(item["key"] == "one_shot_work_notes" for item in layers):
        layers.append(
            layer(
                "one_shot_work_notes",
                "available_if_needed",
                "capture non-blocking side issues without interrupting main work",
                command="python _bridge\\memory_governance.py work-note-add --text <short>",
                verify="closeout package includes active_count and entries",
                max_items=10,
                freshness="current_task_only",
            )
        )

    return {
        "schema": "memory_router.route.v1",
        "ok": True,
        "generated_at": now_iso(),
        "primary": primary,
        "risk": risk,
        "domain_keys": keys,
        "query_terms": query_terms(message, domains),
        "layers": layers,
        "injection_policy": {
            "max_total_items": 8,
            "keep_fields": ["conclusion", "source", "time", "confidence", "verify_live"],
            "do_not_inject": ["raw_logs", "secrets", "full_transcripts", "large_record_bodies"],
        },
        "verification_policy": {
            "live_state_must_be_verified": [
                "MCP current-turn callability",
                "running processes",
                "ports",
                "task states",
                "file existence",
                "remote repository state",
            ],
            "memory_can_seed": ["prior root causes", "user preferences", "stable module boundaries", "reusable procedures"],
        },
        "profile_policy": {
            "on_demand": True,
            "trigger": "explicit profile/preference wording or an implicit preference-sensitive decision point",
            "skip_for": ["simple self-contained tasks", "pure factual lookup with no tradeoff", "live state verification"],
            "use_for": ["communication style", "long-term goals", "identity context", "tradeoff priorities", "automation/safety/efficiency choices"],
        },
        "write_policy": {
            "read_only_by_default": True,
            "long_term_write_requires_approval": True,
            "work_note_authorization_inherited": False,
        },
    }


def validate() -> dict[str, Any]:
    samples = [
        ("翻译这句话", [], "skip_long_term_memory", []),
        ("MCP transport closed 又复现，找到根因", [{"key": "mcp_tools", "keyword_hits": ["mcp"]}], "pmb_prepare", ["pmb"]),
        ("又出现了只ack不思考的问题，找到根因", [{"key": "bridge", "keyword_hits": ["只ack"]}], "pmb_prepare", ["pmb"]),
        ("codex_delegation mobile_ack 后没有 mobile_result_begin", [{"key": "bridge", "keyword_hits": ["codex_delegation", "mobile_ack"]}], "pmb_recall", ["pmb"]),
        ("我希望以后收口时处理临时笔记", [{"key": "memory", "keyword_hits": ["记忆"]}], "pmb_recall", ["user_profile"]),
        ("目前用户画像的来源是什么，利用方式又是什么", [], "pmb_recall", ["user_profile"]),
        ("目前记忆系统的利用方式是什么，继续优化重构记忆治理", [{"key": "memory", "keyword_hits": ["记忆系统"]}], "pmb_recall", ["pmb", "user_profile"]),
        ("目前的工作机制需要优化精简，减少上下文消耗", [{"key": "workflow_governance", "keyword_hits": ["工作机制"]}], "quick_pass", ["user_profile"]),
        ("联网搜索官方文档并沉淀外部知识", [{"key": "memory", "keyword_hits": ["外部知识"]}], "pmb_recall", ["external_knowledge"]),
        ("查询执行记录里之前的失败原因", [{"key": "records_resources", "keyword_hits": ["记录"]}], "pmb_recall", ["record_store"]),
        ("检查全局系统冗余、矛盾和拮抗机制", [{"key": "workflow_governance", "keyword_hits": ["全局机制", "冗余", "矛盾"]}], "quick_pass", ["user_profile"]),
    ]
    checks: list[dict[str, Any]] = []
    for message, domains, expected, required_layers in samples:
        result = route(message, domains)
        actual_layers = [item["key"] for item in result["layers"]]
        missing_layers = [item for item in required_layers if item not in actual_layers]
        checks.append(
            {
                "message": message,
                "expected_primary": expected,
                "actual_primary": result["primary"],
                "required_layers": required_layers,
                "missing_layers": missing_layers,
                "ok": result["primary"] == expected and not missing_layers,
                "layers": actual_layers,
            }
        )
    return {
        "schema": "memory_router.validate.v1",
        "ok": all(item["ok"] for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route task-fit use of local memory layers")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("route")
    p.add_argument("--message", required=True)
    p.add_argument("--risk", default="unknown")
    p.add_argument("--domain", action="append", default=[])
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    if args.command == "route":
        domains = [{"key": item, "keyword_hits": []} for item in args.domain]
        payload = route(args.message, domains, risk=args.risk)
    else:
        payload = validate()
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
