#!/usr/bin/env python3
"""Network route decision cache for the Codex network gateway.

Ownership: store and retrieve network-layer route decisions, freshness state,
and lightweight circuit-breaker signals.
Non-goals: fetching resources, interpreting resource intent, changing system
proxy/DNS, switching Clash nodes, creating leases, or storing secrets.
State behavior: writes a local SQLite cache under `_bridge/runtime`; entries
are route evidence only and may be refreshed or discarded by network owners.
Caller context: `codex_network_gateway.py` uses this module so callers such as
the resource layer can consume route advice without duplicating network policy.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "_bridge" / "runtime" / "codex_network_gateway"
DB_PATH = RUNTIME_DIR / "route_cache.sqlite"
SCHEMA_PREFIX = "network_route_cache"

DEFAULT_FRESH_TTL_SECONDS = 300
DEFAULT_STALE_TTL_SECONDS = 1800
CIRCUIT_OPEN_SECONDS = 120


@dataclass(frozen=True)
class RouteCacheKey:
    target_kind: str
    host: str
    owner_tool: str
    runtime: str

    def as_tuple(self) -> tuple[str, str, str, str]:
        return self.target_kind, self.host, self.owner_tool, self.runtime


def now_epoch() -> float:
    return time.time()


def normalize_host(target: str) -> str:
    value = str(target or "").strip()
    if not value:
        return ""
    parsed = urllib.parse.urlparse(value if "://" in value else f"//{value}")
    return (parsed.hostname or value.split("/", 1)[0]).lower()


def normalize_owner_tool(owner_tool: str = "") -> str:
    return str(owner_tool or "generic").strip().lower().replace(" ", "_") or "generic"


def cache_key(target_kind: str, target: str, runtime: str, owner_tool: str = "") -> RouteCacheKey:
    return RouteCacheKey(
        target_kind=str(target_kind or "external").strip().lower() or "external",
        host=normalize_host(target),
        owner_tool=normalize_owner_tool(owner_tool),
        runtime=str(runtime or "generic").strip().lower() or "generic",
    )


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS route_decisions (
            target_kind TEXT NOT NULL,
            host TEXT NOT NULL,
            runtime TEXT NOT NULL,
            route_mode TEXT NOT NULL,
            route_reason TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            fresh_until REAL NOT NULL,
            stale_until REAL NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            circuit_state TEXT NOT NULL DEFAULT 'closed',
            circuit_until REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (target_kind, host, runtime)
        );
        CREATE TABLE IF NOT EXISTS route_decisions_v2 (
            target_kind TEXT NOT NULL,
            host TEXT NOT NULL,
            owner_tool TEXT NOT NULL DEFAULT 'generic',
            runtime TEXT NOT NULL,
            route_mode TEXT NOT NULL,
            route_reason TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            fresh_until REAL NOT NULL,
            stale_until REAL NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 0,
            circuit_state TEXT NOT NULL DEFAULT 'closed',
            circuit_until REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (target_kind, host, owner_tool, runtime)
        );
        CREATE TABLE IF NOT EXISTS route_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_kind TEXT NOT NULL,
            host TEXT NOT NULL,
            owner_tool TEXT NOT NULL DEFAULT 'generic',
            runtime TEXT NOT NULL,
            route_mode TEXT NOT NULL,
            node TEXT NOT NULL DEFAULT '',
            ok INTEGER NOT NULL,
            latency_ms REAL NOT NULL DEFAULT 0,
            error_class TEXT NOT NULL DEFAULT '',
            observed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS target_host_stats (
            target_kind TEXT NOT NULL,
            host TEXT NOT NULL,
            runtime TEXT NOT NULL,
            route_mode TEXT NOT NULL,
            node TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            total_latency_ms REAL NOT NULL DEFAULT 0,
            last_latency_ms REAL NOT NULL DEFAULT 0,
            last_ok INTEGER NOT NULL DEFAULT 0,
            last_error_class TEXT NOT NULL DEFAULT '',
            last_observed_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (target_kind, host, runtime, route_mode, node)
        );
        CREATE TABLE IF NOT EXISTS target_host_stats_v2 (
            target_kind TEXT NOT NULL,
            host TEXT NOT NULL,
            owner_tool TEXT NOT NULL DEFAULT 'generic',
            runtime TEXT NOT NULL,
            route_mode TEXT NOT NULL,
            node TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            total_latency_ms REAL NOT NULL DEFAULT 0,
            last_latency_ms REAL NOT NULL DEFAULT 0,
            last_ok INTEGER NOT NULL DEFAULT 0,
            last_error_class TEXT NOT NULL DEFAULT '',
            last_observed_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (target_kind, host, owner_tool, runtime, route_mode, node)
        );
        """
    )
    existing_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(route_observations)").fetchall()
    }
    if "node" not in existing_columns:
        conn.execute("ALTER TABLE route_observations ADD COLUMN node TEXT NOT NULL DEFAULT ''")
    if "owner_tool" not in existing_columns:
        conn.execute("ALTER TABLE route_observations ADD COLUMN owner_tool TEXT NOT NULL DEFAULT 'generic'")
    conn.commit()


