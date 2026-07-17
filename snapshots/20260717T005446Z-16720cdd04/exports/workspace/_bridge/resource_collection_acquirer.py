#!/usr/bin/env python3
"""Collect and materialize multiple resources from source-selection candidates.

Ownership: bridge source discovery to bounded batch materialization for user
requests such as "download N images" or "download N PDFs".  It keeps candidate discovery, retry
backfill, network planning, and receipt reporting inside the resource layer.
Non-goals: ad hoc web search, global proxy mutation, package installation, or
remote writes.
State behavior: writes only normal resource manifests/receipts through the
resource scheduler and broker.
Caller context: `resource_cli.py collect` and regression tests.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from resource_broker import DEFAULT_EVENT_LOG, DEFAULT_RECEIPT_LOG, DEFAULT_STORE_ROOT, ResourceBrokerRequest
from resource_fetcher import ResourceIntent
from resource_library_paths import default_artifact_dir
from resource_scheduler import ResourceBatchConfig, execute_batch
from resource_candidate_quality import filter_ranked_candidates, quality_constraints_from_request, quality_summary
from resource_source_executor import execute_source_selection


def _clean_name(value: str, fallback: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in str(value or "").strip())
    text = " ".join(text.split())
    return (text or fallback)[:120]


def candidate_name(candidate: dict[str, Any], index: int) -> str:
    url = str(candidate.get("direct_url") or candidate.get("url") or "")
    suffix = Path(urllib.parse.urlparse(url).path).suffix
    title = str(candidate.get("title") or "").strip()
    base = Path(urllib.parse.urlparse(url).path).name or title or f"resource-{index}"
    name = _clean_name(base, f"resource-{index}")
    if suffix and not name.lower().endswith(suffix.lower()):
        name = f"{name}{suffix}"
    return name


def _target_dir_for_collection(*, task: str, target: str, target_dir: str) -> Path:
    if target_dir:
        return Path(target_dir).expanduser().resolve()
    return default_artifact_dir(task=task, name=target).expanduser().resolve()


def _source_request(
    *, task: str, target: str, source_page: str, count: int,
    resource_kind: str = "auto", max_bytes: int | None = None,
    source_mode: str = "", source_domains: list[str] | None = None,
    authority: str = "", freshness_mode: str = "", max_age_days: int | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source_selection_only": True,
        "candidate_review_before_materialization": False,
        "requested_count": max(1, count),
    }
    if max_bytes:
        metadata["max_bytes"] = int(max_bytes)
    if resource_kind and resource_kind != "auto":
        metadata["resource_kind_hint"] = resource_kind
    if source_page:
        metadata["source_page"] = source_page
        metadata["source_pages"] = [source_page]
    domains = list(source_domains or [])
    constraints = {
        key: value
        for key, value in {
            "site_or_domain": domains[0] if domains else "",
            "authority": authority,
            "freshness": freshness_mode,
            "max_age_days": max_age_days,
        }.items()
        if value not in ("", None)
    }
    if source_mode or domains or constraints:
        metadata["custom_delegation"] = {
            "schema": "resource_custom_delegation.v1",
            "constraints": constraints,
            "source_mode": source_mode,
            "source_domains": domains,
        }
    if source_mode == "multi_source":
        metadata["multi_source_required"] = True
    return {
        "task": task,
        "target": target,
        "url": source_page,
        "intent": ResourceIntent.EXTERNAL_DEPENDENCY,
        "need_materialization": True,
        "allow_network": True,
        "allow_filesystem_write": False,
        "metadata": metadata,
    }


def materialization_request_for_candidate(
    candidate: dict[str, Any],
    *,
    index: int,
    task: str,
    target_dir: Path,
    timeout: int,
    retries: int,
    max_bytes: int | None,
    download_backend: str,
    resume_download: bool,
) -> ResourceBrokerRequest:
    url = str(candidate.get("direct_url") or candidate.get("url") or "").strip()
    metadata = {
        "source_selection_candidate": candidate,
        "candidate_index": index,
        "candidate_source_id": candidate.get("source_id", ""),
        "license_hint": candidate.get("license_hint", ""),
        "attribution": candidate.get("attribution", ""),
        "download_backend": download_backend,
        "resume_download": bool(resume_download),
    }
    return ResourceBrokerRequest(
        url=url,
        task=task,
        name=candidate_name(candidate, index),
        intent=ResourceIntent.EXPLICIT_USER_URL,
        need_materialization=True,
        allow_network=True,
        allow_filesystem_write=True,
        target_dir=str(target_dir),
        max_bytes=max_bytes,
        timeout_seconds=timeout,
        retry_budget=retries,
        metadata=metadata,
    )


def _successful_artifacts(batch: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for item in batch.get("results") or []:
        if item.get("status") != "completed" or not item.get("artifact_path"):
            continue
        artifacts.append(
            {
                "index": item.get("index"),
                "request_id": item.get("request_id", ""),
                "artifact_path": item.get("artifact_path", ""),
                "sha256": item.get("sha256", ""),
                "manifest_path": item.get("manifest_path", ""),
                "network_summary": item.get("network_summary", {}),
            }
        )
    return artifacts


def _candidate_identity(candidate: dict[str, Any], keys: list[str]) -> tuple[str, ...]:
    identities: list[str] = []
    url = str(candidate.get("direct_url") or candidate.get("url") or "").strip().lower()
    for key in keys or ["canonical_url"]:
        if key in {"canonical_url", "source_url"} and url:
            identities.append(f"url:{url}")
        elif key == "source_id" and candidate.get("source_id"):
            identities.append(f"source:{candidate['source_id']}")
        elif key == "title" and candidate.get("title"):
            identities.append(f"title:{str(candidate['title']).strip().lower()}")
    return tuple(identities or ([f"url:{url}"] if url else []))


def _deduplicate_candidates(candidates: list[dict[str, Any]], keys: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in candidates:
        identities = _candidate_identity(candidate, keys)
        if identities and any(identity in seen for identity in identities):
            skipped.append({**candidate, "quality_skip_reasons": ["structured_uniqueness_duplicate"]})
            continue
        seen.update(identities)
        kept.append(candidate)
    return kept, skipped


def _candidate_host(candidate: dict[str, Any]) -> str:
    url = str(candidate.get("direct_url") or candidate.get("url") or "")
    return (urllib.parse.urlparse(url).hostname or "").lower()


def _candidate_timestamp(candidate: dict[str, Any]) -> datetime | None:
    for key in ("published_at", "updated_at", "created_at", "date", "published"):
        raw = str(candidate.get(key) or "").strip()
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _apply_source_policy(
    candidates: list[dict[str, Any]], *, source_mode: str, source_domains: list[str], max_age_days: int | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    domains = [item.lower().lstrip(".") for item in source_domains if item]
    cutoff = datetime.now(timezone.utc).timestamp() - max(0, int(max_age_days or 0)) * 86400 if max_age_days else None
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in candidates:
        host = _candidate_host(candidate)
        domain_match = not domains or any(host == domain or host.endswith(f".{domain}") for domain in domains)
        if source_mode == "specified_domains" and not domain_match:
            skipped.append({**candidate, "quality_skip_reasons": ["structured_source_domain_mismatch"]})
            continue
        timestamp = _candidate_timestamp(candidate)
        if cutoff and timestamp and timestamp.timestamp() < cutoff:
            skipped.append({**candidate, "quality_skip_reasons": ["structured_freshness_expired"]})
            continue
        kept.append(candidate)
    if domains and source_mode != "specified_domains":
        kept.sort(key=lambda item: 0 if any(_candidate_host(item) == domain or _candidate_host(item).endswith(f".{domain}") for domain in domains) else 1)
    if source_mode == "multi_source":
        by_host: dict[str, list[dict[str, Any]]] = {}
        for candidate in kept:
            by_host.setdefault(_candidate_host(candidate) or "unknown", []).append(candidate)
        interleaved: list[dict[str, Any]] = []
        while any(by_host.values()):
            for host in list(by_host):
                if by_host[host]:
                    interleaved.append(by_host[host].pop(0))
        kept = interleaved
    return kept, skipped


def _accept_unique_artifacts(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]], *, keys: list[str], target_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if "content_hash" not in keys:
        return incoming, []
    seen_hashes = {str(item.get("sha256") or "") for item in existing if item.get("sha256")}
    accepted: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    target_root = target_dir.resolve()
    for item in incoming:
        digest = str(item.get("sha256") or "")
        if not digest or digest not in seen_hashes:
            if digest:
                seen_hashes.add(digest)
            accepted.append(item)
            continue
        artifact = Path(str(item.get("artifact_path") or ""))
        removed = False
        try:
            resolved = artifact.resolve()
            if resolved.is_file() and resolved.is_relative_to(target_root):
                resolved.unlink()
                removed = True
        except OSError:
            removed = False
        duplicates.append({**item, "status": "duplicate_content", "duplicate_key": "content_hash", "duplicate_artifact_removed": removed})
    return accepted, duplicates


def collect_resources(
    *,
    task: str,
    target: str,
    count: int,
    resource_kind: str = "auto",
    source_page: str = "",
    target_dir: str = "",
    candidate_limit: int = 24,
    batch_size: int = 6,
    max_active: int = 4,
    per_host_limit: int = 2,
    timeout: int = 30,
    retries: int = 1,
    max_bytes: int | None = None,
    download_backend: str = "",
    resume_download: bool = False,
    uniqueness_required: bool = False,
    deduplication_keys: list[str] | None = None,
    source_mode: str = "",
    source_domains: list[str] | None = None,
    authority: str = "",
    freshness_mode: str = "",
    max_age_days: int | None = None,
    event_log: Path = DEFAULT_EVENT_LOG,
    receipt_log: Path = DEFAULT_RECEIPT_LOG,
    resource_log: Path | None = None,
    store_root: Path = DEFAULT_STORE_ROOT,
) -> dict[str, Any]:
    requested_count = max(1, int(count))
    request = _source_request(
        task=task, target=target, source_page=source_page, count=requested_count,
        resource_kind=resource_kind, max_bytes=max_bytes, source_mode=source_mode,
        source_domains=source_domains, authority=authority,
        freshness_mode=freshness_mode, max_age_days=max_age_days,
    )
    route = {"primary_tool": "resource_router", "intent": ResourceIntent.EXTERNAL_DEPENDENCY, "source_kind": "unknown"}
    source_result = execute_source_selection(request, route, timeout=max(1, min(timeout, 30)))
    resolved_kind = str(source_result.get("resource_kind") or (resource_kind if resource_kind != "auto" else "generic_download"))
    candidates = list(source_result.get("candidates") or [])[: max(requested_count, candidate_limit)]
    usable_by_quality, skipped_by_quality = filter_ranked_candidates(
        candidates,
        resource_kind=resolved_kind,
        constraints=quality_constraints_from_request(request, max_bytes=max_bytes),
    )
    skipped_candidates = list(source_result.get("skipped_candidates") or []) + skipped_by_quality
    usable_candidates = [
        item for item in usable_by_quality
        if str(item.get("direct_url") or item.get("url") or "").startswith(("http://", "https://"))
    ]
    usable_candidates, policy_skipped = _apply_source_policy(
        usable_candidates,
        source_mode=source_mode,
        source_domains=list(source_domains or []),
        max_age_days=max_age_days,
    )
    skipped_candidates.extend(policy_skipped)
    uniqueness_skipped: list[dict[str, Any]] = []
    if uniqueness_required:
        usable_candidates, uniqueness_skipped = _deduplicate_candidates(usable_candidates, list(deduplication_keys or []))
        skipped_candidates.extend(uniqueness_skipped)
    out_dir = _target_dir_for_collection(task=task, target=target, target_dir=target_dir)
    successes: list[dict[str, Any]] = []
    failed_candidates: list[dict[str, Any]] = []
    batch_manifests: list[str] = []
    cursor = 0
    round_index = 0
    while len(successes) < requested_count and cursor < len(usable_candidates):
        remaining = requested_count - len(successes)
        take = max(1, min(max(1, batch_size), remaining))
        window = usable_candidates[cursor: cursor + take]
        cursor += len(window)
        round_index += 1
        requests = [
            materialization_request_for_candidate(
                candidate,
                index=cursor - len(window) + offset,
                task=task,
                target_dir=out_dir,
                timeout=timeout,
                retries=retries,
                max_bytes=max_bytes,
                download_backend=download_backend,
                resume_download=resume_download,
            )
            for offset, candidate in enumerate(window, start=1)
        ]
        if not requests:
            break
        batch = execute_batch(
            requests,
            config=ResourceBatchConfig(max_active=max_active, per_host_limit=per_host_limit),
            event_log=event_log,
            receipt_log=receipt_log,
            resource_log=resource_log,
            store_root=store_root,
        )
        if batch.get("manifest_path"):
            batch_manifests.append(str(batch["manifest_path"]))
        round_successes = _successful_artifacts(batch)
        accepted_successes, duplicate_artifacts = _accept_unique_artifacts(
            successes,
            round_successes,
            keys=list(deduplication_keys or []) if uniqueness_required else [],
            target_dir=out_dir,
        )
        successes.extend(accepted_successes[: max(0, remaining)])
        failed_candidates.extend(duplicate_artifacts)
        for item in batch.get("results") or []:
            if item.get("status") == "completed":
                continue
            failed_candidates.append(
                {
                    "round": round_index,
                    "index": item.get("index"),
                    "status": item.get("status", ""),
                    "error_class": item.get("error_class", ""),
                    "next_action": item.get("next_action", ""),
                    "request_id": item.get("request_id", ""),
                }
            )
    status = "completed" if len(successes) >= requested_count else ("partial" if successes else "failed")
    return {
        "schema": "resource_collection.result.v1",
        "ok": status == "completed",
        "status": status,
        "task": task,
        "target": target,
        "requested_count": requested_count,
        "resource_kind": resolved_kind,
        "completed_count": len(successes),
        "candidate_count": len(candidates),
        "usable_candidate_count": len(usable_candidates),
        "skipped_candidate_count": len(skipped_candidates),
        "attempted_candidate_count": cursor,
        "target_dir": str(out_dir),
        "source_selection": {
            "ok": bool(source_result.get("ok")),
            "status": source_result.get("status", ""),
            "query": source_result.get("query", ""),
            "selected_source_id": source_result.get("selected_source_id", ""),
            "attempted_sources": source_result.get("attempted_sources", []),
        },
        "artifacts": successes[:requested_count],
        "failed_candidates": failed_candidates,
        "skipped_candidates": [
            {
                "source_id": item.get("source_id", ""),
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "quality_skip_reasons": item.get("quality_skip_reasons", []),
                "quality_warnings": item.get("quality_warnings", []),
                "quality_score": item.get("quality_score", 0),
            }
            for item in skipped_candidates[:20]
        ],
        "quality_summary": quality_summary(usable_by_quality, skipped_candidates),
        "structured_execution": {
            "quantity_applied": requested_count,
            "uniqueness_applied": bool(uniqueness_required),
            "deduplication_keys": list(deduplication_keys or []),
            "source_mode": source_mode,
            "source_domains": list(source_domains or []),
            "authority": authority,
            "freshness_mode": freshness_mode,
            "max_age_days": max_age_days,
        },
        "batch_manifests": batch_manifests,
        "next_action": "consume_artifacts" if status == "completed" else "refine_request_or_add_source_page",
        "writes_remote_state": False,
        "writes_global_network_state": False,
    }


def validate() -> dict[str, Any]:
    source_request = _source_request(task="下载两张测试图片", target="test images", source_page="http://example.test/page.html", count=2, resource_kind="image")
    document_request = _source_request(task="下载两份 PDF 手册", target="manual pdf", source_page="http://example.test/page.html", count=2, resource_kind="document")
    route = {"primary_tool": "resource_router", "intent": ResourceIntent.EXTERNAL_DEPENDENCY, "source_kind": "unknown"}
    return {
        "schema": "resource_collection.validate.v1",
        "ok": (
            route.get("primary_tool") == "resource_router"
            and source_request["metadata"]["resource_kind_hint"] == "image"
            and document_request["metadata"]["resource_kind_hint"] == "document"
        ),
        "route": route,
        "writes_remote_state": False,
        "writes_global_network_state": False,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
