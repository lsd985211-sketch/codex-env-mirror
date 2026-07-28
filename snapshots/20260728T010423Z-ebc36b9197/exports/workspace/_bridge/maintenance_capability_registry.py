#!/usr/bin/env python3
"""Derived maintenance capability registry and bounded query surface.

Ownership: machine-readable discovery over the maintenance surface map.
Non-goals: owner business state, arbitrary command execution, scheduling, or
replacing owner validators and repair commands.
State behavior: read-only except explicit ``build --apply`` of a derived SQLite
index under ``_bridge/runtime``.
Caller context: Codex workflow facade, scheduler planning, and maintenance UX.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from bounded_output import bounded_payload


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
MAP_PATH = BRIDGE / "docs" / "maintenance_surface_map.md"
SURFACE_DIR = BRIDGE / "docs" / "maintenance_surfaces"
SURFACE_INDEX_PATH = SURFACE_DIR / "index.json"
INDEX_PATH = BRIDGE / "runtime" / "maintenance_capabilities.sqlite"
INDEX_SCHEMA = "maintenance_capability_registry.v4"
NO_MAINTENANCE_SYSTEMS: dict[str, str] = {}
SAFE_DERIVED_AUTOMATIC_ACTIONS = {"snapshot", "doctor", "validate", "metrics", "coverage", "task-drift"}
KNOWN_ACTIONS = (
    "snapshot",
    "device",
    "problems",
    "classes",
    "doctor",
    "repair-plan",
    "validate",
    "metrics",
    "plan",
    "apply",
    "rollback",
    "status",
    "progress",
    "inspect",
    "get",
    "query",
    "state-query",
    "commands",
    "interfaces",
    "recommend",
    "events",
    "recall",
    "transition",
    "dispose",
    "resolve",
    "watch",
    "diff",
    "android",
    "transport",
    "task-drift",
    "override-plan",
    "bootstrap",
    "handoff",
    "cleanup-plan",
    "mirror-export",
    "work-git-release",
    "prepare-delivery",
    "mark-delivered",
    "decision-plan",
    "decision-apply",
    "decision-readback",
    "backlog-plan",
    "backlog-reconcile",
    "consume-approved",
    "command-contract",
    "reconcile",
)
MUTATING_ACTIONS = {
    "repair-plan",
    "apply",
    "rollback",
    "transition",
    "dispose",
    "resolve",
    "bootstrap",
    "mark-delivered",
    "decision-apply",
    "backlog-reconcile",
    "consume-approved",
    "reconcile",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def capability_id(module_path: str) -> str:
    normalized = module_path.replace("\\", "/").strip().lower()
    stem = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _contract_integer(
    value: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
) -> tuple[int, str]:
    """Parse numeric owner metadata without letting malformed contracts crash discovery."""

    raw = value.get(key, default)
    if isinstance(raw, bool):
        return default, f"{key}_invalid_integer"
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return default, f"{key}_invalid_integer"
    if parsed < minimum:
        return default, f"{key}_below_minimum"
    return parsed, ""


def normalize_maintenance_contract(
    row: dict[str, Any],
    declared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize optional owner metadata without granting inferred write authority."""

    explicit = isinstance(declared, dict) and bool(declared)
    value = dict(declared or {})
    capability = str(row.get("capability_id") or capability_id(str(row.get("module_path") or "")))
    system = str(value.get("system_id") or row.get("system") or "general")
    actions = _string_list(row.get("actions"))
    read_only = [item for item in _string_list(row.get("read_only_actions")) if item in actions]
    requested_level = str(value.get("automation_level") or ("A0" if read_only else "A3"))
    level = requested_level if requested_level in {"A0", "A1", "A2", "A3", "A4"} else "A4"
    safe_derived = [item for item in read_only if item in SAFE_DERIVED_AUTOMATIC_ACTIONS]
    requested_automatic = (
        _string_list(value.get("automatic_actions"))
        if explicit and "automatic_actions" in value
        else list(read_only if explicit else safe_derived)
    )
    automatic = [item for item in requested_automatic if item in actions]
    if level == "A0":
        automatic = [item for item in automatic if item in read_only]
    elif level in {"A3", "A4"}:
        automatic = []
    elif not explicit:
        level = "A3"
        automatic = []
    validation = _string_list(value.get("reverse_validation"))
    if not validation and "validate" in actions:
        validation = [f"{capability}:validate"]
    freshness_ttl_seconds, freshness_error = _contract_integer(
        value,
        "freshness_ttl_seconds",
        default=900 if read_only else 0,
        minimum=0,
    )
    estimated_cost_ms, cost_error = _contract_integer(
        value,
        "estimated_cost_ms",
        default=1000,
        minimum=1,
    )
    timeout_seconds, timeout_error = _contract_integer(
        value,
        "timeout_seconds",
        default=max(30, min(900, (estimated_cost_ms * 4 + 999) // 1000)),
        minimum=1,
    )
    contract_errors = [item for item in (freshness_error, cost_error, timeout_error) if item]
    if contract_errors:
        level = "A4"
        automatic = []
    default_policy = "normalize_and_close_when_healthy" if read_only else "approval_required"
    return {
        "schema": "maintenance_capability_contract.v4",
        "system_id": system,
        "member_ids": _string_list(value.get("member_ids")),
        "signals": _string_list(value.get("signals")) or [f"{system}.state_changed"],
        "dependencies": _string_list(value.get("dependencies")),
        "reverse_validation": validation,
        "conflict_group": str(value.get("conflict_group") or ""),
        "independent_group": str(value.get("independent_group") or (f"{system}.read_only" if read_only else "")),
        "freshness_ttl_seconds": freshness_ttl_seconds,
        "estimated_cost_ms": estimated_cost_ms,
        "timeout_seconds": timeout_seconds,
        "risk_class": str(value.get("risk_class") or ("read_only" if read_only else "approval_required")),
        "effect_class": str(value.get("effect_class") or ("observe" if read_only else "mutate")),
        "automation_level": level,
        "automatic_actions": automatic,
        "idempotent": bool(value.get("idempotent", bool(read_only))),
        "reversible": bool(value.get("reversible", False)),
        "result_policy": str(value.get("result_policy") or default_policy),
        "derived": not explicit,
        "contract_valid": not contract_errors,
        "contract_errors": contract_errors,
    }


def contract_fingerprint(row: dict[str, Any], maintenance: dict[str, Any]) -> str:
    payload = {
        "capability_id": row.get("capability_id"),
        "actions": _string_list(row.get("actions")),
        "read_only_actions": _string_list(row.get("read_only_actions")),
        "parser_signature": str(row.get("parser_signature") or ""),
        "action_commands": row.get("action_commands", {}),
        "maintenance": maintenance,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def global_coverage(
    rows: list[dict[str, Any]] | None = None,
    *,
    active_systems: list[str] | None = None,
    active_members: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    capabilities = rows if rows is not None else parse_surface_map()
    if active_systems is None:
        try:
            from system_membership import snapshot as membership_snapshot

            membership = membership_snapshot()
            active_systems = [str(item) for item in membership.get("systems", [])]
            active_members = [
                item
                for item in membership.get("mirror_source_projection", {}).get("members", [])
                if isinstance(item, dict) and str(item.get("lifecycle") or "active") == "active"
            ]
        except (ImportError, RuntimeError):
            active_systems = []
    active = sorted(set(str(item) for item in active_systems if str(item)))
    by_system: dict[str, list[str]] = {}
    proactive_systems: set[str] = set()
    for row in capabilities:
        system = str(row.get("system") or "")
        if system:
            by_system.setdefault(system, []).append(str(row.get("capability_id") or ""))
            maintenance = row.get("maintenance") if isinstance(row.get("maintenance"), dict) else {}
            if maintenance.get("automatic_actions"):
                proactive_systems.add(system)
    covered = sorted(system for system in active if by_system.get(system) or system in NO_MAINTENANCE_SYSTEMS)
    unmapped = sorted(set(active) - set(covered))
    direct_member_ids: set[str] = set()
    for row in capabilities:
        module_path = str(row.get("module_path") or "").replace("\\", "/").casefold()
        maintenance = row.get("maintenance") if isinstance(row.get("maintenance"), dict) else {}
        declared_ids = {str(item).casefold() for item in maintenance.get("member_ids", []) if str(item)}
        for member in active_members or []:
            member_id = str(member.get("member_id") or "")
            owner = str(member.get("owner") or "").replace("-", "_").casefold()
            if member_id.casefold() in declared_ids or (owner and owner in module_path):
                direct_member_ids.add(member_id)
    member_rows = []
    for member in active_members or []:
        member_id = str(member.get("member_id") or "")
        system = str(member.get("system") or "")
        disposition = "direct_capability" if member_id in direct_member_ids else (
            "system_capability_inherited" if system in covered else "unmapped"
        )
        member_rows.append({"member_id": member_id, "system": system, "disposition": disposition})
    unmapped_members = [item["member_id"] for item in member_rows if item["disposition"] == "unmapped"]
    return {
        "schema": "maintenance_capability_registry.coverage.v1",
        "ok": not unmapped and not unmapped_members,
        "active_system_count": len(active),
        "covered_system_count": len(covered),
        "coverage_percent": round((len(covered) / len(active) * 100.0) if active else 100.0, 2),
        "covered_systems": covered,
        "unmapped_systems": unmapped,
        "proactive_system_count": len(set(active) & proactive_systems),
        "proactive_coverage_percent": round((len(set(active) & proactive_systems) / len(active) * 100.0) if active else 100.0, 2),
        "manual_only_systems": sorted(set(active) - proactive_systems),
        "active_member_count": len(member_rows),
        "direct_member_coverage_count": len(direct_member_ids),
        "inherited_member_coverage_count": sum(
            1 for item in member_rows if item["disposition"] == "system_capability_inherited"
        ),
        "unmapped_members": unmapped_members,
        "underutilized_members": [
            item["member_id"] for item in member_rows if item["disposition"] == "system_capability_inherited"
        ],
        "member_dispositions": member_rows,
        "no_maintenance_dispositions": dict(sorted(NO_MAINTENANCE_SYSTEMS.items())),
        "capability_counts": {system: len(values) for system, values in sorted(by_system.items())},
        "rule": "active system and member identities come from system membership; direct owner contracts are preferred and system-level coverage is an explicit inherited disposition",
    }


def infer_system(module_path: str, text: str) -> str:
    normalized_module = module_path.replace("\\", "/").lower()
    module_routes = (
        (
            "startup",
            (
                "codex_config_guard",
                "codex_session_store",
                "codex_runtime_cache",
                "codex_model_provider",
                "codex_plugin_runtime",
                "codex_desktop_protocol_compatibility",
                "codex_appserver_model_bridge",
                "shared/process_liveness",
            ),
        ),
        ("wsl_workspace", ("wsl_workspace_owner", "bootstrap_wsl_workspace", "platform_paths")),
        ("audio", ("music_library_owner", "music_library_planner", "music_library_transaction", "/audio_toolkit/")),
        ("hardware", ("hardware_system_owner", "wsl_hardware_owner", "windows_hardware_owner", "usb_device_owner", "mtp_media_archive_owner", "/usb_", "device_owner")),
        ("bridge", ("mobile_openclaw", "mobile_bridge", "weixin")),
        ("mail", ("email_", "/email", "mail_")),
        ("scheduler", ("scheduler", "schedule_")),
        ("resource", ("resource_", "/resource")),
        ("network", ("network_", "/network")),
        ("mcp", ("mcp_", "/mcp", "local_mcp_hub")),
        ("memory", ("memory_", "/memory", "pmb_")),
        ("skills", ("skill_", "/skill", "code_maintainability", "module_asset")),
        ("records", ("record_store", "codex_reporter", "migration_")),
        ("backup", ("backup_", "/backup", "codex_environment_mirror", "recovery_mirror")),
        ("office", ("office", "document", "pdf_")),
        ("workflow", ("workflow_", "/workflow", "closeout", "slash_", "dependency_change_intelligence")),
    )
    for system, terms in module_routes:
        if any(term in normalized_module for term in terms):
            return system
    haystack = f"{module_path} {text}".lower()
    routes = (
        ("wsl_workspace", ("wsl workspace", "work git", "bare git", "work-git release")),
        ("audio", ("music library", "audio toolkit", "lyrics sidecar", "album artwork")),
        ("hardware", ("cross-platform hardware", "wsl-visible", "usb device", "usb inventory", "hardware device", "pnp inventory")),
        ("workflow", ("workflow", "closeout", "slash")),
        ("resource", ("resource", "download", "package")),
        ("network", ("network", "gateway", "proxy")),
        ("mcp", ("mcp", "tool_registry", "tool-registry")),
        ("mail", ("email", "mail", "outbox", "inbox")),
        ("scheduler", ("scheduler", "schedule", "定时")),
        ("memory", ("memory", "pmb", "checkpoint")),
        ("skills", ("skill", "module_capability", "code_maintainability")),
        ("records", ("record_store", "record-store", "incident", "migration")),
        ("startup", ("startup", "config_guard", "runtime_cache", "model_provider", "session_store", "session-store", "restore-performance")),
        ("bridge", ("mobile_openclaw", "bridge", "weixin")),
        ("backup", ("backup", "encoding")),
        ("office", ("office", "document", "pdf")),
    )
    for system, terms in routes:
        if any(term in haystack for term in terms):
            return system
    return "general"


def extract_actions(text: str) -> list[str]:
    actions = []
    lowered = text.lower()
    for action in KNOWN_ACTIONS:
        if re.search(rf"(?<![a-z0-9_-]){re.escape(action)}(?![a-z0-9_-])", lowered):
            actions.append(action)
    return actions


def read_owner_command_contract(script: Path, declared_actions: list[str]) -> dict[str, Any]:
    """Consume an optional owner-exported command contract without a second registry."""

    if "command-contract" not in declared_actions:
        return {"ok": False, "reason": "owner_contract_not_declared"}
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "command-contract"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
        payload = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"owner_contract_unreadable:{type(exc).__name__}"}
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    read_only = payload.get("read_only_actions") if isinstance(payload.get("read_only_actions"), list) else []
    raw_commands = payload.get("action_commands") if isinstance(payload.get("action_commands"), dict) else {}
    if completed.returncode != 0 or not payload.get("ok") or not actions:
        return {
            "ok": False,
            "reason": "owner_contract_command_failed",
            "returncode": completed.returncode,
        }
    action_commands = {
        str(action): [str(item) for item in argv]
        for action, argv in raw_commands.items()
        if str(action) in actions and isinstance(argv, list) and argv and all(str(item) for item in argv)
    }
    return {
        "ok": True,
        "actions": [str(item) for item in actions],
        "read_only_actions": [str(item) for item in read_only],
        "parser_signature": str(payload.get("parser_signature") or ""),
        "maintenance": payload.get("maintenance") if isinstance(payload.get("maintenance"), dict) else {},
        "action_commands": action_commands,
    }


def split_markdown_table_row(line: str) -> list[str]:
    """Split a Markdown row without treating pipes inside code spans as cells."""
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    for character in line.strip().strip("|"):
        if character == "`":
            in_code = not in_code
            current.append(character)
        elif character == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    cells.append("".join(current).strip())
    return cells


def load_surface_index() -> dict[str, Any]:
    try:
        payload = json.loads(SURFACE_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "maintenance_surface_index.v1", "systems": []}
    if not isinstance(payload, dict) or not isinstance(payload.get("systems"), list):
        return {"schema": "maintenance_surface_index.v1", "systems": []}
    return payload


def surface_shards() -> dict[str, Path]:
    shards: dict[str, Path] = {}
    docs_root = MAP_PATH.parent.resolve()
    for item in load_surface_index().get("systems", []):
        if not isinstance(item, dict):
            continue
        system = str(item.get("system") or "").strip()
        relative = str(item.get("path") or "").strip()
        if not system or not relative:
            continue
        path = (MAP_PATH.parent / relative).resolve()
        try:
            path.relative_to(docs_root)
        except ValueError:
            continue
        if path.is_file():
            shards[system] = path
    return shards


def _path_state(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
        return (str(path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return (str(path), -1, -1)


def _referenced_script_state(path: Path) -> tuple[tuple[str, int, int], ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    scripts: dict[Path, bool] = {}
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = split_markdown_table_row(line)
        dynamic_contract = len(cells) >= 4 and "command-contract" in extract_actions(cells[3])
        for token in re.findall(r"`([^`]+\.py)`", line):
            normalized = token.replace("\\", "/")
            normalized = normalized if normalized.startswith("_bridge/") else f"_bridge/{normalized}"
            script = (ROOT / normalized).resolve()
            try:
                script.relative_to(BRIDGE.resolve())
            except ValueError:
                continue
            scripts[script] = scripts.get(script, False) or dynamic_contract
    states = [
        _path_state(script) if dynamic else (str(script), int(script.is_file()), 0)
        for script, dynamic in scripts.items()
    ]
    return tuple(sorted(states, key=lambda item: item[0]))


def normalize_source_state(records: list[tuple[str, int, int]]) -> tuple[tuple[str, int, int], ...]:
    """Deduplicate and fully order state records, including same-path variants."""

    return tuple(sorted(set(records)))


def _source_state() -> tuple[tuple[str, int, int], ...]:
    paths = [MAP_PATH, SURFACE_INDEX_PATH, *surface_shards().values()]
    document_state = [_path_state(path) for path in paths]
    script_state = [item for path in surface_shards().values() for item in _referenced_script_state(path)]
    return normalize_source_state([*document_state, *script_state])


def _signature_from_rows(
    state: tuple[tuple[str, int, int], ...],
    owner_rows: list[dict[str, Any]],
) -> str:
    digest = hashlib.sha256(b"maintenance_surface_shards.v1\n")
    for record in state:
        digest.update(json.dumps(record, separators=(",", ":")).encode("utf-8") + b"\n")
    for row in owner_rows:
        if row.get("command_contract_source") != "owner" and not row.get("command_contract_error"):
            continue
        contract_record = {
            "module_path": row.get("module_path"),
            "actions": row.get("actions", []),
            "read_only_actions": row.get("read_only_actions", []),
            "command_contract_source": row.get("command_contract_source"),
            "command_contract_error": row.get("command_contract_error"),
            "parser_signature": row.get("parser_signature"),
            "action_commands": row.get("action_commands", {}),
            "maintenance": row.get("maintenance", {}),
            "contract_fingerprint": row.get("contract_fingerprint", ""),
        }
        digest.update(
            json.dumps(contract_record, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        )
    return digest.hexdigest()


@lru_cache(maxsize=8)
def _cached_source_signature(state: tuple[tuple[str, int, int], ...]) -> str:
    return _signature_from_rows(state, parse_surface_map())


def source_signature(owner_rows: list[dict[str, Any]] | None = None) -> str:
    state = _source_state()
    if owner_rows is not None:
        return _signature_from_rows(state, owner_rows)
    return _cached_source_signature(state)


def contract_stats() -> dict[str, Any]:
    row_locations: dict[str, list[str]] = {}
    total = 0
    authority_counts: dict[str, int] = {}
    for system, path in sorted(surface_shards().items()):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.startswith("| `"):
                continue
            total += 1
            authority_counts[system] = authority_counts.get(system, 0) + 1
            digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
            row_locations.setdefault(digest, []).append(f"{system}:{line_number}")
    duplicates = [locations for locations in row_locations.values() if len(locations) > 1]
    index_counts = {
        str(item.get("system") or ""): int(item.get("contract_count") or 0)
        for item in load_surface_index().get("systems", [])
        if isinstance(item, dict) and str(item.get("system") or "")
    }
    map_counts: dict[str, int] = {}
    if MAP_PATH.is_file():
        for line in MAP_PATH.read_text(encoding="utf-8").splitlines():
            if not line.startswith("| `"):
                continue
            cells = split_markdown_table_row(line)
            if len(cells) < 3:
                continue
            system = cells[0].strip().strip("`")
            try:
                map_counts[system] = int(cells[2])
            except ValueError:
                continue
    systems = sorted(set(authority_counts) | set(index_counts) | set(map_counts))
    projection_mismatches = [
        {
            "system": system,
            "authority_count": authority_counts.get(system),
            "index_count": index_counts.get(system),
            "map_count": map_counts.get(system),
        }
        for system in systems
        if len({authority_counts.get(system), index_counts.get(system), map_counts.get(system)}) != 1
    ]
    return {
        "contract_count": total,
        "declared_contract_count": sum(index_counts.values()),
        "authority_contract_counts": authority_counts,
        "index_contract_counts": index_counts,
        "map_contract_counts": map_counts,
        "contract_projection_mismatches": projection_mismatches,
        "duplicate_contracts": duplicates,
    }


def _render_surface_index_projection(payload: dict[str, Any], authority_counts: dict[str, int]) -> str:
    projected = copy.deepcopy(payload)
    for item in projected.get("systems", []):
        if not isinstance(item, dict):
            continue
        system = str(item.get("system") or "").strip()
        if system in authority_counts:
            item["contract_count"] = authority_counts[system]
    return json.dumps(projected, ensure_ascii=False, indent=2) + "\n"


def _render_compact_map_projection(source: str, authority_counts: dict[str, int]) -> str:
    pattern = re.compile(r"^(?P<prefix>\| `(?P<system>[^`]+)` \|.*\| )(?P<count>\d+)(?P<suffix> \|)$")
    rendered: list[str] = []
    for line in source.splitlines():
        match = pattern.match(line)
        system = match.group("system") if match else ""
        if match and system in authority_counts:
            line = f"{match.group('prefix')}{authority_counts[system]}{match.group('suffix')}"
        rendered.append(line)
    return "\n".join(rendered) + ("\n" if source.endswith("\n") else "")


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def reconcile_projections(*, apply: bool) -> dict[str, Any]:
    """Project shard-owned counts once, then refresh the replaceable SQLite cache."""

    payload = load_surface_index()
    stats = contract_stats()
    authority_counts = dict(stats["authority_contract_counts"])
    declared_systems = [
        str(item.get("system") or "").strip()
        for item in payload.get("systems", [])
        if isinstance(item, dict) and str(item.get("system") or "").strip()
    ]
    missing_authority = sorted(system for system in declared_systems if system not in authority_counts)
    index_source = SURFACE_INDEX_PATH.read_text(encoding="utf-8")
    map_source = MAP_PATH.read_text(encoding="utf-8")
    index_projection = _render_surface_index_projection(payload, authority_counts)
    map_projection = _render_compact_map_projection(map_source, authority_counts)
    changed_paths = [
        str(path)
        for path, current, projected in (
            (SURFACE_INDEX_PATH, index_source, index_projection),
            (MAP_PATH, map_source, map_projection),
        )
        if current != projected
    ]
    result: dict[str, Any] = {
        "schema": "maintenance_capability_registry.reconcile.v1",
        "ok": not missing_authority,
        "apply_requested": apply,
        "applied": False,
        "authority_contract_counts": authority_counts,
        "projection_mismatches_before": stats["contract_projection_mismatches"],
        "changed_paths": changed_paths,
        "missing_authority_systems": missing_authority,
    }
    if not apply or missing_authority:
        return result
    if index_source != index_projection:
        _atomic_write_text(SURFACE_INDEX_PATH, index_projection)
    if map_source != map_projection:
        _atomic_write_text(MAP_PATH, map_projection)
    index_result = build_index(apply=True)
    after = contract_stats()
    result.update(
        {
            "ok": bool(index_result.get("ok")) and not after["contract_projection_mismatches"],
            "applied": True,
            "projection_mismatches_after": after["contract_projection_mismatches"],
            "index": index_result,
        }
    )
    return result


def parse_surface_map(source_paths: tuple[tuple[str, Path], ...] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    selected = source_paths or tuple(sorted(surface_shards().items()))
    for declared_system, source_path in selected:
        text = source_path.read_text(encoding="utf-8")
        source_mtime_ns = source_path.stat().st_mtime_ns
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.startswith("| `"):
                continue
            cells = split_markdown_table_row(line)
            if len(cells) < 4 or cells[0] == "---":
                continue
            surface, owns, non_goals, usual_entry = cells[:4]
            module_paths = [
                token.replace("\\", "/")
                for token in re.findall(r"`([^`]+)`", surface)
                if token.lower().endswith(".py")
            ]
            for module_path in module_paths:
                normalized_module = module_path if module_path.startswith("_bridge/") else f"_bridge/{module_path}"
                script = (ROOT / normalized_module).resolve()
                try:
                    script.relative_to(BRIDGE.resolve())
                except ValueError:
                    continue
                actions = extract_actions(usual_entry)
                owner_contract = read_owner_command_contract(script, actions)
                read_only_actions = [action for action in actions if action not in MUTATING_ACTIONS]
                if owner_contract.get("ok"):
                    actions = owner_contract["actions"]
                    read_only_actions = owner_contract["read_only_actions"]
                row = {
                        "capability_id": capability_id(normalized_module),
                        "system": declared_system,
                        "module_path": normalized_module,
                        "surface": re.sub(r"`", "", surface),
                        "owns": owns,
                        "non_goals": non_goals,
                        "usual_entry": usual_entry,
                        "actions": actions,
                        "read_only_actions": read_only_actions,
                        "command_contract_source": "owner" if owner_contract.get("ok") else "maintenance_surface",
                        "command_contract_error": (
                            ""
                            if owner_contract.get("ok") or owner_contract.get("reason") == "owner_contract_not_declared"
                            else str(owner_contract.get("reason") or "owner_contract_error")
                        ),
                        "parser_signature": str(owner_contract.get("parser_signature") or ""),
                        "action_commands": (
                            owner_contract.get("action_commands", {})
                            if owner_contract.get("ok")
                            else {action: [action] for action in actions}
                        ),
                        "source_path": source_path.relative_to(ROOT).as_posix(),
                        "source_line": line_number,
                        "source_mtime_ns": source_mtime_ns,
                        "script_exists": script.is_file(),
                    }
                maintenance = normalize_maintenance_contract(row, owner_contract.get("maintenance"))
                row["maintenance"] = maintenance
                row["contract_fingerprint"] = contract_fingerprint(row, maintenance)
                rows.append(row)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = unique.get(row["capability_id"])
        if not current:
            unique[row["capability_id"]] = row
            continue
        current["actions"] = sorted(set(current["actions"]) | set(row["actions"]))
        current["read_only_actions"] = sorted(set(current["read_only_actions"]) | set(row["read_only_actions"]))
        current["action_commands"] = {**current.get("action_commands", {}), **row.get("action_commands", {})}
        current["maintenance"] = normalize_maintenance_contract(current, current.get("maintenance"))
        current["contract_fingerprint"] = contract_fingerprint(current, current["maintenance"])
    return sorted(unique.values(), key=lambda item: (item["system"], item["module_path"]))


def build_index(*, apply: bool) -> dict[str, Any]:
    rows = parse_surface_map()
    signature = source_signature(rows)
    result = {
        "schema": "maintenance_capability_registry.build.v1",
        "ok": bool(rows),
        "apply_requested": apply,
        "applied": False,
        "capability_count": len(rows),
        "index_path": str(INDEX_PATH),
        "source_path": str(MAP_PATH),
        "source_index_path": str(SURFACE_INDEX_PATH),
        "source_signature": signature,
    }
    if not apply or not rows:
        return result
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    connection = sqlite3.connect(tmp)
    try:
        connection.execute(
            """CREATE TABLE capabilities (
                capability_id TEXT PRIMARY KEY,
                system TEXT NOT NULL,
                module_path TEXT NOT NULL,
                surface TEXT NOT NULL,
                owns TEXT NOT NULL,
                non_goals TEXT NOT NULL,
                usual_entry TEXT NOT NULL,
                actions_json TEXT NOT NULL,
                read_only_actions_json TEXT NOT NULL,
                command_contract_source TEXT NOT NULL,
                command_contract_error TEXT NOT NULL,
                parser_signature TEXT NOT NULL,
                maintenance_json TEXT NOT NULL,
                contract_fingerprint TEXT NOT NULL,
                action_commands_json TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_line INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                script_exists INTEGER NOT NULL
            )"""
        )
        connection.execute("CREATE INDEX idx_capabilities_system ON capabilities(system)")
        connection.execute("CREATE INDEX idx_capabilities_module ON capabilities(module_path)")
        connection.executemany(
            "INSERT INTO capabilities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row["capability_id"],
                    row["system"],
                    row["module_path"],
                    row["surface"],
                    row["owns"],
                    row["non_goals"],
                    row["usual_entry"],
                    json.dumps(row["actions"], ensure_ascii=False),
                    json.dumps(row["read_only_actions"], ensure_ascii=False),
                    row["command_contract_source"],
                    row["command_contract_error"],
                    row["parser_signature"],
                    json.dumps(row["maintenance"], ensure_ascii=False, sort_keys=True),
                    row["contract_fingerprint"],
                    json.dumps(row["action_commands"], ensure_ascii=False, sort_keys=True),
                    row["source_path"],
                    row["source_line"],
                    row["source_mtime_ns"],
                    int(row["script_exists"]),
                )
                for row in rows
            ],
        )
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            (
                ("schema", INDEX_SCHEMA),
                ("generated_at", now_iso()),
                ("source_signature", signature),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    os.replace(tmp, INDEX_PATH)
    return {**result, "applied": True}


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "capability_id": row["capability_id"],
        "system": row["system"],
        "module_path": row["module_path"],
        "surface": row["surface"],
        "owns": row["owns"],
        "usual_entry": row["usual_entry"],
        "actions": json.loads(row["actions_json"]),
        "read_only_actions": json.loads(row["read_only_actions_json"]),
        "command_contract_source": row["command_contract_source"],
        "command_contract_error": row["command_contract_error"],
        "parser_signature": row["parser_signature"],
        "maintenance": json.loads(row["maintenance_json"]),
        "contract_fingerprint": row["contract_fingerprint"],
        "action_commands": json.loads(row["action_commands_json"]),
        "source_path": row["source_path"],
        "source_line": row["source_line"],
        "script_exists": bool(row["script_exists"]),
    }


def _source_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "capability_id",
            "system",
            "module_path",
            "surface",
            "owns",
            "usual_entry",
            "actions",
            "read_only_actions",
            "command_contract_source",
            "command_contract_error",
            "parser_signature",
            "maintenance",
            "contract_fingerprint",
            "action_commands",
            "source_path",
            "source_line",
            "script_exists",
        )
    }


@lru_cache(maxsize=32)
def _cached_source_rows(
    system: str,
    path_text: str,
    source_mtime_ns: int,
    referenced_script_state: tuple[tuple[str, int, int], ...],
) -> tuple[dict[str, Any], ...]:
    del source_mtime_ns, referenced_script_state
    return tuple(parse_surface_map(((system, Path(path_text)),)))


def _source_rows(system: str) -> tuple[dict[str, Any], ...]:
    path = surface_shards().get(system)
    if path is None:
        return ()
    return _cached_source_rows(
        system,
        str(path),
        path.stat().st_mtime_ns,
        _referenced_script_state(path),
    )


def systems_for_term(term: str) -> list[str]:
    normalized = str(term or "").strip().casefold()
    if not normalized:
        return []
    matches: list[str] = []
    for item in load_surface_index().get("systems", []):
        if not isinstance(item, dict):
            continue
        system = str(item.get("system") or "").strip()
        terms = [system, *[str(value) for value in item.get("terms", [])]]
        if any(value.casefold() in normalized or normalized in value.casefold() for value in terms if value):
            matches.append(system)
    return list(dict.fromkeys(matches))


def index_fresh() -> bool:
    if not INDEX_PATH.is_file():
        return False
    value = None
    schema = None
    columns: set[str] = set()
    try:
        connection = sqlite3.connect(INDEX_PATH)
        value = connection.execute("SELECT value FROM metadata WHERE key='source_signature'").fetchone()
        schema = connection.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(capabilities)").fetchall()}
    except sqlite3.Error:
        return False
    finally:
        if "connection" in locals():
            connection.close()
    required_columns = {"maintenance_json", "contract_fingerprint", "action_commands_json"}
    return bool(
        value
        and value[0] == source_signature()
        and schema
        and schema[0] == INDEX_SCHEMA
        and required_columns <= columns
    )


def _query_source_registry(
    *,
    system: str,
    term: str,
    action: str,
    effective_limit: int,
    index_reason: str,
) -> dict[str, Any]:
    """Preserve read-only discovery when the derived SQLite cache is absent."""

    normalized_term = str(term or "").casefold()
    selected_systems = [system] if system else systems_for_term(term)
    if not selected_systems:
        return bounded_payload(
            {
                "schema": "maintenance_capability_registry.query.v1",
                "ok": False,
                "reason": "scope_required_when_index_unavailable",
                "filters": {"system": system, "term": term, "action": action},
                "available_systems": sorted(surface_shards()),
                "source": "maintenance_surface_shard_fallback",
                "index_status": index_reason,
                "next_action": "specify --system or rebuild the derived index",
            },
            max_bytes=8 * 1024,
        )
    rows = []
    loaded_shards: list[str] = []
    for selected_system in selected_systems:
        source_rows = _source_rows(selected_system)
        if source_rows:
            loaded_shards.append(selected_system)
        for row in source_rows:
            if normalized_term:
                haystack = " ".join(
                    str(row.get(key) or "")
                    for key in ("module_path", "surface", "owns", "usual_entry")
                ).casefold()
                if normalized_term not in haystack and system:
                    continue
            if action and action not in row.get("actions", []):
                continue
            rows.append(row)
    selected = rows[:effective_limit]
    return bounded_payload(
        {
            "schema": "maintenance_capability_registry.query.v1",
            "ok": True,
            "filters": {"system": system, "term": term, "action": action},
            "total": len(rows),
            "returned": len(selected),
            "has_more": len(rows) > len(selected),
            "limit": effective_limit,
            "items": [_source_row_to_dict(row) for row in selected],
            "source": "maintenance_surface_shard_fallback",
            "loaded_shards": loaded_shards,
            "index_status": index_reason,
        },
        max_bytes=8 * 1024,
        max_items=max(20, effective_limit),
        preserve_keys=(
            "schema",
            "ok",
            "filters",
            "total",
            "returned",
            "has_more",
            "limit",
            "items",
            "source",
            "loaded_shards",
            "index_status",
        ),
    )


def query_registry(*, system: str = "", term: str = "", action: str = "", limit: int = 20) -> dict[str, Any]:
    effective_limit = max(1, min(int(limit or 20), 100))
    current_index_status = "fresh" if index_fresh() else ("index_stale" if INDEX_PATH.is_file() else "index_missing")
    if current_index_status != "fresh":
        return _query_source_registry(
            system=system,
            term=term,
            action=action,
            effective_limit=effective_limit,
            index_reason=current_index_status,
        )
    clauses = []
    params: list[Any] = []
    if system:
        clauses.append("system = ?")
        params.append(system)
    if term:
        clauses.append("(module_path LIKE ? OR surface LIKE ? OR owns LIKE ? OR usual_entry LIKE ?)")
        needle = f"%{term}%"
        params.extend([needle, needle, needle, needle])
    if action:
        clauses.append("actions_json LIKE ?")
        params.append(f'%"{action}"%')
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    connection = sqlite3.connect(INDEX_PATH)
    connection.row_factory = sqlite3.Row
    try:
        total = connection.execute(f"SELECT COUNT(*) FROM capabilities {where}", params).fetchone()[0]
        rows = connection.execute(
            f"SELECT * FROM capabilities {where} ORDER BY system, module_path LIMIT ?",
            [*params, effective_limit],
        ).fetchall()
    finally:
        connection.close()
    return bounded_payload(
        {
            "schema": "maintenance_capability_registry.query.v1",
            "ok": True,
            "filters": {"system": system, "term": term, "action": action},
            "total": total,
            "returned": len(rows),
            "has_more": total > len(rows),
            "limit": effective_limit,
            "items": [_row_to_dict(row) for row in rows],
        },
        max_bytes=8 * 1024,
        max_items=max(20, effective_limit),
        preserve_keys=("schema", "ok", "filters", "total", "returned", "has_more", "limit", "items"),
    )


def resolve_capability(capability: str, action: str) -> dict[str, Any]:
    if not INDEX_PATH.is_file():
        return {"ok": False, "reason": "index_missing"}
    connection = sqlite3.connect(INDEX_PATH)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM capabilities WHERE capability_id = ?", (capability,)).fetchone()
    finally:
        connection.close()
    if not row:
        return {"ok": False, "reason": "capability_not_found", "capability_id": capability}
    item = _row_to_dict(row)
    if action not in item["actions"]:
        return {"ok": False, "reason": "action_not_declared", "capability_id": capability, "action": action, "declared_actions": item["actions"]}
    script = (ROOT / item["module_path"]).resolve()
    try:
        script.relative_to(BRIDGE.resolve())
    except ValueError:
        return {"ok": False, "reason": "capability_outside_bridge"}
    if not script.is_file():
        return {"ok": False, "reason": "capability_script_missing", "module_path": item["module_path"]}
    command_argv = item.get("action_commands", {}).get(action) if isinstance(item.get("action_commands"), dict) else None
    if not isinstance(command_argv, list) or not command_argv:
        return {"ok": False, "reason": "action_command_binding_missing", "capability_id": capability, "action": action}
    return {"ok": True, **item, "script": str(script), "action": action, "command_argv": command_argv}


def metrics() -> dict[str, Any]:
    rows = parse_surface_map()
    contracts = contract_stats()
    systems: dict[str, int] = {}
    for row in rows:
        systems[row["system"]] = systems.get(row["system"], 0) + 1
    coverage = global_coverage(rows)
    return {
        "schema": "maintenance_capability_registry.metrics.v1",
        "ok": bool(rows),
        "capability_count": len(rows),
        "system_count": len(systems),
        "systems": systems,
        "surface_shard_count": len(surface_shards()),
        "declared_surface_count": len(load_surface_index().get("systems", [])),
        "compact_map_bytes": MAP_PATH.stat().st_size if MAP_PATH.is_file() else 0,
        "compact_map_budget_bytes": 16 * 1024,
        "compact_map_within_budget": MAP_PATH.is_file() and MAP_PATH.stat().st_size <= 16 * 1024,
        **contracts,
        "index_exists": INDEX_PATH.is_file(),
        "index_fresh": index_fresh(),
        "owner_command_contract_errors": [
            {
                "module_path": row["module_path"],
                "error": row["command_contract_error"],
            }
            for row in rows
            if row.get("command_contract_error")
        ],
        "coverage": coverage,
    }


def doctor() -> dict[str, Any]:
    metric = metrics()
    issues = []
    if not metric["capability_count"]:
        issues.append("maintenance surface map produced no capabilities")
    if not metric["compact_map_within_budget"]:
        issues.append("compact maintenance surface map exceeds 16 KiB budget")
    if metric["surface_shard_count"] != metric["declared_surface_count"]:
        issues.append("maintenance surface manifest and shard counts differ")
    if metric["contract_projection_mismatches"]:
        systems = ",".join(item["system"] for item in metric["contract_projection_mismatches"])
        issues.append(f"maintenance contract count projection drift: {systems}")
    if metric["duplicate_contracts"]:
        issues.append("maintenance contracts are duplicated across authority shards")
    if metric.get("owner_command_contract_errors"):
        issues.append("one or more owner command contracts could not be consumed")
    coverage = metric.get("coverage") if isinstance(metric.get("coverage"), dict) else {}
    unmapped_systems = [str(item) for item in coverage.get("unmapped_systems", []) if str(item)]
    unmapped_members = [str(item) for item in coverage.get("unmapped_members", []) if str(item)]
    if unmapped_systems or unmapped_members:
        systems = ",".join(unmapped_systems)
        members = ",".join(unmapped_members)
        detail = "; ".join(
            item for item in (f"systems={systems}" if systems else "", f"members={members}" if members else "") if item
        )
        issues.append(f"active membership missing maintenance disposition: {detail}")
    if not metric["index_exists"]:
        issues.append("maintenance capability index missing")
    elif not metric["index_fresh"]:
        issues.append("maintenance capability index stale")
    return {"schema": "maintenance_capability_registry.doctor.v1", "ok": not issues, "issues": issues, "metrics": metric}


def print_json(payload: dict[str, Any]) -> None:
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derived maintenance capability registry")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--apply", action="store_true")
    query = sub.add_parser("query")
    query.add_argument("--system", default="")
    query.add_argument("--term", default="")
    query.add_argument("--action", default="")
    query.add_argument("--limit", type=int, default=20)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--capability-id", required=True)
    resolve.add_argument("--action", required=True)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--apply", action="store_true")
    for name in ("snapshot", "doctor", "validate", "metrics", "coverage"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    if args.command == "build":
        payload = build_index(apply=args.apply)
    elif args.command == "query":
        payload = query_registry(system=args.system, term=args.term, action=args.action, limit=args.limit)
    elif args.command == "resolve":
        payload = resolve_capability(args.capability_id, args.action)
    elif args.command == "reconcile":
        payload = reconcile_projections(apply=args.apply)
    elif args.command == "metrics":
        payload = metrics()
    elif args.command == "coverage":
        payload = global_coverage()
    elif args.command in {"doctor", "validate"}:
        payload = doctor()
    else:
        payload = {"schema": "maintenance_capability_registry.snapshot.v1", **metrics(), "source_path": str(MAP_PATH), "index_path": str(INDEX_PATH)}
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
