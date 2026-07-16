#!/usr/bin/env python3
"""Clash node metrics cache and request-aware recommendation.

Ownership: maintain a lightweight local cache that combines high-frequency
Mihomo delay tests with lower-frequency real access probes, then recommend a
node and access mode for Codex network gateway requests.
Non-goals: editing subscriptions, changing system proxy/DNS, switching the main
Clash node, owning resource fetching, or running a scheduler daemon.
State behavior: writes JSON cache files under `_bridge/runtime/clash_mihomo`;
all refresh commands are bounded and do not mutate production network state.
Caller context: `codex_network_gateway.py`, resource-layer routing, and manual
network diagnostics that need cached node/site evidence before choosing a route.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import clash_mihomo_control as clash
from network_route_cache import record_observation as record_route_observation


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
RUNTIME_DIR = BRIDGE / "runtime" / "clash_mihomo"
METRICS_PATH = RUNTIME_DIR / "node_metrics.json"
SCHEMA_PREFIX = "clash_node_metrics"
DEFAULT_GROUP = "ClashGit.com"
DELAY_FRESH_SECONDS = 1800
ACCESS_FRESH_SECONDS = 21600
DEFAULT_DELAY_LIMIT = 6
DEFAULT_ACCESS_LIMIT = 3
TARGET_TO_SITES = {
    "github": ["github"],
    "openai": ["openai"],
    "package": ["npm", "pypi"],
    "docs": ["microsoft_docs"],
    "browser": ["github", "microsoft_docs"],
    "paper": ["openalex", "arxiv"],
    "image": ["wikimedia_commons", "openverse"],
    "dataset": ["huggingface_datasets", "zenodo"],
    "web": ["generic_web"],
    "external": ["github", "npm", "microsoft_docs"],
    "generic": ["github", "npm", "microsoft_docs"],
}


def observation_target_url(target: str) -> str:
    urls = getattr(clash, "TARGET_TEST_URLS", {}).get(str(target or "generic").strip().lower())
    if isinstance(urls, (list, tuple)) and urls:
        return str(urls[0])
    return str(target or "")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def hidden_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_json(argv: list[str], *, timeout: int) -> dict[str, Any]:
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
        return {"ok": False, "reason": f"json_decode_failed: {exc}", "stdout_tail": (proc.stdout or "")[-1000:]}


def read_cache() -> dict[str, Any]:
    if not METRICS_PATH.exists():
        return {
            "schema": f"{SCHEMA_PREFIX}.cache.v1",
            "created_at": now_iso(),
            "updated_at": "",
            "groups": {},
            "policy": {
                "delay_fresh_seconds": DELAY_FRESH_SECONDS,
                "access_fresh_seconds": ACCESS_FRESH_SECONDS,
                "delay_can_refresh_more_frequently": True,
                "access_probe_is_lower_frequency": True,
            },
        }
    try:
        data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("groups", {})
            return data
    except Exception:
        pass
    return {"schema": f"{SCHEMA_PREFIX}.cache.v1", "created_at": now_iso(), "updated_at": "", "groups": {}}


def write_cache(cache: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    cache["updated_at"] = now_iso()
    cache.setdefault("schema", f"{SCHEMA_PREFIX}.cache.v1")
    METRICS_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def age_seconds(value: str) -> float | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (now_utc() - parsed).total_seconds())


def target_sites(target: str, sites: list[str] | None = None) -> list[str]:
    if sites:
        return sites
    return TARGET_TO_SITES.get(str(target or "generic").strip().lower(), TARGET_TO_SITES["generic"])


def group_bucket(cache: dict[str, Any], group: str) -> dict[str, Any]:
    groups = cache.setdefault("groups", {})
    bucket = groups.setdefault(group, {"nodes": {}, "updated_at": ""})
    bucket.setdefault("nodes", {})
    return bucket


def node_bucket(cache: dict[str, Any], group: str, node: str) -> dict[str, Any]:
    bucket = group_bucket(cache, group)
    nodes = bucket.setdefault("nodes", {})
    return nodes.setdefault(node, {"delay_by_site": {}, "access_by_target": {}, "last_seen": ""})


def update_delay_cache(cache: dict[str, Any], group: str, speedtest: dict[str, Any]) -> None:
    for row in speedtest.get("ranked") or []:
        node = str(row.get("node") or "")
        if not node:
            continue
        node_data = node_bucket(cache, group, node)
        node_data["last_seen"] = now_iso()
        delay_by_site = node_data.setdefault("delay_by_site", {})
        for site_result in row.get("site_results") or []:
            site = str(site_result.get("site") or "")
            if not site:
                continue
            delay_by_site[site] = {
                "ok": bool(site_result.get("ok")),
                "average_delay_ms": site_result.get("average_delay_ms"),
                "success_count": site_result.get("success_count", 0),
                "timeout_count": site_result.get("timeout_count", 0),
                "checked_at": speedtest.get("generated_at") or now_iso(),
            }


def update_access_cache(cache: dict[str, Any], group: str, target: str, access: dict[str, Any]) -> None:
    node = str(access.get("node") or "")
    if not node:
        return
    node_data = node_bucket(cache, group, node)
    node_data["last_seen"] = now_iso()
    node_data.setdefault("access_by_target", {})[target] = {
        "ok": bool(access.get("ok")),
        "score": access.get("score", 0),
        "checked_at": access.get("generated_at") or now_iso(),
        "checks": access.get("checks", []),
        "isolated": bool(access.get("uses_isolated_mihomo_process")),
    }
    latency_values: list[float] = []
    error_class = ""
    for check in access.get("checks") or []:
        if not isinstance(check, dict):
            continue
        elapsed = check.get("elapsed_ms") or check.get("latency_ms")
        if isinstance(elapsed, (int, float)):
            latency_values.append(float(elapsed))
        if not check.get("ok") and not error_class:
            error_class = str(check.get("error") or check.get("reason") or "access_probe_failed")[:160]
    average_latency = round(sum(latency_values) / len(latency_values), 1) if latency_values else 0
    record_route_observation(
        target_kind=target,
        target=observation_target_url(target),
        runtime="clash_isolated_probe",
        route_mode="isolated_mihomo",
        node=node,
        ok=bool(access.get("ok")),
        latency_ms=average_latency,
        error_class="" if access.get("ok") else error_class,
    )


def refresh_delay(
    *,
    group: str,
    target: str,
    sites: list[str],
    include: str,
    limit: int,
    timeout_ms: int,
) -> dict[str, Any]:
    selected_sites = target_sites(target, sites)
    command = [
        sys.executable,
        str(BRIDGE / "clash_mihomo_control.py"),
        "site-speedtest",
        "--group",
        group,
        "--limit",
        str(limit),
        "--timeout-ms",
        str(timeout_ms),
    ]
    for site in selected_sites:
        command.extend(["--site", site])
    if include:
        command.extend(["--include", include])
    speedtest = run_json(command, timeout=max(45, int((timeout_ms / 1000) * max(1, limit) * max(1, len(selected_sites))) + 30))
    cache = read_cache()
    if speedtest.get("ok"):
        update_delay_cache(cache, group, speedtest)
        write_cache(cache)
    return {
        "schema": f"{SCHEMA_PREFIX}.refresh_delay.v1",
        "ok": bool(speedtest.get("ok")),
        "generated_at": now_iso(),
        "group": group,
        "target": target,
        "sites": selected_sites,
        "result": speedtest,
        "cache_path": str(METRICS_PATH),
        "writes_network_state": False,
    }


def ranked_from_cache(cache: dict[str, Any], group: str, target: str, sites: list[str]) -> list[dict[str, Any]]:
    nodes = ((cache.get("groups") or {}).get(group) or {}).get("nodes") or {}
    ranked: list[dict[str, Any]] = []
    for node, node_data in nodes.items():
        delay_scores: list[float] = []
        stale_delay_count = 0
        for site in sites:
            row = (node_data.get("delay_by_site") or {}).get(site) or {}
            if not row.get("ok"):
                continue
            checked_age = age_seconds(str(row.get("checked_at") or ""))
            if checked_age is None or checked_age > DELAY_FRESH_SECONDS:
                stale_delay_count += 1
            value = row.get("average_delay_ms")
            if isinstance(value, (int, float)):
                delay_scores.append(float(value))
        access_row = (node_data.get("access_by_target") or {}).get(target) or {}
        access_age = age_seconds(str(access_row.get("checked_at") or ""))
        has_fresh_access = bool(access_row.get("ok")) and access_age is not None and access_age <= ACCESS_FRESH_SECONDS
        access_penalty = 0 if has_fresh_access else 5000
        if not delay_scores and not has_fresh_access:
            continue
        average_delay = round(sum(delay_scores) / len(delay_scores), 2) if delay_scores else None
        access_score = int(access_row.get("score", 0) or 0)
        if has_fresh_access:
            score = int(200000 + access_score - (average_delay or 2000) - stale_delay_count * 1000)
        else:
            score = int(100000 - (average_delay or 2000) - stale_delay_count * 1000 - access_penalty)
        ranked.append(
            {
                "node": node,
                "score": score,
                "average_delay_ms": average_delay,
                "delay_site_count": len(delay_scores),
                "stale_delay_count": stale_delay_count,
                "access_ok": bool(access_row.get("ok")),
                "access_age_seconds": access_age,
                "has_fresh_access": has_fresh_access,
                "access_score": access_score,
            }
        )
    return sorted(ranked, key=lambda row: row["score"], reverse=True)


def delay_qualified_missing_access(ranked: list[dict[str, Any]], limit: int) -> list[str]:
    candidates: list[str] = []
    for row in ranked:
        if len(candidates) >= max(1, limit):
            break
        if row.get("has_fresh_access"):
            continue
        if not row.get("delay_site_count"):
            continue
        if row.get("stale_delay_count"):
            continue
        node = str(row.get("node") or "")
        if node:
            candidates.append(node)
    return candidates


def has_fresh_access_candidate(ranked: list[dict[str, Any]], limit: int) -> bool:
    return any(row.get("has_fresh_access") for row in ranked[: max(1, limit)])


def refresh_access_for_nodes(
    *,
    group: str,
    target: str,
    nodes: list[str],
    timeout_seconds: int,
    max_bytes: int,
) -> list[dict[str, Any]]:
    cache = read_cache()
    results: list[dict[str, Any]] = []
    for node in nodes:
        command = [
            sys.executable,
            str(BRIDGE / "clash_mihomo_control.py"),
            "isolated-probe",
            "--group",
            group,
            "--node",
            node,
            "--target",
            target,
            "--method",
            "HEAD",
            "--timeout-seconds",
            str(timeout_seconds),
            "--max-bytes",
            str(max_bytes),
        ]
        access = run_json(command, timeout=max(timeout_seconds + 45, 70))
        access["node"] = node
        results.append(access)
        update_access_cache(cache, group, target, access)
    write_cache(cache)
    return results


def recommend(
    *,
    group: str,
    target: str,
    sites: list[str],
    include: str,
    delay_limit: int,
    access_limit: int,
    timeout_ms: int,
    refresh_if_stale: bool,
) -> dict[str, Any]:
    selected_sites = target_sites(target, sites)
    cache = read_cache()
    ranked = ranked_from_cache(cache, group, target, selected_sites)
    stale_or_empty = not ranked or any(row.get("stale_delay_count") for row in ranked[: max(1, access_limit)])
    delay_refresh: dict[str, Any] = {}
    access_refresh: list[dict[str, Any]] = []
    if refresh_if_stale and stale_or_empty:
        delay_refresh = refresh_delay(
            group=group,
            target=target,
            sites=selected_sites,
            include=include,
            limit=delay_limit,
            timeout_ms=timeout_ms,
        )
        cache = read_cache()
        ranked = ranked_from_cache(cache, group, target, selected_sites)
    had_fresh_access_before_refresh = has_fresh_access_candidate(ranked, access_limit)
    needs_access = []
    if not had_fresh_access_before_refresh:
        needs_access = delay_qualified_missing_access(ranked, access_limit)
    if refresh_if_stale and needs_access:
        access_refresh = refresh_access_for_nodes(
            group=group,
            target=target,
            nodes=needs_access,
            timeout_seconds=max(5, int(timeout_ms / 1000) + 4),
            max_bytes=1024,
        )
        cache = read_cache()
        ranked = ranked_from_cache(cache, group, target, selected_sites)
    best = ranked[0] if ranked else {}
    node = str(best.get("node") or "")
    access_mode = "isolated_lease" if node else "current_proxy_or_direct"
    reason = "cache_delay_plus_access" if best.get("has_fresh_access") else "cache_delay_only_or_refresh_failed"
    return {
        "schema": f"{SCHEMA_PREFIX}.recommend.v1",
        "ok": bool(node),
        "generated_at": now_iso(),
        "group": group,
        "target": target,
        "sites": selected_sites,
        "recommended_node": node,
        "recommended_access_mode": access_mode,
        "reason": reason,
        "best": best,
        "ranked": ranked[:10],
        "access_candidate_policy": {
            "delay_qualified_only": True,
            "fresh_access_cache_reused": had_fresh_access_before_refresh,
            "fresh_access_available_after_refresh": has_fresh_access_candidate(ranked, access_limit),
            "default_access_limit": DEFAULT_ACCESS_LIMIT,
            "requested_access_limit": access_limit,
            "scoring_order": [
                "fresh_real_access_success",
                "real_access_score",
                "delay_latency",
                "freshness_penalty",
            ],
            "tested_nodes": [item.get("node") for item in access_refresh],
        },
        "delay_refresh": delay_refresh,
        "access_refresh": access_refresh,
        "cache_path": str(METRICS_PATH),
        "writes_network_state": False,
        "secret_values_returned": False,
    }


def snapshot() -> dict[str, Any]:
    cache = read_cache()
    groups = cache.get("groups") or {}
    node_count = sum(len((bucket or {}).get("nodes") or {}) for bucket in groups.values() if isinstance(bucket, dict))
    return {
        "schema": f"{SCHEMA_PREFIX}.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "cache_path": str(METRICS_PATH),
        "cache_exists": METRICS_PATH.exists(),
        "group_count": len(groups),
        "node_count": node_count,
        "policy": cache.get("policy", {}),
        "updated_at": cache.get("updated_at", ""),
    }


def validate() -> dict[str, Any]:
    snap = snapshot()
    target_url_keys = set(getattr(clash, "TARGET_TEST_URLS", {}) or {})
    site_profile_keys = set(getattr(clash, "SITE_PROFILES", {}) or {})
    missing_target_urls = sorted(key for key in TARGET_TO_SITES if key not in target_url_keys)
    missing_site_profiles = sorted(
        f"{target}:{site}"
        for target, sites in TARGET_TO_SITES.items()
        for site in sites
        if site not in site_profile_keys
    )
    mapping_ok = not missing_target_urls and not missing_site_profiles
    return {
        "schema": f"{SCHEMA_PREFIX}.validate.v1",
        "ok": bool(snap.get("ok")) and mapping_ok,
        "generated_at": now_iso(),
        "cache_path": str(METRICS_PATH),
        "cache_exists": METRICS_PATH.exists(),
        "node_count": snap.get("node_count", 0),
        "delay_fresh_seconds": DELAY_FRESH_SECONDS,
        "access_fresh_seconds": ACCESS_FRESH_SECONDS,
        "mapping_ok": mapping_ok,
        "missing_target_urls": missing_target_urls,
        "missing_site_profiles": missing_site_profiles,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clash node metrics cache and recommendation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    delay = sub.add_parser("refresh-delay")
    delay.add_argument("--group", default=DEFAULT_GROUP)
    delay.add_argument("--target", default="generic")
    delay.add_argument("--site", action="append", default=[])
    delay.add_argument("--include", default="")
    delay.add_argument("--limit", type=int, default=DEFAULT_DELAY_LIMIT)
    delay.add_argument("--timeout-ms", type=int, default=3000)
    rec = sub.add_parser("recommend")
    rec.add_argument("--group", default=DEFAULT_GROUP)
    rec.add_argument("--target", default="generic")
    rec.add_argument("--site", action="append", default=[])
    rec.add_argument("--include", default="")
    rec.add_argument("--delay-limit", type=int, default=DEFAULT_DELAY_LIMIT)
    rec.add_argument("--access-limit", type=int, default=DEFAULT_ACCESS_LIMIT)
    rec.add_argument("--timeout-ms", type=int, default=3000)
    rec.add_argument("--refresh-if-stale", action="store_true")
    access = sub.add_parser("refresh-access")
    access.add_argument("--group", default=DEFAULT_GROUP)
    access.add_argument("--target", default="generic")
    access.add_argument("--node", action="append", default=[])
    access.add_argument("--timeout-seconds", type=int, default=8)
    access.add_argument("--max-bytes", type=int, default=1024)
    sub.add_parser("validate")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cmd == "snapshot":
        emit(snapshot())
    elif args.cmd == "refresh-delay":
        emit(refresh_delay(group=args.group, target=args.target, sites=args.site, include=args.include, limit=args.limit, timeout_ms=args.timeout_ms))
    elif args.cmd == "recommend":
        emit(
            recommend(
                group=args.group,
                target=args.target,
                sites=args.site,
                include=args.include,
                delay_limit=args.delay_limit,
                access_limit=args.access_limit,
                timeout_ms=args.timeout_ms,
                refresh_if_stale=args.refresh_if_stale,
            )
        )
    elif args.cmd == "refresh-access":
        results = refresh_access_for_nodes(group=args.group, target=args.target, nodes=args.node, timeout_seconds=args.timeout_seconds, max_bytes=args.max_bytes)
        emit({"schema": f"{SCHEMA_PREFIX}.refresh_access.v1", "ok": all(item.get("ok") for item in results), "generated_at": now_iso(), "results": results, "cache_path": str(METRICS_PATH)})
    elif args.cmd == "validate":
        emit(validate())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
