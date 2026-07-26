#!/usr/bin/env python3
"""Read-only network diagnostics for Codex workspace operations.

Checks a small set of official endpoints and local language-runtime HTTP paths
so we can distinguish proxy, DNS, certificate, and runtime-specific failures.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from network_policy import (
    emit_json,
    env_for_runtime,
    recommendation_for_target,
    snapshot as policy_snapshot,
    work_plan_for_target,
)
from shared.windows_runtime_assets import openclaw_node_path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Probe:
    name: str
    ok: bool
    detail: str


URLS = [
    ("learn.microsoft.com", "https://learn.microsoft.com/"),
    ("nodejs.org", "https://nodejs.org/"),
    ("pypi.requests", "https://pypi.org/simple/requests/"),
    ("openai.codex.manual", "https://developers.openai.com/codex/codex-manual.md"),
]

DEFAULT_SUITE_TARGETS = [
    ("openai_api", "https://api.openai.com/v1/models", "codex_chat"),
    ("chatgpt", "https://chatgpt.com/", "codex_chat"),
    ("github", "https://github.com/", "github"),
    ("npm", "https://registry.npmjs.org/", "package"),
    ("pypi", "https://pypi.org/simple/requests/", "package"),
    ("microsoft_docs", "https://learn.microsoft.com/", "docs"),
]

PYTHON_MODULES = [
    "requests",
    "httpx",
    "aiohttp",
    "certifi",
    "truststore",
    "socks",
    "requests_toolbelt",
    "requests_cache",
    "dns",
    "tenacity",
    "h2",
]

NODE_MODULES = [
    "undici",
    "proxy-agent",
    "https-proxy-agent",
    "socks-proxy-agent",
    "pac-proxy-agent",
    "proxy-from-env",
    "hpagent",
    "axios",
    "got",
]


def probe_url(url: str, method: str = "HEAD") -> tuple[bool, str]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "codex-network-doctor/0.1"})
    errors: list[str] = []
    for attempt in range(1, 3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return True, f"status={r.status} attempts={attempt}"
        except Exception as exc:
            code = getattr(exc, "code", None)
            errors.append(f"attempt={attempt} status={code} error={repr(exc)[:160]}")
            time.sleep(0.5)
    return False, "; ".join(errors)


def powershell_probe(url: str) -> tuple[bool, str]:
    script = f"""
$ErrorActionPreference='Stop'
try {{
  $r = Invoke-WebRequest -Uri '{url}' -Method Head -UseBasicParsing -TimeoutSec 15
  Write-Output ("status=" + [int]$r.StatusCode)
}} catch {{
  Write-Output ("error=" + $_.Exception.Message)
  exit 1
}}
"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return proc.returncode == 0 or "status=" in out, out


def python_probe(url: str) -> tuple[bool, str]:
    errors: list[str] = []
    for attempt in range(1, 3):
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "codex-network-doctor/0.1"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return True, f"status={r.status} attempts={attempt}"
        except Exception as exc:
            code = getattr(exc, "code", None)
            errors.append(f"attempt={attempt} status={code} error={repr(exc)[:160]}")
            time.sleep(0.5)
    return False, "; ".join(errors)


def node_probe(url: str) -> tuple[bool, str]:
    node_candidates = [
        Path(r"C:\Program Files\nodejs\node.exe"),
        openclaw_node_path(),
    ]
    node = next((p for p in node_candidates if p.exists()), None)
    if node is None:
        return False, "node.exe not found"
    script = rf"""
const url = {json.dumps(url)};
let errors = [];
for (let attempt = 1; attempt <= 2; attempt++) {{
  try {{
    const r = await fetch(url, {{ method: 'HEAD' }});
    console.log('status=' + r.status + ' attempts=' + attempt);
    process.exit(0);
  }} catch (e) {{
    errors.push('attempt=' + attempt + ' error=' + e.message);
    await new Promise(resolve => setTimeout(resolve, 500));
  }}
}}
console.log(errors.join('; '));
process.exit(1);
"""
    env = os.environ.copy()
    env.setdefault("NODE_USE_ENV_PROXY", "1")
    proc = subprocess.run(
        [str(node), "-e", script],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        env=env,
    )
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return proc.returncode == 0 or "status=" in out, out


