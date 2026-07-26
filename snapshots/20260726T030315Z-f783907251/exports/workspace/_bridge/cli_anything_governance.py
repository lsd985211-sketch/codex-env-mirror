#!/usr/bin/env python3
"""Govern local CLI-Anything / cli-hub integration.

This wrapper is intentionally conservative. CLI-Anything is trusted as a
project, but installing individual harnesses still executes package-manager
commands and may touch external software. Default commands are read-only and
disable cli-hub analytics for Codex-managed invocations.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = Path.home() / ".codex" / "skills" / "cli-anything"
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
REQUIRED_LOCAL_HARNESSES = {"cli-anything-microsoft-office"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME") or "microsoft" in platform.release().lower():
        return "wsl"
    return sys.platform


def windows_office_runtime_available() -> bool:
    return runtime_platform() == "windows"


def platform_issue(severity: str, *, code: str, message: str, **extra: Any) -> dict[str, Any]:
    if not windows_office_runtime_available() and severity in {"blocker", "risk"}:
        return {
            "severity": "advisory",
            "code": f"{code}_platform_deferred",
            "message": message,
            "original_severity": severity,
            "platform_scope": runtime_platform(),
            "activation_rule": "Run the Office harness validator on a Windows-native owner path when an Office task is actually requested.",
            **extra,
        }
    return {"severity": severity, "code": code, "message": message, **extra}


def cli_hub_command() -> str | None:
    return shutil.which("cli-hub")


def run_cli_hub(args: list[str], *, expect_json: bool = False, timeout: int = 120) -> dict[str, Any]:
    command = cli_hub_command()
    if not command:
        return {
            "ok": False,
            "reason": "cli_hub_not_found",
            "hint": "Install with: python -m pip install cli-anything-hub",
        }
    env = os.environ.copy()
    env.setdefault("CLI_HUB_NO_ANALYTICS", "1")
    try:
        proc = subprocess.run(
            [command, *args],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **NO_WINDOW_KW,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "timeout",
            "args": args,
            "stdout_preview": (exc.stdout or "")[:2000] if isinstance(exc.stdout, str) else "",
            "stderr_preview": (exc.stderr or "")[:2000] if isinstance(exc.stderr, str) else "",
        }

    stdout = (proc.stdout or "").strip()
    payload: Any = stdout
    parse_error = ""
    if expect_json:
        try:
            payload = json.loads(stdout) if stdout else None
        except json.JSONDecodeError as exc:
            parse_error = str(exc)
    return {
        "ok": proc.returncode == 0 and not parse_error,
        "returncode": proc.returncode,
        "args": args,
        "json": payload if expect_json and not parse_error else None,
        "stdout": stdout[:6000] if not expect_json or parse_error else "",
        "stderr": (proc.stderr or "").strip()[:2000],
        "parse_error": parse_error,
        "analytics_disabled": env.get("CLI_HUB_NO_ANALYTICS") in {"1", "true", "yes"},
    }


def run_entrypoint(entrypoint: str, args: list[str], *, timeout: int = 30) -> dict[str, Any]:
    command = shutil.which(entrypoint)
    if not command:
        return {
            "ok": False,
            "reason": "entrypoint_not_found",
            "entrypoint": entrypoint,
            "hint": f"Command is not on PATH: {entrypoint}",
        }
    env = os.environ.copy()
    env.setdefault("CLI_HUB_NO_ANALYTICS", "1")
    try:
        proc = subprocess.run(
            [command, *args],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            **NO_WINDOW_KW,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "timeout",
            "entrypoint": entrypoint,
            "args": args,
            "stdout_preview": (exc.stdout or "")[:2000] if isinstance(exc.stdout, str) else "",
            "stderr_preview": (exc.stderr or "")[:2000] if isinstance(exc.stderr, str) else "",
        }
    return {
        "ok": proc.returncode == 0,
        "entrypoint": entrypoint,
        "command": command,
        "args": args,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:12000],
        "stderr": (proc.stderr or "").strip()[:4000],
    }


def parse_click_help(text: str) -> dict[str, Any]:
    commands: list[dict[str, str]] = []
    in_commands = False
    for line in str(text or "").splitlines():
        if line.strip() == "Commands:":
            in_commands = True
            continue
        if not in_commands:
            continue
        if not line.strip():
            continue
        match = re.match(r"^\s{2,}([A-Za-z0-9_-]+)(?:\s{2,}(.*))?$", line)
        if match:
            commands.append({"name": match.group(1), "description": (match.group(2) or "").strip()})
    usage = ""
    for line in str(text or "").splitlines():
        if line.startswith("Usage:"):
            usage = line.strip()
            break
    return {"usage": usage, "commands": commands}


def installed_harnesses() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for script in importlib.metadata.entry_points(group="console_scripts"):
        name = str(script.name)
        if not name.startswith("cli-anything-"):
            continue
        if name in seen:
            continue
        seen.add(name)
        items.append(
            {
                "entrypoint": name,
                "module": str(script.value),
                "path": shutil.which(name),
            }
        )
    items.sort(key=lambda item: item["entrypoint"])
    return {
        "schema": "cli_anything.installed_harnesses.v1",
        "ok": True,
        "generated_at": now_iso(),
        "count": len(items),
        "items": items,
        "dry_run_contract": {
            "runs_help_only": False,
            "installs_harnesses": False,
            "runs_target_software": False,
            "writes_project_files": False,
        },
    }


def command_surface(entrypoint: str, *, depth: int = 2) -> dict[str, Any]:
    depth = max(0, min(int(depth), 3))
    root = run_entrypoint(entrypoint, ["--help"])
    surfaces: list[dict[str, Any]] = [
        {
            "path": [],
            "ok": root.get("ok"),
            "returncode": root.get("returncode"),
            "help": root.get("stdout"),
            "stderr": root.get("stderr"),
            "parsed": parse_click_help(str(root.get("stdout") or "")),
        }
    ]
    if root.get("ok") and depth >= 1:
        for command in surfaces[0]["parsed"].get("commands", []):
            name = command.get("name")
            if not name:
                continue
            child = run_entrypoint(entrypoint, [name, "--help"])
            child_surface = {
                "path": [name],
                "ok": child.get("ok"),
                "returncode": child.get("returncode"),
                "help": child.get("stdout"),
                "stderr": child.get("stderr"),
                "parsed": parse_click_help(str(child.get("stdout") or "")),
            }
            surfaces.append(child_surface)
            if depth >= 2 and child.get("ok"):
                for sub in child_surface["parsed"].get("commands", []):
                    sub_name = sub.get("name")
                    if not sub_name:
                        continue
                    grandchild = run_entrypoint(entrypoint, [name, sub_name, "--help"])
                    surfaces.append(
                        {
                            "path": [name, sub_name],
                            "ok": grandchild.get("ok"),
                            "returncode": grandchild.get("returncode"),
                            "help": grandchild.get("stdout"),
                            "stderr": grandchild.get("stderr"),
                            "parsed": parse_click_help(str(grandchild.get("stdout") or "")),
                        }
                    )
    return {
        "schema": "cli_anything.command_surface.v1",
        "ok": bool(root.get("ok")),
        "generated_at": now_iso(),
        "entrypoint": entrypoint,
        "command_path": root.get("command"),
        "depth": depth,
        "surfaces": surfaces,
        "dry_run_contract": {
            "runs_help_only": True,
            "installs_harnesses": False,
            "runs_target_software": False,
            "writes_project_files": False,
        },
    }


def skill_state() -> dict[str, Any]:
    required = [
        "SKILL.md",
        "references/HARNESS.md",
        "references/commands/cli-anything.md",
        "references/commands/refine.md",
        "references/commands/test.md",
        "references/commands/validate.md",
        "references/commands/list.md",
        "references/docs/PREVIEW_PROTOCOL.md",
        "scripts/repl_skin.py",
        "scripts/preview_bundle.py",
        "scripts/skill_generator.py",
    ]
    files = {item: (SKILL_DIR / item).is_file() for item in required}
    return {
        "installed": SKILL_DIR.is_dir(),
        "path": str(SKILL_DIR),
        "required_files": files,
        "missing": [path for path, exists in files.items() if not exists],
    }


def snapshot() -> dict[str, Any]:
    version = run_cli_hub(["--version"])
    listed = run_cli_hub(["list", "--json"], expect_json=True)
    matrices = run_cli_hub(["matrix", "list", "--json"], expect_json=True)
    cli_items = listed.get("json") if isinstance(listed.get("json"), list) else []
    matrix_items = matrices.get("json") if isinstance(matrices.get("json"), list) else []
    local_harnesses = installed_harnesses()
    return {
        "schema": "cli_anything.snapshot.v1",
        "ok": bool(version.get("ok") and listed.get("ok") and matrices.get("ok")),
        "generated_at": now_iso(),
        "cli_hub": {
            "command": cli_hub_command(),
            "version": version.get("stdout"),
            "analytics_disabled_for_wrapper": True,
            "list_ok": listed.get("ok"),
            "matrix_list_ok": matrices.get("ok"),
        },
        "catalog": {
            "cli_count": len(cli_items),
            "matrix_count": len(matrix_items),
            "sample_clis": [
                {
                    "name": item.get("name"),
                    "category": item.get("category"),
                    "entry_point": item.get("entry_point"),
                }
                for item in cli_items[:10]
                if isinstance(item, dict)
            ],
            "sample_matrices": [
                {
                    "name": item.get("name"),
                    "category": item.get("category"),
                }
                for item in matrix_items[:10]
                if isinstance(item, dict)
            ],
        },
        "skill": skill_state(),
        "local_harnesses": local_harnesses,
        "dry_run_contract": {
            "installs_harnesses": False,
            "runs_target_software": False,
            "writes_project_files": False,
            "changes_mcp_config": False,
        },
    }


def doctor(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    issues: list[dict[str, Any]] = []
    if not snap.get("cli_hub", {}).get("command"):
        issues.append(platform_issue("blocker", code="cli_hub_missing", message="cli-hub executable is not on PATH"))
    if not snap.get("cli_hub", {}).get("list_ok"):
        issues.append(platform_issue("risk", code="catalog_unavailable", message="cli-hub list --json failed"))
    if not snap.get("cli_hub", {}).get("matrix_list_ok"):
        issues.append({"severity": "advisory", "code": "matrix_catalog_unavailable", "message": "cli-hub matrix list --json failed"})
    skill = snap.get("skill") if isinstance(snap.get("skill"), dict) else {}
    if not skill.get("installed"):
        issues.append({"severity": "risk", "code": "codex_skill_missing", "message": "Codex cli-anything skill is not installed"})
    elif skill.get("missing"):
        issues.append({"severity": "risk", "code": "codex_skill_incomplete", "missing": skill.get("missing")})
    if not snap.get("cli_hub", {}).get("analytics_disabled_for_wrapper"):
        issues.append({"severity": "advisory", "code": "analytics_not_disabled", "message": "Wrapper should set CLI_HUB_NO_ANALYTICS=1"})
    installed_names = {
        str(item.get("entrypoint") or "")
        for item in snap.get("local_harnesses", {}).get("items", [])
        if isinstance(item, dict)
    }
    for name in sorted(REQUIRED_LOCAL_HARNESSES - installed_names):
        issues.append(platform_issue("risk", code="required_local_harness_missing", message="Required local harness is not installed", entrypoint=name))
    return {
        "schema": "cli_anything.doctor.v1",
        "ok": not any(item.get("severity") in {"blocker", "risk"} for item in issues),
        "generated_at": now_iso(),
        "platform_scope": runtime_platform(),
        "windows_office_runtime_available": windows_office_runtime_available(),
        "issues": issues,
        "summary": {
            "cli_count": snap.get("catalog", {}).get("cli_count"),
            "matrix_count": snap.get("catalog", {}).get("matrix_count"),
            "skill_installed": skill.get("installed"),
        },
        "policy": {
            "trusted_project": True,
            "default_mode": "read_only_discovery_and_planning",
            "install_boundary": "Installing a concrete harness still requires explicit task intent and validation because install commands can execute package-manager code.",
        },
    }


def validate(snap: dict[str, Any] | None = None, *, require_office: bool = False) -> dict[str, Any]:
    snap = snap or snapshot()
    doc = doctor(snap)
    failures = [item for item in doc.get("issues", []) if item.get("severity") in {"blocker", "risk"}]
    office_surface = command_surface("cli-anything-microsoft-office", depth=2)
    office_paths = {tuple(item.get("path") or []) for item in office_surface.get("surfaces", []) if isinstance(item, dict)}
    required_paths = {
        (app, command)
        for app in ("word", "excel", "powerpoint")
        for command in ("inspect", "edit", "operations", "export-pdf")
    }
    missing_paths = sorted(required_paths - office_paths)
    if not office_surface.get("ok") or missing_paths:
        issue = {
            "severity": "risk" if require_office or windows_office_runtime_available() else "advisory",
            "code": "office_harness_command_surface_invalid" if require_office or windows_office_runtime_available() else "office_harness_command_surface_platform_deferred",
            "missing_paths": missing_paths,
            "platform_scope": runtime_platform(),
            "require_office": require_office,
        }
        if issue["severity"] in {"blocker", "risk"}:
            failures.append(issue)
    return {
        "schema": "cli_anything.validate.v1",
        "ok": not failures,
        "generated_at": now_iso(),
        "snapshot_ok": bool(snap.get("ok")),
        "platform_scope": runtime_platform(),
        "windows_office_runtime_available": windows_office_runtime_available(),
        "require_office": require_office,
        "failures": failures,
        "advisory_count": sum(1 for item in doc.get("issues", []) if item.get("severity") == "advisory"),
        "office_harness": {"ok": office_surface.get("ok"), "missing_paths": missing_paths, "deferred": bool(missing_paths and not require_office and not windows_office_runtime_available())},
    }


def metrics(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    snap = snap or snapshot()
    return {
        "schema": "cli_anything.metrics.v1",
        "ok": bool(snap.get("ok")),
        "generated_at": now_iso(),
        "cli_count": snap.get("catalog", {}).get("cli_count"),
        "matrix_count": snap.get("catalog", {}).get("matrix_count"),
        "skill_installed": snap.get("skill", {}).get("installed"),
        "skill_missing_file_count": len(snap.get("skill", {}).get("missing") or []),
        "local_harness_count": snap.get("local_harnesses", {}).get("count", 0),
    }


def search(query: str) -> dict[str, Any]:
    result = run_cli_hub(["search", query, "--json"], expect_json=True)
    items = result.get("json") if isinstance(result.get("json"), list) else []
    return {
        "schema": "cli_anything.search.v1",
        "ok": bool(result.get("ok")),
        "generated_at": now_iso(),
        "query": query,
        "count": len(items),
        "items": items[:20],
        "stderr": result.get("stderr"),
    }


def info(name: str) -> dict[str, Any]:
    result = run_cli_hub(["info", name])
    return {
        "schema": "cli_anything.info.v1",
        "ok": bool(result.get("ok")),
        "generated_at": now_iso(),
        "name": name,
        "text": result.get("stdout"),
        "stderr": result.get("stderr"),
        "note": "cli-hub info is currently human-readable text, not structured JSON.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CLI-Anything integration governance")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "doctor", "metrics"):
        sub.add_parser(command)
    p_validate = sub.add_parser("validate")
    p_validate.add_argument("--require-office", action="store_true")
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_info = sub.add_parser("info")
    p_info.add_argument("name")
    sub.add_parser("installed")
    p_commands = sub.add_parser("commands")
    p_commands.add_argument("entrypoint")
    p_commands.add_argument("--depth", type=int, default=2)
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "validate":
        payload = validate(require_office=args.require_office)
    elif args.command == "metrics":
        payload = metrics()
    elif args.command == "search":
        payload = search(args.query)
    elif args.command == "info":
        payload = info(args.name)
    elif args.command == "installed":
        payload = installed_harnesses()
    else:
        payload = command_surface(args.entrypoint, depth=args.depth)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
