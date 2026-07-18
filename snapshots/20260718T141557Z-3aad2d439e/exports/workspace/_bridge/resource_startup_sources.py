#!/usr/bin/env python3
"""Read-only startup-source analysis for resource process fanout.

Owns: command-line normalization, close-in-time launch batch grouping, parent
source aggregation, and governance recommendations for repeated launcher
fanout.
Non-goals: process snapshot collection, process cleanup, killing processes,
rewriting startup configuration, or MCP health diagnosis.
State behavior: consumes caller-provided process snapshots only; never reads or
writes external state.
Normal callers: `resource_process_doctor.py startup-sources` and repair-plan
planning paths.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from shared.json_cli import now_iso


JsonDict = dict[str, Any]
ExpectedMax = Callable[[JsonDict], int]


def normalize_command(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:500]


def launch_batches(processes: list[JsonDict], window_seconds: int = 5) -> list[JsonDict]:
    """Group root-instance starts that are close enough to be one launch wave."""
    parsed: list[tuple[datetime, JsonDict]] = []
    for proc in processes:
        start_text = str(proc.get("start_time") or "")
        if not start_text:
            continue
        try:
            start = datetime.fromisoformat(start_text)
        except ValueError:
            continue
        parsed.append((start, proc))
    parsed.sort(key=lambda item: item[0])

    batches: list[JsonDict] = []
    for start, proc in parsed:
        if not batches:
            batches.append(
                {
                    "start_time": start.isoformat(),
                    "end_time": start.isoformat(),
                    "count": 1,
                    "pids": [proc.get("pid")],
                }
            )
            continue
        last = batches[-1]
        last_end = datetime.fromisoformat(str(last["end_time"]))
        if (start - last_end).total_seconds() <= window_seconds:
            last["end_time"] = start.isoformat()
            last["count"] = int(last["count"]) + 1
            last["pids"].append(proc.get("pid"))
        else:
            batches.append(
                {
                    "start_time": start.isoformat(),
                    "end_time": start.isoformat(),
                    "count": 1,
                    "pids": [proc.get("pid")],
                }
            )
    return batches


def startup_governance_policy(group: JsonDict, parent_rows: list[JsonDict], effective_expected_max: ExpectedMax) -> JsonDict:
    group_name = str(group.get("group") or "")
    protected = bool(group.get("protected"))
    count = int(group.get("root_instance_count") or group.get("count") or 0)
    expected = effective_expected_max(group)
    dominant_parent = parent_rows[0] if parent_rows else {}
    parent_count = int(dominant_parent.get("count") or 0)
    recommendation = "observe_only"
    if count > expected and parent_count >= 2:
        recommendation = "govern_repeated_launcher"
    if protected:
        recommendation = "protected_manual_review"
    if group_name in {"node_repl"}:
        recommendation = "cap_idle_repl_lifecycle"
    return {
        "recommendation": recommendation,
        "preferred_fix": (
            "identify why this parent repeatedly starts the same MCP/resource server and add a singleton/session reuse guard"
            if recommendation == "govern_repeated_launcher"
            else "manual review only; do not stop or modify protected services from this plan"
            if recommendation == "protected_manual_review"
            else "close only idle REPL/session owners after confirming no active task depends on them"
            if recommendation == "cap_idle_repl_lifecycle"
            else "no action unless fanout grows or becomes user-visible"
        ),
        "forbidden_auto_actions": [
            "kill_processes",
            "rewrite_codex_config",
            "restart_bridge_or_gateway",
            "stop_reasonix_responder",
            "send_weixin_messages",
        ],
    }


def build_startup_sources(snapshot: JsonDict, effective_expected_max: ExpectedMax) -> JsonDict:
    groups = snapshot.get("groups") if isinstance(snapshot.get("groups"), list) else []
    processes = snapshot.get("processes") if isinstance(snapshot.get("processes"), list) else []
    results: list[JsonDict] = []
    for group in groups:
        group_name = str(group.get("group") or "")
        group_processes = [proc for proc in processes if proc.get("group") == group_name]
        by_parent: dict[str, JsonDict] = {}
        root_processes = [proc for proc in group_processes if proc.get("instance_root")]
        for proc in root_processes:
            key = "{pid}|{name}|{cmd}".format(
                pid=proc.get("parent_pid") or 0,
                name=proc.get("parent_name") or "",
                cmd=normalize_command(proc.get("parent_command_line")),
            )
            parent = by_parent.setdefault(
                key,
                {
                    "parent_pid": proc.get("parent_pid"),
                    "parent_name": proc.get("parent_name") or "",
                    "parent_command_line": normalize_command(proc.get("parent_command_line")),
                    "count": 0,
                    "child_pids": [],
                    "child_names": {},
                    "root_processes": [],
                    "oldest_start_time": "",
                    "newest_start_time": "",
                },
            )
            parent["count"] += 1
            parent["child_pids"].append(proc.get("pid"))
            parent["root_processes"].append(proc)
            child_name = str(proc.get("name") or "")
            parent["child_names"][child_name] = int(parent["child_names"].get(child_name, 0)) + 1
            starts = sorted(
                str(item.get("start_time") or "")
                for item in group_processes
                if "{pid}|{name}|{cmd}".format(
                    pid=item.get("parent_pid") or 0,
                    name=item.get("parent_name") or "",
                    cmd=normalize_command(item.get("parent_command_line")),
                )
                == key
                and item.get("start_time")
            )
            parent["oldest_start_time"] = starts[0] if starts else ""
            parent["newest_start_time"] = starts[-1] if starts else ""
        for parent in by_parent.values():
            parent["launch_batches"] = launch_batches(
                parent.pop("root_processes", []),
                window_seconds=5,
            )
            parent["launch_batch_count"] = len(parent["launch_batches"])
        parent_rows = sorted(by_parent.values(), key=lambda row: (-int(row.get("count") or 0), str(row.get("parent_name") or "")))
        expected = effective_expected_max(group)
        results.append(
            {
                "group": group_name,
                "category": group.get("category"),
                "count": group.get("count"),
                "root_instance_count": group.get("root_instance_count"),
                "expected_max": group.get("expected_max"),
                "effective_expected_max": expected,
                "host_root_counts": group.get("host_root_counts"),
                "excess": max(
                    0,
                    int(group.get("root_instance_count") or group.get("count") or 0)
                    - expected,
                ),
                "protected": bool(group.get("protected")),
                "cleanup_policy": group.get("cleanup_policy"),
                "parent_source_count": len(parent_rows),
                "top_parent_sources": parent_rows[:8],
                "governance": startup_governance_policy(group, parent_rows, effective_expected_max),
            }
        )
    fanout = [item for item in results if int(item.get("excess") or 0) > 0]
    return {
        "schema": "resource_process.startup_sources.v1",
        "ok": bool(snapshot.get("ok")),
        "generated_at": now_iso(),
        "summary": {
            "fanout_group_count": len(fanout),
            "group_count": len(results),
            "single_parent_fanout_groups": [
                item.get("group")
                for item in fanout
                if int(item.get("parent_source_count") or 0) == 1
            ],
            "multi_parent_fanout_groups": [
                item.get("group")
                for item in fanout
                if int(item.get("parent_source_count") or 0) > 1
            ],
        },
        "groups": results,
        "dry_run_contract": {
            "writes_files": False,
            "kills_processes": False,
            "starts_processes": False,
            "sends_messages": False,
            "changes_startup_sources": False,
        },
    }
