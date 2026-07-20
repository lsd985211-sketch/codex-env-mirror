#!/usr/bin/env python3
"""Governed CLI facade for reusable local music-library organization.

Ownership: inventory and deterministic plan orchestration, USB storage-health
binding, explicit apply/rollback admission, and bounded machine-readable
receipts.
Non-goals: network research, device control, formatting/ejecting media,
transcoding, tag rewriting, deletion, or arbitrary filesystem operations.
State behavior: inventory/doctor/validate are read-only; plan writes only an
explicit or owner-runtime plan; apply/rollback require the exact plan id and
delegate same-volume moves to music_library_transaction.py.
Caller context: Codex workflow, windows-audio-ops, and direct maintenance use.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from music_library_planner import (
    SCHEMA,
    build_plan,
    find_ffprobe,
    load_corrections,
    scan_media,
    validate_plan_structure,
)
from music_library_transaction import apply_plan, rollback_plan, safe_path, validate_state, write_new_text
from shared.json_cli import configure_utf8_stdio, now_iso, print_json
from usb_device_owner import storage_binding


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "_bridge" / "runtime" / "music_library"

configure_utf8_stdio()


def load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("plan_must_be_json_object")
    issues = validate_plan_structure(payload)
    if issues:
        raise ValueError(f"invalid_plan:{json.dumps(issues, ensure_ascii=False, separators=(',', ':'))}")
    return payload


def drive_letter_for_root(root: Path) -> str:
    resolved = root.resolve()
    drive = resolved.drive.rstrip(":").upper()
    if not re.fullmatch(r"[A-Z]", drive):
        raise ValueError(f"root_must_use_windows_drive_letter:{resolved}")
    return drive


def inspect_root(root: Path) -> tuple[Path, dict[str, Any]]:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ValueError(f"music_root_not_directory:{resolved}")
    hardware = storage_binding(drive_letter_for_root(resolved))
    return resolved, hardware


def require_mutation_safe(hardware: dict[str, Any]) -> None:
    if not hardware.get("safe_for_content_mutation"):
        raise ValueError(
            "hardware_not_safe_for_content_mutation:"
            + json.dumps(hardware.get("issues", []), ensure_ascii=False, separators=(",", ":"))
        )


def default_plan_path(plan_id: str) -> Path:
    return RUNTIME_ROOT / "plans" / f"plan-{plan_id}.json"


def journal_path(plan: dict[str, Any]) -> Path:
    root = Path(str(plan["root"])).resolve()
    return root / "整理记录" / f"journal-{plan['plan_id']}.jsonl"


def plan_summary(plan: dict[str, Any], *, plan_path: Path | None = None) -> dict[str, Any]:
    payload = {
        "schema": f"{SCHEMA}.plan_summary",
        "ok": True,
        "plan_id": plan.get("plan_id"),
        "root": plan.get("root"),
        "hardware_fingerprint": (plan.get("hardware_binding") or {}).get("stable_fingerprint"),
        "source_snapshot": plan.get("source_snapshot", {}),
        "summary": plan.get("summary", {}),
        "safety": plan.get("safety", {}),
    }
    if plan_path is not None:
        payload["plan_path"] = str(plan_path)
    return payload


def reusable_inventory_rows(plan: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Reuse owner-produced hashes for metadata iterations; apply rehashes all files."""
    expected: dict[str, dict[str, Any]] = {
        str(row["source"]): row for row in plan["entries"] if isinstance(row, dict)
    }
    current_paths = {path.relative_to(root).as_posix(): path for path in scan_media(root)}
    if set(current_paths) != set(expected):
        added = sorted(set(current_paths) - set(expected))[:20]
        missing = sorted(set(expected) - set(current_paths))[:20]
        raise ValueError(f"inventory_snapshot_stale:added={added}:missing={missing}")
    rows: list[dict[str, Any]] = []
    for relative, source_row in expected.items():
        path = safe_path(root, relative)
        if not path.is_file() or path.stat().st_size != int(source_row["size_bytes"]):
            raise ValueError(f"inventory_snapshot_stale:size_or_missing:{relative}")
        rows.append(
            {
                "path": path,
                "size": int(source_row["size_bytes"]),
                "sha256": str(source_row["sha256"]),
                "metadata": source_row.get("metadata", {}),
            }
        )
    return rows


def inventory_command(args: argparse.Namespace) -> dict[str, Any]:
    root, hardware = inspect_root(args.root)
    corrections = load_corrections(args.corrections)
    plan = build_plan(root, corrections=corrections, hardware_binding=hardware)
    if args.full:
        return {**plan, "schema": f"{SCHEMA}.inventory", "read_only": True}
    summary = plan_summary(plan)
    summary.update(
        {
            "schema": f"{SCHEMA}.inventory",
            "read_only": True,
            "hardware_safe_for_content_mutation": hardware.get("safe_for_content_mutation"),
            "hardware_issues": hardware.get("issues", []),
            "sample": plan.get("entries", [])[: min(max(args.limit, 0), 100)],
            "sample_truncated": len(plan.get("entries", [])) > min(max(args.limit, 0), 100),
        }
    )
    return summary


