"""Pure lifecycle selection helpers for resource_process_doctor.

Owns process-tree and launch-batch selection policy for resource cleanup plans.
Non-goals: process sampling, process termination, current-turn MCP evidence,
CLI argument parsing, and repair-plan output assembly. This module is pure
data transformation so resource_process_doctor can keep process safety gates
and command ownership in one place.
"""

from __future__ import annotations

import re
from typing import Any


def live_watch_signature(proc: dict[str, Any]) -> tuple[str, str]:
    command = str(proc.get("command_line") or "")
    port_match = re.search(r"--port\s+(\d+)", command)
    output_match = re.search(r'--output\s+("[^"]+"|\S+)', command)
    port = port_match.group(1) if port_match else "18791"
    output = output_match.group(1).strip('"') if output_match else ""
    return port, output.lower()


def descendant_pids(root_pid: Any, processes: list[dict[str, Any]]) -> set[Any]:
    children_by_parent: dict[Any, list[Any]] = {}
    for proc in processes:
        parent_pid = proc.get("parent_pid")
        pid = proc.get("pid")
        if pid is None:
            continue
        children_by_parent.setdefault(parent_pid, []).append(pid)

    seen: set[Any] = set()
    stack = list(children_by_parent.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children_by_parent.get(pid, []))
    return seen


def pids_with_descendants(root_pids: set[Any], processes: list[dict[str, Any]]) -> set[Any]:
    pids = set(root_pids)
    for pid in list(root_pids):
        pids.update(descendant_pids(pid, processes))
    return pids


def live_watch_duplicate_lifecycle(root_candidates: list[dict[str, Any]], processes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not root_candidates:
        return None
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in root_candidates:
        buckets.setdefault(live_watch_signature(item), []).append(item)
    keep_root_pids: set[Any] = set()
    orphan_root_pids: set[Any] = set()
    duplicate_sets: list[dict[str, Any]] = []
    for signature, items in buckets.items():
        sorted_items = sorted(items, key=lambda row: str(row.get("start_time") or ""))
        keep = sorted_items[-1:]
        orphan = sorted_items[:-1]
        keep_root_pids.update(item.get("pid") for item in keep)
        orphan_root_pids.update(item.get("pid") for item in orphan)
        if orphan:
            duplicate_sets.append(
                {
                    "signature": {"port": signature[0], "output": signature[1]},
                    "keep_root_pids": [item.get("pid") for item in keep],
                    "orphan_root_pids": [item.get("pid") for item in orphan],
                }
            )
    if not orphan_root_pids:
        return None
    keep_pids = pids_with_descendants(keep_root_pids, processes)
    orphan_pids = pids_with_descendants(orphan_root_pids, processes)
    orphan_pids.difference_update(keep_pids)
    return {
        "policy": "keep_newest_dashboard_observer_per_port_output",
        "reason": "Duplicate codex_app_live_watch instances for the same dashboard output/port multiply thread/turns/list load; keep newest and stop older roots.",
        "keep_pids": sorted(keep_pids),
        "keep_root_instance_pids": sorted(keep_root_pids),
        "orphan_candidate_pids": sorted(orphan_pids),
        "orphan_candidate_root_instance_pids": sorted(orphan_root_pids),
        "orphan_batches": duplicate_sets,
        "latest_batches_kept": duplicate_sets,
    }


def normalized_launch_batches(source_group: dict[str, Any]) -> list[dict[str, Any]]:
    parent_sources = (
        source_group.get("top_parent_sources")
        if isinstance(source_group.get("top_parent_sources"), list)
        else []
    )
    all_batches: list[dict[str, Any]] = []
    for source in parent_sources:
        batches = source.get("launch_batches") if isinstance(source.get("launch_batches"), list) else []
        for batch in batches:
            pids = [pid for pid in batch.get("pids", []) if pid is not None]
            if not pids:
                continue
            all_batches.append(
                {
                    "parent_pid": source.get("parent_pid"),
                    "parent_name": source.get("parent_name"),
                    "parent_command_line": source.get("parent_command_line"),
                    "start_time": batch.get("start_time"),
                    "end_time": batch.get("end_time"),
                    "pids": pids,
                }
            )
    return sorted(all_batches, key=lambda item: str(item.get("start_time") or ""))


def launch_batch_lifecycle(
    *,
    all_batches: list[dict[str, Any]],
    expected: int,
    processes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if len(all_batches) <= expected:
        return None
    kept_batches = all_batches[-expected:]
    orphan_batches = all_batches[:-expected]
    kept_root_pids: set[Any] = set()
    orphan_root_pids: set[Any] = set()
    for batch in kept_batches:
        kept_root_pids.update(batch.get("pids") or [])
    for batch in orphan_batches:
        orphan_root_pids.update(batch.get("pids") or [])
    keep_pids = pids_with_descendants(kept_root_pids, processes)
    orphan_pids = pids_with_descendants(orphan_root_pids, processes)
    orphan_pids.difference_update(keep_pids)
    parent_count = len({batch.get("parent_pid") for batch in all_batches if batch.get("parent_pid") is not None})
    policy = (
        "keep_latest_global_launch_roots_across_multiple_parents"
        if parent_count > 1
        else "keep_latest_launch_roots_within_expected_budget_review_older_batches"
    )
    return {
        "policy": policy,
        "reason": "Multiple launch batches exceed effective_expected_max; keep the newest batch roots globally and review older roots as orphan candidates.",
        "keep_pids": sorted(keep_pids),
        "keep_root_instance_pids": sorted(kept_root_pids),
        "orphan_candidate_pids": sorted(orphan_pids),
        "orphan_candidate_root_instance_pids": sorted(orphan_root_pids),
        "orphan_batches": orphan_batches,
        "latest_batches_kept": kept_batches,
    }


def latest_root_instances_fallback_lifecycle(
    *,
    root_candidates: list[dict[str, Any]],
    expected: int,
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    keep_roots = root_candidates[-expected:] if root_candidates else []
    keep_root_pids = {item.get("pid") for item in keep_roots}
    keep_pids = pids_with_descendants(keep_root_pids, processes)
    return {
        "policy": "latest_root_instances_fallback",
        "reason": "Observed launch batches are within effective_expected_max; repair plan stays conservative.",
        "keep_pids": sorted(keep_pids),
        "keep_root_instance_pids": sorted(keep_root_pids),
        "orphan_candidate_pids": [],
        "orphan_candidate_root_instance_pids": [],
        "orphan_batches": [],
        "latest_batches_kept": [],
    }


def lifecycle_candidates(
    *,
    group_name: str,
    expected: int,
    source_group: dict[str, Any],
    root_candidates: list[dict[str, Any]],
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    if group_name == "codex_app_live_watch":
        live_watch_lifecycle = live_watch_duplicate_lifecycle(root_candidates, processes)
        if live_watch_lifecycle:
            return live_watch_lifecycle
    all_batches = normalized_launch_batches(source_group)
    batch_lifecycle = launch_batch_lifecycle(
        all_batches=all_batches,
        expected=expected,
        processes=processes,
    )
    if batch_lifecycle:
        return batch_lifecycle
    return latest_root_instances_fallback_lifecycle(
        root_candidates=root_candidates,
        expected=expected,
        processes=processes,
    )
