#!/usr/bin/env python3
"""Categorized module-asset catalog for local maintenance work.

Ownership:
- Builds a read-only, derived catalog from code_maintainability's module
  capability index so Codex can find existing module assets before editing.

Non-goals:
- Does not refactor code, decide ownership by itself, or replace
  code_maintainability placement plans and owner validators.

State behavior:
- Read-only by default. With --rebuild it refreshes the derived runtime index
  owned by code_maintainability; it does not modify source files.

Caller context:
- Use before non-simple code or maintenance work when module count makes owner,
  category, reuse, or validation lookup ambiguous.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from code_maintainability import MODULE_CAPABILITY_INDEX, build_module_index, load_module_index
from intent_routing import matched_terms
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "_bridge" / "runtime" / "module_asset_catalog.json"


CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("workflow_orchestration", ("workflow", "capability_routes", "slash", "codex_workflow", "orchestrator")),
    ("resource_layer", ("resource_", "resource_cli", "resource_broker", "resource_source", "resource_owner")),
    ("network_layer", ("network", "gateway", "proxy", "lease")),
    ("system_diagnostics", ("windows_memory", "kernel_pool", "performance_doctor", "performance_maintenance", "resource_process", "windows_diagnostic")),
    ("mcp_tooling", ("mcp_", "local_mcp", "hub", "tool_coordination")),
    ("memory_and_skills", ("memory", "pmb", "skill_", "skills", "external_knowledge")),
    ("mobile_bridge", ("mobile_openclaw_bridge", "weixin", "openclaw")),
    ("mail", ("email", "mail", "smtp", "inbox", "outbox")),
    ("records_backups", ("record_store", "backup", "archive", "hygiene")),
    ("gui_browser", ("browser", "chrome", "playwright", "gui_")),
    ("shared_primitives", ("/shared/", "shared\\")),
)


ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cli_or_facade", ("cli", "main", "parser", "entry")),
    ("doctor_or_validator", ("doctor", "validate", "health", "smoke")),
    ("strategy_or_policy", ("strategy", "policy", "route", "rules", "matrix")),
    ("adapter_or_executor", ("adapter", "executor", "execute", "hub", "mcp", "gateway")),
    ("scheduler_or_worker", ("scheduler", "worker", "queue", "batch")),
    ("persistence_or_index", ("store", "cache", "index", "sqlite", "record", "manifest")),
    ("tests_or_regression", ("test", "tests", "smoke")),
)


CODE_SCENARIO_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("route_or_policy_change", ("route", "policy", "strategy", "rules", "matrix")),
    ("external_resource_work", ("resource", "source", "download", "package", "owner")),
    ("mcp_or_tool_calling", ("mcp", "hub", "tool", "gateway", "profile")),
    ("state_query_or_indexing", ("sqlite", "query", "index", "record", "cache", "store")),
    ("workflow_or_prompting", ("workflow", "prompt", "plan", "closeout", "gate")),
    ("process_or_scheduler", ("process", "scheduler", "worker", "queue", "batch")),
    ("validation_or_diagnosis", ("validate", "doctor", "health", "smoke", "diagnose")),
    ("ui_or_browser_automation", ("browser", "chrome", "playwright", "gui", "dashboard")),
)


def _module_text(module: dict[str, Any]) -> str:
    pieces = [
        str(module.get("module") or ""),
        str(module.get("purpose") or ""),
        str(module.get("boundary") or ""),
        str(module.get("state_behavior") or ""),
        " ".join(str(item) for item in module.get("capability_terms", []) if item),
    ]
    for entry in module.get("public_entrypoints", []) or []:
        if isinstance(entry, dict):
            pieces.append(str(entry.get("name") or ""))
    return " ".join(pieces).replace("\\", "/").lower()


def _first_rule_match(text: str, rules: tuple[tuple[str, tuple[str, ...]], ...], default: str) -> str:
    for name, markers in rules:
        if any(marker in text for marker in markers):
            return name
    return default


def asset_category(module: dict[str, Any]) -> str:
    text = _module_text(module)
    diagnostic_markers = dict(CATEGORY_RULES)["system_diagnostics"]
    if any(marker in text for marker in diagnostic_markers):
        return "system_diagnostics"
    boundary = str(module.get("boundary") or "")
    if boundary and boundary != "general_bridge":
        return boundary
    return _first_rule_match(text, CATEGORY_RULES, "general_bridge")


def asset_roles(module: dict[str, Any]) -> list[str]:
    text = _module_text(module)
    roles = [name for name, markers in ROLE_RULES if any(marker in text for marker in markers)]
    if not roles:
        roles.append("domain_component")
    return roles


def lifecycle_stage(module: dict[str, Any]) -> str:
    module_path = str(module.get("module") or "")
    line_count = int(module.get("line_count") or 0)
    has_validate = "validate" in _module_text(module)
    if module_path.endswith("_tests.py") or "tests" in module_path:
        return "regression_asset"
    if line_count >= 1200 or module.get("large_file_risk"):
        return "refactor_candidate"
    if has_validate or module_path.endswith(("_doctor.py", "_governance.py")):
        return "stable_maintenance_surface"
    return "supporting_component"


def responsibility(module: dict[str, Any]) -> str:
    roles = asset_roles(module)
    if "doctor_or_validator" in roles:
        return "health_and_validation"
    if "strategy_or_policy" in roles:
        return "routing_policy_and_strategy"
    if "adapter_or_executor" in roles:
        return "execution_adapter"
    if "scheduler_or_worker" in roles:
        return "runtime_scheduling"
    if "persistence_or_index" in roles:
        return "state_and_indexing"
    if "cli_or_facade" in roles:
        return "entrypoint_facade"
    if "tests_or_regression" in roles:
        return "regression_safety"
    return "domain_logic"


def code_scenarios(module: dict[str, Any]) -> list[str]:
    text = _module_text(module)
    scenarios = [name for name, markers in CODE_SCENARIO_RULES if any(marker in text for marker in markers)]
    if not scenarios:
        scenarios.append("general_code_support")
    return scenarios


def reuse_target(module: dict[str, Any]) -> str:
    stage = lifecycle_stage(module)
    if stage == "refactor_candidate":
        return "extend_through_facade_or_extract_peer"
    if stage == "stable_maintenance_surface":
        return "call_owner_surface_or_add_thin_route"
    if stage == "regression_asset":
        return "add_or_extend_regression_case"
    return "reuse_or_extend_when_boundary_matches"


def catalog_record(module: dict[str, Any]) -> dict[str, Any]:
    text = _module_text(module)
    terms = sorted(set(str(item) for item in module.get("capability_terms", []) if item))
    category = asset_category(module)
    return {
        "module": module.get("module"),
        "category": category,
        "roles": asset_roles(module),
        "responsibility": responsibility(module),
        "code_scenarios": code_scenarios(module),
        "reuse_target": reuse_target(module),
        "lifecycle_stage": lifecycle_stage(module),
        "purpose": module.get("purpose"),
        "boundary": module.get("boundary"),
        "state_behavior": module.get("state_behavior"),
        "owner_cli": module.get("owner_cli"),
        "validation": module.get("validation", [])[:5],
        "reuse_policy": module.get("reuse_policy", {}),
        "public_entrypoints": module.get("public_entrypoints", [])[:10],
        "capability_terms": terms[:60],
        "search_text": text[:1000],
    }


def load_or_build_index(*, rebuild: bool, all_bridge: bool, limit: int) -> dict[str, Any]:
    if rebuild or not MODULE_CAPABILITY_INDEX.exists():
        args = SimpleNamespace(root=None, all_bridge=True, limit=limit)
        build_module_index(args)
    return load_module_index(prefer_full=True)


def grouped_view(records: list[dict[str, Any]], key: str, *, limit: int) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        raw = record.get(key)
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            name = str(value or "unknown")
            grouped[name].append(record)
    for items in grouped.values():
        items.sort(key=lambda record: (str(record.get("lifecycle_stage") or ""), str(record.get("module") or "")))
    return {key: value[:limit] for key, value in sorted(grouped.items())}


def task_mode_views(records: list[dict[str, Any]], *, limit: int) -> dict[str, Any]:
    return {
        "maintenance": {
            "focus": ["owning_system", "responsibility", "state_behavior", "owner_cli", "validation"],
            "by_system": grouped_view(records, "category", limit=limit),
            "by_responsibility": grouped_view(records, "responsibility", limit=limit),
            "by_lifecycle_stage": grouped_view(records, "lifecycle_stage", limit=limit),
        },
        "code": {
            "focus": ["role", "code_scenario", "reuse_target", "public_entrypoints", "capability_terms"],
            "by_role": grouped_view(records, "roles", limit=limit),
            "by_scenario": grouped_view(records, "code_scenarios", limit=limit),
            "by_reuse_target": grouped_view(records, "reuse_target", limit=limit),
        },
    }


def build_catalog(args: argparse.Namespace) -> dict[str, Any]:
    index = load_or_build_index(
        rebuild=bool(getattr(args, "rebuild", False)),
        all_bridge=bool(getattr(args, "all_bridge", False)),
        limit=int(getattr(args, "index_limit", getattr(args, "limit", 1000)) or 1000),
    )
    modules = [item for item in index.get("modules", []) if isinstance(item, dict)]
    records = [catalog_record(module) for module in modules]
    category_counts = Counter(str(item.get("category") or "unknown") for item in records)
    role_counts = Counter(role for item in records for role in item.get("roles", []))
    lifecycle_counts = Counter(str(item.get("lifecycle_stage") or "unknown") for item in records)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        grouped[str(item.get("category") or "unknown")].append(item)
    for items in grouped.values():
        items.sort(key=lambda record: (str(record.get("lifecycle_stage") or ""), str(record.get("module") or "")))
    result = {
        "schema": "module_asset_catalog.v1",
        "ok": True,
        "generated_at": now_iso(),
        "source_index": str(MODULE_CAPABILITY_INDEX),
        "source_scope": str(index.get("source", {}).get("scan_scope") or ""),
        "catalog_path": str(CATALOG_PATH),
        "source_count": len(modules),
        "module_count": len(records),
        "category_counts": dict(sorted(category_counts.items())),
        "role_counts": dict(sorted(role_counts.items())),
        "lifecycle_counts": dict(sorted(lifecycle_counts.items())),
        "records": records,
        "groups": {key: value[: int(getattr(args, "group_limit", 20) or 20)] for key, value in sorted(grouped.items())},
        "task_mode_views": task_mode_views(records, limit=int(getattr(args, "group_limit", 20) or 20)),
        "rules": {
            "reuse_before_new_module": True,
            "category_is_for_lookup_not_permission": True,
            "owner_validator_remains_source_of_truth": True,
            "new_modules_should_add_category_role_and_validation_surface": True,
            "maintenance_mode_prioritizes_system_responsibility_and_validation": True,
            "code_mode_prioritizes_role_scenario_and_reuse_fit": True,
            "full_catalog_requires_all_bridge_index": True,
            "core_index_must_not_overwrite_full_catalog": True,
        },
        "external_practice_basis": [
            "software_catalog_style_component_metadata",
            "architecture_decision_records_for_stable_boundaries",
            "documentation_as_code_for_module_ownership_and_validation",
            "actionable_monitoring_for_governance_drift",
        ],
    }
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def compact_catalog_record(record: dict[str, Any], *, task_mode: str) -> dict[str, Any]:
    base = {
        "module": record.get("module"),
        "purpose": record.get("purpose"),
        "boundary": record.get("boundary"),
    }
    if task_mode == "maintenance":
        base.update(
            {
                "category": record.get("category"),
                "responsibility": record.get("responsibility"),
                "lifecycle_stage": record.get("lifecycle_stage"),
                "state_behavior": record.get("state_behavior"),
                "owner_cli": record.get("owner_cli"),
                "validation": list(record.get("validation") or [])[:2],
            }
        )
    elif task_mode == "code":
        base.update(
            {
                "roles": list(record.get("roles") or [])[:4],
                "code_scenarios": list(record.get("code_scenarios") or [])[:4],
                "reuse_target": record.get("reuse_target"),
                "public_entrypoints": [
                    str(item.get("name") or "")
                    for item in list(record.get("public_entrypoints") or [])[:5]
                    if isinstance(item, dict)
                ],
            }
        )
    else:
        base.update(
            {
                "category": record.get("category"),
                "roles": list(record.get("roles") or [])[:3],
                "responsibility": record.get("responsibility"),
                "lifecycle_stage": record.get("lifecycle_stage"),
                "owner_cli": record.get("owner_cli"),
            }
        )
    for key in ("score", "matched_terms", "match_terms"):
        if key in record:
            base[key] = record.get(key)
    return base


def catalog_records(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the complete persisted catalog, with a legacy grouped fallback."""
    records = catalog.get("records")
    if isinstance(records, list):
        return [item for item in records if isinstance(item, dict)]
    return [
        item
        for group in catalog.get("groups", {}).values()
        for item in group
        if isinstance(group, list) and isinstance(item, dict)
    ]


