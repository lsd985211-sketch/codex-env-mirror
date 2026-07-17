#!/usr/bin/env python3
"""Coherence checks across Codex governance surfaces.

This doctor catches problems that individual validators intentionally miss:
route under-detection, closeout queue fragmentation, noisy MCP advisories, and
pending review items spread across memory/external-knowledge/work-note layers.
It does not repair, write memory, mutate queues, or change permissions. The
CLI writes only a bounded diagnostic artifact so full evidence can be fetched
without flooding the caller context.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from bounded_output import bounded_payload, json_size_bytes, output_evidence_policy
from shared.json_cli import configure_utf8_stdio, now_iso, print_json
from workflow_failure_diagnostics import extract_failure_diagnostics
from workflow_review_queue import stable_review_key


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
RUNTIME_DIR = BRIDGE / "runtime" / "global_coherence_doctor"
DEFAULT_INLINE_BYTES = 8 * 1024

configure_utf8_stdio()

COHERENCE_PROMPT = "检查当前全局下系统存在的冗余和互相矛盾或拮抗的机制问题"
BLOCKING_OWNER_SEVERITIES = frozenset({"critical", "error", "high", "risk"})


def run_json(args: list[str], *, timeout: int = 30) -> dict[str, Any]:
    command = [sys.executable, *args]
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "command": command}
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception:
        return {
            "ok": False,
            "command": command,
            "returncode": proc.returncode,
            "stdout_preview": (proc.stdout or "")[:1200],
            "stderr_preview": (proc.stderr or "")[:1200],
        }
    if not isinstance(payload, dict):
        return {"ok": False, "command": command, "reason": "json_root_not_object"}
    payload.setdefault("ok", proc.returncode == 0)
    return payload


def issue(severity: str, code: str, message: str, **detail: Any) -> dict[str, Any]:
    item = {"severity": severity, "code": code, "message": message}
    item.update({key: value for key, value in detail.items() if value not in (None, "", [], {})})
    return item


def check_route_under_detection(workflow: dict[str, Any], memory: dict[str, Any], skill: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    workflow_domains = [str(item.get("key") or "") for item in workflow.get("domains", []) if isinstance(item, dict)]
    memory_domains = [str(item) for item in memory.get("domain_keys", [])]
    skill_domains = [str(item.get("key") or "") for item in skill.get("domains", []) if isinstance(item, dict)]
    skill_names = [str(item.get("name") or "") for item in skill.get("selected_skills", []) if isinstance(item, dict)]
    if "workflow_governance" not in workflow_domains:
        issues.append(
            issue(
                "risk",
                "workflow_governance_route_missing",
                "Workflow orchestrator did not classify global mechanism conflict audit as workflow governance.",
                workflow_domains=workflow_domains,
            )
        )
    if "workflow_governance" not in memory_domains:
        issues.append(
            issue(
                "advisory",
                "memory_governance_route_missing",
                "Memory router did not receive or infer workflow_governance for a global mechanism audit.",
                memory_domains=memory_domains,
            )
        )
    if "workflow_governance" not in skill_domains:
        issues.append(
            issue(
                "risk",
                "skill_governance_route_missing",
                "Skill orchestrator did not classify global mechanism conflict audit as workflow governance.",
                skill_domains=skill_domains,
            )
        )
    if not {"global-framework", "diagnose"} & set(skill_names):
        issues.append(
            issue(
                "risk",
                "governance_skill_selection_missing",
                "Global governance audit should select at least global-framework or diagnose.",
                selected_skills=skill_names,
            )
        )
    return issues


def check_closeout(closeout: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    pending = closeout.get("pending_disposition") if isinstance(closeout.get("pending_disposition"), dict) else {}
    if not pending:
        issues.append(
            issue(
                "risk",
                "closeout_pending_disposition_missing",
                "Closeout package lacks a unified pending_disposition queue.",
            )
        )
    work_notes = closeout.get("work_notes") if isinstance(closeout.get("work_notes"), dict) else {}
    if int(work_notes.get("active_count") or 0) > 0 and int(pending.get("pending_count") or 0) == 0:
        issues.append(
            issue(
                "risk",
                "work_notes_not_in_closeout_queue",
                "Active work notes exist but are not represented in the unified closeout queue.",
                active_count=work_notes.get("active_count"),
            )
        )
    external = closeout.get("external_knowledge_candidates") if isinstance(closeout.get("external_knowledge_candidates"), dict) else {}
    final_summary = closeout.get("final_reply_must_show") if isinstance(closeout.get("final_reply_must_show"), dict) else {}
    if int(external.get("selected_count") or 0) > 0:
        pending_items = pending.get("items", []) if isinstance(pending.get("items"), list) else []
        external_pending = next(
            (
                item for item in pending_items
                if isinstance(item, dict) and item.get("kind") == "external_knowledge_memory_candidates"
            ),
            {},
        )
        review_items = external_pending.get("review_items") if isinstance(external_pending, dict) else []
        if isinstance(review_items, list):
            keys = [
                stable_review_key(item, kind="external_knowledge_memory_candidates")
                for item in review_items
                if isinstance(item, dict)
            ]
            if len(keys) != len(set(keys)):
                issues.append(
                    issue(
                        "risk",
                        "external_knowledge_review_items_duplicate",
                        "External knowledge review items contain duplicate candidate identities.",
                        review_item_count=len(keys),
                        unique_count=len(set(keys)),
                    )
                )
        if not isinstance(review_items, list) or len(review_items) < int(external.get("selected_count") or 0):
            issues.append(
                issue(
                    "risk",
                    "external_knowledge_candidates_not_reviewable_in_closeout",
                    "External knowledge candidates are pending but pending_disposition lacks reviewable item details.",
                    selected_count=external.get("selected_count"),
                    review_item_count=len(review_items) if isinstance(review_items, list) else 0,
                )
            )
        else:
            incomplete = [
                item.get("source_item_id") or item.get("title") or f"item-{index}"
                for index, item in enumerate(review_items, start=1)
                if isinstance(item, dict)
                and not all(str(item.get(field) or "").strip() for field in ("title", "summary", "trust_tier", "freshness_class", "proposed_destination_namespace"))
            ]
            if incomplete:
                issues.append(
                    issue(
                        "risk",
                        "external_knowledge_review_items_missing_attributes",
                        "External knowledge review items must include title, summary, trust_tier, freshness_class, and proposed destination.",
                        incomplete_items=incomplete,
                    )
                )
        issues.append(
            issue(
                "advisory",
                "external_knowledge_candidates_pending_review",
                "Reusable external knowledge candidates are pending review; this is expected only if surfaced in pending_disposition.",
                selected_count=external.get("selected_count"),
            )
        )
    pending_items = pending.get("items", []) if isinstance(pending.get("items"), list) else []
    must_surface_count = sum(1 for item in pending_items if isinstance(item, dict) and item.get("must_surface_to_user"))
    if must_surface_count and int(final_summary.get("total_review_cards") or 0) == 0:
        issues.append(
            issue(
                "risk",
                "closeout_must_surface_missing_review_summary",
                "Closeout has must-surface pending items but final_reply_must_show has no review cards.",
                must_surface_count=must_surface_count,
            )
        )
    cards = final_summary.get("cards") if isinstance(final_summary.get("cards"), list) else []
    if cards:
        card_keys = [
            stable_review_key(
                {
                    "source_item_id": item.get("id", ""),
                    "source_url": item.get("source", ""),
                    "title": item.get("title", ""),
                    "summary": item.get("digest", ""),
                },
                kind=str(item.get("kind") or ""),
            )
            for item in cards
            if isinstance(item, dict)
        ]
        if len(card_keys) != len(set(card_keys)):
            issues.append(
                issue(
                    "risk",
                    "closeout_review_cards_duplicate",
                    "Final reply review cards contain duplicate candidate identities.",
                    card_count=len(card_keys),
                    unique_count=len(set(card_keys)),
                )
            )
    return issues


def check_mcp(mcp_validate: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if mcp_validate and not mcp_validate.get("ok"):
        issues.append(
            issue(
                "risk",
                "mcp_validate_failed",
                "MCP route/session validation failed and must not be hidden by advisory-only callability summaries.",
                details=mcp_validate.get("issues"),
            )
        )
    summary = mcp_validate.get("advisory_summary") if isinstance(mcp_validate.get("advisory_summary"), dict) else {}
    if not summary:
        issues.append(
            issue(
                "risk",
                "mcp_advisory_summary_missing",
                "MCP validation lacks structured advisory_summary for unproven current-turn callability.",
            )
        )
    elif int(summary.get("unproven_native_callability_count") or 0) >= 10:
        issues.append(
            issue(
                "advisory",
                "mcp_unproven_callability_broad",
                "Many native tools are merely unproven in this turn; probe only when the selected execution affinity reaches a native or session-bound route.",
                count=summary.get("unproven_native_callability_count"),
            )
        )
    return issues


def owner_health_snapshot(registry: dict[str, Any]) -> list[dict[str, Any]]:
    contracts = registry.get("contracts") if isinstance(registry.get("contracts"), dict) else {}
    jobs: list[tuple[str, dict[str, Any]]] = []
    for system, contract in contracts.items():
        if not isinstance(contract, dict):
            continue
        for command in contract.get("health_commands", []):
            if isinstance(command, dict) and isinstance(command.get("args"), list):
                jobs.append((str(system), command))

    def execute(system: str, command: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        result = run_json([str(item) for item in command.get("args", [])], timeout=int(command.get("timeout") or 60))
        diagnostics = extract_failure_diagnostics(result) if not bool(result.get("ok")) else {}
        command_args = [str(item) for item in command.get("args", [])]
        return {
            "system": system,
            "name": str(command.get("name") or "owner_health"),
            "severity": str(command.get("severity") or "risk"),
            "args": command_args,
            "ok": bool(result.get("ok")),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "owner_schema": result.get("schema"),
            "owner_status": result.get("status") or result.get("doctor_status"),
            "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
            "diagnostics": diagnostics,
            "result_ref": f"command:{sys.executable} {' '.join(command_args)}",
        }

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(jobs)))) as pool:
        futures = [pool.submit(execute, system, command) for system, command in jobs]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: (item["system"], item["name"]))


def check_owner_health(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in values:
        if item.get("ok"):
            continue
        diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
        issues.append(
            issue(
                str(item.get("severity") or "risk"),
                "owner_health_failed",
                f"{item.get('system')}/{item.get('name')} owner health failed.",
                system=item.get("system"),
                owner_check=item.get("name"),
                owner_schema=item.get("owner_schema"),
                owner_status=item.get("owner_status"),
                root_cause=diagnostics.get("reason"),
                next_action=diagnostics.get("next_action"),
                diagnostic_count=diagnostics.get("diagnostic_count"),
                details=diagnostics.get("items"),
                owner_result_ref=item.get("result_ref"),
                elapsed_ms=item.get("elapsed_ms"),
            )
        )
    return issues


def check_system_membership(system_membership: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not system_membership:
        issues.append(
            issue(
                "risk",
                "system_membership_validate_missing",
                "System membership contract validation did not run.",
            )
        )
        return issues
    if not system_membership.get("ok"):
        issues.append(
            issue(
                "risk",
                "system_membership_validate_failed",
                "System membership contract validation failed.",
                details=system_membership.get("issues"),
            )
        )
    probes = system_membership.get("probes") if isinstance(system_membership.get("probes"), dict) else {}
    if not probes.get("impact_contract_upgrade_required"):
        issues.append(
            issue(
                "risk",
                "system_membership_impact_probe_missing",
                "System membership impact probe did not require a contract upgrade for MCP route changes.",
            )
        )
    return issues


def check_online_access_gate(online_gate: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not online_gate:
        issues.append(
            issue(
                "risk",
                "online_access_gate_validate_missing",
                "Online access gate validation did not run.",
            )
        )
        return issues
    if not online_gate.get("ok"):
        issues.append(
            issue(
                "risk",
                "online_access_gate_validate_failed",
                "Online access gate validation failed.",
                details=online_gate.get("issues"),
            )
        )
    case_ok = online_gate.get("case_ok") if isinstance(online_gate.get("case_ok"), dict) else {}
    if case_ok.get("check_rejects_web_without_exception") is not True:
        issues.append(
            issue(
                "risk",
                "online_access_gate_direct_web_probe_missing",
                "Online access gate did not prove that generic web without resource-layer exception is rejected.",
            )
        )
    return issues


def snapshot() -> dict[str, Any]:
    workflow = run_json([str(BRIDGE / "workflow_orchestrator.py"), "plan", "--message", COHERENCE_PROMPT, "--detail", "micro"])
    memory = run_json([str(BRIDGE / "memory_router.py"), "route", "--message", COHERENCE_PROMPT])
    skill = run_json([str(BRIDGE / "skill_orchestrator.py"), "plan", "--message", COHERENCE_PROMPT])
    closeout = run_json(
        [
            str(BRIDGE / "codex_workflow_entry.py"),
            "closeout",
            "--task-kind",
            "closeout-structure-probe",
            "--selected",
            "global-framework,diagnose",
            "--used",
            "global-framework,diagnose",
            "--outcome",
            "ok",
        ]
    )
    mcp = run_json([str(BRIDGE / "mcp_session_doctor.py"), "validate"], timeout=45)
    system_membership = run_json([str(BRIDGE / "system_membership.py"), "validate"], timeout=45)
    system_registry = run_json([str(BRIDGE / "system_membership.py"), "snapshot"], timeout=45)
    owner_health = owner_health_snapshot(system_registry)
    online_gate = run_json([str(BRIDGE / "online_access_gate.py"), "validate"], timeout=45)
    memory_validate = run_json([str(BRIDGE / "memory_governance.py"), "validate"], timeout=45)
    blocking_owner_failures = [
        item
        for item in owner_health
        if not bool(item.get("ok")) and str(item.get("severity") or "risk").lower() in BLOCKING_OWNER_SEVERITIES
    ]
    return {
        "schema": "global_coherence_doctor.snapshot.v1",
        "ok": all(bool(item.get("ok")) for item in (workflow, memory, skill, closeout, mcp, system_membership, online_gate, memory_validate)) and not blocking_owner_failures,
        "generated_at": now_iso(),
        "prompt": COHERENCE_PROMPT,
        "owner_health_summary": {
            "check_count": len(owner_health),
            "failed_count": sum(1 for item in owner_health if not bool(item.get("ok"))),
            "blocking_failure_count": len(blocking_owner_failures),
            "advisory_failure_count": sum(
                1
                for item in owner_health
                if not bool(item.get("ok")) and str(item.get("severity") or "risk").lower() not in BLOCKING_OWNER_SEVERITIES
            ),
        },
        "surfaces": {
            "workflow": workflow,
            "memory_route": memory,
            "skill_route": skill,
            "closeout": closeout,
            "mcp_validate": mcp,
            "system_membership_validate": system_membership,
            "system_registry": system_registry,
            "owner_health": owner_health,
            "online_access_gate_validate": online_gate,
            "memory_validate": memory_validate,
        },
    }


def doctor() -> dict[str, Any]:
    snap = snapshot()
    surfaces = snap.get("surfaces") if isinstance(snap.get("surfaces"), dict) else {}
    issues: list[dict[str, Any]] = []
    issues.extend(
        check_route_under_detection(
            surfaces.get("workflow", {}) if isinstance(surfaces.get("workflow"), dict) else {},
            surfaces.get("memory_route", {}) if isinstance(surfaces.get("memory_route"), dict) else {},
            surfaces.get("skill_route", {}) if isinstance(surfaces.get("skill_route"), dict) else {},
        )
    )
    issues.extend(check_closeout(surfaces.get("closeout", {}) if isinstance(surfaces.get("closeout"), dict) else {}))
    issues.extend(check_mcp(surfaces.get("mcp_validate", {}) if isinstance(surfaces.get("mcp_validate"), dict) else {}))
    issues.extend(check_system_membership(surfaces.get("system_membership_validate", {}) if isinstance(surfaces.get("system_membership_validate"), dict) else {}))
    issues.extend(check_online_access_gate(surfaces.get("online_access_gate_validate", {}) if isinstance(surfaces.get("online_access_gate_validate"), dict) else {}))
    issues.extend(check_owner_health(surfaces.get("owner_health", []) if isinstance(surfaces.get("owner_health"), list) else []))
    status = "risk" if any(item["severity"] == "risk" for item in issues) else "advisory" if issues else "ok"
    return {
        "schema": "global_coherence_doctor.doctor.v1",
        "ok": status != "risk",
        "generated_at": now_iso(),
        "status": status,
        "issues": issues,
        "summary": {
            "risk_count": sum(1 for item in issues if item["severity"] == "risk"),
            "advisory_count": sum(1 for item in issues if item["severity"] == "advisory"),
        },
        "snapshot": snap,
    }


def validate() -> dict[str, Any]:
    doc = doctor()
    issues = doc.get("issues", []) if isinstance(doc.get("issues"), list) else []
    blocking_codes = {
        "workflow_governance_route_missing",
        "skill_governance_route_missing",
        "governance_skill_selection_missing",
        "closeout_pending_disposition_missing",
        "work_notes_not_in_closeout_queue",
        "external_knowledge_review_items_duplicate",
        "closeout_must_surface_missing_review_summary",
        "closeout_review_cards_duplicate",
        "mcp_advisory_summary_missing",
        "mcp_validate_failed",
        "owner_health_failed",
        "system_membership_validate_missing",
        "system_membership_validate_failed",
        "system_membership_impact_probe_missing",
        "online_access_gate_validate_missing",
        "online_access_gate_validate_failed",
        "online_access_gate_direct_web_probe_missing",
    }
    blockers = [
        item
        for item in issues
        if isinstance(item, dict)
        and item.get("code") in blocking_codes
        and (
            item.get("code") != "owner_health_failed"
            or str(item.get("severity") or "risk").lower() in BLOCKING_OWNER_SEVERITIES
        )
    ]
    return {
        "schema": "global_coherence_doctor.validate.v1",
        "ok": not blockers,
        "generated_at": now_iso(),
        "blockers": blockers,
        "doctor_status": doc.get("status"),
        "advisory_count": sum(1 for item in issues if isinstance(item, dict) and item.get("severity") == "advisory"),
    }


def metrics() -> dict[str, Any]:
    doc = doctor()
    issues = doc.get("issues", []) if isinstance(doc.get("issues"), list) else []
    return {
        "schema": "global_coherence_doctor.metrics.v1",
        "ok": True,
        "generated_at": now_iso(),
        "issue_count": len(issues),
        "risk_count": sum(1 for item in issues if isinstance(item, dict) and item.get("severity") == "risk"),
        "advisory_count": sum(1 for item in issues if isinstance(item, dict) and item.get("severity") == "advisory"),
        "read_only": True,
    }


def persist_full_result(command: str, payload: dict[str, Any]) -> str:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNTIME_DIR / f"{command}-latest.json"
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)
    return str(path.resolve())


def compact_issue(item: dict[str, Any]) -> dict[str, Any]:
    code = str(item.get("code") or "unknown")
    system = str(item.get("system") or "global")
    owner_check = str(item.get("owner_check") or "general")
    return {
        "id": f"global_coherence:{code}:{system}:{owner_check}".lower(),
        "severity": item.get("severity"),
        "code": item.get("code"),
        "message": item.get("message"),
        "system": item.get("system"),
        "owner_check": item.get("owner_check"),
        "root_cause": item.get("root_cause"),
        "next_action": item.get("next_action"),
        "owner_status": item.get("owner_status"),
        "owner_result_ref": item.get("owner_result_ref"),
        "details": list(item.get("details") or [])[:4] if isinstance(item.get("details"), list) else item.get("details"),
    }


def surface_status_rows(snapshot_payload: dict[str, Any]) -> list[dict[str, Any]]:
    surfaces = snapshot_payload.get("surfaces") if isinstance(snapshot_payload.get("surfaces"), dict) else {}
    rows: list[dict[str, Any]] = []
    for name, value in surfaces.items():
        if name in {"system_registry", "owner_health"} or not isinstance(value, dict):
            continue
        rows.append(
            {
                "surface": name,
                "ok": bool(value.get("ok")),
                "status": value.get("status") or value.get("doctor_status"),
                "reason": value.get("reason") or value.get("error"),
                "next_action": value.get("next_action"),
                "schema": value.get("schema"),
            }
        )
    owner_health = surfaces.get("owner_health") if isinstance(surfaces.get("owner_health"), list) else []
    for item in owner_health:
        if isinstance(item, dict) and not item.get("ok"):
            rows.append(
                {
                    "surface": f"owner:{item.get('system')}/{item.get('name')}",
                    "ok": False,
                    "status": item.get("owner_status"),
                    "reason": (item.get("diagnostics") or {}).get("reason") if isinstance(item.get("diagnostics"), dict) else "owner_health_failed",
                    "next_action": (item.get("diagnostics") or {}).get("next_action") if isinstance(item.get("diagnostics"), dict) else "",
                    "result_ref": item.get("result_ref"),
                }
            )
    return rows


def compact_cli_payload(command: str, payload: dict[str, Any], *, artifact_ref: str) -> dict[str, Any]:
    if command == "doctor":
        issues = [compact_issue(item) for item in payload.get("issues", []) if isinstance(item, dict)]
        issues.sort(key=lambda item: (0 if item.get("severity") == "risk" else 1, str(item.get("code") or "")))
        snapshot_payload = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        result: dict[str, Any] = {
            "schema": payload.get("schema"),
            "ok": payload.get("ok"),
            "status": payload.get("status"),
            "generated_at": payload.get("generated_at"),
            "summary": payload.get("summary"),
            "issues": issues[:20],
            "issue_count": len(issues),
            "has_more": len(issues) > 20,
            "surface_status": surface_status_rows(snapshot_payload),
        }
    elif command == "snapshot":
        result = {
            "schema": payload.get("schema"),
            "ok": payload.get("ok"),
            "generated_at": payload.get("generated_at"),
            "owner_health_summary": payload.get("owner_health_summary"),
            "surface_status": surface_status_rows(payload),
        }
    else:
        result = dict(payload)
    result["raw_result_ref"] = f"artifact:{artifact_ref}"
    result["full_command_ref"] = f"command:python _bridge/global_coherence_doctor.py {command} --full"
    result["output_evidence_policy"] = output_evidence_policy()
    projected = bounded_payload(
        result,
        max_bytes=DEFAULT_INLINE_BYTES,
        max_items=24,
        max_string=900,
        preserve_keys=("surface_status", "issue_count", "issues", "full_command_ref"),
        artifact_ref=f"artifact:{artifact_ref}",
    )
    projected["output_budget"]["original_full_result_bytes"] = json_size_bytes(payload)
    return projected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only global coherence doctor")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("snapshot", "doctor", "validate", "metrics"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("--full", action="store_true", help="Emit the complete successful result.")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "doctor":
        payload = doctor()
    elif args.command == "validate":
        payload = validate()
    elif args.command == "metrics":
        payload = metrics()
    else:  # pragma: no cover
        parser.error(f"unsupported command: {args.command}")
    if args.full:
        output = payload
    else:
        artifact_ref = persist_full_result(args.command, payload)
        output = compact_cli_payload(args.command, payload, artifact_ref=artifact_ref)
    print_json(output)
    return 0 if payload.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
