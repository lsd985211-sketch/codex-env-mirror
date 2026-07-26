#!/usr/bin/env python3
"""Thin Codex network gateway control plane.

Ownership: classifies Codex network requests, returns safe per-request route
plans, and delegates experiments to existing network owner modules.
Non-goals: permanent system proxy/DNS changes, Clash subscription/config edits,
Hub startup changes, background services, credential handling, or replacing the
resource/GitHub/browser/package owner tools.
State behavior: read-only by default; smoke commands may write compact reports
under `_bridge/runtime/codex_network_gateway` and delegate lab-only temp process
creation to owner modules that clean up after themselves.
Caller context: Codex resource acquisition, package/GitHub/docs/browser network
work, and controlled network gateway experiments.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tomllib
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import network_policy
from network_route_cache import (
    DEFAULT_FRESH_TTL_SECONDS,
    DEFAULT_STALE_TTL_SECONDS,
    decision_metadata as route_cache_decision_metadata,
    get_decision as get_cached_route_decision,
    put_decision as put_cached_route_decision,
    record_observation as record_route_observation,
    snapshot as route_cache_snapshot,
    target_stats as route_cache_target_stats,
    validate as validate_route_cache,
)


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
RUNTIME_DIR = BRIDGE / "runtime" / "codex_network_gateway"
SCHEMA_PREFIX = "codex_network_gateway"
NETWORK_PROFILE_VERSION = "resource_target_profiles.v2"
DEFAULT_TEST_TARGETS = {
    "codex_chat": "",
    "codex_model_api": "",
    "openai": "https://api.openai.com/v1/models",
    "github": "https://api.github.com/",
    "package": "https://registry.npmjs.org/",
    "docs": "https://duckduckgo.com/",
    "browser": "https://example.com/",
    "paper": "https://api.openalex.org/works?search=artificial%20intelligence&per-page=1",
    "image": "https://commons.wikimedia.org/",
    "dataset": "https://huggingface.co/datasets",
    "web": "https://example.com/",
    "external": "https://www.gstatic.com/generate_204",
}
SAFE_ISOLATED_TARGETS = {"openai", "github", "package", "docs", "browser", "paper", "image", "dataset", "web", "external"}
DEFAULT_CLASH_GROUP = "ClashGit.com"
DEFAULT_NODE_HINTS = {
    "openai": "[Japan] 日本-极速 [推荐]",
    "github": "[Japan] 日本-极速 [推荐]",
    "package": "[Japan] 日本-极速 [推荐]",
    "docs": "[Singapore] 新加坡2-极速 [推荐]",
    "browser": "[Japan] 日本-极速 [推荐]",
    "paper": "[Japan] 日本-极速 [推荐]",
    "image": "[Japan] 日本-极速 [推荐]",
    "dataset": "[Japan] 日本-极速 [推荐]",
    "web": "[Japan] 日本-极速 [推荐]",
    "external": "[Japan] 日本-极速 [推荐]",
}
PROTECTED_CODEX_TARGETS = {"codex_chat", "codex_model_api"}


@dataclass(frozen=True)
class GatewayPlan:
    target: str
    target_kind: str
    route_mode: str
    route_reason: str
    proxy_url: str
    env: dict[str, str]
    unset_env: tuple[str, ...]
    lease_kind: str
    lease_command: str
    cleanup: str
    risk: str
    approval_required: bool
    boundaries: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def hidden_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def codex_config_base_url() -> str:
    try:
        with CODEX_CONFIG_PATH.open("rb") as fh:
            payload = tomllib.load(fh)
    except Exception:
        return ""
    value = payload.get("model_provider")
    providers = payload.get("model_providers") if isinstance(payload.get("model_providers"), dict) else {}
    provider = providers.get(value) if isinstance(value, str) else None
    if isinstance(provider, dict):
        return str(provider.get("base_url") or "").strip()
    return str(payload.get("base_url") or "").strip()


def codex_model_probe_url() -> str:
    base_url = codex_config_base_url().rstrip("/")
    if not base_url:
        return "https://api.openai.com/v1/models"
    if base_url.endswith("/v1"):
        return f"{base_url}/models"
    return base_url


def run_json(argv: list[str], *, timeout: int = 60) -> dict[str, Any]:
    proc = subprocess.run(
        argv,
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=hidden_creationflags(),
    )
    if proc.returncode != 0:
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload:
            payload.setdefault("ok", False)
            payload.setdefault("returncode", proc.returncode)
            payload.setdefault("stderr_tail", (proc.stderr or "")[-1000:])
            return payload
        return {
            "ok": False,
            "returncode": proc.returncode,
            "argv": argv,
            "stdout_tail": (proc.stdout or "")[-1000:],
            "stderr_tail": (proc.stderr or "")[-1000:],
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "reason": f"json_decode_failed: {exc}",
            "argv": argv,
            "stdout_tail": (proc.stdout or "")[-1000:],
            "stderr_tail": (proc.stderr or "")[-1000:],
        }


def target_for_kind(target_kind: str, target: str = "") -> str:
    if target:
        return target
    if target_kind in PROTECTED_CODEX_TARGETS:
        return codex_model_probe_url()
    return DEFAULT_TEST_TARGETS.get(target_kind, DEFAULT_TEST_TARGETS["external"])


def normalize_target_kind(target_kind: str, target: str = "") -> str:
    value = str(target_kind or "").strip().lower()
    if value:
        return value
    category = network_policy.recommendation_for_target(target or DEFAULT_TEST_TARGETS["external"]).category
    if category == "openai":
        return "openai"
    return category if category in DEFAULT_TEST_TARGETS else "external"


def request_interfaces() -> dict[str, Any]:
    codex_base_url = codex_config_base_url()
    return {
        "schema": f"{SCHEMA_PREFIX}.interfaces.v1",
        "ok": True,
        "generated_at": now_iso(),
        "production_mutation": {
            "writes_system_proxy": False,
            "writes_dns": False,
            "writes_clash_config": False,
            "changes_codex_conversation_route": False,
            "switches_production_node": False,
        },
        "interfaces": [
            {
                "target_kind": "codex_chat",
                "aliases": ["codex_model_api"],
                "default_target": target_for_kind("codex_chat"),
                "source": "current_codex_config_base_url",
                "base_url_present": bool(codex_base_url),
                "protected": True,
                "allowed_commands": ["snapshot", "interfaces", "plan", "env"],
                "disallowed_without_separate_approval": ["isolated_smoke", "production_node_switch", "system_proxy_change"],
                "rule": "Represents the current Codex model API route, which may be third-party and must not be treated as official api.openai.com.",
            },
            {
                "target_kind": "openai",
                "aliases": ["openai_official"],
                "default_target": DEFAULT_TEST_TARGETS["openai"],
                "source": "official_openai_endpoint",
                "protected": False,
                "allowed_commands": ["plan --probe", "smoke --mode isolated", "lease-start", "env"],
                "rule": "Official OpenAI target for experiments; it is not the current Codex conversation route unless config proves that separately.",
            },
            {
                "target_kind": "github",
                "default_target": DEFAULT_TEST_TARGETS["github"],
                "allowed_commands": ["plan --probe", "env", "smoke", "lease-start"],
                "owner_boundary": "GitHub MCP/API remains owner for repository facts and writes.",
            },
            {
                "target_kind": "package",
                "default_target": DEFAULT_TEST_TARGETS["package"],
                "allowed_commands": ["plan --probe", "env", "smoke", "lease-start"],
                "owner_boundary": "Package managers remain owner for install/update; gateway supplies per-request route evidence.",
            },
            {
                "target_kind": "docs",
                "default_target": DEFAULT_TEST_TARGETS["docs"],
                "allowed_commands": ["plan --probe", "env", "smoke", "lease-start"],
                "owner_boundary": "Microsoft Docs/Context7/browser tools remain source owners.",
            },
            {
                "target_kind": "browser",
                "default_target": DEFAULT_TEST_TARGETS["browser"],
                "allowed_commands": ["plan", "env", "smoke", "lease-start"],
                "owner_boundary": "Browser/DevTools/Playwright remain owner for UI/runtime evidence.",
            },
            {
                "target_kind": "paper",
                "default_target": DEFAULT_TEST_TARGETS["paper"],
                "allowed_commands": ["plan --probe", "env", "smoke", "lease-start"],
                "owner_boundary": "Resource layer remains owner for academic source selection and materialization.",
            },
            {
                "target_kind": "image",
                "default_target": DEFAULT_TEST_TARGETS["image"],
                "allowed_commands": ["plan --probe", "env", "smoke", "lease-start"],
                "owner_boundary": "Resource layer remains owner for image source selection, attribution, and downloads.",
            },
            {
                "target_kind": "dataset",
                "default_target": DEFAULT_TEST_TARGETS["dataset"],
                "allowed_commands": ["plan --probe", "env", "smoke", "lease-start"],
                "owner_boundary": "Resource layer remains owner for dataset/license/size/source strategy.",
            },
            {
                "target_kind": "web",
                "default_target": DEFAULT_TEST_TARGETS["web"],
                "allowed_commands": ["plan --probe", "env", "smoke", "lease-start"],
                "owner_boundary": "Resource layer remains owner for generic web source discovery and fallback policy.",
            },
            {
                "target_kind": "external",
                "default_target": DEFAULT_TEST_TARGETS["external"],
                "allowed_commands": ["plan --probe", "env", "smoke", "lease-start"],
                "owner_boundary": "Generic external URL route; resource acquisition policy still applies.",
            },
        ],
    }


def proxy_check(proxy_url: str, target: str, *, timeout: int = 15, max_bytes: int = 4096) -> dict[str, Any]:
    started = time.perf_counter()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    request = urllib.request.Request(target, method="GET", headers={"User-Agent": "codex-network-gateway/1.0"})
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes)
            return {
                "ok": True,
                "url": target,
                "status": getattr(response, "status", 0),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                "bytes": len(body),
                "error": "",
            }
    except Exception as exc:
        return {
            "ok": False,
            "url": target,
            "status": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "bytes": 0,
            "error": str(exc)[:300],
        }


def probe_route(target: str, *, context: str, timeout: int) -> dict[str, Any]:
    return run_json(
        [
            sys.executable,
            str(BRIDGE / "network_doctor.py"),
            "probe",
            target,
            "--context",
            context,
            "--timeout",
            str(timeout),
        ],
        timeout=max(timeout + 20, 45),
    )


def direct_env(context: str) -> dict[str, str]:
    return {
        "NO_PROXY": os.environ.get("NO_PROXY") or network_policy.DEFAULT_NO_PROXY,
        "CODEX_NETWORK_CONTEXT": context,
        "CODEX_NETWORK_ROUTE": "direct",
    }


def clash_node_recommendation(kind: str, *, refresh_if_stale: bool = False) -> dict[str, Any]:
    if kind in PROTECTED_CODEX_TARGETS:
        return {"ok": False, "reason": "protected_codex_target_has_no_clash_node_recommendation", "target_kind": kind}
    command = [
        sys.executable,
        str(BRIDGE / "clash_node_metrics.py"),
        "recommend",
        "--target",
        kind,
        "--delay-limit",
        "6",
        "--access-limit",
        "3",
        "--timeout-ms",
        "3000",
    ]
    if refresh_if_stale:
        command.append("--refresh-if-stale")
    return run_json(command, timeout=140 if refresh_if_stale else 30)


def route_plan(
    *,
    target_kind: str,
    target: str,
    runtime: str,
    owner_tool: str = "",
    isolation: str,
    group: str,
    node: str,
    probe: bool = False,
    probe_timeout: int = 12,
    force_probe: bool = False,
    fresh_ttl_seconds: int = DEFAULT_FRESH_TTL_SECONDS,
    stale_ttl_seconds: int = DEFAULT_STALE_TTL_SECONDS,
) -> dict[str, Any]:
    kind = normalize_target_kind(target_kind, target)
    resolved_target = target_for_kind(kind, target)
    normalized_owner_tool = str(owner_tool or kind or "generic").strip().lower().replace(" ", "_") or "generic"
    if kind not in PROTECTED_CODEX_TARGETS and resolved_target:
        cached = get_cached_route_decision(kind, resolved_target, runtime, owner_tool=normalized_owner_tool, allow_stale=True)
        if cached.get("cache_hit") and not force_probe:
            payload = dict(cached.get("plan") or {})
            if (
                payload.get("schema") == f"{SCHEMA_PREFIX}.plan.v1"
                and payload.get("network_profile_version") == NETWORK_PROFILE_VERSION
            ):
                payload["route_cache"] = route_cache_decision_metadata(cached)
                payload["cache_status"] = f"route_cache_{cached.get('freshness')}"
                return payload
    recommendation = network_policy.recommendation_for_target(resolved_target, context=f"codex_gateway:{kind}")
    env_payload = network_policy.env_for_runtime(resolved_target, runtime=runtime, context=f"codex_gateway:{kind}")
    proxy_url = recommendation.proxy_url or network_policy.best_proxy_url()
    isolation_mode = str(isolation or "auto").strip().lower()
    node_recommendation = clash_node_recommendation(kind, refresh_if_stale=isolation_mode in {"prefer", "required"})
    recommended_node = str(node_recommendation.get("recommended_node") or "")
    requested_node = node or recommended_node or DEFAULT_NODE_HINTS.get(kind, "")
    lease_kind = "none"
    lease_command = ""
    cleanup = "none"
    approval_required = False
    risk = "L1"
    route_mode = recommendation.route
    route_reason = recommendation.reason
    probe_payload: dict[str, Any] = {}
    unset_env: tuple[str, ...] = ()

    if kind in PROTECTED_CODEX_TARGETS:
        route_mode = "protected_primary_route"
        route_reason = "protect_current_codex_model_base_url; do not create isolated temp route or change production route automatically"
        lease_kind = "none"
        risk = "L2"
    elif probe:
        probe_payload = probe_route(resolved_target, context=f"codex_gateway:{kind}", timeout=probe_timeout)
        preferred_route = str(probe_payload.get("classification", {}).get("preferred_route") or "")
        if preferred_route == "direct":
            route_mode = "probe_selected_direct"
            route_reason = "probe_selected_direct_for_this_request"
            env_payload = {
                "schema": "network_policy.runtime_env.v1",
                "ok": True,
                "runtime": runtime,
                "target": resolved_target,
                "recommendation": recommendation.to_dict(),
                "env": direct_env(f"codex_gateway:{kind}"),
                "notes": ["caller_should_remove_proxy_env_for_direct_route"],
                "rule": "per-process suggestion only; never persist globally without explicit approval",
            }
            unset_env = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        elif preferred_route == "proxy":
            route_mode = "probe_selected_proxy"
            route_reason = "probe_selected_proxy_for_this_request"
        elif preferred_route == "none":
            route_mode = "probe_no_working_route"
            route_reason = "probe_found_no_working_direct_or_proxy_route"
            risk = "L2"
        elif preferred_route:
            route_mode = f"probe_selected_{preferred_route}"
            route_reason = "probe_selected_special_route_for_this_request"
    elif isolation_mode in {"prefer", "required"} and kind in SAFE_ISOLATED_TARGETS:
        lease_kind = "isolated_mihomo_probe"
        route_mode = "isolated_mihomo"
        cleanup = "owner_command_terminates_temp_process"
        lease_command = (
            f"python _bridge\\clash_mihomo_control.py isolated-probe --group {json.dumps(group)} "
            f"--node {json.dumps(requested_node)} --target {json.dumps(kind)}"
        )
    elif isolation_mode == "wrapper":
        lease_kind = "proxy_chain_wrapper"
        route_mode = "proxy_chain_wrapper"
        cleanup = "owner_command_terminates_temp_proxy"
        lease_command = (
            f"python _bridge\\network_gateway_component_lab.py proxy-chain-smoke "
            f"--upstream-proxy {json.dumps(proxy_url)} --test-url {json.dumps(resolved_target)}"
        )
    elif proxy_url:
        lease_kind = "current_local_proxy"
        route_mode = "current_proxy_env"
        cleanup = "none"
    else:
        route_mode = "direct"
        lease_kind = "none"

    plan = GatewayPlan(
        target=resolved_target,
        target_kind=kind,
        route_mode=route_mode,
        route_reason=route_reason,
        proxy_url=proxy_url,
        env=dict(env_payload.get("env", {})),
        unset_env=unset_env,
        lease_kind=lease_kind,
        lease_command=lease_command,
        cleanup=cleanup,
        risk=risk,
        approval_required=approval_required,
        boundaries=(
            "no_system_proxy_or_dns_mutation",
            "no_clash_subscription_or_config_edit",
            "no_codex_conversation_route_change",
            "owner_tool_permissions_preserved",
        ),
    )
    payload = {
        "schema": f"{SCHEMA_PREFIX}.plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "network_profile_version": NETWORK_PROFILE_VERSION,
        "owner_tool": normalized_owner_tool,
        "plan": plan.to_dict(),
        "network_recommendation": recommendation.to_dict(),
        "clash_node_recommendation": node_recommendation,
        "runtime_env": env_payload,
        "probe": probe_payload,
        "route_cache": {
            "cache_hit": False,
            "freshness": "miss_or_refresh",
            "source": "computed",
        },
    }
    if kind not in PROTECTED_CODEX_TARGETS and resolved_target:
        stored_decision = put_cached_route_decision(
            target_kind=kind,
            target=resolved_target,
            runtime=runtime,
            owner_tool=normalized_owner_tool,
            plan=payload,
            fresh_ttl_seconds=fresh_ttl_seconds,
            stale_ttl_seconds=stale_ttl_seconds,
        )
        payload["route_cache"] = route_cache_decision_metadata(stored_decision)
    return payload


def batch_plan(
    requests: list[dict[str, Any]],
    *,
    probe: bool = False,
    force_probe: bool = False,
    default_runtime: str = "generic",
    isolation: str = "auto",
    group: str = DEFAULT_CLASH_GROUP,
    total_budget_seconds: float = 0.0,
) -> dict[str, Any]:
    started = time.monotonic()
    total_budget = max(0.0, float(total_budget_seconds or 0.0))
    deadline = started + total_budget if total_budget > 0 else 0.0
    results: list[dict[str, Any]] = []
    for index, item in enumerate(requests, start=1):
        if not isinstance(item, dict):
            results.append({"index": index, "ok": False, "reason": "request_item_not_object"})
            continue
        remaining = max(0.0, deadline - time.monotonic()) if deadline else float("inf")
        if deadline and remaining < 1.0:
            results.append(
                {
                    "index": index,
                    "ok": False,
                    "reason": "total_budget_exhausted",
                    "error_class": "total_budget_exhausted",
                    "plan": {"ok": False, "reason": "total_budget_exhausted"},
                }
            )
            continue
        target_kind = str(item.get("target_kind") or item.get("kind") or "")
        target = str(item.get("target") or item.get("url") or "")
        owner_tool = str(item.get("owner_tool") or item.get("tool") or target_kind or "").strip()
        runtime = str(item.get("runtime") or default_runtime or "generic")
        requested_probe_timeout = int(item.get("probe_timeout") or 12)
        effective_probe_timeout = requested_probe_timeout if remaining == float("inf") else min(requested_probe_timeout, int(remaining))
        plan = route_plan(
            target_kind=target_kind,
            target=target,
            runtime=runtime,
            owner_tool=owner_tool,
            isolation=str(item.get("isolation") or isolation),
            group=str(item.get("group") or group),
            node=str(item.get("node") or ""),
            probe=bool(item.get("probe", probe)),
            probe_timeout=max(1, effective_probe_timeout),
            force_probe=bool(item.get("force_probe", force_probe)),
        )
        results.append(
            {
                "index": index,
                "ok": bool(plan.get("ok")),
                "target_kind": plan.get("plan", {}).get("target_kind", target_kind),
                "target": plan.get("plan", {}).get("target", target),
                "owner_tool": plan.get("owner_tool", owner_tool),
                "runtime": runtime,
                "route_mode": plan.get("plan", {}).get("route_mode", ""),
                "route_cache": plan.get("route_cache", {}),
                "plan": plan,
            }
        )
    cache_hits = sum(1 for item in results if (item.get("route_cache") or {}).get("cache_hit"))
    stale_hits = sum(1 for item in results if (item.get("route_cache") or {}).get("freshness") == "stale")
    return {
        "schema": f"{SCHEMA_PREFIX}.batch_plan.v1",
        "ok": all(bool(item.get("ok")) for item in results),
        "generated_at": now_iso(),
        "request_count": len(requests),
        "cache_hit_count": cache_hits,
        "stale_hit_count": stale_hits,
        "results": results,
        "execution_budget": {
            "schema": "network_gateway.batch_budget.v1",
            "bounded": bool(deadline),
            "total_seconds": total_budget,
            "elapsed_seconds": round(max(0.0, time.monotonic() - started), 3),
            "exhausted": bool(deadline and time.monotonic() >= deadline),
            "rule": "network batch planning consumes one shared deadline and does not reset timeout per target",
        },
        "boundaries": [
            "network_layer_only_returns_route_decisions",
            "resource_layer_keeps_resource_policy_and_execution",
            "no_system_proxy_or_dns_mutation",
            "no_codex_conversation_route_change",
        ],
    }


def env_for_request(target_kind: str, target: str, runtime: str) -> dict[str, Any]:
    kind = normalize_target_kind(target_kind, target)
    resolved_target = target_for_kind(kind, target)
    payload = network_policy.env_for_runtime(resolved_target, runtime=runtime, context=f"codex_gateway:{kind}")
    payload["schema"] = f"{SCHEMA_PREFIX}.env.v1"
    payload["target_kind"] = kind
    payload["boundaries"] = ["per_process_only", "not_persisted_globally"]
    return payload


def snapshot() -> dict[str, Any]:
    policy = network_policy.snapshot()
    component = run_json([sys.executable, str(BRIDGE / "network_gateway_component_lab.py"), "snapshot"], timeout=30)
    clash = run_json([sys.executable, str(BRIDGE / "clash_mihomo_control.py"), "gateway-status"], timeout=30)
    clash_metrics = run_json([sys.executable, str(BRIDGE / "clash_node_metrics.py"), "snapshot"], timeout=20)
    leases = run_json([sys.executable, str(BRIDGE / "network_gateway_leases.py"), "status"], timeout=20)
    route_cache = route_cache_snapshot(limit=20)
    core_ok = (
        bool(policy.get("ok"))
        and bool(component.get("ok"))
        and bool(clash_metrics.get("ok"))
        and bool(leases.get("ok"))
        and bool(route_cache.get("ok"))
    )
    warnings: list[dict[str, str]] = []
    if not clash.get("ok"):
        warnings.append(
            {
                "code": "clash_control_unavailable",
                "severity": "warning",
                "reason": str(clash.get("reason") or clash.get("error") or "clash gateway-status unavailable")[:300],
            }
        )
    return {
        "schema": f"{SCHEMA_PREFIX}.snapshot.v1",
        "ok": core_ok,
        "generated_at": now_iso(),
        "warnings": warnings,
        "codex_model_api": {
            "config_path": str(CODEX_CONFIG_PATH),
            "base_url_present": bool(codex_config_base_url()),
            "probe_target": codex_model_probe_url(),
            "protected_primary_route": True,
            "secret_values_returned": False,
            "rule": "current Codex model API may be third-party; do not treat it as official OpenAI or mutate its production route automatically",
        },
        "network_policy": policy,
        "component_lab": component,
        "clash": {
            "ok": clash.get("ok", False),
            "degraded": not bool(clash.get("ok")),
            "current_group": (clash.get("recommended_group") or {}).get("name", ""),
            "current_node": (clash.get("recommended_group") or {}).get("now", ""),
            "capabilities": clash.get("capabilities", {}),
            "reason": str(clash.get("reason") or clash.get("error") or "")[:300],
            "controller_diagnosis": clash.get("controller_diagnosis", {}),
            "secret_values_returned": False,
        },
        "clash_node_metrics": {
            "ok": clash_metrics.get("ok", False),
            "cache_exists": clash_metrics.get("cache_exists", False),
            "node_count": clash_metrics.get("node_count", 0),
            "updated_at": clash_metrics.get("updated_at", ""),
        },
        "leases": {
            "ok": leases.get("ok", False),
            "active_count": leases.get("count", 0),
            "runtime_owned": True,
            "writes_global_network_state": False,
        },
        "route_cache": {
            "ok": route_cache.get("ok", False),
            "decision_count": route_cache.get("decision_count", 0),
            "observation_count": route_cache.get("observation_count", 0),
            "db_path": route_cache.get("db_path", ""),
        },
        "safety": {
            "daemon": False,
            "writes_system_proxy": False,
            "writes_dns": False,
            "writes_clash_config": False,
            "changes_codex_conversation_route": False,
        },
    }


def smoke(
    *,
    mode: str,
    target_kind: str,
    target: str,
    timeout: int,
    group: str,
    node: str,
    upstream_proxy: str,
) -> dict[str, Any]:
    kind = normalize_target_kind(target_kind, target)
    resolved_target = target_for_kind(kind, target)
    selected_mode = str(mode or "current").strip().lower()
    proxy_url = upstream_proxy or network_policy.best_proxy_url()
    if selected_mode == "current":
        result = proxy_check(proxy_url, resolved_target, timeout=timeout) if proxy_url else {"ok": False, "reason": "no_proxy_candidate"}
    elif selected_mode == "proxy-chain":
        result = run_json(
            [
                sys.executable,
                str(BRIDGE / "network_gateway_component_lab.py"),
                "proxy-chain-smoke",
                "--upstream-proxy",
                proxy_url,
                "--test-url",
                resolved_target,
                "--timeout",
                str(timeout),
            ],
            timeout=max(timeout + 30, 60),
        )
    elif selected_mode == "isolated":
        if kind not in SAFE_ISOLATED_TARGETS:
            result = {"ok": False, "reason": "isolated_mode_not_allowed_for_target_kind", "target_kind": kind}
        else:
            selected_node = node or DEFAULT_NODE_HINTS.get(kind, "")
            result = run_json(
                [
                    sys.executable,
                    str(BRIDGE / "clash_mihomo_control.py"),
                    "isolated-probe",
                    "--group",
                    group,
                    "--node",
                    selected_node,
                    "--target",
                    kind,
                    "--method",
                    "HEAD",
                    "--timeout-seconds",
                    str(timeout),
                    "--max-bytes",
                    "1024",
                    "--save-report",
                ],
                timeout=max(timeout + 30, 60),
            )
    else:
        result = {"ok": False, "reason": "unsupported_smoke_mode", "mode": selected_mode}
    latency_ms = result.get("elapsed_ms") or result.get("latency_ms") or result.get("duration_ms") or 0
    if not isinstance(latency_ms, (int, float)):
        latency_ms = 0
    error_class = ""
    if not result.get("ok"):
        error_class = str(result.get("reason") or result.get("error") or result.get("stderr_tail") or "request_failed")[:160]
    route_observation = record_route_observation(
        target_kind=kind,
        target=resolved_target,
        runtime="generic",
        route_mode=selected_mode,
        ok=bool(result.get("ok")),
        latency_ms=float(latency_ms or 0),
        error_class=error_class,
        node=node or str(result.get("node") or ""),
    )
    payload = {
        "schema": f"{SCHEMA_PREFIX}.smoke.v1",
        "ok": bool(result.get("ok")),
        "generated_at": now_iso(),
        "mode": selected_mode,
        "target_kind": kind,
        "target": resolved_target,
        "result": result,
        "route_observation": route_observation,
        "writes_system_proxy": False,
        "writes_dns": False,
        "writes_clash_config": False,
        "changes_codex_conversation_route": False,
    }
    write_report(payload)
    return payload


def write_report(payload: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNTIME_DIR / "last_smoke.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    payload["saved_report"] = str(path)


def lease_start(
    *,
    target_kind: str,
    group: str,
    node: str,
    ttl_seconds: int,
    check_url: str,
    check_method: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    kind = normalize_target_kind(target_kind, "")
    if kind in PROTECTED_CODEX_TARGETS:
        return {
            "schema": f"{SCHEMA_PREFIX}.lease_start.v1",
            "ok": False,
            "reason": "protected_codex_target_cannot_use_isolated_lease",
            "target_kind": kind,
            "changes_codex_conversation_route": False,
        }
    node_recommendation = clash_node_recommendation(kind, refresh_if_stale=True)
    selected_node = node or str(node_recommendation.get("recommended_node") or "")
    command = [
        sys.executable,
        str(BRIDGE / "network_gateway_leases.py"),
        "start-isolated",
        "--target-kind",
        kind,
        "--group",
        group,
        "--ttl-seconds",
        str(ttl_seconds),
        "--timeout-seconds",
        str(timeout_seconds),
        "--check-method",
        check_method,
    ]
    if selected_node:
        command.extend(["--node", selected_node])
    if check_url:
        command.extend(["--check-url", check_url])
    result = run_json(command, timeout=max(timeout_seconds + 35, 60))
    result["schema"] = f"{SCHEMA_PREFIX}.lease_start.v1"
    result["target_kind"] = kind
    result["clash_node_recommendation"] = node_recommendation
    result["boundaries"] = [
        "no_system_proxy_or_dns_mutation",
        "no_main_clash_node_switch",
        "no_codex_conversation_route_change",
        "lease_must_be_stopped_or_expire",
    ]
    return result


def lease_status(lease_id: str = "") -> dict[str, Any]:
    command = [sys.executable, str(BRIDGE / "network_gateway_leases.py"), "status"]
    if lease_id:
        command.extend(["--lease-id", lease_id])
    result = run_json(command, timeout=20)
    result["schema"] = f"{SCHEMA_PREFIX}.lease_status.v1"
    return result


def lease_stop(lease_id: str) -> dict[str, Any]:
    result = run_json([sys.executable, str(BRIDGE / "network_gateway_leases.py"), "stop", "--lease-id", lease_id], timeout=30)
    result["schema"] = f"{SCHEMA_PREFIX}.lease_stop.v1"
    return result


def lease_cleanup() -> dict[str, Any]:
    result = run_json([sys.executable, str(BRIDGE / "network_gateway_leases.py"), "cleanup"], timeout=30)
    result["schema"] = f"{SCHEMA_PREFIX}.lease_cleanup.v1"
    return result


def validate() -> dict[str, Any]:
    snap = snapshot()
    github_plan = route_plan(
        target_kind="github",
        target="",
        runtime="python",
        isolation="auto",
        group=DEFAULT_CLASH_GROUP,
        node="",
    )
    package_env = env_for_request("package", "", "node")
    metrics_validation = run_json([sys.executable, str(BRIDGE / "clash_node_metrics.py"), "validate"], timeout=30)
    lease_validation = run_json([sys.executable, str(BRIDGE / "network_gateway_leases.py"), "validate"], timeout=30)
    route_cache_validation = validate_route_cache()
    issues: list[dict[str, str]] = []
    if not snap.get("ok"):
        issues.append({"severity": "risk", "code": "snapshot_not_ok"})
    if snap.get("clash", {}).get("degraded"):
        diagnosis = snap.get("clash", {}).get("controller_diagnosis") if isinstance(snap.get("clash"), dict) else {}
        issues.append(
            {
                "severity": "warning",
                "code": "clash_control_degraded",
                "root_cause": str((diagnosis or {}).get("root_cause") or ""),
                "next_action": str((diagnosis or {}).get("next_action") or ""),
            }
        )
    if github_plan.get("plan", {}).get("target_kind") != "github":
        issues.append({"severity": "risk", "code": "github_plan_wrong_kind"})
    if not package_env.get("env"):
        issues.append({"severity": "risk", "code": "package_env_empty"})
    if not lease_validation.get("ok"):
        issues.append({"severity": "risk", "code": "lease_validation_failed"})
    if not metrics_validation.get("ok"):
        issues.append({"severity": "risk", "code": "clash_node_metrics_validation_failed"})
    if not route_cache_validation.get("ok"):
        issues.append({"severity": "risk", "code": "route_cache_validation_failed"})
    for target_kind in ("paper", "image", "dataset", "web"):
        if not target_for_kind(target_kind):
            issues.append({"severity": "risk", "code": f"{target_kind}_default_target_missing"})
    sample_batch = batch_plan(
        [
            {"target_kind": "github", "target": "https://github.com/openai/codex", "owner_tool": "github", "runtime": "python"},
            {"target_kind": "github", "target": "https://github.com/openai/codex", "owner_tool": "browser", "runtime": "python"},
            {"target_kind": "paper", "runtime": "python"},
            {"target_kind": "image", "runtime": "generic"},
            {"target_kind": "dataset", "runtime": "python"},
            {"target_kind": "web", "runtime": "generic"},
        ],
        probe=False,
    )
    batch_owner_tools = [str(item.get("owner_tool") or "") for item in sample_batch.get("results", [])[:2]]
    batch_owner_tool_dimension_ok = batch_owner_tools == ["github", "browser"]
    if not batch_owner_tool_dimension_ok:
        issues.append({"severity": "risk", "code": "batch_plan_owner_tool_dimension_lost"})
    return {
        "schema": f"{SCHEMA_PREFIX}.validate.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "issues": issues,
        "snapshot_ok": snap.get("ok", False),
        "github_plan_mode": github_plan.get("plan", {}).get("route_mode", ""),
        "package_env_keys": sorted(package_env.get("env", {}).keys()),
        "lease_validation_ok": lease_validation.get("ok", False),
        "clash_node_metrics_ok": metrics_validation.get("ok", False),
        "route_cache_ok": route_cache_validation.get("ok", False),
        "target_stats_sample": route_cache_target_stats("github", "https://github.com/openai/codex", "python", owner_tool="github", limit=3).get("rows", []),
        "batch_plan_ok": sample_batch.get("ok", False),
        "batch_plan_cache_hit_count": sample_batch.get("cache_hit_count", 0),
        "batch_plan_owner_tool_dimension_ok": batch_owner_tool_dimension_ok,
        "active_lease_count": lease_validation.get("active_lease_count", 0),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Thin Codex network gateway control plane")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("interfaces")
    plan = sub.add_parser("plan")
    plan.add_argument("--target-kind", default="")
    plan.add_argument("--target", default="")
    plan.add_argument("--runtime", default="generic")
    plan.add_argument("--owner-tool", default="")
    plan.add_argument("--isolation", default="auto", choices=("auto", "never", "prefer", "required", "wrapper"))
    plan.add_argument("--group", default=DEFAULT_CLASH_GROUP)
    plan.add_argument("--node", default="")
    plan.add_argument("--probe", action="store_true")
    plan.add_argument("--force-probe", action="store_true")
    plan.add_argument("--probe-timeout", type=int, default=12)
    plan.add_argument("--fresh-ttl-seconds", type=int, default=DEFAULT_FRESH_TTL_SECONDS)
    plan.add_argument("--stale-ttl-seconds", type=int, default=DEFAULT_STALE_TTL_SECONDS)
    batch = sub.add_parser("batch-plan")
    batch.add_argument("--requests-json", default="")
    batch.add_argument("--requests-file", default="")
    batch.add_argument("--runtime", default="generic")
    batch.add_argument("--isolation", default="auto", choices=("auto", "never", "prefer", "required", "wrapper"))
    batch.add_argument("--group", default=DEFAULT_CLASH_GROUP)
    batch.add_argument("--probe", action="store_true")
    batch.add_argument("--force-probe", action="store_true")
    batch.add_argument("--total-timeout-seconds", type=float, default=0.0)
    env_cmd = sub.add_parser("env")
    env_cmd.add_argument("--target-kind", default="")
    env_cmd.add_argument("--target", default="")
    env_cmd.add_argument("--runtime", default="generic")
    smoke_cmd = sub.add_parser("smoke")
    smoke_cmd.add_argument("--mode", default="current", choices=("current", "proxy-chain", "isolated"))
    smoke_cmd.add_argument("--target-kind", default="github")
    smoke_cmd.add_argument("--target", default="")
    smoke_cmd.add_argument("--timeout", type=int, default=20)
    smoke_cmd.add_argument("--group", default=DEFAULT_CLASH_GROUP)
    smoke_cmd.add_argument("--node", default="")
    smoke_cmd.add_argument("--upstream-proxy", default="")
    lease_start_cmd = sub.add_parser("lease-start")
    lease_start_cmd.add_argument("--target-kind", default="external")
    lease_start_cmd.add_argument("--group", default=DEFAULT_CLASH_GROUP)
    lease_start_cmd.add_argument("--node", default="")
    lease_start_cmd.add_argument("--ttl-seconds", type=int, default=300)
    lease_start_cmd.add_argument("--check-url", default="")
    lease_start_cmd.add_argument("--check-method", default="HEAD", choices=("GET", "HEAD"))
    lease_start_cmd.add_argument("--timeout-seconds", type=int, default=12)
    lease_status_cmd = sub.add_parser("lease-status")
    lease_status_cmd.add_argument("--lease-id", default="")
    lease_stop_cmd = sub.add_parser("lease-stop")
    lease_stop_cmd.add_argument("--lease-id", required=True)
    sub.add_parser("lease-cleanup")
    sub.add_parser("validate")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.cmd == "snapshot":
            emit(snapshot())
        elif args.cmd == "interfaces":
            emit(request_interfaces())
        elif args.cmd == "plan":
            emit(
                route_plan(
                    target_kind=args.target_kind,
                    target=args.target,
                    runtime=args.runtime,
                    owner_tool=args.owner_tool,
                    isolation=args.isolation,
                    group=args.group,
                    node=args.node,
                    probe=args.probe,
                    probe_timeout=args.probe_timeout,
                    force_probe=args.force_probe,
                    fresh_ttl_seconds=args.fresh_ttl_seconds,
                    stale_ttl_seconds=args.stale_ttl_seconds,
                )
            )
        elif args.cmd == "batch-plan":
            if args.requests_file:
                raw = Path(args.requests_file).expanduser().read_text(encoding="utf-8")
            else:
                raw = args.requests_json or "[]"
            decoded = json.loads(raw)
            requests = decoded.get("requests") if isinstance(decoded, dict) else decoded
            if not isinstance(requests, list):
                raise ValueError("batch-plan input must be a list or an object with requests")
            emit(batch_plan(requests, probe=args.probe, force_probe=args.force_probe, default_runtime=args.runtime, isolation=args.isolation, group=args.group, total_budget_seconds=args.total_timeout_seconds))
        elif args.cmd == "env":
            emit(env_for_request(args.target_kind, args.target, args.runtime))
        elif args.cmd == "smoke":
            emit(smoke(mode=args.mode, target_kind=args.target_kind, target=args.target, timeout=args.timeout, group=args.group, node=args.node, upstream_proxy=args.upstream_proxy))
        elif args.cmd == "lease-start":
            emit(lease_start(target_kind=args.target_kind, group=args.group, node=args.node, ttl_seconds=args.ttl_seconds, check_url=args.check_url, check_method=args.check_method, timeout_seconds=args.timeout_seconds))
        elif args.cmd == "lease-status":
            emit(lease_status(args.lease_id))
        elif args.cmd == "lease-stop":
            emit(lease_stop(args.lease_id))
        elif args.cmd == "lease-cleanup":
            emit(lease_cleanup())
        elif args.cmd == "validate":
            emit(validate())
        return 0
    except Exception as exc:
        emit({"schema": f"{SCHEMA_PREFIX}.error.v1", "ok": False, "reason": str(exc)[:500]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
