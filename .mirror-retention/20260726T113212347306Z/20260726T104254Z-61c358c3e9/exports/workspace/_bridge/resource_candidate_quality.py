#!/usr/bin/env python3
"""Quality scoring and filtering for resource source candidates.

Ownership: reusable candidate-level governance before resource materialization.
Non-goals: network access, downloads, license legal advice, or source-specific
API calls.  This module only annotates, sorts, and filters metadata already
returned by resource source adapters.
"""

from __future__ import annotations

import urllib.parse
from typing import Any


COMMON_DOWNLOAD_EXTENSIONS = {
    "7z",
    "aac",
    "bin",
    "bz2",
    "ckpt",
    "csv",
    "doc",
    "docx",
    "epub",
    "flac",
    "gz",
    "gguf",
    "html",
    "jpeg",
    "jpg",
    "json",
    "jsonl",
    "m4a",
    "md",
    "mp3",
    "mp4",
    "onnx",
    "opus",
    "parquet",
    "pdf",
    "png",
    "ppt",
    "pptx",
    "pt",
    "pth",
    "rar",
    "safetensors",
    "tar",
    "tgz",
    "tsv",
    "txt",
    "wav",
    "webm",
    "xls",
    "xlsx",
    "xml",
    "zip",
}

RESOURCE_KIND_EXTENSIONS = {
    "academic_paper": {"pdf", "html"},
    "audio": {"aac", "flac", "m4a", "mp3", "ogg", "opus", "wav", "wma"},
    "dataset": {"csv", "json", "jsonl", "parquet", "tsv", "txt", "xml", "zip", "gz", "tar", "tgz"},
    "document": {"csv", "doc", "docx", "epub", "html", "md", "pdf", "ppt", "pptx", "txt", "xls", "xlsx"},
    "generic_download": COMMON_DOWNLOAD_EXTENSIONS,
    "github_project": COMMON_DOWNLOAD_EXTENSIONS,
    "image": {"avif", "gif", "jpeg", "jpg", "png", "svg", "webp"},
    "model_artifact": {"bin", "ckpt", "gguf", "onnx", "pt", "pth", "safetensors", "tar", "zip"},
    "video": {"avi", "flv", "m4v", "mkv", "mov", "mp4", "mpeg", "mpg", "webm", "wmv"},
}

SOURCE_TRUST = {
    "academic_arxiv": 0.95,
    "academic_openalex": 0.9,
    "github_release_assets": 0.86,
    "huggingface_files": 0.86,
    "zenodo_files": 0.94,
    "image_wikimedia_commons": 0.9,
    "image_openverse": 0.82,
    "image_webpage_assets": 0.7,
    "webpage_download_assets": 0.68,
}

OPEN_LICENSE_TERMS = {
    "apache",
    "arxiv",
    "bsd",
    "cc-",
    "cc0",
    "cc by",
    "cc-by",
    "gpl",
    "lgpl",
    "mit",
    "mpl",
    "open",
    "public domain",
}

RESTRICTED_LICENSE_TERMS = {
    "all rights reserved",
    "copyright",
    "noncommercial",
    "proprietary",
    "restricted",
}


def extension_from_candidate(candidate: dict[str, Any]) -> str:
    explicit = str(candidate.get("file_type") or "").lower().strip().lstrip(".")
    if explicit and "/" not in explicit and len(explicit) <= 16:
        return explicit
    for key in ("direct_url", "url", "title"):
        value = str(candidate.get(key) or "")
        parsed = urllib.parse.urlparse(value)
        path = urllib.parse.unquote(parsed.path or value).lower().split("?", 1)[0].split("#", 1)[0]
        for segment in reversed([part for part in path.split("/") if part]):
            if segment in {"content", "download", "raw", "resolve"}:
                continue
            if "." not in segment:
                continue
            ext = segment.rsplit(".", 1)[-1].strip()
            if ext and "/" not in ext and len(ext) <= 16:
                return ext
    return ""


def allowed_extensions(resource_kind: str, constraints: dict[str, Any] | None = None) -> set[str]:
    constraints = constraints or {}
    explicit = constraints.get("allowed_extensions") or constraints.get("expected_extensions")
    if isinstance(explicit, str):
        return {item.strip().lower().lstrip(".") for item in explicit.split(",") if item.strip()}
    if isinstance(explicit, list):
        return {str(item).strip().lower().lstrip(".") for item in explicit if str(item).strip()}
    return set(RESOURCE_KIND_EXTENSIONS.get(resource_kind) or COMMON_DOWNLOAD_EXTENSIONS)


