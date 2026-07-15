#!/usr/bin/env python3
"""Shared resource acquisition helpers for local bridge workflows."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from network_policy import recommendation_for_target
from resource_download_backends import availability as download_backend_availability
from resource_download_backends import run_backend_download
from resource_network_execution import url_attempt_specs_from_gateway_plan
from resource_strategy_policy import download_backend_decision, download_health_for, recovery_decision_for_error, should_retry_error


DEFAULT_CHUNK_BYTES = 1024 * 1024
DEFAULT_POLICY_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_PREVIEW_BYTES = 8192


class ResourceStage:
    DISCOVER = "discover"
    PROBE = "probe"
    PREVIEW = "preview"
    MATERIALIZE = "materialize"
    AUDIT = "audit"


class ResourceIntent:
    EXPLICIT_ATTACHMENT = "explicit_attachment"
    EXPLICIT_LOCAL_FILE = "explicit_local_file"
    EXPLICIT_USER_URL = "explicit_user_url"
    INLINE_URL_CANDIDATE = "inline_url_candidate"
    EXTERNAL_DEPENDENCY = "external_dependency"
    PACKAGE_DEPENDENCY = "package_dependency"
    DOCUMENTATION_LOOKUP = "documentation_lookup"
    GENERATED_OUTPUT = "generated_output"
    TOOL_OUTPUT = "tool_output"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ResourceRequest:
    source: str
    target_dir: Path
    name: str = "resource"
    local_path: Path | None = None
    url: str = ""
    expected_sha256: str = ""
    max_bytes: int | None = None
    cache: bool = True
    timeout_seconds: int = 30
    retries: int = 0
    retry_delay_seconds: float = 1.0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResourcePolicy:
    name: str
    intent: str
    allowed_sources: tuple[str, ...] = ("local_file",)
    allowed_schemes: tuple[str, ...] = ()
    max_bytes: int | None = DEFAULT_POLICY_MAX_BYTES
    timeout_seconds: int = 30
    retries: int = 0
    retry_delay_seconds: float = 1.0
    cache: bool = True
    auto_acquire: bool = True
    requires_confirmation: bool = False
    preview_mode: str = "metadata_and_light_preview"
    failure_mode: str = "degrade"
    next_action: str = "materialize_resource"


@dataclass(frozen=True)
class ResourceResult:
    ok: bool
    source: str
    local_path: str = ""
    stored_path: str = ""
    original_local_path: str = ""
    name: str = ""
    sha256: str = ""
    size: int = 0
    cache_hit: bool = False
    error: str = ""
    metadata: dict[str, Any] | None = None
    decision: str = ""
    policy_name: str = ""
    policy_reason: str = ""
    intent: str = ""
    resource_kind: str = ""
    next_action: str = ""
    risk_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceNetworkAttempt:
    route: str
    proxy_url: str
    opener: urllib.request.OpenerDirector


def safe_filename(value: str, fallback: str = "resource") -> str:
    name = Path(str(value or fallback)).name.strip() or fallback
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" .")
    return name[:160] or fallback


def policy_for_intent(intent: str) -> ResourcePolicy:
    value = str(intent or ResourceIntent.UNKNOWN).strip() or ResourceIntent.UNKNOWN
    if value == ResourceIntent.EXPLICIT_ATTACHMENT:
        return ResourcePolicy(
            name="explicit_attachment_v1",
            intent=value,
            allowed_sources=("local_file", "url"),
            allowed_schemes=("http", "https"),
            max_bytes=DEFAULT_POLICY_MAX_BYTES,
            timeout_seconds=30,
            retries=2,
            retry_delay_seconds=1.0,
            next_action="materialize_attachment_and_persist_preview",
        )
    if value == ResourceIntent.EXPLICIT_LOCAL_FILE:
        return ResourcePolicy(
            name="explicit_local_file_v1",
            intent=value,
            allowed_sources=("local_file",),
            max_bytes=DEFAULT_POLICY_MAX_BYTES,
            timeout_seconds=30,
            retries=0,
            next_action="copy_verify_and_preview",
        )
    if value == ResourceIntent.EXPLICIT_USER_URL:
        return ResourcePolicy(
            name="explicit_user_url_v1",
            intent=value,
            allowed_sources=("url",),
            allowed_schemes=("http", "https"),
            max_bytes=50 * 1024 * 1024,
            timeout_seconds=30,
            retries=2,
            retry_delay_seconds=1.0,
            next_action="download_verify_and_preview",
        )
    if value == ResourceIntent.INLINE_URL_CANDIDATE:
        return ResourcePolicy(
            name="inline_url_candidate_v1",
            intent=value,
            allowed_sources=("url",),
            allowed_schemes=("http", "https"),
            max_bytes=10 * 1024 * 1024,
            timeout_seconds=15,
            retries=0,
            auto_acquire=False,
            requires_confirmation=True,
            next_action="ask_for_explicit_confirmation_before_download",
        )
    if value == ResourceIntent.EXTERNAL_DEPENDENCY:
        return ResourcePolicy(
            name="external_dependency_v1",
            intent=value,
            allowed_sources=("url", "local_file", "unknown"),
            allowed_schemes=("http", "https"),
            max_bytes=50 * 1024 * 1024,
            timeout_seconds=30,
            retries=0,
            auto_acquire=False,
            requires_confirmation=True,
            next_action="classify_dependency_and_choose_fetch_route",
        )
    if value == ResourceIntent.PACKAGE_DEPENDENCY:
        return ResourcePolicy(
            name="package_dependency_v1",
            intent=value,
            allowed_sources=("url", "unknown"),
            allowed_schemes=("http", "https"),
            max_bytes=1024 * 1024,
            timeout_seconds=15,
            retries=0,
            auto_acquire=False,
            requires_confirmation=True,
            next_action="use_package_manager_policy_before_install",
        )
    if value == ResourceIntent.DOCUMENTATION_LOOKUP:
        return ResourcePolicy(
            name="documentation_lookup_v1",
            intent=value,
            allowed_sources=("url", "unknown"),
            allowed_schemes=("http", "https"),
            max_bytes=5 * 1024 * 1024,
            timeout_seconds=15,
            retries=0,
            auto_acquire=False,
            requires_confirmation=False,
            next_action="prefer_official_docs_or_mcp_lookup",
        )
    if value in {ResourceIntent.GENERATED_OUTPUT, ResourceIntent.TOOL_OUTPUT}:
        return ResourcePolicy(
            name=f"{value}_v1",
            intent=value,
            allowed_sources=("local_file",),
            max_bytes=DEFAULT_POLICY_MAX_BYTES,
            timeout_seconds=30,
            retries=0,
            next_action="copy_verify_and_preview",
        )
    return ResourcePolicy(
        name="unknown_resource_v1",
        intent=ResourceIntent.UNKNOWN,
        allowed_sources=(),
        auto_acquire=False,
        requires_confirmation=True,
        next_action="classify_resource_intent_before_acquiring",
    )


def resource_kind_for_request(request: ResourceRequest) -> str:
    if request.local_path:
        return "local_file"
    if request.url:
        return "url"
    return "unknown"


def resource_scheme(request: ResourceRequest) -> str:
    if not request.url:
        return ""
    return urllib.parse.urlparse(request.url).scheme.lower()


def content_length_value(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def response_metadata(
    response: Any,
    request: ResourceRequest,
    *,
    method: str,
    stage: str,
    network_attempt: ResourceNetworkAttempt | None = None,
    attempted_routes: list[str] | None = None,
) -> dict[str, Any]:
    headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
    content_length = content_length_value(headers.get("content-length"))
    final_url = str(getattr(response, "url", "") or response.geturl())
    metadata = {
        "url": request.url,
        "final_url": final_url,
        "method": method,
        "stage": stage,
        "http_status": int(getattr(response, "status", 0) or response.getcode()),
        "content_type": headers.get("content-type", ""),
        "content_length": content_length,
        "headers": headers,
        "redirected": bool(final_url and final_url != request.url),
        **network_recommendation_metadata(request),
        **(request.metadata or {}),
    }
    if network_attempt:
        metadata.setdefault("network", {})
        metadata["network"].update(
            {
                "execution_route": network_attempt.route,
                "execution_proxy_present": bool(network_attempt.proxy_url),
                "attempted_routes": attempted_routes or [network_attempt.route],
            }
        )
    return metadata


def network_recommendation_metadata(request: ResourceRequest) -> dict[str, Any]:
    if not request.url:
        return {}
    recommendation = recommendation_for_target(request.url, context="resource_acquisition")
    gateway_plan = (request.metadata or {}).get("network_gateway_plan") if isinstance(request.metadata, dict) else {}
    plan = gateway_plan.get("plan") if isinstance(gateway_plan, dict) and isinstance(gateway_plan.get("plan"), dict) else {}
    next_action = {
        "openai": "if slow or timed out, run network_doctor.py probe <url> and inspect Clash/Mihomo OpenAI strategy group",
        "github": "if slow or timed out, compare direct/proxy with network_doctor.py probe before changing GitHub owner tool",
        "package": "if slow or timed out, compare direct/proxy and package-manager mirror/proxy policy before installing",
        "external": "if slow or timed out, run network_doctor.py probe and keep resource owner permissions unchanged",
        "local": "local resources should remain direct and bypass proxy",
    }.get(recommendation.category, "run network_doctor.py probe for target-specific evidence")
    network = {
        "profile": recommendation.profile,
        "route": recommendation.route,
        "category": recommendation.category,
        "host": recommendation.host,
        "reason": recommendation.reason,
        "health_score": getattr(recommendation, "health_score", 0),
        "retry_budget": getattr(recommendation, "retry_budget", 1),
        "failover_policy": getattr(recommendation, "failover_policy", "none"),
        "observability_tags": list(getattr(recommendation, "observability_tags", ())),
        "proxy_candidate_present": bool(recommendation.proxy_url),
        "warnings": list(recommendation.warnings),
        "next_action": next_action,
        "absorbed_gateway_patterns": [
            "route_health_score",
            "retry_budget",
            "failover_policy",
            "route_observability",
        ],
        "excluded_permission_mechanisms": ["oauth", "rbac", "multi_tenant_auth"],
    }
    if plan:
        network.update(
            {
                "gateway_target_kind": str(plan.get("target_kind") or ""),
                "gateway_route_mode": str(plan.get("route_mode") or ""),
                "gateway_route_reason": str(plan.get("route_reason") or ""),
            }
        )
    return {
        "network": {
            **network,
        }
    }


def opener_for_route(route: str, proxy_url: str = "") -> urllib.request.OpenerDirector:
    if route == "proxy" and proxy_url:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def gateway_network_attempts_for_url(request: ResourceRequest) -> list[ResourceNetworkAttempt] | None:
    """Return gateway-selected attempts when a broker supplied a route plan."""

    metadata = request.metadata or {}
    specs = url_attempt_specs_from_gateway_plan(metadata.get("network_gateway_plan"))
    if specs is None:
        return None
    return [
        ResourceNetworkAttempt(
            route=spec["route"],
            proxy_url=spec["proxy_url"],
            opener=opener_for_route("proxy" if spec["proxy_url"] else "direct", spec["proxy_url"]),
        )
        for spec in specs
    ]


def network_attempts_for_url(request: ResourceRequest) -> list[ResourceNetworkAttempt]:
    gateway_attempts = gateway_network_attempts_for_url(request)
    if gateway_attempts is not None:
        return gateway_attempts
    recommendation = recommendation_for_target(request.url, context="resource_acquisition")
    route_names: list[str]
    if recommendation.route == "proxy_preferred" and recommendation.proxy_url:
        route_names = ["proxy", "direct"]
    elif recommendation.route == "direct":
        route_names = ["direct"]
    elif recommendation.route in {"auto_fastest", "system_or_auto", "direct_with_risk"}:
        route_names = ["direct", "proxy"] if recommendation.proxy_url else ["direct"]
    else:
        route_names = ["direct"]
    attempts: list[ResourceNetworkAttempt] = []
    seen: set[str] = set()
    for route in route_names:
        if route in seen:
            continue
        seen.add(route)
        proxy_url = recommendation.proxy_url if route == "proxy" else ""
        attempts.append(ResourceNetworkAttempt(route=route, proxy_url=proxy_url, opener=opener_for_route(route, proxy_url)))
    return attempts


def opener_for_url(request: ResourceRequest) -> urllib.request.OpenerDirector:
    return network_attempts_for_url(request)[0].opener


def network_error_result(
    request: ResourceRequest,
    *,
    stage: str,
    error: str,
    error_type: str,
    metadata: dict[str, Any] | None = None,
    risk_flags: tuple[str, ...] = (),
) -> ResourceResult:
    combined_metadata = {
        "url": request.url,
        "stage": stage,
        "error_type": error_type,
        **network_recommendation_metadata(request),
        **(metadata or {}),
        **(request.metadata or {}),
    }
    combined_metadata["resource_strategy"] = recovery_decision_for_error(combined_metadata).to_dict()
    return ResourceResult(
        ok=False,
        source=request.source,
        name=request.name,
        error=error,
        metadata=combined_metadata,
        decision="failed",
        policy_reason=error_type,
        resource_kind=resource_kind_for_request(request),
        next_action="surface_resource_failure",
        risk_flags=risk_flags,
    )


def route_unavailable_result(request: ResourceRequest, *, stage: str) -> ResourceResult:
    return network_error_result(
        request,
        stage=stage,
        error="network route unavailable according to gateway plan",
        error_type="network_route_unavailable",
        metadata={"method": "gateway_plan", "attempted_routes": []},
        risk_flags=("network_route_unavailable",),
    )


def validate_http_url_request(request: ResourceRequest, *, stage: str) -> ResourceResult | None:
    if not request.url:
        return network_error_result(request, stage=stage, error="url is required", error_type="missing_url")
    scheme = resource_scheme(request)
    if scheme not in {"http", "https"}:
        return network_error_result(
            request,
            stage=stage,
            error="unsupported_url_scheme",
            error_type="unsupported_url_scheme",
            risk_flags=("unsupported_scheme",),
        )
    parsed = urllib.parse.urlparse(request.url)
    if not parsed.netloc:
        return network_error_result(
            request,
            stage=stage,
            error="url host is required",
            error_type="missing_host",
            risk_flags=("missing_host",),
        )
    return None


def http_status_error_result(request: ResourceRequest, exc: urllib.error.HTTPError, *, stage: str, method: str) -> ResourceResult:
    return network_error_result(
        request,
        stage=stage,
        error=f"http_status={exc.code}",
        error_type="http_status",
        metadata={
            "http_status": exc.code,
            "method": method,
            "final_url": getattr(exc, "url", request.url),
        },
        risk_flags=("http_status",),
    )


def url_error_result(request: ResourceRequest, exc: BaseException, *, stage: str, method: str) -> ResourceResult:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, TimeoutError) or isinstance(exc, TimeoutError):
        return network_error_result(
            request,
            stage=stage,
            error=f"timeout={reason}",
            error_type="timeout",
            metadata={"method": method},
            risk_flags=("timeout",),
        )
    return network_error_result(
        request,
        stage=stage,
        error=f"network_error={reason}",
        error_type="network_error",
        metadata={"method": method},
        risk_flags=("network_error",),
    )


def check_content_length_limit(request: ResourceRequest, metadata: dict[str, Any], *, stage: str) -> ResourceResult | None:
    content_length = metadata.get("content_length")
    if request.max_bytes is not None and isinstance(content_length, int) and content_length > request.max_bytes:
        return network_error_result(
            request,
            stage=stage,
            error=f"content_length larger than {request.max_bytes} bytes",
            error_type="content_length_too_large",
            metadata=metadata,
            risk_flags=("too_large",),
        )
    return None


def decision_result(
    request: ResourceRequest,
    policy: ResourcePolicy,
    *,
    decision: str,
    reason: str,
    error: str = "",
    resource_kind: str = "",
    risk_flags: tuple[str, ...] = (),
) -> ResourceResult:
    kind = resource_kind or resource_kind_for_request(request)
    return ResourceResult(
        ok=False,
        source=request.source,
        name=request.name,
        original_local_path=str(request.local_path.expanduser().resolve()) if request.local_path else "",
        error=error or reason,
        metadata={**(request.metadata or {}), "url": request.url} if request.url else (request.metadata or {}),
        decision=decision,
        policy_name=policy.name,
        policy_reason=reason,
        intent=policy.intent,
        resource_kind=kind,
        next_action=policy.next_action,
        risk_flags=risk_flags,
    )


def annotate_result(result: ResourceResult, request: ResourceRequest, policy: ResourcePolicy) -> ResourceResult:
    metadata = {
        "policy_name": policy.name,
        "policy_reason": "allowed_by_policy",
        "intent": policy.intent,
        "resource_kind": resource_kind_for_request(request),
        "next_action": policy.next_action,
        **(result.metadata or {}),
    }
    return ResourceResult(
        **{
            **result.to_dict(),
            "metadata": metadata,
            "decision": result.decision or ("allowed" if result.ok else "failed"),
            "policy_name": policy.name,
            "policy_reason": "allowed_by_policy" if result.ok else (result.error or "acquire_failed"),
            "intent": policy.intent,
            "resource_kind": resource_kind_for_request(request),
            "next_action": result.next_action or (policy.next_action if result.ok else "surface_resource_failure"),
        }
    )


def request_with_policy_defaults(request: ResourceRequest, policy: ResourcePolicy) -> ResourceRequest:
    return ResourceRequest(
        source=request.source,
        target_dir=request.target_dir,
        name=request.name,
        local_path=request.local_path,
        url=request.url,
        expected_sha256=request.expected_sha256,
        max_bytes=request.max_bytes if request.max_bytes is not None else policy.max_bytes,
        cache=request.cache and policy.cache,
        timeout_seconds=policy.timeout_seconds if request.timeout_seconds == 30 else request.timeout_seconds,
        retries=policy.retries if request.retries == 0 else request.retries,
        retry_delay_seconds=policy.retry_delay_seconds if request.retry_delay_seconds == 1.0 else request.retry_delay_seconds,
        metadata={
            "policy_name": policy.name,
            "intent": policy.intent,
            "resource_kind": resource_kind_for_request(request),
            **(request.metadata or {}),
        },
    )


def acquire_resource_with_policy(
    request: ResourceRequest,
    *,
    intent: str = ResourceIntent.UNKNOWN,
    policy: ResourcePolicy | None = None,
    stage: str = ResourceStage.MATERIALIZE,
) -> ResourceResult:
    active_policy = policy or policy_for_intent(intent)
    kind = resource_kind_for_request(request)
    if kind not in active_policy.allowed_sources:
        return decision_result(
            request,
            active_policy,
            decision="blocked",
            reason=f"source_kind_not_allowed:{kind}",
            error="resource source kind is not allowed by policy",
            resource_kind=kind,
            risk_flags=("source_not_allowed",),
        )
    if request.url:
        scheme = resource_scheme(request)
        if scheme not in active_policy.allowed_schemes:
            return decision_result(
                request,
                active_policy,
                decision="blocked",
                reason=f"unsupported_url_scheme:{scheme or '<none>'}",
                error="unsupported_url_scheme",
                resource_kind=kind,
            risk_flags=("unsupported_scheme",),
        )
    if stage == ResourceStage.DISCOVER:
        return ResourceResult(
            ok=True,
            source=request.source,
            name=request.name,
            metadata={
                "url": request.url,
                "stage": stage,
                "policy_name": active_policy.name,
                "intent": active_policy.intent,
                "resource_kind": kind,
                **(request.metadata or {}),
            },
            decision="discovered",
            policy_name=active_policy.name,
            policy_reason="classified_by_policy",
            intent=active_policy.intent,
            resource_kind=kind,
            next_action=active_policy.next_action,
        )
    effective_request = request_with_policy_defaults(request, active_policy)
    if stage == ResourceStage.PROBE:
        if kind != "url":
            return decision_result(
                request,
                active_policy,
                decision="blocked",
                reason=f"stage_requires_url:{stage}",
                error="resource stage requires URL",
                resource_kind=kind,
                risk_flags=("stage_source_mismatch",),
            )
        return annotate_result(probe_url_resource(effective_request), effective_request, active_policy)
    if stage == ResourceStage.PREVIEW:
        if kind != "url":
            return decision_result(
                request,
                active_policy,
                decision="blocked",
                reason=f"stage_requires_url:{stage}",
                error="resource stage requires URL",
                resource_kind=kind,
                risk_flags=("stage_source_mismatch",),
            )
        return annotate_result(preview_url_resource(effective_request), effective_request, active_policy)
    if stage not in {ResourceStage.MATERIALIZE, ResourceStage.AUDIT}:
        return decision_result(
            request,
            active_policy,
            decision="blocked",
            reason=f"unknown_stage:{stage}",
            error="unknown resource stage",
            resource_kind=kind,
            risk_flags=("unknown_stage",),
        )
    if active_policy.requires_confirmation or not active_policy.auto_acquire:
        reason = "policy_requires_confirmation" if active_policy.requires_confirmation else "policy_deferred_no_auto_acquire"
        error = (
            "resource acquisition requires explicit confirmation"
            if active_policy.requires_confirmation
            else "resource acquisition is deferred by policy"
        )
        risk_flags = ("requires_confirmation",) if active_policy.requires_confirmation else ("auto_acquire_disabled",)
        return decision_result(
            request,
            active_policy,
            decision="deferred",
            reason=reason,
            error=error,
            resource_kind=kind,
            risk_flags=risk_flags,
        )
    if stage == ResourceStage.AUDIT:
        if kind == "local_file":
            source_path = request.local_path.expanduser().resolve() if request.local_path else None
            if not source_path or not source_path.exists() or not source_path.is_file():
                return decision_result(
                    request,
                    active_policy,
                    decision="failed",
                    reason="local_file_missing",
                    error="local file does not exist",
                    resource_kind=kind,
                    risk_flags=("missing_local_file",),
                )
        return ResourceResult(
            ok=True,
            source=request.source,
            name=request.name,
            metadata={
                "url": request.url,
                "stage": stage,
                "policy_name": active_policy.name,
                "intent": active_policy.intent,
                "resource_kind": kind,
                **(request.metadata or {}),
            },
            decision="audited",
            policy_name=active_policy.name,
            policy_reason="audit_only",
            intent=active_policy.intent,
            resource_kind=kind,
            next_action=active_policy.next_action,
        )
    if kind == "local_file":
        return annotate_result(acquire_local_resource(effective_request), effective_request, active_policy)
    if kind == "url":
        return annotate_result(acquire_url_resource(effective_request), effective_request, active_policy)
    return decision_result(
        request,
        active_policy,
        decision="blocked",
        reason="unknown_resource_kind",
        error="unknown resource kind",
        resource_kind=kind,
        risk_flags=("unknown_kind",),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(DEFAULT_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target_path(target_dir: Path, name: str, digest: str, suffix_fallback: str = "") -> Path:
    safe_name = safe_filename(name, "resource")
    suffix = Path(safe_name).suffix or suffix_fallback
    stem = safe_filename(Path(safe_name).stem, "resource")
    return target_dir / f"{digest[:16]}-{stem}{suffix}"


def suffix_from_content_type(content_type: str) -> str:
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    return {
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "application/json": ".json",
        "application/ld+json": ".json",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(media_type, "")


def url_resource_name(request: ResourceRequest, metadata: dict[str, Any] | None = None) -> str:
    if request.name and Path(request.name).suffix:
        return request.name
    final_url = str((metadata or {}).get("final_url") or request.url or "")
    parsed = urllib.parse.urlparse(final_url)
    url_name = Path(parsed.path.rstrip("/")).name
    if url_name and Path(url_name).suffix:
        return url_name
    stem = safe_filename(request.name or url_name or parsed.netloc or "resource", "resource")
    suffix = suffix_from_content_type(str((metadata or {}).get("content_type") or "")) or ".html"
    return f"{stem}{suffix}"


def _within_limit(path: Path, max_bytes: int | None) -> tuple[bool, int, str]:
    size = path.stat().st_size
    if max_bytes is not None and size > max_bytes:
        return False, size, f"resource larger than {max_bytes} bytes"
    return True, size, ""


def acquire_local_resource(request: ResourceRequest) -> ResourceResult:
    if not request.local_path:
        return ResourceResult(ok=False, source=request.source, name=request.name, error="local_path is required")
    source_path = request.local_path.expanduser().resolve()
    if not source_path.exists() or not source_path.is_file():
        return ResourceResult(
            ok=False,
            source=request.source,
            name=request.name,
            original_local_path=str(source_path),
            error="local file does not exist",
        )
    ok, size, error = _within_limit(source_path, request.max_bytes)
    if not ok:
        return ResourceResult(
            ok=False,
            source=request.source,
            name=request.name,
            original_local_path=str(source_path),
            size=size,
            error=error,
        )
    digest = sha256_file(source_path)
    if request.expected_sha256 and digest.lower() != request.expected_sha256.lower():
        return ResourceResult(
            ok=False,
            source=request.source,
            name=request.name,
            original_local_path=str(source_path),
            sha256=digest,
            size=size,
            error="sha256 mismatch",
        )
    request.target_dir.mkdir(parents=True, exist_ok=True)
    dest = _target_path(request.target_dir, request.name or source_path.name, digest, source_path.suffix)
    cache_hit = dest.exists()
    if source_path != dest and not cache_hit:
        tmp = dest.with_name(dest.name + f".tmp-{time.time_ns()}")
        shutil.copy2(source_path, tmp)
        tmp.replace(dest)
    return ResourceResult(
        ok=True,
        source=request.source,
        local_path=str(dest),
        stored_path=str(dest),
        original_local_path=str(source_path),
        name=safe_filename(request.name or source_path.name, source_path.name),
        sha256=digest,
        size=size,
        cache_hit=cache_hit,
        metadata=request.metadata or {},
    )


def acquire_bytes_resource(
    *,
    source: str,
    data: bytes,
    target_dir: Path,
    name: str,
    max_bytes: int | None = None,
    expected_sha256: str = "",
    metadata: dict[str, Any] | None = None,
) -> ResourceResult:
    size = len(data)
    if max_bytes is not None and size > max_bytes:
        return ResourceResult(ok=False, source=source, name=name, size=size, error=f"resource larger than {max_bytes} bytes")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        return ResourceResult(ok=False, source=source, name=name, sha256=digest, size=size, error="sha256 mismatch")
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = _target_path(target_dir, name, digest, Path(name).suffix)
    cache_hit = dest.exists()
    if not cache_hit:
        tmp = dest.with_name(dest.name + f".tmp-{time.time_ns()}")
        tmp.write_bytes(data)
        tmp.replace(dest)
    return ResourceResult(
        ok=True,
        source=source,
        local_path=str(dest),
        stored_path=str(dest),
        name=safe_filename(name, "resource"),
        sha256=digest,
        size=size,
        cache_hit=cache_hit,
        metadata=metadata or {},
    )


def probe_url_resource(request: ResourceRequest) -> ResourceResult:
    invalid = validate_http_url_request(request, stage=ResourceStage.PROBE)
    if invalid:
        return invalid
    attempts = network_attempts_for_url(request)
    attempted_routes = [attempt.route for attempt in attempts]
    if not attempts:
        return route_unavailable_result(request, stage=ResourceStage.PROBE)
    last_error: ResourceResult | None = None
    for method in ("HEAD", "GET"):
        head_not_allowed = False
        for attempt in attempts:
            try:
                req = urllib.request.Request(
                    request.url,
                    method=method,
                    headers={"User-Agent": "codex-resource-fetcher/0.1", "Accept": "*/*"},
                )
                with attempt.opener.open(req, timeout=request.timeout_seconds) as response:
                    metadata = response_metadata(
                        response,
                        request,
                        method=method,
                        stage=ResourceStage.PROBE,
                        network_attempt=attempt,
                        attempted_routes=attempted_routes,
                    )
                    too_large = check_content_length_limit(request, metadata, stage=ResourceStage.PROBE)
                    if too_large:
                        return too_large
                    return ResourceResult(
                        ok=True,
                        source=request.source,
                        name=request.name,
                        metadata=metadata,
                        decision="probed",
                        resource_kind="url",
                        next_action="preview_or_materialize_if_policy_allows",
                    )
            except urllib.error.HTTPError as exc:
                if method == "HEAD" and exc.code in {405, 501}:
                    head_not_allowed = True
                    break
                return http_status_error_result(request, exc, stage=ResourceStage.PROBE, method=method)
            except TimeoutError as exc:
                last_error = url_error_result(request, exc, stage=ResourceStage.PROBE, method=method)
            except urllib.error.URLError as exc:
                last_error = url_error_result(request, exc, stage=ResourceStage.PROBE, method=method)
            except OSError as exc:
                last_error = url_error_result(request, exc, stage=ResourceStage.PROBE, method=method)
        if head_not_allowed:
            continue
    return last_error or network_error_result(request, stage=ResourceStage.PROBE, error="probe_failed", error_type="probe_failed")


def preview_url_resource(request: ResourceRequest, *, preview_bytes: int = DEFAULT_PREVIEW_BYTES) -> ResourceResult:
    invalid = validate_http_url_request(request, stage=ResourceStage.PREVIEW)
    if invalid:
        return invalid
    attempts = network_attempts_for_url(request)
    attempted_routes = [attempt.route for attempt in attempts]
    if not attempts:
        return route_unavailable_result(request, stage=ResourceStage.PREVIEW)
    last_error: ResourceResult | None = None
    for attempt in attempts:
        try:
            req = urllib.request.Request(
                request.url,
                method="GET",
                headers={"User-Agent": "codex-resource-fetcher/0.1", "Accept": "*/*"},
            )
            with attempt.opener.open(req, timeout=request.timeout_seconds) as response:
                metadata = response_metadata(
                    response,
                    request,
                    method="GET",
                    stage=ResourceStage.PREVIEW,
                    network_attempt=attempt,
                    attempted_routes=attempted_routes,
                )
                too_large = check_content_length_limit(request, metadata, stage=ResourceStage.PREVIEW)
                if too_large:
                    return too_large
                limit = max(0, int(preview_bytes))
                data = response.read(limit + 1)
            truncated = len(data) > limit
            preview_data = data[:limit]
            content_type = str(metadata.get("content_type") or "")
            charset = "utf-8"
            charset_match = re.search(r"charset=([^;\s]+)", content_type, flags=re.IGNORECASE)
            if charset_match:
                charset = charset_match.group(1).strip("\"'")
            try:
                preview_text = preview_data.decode(charset, errors="replace")
            except LookupError:
                preview_text = preview_data.decode("utf-8", errors="replace")
            metadata.update(
                {
                    "preview_bytes": len(preview_data),
                    "preview_limit": limit,
                    "preview_truncated": truncated,
                    "preview_text": preview_text,
                }
            )
            return ResourceResult(
                ok=True,
                source=request.source,
                name=request.name,
                size=len(preview_data),
                metadata=metadata,
                decision="previewed",
                resource_kind="url",
                next_action="materialize_if_policy_allows",
            )
        except urllib.error.HTTPError as exc:
            return http_status_error_result(request, exc, stage=ResourceStage.PREVIEW, method="GET")
        except TimeoutError as exc:
            last_error = url_error_result(request, exc, stage=ResourceStage.PREVIEW, method="GET")
        except urllib.error.URLError as exc:
            last_error = url_error_result(request, exc, stage=ResourceStage.PREVIEW, method="GET")
        except OSError as exc:
            last_error = url_error_result(request, exc, stage=ResourceStage.PREVIEW, method="GET")
    return last_error or network_error_result(request, stage=ResourceStage.PREVIEW, error="preview_failed", error_type="preview_failed")


def url_download_failure_result(
    request: ResourceRequest,
    *,
    error: str,
    metadata: dict[str, Any],
    attempted_routes: list[str],
    risk_flags: tuple[str, ...] = (),
) -> ResourceResult:
    strategy = metadata.get("resource_strategy") or recovery_decision_for_error(metadata).to_dict()
    return ResourceResult(
        ok=False,
        source=request.source,
        name=request.name,
        error=error or "download_failed",
        metadata={
            "url": request.url,
            **network_recommendation_metadata(request),
            **metadata,
            "resource_strategy": strategy,
            "network_attempted_routes": attempted_routes,
            **(request.metadata or {}),
        },
        decision="failed",
        policy_reason=str(metadata.get("error_type") or "download_failed"),
        resource_kind=resource_kind_for_request(request),
        next_action=str(strategy.get("next_action") or "surface_resource_failure"),
        risk_flags=risk_flags,
    )


def url_failure_metadata(
    *,
    error_type: str,
    attempt: int,
    attempts: int,
    retry_budget: int,
    route: str,
    url: str = "",
    http_status: int | None = None,
    retry_after_seconds: float | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "url": url,
        "error_type": error_type,
        "attempt": attempt,
        "attempts": attempts,
        "retry_budget": retry_budget,
        "retry_budget_exhausted": attempt >= retry_budget,
        "network_execution_route": route,
    }
    if http_status is not None:
        metadata["http_status"] = http_status
    if retry_after_seconds is not None:
        metadata["retry_after_seconds"] = retry_after_seconds
    metadata["resource_strategy"] = recovery_decision_for_error(metadata).to_dict()
    return metadata


def annotate_failed_download_result(result: ResourceResult, request: ResourceRequest) -> ResourceResult:
    if result.ok:
        return result
    if result.error == "sha256 mismatch":
        error_type = "sha256_mismatch"
    elif result.error.startswith("resource larger than"):
        error_type = "too_large"
    else:
        return result
    metadata = {
        "url": request.url,
        "error_type": error_type,
        **(result.metadata or {}),
    }
    metadata["resource_strategy"] = recovery_decision_for_error(metadata).to_dict()
    return ResourceResult(**{**result.to_dict(), "metadata": metadata})


def _bool_metadata(metadata: dict[str, Any] | None, key: str, default: bool = False) -> bool:
    value = (metadata or {}).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def selected_download_backend_decision(request: ResourceRequest) -> dict[str, Any]:
    availability = download_backend_availability().to_dict()
    partial_name = safe_filename(request.name or Path(urllib.parse.urlparse(request.url).path).name or "resource", "resource")
    partial_path = request.target_dir / f"{partial_name}.part"
    partial_size = partial_path.stat().st_size if partial_path.exists() else 0
    return download_backend_decision(
        metadata=request.metadata,
        availability=availability,
        max_bytes=request.max_bytes,
        existing_partial_bytes=partial_size,
    ).to_dict()


def selected_download_backend(request: ResourceRequest) -> str:
    return str(selected_download_backend_decision(request).get("backend") or "")


def backend_download_failure_result(
    request: ResourceRequest,
    *,
    backend_result: dict[str, Any],
    attempted_routes: list[str],
    route: str,
    attempt: int,
    attempts: int,
    retry_budget: int,
) -> ResourceResult:
    error_type = str(backend_result.get("error_class") or "download_backend_failed")
    metadata = {
        "error_type": error_type,
        "attempt": attempt,
        "attempts": attempts,
        "retry_budget": retry_budget,
        "retry_budget_exhausted": attempt >= retry_budget,
        "network_execution_route": route,
        "download_backend": backend_result.get("backend", ""),
        "download_backend_result": backend_result,
        "download_backend_selection": (request.metadata or {}).get("download_backend_selection") or {},
    }
    metadata["resource_strategy"] = recovery_decision_for_error(metadata).to_dict()
    return url_download_failure_result(
        request,
        error=str(backend_result.get("error") or error_type),
        metadata=metadata,
        attempted_routes=attempted_routes,
        risk_flags=("download_backend", error_type),
    )


def acquire_url_resource_with_backend(
    request: ResourceRequest,
    *,
    backend: str,
    network_attempt: ResourceNetworkAttempt,
    attempted_routes: list[str],
    attempt: int,
    attempts: int,
    retry_budget: int,
) -> ResourceResult:
    partial_name = safe_filename(request.name or Path(urllib.parse.urlparse(request.url).path).name or "resource", "resource")
    partial_path = request.target_dir / f"{partial_name}.part"
    backend_result = run_backend_download(
        backend=backend,
        url=request.url,
        partial_path=partial_path,
        timeout_seconds=request.timeout_seconds,
        proxy_url=network_attempt.proxy_url,
        resume=_bool_metadata(request.metadata, "resume_download", True),
    )
    if not backend_result.ok:
        return backend_download_failure_result(
            request,
            backend_result=backend_result.to_dict(),
            attempted_routes=attempted_routes,
            route=network_attempt.route,
            attempt=attempt,
            attempts=attempts,
            retry_budget=retry_budget,
        )
    elapsed_seconds = float(backend_result.elapsed_seconds)
    download_health = download_health_for(
        bytes_read=backend_result.bytes_read,
        elapsed_seconds=elapsed_seconds,
        metadata=request.metadata,
    ).to_dict()
    result = acquire_local_resource(
        ResourceRequest(
            source=request.source,
            target_dir=request.target_dir,
            name=request.name or partial_name,
            local_path=Path(backend_result.path),
            expected_sha256=request.expected_sha256,
            max_bytes=request.max_bytes,
            metadata={
                "url": request.url,
                "attempt": attempt,
                "attempts": attempts,
                "timeout_seconds": request.timeout_seconds,
                "retry_budget": retry_budget,
                "retry_budget_exhausted": attempt >= retry_budget,
                "download_health": download_health,
                "download_backend": backend,
                "download_backend_result": backend_result.to_dict(),
                "download_backend_selection": (request.metadata or {}).get("download_backend_selection") or {},
                **network_recommendation_metadata(request),
                **(request.metadata or {}),
            },
        )
    )
    try:
        Path(backend_result.path).unlink(missing_ok=True)
    except OSError:
        pass
    return annotate_failed_download_result(result, request)


def acquire_url_resource(request: ResourceRequest) -> ResourceResult:
    if not request.url:
        return ResourceResult(ok=False, source=request.source, name=request.name, error="url is required")
    attempts = max(1, request.retries + 1)
    network_attempts = network_attempts_for_url(request)
    attempted_routes = [attempt.route for attempt in network_attempts]
    if not network_attempts:
        return route_unavailable_result(request, stage=ResourceStage.MATERIALIZE)
    recommendation = recommendation_for_target(request.url, context="resource_acquisition")
    retry_budget = int(getattr(recommendation, "retry_budget", 1) or 1)
    backend_selection = selected_download_backend_decision(request)
    backend = str(backend_selection.get("backend") or "")
    request_metadata = {**(request.metadata or {}), "download_backend_selection": backend_selection}
    request = replace(request, metadata=request_metadata)
    last_error = ""
    last_metadata: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        for network_attempt in network_attempts:
            try:
                if backend:
                    backend_result = acquire_url_resource_with_backend(
                        request,
                        backend=backend,
                        network_attempt=network_attempt,
                        attempted_routes=attempted_routes,
                        attempt=attempt,
                        attempts=attempts,
                        retry_budget=retry_budget,
                    )
                    if backend_result.ok:
                        return backend_result
                    last_error = backend_result.error
                    last_metadata = backend_result.metadata or {}
                    fallback_to_builtin = str(last_metadata.get("error_type") or "") in {
                        "aria2_failed",
                        "curl_failed",
                        "download_backend_failed",
                    }
                    if fallback_to_builtin:
                        backend = ""
                        backend_selection = {
                            **backend_selection,
                            "fallback_backend": "builtin",
                            "fallback_reason": str(last_metadata.get("error_type") or "download_backend_failed"),
                        }
                    else:
                        if not should_retry_error(last_metadata, attempt_index=attempt, max_attempts=attempts):
                            return backend_result
                        continue
                started_at = time.monotonic()
                with network_attempt.opener.open(request.url, timeout=request.timeout_seconds) as response:
                    status = getattr(response, "status", None)
                    if status is not None and int(status) >= 400:
                        last_error = f"http_status={status}"
                        last_metadata = url_failure_metadata(
                            error_type="http_status",
                            http_status=int(status),
                            attempt=attempt,
                            attempts=attempts,
                            retry_budget=retry_budget,
                            route=network_attempt.route,
                            url=request.url,
                        )
                        if not should_retry_error(last_metadata, attempt_index=attempt, max_attempts=attempts):
                            return url_download_failure_result(
                                request,
                                error=last_error,
                                metadata=last_metadata,
                                attempted_routes=attempted_routes,
                                risk_flags=("http_status",),
                            )
                        continue
                    if request.max_bytes is None:
                        data = response.read()
                    else:
                        data = response.read(request.max_bytes + 1)
                elapsed_seconds = time.monotonic() - started_at
                download_health = download_health_for(
                    bytes_read=len(data),
                    elapsed_seconds=elapsed_seconds,
                    metadata=request.metadata,
                ).to_dict()
                network_metadata = network_recommendation_metadata(request)
                network_metadata.setdefault("network", {})
                network_metadata["network"].update(
                    {
                        "execution_route": network_attempt.route,
                        "execution_proxy_present": bool(network_attempt.proxy_url),
                        "attempted_routes": attempted_routes,
                    }
                )
                content_metadata = response_metadata(
                    response,
                    request,
                    method="GET",
                    stage=ResourceStage.MATERIALIZE,
                    network_attempt=network_attempt,
                    attempted_routes=attempted_routes,
                )
                result = acquire_bytes_resource(
                    source=request.source,
                    data=data,
                    target_dir=request.target_dir,
                    name=url_resource_name(request, content_metadata),
                    max_bytes=request.max_bytes,
                    expected_sha256=request.expected_sha256,
                    metadata={
                        "url": request.url,
                        **content_metadata,
                        "attempt": attempt,
                        "attempts": attempts,
                        "timeout_seconds": request.timeout_seconds,
                        "retry_budget": retry_budget,
                        "retry_budget_exhausted": attempt >= retry_budget,
                        "download_health": download_health,
                        "download_backend_selection": backend_selection,
                        **network_metadata,
                        **(request.metadata or {}),
                    },
                )
                return annotate_failed_download_result(result, request)
            except urllib.error.HTTPError as exc:
                last_error = f"http_status={exc.code}"
                retry_after_seconds = None
                retry_after = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else ""
                if retry_after:
                    try:
                        retry_after_seconds = float(retry_after)
                    except ValueError:
                        retry_after_seconds = 0.0
                last_metadata = url_failure_metadata(
                    error_type="http_status",
                    http_status=exc.code,
                    attempt=attempt,
                    attempts=attempts,
                    retry_budget=retry_budget,
                    route=network_attempt.route,
                    url=request.url,
                    retry_after_seconds=retry_after_seconds,
                )
                if not should_retry_error(last_metadata, attempt_index=attempt, max_attempts=attempts):
                    return url_download_failure_result(
                        request,
                        error=last_error,
                        metadata=last_metadata,
                        attempted_routes=attempted_routes,
                        risk_flags=("http_status",),
                    )
            except TimeoutError as exc:
                last_error = f"timeout={exc}"
                last_metadata = url_failure_metadata(
                    error_type="timeout",
                    attempt=attempt,
                    attempts=attempts,
                    retry_budget=retry_budget,
                    route=network_attempt.route,
                    url=request.url,
                )
            except urllib.error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, TimeoutError):
                    last_error = f"timeout={reason}"
                    last_metadata = url_failure_metadata(
                        error_type="timeout",
                        attempt=attempt,
                        attempts=attempts,
                        retry_budget=retry_budget,
                        route=network_attempt.route,
                        url=request.url,
                    )
                else:
                    last_error = f"network_error={reason}"
                    last_metadata = url_failure_metadata(
                        error_type="network_error",
                        attempt=attempt,
                        attempts=attempts,
                        retry_budget=retry_budget,
                        route=network_attempt.route,
                        url=request.url,
                    )
            except OSError as exc:
                last_error = f"network_error={exc}"
                last_metadata = url_failure_metadata(
                    error_type="network_error",
                    attempt=attempt,
                    attempts=attempts,
                    retry_budget=retry_budget,
                    route=network_attempt.route,
                    url=request.url,
                )
        if attempt < attempts and request.retry_delay_seconds > 0:
            time.sleep(request.retry_delay_seconds)
    return url_download_failure_result(
        request,
        error=last_error or "download_failed",
        metadata=last_metadata,
        attempted_routes=attempted_routes,
    )


def append_resource_log(log_path: Path, result: ResourceResult) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