def python_module_probe(module: str) -> tuple[bool, str]:
    try:
        __import__(module)
        return True, "import=ok"
    except Exception as exc:
        return False, f"import_error={repr(exc)[:200]}"


def node_module_probe(module: str) -> tuple[bool, str]:
    node_candidates = [
        Path(r"C:\Program Files\nodejs\node.exe"),
        openclaw_node_path(),
    ]
    node = next((p for p in node_candidates if p.exists()), None)
    if node is None:
        return False, "node.exe not found"
    script = f"import({json.dumps(module)}).then(()=>console.log('import=ok')).catch(e=>{{console.log('import_error='+e.message); process.exit(1)}})"
    proc = subprocess.run(
        [str(node), "-e", script],
        cwd=str(ROOT / "_tools" / "network_toolkit"),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return proc.returncode == 0, out


def summarize(probes: list[Probe]) -> list[str]:
    advice: list[str] = []
    if any(not p.ok for p in probes if p.name.startswith("powershell.")):
        advice.append("PowerShell path failed: check WinHTTP proxy, TLS trust store, or Invoke-WebRequest restrictions.")
    if any(not p.ok for p in probes if p.name.startswith("python.")):
        advice.append("Python path failed: check certificate bundle, proxy environment, or Python runtime packaging.")
    if any(not p.ok for p in probes if p.name.startswith("node.")):
        advice.append("Node path failed: check NODE_USE_ENV_PROXY, proxy env vars, or Node runtime availability.")
    if any("status=4" in p.detail or "status=5" in p.detail for p in probes):
        advice.append("Some probes reached the server but received HTTP 4xx/5xx. Treat that as target/auth/request-method specific, not a local connectivity failure.")
    if not advice:
        advice.append("All tested paths succeeded. Network issues are likely target-specific, authentication-specific, or provider-side.")
    return advice


def curl_timing(url: str, *, proxy: str = "", timeout: int = 20) -> dict[str, Any]:
    command = [
        "curl.exe",
        "-L",
        "-o",
        "NUL",
        "-s",
        "-m",
        str(max(1, min(timeout, 120))),
        "-w",
        "dns=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} starttransfer=%{time_starttransfer} total=%{time_total} http=%{http_code}",
    ]
    if proxy:
        command.extend(["-x", proxy])
    command.append(url)
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout + 5,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "proxy": proxy}
    output = (proc.stdout or proc.stderr or "").strip()
    metrics: dict[str, Any] = {"ok": proc.returncode == 0, "proxy": proxy, "raw": output}
    for part in output.split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        try:
            metrics[key] = float(value) if key != "http" else int(value)
        except ValueError:
            metrics[key] = value
    if int(metrics.get("http") or 0) > 0:
        metrics["ok"] = True
    return metrics


def command_snapshot(_: argparse.Namespace) -> int:
    emit_json(policy_snapshot())
    return 0


def command_recommend(args: argparse.Namespace) -> int:
    emit_json(
        {
            "schema": "network_doctor.recommend.v1",
            "ok": True,
            "recommendation": recommendation_for_target(args.target, context=args.context).to_dict(),
        }
    )
    return 0


def command_env(args: argparse.Namespace) -> int:
    runtime_payload = env_for_runtime(args.target, runtime=args.runtime, context=args.context)
    recommendation = recommendation_for_target(args.target, context=args.context)
    payload = {
        "schema": "network_doctor.env.v1",
        "ok": True,
        "target": args.target,
        "runtime": args.runtime,
        "profile": recommendation.profile,
        "route": recommendation.route,
        "reason": recommendation.reason,
        "env": runtime_payload["env"],
        "notes": runtime_payload["notes"],
        "warning": "This is a per-process environment suggestion only; do not write it permanently without approval.",
    }
    if args.format == "powershell":
        for key, value in recommendation.env.items():
            print(f"$env:{key} = {json.dumps(value, ensure_ascii=False)}")
    else:
        emit_json(payload)
    return 0


def command_plan(args: argparse.Namespace) -> int:
    emit_json(work_plan_for_target(args.target, context=args.context))
    return 0


