#!/usr/bin/env python3
"""Read-only changed-file validation dependency graph shadow.

Ownership: combine existing changed-file, rule, capability, and dependency
authorities into a non-enforcing validation DAG projection. Non-goals: execute,
schedule, reuse receipts, cache, select, skip, reorder, or pass validation
nodes. State behavior: pure and process-local. Caller context: the workflow
orchestrator attaches this result only after its authoritative validation checks
have completed.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import rule_governance
import system_membership
from maintenance_capability_registry import (
    create_registry_read_view,
    parse_surface_map,
    query_registry_batch,
)
from maintenance_upgrade_governance import convergence_nodes_from_registry, validate_dependency_graph
from maintenance_convergence_runtime import load_terminal_receipts


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SIGNATURE_SCHEMA = "workflow_validation_dag_shadow.input_signature.v1"
ACCEPTANCE_PREDICATE_VERSION = "workflow_validation_dag_shadow.c3.v1"
SIGNATURE_FIELDS = (
    "changed_file_bytes",
    "owner_source",
    "command_contract",
    "validator_schema",
    "validation_arguments",
    "membership_authority",
    "rule_authority",
    "platform_environment",
    "acceptance_predicate",
)
VALID_STATUSES = {
    "would_execute",
    "would_block_dependency",
    "would_block_conflict",
    "would_defer_platform_scope",
    "invalid_signature",
    "unmapped_change",
    "cycle_detected",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_signature(paths: list[Path], *, root: Path = WORKSPACE_ROOT) -> str:
    rows: list[dict[str, str]] = []
    base = root.resolve()
    for path in sorted((Path(item).resolve(strict=False) for item in paths), key=str):
        try:
            relative = path.relative_to(base).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return ""
        rows.append({"path": relative, "sha256": digest})
    return _digest(rows) if rows else ""


def _authority_signature(snapshot: dict[str, Any], fields: tuple[str, ...]) -> str:
    if not snapshot.get("ok"):
        return ""
    selected = {field: snapshot.get(field) for field in fields}
    return _digest(selected) if all(field in snapshot for field in fields) else ""


def build_input_signature(fields: dict[str, Any]) -> dict[str, Any]:
    """Return one canonical C3 signature without persisting or reusing it."""

    normalized = {field: fields.get(field) for field in SIGNATURE_FIELDS}
    missing = [field for field in SIGNATURE_FIELDS if not normalized[field]]
    return {
        "schema": SIGNATURE_SCHEMA,
        "input_signature": _digest(normalized) if not missing else "",
        "signature_fields_complete": not missing,
        "signature_missing_fields": missing,
    }


def _normalize_changed_files(changed_files: list[str], *, root: Path = WORKSPACE_ROOT) -> tuple[list[str], str]:
    if not changed_files:
        return [], "changed_files_required"
    normalized: list[str] = []
    base = root.resolve()
    for raw in changed_files:
        value = str(raw or "").strip().replace("\\", "/")
        if not value:
            return [], "changed_file_empty"
        candidate = Path(value)
        if not candidate.is_absolute():
            relative = value.removeprefix("workspace/")
            candidate = base / relative
        try:
            relative_path = candidate.resolve(strict=False).relative_to(base)
        except ValueError:
            return [], "changed_file_outside_worktree"
        if ".git" in relative_path.parts:
            return [], "changed_file_git_metadata"
        canonical = f"workspace/{relative_path.as_posix()}"
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized, ""


def _empty_shadow(changed: list[str], *, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema": "workflow_validation_dag_shadow.v1",
        "ok": False,
        "reason": reason,
        "changed_files": changed,
        "node_count": 0,
        "status_counts": {status: 1},
        "nodes": [],
        "blockers": [{"status": status, "reason": reason}],
        "read_only": True,
        "enforcement": False,
        "execution_enabled": False,
        "receipt_reuse_enabled": False,
        "cache_enabled": False,
        "signature_fields_complete": False,
    }


def _batch_queries() -> list[dict[str, Any]]:
    return [
        {"system": "workflow", "term": "orchestrator", "limit": 20},
        {"system": "workflow", "term": "validation", "limit": 20},
        {"system": "workflow", "term": "maintenance upgrade", "limit": 20},
        {"system": "bridge", "term": "long_command_receipt", "limit": 20},
        # Exact validator references are separate queries because the
        # registry search treats multi-word terms as an intersection.  The
        # explicit rows below keep mandatory coverage tied to real owner
        # nodes instead of relying on a broad, truncated search result.
        {"system": "workflow", "term": "workflow_orchestrator.py", "limit": 20},
        {"system": "workflow", "term": "maintenance_control_plane_tests.py", "limit": 20},
    ]


def _batch_capability_ids(batch: dict[str, Any]) -> set[str]:
    return {
        str(item.get("capability_id") or "")
        for result in batch.get("results", [])
        if isinstance(result, dict)
        for item in result.get("items", [])
        if isinstance(item, dict) and str(item.get("capability_id") or "")
    }


def _normalize_contract_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    normalized = normalized.strip("`'\"").removeprefix("./")
    return normalized.removeprefix("workspace/")


def _validator_scripts(command: str) -> list[str]:
    """Extract every Python owner referenced by one validator contract."""

    return [_normalize_contract_path(item) for item in re.findall(
        r"(?:(?:python(?:3)?|py)(?:\s+-m)?\s+)?([A-Za-z0-9_./\\-]+\.py)",
        command,
        flags=re.IGNORECASE,
    )]


def _validator_rows(
    rows: list[dict[str, Any]], impact: dict[str, Any], selected_ids: set[str],
) -> list[dict[str, Any]]:
    """Add registry rows that explicitly own or expose mandatory validators."""

    scripts = [
        script
        for item in impact.get("affected", [])
        if isinstance(item, dict)
        for script in _validator_scripts(str(item.get("validator") or ""))
    ]
    if not scripts:
        return [row for row in rows if str(row.get("capability_id") or "") in selected_ids]
    selected = {
        str(row.get("capability_id") or ""): row
        for row in rows
        if str(row.get("capability_id") or "") in selected_ids
    }
    for row in rows:
        module_path = _normalize_contract_path(str(row.get("module_path") or ""))
        contract_text = " ".join(
            str(row.get(key) or "") for key in ("surface", "usual_entry")
        ).replace("\\", "/")
        if any(
            module_path == script
            or module_path.endswith("/" + script)
            or Path(script).name in contract_text
            for script in scripts
        ):
            selected[str(row.get("capability_id") or "")] = row
    return list(selected.values())


def _validator_coverage(
    nodes: list[dict[str, Any]], impact: dict[str, Any], rows: list[dict[str, Any]],
) -> list[str]:
    # Validator commands are owned by the existing maintenance registry.  Use
    # normalized module/action pairs for direct owners and the registry's
    # `usual_entry` contract for validators that intentionally validate a
    # facade (for example maintenance_control_plane_tests.py validating
    # bounded_output.py).  This keeps coverage a projection, not a second
    # validator catalog.
    direct: set[tuple[str, str]] = set()
    direct_by_name: dict[str, set[str]] = {}
    for node in nodes:
        owner = _normalize_contract_path(str(node.get("owner") or ""))
        action = str(node.get("action") or "").strip()
        if not owner or not action:
            continue
        direct.add((owner, action))
        direct_by_name.setdefault(Path(owner).name, set()).add(action)

    capability_ids = {str(node.get("capability_id") or "") for node in nodes}
    registry_text = {
        str(row.get("capability_id") or ""): " ".join(
            str(row.get(key) or "") for key in ("module_path", "surface", "usual_entry")
        )
        for row in rows
        if str(row.get("capability_id") or "") in capability_ids
    }

    def script_is_covered(script: str, command: str) -> bool:
        basename = Path(script).name
        action_tokens = {
            token.strip("`'\"(),;:&|")
            for token in re.split(r"\s+", command)
            if token.strip("`'\"(),;:&|")
        }
        requested_actions = {
            token for token in action_tokens if token and not token.endswith(".py")
        }
        owner_actions = direct_by_name.get(basename, set())
        if owner_actions and (not requested_actions or owner_actions.intersection(requested_actions)):
            return True
        for owner, action in direct:
            if owner.endswith("/" + script) or owner == script:
                if not requested_actions or action in requested_actions:
                    return True
        # A facade validator is authoritative when the selected registry
        # contract explicitly names the validator in its usual entry.
        return any(basename in text.replace("\\", "/") for text in registry_text.values())

    missing: list[str] = []
    for row in impact.get("affected", []):
        if not isinstance(row, dict):
            continue
        validator = str(row.get("validator") or "").strip()
        if not validator:
            continue
        scripts = _validator_scripts(validator)
        if not scripts:
            missing.append(validator)
            continue
        for script in scripts:
            if not script_is_covered(script, validator):
                missing.append(script)
    return sorted(set(missing))


def _node_projection(
    node: dict[str, Any], *, status: str, reason: str = "", signature: dict[str, Any] | None = None,
    receipt_readback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError("validation_dag_shadow_status_invalid")
    signature = signature or build_input_signature({})
    return {
        "node_id": str(node.get("node_id") or ""),
        "owner_ref": f"maintenance-capability:{str(node.get('capability_id') or '')}",
        "command_contract_ref": str(node.get("owner_contract_fingerprint") or ""),
        "dependencies": list(node.get("dependencies") or []),
        "reverse_validation": list(node.get("reverse_validation") or []),
        "conflict_group": str(node.get("conflict_group") or ""),
        "platform_scope": str(node.get("platform_scope") or "all"),
        "input_signature": str(signature.get("input_signature") or ""),
        "signature_fields_complete": bool(signature.get("signature_fields_complete")),
        "signature_missing_fields": list(signature.get("signature_missing_fields") or []),
        "receipt_readback": receipt_readback or {"status": "missing"},
        "shadow_status": status,
        "reason": reason,
    }


def _receipt_readback(node_id: str, input_signature: str, receipts: list[dict[str, Any]]) -> dict[str, Any]:
    receipt = next((item for item in receipts if str(item.get("node_id") or "") == node_id), None)
    if not receipt:
        return {"status": "missing"}
    if str(receipt.get("input_signature") or "") != input_signature:
        return {"status": "signature_mismatch", "receipt_ref": str(receipt.get("artifact_ref") or "")}
    if str(receipt.get("status") or "") not in {"healthy", "converged"}:
        return {"status": "terminal_not_success", "receipt_ref": str(receipt.get("artifact_ref") or "")}
    if not str(receipt.get("artifact_ref") or "") or receipt.get("readback_ok") is not True:
        return {"status": "readback_incomplete", "receipt_ref": str(receipt.get("artifact_ref") or "")}
    return {"status": "eligible", "receipt_ref": str(receipt["artifact_ref"])}


def _node_statuses(nodes: list[dict[str, Any]], graph: dict[str, Any], *, platforms: set[str]) -> dict[str, tuple[str, str]]:
    status = {str(node.get("node_id") or ""): ("would_execute", "") for node in nodes}
    if not graph.get("ok"):
        if graph.get("reason") == "dependency_cycle":
            cycle = set(graph.get("cycle") or [])
            for node_id in status:
                status[node_id] = ("cycle_detected", "dependency_cycle") if node_id in cycle else ("would_block_dependency", "dependency_cycle")
        else:
            blocked = {str(item.get("node_id") or "") for item in graph.get("missing_dependencies", []) if isinstance(item, dict)}
            for node_id in status:
                if node_id in blocked:
                    status[node_id] = ("would_block_dependency", str(graph.get("reason") or "dependency_invalid"))
        return status
    groups: dict[tuple[tuple[str, ...], str], list[str]] = {}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        scope = str(node.get("platform_scope") or "all")
        if scope != "all" and scope not in platforms:
            status[node_id] = ("would_defer_platform_scope", "platform_scope_not_current")
        if not str(node.get("owner_contract_fingerprint") or ""):
            status[node_id] = ("invalid_signature", "owner_contract_fingerprint_missing")
        conflict = str(node.get("conflict_group") or "")
        if conflict:
            groups.setdefault((tuple(sorted(str(item) for item in node.get("dependencies") or [])), conflict), []).append(node_id)
    for node_ids in groups.values():
        for node_id in sorted(node_ids)[1:]:
            if status[node_id][0] == "would_execute":
                status[node_id] = ("would_block_conflict", "conflict_group_shared_frontier")
    return status


def _attach_platform_scopes(nodes: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scopes = {
        f"{str(row.get('capability_id') or '')}:{action}": str(
            (row.get("maintenance") or {}).get("platform_scope") or "all"
        )
        for row in rows
        if isinstance(row.get("maintenance"), dict)
        for action in (row.get("maintenance") or {}).get("automatic_actions", [])
        if str(row.get("capability_id") or "") and str(action or "")
    }
    return [{**node, "platform_scope": scopes.get(str(node.get("node_id") or ""), "all")} for node in nodes]


def _signature_fields_for_node(
    node: dict[str, Any], *, changed_bytes_signature: str, registry_read_view: dict[str, Any],
    membership_snapshot: dict[str, Any], rule_snapshot: dict[str, Any], platform_scopes: set[str],
    validation_tier: str,
) -> dict[str, Any]:
    owner_path = WORKSPACE_ROOT / str(node.get("owner") or "")
    owner_file_signature = _file_signature([owner_path])
    return {
        "changed_file_bytes": changed_bytes_signature,
        "owner_source": _digest({
            "registry_source_signature": str(registry_read_view.get("source_signature") or ""),
            "owner_module_signature": owner_file_signature,
        }) if registry_read_view.get("source_signature") and owner_file_signature else "",
        "command_contract": str(node.get("owner_contract_fingerprint") or ""),
        "validator_schema": _digest({
            "schema": SIGNATURE_SCHEMA,
            "registry_index_schema": str(registry_read_view.get("index_schema") or ""),
            "validator_source_signature": owner_file_signature,
        }) if registry_read_view.get("index_schema") and owner_file_signature else "",
        "validation_arguments": _digest({
            "argv": list(node.get("command_argv") or []),
            "validation_tier": str(validation_tier or ""),
        }) if node.get("command_argv") and validation_tier else "",
        "membership_authority": _authority_signature(
            membership_snapshot, ("schema", "systems", "contracts", "impact_rule_count"),
        ),
        "rule_authority": _authority_signature(
            rule_snapshot, ("schema", "registry", "activation", "surfaces", "surface_count"),
        ),
        "platform_environment": _digest({
            "platform_scopes": sorted(platform_scopes),
            "python_platform": sys.platform,
        }) if platform_scopes and sys.platform else "",
        "acceptance_predicate": ACCEPTANCE_PREDICATE_VERSION,
    }


def build_validation_dag_shadow(
    changed_files: list[str],
    *,
    platform_scopes: set[str] | None = None,
    validation_tier: str = "full",
    membership_impact: dict[str, Any] | None = None,
    rule_impact: dict[str, Any] | None = None,
    membership_snapshot: dict[str, Any] | None = None,
    rule_snapshot: dict[str, Any] | None = None,
    registry_batch: dict[str, Any] | None = None,
    registry_rows: list[dict[str, Any]] | None = None,
    terminal_receipts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a non-executing DAG projection for one explicit changed-file set."""

    changed, error = _normalize_changed_files(changed_files)
    if error:
        return _empty_shadow(changed, status="invalid_signature", reason=error)
    membership = membership_impact or system_membership.impact(changed)
    if not membership.get("ok") or not membership.get("coverage_complete"):
        return _empty_shadow(changed, status="unmapped_change", reason="system_membership_coverage_incomplete")
    rules = rule_impact or rule_governance.impact(changed)
    if not rules.get("ok") or rules.get("unmatched"):
        return _empty_shadow(changed, status="invalid_signature", reason="rule_governance_coverage_incomplete")

    view = create_registry_read_view()
    batch = registry_batch or query_registry_batch(_batch_queries(), read_view=view)
    read_view = batch.get("read_view") if isinstance(batch.get("read_view"), dict) else view.summary()
    if not batch.get("ok") or not read_view.get("source_signature"):
        return _empty_shadow(changed, status="invalid_signature", reason="maintenance_registry_read_view_incomplete")
    capability_ids = _batch_capability_ids(batch)
    rows = registry_rows if registry_rows is not None else parse_surface_map()
    selected_rows = _validator_rows(rows, rules, capability_ids)
    active_platforms = set(platform_scopes or {"linux", "wsl"})
    nodes = _attach_platform_scopes(convergence_nodes_from_registry(selected_rows), selected_rows)
    graph = validate_dependency_graph(nodes)
    statuses = _node_statuses(nodes, graph, platforms=active_platforms)
    missing_validators = _validator_coverage(nodes, rules, selected_rows)
    membership_authority = membership_snapshot or system_membership.snapshot()
    rule_authority = rule_snapshot or rule_governance.snapshot()
    changed_bytes_signature = _file_signature(
        [WORKSPACE_ROOT / item.removeprefix("workspace/") for item in changed]
    )
    receipts = terminal_receipts if terminal_receipts is not None else load_terminal_receipts()
    projected_nodes = []
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        status, reason = statuses[node_id]
        signature = build_input_signature(_signature_fields_for_node(
            node,
            changed_bytes_signature=changed_bytes_signature,
            registry_read_view=read_view,
            membership_snapshot=membership_authority,
            rule_snapshot=rule_authority,
            platform_scopes=active_platforms,
            validation_tier=validation_tier,
        ))
        if not signature["signature_fields_complete"]:
            status = "invalid_signature"
            reason = "signature_fields_incomplete"
        projected_nodes.append(_node_projection(
            node, status=status, reason=reason, signature=signature,
            receipt_readback=_receipt_readback(node_id, str(signature["input_signature"]), receipts),
        ))
    counts: dict[str, int] = {}
    for node in projected_nodes:
        value = str(node["shadow_status"])
        counts[value] = counts.get(value, 0) + 1
    blockers = [
        {"status": node["shadow_status"], "node_id": node["node_id"], "reason": node["reason"]}
        for node in projected_nodes
        if node["shadow_status"] != "would_execute"
    ]
    if missing_validators:
        blockers.append({"status": "invalid_signature", "reason": "mandatory_validator_not_graph_covered", "validators": missing_validators})
    receipt_status_counts: dict[str, int] = {}
    for node in projected_nodes:
        value = str((node.get("receipt_readback") or {}).get("status") or "missing")
        receipt_status_counts[value] = receipt_status_counts.get(value, 0) + 1
    return {
        "schema": "workflow_validation_dag_shadow.v1",
        "ok": bool(graph.get("ok")) and not blockers,
        "reason": "" if not blockers else str(blockers[0].get("reason") or "validation_dag_shadow_blocked"),
        "changed_files": changed,
        "membership_impact_ref": str(membership.get("schema") or ""),
        "rule_impact_ref": str(rules.get("schema") or ""),
        "signature_schema": SIGNATURE_SCHEMA,
        "registry_read_view": deepcopy(read_view),
        "node_count": len(projected_nodes),
        "status_counts": counts,
        "nodes": projected_nodes,
        "blockers": blockers,
        "receipt_observation": {"status_counts": receipt_status_counts, "eligible_count": receipt_status_counts.get("eligible", 0)},
        "read_only": True,
        "enforcement": False,
        "execution_enabled": False,
        "receipt_reuse_enabled": False,
        "cache_enabled": False,
        "signature_fields_complete": bool(projected_nodes) and all(
            bool(node.get("signature_fields_complete")) for node in projected_nodes
        ),
    }


def attach_dag_shadow(payload: dict[str, Any], shadow: dict[str, Any]) -> dict[str, Any]:
    observed = deepcopy(payload)
    projection = deepcopy(shadow)
    # A nested `ok` is consumed by generic closeout aggregators as owner health.
    # Keep fail-closed readiness separate so an observational gap cannot rewrite
    # the authoritative validator outcome.
    projection["shadow_ok"] = bool(projection.get("ok"))
    projection["activation_ready"] = bool(projection.get("ok"))
    projection["ok"] = True
    observed["validation_dag_shadow"] = projection
    return observed


def compact_dag_shadow(value: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "schema", "signature_schema", "ok", "reason", "changed_files", "node_count", "status_counts",
        "read_only", "enforcement", "execution_enabled", "receipt_reuse_enabled",
        "cache_enabled", "signature_fields_complete", "shadow_ok", "activation_ready",
    )
    projected = {field: value.get(field) for field in fields}
    projected["full_result_ref"] = "command:python _bridge/workflow_orchestrator.py validate --full"
    return projected
