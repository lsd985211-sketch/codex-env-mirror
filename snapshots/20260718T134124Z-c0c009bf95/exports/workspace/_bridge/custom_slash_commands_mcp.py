#!/usr/bin/env python3
"""Small stdio MCP server for local slash command templates.

This server deliberately does not execute shell commands. It exposes a local
command registry so Codex can list, inspect, validate, and render repeatable
task prompts without depending on chat context.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "_bridge" / "slash_commands" / "commands.json"
MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "custom-slash-commands"
SERVER_VERSION = "0.1.0"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "custom_slash_commands.v1", "commands": []}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {"schema": "custom_slash_commands.v1", "commands": []}


def registry_commands(path: Path) -> list[dict[str, Any]]:
    payload = load_registry(path)
    commands = payload.get("commands")
    return [item for item in commands if isinstance(item, dict)] if isinstance(commands, list) else []


def command_name(value: Any) -> str:
    text = str(value or "").strip()
    return text[1:] if text.startswith("/") else text


def find_command(path: Path, name: str) -> dict[str, Any] | None:
    target = command_name(name)
    for item in registry_commands(path):
        if command_name(item.get("name")) == target:
            return item
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        if target in [command_name(alias) for alias in aliases]:
            return item
    return None


def validate_payload(path: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    commands = registry_commands(path)
    seen: set[str] = set()
    for index, item in enumerate(commands):
        name = command_name(item.get("name"))
        if not name:
            issues.append({"severity": "risk", "code": "missing_name", "message": f"command index {index} has no name"})
            continue
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$", name):
            issues.append({"severity": "risk", "code": "invalid_name", "message": f"invalid command name: {name}"})
        if name in seen:
            issues.append({"severity": "risk", "code": "duplicate_name", "message": f"duplicate command name: {name}"})
        seen.add(name)
        template = str(item.get("template") or "")
        if not template.strip():
            issues.append({"severity": "risk", "code": "missing_template", "message": f"command {name} has no template"})
        if "run_shell" in item or "command" in item:
            issues.append({"severity": "risk", "code": "execution_field_forbidden", "message": f"command {name} contains execution-like fields"})
    return {
        "ok": not any(issue.get("severity") == "risk" for issue in issues),
        "generated_at": now_iso(),
        "registry_path": str(path),
        "command_count": len(commands),
        "issues": issues,
    }


def render_template(template: str, variables: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = variables.get(key, "")
        return str(value)

    return re.sub(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}", replace, template)


class SlashCommandService:
    def __init__(self, registry_path: Path):
        self.registry_path = registry_path

    def instructions(self) -> str:
        return (
            "Use custom slash commands as local prompt templates only. "
            "This MCP does not execute shell commands. Rendered output should be "
            "reviewed and handled under normal permission, backup, and maintenance rules."
        )

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "slash.list_commands",
                "description": "List registered local slash command templates.",
                "annotations": {
                    "title": "List Slash Commands",
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "slash.get_command",
                "description": "Get one slash command template and metadata by name or alias.",
                "annotations": {
                    "title": "Get Slash Command",
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
                "inputSchema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "slash.render_command",
                "description": "Render a slash command template with explicit variables. This does not execute the result.",
                "annotations": {
                    "title": "Render Slash Command",
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "variables": {"type": "object"},
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "slash.validate_registry",
                "description": "Validate the local slash command registry structure and safety constraints.",
                "annotations": {
                    "title": "Validate Slash Registry",
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "idempotentHint": True,
                    "openWorldHint": False,
                },
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ]

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": self.instructions(),
        }

    def tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {"tools": self.tool_specs()}

    def tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        result = self.dispatch_tool(name, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            "isError": not bool(result.get("ok", True)),
        }

    def dispatch_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        if name == "slash.list_commands":
            items = []
            for item in registry_commands(self.registry_path):
                items.append(
                    {
                        "name": item.get("name"),
                        "aliases": item.get("aliases", []),
                        "description": item.get("description", ""),
                        "category": item.get("category", ""),
                        "variables": item.get("variables", []),
                    }
                )
            return {"ok": True, "registry_path": str(self.registry_path), "commands": items}
        if name == "slash.get_command":
            item = find_command(self.registry_path, str(params.get("name") or ""))
            if not item:
                return {"ok": False, "reason": "command_not_found"}
            return {"ok": True, "command": item}
        if name == "slash.render_command":
            item = find_command(self.registry_path, str(params.get("name") or ""))
            if not item:
                return {"ok": False, "reason": "command_not_found"}
            variables = params.get("variables") if isinstance(params.get("variables"), dict) else {}
            rendered = render_template(str(item.get("template") or ""), variables)
            missing = sorted(set(re.findall(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}", rendered)))
            return {
                "ok": not missing,
                "command_name": item.get("name"),
                "rendered": rendered,
                "missing_variables": missing,
                "execution": "not_executed",
            }
        if name == "slash.validate_registry":
            return validate_payload(self.registry_path)
        return {"ok": False, "reason": "unknown_tool", "tool": name}


def serve(service: SlashCommandService) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
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
            if "error" in result:
                response = {"jsonrpc": "2.0", "id": request.get("id"), **result}
            else:
                response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Custom slash commands MCP server")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = SlashCommandService(args.registry)
    return serve(service)


if __name__ == "__main__":
    raise SystemExit(main())
