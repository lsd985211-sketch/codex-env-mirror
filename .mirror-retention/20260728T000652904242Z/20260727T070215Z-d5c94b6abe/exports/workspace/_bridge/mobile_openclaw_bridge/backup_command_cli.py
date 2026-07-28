"""Backup command adapters for mobile_openclaw_cli.

Owns: argparse registration and dispatch for backup hygiene and backup-router
commands.
Non-goals: backup routing policy, archive selection logic, file copying, or
bridge queue mutation.
State behavior: backup-hygiene apply and backup-router create can write files,
but only through their existing explicit command contracts; other actions are
read-only.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main
when args.cmd is backup-hygiene or backup-router.
"""

from __future__ import annotations

from typing import Any


def register_backup_command_parsers(subparsers: Any) -> None:
    backup_hygiene = subparsers.add_parser("backup-hygiene", help="Backup hygiene doctor and gated archive apply for local .bak-* files")
    backup_hygiene.add_argument("action", choices=["snapshot", "doctor", "repair-plan", "metrics", "validate", "apply"])
    backup_hygiene.add_argument("--apply", action="store_true", help="Archive eligible old backups into _bridge/backups/archive")
    backup_hygiene.add_argument("--confirm", default="", help="Required literal value: archive-old-backups")

    backup_router = subparsers.add_parser("backup-router", help="Plan/create/validate routed backups with manifests")
    backup_router.add_argument("action", choices=["plan", "create", "validate"])
    backup_router.add_argument("paths", nargs="*", help="Files to plan or back up")
    backup_router.add_argument("--remark", default="local-edit")
    backup_router.add_argument("--purpose", default="")
    backup_router.add_argument("--category", default="")
    backup_router.add_argument("--trigger", default="codex")
    backup_router.add_argument("--root", default="", help="Root to validate for manifest health")


def run_backup_hygiene_command(args: Any) -> dict[str, Any]:
    from backup_hygiene_doctor import apply
    from backup_hygiene_doctor import backup_snapshot
    from backup_hygiene_doctor import doctor
    from backup_hygiene_doctor import metrics
    from backup_hygiene_doctor import repair_plan
    from backup_hygiene_doctor import validate

    if args.action == "snapshot":
        return backup_snapshot()
    if args.action == "doctor":
        return doctor()
    if args.action == "repair-plan":
        return repair_plan()
    if args.action == "metrics":
        return metrics()
    if args.action == "validate":
        return validate()
    return apply(confirm=str(args.confirm or ""))


def run_backup_router_command(args: Any) -> dict[str, Any]:
    from shared.backup_router import create_backup
    from shared.backup_router import plan
    from shared.backup_router import validate

    if args.action == "plan":
        return plan(
            list(args.paths or []),
            remark=str(args.remark or ""),
            purpose=str(args.purpose or ""),
            category=str(args.category or ""),
        )
    if args.action == "create":
        return create_backup(
            list(args.paths or []),
            remark=str(args.remark or ""),
            purpose=str(args.purpose or ""),
            category=str(args.category or ""),
            trigger=str(args.trigger or "codex"),
        )
    return validate(str(args.root or ""))
