#!/usr/bin/env python3
"""Read-only script quality checks for low-noise workspace scripts.

This tool uses script_inventory.py as its source of truth, then runs lightweight
syntax/static checks without modifying the checked scripts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import script_inventory


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATEGORIES = {"active", "helper"}
MAX_MESSAGE_CHARS = 1600
LOCAL_POWERSHELL_MODULES = ROOT / "_tools" / "powershell_modules"
SYSTEM_NODE = Path(r"C:\Program Files\nodejs\node.exe")


@dataclass(frozen=True)
class CheckResult:
    path: str
    category: str
    check: str
    status: str
    message: str
    exit_code: int | None = None


def shorten(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "... [truncated]"


def command_env(extra: dict[str, str] | None = None) -> dict[str, str] | None:
    if not extra and not LOCAL_POWERSHELL_MODULES.exists():
        return None
    env = dict(**__import__("os").environ)
    if LOCAL_POWERSHELL_MODULES.exists():
        current = env.get("PSModulePath", "")
        env["PSModulePath"] = str(LOCAL_POWERSHELL_MODULES) + (";" + current if current else "")
    if extra:
        env.update(extra)
    return env


def run_command(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=command_env(),
        timeout=timeout,
    )


def check_python(item: script_inventory.ScriptItem) -> CheckResult:
    path = ROOT / item.path
    proc = run_command([sys.executable, "-m", "py_compile", str(path)])
    if proc.returncode == 0:
        return CheckResult(item.path, item.category, "python-py_compile", "ok", "", proc.returncode)
    return CheckResult(
        item.path,
        item.category,
        "python-py_compile",
        "failed",
        shorten(proc.stderr or proc.stdout),
        proc.returncode,
    )


def check_node(item: script_inventory.ScriptItem) -> CheckResult:
    node = str(SYSTEM_NODE) if SYSTEM_NODE.exists() else shutil.which("node")
    if not node:
        return CheckResult(item.path, item.category, "node-check", "skipped", "node executable not found")
    path = ROOT / item.path
    proc = run_command([node, "--check", str(path)])
    if proc.returncode == 0:
        return CheckResult(item.path, item.category, "node-check", "ok", "", proc.returncode)
    return CheckResult(
        item.path,
        item.category,
        "node-check",
        "failed",
        shorten(proc.stderr or proc.stdout),
        proc.returncode,
    )


def powershell_exe() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


def check_powershell_syntax(item: script_inventory.ScriptItem) -> CheckResult:
    ps = powershell_exe()
    if not ps:
        return CheckResult(item.path, item.category, "powershell-syntax", "skipped", "powershell executable not found")
    path = str(ROOT / item.path)
    command = (
        "$ErrorActionPreference='Stop'; "
        "$tokens=$null; $errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile({json.dumps(path)}, [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { $errors | Select-Object Message,Extent | ConvertTo-Json -Depth 3; exit 1 }"
    )
    proc = run_command([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command])
    if proc.returncode == 0:
        return CheckResult(item.path, item.category, "powershell-syntax", "ok", "", proc.returncode)
    return CheckResult(
        item.path,
        item.category,
        "powershell-syntax",
        "failed",
        shorten(proc.stderr or proc.stdout),
        proc.returncode,
    )


def check_psscriptanalyzer_available() -> bool:
    ps = powershell_exe()
    if not ps:
        return False
    proc = run_command(
        [
            ps,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "if (Get-Command Invoke-ScriptAnalyzer -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }",
        ],
        timeout=15,
    )
    return proc.returncode == 0


def check_psscriptanalyzer(item: script_inventory.ScriptItem) -> CheckResult:
    ps = powershell_exe()
    if not ps:
        return CheckResult(item.path, item.category, "psscriptanalyzer", "skipped", "powershell executable not found")
    path = str(ROOT / item.path)
    command = (
        "$ErrorActionPreference='Stop'; "
        f"$r=Invoke-ScriptAnalyzer -Path {json.dumps(path)}; "
        "if ($r) { $r | Select-Object RuleName,Severity,Line,Column,Message | ConvertTo-Json -Depth 4; "
        "if ($r | Where-Object { $_.Severity -eq 'Error' }) { exit 1 } else { exit 2 } }"
    )
    proc = run_command([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command], timeout=60)
    if proc.returncode == 0:
        return CheckResult(item.path, item.category, "psscriptanalyzer", "ok", "", proc.returncode)
    if proc.returncode == 2:
        return CheckResult(
            item.path,
            item.category,
            "psscriptanalyzer",
            "warning",
            shorten(proc.stderr or proc.stdout),
            proc.returncode,
        )
    return CheckResult(
        item.path,
        item.category,
        "psscriptanalyzer",
        "failed",
        shorten(proc.stderr or proc.stdout),
        proc.returncode,
    )


def selected_items(args: argparse.Namespace) -> list[script_inventory.ScriptItem]:
    categories = set(args.category or DEFAULT_CATEGORIES)
    items = script_inventory.build_inventory(args.root or script_inventory.DEFAULT_ROOTS, args.include_history)
    return [item for item in items if item.category in categories]


def run_checks(items: Iterable[script_inventory.ScriptItem], run_pssa: bool) -> list[CheckResult]:
    results: list[CheckResult] = []
    pssa_available = check_psscriptanalyzer_available() if run_pssa else False
    for item in items:
        suffix = Path(item.path).suffix.lower()
        if suffix == ".py":
            results.append(check_python(item))
        elif suffix in {".js", ".mjs"}:
            results.append(check_node(item))
        elif suffix == ".ps1":
            results.append(check_powershell_syntax(item))
            if run_pssa:
                if pssa_available:
                    results.append(check_psscriptanalyzer(item))
                else:
                    results.append(
                        CheckResult(
                            item.path,
                            item.category,
                            "psscriptanalyzer",
                            "skipped",
                            "Invoke-ScriptAnalyzer is not installed or not on PSModulePath",
                        )
                    )
        else:
            results.append(CheckResult(item.path, item.category, "unsupported", "skipped", f"suffix {suffix} not checked"))
    return results


def render_human(results: list[CheckResult], items_count: int, args: argparse.Namespace) -> str:
    status_counts = Counter(result.status for result in results)
    check_counts = Counter(result.check for result in results)
    lines = [
        "Script quality check",
        f"root: {ROOT}",
        f"items_checked: {items_count}",
        f"checks_run: {len(results)}",
        f"include_history: {str(args.include_history).lower()}",
        f"categories: {', '.join(args.category or sorted(DEFAULT_CATEGORIES))}",
        f"psscriptanalyzer_requested: {str(args.psscriptanalyzer).lower()}",
        "",
        "Status counts:",
    ]
    for status in ("ok", "failed", "skipped"):
        lines.append(f"- {status}: {status_counts.get(status, 0)}")
    lines.append(f"- warning: {status_counts.get('warning', 0)}")
    lines.append("")
    lines.append("Check counts:")
    for check, count in sorted(check_counts.items()):
        lines.append(f"- {check}: {count}")

    grouped: dict[str, list[CheckResult]] = defaultdict(list)
    for result in results:
        grouped[result.status].append(result)

    for status in ("failed", "warning", "skipped"):
        if not grouped.get(status):
            continue
        lines.extend(["", f"{status}:"])
        for result in grouped[status][:12]:
            message = f" - {result.message}" if result.message else ""
            lines.append(f"- {result.path} [{result.check}]{message}")
        omitted = len(grouped[status]) - 12
        if omitted > 0:
            lines.append(f"- ... {omitted} more omitted; use --json for full structured output")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only script quality checks")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--include-history", action="store_true", help="Include inventory history/noisy trees")
    parser.add_argument("--root", action="append", help="Inventory root; repeatable")
    parser.add_argument(
        "--category",
        action="append",
        choices=["active", "helper", "legacy", "dependency", "backup"],
        help="Category to check; repeatable. Defaults to active+helper.",
    )
    parser.add_argument(
        "--psscriptanalyzer",
        action="store_true",
        help="Run PSScriptAnalyzer in addition to PowerShell syntax checks when available",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    items = selected_items(args)
    results = run_checks(items, args.psscriptanalyzer)
    payload = {
        "ok": not any(result.status == "failed" for result in results),
        "workspace": str(ROOT),
        "include_history": bool(args.include_history),
        "categories": args.category or sorted(DEFAULT_CATEGORIES),
        "items_checked": len(items),
        "checks_run": len(results),
        "counts": dict(Counter(result.status for result in results)),
        "results": [asdict(result) for result in results],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_human(results, len(items), args))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
