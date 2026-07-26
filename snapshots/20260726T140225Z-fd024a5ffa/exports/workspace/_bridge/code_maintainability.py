#!/usr/bin/env python3
"""Read-only maintainability metrics for Codex-facing project code.

This is intentionally a scanner, not a formatter or refactor tool. It helps
Codex pick the next safe cleanup target by ranking file size, function size,
duplicate local helper names, and backup-file fanout in code directories.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from code_maintainability_toolchain import developer_toolchain_snapshot
from intent_routing import IntentRule, rank_intents
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


ROOT = Path(__file__).resolve().parents[1]
MODULE_CAPABILITY_INDEX = ROOT / "_bridge" / "runtime" / "module_capability_index.json"
MODULE_CAPABILITY_CORE_INDEX = ROOT / "_bridge" / "runtime" / "module_capability_index.core.json"
DEFAULT_ROOTS = [
    ROOT / "_bridge" / "code_maintainability.py",
    ROOT / "_bridge" / "code_maintainability_toolchain.py",
    ROOT / "_bridge" / "workflow_orchestrator.py",
    ROOT / "_bridge" / "workflow_plan_detail.py",
    ROOT / "_bridge" / "workflow_validation.py",
    ROOT / "_bridge" / "maintenance_upgrade_governance.py",
    ROOT / "_bridge" / "codex_workflow_entry.py",
    ROOT / "_bridge" / "mcp_capability_routes.py",
    ROOT / "_bridge" / "slash_command_governance.py",
    ROOT / "_bridge" / "skill_orchestrator.py",
    ROOT / "_bridge" / "memory_router.py",
    ROOT / "_bridge" / "memory_candidate_notes.py",
    ROOT / "_bridge" / "memory_governance.py",
    ROOT / "_bridge" / "draft_governance.py",
    ROOT / "_bridge" / "local_mcp_hub.py",
    ROOT / "_bridge" / "local_mcp_hub_routes.py",
    ROOT / "_bridge" / "mcp_session_doctor.py",
    ROOT / "_bridge" / "mcp_session_doctor_routes.py",
    ROOT / "_bridge" / "resource_broker.py",
    ROOT / "_bridge" / "resource_cli.py",
    ROOT / "_bridge" / "resource_cli_parser.py",
    ROOT / "_bridge" / "resource_fetcher.py",
    ROOT / "_bridge" / "resource_network_execution.py",
    ROOT / "_bridge" / "resource_owner_executor.py",
    ROOT / "_bridge" / "resource_package_owner.py",
    ROOT / "_bridge" / "resource_process_doctor.py",
    ROOT / "_bridge" / "resource_process_observations.py",
    ROOT / "_bridge" / "resource_route_rules.py",
    ROOT / "_bridge" / "resource_router.py",
    ROOT / "_bridge" / "resource_scheduler.py",
    ROOT / "_bridge" / "resource_store.py",
    ROOT / "_bridge" / "resource_strategy_review.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_openclaw_cli.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_maintenance.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_maintenance_probe_policy.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_dashboard.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "mobile_bridge_mcp_server.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "permission_policy.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "capability_tokens.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "capability_passphrase_text.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "final_reply_classification.py",
    ROOT / "_bridge" / "mobile_openclaw_bridge" / "reply_status_text.py",
    ROOT / "_bridge" / "shared" / "record_store_maintenance.py",
    ROOT / "_bridge" / "shared" / "backup_router.py",
]
ALL_BRIDGE_ROOTS = [ROOT / "_bridge"]
LOOKUP_STOP_TERMS = {
    "a",
    "an",
    "and",
    "by",
    "default",
    "for",
    "in",
    "include",
    "index",
    "module",
    "modules",
    "of",
    "on",
    "peer",
    "py",
    "python",
    "the",
    "to",
    "with",
}
BROAD_PLACEMENT_TERMS = {"workflow", "bridge", "governance", "maintenance"}
STRONG_PLACEMENT_TERMS = {
    "compact",
    "context",
    "context_budget",
    "detail",
    "permission",
    "plan",
    "process",
    "repair",
    "resource",
    "scheduler",
    "route",
    "state",
    "validation",
    "validate",
}
EXCLUDED_PARTS = {
    "__pycache__",
    ".git",
    ".codegraph",
    ".backups",
    ".venv",
    "venv",
    "venvs",
    "node_modules",
    "pnpm-store",
    "site-packages",
    "runtime",
    "logs",
    "archive",
    "backups",
    "_backup",
    "attachments",
    "tmp",
    "wheelhouse",
    "runtime_dependencies",
}
HELPER_NAMES = {
    "now_iso",
    "read_text",
    "write_text",
    "run_json",
    "run_json_command",
    "split_items",
    "split_csv",
    "compact_items",
    "parse_key_value_items",
    "configure_utf8_stdio",
}
DECISION_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.BoolOp,
    ast.IfExp,
    ast.Match,
)
def is_excluded(path: Path) -> bool:
    return any(part.lower() in EXCLUDED_PARTS for part in path.parts)


def iter_python_files(roots: list[Path], *, include_excluded: bool = False) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            files.append(root)
            continue
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if include_excluded or not is_excluded(path):
                files.append(path)
    return sorted(set(files))


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def normalize_module_path(value: Any) -> str:
    """Return a stable module identity across WSL and Windows separators."""
    return str(value or "").replace("\\", "/").lower()


def analyze_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError as exc:
        return {
            "path": rel(path),
            "ok": False,
            "line_count": len(lines),
            "error": f"SyntaxError: {exc}",
            "functions": [],
            "imports_shared_json_cli": "shared.json_cli" in text,
        }
    functions: list[dict[str, Any]] = []
    public_entrypoints: list[dict[str, Any]] = []
    imports: list[str] = []
    local_helpers: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            public_entrypoints.append(
                {
                    "name": node.name,
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "line": node.lineno,
                }
            )
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            decision_count = sum(1 for child in ast.walk(node) if isinstance(child, DECISION_NODES))
            item = {
                "name": node.name,
                "line": node.lineno,
                "line_count": end - node.lineno + 1,
                "decision_count": decision_count,
                "risk_score": (end - node.lineno + 1) + decision_count * 6,
            }
            functions.append(item)
            if node.name in HELPER_NAMES:
                local_helpers.append(node.name)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    functions.sort(key=lambda item: item["risk_score"], reverse=True)
    return {
        "path": rel(path),
        "ok": True,
        "line_count": len(lines),
        "function_count": len(functions),
        "max_function": functions[0] if functions else {"name": "", "line": 0, "line_count": 0, "decision_count": 0, "risk_score": 0},
        "large_functions": [item for item in functions if item["line_count"] >= 120 or item["decision_count"] >= 25][:10],
        "local_helpers": sorted(set(local_helpers)),
        "imports_shared_json_cli": any(item == "shared.json_cli" for item in imports),
        "module_docstring": ast.get_docstring(tree) or "",
        "public_entrypoints": public_entrypoints[:40],
    }


def backup_fanout(roots: list[Path]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    examples: defaultdict[str, list[str]] = defaultdict(list)
    patterns = ("*.bak", "*.bak-*", "*.bak.*", "*~")
    for root in roots:
        if not root.exists() or root.is_file():
            continue
        for pattern in patterns:
            for path in root.rglob(pattern):
                if is_excluded(path):
                    continue
                parent = rel(path.parent)
                counts[parent] += 1
                if len(examples[parent]) < 5:
                    examples[parent].append(path.name)
    return [
        {"directory": directory, "count": count, "examples": examples[directory]}
        for directory, count in counts.most_common(20)
    ]


def snapshot(args: argparse.Namespace) -> dict[str, Any]:
    default_roots = ALL_BRIDGE_ROOTS if args.all_bridge else DEFAULT_ROOTS
    roots = [Path(value).resolve() for value in (args.root or [str(path) for path in default_roots])]
    files = iter_python_files(roots, include_excluded=bool(getattr(args, "include_excluded", False)))
    analyses = [analyze_file(path) for path in files]
    helper_index: defaultdict[str, list[str]] = defaultdict(list)
    for item in analyses:
        for helper in item.get("local_helpers", []):
            helper_index[helper].append(item["path"])
    largest = sorted(analyses, key=lambda item: item.get("line_count", 0), reverse=True)[: args.limit]
    largest_functions = sorted(
        [
            {
                "path": item["path"],
                **item.get("max_function", {}),
            }
            for item in analyses
            if item.get("ok")
        ],
        key=lambda item: int(item.get("risk_score", 0)),
        reverse=True,
    )[: args.limit]
    duplicate_helpers = [
        {"helper": helper, "count": len(paths), "paths": paths[: args.limit]}
        for helper, paths in sorted(helper_index.items())
        if len(paths) >= 2
    ]
    issues: list[dict[str, Any]] = []
    for item in analyses:
        if item.get("line_count", 0) >= args.large_file_lines:
            issues.append({"severity": "risk", "code": "large_file", "path": item["path"], "line_count": item["line_count"]})
        max_function = item.get("max_function", {})
        if max_function.get("line_count", 0) >= args.large_function_lines or max_function.get("decision_count", 0) >= args.large_function_decisions:
            issues.append(
                {
                    "severity": "risk",
                    "code": "large_function",
                    "path": item["path"],
                    "function": max_function.get("name"),
                    "line": max_function.get("line"),
                    "line_count": max_function.get("line_count"),
                    "decision_count": max_function.get("decision_count"),
                    "risk_score": max_function.get("risk_score"),
                }
            )
    for item in duplicate_helpers:
        issues.append({"severity": "advisory", "code": "duplicate_helper", **item})
    fanout = backup_fanout(roots)
    for item in fanout:
        issues.append({"severity": "advisory", "code": "backup_fanout", **item})
    return {
        "schema": "code_maintainability.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "roots": [str(path) for path in roots],
        "scan_scope": "all_bridge" if args.all_bridge else ("custom" if args.root else "governance_default"),
        "file_count": len(files),
        "thresholds": {
            "large_file_lines": args.large_file_lines,
            "large_function_lines": args.large_function_lines,
            "large_function_decisions": args.large_function_decisions,
        },
        "largest_files": largest,
        "largest_functions": largest_functions,
        "duplicate_helpers": duplicate_helpers,
        "backup_fanout": fanout,
        "developer_toolchain": developer_toolchain_snapshot(),
        "issues": issues[: args.issue_limit],
    }


def validate(args: argparse.Namespace) -> dict[str, Any]:
    payload = snapshot(args)
    blockers = [item for item in payload["issues"] if item.get("severity") == "blocker"]
    toolchain = payload.get("developer_toolchain", {})
    toolchain_missing = toolchain.get("missing_required", []) if isinstance(toolchain, dict) else []
    placement_sample_args = argparse.Namespace(**vars(args))
    placement_sample_args.message = "给 local_mcp_hub 增加 reload 重载进程控制"
    placement_sample_args.target = "_bridge\\local_mcp_hub.py"
    placement_sample_args.term = ["local_mcp_hub", "reload", "process"]
    placement_sample_args.limit = max(int(getattr(args, "limit", 12) or 12), 8)
    placement_sample = placement_plan(placement_sample_args)
    placement_gate_ok = (
        placement_sample.get("recommended_placement") == "new_peer_module"
        and normalize_module_path(placement_sample.get("new_module_name")) == "_bridge/local_mcp_hub_process.py"
        and "process_lifecycle" == placement_sample.get("change_kind", {}).get("kind")
    )
    existing_detail_args = argparse.Namespace(**vars(args))
    existing_detail_args.message = "给 workflow_orchestrator 增加 detail 裁剪和 context_budget 输出"
    existing_detail_args.target = "_bridge\\workflow_orchestrator.py"
    existing_detail_args.term = []
    existing_detail_args.limit = max(int(getattr(args, "limit", 12) or 12), 8)
    existing_detail_sample = placement_plan(existing_detail_args)
    existing_detail_ok = (
        existing_detail_sample.get("recommended_placement") == "existing_purpose_module_or_owner_facade"
        and normalize_module_path(existing_detail_sample.get("existing_purpose", {}).get("match", {}).get("module"))
        == "_bridge/workflow_plan_detail.py"
    )
    explicit_target_args = argparse.Namespace(**vars(args))
    explicit_target_args.message = "include workflow peer modules in default module index"
    explicit_target_args.target = "_bridge\\code_maintainability.py"
    explicit_target_args.term = []
    explicit_target_args.limit = max(int(getattr(args, "limit", 12) or 12), 8)
    explicit_target_sample = placement_plan(explicit_target_args)
    explicit_target_ok = (
        explicit_target_sample.get("owner_module") == "_bridge\\code_maintainability.py"
        and explicit_target_sample.get("recommended_placement") == "owner_module"
    )
    resource_scheduler_args = argparse.Namespace(**vars(args))
    resource_scheduler_args.message = "优化资源层批量调度、网关接入、owner execution 和资源获取策略"
    resource_scheduler_args.target = ""
    resource_scheduler_args.term = ["resource", "scheduler"]
    resource_scheduler_args.limit = max(int(getattr(args, "limit", 12) or 12), 8)
    resource_scheduler_sample = placement_plan(resource_scheduler_args)
    resource_scheduler_ok = (
        normalize_module_path(resource_scheduler_sample.get("owner_module")) == "_bridge/resource_scheduler.py"
        and normalize_module_path(resource_scheduler_sample.get("existing_purpose", {}).get("match", {}).get("module"))
        == "_bridge/resource_scheduler.py"
    )
    skill_boundary_args = argparse.Namespace(**vars(args))
    skill_boundary_args.message = "新增共享意图与 skill 路由模块，修复关键词分流"
    skill_boundary_args.target = ""
    skill_boundary_args.term = []
    skill_boundary_args.limit = max(int(getattr(args, "limit", 12) or 12), 8)
    skill_boundary_sample = placement_plan(skill_boundary_args)
    skill_boundary_ok = skill_boundary_sample.get("change_kind", {}).get("kind") != "process_lifecycle"
    try:
        catalog_proc = subprocess.run(
            [sys.executable, str(ROOT / "_bridge" / "module_asset_catalog.py"), "validate"],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=90,
        )
        catalog_validate = json.loads(catalog_proc.stdout or "{}")
        if not isinstance(catalog_validate, dict):
            catalog_validate = {"ok": False, "error": "non_dict_payload"}
        catalog_validate.setdefault("ok", catalog_proc.returncode == 0)
    except Exception as exc:
        catalog_validate = {"ok": False, "error_class": type(exc).__name__, "error": str(exc)[:500]}
    module_asset_catalog_ok = bool(catalog_validate.get("ok"))
    placement_checks_ok = placement_gate_ok and existing_detail_ok and explicit_target_ok and resource_scheduler_ok and skill_boundary_ok
    return {
        "schema": "code_maintainability.validate.v1",
        "ok": not blockers and not toolchain_missing and placement_checks_ok and module_asset_catalog_ok,
        "generated_at": now_iso(),
        "snapshot_ok": payload["ok"],
        "developer_toolchain_ok": not toolchain_missing,
        "developer_toolchain": toolchain,
        "module_asset_catalog_ok": module_asset_catalog_ok,
        "module_asset_catalog": catalog_validate,
        "placement_gate_ok": placement_checks_ok,
        "placement_gate_sample": {
            "process_peer": {
                "ok": placement_gate_ok,
                "recommended_placement": placement_sample.get("recommended_placement"),
                "new_module_name": placement_sample.get("new_module_name"),
                "change_kind": placement_sample.get("change_kind", {}).get("kind"),
            },
            "existing_purpose_detail": {
                "ok": existing_detail_ok,
                "recommended_placement": existing_detail_sample.get("recommended_placement"),
                "existing_module": existing_detail_sample.get("existing_purpose", {}).get("match", {}).get("module"),
            },
            "explicit_target_priority": {
                "ok": explicit_target_ok,
                "owner_module": explicit_target_sample.get("owner_module"),
                "recommended_placement": explicit_target_sample.get("recommended_placement"),
            },
            "resource_scheduler_route": {
                "ok": resource_scheduler_ok,
                "owner_module": resource_scheduler_sample.get("owner_module"),
                "existing_module": resource_scheduler_sample.get("existing_purpose", {}).get("match", {}).get("module"),
                "change_kind": resource_scheduler_sample.get("change_kind", {}).get("kind"),
            },
            "skill_does_not_match_kill": {
                "ok": skill_boundary_ok,
                "change_kind": skill_boundary_sample.get("change_kind", {}).get("kind"),
                "keyword_hits": skill_boundary_sample.get("change_kind", {}).get("keyword_hits", []),
            },
        },
        "issue_count": len(payload["issues"]),
        "blockers": blockers,
        "top_risks": [item for item in payload["issues"] if item.get("severity") == "risk"][: args.limit],
        "refactor_plan_available": True,
    }


def boundary_for_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    if "mcp_session" in normalized or "local_mcp_hub" in normalized or "resource_process" in normalized:
        return "tooling_mcp_runtime"
    if "memory" in normalized or "skill" in normalized:
        return "knowledge_memory_skill"
    if "workflow" in normalized or "capability_routes" in normalized or "slash" in normalized:
        return "workflow_orchestration"
    if "record_store" in normalized or "backup" in normalized:
        return "resource_and_backup_governance"
    return "general_bridge"


def module_purpose_for_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    stem = Path(normalized).stem
    if stem.endswith("_doctor"):
        return "diagnostic_entrypoint"
    if stem.endswith("_maintenance"):
        return "maintenance_entrypoint"
    if stem.endswith("_governance"):
        return "policy_workflow_entrypoint"
    if stem.endswith("_analysis"):
        return "read_only_analysis"
    if stem.endswith("_specs"):
        return "static_specs_or_route_tables"
    if "workflow_orchestrator" in normalized:
        return "workflow_routing"
    if "code_maintainability" in normalized:
        return "code_module_context_and_refactor_planning"
    if "capability_routes" in normalized:
        return "tool_route_index"
    if "memory_router" in normalized:
        return "task_fit_memory_layer_routing"
    if "memory_" in normalized:
        return "memory_governance_component"
    if "resource_" in normalized:
        return "resource_runtime_component"
    if "/shared/" in normalized:
        return "cross_domain_shared_primitive"
    return "domain_service_or_cli_component"


def state_behavior_for_path(path: str) -> str:
    normalized = path.replace("\\", "/").lower()
    if any(marker in normalized for marker in ("doctor", "analysis", "snapshot", "orchestrator", "code_maintainability")):
        return "read_only_by_default"
    if any(marker in normalized for marker in ("backup_router", "maintenance", "governance")):
        return "mixed_read_plan_write_with_explicit_command"
    return "inspect_before_write"


def owner_cli_for_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.endswith(".py"):
        return normalized
    return ""


def module_route_for_analysis(item: dict[str, Any]) -> dict[str, Any]:
    path = str(item.get("path") or "")
    return {
        "module": path,
        "purpose": module_purpose_for_path(path),
        "boundary": boundary_for_path(path),
        "owner_cli": owner_cli_for_path(path),
        "state_behavior": state_behavior_for_path(path),
        "normal_callers": [],
        "do_not_place": [
            "unrelated_domain_logic",
            "permission_bypass",
            "state_writes_without_explicit_apply_path",
        ],
        "validation": verification_for({"path": path}),
        "line_count": item.get("line_count"),
        "max_function": item.get("max_function"),
        "public_entrypoints": item.get("public_entrypoints", []),
        "large_file_risk": int(item.get("line_count") or 0) >= 1200,
        "module_docstring_required_for_new_peer": True,
    }


def compact_module_context_route(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "module": route.get("module"),
        "purpose": route.get("purpose"),
        "boundary": route.get("boundary"),
        "owner_cli": route.get("owner_cli"),
        "state_behavior": route.get("state_behavior"),
        "validation": list(route.get("validation") or [])[:3],
        "line_count": route.get("line_count"),
        "max_function": route.get("max_function"),
        "public_entrypoints": [
            {
                "name": item.get("name"),
                "kind": item.get("kind"),
                "line": item.get("line"),
            }
            for item in list(route.get("public_entrypoints") or [])[:6]
            if isinstance(item, dict)
        ],
        "issues": [
            {
                key: issue.get(key)
                for key in ("kind", "path", "line", "name", "detail", "severity")
                if issue.get(key) not in (None, "", [])
            }
            for issue in list(route.get("issues") or [])[:4]
            if isinstance(issue, dict)
        ],
        "large_file_risk": bool(route.get("large_file_risk")),
    }


def capability_terms_for_route(route: dict[str, Any]) -> list[str]:
    text_parts = [
        str(route.get("module") or ""),
        str(route.get("purpose") or ""),
        str(route.get("boundary") or ""),
        str(route.get("state_behavior") or ""),
    ]
    for entrypoint in route.get("public_entrypoints", []):
        if isinstance(entrypoint, dict):
            text_parts.append(str(entrypoint.get("name") or ""))
    raw = " ".join(text_parts).replace("\\", "/").replace("-", "_")
    tokens: set[str] = set()
    for chunk in raw.lower().replace("/", " ").replace(".", " ").split():
        for part in chunk.split("_"):
            if len(part) >= 3:
                tokens.add(part)
        if len(chunk) >= 3:
            tokens.add(chunk)
    return sorted(tokens)


def module_capability_for_analysis(item: dict[str, Any]) -> dict[str, Any]:
    route = module_route_for_analysis(item)
    route["capability_terms"] = capability_terms_for_route(route)
    route["reuse_policy"] = {
        "prefer": "reuse_or_extend_when_boundary_and_state_behavior_match",
        "create_new_module_when": [
            "existing_module_boundary_would_be_polluted",
            "state_write_semantics_are_different",
            "validation_owner_would_become_ambiguous",
        ],
        "do_not_use_for": route["do_not_place"],
    }
    return route


def build_module_index(args: argparse.Namespace) -> dict[str, Any]:
    default_roots = ALL_BRIDGE_ROOTS if args.all_bridge else DEFAULT_ROOTS
    roots = [Path(value).resolve() for value in (args.root or [str(path) for path in default_roots])]
    files = iter_python_files(roots, include_excluded=bool(getattr(args, "include_excluded", False)))
    analyses = [analyze_file(path) for path in files]
    modules = [
        module_capability_for_analysis(item)
        for item in analyses
        if isinstance(item, dict) and item.get("ok") and item.get("path")
    ]
    modules.sort(key=lambda item: (str(item.get("boundary") or ""), str(item.get("module") or "")))
    index_path = MODULE_CAPABILITY_INDEX if args.all_bridge else MODULE_CAPABILITY_CORE_INDEX
    index_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "schema": "code_maintainability.module_capability_index.v1",
        "ok": True,
        "generated_at": now_iso(),
        "source": {
            "kind": "derived_runtime_cache",
            "source_of_truth": ["python_source", "_bridge/docs/code_maintainability_guidelines.md"],
            "scan_scope": "all_bridge" if args.all_bridge else ("custom" if args.root else "governance_default"),
            "roots": [str(path) for path in roots],
            "file_count": len(modules),
        },
        "rules": {
            "lookup_before_non_simple_code_edits": True,
            "reuse_first": True,
            "new_module_requires_reason": True,
            "index_is_not_source_of_truth": True,
            "display_limit_does_not_truncate_persisted_index": True,
        },
        "modules": modules,
    }
    index_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "schema": "code_maintainability.build_module_index.v1",
        "ok": True,
        "generated_at": result["generated_at"],
        "index_path": str(index_path),
        "source": result["source"],
        "module_count": len(result["modules"]),
        "rules": result["rules"],
        "sample_modules": [item.get("module") for item in result["modules"][: max(1, min(args.limit, 8))]],
    }


def load_module_index(*, prefer_full: bool = True) -> dict[str, Any]:
    index_path = MODULE_CAPABILITY_INDEX if prefer_full and MODULE_CAPABILITY_INDEX.exists() else MODULE_CAPABILITY_CORE_INDEX
    if not index_path.exists():
        return {
            "ok": False,
            "schema": "code_maintainability.module_lookup.v1",
            "reason": "module_capability_index_missing",
            "index_path": str(index_path),
            "build_command": "python _bridge\\code_maintainability.py build-module-index --all-bridge --limit 1000",
        }
    return json.loads(index_path.read_text(encoding="utf-8"))


def lookup_module(args: argparse.Namespace) -> dict[str, Any]:
    data = load_module_index()
    if not data.get("ok"):
        return data
    terms = [str(item).strip().lower() for item in (args.term or []) if str(item).strip()]
    if not terms:
        terms = ["bridge", "workflow", "maintenance"]
    matches: list[dict[str, Any]] = []
    for module in data.get("modules", []):
        if not isinstance(module, dict):
            continue
        haystack_items = [
            str(module.get("module") or ""),
            str(module.get("purpose") or ""),
            str(module.get("boundary") or ""),
            " ".join(module.get("capability_terms", [])),
        ]
        for entrypoint in module.get("public_entrypoints", []):
            if isinstance(entrypoint, dict):
                haystack_items.append(str(entrypoint.get("name") or ""))
        haystack = " ".join(haystack_items).lower()
        score = sum(3 if term in str(module.get("module") or "").lower() else 1 for term in terms if term in haystack)
        if score:
            matches.append(
                {
                    "score": score,
                    "module": module.get("module"),
                    "purpose": module.get("purpose"),
                    "boundary": module.get("boundary"),
                    "state_behavior": module.get("state_behavior"),
                    "owner_cli": module.get("owner_cli"),
                    "validation": module.get("validation", [])[:4],
                    "public_entrypoints": module.get("public_entrypoints", [])[:6],
                    "reuse_policy": module.get("reuse_policy"),
                    "match_terms": [term for term in terms if term in haystack],
                }
            )
    matches.sort(key=lambda item: (-int(item["score"]), str(item.get("module") or "")))
    requested_limit = int(args.limit or 12)
    bounded_limit = max(1, min(requested_limit, 200))
    selected = matches[:bounded_limit]
    return {
        "schema": "code_maintainability.module_lookup.v1",
        "ok": True,
        "generated_at": now_iso(),
        "index_path": str(MODULE_CAPABILITY_INDEX if MODULE_CAPABILITY_INDEX.exists() else MODULE_CAPABILITY_CORE_INDEX),
        "terms": terms,
        "match_count": len(matches),
        "matches": selected,
        "output_budget": {
            "requested_limit": requested_limit,
            "effective_limit": bounded_limit,
            "returned_record_count": len(selected),
            "strict_total_record_limit": True,
            "per_record_entrypoint_limit": 6,
        },
        "reuse_gate": [
            "use_existing_module_if_boundary_state_and_validation_match",
            "extend_existing_module_when_new_code_shares_owner_and_non_goals",
            "create_new_module_only_with_boundary_reason_and_validation_owner",
        ],
    }


def module_context(args: argparse.Namespace) -> dict[str, Any]:
    payload = snapshot(args)
    routes = [
        module_route_for_analysis(item)
        for item in payload.get("largest_files", [])
        if isinstance(item, dict) and item.get("path")
    ]
    issues_by_path: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in payload.get("issues", []):
        path = str(issue.get("path") or "")
        if not path and isinstance(issue.get("paths"), list) and issue["paths"]:
            path = str(issue["paths"][0])
        if path:
            issues_by_path[path].append(issue)
    for route in routes:
        route["issues"] = issues_by_path.get(str(route.get("module") or ""), [])
    code_task_terms = [str(item).strip().lower() for item in (args.term or []) if str(item).strip()]
    matched_routes = routes
    if code_task_terms:
        matched_routes = [
            route for route in routes
            if any(term in str(route.get("module") or "").lower() or term in str(route.get("purpose") or "").lower() for term in code_task_terms)
        ] or routes
        matched_routes = sorted(matched_routes, key=lambda route: (-route_match_score(route, code_task_terms), str(route.get("module") or "")))
    bounded_limit = max(1, min(int(args.limit or 12), 200))
    selected_routes = [compact_module_context_route(route) for route in matched_routes[:bounded_limit]]
    return {
        "schema": "code_maintainability.module_context.v1",
        "ok": True,
        "generated_at": now_iso(),
        "scan_scope": payload.get("scan_scope"),
        "terms": code_task_terms,
        "route_count": len(matched_routes),
        "routes": selected_routes,
        "output_budget": {
            "requested_limit": bounded_limit,
            "returned_record_count": len(selected_routes),
            "strict_total_record_limit": True,
            "per_route_entrypoint_limit": 6,
            "per_route_issue_limit": 4,
        },
        "module_gate": [
            "classify_current_and_target_module_purpose",
            "choose_module_asset_task_mode_maintenance_or_code",
            "use_module_assets_view_before_lookup_when_module_count_or_owner_boundary_is_ambiguous",
            "choose_function_coupled_module_name_before_writing_code",
            "preserve_existing_entrypoints_as_facades_until_validation_passes",
            "move_one_responsibility_per_patch",
            "validate_with_owner_cli_or_smallest_doctor",
            "record_module_route_change_at_closeout",
        ],
        "workflow_integration": {
            "pre_edit_use": "run module-context before non-simple code edits",
            "lookup_use": "run module-assets for categorized reuse view, then lookup-module against the derived module capability index before creating a new module",
            "task_mode_rule": "maintenance tasks use module-assets --task-mode maintenance; code implementation tasks use --task-mode code; auto is acceptable only when task intent is clear from terms",
            "index_path": str(MODULE_CAPABILITY_INDEX),
            "fast_index_path": str(MODULE_CAPABILITY_CORE_INDEX),
            "build_index": "python _bridge\\code_maintainability.py build-module-index --all-bridge --limit 1000",
            "build_asset_catalog": "python _bridge\\code_maintainability.py module-assets --all-bridge --limit 1000",
            "fast_build_index": "python _bridge\\code_maintainability.py build-module-index --limit 500",
            "lookup_asset_catalog": "python _bridge\\code_maintainability.py module-assets --term <domain> --term <capability>",
            "lookup_index": "python _bridge\\code_maintainability.py lookup-module --term <domain> --term <capability>",
            "edit_rule": "modify the owning module or create a purpose-owned peer; avoid miscellaneous buckets",
            "closeout_rule": "record changed module purpose, facade preservation, and validation evidence",
        },
        "external_design_basis": [
            "building_block_view_for_static_module_map",
            "branch_by_abstraction_for_incremental_facades",
            "adr_for_stable_boundary_decisions",
            "python_import_boundary_awareness",
        ],
    }


CHANGE_KIND_RULES = [
    (
        "process_lifecycle",
        ("process", "reload", "restart", "start", "stop", "kill", "task", "scheduled", "进程", "重载", "重启", "启动", "停止", "计划任务"),
        "new_peer_module",
    ),
    (
        "permission_boundary",
        ("permission", "token", "capability", "grant", "auth", "secret", "权限", "令牌", "授权", "口令", "密钥"),
        "new_peer_module",
    ),
    (
        "state_query",
        ("state", "queue", "sqlite", "receipt", "inbox", "outbox", "状态", "队列", "回执", "入队", "数据库"),
        "owner_or_existing_query_module",
    ),
    (
        "repair_plan",
        ("repair", "doctor", "validate", "cleanup", "治理", "修复", "体检", "清理", "验证"),
        "owner_or_existing_maintenance_module",
    ),
    (
        "tool_route",
        ("mcp", "hub", "gateway", "tool", "route", "browser", "devtools", "工具", "路由", "浏览器"),
        "owner_or_existing_route_module",
    ),
    (
        "resource_acquisition",
        (
            "resource",
            "resource_layer",
            "resource_cli",
            "resource_broker",
            "resource_scheduler",
            "scheduler",
            "fetcher",
            "batch",
            "acquire",
            "资源",
            "资源层",
            "资源请求",
            "调度",
            "批量",
            "获取资源",
        ),
        "owner_or_existing_route_module",
    ),
    (
        "static_specs",
        ("schema", "spec", "matrix", "table", "docs", "规则", "矩阵", "文档", "表"),
        "owner_module",
    ),
]


def classify_change_kind(message: str, terms: list[str]) -> dict[str, Any]:
    haystack = " ".join([message, *terms]).lower()
    placements = {kind: default_placement for kind, _keywords, default_placement in CHANGE_KIND_RULES}
    ranked = rank_intents(haystack, tuple(IntentRule(kind, tuple(keywords)) for kind, keywords, _placement in CHANGE_KIND_RULES))
    scores = [
        {
            "kind": str(item["key"]),
            "score": int(item["score"]),
            "hits": list(item["hits"])[:8],
            "suppressed_negated_hits": list(item["suppressed_negated_hits"]),
            "default_placement": placements[str(item["key"])],
        }
        for item in ranked
    ]
    if ranked:
        top = scores[0]
        return {
            "kind": top["kind"],
            "confidence": float(ranked[0]["route_confidence"]),
            "keyword_hits": top["hits"],
            "default_placement": top["default_placement"],
            "candidates": scores[:4],
        }
    return {
        "kind": "business_logic",
        "confidence": 0.25,
        "keyword_hits": [],
        "default_placement": "owner_module",
        "candidates": [],
    }


def module_name_for_target(target_path: str, change_kind: str) -> str:
    target = Path(target_path.replace("\\", "/"))
    stem = target.stem if target.suffix else str(target.name or "module")
    suffix = {
        "process_lifecycle": "process",
        "permission_boundary": "policy",
        "state_query": "state",
        "repair_plan": "maintenance",
        "resource_acquisition": "resource",
        "tool_route": "routes",
        "static_specs": "specs",
    }.get(change_kind, "service")
    if stem.endswith(f"_{suffix}"):
        return str(target)
    return str(target.with_name(f"{stem}_{suffix}.py"))


def unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        normalized = str(item or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def target_route(target: str, routes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not target:
        return None
    normalized = normalize_module_path(target)
    for route in routes:
        module = normalize_module_path(route.get("module"))
        if module.endswith(normalized) or normalized.endswith(module):
            return route
    return None


def route_match_score(route: dict[str, Any], terms: list[str]) -> int:
    if not terms:
        return 0
    haystack = " ".join(
        [
            str(route.get("module") or ""),
            str(route.get("purpose") or ""),
            str(route.get("boundary") or ""),
            " ".join(str(term) for term in route.get("capability_terms", []) or []),
        ]
    ).lower()
    module = str(route.get("module") or "").replace("\\", "/").lower()
    score = 0
    for term in unique_preserve_order(terms):
        if term in haystack:
            score += 1
        if term in module:
            score += 2
    if "scheduler" in terms and "resource_scheduler.py" in module:
        score += 5
    if "broker" in terms and "resource_broker.py" in module:
        score += 5
    if "owner" in terms and "resource_owner_executor.py" in module:
        score += 5
    if "gateway" in terms and "resource_network_execution.py" in module:
        score += 4
    if "process" not in terms and "resource_process" in module:
        score -= 2
    return score


def select_route_for_terms(routes: list[dict[str, Any]], terms: list[str]) -> dict[str, Any] | None:
    if not routes:
        return None
    if not terms:
        return routes[0]
    ranked = sorted(routes, key=lambda route: (-route_match_score(route, terms), str(route.get("module") or "")))
    return ranked[0]


def split_lookup_terms(text: str) -> list[str]:
    terms = re.findall(r"[A-Za-z0-9_]+", str(text or "").lower())
    if "资源" in text or "resource" in text:
        terms.extend(["resource", "resource_runtime_component"])
    if "调度" in text or "scheduler" in text:
        terms.append("scheduler")
    if "批量" in text or "batch" in text:
        terms.append("batch")
    if "上下文" in text or "context" in text:
        terms.extend(["context_budget", "detail"])
    if "裁剪" in text or "压缩" in text or "精简" in text or "compact" in text:
        terms.extend(["detail", "compact"])
    if "验证" in text or "validate" in text or "validation" in text:
        terms.append("validation")
    if "工作流" in text or "workflow" in text:
        terms.append("workflow")
    return [term for term in terms if term and term not in LOOKUP_STOP_TERMS]


def target_terms(target: str) -> set[str]:
    path = Path(str(target or "").replace("\\", "/"))
    return set(split_lookup_terms(path.stem))


def lookup_existing_purpose_module(args: argparse.Namespace, target: str) -> dict[str, Any]:
    explicit_terms = [str(item).strip().lower() for item in (getattr(args, "term", None) or []) if str(item).strip()]
    message_terms = split_lookup_terms(str(getattr(args, "message", "") or ""))
    semantic_terms = unique_preserve_order([*explicit_terms, *message_terms])
    if not semantic_terms:
        return {"ok": False, "reason": "no_semantic_terms"}

    lookup_args = argparse.Namespace(term=semantic_terms, limit=6)
    lookup = lookup_module(lookup_args)
    if not lookup.get("ok"):
        return {"ok": False, "reason": lookup.get("reason", "lookup_failed"), "lookup": lookup}

    normalized_target = normalize_module_path(target)
    target_word_set = target_terms(target)
    for match in lookup.get("matches", []):
        module = normalize_module_path(match.get("module"))
        if normalized_target and (module.endswith(normalized_target) or normalized_target.endswith(module)):
            continue
        matched_terms = set(str(term) for term in match.get("match_terms", []))
        semantic_match_terms = sorted(matched_terms - target_word_set)
        if "scheduler" in semantic_terms and "scheduler" not in module and "scheduler" not in matched_terms:
            continue
        if "broker" in semantic_terms and "broker" not in module and "broker" not in matched_terms:
            continue
        if "gateway" in semantic_terms and "network" not in module and "gateway" not in matched_terms:
            continue
        strong_terms = sorted(set(semantic_match_terms) & STRONG_PLACEMENT_TERMS)
        broad_only = bool(semantic_match_terms) and not strong_terms and matched_terms <= BROAD_PLACEMENT_TERMS
        if int(match.get("score") or 0) >= 2 and strong_terms and not broad_only:
            return {
                "ok": True,
                "match": match,
                "lookup_terms": semantic_terms,
                "semantic_match_terms": semantic_match_terms,
                "strong_match_terms": strong_terms,
                "rule": "existing purpose module should be reused before adding independent logic to a large owner file",
            }
    return {"ok": False, "reason": "no_existing_purpose_match", "lookup_terms": semantic_terms}


def placement_plan(args: argparse.Namespace) -> dict[str, Any]:
    terms = [str(item).strip().lower() for item in (args.term or []) if str(item).strip()]
    message = str(getattr(args, "message", "") or "")
    target = str(getattr(args, "target", "") or "")
    context_args = argparse.Namespace(**vars(args))
    context_args.limit = max(int(getattr(args, "limit", 12) or 12), 12)
    context = module_context(context_args)
    routes = [route for route in context.get("routes", []) if isinstance(route, dict)]
    route_terms = unique_preserve_order([*terms, *split_lookup_terms(message)])
    selected_route = target_route(target, routes) if target else select_route_for_terms(routes, route_terms)
    change = classify_change_kind(message, terms)
    owner_module = str((selected_route or {}).get("module") or target or "")
    large_file_risk = bool((selected_route or {}).get("large_file_risk"))
    default_placement = str(change["default_placement"])
    independent_boundary_kinds = {"process_lifecycle", "permission_boundary", "state_query", "repair_plan", "tool_route", "resource_acquisition"}
    existing_purpose = lookup_existing_purpose_module(args, target)
    if not target and existing_purpose.get("ok"):
        match = existing_purpose.get("match") if isinstance(existing_purpose.get("match"), dict) else {}
        owner_module = str(match.get("module") or owner_module)
        if match:
            selected_route = {**match, "large_file_risk": False, "issues": []}
            large_file_risk = False
    reasons: list[str] = []
    if large_file_risk:
        reasons.append("target_has_large_file_risk")
    if change["kind"] in independent_boundary_kinds:
        reasons.append(f"change_kind_has_independent_boundary:{change['kind']}")
    if default_placement == "new_peer_module":
        reasons.append("default_for_change_kind_is_new_peer_module")
    if existing_purpose.get("ok"):
        reasons.append(f"existing_purpose_module:{existing_purpose.get('match', {}).get('module')}")
    recommended = "owner_module"
    if default_placement.startswith("owner_or_existing"):
        recommended = "existing_purpose_module_or_owner_facade"
    if existing_purpose.get("ok"):
        recommended = "existing_purpose_module_or_owner_facade"
    if large_file_risk and change["kind"] in independent_boundary_kinds:
        recommended = "new_peer_module"
    if default_placement == "new_peer_module":
        recommended = "new_peer_module"
    new_module_name = module_name_for_target(owner_module, str(change["kind"])) if owner_module and recommended == "new_peer_module" else ""
    facade_required = recommended in {"new_peer_module", "existing_purpose_module_or_owner_facade"}
    stop_conditions = [
        "do_not_place_independent_lifecycle_or_permission_logic_in_large_owner_file",
        "do_not_create_new_module_without_docstring_owner_non_goals_state_behavior",
        "do_not_bypass_existing_permission_or_maintenance_boundary",
    ]
    return {
        "schema": "code_maintainability.placement_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "message": message,
        "terms": terms,
        "target": target,
        "change_kind": change,
        "owner_module": owner_module,
        "owner_route": selected_route or {},
        "existing_purpose": existing_purpose,
        "recommended_placement": recommended,
        "new_module_name": new_module_name,
        "facade_required": facade_required,
        "why_not_owner_file": reasons,
        "allowed_owner_file_work": [
            "thin_cli_or_mcp_facade",
            "routing_table_registration",
            "readback_or_validation_hook",
        ],
        "new_module_contract": {
            "module_docstring_required": True,
            "docstring_fields": ["ownership", "non_goals", "state_behavior", "caller_context"],
            "must_have_validation_owner": True,
        },
        "validation": verification_for({"path": owner_module}) if owner_module else ["python -m py_compile <changed-files>", "python _bridge\\code_maintainability.py validate"],
        "stop_conditions": stop_conditions,
        "rule": "placement-plan is a pre-edit gate; do not write into a large owner file first and extract later when this plan recommends a peer module.",
    }


def refactor_technique(issue: dict[str, Any]) -> str:
    code = issue.get("code")
    if code == "large_function":
        return "extract_function_then_extract_module"
    if code == "large_file":
        return "strangler_module_extraction"
    if code == "duplicate_helper":
        return "move_to_shared_json_cli"
    if code == "backup_fanout":
        return "route_backups_through_backup_router"
    return "focused_cleanup"


def verification_for(issue: dict[str, Any]) -> list[str]:
    path = str(issue.get("path") or "")
    checks = ["python -m py_compile <changed-files>", "python _bridge\\code_maintainability.py validate"]
    if "workflow_orchestrator" in path:
        checks.append("python _bridge\\workflow_orchestrator.py validate")
    if "mcp_session" in path or "local_mcp_hub" in path:
        checks.extend(["python _bridge\\mcp_session_doctor.py validate", "python _bridge\\local_mcp_hub.py validate"])
    if "memory" in path:
        checks.append("python _bridge\\memory_governance.py validate")
    if "memory_router" in path:
        checks.append("python _bridge\\memory_router.py validate")
    if "resource_process" in path:
        checks.append("python _bridge\\resource_process_doctor.py doctor")
    return checks


def refactor_plan(args: argparse.Namespace) -> dict[str, Any]:
    payload = snapshot(args)
    candidates: list[dict[str, Any]] = []
    for issue in payload["issues"]:
        code = issue.get("code")
        if code not in {"large_function", "large_file", "duplicate_helper", "backup_fanout"}:
            continue
        paths = issue.get("paths") if isinstance(issue.get("paths"), list) else []
        path = str(issue.get("path") or (paths[0] if paths else ""))
        priority = "p1"
        if code == "large_function" and int(issue.get("risk_score") or 0) >= 260:
            priority = "p0"
        if code in {"duplicate_helper", "backup_fanout"}:
            priority = "p2"
        candidates.append(
            {
                "priority": priority,
                "code": code,
                "boundary": boundary_for_path(path),
                "target": issue,
                "technique": refactor_technique(issue),
                "recommended_shape": {
                    "mode": "incremental",
                    "rule": "create or extend a purpose-owned module first, redirect one call path, validate, then repeat",
                    "module_gate": [
                        "classify_current_and_target_module_purpose",
                        "choose_function_coupled_module_name_before_writing_code",
                        "add_module_docstring_with_ownership_non_goals_state_behavior_and_callers",
                        "place_module_in_purpose_category_not_misc_bucket",
                        "prefer_reuse_or_clear_future_work_reduction_over_size_only_split",
                        "require_governance_upgrade_reuse_or_validation_value_not_line_count_only",
                        "keep_existing_entry_points_as_facades_until_validation_passes",
                        "move_one_responsibility_per_patch",
                        "update_maintenance_map_or_capability_table_when_new_module_becomes_stable_entry",
                    ],
                    "purpose_categories": [
                        "shared_utility",
                        "domain_service",
                        "adapter",
                        "schema_or_tool_spec_table",
                        "cli_wrapper",
                        "validator_or_doctor",
                        "repair_planner",
                        "persistence_boundary",
                    ],
                    "naming_rule": "module_name_must_describe_function; avoid utils_helpers_common_misc unless truly shared primitive",
                    "docstring_rule": "new purpose modules must document owns, non_goals, reads_or_writes_state, and normal_callers",
                    "avoid": ["big_bang_rewrite", "cross_boundary_cleanup_without_tests", "format_only_churn"],
                },
                "approval_required_for_apply": True,
                "validation": verification_for(issue),
            }
        )
    candidates.sort(key=lambda item: (item["priority"], item["boundary"], item["code"]))
    return {
        "schema": "code_maintainability.refactor_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "source_snapshot": {
            "scan_scope": payload["scan_scope"],
            "file_count": payload["file_count"],
            "issue_count": len(payload["issues"]),
        },
        "principles": [
            "local_python_style_snake_case_pascal_case_upper_snake_case",
            "meaningful_boolean_names",
            "single_responsibility_for_new_or_touched_code",
            "guard_clauses_over_deep_nesting",
            "metric_guided_hotspots",
            "incremental_strangler_extraction",
            "purpose_based_module_boundaries",
            "function_coupled_module_names",
            "module_docstring_ownership_boundary",
            "concentrated_module_category_layout",
            "module_extraction_must_reduce_future_repeated_work",
            "module_extraction_must_improve_governance_upgrade_reuse_or_validation",
            "behavior_preserving_refactor",
            "small_validation_loop_before_and_after",
            "shared_helpers_only_after_real_duplication",
            "preserve_permission_and_maintenance_boundaries",
        ],
        "candidates": candidates[: args.limit],
        "next_safe_step": candidates[0] if candidates else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only code maintainability metrics")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "validate", "plan", "module-context", "placement-plan", "build-module-index", "lookup-module", "module-assets", "toolchain"):
        child = sub.add_parser(command)
        child.add_argument("--root", action="append", help="Root file/directory to scan. Defaults to _bridge.")
        child.add_argument("--all-bridge", action="store_true", help="Scan all _bridge Python files. Slower; use for deeper audits.")
        child.add_argument("--include-excluded", action="store_true", help="Include vendored/runtime/cache roots for an explicitly requested environment audit.")
        child.add_argument("--term", action="append", help="Optional module/purpose term to prioritize in module-context.")
        child.add_argument("--task-mode", choices=("auto", "maintenance", "code"), default="auto", help="Module asset lookup view: maintenance groups by system/responsibility; code groups by role/scenario.")
        child.add_argument("--message", default="", help="Task or change description for placement-plan.")
        child.add_argument("--target", default="", help="Candidate target file for placement-plan.")
        child.add_argument("--limit", type=int, default=12)
        child.add_argument("--issue-limit", type=int, default=80)
        child.add_argument("--large-file-lines", type=int, default=1200)
        child.add_argument("--large-function-lines", type=int, default=160)
        child.add_argument("--large-function-decisions", type=int, default=30)
        child.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args()
    if args.command == "validate":
        payload = validate(args)
    elif args.command == "plan":
        payload = refactor_plan(args)
    elif args.command == "module-context":
        payload = module_context(args)
    elif args.command == "placement-plan":
        payload = placement_plan(args)
    elif args.command == "build-module-index":
        payload = build_module_index(args)
    elif args.command == "lookup-module":
        payload = lookup_module(args)
    elif args.command == "module-assets":
        from module_asset_catalog import bounded_catalog_output, build_catalog, lookup_catalog

        payload = (
            lookup_catalog(args)
            if args.term
            else bounded_catalog_output(build_catalog(args), limit=args.limit, task_mode=args.task_mode)
        )
    elif args.command == "toolchain":
        payload = developer_toolchain_snapshot()
    else:
        payload = snapshot(args)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