def row_to_decision(row: sqlite3.Row, *, generated_at: float | None = None) -> dict[str, Any]:
    current = now_epoch() if generated_at is None else generated_at
    plan = compact_plan_for_storage(json.loads(row["plan_json"] or "{}"))
    if current <= float(row["fresh_until"]):
        freshness = "fresh"
    elif current <= float(row["stale_until"]):
        freshness = "stale"
    else:
        freshness = "expired"
    circuit_state = str(row["circuit_state"] or "closed")
    if circuit_state == "open" and current > float(row["circuit_until"] or 0):
        circuit_state = "half_open"
    return {
        "schema": f"{SCHEMA_PREFIX}.decision.v1",
        "ok": True,
        "cache_hit": freshness in {"fresh", "stale"},
        "freshness": freshness,
        "target_kind": row["target_kind"],
        "host": row["host"],
        "owner_tool": row["owner_tool"] if "owner_tool" in row.keys() else "generic",
        "runtime": row["runtime"],
        "route_mode": row["route_mode"],
        "route_reason": row["route_reason"],
        "plan": plan,
        "updated_at_epoch": float(row["updated_at"]),
        "fresh_until_epoch": float(row["fresh_until"]),
        "stale_until_epoch": float(row["stale_until"]),
        "failure_count": int(row["failure_count"]),
        "circuit_state": circuit_state,
        "circuit_until_epoch": float(row["circuit_until"] or 0),
        "should_revalidate_async": freshness == "stale" or circuit_state == "half_open",
        "source": "network_route_cache",
    }


