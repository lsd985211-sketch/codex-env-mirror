#!/usr/bin/env python3
"""Read-only planner for system maintenance and upgrade governance.

Ownership: builds a batchable, evidence-aware plan for changing an existing
system without treating module split, CodeGraph, SQLite, or any validator as a
fixed ritual.
Non-goals: applying repairs, mutating state, replacing owner validators, or
forcing every task through the same tool chain.
State behavior: read-only; emits machine-readable plans and validation checks.
Caller context: Codex workflow phase 6, system membership closeout, and broad
maintenance/upgrade work where Codex needs to decide where to edit first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intent_routing import matched_terms
from shared.json_cli import configure_utf8_stdio, now_iso, print_json


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
SCHEMA = "maintenance_upgrade_governance.v1"
TERMINAL_RESULT_STATES = {"healthy", "converged", "blocked", "failed"}


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _node_id(value: dict[str, Any]) -> str:
    return str(value.get("node_id") or "").strip()


def _node_dependencies(value: dict[str, Any]) -> list[str]:
    raw = value.get("dependencies") if isinstance(value.get("dependencies"), list) else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


def _shortest_cycle(nodes: dict[str, dict[str, Any]]) -> list[str]:
    best: list[str] = []
    for start in sorted(nodes):
        queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
        while queue:
            current, path = queue.popleft()
            for dependency in sorted(_node_dependencies(nodes[current])):
                if dependency == start:
                    candidate = [*path, start]
                    if not best or len(candidate) < len(best) or (len(candidate) == len(best) and candidate < best):
                        best = candidate
                    queue.clear()
                    break
                if dependency in nodes and dependency not in path and (not best or len(path) + 1 < len(best)):
                    queue.append((dependency, [*path, dependency]))
    return best


def validate_dependency_graph(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for raw in nodes:
        node_id = _node_id(raw)
        if not node_id:
            return {"ok": False, "reason": "node_id_missing"}
        if node_id in by_id:
            duplicates.append(node_id)
        by_id[node_id] = dict(raw)
    if duplicates:
        return {"ok": False, "reason": "duplicate_node_id", "node_ids": sorted(set(duplicates))}
    missing = [
        {"node_id": node_id, "dependency": dependency}
        for node_id, node in sorted(by_id.items())
        for dependency in _node_dependencies(node)
        if dependency not in by_id
    ]
    if missing:
        return {"ok": False, "reason": "dependency_missing", "missing_dependencies": missing}
    cycle = _shortest_cycle(by_id)
    if cycle:
        return {"ok": False, "reason": "dependency_cycle", "cycle": cycle}
    dependents: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    indegree = {node_id: 0 for node_id in by_id}
    for node_id, node in by_id.items():
        for dependency in _node_dependencies(node):
            dependents[dependency].append(node_id)
            indegree[node_id] += 1
    ready = sorted((node_id for node_id, degree in indegree.items() if degree == 0), key=lambda item: (int(by_id[item].get("estimated_cost_ms") or 0), item))
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for dependent in sorted(dependents[current]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=lambda item: (int(by_id[item].get("estimated_cost_ms") or 0), item))
    return {"ok": True, "ordered_node_ids": ordered, "node_count": len(ordered)}


def _selected_closure(nodes: dict[str, dict[str, Any]], roots: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    missing_roots = sorted(root for root in roots if root not in nodes)
    selected: set[str] = set()

    def add_with_dependencies(node_id: str) -> None:
        if node_id in selected or node_id not in nodes:
            return
        selected.add(node_id)
        for dependency in _node_dependencies(nodes[node_id]):
            add_with_dependencies(dependency)

    for root in roots:
        add_with_dependencies(root)
    changed = True
    while changed:
        changed = False
        for node_id in list(selected):
            reverse = nodes[node_id].get("reverse_validation") if isinstance(nodes[node_id].get("reverse_validation"), list) else []
            for validation_id in (str(item) for item in reverse):
                if validation_id in nodes and validation_id not in selected:
                    add_with_dependencies(validation_id)
                    selected.add(validation_id)
                    changed = True
                if validation_id in nodes and validation_id != node_id:
                    dependencies = _node_dependencies(nodes[validation_id])
                    if node_id not in dependencies:
                        nodes[validation_id] = {**nodes[validation_id], "dependencies": [*dependencies, node_id]}
    return [dict(nodes[node_id]) for node_id in sorted(selected)], missing_roots


def _input_signatures(
    nodes: list[dict[str, Any]],
    order: list[str],
    root_node_ids: list[str],
    signal_generation: str,
) -> dict[str, str]:
    by_id = {_node_id(node): node for node in nodes}
    roots = set(root_node_ids)
    signatures: dict[str, str] = {}
    for node_id in order:
        node = by_id[node_id]
        signatures[node_id] = _digest(
            {
                "node_id": node_id,
                "owner_contract_fingerprint": str(node.get("owner_contract_fingerprint") or ""),
                "action": str(node.get("action") or ""),
                "signal_generation": signal_generation if node_id in roots else "",
                "dependency_signatures": [signatures[item] for item in _node_dependencies(node)],
            }
        )
    return signatures


def _execution_batches(nodes: list[dict[str, Any]], order: list[str], reused: set[str]) -> list[dict[str, Any]]:
    by_id = {_node_id(node): node for node in nodes}
    pending = [node_id for node_id in order if node_id not in reused]
    completed: set[str] = set(reused)
    batches: list[dict[str, Any]] = []
    while pending:
        ready = [node_id for node_id in pending if set(_node_dependencies(by_id[node_id])) <= completed]
        if not ready:
            break
        ready.sort(key=lambda item: (int(by_id[item].get("estimated_cost_ms") or 0), item))
        selected: list[str] = []
        conflicts: set[str] = set()
        independent_group = str(by_id[ready[0]].get("independent_group") or "")
        for node_id in ready:
            conflict = str(by_id[node_id].get("conflict_group") or "")
            if conflict and conflict in conflicts:
                continue
            if selected and (
                not independent_group
                or str(by_id[node_id].get("independent_group") or "") != independent_group
            ):
                continue
            selected.append(node_id)
            if conflict:
                conflicts.add(conflict)
        batches.append(
            {
                "batch_id": f"batch-{len(batches) + 1}",
                "node_ids": selected,
                "estimated_cost_ms": sum(int(by_id[item].get("estimated_cost_ms") or 0) for item in selected),
                "parallel": len(selected) > 1,
            }
        )
        completed.update(selected)
        pending = [item for item in pending if item not in selected]
    return batches


def build_convergence_plan(
    *,
    intent: str,
    nodes: list[dict[str, Any]],
    root_node_ids: list[str],
    source_generation: str,
    receipts: list[dict[str, Any]],
    signal_generation: str = "",
    at: datetime | None = None,
) -> dict[str, Any]:
    all_nodes = {_node_id(node): dict(node) for node in nodes if _node_id(node)}
    selected, missing_roots = _selected_closure(all_nodes, root_node_ids)
    if missing_roots:
        return {"schema": "maintenance_convergence_plan.v1", "ok": False, "reason": "root_node_missing", "missing_root_node_ids": missing_roots}
    graph = validate_dependency_graph(selected)
    if not graph.get("ok"):
        return {"schema": "maintenance_convergence_plan.v1", **graph}
    order = list(graph["ordered_node_ids"])
    signatures = _input_signatures(selected, order, root_node_ids, signal_generation)
    terminal_by_id = {
        str(item.get("node_id") or ""): item
        for item in receipts
        if str(item.get("status") or "") in TERMINAL_RESULT_STATES
    }
    current_time = at or datetime.now(timezone.utc)

    def receipt_reusable(node_id: str, receipt: dict[str, Any]) -> bool:
        if node_id not in signatures or str(receipt.get("input_signature") or "") != signatures[node_id]:
            return False
        status = str(receipt.get("status") or "")
        if status in {"blocked", "failed"}:
            return True
        node = next(item for item in selected if _node_id(item) == node_id)
        ttl = max(0, int(node.get("freshness_ttl_seconds") or 0))
        finished_text = str(receipt.get("finished_at") or receipt.get("recorded_at") or "")
        if not finished_text:
            return False
        try:
            finished = datetime.fromisoformat(finished_text.replace("Z", "+00:00"))
        except ValueError:
            return False
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        return (current_time - finished.astimezone(timezone.utc)).total_seconds() <= ttl

    reused = {
        node_id
        for node_id, receipt in terminal_by_id.items()
        if receipt_reusable(node_id, receipt)
    }
    failed = sorted(
        node_id
        for node_id in reused
        if str(terminal_by_id[node_id].get("status") or "") in {"blocked", "failed"}
    )
    projected_nodes = [
        {**next(node for node in selected if _node_id(node) == node_id), "input_signature": signatures[node_id]}
        for node_id in order
    ]
    batches = _execution_batches(projected_nodes, order, reused)
    next_node_id = batches[0]["node_ids"][0] if batches and batches[0]["node_ids"] and not failed else ""
    next_action = next((item for item in projected_nodes if item["node_id"] == next_node_id), None)
    plan_id = f"maintenance-plan:{_digest({'intent': intent, 'roots': root_node_ids, 'source_generation': source_generation, 'signal_generation': signal_generation, 'nodes': signatures})[:20]}"
    return {
        "schema": "maintenance_convergence_plan.v1",
        "ok": not failed,
        "status": "blocked" if failed else ("complete" if not next_action else "ready"),
        "plan_id": plan_id,
        "intent": intent,
        "source_generation": source_generation,
        "signal_generation": signal_generation,
        "root_node_ids": list(dict.fromkeys(root_node_ids)),
        "nodes": projected_nodes,
        "reused_node_ids": sorted(reused),
        "terminal_failure_node_ids": failed,
        "batches": batches,
        "next_action": next_action,
        "block": {"reason": "terminal_failure_reused", "node_ids": failed} if failed else None,
        "rule": "return one next action or one block; matching terminal receipts are reused",
    }


def normalize_maintenance_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("stale"):
        status = "stale"
    elif result.get("requires_approval"):
        status = "approval_required"
    elif result.get("deferred"):
        status = "deferred"
    elif result.get("ok") and result.get("changed"):
        status = "converged"
    elif result.get("ok"):
        status = "healthy"
    elif str(result.get("reason") or "") in {"owner_contract_missing", "permission_missing", "dependency_missing", "input_missing"}:
        status = "blocked"
    else:
        status = "failed"
    review_items = result.get("items") if isinstance(result.get("items"), list) else []
    enqueue = status == "approval_required" and bool(review_items)
    return {
        "schema": "maintenance_result.v1",
        "ok": status in {"healthy", "converged", "stale"},
        "status": status,
        "enqueue_review": enqueue,
        "review_items": review_items if enqueue else [],
        "owner": str(result.get("owner") or ""),
        "reason": str(result.get("reason") or ""),
        "artifact_ref": str(result.get("artifact_ref") or ""),
    }


def convergence_nodes_from_registry(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    capability_actions: dict[str, list[str]] = {}
    for row in rows:
        maintenance = row.get("maintenance") if isinstance(row.get("maintenance"), dict) else {}
        automatic = [str(item) for item in maintenance.get("automatic_actions", []) if str(item)]
        capability_actions[str(row.get("capability_id") or "")] = automatic
    for row in rows:
        capability = str(row.get("capability_id") or "")
        maintenance = row.get("maintenance") if isinstance(row.get("maintenance"), dict) else {}

        def resolve_reference(reference: str) -> str:
            value = reference.removeprefix("capability:")
            if ":" in value:
                return value
            actions = capability_actions.get(value, [])
            return f"{value}:{actions[0]}" if actions else value

        for action in capability_actions.get(capability, []):
            nodes.append(
                {
                    "node_id": f"{capability}:{action}",
                    "capability_id": capability,
                    "system": str(row.get("system") or ""),
                    "owner": str(row.get("module_path") or ""),
                    "action": action,
                    "command_argv": list(row.get("action_commands", {}).get(action, [action])),
                    "dependencies": [resolve_reference(str(item)) for item in maintenance.get("dependencies", [])],
                    "reverse_validation": [resolve_reference(str(item)) for item in maintenance.get("reverse_validation", [])],
                    "conflict_group": str(maintenance.get("conflict_group") or ""),
                    "independent_group": str(maintenance.get("independent_group") or ""),
                    "freshness_ttl_seconds": int(maintenance.get("freshness_ttl_seconds") or 0),
                    "estimated_cost_ms": int(maintenance.get("estimated_cost_ms") or 1000),
                    "automation_level": str(maintenance.get("automation_level") or "A4"),
                    "effect_class": str(maintenance.get("effect_class") or "unknown"),
                    "result_policy": str(maintenance.get("result_policy") or "blocked"),
                    "owner_contract_fingerprint": str(row.get("contract_fingerprint") or ""),
                }
            )
    return sorted(nodes, key=lambda item: (item["system"], item["capability_id"], item["action"]))


def build_registry_convergence_plan(
    *,
    intent: str,
    system: str = "",
    signals: list[str] | None = None,
    receipts: list[dict[str, Any]] | None = None,
    root_limit: int = 32,
) -> dict[str, Any]:
    from maintenance_capability_registry import global_coverage, parse_surface_map, source_signature

    rows = parse_surface_map()
    selected_system = str(system or "").strip()
    if not selected_system:
        detected = detect_systems(intent, "", [])
        candidate = str(detected[0].get("system") or "") if detected else ""
        selected_system = "" if candidate in {"", "unknown"} else candidate
    scoped_rows = [row for row in rows if not selected_system or row.get("system") == selected_system]
    generic_terms = {
        "global",
        "maintain",
        "maintenance",
        "scheduled",
        "system",
        "validate",
        "validation",
        "workflow",
    }
    terms = [
        term.casefold()
        for term in text_terms(intent, [])
        if len(term) >= 3
        and term.casefold() not in generic_terms
        and term.casefold() != selected_system.casefold()
    ]
    matched_rows = [
        row
        for row in scoped_rows
        if any(
            term in " ".join(str(row.get(key) or "") for key in ("module_path", "surface", "owns")).casefold()
            for term in terms
        )
    ]
    candidates = matched_rows or scoped_rows
    preference = ("doctor", "snapshot", "status", "metrics", "validate", "plan", "query")
    roots: list[str] = []
    for row in candidates:
        maintenance = row.get("maintenance") if isinstance(row.get("maintenance"), dict) else {}
        automatic = [str(item) for item in maintenance.get("automatic_actions", []) if str(item)]
        action = next((item for item in preference if item in automatic), automatic[0] if automatic else "")
        if action:
            roots.append(f"{row['capability_id']}:{action}")
        if len(roots) >= max(1, min(int(root_limit), 100)):
            break
    nodes = convergence_nodes_from_registry(rows)
    coverage = global_coverage(rows)
    if not roots:
        return {
            "schema": "maintenance_convergence_plan.v1",
            "ok": False,
            "status": "blocked",
            "reason": "no_automatic_read_only_maintenance_root",
            "system": selected_system,
            "coverage": coverage,
        }
    normalized_signals = list(dict.fromkeys(str(item) for item in (signals or []) if str(item)))
    plan = build_convergence_plan(
        intent=intent,
        nodes=nodes,
        root_node_ids=roots,
        source_generation=source_signature(rows),
        signal_generation=_digest(normalized_signals) if normalized_signals else "",
        receipts=list(receipts or []),
    )
    return {
        **plan,
        "system": selected_system,
        "signals": normalized_signals,
        "coverage": coverage,
        "selection": {
            "candidate_capability_count": len(candidates),
            "selected_root_count": len(roots),
            "root_limit": max(1, min(int(root_limit), 100)),
            "matched_by_intent": bool(matched_rows),
        },
    }


def explain_convergence_plan(plan: dict[str, Any], node_id: str = "") -> dict[str, Any]:
    nodes = plan.get("nodes") if isinstance(plan.get("nodes"), list) else []
    selected = next((item for item in nodes if str(item.get("node_id") or "") == node_id), None) if node_id else plan.get("next_action")
    return {
        "schema": "maintenance_convergence_explain.v1",
        "ok": bool(plan.get("ok")) and (selected is not None or plan.get("status") == "complete"),
        "plan_id": str(plan.get("plan_id") or ""),
        "status": str(plan.get("status") or ""),
        "selected_node": selected,
        "reused_node_ids": list(plan.get("reused_node_ids") or []),
        "block": plan.get("block"),
        "coverage": plan.get("coverage"),
        "rule": "the planner explains owner, dependency, signature, risk, and skip evidence without executing the owner",
    }


def uniq(values: list[str], limit: int = 20) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def text_terms(message: str, explicit_terms: list[str]) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_./\\-]+|[\u4e00-\u9fff]{2,}", message)
    return uniq([*explicit_terms, *[word.lower() for word in words]], 16)


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return bool(matched_terms(text, needles))


SYSTEM_RULES: list[dict[str, Any]] = [
    {
        "system": "workflow",
        "keywords": ("workflow", "工作流", "编排", "准则", "route", "phase", "closeout", "preflight"),
        "owner_modules": [
            "_bridge/workflow_orchestrator.py",
            "_bridge/workflow_validation.py",
            "_bridge/execution_route_pack.py",
            "_bridge/task_route_contract.py",
            "_bridge/codex_workflow_entry.py",
        ],
        "owner_validators": [
            "python _bridge\\workflow_orchestrator.py validate",
            "python _bridge\\task_route_contract.py validate",
        ],
    },
    {
        "system": "mcp",
        "keywords": ("mcp", "hub", "tool", "工具", "current-turn", "transport closed"),
        "owner_modules": [
            "_bridge/mcp_session_doctor.py",
            "_bridge/local_mcp_hub.py",
            "_bridge/mcp_capability_routes.py",
        ],
        "owner_validators": [
            "python _bridge\\mcp_session_doctor.py validate",
            "python _bridge\\local_mcp_hub.py validate",
        ],
    },
    {
        "system": "resource",
        "keywords": ("resource", "资源层", "资源获取", "broker", "download", "handoff_required"),
        "owner_modules": [
            "_bridge/resource_broker.py",
            "_bridge/resource_cli.py",
            "_bridge/resource_router.py",
            "_bridge/resource_fetcher.py",
        ],
        "owner_validators": ["python _bridge\\resource_process_doctor.py validate"],
    },
    {
        "system": "network",
        "keywords": ("network", "proxy", "dns", "网关", "网络", "代理", "lease"),
        "owner_modules": [
            "_bridge/codex_network_gateway.py",
            "_bridge/network_doctor.py",
            "_bridge/network_policy.py",
        ],
        "owner_validators": ["python _bridge\\codex_network_gateway.py validate"],
    },
    {
        "system": "bridge",
        "keywords": ("mobile", "weixin", "openclaw", "ack", "微信", "手机", "桥接", "只ack"),
        "owner_modules": [
            "_bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py",
            "_bridge/mobile_openclaw_bridge/mobile_maintenance.py",
            "_bridge/mobile_openclaw_bridge/mobile_prompt_contract.py",
        ],
        "owner_validators": ["mobile bridge owner validate or maintenance summary"],
    },
    {
        "system": "mail",
        "keywords": ("email", "mail", "imap", "smtp", "邮件", "邮箱", "附件", "回信"),
        "owner_modules": [
            "_bridge/shared/email_scheduler.py",
            "_bridge/email_state_query.py",
        ],
        "owner_validators": ["email scheduler validate / mailbox queue readback"],
    },
    {
        "system": "memory",
        "keywords": ("memory", "pmb", "记忆", "画像", "候选记忆", "work note"),
        "owner_modules": [
            "_bridge/memory_router.py",
            "_bridge/memory_governance.py",
            "_bridge/local_pmb_memory.py",
        ],
        "owner_validators": ["python _bridge\\memory_router.py validate", "python _bridge\\memory_governance.py validate"],
    },
    {
        "system": "records",
        "keywords": ("record", "记录", "索引", "归档", "sqlite", "resource library"),
        "owner_modules": [
            "_bridge/shared/record_store_maintenance.py",
        ],
        "owner_validators": ["python _bridge\\shared\\record_store_maintenance.py validate"],
    },
    {
        "system": "startup",
        "keywords": ("startup", "启动", "重启", "session store", "baseline", "config guard"),
        "owner_modules": ["_bridge/codex_config_guard.py", "_bridge/codex_session_store_doctor.py", "_bridge/codex_baseline_update.py"],
        "owner_validators": ["python _bridge\\codex_config_guard.py validate", "python _bridge\\codex_session_store_doctor.py validate"],
    },
    {
        "system": "hardware",
        "keywords": ("hardware", "device", "pnp", "usb", "硬件", "设备", "外设", "热插拔"),
        "owner_modules": [
            "_bridge/windows_hardware_owner.py",
            "_bridge/usb_device_owner.py",
            "_bridge/usb_device_control.py",
        ],
        "owner_validators": [
            "python _bridge\\windows_hardware_owner.py validate",
            "python _bridge\\usb_device_owner.py validate",
            "python _bridge\\usb_device_control.py validate",
        ],
    },
    {
        "system": "skills",
        "keywords": ("skill", "技能", "skill lifecycle", "skill router"),
        "owner_modules": ["_bridge/skill_lifecycle_governance.py", "_bridge/skill_orchestrator.py"],
        "owner_validators": ["python _bridge\\skill_lifecycle_governance.py doctor", "python _bridge\\skill_orchestrator.py validate"],
    },
    {
        "system": "drafts",
        "keywords": ("draft", "草案", "retained_reference", "pending_review", "artifact_ref"),
        "owner_modules": ["_bridge/draft_governance.py", "_bridge/workflow_review_queue.py"],
        "owner_validators": ["python _bridge\\draft_governance.py validate"],
    },
]


EVIDENCE_RULES: list[dict[str, Any]] = [
    {
        "key": "module_context",
        "when": "non-simple code, refactor, module boundary, owner placement, or reusable module lookup is involved",
        "triggers": ("code", "代码", "模块", "module", "refactor", "重构", "治理", "upgrade", "升级"),
        "policy_ref": "code_maintainability.py module-context/lookup-module/placement-plan",
        "route_source": "maintenance registry lookup, then the selected system shard",
        "validation_goal": "owner module and placement decision are explicit before edits",
    },
    {
        "key": "codegraph",
        "when": "source structure, symbol flow, callers/callees, or blast-radius evidence is needed",
        "triggers": ("call", "caller", "callee", "impact", "symbol", "结构", "调用", "影响", "blast", "codegraph"),
        "policy_ref": "workflow_tools_contract.codegraph_policy",
        "route_source": "mcp_capability_routes.py lookup, then the selected matrix section and CodeGraph current-turn policy",
        "validation_goal": "source-structure evidence is current or a same-boundary fallback reason is recorded",
    },
    {
        "key": "sqlite_state",
        "when": "queues, receipts, scheduler state, record indexes, inbox/outbox, .sqlite/.db, or database-backed status matters",
        "triggers": ("sqlite", "db", "queue", "status", "receipt", "状态", "队列", "回执", "索引", "数据库"),
        "policy_ref": "workflow_tools_contract.structured_state_policy",
        "route_source": "SQLite MCP/Hub plus owning business query surface",
        "validation_goal": "structured state evidence comes from read-only query or an explicit indexed-route miss",
    },
    {
        "key": "network_route",
        "when": "connectivity, proxy, DNS, slow download, external docs, package manager, browser, or API route behavior matters",
        "triggers": ("network", "proxy", "dns", "联网", "网络", "代理", "下载", "timeout", "slow", "package"),
        "policy_ref": "workflow_tools_contract.network_policy",
        "route_source": "codex_network_gateway.py and network_doctor.py surfaces",
        "validation_goal": "network route evidence is per-target and does not mutate global proxy/DNS by default",
    },
    {
        "key": "resource_layer",
        "when": "external resource acquisition, docs research, URL discovery/materialization, downloads, or package metadata is the task",
        "triggers": ("resource acquisition", "resource request", "资源获取", "资源委托", "联网", "搜索", "download", "docs", "github", "package", "下载"),
        "policy_ref": "workflow_tools_contract.external_docs_policy and resource acquisition surface",
        "route_source": "resource_broker.py/resource_cli.py plus owner MCP boundaries",
        "validation_goal": "resource request has a terminal receipt or documented ownership release",
    },
    {
        "key": "owner_maintenance",
        "when": "the changed system has an owning doctor, validate, metrics, repair-plan, or snapshot surface",
        "triggers": ("治理", "维护", "修复", "validate", "doctor", "repair", "升级", "优化"),
        "policy_ref": "maintenance_capability_registry.py query and returned source shard",
        "route_source": "owner snapshot/doctor/repair-plan/validate where defined",
        "validation_goal": "the owning surface, not this planner, proves the change",
    },
    {
        "key": "system_membership",
        "when": "a system member, route, module, MCP, startup surface, or architecture contract changes",
        "triggers": ("member", "成员", "契约", "architecture", "架构", "mcp", "route", "模块"),
        "policy_ref": "system_membership.py impact/upgrade-plan",
        "route_source": "system membership contract",
        "validation_goal": "architecture/member synchronization obligations are visible at closeout",
    },
]


def detect_systems(message: str, target_system: str, targets: list[str]) -> list[dict[str, Any]]:
    if target_system:
        rules = [rule for rule in SYSTEM_RULES if rule["system"] == target_system]
        if not rules:
            return [{"system": target_system, "confidence": "explicit", "owner_modules": targets, "owner_validators": []}]
        return [{**rules[0], "confidence": "explicit"}]
    matched: list[dict[str, Any]] = []
    haystack = " ".join([message, *targets])
    for rule in SYSTEM_RULES:
        score = sum(1 for keyword in rule["keywords"] if keyword.lower() in haystack.lower())
        if score:
            matched.append({**rule, "confidence": "high" if score >= 2 else "medium", "match_score": score})
    if not matched and targets:
        validators = ["targeted readback"]
        if any(str(target).endswith(".py") for target in targets):
            validators.append("python -m py_compile <changed-files>")
        matched.append({"system": "targeted_surface", "confidence": "target_path", "owner_modules": targets, "owner_validators": validators})
    if not matched:
        matched.append({"system": "unknown", "confidence": "low", "owner_modules": [], "owner_validators": []})
    return sorted(matched, key=lambda item: (-int(item.get("match_score") or 0), str(item.get("system") or "")))[:4]


def evidence_chain(message: str, systems: list[dict[str, Any]], targets: list[str]) -> list[dict[str, Any]]:
    # Evidence classes should be selected from task semantics and identified
    # systems. Target paths are used later as candidate files; letting path
    # fragments such as "docs" trigger resource/web evidence creates noisy plans.
    haystack = " ".join([message, *[str(item.get("system") or "") for item in systems]])
    has_python_target = any(str(target).lower().endswith(".py") for target in targets)
    selected: list[dict[str, Any]] = []
    for rule in EVIDENCE_RULES:
        matched = [trigger for trigger in rule["triggers"] if trigger.lower() in haystack.lower()]
        default_selected = rule["key"] == "owner_maintenance" or (rule["key"] == "module_context" and has_python_target)
        if matched or default_selected:
            selected.append(
                {
                    "key": rule["key"],
                    "selected": bool(matched or default_selected),
                    "matched_triggers": matched,
                    "when": rule["when"],
                    "policy_ref": rule["policy_ref"],
                    "route_source": rule["route_source"],
                    "validation_goal": rule["validation_goal"],
                }
            )
    return selected


def target_files(systems: list[dict[str, Any]], explicit_targets: list[str]) -> list[str]:
    files: list[str] = [*explicit_targets]
    for system in systems:
        files.extend(str(item) for item in system.get("owner_modules", []) if str(item).strip())
    return uniq(files, 12)


def batch_plan(message: str, systems: list[dict[str, Any]], targets: list[str], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    owner_validators = uniq(
        [
            command
            for system in systems
            for command in system.get("owner_validators", [])
            if str(command).strip()
        ],
        12,
    )
    files = target_files(systems, targets)
    evidence_keys = [str(item.get("key") or "") for item in evidence if item.get("selected")]
    route_actions = [
        "run workflow_orchestrator.py plan for routing context if not already done",
        "run maintenance_upgrade_governance.py plan with explicit --target values when known",
    ]
    if "module_context" in evidence_keys:
        route_actions.append("run code_maintainability placement-plan only when code/module placement is involved")
    validation_actions = [*owner_validators]
    if any(path.lower().endswith(".py") for path in files):
        validation_actions.append("python -m py_compile <changed-files>")
    batches = [
        {
            "id": "batch_1_route_and_scope",
            "purpose": "Confirm owner system, candidate modules, boundaries, and what evidence is actually relevant for this task.",
            "read_only": True,
            "suggested_actions": route_actions,
            "evidence_keys": [key for key in ("module_context", "system_membership") if key in evidence_keys],
            "stop_conditions": ["unknown_owner_system_without_explicit_target", "task_requires_write_but_no_backup_or_approval"],
        },
        {
            "id": "batch_2_task_specific_evidence",
            "purpose": "Collect only the evidence classes triggered by the task, current environment, and affected system.",
            "read_only": True,
            "suggested_actions": [
                f"collect {item['key']} evidence via {item['policy_ref']} when: {item['when']}"
                for item in evidence
                if item.get("selected") and item["key"] not in {"module_context", "owner_maintenance"}
            ][:8],
            "evidence_keys": evidence_keys,
            "stop_conditions": ["required_evidence_route_unavailable_without_same_boundary_fallback"],
        },
        {
            "id": "batch_3_implementation_slice",
            "purpose": "Make one bounded semantic change in the owning module or purpose-owned peer module.",
            "read_only": False,
            "candidate_files": files,
            "suggested_actions": [
                "create a routed backup before edits",
                "preserve owner facade/CLI compatibility while moving independent lifecycle or state logic",
                "avoid creating a new module unless the boundary and validator owner are explicit",
            ],
            "stop_conditions": ["edit_target_conflicts_with_owner_route", "new_module_without_docstring_boundary_or_validator"],
        },
        {
            "id": "batch_4_owner_validation",
            "purpose": "Validate through the owning maintenance surface and smallest relevant regression checks.",
            "read_only": True,
            "suggested_actions": uniq(validation_actions, 10),
            "evidence_keys": ["owner_maintenance"],
            "stop_conditions": ["no_owner_validator_or_equivalent_readback"],
        },
        {
            "id": "batch_5_contract_and_closeout",
            "purpose": "Update discoverability contracts only if the change altered a member, route, module boundary, or maintenance surface.",
            "read_only": True,
            "suggested_actions": [
                "python _bridge\\system_membership.py impact --changed <changed-file>",
                "query the maintenance registry and read the returned source shard if a surface changed",
                "python _bridge\\codex_workflow_entry.py closeout --task-kind maintenance_upgrade_governance --outcome ok ...",
            ],
            "evidence_keys": ["system_membership"],
            "stop_conditions": ["architecture_change_without_membership_or_surface_update"],
        },
    ]
    # If the task is explicitly non-code and no target files are known, make the
    # implementation slice conditional so callers do not mistake planning for a write requirement.
    if not files:
        batches[2]["read_only"] = True
        batches[2]["purpose"] = "No implementation files were identified yet; first refine owner route or target system."
        batches[2]["suggested_actions"] = ["refine --target-system or --target before editing"]
    return batches


def plan(args: argparse.Namespace) -> dict[str, Any]:
    message = str(getattr(args, "message", "") or "")
    targets = uniq(list(getattr(args, "target", []) or []), 12)
    explicit_terms = [str(item).lower() for item in (getattr(args, "term", []) or []) if str(item).strip()]
    terms = text_terms(message, explicit_terms)
    systems = detect_systems(message, str(getattr(args, "target_system", "") or ""), targets)
    evidence = evidence_chain(message, systems, targets)
    batches = batch_plan(message, systems, targets, evidence)
    blockers: list[dict[str, Any]] = []
    if systems and systems[0].get("system") == "unknown":
        blockers.append(
            {
                "code": "owner_system_ambiguous",
                "message": "Refine --target-system, --target, or task wording before changing files.",
            }
        )
    return {
        "schema": f"{SCHEMA}.plan",
        "ok": not blockers,
        "generated_at": now_iso(),
        "read_only": True,
        "message": message,
        "terms": terms,
        "target_system": str(getattr(args, "target_system", "") or ""),
        "targets": targets,
        "detected_systems": systems,
        "conditional_evidence_chain": evidence,
        "recommended_batches": batches,
        "principles": [
            "module systems exist to make governance, upgrades, validation, and reuse easier; do not split for splitting's sake",
            "select evidence by task and configured environment; tool-call order stays in existing route policies",
            "Codex owns judgment and exceptions; owning tools provide repeatable evidence and execution",
            "production state repairs must go through owner maintenance surfaces, not direct database writes",
        ],
        "blockers": blockers,
    }


def snapshot() -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": True,
        "generated_at": now_iso(),
        "read_only": True,
        "known_systems": [item["system"] for item in SYSTEM_RULES],
        "evidence_rules": [{"key": item["key"], "when": item["when"]} for item in EVIDENCE_RULES],
        "entrypoints": ["plan", "convergence-plan", "explain", "coverage", "snapshot", "validate"],
    }


def validate() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    code_args = argparse.Namespace(
        message="优化模块系统，让维护升级治理根据任务选择 CodeGraph SQLite owner validator 等证据",
        target_system="workflow",
        target=["_bridge/workflow_orchestrator.py"],
        term=[],
    )
    code_plan = plan(code_args)
    keys = [item.get("key") for item in code_plan.get("conditional_evidence_chain", [])]
    for key in ("module_context", "codegraph", "owner_maintenance", "system_membership"):
        if key not in keys:
            issues.append({"severity": "risk", "code": "code_upgrade_missing_evidence_key", "key": key})
    state_args = argparse.Namespace(
        message="检查邮件队列状态和回执",
        target_system="",
        target=[],
        term=[],
    )
    state_plan = plan(state_args)
    state_keys = [item.get("key") for item in state_plan.get("conditional_evidence_chain", [])]
    if "sqlite_state" not in state_keys:
        issues.append({"severity": "risk", "code": "state_task_missing_sqlite_evidence"})
    simple_args = argparse.Namespace(
        message="只修改文档措辞",
        target_system="",
        target=["_bridge/docs/maintenance_surface_map.md"],
        term=[],
    )
    simple_plan = plan(simple_args)
    simple_keys = [item.get("key") for item in simple_plan.get("conditional_evidence_chain", [])]
    if "sqlite_state" in simple_keys or "network_route" in simple_keys:
        issues.append({"severity": "risk", "code": "simple_doc_task_overselected_runtime_evidence", "keys": simple_keys})
    hardware_args = argparse.Namespace(
        message="优化 Windows PnP 和 USB 硬件诊断",
        target_system="hardware",
        target=["_bridge/windows_hardware_owner.py"],
        term=[],
    )
    hardware_plan = plan(hardware_args)
    hardware_validators = {
        str(command)
        for batch in hardware_plan.get("recommended_batches", [])
        if batch.get("id") == "batch_4_owner_validation"
        for command in batch.get("suggested_actions", [])
    }
    for command in (
        "python _bridge\\windows_hardware_owner.py validate",
        "python _bridge\\usb_device_owner.py validate",
        "python _bridge\\usb_device_control.py validate",
    ):
        if command not in hardware_validators:
            issues.append({"severity": "risk", "code": "hardware_owner_validator_missing", "command": command})
    graph_probe = validate_dependency_graph(
        [
            {"node_id": "owner:snapshot", "dependencies": [], "estimated_cost_ms": 1},
            {"node_id": "owner:validate", "dependencies": ["owner:snapshot"], "estimated_cost_ms": 2},
        ]
    )
    if not graph_probe.get("ok") or graph_probe.get("ordered_node_ids") != ["owner:snapshot", "owner:validate"]:
        issues.append({"severity": "risk", "code": "maintenance_convergence_graph_probe_failed", "probe": graph_probe})
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "read_only": True,
        "issues": issues,
        "probes": {
            "code_plan_ok": code_plan.get("ok"),
            "code_plan_evidence_keys": keys,
            "state_plan_evidence_keys": state_keys,
            "simple_plan_evidence_keys": simple_keys,
            "hardware_owner_validators": sorted(hardware_validators),
            "convergence_graph": graph_probe,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only maintenance upgrade governance planner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("validate")
    p = sub.add_parser("plan")
    p.add_argument("--message", required=True)
    p.add_argument("--target-system", default="")
    p.add_argument("--target", action="append", default=[])
    p.add_argument("--term", action="append", default=[])
    for name in ("convergence-plan", "explain"):
        convergence = sub.add_parser(name)
        convergence.add_argument("--message", default="scheduled global maintenance coverage")
        convergence.add_argument("--system", default="")
        convergence.add_argument("--signal", action="append", default=[])
        convergence.add_argument("--root-limit", type=int, default=32)
        if name == "explain":
            convergence.add_argument("--node", default="")
    sub.add_parser("coverage")
    return parser


def main() -> int:
    configure_utf8_stdio()
    args = build_parser().parse_args()
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "coverage":
        from maintenance_capability_registry import global_coverage

        payload = global_coverage()
    elif args.command in {"convergence-plan", "explain"}:
        convergence = build_registry_convergence_plan(
            intent=args.message,
            system=args.system,
            signals=args.signal,
            root_limit=args.root_limit,
        )
        payload = explain_convergence_plan(convergence, args.node) if args.command == "explain" else convergence
    else:
        payload = plan(args)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
