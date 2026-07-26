from __future__ import annotations

from typing import Any, Callable


def mobile_bridge_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "mobile_bridge.get_pending_batch",
            "description": (
                "Hub direct fallback for mobile-openclaw-bridge bridge.get_pending_batch. "
                "Use after native bridge.get_pending_batch is unavailable in the current turn."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "fallback_ack": {
                        "type": "string",
                        "description": "Must be native-mcp-unavailable-and-original-permissions-apply.",
                    },
                },
                "required": ["thread_id", "fallback_ack"],
                "additionalProperties": False,
            },
        },
        {
            "name": "mobile_bridge.ack_message",
            "description": (
                "Hub direct fallback for mobile-openclaw-bridge bridge.ack_message. "
                "Use only for supplement messages already incorporated into the active mobile reply."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "message_id": {"type": "string"},
                    "fallback_ack": {
                        "type": "string",
                        "description": "Must be native-mcp-unavailable-and-original-permissions-apply.",
                    },
                },
                "required": ["thread_id", "message_id", "fallback_ack"],
                "additionalProperties": False,
            },
        },
    ]


def mobile_bridge_call(
    name: str,
    arguments: dict[str, Any],
    run_json_command: Callable[[list[str], int], dict[str, Any]],
) -> dict[str, Any] | None:
    if not name.startswith("mobile_bridge."):
        return None
    fallback_ack = str(arguments.get("fallback_ack") or "")
    if fallback_ack != "native-mcp-unavailable-and-original-permissions-apply":
        return {
            "ok": False,
            "reason": "fallback_ack_required",
            "required": "native-mcp-unavailable-and-original-permissions-apply",
            "tool": name,
        }
    thread_id = str(arguments.get("thread_id") or "").strip()
    if not thread_id:
        return {"ok": False, "reason": "thread_id_required", "tool": name}
    base = ["python", "_bridge\\mobile_openclaw_bridge\\mobile_openclaw_cli.py", "supplement-fallback"]
    if name == "mobile_bridge.get_pending_batch":
        command = [*base, "get-pending-batch", "--thread-id", thread_id]
        return run_json_command(command, 15)
    if name == "mobile_bridge.ack_message":
        message_id = str(arguments.get("message_id") or "").strip()
        if not message_id:
            return {"ok": False, "reason": "message_id_required", "tool": name}
        command = [*base, "ack-message", "--thread-id", thread_id, "--message-id", message_id]
        return run_json_command(command, 15)
    return {"ok": False, "reason": "unknown_mobile_bridge_tool", "tool": name}