def _estimated_size(candidate: dict[str, Any]) -> int:
    for key in ("estimated_size", "size", "size_bytes", "bytes"):
        try:
            value = int(candidate.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def _constraint_list(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list | tuple | set) else [value]
    items: list[str] = []
    for raw in raw_items:
        for part in str(raw or "").split(","):
            text = part.strip().lower()
            if text:
                items.append(text)
    return list(dict.fromkeys(items))


def _candidate_host(candidate: dict[str, Any]) -> str:
    for key in ("direct_url", "url", "landing_url", "source_page"):
        value = str(candidate.get(key) or "").strip()
        if not value:
            continue
        parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
        if parsed.netloc:
            return parsed.netloc.lower()
    return ""


def _host_matches_domain(host: str, domain: str) -> bool:
    host = host.lower().removeprefix("www.")
    domain = domain.lower().removeprefix("www.")
    if not host or not domain:
        return False
    return host == domain or host.endswith(f".{domain}")


def _license_status(candidate: dict[str, Any], constraints: dict[str, Any]) -> tuple[str, str]:
    hint = str(candidate.get("license_hint") or candidate.get("license") or "").strip()
    if bool(candidate.get("open_access")):
        return "open", hint or "open_access"
    lowered = hint.lower()
    if lowered and any(term in lowered for term in RESTRICTED_LICENSE_TERMS):
        return "restricted", hint
    if lowered and any(term in lowered for term in OPEN_LICENSE_TERMS):
        return "open", hint
    if constraints.get("require_open_license") or constraints.get("license_policy") in {"open_only", "open_required"}:
        return "unknown", hint
    return ("unknown" if not hint else "declared", hint)


def quality_constraints_from_request(request: dict[str, Any], *, max_bytes: int | None = None) -> dict[str, Any]:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    custom_delegation = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    custom_constraints = custom_delegation.get("constraints") if isinstance(custom_delegation.get("constraints"), dict) else {}
    constraints: dict[str, Any] = {}
    for key in ("allowed_extensions", "expected_extensions", "require_open_license", "license_policy"):
        if key in metadata:
            constraints[key] = metadata[key]
        if key in custom_constraints:
            constraints[key] = custom_constraints[key]
    preferred_domains = _constraint_list(metadata.get("preferred_domains")) + _constraint_list(custom_constraints.get("site_or_domain"))
    if preferred_domains:
        constraints["preferred_domains"] = list(dict.fromkeys(preferred_domains))
    for key in ("max_bytes", "size_budget_bytes"):
        raw = max_bytes if max_bytes is not None and key == "max_bytes" else metadata.get(key, custom_constraints.get(key, request.get(key)))
        try:
            value = int(raw or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            constraints["max_bytes"] = value
            break
    return constraints


def annotate_candidate(candidate: dict[str, Any], *, resource_kind: str, constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    constraints = constraints or {}
    item = dict(candidate)
    ext = extension_from_candidate(item)
    item["file_type"] = ext
    allowed = allowed_extensions(resource_kind, constraints)
    reasons: list[str] = []
    warnings: list[str] = []
    skip_reasons: list[str] = []

    format_status = "unknown"
    if ext:
        if ext in allowed:
            format_status = "matched"
            reasons.append(f"format:{ext}")
        else:
            format_status = "mismatch"
            skip_reasons.append(f"format_mismatch:{ext}")
    else:
        warnings.append("format_unknown")

    estimated_size = _estimated_size(item)
    max_bytes_value = int(constraints.get("max_bytes") or 0)
    size_status = "unknown"
    if estimated_size > 0:
        if max_bytes_value and estimated_size > max_bytes_value:
            size_status = "over_max"
            skip_reasons.append(f"size_over_max:{estimated_size}>{max_bytes_value}")
        else:
            size_status = "ok"
            reasons.append("size_known")
    elif max_bytes_value:
        warnings.append("size_unknown_under_budget")

    license_status, license_hint = _license_status(item, constraints)
    item["license_hint"] = license_hint
    if license_status == "open":
        reasons.append("license_open")
    elif license_status == "restricted" and (constraints.get("require_open_license") or constraints.get("license_policy") in {"open_only", "open_required"}):
        skip_reasons.append("license_restricted")
    elif license_status == "unknown" and (constraints.get("require_open_license") or constraints.get("license_policy") in {"open_only", "open_required"}):
        warnings.append("license_unknown")

    preferred_domains = _constraint_list(constraints.get("preferred_domains"))
    if preferred_domains:
        host = _candidate_host(item)
        if host and any(_host_matches_domain(host, domain) for domain in preferred_domains):
            reasons.append(f"domain:{host}")
        else:
            skip_reasons.append("domain_mismatch")

    source_id = str(item.get("source_id") or "")
    source_trust = float(SOURCE_TRUST.get(source_id, 0.6))
    raw_score = max(0.0, min(1.0, float(item.get("score") or 0.0)))
    quality_score = (raw_score * 0.62) + (source_trust * 0.2)
    quality_score += 0.08 if format_status == "matched" else 0.0
    quality_score += 0.05 if license_status == "open" else 0.0
    quality_score += 0.03 if estimated_size > 0 else 0.0
    quality_score -= 0.08 * len(warnings)
    quality_score -= 0.5 * len(skip_reasons)

    item.update(
        {
            "quality_score": round(max(0.0, min(1.0, quality_score)), 3),
            "quality_reasons": reasons,
            "quality_warnings": warnings,
            "quality_skip_reasons": skip_reasons,
            "quality_status": "skipped" if skip_reasons else ("warning" if warnings else "ok"),
            "format_status": format_status,
            "size_status": size_status,
            "license_status": license_status,
            "source_trust": round(source_trust, 3),
            "estimated_size": estimated_size,
        }
    )
    return item


def rank_candidates(candidates: list[dict[str, Any]], *, resource_kind: str, constraints: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    annotated = [annotate_candidate(item, resource_kind=resource_kind, constraints=constraints) for item in candidates]
    return sorted(
        annotated,
        key=lambda item: (
            bool(item.get("quality_skip_reasons")),
            -float(item.get("quality_score") or 0.0),
            -float(item.get("score") or 0.0),
            str(item.get("source_id") or ""),
            str(item.get("url") or ""),
        ),
    )


def filter_ranked_candidates(
    candidates: list[dict[str, Any]],
    *,
    resource_kind: str,
    constraints: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked = rank_candidates(candidates, resource_kind=resource_kind, constraints=constraints)
    usable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in ranked:
        if item.get("quality_skip_reasons"):
            skipped.append(item)
        else:
            usable.append(item)
    return usable, skipped


def quality_summary(candidates: list[dict[str, Any]], skipped: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    skipped = skipped or []
    statuses: dict[str, int] = {}
    for item in candidates:
        status = str(item.get("quality_status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "candidate_count": len(candidates),
        "skipped_count": len(skipped),
        "statuses": statuses,
        "top_quality_score": max((float(item.get("quality_score") or 0.0) for item in candidates), default=0.0),
    }


def validate() -> dict[str, Any]:
    good = annotate_candidate(
        {"source_id": "zenodo_files", "url": "https://example.test/data.csv", "score": 0.5, "license_hint": "cc-by-4.0", "estimated_size": 100},
        resource_kind="dataset",
        constraints={"max_bytes": 1000, "require_open_license": True},
    )
    bad = annotate_candidate(
        {"source_id": "webpage_download_assets", "url": "https://example.test/video.mp4", "score": 1.0, "estimated_size": 2000},
        resource_kind="document",
        constraints={"max_bytes": 1000},
    )
    domain_good = annotate_candidate(
        {"source_id": "generic_web_page", "url": "https://platform.openai.com/docs/codex", "score": 0.5, "license_hint": "official_docs"},
        resource_kind="generic_web",
        constraints={"preferred_domains": ["openai.com"]},
    )
    domain_bad = annotate_candidate(
        {"source_id": "generic_web_page", "url": "https://www.envoyproxy.io/docs/", "score": 0.9, "license_hint": "official_docs"},
        resource_kind="generic_web",
        constraints={"preferred_domains": ["openai.com"]},
    )
    return {
        "schema": "resource_candidate_quality.validate.v1",
        "ok": good["quality_status"] == "ok"
        and bool(bad["quality_skip_reasons"])
        and not domain_good["quality_skip_reasons"]
        and "domain_mismatch" in domain_bad["quality_skip_reasons"],
        "good": good,
        "bad": bad,
        "domain_good": domain_good,
        "domain_bad": domain_bad,
        "writes_files": False,
        "writes_remote_state": False,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
