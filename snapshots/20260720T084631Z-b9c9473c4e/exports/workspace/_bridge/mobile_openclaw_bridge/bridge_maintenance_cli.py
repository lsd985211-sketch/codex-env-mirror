"""Bridge maintenance CLI adapter for mobile_openclaw_cli.

Owns: argparse registration and dispatch for the high-level bridge
maintenance command family.
Non-goals: maintenance diagnosis implementation, repair semantics, queue
schema, Weixin delivery, or permission decisions.
State behavior: mirrors mobile_maintenance; repair writes only when the
existing explicit --apply contract is used.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main
when args.cmd == "maintenance".
"""

from __future__ import annotations

from typing import Any

from bounded_output import bounded_value, governed_cli_payload, output_evidence_policy
from mobile_maintenance import doctor_report
from mobile_maintenance import inspect_report
from mobile_maintenance import iteration_gate_report
from mobile_maintenance import metrics_report
from mobile_maintenance import repair_report
from mobile_maintenance import summary_report


def _doctor_receipt(payload: dict[str, Any], *, full: bool) -> dict[str, Any]:
    full_ref = "command:python _bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py maintenance doctor --full"
    if full:
        return governed_cli_payload(payload, full=True, full_result_ref=full_ref)
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    diagnosis = payload.get("diagnosis") if isinstance(payload.get("diagnosis"), dict) else {}
    database = snapshot.get("database") if isinstance(snapshot.get("database"), dict) else {}
    issues = diagnosis.get("issues") if isinstance(diagnosis.get("issues"), list) else []
    return {
        "schema": "mobile-openclaw-maintenance/doctor-receipt/v1",
        "ok": bool(payload.get("ok")),
        "generated_at": snapshot.get("generated_at"),
        "health": {
            "diagnosis_ok": bool(diagnosis.get("ok")),
            "issue_count": int(diagnosis.get("issue_count") or 0),
            "blocking_issue_count": int(diagnosis.get("blocking_issue_count") or 0),
            "external_dependency_issue_count": int(diagnosis.get("external_dependency_issue_count") or 0),
        },
        "state_summary": {
            "database": {
                "ok": database.get("ok"),
                "integrity_check": database.get("integrity_check"),
                "journal_mode": database.get("journal_mode"),
                "bytes": database.get("bytes"),
                "under_limit": database.get("under_limit"),
            },
            "status_counts": (snapshot.get("counts") or {}).get("by_status") if isinstance(snapshot.get("counts"), dict) else {},
            "active_count": len(snapshot.get("active") or []),
            "pending_count": len(snapshot.get("pending") or []),
            "reply_problem_count": len(snapshot.get("reply_problems") or []),
            "layer_status": snapshot.get("layer_status") if isinstance(snapshot.get("layer_status"), dict) else {},
        },
        "issues": [bounded_value(item, max_depth=5, max_items=8, max_string=700) for item in issues[:20]],
        "has_more_issues": len(issues) > 20,
        "commands": {
            "full": full_ref.removeprefix("command:"),
            "metrics": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py maintenance metrics",
            "repair_plan": "python _bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py maintenance repair",
        },
        "raw_result_ref": full_ref,
        "output_evidence_policy": output_evidence_policy(),
    }


def register_bridge_maintenance_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("maintenance", help="Summarize, inspect, diagnose, or safely repair the Weixin bridge")
    parser.add_argument("action", choices=["summary", "inspect", "doctor", "repair", "metrics", "iteration"])
    parser.add_argument("--apply", action="store_true", help="Apply safe repairs; repair defaults to dry-run")
    parser.add_argument("--deep", action="store_true", help="Run deep probes for maintenance summary")
    parser.add_argument("--full", action="store_true", help="Emit the complete maintenance result instead of the bounded default receipt")
    parser.add_argument(
        "--include-reply-send",
        action="store_true",
        help="Allow repair to schedule due reply_pending messages for Weixin sending",
    )


def run_bridge_maintenance_command(args: Any, queue: Any, config: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    if args.action == "summary":
        return summary_report(queue, config, deep=bool(args.deep)), None
    if args.action == "inspect":
        payload = inspect_report(queue, config)
        return "", governed_cli_payload(payload, full=bool(args.full), full_result_ref="command:python _bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py maintenance inspect --full")
    if args.action == "doctor":
        payload = doctor_report(queue, config)
        return "", _doctor_receipt(payload, full=bool(args.full))
    if args.action == "repair":
        return "", repair_report(
            queue,
            config,
            apply=bool(args.apply),
            include_reply_send=bool(args.include_reply_send),
        )
    if args.action == "metrics":
        payload = metrics_report(queue, config, deep_probes=bool(args.deep))
        return "", governed_cli_payload(payload, full=bool(args.full), full_result_ref="command:python _bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py maintenance metrics --full")
    payload = iteration_gate_report(queue, config)
    return "", governed_cli_payload(payload, full=bool(args.full), full_result_ref="command:python _bridge/mobile_openclaw_bridge/mobile_openclaw_cli.py maintenance iteration --full")
