#!/usr/bin/env python3
"""Read-only authority-reference projection for scoped authorization.

Ownership: compose bounded stable signatures from existing workflow, membership,
rule, maintenance, state-write, dependency-intelligence, and runtime-health
owners. Non-goals: deciding permissions, issuing permits, copying owner catalogs,
or writing runtime state. State behavior: read-only with a short in-process cache.
Caller context: authorization PEP callers request a current snapshot while
scoped_authorization remains the sole PDP and persistence authority.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import dependency_change_intelligence_process
import maintenance_capability_registry
import rule_governance
import state_write_authority
import system_membership


CACHE_SECONDS = 60.0
_CACHE: tuple[float, dict[str, Any]] | None = None


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _file_signature(paths: list[Path]) -> str:
    rows = []
    for path in sorted(paths):
        try:
            rows.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        except OSError:
            rows.append({"path": str(path), "sha256": "missing"})
    return _digest(rows)


def dependency_snapshot() -> dict[str, Any]:
    owner = dependency_change_intelligence_process
    validation = owner.validate()
    contracts = [path for path in owner.CONTRACT_ROOT.glob("*.json") if path.is_file()]
    baselines = [path for path in owner.DEFAULT_STATE_ROOT.glob("*/last_validated.json") if path.is_file()]
    ok = bool(validation.get("ok"))
    return {
        "signature": _digest({
            "contracts": _file_signature(contracts),
            "validated_baselines": _file_signature(baselines),
            "profile_count": validation.get("registry_profile_count"),
            "proposal_status_contract": validation.get("proposal_status_contract"),
        }),
        "status": "ok" if ok else "incompatible",
        "authorization_effect": "neutral" if ok else "incompatible",
        "authority_ref": "dependency_change_intelligence_process.validate+last_validated",
    }


def _base_snapshot(*, force_fresh: bool = False) -> dict[str, Any]:
    global _CACHE
    now = time.monotonic()
    if not force_fresh and _CACHE and now - _CACHE[0] < CACHE_SECONDS:
        return json.loads(json.dumps(_CACHE[1]))
    membership = system_membership.snapshot()
    rules = rule_governance.snapshot()
    state_write = state_write_authority.observed_state_signature()
    dependency = dependency_snapshot()
    runtime = {
        "membership_ok": bool(membership.get("ok")),
        "rule_snapshot_ok": bool(rules.get("ok")),
        "state_write_signature_available": bool(state_write.get("signature")),
        "active_state_writer": any(
            bool(item.get("active_lease"))
            for item in (state_write.get("coordination") or {}).values()
            if isinstance(item, dict)
        ),
    }
    runtime_ok = all((runtime["membership_ok"], runtime["rule_snapshot_ok"], runtime["state_write_signature_available"]))
    sources = {
        "system_membership": {
            "signature": _digest({"systems": membership.get("systems", []), "impact_rule_count": membership.get("impact_rule_count")}),
            "status": "ok" if membership.get("ok") else "unavailable",
            "authorization_effect": "neutral" if membership.get("ok") else "unavailable",
            "authority_ref": "system_membership.snapshot",
        },
        "rule_governance": {
            "signature": _digest({"surface_count": rules.get("surface_count"), "activation": rules.get("activation", {})}),
            "status": "ok" if rules.get("ok") else "unavailable",
            "authorization_effect": "neutral" if rules.get("ok") else "unavailable",
            "authority_ref": "rule_governance.snapshot",
        },
        "maintenance_capability": {
            "signature": maintenance_capability_registry.source_signature(),
            "status": "ok", "authorization_effect": "neutral",
            "authority_ref": "maintenance_capability_registry.source_signature",
        },
        "state_write_authority": {
            "signature": str(state_write.get("signature") or ""),
            "status": "ok" if state_write.get("signature") else "unavailable",
            "authorization_effect": "neutral" if state_write.get("signature") else "unavailable",
            "authority_ref": "state_write_authority.observed_state_signature",
        },
        "dependency_intelligence": dependency,
        "runtime_health": {
            "signature": _digest(runtime),
            "status": "ok" if runtime_ok else "unavailable",
            "authorization_effect": "neutral" if runtime_ok else "unavailable",
            "authority_ref": "authorization_environment_provider.runtime_health",
        },
    }
    result = {"schema": "authorization_environment_base.v1", "ok": all(item["status"] == "ok" for item in sources.values()), "sources": sources}
    _CACHE = (now, result)
    return json.loads(json.dumps(result))


def snapshot(
    *, workflow_semantic_hash: str, owner: str, owner_capability_signature: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Return one complete snapshot specialized to a caller's stable contract."""

    base = _base_snapshot(force_fresh=force_fresh)
    sources = dict(base["sources"])
    sources["workflow"] = {
        "signature": workflow_semantic_hash, "status": "ok", "authorization_effect": "neutral",
        "authority_ref": "caller.workflow_semantic_hash",
    }
    sources["owner_capability"] = {
        "signature": owner_capability_signature, "status": "ok", "authorization_effect": "neutral",
        "authority_ref": f"owner:{owner}",
    }
    return {
        "schema": "authorization_environment_snapshot.v1",
        "ok": bool(base.get("ok")) and bool(workflow_semantic_hash) and bool(owner_capability_signature),
        "workflow_semantic_hash": workflow_semantic_hash,
        "authorization_semantic_signature": _digest({"workflow": workflow_semantic_hash, "owner": owner, "capability": owner_capability_signature}),
        "required_sources": sorted(sources),
        "sources": sources,
    }


def clear_cache() -> None:
    global _CACHE
    _CACHE = None