def get_decision(target_kind: str, target: str, runtime: str, *, owner_tool: str = "", allow_stale: bool = True) -> dict[str, Any]:
    key = cache_key(target_kind, target, runtime, owner_tool)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM route_decisions_v2
            WHERE target_kind = ? AND host = ? AND owner_tool = ? AND runtime = ?
            """,
            key.as_tuple(),
        ).fetchone()
    if not row:
        return {
            "schema": f"{SCHEMA_PREFIX}.decision.v1",
            "ok": False,
            "cache_hit": False,
            "freshness": "miss",
            "target_kind": key.target_kind,
            "host": key.host,
            "owner_tool": key.owner_tool,
            "runtime": key.runtime,
            "reason": "route_decision_missing",
        }
    decision = row_to_decision(row)
    if decision["freshness"] == "expired" or (decision["freshness"] == "stale" and not allow_stale):
        decision["cache_hit"] = False
    return decision


def _compact_clash_recommendation(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    compact = {
        key: value.get(key)
        for key in (
            "schema",
            "ok",
            "target",
            "sites",
            "group",
            "reason",
            "recommended_access_mode",
            "recommended_node",
            "best",
            "generated_at",
            "writes_network_state",
            "secret_values_returned",
        )
        if key in value
    }
    ranked = value.get("ranked")
    if isinstance(ranked, list):
        compact["ranked_top"] = ranked[:3]
        compact["ranked_count"] = len(ranked)
    policy = value.get("access_candidate_policy")
    if isinstance(policy, dict):
        compact["access_candidate_policy"] = {
            key: policy.get(key)
            for key in (
                "default_access_limit",
                "requested_access_limit",
                "delay_qualified_only",
                "fresh_access_cache_reused",
                "fresh_access_available_after_refresh",
                "scoring_order",
            )
            if key in policy
        }
    return compact


def _compact_probe(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        key: value.get(key)
        for key in (
            "ok",
            "schema",
            "target",
            "context",
            "classification",
            "latency_ms",
            "error_class",
            "warnings",
        )
        if key in value
    }


def compact_plan_for_storage(plan: dict[str, Any]) -> dict[str, Any]:
    """Return the durable route-decision subset stored in SQLite.

    Cache entries must not recursively store previous cache hits. They also
    should not retain full node rankings or verbose diagnostic payloads; those
    belong in fresh command output, not in every future cached response.
    """

    if not isinstance(plan, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in ("schema", "ok", "generated_at", "network_profile_version", "owner_tool", "plan", "network_recommendation", "runtime_env"):
        if key in plan:
            compact[key] = plan[key]
    if "clash_node_recommendation" in plan:
        compact["clash_node_recommendation"] = _compact_clash_recommendation(plan.get("clash_node_recommendation"))
    if "probe" in plan:
        compact["probe"] = _compact_probe(plan.get("probe"))
    compact["route_cache"] = {
        "cache_hit": False,
        "freshness": "stored_compact",
        "source": "network_route_cache",
    }
    compact.pop("cache_status", None)
    return compact


def decision_metadata(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        key: decision.get(key)
        for key in (
            "schema",
            "ok",
            "cache_hit",
            "freshness",
            "target_kind",
            "host",
            "owner_tool",
            "runtime",
            "route_mode",
            "route_reason",
            "updated_at_epoch",
            "fresh_until_epoch",
            "stale_until_epoch",
            "failure_count",
            "circuit_state",
            "circuit_until_epoch",
            "should_revalidate_async",
            "source",
        )
        if key in decision
    }


def put_decision(
    *,
    target_kind: str,
    target: str,
    runtime: str,
    owner_tool: str = "",
    plan: dict[str, Any],
    fresh_ttl_seconds: int = DEFAULT_FRESH_TTL_SECONDS,
    stale_ttl_seconds: int = DEFAULT_STALE_TTL_SECONDS,
) -> dict[str, Any]:
    key = cache_key(target_kind, target, runtime, owner_tool)
    stored_plan = compact_plan_for_storage(plan)
    route = plan.get("plan") if isinstance(plan.get("plan"), dict) else plan
    route_mode = str(route.get("route_mode") or plan.get("route_mode") or "")
    route_reason = str(route.get("route_reason") or plan.get("route_reason") or "")
    current = now_epoch()
    fresh_until = current + max(1, int(fresh_ttl_seconds))
    stale_until = fresh_until + max(0, int(stale_ttl_seconds))
    with connect() as conn:
        previous = conn.execute(
            """
            SELECT failure_count, circuit_state, circuit_until FROM route_decisions
            WHERE target_kind = ? AND host = ? AND runtime = ?
            """,
            (key.target_kind, key.host, key.runtime),
        ).fetchone()
        previous_v2 = conn.execute(
            """
            SELECT failure_count, circuit_state, circuit_until FROM route_decisions_v2
            WHERE target_kind = ? AND host = ? AND owner_tool = ? AND runtime = ?
            """,
            key.as_tuple(),
        ).fetchone()
        previous = previous_v2 or previous
        conn.execute(
            """
            INSERT OR REPLACE INTO route_decisions_v2
            (target_kind, host, owner_tool, runtime, route_mode, route_reason, plan_json,
             updated_at, fresh_until, stale_until, failure_count, circuit_state, circuit_until)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key.target_kind,
                key.host,
                key.owner_tool,
                key.runtime,
                route_mode,
                route_reason,
                json.dumps(stored_plan, ensure_ascii=False, sort_keys=True),
                current,
                fresh_until,
                stale_until,
                int(previous["failure_count"]) if previous else 0,
                str(previous["circuit_state"]) if previous else "closed",
                float(previous["circuit_until"]) if previous else 0.0,
            ),
        )
        conn.commit()
    return get_decision(target_kind, target, runtime, owner_tool=owner_tool)


