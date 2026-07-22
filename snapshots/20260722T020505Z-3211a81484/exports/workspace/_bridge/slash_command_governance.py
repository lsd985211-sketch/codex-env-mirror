#!/usr/bin/env python3
"""Governed extension workflow for local slash command templates.

This CLI owns registry changes. The MCP server stays read-only/render-only so
template authoring cannot accidentally become command execution.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from custom_slash_commands_mcp import command_name, find_command, render_template, validate_payload
from shared.backup_router import create_backup
from shared.json_cli import now_iso


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "_bridge" / "slash_commands" / "commands.json"
SCHEMA = "slash_command_governance.v1"
FORBIDDEN_FIELDS = {"command", "run_shell", "shell", "exec", "powershell", "cmd"}
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "custom_slash_commands.v1", "version": 0, "commands": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"schema": "custom_slash_commands.v1", "version": 0, "commands": []}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_list(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            text = part.strip()
            if text and text not in items:
                items.append(text)
    return items


def proposal_from_args(args: argparse.Namespace) -> dict[str, Any]:
    command: dict[str, Any] = {
        "name": command_name(args.name),
        "aliases": normalize_list(args.alias),
        "category": args.category.strip(),
        "description": args.description.strip(),
        "target_module": args.target_module.strip(),
        "output_contract": args.output_contract.strip(),
        "variables": normalize_list(args.variable),
        "template": args.template.strip(),
    }
    return {"schema": f"{SCHEMA}.proposal", "generated_at": now_iso(), "command": command}


def load_proposal(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("command"), dict):
        return payload["command"]
    if isinstance(payload, dict):
        return payload
    raise ValueError("proposal must be an object or contain a command object")


def command_keys(item: dict[str, Any]) -> set[str]:
    keys = {command_name(item.get("name"))}
    aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
    keys.update(command_name(alias) for alias in aliases)
    return {key for key in keys if key}


def validate_command(command: dict[str, Any], registry: Path, *, replace: bool = False) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    name = command_name(command.get("name"))
    if not name or not NAME_RE.match(name):
        issues.append({"severity": "risk", "code": "invalid_name", "detail": name})
    if not str(command.get("description") or "").strip():
        issues.append({"severity": "risk", "code": "missing_description", "detail": name})
    if not str(command.get("category") or "").strip():
        issues.append({"severity": "risk", "code": "missing_category", "detail": name})
    if not str(command.get("target_module") or "").strip():
        issues.append({"severity": "risk", "code": "missing_target_module", "detail": name})
    if not str(command.get("output_contract") or "").strip():
        issues.append({"severity": "risk", "code": "missing_output_contract", "detail": name})
    template = str(command.get("template") or "")
    if not template.strip():
        issues.append({"severity": "risk", "code": "missing_template", "detail": name})

    forbidden = sorted(FORBIDDEN_FIELDS.intersection(command.keys()))
    if forbidden:
        issues.append({"severity": "risk", "code": "forbidden_execution_fields", "detail": forbidden})

    variables = normalize_list([str(item) for item in command.get("variables", [])]) if isinstance(command.get("variables"), list) else []
    placeholders = sorted(set(PLACEHOLDER_RE.findall(template)))
    undeclared = sorted(set(placeholders) - set(variables))
    unused = sorted(set(variables) - set(placeholders))
    if undeclared:
        issues.append({"severity": "risk", "code": "undeclared_placeholders", "detail": undeclared})
    if unused:
        issues.append({"severity": "advisory", "code": "unused_variables", "detail": unused})

    payload = read_json(registry)
    existing = payload.get("commands") if isinstance(payload.get("commands"), list) else []
    new_keys = command_keys(command)
    for item in existing:
        if not isinstance(item, dict):
            continue
        existing_name = command_name(item.get("name"))
        overlap = sorted(new_keys.intersection(command_keys(item)))
        if overlap and (not replace or existing_name != name):
            issues.append({"severity": "risk", "code": "duplicate_name_or_alias", "detail": overlap})
    return issues


def validate_registry_extended(registry: Path) -> dict[str, Any]:
    base = validate_payload(registry)
    payload = read_json(registry)
    commands = payload.get("commands") if isinstance(payload.get("commands"), list) else []
    issues: list[dict[str, Any]] = list(base.get("issues", []))
    seen: dict[str, str] = {}
    for item in commands:
        if not isinstance(item, dict):
            issues.append({"severity": "risk", "code": "invalid_command_item"})
            continue
        name = command_name(item.get("name"))
        for key in command_keys(item):
            owner = seen.get(key)
            if owner and owner != name:
                issues.append({"severity": "risk", "code": "duplicate_alias", "detail": {"alias": key, "owners": [owner, name]}})
            seen[key] = name
        issues.extend(validate_command(item, registry, replace=True))
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not any(issue.get("severity") == "risk" for issue in issues),
        "generated_at": now_iso(),
        "registry_path": str(registry),
        "command_count": len(commands),
        "issues": issues,
    }


def sample_variables(command: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    variables = command.get("variables") if isinstance(command.get("variables"), list) else []
    for variable in variables:
        values[str(variable)] = f"sample_{variable}"
    return values


def smoke_render(command: dict[str, Any]) -> dict[str, Any]:
    rendered = render_template(str(command.get("template") or ""), sample_variables(command))
    missing = sorted(set(PLACEHOLDER_RE.findall(rendered)))
    return {
        "ok": not missing,
        "command_name": command.get("name"),
        "missing_variables": missing,
        "rendered_preview": rendered[:1000],
        "execution": "not_executed",
    }


def apply_command(command: dict[str, Any], registry: Path, *, replace: bool, confirm_apply: bool) -> dict[str, Any]:
    issues = validate_command(command, registry, replace=replace)
    blocking = [issue for issue in issues if issue.get("severity") == "risk"]
    if blocking:
        return {"schema": f"{SCHEMA}.apply", "ok": False, "reason": "proposal_validation_failed", "issues": issues}
    if not confirm_apply:
        return {
            "schema": f"{SCHEMA}.apply",
            "ok": False,
            "reason": "confirm_apply_required",
            "issues": issues,
            "dry_run": True,
        }

    backup = create_backup(
        [str(registry)],
        remark="slash-template-governance-apply",
        purpose="apply governed slash command template change",
        category="slash-commands",
        trigger="slash_command_governance",
    )
    if not backup.get("ok"):
        return {"schema": f"{SCHEMA}.apply", "ok": False, "reason": "backup_failed", "backup": backup}

    payload = read_json(registry)
    commands = payload.get("commands") if isinstance(payload.get("commands"), list) else []
    name = command_name(command.get("name"))
    replaced = False
    next_commands: list[dict[str, Any]] = []
    for item in commands:
        if isinstance(item, dict) and command_name(item.get("name")) == name:
            if replace:
                next_commands.append(command)
                replaced = True
            else:
                next_commands.append(item)
        elif isinstance(item, dict):
            next_commands.append(item)
    if not replaced:
        next_commands.append(command)
    payload["schema"] = payload.get("schema") or "custom_slash_commands.v1"
    payload["version"] = int(payload.get("version") or 0) + 1
    payload["updated_at"] = now_iso()
    payload["commands"] = next_commands
    write_json(registry, payload)

    post = validate_registry_extended(registry)
    smoke = smoke_render(command)
    return {
        "schema": f"{SCHEMA}.apply",
        "ok": bool(post.get("ok")) and bool(smoke.get("ok")),
        "generated_at": now_iso(),
        "registry_path": str(registry),
        "action": "replaced" if replaced else "added",
        "command_name": name,
        "backup": backup,
        "post_validate": post,
        "smoke_render": smoke,
        "warnings": [issue for issue in issues if issue.get("severity") != "risk"],
    }


def snapshot(registry: Path) -> dict[str, Any]:
    payload = read_json(registry)
    commands = payload.get("commands") if isinstance(payload.get("commands"), list) else []
    categories: dict[str, int] = {}
    for item in commands:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "uncategorized")
        categories[category] = categories.get(category, 0) + 1
    return {
        "schema": f"{SCHEMA}.snapshot",
        "ok": True,
        "generated_at": now_iso(),
        "registry_path": str(registry),
        "command_count": len(commands),
        "version": payload.get("version"),
        "updated_at": payload.get("updated_at"),
        "categories": categories,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Govern slash command template registry changes")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("snapshot")
    sub.add_parser("validate")

    propose = sub.add_parser("proposal")
    for target in (propose,):
        target.add_argument("--name", required=True)
        target.add_argument("--alias", action="append", default=[])
        target.add_argument("--category", required=True)
        target.add_argument("--description", required=True)
        target.add_argument("--target-module", required=True)
        target.add_argument("--output-contract", required=True)
        target.add_argument("--variable", action="append", default=[])
        target.add_argument("--template", required=True)

    apply = sub.add_parser("apply")
    apply.add_argument("--proposal-file", default="")
    apply.add_argument("--replace", action="store_true")
    apply.add_argument("--confirm-apply", action="store_true")
    apply.add_argument("--name", default="")
    apply.add_argument("--alias", action="append", default=[])
    apply.add_argument("--category", default="")
    apply.add_argument("--description", default="")
    apply.add_argument("--target-module", default="")
    apply.add_argument("--output-contract", default="")
    apply.add_argument("--variable", action="append", default=[])
    apply.add_argument("--template", default="")

    smoke = sub.add_parser("render-smoke")
    smoke.add_argument("--name", required=True)

    args = parser.parse_args(argv)
    registry = Path(args.registry)
    if not registry.is_absolute():
        registry = (ROOT / registry).resolve()

    if args.command == "snapshot":
        result = snapshot(registry)
    elif args.command == "validate":
        result = validate_registry_extended(registry)
    elif args.command == "proposal":
        result = proposal_from_args(args)
        result["validation"] = validate_command(result["command"], registry)
        result["smoke_render"] = smoke_render(result["command"])
        result["ok"] = not any(issue.get("severity") == "risk" for issue in result["validation"]) and result["smoke_render"]["ok"]
    elif args.command == "apply":
        if args.proposal_file:
            command = load_proposal(args.proposal_file)
        else:
            if not args.name:
                raise SystemExit("--name or --proposal-file is required")
            command = proposal_from_args(args)["command"]
        result = apply_command(command, registry, replace=args.replace, confirm_apply=args.confirm_apply)
    else:
        item = find_command(registry, args.name)
        if not item:
            result = {"schema": f"{SCHEMA}.render_smoke", "ok": False, "reason": "command_not_found"}
        else:
            result = {"schema": f"{SCHEMA}.render_smoke", **smoke_render(item)}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
