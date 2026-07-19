#!/usr/bin/env python3
"""Build bounded task-relevant context about the Codex working environment.

Ownership: workflow route projection over existing membership, maintenance, and
MCP route authorities.
Non-goals: define members, duplicate capability inventories, choose task owners,
execute commands, or mutate workspace state.
State behavior: read-only; queries derived indexes and degrades to stable
expansion commands when an index is unavailable.
Caller context: workflow_orchestrator builds the context once and
execution_route_pack carries it to micro, standard, and full plan consumers.
"""

from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from maintenance_capability_registry import query_registry
from mcp_capability_routes import lookup as lookup_mcp_routes
from shared.json_cli import configure_utf8_stdio, now_iso, print_json
from system_membership import CONTRACTS


configure_utf8_stdio()


ARCHITECTURE_CHAIN: tuple[dict[str, str], ...] = (
    {
        "layer": "instructions",
        "responsibility": "platform, global, and nearest workspace rules define precedence and hard boundaries",
        "next": "workflow_decision",
    },
    {
        "layer": "workflow_decision",
        "responsibility": "structured facts select task mode, owner, gates, stop conditions, validation, and closeout",
        "next": "owner_contract",
    },
    {
        "layer": "owner_contract",
        "responsibility": "the owning system defines commands, permissions, retries, fallback, evidence, and lifecycle",
        "next": "tool_execution",
    },
    {
        "layer": "tool_execution",
        "responsibility": "MCP, Hub, CLI, API, or GUI performs the bounded action through the configured route",
        "next": "owner_state",
    },
    {
        "layer": "owner_state",
        "responsibility": "the real owner state and receipt remain authoritative; transport success alone is insufficient",
        "next": "validation_closeout",
    },
    {
        "layer": "validation_closeout",
        "responsibility": "acceptance predicates, changed-file impact, durable evidence, and required receipts complete the task",
        "next": "complete",
    },
)


DOMAIN_SYSTEMS: dict[str, tuple[str, ...]] = {
    "structured_state": ("records",),
    "bridge": ("bridge",),
    "hardware": ("hardware",),
    "audio": ("audio", "hardware"),
    "mcp_tools": ("mcp",),
    "network_routing": ("network", "mcp"),
    "cli_harness": ("mcp",),
    "office_native": ("office",),
    "memory": ("memory",),
    "workflow_governance": ("workflow",),
    "email": ("mail",),
    "skills_templates": ("skills",),
    "code_maintainability": ("workflow",),
    "github": ("mcp", "resource", "network"),
    "external_docs_research": ("resource", "mcp", "network"),
    "gui_browser": ("mcp",),
    "records_resources": ("records", "resource"),
    "resource_acquisition": ("resource", "network", "mcp"),
    "editing_backup": ("workflow",),
    "encoding": ("workflow",),
}


FACT_SYSTEMS: dict[str, tuple[str, ...]] = {
    "local_write": ("workflow",),
    "config_change": ("startup", "workflow"),
    "system_member_change": ("workflow",),
    "external_network_read": ("resource", "network", "mcp"),
    "external_write": ("workflow",),
    "resource_materialization": ("resource", "network"),
    "package_install": ("resource", "network"),
    "database_write": ("records",),
    "gui_or_browser_state": ("mcp",),
    "reload_or_restart_required": ("startup",),
    "durable_closeout_required": ("workflow",),
    "explicit_mobile_envelope": ("bridge",),
}


