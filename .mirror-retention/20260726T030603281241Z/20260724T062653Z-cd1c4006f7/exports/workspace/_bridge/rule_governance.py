#!/usr/bin/env python3
"""Rule authority and lifecycle governance for the Codex work environment.

Ownership: inventory rule-bearing surfaces, validate authority/lifecycle metadata,
and map changed files to rule owners without copying rule text.
Non-goals: execute business actions, replace AGENTS discovery, own tool policy,
or mutate another owner's source.
State behavior: read-only; retirement-plan emits proposals only.
Caller context: workflow preflight/finalization, maintenance discovery, and audits.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
WORKTREE_ROOT = ROOT.parent
REGISTRY = ROOT / "_bridge" / "policies" / "rule_authority_registry.json"
LEGACY_CODEX_HOME = "C:/Users/45543/.codex"


def resolve_codex_home() -> Path:
    configured = str(os.environ.get("CODEX_HOME") or "").strip()
    if configured:
        normalized = configured.replace("/", "\\").casefold()
        if sys.platform != "win32" or not normalized.startswith(("\\\\wsl.localhost\\", "\\\\wsl$\\")):
            return Path(configured).expanduser().resolve()
    if sys.platform == "win32":
        return (Path.home() / ".codex").resolve()
    declarative = ROOT.parent / "codex-home"
    if declarative.is_dir():
        return declarative.resolve()
    return (Path.home() / ".codex").resolve()


CODEX_HOME = resolve_codex_home()
MAX_INLINE_ITEMS = 40
RUNTIME_PROBE_TIMEOUT_SECONDS = 60

# This is deliberately a fixed allow-list rather than registry-provided commands.
# Rule metadata remains declarative; it cannot acquire command execution authority.
RUNTIME_ENFORCEMENT_PROBES = {
    "agents_instruction_mirror": {
        "script": "agents_rule_mirror.py",
        "args": ("validate",),
        "rules": (
            "platform.precedence",
            "workspace.instructions",
            "workspace.bridge_subtree.instructions",
        ),
    },
    "task_route_contract": {
        "script": "task_route_contract.py",
        "args": ("validate",),
        "rules": ("workflow.task_contract",),
    },
    "workflow_route": {
        "script": "workflow_orchestrator.py",
        "args": ("validate",),
        "rules": (
            "workflow.route_plan",
            "workflow.execution_decision",
            "workflow.execution_economy",
        ),
    },
    "mcp_capability_route": {
        "script": "mcp_capability_routes.py",
        "args": ("validate",),
        "rules": ("tool.mcp_priority",),
    },
    "online_access_gate": {
        "script": "online_access_gate.py",
        "args": ("validate",),
        "rules": ("external.online_access",),
    },
}

REQUIRED_SURFACE_FIELDS = (
    "rule_id",
    "scope",
    "source",
    "owner",
    "trigger_type",
    "enforcement_point",
    "enforcement_strength",
    "validator",
    "lifecycle",
    "precedence",
    "last_verified",
)

REQUIRED_ACTIVATION_FIELDS = (
    "rule_id",
    "effect",
    "layer_role",
    "consumers",
    "verification_kind",
    "acceptance",
)

SCOPE_LAYER_ROLES = {
    "machine": {"instruction_boundary"},
    "workspace": {"scoped_instruction"},
    "workflow": {"admission_gate", "advisory_signal", "route_decision", "execution_adapter", "reconciliation_gate"},
    "system": {"lifecycle_gate"},
    "owner": {"owner_policy", "owner_discovery", "structured_schema", "result_acceptance", "output_policy"},
    "skill": {"lifecycle_gate", "advisory_router"},
    "memory": {"lifecycle_gate"},
    "foreign_subtree": {"scoped_instruction"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_registry(path: Path = REGISTRY) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def posix_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        try:
            return "worktree:" + path.resolve().relative_to(WORKTREE_ROOT.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()


def source_pattern_path(source: str) -> tuple[Path, str]:
    normalized = str(source or "").replace("\\", "/")
    if normalized.casefold().startswith("worktree:"):
        suffix = normalized.split(":", 1)[1].lstrip("/")
        mapped = WORKTREE_ROOT / suffix
        return mapped, mapped.as_posix()
    legacy = LEGACY_CODEX_HOME.casefold()
    if normalized.casefold() == legacy or normalized.casefold().startswith(legacy + "/"):
        suffix = normalized[len(LEGACY_CODEX_HOME):].lstrip("/")
        mapped = CODEX_HOME / suffix
        return mapped, mapped.as_posix()
    if len(normalized) > 2 and normalized[1:3] == ":/":
        return Path(normalized), normalized
    return ROOT, normalized


def expand_source(source: str) -> list[Path]:
    base, pattern = source_pattern_path(source)
    if base != ROOT:
        if any(char in pattern for char in "*?["):
            anchor = Path(pattern[: pattern.find("*")]).parent
            return sorted(anchor.glob(Path(pattern).name)) if anchor.exists() else []
        return [base] if base.exists() else []
    if any(char in pattern for char in "*?["):
        return sorted(path for path in ROOT.glob(pattern) if path.is_file())
    path = ROOT / pattern
    return [path] if path.is_file() else []


def pattern_matches(path: str, pattern: str) -> bool:
    candidate = path.replace("\\", "/")
    if str(pattern or "").casefold().startswith("worktree:"):
        expected = str(pattern).split(":", 1)[1].lstrip("/")
        if not candidate.casefold().startswith("worktree:"):
            return False
        candidate = candidate.split(":", 1)[1].lstrip("/")
        return fnmatch.fnmatchcase(candidate.casefold(), expected.casefold())
    base, normalized = source_pattern_path(pattern)
    if base != ROOT:
        if candidate.casefold().startswith("worktree:"):
            return fnmatch.fnmatchcase(candidate.casefold(), posix_path(base).casefold())
        canonical_pattern = str(base.resolve()).replace("\\", "/")
        return fnmatch.fnmatchcase(str(Path(candidate).resolve()).replace("\\", "/").casefold(), canonical_pattern.casefold())
    return fnmatch.fnmatchcase(candidate.casefold(), normalized.casefold())


def changed_path_candidates(path: str) -> list[str]:
    normalized = str(path or "").replace("\\", "/").strip()
    if normalized.casefold() == "agents.md":
        return ["worktree:AGENTS.md"]
    candidates = [normalized] if normalized else []
    if normalized.casefold().startswith("workspace/"):
        candidates.append(normalized[len("workspace/"):])
    elif normalized and not normalized.casefold().startswith("worktree:"):
        candidates.append("worktree:" + normalized.lstrip("/"))
    marker = "/codex-workspace/workspace/"
    index = normalized.casefold().find(marker)
    if index >= 0:
        candidates.append(normalized[index + len(marker):])
    return list(dict.fromkeys(item for item in candidates if item))


def discover_rule_surfaces() -> list[dict[str, Any]]:
    candidates: set[Path] = set()
    candidates.update(path for path in CODEX_HOME.glob("AGENTS*.md") if path.is_file())
    candidates.update(path for path in WORKTREE_ROOT.glob("AGENTS*.md") if path.is_file())
    candidates.update(path for path in ROOT.glob("AGENTS*.md") if path.is_file())
    candidates.update(path for path in ROOT.glob("_tools/**/AGENTS*.md") if path.is_file())
    for pattern in ("_bridge/*policy*.py", "_bridge/*contract*.py", "_bridge/*governance*.py"):
        candidates.update(path for path in ROOT.glob(pattern) if path.is_file() and "test" not in path.stem.casefold())
    candidates.update(
        path
        for path in (
            ROOT / "_bridge" / "task_route_contract.py",
            ROOT / "_bridge" / "intent_routing.py",
            ROOT / "_bridge" / "workflow_orchestrator.py",
            ROOT / "_bridge" / "execution_route_pack.py",
            ROOT / "_bridge" / "workflow_owner_facade.py",
            ROOT / "_bridge" / "workflow_finalization.py",
            ROOT / "_bridge" / "system_membership.py",
            ROOT / "_bridge" / "structured_task_envelope.py",
            ROOT / "_bridge" / "bounded_output.py",
            ROOT / "_bridge" / "codegraph_query_runtime.py",
            ROOT / "_bridge" / "online_access_gate.py",
            ROOT / "_bridge" / "docs" / "mcp_capability_matrix.md",
            ROOT / "_bridge" / "docs" / "maintenance_surface_map.md",
            ROOT / "_bridge" / "mobile_openclaw_bridge" / "permission_table.json",
        )
        if path.is_file()
    )
    return [
        {
            "path": posix_path(path),
            "bytes": path.stat().st_size,
            "foreign_scoped": "_tools/" in posix_path(path),
        }
        for path in sorted(candidates, key=lambda item: posix_path(item).casefold())
    ]


def classify_discovered(registry: dict[str, Any], discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    surfaces = registry.get("surfaces") if isinstance(registry.get("surfaces"), list) else []
    patterns = registry.get("coverage_patterns") if isinstance(registry.get("coverage_patterns"), list) else []
    rows: list[dict[str, Any]] = []
    for item in discovered:
        path = str(item.get("path") or "")
        matched_rules = [str(row.get("rule_id")) for row in surfaces if pattern_matches(path, str(row.get("source") or ""))]
        matched_patterns = [str(row.get("classification")) for row in patterns if pattern_matches(path, str(row.get("pattern") or ""))]
        if matched_rules:
            coverage = "registered"
        elif matched_patterns:
            coverage = "category_covered"
        elif item.get("foreign_scoped"):
            coverage = "foreign_scoped"
        else:
            coverage = "unregistered"
        rows.append({**item, "coverage": coverage, "rule_ids": matched_rules, "classifications": matched_patterns})
    return rows


def migration_summary(registry: dict[str, Any]) -> dict[str, Any]:
    ledger = registry.get("migration_ledger") if isinstance(registry.get("migration_ledger"), dict) else {}
    items = ledger.get("items") if isinstance(ledger.get("items"), list) else []
    counts: dict[str, int] = {}
    for item in items:
        disposition = str(item.get("disposition") or "unknown")
        counts[disposition] = counts.get(disposition, 0) + 1
    return {
        "item_count": len(items),
        "expected_count": int(ledger.get("global_rule_count") or 0) + int(ledger.get("workspace_rule_count") or 0),
        "by_disposition": counts,
    }


def activation_summary(registry: dict[str, Any]) -> dict[str, Any]:
    surfaces = registry.get("surfaces") if isinstance(registry.get("surfaces"), list) else []
    contracts = registry.get("activation_contracts") if isinstance(registry.get("activation_contracts"), list) else []
    by_effect: dict[str, int] = {}
    by_strength: dict[str, int] = {}
    for item in contracts:
        effect = str(item.get("effect") or "unknown")
        by_effect[effect] = by_effect.get(effect, 0) + 1
    for item in surfaces:
        strength = str(item.get("enforcement_strength") or "unknown")
        by_strength[strength] = by_strength.get(strength, 0) + 1
    return {
        "surface_count": len(surfaces),
        "contract_count": len(contracts),
        "coverage_complete": {str(item.get("rule_id") or "") for item in surfaces}
        == {str(item.get("rule_id") or "") for item in contracts},
        "by_effect": by_effect,
        "by_enforcement_strength": by_strength,
        "runtime_claim_count": sum(
            count for strength, count in by_strength.items()
            if strength in {"owner_enforced", "closeout_enforced"}
        ),
        "non_runtime_count": sum(
            count for strength, count in by_strength.items()
            if strength in {"instruction_only", "advisory", "observational"}
        ),
    }


def _bounded_probe_detail(value: str) -> str:
    normalized = str(value or "").strip().replace("\x00", "")
    return normalized[-800:]


def _run_fixed_probe(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    script = ROOT / "_bridge" / str(spec["script"])
    command = [sys.executable, str(script), *[str(item) for item in spec["args"]]]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RUNTIME_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "name": name,
            "ok": False,
            "rules": list(spec["rules"]),
            "command": ["python", f"_bridge/{spec['script']}", *spec["args"]],
            "detail": _bounded_probe_detail(str(exc)),
        }
    output = completed.stdout if completed.returncode == 0 else completed.stderr or completed.stdout
    result = {
        "name": name,
        "ok": completed.returncode == 0,
        "rules": list(spec["rules"]),
        "command": ["python", f"_bridge/{spec['script']}", *spec["args"]],
        "exit_code": completed.returncode,
    }
    if completed.returncode == 0:
        try:
            receipt = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError):
            receipt = {}
        if isinstance(receipt, dict):
            result["receipt"] = {
                key: receipt.get(key)
                for key in ("schema", "generated_at", "check_count", "passed_count")
                if receipt.get(key) is not None
            }
    else:
        result["detail"] = _bounded_probe_detail(output)
    return result


def runtime_enforcement_probes(
    registry: dict[str, Any], *, discovered: list[dict[str, Any]] | None = None
) -> dict[str, dict[str, Any]]:
    """Return bounded evidence for declarative rules without executing registry text."""
    results = {name: _run_fixed_probe(name, spec) for name, spec in RUNTIME_ENFORCEMENT_PROBES.items()}
    surfaces = registry.get("surfaces") if isinstance(registry.get("surfaces"), list) else []
    foreign_surface = next((item for item in surfaces if item.get("rule_id") == "foreign.nested_agents"), None)
    classified = discovered if discovered is not None else classify_discovered(registry, discover_rule_surfaces())
    foreign_rows = [item for item in classified if item.get("foreign_scoped")]
    invalid_foreign = [
        str(item.get("path") or "")
        for item in foreign_rows
        if item.get("coverage") not in {"registered", "foreign_scoped"}
    ]
    results["foreign_scope"] = {
        "name": "foreign_scope",
        "ok": bool(foreign_surface)
        and str(foreign_surface.get("lifecycle") or "") == "foreign_scoped"
        and not invalid_foreign,
        "rules": ["foreign.nested_agents"],
        "foreign_surface_count": len(foreign_rows),
        "invalid_foreign_sources": invalid_foreign,
    }
    return results


def runtime_probe_by_rule(results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {rule_id: result for result in results.values() for rule_id in result.get("rules") or []}


def snapshot(*, full: bool = False) -> dict[str, Any]:
    registry = load_registry()
    discovered = classify_discovered(registry, discover_rule_surfaces())
    surfaces = registry.get("surfaces") if isinstance(registry.get("surfaces"), list) else []
    registered_paths = [posix_path(path) for row in surfaces for path in expand_source(str(row.get("source") or ""))]
    payload = {
        "schema": "rule_governance.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "registry": str(REGISTRY),
        "surface_count": len(surfaces),
        "registered_path_count": len(set(registered_paths)),
        "discovered_count": len(discovered),
        "unregistered_count": sum(1 for item in discovered if item.get("coverage") == "unregistered"),
        "foreign_scoped_count": sum(1 for item in discovered if item.get("foreign_scoped")),
        "foreign_scoped_registered_count": sum(1 for item in surfaces if item.get("lifecycle") == "foreign_scoped"),
        "migration": migration_summary(registry),
        "activation": activation_summary(registry),
        "surfaces": surfaces if full else surfaces[:MAX_INLINE_ITEMS],
        "discovered": discovered if full else discovered[:MAX_INLINE_ITEMS],
        "detail_rule": "Use --full only for explicit rule inventory review; default output is bounded.",
    }
    if not full:
        payload["truncated"] = len(surfaces) > MAX_INLINE_ITEMS or len(discovered) > MAX_INLINE_ITEMS
    return payload


def doctor(*, full: bool = False) -> dict[str, Any]:
    registry = load_registry()
    surfaces = registry.get("surfaces") if isinstance(registry.get("surfaces"), list) else []
    discovered = classify_discovered(registry, discover_rule_surfaces())
    runtime_probes = runtime_enforcement_probes(registry, discovered=discovered)
    probes_by_rule = runtime_probe_by_rule(runtime_probes)
    valid_lifecycles = set(registry.get("valid_lifecycles") or [])
    valid_dispositions = set(registry.get("valid_dispositions") or [])
    valid_effects = set(registry.get("valid_effects") or [])
    valid_layer_roles = set(registry.get("valid_layer_roles") or [])
    valid_verification_kinds = set(registry.get("valid_verification_kinds") or [])
    valid_enforcement_strengths = set(registry.get("valid_enforcement_strengths") or [])
    activation_contracts = registry.get("activation_contracts") if isinstance(registry.get("activation_contracts"), list) else []
    issues: list[dict[str, Any]] = []
    for scope, roles in sorted(SCOPE_LAYER_ROLES.items()):
        for role in sorted(roles - valid_layer_roles):
            issues.append(
                {
                    "severity": "blocker",
                    "code": "scope_layer_role_unregistered",
                    "scope": scope,
                    "layer_role": role,
                }
            )
    seen_ids: set[str] = set()
    for index, surface in enumerate(surfaces):
        missing = [field for field in REQUIRED_SURFACE_FIELDS if surface.get(field) in (None, "")]
        rule_id = str(surface.get("rule_id") or f"surface_{index}")
        if missing:
            issues.append({"severity": "blocker", "code": "surface_fields_missing", "rule_id": rule_id, "fields": missing})
        if rule_id in seen_ids:
            issues.append({"severity": "blocker", "code": "duplicate_rule_id", "rule_id": rule_id})
        seen_ids.add(rule_id)
        lifecycle = str(surface.get("lifecycle") or "")
        if lifecycle not in valid_lifecycles:
            issues.append({"severity": "blocker", "code": "invalid_lifecycle", "rule_id": rule_id, "lifecycle": lifecycle})
        strength = str(surface.get("enforcement_strength") or "")
        if strength not in valid_enforcement_strengths:
            issues.append({"severity": "blocker", "code": "invalid_enforcement_strength", "rule_id": rule_id, "enforcement_strength": strength})
        if strength in {"instruction_only", "observational"}:
            probe = probes_by_rule.get(rule_id)
            if not probe or not probe.get("ok"):
                issues.append(
                    {
                        "severity": "risk",
                        "code": "runtime_enforcement_probe_failed",
                        "rule_id": rule_id,
                        "effect": "declared strength remains authoritative; its fixed read-only verification evidence is unavailable or failing",
                        "enforcement_strength": strength,
                        "probe": probe or {"status": "missing"},
                    }
                )
        if lifecycle in {"active", "deprecated"} and not expand_source(str(surface.get("source") or "")):
            issues.append({"severity": "blocker", "code": "source_missing", "rule_id": rule_id, "source": surface.get("source")})
        if lifecycle == "retired" and not str(surface.get("replacement") or ""):
            issues.append({"severity": "risk", "code": "retired_without_replacement", "rule_id": rule_id})
    contracts_by_id: dict[str, dict[str, Any]] = {}
    for index, contract in enumerate(activation_contracts):
        rule_id = str(contract.get("rule_id") or f"activation_{index}")
        missing = [field for field in REQUIRED_ACTIVATION_FIELDS if contract.get(field) in (None, "", [])]
        if missing:
            issues.append({"severity": "blocker", "code": "activation_fields_missing", "rule_id": rule_id, "fields": missing})
        if rule_id in contracts_by_id:
            issues.append({"severity": "blocker", "code": "duplicate_activation_contract", "rule_id": rule_id})
        contracts_by_id[rule_id] = contract
        effect = str(contract.get("effect") or "")
        role = str(contract.get("layer_role") or "")
        verification_kind = str(contract.get("verification_kind") or "")
        if effect not in valid_effects:
            issues.append({"severity": "blocker", "code": "invalid_rule_effect", "rule_id": rule_id, "effect": effect})
        if role not in valid_layer_roles:
            issues.append({"severity": "blocker", "code": "invalid_layer_role", "rule_id": rule_id, "layer_role": role})
        if verification_kind not in valid_verification_kinds:
            issues.append({"severity": "blocker", "code": "invalid_verification_kind", "rule_id": rule_id, "verification_kind": verification_kind})
        surface = next((item for item in surfaces if str(item.get("rule_id") or "") == rule_id), None)
        if surface:
            scope = str(surface.get("scope") or "")
            if role not in SCOPE_LAYER_ROLES.get(scope, set()):
                issues.append({"severity": "blocker", "code": "layer_scope_mismatch", "rule_id": rule_id, "scope": scope, "layer_role": role})
        if effect == "advisory" and role not in {"advisory_signal", "advisory_router"}:
            issues.append({"severity": "blocker", "code": "advisory_rule_has_mandatory_role", "rule_id": rule_id, "layer_role": role})
        if effect != "advisory" and role in {"advisory_signal", "advisory_router"}:
            issues.append({"severity": "blocker", "code": "mandatory_rule_has_advisory_role", "rule_id": rule_id, "layer_role": role})
        for consumer in contract.get("consumers") or []:
            if not expand_source(str(consumer or "")) and str((surface or {}).get("lifecycle") or "") != "foreign_scoped":
                issues.append({"severity": "blocker", "code": "activation_consumer_missing", "rule_id": rule_id, "consumer": consumer})
    surface_ids = {str(item.get("rule_id") or "") for item in surfaces}
    contract_ids = set(contracts_by_id)
    for rule_id in sorted(surface_ids - contract_ids):
        issues.append({"severity": "blocker", "code": "activation_contract_missing", "rule_id": rule_id})
    for rule_id in sorted(contract_ids - surface_ids):
        issues.append({"severity": "blocker", "code": "activation_contract_orphaned", "rule_id": rule_id})
    ledger = registry.get("migration_ledger") if isinstance(registry.get("migration_ledger"), dict) else {}
    ledger_items = ledger.get("items") if isinstance(ledger.get("items"), list) else []
    ledger_ids: set[str] = set()
    for item in ledger_items:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in ledger_ids:
            issues.append({"severity": "blocker", "code": "invalid_migration_id", "id": item_id})
        ledger_ids.add(item_id)
        if str(item.get("disposition") or "") not in valid_dispositions:
            issues.append({"severity": "blocker", "code": "invalid_disposition", "id": item_id})
        if str(item.get("target_rule_id") or "") not in seen_ids:
            issues.append({"severity": "blocker", "code": "migration_target_missing", "id": item_id, "target_rule_id": item.get("target_rule_id")})
    expected = int(ledger.get("global_rule_count") or 0) + int(ledger.get("workspace_rule_count") or 0)
    if len(ledger_items) != expected:
        issues.append({"severity": "blocker", "code": "migration_ledger_incomplete", "expected": expected, "actual": len(ledger_items)})
    for item in discovered:
        if item.get("coverage") == "unregistered":
            issues.append({"severity": "risk", "code": "unregistered_rule_surface", "path": item.get("path")})
    blocking = [item for item in issues if item.get("severity") == "blocker"]
    payload = {
        "schema": "rule_governance.doctor.v1",
        "ok": not blocking,
        "generated_at": now_iso(),
        "status": "blocker" if blocking else ("risk" if issues else "ok"),
        "issue_count": len(issues),
        "blocking_count": len(blocking),
        "issues": issues if full else issues[:MAX_INLINE_ITEMS],
        "migration": migration_summary(registry),
        "activation": activation_summary(registry),
        "runtime_enforcement": {
            "declared_strengths_are_not_rewritten": True,
            "verification_mode": "fixed_read_only_probes",
            "verified_rule_count": sum(
                len(probe.get("rules") or []) for probe in runtime_probes.values() if probe.get("ok")
            ),
            "failed_probe_count": sum(1 for probe in runtime_probes.values() if not probe.get("ok")),
            "probes": list(runtime_probes.values()),
        },
        "detail_rule": "Issues are bounded by default; use --full for an explicit complete inventory.",
    }
    if not full:
        payload["truncated"] = len(issues) > MAX_INLINE_ITEMS
    return payload


def plan(source: str = "", rule_id: str = "") -> dict[str, Any]:
    registry = load_registry()
    surfaces = registry.get("surfaces") if isinstance(registry.get("surfaces"), list) else []
    selected = [
        item
        for item in surfaces
        if (not rule_id or str(item.get("rule_id") or "") == rule_id)
        and (not source or pattern_matches(source, str(item.get("source") or "")) or pattern_matches(str(item.get("source") or ""), source))
    ]
    return {
        "schema": "rule_governance.plan.v1",
        "ok": bool(selected),
        "generated_at": now_iso(),
        "source": source,
        "rule_id": rule_id,
        "items": selected[:MAX_INLINE_ITEMS],
        "required_steps": [
            "identify_authoritative_rule_owner",
            "record_retained_migrated_merged_superseded_retired_or_rejected_disposition",
            "update_enforcement_and_validator_in_same_change",
            "run_changed_file_rule_impact_before_closeout",
        ],
    }


def impact(changed: Iterable[str]) -> dict[str, Any]:
    changed_items = list(dict.fromkeys(str(item) for item in changed if str(item).strip()))
    registry = load_registry()
    surfaces = registry.get("surfaces") if isinstance(registry.get("surfaces"), list) else []
    activation_contracts = (
        registry.get("activation_contracts")
        if isinstance(registry.get("activation_contracts"), list)
        else []
    )
    consumers_by_rule = {
        str(item.get("rule_id") or ""): [str(value) for value in item.get("consumers") or []]
        for item in activation_contracts
        if isinstance(item, dict)
    }
    rows: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for value in changed_items:
        normalized = value.replace("\\", "/")
        candidates = changed_path_candidates(normalized)
        matches: list[tuple[dict[str, Any], str]] = []
        for item in surfaces:
            rule_id = str(item.get("rule_id") or "")
            if any(pattern_matches(candidate, str(item.get("source") or "")) for candidate in candidates):
                matches.append((item, "authority_source"))
                continue
            if any(
                pattern_matches(candidate, pattern)
                for candidate in candidates
                for pattern in consumers_by_rule.get(rule_id, [])
            ):
                matches.append((item, "enforcement_consumer"))
        if not matches:
            unmatched.append(normalized)
            continue
        for item, match_kind in matches:
            rows.append(
                {
                    "changed": normalized,
                    "rule_id": item.get("rule_id"),
                    "owner": item.get("owner"),
                    "validator": item.get("validator"),
                    "enforcement_point": item.get("enforcement_point"),
                    "match_kind": match_kind,
                }
            )
    return {
        "schema": "rule_governance.impact.v1",
        "ok": bool(rows) or not changed_items,
        "generated_at": now_iso(),
        "rule_change_required": bool(rows),
        "affected": rows[:MAX_INLINE_ITEMS],
        "unmatched": unmatched[:MAX_INLINE_ITEMS],
        "required_receipt": "rule_governance=ok" if rows else "",
    }


def retirement_plan(rule_id: str, replacement: str, reason: str) -> dict[str, Any]:
    registry = load_registry()
    surface = next((item for item in registry.get("surfaces", []) if item.get("rule_id") == rule_id), None)
    blockers: list[str] = []
    if not surface:
        blockers.append("unknown_rule_id")
    if not reason.strip():
        blockers.append("reason_required")
    if surface and surface.get("lifecycle") == "active" and not replacement.strip():
        blockers.append("active_rule_requires_replacement_or_explicit_no_replacement_evidence")
    return {
        "schema": "rule_governance.retirement_plan.v1",
        "ok": not blockers,
        "generated_at": now_iso(),
        "rule": surface or {},
        "replacement": replacement,
        "reason": reason,
        "blockers": blockers,
        "required_evidence": [
            "replacement_or_no_replacement_decision",
            "all_enforcement_points_migrated_or_removed",
            "validators_and_guidance_no_longer_activate_rule",
            "negative_tombstone_prevents_reintroduction",
            "closeout_receipt_rule_governance_ok",
        ],
    }


def validate() -> dict[str, Any]:
    doc = doctor(full=True)
    snap = snapshot(full=False)
    consumer_impact = impact(["_bridge/intent_resource_router.py"])
    checks = [
        {"name": "registry_schema", "ok": load_registry().get("schema") == "rule_authority_registry.v1"},
        {"name": "doctor_has_no_blockers", "ok": bool(doc.get("ok")), "detail": doc.get("blocking_count")},
        {
            "name": "runtime_enforcement_probes_pass",
            "ok": int(doc.get("runtime_enforcement", {}).get("failed_probe_count") or 0) == 0,
            "detail": doc.get("runtime_enforcement", {}).get("probes", []),
        },
        {"name": "legacy_rules_dispositioned", "ok": snap.get("migration", {}).get("item_count") == snap.get("migration", {}).get("expected_count"), "detail": snap.get("migration")},
        {"name": "active_surfaces_registered", "ok": int(snap.get("surface_count") or 0) >= 20, "detail": snap.get("surface_count")},
        {"name": "activation_contracts_cover_surfaces", "ok": bool(snap.get("activation", {}).get("coverage_complete")), "detail": snap.get("activation")},
        {
            "name": "enforcement_strength_is_explicit_for_every_surface",
            "ok": sum((snap.get("activation", {}).get("by_enforcement_strength") or {}).values()) == int(snap.get("surface_count") or 0),
            "detail": snap.get("activation", {}).get("by_enforcement_strength"),
        },
        {
            "name": "foreign_agents_classified",
            "ok": int(snap.get("foreign_scoped_registered_count") or 0) >= 1,
            "detail": {
                "registered": snap.get("foreign_scoped_registered_count"),
                "materialized": snap.get("foreign_scoped_count"),
            },
        },
        {
            "name": "enforcement_consumers_trigger_rule_impact",
            "ok": any(
                item.get("rule_id") == "resource.source_and_satisfaction"
                and item.get("match_kind") == "enforcement_consumer"
                for item in consumer_impact.get("affected", [])
            ),
            "detail": consumer_impact.get("affected", []),
        },
    ]
    return {
        "schema": "rule_governance.validate.v1",
        "ok": all(item.get("ok") for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "issues": doc.get("issues", [])[:MAX_INLINE_ITEMS],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rule authority and lifecycle governance")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "doctor"):
        item = sub.add_parser(name)
        item.add_argument("--full", action="store_true")
    p = sub.add_parser("plan")
    p.add_argument("--source", default="")
    p.add_argument("--rule-id", default="")
    p = sub.add_parser("impact")
    p.add_argument("--changed", action="append", default=[])
    p = sub.add_parser("retirement-plan")
    p.add_argument("--rule-id", required=True)
    p.add_argument("--replacement", default="")
    p.add_argument("--reason", required=True)
    sub.add_parser("validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "snapshot":
        payload = snapshot(full=args.full)
    elif args.command == "doctor":
        payload = doctor(full=args.full)
    elif args.command == "plan":
        payload = plan(args.source, args.rule_id)
    elif args.command == "impact":
        payload = impact(args.changed)
    elif args.command == "retirement-plan":
        payload = retirement_plan(args.rule_id, args.replacement, args.reason)
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