def command_probe(args: argparse.Namespace) -> int:
    recommendation = recommendation_for_target(args.target, context=args.context)
    url = args.target if args.target.startswith(("http://", "https://")) else f"https://{recommendation.host or args.target}/"
    direct = curl_timing(url, timeout=args.timeout)
    via_proxy = curl_timing(url, proxy=recommendation.proxy_url, timeout=args.timeout) if recommendation.proxy_url else {
        "ok": False,
        "reason": "no_proxy_candidate_detected",
    }
    payload = {
        "schema": "network_doctor.probe.v1",
        "ok": direct.get("ok") or via_proxy.get("ok"),
        "target": args.target,
        "url": url,
        "recommendation": recommendation.to_dict(),
        "direct": direct,
        "proxy": via_proxy,
        "classification": classify_probe(direct, via_proxy, recommendation.to_dict()),
    }
    emit_json(payload)
    return 0 if payload["ok"] else 1


def classify_probe(direct: dict[str, Any], proxy: dict[str, Any], recommendation: dict[str, Any]) -> dict[str, Any]:
    direct_total = float(direct.get("total") or 0)
    proxy_total = float(proxy.get("total") or 0)
    direct_start = float(direct.get("starttransfer") or direct_total or 0)
    proxy_start = float(proxy.get("starttransfer") or proxy_total or 0)
    proxy_tls = float(proxy.get("tls") or 0)
    category = str(recommendation.get("category") or "")
    issues: list[str] = []
    preferred_route = str(recommendation.get("route") or "system_or_auto")
    if not direct.get("ok") and proxy.get("ok"):
        issues.append("direct_failed_proxy_ok")
        preferred_route = "proxy"
    if direct.get("ok") and not proxy.get("ok"):
        issues.append("proxy_failed_direct_ok")
        preferred_route = "direct"
    if direct.get("ok") and proxy.get("ok") and direct_start and proxy_start:
        direct_effective = direct_total or direct_start
        proxy_effective = proxy_total or proxy_start
        if proxy_effective > direct_effective * 1.5:
            issues.append("proxy_slower_than_direct")
            preferred_route = "direct"
        elif direct_effective > proxy_effective * 1.5:
            issues.append("direct_slower_than_proxy")
            preferred_route = "proxy"
        else:
            preferred_route = "proxy" if proxy_start < direct_start else "direct"
    if category == "openai" and proxy_tls > 3:
        issues.append("openai_proxy_tls_slow")
        preferred_route = "proxy_but_target_strategy_slow"
    if not direct.get("ok") and not proxy.get("ok"):
        issues.append("all_routes_failed")
        preferred_route = "none"
    score = route_score(direct, proxy, category=category)
    return {
        "issues": issues,
        "score": score,
        "preferred_route": preferred_route,
        "recommended_next_action": "check_target_specific_proxy_rule_or_node" if "openai_proxy_tls_slow" in issues else "use_recommended_route",
    }


def route_score(direct: dict[str, Any], proxy: dict[str, Any], *, category: str) -> dict[str, Any]:
    def score_one(item: dict[str, Any]) -> int:
        if not item.get("ok"):
            return 0
        start = float(item.get("starttransfer") or item.get("total") or 0)
        total = float(item.get("total") or start or 0)
        tls = float(item.get("tls") or 0)
        value = 100
        if start > 1:
            value -= min(60, int(start * 8))
        if total > 3:
            value -= min(40, int((total - 3) * 5))
        if tls > 1:
            value -= min(30, int(tls * 5))
        if int(item.get("http") or 0) == 0:
            value -= 20
        return max(0, value)

    return {
        "direct": score_one(direct),
        "proxy": score_one(proxy),
        "category": category,
        "rule": "score uses reachability plus start-transfer/total/TLS timing; it is advisory, not a permission decision",
    }


def command_probe_suite(args: argparse.Namespace) -> int:
    results: list[dict[str, Any]] = []
    for name, target, context in DEFAULT_SUITE_TARGETS:
        recommendation = recommendation_for_target(target, context=context)
        direct = curl_timing(target, timeout=args.timeout)
        proxy = curl_timing(target, proxy=recommendation.proxy_url, timeout=args.timeout) if recommendation.proxy_url else {
            "ok": False,
            "reason": "no_proxy_candidate_detected",
        }
        classification = classify_probe(direct, proxy, recommendation.to_dict())
        results.append(
            {
                "name": name,
                "target": target,
                "context": context,
                "recommendation": recommendation.to_dict(),
                "direct": direct,
                "proxy": proxy,
                "classification": classification,
            }
        )
    issues = [
        {"name": item["name"], "issues": item["classification"].get("issues", [])}
        for item in results
        if item["classification"].get("issues")
    ]
    payload = {
        "schema": "network_doctor.probe_suite.v1",
        "ok": not any("all_routes_failed" in issue["issues"] for issue in issues),
        "timeout": args.timeout,
        "results": results,
        "issues": issues,
        "summary": summarize_suite(results),
    }
    emit_json(payload)
    return 0 if payload["ok"] else 1


