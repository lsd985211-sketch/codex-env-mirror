#!/usr/bin/env python3
"""Pure bounded terminal-convergence planning for the workflow system."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any


PHASES = ("authoritative_inputs", "git_stability", "external_publish", "read_only_acceptance")
EFFECTS = {"mutation", "derived_mutation", "read_only"}
MUTATION_EFFECTS = {"mutation", "derived_mutation"}
TERMINAL_ACTION_ADAPTERS = {
    "maintenance.index": {
        "owner": "maintenance_capability_registry",
        "phase": "authoritative_inputs",
        "effect": "derived_mutation",
        "owner_contract_version": "maintenance_capability_registry.v1",
        "verify_entrypoint": "maintenance_capability_registry.py validate",
        "authority_facts": ["maintenance.capability_index"],
        "receipt_acceptance": ["ok", "index_current"],
    },
    "host_projection.apply": {
        "owner": "wsl_workspace_owner",
        "phase": "authoritative_inputs",
        "effect": "derived_mutation",
        "owner_contract_version": "wsl_workspace_owner.host_compatibility_projection.v1",
        "verify_entrypoint": "wsl_workspace_owner.py validate",
        "authority_facts": ["windows.host_compatibility_projection"],
        "receipt_acceptance": ["ok", "projection_current", "source_authority_preserved"],
    },
    "baseline.adopt": {
        "owner": "baseline_update_owner",
        "phase": "authoritative_inputs",
        "effect": "mutation",
        "owner_contract_version": "baseline_update_owner.v1",
        "verify_entrypoint": "baseline_update_owner.py validate",
        "authority_facts": ["startup.baseline"],
        "receipt_acceptance": ["ok", "baseline_validated"],
    },
    "checkpoint.write": {
        "owner": "project_checkpoint_finalize",
        "phase": "authoritative_inputs",
        "effect": "mutation",
        "owner_contract_version": "project_checkpoint_finalize.v1",
        "verify_entrypoint": "project_checkpoint_finalize.py plan",
        "authority_facts": ["project.checkpoint"],
        "receipt_acceptance": ["ok", "checkpoint_current"],
    },
    "work_git.sync_bare": {
        "owner": "work_git_change_owner",
        "phase": "git_stability",
        "effect": "mutation",
        "owner_contract_version": "work_git_change_owner.sync.v1",
        "verify_entrypoint": "work_git_change_owner.py snapshot",
        "authority_facts": ["windows_bare_git.main"],
        "receipt_acceptance": ["ok", "head_matches_origin_main"],
    },
    "mirror.publish": {
        "owner": "codex_environment_mirror",
        "phase": "external_publish",
        "effect": "mutation",
        "owner_contract_version": "codex_environment_mirror.publish.v1",
        "verify_entrypoint": "codex_environment_mirror.py status --force-fresh",
        "authority_facts": ["codex_environment_mirror.snapshot"],
        "receipt_acceptance": ["ok", "snapshot_id", "source_freshness"],
    },
    "release.publish": {
        "owner": "codex_environment_mirror",
        "phase": "external_publish",
        "effect": "mutation",
        "owner_contract_version": "codex_environment_mirror.release.v1",
        "verify_entrypoint": "codex_environment_mirror.py release-plan",
        "authority_facts": ["github.recovery_release"],
        "receipt_acceptance": ["ok", "release_url", "tag"],
        "approval_required": True,
    },
    "long_command.consume": {
        "owner": "long_command_receipt",
        "phase": "read_only_acceptance",
        "effect": "read_only",
        "owner_contract_version": "long_command_receipt.v1",
        "verify_entrypoint": "shared/long_command_receipt.py status",
        "authority_facts": [],
        "receipt_acceptance": ["terminal", "exit_code"],
    },
}


def terminal_action_adapter(action_id: str, *, input_signature: str, depends_on: list[str] | None = None, approval_granted: bool = False, **overrides: Any) -> dict[str, Any]:
    """Materialize one declarative owner adapter without importing owner logic."""

    template = TERMINAL_ACTION_ADAPTERS.get(str(action_id))
    if not template:
        return {"schema": "terminal_action_adapter.v1", "ok": False, "reason": "unknown_terminal_action", "action_id": str(action_id)}
    contract = {
        "schema": "terminal_action_contract.v1",
        "action_id": str(action_id),
        "owner": template["owner"],
        "phase": template["phase"],
        "effect": template["effect"],
        "depends_on": list(depends_on or []),
        "invalidates": [],
        "authority_facts": list(template["authority_facts"]),
        "input_signature": str(input_signature),
        "owner_contract_version": template["owner_contract_version"],
        "approval": {
            "required": bool(template.get("approval_required")),
            "granted": bool(approval_granted),
            "scope": str(action_id),
        },
        "receipt_acceptance": list(template["receipt_acceptance"]),
        "verify_entrypoint": template["verify_entrypoint"],
        "verify_effect": "read_only",
        **overrides,
    }
    normalized = normalize_action_contract(contract)
    return {"schema": "terminal_action_adapter.v1", "ok": normalized["ok"], "reason": "" if normalized["ok"] else "invalid_adapter_contract", "contract": normalized["contract"], "missing": normalized["missing"]}


def terminal_receipt_for_action(*, action_id: str, input_signature: str, intent_id: str, result: dict[str, Any], ref: str = "") -> dict[str, Any]:
    """Project an owner result into a reusable terminal receipt."""

    adapter = terminal_action_adapter(action_id, input_signature=input_signature)
    if not adapter["ok"]:
        return {"schema": "terminal_action_receipt.v1", "ok": False, "reason": adapter["reason"], "action_id": action_id}
    contract = adapter["contract"]
    accepted = bool(result.get("ok"))
    if action_id == "long_command.consume":
        accepted = bool(result.get("terminal") and isinstance(result.get("exit_code"), int))
    return {
        "schema": "terminal_action_receipt.v1",
        "ok": accepted,
        "action_id": action_id,
        "input_signature": input_signature,
        "owner_contract_version": contract["owner_contract_version"],
        "intent_id": intent_id,
        "reuse_key": receipt_reuse_key(
            action_id=action_id,
            input_signature=input_signature,
            owner_contract_version=contract["owner_contract_version"],
            intent_id=intent_id,
        ),
        "accepted": accepted,
        "ref": str(ref or result.get("receipt") or result.get("snapshot_id") or result.get("release_url") or ""),
        "result": result,
    }


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def receipt_reuse_key(*, action_id: str, input_signature: str, owner_contract_version: str, intent_id: str) -> str:
    return _digest({"action_id": action_id, "input_signature": input_signature, "owner_contract_version": owner_contract_version, "intent_id": intent_id})


def terminal_source_signature(*, convergence_id: str, intent_id: str, source_state: dict[str, Any]) -> str:
    return _digest({"convergence_id": convergence_id, "intent_id": intent_id, "source_state": source_state})


def normalize_action_contract(value: dict[str, Any]) -> dict[str, Any]:
    contract = dict(value) if isinstance(value, dict) else {}
    contract.setdefault("schema", "terminal_action_contract.v1")
    for field in ("action_id", "owner", "phase", "effect", "input_signature", "owner_contract_version", "verify_entrypoint", "verify_effect"):
        contract[field] = str(contract.get(field) or "").strip()
    for field in ("depends_on", "invalidates", "authority_facts", "receipt_acceptance"):
        contract[field] = list(dict.fromkeys(str(item) for item in contract.get(field, []) if str(item)))
    contract["approval"] = dict(contract.get("approval") or {})
    missing = [field for field in ("action_id", "owner", "phase", "effect", "input_signature", "owner_contract_version") if not contract[field]]
    if contract["phase"] not in PHASES:
        missing.append("phase_valid")
    if contract["effect"] not in EFFECTS:
        missing.append("effect_valid")
    if contract["effect"] in MUTATION_EFFECTS:
        if not isinstance(value.get("depends_on"), list):
            missing.append("depends_on")
        if not isinstance(value.get("invalidates"), list):
            missing.append("invalidates")
        if not isinstance(value.get("approval"), dict):
            missing.append("approval")
        if not contract["receipt_acceptance"]:
            missing.append("receipt_acceptance")
        if not contract["verify_entrypoint"]:
            missing.append("verify_entrypoint")
        if contract["verify_effect"] != "read_only":
            missing.append("verify_effect=read_only")
    return {"schema": "terminal_action_contract.normalized.v1", "ok": not missing, "missing": list(dict.fromkeys(missing)), "contract": contract}


def _shortest_cycle(actions: list[dict[str, Any]]) -> list[str]:
    graph = {item["action_id"]: list(item["depends_on"]) for item in actions}
    best: list[str] = []
    for start in graph:
        queue: deque[list[str]] = deque([[start]])
        while queue:
            path = queue.popleft()
            for neighbor in graph.get(path[-1], []):
                if neighbor == start:
                    candidate = [*path, start]
                    if not best or len(candidate) < len(best):
                        best = candidate
                elif neighbor not in path and (not best or len(path) + 2 < len(best)):
                    queue.append([*path, neighbor])
    return best


def _topological_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index = {item["action_id"]: position for position, item in enumerate(actions)}
    by_id = {item["action_id"]: item for item in actions}
    indegree = {action_id: 0 for action_id in by_id}
    downstream = {action_id: [] for action_id in by_id}
    for item in actions:
        for dependency in item["depends_on"]:
            indegree[item["action_id"]] += 1
            downstream[dependency].append(item["action_id"])
    ready = sorted((item for item, count in indegree.items() if count == 0), key=index.get)
    ordered: list[dict[str, Any]] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for dependent in sorted(downstream[current], key=index.get):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=index.get)
    return ordered


def validate_action_graph(actions: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [normalize_action_contract(item) for item in actions if isinstance(item, dict)]
    invalid = [item for item in normalized if not item["ok"]]
    if invalid or len(normalized) != len(actions):
        return {"schema": "terminal_action_graph.validation.v1", "ok": False, "reason": "invalid_action_contract", "invalid_contracts": invalid}
    contracts = [item["contract"] for item in normalized]
    identifiers = [item["action_id"] for item in contracts]
    if len(set(identifiers)) != len(identifiers):
        duplicate = next(item for item in identifiers if identifiers.count(item) > 1)
        return {"schema": "terminal_action_graph.validation.v1", "ok": False, "reason": "duplicate_action_id", "action_id": duplicate}
    known = set(identifiers)
    for item in contracts:
        for dependency in item["depends_on"]:
            if dependency not in known:
                return {"schema": "terminal_action_graph.validation.v1", "ok": False, "reason": "missing_dependency", "action_id": item["action_id"], "dependency": dependency}
        conflicts = sorted(set(item["depends_on"]) & set(item["invalidates"]))
        if conflicts:
            return {"schema": "terminal_action_graph.validation.v1", "ok": False, "reason": "contradictory_dependency_invalidation", "action_id": item["action_id"], "conflicts": conflicts}
    writers: dict[str, str] = {}
    for item in contracts:
        if item["effect"] not in MUTATION_EFFECTS:
            continue
        for fact in item["authority_facts"]:
            if fact in writers:
                return {"schema": "terminal_action_graph.validation.v1", "ok": False, "reason": "duplicate_authority_writer", "authority_fact": fact, "writers": [writers[fact], item["action_id"]]}
            writers[fact] = item["action_id"]
    cycle = _shortest_cycle(contracts)
    if cycle:
        return {"schema": "terminal_action_graph.validation.v1", "ok": False, "reason": "dependency_cycle", "cycle": cycle}
    return {"schema": "terminal_action_graph.validation.v1", "ok": True, "ordered_actions": _topological_actions(contracts)}


def invalidate_dependents(actions: list[dict[str, Any]], changed_action_ids: list[str]) -> list[str]:
    reverse: dict[str, list[str]] = {}
    for item in actions:
        for dependency in item.get("depends_on", []):
            reverse.setdefault(str(dependency), []).append(str(item.get("action_id") or ""))
    queue: deque[str] = deque(map(str, changed_action_ids))
    invalidated: list[str] = []
    while queue:
        current = queue.popleft()
        for dependent in reverse.get(current, []):
            if dependent and dependent not in invalidated:
                invalidated.append(dependent)
                queue.append(dependent)
    return invalidated


def _accepted_receipt(action: dict[str, Any], receipts: list[dict[str, Any]], intent_id: str) -> dict[str, Any] | None:
    expected = receipt_reuse_key(action_id=action["action_id"], input_signature=action["input_signature"], owner_contract_version=action["owner_contract_version"], intent_id=intent_id)
    return next((item for item in receipts if isinstance(item, dict) and item.get("action_id") == action["action_id"] and item.get("reuse_key") == expected and item.get("accepted") is True), None)


def build_convergence_plan(*, convergence_id: str, intent_id: str, terminal_goal: str, source_state: dict[str, Any], actions: list[dict[str, Any]], receipts: list[dict[str, Any]], entered_read_only_acceptance: bool = False) -> dict[str, Any]:
    graph = validate_action_graph(actions)
    base: dict[str, Any] = {"schema": "terminal_convergence.plan.v1", "ok": bool(graph.get("ok")), "relevant": bool(actions), "convergence_id": convergence_id, "intent_id": intent_id, "terminal_goal": terminal_goal, "terminal_source_signature": terminal_source_signature(convergence_id=convergence_id, intent_id=intent_id, source_state=source_state), "completed_receipts": [], "completed_action_ids": [], "invalidated_receipts": [], "invalidated_action_ids": [], "mutation_barrier": {"rule": "all_source_affecting_actions_complete_before_external_publish"}, "verification_barrier": {"entered": entered_read_only_acceptance, "rule": "only_read_only_owner_entrypoints_after_terminal_mutation"}, "automatic_loop_allowed": False}
    if not graph.get("ok"):
        action_id = str(graph.get("action_id") or "action_graph")
        return {**base, "ok": False, "reason": graph.get("reason"), "first_invalid_action": action_id, "next_action": {"action_id": action_id, "decision": "block", "reason": graph.get("reason")}, "graph_validation": graph}
    ordered = list(graph["ordered_actions"])
    base["ordered_actions"] = ordered
    for action in ordered:
        receipt = _accepted_receipt(action, receipts, intent_id)
        if receipt:
            base["completed_action_ids"].append(action["action_id"])
            base["completed_receipts"].append({"action_id": action["action_id"], "ref": receipt.get("ref", "")})
            continue
        if entered_read_only_acceptance and action["effect"] in MUTATION_EFFECTS:
            return {**base, "ok": False, "reason": "duplicate_terminal_mutation", "first_invalid_action": action["action_id"], "current_phase": "read_only_acceptance", "next_action": {"action_id": action["action_id"], "owner": action["owner"], "decision": "block", "reason": "duplicate_terminal_mutation"}}
        approval = action["approval"]
        if action["effect"] in MUTATION_EFFECTS and approval.get("required") and not approval.get("granted"):
            decision, reason = "block", "approval_required"
        elif action.get("transport_incomplete"):
            decision, reason = "resume", "transport_receipt_incomplete"
        elif action["effect"] == "read_only":
            decision, reason = "verify", "read_only_acceptance_required"
        else:
            decision, reason = "execute", "receipt_missing_or_invalid"
        return {**base, "ok": decision != "block", "reason": reason, "first_invalid_action": action["action_id"], "current_phase": action["phase"], "next_action": {"action_id": action["action_id"], "owner": action["owner"], "decision": decision, "why": reason, "reason": reason, "approval_required": bool(approval.get("required"))}}
    return {**base, "ok": True, "reason": "complete", "first_invalid_action": "", "current_phase": "read_only_acceptance", "next_action": {"action_id": "terminal.complete", "owner": "workflow", "decision": "reuse", "reason": "all_action_receipts_accepted", "approval_required": False}}
