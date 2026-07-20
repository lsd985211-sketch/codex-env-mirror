#!/usr/bin/env python3
"""MCP server for the Windows desktop Weixin CLI-Anything harness.

This is a thin, explicit wrapper around the local `cli-anything-weixin`
backend. It exposes stable MCP tools for current desktop Weixin abilities
without turning the MCP into an arbitrary GUI command executor.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = ROOT / "_bridge" / "cli_anything_weixin" / "agent-harness"
RUNTIME_ROOT = ROOT / "_bridge" / "runtime" / "desktop_weixin_mcp"
MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "desktop-weixin"
SERVER_VERSION = "0.1.0"
MAX_TEXT_CHARS = 5000
MAX_SEARCH_CHARS = 256

if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")

from cli_anything.weixin.core import windows  # noqa: E402


def _json_content(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": not bool(payload.get("ok", True)),
    }


def _safe_runtime_dir(prefix: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in prefix)[:48] or "run"
    out_dir = RUNTIME_ROOT / f"{safe}_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _text_arg(params: dict[str, Any], name: str, *, required: bool = True, max_chars: int = MAX_TEXT_CHARS) -> str:
    value = str(params.get(name) or "")
    if required and not value:
        raise RuntimeError(f"{name} must not be empty.")
    if len(value) > max_chars:
        raise RuntimeError(f"{name} exceeds {max_chars} characters.")
    return value


def _bool_arg(params: dict[str, Any], name: str, default: bool = False) -> bool:
    value = params.get(name, default)
    if isinstance(value, bool):
        return value
    raise RuntimeError(f"{name} must be a boolean.")


def _int_arg(params: dict[str, Any], name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    value = int(params.get(name, default))
    if minimum is not None and value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"{name} must be <= {maximum}.")
    return value


def _float_arg(params: dict[str, Any], name: str, default: float) -> float:
    value = float(params.get(name, default))
    if value < 0.0 or value > 1.0:
        raise RuntimeError(f"{name} must be between 0.0 and 1.0.")
    return value


def _confirm(params: dict[str, Any], name: str, expected: str) -> str:
    value = str(params.get(name) or "")
    if value != expected:
        raise RuntimeError(f"{name} must be exactly {expected}.")
    return value


def _status(_: dict[str, Any]) -> dict[str, Any]:
    items = windows.list_windows()
    return {"ok": bool(items), "window_count": len(items), "windows": items, "best": items[0] if items else None}


def _capabilities(_: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "backend": {
            "type": "cli-anything-weixin-python-api",
            "harness_root": str(HARNESS_ROOT),
            "runtime_root": str(RUNTIME_ROOT),
        },
        "safety_policy": {
            "send_requires": {"confirm_send": "SEND"},
            "close_requires": {"confirm_close": "CLOSE"},
            "prepare_requires": {"confirm_prepare": "DRAFT"},
            "smoke_requires": {"draft": "DRAFT", "emoji_panel": "PANEL", "file_picker": "PICKER"},
            "forbidden": [
                "login automation",
                "payment",
                "contact mutation",
                "call control",
                "chat transcript extraction",
                "arbitrary shell execution",
            ],
        },
        "extension_contract": {
            "add_backend_function": "cli_anything.weixin.core.windows",
            "add_mcp_tool": "register a ToolEntry with schema, annotations, and bounded parameter validation",
            "no_freeform_executor": True,
        },
        "tools": sorted(TOOL_REGISTRY),
    }


def _activate(_: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **windows.activate_window()}


def _open(params: dict[str, Any]) -> dict[str, Any]:
    return windows.open_weixin(wait_seconds=float(params.get("wait_seconds", 8.0)))


def _close(params: dict[str, Any]) -> dict[str, Any]:
    return windows.close_weixin(
        confirm_close=_confirm(params, "confirm_close", "CLOSE"),
        wait_seconds=float(params.get("wait_seconds", 3.0)),
    )


def _screenshot(_: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **windows.screenshot(_safe_runtime_dir("screenshot") / "weixin_window.png")}


def _chat_select_row(params: dict[str, Any]) -> dict[str, Any]:
    return windows.select_chat_row(
        index=_int_arg(params, "index", 3, minimum=1, maximum=50),
        x=_int_arg(params, "x", 180, minimum=0, maximum=2000),
        first_y=_int_arg(params, "first_y", 135, minimum=0, maximum=2000),
        row_height=_int_arg(params, "row_height", 82, minimum=1, maximum=400),
        output_dir=_safe_runtime_dir("select_row"),
    )


def _chat_search(params: dict[str, Any]) -> dict[str, Any]:
    return windows.search_chat(
        _text_arg(params, "query", max_chars=MAX_SEARCH_CHARS),
        select_first=_bool_arg(params, "select_first", False),
        output_dir=_safe_runtime_dir("search_chat"),
    )


def _chat_clear_search(_: dict[str, Any]) -> dict[str, Any]:
    return windows.clear_search(output_dir=_safe_runtime_dir("clear_search"))


def _panel_emoji_smoke(params: dict[str, Any]) -> dict[str, Any]:
    return windows.emoji_smoke(
        confirm_smoke=_confirm(params, "confirm_smoke", "PANEL"),
        output_dir=_safe_runtime_dir("emoji_smoke"),
        x_ratio=_float_arg(params, "x_ratio", 0.377),
        y_ratio=_float_arg(params, "y_ratio", 0.937),
    )


def _file_picker_smoke(params: dict[str, Any]) -> dict[str, Any]:
    return windows.file_picker_smoke(
        confirm_smoke=_confirm(params, "confirm_smoke", "PICKER"),
        output_dir=_safe_runtime_dir("file_picker_smoke"),
        x_ratio=_float_arg(params, "x_ratio", 0.458),
        y_ratio=_float_arg(params, "y_ratio", 0.937),
    )


def _draft_focus_input(params: dict[str, Any]) -> dict[str, Any]:
    return windows.focus_input(
        x_ratio=_float_arg(params, "x_ratio", 0.55),
        y_ratio=_float_arg(params, "y_ratio", 0.90),
    )


def _draft_paste(params: dict[str, Any]) -> dict[str, Any]:
    return windows.paste_draft(
        _text_arg(params, "text"),
        activate=_bool_arg(params, "activate", True),
    )


def _draft_clear(params: dict[str, Any]) -> dict[str, Any]:
    return windows.clear_input(activate=_bool_arg(params, "activate", True))


def _draft_smoke(params: dict[str, Any]) -> dict[str, Any]:
    text = _text_arg(params, "text", required=False) or "MCP_WEIXIN_DRAFT_TEST_DO_NOT_SEND"
    return windows.draft_smoke(
        text,
        confirm_smoke=_confirm(params, "confirm_smoke", "DRAFT"),
        output_dir=_safe_runtime_dir("draft_smoke"),
        x_ratio=_float_arg(params, "x_ratio", 0.55),
        y_ratio=_float_arg(params, "y_ratio", 0.90),
    )


def _draft_send_current(params: dict[str, Any]) -> dict[str, Any]:
    return windows.send_current(confirm_send=_confirm(params, "confirm_send", "SEND"))


def _message_prepare(params: dict[str, Any]) -> dict[str, Any]:
    return windows.prepare_message(
        _text_arg(params, "text"),
        confirm_prepare=_confirm(params, "confirm_prepare", "DRAFT"),
        output_dir=_safe_runtime_dir("prepare_message"),
        x_ratio=_float_arg(params, "x_ratio", 0.55),
        y_ratio=_float_arg(params, "y_ratio", 0.90),
    )


def _message_send_text(params: dict[str, Any]) -> dict[str, Any]:
    return windows.send_text(
        _text_arg(params, "text"),
        confirm_send=_confirm(params, "confirm_send", "SEND"),
        output_dir=_safe_runtime_dir("send_text"),
        x_ratio=_float_arg(params, "x_ratio", 0.55),
        y_ratio=_float_arg(params, "y_ratio", 0.90),
    )


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TEXT_PROP = {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_CHARS}
RATIO_PROP = {"type": "number", "minimum": 0.0, "maximum": 1.0}
CONFIRM_SEND_PROP = {"type": "string", "const": "SEND"}
CONFIRM_DRAFT_PROP = {"type": "string", "const": "DRAFT"}


@dataclass(frozen=True)
class ToolEntry:
    description: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    input_schema: dict[str, Any]
    read_only: bool = False
    destructive: bool = False

    def spec(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "description": self.description,
            "annotations": {
                "title": name,
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": self.read_only,
                "openWorldHint": False,
            },
            "inputSchema": self.input_schema,
        }


TOOL_REGISTRY: dict[str, ToolEntry] = {
    "desktop_weixin.capabilities": ToolEntry(
        "Describe the desktop Weixin MCP backend, safety policy, and extension contract.",
        _capabilities,
        _schema({}),
        read_only=True,
    ),
    "desktop_weixin.status": ToolEntry("List visible Windows desktop Weixin candidates.", _status, _schema({}), read_only=True),
    "desktop_weixin.activate": ToolEntry("Activate the best visible desktop Weixin window.", _activate, _schema({})),
    "desktop_weixin.open": ToolEntry(
        "Open the desktop Weixin app or activate an existing Weixin window.",
        _open,
        _schema({"wait_seconds": {"type": "number", "minimum": 0.5, "maximum": 30.0, "default": 8.0}}),
    ),
    "desktop_weixin.close": ToolEntry(
        "Close the current desktop Weixin window. Requires confirm_close=CLOSE.",
        _close,
        _schema({"confirm_close": {"type": "string", "const": "CLOSE"}, "wait_seconds": {"type": "number", "minimum": 0.5, "maximum": 30.0, "default": 3.0}}, ["confirm_close"]),
        destructive=True,
    ),
    "desktop_weixin.screenshot": ToolEntry("Capture the current desktop Weixin window to the MCP runtime evidence directory.", _screenshot, _schema({})),
    "desktop_weixin.chat_select_row": ToolEntry(
        "Select a visible chat row by approximate window-relative geometry.",
        _chat_select_row,
        _schema({
            "index": {"type": "integer", "minimum": 1, "maximum": 50, "default": 3},
            "x": {"type": "integer", "minimum": 0, "maximum": 2000, "default": 180},
            "first_y": {"type": "integer", "minimum": 0, "maximum": 2000, "default": 135},
            "row_height": {"type": "integer", "minimum": 1, "maximum": 400, "default": 82},
        }),
    ),
    "desktop_weixin.chat_search": ToolEntry(
        "Search desktop Weixin chats or contacts and optionally select the first visible result.",
        _chat_search,
        _schema({"query": {"type": "string", "minLength": 1, "maxLength": MAX_SEARCH_CHARS}, "select_first": {"type": "boolean", "default": False}}, ["query"]),
    ),
    "desktop_weixin.chat_clear_search": ToolEntry("Clear the desktop Weixin search box and exit search state.", _chat_clear_search, _schema({})),
    "desktop_weixin.panel_emoji_smoke": ToolEntry(
        "Open and close the emoji panel for a safe smoke test. Requires confirm_smoke=PANEL.",
        _panel_emoji_smoke,
        _schema({"confirm_smoke": {"type": "string", "const": "PANEL"}, "x_ratio": RATIO_PROP, "y_ratio": RATIO_PROP}, ["confirm_smoke"]),
    ),
    "desktop_weixin.file_picker_smoke": ToolEntry(
        "Open and cancel the file picker for a safe smoke test. Requires confirm_smoke=PICKER.",
        _file_picker_smoke,
        _schema({"confirm_smoke": {"type": "string", "const": "PICKER"}, "x_ratio": RATIO_PROP, "y_ratio": RATIO_PROP}, ["confirm_smoke"]),
    ),
    "desktop_weixin.draft_focus_input": ToolEntry("Click the expected current-chat input area.", _draft_focus_input, _schema({"x_ratio": RATIO_PROP, "y_ratio": RATIO_PROP})),
    "desktop_weixin.draft_paste": ToolEntry("Paste text into the current Weixin input without sending.", _draft_paste, _schema({"text": TEXT_PROP, "activate": {"type": "boolean", "default": True}}, ["text"])),
    "desktop_weixin.draft_clear": ToolEntry("Clear the current Weixin input field.", _draft_clear, _schema({"activate": {"type": "boolean", "default": True}})),
    "desktop_weixin.draft_smoke": ToolEntry(
        "Paste, screenshot, and clear a draft marker without sending. Requires confirm_smoke=DRAFT.",
        _draft_smoke,
        _schema({"text": {"type": "string", "maxLength": MAX_TEXT_CHARS}, "confirm_smoke": CONFIRM_DRAFT_PROP, "x_ratio": RATIO_PROP, "y_ratio": RATIO_PROP}, ["confirm_smoke"]),
    ),
    "desktop_weixin.draft_send_current": ToolEntry(
        "Send the current Weixin input. Requires confirm_send=SEND.",
        _draft_send_current,
        _schema({"confirm_send": CONFIRM_SEND_PROP}, ["confirm_send"]),
        destructive=True,
    ),
    "desktop_weixin.message_prepare": ToolEntry(
        "Prepare a visible draft message without sending. Requires confirm_prepare=DRAFT.",
        _message_prepare,
        _schema({"text": TEXT_PROP, "confirm_prepare": CONFIRM_DRAFT_PROP, "x_ratio": RATIO_PROP, "y_ratio": RATIO_PROP}, ["text", "confirm_prepare"]),
    ),
    "desktop_weixin.message_send_text": ToolEntry(
        "Prepare and send text in the current chat. Requires confirm_send=SEND.",
        _message_send_text,
        _schema({"text": TEXT_PROP, "confirm_send": CONFIRM_SEND_PROP, "x_ratio": RATIO_PROP, "y_ratio": RATIO_PROP}, ["text", "confirm_send"]),
        destructive=True,
    ),
}


class DesktopWeixinService:
    def instructions(self) -> str:
        return (
            "Use this MCP only for the Windows desktop Weixin app. "
            "It is separate from the mobile OpenClaw bridge. Sending tools require explicit confirmation."
        )

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": self.instructions(),
        }

    def tool_specs(self) -> list[dict[str, Any]]:
        return [entry.spec(name) for name, entry in sorted(TOOL_REGISTRY.items())]

    def tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {"tools": self.tool_specs()}

    def tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        entry = TOOL_REGISTRY.get(name)
        if not entry:
            return _json_content({"ok": False, "reason": "unknown_tool", "tool": name})
        try:
            payload = entry.handler(arguments)
            if "ok" not in payload:
                payload = {"ok": True, **payload}
            return _json_content(payload)
        except Exception as exc:
            return _json_content({"ok": False, "reason": "tool_error", "error": f"{type(exc).__name__}: {exc}", "tool": name})


def serve(service: DesktopWeixinService) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = str(request.get("method") or "")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            if method == "initialize":
                result = service.initialize(params)
            elif method == "tools/list":
                result = service.tools_list(params)
            elif method == "tools/call":
                result = service.tools_call(params)
            elif method == "notifications/initialized":
                continue
            else:
                result = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
            response = {"jsonrpc": "2.0", "id": request_id, **result} if "error" in result else {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    return serve(DesktopWeixinService())


if __name__ == "__main__":
    raise SystemExit(main())
