#!/usr/bin/env python3
"""Network policy primitives for Codex infrastructure.

Ownership:
- Classifies network targets and exposes dynamic route recommendations for
  Codex, MCP, resource acquisition, package managers, and diagnostics.

Non-goals:
- Does not modify Windows proxy settings, DNS, Clash/Mihomo rules, or system
  environment variables.
- Does not force every request through one proxy endpoint.

State behavior:
- Read-only. Recommendations are derived from current process environment,
  Windows proxy hints, and local proxy listeners.

Caller context:
- `network_doctor.py` owns CLI/diagnostics.
- resource acquisition and Hub tools may attach these recommendations as
  evidence before performing network work.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


GENERIC_NO_PROXY = "localhost,127.0.0.1,::1,.local"
CURL_NO_PROXY = "localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.local"
DEFAULT_NO_PROXY = GENERIC_NO_PROXY
LOCAL_PROXY_PORT_CANDIDATES = (7897, 7890, 7891, 1080, 10808, 10809, 20171)

OPENAI_SUFFIXES = (
    "openai.com",
    "chatgpt.com",
    "oaistatic.com",
    "oaiusercontent.com",
)
GITHUB_SUFFIXES = ("github.com", "githubusercontent.com", "githubassets.com")
PACKAGE_SUFFIXES = (
    "registry.npmjs.org",
    "npmjs.org",
    "pypi.org",
    "pythonhosted.org",
    "files.pythonhosted.org",
    "nodejs.org",
)

GATEWAY_PATTERN_SOURCE = "envoy_traefik_litellm_patterns_without_permission_layer"


@dataclass(frozen=True)
class LightweightGatewayPolicy:
    health_score: int
    retry_budget: int
    failover_policy: str
    observability_tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NetworkRouteRecommendation:
    target: str
    host: str
    category: str
    route: str
    profile: str
    reason: str
    proxy_url: str = ""
    no_proxy: str = DEFAULT_NO_PROXY
    env: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    runtime_notes: tuple[str, ...] = ()
    health_score: int = 0
    retry_budget: int = 1
    failover_policy: str = "none"
    observability_tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def host_from_target(target: str) -> str:
    value = str(target or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value) else f"//{value}")
    host = parsed.hostname or value.split("/", 1)[0]
    return host.strip("[]").lower()


def is_local_or_lan_host(host: str) -> bool:
    value = str(host or "").strip().lower()
    if value in {"", "localhost"} or value.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(value)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


def domain_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    value = str(host or "").lower().rstrip(".")
    return any(value == suffix or value.endswith(f".{suffix}") for suffix in suffixes)


def category_for_host(host: str) -> str:
    if is_local_or_lan_host(host):
        return "local"
    if domain_matches(host, OPENAI_SUFFIXES):
        return "openai"
    if domain_matches(host, GITHUB_SUFFIXES):
        return "github"
    if domain_matches(host, PACKAGE_SUFFIXES):
        return "package"
    return "external"


def gateway_policy_for_category(category: str, *, has_proxy: bool) -> LightweightGatewayPolicy:
    value = str(category or "external")
    if value == "local":
        return LightweightGatewayPolicy(
            health_score=100,
            retry_budget=2,
            failover_policy="direct_only_no_proxy",
            observability_tags=("local_bypass", "no_proxy"),
        )
    if value == "openai":
        return LightweightGatewayPolicy(
            health_score=75 if has_proxy else 45,
            retry_budget=2,
            failover_policy="proxy_first_with_direct_fallback",
            observability_tags=("provider_sensitive", "proxy_strategy_sensitive", "record_tls_timing"),
        )
    if value in {"github", "package"}:
        return LightweightGatewayPolicy(
            health_score=80 if has_proxy else 65,
            retry_budget=2,
            failover_policy="probe_selected_with_proxy_candidate",
            observability_tags=("auto_fastest", "record_route_latency"),
        )
    return LightweightGatewayPolicy(
        health_score=70 if has_proxy else 55,
        retry_budget=1,
        failover_policy="system_or_direct_with_optional_proxy_fallback",
        observability_tags=("generic_external", "record_route_latency"),
    )


def env_proxy_url() -> str:
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key)
        if value:
            return value.strip()
    return ""


def windows_user_proxy() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not int(enabled):
                return ""
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            value = str(server or "").strip()
            if "=" in value:
                for part in value.split(";"):
                    if part.lower().startswith(("https=", "http=")):
                        return "http://" + part.split("=", 1)[1].strip()
            if value:
                return value if "://" in value else f"http://{value}"
    except OSError:
        return ""
    return ""


def local_proxy_listener_url() -> str:
    for port in LOCAL_PROXY_PORT_CANDIDATES:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return f"http://127.0.0.1:{port}"
        except OSError:
            continue
    return ""


def proxy_candidates() -> list[dict[str, str]]:
    seen: set[str] = set()
    candidates: list[dict[str, str]] = []
    for source, value in (
        ("environment", env_proxy_url()),
        ("windows_user_proxy", windows_user_proxy()),
        ("local_listener", local_proxy_listener_url()),
    ):
        if value and value not in seen:
            seen.add(value)
            candidates.append({"source": source, "url": value})
    return candidates


def best_proxy_url() -> str:
    candidates = proxy_candidates()
    return candidates[0]["url"] if candidates else ""


def recommendation_for_target(target: str, *, context: str = "") -> NetworkRouteRecommendation:
    host = host_from_target(target)
    category = category_for_host(host)
    proxy_url = best_proxy_url()
    warnings: list[str] = []
    if category == "local":
        gateway_policy = gateway_policy_for_category(category, has_proxy=False)
        return NetworkRouteRecommendation(
            target=target,
            host=host,
            category=category,
            route="direct",
            profile="local_direct",
            reason="local_or_private_target_must_not_use_proxy",
            health_score=gateway_policy.health_score,
            retry_budget=gateway_policy.retry_budget,
            failover_policy=gateway_policy.failover_policy,
            observability_tags=gateway_policy.observability_tags,
        )
    if not proxy_url:
        warnings.append("no_proxy_candidate_detected")
    if category == "openai":
        route = "proxy_preferred" if proxy_url else "direct_with_risk"
        reason = "openai_targets_are_proxy_preferred_due_to_observed_direct_dns_or_exit_risk"
        profile = "openai_proxy"
    elif category in {"github", "package"}:
        route = "auto_fastest"
        reason = f"{category}_targets_may_be_direct_or_proxy_based_on_probe_results"
        profile = f"{category}_auto"
    else:
        route = "system_or_auto"
        reason = "generic_external_target_uses_system_or_probe_selected_route"
        profile = "external_auto"
    env = proxy_env(proxy_url) if proxy_url and route != "direct" else {"NO_PROXY": DEFAULT_NO_PROXY}
    if context:
        env["CODEX_NETWORK_CONTEXT"] = context
    gateway_policy = gateway_policy_for_category(category, has_proxy=bool(proxy_url))
    return NetworkRouteRecommendation(
        target=target,
        host=host,
        category=category,
        route=route,
        profile=profile,
        reason=reason,
        proxy_url=proxy_url,
        env=env,
        warnings=tuple(warnings),
        health_score=gateway_policy.health_score,
        retry_budget=gateway_policy.retry_budget,
        failover_policy=gateway_policy.failover_policy,
        observability_tags=gateway_policy.observability_tags,
    )


def proxy_env(proxy_url: str) -> dict[str, str]:
    if not proxy_url:
        return {"NO_PROXY": DEFAULT_NO_PROXY}
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "NO_PROXY": os.environ.get("NO_PROXY") or GENERIC_NO_PROXY,
        "NODE_USE_ENV_PROXY": "1",
    }


def env_for_runtime(target: str, *, runtime: str = "generic", context: str = "") -> dict[str, Any]:
    recommendation = recommendation_for_target(target, context=context)
    runtime_key = str(runtime or "generic").strip().lower()
    env = dict(recommendation.env)
    notes: list[str] = []
    if runtime_key in {"curl", "libcurl"}:
        env["NO_PROXY"] = os.environ.get("NO_PROXY") or CURL_NO_PROXY
        notes.append("curl_supports_cidr_no_proxy_in_modern_versions")
    elif runtime_key in {"node", "nodejs", "npm", "npx"}:
        env["NO_PROXY"] = os.environ.get("NO_PROXY") or GENERIC_NO_PROXY
        env["NODE_USE_ENV_PROXY"] = "1"
        notes.append("node_requires_node_use_env_proxy_or_use_env_proxy_flag_for_builtin_proxy")
    elif runtime_key in {"python", "pip", "uv", "uvx"}:
        env["NO_PROXY"] = os.environ.get("NO_PROXY") or GENERIC_NO_PROXY
        notes.append("python_tooling_varies_by_library_keep_no_proxy_conservative")
    elif runtime_key in {"winhttp", "service"}:
        notes.append("winhttp_uses_separate_configuration_do_not_assume_wininet_or_env_proxy")
    elif runtime_key in {"browser", "wininet"}:
        notes.append("browser_typically_uses_wininet_or_app_proxy_configuration")
    return {
        "schema": "network_policy.runtime_env.v1",
        "ok": True,
        "runtime": runtime_key,
        "target": target,
        "recommendation": recommendation.to_dict(),
        "env": env,
        "notes": notes,
        "rule": "per-process suggestion only; never persist globally without explicit approval",
    }


def work_plan_for_target(target: str, *, context: str = "") -> dict[str, Any]:
    recommendation = recommendation_for_target(target, context=context)
    category = recommendation.category
    steps: list[dict[str, str]] = [
        {
            "step": "classify",
            "command": f"python _bridge\\network_doctor.py recommend {json.dumps(target)} --context {json.dumps(context)}",
            "purpose": "choose per-target route without hard-binding global traffic",
        },
        {
            "step": "probe",
            "command": f"python _bridge\\network_doctor.py probe {json.dumps(target)} --context {json.dumps(context)}",
            "purpose": "compare direct and proxy timing before changing callers",
        },
    ]
    if category == "openai":
        steps.append(
            {
                "step": "caller_env",
                "command": f"python _bridge\\network_doctor.py env {json.dumps(target)} --runtime node --context codex_chat",
                "purpose": "generate temporary Node/Codex subprocess proxy env; includes NODE_USE_ENV_PROXY",
            }
        )
        steps.append(
            {
                "step": "proxy_owner",
                "command": "Inspect Clash/Mihomo OpenAI strategy group/node outside Codex; do not change system proxy from network_doctor.",
                "purpose": "slow OpenAI proxy TLS usually means target strategy/node quality",
            }
        )
    elif category in {"github", "package"}:
        steps.append(
            {
                "step": "caller_env",
                "command": f"python _bridge\\network_doctor.py env {json.dumps(target)} --runtime generic --context {json.dumps(category)}",
                "purpose": "generate temporary env only if probe shows proxy is healthier than direct",
            }
        )
    return {
        "schema": "network_policy.work_plan.v1",
        "ok": True,
        "target": target,
        "category": category,
        "recommendation": recommendation.to_dict(),
        "steps": steps,
        "boundaries": [
            "no_system_proxy_or_dns_mutation",
            "no_clash_node_switching_without_user_action_or_owner_tool",
            "no_oauth_rbac_or_multi_tenant_permission_layer_added_by_network_policy",
            "resource_github_browser_package_owners_keep_their_own_permissions",
        ],
        "absorbed_gateway_patterns": {
            "source": GATEWAY_PATTERN_SOURCE,
            "kept": ["route_health_score", "retry_budget", "failover_policy", "observability_tags"],
            "excluded": ["oauth", "rbac", "multi_tenant_auth", "global_proxy_mutation"],
        },
    }


def snapshot() -> dict[str, Any]:
    winhttp = ""
    if sys.platform == "win32":
        try:
            proc = subprocess.run(
                ["netsh", "winhttp", "show", "proxy"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=5,
            )
            winhttp = (proc.stdout or proc.stderr or "").strip()
        except Exception as exc:
            winhttp = f"error={type(exc).__name__}: {exc}"
    return {
        "schema": "network_policy.snapshot.v1",
        "ok": True,
        "env_proxy": env_proxy_url(),
        "windows_user_proxy": windows_user_proxy(),
        "local_proxy_listener": local_proxy_listener_url(),
        "proxy_candidates": proxy_candidates(),
        "winhttp": winhttp,
        "no_proxy_default": DEFAULT_NO_PROXY,
        "no_proxy_profiles": {
            "generic": GENERIC_NO_PROXY,
            "curl": CURL_NO_PROXY,
        },
        "gateway_patterns": {
            "source": GATEWAY_PATTERN_SOURCE,
            "policy_by_category": {
                key: gateway_policy_for_category(key, has_proxy=bool(best_proxy_url())).to_dict()
                for key in ("local", "openai", "github", "package", "external")
            },
            "excluded_permission_mechanisms": ["oauth", "rbac", "multi_tenant_auth"],
        },
        "rule": "read-only network route discovery; callers choose per target and must not hard-bind all traffic to one proxy",
    }


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
