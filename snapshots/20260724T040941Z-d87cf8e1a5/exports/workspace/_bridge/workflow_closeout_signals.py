#!/usr/bin/env python3
"""Signal-driven optional closeout checks.

Ownership: decide and run optional closeout checks only when current-turn
signals justify them.
Non-goals: assembling closeout packages, saving records, mutating memory, or
clearing work notes.
State behavior: read-only except external_knowledge candidate materialization
when explicit external-research signals or caller flags request that draft
candidate path.
Caller context: codex_workflow_entry.closeout uses this to keep ordinary
closeout compact and avoid unconditional candidate scans.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from workflow_finalization import finalize as finalize_workflow
from shared.json_cli import now_iso


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"


def membership_mirror_change_roots() -> list[str]:
    try:
        import system_membership

        projection = system_membership.mirror_source_projection()
        return [str(item) for item in projection.get("change_roots", []) if str(item)]
    except Exception:
        return []


def _change_root_matches(path: str, root: str, *, home: str, workspace: str, worktree: str) -> bool:
    path = normalized_change_path(path)
    root = normalized_change_path(root)
    if ":" not in root:
        return False
    namespace, suffix = root.split(":", 1)
    suffix = suffix.lstrip("/")
    bases = {
        "workspace": workspace,
        "worktree": worktree,
        "work_git": worktree,
        "codex_home": f"{home}/.codex",
        "agent_home": f"{home}/.agents",
        "cc_switch": f"{home}/.cc-switch",
    }
    host_home_markers = {
        "codex_home": "/.codex",
        "agent_home": "/.agents",
        "cc_switch": "/.cc-switch",
    }
    marker = host_home_markers.get(namespace)
    if marker:
        marker_index = path.find(marker)
        if marker_index >= 0:
            discovered_base = path[: marker_index + len(marker)]
            target = f"{discovered_base}/{suffix}" if suffix else discovered_base
            return path == target or path.startswith(target.rstrip("/") + "/")
    base = bases.get(namespace)
    if not base:
        return False
    target = f"{base}/{suffix}" if suffix else base
    return path == target or path.startswith(target.rstrip("/") + "/")


def normalized_change_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").lower()


def mirror_refresh_required(changed_files: list[str]) -> bool:
    home = normalized_change_path(str(Path.home()))
    workspace = normalized_change_path(str(ROOT))
    worktree = normalized_change_path(str(ROOT.parent))
    membership_roots = membership_mirror_change_roots()
    for value in changed_files:
        path = normalized_change_path(value)
        if not path:
            continue
        # The mirror is an output artifact, never a source of mirror scope.
        # Counting it here would make a publication/closeout cycle retrigger
        # itself on the next pass.
        if path == normalized_change_path(str(Path.home() / "codex-env-mirror")) or path.startswith(
            normalized_change_path(str(Path.home() / "codex-env-mirror")) + "/"
        ):
            continue
        candidates = [path]
        if ":" not in path and not path.startswith("/"):
            candidates.append(f"{workspace}/{path}")
            candidates.append(f"{worktree}/{path}")
        if any(
            _change_root_matches(path, root, home=home, workspace=workspace, worktree=worktree)
            for root in membership_roots
        ):
            return True
        if any(
            _change_root_matches(candidate, root, home=home, workspace=workspace, worktree=worktree)
            for candidate in candidates
            for root in membership_roots
        ):
            return True
    return False


def _unique_thread_ids(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        thread_id = str(value or "").strip()
        if not thread_id or thread_id in seen:
            continue
        result.append(thread_id)
        seen.add(thread_id)
    return result


def _receipt_thread_ids(receipts: list[str] | None) -> list[str]:
    targets: list[str] = []
    for value in receipts or []:
        text = str(value or "").strip()
        if not text:
            continue
        if text.startswith("thread_id="):
            targets.append(text.split("=", 1)[1].strip())
            continue
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            target = str(payload.get("threadId") or payload.get("thread_id") or "").strip()
            if target:
                targets.append(target)
    return _unique_thread_ids(targets)


def concurrent_closeout_handoff(
    *,
    current_task_complete: bool,
    active_workspace_threads: list[str] | None,
    handoff_target_thread: str = "",
    handoff_receipts: list[str] | None = None,
) -> dict[str, Any]:
    """Build the handoff contract from caller-supplied Codex App thread facts."""
    active = _unique_thread_ids(active_workspace_threads)
    required = bool(current_task_complete and active)
    target = str(handoff_target_thread or "").strip()
    if required and not target and len(active) == 1:
        target = active[0]
    blockers: list[dict[str, Any]] = []
    if required and not target:
        blockers.append({"code": "handoff_target_required", "active_workspace_threads": active})
    elif required and target not in active:
        blockers.append({"code": "handoff_target_not_active_in_workspace", "target_thread": target})
    receipt_targets = _receipt_thread_ids(handoff_receipts)
    if required and target and target not in receipt_targets:
        blockers.append({"code": "handoff_message_receipt_missing", "target_thread": target})
    complete = not required or not blockers
    return {
        "schema": "workflow_closeout.concurrent_handoff.v1",
        "ok": complete,
        "required": required,
        "complete": complete,
        "active_workspace_threads": active,
        "target_thread": target,
        "receipt_targets": receipt_targets,
        "message_required": required,
        "mirror_delegated": bool(required and complete),
        "milestone_delegated": bool(required and complete),
        "deferred_actions": (
            ["mirror_publish", "release_plan", "contract_review", "milestone_release"] if required else []
        ),
        "publication_order": (
            [
                "publish_one_final_recovery_mirror",
                "run_release_plan_after_publish",
                "run_contract_review_before_milestone_when_required",
                "create_milestone_only_when_release_plan_recommends_or_user_approves",
            ]
            if required
            else []
        ),
        "required_message_fields": (
            ["integrated_head", "validation_receipts", "pending_actions", "source_stability_acceptance"]
            if required
            else []
        ),
        "source_stability_acceptance": (
            "all remaining task branches integrated; main clean; Windows bare main matches Work Git main"
            if required
            else ""
        ),
        "current_task_scope": "closeout_only_after_handoff" if required else "normal_closeout",
        "blockers": blockers,
        "next_action": (
            "send the structured synchronization message with the Codex thread tool and pass its receipt to closeout"
            if required and not complete
            else (
                "target task first publishes one final recovery mirror; after publish, run release-plan and contract-review, then create a milestone only when recommended or explicitly approved"
                if required
                else ""
            )
        ),
        "fact_source_rule": "active workspace task IDs must come from Codex App thread state, not task-text inference",
    }


def apply_post_closeout_mirror(
    finalization: dict[str, Any],
    *,
    changed_files: list[str],
    apply: bool,
    outcome: str,
    owner_checks_ok: bool,
    concurrent_handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required = mirror_refresh_required(changed_files)
    decision: dict[str, Any] = {
        "schema": "workflow_closeout.post_mirror_publish.v1",
        "required": required,
        "applied": False,
        "ordering": "finalization_and_owner_checks_then_mirror_publish",
        "reason": "mirrored_source_changed" if required else "no_mirrored_source_changed",
    }
    if not required:
        finalization["post_closeout_mirror"] = decision
        return finalization
    if not owner_checks_ok:
        decision["reason"] = "required_owner_checks_not_successful"
        decision["next_action"] = "Resolve the reported self-update owner issues, then rerun the targeted closeout."
        finalization["post_closeout_mirror"] = decision
        finalization["ok"] = False
        finalization["blocked_reason"] = "required_owner_checks_not_successful"
        return finalization

    handoff = concurrent_handoff if isinstance(concurrent_handoff, dict) else {}
    if handoff.get("required"):
        decision["delegated"] = bool(handoff.get("complete"))
        decision["target_thread"] = handoff.get("target_thread", "")
        decision["handoff_receipt_targets"] = handoff.get("receipt_targets", [])
        if not handoff.get("ok") or not handoff.get("complete"):
            decision["reason"] = "concurrent_handoff_incomplete"
            decision["next_action"] = handoff.get("next_action", "")
            finalization["post_closeout_mirror"] = decision
            finalization["ok"] = False
            finalization["blocked_reason"] = "concurrent_handoff_incomplete"
            return finalization
        decision["ok"] = True
        decision["reason"] = "delegated_to_active_workspace_thread"
        decision["next_action"] = handoff.get("next_action", "")
        finalization["post_closeout_mirror"] = decision
        return finalization

    existing = finalization.get("post_closeout_mirror") if isinstance(finalization, dict) else None
    if isinstance(existing, dict) and existing.get("applied") and existing.get("ok"):
        reused = dict(existing)
        reused["reused"] = True
        reused["reason"] = "existing_successful_receipt"
        finalization["post_closeout_mirror"] = reused
        return finalization
    if not apply:
        decision["next_action"] = "python _bridge\\codex_workflow_entry.py mirror publish --confirm PUBLISH-CODEX-MIRROR"
        finalization["post_closeout_mirror"] = decision
        return finalization
    if outcome not in {"ok", "complete"} or not finalization.get("ok"):
        decision["reason"] = "finalization_not_successful"
        finalization["post_closeout_mirror"] = decision
        return finalization
    pre_owner = finalization.get("pre_owner_reconciliation") if isinstance(finalization.get("pre_owner_reconciliation"), dict) else {}
    existing_work_git = pre_owner.get("work_git_finalization") if isinstance(pre_owner.get("work_git_finalization"), dict) else {}
    work_git_finalization = existing_work_git or commit_generated_finalization(finalization)
    decision["work_git_finalization"] = work_git_finalization
    if not work_git_finalization.get("ok"):
        decision["reason"] = "generated_finalization_git_handoff_failed"
        decision["next_action"] = work_git_finalization.get("next_action", "")
        decision["ok"] = False
        finalization["post_closeout_mirror"] = decision
        finalization["ok"] = False
        finalization["blocked_reason"] = "generated_finalization_git_handoff_failed"
        return finalization
    publish_paths = list(dict.fromkeys([*changed_files, *work_git_finalization.get("paths", [])]))
    try:
        from codex_environment_mirror import PUBLISH_CONFIRMATION, publish

        result = publish(PUBLISH_CONFIRMATION, changed_paths=publish_paths)
    except Exception as exc:
        result = {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    decision["applied"] = True
    decision["result"] = result
    decision["ok"] = bool(result.get("ok"))
    finalization["post_closeout_mirror"] = decision
    if not result.get("ok"):
        finalization["ok"] = False
        finalization["blocked_reason"] = "post_closeout_mirror_publish_failed"
    return finalization


def host_projection_change_selectors(changed_files: list[str]) -> list[str]:
    import wsl_workspace_owner as owner

    selectors = [
        *list(owner.HOST_PROJECTION_FILES),
        owner.HOST_STARTUP_BASELINE,
        *[f"scripts/{item}" for item in owner.DESKTOP_SCRIPT_PROJECTION_FILES],
    ]
    aliases: set[str] = set()
    for value in changed_files:
        path = normalized_change_path(value).lstrip("./")
        if not path:
            continue
        aliases.add(path)
        for prefix in ("workspace/", "codex-home/"):
            if path.startswith(prefix):
                aliases.add(path[len(prefix) :])
        for marker in ("/workspace/", "/codex-home/"):
            if marker in path:
                aliases.add(path.split(marker, 1)[1])
    return [selector for selector in selectors if normalized_change_path(selector) in aliases]


def reconcile_host_projection(changed_files: list[str], *, apply: bool) -> dict[str, Any]:
    try:
        selectors = host_projection_change_selectors(changed_files)
    except Exception as exc:
        return {
            "schema": "workflow_closeout.host_projection_reconciliation.v1",
            "ok": False,
            "required": True,
            "applied": False,
            "selectors": [],
            "reason": f"projection_owner_unavailable:{type(exc).__name__}:{exc}",
            "next_action": "repair the WSL workspace host projection owner before closeout validation",
        }
    base = {
        "schema": "workflow_closeout.host_projection_reconciliation.v1",
        "ok": True,
        "required": bool(selectors),
        "applied": False,
        "selectors": selectors,
    }
    if not selectors:
        return {**base, "reason": "no_projection_source_changed"}
    if not apply:
        return {**base, "reason": "auto_finalize_disabled"}

    try:
        branch_result = subprocess.run(
            ["git", "-C", str(ROOT.parent), "branch", "--show-current"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            **base,
            "ok": False,
            "reason": f"work_git_branch_query_failed:{type(exc).__name__}:{exc}",
            "next_action": "repair local Work Git status before host projection reconciliation",
        }
    if branch_result.returncode != 0:
        return {
            **base,
            "ok": False,
            "reason": "work_git_branch_query_failed",
            "stderr": str(branch_result.stderr or "")[-500:],
            "next_action": "repair local Work Git status before host projection reconciliation",
        }
    branch = str(branch_result.stdout or "").strip()
    if branch != "main":
        return {
            **base,
            "reason": "deferred_to_main_integration_closeout",
            "branch": branch,
            "next_action": "run the integration closeout on main before Windows-only owners or mirror publication",
        }

    try:
        import wsl_workspace_owner as owner

        plan = owner.host_compatibility_projection_plan()
        if not plan.get("ok"):
            return {**base, "ok": False, "reason": "host_projection_plan_failed", "plan": plan}
        if not plan.get("would_change"):
            return {**base, "reason": "already_current", "plan": plan}
        result = owner.host_compatibility_projection_apply(confirm=owner.HOST_PROJECTION_CONFIRM)
        return {
            **base,
            "ok": bool(result.get("ok")),
            "applied": bool(result.get("applied")),
            "reason": "reconciled" if result.get("ok") else "host_projection_apply_failed",
            "result": result,
        }
    except Exception as exc:
        return {
            **base,
            "ok": False,
            "reason": f"{type(exc).__name__}:{exc}",
            "next_action": "repair the WSL workspace host projection owner before closeout validation",
        }


def prepare_closeout_state(
    finalization: dict[str, Any],
    *,
    changed_files: list[str],
    apply: bool,
    outcome: str,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": "workflow_closeout.pre_owner_reconciliation.v1",
        "ok": True,
        "applied": False,
        "ordering": "generated_finalization_then_host_projection_then_owner_checks",
    }
    if not apply:
        receipt["reason"] = "auto_finalize_disabled"
        finalization["pre_owner_reconciliation"] = receipt
        return finalization
    if outcome not in {"ok", "complete"} or not finalization.get("ok"):
        receipt["reason"] = "finalization_not_successful"
        finalization["pre_owner_reconciliation"] = receipt
        return finalization

    work_git = commit_generated_finalization(finalization)
    receipt["work_git_finalization"] = work_git
    receipt["applied"] = bool(work_git.get("required"))
    if not work_git.get("ok"):
        receipt["ok"] = False
        receipt["reason"] = "generated_finalization_git_handoff_failed"
        receipt["next_action"] = work_git.get("next_action", "")
        finalization["pre_owner_reconciliation"] = receipt
        finalization["ok"] = False
        finalization["blocked_reason"] = receipt["reason"]
        return finalization

    projection = reconcile_host_projection(changed_files, apply=True)
    receipt["host_projection"] = projection
    receipt["applied"] = bool(receipt["applied"] or projection.get("applied"))
    if not projection.get("ok"):
        receipt["ok"] = False
        receipt["reason"] = "host_projection_reconciliation_failed"
        receipt["next_action"] = projection.get("next_action", "")
        finalization["pre_owner_reconciliation"] = receipt
        finalization["ok"] = False
        finalization["blocked_reason"] = receipt["reason"]
        return finalization

    receipt["reason"] = "complete"
    receipt["paths"] = list(work_git.get("paths", []))
    finalization["pre_owner_reconciliation"] = receipt
    return finalization


def _work_git_relative_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (ROOT.parent / candidate) if raw.replace("\\", "/").startswith("workspace/") else (ROOT / candidate)
    try:
        return candidate.resolve(strict=False).relative_to(ROOT.parent.resolve()).as_posix()
    except ValueError:
        return ""


def generated_finalization_paths(finalization: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    checkpoint = finalization.get("project_checkpoint") if isinstance(finalization.get("project_checkpoint"), dict) else {}
    checkpoint_result = checkpoint.get("result") if isinstance(checkpoint.get("result"), dict) else {}
    checkpoint_payload = checkpoint_result.get("checkpoint") if isinstance(checkpoint_result.get("checkpoint"), dict) else {}
    checkpoint_path = _work_git_relative_path(str(checkpoint_payload.get("workspace_relative_path") or checkpoint_result.get("written") or ""))
    if checkpoint.get("applied") and not checkpoint_result.get("reused") and checkpoint_path:
        paths.extend([checkpoint_path, "workspace/_bridge/shared/checkpoints/MANIFEST.md"])

    baseline = finalization.get("startup_baseline") if isinstance(finalization.get("startup_baseline"), dict) else {}
    adopt = baseline.get("adopt") if isinstance(baseline.get("adopt"), dict) else {}
    baseline_path = _work_git_relative_path(str(adopt.get("baseline") or ""))
    if baseline.get("applied") and baseline_path:
        paths.append(baseline_path)
    return list(dict.fromkeys(path for path in paths if path))


def commit_generated_finalization(finalization: dict[str, Any]) -> dict[str, Any]:
    paths = generated_finalization_paths(finalization)
    if not paths:
        return {"schema": "workflow_closeout.finalization_git_handoff.v1", "ok": True, "required": False, "paths": []}
    try:
        import work_git_change_owner as git_owner

        checkpoint = finalization.get("project_checkpoint") if isinstance(finalization.get("project_checkpoint"), dict) else {}
        result = checkpoint.get("result") if isinstance(checkpoint.get("result"), dict) else {}
        payload = result.get("checkpoint") if isinstance(result.get("checkpoint"), dict) else {}
        identity = str(payload.get("checkpoint_id") or hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()[:12])
        task_id = f"finalize-{identity}"[:64]
        title = str(payload.get("title") or "workflow finalization").strip()
        message = f"chore: record {title}"
        plan = git_owner.commit_plan(task_id, paths, root=ROOT.parent, message=message)
        if not plan.get("ok") or int(plan.get("foreign_change_count") or 0):
            return {
                "schema": "workflow_closeout.finalization_git_handoff.v1",
                "ok": False,
                "required": True,
                "reason": "generated_paths_not_the_only_work_git_changes",
                "paths": paths,
                "plan": plan,
                "next_action": "resolve non-finalization Work Git changes, then rerun closeout",
            }
        commit = git_owner.commit_change_set(
            task_id,
            paths,
            message=message,
            confirm=git_owner.COMMIT_CONFIRM,
            root=ROOT.parent,
        )
        if not commit.get("ok"):
            return {
                "schema": "workflow_closeout.finalization_git_handoff.v1",
                "ok": False,
                "required": True,
                "reason": "generated_finalization_commit_failed",
                "paths": paths,
                "commit": commit,
                "next_action": "consume the Work Git change-set owner commit receipt and repair its blocker",
            }
        sync = git_owner.sync_branch(confirm=git_owner.SYNC_CONFIRM, root=ROOT.parent)
        return {
            "schema": "workflow_closeout.finalization_git_handoff.v1",
            "ok": bool(sync.get("ok")),
            "required": True,
            "paths": paths,
            "commit": commit,
            "sync": sync,
            "next_action": "repair Windows bare Git synchronization before mirror publish" if not sync.get("ok") else "",
        }
    except Exception as exc:
        return {
            "schema": "workflow_closeout.finalization_git_handoff.v1",
            "ok": False,
            "required": True,
            "reason": f"{type(exc).__name__}:{exc}",
            "paths": paths,
            "next_action": "repair the generated finalization Work Git handoff before mirror publish",
        }


def proposal_has_type(proposals: list[dict[str, Any]], *types: str) -> bool:
    expected = {item for item in types if item}
    return any(str(item.get("type") or "") in expected for item in proposals)


def empty_profile_plan(reason: str) -> dict[str, Any]:
    return {
        "schema": "memory_governance.profile_plan.skipped.v1",
        "ok": True,
        "candidate_count": 0,
        "candidates": [],
        "writes_profile": False,
        "skipped": True,
        "reason": reason,
    }


def read_profile_plan(limit: int = 20, profile_signals: list[str] | None = None) -> dict[str, Any]:
    command = [sys.executable, str(BRIDGE / "memory_governance.py"), "profile-plan", "--limit", str(max(1, int(limit)))]
    for signal in profile_signals or []:
        command.extend(["--signal", str(signal)])
    try:
        proc = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {
            "schema": "memory_governance.profile_plan.unavailable.v1",
            "ok": False,
            "candidate_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "writes_profile": False,
        }
    if not isinstance(payload, dict):
        return {"schema": "memory_governance.profile_plan.unavailable.v1", "ok": False, "candidate_count": 0}
    payload.setdefault("writes_profile", False)
    payload.setdefault("candidate_count", 0)
    return payload


def empty_external_knowledge_candidates(reason: str) -> dict[str, Any]:
    return {
        "schema": "external_knowledge.pending_memory_candidates.skipped.v1",
        "ok": True,
        "exists": False,
        "selected_count": 0,
        "candidate_count": 0,
        "would_write": [],
        "requires_user_review": False,
        "skipped": True,
        "reason": reason,
    }


def read_external_knowledge_pending_candidates() -> dict[str, Any]:
    materialize_command = [sys.executable, str(BRIDGE / "external_knowledge.py"), "memory-candidates", "--apply", "--limit", "20"]
    try:
        materialize_proc = subprocess.run(materialize_command, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except Exception as exc:
        return {
            "schema": "external_knowledge.pending_memory_candidates.v1",
            "ok": False,
            "exists": False,
            "selected_count": 0,
            "error": f"candidate_note_materialization_failed:{type(exc).__name__}: {exc}",
        }
    if materialize_proc.returncode != 0:
        return {
            "schema": "external_knowledge.pending_memory_candidates.v1",
            "ok": False,
            "exists": False,
            "selected_count": 0,
            "error": "candidate_note_materialization_failed",
            "stdout_preview": (materialize_proc.stdout or "")[:1200],
            "stderr_preview": (materialize_proc.stderr or "")[:1200],
        }
    command = [sys.executable, str(BRIDGE / "external_knowledge.py"), "pending-memory-candidates"]
    try:
        proc = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {
            "schema": "external_knowledge.pending_memory_candidates.v1",
            "ok": False,
            "exists": False,
            "selected_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(payload, dict):
        return {"schema": "external_knowledge.pending_memory_candidates.v1", "ok": False, "exists": False, "selected_count": 0}
    payload.setdefault("selected_count", 0)
    payload.setdefault("candidate_count", 0)
    payload.setdefault("would_write", [])
    payload.setdefault("candidate_note_materialized_by_closeout", True)
    return payload


def empty_finalization(outcome: str) -> dict[str, Any]:
    return {
        "schema": "workflow_finalization.skipped.v1",
        "ok": True,
        "generated_at": now_iso(),
        "apply": False,
        "outcome": outcome,
        "success": outcome in {"ok", "complete", "partial"},
        "signals": {"config_changed": False, "major_change": False},
        "actions": [],
        "skipped": True,
        "reason": "no_config_or_major_change_signal",
    }


def optional_closeout_sections(
    *,
    outcome: str,
    proposals: list[dict[str, Any]],
    profile_signal: list[str],
    check_profile_candidates: bool,
    check_external_knowledge: bool,
    web_search_used: bool,
    owner_mcp_tools: list[str],
    config_changed: bool,
    major_change: bool,
    auto_finalize: bool,
    finalization_project_id: str,
    finalization_title: str,
    finalization_summary: str,
    finalization_changed_file: list[str],
    finalization_evidence: list[str],
    finalization_backup: list[str],
    finalization_stable_conclusion: list[str],
    validation_items: list[dict[str, Any]],
    validation_receipts: list[str],
    task_kind: str,
    defer_post_mirror: bool = False,
) -> dict[str, Any]:
    profile_candidates = (
        read_profile_plan(limit=20, profile_signals=profile_signal)
        if check_profile_candidates or bool(profile_signal) or proposal_has_type(proposals, "profile", "user_profile")
        else empty_profile_plan("no_profile_signal")
    )
    external_candidates = (
        read_external_knowledge_pending_candidates()
        if check_external_knowledge or web_search_used or bool(owner_mcp_tools) or proposal_has_type(proposals, "external_knowledge")
        else empty_external_knowledge_candidates("no_external_research_signal")
    )
    needs_finalization = bool(
        config_changed
        or major_change
        or auto_finalize
        or finalization_project_id
        or finalization_title
        or finalization_changed_file
        or finalization_evidence
        or finalization_backup
        or finalization_stable_conclusion
    )
    finalization = (
        finalize_workflow(
            task_kind=task_kind,
            outcome=outcome,
            config_changed=config_changed,
            major_change=major_change,
            apply=auto_finalize,
            project_id=finalization_project_id,
            title=finalization_title,
            summary=finalization_summary or outcome,
            changed_files=finalization_changed_file,
            evidence=finalization_evidence,
            verification=[item.get("value", "") for item in validation_items if item.get("value")],
            backups=finalization_backup,
            stable_conclusions=finalization_stable_conclusion,
            validation_receipts=validation_receipts,
        )
        if needs_finalization
        else empty_finalization(outcome)
    )
    if not defer_post_mirror:
        finalization = apply_post_closeout_mirror(
            finalization,
            changed_files=finalization_changed_file,
            apply=auto_finalize,
            outcome=outcome,
            owner_checks_ok=False,
        )
    return {
        "profile_candidates": profile_candidates,
        "external_candidates": external_candidates,
        "finalization": finalization,
    }