def record_observation(
    *,
    target_kind: str,
    target: str,
    runtime: str,
    route_mode: str,
    ok: bool,
    owner_tool: str = "",
    latency_ms: float = 0,
    error_class: str = "",
    node: str = "",
) -> dict[str, Any]:
    key = cache_key(target_kind, target, runtime, owner_tool)
    current = now_epoch()
    normalized_node = str(node or "")
    normalized_route = str(route_mode or "")
    normalized_error = str(error_class or "")
    normalized_latency = float(latency_ms or 0)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO route_observations
            (target_kind, host, owner_tool, runtime, route_mode, node, ok, latency_ms, error_class, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key.target_kind,
                key.host,
                key.owner_tool,
                key.runtime,
                normalized_route,
                normalized_node,
                1 if ok else 0,
                normalized_latency,
                normalized_error,
                current,
            ),
        )
        conn.execute(
            """
            INSERT INTO target_host_stats_v2 (
              target_kind, host, owner_tool, runtime, route_mode, node, attempt_count,
              success_count, failure_count, total_latency_ms, last_latency_ms,
              last_ok, last_error_class, last_observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_kind, host, owner_tool, runtime, route_mode, node) DO UPDATE SET
              attempt_count = attempt_count + 1,
              success_count = success_count + excluded.success_count,
              failure_count = failure_count + excluded.failure_count,
              total_latency_ms = total_latency_ms + excluded.total_latency_ms,
              last_latency_ms = excluded.last_latency_ms,
              last_ok = excluded.last_ok,
              last_error_class = excluded.last_error_class,
              last_observed_at = excluded.last_observed_at
            """,
            (
                key.target_kind,
                key.host,
                key.owner_tool,
                key.runtime,
                normalized_route,
                normalized_node,
                1 if ok else 0,
                0 if ok else 1,
                normalized_latency if normalized_latency > 0 else 0,
                normalized_latency,
                1 if ok else 0,
                "" if ok else normalized_error,
                current,
            ),
        )
        row = conn.execute(
            """
            SELECT failure_count FROM route_decisions_v2
            WHERE target_kind = ? AND host = ? AND owner_tool = ? AND runtime = ?
            """,
            key.as_tuple(),
        ).fetchone()
        failure_count = 0 if ok else (int(row["failure_count"]) + 1 if row else 1)
        circuit_state = "closed"
        circuit_until = 0.0
        if failure_count >= 3:
            circuit_state = "open"
            circuit_until = current + CIRCUIT_OPEN_SECONDS
        if row:
            conn.execute(
                """
                UPDATE route_decisions_v2
                SET failure_count = ?, circuit_state = ?, circuit_until = ?
                WHERE target_kind = ? AND host = ? AND owner_tool = ? AND runtime = ?
                """,
                (failure_count, circuit_state, circuit_until, *key.as_tuple()),
            )
        conn.commit()
    return {
        "schema": f"{SCHEMA_PREFIX}.observation.v1",
        "ok": True,
        "target_kind": key.target_kind,
        "host": key.host,
        "owner_tool": key.owner_tool,
        "runtime": key.runtime,
        "route_mode": normalized_route,
        "node": normalized_node,
        "observation_ok": bool(ok),
        "latency_ms": normalized_latency,
        "error_class": normalized_error,
        "failure_count": failure_count,
        "circuit_state": circuit_state,
        "circuit_until_epoch": circuit_until,
    }


