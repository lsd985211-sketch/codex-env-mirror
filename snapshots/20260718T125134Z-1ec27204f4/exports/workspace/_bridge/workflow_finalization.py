#!/usr/bin/env python3
"""Workflow finalization for startup baseline and project checkpoints.

Owns: bounded closeout-time finalization for verified Codex environment config
changes and major project changes.
Non-goals: deciding task correctness, writing long-term memory, editing skills,
or bypassing checkpoint/baseline owner tools.
State behavior: read-only by default; writes only when called with apply=True
and explicit finalization signals.
Normal callers: codex_workflow_entry closeout and validation smokes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.cli_contract import enum_arg
from rule_governance import impact as rule_impact
from rule_governance import validate as rule_validate
from system_membership import impact as membership_impact
from system_membership import validate as membership_validate


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
BASELINE_UPDATE = BRIDGE / "codex_baseline_update.py"
CHECKPOINT_FINALIZE = BRIDGE / "project_checkpoint_finalize.py"


FINALIZATION_OUTCOMES = {"ok", "partial", "failed", "blocked", "unknown"}
SUCCESS_OUTCOMES = {"ok", "partial"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_json(command: list[str], timeout: int = 120) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "command": command, "error": repr(exc)}
    payload: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "command": command,
        "returncode": proc.returncode,
        "stderr": (proc.stderr or "").strip()[:4000],
    }
    stdout = (proc.stdout or "").strip()
    if not stdout:
        return payload
    try:
        parsed = json.loads(stdout)
    except Exception:
        payload["stdout"] = stdout[:4000]
        return payload
    if isinstance(parsed, dict):
        parsed.setdefault("ok", proc.returncode == 0)
        parsed["_command"] = command
        parsed["_returncode"] = proc.returncode
        if payload["stderr"]:
            parsed["_stderr"] = payload["stderr"]
        return parsed
    payload["result"] = parsed
    return payload


def compact_items(items: list[str] | None, limit: int = 12) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        result.append(text)
        seen.add(key)
        if len(result) >= limit:
            break
    return result


def safe_project_id(task_kind: str, explicit: str = "") -> str:
    value = (explicit or task_kind or "codex-workflow").strip().lower()
    cleaned = []
    for ch in value:
        cleaned.append(ch if ch.isalnum() or ch in {"-", "_", "."} else "-")
    text = "".join(cleaned).strip("-._")
    return text or "codex-workflow"


def task_is_success(outcome: str) -> bool:
    return str(outcome or "").strip().lower() in SUCCESS_OUTCOMES


def owner_receipt_ok(receipts: list[str] | None, expected_owner: str) -> bool:
    normalized_expected = expected_owner.replace("-", "_").casefold()
    for item in receipts or []:
        text = str(item or "").strip()
        if not text:
            continue
        if text.casefold() == f"{normalized_expected}=ok":
            return True
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            owner = str(payload.get("owner") or payload.get("name") or "").replace("-", "_").casefold()
            if owner == normalized_expected and payload.get("ok") is True:
                return True
    return False


def membership_receipt_ok(receipts: list[str] | None) -> bool:
    return owner_receipt_ok(receipts, "system_membership")


def reconcile_rule_governance(
    changed_files: list[str] | None,
    validation_receipts: list[str] | None,
) -> dict[str, Any]:
    changed = compact_items(changed_files, limit=50)
    if not changed:
        return {
            "schema": "workflow_finalization.rule_reconciliation.v1",
            "ok": True,
            "required": False,
            "complete": True,
            "changed_files": [],
            "reason": "no_changed_files",
        }
    impact = rule_impact(changed)
    required = bool(impact.get("rule_change_required"))
    validation = rule_validate() if required else {"ok": True, "skipped": True, "reason": "no_rule_impact"}
    receipt_ok = owner_receipt_ok(validation_receipts, "rule_governance")
    complete = True if not required else bool(impact.get("ok")) and bool(validation.get("ok")) and receipt_ok
    return {
        "schema": "workflow_finalization.rule_reconciliation.v1",
        "ok": complete,
        "required": required,
        "complete": complete,
        "changed_files": changed,
        "affected": impact.get("affected", []),
        "unmatched": impact.get("unmatched", []),
        "owner_validation": validation,
        "required_receipt": "rule_governance=ok" if required else "",
        "receipt_ok": receipt_ok if required else True,
        "reason": "complete" if complete else "rule_governance_evidence_incomplete",
    }


def reconcile_system_membership(
    changed_files: list[str] | None,
    validation_receipts: list[str] | None,
) -> dict[str, Any]:
    changed = compact_items(changed_files, limit=50)
    if not changed:
        return {
            "schema": "workflow_finalization.membership_reconciliation.v1",
            "ok": True,
            "required": False,
            "complete": True,
            "changed_files": [],
            "reason": "no_changed_files",
        }
    impact = membership_impact(changed)
    required = bool(impact.get("contract_upgrade_required"))
    validation = membership_validate() if required else {"ok": True, "skipped": True, "reason": "no_membership_impact"}
    receipt_ok = membership_receipt_ok(validation_receipts)
    complete = bool(impact.get("ok")) and (not required or (bool(validation.get("ok")) and receipt_ok))
    return {
        "schema": "workflow_finalization.membership_reconciliation.v1",
        "ok": complete,
        "required": required,
        "complete": complete,
        "changed_files": changed,
        "affected_systems": impact.get("affected_systems", []),
        "affected_surfaces": impact.get("affected_surfaces", []),
        "required_next_commands": impact.get("required_next_commands", []),
        "impact": impact,
        "owner_validation": validation,
        "required_receipt": "system_membership=ok" if required else "",
        "receipt_ok": receipt_ok if required else True,
        "reason": "complete" if complete else "membership_evidence_incomplete",
    }


def baseline_check_command(reason: str) -> list[str]:
    return [sys.executable, str(BASELINE_UPDATE), "--check-current", "--reason", reason]


def baseline_adopt_command(reason: str) -> list[str]:
    return [sys.executable, str(BASELINE_UPDATE), "--adopt-current", "--reason", reason]


def checkpoint_command(
    *,
    project_id: str,
    change_type: str,
    title: str,
    summary: str,
    changed_files: list[str],
    evidence: list[str],
    verification: list[str],
    backups: list[str],
    stable_conclusions: list[str],
    apply: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(CHECKPOINT_FINALIZE),
        "--project-id",
        project_id,
        "--change-type",
        change_type,
        "--title",
        title,
        "--summary",
        summary,
        "--json",
    ]
    for value in changed_files:
        command.extend(["--changed-file", value])
    for value in evidence:
        command.extend(["--evidence", value])
    for value in verification:
        command.extend(["--verification", value])
    for value in backups:
        command.extend(["--backup", value])
    for value in stable_conclusions:
        command.extend(["--stable-conclusion", value])
    if apply:
        command.append("--write")
    return command


def finalize(
    *,
    task_kind: str,
    outcome: str,
    config_changed: bool = False,
    major_change: bool = False,
    apply: bool = False,
    project_id: str = "",
    title: str = "",
    summary: str = "",
    changed_files: list[str] | None = None,
    evidence: list[str] | None = None,
    verification: list[str] | None = None,
    backups: list[str] | None = None,
    stable_conclusions: list[str] | None = None,
    validation_receipts: list[str] | None = None,
) -> dict[str, Any]:
    success = task_is_success(outcome)
    reason = summary or title or f"{task_kind} verified closeout"
    actions: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []
    if (config_changed or major_change) and not success:
        blocked_reasons.append("task_outcome_not_successful")
    changed = compact_items(changed_files, limit=50)
    membership = reconcile_system_membership(changed, validation_receipts)
    rules = reconcile_rule_governance(changed, validation_receipts)
    if major_change and success and not changed:
        blocked_reasons.append("changed_files_required_for_major_change")
    if success and membership.get("required") and not membership.get("complete"):
        blocked_reasons.append("system_membership_reconciliation_incomplete")
    if success and rules.get("required") and not rules.get("complete"):
        blocked_reasons.append("rule_governance_reconciliation_incomplete")
    blocked_reason = ";".join(dict.fromkeys(blocked_reasons))

    baseline: dict[str, Any] = {
        "needed": bool(config_changed),
        "applied": False,
        "check": {},
        "adopt": {},
        "rule": "When a Codex working-environment config change is intentional and verified, adopt current config into the startup baseline after check-current.",
    }
    if config_changed and success and not blocked_reason:
        check = run_json(baseline_check_command(reason), timeout=90)
        baseline["check"] = check
        stale = bool(check.get("baseline_stale"))
        if stale:
            actions.append({"kind": "startup_baseline_adopt", "mode": "apply" if apply else "plan"})
            if apply:
                adopt = run_json(baseline_adopt_command(reason), timeout=120)
                baseline["adopt"] = adopt
                baseline["applied"] = bool(adopt.get("ok"))
        else:
            baseline["applied"] = False
            baseline["reason"] = "baseline_already_current"

    checkpoint: dict[str, Any] = {
        "needed": bool(major_change),
        "applied": False,
        "result": {},
        "rule": "After a verified major project change, write a project checkpoint automatically when apply is enabled.",
    }
    if major_change and success and not blocked_reason:
        project = safe_project_id(task_kind, project_id)
        effective_title = title or f"{project} verified closeout"
        effective_summary = summary or f"Verified major change completed for {project}."
        command = checkpoint_command(
            project_id=project,
            change_type="maintenance",
            title=effective_title,
            summary=effective_summary,
            changed_files=changed,
            evidence=compact_items(evidence),
            verification=compact_items(verification),
            backups=compact_items(backups),
            stable_conclusions=compact_items(stable_conclusions) or [effective_summary],
            apply=apply,
        )
        actions.append({"kind": "project_checkpoint", "mode": "apply" if apply else "plan", "project_id": project})
        result = run_json(command, timeout=90)
        checkpoint["result"] = result
        checkpoint["applied"] = bool(apply and result.get("ok") and result.get("written"))

    return {
        "schema": "workflow_finalization.v1",
        "ok": not blocked_reason,
        "generated_at": now_iso(),
        "apply": bool(apply),
        "task_kind": task_kind,
        "outcome": outcome,
        "success": success,
        "signals": {
            "config_changed": bool(config_changed),
            "major_change": bool(major_change),
        },
        "blocked_reason": blocked_reason,
        "actions": actions,
        "startup_baseline": baseline,
        "project_checkpoint": checkpoint,
        "membership_reconciliation": membership,
        "rule_reconciliation": rules,
        "policy": {
            "no_signal_no_write": True,
            "writes_require_successful_outcome": True,
            "startup_baseline_owner": str(BASELINE_UPDATE),
            "project_checkpoint_owner": str(CHECKPOINT_FINALIZE),
            "membership_owner": str(BRIDGE / "system_membership.py"),
            "membership_rule": "changed-file impact is a closeout enforcement gate, not advisory prose",
            "rule_governance_owner": str(BRIDGE / "rule_governance.py"),
            "rule_governance_rule": "rule-bearing changed files require authority/lifecycle validation and a machine-readable rule_governance receipt",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Finalize verified workflow changes into baselines/checkpoints.")
    parser.add_argument("--task-kind", default="general")
    parser.add_argument(
        "--outcome",
        default="unknown",
        type=enum_arg("finalization --outcome", FINALIZATION_OUTCOMES, prose_destination="--summary"),
        help="Machine status only: ok|partial|failed|blocked|unknown. Put prose in --summary.",
    )
    parser.add_argument("--config-changed", action="store_true")
    parser.add_argument("--major-change", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--verification", action="append", default=[])
    parser.add_argument("--backup", action="append", default=[])
    parser.add_argument("--stable-conclusion", action="append", default=[])
    parser.add_argument("--validation-receipt", action="append", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = finalize(
        task_kind=args.task_kind,
        outcome=args.outcome,
        config_changed=args.config_changed,
        major_change=args.major_change,
        apply=args.apply,
        project_id=args.project_id,
        title=args.title,
        summary=args.summary,
        changed_files=args.changed_file,
        evidence=args.evidence,
        verification=args.verification,
        backups=args.backup,
        stable_conclusions=args.stable_conclusion,
        validation_receipts=args.validation_receipt,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