def bounded_catalog_output(catalog: dict[str, Any], *, limit: int, task_mode: str) -> dict[str, Any]:
    bounded_limit = max(1, min(int(limit or 12), 200))
    mode = task_mode if task_mode in {"maintenance", "code"} else "auto"
    records = catalog_records(catalog)
    unique: dict[str, dict[str, Any]] = {}
    for item in records:
        module = str(item.get("module") or "")
        if module and module not in unique:
            unique[module] = item
    ordered = sorted(unique.values(), key=lambda item: (str(item.get("category") or ""), str(item.get("module") or "")))
    selected = [compact_catalog_record(item, task_mode=mode) for item in ordered[:bounded_limit]]
    return {
        "schema": "module_asset_catalog.output.v1",
        "ok": bool(catalog.get("ok")),
        "generated_at": catalog.get("generated_at"),
        "catalog_path": catalog.get("catalog_path"),
        "source_scope": catalog.get("source_scope"),
        "module_count": catalog.get("module_count"),
        "category_counts": catalog.get("category_counts", {}),
        "role_counts": catalog.get("role_counts", {}),
        "lifecycle_counts": catalog.get("lifecycle_counts", {}),
        "task_mode": mode,
        "records": selected,
        "output_budget": {
            "requested_limit": bounded_limit,
            "returned_record_count": len(selected),
            "strict_total_record_limit": True,
            "full_catalog_persisted_only": True,
        },
        "rules": catalog.get("rules", {}),
    }


