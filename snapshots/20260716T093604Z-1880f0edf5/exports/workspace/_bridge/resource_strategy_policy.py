#!/usr/bin/env python3
"""Deterministic strategy policy for resource acquisition.

Ownership: rank resource acquisition attempts, classify retry/fallback
decisions, and validate whether a successful owner-tool result is relevant
enough to satisfy the original request.
Non-goals: fetching resources, calling MCP tools, changing network state,
installing packages, or writing files.
State behavior: pure read-only decisions from caller-supplied request, route,
attempt, and result payloads.
Caller context: `resource_broker.py` uses this module while executing resource
requests and producing receipts.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from intent_routing import matched_terms
from structured_task_envelope import resource_contract_from_metadata


FATAL_ERROR_CLASSES = {
    "ambiguous_reference",
    "filesystem_write_not_allowed",
    "network_not_allowed",
    "package_ecosystem_not_supported_for_auto_owner",
}

RECOVERABLE_ERROR_CLASSES = {
    "insufficient_coverage",
    "low_relevance",
    "minimum_candidates_not_met",
    "no_consumable_content_or_artifact",
    "empty_owner_result",
    "owner_gateway_unavailable",
    "gateway_tool_call_failed",
    "tool_call_response_missing",
    "timeout",
    "TimeoutError",
    "TimeoutExpired",
    "URLError",
    "url_error",
    "ConnectionError",
    "TemporaryFailure",
    "network_error",
    "network_route_unavailable",
    "probe_failed",
    "preview_failed",
    "transport_closed",
    "tool_unbound",
}

TERMINAL_HTTP_STATUSES = {400, 401, 403, 404, 405, 409, 410, 451}
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
MEDIA_HOST_RECOVERABLE_HTTP_STATUSES = {403}
DEFAULT_MIN_SPEED_BYTES_PER_SEC = 1024
DEFAULT_SLOW_WINDOW_SECONDS = 8.0
DEFAULT_LARGE_DOWNLOAD_BYTES = 32 * 1024 * 1024

GENERIC_TERMS = {
    "a",
    "an",
    "and",
    "api",
    "as",
    "by",
    "codex",
    "docs",
    "documentation",
    "fetch",
    "file",
    "find",
    "for",
    "from",
    "get",
    "guide",
    "html",
    "http",
    "https",
    "inspect",
    "library",
    "lookup",
    "look",
    "manual",
    "md",
    "metadata",
    "of",
    "org",
    "page",
    "read",
    "resource",
    "search",
    "the",
    "to",
    "tool",
    "up",
    "url",
    "use",
    "using",
    "version",
    "web",
    "文档",
    "资源",
    "获取",
}


@dataclass(frozen=True)
class RelevanceDecision:
    ok: bool
    score: float
    threshold: float
    matched_terms: tuple[str, ...]
    missing_terms: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "score": self.score,
            "threshold": self.threshold,
            "matched_terms": list(self.matched_terms),
            "missing_terms": list(self.missing_terms),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SufficiencyDecision:
    ok: bool
    required_source_count: int
    actual_source_count: int
    sources: tuple[str, ...]
    reason: str
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "required_source_count": self.required_source_count,
            "actual_source_count": self.actual_source_count,
            "sources": list(self.sources),
            "reason": self.reason,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class ResourceSatisfactionDecision:
    satisfied: bool
    result_kind: str
    reason: str
    next_action: str
    relevance: dict[str, Any]
    sufficiency: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "resource_satisfaction.v1",
            "satisfied": self.satisfied,
            "result_kind": self.result_kind,
            "reason": self.reason,
            "next_action": self.next_action,
            "relevance": self.relevance,
            "sufficiency": self.sufficiency,
        }


@dataclass(frozen=True)
class ResourceRecoveryDecision:
    failure_class: str
    recoverable: bool
    retry_allowed: bool
    fallback_allowed: bool
    terminal: bool
    next_action: str
    reason: str
    retry_after_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "resource_strategy.recovery_decision.v1",
            "failure_class": self.failure_class,
            "recoverable": self.recoverable,
            "retry_allowed": self.retry_allowed,
            "fallback_allowed": self.fallback_allowed,
            "terminal": self.terminal,
            "next_action": self.next_action,
            "reason": self.reason,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass(frozen=True)
class DownloadHealth:
    bytes_read: int
    elapsed_seconds: float
    bytes_per_second: float
    slow: bool
    min_speed_bytes_per_sec: int
    slow_window_seconds: float
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "resource_strategy.download_health.v1",
            "bytes_read": self.bytes_read,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "bytes_per_second": round(self.bytes_per_second, 3),
            "slow": self.slow,
            "min_speed_bytes_per_sec": self.min_speed_bytes_per_sec,
            "slow_window_seconds": self.slow_window_seconds,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class DownloadBackendDecision:
    backend: str
    reason: str
    explicit: bool
    available: bool
    resume: bool
    expected_large: bool
    background_candidate: bool
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "resource_strategy.download_backend_decision.v1",
            "backend": self.backend,
            "reason": self.reason,
            "explicit": self.explicit,
            "available": self.available,
            "resume": self.resume,
            "expected_large": self.expected_large,
            "background_candidate": self.background_candidate,
            "next_action": self.next_action,
        }


def _text_from_request(request: dict[str, Any]) -> str:
    return " ".join(str(request.get(key) or "") for key in ("task", "target", "url", "name")).strip()


def _url_terms(url: str) -> list[str]:
    if not url:
        return []
    parsed = urllib.parse.urlparse(url)
    pieces = [parsed.netloc, parsed.path]
    return _tokenize(" ".join(pieces).replace(".", " ").replace("/", " "))


def _tokenize(text: str) -> list[str]:
    tokens = [item.lower() for item in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{1,}|[\u4e00-\u9fff]{2,}", text)]
    filtered: list[str] = []
    for token in tokens:
        token = token.strip("_-+")
        if len(token) < 2 or token in GENERIC_TERMS:
            continue
        if token.isdigit():
            continue
        filtered.append(token)
    return filtered


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def download_backend_decision(
    *,
    metadata: dict[str, Any] | None = None,
    availability: dict[str, Any] | None = None,
    max_bytes: int | None = None,
    existing_partial_bytes: int = 0,
) -> DownloadBackendDecision:
    """Choose an optional process-backed URL download backend.

    Empty backend means the caller should keep its built-in HTTP path. This
    function never installs tools and never changes network state.
    """

    data = metadata or {}
    available = availability or {}
    backend = str(data.get("download_backend") or "").strip().lower()
    if backend not in {"", "auto", "curl", "aria2"}:
        return DownloadBackendDecision(
            backend="",
            reason="unsupported_backend_value",
            explicit=True,
            available=False,
            resume=False,
            expected_large=False,
            background_candidate=False,
            next_action="fix_resource_request_download_backend",
        )

    resume = _bool_value(data.get("resume_download"), False) or existing_partial_bytes > 0
    expected_size = _int_value(data.get("expected_bytes") or data.get("content_length"), 0)
    budget = max(0, int(max_bytes or 0), expected_size)
    expected_large = bool(budget >= DEFAULT_LARGE_DOWNLOAD_BYTES)
    curl_available = bool(available.get("curl_path") or available.get("curl_available"))
    aria2_available = bool(available.get("aria2c_path") or available.get("aria2c_available"))

    if backend in {"curl", "aria2"}:
        is_available = curl_available if backend == "curl" else aria2_available
        return DownloadBackendDecision(
            backend=backend,
            reason="explicit_backend_requested",
            explicit=True,
            available=is_available,
            resume=resume or backend == "aria2",
            expected_large=expected_large,
            background_candidate=expected_large or backend == "aria2",
            next_action="use_selected_backend" if is_available else "request_backend_install_or_choose_available_backend",
        )

    should_use_process_backend = backend == "auto" or resume or expected_large
    if not should_use_process_backend:
        return DownloadBackendDecision(
            backend="",
            reason="builtin_http_path_sufficient",
            explicit=False,
            available=True,
            resume=False,
            expected_large=False,
            background_candidate=False,
            next_action="use_builtin_http_download",
        )

    if aria2_available:
        selected = "aria2"
        reason = "aria2_available_for_resume_or_large_download"
    elif curl_available:
        selected = "curl"
        reason = "curl_available_for_resume_or_large_download"
    else:
        selected = ""
        reason = "no_process_download_backend_available"
    return DownloadBackendDecision(
        backend=selected,
        reason=reason,
        explicit=False,
        available=bool(selected),
        resume=resume,
        expected_large=expected_large,
        background_candidate=expected_large,
        next_action="use_selected_backend" if selected else "use_builtin_http_download_or_request_backend_install",
    )


def request_key_terms(request: dict[str, Any]) -> tuple[str, ...]:
    """Return compact semantic terms that an acquisition result should cover."""

    text_terms = _tokenize(_text_from_request(request))
    url = str(request.get("url") or "").strip()
    terms = [*text_terms, *_url_terms(url)]
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    for key in ("library_id", "library_name", "package", "package_name", "repo", "repository"):
        terms.extend(_tokenize(str(metadata.get(key) or "")))
    return tuple(list(dict.fromkeys(terms))[:12])


def request_requires_multi_source_research(request: dict[str, Any]) -> bool:
    """Return whether a request needs coverage beyond one owner result."""

    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    envelope = resource_contract_from_metadata(metadata)
    resource = envelope.get("resource", {}) if envelope else {}
    source_policy = resource.get("source_policy", {}) if isinstance(resource, dict) else {}
    quality = resource.get("quality", {}) if isinstance(resource, dict) else {}
    if source_policy.get("mode") == "multi_source":
        return True
    if _int_value(quality.get("required_source_count"), 0) > 1:
        return True
    custom = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    explicit = metadata.get("multi_source_required", custom.get("multi_source_required"))
    if isinstance(explicit, bool):
        return explicit
    if _int_value(metadata.get("required_source_count") or metadata.get("min_source_count") or custom.get("required_source_count") or custom.get("min_source_count"), 0) > 1:
        return True
    text = _text_from_request(request).lower()
    broad_terms = (
        "best practice",
        "best practices",
        "comparison",
        "compare",
        "alternatives",
        "survey",
        "overview",
        "multi-source",
        "multi source",
        "projects",
        "mature",
        "相关知识",
        "成熟知识",
        "成熟做法",
        "成熟方案",
        "成熟项目",
        "多源",
        "多个来源",
        "多种",
        "对比",
        "比较",
        "综述",
        "汇总",
        "综合",
        "完善计划",
        "完善方案",
        "辅助设计",
    )
    return bool(matched_terms(text, broad_terms))


def _source_identity(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text)
    if parsed.netloc:
        return parsed.netloc
    return text[:120]


def _result_source_identities(result: dict[str, Any]) -> tuple[str, ...]:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    owner_result = result.get("owner_result") if isinstance(result.get("owner_result"), dict) else {}
    values: list[Any] = []
    for key in ("library_id", "full_name", "html_url", "top_url", "url", "source_url", "repository"):
        values.append(metadata.get(key))
        values.append(result.get(key))
        values.append(owner_result.get(key))
    citations = result.get("citations") if isinstance(result.get("citations"), list) else []
    values.extend(citations)
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    for candidate in candidates:
        if isinstance(candidate, dict):
            for key in ("source_id", "source", "url", "landing_url"):
                values.append(candidate.get(key))
    for container in (metadata, result, owner_result):
        items = container.get("items") if isinstance(container.get("items"), list) else []
        for item in items:
            if isinstance(item, dict):
                for key in ("full_name", "html_url", "url", "name"):
                    values.append(item.get(key))
    identities = [_source_identity(value) for value in values]
    return tuple(item for item in dict.fromkeys(identities) if item)


def _result_candidate_count(result: dict[str, Any]) -> int:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    items = metadata.get("items") if isinstance(metadata.get("items"), list) else []
    usable_candidates = [
        item
        for item in candidates
        if isinstance(item, dict)
        and any(str(item.get(key) or "").strip() for key in ("url", "landing_url", "source_id", "title", "summary", "full_name"))
    ]
    usable_items = [
        item
        for item in items
        if isinstance(item, dict)
        and any(str(item.get(key) or "").strip() for key in ("url", "landing_url", "source_id", "title", "summary", "body", "full_name", "html_url"))
    ]
    count = max(
        _int_value(result.get("candidate_count"), 0),
        _int_value(metadata.get("candidate_count"), 0),
        len(usable_candidates),
        len(usable_items),
    )
    if count == 0 and any(metadata.get(key) for key in ("full_name", "html_url", "top_url", "url", "library_id")):
        return 1
    return count


def _minimum_candidate_count(request: dict[str, Any], result: dict[str, Any]) -> int:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    batch = metadata.get("batch_item_contract") if isinstance(metadata.get("batch_item_contract"), dict) else {}
    acceptance = batch.get("acceptance") if isinstance(batch.get("acceptance"), dict) else {}
    declared_kind = str(result.get("result_kind") or "").strip().lower()
    discovery_result = any(term in declared_kind for term in ("search", "discovery", "source_selection", "candidates"))
    return max(
        0,
        _int_value(acceptance.get("minimum_candidates") or acceptance.get("minimum_quantity"), 0),
        1 if discovery_result else 0,
    )


def owner_result_sufficiency(
    *,
    request: dict[str, Any],
    tool: str,
    result: dict[str, Any],
) -> SufficiencyDecision:
    """Judge whether a relevant owner result has enough source coverage."""

    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    envelope = resource_contract_from_metadata(metadata)
    structured_quality = envelope.get("resource", {}).get("quality", {}) if envelope else {}
    custom = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    required = max(2, _int_value(structured_quality.get("required_source_count") or metadata.get("required_source_count") or metadata.get("min_source_count") or custom.get("required_source_count") or custom.get("min_source_count"), 2))
    if not result.get("ok"):
        return SufficiencyDecision(True, required, 0, (), "owner_result_not_ok", "try_next_route")
    sources = _result_source_identities(result)
    actual = len(sources)
    if not request_requires_multi_source_research(request):
        discovery_kind = any(
            term in str(result.get("result_kind") or "").lower()
            for term in ("search", "discovery", "source_selection", "candidate")
        )
        if discovery_kind and actual == 0:
            return SufficiencyDecision(False, 1, 0, (), "no_valid_source_identity", "refine_resource_delegation_and_retry")
        return SufficiencyDecision(True, 1, actual, sources[:8], "single_source_allowed", "consume_resource")
    if actual >= required:
        return SufficiencyDecision(True, required, actual, sources[:8], "coverage_ok", "consume_resource")
    if tool in {"context7", "microsoftdocs", "github", "playwright", "chrome-devtools", "generic_search"}:
        return SufficiencyDecision(
            False,
            required,
            actual,
            sources[:8],
            "insufficient_coverage",
            "refine_resource_delegation_and_retry",
        )
    return SufficiencyDecision(True, required, actual, sources[:8], "coverage_not_required_for_tool", "consume_resource")


def _content_text(result: dict[str, Any]) -> str:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    owner_result = result.get("owner_result") if isinstance(result.get("owner_result"), dict) else {}
    identity_metadata_keys = {
        "full_name",
        "html_url",
        "library_id",
        "name",
        "package",
        "package_name",
        "top_url",
        "url",
    }
    chunks = [
        str(result.get("content") or ""),
        str(result.get("summary") or ""),
        str(result.get("title") or ""),
        " ".join(str(item) for item in result.get("citations") or []),
        " ".join(
            f"{key} {value}"
            for key, value in metadata.items()
            if key in identity_metadata_keys and isinstance(value, (str, int, float))
        ),
        " ".join(
            f"{key} {value}"
            for key, value in owner_result.items()
            if key in {"content", "summary", "title", "text", "markdown", "url", "artifact_path"}
            and isinstance(value, (str, int, float))
        ),
    ]
    return "\n".join(chunks).lower()


def _json_payload_has_consumable_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_json_payload_has_consumable_value(item) for item in value)
    if isinstance(value, dict):
        result_keys = {"results", "items", "candidates", "documents", "matches", "data", "sources"}
        text_keys = {"content", "text", "body", "summary", "markdown", "snippet", "description"}
        if any(key in value for key in result_keys):
            if any(_json_payload_has_consumable_value(value.get(key)) for key in result_keys if key in value):
                return True
            return any(str(value.get(key) or "").strip() for key in text_keys if key in value)
        return any(_json_payload_has_consumable_value(item) for item in value.values())
    return bool(str(value or "").strip())


def _consumable_content_text(result: dict[str, Any]) -> str:
    """Return user-consumable payload text without counting identity metadata."""

    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    owner_result = result.get("owner_result") if isinstance(result.get("owner_result"), dict) else {}
    values = [
        result.get("content"),
        result.get("text"),
        result.get("markdown"),
        result.get("body"),
        result.get("summary"),
        result.get("preview_text"),
        metadata.get("preview_text"),
        owner_result.get("content"),
        owner_result.get("text"),
        owner_result.get("markdown"),
        owner_result.get("body"),
        owner_result.get("summary"),
    ]
    usable: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text[:1] in {"{", "["}:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None and not _json_payload_has_consumable_value(parsed):
                continue
        usable.append(text)
    return "\n".join(usable)


def _consumable_result_required(request: dict[str, Any]) -> bool:
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    batch_contract = metadata.get("batch_item_contract") if isinstance(metadata.get("batch_item_contract"), dict) else {}
    acceptance = batch_contract.get("acceptance") if isinstance(batch_contract.get("acceptance"), dict) else {}
    return bool(acceptance.get("consumable_required", True))


def _owner_no_result_reason(*, tool: str, content: str) -> str:
    if tool == "context7" and any(
        marker in content
        for marker in (
            "no libraries found",
            "try a different search term",
            "no context7-compatible library",
        )
    ):
        return "owner_no_results"
    if tool == "microsoftdocs" and any(marker in content for marker in ("no results found", "0 results")):
        return "owner_no_results"
    if tool == "github" and any(marker in content for marker in ("total_count 0", "total_count: 0", '"total_count": 0')):
        return "owner_no_results"
    return ""


def owner_result_relevance(
    *,
    request: dict[str, Any],
    tool: str,
    result: dict[str, Any],
    threshold: float | None = None,
) -> RelevanceDecision:
    """Judge whether an owner-tool result is semantically useful enough.

    This is intentionally deterministic and conservative. Owner execution
    success proves that a tool returned something; it does not prove the answer
    matches the requested resource.
    """

    if not result.get("ok"):
        return RelevanceDecision(False, 0.0, threshold or 0.0, (), (), "owner_result_not_ok")
    if tool not in {"context7", "microsoftdocs", "github", "package_manager", "markitdown", "playwright", "chrome-devtools", "generic_search"}:
        return RelevanceDecision(True, 1.0, threshold or 0.0, (), (), "relevance_not_required_for_tool")
    metadata = request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
    custom = metadata.get("custom_delegation") if isinstance(metadata.get("custom_delegation"), dict) else {}
    if threshold is None:
        threshold = _float_value(custom.get("relevance_threshold") or metadata.get("relevance_threshold"), 0.0)
    terms = request_key_terms(request)
    if not terms:
        return RelevanceDecision(True, 1.0, threshold or 0.0, (), (), "no_specific_terms_required")
    content = _content_text(result)
    no_result_reason = _owner_no_result_reason(tool=tool, content=content)
    if no_result_reason:
        return RelevanceDecision(False, 0.0, threshold or 0.0, (), terms[:8], no_result_reason)
    matched = tuple(term for term in terms if term in content)
    denominator = max(1, min(len(terms), 6))
    score = min(1.0, len(matched) / denominator)

    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    library_id = str(metadata.get("library_id") or "").lower()
    if tool == "context7" and library_id:
        if any(term in library_id for term in terms):
            score = max(score, 0.5)
    if tool == "github":
        full_name = str(metadata.get("full_name") or "").lower()
        if any(term in full_name for term in terms):
            score = max(score, 0.75)
    if tool == "package_manager":
        package_name = str(metadata.get("package") or metadata.get("name") or "").lower()
        if any(term == package_name or term in package_name for term in terms):
            score = max(score, 0.8)

    required_threshold = 0.25 if threshold is None else threshold
    if tool in {"context7", "microsoftdocs"}:
        required_threshold = max(required_threshold, 0.34)
        if str(request.get("url") or "").strip():
            required_threshold = max(required_threshold, 0.67)
    missing = tuple(term for term in terms if term not in matched)
    ok = score >= required_threshold
    return RelevanceDecision(
        ok=ok,
        score=round(score, 3),
        threshold=round(required_threshold, 3),
        matched_terms=matched[:8],
        missing_terms=missing[:8],
        reason="relevance_ok" if ok else "low_relevance",
    )


def resource_result_satisfaction(*, request: dict[str, Any], tool: str, result: dict[str, Any]) -> ResourceSatisfactionDecision:
    """Decide whether one result satisfies the resource need, not merely whether the tool ran."""
    if not result.get("ok"):
        return ResourceSatisfactionDecision(False, "none", str(result.get("reason") or result.get("error_class") or "result_not_ok"), "try_next_route", {}, {})
    minimum_candidates = _minimum_candidate_count(request, result)
    if _result_candidate_count(result) < minimum_candidates:
        return ResourceSatisfactionDecision(
            False,
            "metadata",
            "minimum_candidates_not_met",
            "refine_resource_delegation_and_retry",
            {},
            {},
        )
    relevance = owner_result_relevance(request=request, tool=tool, result=result)
    if not relevance.ok:
        return ResourceSatisfactionDecision(False, "metadata", relevance.reason, "try_next_route", relevance.to_dict(), {})
    sufficiency = owner_result_sufficiency(request=request, tool=tool, result=result)
    if not sufficiency.ok:
        return ResourceSatisfactionDecision(False, "metadata", sufficiency.reason, sufficiency.next_action, relevance.to_dict(), sufficiency.to_dict())
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    artifact = str(result.get("stored_path") or result.get("local_path") or result.get("artifact_path") or "")
    content = _consumable_content_text(result).strip()
    has_preview = bool(metadata.get("preview_text") or result.get("preview_text"))
    declared_kind = str(result.get("result_kind") or "").strip().lower()
    metadata_only = declared_kind in {"metadata", "classification", "classified_by_policy"} or str(result.get("reason") or "") == "classified_by_policy"
    consumable = bool(artifact or has_preview or content) and not metadata_only
    if not consumable and _consumable_result_required(request):
        return ResourceSatisfactionDecision(False, "metadata", "no_consumable_content_or_artifact", "try_next_route", relevance.to_dict(), sufficiency.to_dict())
    if not consumable:
        return ResourceSatisfactionDecision(True, "metadata", "metadata_acceptance_predicate_satisfied", "consume_resource", relevance.to_dict(), sufficiency.to_dict())
    result_kind = "artifact" if artifact else ("preview" if has_preview else (declared_kind or "content"))
    return ResourceSatisfactionDecision(True, result_kind, "completion_predicate_satisfied", "consume_resource", relevance.to_dict(), sufficiency.to_dict())


def classify_attempt_outcome(attempt: dict[str, Any]) -> dict[str, Any]:
    error_class = str(attempt.get("error_class") or "")
    status = str(attempt.get("status") or "")
    if status in {"completed"} and (attempt.get("result") or {}).get("ok"):
        return {"class": "success", "terminal": True, "retryable": False, "fallback_allowed": False}
    if status == "handoff_required":
        return {"class": "handoff", "terminal": False, "retryable": False, "fallback_allowed": True}
    if error_class in FATAL_ERROR_CLASSES:
        return {"class": "policy_block", "terminal": True, "retryable": False, "fallback_allowed": False}
    if error_class in RECOVERABLE_ERROR_CLASSES or status in {"degraded", "deferred", "skipped"}:
        return {"class": "recoverable", "terminal": False, "retryable": True, "fallback_allowed": True}
    if status == "failed":
        return {"class": "failed", "terminal": True, "retryable": False, "fallback_allowed": False}
    return {"class": "unknown", "terminal": False, "retryable": False, "fallback_allowed": True}


def recovery_decision_for_error(metadata: dict[str, Any] | None) -> ResourceRecoveryDecision:
    """Classify common resource acquisition failures into an action policy."""

    data = metadata or {}
    error_type = str(data.get("error_type") or data.get("error_class") or "")
    http_status = _int_value(data.get("http_status"), 0)
    retry_after = _float_value(data.get("retry_after_seconds"), 0.0)
    url = str(data.get("url") or data.get("final_url") or "")
    host = urllib.parse.urlparse(url).netloc.lower()
    if error_type == "http_status":
        if http_status in MEDIA_HOST_RECOVERABLE_HTTP_STATUSES and host in {"upload.wikimedia.org", "commons.wikimedia.org"}:
            return ResourceRecoveryDecision(
                failure_class="media_http_recoverable",
                recoverable=True,
                retry_allowed=True,
                fallback_allowed=True,
                terminal=False,
                next_action="retry_via_media_source_api_or_next_candidate",
                reason=f"http_status={http_status};host={host}",
                retry_after_seconds=retry_after,
            )
        if http_status in TRANSIENT_HTTP_STATUSES:
            return ResourceRecoveryDecision(
                failure_class="http_transient",
                recoverable=True,
                retry_allowed=True,
                fallback_allowed=True,
                terminal=False,
                next_action="retry_with_backoff_or_try_next_route",
                reason=f"http_status={http_status}",
                retry_after_seconds=retry_after,
            )
        if http_status in TERMINAL_HTTP_STATUSES or 400 <= http_status < 500:
            return ResourceRecoveryDecision(
                failure_class="http_terminal",
                recoverable=False,
                retry_allowed=False,
                fallback_allowed=False,
                terminal=True,
                next_action="surface_terminal_http_status",
                reason=f"http_status={http_status}",
            )
    if error_type in {"timeout", "network_error", "probe_failed", "preview_failed", "network_route_unavailable"}:
        return ResourceRecoveryDecision(
            failure_class="target_unreachable" if error_type != "timeout" else "timeout",
            recoverable=True,
            retry_allowed=True,
            fallback_allowed=True,
            terminal=False,
            next_action="retry_with_route_fallback",
            reason=error_type,
        )
    if error_type == "total_budget_exhausted":
        return ResourceRecoveryDecision(
            failure_class="total_budget_exhausted",
            recoverable=False,
            retry_allowed=False,
            fallback_allowed=False,
            terminal=True,
            next_action="narrow_request_or_raise_total_budget",
            reason=error_type,
        )
    if error_type in {"sha256_mismatch", "content_mismatch"}:
        return ResourceRecoveryDecision(
            failure_class=error_type,
            recoverable=True,
            retry_allowed=True,
            fallback_allowed=True,
            terminal=False,
            next_action="discard_partial_and_retry_from_alternate_route",
            reason=error_type,
        )
    if error_type in {"content_length_too_large", "too_large"}:
        return ResourceRecoveryDecision(
            failure_class="size_policy_block",
            recoverable=False,
            retry_allowed=False,
            fallback_allowed=False,
            terminal=True,
            next_action="request_larger_size_budget_or_materialization_approval",
            reason=error_type,
        )
    if error_type in {"unsupported_url_scheme", "missing_url", "missing_host"}:
        return ResourceRecoveryDecision(
            failure_class="request_invalid",
            recoverable=False,
            retry_allowed=False,
            fallback_allowed=False,
            terminal=True,
            next_action="fix_resource_request",
            reason=error_type,
        )
    if error_type == "backend_unavailable":
        return ResourceRecoveryDecision(
            failure_class="backend_unavailable",
            recoverable=False,
            retry_allowed=False,
            fallback_allowed=False,
            terminal=True,
            next_action="request_backend_install_or_choose_available_backend",
            reason=error_type,
        )
    return ResourceRecoveryDecision(
        failure_class=error_type or "unknown_failure",
        recoverable=False,
        retry_allowed=False,
        fallback_allowed=False,
        terminal=bool(error_type),
        next_action="inspect_error_and_retry_or_escalate",
        reason=error_type or "no_error_type",
    )


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def recovery_decision_for_attempt(attempt: dict[str, Any]) -> ResourceRecoveryDecision:
    """Return the canonical recovery policy for a broker attempt.

    Broker attempts can come from URL fetches, owner MCP adapters, package
    metadata probes, or explicit handoff contracts. The caller should not need
    to know each executor's private error shape before deciding whether the
    next strategy step is allowed.
    """

    result = _first_mapping(attempt.get("result"))
    metadata = _first_mapping(result.get("metadata"))
    embedded_strategy = _first_mapping(metadata.get("resource_strategy"), result.get("resource_strategy"))
    if embedded_strategy:
        return ResourceRecoveryDecision(
            failure_class=str(embedded_strategy.get("failure_class") or "unknown_failure"),
            recoverable=bool(embedded_strategy.get("recoverable")),
            retry_allowed=bool(embedded_strategy.get("retry_allowed")),
            fallback_allowed=bool(embedded_strategy.get("fallback_allowed")),
            terminal=bool(embedded_strategy.get("terminal")),
            next_action=str(embedded_strategy.get("next_action") or "inspect_error_and_retry_or_escalate"),
            reason=str(embedded_strategy.get("reason") or ""),
            retry_after_seconds=_float_value(embedded_strategy.get("retry_after_seconds"), 0.0),
        )

    status = str(attempt.get("status") or result.get("status") or "")
    error_class = str(attempt.get("error_class") or result.get("error_class") or result.get("reason") or "")
    reason = str(attempt.get("reason") or result.get("reason") or result.get("error") or "")

    if status == "completed" and result.get("ok"):
        return ResourceRecoveryDecision(
            failure_class="success",
            recoverable=False,
            retry_allowed=False,
            fallback_allowed=False,
            terminal=True,
            next_action="consume_resource",
            reason="attempt_completed",
        )
    if status == "handoff_required":
        return ResourceRecoveryDecision(
            failure_class="handoff_required",
            recoverable=True,
            retry_allowed=False,
            fallback_allowed=True,
            terminal=False,
            next_action=str(attempt.get("next_action") or result.get("next_action") or "execute_owner_call_then_attach_result"),
            reason=reason or error_class or "owner_tool_handoff_required",
        )
    if error_class in FATAL_ERROR_CLASSES:
        return ResourceRecoveryDecision(
            failure_class="policy_block",
            recoverable=False,
            retry_allowed=False,
            fallback_allowed=False,
            terminal=True,
            next_action="surface_policy_blocker",
            reason=error_class,
        )
    if error_class in RECOVERABLE_ERROR_CLASSES or status in {"degraded", "deferred", "skipped"}:
        return ResourceRecoveryDecision(
            failure_class=error_class or status or "recoverable_failure",
            recoverable=True,
            retry_allowed=error_class not in {"low_relevance", "tool_unbound"},
            fallback_allowed=True,
            terminal=False,
            next_action=str(attempt.get("next_action") or result.get("next_action") or "try_next_route"),
            reason=reason or error_class or status,
        )
    if status == "failed":
        return ResourceRecoveryDecision(
            failure_class=error_class or "failed",
            recoverable=False,
            retry_allowed=False,
            fallback_allowed=False,
            terminal=True,
            next_action=str(attempt.get("next_action") or result.get("next_action") or "surface_resource_failure"),
            reason=reason or error_class or "attempt_failed",
        )
    return ResourceRecoveryDecision(
        failure_class=error_class or status or "unknown_failure",
        recoverable=False,
        retry_allowed=False,
        fallback_allowed=True,
        terminal=False,
        next_action=str(attempt.get("next_action") or result.get("next_action") or "inspect_error_and_retry_or_escalate"),
        reason=reason or error_class or status or "unknown_attempt_state",
    )


def should_retry_error(metadata: dict[str, Any] | None, *, attempt_index: int, max_attempts: int) -> bool:
    decision = recovery_decision_for_error(metadata)
    return bool(decision.retry_allowed and attempt_index < max_attempts)


def download_health_for(
    *,
    bytes_read: int,
    elapsed_seconds: float,
    metadata: dict[str, Any] | None = None,
) -> DownloadHealth:
    data = metadata or {}
    min_speed = max(0, _int_value(data.get("min_speed_bytes_per_sec"), DEFAULT_MIN_SPEED_BYTES_PER_SEC))
    slow_window = max(0.0, _float_value(data.get("slow_window_seconds"), DEFAULT_SLOW_WINDOW_SECONDS))
    elapsed = max(0.0, float(elapsed_seconds))
    speed = float(bytes_read) / elapsed if elapsed > 0 else float(bytes_read)
    slow = bool(bytes_read > 0 and elapsed >= slow_window and min_speed > 0 and speed < min_speed)
    return DownloadHealth(
        bytes_read=max(0, int(bytes_read)),
        elapsed_seconds=elapsed,
        bytes_per_second=speed,
        slow=slow,
        min_speed_bytes_per_sec=min_speed,
        slow_window_seconds=slow_window,
        next_action="try_faster_route_or_background_download" if slow else "keep_selected_route",
    )


def should_continue_after_attempt(attempt: dict[str, Any], *, need_materialization: bool) -> bool:
    decision = recovery_decision_for_attempt(attempt)
    if decision.failure_class == "success":
        return False
    if decision.failure_class == "policy_block":
        return False
    if attempt.get("status") == "handoff_required" and not need_materialization:
        return True
    return bool(decision.fallback_allowed and not decision.terminal)


def strategy_summary(attempts: list[dict[str, Any]], plan: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [classify_attempt_outcome(attempt) for attempt in attempts]
    recovery_decisions = [recovery_decision_for_attempt(attempt).to_dict() for attempt in attempts]
    fallback_count = sum(1 for outcome in outcomes if outcome.get("fallback_allowed"))
    fatal = next((attempt for attempt, outcome in zip(attempts, outcomes) if outcome.get("terminal") and outcome.get("class") in {"policy_block", "failed"}), None)
    low_relevance = [attempt for attempt in attempts if attempt.get("error_class") == "low_relevance"]
    insufficient_coverage = [attempt for attempt in attempts if attempt.get("error_class") == "insufficient_coverage"]
    slow_downloads = [
        attempt
        for attempt in attempts
        if (((attempt.get("result") or {}).get("metadata") or {}).get("download_health") or {}).get("slow")
    ]
    return {
        "schema": "resource_strategy.summary.v1",
        "plan_count": len(plan),
        "attempt_count": len(attempts),
        "fallback_eligible_count": fallback_count,
        "recoverable_count": sum(1 for decision in recovery_decisions if decision.get("recoverable")),
        "retry_allowed_count": sum(1 for decision in recovery_decisions if decision.get("retry_allowed")),
        "slow_download_count": len(slow_downloads),
        "low_relevance_count": len(low_relevance),
        "insufficient_coverage_count": len(insufficient_coverage),
        "terminal_error_class": str((fatal or {}).get("error_class") or ""),
        "last_attempt_status": str((attempts[-1] if attempts else {}).get("status") or ""),
        "last_recovery_decision": recovery_decisions[-1] if recovery_decisions else {},
    }


def validate() -> dict[str, Any]:
    good = owner_result_relevance(
        request={"task": "python json docs", "url": "https://docs.python.org/3/library/json.html"},
        tool="context7",
        result={"ok": True, "content": "Python json module documentation", "metadata": {"library_id": "/python/cpython"}},
    )
    bad = owner_result_relevance(
        request={"task": "python json docs", "url": "https://docs.python.org/3/library/json.html"},
        tool="context7",
        result={"ok": True, "content": "jOOQ SQL builder manual", "metadata": {"library_id": "/jooq/jooq"}},
    )
    handoff = recovery_decision_for_attempt({"status": "handoff_required", "error_class": "handoff_required_for_owner_tool"})
    low_relevance = recovery_decision_for_attempt({"status": "degraded", "error_class": "low_relevance"})
    insufficient_coverage = recovery_decision_for_attempt({"status": "degraded", "error_class": "insufficient_coverage"})
    policy_block = recovery_decision_for_attempt({"status": "blocked", "error_class": "filesystem_write_not_allowed"})
    narrow = owner_result_sufficiency(
        request={"task": "research mature workflow routing best practices", "metadata": {"required_source_count": 2}},
        tool="context7",
        result={"ok": True, "content": "workflow docs", "metadata": {"library_id": "/example/workflow"}},
    )
    simple_docs = owner_result_sufficiency(
        request={"task": "python json docs"},
        tool="context7",
        result={"ok": True, "content": "Python json documentation", "metadata": {"library_id": "/python/cpython"}},
    )
    github_multi = owner_result_sufficiency(
        request={"task": "find GitHub projects for local MCP gateway"},
        tool="github",
        result={
            "ok": True,
            "metadata": {
                "items": [
                    {"full_name": "IBM/mcp-context-forge", "html_url": "https://github.com/IBM/mcp-context-forge"},
                    {"full_name": "example/mcp-gateway", "html_url": "https://github.com/example/mcp-gateway"},
                ]
            },
        },
    )
    empty_search = resource_result_satisfaction(
        request={"task": "official policy documentation", "metadata": {"source_domains": ["openpolicyagent.org"]}},
        tool="generic_search",
        result={
            "ok": True,
            "result_kind": "generic_text_search",
            "metadata": {"items": [{"title": "", "url": "", "summary": ""}]},
            "owner_result": {"source_tool": "generic_search", "result_kind": "generic_text_search"},
        },
    )
    backend_auto = download_backend_decision(
        metadata={"download_backend": "auto"},
        availability={"curl_available": True, "aria2c_available": True},
        max_bytes=DEFAULT_LARGE_DOWNLOAD_BYTES,
    )
    backend_builtin = download_backend_decision(metadata={}, availability={"curl_available": True}, max_bytes=1024)
    backend_missing = recovery_decision_for_error({"error_type": "backend_unavailable"})
    media_403 = recovery_decision_for_error({"error_type": "http_status", "http_status": 403, "url": "https://upload.wikimedia.org/example.jpg"})
    ok = (
        good.ok
        and not bad.ok
        and handoff.fallback_allowed
        and low_relevance.fallback_allowed
        and insufficient_coverage.fallback_allowed
        and not narrow.ok
        and simple_docs.ok
        and github_multi.ok
        and not empty_search.satisfied
        and empty_search.reason in {"minimum_candidates_not_met", "no_valid_source_identity", "no_consumable_content_or_artifact"}
        and policy_block.terminal
        and backend_auto.backend == "aria2"
        and backend_builtin.backend == ""
        and backend_missing.terminal
        and media_403.recoverable
        and not media_403.terminal
    )
    return {
        "schema": "resource_strategy_policy.validate.v1",
        "ok": ok,
        "good": good.to_dict(),
        "bad": bad.to_dict(),
        "handoff": handoff.to_dict(),
        "low_relevance": low_relevance.to_dict(),
        "insufficient_coverage": insufficient_coverage.to_dict(),
        "narrow_research": narrow.to_dict(),
        "simple_docs": simple_docs.to_dict(),
        "github_multi": github_multi.to_dict(),
        "empty_search": empty_search.to_dict(),
        "policy_block": policy_block.to_dict(),
        "backend_auto": backend_auto.to_dict(),
        "backend_builtin": backend_builtin.to_dict(),
        "backend_missing": backend_missing.to_dict(),
        "media_403": media_403.to_dict(),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
