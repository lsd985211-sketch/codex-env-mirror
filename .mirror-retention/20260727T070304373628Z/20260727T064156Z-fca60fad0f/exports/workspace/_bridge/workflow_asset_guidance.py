#!/usr/bin/env python3
"""Derive bounded admission-time guidance for useful workspace assets.

Ownership: workflow route projection over already selected rules, skills,
maintenance owners, and tool policies.
Non-goals: create an asset catalog, execute tools, prove that an asset was
used, or turn optional guidance into a completion gate.
State behavior: pure and read-only; output depends only on the supplied plan
and existing route projections.
Caller context: execution_route_pack builds this after route and owner
selection so Codex sees the smallest useful asset path before execution.
"""

from __future__ import annotations

from typing import Any

from intent_routing import matched_terms


CODE_TERMS = (
    "code",
    "source",
    "module",
    "function",
    "class",
    "refactor",
    "implementation",
    "代码",
    "源码",
    "模块",
    "函数",
    "重构",
    "实现",
    "修改",
    "修复",
    "解决",
    "架构",
    "architecture",
    "操作系统",
    "AI OS",
    "ai operating system",
    "agent runtime",
    "production AI",
)

FLOW_TERMS = (
    "call path",
    "call chain",
    "impact",
    "blast radius",
    "shared",
    "cross-repository",
    "调用链",
    "影响范围",
    "共享",
    "跨仓库",
)

STATE_TERMS = (
    "queue",
    "receipt",
    "scheduler",
    "inbox",
    "outbox",
    "sqlite",
    ".db",
    "database",
    "队列",
    "回执",
    "调度器",
    "收件箱",
    "数据库",
)

CONTEXT_COMPRESSION_DIRECT_TERMS = (
    "headroom",
    "context compression",
    "compress context",
    "上下文压缩",
    "压缩上下文",
    "减少上下文占用",
    "降低上下文占用",
)

OVERSIZED_PAYLOAD_TERMS = (
    "oversized",
    "large",
    "long",
    "大量",
    "大型",
    "超长",
    "大段",
    "多文件",
)