SOURCE_REFS = {
    "instructions": [str(Path.home() / ".codex" / "AGENTS.md"), "AGENTS.md"],
    "membership": ["_bridge/system_membership.py"],
    "maintenance": [
        "_bridge/docs/maintenance_surface_map.md",
        "_bridge/runtime/maintenance_capabilities.sqlite",
    ],
    "mcp": [
        "_bridge/docs/mcp_capability_matrix.md",
        "_bridge/runtime/mcp_capability_routes.json",
    ],
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unique(values: list[str], limit: int) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _terms(message: str, domain_keys: list[str], selected_skills: list[str], matrix_terms: list[str]) -> list[str]:
    tokens = re.findall(r"[a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", str(message or "").lower())
    return _unique([*domain_keys, *selected_skills, *matrix_terms, *tokens], 32)


def _relevant_systems(domain_keys: list[str], task_facts: dict[str, Any], selected_skills: list[str]) -> list[dict[str, Any]]:
    scores: dict[str, int] = {"workflow": 1}
    reasons: dict[str, list[str]] = {"workflow": ["workflow_route"]}

    def add(system: str, reason: str, weight: int) -> None:
        if system not in CONTRACTS:
            return
        scores[system] = scores.get(system, 0) + weight
        reasons.setdefault(system, []).append(reason)

    for domain in domain_keys:
        if domain in CONTRACTS:
            add(domain, f"domain:{domain}", 4)
        for system in DOMAIN_SYSTEMS.get(domain, ()):
            add(system, f"domain:{domain}", 3)
    for fact, value in task_facts.items():
        if not value:
            continue
        for system in FACT_SYSTEMS.get(str(fact), ()):
            add(system, f"fact:{fact}", 4)
    operational_skills = [skill for skill in selected_skills if str(skill).strip() != "global-framework"]
    if operational_skills:
        add("skills", "selected_skills", 2)
    if any("memory" in str(skill).lower() or "pmb" in str(skill).lower() for skill in operational_skills):
        add("memory", "selected_memory_skill", 3)

    ordered = sorted(scores, key=lambda system: (-scores[system], system))[:6]
    return [
        {"system": system, "score": scores[system], "reasons": _unique(reasons.get(system, []), 4)}
        for system in ordered
    ]


def _health_entry(contract: dict[str, Any]) -> str:
    commands = contract.get("health_commands") if isinstance(contract.get("health_commands"), list) else []
    if not commands or not isinstance(commands[0], dict):
        return ""
    args = commands[0].get("args") if isinstance(commands[0].get("args"), list) else []
    return "python " + " ".join(str(part).replace("/", "\\") for part in args) if args else ""


def _owner_terms(contract: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for surface in contract.get("required_surfaces", []):
        if isinstance(surface, dict):
            values.append(str(surface.get("owner") or ""))
    terms: list[str] = []
    for value in values:
        for match in re.findall(r"(?:_bridge[/\\])?([a-zA-Z0-9_\-]+\.py)", value):
            terms.append(match)
    return _unique(terms, 8)


def _health_terms(health_entry: str) -> list[str]:
    return _unique(re.findall(r"(?:_bridge[/\\])?([a-zA-Z0-9_\-]+\.py)", health_entry), 3)


@lru_cache(maxsize=64)
def _query_capabilities(system: str, term: str = "") -> dict[str, Any]:
    return query_registry(system=system, term=term, limit=20)


def _capability_score(
    item: dict[str, Any],
    terms: list[str],
    owner_terms: list[str],
    health_terms: list[str],
) -> int:
    identity = " ".join(
        str(item.get(key) or "")
        for key in ("module_path", "surface", "owns", "usual_entry")
    ).lower()
    score = 0
    for owner_term in owner_terms:
        if owner_term.lower() in identity:
            score += 20
    for health_term in health_terms:
        if health_term.lower() in identity:
            score += 5
    for term in terms:
        normalized = str(term).lower().replace("_", " ").strip()
        if len(normalized) >= 2 and normalized in identity:
            score += 2
    if item.get("script_exists"):
        score += 1
    return score


def _system_context(system_row: dict[str, Any], terms: list[str], issues: list[dict[str, str]]) -> dict[str, Any]:
    system = str(system_row.get("system") or "")
    contract = _as_dict(CONTRACTS.get(system))
    health_entry = _health_entry(contract)
    owner_terms = _owner_terms(contract)
    health_terms = _health_terms(health_entry)
    queries = [_query_capabilities(system)]
    queries.extend(_query_capabilities(system, term) for term in [*owner_terms, *health_terms])
    items_by_module: dict[str, dict[str, Any]] = {}
    for query in queries:
        for item in query.get("items", []) if isinstance(query.get("items"), list) else []:
            if not isinstance(item, dict) or not item.get("module_path"):
                continue
            items_by_module[str(item["module_path"])] = item
    query = queries[0]
    items = list(items_by_module.values())
    if not query.get("ok"):
        issues.append({"code": "maintenance_index_unavailable", "system": system, "reason": str(query.get("reason") or "unknown")})
    ranked = sorted(
        (item for item in items if isinstance(item, dict)),
        key=lambda item: (-_capability_score(item, terms, owner_terms, health_terms), str(item.get("module_path") or "")),
    )[:2]
    kinds = [str(item) for item in contract.get("member_kinds", []) if str(item).strip()]
    role = "Owns lifecycle and integration for " + ", ".join(kinds[:4])
    if ranked and ranked[0].get("owns"):
        role += "; task-relevant member: " + str(ranked[0]["owns"])[:220]
    return {
        "system": system,
        "relevance": {"score": system_row.get("score"), "reasons": system_row.get("reasons", [])},
        "role": role,
        "member_kinds": [str(item) for item in contract.get("member_kinds", [])[:5]],
        "authority": "_bridge/system_membership.py",
        "validator": health_entry,
        "selected_members": [
            {
                "member": item.get("module_path"),
                "responsibility": item.get("owns"),
                "entry": item.get("usual_entry"),
                "actions": item.get("actions", [])[:5],
                "source": "_bridge/docs/maintenance_surface_map.md",
            }
            for item in ranked
        ],
        "expand": f"python _bridge\\codex_workflow_entry.py maintenance catalog --system {system} --limit 20",
    }


def _mcp_context(terms: list[str], relevant_systems: list[str], issues: list[dict[str, str]]) -> list[dict[str, Any]]:
    if "mcp" not in relevant_systems:
        return []
    try:
        payload = lookup_mcp_routes(terms[:16])
    except Exception as exc:  # noqa: BLE001 - advisory context must not break the workflow route.
        issues.append({"code": "mcp_route_lookup_failed", "reason": str(exc)[:240]})
        return []
    matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
    return [
        {
            "capability": item.get("capability"),
            "owner_profile": item.get("owner_profile") or item.get("profile"),
            "execution_affinity": item.get("execution_affinity"),
            "session_binding": item.get("session_binding"),
            "required_first_step": item.get("required_first_step"),
            "direct_hub_tools": item.get("direct_hub_tools", [])[:4],
            "validation": item.get("validation_command"),
            "source": "_bridge/runtime/mcp_capability_routes.json",
        }
        for item in matches[:4]
        if isinstance(item, dict)
    ]


def _entrypoints(relevant_systems: list[str], systems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = [
        {
            "purpose": "route a non-simple task",
            "command": "python _bridge\\workflow_orchestrator.py plan --message <task> --detail micro",
            "authority": "workflow_orchestrator",
        },
        {
            "purpose": "discover the owning maintenance surface",
            "command": "python _bridge\\codex_workflow_entry.py maintenance catalog --system <system> --term <term> --limit 20",
            "authority": "maintenance_capability_registry",
        },
    ]
    if "mcp" in relevant_systems:
        entries.append(
            {
                "purpose": "resolve MCP affinity and forward fallback",
                "command": "python _bridge\\mcp_capability_routes.py lookup --terms <capability terms>",
                "authority": "mcp_capability_routes",
            }
        )
    if "workflow" in relevant_systems:
        entries.append(
            {
                "purpose": "reconcile changed architecture files",
                "command": "python _bridge\\system_membership.py impact --changed <file>",
                "authority": "system_membership",
            }
        )
    for system in systems:
        for member in system.get("selected_members", [])[:1]:
            entry = str(member.get("entry") or "").strip()
            if not entry:
                continue
            entries.append(
                {
                    "purpose": str(member.get("responsibility") or "")[:240],
                    "entry": entry,
                    "owner": member.get("member"),
                    "authority": "maintenance_capability_registry",
                }
            )
    return entries[:8]


def build_environment_context(
    *,
    message: str,
    domain_keys: list[str],
    task_facts: dict[str, Any],
    selected_skills: list[str],
    matrix_terms: list[str],
) -> dict[str, Any]:
    """Return a bounded projection that teaches the next action, not the whole environment."""

    issues: list[dict[str, str]] = []
    terms = _terms(message, domain_keys, selected_skills, matrix_terms)
    relevant = _relevant_systems(domain_keys, task_facts, selected_skills)
    systems = [_system_context(item, terms, issues) for item in relevant]
    system_names = [str(item.get("system") or "") for item in systems]
    source_refs = [*SOURCE_REFS["instructions"], *SOURCE_REFS["membership"], *SOURCE_REFS["maintenance"]]
    if "mcp" in system_names:
        source_refs.extend(SOURCE_REFS["mcp"])
    return {
        "schema": "workflow_environment_context.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "projection_rule": "task_relevant_derived_context_only; authority_remains_with_referenced_owners",
        "architecture_chain": list(ARCHITECTURE_CHAIN),
        "relevant_systems": systems,
        "tool_entrypoints": _entrypoints(system_names, systems),
        "mcp_routes": _mcp_context(terms, system_names, issues),
        "relationships": [
            "workflow decides routing and gates; owners retain commands, permissions, retries, state, and evidence",
            "membership and capability indexes are discoverable authorities; this projection does not register members",
            "skills, templates, and memory guide execution but cannot override owner contracts or hard gates",
            "completion requires owner-state acceptance plus the route pack's validation and closeout obligations",
        ],
        "source_refs": _unique(source_refs, 10),
        "expansion_commands": [
            "python _bridge\\workflow_orchestrator.py plan --message <task> --detail standard",
            "python _bridge\\workflow_orchestrator.py plan --message <task> --detail full",
            "python _bridge\\system_membership.py snapshot",
            "python _bridge\\codex_workflow_entry.py maintenance catalog --system <system> --limit 20",
        ],
        "issues": issues,
        "limits": {"systems": 6, "members_per_system": 2, "tool_entrypoints": 8, "mcp_routes": 4},
    }


def validate() -> dict[str, Any]:
    cases = [
        build_environment_context(
            message="联网查询官方文档但不要下载",
            domain_keys=["external_docs_research"],
            task_facts={"external_network_read": True},
            selected_skills=["find-docs"],
            matrix_terms=["documentation"],
        ),
        build_environment_context(
            message="分析一个未知的本地问题",
            domain_keys=["general"],
            task_facts={},
            selected_skills=[],
            matrix_terms=[],
        ),
    ]
    docs_systems = {item.get("system") for item in cases[0].get("relevant_systems", [])}
    unknown_systems = {item.get("system") for item in cases[1].get("relevant_systems", [])}
    forbidden_inventory_keys = {"required_surfaces", "architecture_surfaces", "health_commands", "fallback_chain"}

    def contains_forbidden(value: Any) -> bool:
        if isinstance(value, dict):
            return bool(forbidden_inventory_keys.intersection(value)) or any(contains_forbidden(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_forbidden(item) for item in value)
        return False

    checks = [
        {"name": "docs_route_selects_resource_network_mcp", "ok": {"resource", "network", "mcp"}.issubset(docs_systems), "detail": sorted(docs_systems)},
        {"name": "docs_route_excludes_unrelated_office", "ok": "office" not in docs_systems, "detail": sorted(docs_systems)},
        {"name": "unknown_route_stays_bounded", "ok": unknown_systems == {"workflow"}, "detail": sorted(unknown_systems)},
        {"name": "projection_does_not_copy_authority_inventories", "ok": not any(contains_forbidden(case) for case in cases), "detail": sorted(forbidden_inventory_keys)},
        {"name": "all_members_have_authority_source", "ok": all(member.get("source") for case in cases for system in case.get("relevant_systems", []) for member in system.get("selected_members", [])), "detail": "maintenance_surface_map"},
        {"name": "projection_limits_hold", "ok": all(len(case.get("relevant_systems", [])) <= 6 and len(case.get("tool_entrypoints", [])) <= 8 and len(case.get("mcp_routes", [])) <= 4 for case in cases), "detail": [case.get("limits") for case in cases]},
    ]
    return {
        "schema": "workflow_environment_context.validate.v1",
        "ok": all(item["ok"] for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build bounded task-relevant Codex environment context")
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--message", required=True)
    build_parser.add_argument("--domains", nargs="*", default=[])
    build_parser.add_argument("--facts-json", default="{}")
    build_parser.add_argument("--skills", nargs="*", default=[])
    build_parser.add_argument("--matrix-terms", nargs="*", default=[])
    sub.add_parser("validate")
    args = parser.parse_args(argv)
    if args.command == "validate":
        payload = validate()
    else:
        try:
            facts = json.loads(args.facts_json)
        except json.JSONDecodeError as exc:
            payload = {"schema": "workflow_environment_context.error.v1", "ok": False, "reason": "invalid_facts_json", "detail": str(exc)}
        else:
            payload = build_environment_context(
                message=args.message,
                domain_keys=args.domains,
                task_facts=facts if isinstance(facts, dict) else {},
                selected_skills=args.skills,
                matrix_terms=args.matrix_terms,
            )
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
