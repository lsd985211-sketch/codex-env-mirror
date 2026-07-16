#!/usr/bin/env python3
"""Controlled local mihomo external-controller client.

Ownership: localhost Clash Verge Rev / mihomo external-controller inspection and
bounded node switching for the Codex network gateway lab.
Non-goals: editing subscriptions, changing system proxy/DNS, exposing secrets,
or replacing Clash Verge's own configuration UI.
State behavior: read-only by default; switch writes only through the local
mihomo HTTP API and records the previous group selection for restore.
Caller context: Codex network diagnostics, resource acquisition route tests,
and explicit user-approved node switching.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from secret_vault import get_secret


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "_bridge" / "runtime" / "clash_mihomo"
STATE_PATH = RUNTIME_DIR / "last_switch.json"
LAST_ASSESSMENT_PATH = RUNTIME_DIR / "last_assessment.json"
DEFAULT_BASE_URL = "http://127.0.0.1:9090"
DEFAULT_VERGE_HOME = Path(os.environ.get("APPDATA", "")) / "io.github.clash-verge-rev.clash-verge-rev"
DEFAULT_VERGE_CONFIG = DEFAULT_VERGE_HOME / "clash-verge.yaml"
DEFAULT_VERGE_GENERATED_CONFIG = DEFAULT_VERGE_HOME / "config.yaml"
SECRET_ALIAS = "clash.mihomo.secret"
SCHEMA_PREFIX = "clash_mihomo_control"
NOISE_NODE_PREFIXES = (
    "剩余流量",
    "距离下次重置",
    "套餐到期",
    "官网",
    "客服邮箱",
)
POLICY_NODE_NAMES = {"自动选择", "故障转移"}
DEFAULT_TEST_URL = "https://www.gstatic.com/generate_204"
TARGET_TEST_URLS = {
    "generic": (DEFAULT_TEST_URL,),
    "openai": ("https://chatgpt.com/", "https://api.openai.com/v1/models"),
    "github": ("https://github.com/", "https://api.github.com/"),
    "package": ("https://registry.npmjs.org/", "https://pypi.org/simple/requests/"),
    "docs": ("https://learn.microsoft.com/",),
    "browser": ("https://example.com/",),
    "paper": ("https://api.openalex.org/works?search=artificial%20intelligence&per-page=1", "https://export.arxiv.org/api/query?search_query=all:ai&max_results=1"),
    "image": ("https://commons.wikimedia.org/", "https://api.openverse.org/v1/images/?q=architecture&page_size=1"),
    "dataset": ("https://huggingface.co/datasets", "https://zenodo.org/api/records/?q=machine%20learning&size=1"),
    "web": ("https://example.com/", DEFAULT_TEST_URL),
    "external": (DEFAULT_TEST_URL,),
}
SITE_PROFILES = {
    "github": ("https://github.com/", "https://api.github.com/"),
    "openai": ("https://chatgpt.com/", "https://api.openai.com/v1/models"),
    "npm": ("https://registry.npmjs.org/",),
    "pypi": ("https://pypi.org/simple/requests/",),
    "microsoft_docs": ("https://learn.microsoft.com/",),
    "openalex": ("https://api.openalex.org/works?search=artificial%20intelligence&per-page=1",),
    "arxiv": ("https://export.arxiv.org/api/query?search_query=all:ai&max_results=1",),
    "wikimedia_commons": ("https://commons.wikimedia.org/",),
    "openverse": ("https://api.openverse.org/v1/images/?q=architecture&page_size=1",),
    "huggingface_datasets": ("https://huggingface.co/datasets",),
    "zenodo": ("https://zenodo.org/api/records/?q=machine%20learning&size=1",),
    "generic_web": ("https://example.com/", DEFAULT_TEST_URL),
}


class ClashControlError(RuntimeError):
    pass


@dataclass(frozen=True)
class SwitchTarget:
    base_url: str
    group: str
    node: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def parse_controller_url(value: str) -> str:
    text = str(value or "").strip().strip("'\"")
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return text.rstrip("/")
    return f"http://{text}".rstrip("/")


def controller_host_port(base_url: str) -> tuple[str, int] | None:
    parsed = urllib.parse.urlparse(parse_controller_url(base_url))
    if not parsed.hostname or not parsed.port:
        return None
    return parsed.hostname, int(parsed.port)


def tcp_reachable(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def controller_config_summary(path: Path) -> dict[str, Any]:
    item: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return item
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception as exc:
        item["error"] = str(exc)[:200]
        return item
    controller = parse_controller_url(str(data.get("external-controller") or ""))
    host_port = controller_host_port(controller) if controller else None
    item.update(
        {
            "mixed_port": data.get("mixed-port"),
            "external_controller": controller,
            "external_controller_present": bool(controller),
            "external_controller_reachable": bool(host_port and tcp_reachable(*host_port)),
            "external_controller_pipe_present": bool(data.get("external-controller-pipe")),
            "secret_present": bool(data.get("secret")),
            "secret_is_default_placeholder": str(data.get("secret") or "") == "set-your-secret",
            "allow_lan": data.get("allow-lan"),
            "mode": data.get("mode"),
        }
    )
    return item


def controller_diagnosis(base_url: str) -> dict[str, Any]:
    requested = parse_controller_url(base_url or DEFAULT_BASE_URL)
    requested_host_port = controller_host_port(requested)
    requested_reachable = bool(requested_host_port and tcp_reachable(*requested_host_port))
    config_paths = [
        DEFAULT_VERGE_CONFIG,
        DEFAULT_VERGE_GENERATED_CONFIG,
        Path.home() / ".config" / "clash" / "config.yaml",
    ]
    configs = [controller_config_summary(path) for path in config_paths]
    reachable_config_controllers = [
        item.get("external_controller")
        for item in configs
        if item.get("external_controller") and item.get("external_controller_reachable")
    ]
    configured_controllers = [item.get("external_controller") for item in configs if item.get("external_controller")]
    pipe_present = any(bool(item.get("external_controller_pipe_present")) for item in configs)
    primary = configs[0] if configs else {}
    if requested_reachable:
        root_cause = "requested_controller_reachable"
        next_action = "use_requested_controller"
    elif reachable_config_controllers:
        root_cause = "default_base_url_mismatch"
        next_action = "retry_with_reachable_configured_controller"
    elif not primary.get("external_controller_present") and pipe_present:
        root_cause = "tcp_external_controller_disabled_pipe_only"
        next_action = "keep_gateway_degraded_or_enable_local_tcp_controller_in_clash_ui_if_switching_is_needed"
    elif configured_controllers:
        root_cause = "configured_tcp_controller_not_listening"
        next_action = "restart_clash_verge_or_enable_external_controller_before using controller operations"
    else:
        root_cause = "no_tcp_external_controller_configured"
        next_action = "enable_localhost_external_controller_only_if controller operations are required"
    return {
        "schema": f"{SCHEMA_PREFIX}.controller_diagnosis.v1",
        "ok": requested_reachable,
        "requested_base_url": requested,
        "requested_reachable": requested_reachable,
        "configured_controllers": configured_controllers,
        "reachable_configured_controllers": reachable_config_controllers,
        "pipe_controller_present": pipe_present,
        "root_cause": root_cause,
        "next_action": next_action,
        "config_summaries": configs,
        "secret_values_returned": False,
    }


def auth_headers(secret_value: str = "") -> dict[str, str]:
    secret = secret_value or get_secret(SECRET_ALIAS)
    if not secret:
        raise ClashControlError("missing clash.mihomo.secret")
    return {"Authorization": f"Bearer {secret}"}


def api_request(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: int = 8,
    secret_value: str = "",
) -> Any:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = None
    headers = {"Accept": "application/json", **auth_headers(secret_value)}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ClashControlError(f"http_{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ClashControlError(f"connection_failed: {exc.reason}") from exc
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def controller_config(base_url: str) -> dict[str, Any]:
    payload = api_request(base_url, "GET", "/configs")
    if not isinstance(payload, dict):
        raise ClashControlError("invalid /configs response")
    return payload


def local_http_proxy_url(base_url: str) -> str:
    config = controller_config(base_url)
    port = config.get("mixed-port") or config.get("port")
    if isinstance(port, int):
        return f"http://127.0.0.1:{port}"
    return "http://127.0.0.1:7897"


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def hidden_creationflags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]


def find_mihomo_executable(explicit_path: str = "") -> Path:
    candidates = [
        Path(explicit_path) if explicit_path else None,
        Path(r"C:\Program Files\Clash Verge\verge-mihomo.exe"),
        Path(r"C:\Program Files\Clash Verge Rev\verge-mihomo.exe"),
        shutil.which("mihomo"),
        shutil.which("verge-mihomo"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    raise ClashControlError("mihomo executable not found")


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ClashControlError(f"config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ClashControlError(f"invalid yaml config: {path}")
    return data


def copy_geo_assets(source_dir: Path, lab_dir: Path) -> None:
    for name in ("Country.mmdb", "geoip.dat", "geosite.dat", "GeoIP.dat", "GeoSite.dat"):
        source = source_dir / name
        if source.exists() and source.is_file():
            shutil.copy2(source, lab_dir / name)


def build_isolated_config(source_config: Path, lab_dir: Path, *, mixed_port: int, controller_port: int, secret: str) -> Path:
    config = load_yaml_file(source_config)
    config["mixed-port"] = mixed_port
    config["allow-lan"] = False
    config["bind-address"] = "127.0.0.1"
    config["external-controller"] = f"127.0.0.1:{controller_port}"
    config["secret"] = secret
    config["log-level"] = "warning"
    config.pop("external-controller-unix", None)
    config.pop("external-controller-pipe", None)
    tun = config.get("tun")
    if isinstance(tun, dict):
        tun["enable"] = False
    profile = config.get("profile")
    if isinstance(profile, dict):
        profile["store-selected"] = False
    lab_config = lab_dir / "isolated-clash-verge.yaml"
    lab_config.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    copy_geo_assets(source_config.parent, lab_dir)
    return lab_config


def wait_for_controller(base_url: str, secret: str, timeout_seconds: int) -> None:
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            api_request(base_url, "GET", "/version", timeout=2, secret_value=secret)
            return
        except Exception as exc:
            last_error = str(exc)[:200]
            time.sleep(0.25)
    raise ClashControlError(f"isolated controller did not become ready: {last_error}")


def terminate_process(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def proxies(base_url: str) -> dict[str, Any]:
    payload = api_request(base_url, "GET", "/proxies")
    items = payload.get("proxies")
    if not isinstance(items, dict):
        raise ClashControlError("invalid /proxies response")
    return items


def selector_groups(items: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for name, item in sorted(items.items()):
        if not isinstance(item, dict):
            continue
        all_nodes = item.get("all")
        if isinstance(all_nodes, list) and all_nodes:
            clean_nodes = [node for node in all_nodes if is_real_node(str(node))]
            groups.append(
                {
                    "name": name,
                    "type": item.get("type"),
                    "now": item.get("now"),
                    "node_count": len(all_nodes),
                    "real_node_count": len(clean_nodes),
                    "sample": all_nodes[:8],
                    "real_node_sample": clean_nodes[:8],
                }
            )
    return groups


def is_real_node(name: str) -> bool:
    stripped = name.strip()
    if not stripped:
        return False
    if stripped in {"DIRECT", "REJECT", "PASS", "REJECT-DROP", *POLICY_NODE_NAMES}:
        return False
    return not any(stripped.startswith(prefix) for prefix in NOISE_NODE_PREFIXES)


def choose_gateway_group(groups: list[dict[str, Any]], preferred_group: str = "") -> dict[str, Any]:
    if preferred_group:
        for group in groups:
            if group.get("name") == preferred_group:
                return group
        raise ClashControlError(f"preferred group not found: {preferred_group}")
    for name in ("ClashGit.com", "Proxy", "节点选择", "GLOBAL"):
        for group in groups:
            if group.get("name") == name:
                return group
    if not groups:
        raise ClashControlError("no selector groups found")
    return groups[0]


def snapshot(base_url: str) -> dict[str, Any]:
    version = api_request(base_url, "GET", "/version")
    items = proxies(base_url)
    groups = selector_groups(items)
    return {
        "schema": f"{SCHEMA_PREFIX}.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "base_url": base_url,
        "version": version,
        "selector_group_count": len(groups),
        "selector_groups": groups,
        "secret_values_returned": False,
    }


def list_nodes(base_url: str, group: str) -> dict[str, Any]:
    items = proxies(base_url)
    item = items.get(group)
    if not isinstance(item, dict):
        raise ClashControlError(f"group not found: {group}")
    nodes = item.get("all")
    if not isinstance(nodes, list):
        raise ClashControlError(f"group is not switchable: {group}")
    return {
        "schema": f"{SCHEMA_PREFIX}.nodes.v1",
        "ok": True,
        "group": group,
        "now": item.get("now"),
        "node_count": len(nodes),
        "nodes": nodes,
        "real_nodes": [node for node in nodes if is_real_node(str(node))],
        "secret_values_returned": False,
    }


def write_state(payload: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_assessment(payload: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LAST_ASSESSMENT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        raise ClashControlError("no restore state found")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def current_group_state(base_url: str, group: str) -> dict[str, Any]:
    item = proxies(base_url).get(group)
    if not isinstance(item, dict):
        raise ClashControlError(f"group not found: {group}")
    allowed = item.get("all")
    if not isinstance(allowed, list):
        raise ClashControlError(f"group is not switchable: {group}")
    return item


def switch_plan(base_url: str, group: str, node: str) -> dict[str, Any]:
    item = current_group_state(base_url, group)
    allowed = item.get("all") or []
    return {
        "schema": f"{SCHEMA_PREFIX}.switch_plan.v1",
        "ok": node in allowed,
        "base_url": base_url,
        "group": group,
        "before": item.get("now"),
        "after": node,
        "node_available": node in allowed,
        "node_is_concrete_endpoint": is_real_node(node),
        "node_is_policy_choice": node in POLICY_NODE_NAMES,
        "writes_network_state": node in allowed,
        "requires_confirm_switch": True,
        "secret_values_returned": False,
    }


def apply_switch(target: SwitchTarget, *, save_restore: bool) -> dict[str, Any]:
    before_group = current_group_state(target.base_url, target.group)
    old_node = before_group.get("now")
    allowed = before_group.get("all") or []
    if target.node not in allowed:
        raise ClashControlError(f"node not available in group: {target.node}")
    if save_restore:
        write_state(
            {
                "schema": f"{SCHEMA_PREFIX}.restore_state.v1",
                "base_url": target.base_url,
                "group": target.group,
                "node": old_node,
                "saved_at": now_iso(),
            }
        )
    api_request(target.base_url, "PUT", f"/proxies/{urllib.parse.quote(target.group, safe='')}", {"name": target.node})
    after = proxies(target.base_url).get(target.group, {})
    return {
        "schema": f"{SCHEMA_PREFIX}.switch.v1",
        "ok": True,
        "group": target.group,
        "before": old_node,
        "after": after.get("now") if isinstance(after, dict) else None,
        "restore_state": str(STATE_PATH) if save_restore else "",
        "secret_values_returned": False,
    }


def switch_node(base_url: str, group: str, node: str, confirm_switch: bool) -> dict[str, Any]:
    if not confirm_switch:
        raise ClashControlError("switch requires --confirm-switch")
    return apply_switch(SwitchTarget(base_url, group, node), save_restore=True)


def restore(base_url: str, confirm_switch: bool) -> dict[str, Any]:
    if not confirm_switch:
        raise ClashControlError("restore requires --confirm-switch")
    state = read_state()
    target = SwitchTarget(base_url or state.get("base_url") or DEFAULT_BASE_URL, str(state["group"]), str(state["node"]))
    result = apply_switch(target, save_restore=False)
    result["schema"] = f"{SCHEMA_PREFIX}.restore.v1"
    result["restored_from"] = str(STATE_PATH)
    return result


def gateway_status(base_url: str, preferred_group: str = "") -> dict[str, Any]:
    snap = snapshot(base_url)
    chosen = choose_gateway_group(snap["selector_groups"], preferred_group)
    return {
        "schema": f"{SCHEMA_PREFIX}.gateway_status.v1",
        "ok": True,
        "generated_at": now_iso(),
        "base_url": base_url,
        "controller": {
            "reachable": True,
            "version": snap["version"],
            "secret_alias": SECRET_ALIAS,
            "secret_values_returned": False,
        },
        "recommended_group": chosen,
        "capabilities": {
            "snapshot": True,
            "list_groups": True,
            "list_nodes": True,
            "plan_switch": True,
            "switch_node": True,
            "restore": STATE_PATH.exists(),
            "delay_test": True,
            "assess_nodes": True,
            "recommend_node": True,
            "site_speedtest": True,
            "probe_node_access": True,
            "isolated_probe": True,
        },
        "safety": {
            "localhost_only_default": base_url.startswith("http://127.0.0.1"),
            "switch_requires_confirm": True,
            "does_not_modify_system_proxy": True,
            "does_not_edit_subscription": True,
        },
        "secret_values_returned": False,
    }


def delay_test(base_url: str, node: str, test_url: str, timeout_ms: int) -> dict[str, Any]:
    path = f"/proxies/{urllib.parse.quote(node, safe='')}/delay"
    query = urllib.parse.urlencode({"url": test_url, "timeout": timeout_ms})
    payload = api_request(base_url, "GET", f"{path}?{query}", timeout=max(3, int(timeout_ms / 1000) + 2))
    return {
        "schema": f"{SCHEMA_PREFIX}.delay.v1",
        "ok": "delay" in payload,
        "node": node,
        "test_url": test_url,
        "timeout_ms": timeout_ms,
        "result": payload,
        "secret_values_returned": False,
    }


def target_urls(target: str, urls: list[str]) -> list[str]:
    if urls:
        return urls
    return list(TARGET_TEST_URLS.get(str(target or "generic").strip().lower(), TARGET_TEST_URLS["generic"]))


def candidate_nodes(base_url: str, group: str, include: str, limit: int) -> list[str]:
    node_info = list_nodes(base_url, group)
    nodes = [str(node) for node in node_info["real_nodes"]]
    include_text = str(include or "").strip().lower()
    if include_text:
        nodes = [node for node in nodes if include_text in node.lower()]
    return nodes[: max(1, limit)]


def assess_single_node(base_url: str, node: str, urls: list[str], timeout_ms: int) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    ok_delays: list[int] = []
    for url in urls:
        try:
            result = delay_test(base_url, node, url, timeout_ms)
            delay_value = result.get("result", {}).get("delay")
            is_ok = isinstance(delay_value, int)
            if is_ok:
                ok_delays.append(delay_value)
            checks.append({"url": url, "ok": is_ok, "delay_ms": delay_value, "error": ""})
        except Exception as exc:
            checks.append({"url": url, "ok": False, "delay_ms": None, "error": str(exc)[:240]})
    success_count = len(ok_delays)
    average_delay = round(sum(ok_delays) / success_count, 2) if success_count else None
    timeout_count = len(checks) - success_count
    score = (100000 - int(average_delay or timeout_ms) - timeout_count * timeout_ms) if success_count else 0
    return {
        "node": node,
        "ok": success_count > 0,
        "success_count": success_count,
        "timeout_count": timeout_count,
        "average_delay_ms": average_delay,
        "score": score,
        "checks": checks,
    }


def assess_nodes(
    base_url: str,
    group: str,
    *,
    target: str,
    urls: list[str],
    include: str,
    limit: int,
    timeout_ms: int,
    save_report: bool,
) -> dict[str, Any]:
    test_urls = target_urls(target, urls)
    nodes = candidate_nodes(base_url, group, include, limit)
    assessments = [assess_single_node(base_url, node, test_urls, timeout_ms) for node in nodes]
    ranked = sorted(assessments, key=lambda row: (row["score"], -row["timeout_count"]), reverse=True)
    payload = {
        "schema": f"{SCHEMA_PREFIX}.assessment.v1",
        "ok": True,
        "generated_at": now_iso(),
        "base_url": base_url,
        "group": group,
        "target": target,
        "test_urls": test_urls,
        "candidate_count": len(nodes),
        "best": ranked[0] if ranked else None,
        "ranked": ranked,
        "writes_network_state": False,
        "saved_report": "",
        "secret_values_returned": False,
    }
    if save_report:
        write_assessment(payload)
        payload["saved_report"] = str(LAST_ASSESSMENT_PATH)
    return payload


def site_speedtest_for_node(base_url: str, node: str, sites: list[str], timeout_ms: int) -> dict[str, Any]:
    selected_sites = sites or list(SITE_PROFILES)
    site_results: list[dict[str, Any]] = []
    ok_delays: list[int] = []
    for site in selected_sites:
        urls = SITE_PROFILES.get(site)
        if not urls:
            site_results.append({"site": site, "ok": False, "error": "unknown_site", "checks": []})
            continue
        checks: list[dict[str, Any]] = []
        site_ok_delays: list[int] = []
        for url in urls:
            try:
                result = delay_test(base_url, node, url, timeout_ms)
                delay_value = result.get("result", {}).get("delay")
                is_ok = isinstance(delay_value, int)
                if is_ok:
                    site_ok_delays.append(delay_value)
                    ok_delays.append(delay_value)
                checks.append({"url": url, "ok": is_ok, "delay_ms": delay_value, "error": ""})
            except Exception as exc:
                checks.append({"url": url, "ok": False, "delay_ms": None, "error": str(exc)[:240]})
        average_delay = round(sum(site_ok_delays) / len(site_ok_delays), 2) if site_ok_delays else None
        site_results.append(
            {
                "site": site,
                "ok": bool(site_ok_delays),
                "average_delay_ms": average_delay,
                "success_count": len(site_ok_delays),
                "timeout_count": len(checks) - len(site_ok_delays),
                "checks": checks,
            }
        )
    overall_average = round(sum(ok_delays) / len(ok_delays), 2) if ok_delays else None
    return {
        "node": node,
        "ok": bool(ok_delays),
        "average_delay_ms": overall_average,
        "site_results": site_results,
        "score": (100000 - int(overall_average or timeout_ms)) if ok_delays else 0,
    }


def site_speedtest(
    base_url: str,
    group: str,
    *,
    nodes: list[str],
    sites: list[str],
    include: str,
    limit: int,
    timeout_ms: int,
    save_report: bool,
) -> dict[str, Any]:
    selected_nodes = nodes or candidate_nodes(base_url, group, include, limit)
    results = [site_speedtest_for_node(base_url, node, sites, timeout_ms) for node in selected_nodes]
    ranked = sorted(results, key=lambda row: row["score"], reverse=True)
    payload = {
        "schema": f"{SCHEMA_PREFIX}.site_speedtest.v1",
        "ok": True,
        "generated_at": now_iso(),
        "base_url": base_url,
        "group": group,
        "sites": sites or list(SITE_PROFILES),
        "candidate_count": len(selected_nodes),
        "best": ranked[0] if ranked else None,
        "ranked": ranked,
        "writes_network_state": False,
        "saved_report": "",
        "secret_values_returned": False,
    }
    if save_report:
        write_assessment(payload)
        payload["saved_report"] = str(LAST_ASSESSMENT_PATH)
    return payload


def recommend_node(
    base_url: str,
    group: str,
    *,
    target: str,
    urls: list[str],
    include: str,
    limit: int,
    timeout_ms: int,
) -> dict[str, Any]:
    assessment = assess_nodes(
        base_url,
        group,
        target=target,
        urls=urls,
        include=include,
        limit=limit,
        timeout_ms=timeout_ms,
        save_report=False,
    )
    best = assessment.get("best") or {}
    node = str(best.get("node") or "")
    command = ""
    if node:
        command = (
            f"python _bridge\\clash_mihomo_control.py switch-plan "
            f"--group {json.dumps(group, ensure_ascii=False)} --node {json.dumps(node, ensure_ascii=False)}"
        )
    return {
        "schema": f"{SCHEMA_PREFIX}.recommend_node.v1",
        "ok": bool(node),
        "generated_at": now_iso(),
        "base_url": base_url,
        "group": group,
        "target": target,
        "recommended_node": node,
        "recommended_score": best.get("score"),
        "recommended_average_delay_ms": best.get("average_delay_ms"),
        "candidate_count": assessment["candidate_count"],
        "switch_plan_command": command,
        "requires_separate_confirmed_switch": True,
        "assessment": assessment,
        "secret_values_returned": False,
    }


def fetch_url_via_proxy(proxy_url: str, url: str, method: str, timeout_seconds: int, max_bytes: int) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    request = urllib.request.Request(url, method=method.upper(), headers={"User-Agent": "codex-clash-access-probe/0.1"})
    started = time.perf_counter()
    status = 0
    headers: dict[str, str] = {}
    body = b""
    error = ""
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            headers = dict(response.headers.items())
            if method.upper() != "HEAD":
                body = response.read(max(0, max_bytes))
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        headers = dict(exc.headers.items())
        if method.upper() != "HEAD":
            body = exc.read(max(0, max_bytes))
    except Exception as exc:
        error = str(exc)[:240]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    body_hash = hashlib.sha256(body).hexdigest()[:16] if body else ""
    return {
        "url": url,
        "method": method.upper(),
        "reachable": bool(status),
        "status": status,
        "elapsed_ms": elapsed_ms,
        "content_type": headers.get("Content-Type", ""),
        "server": headers.get("Server", ""),
        "body_bytes_sampled": len(body),
        "body_sha256_16": body_hash,
        "error": error,
    }


def access_score(checks: list[dict[str, Any]], timeout_seconds: int) -> int:
    reachable = [check for check in checks if check.get("reachable")]
    if not reachable:
        return 0
    average_ms = sum(float(check.get("elapsed_ms") or timeout_seconds * 1000) for check in reachable) / len(reachable)
    failures = len(checks) - len(reachable)
    return max(1, int(100000 - average_ms - failures * timeout_seconds * 1000))


def probe_node_access(
    base_url: str,
    group: str,
    node: str,
    *,
    target: str,
    urls: list[str],
    method: str,
    timeout_seconds: int,
    max_bytes: int,
    confirm_temp_switch: bool,
    save_report: bool,
) -> dict[str, Any]:
    test_urls = target_urls(target, urls)
    group_state = current_group_state(base_url, group)
    before_node = str(group_state.get("now") or "")
    allowed = group_state.get("all") or []
    if node not in allowed:
        raise ClashControlError(f"node not available in group: {node}")
    needs_switch = node != before_node
    if needs_switch and not confirm_temp_switch:
        raise ClashControlError("access probe for a non-current node requires --confirm-temp-switch")
    proxy_url = local_http_proxy_url(base_url)
    restore_error = ""
    switched = False
    checks: list[dict[str, Any]] = []
    try:
        if needs_switch:
            api_request(base_url, "PUT", f"/proxies/{urllib.parse.quote(group, safe='')}", {"name": node})
            switched = True
            time.sleep(0.25)
        checks = [fetch_url_via_proxy(proxy_url, url, method, timeout_seconds, max_bytes) for url in test_urls]
    finally:
        if switched:
            try:
                api_request(base_url, "PUT", f"/proxies/{urllib.parse.quote(group, safe='')}", {"name": before_node})
            except Exception as exc:
                restore_error = str(exc)[:240]
    after_state = current_group_state(base_url, group)
    payload = {
        "schema": f"{SCHEMA_PREFIX}.access_probe.v1",
        "ok": bool(checks) and all(check.get("reachable") for check in checks) and not restore_error,
        "generated_at": now_iso(),
        "base_url": base_url,
        "group": group,
        "node": node,
        "target": target,
        "test_urls": test_urls,
        "proxy_url": proxy_url,
        "before": before_node,
        "after": after_state.get("now") if isinstance(after_state, dict) else None,
        "temporary_switch_performed": switched,
        "restored": (not switched) or (not restore_error and after_state.get("now") == before_node),
        "restore_error": restore_error,
        "score": access_score(checks, timeout_seconds),
        "checks": checks,
        "writes_network_state": switched,
        "requires_confirm_temp_switch": needs_switch,
        "saved_report": "",
        "secret_values_returned": False,
    }
    if save_report:
        write_assessment(payload)
        payload["saved_report"] = str(LAST_ASSESSMENT_PATH)
    return payload


def isolated_probe(
    *,
    config_path: str,
    mihomo_path: str,
    group: str,
    node: str,
    target: str,
    urls: list[str],
    method: str,
    timeout_seconds: int,
    max_bytes: int,
    keep_lab_dir: bool,
    save_report: bool,
) -> dict[str, Any]:
    source_config = Path(config_path) if config_path else DEFAULT_VERGE_CONFIG
    executable = find_mihomo_executable(mihomo_path)
    lab_root = RUNTIME_DIR / "isolated"
    lab_root.mkdir(parents=True, exist_ok=True)
    lab_dir = lab_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    lab_dir.mkdir(parents=True, exist_ok=True)
    mixed_port = free_local_port()
    controller_port = free_local_port()
    lab_secret = secrets.token_urlsafe(24)
    lab_config = build_isolated_config(
        source_config,
        lab_dir,
        mixed_port=mixed_port,
        controller_port=controller_port,
        secret=lab_secret,
    )
    base_url = f"http://127.0.0.1:{controller_port}"
    proc: subprocess.Popen[Any] | None = None
    startup_error = ""
    checks: list[dict[str, Any]] = []
    selected_after = ""
    try:
        proc = subprocess.Popen(
            [str(executable), "-d", str(lab_dir), "-f", str(lab_config)],
            cwd=str(lab_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=hidden_creationflags(),
        )
        wait_for_controller(base_url, lab_secret, timeout_seconds=12)
        item = api_request(base_url, "GET", f"/proxies/{urllib.parse.quote(group, safe='')}", timeout=5, secret_value=lab_secret)
        allowed = item.get("all") if isinstance(item, dict) else None
        if not isinstance(allowed, list) or node not in allowed:
            raise ClashControlError(f"node not available in isolated group: {node}")
        api_request(base_url, "PUT", f"/proxies/{urllib.parse.quote(group, safe='')}", {"name": node}, timeout=5, secret_value=lab_secret)
        selected = api_request(base_url, "GET", f"/proxies/{urllib.parse.quote(group, safe='')}", timeout=5, secret_value=lab_secret)
        selected_after = str(selected.get("now") if isinstance(selected, dict) else "")
        proxy_url = f"http://127.0.0.1:{mixed_port}"
        checks = [fetch_url_via_proxy(proxy_url, url, method, timeout_seconds, max_bytes) for url in target_urls(target, urls)]
    except Exception as exc:
        startup_error = str(exc)[:300]
    finally:
        if proc is not None:
            terminate_process(proc)
        if not keep_lab_dir:
            shutil.rmtree(lab_dir, ignore_errors=True)
    payload = {
        "schema": f"{SCHEMA_PREFIX}.isolated_probe.v1",
        "ok": bool(checks) and all(check.get("reachable") for check in checks) and not startup_error,
        "generated_at": now_iso(),
        "group": group,
        "node": node,
        "target": target,
        "test_urls": target_urls(target, urls),
        "selected_after": selected_after,
        "mixed_port": mixed_port,
        "controller_port": controller_port,
        "checks": checks,
        "score": access_score(checks, timeout_seconds),
        "startup_error": startup_error,
        "writes_global_network_state": False,
        "uses_isolated_mihomo_process": True,
        "lab_dir": str(lab_dir) if keep_lab_dir else "",
        "saved_report": "",
        "secret_values_returned": False,
    }
    if save_report:
        write_assessment(payload)
        payload["saved_report"] = str(LAST_ASSESSMENT_PATH)
    return payload


def validate(base_url: str) -> dict[str, Any]:
    try:
        snap = snapshot(base_url)
    except Exception as exc:
        return {
            "schema": f"{SCHEMA_PREFIX}.validate.v1",
            "ok": False,
            "reason": str(exc),
            "controller_diagnosis": controller_diagnosis(base_url),
            "secret_values_returned": False,
        }
    return {
        "schema": f"{SCHEMA_PREFIX}.validate.v1",
        "ok": True,
        "base_url": base_url,
        "selector_group_count": snap["selector_group_count"],
        "gateway_ready": True,
        "secret_values_returned": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled mihomo external-controller client")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    gateway = sub.add_parser("gateway-status")
    gateway.add_argument("--preferred-group", default="")
    sub.add_parser("groups")
    nodes = sub.add_parser("nodes")
    nodes.add_argument("--group", required=True)
    switch = sub.add_parser("switch-node")
    switch.add_argument("--group", required=True)
    switch.add_argument("--node", required=True)
    switch.add_argument("--confirm-switch", action="store_true")
    plan = sub.add_parser("switch-plan")
    plan.add_argument("--group", required=True)
    plan.add_argument("--node", required=True)
    delay = sub.add_parser("delay")
    delay.add_argument("--node", required=True)
    delay.add_argument("--url", default=DEFAULT_TEST_URL)
    delay.add_argument("--timeout-ms", type=int, default=5000)
    assess = sub.add_parser("assess-nodes")
    assess.add_argument("--group", required=True)
    assess.add_argument("--target", default="generic", choices=sorted(TARGET_TEST_URLS))
    assess.add_argument("--url", action="append", default=[])
    assess.add_argument("--include", default="")
    assess.add_argument("--limit", type=int, default=8)
    assess.add_argument("--timeout-ms", type=int, default=5000)
    assess.add_argument("--save-report", action="store_true")
    recommend = sub.add_parser("recommend-node")
    recommend.add_argument("--group", required=True)
    recommend.add_argument("--target", default="generic", choices=sorted(TARGET_TEST_URLS))
    recommend.add_argument("--url", action="append", default=[])
    recommend.add_argument("--include", default="")
    recommend.add_argument("--limit", type=int, default=8)
    recommend.add_argument("--timeout-ms", type=int, default=5000)
    speed = sub.add_parser("site-speedtest")
    speed.add_argument("--group", required=True)
    speed.add_argument("--node", action="append", default=[])
    speed.add_argument("--site", action="append", choices=sorted(SITE_PROFILES), default=[])
    speed.add_argument("--include", default="")
    speed.add_argument("--limit", type=int, default=8)
    speed.add_argument("--timeout-ms", type=int, default=5000)
    speed.add_argument("--save-report", action="store_true")
    access = sub.add_parser("probe-access")
    access.add_argument("--group", required=True)
    access.add_argument("--node", required=True)
    access.add_argument("--target", default="generic", choices=sorted(TARGET_TEST_URLS))
    access.add_argument("--url", action="append", default=[])
    access.add_argument("--method", default="GET", choices=("GET", "HEAD"))
    access.add_argument("--timeout-seconds", type=int, default=12)
    access.add_argument("--max-bytes", type=int, default=4096)
    access.add_argument("--confirm-temp-switch", action="store_true")
    access.add_argument("--save-report", action="store_true")
    isolated = sub.add_parser("isolated-probe")
    isolated.add_argument("--group", required=True)
    isolated.add_argument("--node", required=True)
    isolated.add_argument("--target", default="generic", choices=sorted(TARGET_TEST_URLS))
    isolated.add_argument("--url", action="append", default=[])
    isolated.add_argument("--method", default="GET", choices=("GET", "HEAD"))
    isolated.add_argument("--timeout-seconds", type=int, default=12)
    isolated.add_argument("--max-bytes", type=int, default=4096)
    isolated.add_argument("--config-path", default=str(DEFAULT_VERGE_CONFIG))
    isolated.add_argument("--mihomo-path", default="")
    isolated.add_argument("--keep-lab-dir", action="store_true")
    isolated.add_argument("--save-report", action="store_true")
    restore_cmd = sub.add_parser("restore")
    restore_cmd.add_argument("--confirm-switch", action="store_true")
    sub.add_parser("validate")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.cmd == "snapshot":
            emit(snapshot(args.base_url))
        elif args.cmd == "gateway-status":
            emit(gateway_status(args.base_url, args.preferred_group))
        elif args.cmd == "groups":
            emit({"schema": f"{SCHEMA_PREFIX}.groups.v1", "ok": True, "groups": selector_groups(proxies(args.base_url)), "secret_values_returned": False})
        elif args.cmd == "nodes":
            emit(list_nodes(args.base_url, args.group))
        elif args.cmd == "switch-plan":
            emit(switch_plan(args.base_url, args.group, args.node))
        elif args.cmd == "switch-node":
            emit(switch_node(args.base_url, args.group, args.node, args.confirm_switch))
        elif args.cmd == "delay":
            emit(delay_test(args.base_url, args.node, args.url, args.timeout_ms))
        elif args.cmd == "assess-nodes":
            emit(
                assess_nodes(
                    args.base_url,
                    args.group,
                    target=args.target,
                    urls=args.url,
                    include=args.include,
                    limit=args.limit,
                    timeout_ms=args.timeout_ms,
                    save_report=args.save_report,
                )
            )
        elif args.cmd == "recommend-node":
            emit(
                recommend_node(
                    args.base_url,
                    args.group,
                    target=args.target,
                    urls=args.url,
                    include=args.include,
                    limit=args.limit,
                    timeout_ms=args.timeout_ms,
                )
            )
        elif args.cmd == "site-speedtest":
            emit(
                site_speedtest(
                    args.base_url,
                    args.group,
                    nodes=args.node,
                    sites=args.site,
                    include=args.include,
                    limit=args.limit,
                    timeout_ms=args.timeout_ms,
                    save_report=args.save_report,
                )
            )
        elif args.cmd == "probe-access":
            emit(
                probe_node_access(
                    args.base_url,
                    args.group,
                    args.node,
                    target=args.target,
                    urls=args.url,
                    method=args.method,
                    timeout_seconds=args.timeout_seconds,
                    max_bytes=args.max_bytes,
                    confirm_temp_switch=args.confirm_temp_switch,
                    save_report=args.save_report,
                )
            )
        elif args.cmd == "isolated-probe":
            emit(
                isolated_probe(
                    config_path=args.config_path,
                    mihomo_path=args.mihomo_path,
                    group=args.group,
                    node=args.node,
                    target=args.target,
                    urls=args.url,
                    method=args.method,
                    timeout_seconds=args.timeout_seconds,
                    max_bytes=args.max_bytes,
                    keep_lab_dir=args.keep_lab_dir,
                    save_report=args.save_report,
                )
            )
        elif args.cmd == "restore":
            emit(restore(args.base_url, args.confirm_switch))
        elif args.cmd == "validate":
            emit(validate(args.base_url))
        return 0
    except Exception as exc:
        emit(
            {
                "schema": f"{SCHEMA_PREFIX}.error.v1",
                "ok": False,
                "reason": str(exc),
                "controller_diagnosis": controller_diagnosis(getattr(args, "base_url", DEFAULT_BASE_URL)),
                "secret_values_returned": False,
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
