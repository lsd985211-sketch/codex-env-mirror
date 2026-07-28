#!/usr/bin/env python3
"""Unified finalization gate for project memory, baseline, and skill upkeep.

The finalizer is intentionally thin. It orchestrates existing tools instead of
reimplementing iteration review, checkpoint writing, or Codex baseline adoption.
Default mode is read-only; persistent writes require explicit flags.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
SHARED = BRIDGE / "shared"
RUNTIME = BRIDGE / "runtime" / "knowledge-finalizer"
ITERATION_REVIEW = BRIDGE / "iteration_layer_review.py"
CHECKPOINT_FINALIZE = BRIDGE / "project_checkpoint_finalize.py"
BASELINE_UPDATE = BRIDGE / "codex_baseline_update.py"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_json(args: list[str], timeout: int = 120) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "command": args, "error": repr(exc)}
    payload: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "command": args,
        "returncode": proc.returncode,
        "stderr": (proc.stderr or "").strip()[:4000],
    }
    stdout = (proc.stdout or "").strip()
    if stdout:
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                parsed.setdefault("ok", proc.returncode == 0)
                parsed["_command"] = args
                parsed["_returncode"] = proc.returncode
                if payload["stderr"]:
                    parsed["_stderr"] = payload["stderr"]
                return parsed
            payload["result"] = parsed
        except Exception:
            payload["stdout"] = stdout[:4000]
    return payload


def split_items(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        for part in value.split(";"):
            item = part.strip()
            if item:
                items.append(item)
    return items


def stable_conclusions_from_iteration(iteration: dict[str, Any]) -> list[str]:
    conclusions: list[str] = []
    summary = iteration.get("decision_summary")
    if isinstance(summary, dict):
        for key in ("summary_text", "primary_boundary", "primary_validation"):
            value = str(summary.get(key) or "").strip()
            if value:
                conclusions.append(value)
    for item in iteration.get("knowledge_promotion_suggestions") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("stable_conclusion") or item.get("summary") or item.get("text") or "").strip()
        if value:
            conclusions.append(value)
    unique: list[str] = []
    seen: set[str] = set()
    for item in conclusions:
        key = item.casefold()
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return unique[:8]


def run_iteration(args: argparse.Namespace) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ITERATION_REVIEW),
        "--json",
        "--recent-limit",
        str(args.recent_limit),
        "--run-validation",
        "--validation-profile",
        args.validation_profile,
    ]
    return run_json(command, timeout=args.validation_timeout)


def build_checkpoint_command(args: argparse.Namespace, iteration: dict[str, Any], write: bool) -> list[str]:
    title = args.title or f"{args.project_id} finalization"
    summary = args.summary
    if not summary:
        decision = iteration.get("decision_summary") if isinstance(iteration.get("decision_summary"), dict) else {}
        summary = str(decision.get("summary_text") or "Unified finalization completed for verified project work.")
    command = [
        sys.executable,
        str(CHECKPOINT_FINALIZE),
        "--project-id",
        args.project_id,
        "--change-type",
        args.change_type,
        "--title",
        title,
        "--summary",
        summary,
        "--json",
    ]
    for value in split_items(args.changed_file):
        command.extend(["--changed-file", value])
    for value in split_items(args.evidence):
        command.extend(["--evidence", value])
    for value in split_items(args.verification):
        command.extend(["--verification", value])
    for value in split_items(args.backup):
        command.extend(["--backup", value])
    stable = split_items(args.stable_conclusion) or stable_conclusions_from_iteration(iteration)
    for value in stable:
        command.extend(["--stable-conclusion", value])
    for value in split_items(args.followup):
        command.extend(["--followup", value])
    if write:
        command.append("--write")
    return command


def run_checkpoint(args: argparse.Namespace, iteration: dict[str, Any], write: bool) -> dict[str, Any]:
    return run_json(build_checkpoint_command(args, iteration, write), timeout=60)


def run_baseline(args: argparse.Namespace, apply: bool) -> dict[str, Any]:
    if not args.include_baseline:
        return {"ok": True, "skipped": True, "reason": "baseline_update_not_requested"}
    command = [sys.executable, str(BASELINE_UPDATE), "--reason", args.baseline_reason or args.summary or args.title or "verified finalization"]
    if apply:
        command.append("--adopt-current")
    return run_json(command, timeout=90)


def skills_plan() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "external_mcp_action_required",
        "reason": "MySkills is an MCP service exposed to the agent session, not a project-local script API.",
        "recommended_actions": [
            "Run myskills.skills_rescan after finalizer apply.",
            "If skills are drifted or broken, review align_plan before any align_apply.",
            "Only author or revise a skill after explicit human confirmation.",
        ],
    }


def memory_plan(checkpoint: dict[str, Any]) -> dict[str, Any]:
    suggestions = checkpoint.get("suggestions") if isinstance(checkpoint.get("suggestions"), dict) else {}
    return {
        "ok": True,
        "mode": "candidate_only",
        "reason": "PMB promotion requires deliberate agent-side review; the finalizer does not write global memory directly.",
        "pmb_memory": suggestions.get("pmb_memory") if isinstance(suggestions, dict) else {},
        "project_checkpoint": suggestions.get("project_checkpoint") if isinstance(suggestions, dict) else {},
    }


def write_run_record(payload: dict[str, Any]) -> Path:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = RUNTIME / f"{stamp}-finalizer.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    latest = RUNTIME / "latest.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Finalize project knowledge, baseline, and skill upkeep through one gate.")
    parser.add_argument("--project-id", default="mobile-openclaw-bridge")
    parser.add_argument("--change-type", default="maintenance")
    parser.add_argument("--title", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--changed-file", action="append")
    parser.add_argument("--evidence", action="append")
    parser.add_argument("--verification", action="append")
    parser.add_argument("--backup", action="append")
    parser.add_argument("--stable-conclusion", action="append")
    parser.add_argument("--followup", action="append")
    parser.add_argument("--recent-limit", type=int, default=12)
    parser.add_argument("--validation-profile", choices=["quick", "full"], default="quick")
    parser.add_argument("--validation-timeout", type=int, default=60)
    parser.add_argument("--include-baseline", action="store_true", help="Also run codex_baseline_update dry-run/apply.")
    parser.add_argument("--baseline-reason", default="")
    parser.add_argument("--apply", action="store_true", help="Write checkpoint and requested baseline update.")
    parser.add_argument("--save-run-record", action="store_true", help="Save finalizer output under _bridge/runtime/knowledge-finalizer.")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    iteration = run_iteration(args)
    checkpoint = run_checkpoint(args, iteration, write=bool(args.apply))
    baseline = run_baseline(args, apply=bool(args.apply))
    payload = {
        "ok": bool(iteration.get("ok")) and bool(checkpoint.get("ok")) and bool(baseline.get("ok")),
        "schema": "knowledge-finalizer/v1",
        "generated_at": now_iso(),
        "workspace": str(ROOT),
        "apply": bool(args.apply),
        "policy": {
            "default_mode": "dry-run",
            "checkpoint_writes_require_apply": True,
            "baseline_writes_require_apply_and_include_baseline": True,
            "memory_writes": "candidate_only",
            "skill_writes": "external_mcp_action_required",
        },
        "iteration": iteration,
        "checkpoint": checkpoint,
        "baseline": baseline,
        "memory": memory_plan(checkpoint),
        "skills": skills_plan(),
    }
    if args.save_run_record or args.apply:
        payload["run_record"] = str(write_run_record(payload))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        mode = "apply" if args.apply else "dry-run"
        print(f"knowledge_finalizer {mode}: ok={payload['ok']}")
        print(f"- checkpoint: ok={checkpoint.get('ok')} dry_run={checkpoint.get('dry_run')}")
        print(f"- baseline: ok={baseline.get('ok')} skipped={baseline.get('skipped', False)}")
        print("- memory: candidate_only")
        print("- skills: run myskills.skills_rescan from the agent session")
        if payload.get("run_record"):
            print(f"- run_record: {payload['run_record']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
