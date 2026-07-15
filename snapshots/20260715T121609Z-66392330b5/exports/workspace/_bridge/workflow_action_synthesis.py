#!/usr/bin/env python3
"""Deterministic action synthesis for workflow route packs.

Ownership: translate reliable route-pack evidence plus explicit caller fields
into owner, operation, arguments, and machine-readable missing-input records.
Non-goals: execute owners, mutate business state, retry work, infer secrets, or
replace owner-specific validation and permission checks.
State behavior: pure functions only; this module performs no filesystem writes.
Caller context: workflow_owner_facade builds versioned action contracts from
the synthesis result and remains responsible for lifecycle execution.
"""

from __future__ import annotations

from typing import Any


DOMAIN_OWNER = {
    "email": "email",
    "workflow_governance": "maintenance",
    "code_maintainability": "maintenance",
    "mcp_tools": "mcp",
    "gui_browser": "mcp",
    "mobile_bridge": "mobile",
    "network": "network",
    "office_native": "office",
}

DEFAULT_OPERATION = {
    "resource": "resource_job",
    "email": "intent_submit",
    "maintenance": "owner_command",
    "mcp": "tool_call",
    "mobile": "status",
    "network": "plan",
    "office": "office_command",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _route_context(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    route_pack = _as_dict(plan.get("execution_route_pack"))
    decision = _as_dict(route_pack.get("route_decision"))
    owner_route = _as_dict(decision.get("owner_route"))
    return route_pack, decision, owner_route


def _route_reliable(decision: dict[str, Any]) -> bool:
    ambiguity = _as_dict(decision.get("ambiguity"))
    confidence = float(decision.get("confidence") or 0.0)
    match_quality = str(decision.get("match_quality") or "")
    return not bool(ambiguity.get("is_ambiguous")) and (confidence >= 0.5 or match_quality == "strong")


def _input(name: str, reason: str, *, candidates: list[str] | None = None) -> dict[str, Any]:
    item = {"name": name, "reason": reason, "required": True, "expected_type": "string"}
    if candidates:
        item["candidates"] = candidates
    return item


def _required_arguments(owner: str, operation: str) -> list[str]:
    if owner == "resource":
        return ["task", "target"]
    if owner == "email":
        return ["to", "content", "time"]
    if owner == "maintenance":
        return ["subcommand"]
    if owner == "mcp":
        return ["profile", "tool"] if operation == "tool_call" else (["profile"] if operation == "recover_plan" else [])
    if owner == "mobile":
        return ["task_id"] if operation == "task_get" else (["tool"] if operation == "session_tool_call" else [])
    if owner == "network":
        return ["lease_id"] if operation in {"lease_status", "lease_stop"} else []
    if owner == "office":
        return ["app", "command"]
    return []


def synthesize(
    plan: dict[str, Any],
    *,
    message: str,
    owner: str = "",
    operation: str = "",
    arguments: dict[str, Any] | None = None,
    owner_capabilities: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return deterministic action fields without executing or persisting work."""

    route_pack, decision, owner_route = _route_context(plan)
    explicit_owner = str(owner or "").strip()
    explicit_operation = str(operation or "").strip()
    reliable = _route_reliable(decision)
    primary = str(decision.get("primary_domain") or "")
    supplied = dict(arguments or {})

    selected_owner = explicit_owner
    owner_source = "explicit" if explicit_owner else ""
    capsule_kinds = {
        str(item.get("kind") or "")
        for item in _as_list(route_pack.get("capsules"))
        if isinstance(item, dict) and item.get("kind")
    }
    if not selected_owner and capsule_kinds == {"network"}:
        selected_owner, owner_source = "network", "route_pack.single_network_capsule"
        reliable = True
    if not selected_owner and reliable:
        if decision.get("resource_delegation_required"):
            selected_owner, owner_source = "resource", "route_decision.resource_delegation_required"
        elif primary in DOMAIN_OWNER:
            selected_owner, owner_source = DOMAIN_OWNER[primary], "route_decision.primary_domain"
        elif str(owner_route.get("mcp_profile") or owner_route.get("owner_profile") or ""):
            selected_owner, owner_source = "mcp", "route_decision.owner_route"

    selected_operation = explicit_operation or DEFAULT_OPERATION.get(selected_owner, "")
    operation_source = "explicit" if explicit_operation else ("owner_default" if selected_operation else "")

    if selected_owner == "mcp" and reliable:
        profile = str(owner_route.get("mcp_profile") or owner_route.get("owner_profile") or "")
        tool = str(owner_route.get("tool") or owner_route.get("native_tool") or "")
        capability = str(owner_route.get("capability") or "")
        if profile and "|" not in profile:
            supplied.setdefault("profile", profile)
        if tool and "|" not in tool:
            supplied.setdefault("tool", tool)
        if capability:
            supplied.setdefault("capability", capability)
    elif selected_owner == "mobile" and reliable:
        route_profile = str(owner_route.get("mcp_profile") or owner_route.get("owner_profile") or "")
        route_tool = str(owner_route.get("tool") or owner_route.get("native_tool") or "")
        if route_profile == "mobile-openclaw-bridge" and route_tool:
            selected_operation = explicit_operation or "session_tool_call"
            supplied.setdefault("tool", route_tool)
            supplied.setdefault("capability", str(owner_route.get("capability") or "mobile_bridge"))
    elif selected_owner == "network" and selected_operation == "plan":
        supplied.setdefault("target", message)
        supplied.setdefault("target_kind", "web")

    capabilities = _as_dict((owner_capabilities or {}).get(selected_owner))
    issues: list[str] = []
    fields: list[dict[str, Any]] = []
    if not selected_owner:
        issues.append("owner_not_selected")
        reason = "route_pack_is_ambiguous_or_has_no_supported_owner" if not reliable else "route_pack_has_no_supported_owner"
        fields.append(_input("owner", reason, candidates=sorted((owner_capabilities or {}).keys())))
    elif not capabilities:
        issues.append(f"owner_not_supported:{selected_owner}")
        fields.append(_input("owner", "selected_owner_is_not_supported", candidates=sorted((owner_capabilities or {}).keys())))

    operations = [str(item) for item in _as_list(capabilities.get("operations"))]
    if selected_owner and not selected_operation:
        issues.append("operation_not_selected")
        fields.append(_input("operation", "owner_operation_could_not_be_derived", candidates=operations))
    elif capabilities and selected_operation not in operations:
        issues.append(f"operation_not_supported:{selected_operation}")
        fields.append(_input("operation", "operation_is_not_supported_by_owner", candidates=operations))

    profile_candidate = str(owner_route.get("mcp_profile") or owner_route.get("owner_profile") or "")
    if selected_owner == "mcp" and "|" in profile_candidate and not supplied.get("profile"):
        candidates = [item.strip() for item in profile_candidate.split("|") if item.strip()]
        issues.append("missing_argument:profile")
        fields.append(_input("profile", "route_pack_contains_multiple_session_tools", candidates=candidates))

    if selected_owner == "maintenance" and not (supplied.get("capability_id") or supplied.get("script")):
        issues.append("missing_argument:capability_id_or_script")
        fields.append(
            _input(
                "capability_id_or_script",
                "maintenance_requires_capability_catalog_id_or_legacy_script",
                candidates=["capability_id", "script"],
            )
        )

    for key in _required_arguments(selected_owner, selected_operation):
        if supplied.get(key):
            continue
        issue = f"missing_argument:{key}"
        if issue not in issues:
            issues.append(issue)
        if not any(item.get("name") == key for item in fields):
            fields.append(_input(key, f"required_by:{selected_owner}.{selected_operation}"))

    return {
        "owner": selected_owner,
        "operation": selected_operation,
        "arguments": supplied,
        "complete": not issues,
        "issues": issues,
        "needs_input": {
            "required": bool(fields),
            "fields": fields,
            "next_action": "provide_missing_fields_and_rebuild_action" if fields else "run",
        },
        "synthesis": {
            "route_reliable": reliable,
            "primary_domain": primary,
            "confidence": float(decision.get("confidence") or 0.0),
            "match_quality": str(decision.get("match_quality") or ""),
            "owner_source": owner_source,
            "operation_source": operation_source,
            "explicit_owner": bool(explicit_owner),
            "explicit_operation": bool(explicit_operation),
        },
    }


def validate() -> dict[str, Any]:
    capabilities = {
        "resource": {"operations": ["resource_job"]},
        "maintenance": {"operations": ["owner_command"]},
        "mcp": {"operations": ["tool_call"]},
        "network": {"operations": ["plan"]},
    }
    strong_resource = synthesize(
        {"execution_route_pack": {"route_decision": {"primary_domain": "github", "confidence": 1.0, "match_quality": "strong", "ambiguity": {"is_ambiguous": False}, "resource_delegation_required": True}}},
        message="find repository",
        arguments={"task": "find repository", "target": "find repository"},
        owner_capabilities=capabilities,
    )
    weak = synthesize(
        {"execution_route_pack": {"route_decision": {"primary_domain": "general", "confidence": 0.0, "match_quality": "no_match_fallback", "ambiguity": {"is_ambiguous": True}}}},
        message="unknown",
        owner_capabilities=capabilities,
    )
    network = synthesize(
        {"execution_route_pack": {"route_decision": {"primary_domain": "general", "confidence": 0.0, "match_quality": "fallback", "ambiguity": {"is_ambiguous": True}}, "capsules": [{"kind": "network", "contract": {}}]}},
        message="network plan",
        owner_capabilities=capabilities,
    )
    maintenance_capability = synthesize(
        {},
        message="scheduler metrics",
        owner="maintenance",
        operation="owner_command",
        arguments={"capability_id": "scheduler", "subcommand": "metrics"},
        owner_capabilities=capabilities,
    )
    maintenance_legacy = synthesize(
        {},
        message="scheduler metrics",
        owner="maintenance",
        operation="owner_command",
        arguments={"script": "scheduler.py", "subcommand": "metrics"},
        owner_capabilities=capabilities,
    )
    issues = []
    if strong_resource.get("owner") != "resource" or not strong_resource.get("complete"):
        issues.append("strong_resource_route_not_synthesized")
    if weak.get("owner") or [item.get("name") for item in _as_dict(weak.get("needs_input")).get("fields", [])] != ["owner"]:
        issues.append("ambiguous_route_needs_input_invalid")
    if network.get("owner") != "network" or network.get("operation") != "plan" or not network.get("complete"):
        issues.append("single_network_capsule_not_synthesized")
    if not maintenance_capability.get("complete"):
        issues.append("maintenance_capability_route_not_synthesized")
    if not maintenance_legacy.get("complete"):
        issues.append("maintenance_legacy_route_not_synthesized")
    return {"schema": "workflow_action_synthesis.validate.v1", "ok": not issues, "issues": issues}


if __name__ == "__main__":
    import json

    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("ok") else 1)
