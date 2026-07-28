#!/usr/bin/env python3
"""Lifecycle implementation for external dependency change intelligence.

Ownership: normalize dependency events, load declarative profiles, orchestrate
bounded read-only probes, classify risk, deduplicate results, and emit review
proposals and incident handoff records.
Non-goals: fetching resources without the resource owners, applying repairs,
advancing validated baselines, changing product/account state, or scheduling.
State behavior: product/source read-only; writes only derived owner runtime,
resource receipts, proposal records, and reporter-compatible incident handoffs.
Caller context: ``dependency_change_intelligence.py`` is the stable facade;
launchers only create trigger envelopes and never invoke this module inline.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable

from shared.incident_index import incident_fingerprint


BRIDGE = Path(__file__).resolve().parent
ROOT = BRIDGE.parents[1]
CONTRACT_ROOT = BRIDGE / "contracts" / "external_dependencies"
DEFAULT_STATE_ROOT = BRIDGE / "runtime" / "dependency_change_intelligence"
MAX_TRIGGER_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_SUMMARY_CHARS = 4_000
MAX_PERIODIC_REVIEW_EVENTS = 20
VOLATILE_KEYS = {
    "captured_at",
    "completed_at",
    "created_at",
    "detected_at",
    "duration_ms",
    "elapsed_ms",
    "error",
    "error_text",
    "generated_at",
    "pid",
    "started_at",
    "timestamp",
}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3, "unknown": 4}
PROPOSAL_STATUSES = {"proposed", "approved", "rejected", "applied", "validated", "failed", "superseded"}


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json_object(path: Path, *, max_bytes: int = MAX_TRIGGER_BYTES) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def stable_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): stable_projection(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [stable_projection(child) for child in value]
    return value


def normalize_component(component: dict[str, Any]) -> dict[str, Any]:
    normalized = stable_projection(component)
    normalized["status"] = str(normalized.get("status") or "unknown")
    normalized["digest"] = sha256_json({key: value for key, value in normalized.items() if key != "digest"})
    return normalized


def normalize_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    raw_components = payload.get("components") if isinstance(payload.get("components"), dict) else {}
    components = {
        str(name): normalize_component(value)
        for name, value in sorted(raw_components.items())
        if isinstance(value, dict)
    }
    stable = {
        "schema": "codex_local_version_fingerprint.v1",
        "host": stable_projection(payload.get("host") if isinstance(payload.get("host"), dict) else {}),
        "components": components,
    }
    stable["digest"] = sha256_json(stable)
    collector_digest = str(payload.get("digest") or "").strip()
    if collector_digest:
        stable["collector_digest"] = collector_digest
    return stable


def observed_digest(fingerprint: dict[str, Any]) -> str:
    return str(fingerprint.get("collector_digest") or fingerprint.get("digest") or "")


def event_identity(profile_id: str, previous_digest: str, current_digest: str, trigger_kind: str) -> tuple[str, str]:
    identity = "\n".join((profile_id, previous_digest, current_digest, trigger_kind))
    dedupe_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return "dce_" + dedupe_key[:24], dedupe_key


def state_root_from_arg(value: str | Path | None = None) -> Path:
    configured = value or os.environ.get("CODEX_DEPENDENCY_INTELLIGENCE_STATE_ROOT") or DEFAULT_STATE_ROOT
    return Path(configured).expanduser().resolve()


def load_registry(contract_root: Path = CONTRACT_ROOT) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    index = read_json_object(contract_root / "index.json")
    if index.get("schema") != "external_dependency_registry.v1":
        issues.append("registry_schema_invalid")
    if not isinstance(index.get("profiles"), list) or not index.get("profiles"):
        issues.append("registry_profiles_missing")
    return index, issues


def load_profile(profile_id: str, contract_root: Path = CONTRACT_ROOT) -> tuple[dict[str, Any], list[str]]:
    index, issues = load_registry(contract_root)
    entry = next(
        (item for item in index.get("profiles", []) if isinstance(item, dict) and item.get("profile_id") == profile_id),
        None,
    )
    if not entry:
        return {}, [*issues, "profile_not_registered"]
    relative = str(entry.get("path") or "")
    profile = read_json_object((contract_root / relative).resolve())
    if profile.get("schema") != "external_dependency_profile.v1":
        issues.append("profile_schema_invalid")
    if profile.get("profile_id") != profile_id:
        issues.append("profile_identity_mismatch")
    contracts = profile.get("capability_contracts")
    if not isinstance(contracts, list) or not contracts:
        issues.append("capability_contracts_missing")
    else:
        seen: set[str] = set()
        for item in contracts:
            capability_id = str(item.get("capability_id") or "") if isinstance(item, dict) else ""
            if not capability_id or capability_id in seen:
                issues.append("capability_identity_invalid")
            seen.add(capability_id)
    return profile, list(dict.fromkeys(issues))


def capability_candidates(profile: dict[str, Any], changed_components: Iterable[str]) -> list[str]:
    changed = set(changed_components)
    result: list[str] = []
    for contract in profile.get("capability_contracts", []):
        if not isinstance(contract, dict):
            continue
        signals = contract.get("version_components") if isinstance(contract.get("version_components"), list) else []
        if not signals or changed.intersection(str(item) for item in signals):
            result.append(str(contract.get("capability_id") or ""))
    return [item for item in result if item]


def component_changes(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    before = previous.get("components") if isinstance(previous.get("components"), dict) else {}
    after = current.get("components") if isinstance(current.get("components"), dict) else {}
    names = sorted(set(before) | set(after))
    return [name for name in names if (before.get(name) or {}).get("digest") != (after.get(name) or {}).get("digest")]


def create_event(
    *,
    profile: dict[str, Any],
    previous: dict[str, Any],
    current: dict[str, Any],
    trigger_kind: str,
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile_id = str(profile.get("profile_id") or "")
    previous_digest = observed_digest(previous)
    current_digest = observed_digest(current)
    event_id, dedupe_key = event_identity(profile_id, previous_digest, current_digest, trigger_kind)
    changed = component_changes(previous, current)
    return {
        "schema": "dependency_change_event.v1",
        "event_id": event_id,
        "profile_id": profile_id,
        "detected_at": now_iso(),
        "trigger_kind": trigger_kind,
        "previous_validated_digest": previous_digest,
        "current_observed_digest": current_digest,
        "changed_components": changed,
        "affected_hosts": sorted(
            {
                str((current.get("components", {}).get(name) or {}).get("host_id") or "")
                for name in changed
                if (current.get("components", {}).get(name) or {}).get("host_id")
            }
        ),
        "product_surfaces": sorted(
            {
                str((current.get("components", {}).get(name) or {}).get("product_surface") or name)
                for name in changed
            }
        ),
        "capability_candidates": capability_candidates(profile, changed),
        "source_refs": source_refs or [],
        "raw_evidence_refs": [],
        "supersedes": [],
        "dedupe_key": dedupe_key,
        "status": "pending",
    }


def ingest_trigger(profile_id: str, trigger_file: Path, state_root: Path) -> dict[str, Any]:
    profile, issues = load_profile(profile_id)
    envelope = read_json_object(trigger_file)
    fingerprint_payload = envelope.get("fingerprint") if isinstance(envelope.get("fingerprint"), dict) else envelope
    if issues or not fingerprint_payload:
        return {
            "schema": "dependency_change_intelligence.ingest.v1",
            "ok": False,
            "status": "rejected",
            "issues": issues or ["trigger_invalid_or_oversized"],
            "trigger_file": str(trigger_file),
        }
    current = normalize_fingerprint(fingerprint_payload)
    state = state_root / profile_id
    last_validated = read_json_object(state / "last_validated.json")
    last_observed = read_json_object(state / "last_observed.json")
    atomic_write_json(state / "last_observed.json", current)
    current_observed_digest = observed_digest(current)
    validated_observed_digest = observed_digest(last_validated)
    if current_observed_digest == validated_observed_digest:
        return {
            "schema": "dependency_change_intelligence.ingest.v1",
            "ok": True,
            "status": "unchanged",
            "event_created": False,
            "current_digest": current_observed_digest,
            "validated_baseline_advanced": False,
        }
    event = create_event(profile=profile, previous=last_validated, current=current, trigger_kind="launcher")
    event_path = state / "events" / f"{event['event_id']}.json"
    pending_path = state / "pending" / f"{event['event_id']}.json"
    completed_path = state / "completed" / f"{event['event_id']}.json"
    duplicate = event_path.exists() or pending_path.exists() or completed_path.exists()
    if not duplicate:
        event["raw_evidence_refs"] = [{"kind": "launcher_trigger", "path": str(trigger_file)}]
        atomic_write_json(event_path, event)
        atomic_write_json(pending_path, event)
        atomic_write_json(
            state / "last_enqueued.json",
            {"schema": "dependency_change_intelligence.last_enqueued.v1", "event_id": event["event_id"], "digest": current_observed_digest},
        )
    return {
        "schema": "dependency_change_intelligence.ingest.v1",
        "ok": True,
        "status": "duplicate_pending" if duplicate else "event_created",
        "event_created": not duplicate,
        "event_id": event["event_id"],
        "current_digest": current_observed_digest,
        "previous_observed_digest": observed_digest(last_observed),
        "validated_baseline_advanced": False,
    }


def _scheduler_command(payload_path: Path, scan_root: Path, *, plan_only: bool, timeout_seconds: int) -> list[str]:
    command = [
        sys.executable,
        str(BRIDGE / "resource_scheduler.py"),
        "execute",
        "--payload-file",
        str(payload_path),
        "--store-root",
        str(scan_root / "resources"),
        "--event-log",
        str(scan_root / "resource-events.jsonl"),
        "--receipt-log",
        str(scan_root / "resource-receipts.jsonl"),
        "--total-timeout-seconds",
        str(timeout_seconds),
        "--json",
    ]
    if plan_only:
        command.append("--plan-only")
    return command


def resource_manifest_semantic_digest(path: Path) -> str:
    payload = read_json_object(path, max_bytes=8 * 1024 * 1024)
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else payload
    attempts = receipt.get("attempts") if isinstance(receipt.get("attempts"), list) else []
    projection: list[dict[str, Any]] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        projection.append(
            {
                "tool": attempt.get("tool"),
                "status": attempt.get("status"),
                "source": result.get("source"),
                "result_kind": result.get("result_kind"),
                "content": result.get("content"),
                "metadata": {
                    "items": metadata.get("items"),
                    "completed_deliverables": metadata.get("completed_deliverables"),
                    "missing_deliverables": metadata.get("missing_deliverables"),
                    "query": metadata.get("query"),
                    "total_count": metadata.get("total_count"),
                },
            }
        )
    return sha256_json(projection) if projection else ""


def _proposal_exists_for_event(state_root: Path, profile_id: str, event_id: str) -> bool:
    for path in (state_root / profile_id / "proposals").glob("*.json"):
        if read_json_object(path).get("event_id") == event_id:
            return True
    return False


def _pending_review_events(state_root: Path, profile_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted((state_root / profile_id / "pending").glob("*.json")):
        event = read_json_object(path)
        event_id = str(event.get("event_id") or "")
        if event_id and not _proposal_exists_for_event(state_root, profile_id, event_id):
            events.append(event)
    return events[:MAX_PERIODIC_REVIEW_EVENTS]


def run_periodic_review(profile_id: str, state_root: Path) -> dict[str, Any]:
    """Turn bounded pending events into review proposals without applying changes."""

    results: list[dict[str, Any]] = []
    for event in _pending_review_events(state_root, profile_id):
        event_id = str(event["event_id"])
        try:
            existing_probes = _event_probes(state_root, event)
            probe = (
                {
                    "ok": True,
                    "status": "existing",
                    "event_id": event_id,
                    "probe_count": len(existing_probes),
                }
                if existing_probes
                else probe_event(event_id, state_root)
            )
            proposal = propose_event(event_id, state_root)
            results.append(
                {
                    "event_id": event_id,
                    "ok": bool(proposal.get("ok")),
                    "probe_status": str(probe.get("status") or "unknown"),
                    "proposal_status": str(proposal.get("status") or "failed"),
                    "proposal_id": str((proposal.get("proposal") or {}).get("proposal_id") or ""),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "event_id": event_id,
                    "ok": False,
                    "probe_status": "failed",
                    "proposal_status": "failed",
                    "error_class": type(exc).__name__,
                }
            )
    return {
        "schema": "dependency_change_intelligence.periodic_review.v1",
        "ok": all(item["ok"] for item in results),
        "reviewed_count": len(results),
        "proposal_count": sum(1 for item in results if item.get("proposal_id")),
        "results": results,
        "auto_apply": False,
        "validated_baseline_advanced": False,
    }


def scan_profile(profile_id: str, trigger: str, state_root: Path, *, plan_only: bool, timeout_seconds: int) -> dict[str, Any]:
    profile, issues = load_profile(profile_id)
    if issues:
        return {"schema": "dependency_change_intelligence.scan.v1", "ok": False, "status": "blocked", "issues": issues}
    source_tasks = profile.get("source_tasks") if isinstance(profile.get("source_tasks"), list) else []
    if not source_tasks:
        return {"schema": "dependency_change_intelligence.scan.v1", "ok": False, "status": "blocked", "issues": ["source_tasks_missing"]}
    scan_id = "scan_" + sha256_json({"profile": profile_id, "trigger": trigger, "tasks": source_tasks, "date": now_iso()[:10]})[:20]
    scan_root = state_root / profile_id / "scans" / scan_id
    payload = {
        "batch_name": f"dependency-intelligence-{profile_id}",
        "execution": {"max_active": 4, "per_host_limit": 2, "fail_fast": False, "total_timeout_seconds": timeout_seconds},
        "items": source_tasks,
    }
    payload_path = scan_root / "source-batch.json"
    atomic_write_json(payload_path, payload)
    try:
        completed = subprocess.run(
            _scheduler_command(payload_path, scan_root, plan_only=plan_only, timeout_seconds=timeout_seconds),
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=max(5, timeout_seconds + 10),
            check=False,
        )
        receipt = json.loads(completed.stdout) if completed.stdout.strip() else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        receipt = {"ok": False, "status": "failed", "error_class": type(exc).__name__}
    results = receipt.get("results") if isinstance(receipt.get("results"), list) else []
    source_refs: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        manifest_path = str(item.get("manifest_path") or (item.get("receipt") or {}).get("manifest_path") or "")
        source_refs.append(
            {
                "item_id": item.get("item_id"),
                "accepted": bool((item.get("acceptance") or {}).get("accepted")),
                "manifest_path": manifest_path,
                "content_digest": resource_manifest_semantic_digest(Path(manifest_path)) if manifest_path else "",
                "source": ((item.get("acceptance") or {}).get("source_id") or ""),
            }
        )
    evidence_projection = [
        {
            "item_id": item.get("item_id"),
            "accepted": item.get("accepted"),
            "source": item.get("source"),
            "content_digest": item.get("content_digest"),
        }
        for item in source_refs
    ]
    scan_receipt = {
        "schema": "dependency_change_intelligence.scan.v1",
        "ok": bool(receipt.get("ok")),
        "status": str(receipt.get("status") or "failed"),
        "scan_id": scan_id,
        "profile_id": profile_id,
        "trigger_kind": trigger,
        "plan_only": plan_only,
        "accepted_count": int(receipt.get("accepted_count") or 0),
        "unmet_required_count": int(receipt.get("unmet_required_count") or 0),
        "source_refs": source_refs,
        "batch_manifest_path": str(receipt.get("manifest_path") or ""),
        "validated_baseline_advanced": False,
    }
    if not receipt.get("ok") and scan_receipt["accepted_count"]:
        scan_receipt["status"] = "degraded"
    if not plan_only and evidence_projection:
        current = {
            "schema": "codex_local_version_fingerprint.v1",
            "host": {"identity": "official_sources"},
            "components": {
                "official_sources": {
                    "status": scan_receipt["status"],
                    "product_surface": "web/account",
                    "source_evidence_digest": sha256_json(evidence_projection),
                }
            },
        }
        current = normalize_fingerprint(current)
        source_state = state_root / profile_id / "source_last_observed.json"
        previous = read_json_object(source_state)
        if previous.get("digest") != current.get("digest"):
            event = create_event(profile=profile, previous=previous, current=current, trigger_kind=trigger, source_refs=source_refs)
            atomic_write_json(state_root / profile_id / "events" / f"{event['event_id']}.json", event)
            atomic_write_json(state_root / profile_id / "pending" / f"{event['event_id']}.json", event)
            atomic_write_json(source_state, current)
            scan_receipt["event_id"] = event["event_id"]
            scan_receipt["event_created"] = True
    if trigger == "periodic" and not plan_only:
        periodic_review = run_periodic_review(profile_id, state_root)
        scan_receipt["periodic_review"] = periodic_review
        if not periodic_review["ok"]:
            scan_receipt["ok"] = False
            scan_receipt["status"] = "degraded"
    atomic_write_json(scan_root / "scan-receipt.json", scan_receipt)
    return scan_receipt


def _run_probe(spec: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    probe_type = str(spec.get("type") or "fixture")
    timeout_seconds = max(1, min(int(spec.get("timeout_seconds") or 10), 60))
    if probe_type == "fixture":
        observed = spec.get("observed")
        expected = spec.get("expected")
        accepted = observed == expected
        status = "supported" if accepted else "degraded"
        evidence_ref = str(spec.get("evidence_ref") or "declarative_fixture")
    elif probe_type == "command":
        command = spec.get("command") if isinstance(spec.get("command"), list) else []
        replacements = {"{python}": sys.executable, "{bridge}": str(BRIDGE), "{root}": str(ROOT)}
        command = [
            replacements.get(str(item), str(item).replace("{bridge}", str(BRIDGE)).replace("{root}", str(ROOT)))
            for item in command
        ]
        if not command:
            accepted = False
            status = "unknown"
            observed = {"reason": "probe_command_missing"}
            evidence_ref = ""
        else:
            try:
                completed = subprocess.run(
                    [str(item) for item in command],
                    cwd=str(ROOT),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                accepted = completed.returncode == int(spec.get("expected_exit_code") or 0)
                observed: dict[str, Any] = {
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[:MAX_EVIDENCE_SUMMARY_CHARS],
                    "stderr": completed.stderr[:MAX_EVIDENCE_SUMMARY_CHARS],
                }
                assertions = spec.get("json_assertions") if isinstance(spec.get("json_assertions"), list) else []
                if assertions:
                    try:
                        json_payload = json.loads(completed.stdout)
                    except json.JSONDecodeError:
                        accepted = False
                        observed["assertions"] = [{"ok": False, "reason": "stdout_not_json"}]
                    else:
                        assertion_results = evaluate_json_assertions(json_payload, assertions)
                        observed["assertions"] = assertion_results
                        observed["selected_values"] = {
                            str(item.get("path") or ""): json_path_value(json_payload, str(item.get("path") or ""))[1]
                            for item in assertions
                        }
                        accepted = accepted and all(item.get("ok") for item in assertion_results)
                    observed.pop("stdout", None)
                status = "supported" if accepted else "degraded"
                evidence_ref = "bounded_command_output"
            except (OSError, subprocess.TimeoutExpired) as exc:
                accepted = False
                status = "unknown" if isinstance(exc, OSError) else "degraded"
                observed = {"reason": type(exc).__name__}
                evidence_ref = ""
    else:
        accepted = False
        status = "unknown"
        observed = {"reason": "unsupported_probe_type"}
        evidence_ref = ""
    return {
        "probe_type": probe_type,
        "status": status,
        "observed": observed,
        "accepted": accepted,
        "evidence_ref": evidence_ref,
        "duration_ms": round((time.monotonic() - started) * 1000, 2),
        "timeout_seconds": timeout_seconds,
        "retry_disposition": "owner_bounded_retry_only",
    }


def json_path_value(payload: Any, path: str) -> tuple[bool, Any]:
    current = payload
    for part in [item for item in str(path or "").split(".") if item]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def evaluate_json_assertions(payload: Any, assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for assertion in assertions:
        path = str(assertion.get("path") or "")
        operation = str(assertion.get("op") or "equals")
        exists, value = json_path_value(payload, path)
        expected = assertion.get("value")
        if operation == "exists":
            accepted = exists
        elif operation == "not_empty":
            accepted = exists and value not in (None, "", [], {})
        elif operation == "contains":
            accepted = exists and isinstance(value, (list, str)) and expected in value
        else:
            accepted = exists and value == expected
        results.append({"path": path, "op": operation, "ok": accepted, "expected": expected})
    return results


def _incident_handoff(event: dict[str, Any], probe: dict[str, Any], state_root: Path) -> dict[str, Any]:
    issue_code = f"dependency_probe_{probe.get('status')}"
    evidence = {
        "issues": [{"code": issue_code}],
        "reason": str(probe.get("status") or "unknown"),
        "event_id": event.get("event_id"),
        "capability_id": probe.get("capability_id"),
    }
    request_shape = {
        "kind": "dependency_capability_probe",
        "title": f"依赖能力探测异常：{probe.get('capability_id')}",
        "policy": "dependency_intelligence_read_only_probe",
        "evidence": evidence,
    }
    family_id, _semantic = incident_fingerprint(request_shape, evidence)
    handoff = {
        "schema": "dependency_change_intelligence.incident_handoff.v1",
        "request_id": "report_" + sha256_json({"event": event.get("event_id"), "incident_family_id": family_id})[:24],
        "created_at": now_iso(),
        "status": "pending",
        "kind": "dependency_capability_probe",
        "title": request_shape["title"],
        "policy": "dependency_intelligence_read_only_probe",
        "incident_family_id": family_id,
        "evidence": evidence,
        "handoff_owner": "shared/codex_reporter.py -> shared/incident_index.py",
    }
    path = state_root / str(event.get("profile_id") or "unknown") / "incident-feedback" / f"{handoff['request_id']}.json"
    if not path.exists():
        atomic_write_json(path, handoff)
    reporter_receipt: dict[str, Any] = {"ok": True, "queued": False, "reason": "shadow_state_root"}
    if state_root.resolve() == DEFAULT_STATE_ROOT.resolve():
        from shared.codex_reporter import enqueue_report

        reporter_receipt = enqueue_report(
            kind="dependency_capability_probe",
            title=request_shape["title"],
            evidence=evidence,
            policy="dependency_intelligence_read_only_probe",
            priority=20,
        )
    return {"incident_family_id": family_id, "handoff_path": str(path), "reporter_receipt": reporter_receipt}


def event_path_for_id(state_root: Path, event_id: str) -> Path | None:
    for path in state_root.glob(f"*/events/{event_id}.json"):
        return path
    return None


def probe_event(event_id: str, state_root: Path) -> dict[str, Any]:
    event_path = event_path_for_id(state_root, event_id)
    event = read_json_object(event_path) if event_path else {}
    if not event:
        return {"schema": "dependency_change_intelligence.probe.v1", "ok": False, "status": "blocked", "issues": ["event_not_found"]}
    profile, issues = load_profile(str(event.get("profile_id") or ""))
    if issues:
        return {"schema": "dependency_change_intelligence.probe.v1", "ok": False, "status": "blocked", "issues": issues}
    candidates = set(event.get("capability_candidates") or [])
    probes: list[dict[str, Any]] = []
    for contract in profile.get("capability_contracts", []):
        if not isinstance(contract, dict) or str(contract.get("capability_id") or "") not in candidates:
            continue
        specs = contract.get("probes") if isinstance(contract.get("probes"), list) else []
        if not specs:
            specs = [{"type": "fixture", "observed": "unavailable", "expected": "runtime_handshake", "evidence_ref": "probe_not_yet_connected"}]
        for spec in specs:
            started_at = now_iso()
            result = _run_probe(spec if isinstance(spec, dict) else {})
            probe = {
                "schema": "dependency_capability_probe.v1",
                "probe_id": "dcp_" + sha256_json({"event": event_id, "capability": contract.get("capability_id"), "spec": spec})[:24],
                "event_id": event_id,
                "capability_id": contract.get("capability_id"),
                "host": contract.get("hosts", []),
                "product_surfaces": contract.get("product_surfaces", []),
                "authentication_boundary": contract.get("authentication_boundary", "runtime_observed"),
                "evidence_level": spec.get("evidence_level", 2) if isinstance(spec, dict) else 2,
                "input_signature": sha256_json(spec),
                "started_at": started_at,
                "completed_at": now_iso(),
                **result,
            }
            if not probe["accepted"]:
                probe["incident_feedback"] = _incident_handoff(event, probe, state_root)
            atomic_write_json(state_root / str(event["profile_id"]) / "probes" / f"{probe['probe_id']}.json", probe)
            probes.append(probe)
    status = "completed" if probes and all(item["accepted"] for item in probes) else "degraded" if probes else "unknown"
    return {
        "schema": "dependency_change_intelligence.probe.v1",
        "ok": status == "completed",
        "status": status,
        "event_id": event_id,
        "probes": probes,
        "validated_baseline_advanced": False,
    }


def _event_probes(state_root: Path, event: dict[str, Any]) -> list[dict[str, Any]]:
    profile_root = state_root / str(event.get("profile_id") or "") / "probes"
    return [payload for path in sorted(profile_root.glob("*.json")) if (payload := read_json_object(path)).get("event_id") == event.get("event_id")]


def classify_event(event: dict[str, Any], profile: dict[str, Any], probes: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_ids = set(event.get("capability_candidates") or [])
    contract_risks = {
        str(contract.get("capability_id") or ""): str(contract.get("risk") or "low")
        for contract in profile.get("capability_contracts", [])
        if isinstance(contract, dict) and contract.get("capability_id") in candidate_ids
    }
    probes_by_capability: dict[str, list[dict[str, Any]]] = {}
    for probe in probes:
        probes_by_capability.setdefault(str(probe.get("capability_id") or ""), []).append(probe)
    effective_probes: list[dict[str, Any]] = []
    weak_only_capabilities: list[str] = []
    for capability_id, capability_probes in probes_by_capability.items():
        strongest_level = min(int(item.get("evidence_level") or 5) for item in capability_probes)
        strongest = [item for item in capability_probes if int(item.get("evidence_level") or 5) == strongest_level]
        effective_probes.extend(strongest)
        if strongest_level >= 5 and strongest and all(item.get("accepted") for item in strongest):
            weak_only_capabilities.append(capability_id)
    unresolved = [item for item in effective_probes if item.get("status") in {"unknown", "degraded"}]
    accepted = [item for item in effective_probes if item.get("accepted")]
    if candidate_ids and not probes:
        risk = "unknown"
        confidence = 0.2
        response = "run_declared_read_only_probes"
    elif unresolved:
        unresolved_capabilities = {str(item.get("capability_id") or "") for item in unresolved}
        severities = [contract_risks[item] for item in unresolved_capabilities if item in contract_risks]
        risk = max(severities or ["medium"], key=lambda item: RISK_ORDER.get(item, 0))
        confidence = 0.7 if any(item.get("evidence_ref") for item in unresolved) else 0.4
        response = "generate_review_proposal_never_auto_apply"
    elif weak_only_capabilities:
        risk = "unknown"
        confidence = 0.35
        response = "run_higher_confidence_runtime_probe"
    else:
        risk = "low"
        confidence = 0.9 if accepted else 0.5
        response = "index_only"
    return {
        "schema": "dependency_change_intelligence.risk_classification.v1",
        "classifier_version": "1.0.0",
        "risk": risk,
        "confidence": confidence,
        "affected_capabilities": sorted(candidate_ids),
        "unresolved_probe_ids": [str(item.get("probe_id") or "") for item in unresolved],
        "weak_only_capabilities": sorted(weak_only_capabilities),
        "default_response": response,
    }


def propose_event(event_id: str, state_root: Path) -> dict[str, Any]:
    event_path = event_path_for_id(state_root, event_id)
    event = read_json_object(event_path) if event_path else {}
    if not event:
        return {"schema": "dependency_change_intelligence.propose.v1", "ok": False, "status": "blocked", "issues": ["event_not_found"]}
    profile, issues = load_profile(str(event.get("profile_id") or ""))
    if issues:
        return {"schema": "dependency_change_intelligence.propose.v1", "ok": False, "status": "blocked", "issues": issues}
    probes = _event_probes(state_root, event)
    classification = classify_event(event, profile, probes)
    contracts = [
        item for item in profile.get("capability_contracts", [])
        if isinstance(item, dict) and item.get("capability_id") in set(event.get("capability_candidates") or [])
    ]
    owners = sorted({str(owner) for item in contracts for owner in item.get("affected_owners", []) if str(owner)})
    actions = [action for item in contracts for action in item.get("planned_actions", []) if isinstance(action, dict)]
    proposal_core = {
        "event_id": event_id,
        "risk": classification["risk"],
        "affected_capabilities": classification["affected_capabilities"],
        "selected_owners": owners,
        "planned_actions": actions,
    }
    proposal_id = "dcp_" + sha256_json(proposal_core)[:24]
    proposal = {
        "schema": "dependency_change_proposal.v1",
        "proposal_id": proposal_id,
        "event_id": event_id,
        "status": "proposed",
        "title": f"Codex 依赖变化审阅：{classification['risk']}",
        "summary": "检测到可能影响本地能力契约的变化。当前仅生成审阅提案，未修改配置、代码、进程、任务或产品状态。",
        "risk": classification["risk"],
        "confidence": classification["confidence"],
        "affected_capabilities": classification["affected_capabilities"],
        "evidence_receipts": [str(item.get("probe_id") or "") for item in probes],
        "unresolved_uncertainties": [
            *classification["unresolved_probe_ids"],
            *[f"weak_evidence_only:{item}" for item in classification.get("weak_only_capabilities", [])],
        ],
        "selected_owners": owners,
        "planned_actions": actions,
        "declared_state_changes": [change for action in actions for change in action.get("state_changes", [])],
        "approval": {"required": True, "scope": "proposal_id_and_input_signature", "auto_apply": False},
        "rollback_owners": sorted({str(item.get("rollback_owner") or "") for item in contracts if item.get("rollback_owner")}),
        "recovery_evidence_required": True,
        "required_post_change_probes": sorted({str(item) for contract in contracts for item in contract.get("required_validation", [])}),
        "validated_baseline_advance": "only_after_approved_owner_apply_and_consumed_validation_or_explicit_waiver",
        "created_at": now_iso(),
        "input_signature": sha256_json(proposal_core),
    }
    path = state_root / str(event["profile_id"]) / "proposals" / f"{proposal_id}.json"
    if not path.exists():
        atomic_write_json(path, proposal)
    return {
        "schema": "dependency_change_intelligence.propose.v1",
        "ok": True,
        "status": "existing" if path.exists() and read_json_object(path).get("created_at") != proposal["created_at"] else "created",
        "proposal": read_json_object(path),
        "validated_baseline_advanced": False,
    }


def status(state_root: Path, *, event_id: str = "", pending_only: bool = False) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for path in sorted(state_root.glob("*/events/*.json")):
        payload = read_json_object(path)
        if payload and (not event_id or payload.get("event_id") == event_id):
            events.append(payload)
    for path in sorted(state_root.glob("*/proposals/*.json")):
        payload = read_json_object(path)
        if not payload or (event_id and payload.get("event_id") != event_id):
            continue
        if pending_only and payload.get("status") not in {"proposed", "approved", "failed"}:
            continue
        proposals.append(
            {
                **payload,
                "code": payload.get("proposal_id"),
                "message": payload.get("summary"),
                "severity": "risk" if payload.get("risk") in {"critical", "high", "unknown"} else "warn",
                "next_action": "审阅该具体提案；任何应用动作仍需绑定 proposal_id 和 input_signature 的明确批准。",
                "path": str(path),
            }
        )
    return {
        "schema": "dependency_change_intelligence.status.v1",
        "ok": True,
        "event_count": len(events),
        "proposal_count": len(proposals),
        "events": events[-50:],
        "proposals": proposals[-50:],
        "product_state_read_only": True,
    }


def validate(contract_root: Path = CONTRACT_ROOT) -> dict[str, Any]:
    index, index_issues = load_registry(contract_root)
    profiles: list[dict[str, Any]] = []
    issues = list(index_issues)
    for entry in index.get("profiles", []):
        if not isinstance(entry, dict):
            issues.append("profile_entry_invalid")
            continue
        profile, profile_issues = load_profile(str(entry.get("profile_id") or ""), contract_root)
        profiles.append({"profile_id": entry.get("profile_id"), "ok": not profile_issues, "issues": profile_issues})
        issues.extend(profile_issues)
    sample = {
        "schema": "codex_local_version_fingerprint.v1",
        "captured_at": "volatile-a",
        "components": {"desktop": {"status": "supported", "version": "1.2.3", "duration_ms": 10}},
    }
    changed = {**sample, "captured_at": "volatile-b", "components": {"desktop": {"status": "supported", "version": "1.2.3", "duration_ms": 99}}}
    fingerprint_stable = normalize_fingerprint(sample)["digest"] == normalize_fingerprint(changed)["digest"]
    proposal_status_contract = PROPOSAL_STATUSES == {"proposed", "approved", "rejected", "applied", "validated", "failed", "superseded"}
    return {
        "schema": "dependency_change_intelligence.validate.v1",
        "ok": not issues and fingerprint_stable and proposal_status_contract,
        "registry_profile_count": len(profiles),
        "profiles": profiles,
        "issues": list(dict.fromkeys(issues)),
        "fingerprint_stable_excluding_volatile_fields": fingerprint_stable,
        "proposal_status_contract": proposal_status_contract,
        "commands_are_product_read_only": True,
        "repairs_owned_elsewhere": True,
        "validated_baseline_auto_advance": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only external dependency change intelligence")
    parser.add_argument("--state-root", default="")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest-trigger")
    ingest.add_argument("--profile", required=True)
    ingest.add_argument("--trigger-file", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--profile", required=True)
    scan.add_argument("--trigger", choices=("periodic", "manual"), default="manual")
    scan.add_argument("--plan-only", action="store_true")
    scan.add_argument("--timeout-seconds", type=int, default=90)
    probe = sub.add_parser("probe")
    probe.add_argument("--event-id", required=True)
    propose = sub.add_parser("propose")
    propose.add_argument("--event-id", required=True)
    current = sub.add_parser("status")
    current.add_argument("--event-id", default="")
    current.add_argument("--pending-only", action="store_true")
    sub.add_parser("validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    state_root = state_root_from_arg(args.state_root)
    if args.command == "ingest-trigger":
        payload = ingest_trigger(args.profile, Path(args.trigger_file).expanduser().resolve(), state_root)
    elif args.command == "scan":
        payload = scan_profile(args.profile, args.trigger, state_root, plan_only=args.plan_only, timeout_seconds=max(10, min(args.timeout_seconds, 600)))
    elif args.command == "probe":
        payload = probe_event(args.event_id, state_root)
    elif args.command == "propose":
        payload = propose_event(args.event_id, state_root)
    elif args.command == "status":
        payload = status(state_root, event_id=args.event_id, pending_only=args.pending_only)
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
