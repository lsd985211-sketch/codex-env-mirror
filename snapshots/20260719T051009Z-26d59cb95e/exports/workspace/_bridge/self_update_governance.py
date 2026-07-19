#!/usr/bin/env python3
"""Read-only federated evolution health surface for the Codex environment.

Ownership:
- Derives system health owners from ``system_membership`` and augments them with
  focused feedback checks that are not system-member health commands.
- Builds a stable, dependency-aware change set that links changed files,
  affected systems, owner checks, receipts, and closeout evidence.

Non-goals:
- Does not edit skills, memories, prompts, AGENTS files, or resource policy.
- Does not replace owner doctors; it summarizes their results and points back to
  their repair/update commands.

State behavior:
- Source-read-only. It may refresh derived indexes and run owner
  validate/doctor commands, but it never applies business or source repairs.

Caller context:
- Use after repeated workflow/tool/resource mistakes, before broad environment
  governance, and during closeout when changed-file evidence or a failure
  suggests a system contract, rule, skill, memory, or tool route may be stale.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = Path(__file__).resolve().parent
RUNTIME_DIR = BRIDGE / "runtime" / "self_update_governance"
RECENT_RECEIPT_TTL_SECONDS = 180


SYSTEM_ORDER = (
    "bridge",
    "drafts",
    "mail",
    "mcp",
    "memory",
    "network",
    "office",
    "records",
    "resource",
    "skills",
    "startup",
    "workflow",
)

# Environment-mirror health is verified after closeout publication. Running the
# backup owner here would make an intentionally stale mirror block its refresh.
PRE_CLOSEOUT_EXCLUDED_SYSTEMS = {"backup"}

FOCUSED_OWNER_SPECS: dict[str, dict[str, Any]] = {
    "skill_freshness": {
        "system": "skills",
        "command": ["skill_lifecycle_governance.py", "refresh"],
        "severity": "risk",
        "timeout": 90,
        "source": "focused_feedback",
    },
    "skills": {
        "system": "skills",
        "command": ["skill_orchestrator.py", "validate"],
        "severity": "risk",
        "timeout": 90,
        "source": "focused_feedback",
    },
    "skill_usage": {
        "system": "skills",
        "command": ["skill_orchestrator.py", "usage-summary"],
        "severity": "advisory",
        "timeout": 90,
        "source": "focused_feedback",
    },
    "memory": {
        "system": "memory",
        "command": ["memory_governance.py", "doctor"],
        "severity": "risk",
        "timeout": 90,
        "source": "focused_feedback",
    },
    "workflow": {
        "system": "workflow",
        "command": ["workflow_orchestrator.py", "validate"],
        "severity": "risk",
        "timeout": 90,
        "source": "focused_feedback",
    },
    "resource_broker": {
        "system": "resource",
        "command": ["resource_broker.py", "validate"],
        "severity": "risk",
        "timeout": 90,
        "source": "focused_feedback",
    },
    "resource_strategy": {
        "system": "resource",
        "command": ["resource_source_strategy.py", "validate"],
        "severity": "risk",
        "timeout": 90,
        "source": "focused_feedback",
    },
    "resource_process": {
        "system": "resource",
        "command": ["resource_process_doctor.py", "validate"],
        "severity": "risk",
        "timeout": 90,
        "source": "focused_feedback",
    },
}

COMMAND_OWNER_ALIASES = {
    "skill_lifecycle_governance.py": "skill_freshness",
    "skill_orchestrator.py": "skills",
    "memory_governance.py": "memory",
    "workflow_orchestrator.py": "workflow",
    "resource_process_doctor.py": "resource_process",
}

SYSTEM_FOCUSED_ORDER = {
    "skills": ("skill_freshness", "skills", "skill_usage"),
    "resource": ("resource_broker", "resource_strategy", "resource_process"),
}

SURFACE_SYSTEM_ALIASES = {
    "audio": "audio",
    "music": "audio",
    "bridge": "bridge",
    "mobile": "bridge",
    "draft": "drafts",
    "review": "drafts",
    "mail": "mail",
    "email": "mail",
    "mcp": "mcp",
    "tool": "mcp",
    "memory": "memory",
    "pmb": "memory",
    "network": "network",
    "proxy": "network",
    "office": "office",
    "document": "office",
    "record": "records",
    "records": "records",
    "log": "records",
    "resource": "resource",
    "download": "resource",
    "package": "resource",
    "skill": "skills",
    "skills": "skills",
    "startup": "startup",
    "config": "startup",
    "session": "startup",
    "workflow": "workflow",
    "routing": "workflow",
    "rule": "workflow",
    "governance": "workflow",
    "hardware": "hardware",
    "usb": "hardware",
}


def normalized_owner_command(values: Iterable[Any]) -> list[str]:
    command = [str(value) for value in values if str(value or "").strip()]
    if not command:
        return []
    first = command[0].replace("\\", "/")
    if first.startswith("_bridge/"):
        first = first[len("_bridge/") :]
    command[0] = first
    return command


def build_owner_specs() -> dict[str, dict[str, Any]]:
    """Build the owner catalog from membership health commands plus focused checks."""

    derived_by_system: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    discovered_systems = list(SYSTEM_ORDER)
    try:
        from system_membership import snapshot as membership_snapshot

        membership = membership_snapshot()
        contracts = membership.get("contracts", {}) if isinstance(membership, dict) else {}
        discovered_systems = list(membership.get("systems", SYSTEM_ORDER)) if isinstance(membership, dict) else list(SYSTEM_ORDER)
        for system in discovered_systems:
            contract = contracts.get(system, {}) if isinstance(contracts, dict) else {}
            health_commands = contract.get("health_commands", []) if isinstance(contract, dict) else []
            for item in health_commands:
                if not isinstance(item, dict):
                    continue
                compatibility_args = item.get("compatibility_args")
                command = normalized_owner_command(
                    compatibility_args if isinstance(compatibility_args, list) and compatibility_args else item.get("args", [])
                )
                if not command:
                    continue
                executable = Path(command[0]).name
                name = COMMAND_OWNER_ALIASES.get(executable) or str(item.get("name") or Path(executable).stem)
                spec = dict(FOCUSED_OWNER_SPECS.get(name) or {})
                spec.update(
                    {
                        "system": str(system),
                        "command": spec.get("command") or command,
                        "severity": str(item.get("severity") or spec.get("severity") or "risk"),
                        "timeout": int(
                            item.get("compatibility_timeout")
                            if isinstance(compatibility_args, list) and compatibility_args
                            else item.get("timeout") or spec.get("timeout") or 90
                        ),
                        "source": (
                            "system_membership.health_commands.compatibility_args"
                            if isinstance(compatibility_args, list) and compatibility_args
                            else "system_membership.health_commands"
                        ),
                    }
                )
                derived_by_system.setdefault(str(system), []).append((name, spec))
    except Exception:
        derived_by_system = {}

    specs: dict[str, dict[str, Any]] = {
        "system_membership": {
            "system": "workflow",
            "command": ["system_membership.py", "validate"],
            "severity": "risk",
            "timeout": 90,
            "source": "federated_authority",
        }
    }
    owner_system_order = list(SYSTEM_ORDER)
    owner_system_order.extend(
        system
        for system in discovered_systems
        if system not in owner_system_order and system not in PRE_CLOSEOUT_EXCLUDED_SYSTEMS
    )
    for system in owner_system_order:
        focused_names = SYSTEM_FOCUSED_ORDER.get(system, ())
        for name in focused_names:
            specs[name] = dict(FOCUSED_OWNER_SPECS[name])
        for name, spec in derived_by_system.get(system, []):
            if name in focused_names:
                continue
            specs.setdefault(name, spec)

    for name, spec in FOCUSED_OWNER_SPECS.items():
        specs.setdefault(name, dict(spec))
    return specs


OWNER_SPECS = build_owner_specs()
OWNER_COMMANDS: dict[str, list[str]] = {
    name: list(spec["command"])
    for name, spec in OWNER_SPECS.items()
}
OWNER_NAMES = tuple(OWNER_COMMANDS)
OWNER_SYSTEM_ORDER = tuple(
    dict.fromkeys(str(spec.get("system")) for spec in OWNER_SPECS.values() if str(spec.get("system") or ""))
)

RECEIPT_OWNER_ALIASES = {
    "codex_config_guard": "config_guard",
    "codex_config_projection": "config_projection",
    "mcp_session_doctor": "mcp_session",
    "workflow_orchestrator": "workflow",
    "workflow_owner_facade": "owner_facade",
}
MEMBERSHIP_META_PATHS = {
    "_bridge/system_membership.py",
    "_bridge/system_membership_tests.py",
}


def safe_count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def compact_text_items(values: Iterable[Any] | None, *, limit: int = 100) -> list[str]:
    items: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def membership_change_impact(changed_files: Iterable[Any] | None) -> dict[str, Any]:
    changed = compact_text_items(changed_files, limit=50)
    if not changed:
        return {"ok": True, "affected_systems": [], "contract_upgrade_required": False}
    try:
        from system_membership import impact

        payload = impact(changed)
        return payload if isinstance(payload, dict) else {"ok": False, "affected_systems": []}
    except Exception as exc:
        return {
            "ok": False,
            "affected_systems": [],
            "contract_upgrade_required": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def rule_change_impact(changed_files: Iterable[Any] | None) -> dict[str, Any]:
    changed = compact_text_items(changed_files, limit=50)
    if not changed:
        return {"ok": True, "rule_change_required": False, "affected": []}
    try:
        from rule_governance import impact

        payload = impact(changed)
        return payload if isinstance(payload, dict) else {"ok": False, "rule_change_required": False}
    except Exception as exc:
        return {
            "ok": False,
            "rule_change_required": False,
            "affected": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def systems_from_text(values: Iterable[Any] | None) -> list[str]:
    systems: list[str] = []
    for value in values or []:
        tokens = re.findall(r"[a-z0-9]+", str(value or "").casefold().replace("_", "-"))
        for token in tokens:
            system = SURFACE_SYSTEM_ALIASES.get(token)
            if system and system not in systems:
                systems.append(system)
    return systems


def resolve_affected_systems(
    *,
    changed_files: Iterable[Any] | None = None,
    changed_surfaces: Iterable[Any] | None = None,
    task_kind: str = "",
    outcome: str = "unknown",
    config_changed: bool = False,
    major_change: bool = False,
    validated_owners: Iterable[str] | None = None,
) -> dict[str, Any]:
    changed = compact_text_items(changed_files, limit=50)
    surfaces = compact_text_items(changed_surfaces, limit=50)
    membership = membership_change_impact(changed)
    rules = rule_change_impact(changed)
    systems: list[str] = []
    evidence: list[dict[str, Any]] = []

    membership_systems = list(membership.get("affected_systems", []))
    normalized_changed = [str(item).replace("\\", "/").casefold() for item in changed]
    non_meta_changed = [
        item
        for item, normalized in zip(changed, normalized_changed)
        if normalized not in MEMBERSHIP_META_PATHS
    ]
    validated = {normalize_receipt_owner(item) for item in (validated_owners or [])}
    narrowed_membership: dict[str, Any] = {}
    if "system_membership" in validated and non_meta_changed and len(non_meta_changed) < len(changed):
        candidate = membership_change_impact(non_meta_changed)
        if candidate.get("ok") and candidate.get("coverage_complete") and candidate.get("affected_systems"):
            narrowed_membership = candidate
            membership_systems = list(candidate.get("affected_systems", []))
            evidence.append(
                {
                    "source": "validated_membership_authority_plus_specific_changed_files",
                    "systems": list(membership_systems),
                    "excluded_meta_files": sorted(set(changed) - set(non_meta_changed)),
                }
            )

    for system in membership_systems:
        if system in OWNER_SYSTEM_ORDER and system not in systems:
            systems.append(system)
    if systems:
        evidence.append({"source": "changed_file_membership_impact", "systems": list(systems)})

    explicit_systems = systems_from_text(surfaces)
    for system in explicit_systems:
        if system not in systems:
            systems.append(system)
    if explicit_systems:
        evidence.append({"source": "structured_changed_surfaces", "systems": explicit_systems})

    if config_changed and "startup" not in systems:
        systems.append("startup")
        evidence.append({"source": "structured_config_changed", "systems": ["startup"]})

    if rules.get("rule_change_required") and "workflow" not in systems:
        systems.append("workflow")
        evidence.append({"source": "changed_file_rule_impact", "systems": ["workflow"]})

    if not systems and task_kind:
        inferred = systems_from_text([task_kind])
        systems.extend(inferred)
        if inferred:
            evidence.append({"source": "task_kind_fallback", "systems": inferred})

    if not systems and outcome in {"failed", "blocked", "partial"}:
        systems.append("workflow")
        evidence.append({"source": "failed_outcome_fallback", "systems": ["workflow"]})

    if major_change and not systems:
        systems.extend(OWNER_SYSTEM_ORDER)
        evidence.append({"source": "major_change_full_fallback", "systems": list(OWNER_SYSTEM_ORDER)})

    return {
        "systems": systems,
        "evidence": evidence,
        "membership_impact": {
            "ok": bool(membership.get("ok")),
            "contract_upgrade_required": bool(membership.get("contract_upgrade_required")),
            "affected_systems": membership.get("affected_systems", []),
            "selection_affected_systems": membership_systems,
            "narrowed_by_authority_receipt": bool(narrowed_membership),
        },
        "rule_impact": {
            "ok": bool(rules.get("ok")),
            "rule_change_required": bool(rules.get("rule_change_required")),
            "affected_count": len(rules.get("affected", [])),
        },
    }


def owners_for_systems(systems: Iterable[str]) -> list[str]:
    requested = {str(system) for system in systems}
    return [
        name
        for name, spec in OWNER_SPECS.items()
        if str(spec.get("system")) in requested and name != "system_membership"
    ]


def select_owners_for_change(
    *,
    changed_files: Iterable[Any] | None = None,
    changed_surfaces: Iterable[Any] | None = None,
    task_kind: str = "",
    outcome: str = "unknown",
    config_changed: bool = False,
    major_change: bool = False,
    validated_owners: Iterable[str] | None = None,
) -> list[str]:
    resolved = resolve_affected_systems(
        changed_files=changed_files,
        changed_surfaces=changed_surfaces,
        task_kind=task_kind,
        outcome=outcome,
        config_changed=config_changed,
        major_change=major_change,
        validated_owners=validated_owners,
    )
    owners = owners_for_systems(resolved["systems"])
    if resolved["membership_impact"].get("contract_upgrade_required"):
        owners.insert(0, "system_membership")
    if resolved["rule_impact"].get("rule_change_required") and "rule_governance" not in owners:
        insert_at = 1 if owners and owners[0] == "system_membership" else 0
        owners.insert(insert_at, "rule_governance")
    return list(dict.fromkeys(owners))


def build_change_set(
    *,
    selected_owners: Iterable[str] | None = None,
    changed_files: Iterable[Any] | None = None,
    changed_surfaces: Iterable[Any] | None = None,
    task_kind: str = "",
    outcome: str = "unknown",
    config_changed: bool = False,
    major_change: bool = False,
    validated_owners: Iterable[str] | None = None,
) -> dict[str, Any]:
    changed = compact_text_items(changed_files, limit=50)
    surfaces = compact_text_items(changed_surfaces, limit=50)
    resolved = resolve_affected_systems(
        changed_files=changed,
        changed_surfaces=surfaces,
        task_kind=task_kind,
        outcome=outcome,
        config_changed=config_changed,
        major_change=major_change,
        validated_owners=validated_owners,
    )
    if selected_owners is None:
        has_change_context = bool(changed or surfaces or task_kind or config_changed or major_change or outcome in {"failed", "blocked", "partial"})
        owners = select_owners_for_change(
            changed_files=changed,
            changed_surfaces=surfaces,
            task_kind=task_kind,
            outcome=outcome,
            config_changed=config_changed,
            major_change=major_change,
            validated_owners=validated_owners,
        ) if has_change_context else list(OWNER_NAMES)
    else:
        owners = normalize_owner_names(selected_owners)

    identity_payload = {
        "task_kind": task_kind,
        "outcome": outcome,
        "changed_files": sorted(changed),
        "changed_surfaces": sorted(surfaces),
        "systems": resolved["systems"],
        "owners": owners,
        "config_changed": bool(config_changed),
        "major_change": bool(major_change),
    }
    digest = hashlib.sha256(json.dumps(identity_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    authority_steps = [name for name in owners if name in {"system_membership", "rule_governance"}]
    owner_steps: list[dict[str, Any]] = []
    for name in owners:
        spec = OWNER_SPECS[name]
        owner_steps.append(
            {
                "step_id": f"owner:{name}",
                "owner": name,
                "system": spec.get("system"),
                "phase": "authority" if name in authority_steps else "domain_validation",
                "depends_on": [] if name in authority_steps else [f"owner:{item}" for item in authority_steps],
                "command": list(spec.get("command", [])),
                "severity": spec.get("severity", "risk"),
                "acceptance": {
                    "owner_ok": True,
                    "required_receipt": f"{name}=ok",
                    "result_consumed": True,
                },
            }
        )
    return {
        "schema": "self_update_governance.change_set.v1",
        "change_id": f"evo-{digest}",
        "task_kind": task_kind,
        "outcome": outcome,
        "changed_files": changed,
        "changed_surfaces": surfaces,
        "affected_systems": resolved["systems"],
        "selection_evidence": resolved["evidence"],
        "impact": {
            "membership": resolved["membership_impact"],
            "rules": resolved["rule_impact"],
        },
        "selected_owners": owners,
        "owner_steps": owner_steps,
        "execution_policy": {
            "read_only_checks_only": True,
            "max_parallel_owners": 4,
            "single_retry_layer": "owner_contract",
            "repairs_remain_owner_managed": True,
            "closeout_consumes_receipts": True,
        },
    }


def run_owner(name: str, command: list[str], timeout: int = 90) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, str(BRIDGE / command[0]), *command[1:]],
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "execution_state": "transport_failure",
            "error_class": type(exc).__name__,
            "error": str(exc)[:500],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        payload = {
            "ok": False,
            "error_class": "json_decode_failed",
            "error": str(exc),
            "stdout_tail": proc.stdout[-1000:],
        }
    if not isinstance(payload, dict):
        payload = {"ok": proc.returncode == 0, "payload": payload}
    payload.setdefault("ok", proc.returncode == 0)
    payload_ok = bool(payload.get("ok"))
    if payload.get("error_class") == "json_decode_failed":
        execution_state = "parse_failure"
    elif proc.returncode == 0 and payload_ok:
        execution_state = "ok"
    else:
        execution_state = "owner_reported_failure"
    return {
        "name": name,
        "ok": payload_ok and proc.returncode == 0,
        "execution_state": execution_state,
        "returncode": proc.returncode,
        "command": " ".join(command),
        "payload": payload,
        "stderr_tail": proc.stderr[-1000:],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def normalize_owner_names(selected_owners: Iterable[str] | None) -> list[str]:
    if selected_owners is None:
        return list(OWNER_NAMES)
    selected: list[str] = []
    for raw in selected_owners:
        for item in str(raw or "").split(","):
            name = item.strip()
            if not name or name in selected:
                continue
            if name not in OWNER_COMMANDS:
                raise ValueError(f"unknown self-update owner: {name}")
            selected.append(name)
    return selected


def load_validation_receipt(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return {}
    owner_hint = ""
    source = "inline_json"
    candidate = text
    if "=" in text and not text.startswith("{"):
        owner_hint, candidate = (part.strip() for part in text.split("=", 1))
    if candidate.startswith("{"):
        payload = json.loads(candidate)
    else:
        path = Path(candidate).expanduser()
        if path.is_file():
            source = str(path.resolve())
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = {
                "ok": candidate.lower() in {"ok", "pass", "passed", "true", "validated"},
                "status": candidate,
            }
            source = "inline_status"
    if not isinstance(payload, dict):
        payload = {"ok": False, "status": "invalid_receipt_payload", "payload": payload}
    if owner_hint:
        payload.setdefault("owner", owner_hint)
    payload["receipt_source"] = source
    return payload


def normalize_receipt_owner(owner: Any) -> str:
    name = str(owner or "").strip()
    return RECEIPT_OWNER_ALIASES.get(name, name)


def validation_receipt_index(values: Iterable[str | dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    for value in values or []:
        receipt = load_validation_receipt(value)
        owner = normalize_receipt_owner(receipt.get("owner"))
        if owner in OWNER_COMMANDS and "ok" in receipt:
            receipt["owner"] = owner
            receipts[owner] = receipt
    return receipts


def result_from_receipt(name: str, command: list[str], receipt: dict[str, Any]) -> dict[str, Any]:
    ok = bool(receipt.get("ok"))
    payload = receipt.get("payload")
    if not isinstance(payload, dict):
        payload = dict(receipt)
    payload.setdefault("ok", ok)
    return {
        "name": name,
        "ok": ok,
        "execution_state": "validation_receipt_reused",
        "returncode": 0 if ok else 1,
        "command": " ".join(command),
        "payload": payload,
        "stderr_tail": "",
        "elapsed_ms": 0.0,
        "receipt": {
            "source": receipt.get("receipt_source", "provided"),
            "status": receipt.get("status", "ok" if ok else "failed"),
            "age_seconds": receipt.get("receipt_age_seconds"),
            "cache_key": receipt.get("cache_key", ""),
        },
    }


def change_evidence_fingerprint(change_set: dict[str, Any]) -> str:
    files: list[dict[str, Any]] = []
    for raw in change_set.get("changed_files", []):
        text = str(raw or "").strip()
        if not text:
            continue
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        try:
            stat = candidate.stat()
            files.append({"path": text, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        except OSError:
            files.append({"path": text, "missing": True})
    evidence = {
        "change_id": change_set.get("change_id"),
        "task_kind": change_set.get("task_kind"),
        "outcome": change_set.get("outcome"),
        "changed_surfaces": change_set.get("changed_surfaces", []),
        "files": files,
    }
    return hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def owner_receipt_cache_path(change_set: dict[str, Any], name: str, command: list[str]) -> tuple[Path, str]:
    evidence_fingerprint = change_evidence_fingerprint(change_set)
    command_fingerprint = hashlib.sha256(json.dumps(command, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    cache_key = f"{change_set.get('change_id', 'unknown')}:{name}:{command_fingerprint}:{evidence_fingerprint}"
    filename = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24] + ".json"
    return RUNTIME_DIR / filename, cache_key


def load_recent_owner_receipt(change_set: dict[str, Any], name: str, command: list[str]) -> dict[str, Any]:
    path, cache_key = owner_receipt_cache_path(change_set, name, command)
    try:
        age = max(0.0, time.time() - path.stat().st_mtime)
        if age > RECENT_RECEIPT_TTL_SECONDS:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("cache_key") != cache_key or payload.get("ok") is not True:
        return {}
    owner_payload = payload.get("payload")
    if not isinstance(owner_payload, dict) or owner_payload.get("ok") is not True:
        return {}
    return {
        "owner": name,
        "ok": True,
        "status": "ok",
        "payload": owner_payload,
        "receipt_source": str(path),
        "receipt_age_seconds": round(age, 1),
        "cache_key": cache_key,
    }


def persist_recent_owner_receipt(change_set: dict[str, Any], name: str, command: list[str], result: dict[str, Any]) -> None:
    if result.get("ok") is not True or result.get("execution_state") != "ok":
        return
    payload = result.get("payload")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return
    path, cache_key = owner_receipt_cache_path(change_set, name, command)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "self_update_governance.validation_receipt_cache.v1",
        "ok": True,
        "generated_at": time.time(),
        "owner": name,
        "change_id": change_set.get("change_id"),
        "command": command,
        "cache_key": cache_key,
        "payload": payload,
    }
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def owner_review_items(
    surface: str,
    payload: dict[str, Any],
    next_action: str,
    *,
    change_id: str = "",
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in (
        "review_items",
        "failures",
        "issues",
        "signals",
        "risk_signals",
        "warn_signals",
        "actions",
        "proposals",
    ):
        values = payload.get(key)
        if isinstance(values, list):
            candidates.extend(item for item in values if isinstance(item, dict))
    code_counts: dict[str, int] = {}
    for index, item in enumerate(candidates[:30], start=1):
        code = str(item.get("code") or item.get("id") or item.get("kind") or f"item-{index}")
        code_counts[code] = code_counts.get(code, 0) + 1
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(candidates[:30], start=1):
        code = str(item.get("code") or item.get("id") or item.get("kind") or f"item-{index}")
        summary = str(
            item.get("message")
            or item.get("detail")
            or item.get("reason")
            or item.get("summary")
            or item.get("error")
            or code
        )
        stable_key = f"{surface}:{code}:{summary}"
        if stable_key in seen:
            continue
        seen.add(stable_key)
        covered_by = "work_notes" if code == "ephemeral_work_notes_pending_closeout" else ""
        attributes = {
            "code": code,
            "severity": str(item.get("severity") or "advisory"),
            "count": safe_count(item.get("count")),
        }
        if change_id:
            attributes["change_id"] = change_id
        for key in (
            "profile",
            "system",
            "member",
            "owner",
            "path",
            "pid",
            "root_pid",
            "root_instance_count",
            "working_set_mb",
            "warn_budget",
            "risk_budget",
        ):
            value = item.get(key)
            if value not in (None, "", [], {}):
                attributes[key] = value
        source_item_id = f"self_update:{surface}:{code}"
        if code_counts.get(code, 0) > 1:
            identity = json.dumps(
                {
                    "summary": summary,
                    "profile": item.get("profile"),
                    "system": item.get("system"),
                    "member": item.get("member"),
                    "path": item.get("path"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            source_item_id += f":{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:10]}"
        items.append(
            {
                "source_item_id": source_item_id,
                "title": str(item.get("title") or f"{surface}: {code}"),
                "summary": summary,
                "source_url": str(item.get("source_url") or item.get("path") or item.get("source") or ""),
                "trust_tier": "local_owner_doctor",
                "freshness_class": "closeout_current_run",
                "proposed_destination_namespace": f"workflow.self_update.{surface}",
                "approval_action": str(
                    item.get("next_action")
                    or item.get("approval_action")
                    or item.get("manual_action")
                    or next_action
                ),
                "required_checks": [
                    "Use the named owner doctor/validate output first",
                    "Apply repairs only through owner repair-plan or exact user approval",
                ],
                "attributes": attributes,
                "covered_by": covered_by,
            }
        )
    return items


def stale_signals(results: dict[str, dict[str, Any]], *, change_id: str = "") -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if "skill_freshness" in results:
        skill_refresh = results["skill_freshness"].get("payload", {})
        blocked_changes = [
            item for item in skill_refresh.get("changes", [])
            if item.get("change_kind") != "removed" and item.get("detail", {}).get("routing_eligible") is False
        ]
        if blocked_changes:
            signals.append(
                {
                    "surface": "skills",
                    "code": "changed_skills_quarantined",
                    "severity": "risk",
                    "detail": [item.get("name") for item in blocked_changes],
                    "next_action": "Run skill_lifecycle_governance.py doctor/repair-plan and repair the changed skill before routing it.",
                }
            )
    if "skill_usage" in results and int(results["skill_usage"].get("payload", {}).get("record_count") or 0) == 0:
        signals.append(
            {
                "surface": "skills",
                "code": "skill_usage_feedback_absent",
                "severity": "warn",
                "detail": "Skill usage log is empty; stale or underperforming skills cannot be detected from outcomes.",
                "next_action": "Record usage through skill_orchestrator.py record-usage during closeout for non-simple skill-routed work.",
            }
        )
    memory_result = results.get("memory", {})
    memory_payload = memory_result.get("payload", {})
    memory_status = str(memory_payload.get("status") or memory_payload.get("doctor_status") or "")
    if memory_result.get("ok") and memory_status not in {"", "ok"}:
        severity = "warn" if memory_status == "advisory" and memory_payload.get("ok") else "risk"
        next_action = "Run memory_governance.py doctor/repair-plan and review the listed items before any memory write."
        concrete_items = owner_review_items("memory", memory_payload, next_action, change_id=change_id)
        signals.append(
            {
                "surface": "memory",
                "code": "memory_governance_not_ok",
                "severity": severity,
                "detail": f"{len(concrete_items)} concrete memory governance item(s) require disposition.",
                "next_action": next_action,
                "review_items": concrete_items,
            }
        )
    for key in results:
        if key == "skill_usage":
            continue
        if key not in results:
            continue
        result = results[key]
        if not result.get("ok"):
            next_action = f"Use {OWNER_COMMANDS[key][0]} doctor/validate output as the owner repair entrypoint."
            concrete_items = owner_review_items(
                key,
                result.get("payload", {}),
                next_action,
                change_id=change_id,
            )
            execution_state = str(result.get("execution_state") or "unknown_failure")
            if concrete_items:
                code = "owner_reported_issues"
                detail = f"Owner returned {len(concrete_items)} concrete issue(s)."
            else:
                code = "owner_evidence_unavailable"
                detail = (
                    result.get("stderr_tail")
                    or result.get("payload", {}).get("error")
                    or result.get("error")
                    or f"Owner evidence unavailable ({execution_state})."
                )
            signals.append(
                {
                    "surface": key,
                    "code": code,
                    "severity": "warn" if OWNER_SPECS.get(key, {}).get("severity") == "advisory" else "risk",
                    "detail": detail,
                    "execution_state": execution_state,
                    "next_action": next_action,
                    "review_items": concrete_items,
                    "change_id": change_id,
                }
            )
    return signals


def snapshot(
    selected_owners: Iterable[str] | None = None,
    validation_receipts: Iterable[str | dict[str, Any]] | None = None,
    *,
    changed_files: Iterable[Any] | None = None,
    changed_surfaces: Iterable[Any] | None = None,
    task_kind: str = "",
    outcome: str = "unknown",
    config_changed: bool = False,
    major_change: bool = False,
    reuse_recent_receipts: bool = True,
    force_fresh: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    receipts = validation_receipt_index(validation_receipts)
    change_set = build_change_set(
        selected_owners=selected_owners,
        changed_files=changed_files,
        changed_surfaces=changed_surfaces,
        task_kind=task_kind,
        outcome=outcome,
        config_changed=config_changed,
        major_change=major_change,
        validated_owners=receipts,
    )
    owner_names = list(change_set["selected_owners"])
    unordered: dict[str, dict[str, Any]] = {}
    live_names: list[str] = []
    for name in owner_names:
        if name in receipts:
            unordered[name] = result_from_receipt(name, OWNER_COMMANDS[name], receipts[name])
        elif reuse_recent_receipts and not force_fresh:
            cached = load_recent_owner_receipt(change_set, name, OWNER_COMMANDS[name])
            if cached:
                unordered[name] = result_from_receipt(name, OWNER_COMMANDS[name], cached)
            else:
                live_names.append(name)
        else:
            live_names.append(name)
    if live_names:
        with ThreadPoolExecutor(max_workers=min(4, len(live_names))) as executor:
            futures = {
                executor.submit(
                    run_owner,
                    name,
                    OWNER_COMMANDS[name],
                    int(OWNER_SPECS[name].get("timeout") or 90),
                ): name
                for name in live_names
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    unordered[name] = future.result()
                except Exception as exc:
                    unordered[name] = {
                        "name": name,
                        "ok": False,
                        "execution_state": "transport_failure",
                        "error_class": type(exc).__name__,
                        "error": str(exc)[:500],
                    }
    for name in live_names:
        persist_recent_owner_receipt(change_set, name, OWNER_COMMANDS[name], unordered[name])
    results = {name: unordered[name] for name in owner_names}
    signals = stale_signals(results, change_id=change_set["change_id"])
    return {
        "schema": "self_update_governance.snapshot.v1",
        "ok": True,
        "change_set": change_set,
        "owners": {
            name: {
                "ok": result.get("ok"),
                "returncode": result.get("returncode"),
                "command": result.get("command"),
                "execution_state": result.get("execution_state"),
                "receipt": result.get("receipt", {}),
                "system": OWNER_SPECS[name].get("system"),
                "source": OWNER_SPECS[name].get("source"),
                "elapsed_ms": float(result.get("elapsed_ms") or 0.0),
            }
            for name, result in results.items()
        },
        "selection": {
            "mode": "full" if selected_owners is None and not any((list(changed_files or []), list(changed_surfaces or []), task_kind, config_changed, major_change)) else "targeted",
            "owners": owner_names,
            "receipt_reuse_count": sum(
                1 for result in results.values()
                if result.get("execution_state") == "validation_receipt_reused"
            ),
            "recent_receipt_reuse_count": sum(
                1
                for result in results.values()
                if result.get("execution_state") == "validation_receipt_reused"
                and bool((result.get("receipt") or {}).get("cache_key"))
            ),
            "force_fresh": bool(force_fresh),
            "receipt_ttl_seconds": RECENT_RECEIPT_TTL_SECONDS,
            "wall_elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "slowest_owner": max(
                owner_names,
                key=lambda item: float(results[item].get("elapsed_ms") or 0.0),
                default="",
            ),
            "max_owner_elapsed_ms": max(
                (float(result.get("elapsed_ms") or 0.0) for result in results.values()),
                default=0.0,
            ),
        },
        "signals": signals,
        "rule": "Use owner modules for repairs; this federated surface only selects, checks, correlates, and reports owner evidence.",
    }


def doctor(
    selected_owners: Iterable[str] | None = None,
    validation_receipts: Iterable[str | dict[str, Any]] | None = None,
    **change_context: Any,
) -> dict[str, Any]:
    snap = snapshot(
        selected_owners=selected_owners,
        validation_receipts=validation_receipts,
        **change_context,
    )
    risks = [item for item in snap["signals"] if item.get("severity") == "risk"]
    authoritative_owners = [
        name
        for name, result in snap.get("owners", {}).items()
        if str(result.get("execution_state") or "") == "ok"
    ]
    return {
        "schema": "self_update_governance.doctor.v1",
        "ok": not risks,
        "status": "risk" if risks else ("warn" if snap["signals"] else "ok"),
        "change_set": snap["change_set"],
        "signals": snap["signals"],
        "authoritative_owners": authoritative_owners,
        "summary": {
            "owner_count": len(snap["owners"]),
            "receipt_reuse_count": snap["selection"]["receipt_reuse_count"],
            "risk_count": len(risks),
            "warn_count": sum(1 for item in snap["signals"] if item.get("severity") == "warn"),
            "authoritative_owner_count": len(authoritative_owners),
            "wall_elapsed_ms": snap["selection"].get("wall_elapsed_ms", 0.0),
            "slowest_owner": snap["selection"].get("slowest_owner", ""),
            "max_owner_elapsed_ms": snap["selection"].get("max_owner_elapsed_ms", 0.0),
        },
    }


def validate(
    selected_owners: Iterable[str] | None = None,
    validation_receipts: Iterable[str | dict[str, Any]] | None = None,
    **change_context: Any,
) -> dict[str, Any]:
    doc = doctor(
        selected_owners=selected_owners,
        validation_receipts=validation_receipts,
        **change_context,
    )
    return {
        "schema": "self_update_governance.validate.v1",
        "ok": bool(doc.get("ok")),
        "change_id": doc.get("change_set", {}).get("change_id"),
        "affected_systems": doc.get("change_set", {}).get("affected_systems", []),
        "selected_owners": doc.get("change_set", {}).get("selected_owners", []),
        "doctor_status": doc.get("status"),
        "risk_signals": [item for item in doc.get("signals", []) if item.get("severity") == "risk"],
        "warn_signals": [item for item in doc.get("signals", []) if item.get("severity") == "warn"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only self-update governance surface")
    parser.add_argument("command", choices=("snapshot", "doctor", "validate"))
    parser.add_argument(
        "--owner",
        action="append",
        choices=OWNER_NAMES,
        default=None,
        help="Run only the selected owner; repeat as needed. Omit for the full legacy set.",
    )
    parser.add_argument(
        "--validation-receipt",
        action="append",
        default=[],
        help="Reuse owner validation evidence as owner=path, owner=ok, or inline JSON.",
    )
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--changed-surface", action="append", default=[])
    parser.add_argument("--task-kind", default="")
    parser.add_argument("--outcome", default="unknown")
    parser.add_argument("--config-changed", action="store_true")
    parser.add_argument("--major-change", action="store_true")
    parser.add_argument("--force-fresh", action="store_true", help="Bypass recent successful owner receipts and run every selected owner live.")
    args = parser.parse_args(argv)
    kwargs = {
        "selected_owners": args.owner,
        "validation_receipts": args.validation_receipt,
        "changed_files": args.changed_file,
        "changed_surfaces": args.changed_surface,
        "task_kind": args.task_kind,
        "outcome": args.outcome,
        "config_changed": args.config_changed,
        "major_change": args.major_change,
        "force_fresh": args.force_fresh,
    }
    payload = snapshot(**kwargs) if args.command == "snapshot" else doctor(**kwargs) if args.command == "doctor" else validate(**kwargs)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
