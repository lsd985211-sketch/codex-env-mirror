"""Resource-layer network execution package helpers.

Ownership: translate Codex network gateway plans into compact execution
packages that resource fetchers, command adapters, and owner-tool handoffs can
consume.
Non-goals: deciding network policy, starting long-lived daemons, mutating system
proxy/DNS, changing Clash configuration, or bypassing owner-tool permissions.
State behavior: read-only; this module does not write files or start leases.
Caller context: `resource_broker.py`, `resource_fetcher.py`, and future
network-aware command adapters that need per-request env/unset evidence.
"""

from __future__ import annotations

import argparse
import json
from typing import Any


PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def execution_package_from_gateway_plan(gateway_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return a stable resource execution package derived from a gateway plan."""
    if not isinstance(gateway_plan, dict) or not gateway_plan.get("ok"):
        return {
            "schema": "resource_network_execution.package.v1",
            "ok": False,
            "reason": "missing_or_failed_gateway_plan",
            "env": {},
            "unset_env": [],
            "route_mode": "",
            "requires_live_lease": False,
        }
    plan = gateway_plan.get("plan") if isinstance(gateway_plan.get("plan"), dict) else {}
    runtime_env = gateway_plan.get("runtime_env") if isinstance(gateway_plan.get("runtime_env"), dict) else {}
    env = _string_dict(plan.get("env") or runtime_env.get("env"))
    unset_env = [str(item) for item in plan.get("unset_env") or []]
    route_mode = str(plan.get("route_mode") or "")
    proxy_url = str(plan.get("proxy_url") or env.get("HTTPS_PROXY") or env.get("HTTP_PROXY") or "")
    lease_kind = str(plan.get("lease_kind") or "")
    return {
        "schema": "resource_network_execution.package.v1",
        "ok": True,
        "route_mode": route_mode,
        "target_kind": str(plan.get("target_kind") or ""),
        "target": str(plan.get("target") or ""),
        "env": env,
        "unset_env": unset_env,
        "proxy_url": proxy_url,
        "lease_kind": lease_kind,
        "lease_command": str(plan.get("lease_command") or ""),
        "requires_live_lease": route_mode in {"isolated_mihomo", "proxy_chain_wrapper"},
        "can_apply_env_directly": bool(env) and route_mode not in {"probe_no_working_route", "protected_primary_route"},
        "boundaries": list(plan.get("boundaries") or []),
        "cleanup": str(plan.get("cleanup") or ""),
        "secret_values_returned": False,
    }


def route_summary_from_gateway_plan(gateway_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return the compact route evidence that callers should read first."""
    package = execution_package_from_gateway_plan(gateway_plan)
    if not package.get("ok"):
        return {
            "schema": "resource_network_execution.route_summary.v1",
            "ok": False,
            "reason": package.get("reason", "no_network_package"),
            "target_kind": "",
            "route_mode": "",
            "preferred_route": "",
            "direct_ok": None,
            "proxy_ok": None,
        }
    probe = gateway_plan.get("probe") if isinstance(gateway_plan, dict) and isinstance(gateway_plan.get("probe"), dict) else {}
    classification = probe.get("classification") if isinstance(probe.get("classification"), dict) else {}
    direct = probe.get("direct") if isinstance(probe.get("direct"), dict) else {}
    proxy = probe.get("proxy") if isinstance(probe.get("proxy"), dict) else {}
    return {
        "schema": "resource_network_execution.route_summary.v1",
        "ok": True,
        "target_kind": package.get("target_kind", ""),
        "target": package.get("target", ""),
        "route_mode": package.get("route_mode", ""),
        "preferred_route": classification.get("preferred_route", ""),
        "direct_ok": direct.get("ok") if direct else None,
        "proxy_ok": proxy.get("ok") if proxy else None,
        "direct_total": direct.get("total") if direct else None,
        "proxy_total": proxy.get("total") if proxy else None,
        "proxy_present": bool(package.get("proxy_url")),
        "env_keys": sorted((package.get("env") or {}).keys()),
        "unset_env": package.get("unset_env", []),
        "requires_live_lease": package.get("requires_live_lease", False),
        "can_apply_env_directly": package.get("can_apply_env_directly", False),
        "secret_values_returned": False,
    }


def owner_execution_contract(owner_tool: str, gateway_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Describe how an owner MCP/tool should consume the network package."""
    package = execution_package_from_gateway_plan(gateway_plan)
    summary = route_summary_from_gateway_plan(gateway_plan)
    if not package.get("ok"):
        return {
            "schema": "resource_network_execution.owner_contract.v1",
            "ok": False,
            "owner_tool": owner_tool,
            "reason": package.get("reason", "no_network_package"),
            "route_summary": summary,
        }
    return {
        "schema": "resource_network_execution.owner_contract.v1",
        "ok": True,
        "owner_tool": owner_tool,
        "execution_package": package,
        "route_summary": summary,
        "suggested_env": package.get("env", {}),
        "unset_env": package.get("unset_env", []),
        "requires_live_lease": package.get("requires_live_lease", False),
        "can_apply_env_directly": package.get("can_apply_env_directly", False),
        "next_action": "start_or_request_lease_then_call_owner_tool"
        if package.get("requires_live_lease")
        else "call_owner_tool_with_execution_env",
        "permission_boundary": "owner_tool_required",
        "rule": "network guidance only; owner tool permission boundary remains unchanged",
    }


def url_attempt_specs_from_package(package: dict[str, Any]) -> list[dict[str, str]] | None:
    """Convert a package into URL fetch route specs, or None for legacy fallback."""
    if not package.get("ok"):
        return None
    route_mode = str(package.get("route_mode") or "")
    proxy_url = str(package.get("proxy_url") or "")
    if route_mode in {"probe_selected_direct", "direct"}:
        return [{"route": "gateway_direct", "proxy_url": ""}]
    if route_mode in {"probe_selected_proxy", "current_proxy_env"}:
        if proxy_url:
            return [{"route": "gateway_proxy", "proxy_url": proxy_url}]
        return None
    if route_mode == "probe_no_working_route":
        return []
    return None


def url_attempt_specs_from_gateway_plan(gateway_plan: dict[str, Any] | None) -> list[dict[str, str]] | None:
    """Convert a full gateway plan into URL route specs.

    The network layer owns whether a target route is known-bad. Resource
    callers consume that signal by returning no attempts instead of spending
    the request timeout on a route the gateway already probed as unavailable.
    """

    if not isinstance(gateway_plan, dict):
        return None
    plan = gateway_plan.get("plan") if isinstance(gateway_plan.get("plan"), dict) else {}
    probe = gateway_plan.get("probe") if isinstance(gateway_plan.get("probe"), dict) else {}
    if str(plan.get("route_mode") or "") == "probe_no_working_route":
        return []
    if probe and not probe.get("ok"):
        return []
    return url_attempt_specs_from_package(execution_package_from_gateway_plan(gateway_plan))


def owner_tool_handoff_metadata(gateway_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Return network guidance that Codex can pass to an owner MCP/tool call."""
    contract = owner_execution_contract("", gateway_plan)
    if not contract.get("ok"):
        return {"ok": False, "reason": contract.get("reason", "no_network_package")}
    return {
        "ok": True,
        "execution_package": contract.get("execution_package", {}),
        "route_summary": contract.get("route_summary", {}),
        "suggested_env": contract.get("suggested_env", {}),
        "unset_env": contract.get("unset_env", []),
        "requires_live_lease": contract.get("requires_live_lease", False),
        "next_action": "call_owner_tool_with_suggested_env_or_request_live_lease"
        if contract.get("requires_live_lease")
        else "call_owner_tool_with_suggested_env",
        "rule": contract.get("rule", "network guidance only; owner tool permission boundary remains unchanged"),
    }


def apply_execution_env(base_env: dict[str, str], package: dict[str, Any]) -> dict[str, str]:
    """Apply a resource network package to a subprocess environment."""
    env = dict(base_env)
    for key in package.get("unset_env") or []:
        env.pop(str(key), None)
    for key, value in _string_dict(package.get("env")).items():
        env[key] = value
    return env


def validate() -> dict[str, Any]:
    direct = execution_package_from_gateway_plan(
        {
            "ok": True,
            "plan": {
                "route_mode": "probe_selected_direct",
                "env": {"CODEX_NETWORK_CONTEXT": "test"},
                "unset_env": ["HTTP_PROXY"],
            },
        }
    )
    proxy = execution_package_from_gateway_plan(
        {
            "ok": True,
            "plan": {
                "route_mode": "current_proxy_env",
                "env": {"HTTPS_PROXY": "http://127.0.0.1:7897"},
            },
        }
    )
    return {
        "schema": "resource_network_execution.validate.v1",
        "ok": bool(
            direct.get("ok")
            and proxy.get("ok")
            and url_attempt_specs_from_package(proxy)
            and route_summary_from_gateway_plan({"ok": True, "plan": {"route_mode": "probe_selected_direct"}}).get("ok")
            and owner_execution_contract("github", {"ok": True, "plan": {"route_mode": "current_proxy_env"}}).get("ok")
        ),
        "direct_route": url_attempt_specs_from_package(direct),
        "proxy_route": url_attempt_specs_from_package(proxy),
        "summary_schema": "resource_network_execution.route_summary.v1",
        "owner_contract_schema": "resource_network_execution.owner_contract.v1",
        "writes_global_network_state": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resource network execution package helpers")
    parser.add_argument("command", choices=("validate",), nargs="?", default="validate")
    args = parser.parse_args()
    if args.command == "validate":
        print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