def plan_command(args: argparse.Namespace) -> dict[str, Any]:
    root, hardware = inspect_root(args.root)
    require_mutation_safe(hardware)
    corrections = load_corrections(args.corrections)
    inventory_rows = None
    inventory_source_plan_id = ""
    if args.inventory_plan:
        inventory_plan = load_plan(args.inventory_plan)
        if Path(str(inventory_plan["root"])).resolve() != root:
            raise ValueError("inventory_plan_root_mismatch")
        inventory_rows = reusable_inventory_rows(inventory_plan, root)
        inventory_source_plan_id = str(inventory_plan["plan_id"])
    plan = build_plan(
        root,
        corrections=corrections,
        hardware_binding=hardware,
        inventory_rows=inventory_rows,
    )
    if inventory_source_plan_id:
        plan["inventory_source_plan_id"] = inventory_source_plan_id
    issues = validate_plan_structure(plan)
    if issues:
        return {"schema": f"{SCHEMA}.plan", "ok": False, "reason": "generated_plan_invalid", "issues": issues}
    output = args.out.resolve() if args.out else default_plan_path(str(plan["plan_id"]))
    content = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_new_text(output, content)
    return plan if args.full else plan_summary(plan, plan_path=output)


def apply_command(args: argparse.Namespace) -> dict[str, Any]:
    plan = load_plan(args.plan)
    root, hardware = inspect_root(Path(str(plan["root"])))
    require_mutation_safe(hardware)
    result = apply_plan(
        plan,
        plan_path=args.plan.resolve(),
        confirm_plan_id=args.confirm_plan_id,
        fresh_hardware=hardware,
        journal_path=journal_path(plan),
    )
    result["root"] = str(root)
    return result


def rollback_command(args: argparse.Namespace) -> dict[str, Any]:
    plan = load_plan(args.plan)
    root, hardware = inspect_root(Path(str(plan["root"])))
    require_mutation_safe(hardware)
    result = rollback_plan(
        plan,
        confirm_plan_id=args.confirm_plan_id,
        fresh_hardware=hardware,
        journal_path=journal_path(plan),
    )
    result["root"] = str(root)
    return result


def validate_command(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        ffprobe = find_ffprobe()
        checks.append({"name": "ffprobe_available", "ok": True, "path": ffprobe})
    except RuntimeError as exc:
        checks.append({"name": "ffprobe_available", "ok": False, "reason": str(exc)})
    checks.extend(
        [
            {"name": "runtime_root_is_local", "ok": RUNTIME_ROOT.drive.upper() == ROOT.drive.upper(), "path": str(RUNTIME_ROOT)},
            {"name": "delete_not_implemented", "ok": True},
            {"name": "device_control_not_owned", "ok": True},
            {"name": "network_not_owned", "ok": True},
        ]
    )
    plan_validation: dict[str, Any] | None = None
    if args.plan:
        try:
            plan = load_plan(args.plan)
            plan_validation = validate_state(plan, expected=args.expected)
            checks.append({"name": "plan_and_state", "ok": bool(plan_validation.get("ok")), "detail": plan_validation})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            checks.append({"name": "plan_and_state", "ok": False, "reason": str(exc)})
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": all(bool(item.get("ok")) for item in checks),
        "generated_at": now_iso(),
        "checks": checks,
        "plan_validation": plan_validation,
    }


def doctor_command(args: argparse.Namespace) -> dict[str, Any]:
    validation_args = argparse.Namespace(plan=None, expected="source")
    validation = validate_command(validation_args)
    hardware: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = [
        {"severity": "risk", "code": "owner_validation_failed", "detail": item}
        for item in validation["checks"]
        if not item.get("ok")
    ]
    if args.root:
        try:
            _, hardware = inspect_root(args.root)
            if not hardware.get("safe_for_content_mutation"):
                issues.append({"severity": "risk", "code": "hardware_not_safe_for_content_mutation", "detail": hardware.get("issues", [])})
        except (OSError, ValueError) as exc:
            issues.append({"severity": "risk", "code": "hardware_inspection_failed", "detail": str(exc)})
    return {
        "schema": f"{SCHEMA}.doctor",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "hardware": hardware,
        "issues": issues,
        "next_action": "inspect_risk_rows" if issues else "none",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Governed USB-aware music library owner")
    sub = parser.add_subparsers(dest="command", required=True)

    inventory = sub.add_parser("inventory")
    inventory.add_argument("--root", type=Path, required=True)
    inventory.add_argument("--corrections", type=Path)
    inventory.add_argument("--limit", type=int, default=20)
    inventory.add_argument("--full", action="store_true")

    plan = sub.add_parser("plan")
    plan.add_argument("--root", type=Path, required=True)
    plan.add_argument("--corrections", type=Path)
    plan.add_argument("--inventory-plan", type=Path, help="Reuse a fresh owner plan's hashes for correction-only replanning.")
    plan.add_argument("--out", type=Path)
    plan.add_argument("--full", action="store_true")

    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--confirm-plan-id", required=True)

    rollback = sub.add_parser("rollback")
    rollback.add_argument("--plan", type=Path, required=True)
    rollback.add_argument("--confirm-plan-id", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--plan", type=Path)
    validate.add_argument("--expected", choices=("source", "applied"), default="source")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inventory":
            payload = inventory_command(args)
        elif args.command == "plan":
            payload = plan_command(args)
        elif args.command == "apply":
            payload = apply_command(args)
        elif args.command == "rollback":
            payload = rollback_command(args)
        elif args.command == "validate":
            payload = validate_command(args)
        else:
            payload = doctor_command(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        payload = {
            "schema": f"{SCHEMA}.{args.command}",
            "ok": False,
            "error_class": type(exc).__name__,
            "reason": str(exc),
            "next_action": "inspect_owner_error_without_bypassing_hardware_or_plan_guards",
        }
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
