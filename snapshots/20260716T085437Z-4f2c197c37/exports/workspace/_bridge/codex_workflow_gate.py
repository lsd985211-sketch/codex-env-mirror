#!/usr/bin/env python3
"""Read-only gates for Codex memory preflight and iteration finalization.

This module does not write memories, skills, baselines, or project knowledge.
It answers whether a turn should load memory first and whether a completed turn
must surface a controlled iteration proposal for user approval.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from intent_routing import matched_terms


ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SYSTEM_KEYWORDS = (
    "桥接",
    "系统",
    "维护",
    "性能",
    "mcp",
    "工具",
    "插件",
    "记忆",
    "基线",
    "技能",
    "自动化",
    "定时",
    "邮箱",
    "权限",
    "配置",
    "defender",
    "codegraph",
    "pmb",
    "workflow",
    "iteration",
    "baseline",
    "skill",
    "memory",
)

ITERATION_TRIGGER_KEYWORDS = (
    "大变动",
    "框架",
    "根本原因",
    "持久化",
    "优化",
    "修复",
    "总结经验",
    "更新记忆",
    "更新基线",
    "更新技能",
    "技能",
    "基线",
    "记忆",
    "工具状态",
    "用户纠正",
    "proposal",
    "approval",
    "baseline",
    "skill",
    "memory",
)

EXTERNAL_KNOWLEDGE_TRIGGER_KEYWORDS = (
    "联网",
    "网页",
    "搜索",
    "查资料",
    "外部知识",
    "文档",
    "资料",
    "source",
    "citation",
    "web",
    "url",
    "docs",
    "research",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_json(command: list[str], timeout: int = 60) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "command": command,
            "stdout_preview": (exc.stdout or "")[:2000] if isinstance(exc.stdout, str) else "",
            "stderr_preview": (exc.stderr or "")[:2000] if isinstance(exc.stderr, str) else "",
        }
    raw = (proc.stdout or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {
            "ok": proc.returncode == 0,
            "parse_error": "stdout_not_json",
            "stdout_preview": raw[:2000],
            "stderr_preview": (proc.stderr or "")[:2000],
        }
    if isinstance(payload, dict):
        payload.setdefault("ok", proc.returncode == 0)
        payload["_returncode"] = proc.returncode
        payload["_command"] = command
        if proc.stderr:
            payload["_stderr_preview"] = proc.stderr[:2000]
        return payload
    return {"ok": proc.returncode == 0, "value": payload, "_returncode": proc.returncode, "_command": command}


def run_text(command: list[str], timeout: int = 60) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "command": command,
            "stdout": (exc.stdout or "")[:4000] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[:2000] if isinstance(exc.stderr, str) else "",
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "command": command,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    return matched_terms(text, keywords)


def memory_preflight(message: str = "", *, check_session: bool = False) -> dict[str, Any]:
    hits = keyword_hits(message, SYSTEM_KEYWORDS)
    should_prepare = bool(hits) or not str(message or "").strip()
    daemon = run_json([sys.executable, "_bridge/local_pmb_memory.py", "doctor"], timeout=90)
    session_smoke: dict[str, Any] | None = None
    if check_session:
        session_smoke = run_json(
            [
                sys.executable,
                "_bridge/mcp_session_doctor.py",
                "smoke",
                "--profile",
                "local-pmb-memory",
                "--timeout-seconds",
                "60",
            ],
            timeout=90,
        )
    blocker = ""
    if not daemon.get("ok"):
        blocker = "local_pmb_memory_doctor_failed"
    elif check_session and session_smoke is not None and not session_smoke.get("ok"):
        blocker = "local_pmb_memory_session_smoke_failed"
    memory_surface = daemon.get("snapshot", {}).get("memory_surface", {})
    user_profile = memory_surface.get("user_profile", {}) if isinstance(memory_surface, dict) else {}
    profile_guidance = user_profile.get("guidance", {}) if isinstance(user_profile, dict) else {}
    return {
        "schema": "codex-workflow-gate.memory_preflight.v1",
        "ok": not bool(blocker),
        "generated_at": now_iso(),
        "message_keyword_hits": hits,
        "should_prepare_memory": should_prepare,
        "required_action": (
            "Apply profile_guidance plus memory_manifest routing, then call local-pmb-memory prepare/recall before substantive work when available."
            if should_prepare
            else "Memory use optional for this turn."
        ),
        "profile_guidance": profile_guidance,
        "blocker": blocker,
        "daemon_ok": bool(daemon.get("ok")),
        "session_smoke_ok": None if session_smoke is None else bool(session_smoke.get("ok")),
        "daemon_summary": {
            "issues": daemon.get("issues", []),
            "pmb_daemon_running": daemon.get("snapshot", {}).get("pmb", {}).get("effective_daemon_running"),
            "pmb_daemon_warm": daemon.get("snapshot", {}).get("pmb", {}).get("effective_daemon_warm"),
            "memory_manifest_ok": memory_surface.get("manifest", {}).get("ok") if isinstance(memory_surface, dict) else None,
            "user_profile_ok": user_profile.get("ok") if isinstance(user_profile, dict) else None,
            "user_profile_guidance_count": profile_guidance.get("selected_fact_count") if isinstance(profile_guidance, dict) else 0,
        },
        "session_summary": None
        if session_smoke is None
        else {
            "reason": session_smoke.get("reason", ""),
            "missing_tools": session_smoke.get("missing_tools", []),
            "tool_names": session_smoke.get("tool_names", [])[:20],
        },
        "dry_run_contract": {
            "writes_files": False,
            "writes_memory": False,
            "starts_persistent_services": False,
            "requires_user_confirmation_for_updates": True,
        },
    }


def finalization_gate(message: str = "", *, include_approval_block: bool = False) -> dict[str, Any]:
    hits = keyword_hits(message, ITERATION_TRIGGER_KEYWORDS)
    external_hits = keyword_hits(message, EXTERNAL_KNOWLEDGE_TRIGGER_KEYWORDS)
    requires_iteration_review = bool(hits)
    work_notes = run_json([sys.executable, "_bridge/memory_governance.py", "work-note-read", "--limit", "100"], timeout=30)
    work_note_count = int(work_notes.get("active_count") or 0) if isinstance(work_notes, dict) else 0
    approval: dict[str, Any] | None = None
    if include_approval_block and requires_iteration_review:
        approval_text = run_text(
            [
                sys.executable,
                "_bridge/iteration_layer_review.py",
                "--approval-only",
                "--recent-limit",
                "8",
            ],
            timeout=90,
        )
        approval = {
            "ok": bool(approval_text.get("ok")),
            "stdout": approval_text.get("stdout", ""),
            "stderr": approval_text.get("stderr", ""),
            "returncode": approval_text.get("returncode"),
        }
    return {
        "schema": "codex-workflow-gate.finalization_gate.v1",
        "ok": True,
        "generated_at": now_iso(),
        "message_keyword_hits": hits,
        "requires_iteration_review": requires_iteration_review,
        "ephemeral_work_notes": {
            "active_count": work_note_count,
            "read_ok": bool(work_notes.get("ok")) if isinstance(work_notes, dict) else False,
            "entries": (work_notes.get("entries") or []) if isinstance(work_notes, dict) else [],
            "contract": {
                "codex_must_read_raw_entries": work_note_count > 0,
                "script_must_not_summarize_or_promote": True,
                "final_reply_should_state_each_note_disposition": work_note_count > 0,
                "valid_dispositions": [
                    "handled_now",
                    "proposal_or_next_task",
                    "deferred_with_reason",
                    "discarded_as_noise",
                ],
                "side_issues_wait_until_main_task_complete_unless_blocking": True,
                "clear_after_closeout": work_note_count > 0,
            },
            "commands": [
                "python _bridge\\memory_governance.py work-note-read --limit 100",
                "python _bridge\\memory_governance.py work-note-clear",
            ],
        },
        "external_knowledge_closeout": {
            "required": bool(external_hits),
            "keyword_hits": external_hits,
            "timing": "batch_at_research_or_work_closeout_not_per_page",
            "required_action": (
                "If web/external sources were used, keep only a compact candidate-source list during research, then batch run external_knowledge capture-decision for reusable candidates at closeout."
                if external_hits
                else "No external-knowledge closeout required by this heuristic."
            ),
            "commands": [
                "python _bridge\\external_knowledge.py capture-policy",
                "python _bridge\\external_knowledge.py capture-decision ...",
                "python _bridge\\external_knowledge.py distill-plan --limit 20",
            ],
            "rule": "Do not run capture-decision after every visited page; batch the few reusable candidates once.",
        },
        "required_action": (
            "Append the Chinese iteration approval block before final reply; read any ephemeral work notes raw, state each disposition, then clear them; do not promote changes without user approval."
            if requires_iteration_review or work_note_count > 0
            else "No iteration approval block or ephemeral work-note closeout required by this heuristic."
        ),
        "approval_block": approval,
        "dry_run_contract": {
            "writes_files": False,
            "writes_memory": False,
            "promotes_skills_or_baselines": False,
            "requires_user_confirmation_for_updates": True,
        },
    }


def snapshot() -> dict[str, Any]:
    return {
        "schema": "codex-workflow-gate.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "gates": {
            "memory_preflight": {
                "purpose": "Detect when PMB memory should be prepared and whether daemon/session exposure is healthy.",
                "actions": ["read-only PMB doctor", "optional MCP smoke"],
            },
            "finalization_gate": {
                "purpose": "Detect when a major/system change must surface an iteration approval proposal and when web knowledge needs batched closeout.",
                "actions": ["keyword heuristic", "optional approval-only iteration block", "batched external knowledge closeout reminder"],
            },
        },
        "dry_run_contract": {
            "writes_files": False,
            "writes_memory": False,
            "changes_codex_config": False,
        },
    }


def doctor() -> dict[str, Any]:
    memory = memory_preflight("系统 记忆 基线 技能", check_session=True)
    finalization = finalization_gate("大变动 更新记忆 更新基线 更新技能")
    issues: list[dict[str, Any]] = []
    if not memory.get("ok"):
        issues.append(
            {
                "severity": "blocker",
                "code": memory.get("blocker"),
                "summary": "PMB memory preflight cannot guarantee memory availability for system-level work.",
            }
        )
    guidance = memory.get("profile_guidance", {}) if isinstance(memory.get("profile_guidance"), dict) else {}
    if int(guidance.get("selected_fact_count") or 0) <= 0:
        issues.append(
            {
                "severity": "risk",
                "code": "user_profile_guidance_empty",
                "summary": "User profile is not being converted into actionable workflow guidance.",
            }
        )
    if not finalization.get("requires_iteration_review"):
        issues.append(
            {
                "severity": "risk",
                "code": "iteration_trigger_heuristic_failed",
                "summary": "Finalization gate did not detect a known trigger phrase.",
            }
        )
    return {
        "schema": "codex-workflow-gate.doctor.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "issues": issues,
        "memory_preflight": memory,
        "finalization_gate": finalization,
    }


def repair_plan() -> dict[str, Any]:
    check = doctor()
    actions: list[dict[str, Any]] = []
    if any(item.get("code") == "local_pmb_memory_session_smoke_failed" for item in check.get("issues", [])):
        actions.append(
            {
                "id": "refresh_current_codex_mcp_session",
                "mode": "manual",
                "reason": "Daemon is governed separately; current Codex session MCP binding may still be stale.",
                "requires_user_action": True,
            }
        )
    return {
        "schema": "codex-workflow-gate.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "actions": actions,
        "default_apply": False,
        "dry_run_contract": {
            "writes_files": False,
            "writes_memory": False,
            "changes_codex_config": False,
        },
    }


def metrics() -> dict[str, Any]:
    memory = memory_preflight("系统 记忆 基线 技能", check_session=True)
    finalization = finalization_gate("大变动 更新记忆 更新基线 更新技能 联网 外部知识")
    return {
        "schema": "codex-workflow-gate.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "memory_preflight_ok": bool(memory.get("ok")),
        "memory_should_prepare": bool(memory.get("should_prepare_memory")),
        "memory_session_smoke_ok": memory.get("session_smoke_ok"),
        "profile_guidance_count": int(((memory.get("profile_guidance") or {}).get("selected_fact_count")) or 0),
        "finalization_gate_detects_iteration": bool(finalization.get("requires_iteration_review")),
        "finalization_gate_detects_external_knowledge_closeout": bool((finalization.get("external_knowledge_closeout") or {}).get("required")),
    }


def validate() -> dict[str, Any]:
    check = doctor()
    return {
        "schema": "codex-workflow-gate.validate.v1",
        "ok": bool(check.get("ok")),
        "generated_at": now_iso(),
        "doctor": check,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Codex workflow gates")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["snapshot", "doctor", "repair-plan", "validate", "metrics"]:
        sub.add_parser(name)
    preflight = sub.add_parser("memory-preflight")
    preflight.add_argument("--message", default="")
    preflight.add_argument("--check-session", action="store_true")
    final = sub.add_parser("finalization-gate")
    final.add_argument("--message", default="")
    final.add_argument("--include-approval-block", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "repair-plan":
        payload = repair_plan()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "metrics":
        payload = metrics()
    elif args.command == "memory-preflight":
        payload = memory_preflight(args.message, check_session=args.check_session)
    elif args.command == "finalization-gate":
        payload = finalization_gate(args.message, include_approval_block=args.include_approval_block)
    else:
        raise SystemExit(f"unknown command: {args.command}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
