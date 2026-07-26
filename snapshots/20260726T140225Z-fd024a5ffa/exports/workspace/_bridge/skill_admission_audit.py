#!/usr/bin/env python3
"""Audit one discovered skill and update the soft-admission registry."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(r"C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager")
CODEX_HOME = Path(r"C:\Users\45543\.codex")
REGISTRY_DIR = WORKSPACE_ROOT / "_bridge" / "shared" / "skill-system"
REGISTRY_PATH = REGISTRY_DIR / "registry.json"
REPORTS_DIR = REGISTRY_DIR / "reports"

GLOBAL_FRAMEWORK_DIR = CODEX_HOME / "skills" / "global-framework"
SKILL_CREATOR_SCRIPTS_DIR = CODEX_HOME / "skills" / ".system" / "skill-creator" / "scripts"

CHECK_ENCODING = GLOBAL_FRAMEWORK_DIR / "scripts" / "check_encoding.py"
QUICK_VALIDATE = SKILL_CREATOR_SCRIPTS_DIR / "quick_validate.py"
CHECK_CONTRACTS = GLOBAL_FRAMEWORK_DIR / "scripts" / "check_skill_contracts.py"
CHECK_OVERLAPS = GLOBAL_FRAMEWORK_DIR / "scripts" / "check_skill_overlaps.py"

LAYER_CHOICES = {
    "routing",
    "execution",
    "constraint-method",
    "evolution-governance",
    "domain-project",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def save_registry(registry: dict[str, Any]) -> None:
    registry["generated_at"] = now_iso()
    REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def run_json_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    payload: dict[str, Any] = {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0,
    }
    if completed.stdout.strip():
        try:
            payload["parsed"] = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload["parsed"] = None
    else:
        payload["parsed"] = None
    return payload


def normalize_path_key(path: str | Path) -> str:
    return str(Path(path).resolve()).lower()


def resolve_registry_entry(registry: dict[str, Any], skill_id: str | None, skill_path: str | None) -> dict[str, Any]:
    skills = registry.get("skills", [])
    if skill_id:
        for row in skills:
            if row["skill_id"] == skill_id:
                return row
        raise SystemExit(f"Skill not found by skill_id: {skill_id}")

    if skill_path:
        wanted = normalize_path_key(skill_path)
        for row in skills:
            if normalize_path_key(row["path"]) == wanted:
                return row
        raise SystemExit(f"Skill not found by path: {skill_path}")

    raise SystemExit("Provide either --skill-id or --path")


def choose_framework_mode(source: str, declared_layer: str | None) -> str:
    if declared_layer in LAYER_CHOICES:
        return "require"
    if source == "user-managed":
        return "auto"
    return "off"


def run_audit_steps(entry: dict[str, Any]) -> dict[str, Any]:
    skill_dir = Path(entry["path"])
    skill_name = entry["name"]
    declared_layer = entry.get("declared_primary_layer")
    framework_mode = choose_framework_mode(entry["source"], declared_layer)

    report: dict[str, Any] = {
        "skill_id": entry["skill_id"],
        "name": skill_name,
        "path": str(skill_dir),
        "source": entry["source"],
        "current_state": entry["state"],
        "detected_change_level": entry.get("last_change_level", "none"),
        "declared_primary_layer": declared_layer,
        "suggested_primary_layer": declared_layer,
        "framework_mode": framework_mode,
        "audited_at": now_iso(),
    }

    report["encoding_result"] = run_json_command([sys.executable, str(CHECK_ENCODING), "--json", str(skill_dir)])
    quick_validate_args = [sys.executable, str(QUICK_VALIDATE), str(skill_dir)]
    report["gate_result"] = run_json_command(quick_validate_args)
    report["gate_result"]["framework_mode"] = framework_mode
    report["gate_result"]["contract_note"] = (
        "Current system quick_validate.py validates the skill directory only; "
        "framework layer checks remain owned by check_skill_contracts.py."
    )

    if declared_layer in LAYER_CHOICES:
        report["contract_result"] = run_json_command(
            [
                sys.executable,
                str(CHECK_CONTRACTS),
                "--root",
                str(skill_dir.parent),
                "--spec",
                f"{skill_name}={declared_layer}",
                "--json",
            ]
        )
    else:
        report["contract_result"] = {
            "command": [],
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "ok": False,
            "parsed": None,
            "skipped": True,
            "reason": "declared_primary_layer missing or unsupported",
        }

    report["overlap_result"] = run_json_command([sys.executable, str(CHECK_OVERLAPS), "--json", str(skill_dir)])
    return report


def classify_risk(report: dict[str, Any]) -> tuple[str, str, str]:
    gate_ok = report["gate_result"]["ok"]
    encoding_ok = report["encoding_result"]["ok"]
    contract_result = report["contract_result"]
    contract_ok = contract_result.get("ok", False) or contract_result.get("skipped", False)
    overlaps = report["overlap_result"].get("parsed") or {}
    has_overlap = bool(overlaps)
    declared_layer = report.get("declared_primary_layer")

    if not encoding_ok or not gate_ok:
        return "high", "revise-before-approve", "Encoding or gate validation failed."
    if not declared_layer:
        return "medium", "approve-with-warning", "Core validation passed, but framework layer is not declared."
    if not contract_ok:
        return "high", "revise-before-approve", "Framework contract audit found structural issues."
    if has_overlap:
        return "medium", "approve-with-warning", "Validation passed, but overlap hints need manual boundary review."
    return "low", "approve", "Validation passed with no structural or overlap concerns detected."


def write_report(report: dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{report['skill_id']}-{report['audited_at'].replace(':', '').replace('+', '_')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def update_registry_entry(registry: dict[str, Any], report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    for row in registry.get("skills", []):
        if row["skill_id"] != report["skill_id"]:
            continue
        row["last_audited_at"] = report["audited_at"]
        row["last_report_path"] = str(report_path)
        row["last_approved_at"] = None
        row["state"] = "audited"
        if report["recommended_action"] in {"approve", "approve-with-warning"}:
            row["state"] = "approval-pending"
        return row
    raise SystemExit(f"Registry entry disappeared during audit: {report['skill_id']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit one skill from the admission registry.")
    parser.add_argument("--skill-id", help="Skill id from registry.json")
    parser.add_argument("--path", help="Absolute path to the skill directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    registry = load_registry()
    entry = resolve_registry_entry(registry, args.skill_id, args.path)
    report = run_audit_steps(entry)
    risk_level, recommended_action, summary = classify_risk(report)
    report["risk_level"] = risk_level
    report["recommended_action"] = recommended_action
    report["summary"] = summary

    report_path = write_report(report)
    updated_row = update_registry_entry(registry, report, report_path)
    save_registry(registry)

    payload = {
        "skill_id": report["skill_id"],
        "name": report["name"],
        "state": updated_row["state"],
        "risk_level": risk_level,
        "recommended_action": recommended_action,
        "report_path": str(report_path),
        "summary": summary,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