def summarize_suite(results: list[dict[str, Any]]) -> dict[str, Any]:
    openai = [item for item in results if item.get("name") in {"openai_api", "chatgpt"}]
    slow_openai = any("openai_proxy_tls_slow" in item.get("classification", {}).get("issues", []) for item in openai)
    direct_failures = [
        item.get("name")
        for item in results
        if "direct_failed_proxy_ok" in item.get("classification", {}).get("issues", [])
    ]
    return {
        "direct_failed_proxy_ok": direct_failures,
        "openai_proxy_tls_slow": slow_openai,
        "next_action": "switch_or_retest_openai_strategy_group_in_clash_mihomo" if slow_openai else "use_per_target_recommendations",
    }


def validate_classification_semantics() -> list[str]:
    samples = [
        (
            "direct_failed_proxy_ok",
            {"ok": False, "total": 8.0, "http": 0},
            {"ok": True, "total": 0.8, "starttransfer": 0.3, "tls": 0.2, "http": 200},
            {"category": "github", "route": "auto_fastest"},
            "proxy",
        ),
        (
            "proxy_failed_direct_ok",
            {"ok": True, "total": 1.2, "starttransfer": 1.0, "tls": 0.5, "http": 200},
            {"ok": False, "total": 5.0, "http": 0},
            {"category": "external", "route": "system_or_auto"},
            "direct",
        ),
        (
            "proxy_slower_than_direct",
            {"ok": True, "total": 0.8, "starttransfer": 0.7, "tls": 0.4, "http": 200},
            {"ok": True, "total": 5.4, "starttransfer": 5.3, "tls": 5.2, "http": 200},
            {"category": "package", "route": "auto_fastest"},
            "direct",
        ),
        (
            "direct_slower_than_proxy",
            {"ok": True, "total": 9.8, "starttransfer": 1.3, "tls": 1.1, "http": 200},
            {"ok": True, "total": 5.5, "starttransfer": 5.3, "tls": 5.2, "http": 200},
            {"category": "package", "route": "auto_fastest"},
            "proxy",
        ),
        (
            "openai_proxy_tls_slow",
            {"ok": False, "total": 8.0, "http": 0},
            {"ok": True, "total": 5.6, "starttransfer": 5.5, "tls": 5.2, "http": 401},
            {"category": "openai", "route": "proxy_preferred"},
            "proxy_but_target_strategy_slow",
        ),
    ]
    issues: list[str] = []
    for expected_issue, direct, proxy, recommendation, expected_route in samples:
        result = classify_probe(direct, proxy, recommendation)
        if expected_issue not in result.get("issues", []):
            issues.append(f"classification_missing_{expected_issue}")
        if result.get("preferred_route") != expected_route:
            issues.append(f"classification_route_{expected_issue}_expected_{expected_route}_got_{result.get('preferred_route')}")
    return issues


def validate_gateway_policy_semantics() -> list[str]:
    issues: list[str] = []
    samples = [
        ("http://127.0.0.1:1", "local", "direct_only_no_proxy"),
        ("https://api.openai.com/v1/models", "openai", "proxy_first_with_direct_fallback"),
        ("https://github.com/", "github", "probe_selected_with_proxy_candidate"),
        ("https://pypi.org/simple/requests/", "package", "probe_selected_with_proxy_candidate"),
        ("https://example.com/", "external", "system_or_direct_with_optional_proxy_fallback"),
    ]
    for target, category, failover_policy in samples:
        recommendation = recommendation_for_target(target, context="validate")
        if recommendation.category != category:
            issues.append(f"gateway_policy_category_{target}_expected_{category}_got_{recommendation.category}")
        if recommendation.failover_policy != failover_policy:
            issues.append(f"gateway_policy_failover_{category}_expected_{failover_policy}_got_{recommendation.failover_policy}")
        if recommendation.health_score <= 0:
            issues.append(f"gateway_policy_health_score_missing_{category}")
        if recommendation.retry_budget < 1:
            issues.append(f"gateway_policy_retry_budget_invalid_{category}")
        if not recommendation.observability_tags:
            issues.append(f"gateway_policy_observability_tags_missing_{category}")
    return issues


