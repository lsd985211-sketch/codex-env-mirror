"""Project Desktop queued-follow-up paths for WSL session resume.

Ownership: pure transformation of Desktop global-state resume contexts and
additive task visibility indexes.
Non-goals: changing session transcripts, SQLite thread rows, MCP definitions,
Desktop workspace preference roots, existing project assignments, or live
state while Desktop is running. Missing assignments may be reconstructed only
when a thread cwd is inside an existing local project root.
State behavior: mutate only the supplied JSON object and return bounded change
counts; the startup owner decides backup, write, and restart-boundary timing.
Caller context: codex_state_repair facade during the WSL startup preflight.
"""

from __future__ import annotations

import ntpath
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


def _canonical_project_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized_slashes = text.replace("\\", "/")
    mount_match = re.match(r"^/mnt/([a-zA-Z])(?:/(.*))?$", normalized_slashes)
    if mount_match:
        rest = str(mount_match.group(2) or "").replace("/", "\\")
        text = f"{mount_match.group(1)}:\\{rest}" if rest else f"{mount_match.group(1)}:\\"
    elif text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return ntpath.normcase(ntpath.normpath(text))


def _project_for_cwd(state: dict[str, Any], cwd: str) -> str:
    canonical_cwd = _canonical_project_path(cwd)
    projects = state.get("local-projects")
    if not canonical_cwd or not isinstance(projects, dict):
        return ""
    matches: list[tuple[int, str]] = []
    for project_id, project in projects.items():
        if not isinstance(project_id, str) or not project_id or not isinstance(project, dict):
            continue
        roots = project.get("rootPaths")
        if not isinstance(roots, list):
            continue
        for root in roots:
            if not isinstance(root, str):
                continue
            canonical_root = _canonical_project_path(root).rstrip("\\")
            if not canonical_root:
                continue
            if canonical_cwd == canonical_root or canonical_cwd.startswith(canonical_root + "\\"):
                matches.append((len(canonical_root), project_id))
    return max(matches, default=(0, ""))[1]


def project_thread_visibility(
    state: dict[str, Any],
    thread_ids: list[str],
    *,
    thread_cwds: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assign known project tasks, then index remaining tasks as projectless."""
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
    assignments_valid = assignments is None or isinstance(assignments, dict)
    if assignments is None:
        assignments = {}
    assigned_ids = set(assignments) if isinstance(assignments, dict) else set()
    new_assignments: dict[str, dict[str, Any]] = {}
    if assignments_valid and isinstance(assignments, dict):
        for thread_id in thread_ids:
            if not isinstance(thread_id, str) or not thread_id or thread_id in assigned_ids:
                continue
            cwd = str((thread_cwds or {}).get(thread_id) or "")
            project_id = _project_for_cwd(state, cwd)
            if not project_id:
                continue
            new_assignments[thread_id] = {
                "projectKind": "local",
                "projectId": project_id,
                "cwd": cwd,
                "pendingCoreUpdate": False,
            }
        if new_assignments:
            assignments.update(new_assignments)
            state["thread-project-assignments"] = assignments
            assigned_ids.update(new_assignments)

    indexed_ids = {item for item in projectless if isinstance(item, str)}
    eligible_ids = {item for item in thread_ids if isinstance(item, str) and item}
    remove_from_projectless = assigned_ids & eligible_ids & indexed_ids
    if remove_from_projectless:
        projectless[:] = [item for item in projectless if item not in remove_from_projectless]
        indexed_ids.difference_update(remove_from_projectless)
    added = 0
    for thread_id in thread_ids:
        if not isinstance(thread_id, str) or not thread_id or thread_id in assigned_ids or thread_id in indexed_ids:
            continue
        projectless.append(thread_id)
        indexed_ids.add(thread_id)
        added += 1
    return {
        "changed": bool(created or added or new_assignments or remove_from_projectless),
        "added_count": added,
        "assigned_count": len(new_assignments),
        "removed_projectless_count": len(remove_from_projectless),
        "eligible_count": len(thread_ids),
        "status": "updated" if added or new_assignments or remove_from_projectless else "current",
    }


def project_wsl_resume_state(
    state: dict[str, Any],
    thread_ids: list[str],
    *,
    thread_cwds: dict[str, str] | None = None,
) -> dict[str, Any]:
    queued = project_queued_follow_up_contexts(state)
    visibility = project_thread_visibility(state, thread_ids, thread_cwds=thread_cwds)
    return {
        "changed": bool(queued.get("changed") or visibility.get("changed")),
        "changed_field_count": int(queued.get("changed_field_count") or 0),
        "thread_count": int(queued.get("thread_count") or 0),
        "task_visibility": visibility,
    }