def target_stats(target_kind: str = "", target: str = "", runtime: str = "", owner_tool: str = "", *, limit: int = 20) -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if target_kind:
        clauses.append("target_kind = ?")
        params.append(str(target_kind).strip().lower())
    if target:
        clauses.append("host = ?")
        params.append(normalize_host(target))
    if runtime:
        clauses.append("runtime = ?")
        params.append(str(runtime).strip().lower())
    if owner_tool:
        clauses.append("owner_tool = ?")
        params.append(normalize_owner_tool(owner_tool))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT target_kind, host, owner_tool, runtime, route_mode, node, attempt_count,
                   success_count, failure_count, total_latency_ms,
                   last_latency_ms, last_ok, last_error_class, last_observed_at
            FROM target_host_stats_v2
            {where}
            ORDER BY last_observed_at DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()
    result_rows: list[dict[str, Any]] = []
    for row in rows:
        attempts = int(row["attempt_count"] or 0)
        successes = int(row["success_count"] or 0)
        result_rows.append(
            {
                "target_kind": row["target_kind"],
                "host": row["host"],
                "owner_tool": row["owner_tool"],
                "runtime": row["runtime"],
                "route_mode": row["route_mode"],
                "node": row["node"],
                "attempt_count": attempts,
                "success_count": successes,
                "failure_count": int(row["failure_count"] or 0),
                "success_rate": round(successes / attempts, 4) if attempts else 0,
                "average_latency_ms": round(float(row["total_latency_ms"] or 0) / attempts, 1) if attempts else 0,
                "last_latency_ms": float(row["last_latency_ms"] or 0),
                "last_ok": bool(row["last_ok"]),
                "last_error_class": row["last_error_class"],
                "last_observed_at_epoch": float(row["last_observed_at"] or 0),
            }
        )
    return {
        "schema": f"{SCHEMA_PREFIX}.target_stats.v1",
        "ok": True,
        "db_path": str(DB_PATH),
        "filters": {"target_kind": target_kind, "target": target, "owner_tool": owner_tool, "runtime": runtime},
        "rows": result_rows,
    }


def snapshot(limit: int = 50) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM route_decisions_v2
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        observation_count = conn.execute("SELECT COUNT(*) AS count FROM route_observations").fetchone()["count"]
        target_stats_count = conn.execute("SELECT COUNT(*) AS count FROM target_host_stats_v2").fetchone()["count"]
    return {
        "schema": f"{SCHEMA_PREFIX}.snapshot.v1",
        "ok": True,
        "db_path": str(DB_PATH),
        "decision_count": len(rows),
        "observation_count": int(observation_count),
        "target_stats_count": int(target_stats_count),
        "decisions": [decision_metadata(row_to_decision(row)) | {"plan_size_bytes": len(row["plan_json"] or "")} for row in rows],
        "target_stats": target_stats(limit=min(10, max(1, int(limit)))).get("rows", []),
        "writes_system_proxy": False,
        "writes_dns": False,
        "writes_clash_config": False,
    }


