#!/usr/bin/env python3
"""Hub adapter for the Codex network gateway control plane.

Ownership: translate Hub `network_gateway.*` tool arguments into bounded
`codex_network_gateway.py` CLI calls.
Non-goals: route policy decisions, network probing implementation, system
proxy/DNS changes, Clash config edits, or Hub tool registration.
State behavior: read-only except delegated gateway smoke/report files and
explicit isolated proxy leases owned by `network_gateway_leases.py`.
Caller context: imported by `local_mcp_hub.py` to keep Hub dispatch thin.
"""

from __future__ import annotations

from typing import Any, Callable


JsonCommand = Callable[[list[str], int], dict[str, Any]]


def network_gateway_call(name: str, arguments: dict[str, Any], run_json_command: JsonCommand) -> dict[str, Any] | None:
    if not name.startswith("network_gateway."):
        return None
    gateway_command = name.split(".", 1)[1].replace("_", "-")
    command = ["python", "_bridge\\codex_network_gateway.py", gateway_command]
    timeout = 20
    if name == "network_gateway.plan":
        target_kind = str(arguments.get("target_kind") or "").strip()
        target = str(arguments.get("target") or "").strip()
        runtime = str(arguments.get("runtime") or "generic").strip() or "generic"
        owner_tool = str(arguments.get("owner_tool") or "").strip()
        isolation = str(arguments.get("isolation") or "auto").strip() or "auto"
        group = str(arguments.get("group") or "").strip()
        node = str(arguments.get("node") or "").strip()
        command.extend(["--target-kind", target_kind, "--runtime", runtime, "--isolation", isolation])
        if owner_tool:
            command.extend(["--owner-tool", owner_tool])
        if target:
            command.extend(["--target", target])
        if group:
            command.extend(["--group", group])
        if node:
            command.extend(["--node", node])
        if bool(arguments.get("probe")):
            probe_timeout = int(arguments.get("probe_timeout") or 12)
            command.extend(["--probe", "--probe-timeout", str(probe_timeout)])
            timeout = max(probe_timeout + 25, 45)
    elif name == "network_gateway.env":
        target_kind = str(arguments.get("target_kind") or "").strip()
        target = str(arguments.get("target") or "").strip()
        runtime = str(arguments.get("runtime") or "generic").strip() or "generic"
        command.extend(["--target-kind", target_kind, "--runtime", runtime])
        if target:
            command.extend(["--target", target])
    elif name == "network_gateway.smoke":
        mode = str(arguments.get("mode") or "current").strip() or "current"
        target_kind = str(arguments.get("target_kind") or "github").strip() or "github"
        target = str(arguments.get("target") or "").strip()
        timeout_seconds = int(arguments.get("timeout") or 20)
        group = str(arguments.get("group") or "").strip()
        node = str(arguments.get("node") or "").strip()
        upstream_proxy = str(arguments.get("upstream_proxy") or "").strip()
        command.extend(["--mode", mode, "--target-kind", target_kind, "--timeout", str(timeout_seconds)])
        if target:
            command.extend(["--target", target])
        if group:
            command.extend(["--group", group])
        if node:
            command.extend(["--node", node])
        if upstream_proxy:
            command.extend(["--upstream-proxy", upstream_proxy])
        timeout = max(timeout_seconds + 40, 70)
    elif name == "network_gateway.lease_start":
        target_kind = str(arguments.get("target_kind") or "external").strip() or "external"
        group = str(arguments.get("group") or "").strip()
        node = str(arguments.get("node") or "").strip()
        ttl_seconds = int(arguments.get("ttl_seconds") or 300)
        check_url = str(arguments.get("check_url") or "").strip()
        check_method = str(arguments.get("check_method") or "HEAD").strip() or "HEAD"
        timeout_seconds = int(arguments.get("timeout_seconds") or 12)
        command.extend(["--target-kind", target_kind, "--ttl-seconds", str(ttl_seconds), "--check-method", check_method, "--timeout-seconds", str(timeout_seconds)])
        if group:
            command.extend(["--group", group])
        if node:
            command.extend(["--node", node])
        if check_url:
            command.extend(["--check-url", check_url])
        timeout = max(timeout_seconds + 45, 70)
    elif name == "network_gateway.lease_status":
        lease_id = str(arguments.get("lease_id") or "").strip()
        if lease_id:
            command.extend(["--lease-id", lease_id])
        timeout = 30
    elif name == "network_gateway.lease_stop":
        lease_id = str(arguments.get("lease_id") or "").strip()
        if not lease_id:
            return {"ok": False, "reason": "lease_id_required", "tool": name}
        command.extend(["--lease-id", lease_id])
        timeout = 40
    elif name == "network_gateway.lease_cleanup":
        timeout = 40
    elif name == "network_gateway.interfaces":
        timeout = 30
    elif name == "network_gateway.snapshot":
        timeout = 30
    elif name == "network_gateway.validate":
        timeout = 30
    else:
        return {"ok": False, "reason": "unknown_network_gateway_tool", "tool": name}
    return run_json_command(command, timeout)
