"""Project Desktop queued-follow-up paths for WSL session resume.

Ownership: pure transformation of Desktop global-state resume contexts and
additive task visibility indexes.
Non-goals: changing session transcripts, SQLite thread rows, MCP definitions,
Desktop workspace preference roots, existing project assignments, or live
state while Desktop is running.
State behavior: mutate only the supplied JSON object and return bounded change
counts; the startup owner decides backup, write, and restart-boundary timing.
Caller context: codex_state_repair facade during the WSL startup preflight.
"""

from __future__ import annotations

import re
from typing import Any


WSL_WORKSPACE_ROOT = "/home/codexlab/work/codex-workspace"
WINDOWS_DRIVE_PATH = re.compile(r"(?i)([a-z]):[\\/](.*)$")


def windows_context_path_to_wsl(value: str) -> str:
    """Translate Windows paths, including malformed POSIX/Windows hybrids."""
    text = str(value or "").strip()
    match = WINDOWS_DRIVE_PATH.search(text)
    if match is None:
        return text
    drive = match.group(1).lower()
    rest = match.group(2).replace("\\", "/").lstrip("/")
    if drive == "w":
        return WSL_WORKSPACE_ROOT if not rest else f"{WSL_WORKSPACE_ROOT}/{rest}"
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def _replace_path(container: dict[str, Any], key: str) -> int:
    value = container.get(key)
    if not isinstance(value, str):
        return 0
    translated = windows_context_path_to_wsl(value)
    if translated == value:
        return 0
    container[key] = translated
    return 1


def project_queued_follow_up_contexts(state: dict[str, Any]) -> dict[str, Any]:
    """Repair only queued follow-up cwd fields used to resume old sessions."""
    queued = state.get("queued-follow-ups")
    if not isinstance(queued, dict):
        atom_state = state.get("electron-persisted-atom-state")
        queued = atom_state.get("queued-follow-ups") if isinstance(atom_state, dict) else None
    if not isinstance(queued, dict):
        return {"changed": False, "changed_field_count": 0, "thread_count": 0}

    changed_field_count = 0
    thread_count = 0
    for entries in queued.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            thread_count += 1
            changed_field_count += _replace_path(entry, "cwd")
            context = entry.get("context")
            if not isinstance(context, dict):
                continue
            changed_field_count += _replace_path(context, "cwd")
            roots = context.get("workspaceRoots")
            if not isinstance(roots, list):
                continue
            for index, root in enumerate(roots):
                if not isinstance(root, str):
                    continue
                translated = windows_context_path_to_wsl(root)
                if translated != root:
                    roots[index] = translated
                    changed_field_count += 1

    return {
        "changed": bool(changed_field_count),
        "changed_field_count": changed_field_count,
        "thread_count": thread_count,
    }


def project_thread_visibility(state: dict[str, Any], thread_ids: list[str]) -> dict[str, Any]:
    """Add unassigned top-level tasks to Desktop's projectless task index."""
    projectless = state.get("projectless-thread-ids")
    if projectless is None:
        if not thread_ids:
            return {
                "changed": False,
                "added_count": 0,
                "eligible_count": 0,
                "status": "current",
            }
        projectless = []
        state["projectless-thread-ids"] = projectless
        created = True
    else:
        created = False
    if not isinstance(projectless, list):
        return {
            "changed": False,
            "added_count": 0,
            "eligible_count": len(thread_ids),
            "status": "invalid_projectless_index",
        }

    assignments = state.get("thread-project-assignments")
    assigned_ids = set(assignments) if isinstance(assignments, dict) else set()
    indexed_ids = {item for item in projectless if isinstance(item, str)}
    added = 0
    for thread_id in thread_ids:
        if not isinstance(thread_id, str) or not thread_id or thread_id in assigned_ids or thread_id in indexed_ids:
            continue
        projectless.append(thread_id)
        indexed_ids.add(thread_id)
        added += 1
    return {
        "changed": bool(created or added),
        "added_count": added,
        "eligible_count": len(thread_ids),
        "status": "updated" if added else "current",
    }


def project_wsl_resume_state(state: dict[str, Any], thread_ids: list[str]) -> dict[str, Any]:
    queued = project_queued_follow_up_contexts(state)
    visibility = project_thread_visibility(state, thread_ids)
    return {
        "changed": bool(queued.get("changed") or visibility.get("changed")),
        "changed_field_count": int(queued.get("changed_field_count") or 0),
        "thread_count": int(queued.get("thread_count") or 0),
        "task_visibility": visibility,
    }
