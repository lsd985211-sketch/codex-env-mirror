#!/usr/bin/env python3
"""Unified CLI for the soft-admission skill workflow."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DISCOVER_SCRIPT = SCRIPT_DIR / "skill_admission_discover.py"
AUDIT_SCRIPT = SCRIPT_DIR / "skill_admission_audit.py"
APPROVE_SCRIPT = SCRIPT_DIR / "skill_admission_approve.py"
DECIDE_SCRIPT = SCRIPT_DIR / "skill_admission_decide.py"


def run_passthrough(script_path: Path, forwarded_args: list[str]) -> int:
    command = [sys.executable, str(script_path), *forwarded_args]
    completed = subprocess.run(command, check=False)
    return completed.returncode


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[2] in {"-h", "--help"} and sys.argv[1] in {"discover", "audit", "approve", "decide"}:
        command = sys.argv[1]
        forwarded_args = sys.argv[2:]
        if command == "discover":
            return run_passthrough(DISCOVER_SCRIPT, forwarded_args)
        if command == "audit":
            return run_passthrough(AUDIT_SCRIPT, forwarded_args)
        if command == "approve":
            return run_passthrough(APPROVE_SCRIPT, forwarded_args)
        if command == "decide":
            return run_passthrough(DECIDE_SCRIPT, forwarded_args)

    parser = argparse.ArgumentParser(
        description="Unified entrypoint for skill admission discovery, audit, approval, and decisions.",
        epilog=(
            "Examples:\n"
            "  python _bridge\\skill_admission.py discover --json\n"
            "  python _bridge\\skill_admission.py audit --skill-id <skill_id> --json\n"
            "  python _bridge\\skill_admission.py approve --skill-id <skill_id> --json\n"
            "  python _bridge\\skill_admission.py decide deferred --skill-id <skill_id>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=("discover", "audit", "approve", "decide"), help="Admission subcommand")
    args, forwarded_args = parser.parse_known_args()

    if args.command == "discover":
        return run_passthrough(DISCOVER_SCRIPT, forwarded_args)
    if args.command == "audit":
        return run_passthrough(AUDIT_SCRIPT, forwarded_args)
    if args.command == "approve":
        return run_passthrough(APPROVE_SCRIPT, forwarded_args)
    if args.command == "decide":
        return run_passthrough(DECIDE_SCRIPT, forwarded_args)

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
