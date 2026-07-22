"""Maintenance command adapters for mobile_openclaw_cli.

Owns: dispatch for project maintenance command families that already expose
snapshot, doctor, repair-plan, metrics, validate, or similarly bounded
contracts.
Non-goals: implementing maintenance logic, bridge queue mutation, Weixin
delivery, or permission decisions.
State behavior: mirrors the called maintenance command; apply-style actions
remain explicit in the original command contract.
Normal callers: mobile_openclaw_cli.main for resource-process,
defender-governance, performance, email-scheduler, codex-config-guard, and
source-scan commands.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def register_maintenance_command_parsers(subparsers: Any) -> None:
    resource_process = subparsers.add_parser("resource-process", help="Resource/MCP process lifecycle doctor")
    resource_process.add_argument("action", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate", "startup-sources", "cleanup"])
    resource_process.add_argument("--apply", action="store_true", help="Apply cleanup for revalidated orphan root candidates")
    resource_process.add_argument("--safe-apply", action="store_true", help="Apply only candidates that pass current-turn MCP safety gates")
    resource_process.add_argument("--include-protected", action="store_true", help="Allow cleanup candidates from protected groups")
    resource_process.add_argument("--group", action="append", default=[], help="Restrict cleanup to one group; can be repeated")
    resource_process.add_argument("--min-age-minutes", type=float, default=15.0, help="Only select orphan root candidates at least this old")

    defender_governance = subparsers.add_parser("defender-governance", help="Persistent Defender governance for Codex paths")
    defender_governance.add_argument("action", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate", "apply"])

    performance = subparsers.add_parser("performance", help="Read-only workstation performance snapshot/doctor/repair-plan")
    performance.add_argument("action", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate"])
    performance.add_argument("--observe-seconds", type=float, default=10.0, help="Observation window, capped by the performance doctor")
    performance.add_argument("--top", type=int, default=15, help="Number of top process rows to return")
    performance.add_argument("--profile", choices=["quick", "standard", "deep"], default=None, help="Probe depth; quick avoids deep WMI/resource validation")

    email_scheduler = subparsers.add_parser("email-scheduler", help="Resident email scheduler maintenance contract")
    email_scheduler.add_argument("action", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate", "commands", "state-index", "state-query"])
    email_scheduler.add_argument("--table", choices=["summary", "tasks", "stages", "inbox", "receipts", "identities"], default="summary")
    email_scheduler.add_argument("--status", default="")
    email_scheduler.add_argument("--limit", type=int, default=50)
    email_scheduler.add_argument("--apply", action="store_true")

    codex_guard = subparsers.add_parser("codex-config-guard", help="Codex config drift guard maintenance contract")
    codex_guard.add_argument("action", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate", "run-once"])
    codex_guard.add_argument("--apply", action="store_true", help="Apply merge-only baseline repair for run-once")
    codex_guard.add_argument("--run-cli", action="store_true", help="Include heavier Codex CLI visibility checks")

    source_scan = subparsers.add_parser("source-scan", help="Read-only source/config file list for mobile bridge governance")
    source_scan.add_argument("--paths-only", action="store_true", help="Emit newline-separated source paths")
    source_scan.add_argument("--validate", action="store_true", help="Validate that source-scan excludes runtime/data paths")

    bridge_db = subparsers.add_parser("bridge-db-maintenance", help="Bridge SQLite event archive and size maintenance")
    bridge_db.add_argument("action", choices=["event-archive", "archive-offload"])
    bridge_db.add_argument("--db", default="", help="Override bridge SQLite database path")
    bridge_db.add_argument("--archive-path", default="", help="Override external archive SQLite path for archive-offload")
    bridge_db.add_argument("--retention-hours", type=int, default=24, help="Only archive eligible noisy events older than this")
    bridge_db.add_argument("--apply", action="store_true", help="Apply reviewed event archive changes")
    bridge_db.add_argument("--vacuum", action="store_true", help="Run VACUUM after apply to reclaim disk space")


def run_resource_process_command(args: Any) -> dict[str, Any]:
    from resource_process_doctor import cleanup_orphan_candidates
    from resource_process_doctor import doctor
    from resource_process_doctor import metrics
    from resource_process_doctor import process_snapshot
    from resource_process_doctor import repair_plan
    from resource_process_doctor import startup_sources
    from resource_process_doctor import validate

    snapshot = process_snapshot()
    if args.action == "snapshot":
        return snapshot
    if args.action == "doctor":
        return doctor(snapshot)
    if args.action == "repair-plan":
        return repair_plan(snapshot)
    if args.action == "metrics":
        return metrics(snapshot)
    if args.action == "validate":
        return validate(snapshot)
    if args.action == "startup-sources":
        return startup_sources(snapshot)
    return cleanup_orphan_candidates(
        apply=bool(args.apply),
        safe_apply=bool(args.safe_apply),
        include_protected=bool(args.include_protected),
        groups=list(args.group or []),
        min_age_minutes=float(args.min_age_minutes),
    )


def run_defender_governance_command(args: Any) -> dict[str, Any]:
    from defender_governance import apply
    from defender_governance import doctor
    from defender_governance import metrics
    from defender_governance import repair_plan
    from defender_governance import snapshot
    from defender_governance import validate

    if args.action == "snapshot":
        return snapshot()
    if args.action == "doctor":
        return doctor()
    if args.action == "repair-plan":
        return repair_plan()
    if args.action == "metrics":
        return metrics()
    if args.action == "validate":
        return validate()
    return apply()


def run_performance_command(args: Any) -> dict[str, Any]:
    from performance_doctor import doctor
    from performance_doctor import metrics
    from performance_doctor import repair_plan
    from performance_doctor import sample
    from performance_doctor import validate

    profile = args.profile
    if profile is None:
        profile = "quick" if args.action == "metrics" else "deep" if args.action == "repair-plan" else "standard"
    kwargs = {
        "observe_seconds": float(args.observe_seconds or 0.0),
        "top": int(args.top or 15),
        "profile": profile,
    }
    if args.action == "snapshot":
        return sample(**kwargs)
    if args.action == "doctor":
        return doctor(**kwargs)
    if args.action == "repair-plan":
        return repair_plan(**kwargs)
    if args.action == "metrics":
        return metrics(**kwargs)
    return validate(**kwargs)


def run_email_scheduler_command(args: Any, project_root: Path) -> dict[str, Any]:
    shared_root = project_root / "_bridge" / "shared"
    if str(shared_root) not in sys.path:
        sys.path.insert(0, str(shared_root))
    from email_scheduler import doctor
    from email_scheduler import command_catalog
    from email_scheduler import email_state_index
    from email_scheduler import email_state_query
    from email_scheduler import metrics
    from email_scheduler import repair_plan
    from email_scheduler import snapshot
    from email_scheduler import validate

    if args.action == "commands":
        return command_catalog()
    if args.action == "state-index":
        return email_state_index("refresh", apply=bool(args.apply))
    if args.action == "state-query":
        return email_state_query(args.table, status=args.status, limit=args.limit)
    if args.action == "snapshot":
        return snapshot()
    if args.action == "doctor":
        return doctor()
    if args.action == "repair-plan":
        return repair_plan()
    if args.action == "metrics":
        return metrics()
    return validate()


def run_codex_config_guard_command(args: Any) -> dict[str, Any]:
    import codex_config_guard

    if args.action == "snapshot":
        return codex_config_guard.snapshot(run_cli=bool(args.run_cli))
    if args.action == "doctor":
        return codex_config_guard.doctor(run_cli=bool(args.run_cli))
    if args.action == "repair-plan":
        return codex_config_guard.repair_plan()
    if args.action == "metrics":
        return codex_config_guard.metrics()
    if args.action == "validate":
        return codex_config_guard.validate()
    return codex_config_guard.run_once(apply=bool(args.apply))


def run_source_scan_command(args: Any, bridge_root: Path) -> tuple[dict[str, Any] | None, list[str] | None]:
    import source_scan

    if args.validate:
        return source_scan.validate(bridge_root), None
    if args.paths_only:
        return None, list(source_scan.source_files(bridge_root))
    return source_scan.snapshot(bridge_root), None


def run_bridge_db_command(args: Any, default_db_path: Path) -> dict[str, Any]:
    from mobile_maintenance import bridge_db_archive_offload
    from mobile_maintenance import bridge_db_event_archive

    db_path = Path(str(args.db or "")) if str(args.db or "").strip() else Path(default_db_path)
    if args.action == "archive-offload":
        archive_path = Path(str(args.archive_path or "")) if str(args.archive_path or "").strip() else None
        return bridge_db_archive_offload(
            db_path,
            archive_path=archive_path,
            apply=bool(args.apply),
            vacuum=bool(args.vacuum),
        )
    return bridge_db_event_archive(
        db_path,
        retention_hours=int(args.retention_hours or 24),
        apply=bool(args.apply),
        vacuum=bool(args.vacuum),
    )