def lookup_catalog(args: argparse.Namespace) -> dict[str, Any]:
    if not CATALOG_PATH.exists() or bool(getattr(args, "rebuild", False)):
        build_catalog(args)
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    terms = [str(item).strip().lower() for item in (args.term or []) if str(item).strip()]
    task_mode = str(getattr(args, "task_mode", "auto") or "auto").strip().lower()
    if task_mode == "auto":
        text = " ".join(terms)
        task_mode = "maintenance" if matched_terms(text, ("维护", "治理", "doctor", "validate", "owner", "system")) else "code"
    records = catalog_records(catalog)
    matches: list[dict[str, Any]] = []
    for record in records:
        mode_fields = (
            [str(record.get("category") or ""), str(record.get("responsibility") or ""), str(record.get("state_behavior") or ""), " ".join(record.get("validation", []))]
            if task_mode == "maintenance"
            else [" ".join(record.get("roles", [])), " ".join(record.get("code_scenarios", [])), str(record.get("reuse_target") or ""), " ".join(record.get("capability_terms", []))]
        )
        haystack = " ".join(
            [
                str(record.get("module") or ""),
                str(record.get("category") or ""),
                " ".join(record.get("roles", [])),
                str(record.get("purpose") or ""),
                str(record.get("search_text") or ""),
                *mode_fields,
            ]
        ).lower()
        mode_text = " ".join(mode_fields).lower()
        score = sum(
            3 if term in str(record.get("module") or "").lower() else 2 if term in mode_text else 1
            for term in terms
            if term in haystack
        )
        if score or not terms:
            matches.append({**record, "score": score, "match_terms": [term for term in terms if term in haystack]})
    if task_mode == "maintenance":
        matches.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("category") or ""), str(item.get("responsibility") or ""), str(item.get("module") or "")))
    else:
        matches.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("reuse_target") or ""), str(item.get("module") or "")))
    requested_limit = int(getattr(args, "limit", 12) or 12)
    bounded_limit = max(1, min(requested_limit, 200))
    selected = matches[:bounded_limit]
    return {
        "schema": "module_asset_catalog.lookup.v1",
        "ok": True,
        "generated_at": now_iso(),
        "catalog_path": str(CATALOG_PATH),
        "task_mode": task_mode,
        "mode_focus": catalog.get("task_mode_views", {}).get(task_mode, {}).get("focus", []),
        "terms": terms,
        "match_count": len(matches),
        "matches": [
            compact_catalog_record(item, task_mode=task_mode)
            for item in selected
        ],
        "output_budget": {
            "requested_limit": requested_limit,
            "effective_limit": bounded_limit,
            "returned_record_count": len(selected),
            "strict_total_record_limit": True,
        },
        "reuse_gate": [
            "maintenance mode: prefer matching system, responsibility, state behavior, owner CLI, and validator",
            "code mode: prefer matching role, scenario, reuse target, public entrypoint, and capability terms",
            "create a peer module only when system ownership, state behavior, or reuse fit would otherwise be ambiguous",
        ],
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    catalog = build_catalog(args)
    required_categories = {"workflow_orchestration", "resource_layer", "mcp_tooling", "system_diagnostics"}
    categories = set(catalog.get("category_counts", {}))
    task_views = catalog.get("task_mode_views", {}) if isinstance(catalog.get("task_mode_views"), dict) else {}
    maintenance_view = task_views.get("maintenance", {}) if isinstance(task_views.get("maintenance"), dict) else {}
    code_view = task_views.get("code", {}) if isinstance(task_views.get("code"), dict) else {}
    maintenance_lookup = lookup_catalog(
        argparse.Namespace(term=["resource", "validate"], task_mode="maintenance", rebuild=False, limit=3)
    )
    code_lookup = lookup_catalog(argparse.Namespace(term=["resource", "scheduler"], task_mode="code", rebuild=False, limit=3))
    checks = [
        {"name": "catalog_has_modules", "ok": int(catalog.get("module_count") or 0) > 0},
        {"name": "required_categories_present", "ok": required_categories.issubset(categories)},
        {"name": "catalog_written", "ok": CATALOG_PATH.exists()},
        {"name": "catalog_uses_full_index", "ok": catalog.get("source_scope") == "all_bridge"},
        {"name": "catalog_persists_all_records", "ok": len(catalog_records(catalog)) == int(catalog.get("module_count") or 0)},
        {"name": "catalog_not_abnormally_narrow", "ok": int(catalog.get("module_count") or 0) >= 100},
        {"name": "rules_present", "ok": bool(catalog.get("rules", {}).get("reuse_before_new_module"))},
        {"name": "maintenance_view_present", "ok": bool(maintenance_view.get("by_system")) and bool(maintenance_view.get("by_responsibility"))},
        {"name": "code_view_present", "ok": bool(code_view.get("by_role")) and bool(code_view.get("by_scenario"))},
        {"name": "maintenance_lookup_mode", "ok": maintenance_lookup.get("task_mode") == "maintenance" and int(maintenance_lookup.get("match_count") or 0) > 0},
        {"name": "code_lookup_mode", "ok": code_lookup.get("task_mode") == "code" and int(code_lookup.get("match_count") or 0) > 0},
    ]
    return {
        "schema": "module_asset_catalog.validate.v1",
        "ok": all(bool(item["ok"]) for item in checks),
        "checks": checks,
        "catalog_path": str(CATALOG_PATH),
        "module_count": catalog.get("module_count"),
        "category_counts": catalog.get("category_counts", {}),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query a categorized module asset catalog.")
    parser.add_argument("command", choices=("build", "lookup", "validate"))
    parser.add_argument("--term", action="append", help="Lookup term; can be repeated.")
    parser.add_argument("--task-mode", choices=("auto", "maintenance", "code"), default="auto")
    parser.add_argument("--rebuild", action="store_true", help="Refresh the underlying module capability index first.")
    parser.add_argument("--all-bridge", action="store_true", help="Scan all _bridge Python files when rebuilding.")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--group-limit", type=int, default=20)
    parser.add_argument("--index-limit", type=int, default=1000)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "build":
        payload = bounded_catalog_output(build_catalog(args), limit=args.limit, task_mode=args.task_mode)
    elif args.command == "lookup":
        payload = lookup_catalog(args)
    else:
        payload = validate(args)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