def command_validate(_: argparse.Namespace) -> int:
    snap = policy_snapshot()
    issues: list[str] = []
    if not snap.get("env_proxy") and not snap.get("windows_user_proxy") and not snap.get("local_proxy_listener"):
        issues.append("no_proxy_candidate_detected")
    issues.extend(validate_classification_semantics())
    issues.extend(validate_gateway_policy_semantics())
    payload = {
        "schema": "network_doctor.validate.v1",
        "ok": not issues,
        "issues": issues,
        "snapshot": snap,
        "rule": "network layer validates route discovery and lightweight gateway policy only; target latency requires probe; no permission model changes are introduced",
    }
    emit_json(payload)
    return 0 if payload["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only network diagnostics.")
    subparsers = parser.add_subparsers(dest="command")
    snapshot_parser = subparsers.add_parser("snapshot", help="Read current proxy and route candidates.")
    snapshot_parser.set_defaults(func=command_snapshot)
    recommend_parser = subparsers.add_parser("recommend", help="Recommend a route for one target.")
    recommend_parser.add_argument("target")
    recommend_parser.add_argument("--context", default="")
    recommend_parser.set_defaults(func=command_recommend)
    env_parser = subparsers.add_parser("env", help="Emit per-process network environment for one target.")
    env_parser.add_argument("target")
    env_parser.add_argument("--context", default="")
    env_parser.add_argument("--runtime", default="generic", choices=("generic", "node", "npm", "python", "pip", "uv", "curl", "winhttp", "browser"))
    env_parser.add_argument("--format", choices=("json", "powershell"), default="json")
    env_parser.set_defaults(func=command_env)
    plan_parser = subparsers.add_parser("plan", help="Emit a practical per-target network work plan.")
    plan_parser.add_argument("target")
    plan_parser.add_argument("--context", default="")
    plan_parser.set_defaults(func=command_plan)
    probe_parser = subparsers.add_parser("probe", help="Compare direct and proxy timing for one target.")
    probe_parser.add_argument("target")
    probe_parser.add_argument("--context", default="")
    probe_parser.add_argument("--timeout", type=int, default=20)
    probe_parser.set_defaults(func=command_probe)
    suite_parser = subparsers.add_parser("probe-suite", help="Probe Codex-critical network targets.")
    suite_parser.add_argument("--timeout", type=int, default=10)
    suite_parser.set_defaults(func=command_probe_suite)
    validate_parser = subparsers.add_parser("validate", help="Validate route discovery.")
    validate_parser.set_defaults(func=command_validate)
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args()
    if hasattr(args, "func"):
        return args.func(args)

    probes: list[Probe] = []
    for name, url in URLS:
        ok, detail = probe_url(url)
        probes.append(Probe(f"urllib.{name}", ok, detail))

    for name, url in URLS:
        ok, detail = powershell_probe(url)
        probes.append(Probe(f"powershell.{name}", ok, detail))

    for name, url in URLS:
        ok, detail = python_probe(url)
        probes.append(Probe(f"python.{name}", ok, detail))

    for name, url in URLS:
        ok, detail = node_probe(url)
        probes.append(Probe(f"node.{name}", ok, detail))

    for module in PYTHON_MODULES:
        ok, detail = python_module_probe(module)
        probes.append(Probe(f"python_module.{module}", ok, detail))

    for module in NODE_MODULES:
        ok, detail = node_module_probe(module)
        probes.append(Probe(f"node_module.{module}", ok, detail))

    payload = {
        "ok": all(p.ok for p in probes),
        "probes": [asdict(p) for p in probes],
        "advice": summarize(probes),
        "env": {
            "HTTP_PROXY": os.environ.get("HTTP_PROXY"),
            "HTTPS_PROXY": os.environ.get("HTTPS_PROXY"),
            "NO_PROXY": os.environ.get("NO_PROXY"),
            "NODE_USE_ENV_PROXY": os.environ.get("NODE_USE_ENV_PROXY"),
        },
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