def validate() -> dict[str, Any]:
    key = cache_key("github", "https://github.com/openai/codex", "python", "github")
    same = cache_key("github", "https://github.com/microsoft/playwright", "python", "github")
    different_owner = cache_key("github", "https://github.com/openai/codex", "python", "browser")
    sample = compact_plan_for_storage(
        {
            "schema": "codex_network_gateway.plan.v1",
            "ok": True,
            "network_profile_version": "sample",
            "plan": {"route_mode": "current_proxy_env", "route_reason": "sample"},
            "route_cache": {"plan": {"route_cache": {"plan": {"bad": True}}}},
            "cache_status": "route_cache_fresh",
            "clash_node_recommendation": {"ranked": [{"node": "a"}, {"node": "b"}, {"node": "c"}, {"node": "d"}]},
        }
    )
    sample_json = json.dumps(sample, ensure_ascii=False, sort_keys=True)
    with connect() as conn:
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    return {
        "schema": f"{SCHEMA_PREFIX}.validate.v1",
        "ok": key == same
        and key != different_owner
        and key.host == "github.com"
        and key.owner_tool == "github"
        and "cache_status" not in sample
        and sample.get("network_profile_version") == "sample"
        and len(sample.get("clash_node_recommendation", {}).get("ranked_top", [])) == 3
        and len(sample_json) < 3000
        and "target_host_stats_v2" in tables
        and "route_decisions_v2" in tables,
        "db_path": str(DB_PATH),
        "cache_key_sample": {"target_kind": key.target_kind, "host": key.host, "owner_tool": key.owner_tool, "runtime": key.runtime},
        "owner_tool_key_dimension_ok": key != different_owner,
        "stored_plan_compaction_ok": "cache_status" not in sample and len(sample_json) < 3000,
        "network_profile_version_preserved": sample.get("network_profile_version") == "sample",
        "target_stats_schema_ok": "target_host_stats_v2" in tables,
        "route_decisions_v2_schema_ok": "route_decisions_v2" in tables,
        "writes_system_proxy": False,
        "writes_dns": False,
        "writes_clash_config": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex network route cache")
    sub = parser.add_subparsers(dest="cmd", required=True)
    get = sub.add_parser("get")
    get.add_argument("--target-kind", required=True)
    get.add_argument("--target", required=True)
    get.add_argument("--owner-tool", default="")
    get.add_argument("--runtime", default="generic")
    get.add_argument("--fresh-only", action="store_true")
    put = sub.add_parser("put")
    put.add_argument("--target-kind", required=True)
    put.add_argument("--target", required=True)
    put.add_argument("--owner-tool", default="")
    put.add_argument("--runtime", default="generic")
    put.add_argument("--plan-json", required=True)
    put.add_argument("--fresh-ttl-seconds", type=int, default=DEFAULT_FRESH_TTL_SECONDS)
    put.add_argument("--stale-ttl-seconds", type=int, default=DEFAULT_STALE_TTL_SECONDS)
    observe = sub.add_parser("observe")
    observe.add_argument("--target-kind", required=True)
    observe.add_argument("--target", required=True)
    observe.add_argument("--owner-tool", default="")
    observe.add_argument("--runtime", default="generic")
    observe.add_argument("--route-mode", required=True)
    observe.add_argument("--node", default="")
    observe.add_argument("--ok", action="store_true")
    observe.add_argument("--latency-ms", type=float, default=0)
    observe.add_argument("--error-class", default="")
    stats = sub.add_parser("target-stats")
    stats.add_argument("--target-kind", default="")
    stats.add_argument("--target", default="")
    stats.add_argument("--owner-tool", default="")
    stats.add_argument("--runtime", default="")
    stats.add_argument("--limit", type=int, default=20)
    snapshot_cmd = sub.add_parser("snapshot")
    snapshot_cmd.add_argument("--limit", type=int, default=50)
    sub.add_parser("validate")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cmd == "get":
        payload = get_decision(args.target_kind, args.target, args.runtime, owner_tool=args.owner_tool, allow_stale=not args.fresh_only)
    elif args.cmd == "put":
        payload = put_decision(
            target_kind=args.target_kind,
            target=args.target,
            runtime=args.runtime,
            owner_tool=args.owner_tool,
            plan=json.loads(args.plan_json),
            fresh_ttl_seconds=args.fresh_ttl_seconds,
            stale_ttl_seconds=args.stale_ttl_seconds,
        )
    elif args.cmd == "observe":
        payload = record_observation(
            target_kind=args.target_kind,
            target=args.target,
            runtime=args.runtime,
            route_mode=args.route_mode,
            ok=args.ok,
            owner_tool=args.owner_tool,
            latency_ms=args.latency_ms,
            error_class=args.error_class,
            node=args.node,
        )
    elif args.cmd == "target-stats":
        payload = target_stats(args.target_kind, args.target, args.runtime, owner_tool=args.owner_tool, limit=args.limit)
    elif args.cmd == "snapshot":
        payload = snapshot(limit=args.limit)
    else:
        payload = validate()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
