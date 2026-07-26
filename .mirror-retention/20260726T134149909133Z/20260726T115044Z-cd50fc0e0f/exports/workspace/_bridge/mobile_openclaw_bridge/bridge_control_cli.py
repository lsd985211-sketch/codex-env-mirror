"""Bridge control and safety-mode CLI adapter for mobile_openclaw_cli.

Owns: argparse registration and dispatch for local operator control commands:
control stop/resume, stop-status, confirm-latest, set-secret-hash, mode, and
status.
Non-goals: control action implementation, confirmation-secret hashing,
permission policy, worker dispatch, queue schema, or Weixin delivery.
State behavior: stop/resume, set-secret-hash, mode shadow/real/pause/resume,
and confirm-latest preserve their existing explicit command side effects;
status and stop-status are read-only.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def register_bridge_control_parsers(subparsers: Any) -> None:
    control = subparsers.add_parser("control", help="Run bridge control actions")
    control.add_argument("action", choices=["stop", "resume"])
    control.add_argument("--actor", default="local")

    subparsers.add_parser("stop-status", help="Show PAUSE and STOP_REQUEST soft-interrupt state")

    confirm_latest = subparsers.add_parser("confirm-latest", help="Confirm the latest waiting high-risk task")
    confirm_latest.add_argument("--secret", required=True)
    confirm_latest.add_argument("--user", default="")

    set_secret = subparsers.add_parser("set-secret-hash", help="Store SHA-256 hash for the confirmation secret")
    set_secret.add_argument("--secret", required=True)

    mode = subparsers.add_parser("mode", help="Show or change bridge safety mode")
    mode.add_argument(
        "action",
        choices=["status", "shadow", "real", "pause", "resume"],
        help="status shows mode; shadow disables real Codex delivery; real enables it; pause/resume toggles the PAUSE file",
    )
    subparsers.add_parser("status", help="Alias for 'mode status'")


def run_bridge_control_command(
    args: Any,
    queue: Any,
    config: dict[str, Any],
    *,
    config_path: Path,
    stop_request_path: Path,
    emergency_stop: Callable[..., dict[str, Any]],
    resume_bridge: Callable[..., dict[str, Any]],
    save_config: Callable[[Path, dict[str, Any]], None],
    set_confirmation_secret_hash: Callable[[Path, dict[str, Any], str], str],
) -> dict[str, Any]:
    if args.cmd == "control":
        if args.action == "stop":
            return emergency_stop(queue, config_path, config, actor=args.actor)
        return resume_bridge(queue, config_path, config, actor=args.actor)

    if args.cmd == "stop-status":
        return {
            "ok": True,
            "paused": queue.is_paused(),
            "pause_file": str(queue.pause_file()),
            "pause_file_exists": queue.pause_file().exists(),
            "stop_request": str(stop_request_path),
            "stop_request_exists": stop_request_path.exists(),
            "shadow_mode": queue.shadow_mode(),
        }

    if args.cmd == "confirm-latest":
        ok, message, task = queue.confirm_latest(args.secret, args.user)
        return {
            "ok": ok,
            "message": message,
            "task_id": task.get("id") if task else None,
            "external_user": task.get("external_user") if task else args.user,
        }

    if args.cmd == "set-secret-hash":
        digest = set_confirmation_secret_hash(config_path, config, args.secret)
        return {"ok": True, "confirmation_secret_hash": digest, "config": str(config_path)}

    if args.cmd == "status" or args.action == "status":
        return queue.health()
    if args.action in {"shadow", "real"}:
        config.setdefault("safety", {})["shadow_mode"] = args.action == "shadow"
        save_config(config_path, config)
        return {"ok": True, "shadow_mode": config["safety"]["shadow_mode"], "config": str(config_path)}
    if args.action == "pause":
        queue.pause_file().write_text("paused\n", encoding="utf-8")
        return {"ok": True, "paused": True, "pause_file": str(queue.pause_file())}
    if queue.pause_file().exists():
        queue.pause_file().unlink()
    if stop_request_path.exists():
        stop_request_path.unlink()
    return {"ok": True, "paused": False, "pause_file": str(queue.pause_file())}
