"""Tool, plugin, GUI, CDP, and supplement fallback CLI adapter.

Owns: argparse registration and dispatch for read-oriented local tool health
commands and the supplement stdio fallback command family.
Non-goals: tool registry policy, GUI/OCR implementation, CDP probing logic,
mobile MCP server implementation, or supplement ownership rules.
State behavior: health/probe commands are read-only; supplement fallback may
ack a specified supplement only through the existing bounded MCP fallback path.
Normal callers: mobile_openclaw_cli.build_parser and mobile_openclaw_cli.main.
"""

from __future__ import annotations

from typing import Any, Callable


def register_tool_health_parsers(subparsers: Any) -> None:
    subparsers.add_parser("tool-registry-health", help="Read-only local Codex tool registry health summary")
    subparsers.add_parser("tool-registry-drift-check", help="Read-only drift audit between TOOL_REGISTRY.md and live tool health")
    subparsers.add_parser("codex-plugin-config-health", help="Read-only Codex plugin config/cache/CLI visibility health check")
    subparsers.add_parser("codex-plugin-cli-visibility-boundary-check", help="Run read-only plugin CLI visibility boundary regression check")

    gui_health = subparsers.add_parser("gui-automation-health", help="Read-only GUI automation runtime/OCR health check")
    gui_health.add_argument("--prewarm", action="store_true", help="Run OCR status prewarm while checking")
    gui_health.add_argument("--ocr-probe", action="store_true", help="Run a real OCR GPU probe image and CPU fallback check")

    subparsers.add_parser("gui-ocr-gpu-probe", help="Read-only OCR GPU candidate and CPU fallback probe")
    subparsers.add_parser("cdp-startup-contract-check", help="Read-only CDP startup script and endpoint contract check")
    subparsers.add_parser("cdp-recovery-plan", help="Read-only CDP recovery plan for the visible route")

    codex_log_health = subparsers.add_parser(
        "codex-log-sqlite-health",
        help="Read-only bounded health check for Codex logs_2.sqlite write pressure",
    )
    codex_log_health.add_argument(
        "--observe-seconds",
        type=float,
        default=0.0,
        help="Optional bounded observation window, capped at 60 seconds",
    )

    supplement_fallback = subparsers.add_parser(
        "supplement-fallback",
        help="Local stdio MCP fallback for supplement get/ack when current-session MCP transport is closed",
    )
    supplement_fallback.add_argument("action", choices=["get-pending-batch", "ack-message", "health"])
    supplement_fallback.add_argument("--thread-id", default="", help="Active Codex thread id for supplement lookup")
    supplement_fallback.add_argument("--message-id", default="", help="Supplement message id to acknowledge")
    supplement_fallback.add_argument("--timeout-seconds", type=int, default=8, help="Bounded local MCP fallback timeout")


def run_tool_health_command(
    args: Any,
    queue: Any,
    config: dict[str, Any],
    *,
    tool_registry_health: Callable[..., dict[str, Any]],
    tool_registry_drift_check: Callable[..., dict[str, Any]],
    codex_plugin_config_health: Callable[[], dict[str, Any]],
    codex_plugin_cli_visibility_boundary_check: Callable[[], dict[str, Any]],
    gui_automation_health_check: Callable[..., dict[str, Any]],
    gui_ocr_gpu_probe: Callable[[dict[str, Any]], dict[str, Any]],
    cdp_startup_contract_check: Callable[[dict[str, Any]], dict[str, Any]],
    cdp_recovery_plan: Callable[[dict[str, Any]], dict[str, Any]],
    codex_logs_sqlite_health: Callable[..., dict[str, Any]],
    mobile_mcp_stdio_tool_call: Callable[..., dict[str, Any]],
    supplement_fallback_get_pending_batch: Callable[..., dict[str, Any]],
    supplement_fallback_ack_message: Callable[..., dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    if args.cmd == "tool-registry-health":
        return tool_registry_health(queue, config), 0
    if args.cmd == "tool-registry-drift-check":
        return tool_registry_drift_check(queue, config), 0
    if args.cmd == "codex-plugin-config-health":
        return codex_plugin_config_health(), 0
    if args.cmd == "codex-plugin-cli-visibility-boundary-check":
        return codex_plugin_cli_visibility_boundary_check(), 0
    if args.cmd == "gui-automation-health":
        return gui_automation_health_check(config, prewarm=bool(args.prewarm), ocr_probe=bool(args.ocr_probe)), 0
    if args.cmd == "gui-ocr-gpu-probe":
        return gui_ocr_gpu_probe(config), 0
    if args.cmd == "cdp-startup-contract-check":
        return cdp_startup_contract_check(config), 0
    if args.cmd == "cdp-recovery-plan":
        return cdp_recovery_plan(config), 0
    if args.cmd == "codex-log-sqlite-health":
        return codex_logs_sqlite_health(observe_seconds=float(args.observe_seconds or 0.0)), 0

    timeout_seconds = max(1, int(args.timeout_seconds or 8))
    if args.action == "health":
        payload = mobile_mcp_stdio_tool_call(
            config,
            "bridge.health",
            {},
            timeout_seconds=timeout_seconds,
        )
    elif args.action == "get-pending-batch":
        payload = supplement_fallback_get_pending_batch(
            config,
            str(args.thread_id or ""),
            timeout_seconds=timeout_seconds,
        )
    else:
        payload = supplement_fallback_ack_message(
            config,
            str(args.thread_id or ""),
            str(args.message_id or ""),
            timeout_seconds=timeout_seconds,
        )
    return payload, 0 if payload.get("ok") else 1