COMPRESSIBLE_PAYLOAD_TERMS = (
    "json",
    "log",
    "tool output",
    "code search result",
    "search result",
    "multi-file evidence",
    "日志",
    "工具输出",
    "代码搜索结果",
    "搜索结果",
    "多文件证据",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique_items(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (str(item.get("kind") or ""), str(item.get("name") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        output.append(item)
        if len(output) >= limit:
            break
    return output


def _selected_skills(plan: dict[str, Any]) -> list[dict[str, Any]]:
    orchestration = _as_dict(plan.get("skill_orchestration"))
    if orchestration.get("ok"):
        selected = [item for item in _as_list(orchestration.get("selected_skills")) if isinstance(item, dict)]
        if selected:
            return selected
        # A healthy dynamic catalog may have no match; preserve the already
        # classified non-general domain candidate instead of silently dropping
        # the route. The default domain intentionally stays quiet.
        domains = [
            str(item.get("key") or "")
            for item in _as_list(plan.get("domains"))
            if isinstance(item, dict)
        ]
        if not any(key and key != "general" for key in domains):
            return []
    return [
        {"name": name, "path": "", "reasons": ["workflow_domain_candidate"]}
        for name in _as_list(_as_dict(plan.get("skills")).get("selected"))
        if str(name).strip()
    ]


def _rule_guidance(route_decision: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for decision in _as_list(route_decision.get("policy_decisions")):
        if not isinstance(decision, dict):
            continue
        rule_id = str(decision.get("rule_id") or "").strip()
        if not rule_id:
            continue
        items.append(
            {
                "kind": "rule",
                "name": rule_id,
                "mode": "constraint",
                "reason": str(decision.get("trigger_fact") or decision.get("decision") or "task admission"),
                "action": "apply the boundary while selecting the execution asset",
                "source": _as_dict(decision.get("provenance")).get("source") or decision.get("enforcement_point"),
            }
        )
    return _unique_items(items, limit=3)


def _skill_guidance(plan: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for skill in _selected_skills(plan):
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        reasons = [str(item) for item in _as_list(skill.get("reasons")) if str(item).strip()]
        items.append(
            {
                "kind": "skill",
                "name": name,
                "mode": "guide",
                "reason": reasons[:3],
                "action": "read the selected SKILL.md, then hand execution to its owning lower layer",
                "path": str(skill.get("path") or ""),
            }
        )
    return _unique_items(items, limit=2)


def _owner_guidance(
    environment_context: dict[str, Any],
    message: str,
    *,
    primary_systems: set[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    systems = [item for item in _as_list(environment_context.get("relevant_systems")) if isinstance(item, dict)]
    primary = [item for item in systems if str(item.get("system") or "") in primary_systems]
    if primary:
        systems = primary
    terms = str(message or "").lower()
    # The maintenance projection can return generic members for a broad system.
    # Prefer a member whose responsibility matches the task-specific terms.
    for system in systems:
        if not isinstance(system, dict):
            continue
        selected_all = [item for item in _as_list(system.get("selected_members")) if isinstance(item, dict)]
        selected = selected_all
        if "memory" in str(system.get("system") or "") and any(term in terms for term in ("pmb", "local_pmb", "daemon")):
            selected = [item for item in selected_all if any(term in str(item.get("member") or "").lower() or term in str(item.get("responsibility") or "").lower() for term in ("pmb", "daemon", "local_pmb"))] or selected_all
        if selected:
            member = selected[0]
            items.append(
                {
                    "kind": "owner",
                    "name": str(member.get("member") or system.get("system") or ""),
                    "mode": "execute_or_validate",
                    "reason": str(member.get("responsibility") or system.get("role") or "")[:320],
                    "action": str(member.get("entry") or system.get("expand") or ""),
                    "system": str(system.get("system") or ""),
                    "authority": str(system.get("authority") or ""),
                }
            )
        elif system.get("system"):
            items.append(
                {
                    "kind": "owner",
                    "name": str(system.get("system")),
                    "mode": "discover",
                    "reason": str(system.get("role") or "")[:320],
                    "action": str(system.get("expand") or ""),
                    "system": str(system.get("system") or ""),
                    "authority": str(system.get("authority") or ""),
                }
            )
    # Keep the task-specific owner before the generic workflow owner.
    items.sort(key=lambda item: (0 if any(term in str(item.get("name") or "").lower() for term in ("pmb", "daemon", "runtime")) else 1, str(item.get("name") or "")))
    return _unique_items(items, limit=2)


def _tool_guidance(
    plan: dict[str, Any],
    route_decision: dict[str, Any],
    tool_policies: list[dict[str, Any]],
    resource_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    message = str(plan.get("message") or "").lower()
    domain_keys = {
        str(item.get("key") or "")
        for item in _as_list(plan.get("domains"))
        if isinstance(item, dict)
    }
    policy_keys = {str(item.get("key") or "") for item in tool_policies if isinstance(item, dict)}
    task_facts = _as_dict(_as_dict(_as_dict(plan.get("structured_route")).get("task_contract")).get("task_facts"))
    tools: list[dict[str, Any]] = []

    if resource_gate.get("enabled") or route_decision.get("resource_delegation_required"):
        tools.append(
            {
                "kind": "tool",
                "name": "resource-layer",
                "mode": "primary",
                "reason": "external source discovery or acquisition is owner-managed",
                "action": str(resource_gate.get("job_run_command_text") or resource_gate.get("delegate_command_text") or ""),
                "use_for": "source selection, network acquisition, retries, and resource result",
                "skip_when": "the task has no external resource need",
            }
        )

    code_context = bool(
        matched_terms(message, CODE_TERMS)
        and domain_keys.intersection({"workflow_governance", "code_maintainability", "mcp_tools", "skills_templates"})
    )
    if "codegraph_policy" in policy_keys and code_context:
        tools.append(
            {
                "kind": "tool",
                "name": "codegraph",
                "mode": "primary_discovery",
                "reason": "the task changes or explains source structure",
                "action": "Hub codegraph.explore with explicit target files and a bounded query",
                "use_for": "exact symbols, source blocks, callers, callees, and narrow local impact",
                "skip_when": "the question is runtime state rather than source structure",
            }
        )
        if matched_terms(message, FLOW_TERMS) or "workflow_governance" in domain_keys:
            tools.append(
                {
                    "kind": "tool",
                    "name": "gitnexus",
                    "mode": "supporting_discovery",
                    "reason": "the task benefits from semantic execution-flow or wider impact context",
                    "action": "Hub gitnexus.query, then context/impact/trace only as needed",
                    "use_for": "semantic flow search, 360-degree symbol context, traces, and cross-repository impact",
                    "skip_when": "CodeGraph already answers the bounded symbol question",
                }
            )

    if "structured_state_policy" in policy_keys and matched_terms(message, STATE_TERMS):
        tools.append(
            {
                "kind": "tool",
                "name": "sqlite-owner-query",
                "mode": "primary_evidence",
                "reason": "the requested state is database or queue backed",
                "action": "use the owning read-only SQLite MCP/Hub query surface",
                "use_for": "bounded current rows, schema, queue, scheduler, and receipt state",
                "skip_when": "the incident is process/runtime state with a dedicated owner",
            }
        )

    compression_requested = bool(matched_terms(message, CONTEXT_COMPRESSION_DIRECT_TERMS))
    oversized_payload = bool(
        matched_terms(message, OVERSIZED_PAYLOAD_TERMS)
        and matched_terms(message, COMPRESSIBLE_PAYLOAD_TERMS)
    )
    if compression_requested or oversized_payload:
        tools.append(
            {
                "kind": "tool",
                "name": "headroom",
                "mode": "supporting_context_compression",
                "reason": "large reversible evidence can be reduced before inline consumption",
                "action": "Hub headroom.compress; retain the returned hash for headroom.retrieve",
                "use_for": "reversible reduction of large tool output, logs, JSON, code-search results, and multi-file evidence",
                "skip_when": "the payload is already bounded, compression has no material benefit, or required decisions and evidence cannot remain independently available",
                "authority": "context_compression capability route",
                "preserve": "source owner receipt or stable artifact; decisions, gates, permissions, failures, acceptance evidence, and next steps",
                "not_for": "durable memory, owner state, or replacement of PMB",
            }
        )

    owner_route = _as_dict(route_decision.get("owner_route"))
    profile = str(owner_route.get("mcp_profile") or owner_route.get("owner_profile") or "").strip()
    if profile and not resource_gate.get("enabled"):
        direct = [str(item) for item in _as_list(owner_route.get("direct_hub_tools")) if str(item).strip()]
        tools.append(
            {
                "kind": "tool",
                "name": profile,
                "mode": "owner_route",
                "reason": str(owner_route.get("priority_reason") or owner_route.get("capability") or "classified owner route"),
                "action": direct[0] if direct else str(owner_route.get("hub_tool") or owner_route.get("native_tool") or ""),
                "use_for": str(owner_route.get("capability") or "owning MCP operation"),
                "skip_when": "a more specific business owner above already owns the action",
            }
        )

    if task_facts.get("gui_or_browser_state") and not any(item.get("name") in {"chrome-devtools", "playwright"} for item in tools):
        tools.append(
            {
                "kind": "tool",
                "name": "browser-or-gui-owner",
                "mode": "runtime_evidence",
                "reason": "the requested truth lives in a live UI/session",
                "action": "use the classified current-session browser or GUI tool",
                "use_for": "visible state, DOM, interaction, and readback",
                "skip_when": "an API or owner CLI can prove the state directly",
            }
        )

    return _unique_items(tools, limit=4)


def build_asset_guidance(
    plan: dict[str, Any],
    *,
    route_decision: dict[str, Any],
    tool_policies: list[dict[str, Any]],
    environment_context: dict[str, Any],
    resource_gate: dict[str, Any],
) -> dict[str, Any]:
    """Return the smallest useful asset path for the task."""

    domains = [str(item.get("key") or "") for item in _as_list(plan.get("domains")) if isinstance(item, dict)]
    task_facts = _as_dict(_as_dict(_as_dict(plan.get("structured_route")).get("task_contract")).get("task_facts"))
    skills = _skill_guidance(plan)
    simple = domains == ["general"] and not any(task_facts.values()) and not tool_policies and not skills
    if simple:
        return {
            "schema": "workflow_asset_guidance.v1",
            "active": False,
            "reason": "simple_task_needs_no_asset_route",
            "sequence": [],
            "rules": [],
            "skills": [],
            "owners": [],
            "tools": [],
        }

    rules = _rule_guidance(route_decision)
    primary_systems = {
        str(system)
        for domain in _as_list(plan.get("domains"))
        if isinstance(domain, dict) and domain.get("drives_execution")
        for system in _as_list(domain.get("systems"))
        if str(system).strip()
    }
    owners = _owner_guidance(
        environment_context,
        str(plan.get("message") or ""),
        primary_systems=primary_systems,
    )
    tools = _tool_guidance(plan, route_decision, tool_policies, resource_gate)
    sequence = [kind for kind, values in (("rules", rules), ("skills", skills), ("owners", owners), ("tools", tools)) if values]
    return {
        "schema": "workflow_asset_guidance.v1",
        "active": bool(sequence),
        "reason": "derived_from_existing_route_authorities",
        "principle": "Use the smallest asset set that materially improves the task. Apply outputs to the work; never call an asset only to prove usage.",
        "sequence": sequence,
        "rules": rules,
        "skills": skills,
        "owners": owners,
        "tools": tools,
        "fallback": "If a selected asset is unavailable or irrelevant after inspection, continue through its configured forward fallback or use the next bounded asset; do not add a usage audit.",
    }


def validate_with_build_plan(build_plan: Any) -> dict[str, Any]:
    cases = {
        "shared_code": build_plan("重构共享工作流代码，分析调用链和影响范围", detail="full"),
        "runtime": build_plan("诊断 PMB 守护进程运行时故障并从根源修复", detail="full"),
        "research": build_plan("联网研究 Python 3.14 官方并发模型变化并给出引用", detail="full"),
        "simple": build_plan("把 hello 翻译成中文", detail="full"),
        "hardware": build_plan("检查 WSL GPU 和 Windows 宿主硬件状态", detail="full"),
        "large_context": build_plan("分析这份大型 JSON 日志和代码搜索结果，保留原始收据并减少上下文占用", detail="full"),
    }
    guidance = {name: _as_dict(_as_dict(plan.get("execution_route_pack")).get("asset_guidance")) for name, plan in cases.items()}
    code_tools = {str(item.get("name") or "") for item in _as_list(guidance["shared_code"].get("tools")) if isinstance(item, dict)}
    runtime_tools = {str(item.get("name") or "") for item in _as_list(guidance["runtime"].get("tools")) if isinstance(item, dict)}
    runtime_owners = {
        str(item.get("system") or item.get("name") or "")
        for item in _as_list(guidance["runtime"].get("owners"))
        if isinstance(item, dict)
    }
    research_tools = {str(item.get("name") or "") for item in _as_list(guidance["research"].get("tools")) if isinstance(item, dict)}
    hardware_owners = {
        str(item.get("system") or item.get("name") or "")
        for item in _as_list(guidance["hardware"].get("owners"))
        if isinstance(item, dict)
    }
    large_context_tools = {
        str(item.get("name") or "")
        for item in _as_list(guidance["large_context"].get("tools"))
        if isinstance(item, dict)
    }
    large_context_domains = {
        str(item.get("key") or "")
        for item in _as_list(cases["large_context"].get("domains"))
        if isinstance(item, dict)
    }
    checks = {
        "shared_code_selects_graph_advantages": {"codegraph", "gitnexus"}.issubset(code_tools),
        "runtime_prefers_pmb_owner": any(item.lower() in {"memory", "pmb", "local-pmb-memory"} for item in runtime_owners),
        "runtime_does_not_force_static_graph": "codegraph" not in runtime_tools and "gitnexus" not in runtime_tools,
        "research_selects_resource_owner": "resource-layer" in research_tools,
        "hardware_prefers_hardware_owner": any("hardware" in item.lower() for item in hardware_owners),
        "large_context_selects_headroom": "headroom" in large_context_tools,
        "existing_search_results_do_not_trigger_resource_acquisition": "resource-layer" not in large_context_tools,
        "existing_search_results_do_not_claim_external_research": "external_docs_research" not in large_context_domains,
        "headroom_preserves_owner_evidence_and_pmb_boundary": any(
            item.get("name") == "headroom"
            and "source owner receipt" in str(item.get("preserve") or "")
            and "PMB" in str(item.get("not_for") or "")
            for item in _as_list(guidance["large_context"].get("tools"))
            if isinstance(item, dict)
        ),
        "simple_task_stays_quiet": guidance["simple"].get("active") is False,
        "guidance_has_no_usage_proof_contract": all(
            "receipt" not in str(payload.get("principle") or "").lower()
            and "proof" not in str(payload.get("principle") or "").lower()
            for payload in guidance.values()
        ),
        "unavailable_asset_has_forward_fallback": all(
            "forward fallback" in str(payload.get("fallback") or "")
            for name, payload in guidance.items()
            if name != "simple"
        ),
    }
    return {
        "schema": "workflow_asset_guidance.validate.v1",
        "ok": all(checks.values()),
        "checks": checks,
        "guidance": guidance,
    }
